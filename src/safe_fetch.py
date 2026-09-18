"""
Fetch a PDF from a URL without letting the URL reach internal services.

Purpose: SSRF-safe PDF download for the web app's PDF proxy.
Spec:    docs/web_parity_spec_2026-09-17.md#SEC-2
Tests:   tests/web/test_safe_fetch.py

Every hop is checked, not just the first URL: a redirect from a public host to
169.254.169.254 or localhost is the standard way past a one-shot check.

  * https only, on every hop;
  * the host must resolve (fail closed), and every address it resolves to must
    be globally routable — this excludes loopback, RFC 1918, link-local
    (cloud metadata), CGNAT 100.64/10, 0/8, IPv6 ULA/link-local, and
    IPv4-mapped forms of those;
  * redirects are followed by hand (at most MAX_REDIRECTS), each re-checked;
  * the connection is made to an address that passed the check (pinned), with
    TLS still verified against the hostname — so DNS answering differently a
    second time (rebinding) has no second lookup to exploit;
  * the body is read in chunks and abandoned past the byte cap;
  * the body must start with the PDF magic bytes.

An earlier version checked the socket's peer after `requests` connected. That
failed on every real redirect: urllib3 reads an empty redirect body to the end
and returns the connection to its pool before the peer can be read. Found by
running the proxy against live Europe PMC links, not by the unit tests.
"""

from __future__ import annotations

import ipaddress
import logging
import socket
import urllib.parse
from typing import Callable, Optional, Set

import certifi
import urllib3

logger = logging.getLogger(__name__)

MAX_REDIRECTS = 5
CHUNK = 64 * 1024


class FetchRefused(Exception):
    """The URL (or a redirect) points somewhere we will not fetch."""


class FetchFailed(Exception):
    """The upstream could not be fetched (network, HTTP error, too many hops)."""


class NotAPdf(Exception):
    """The upstream answered, but not with a PDF (e.g. a publisher landing page)."""


class TooLarge(Exception):
    """The upstream body exceeds the byte cap."""


def _normalise(addr: ipaddress._BaseAddress) -> ipaddress._BaseAddress:
    if isinstance(addr, ipaddress.IPv6Address) and addr.ipv4_mapped is not None:
        return addr.ipv4_mapped
    return addr


def is_public_address(ip: str) -> bool:
    try:
        addr = _normalise(ipaddress.ip_address(ip.split("%", 1)[0]))
    except ValueError:
        return False
    return addr.is_global and not addr.is_multicast


def resolve_public(host: str, port: int = 443,
                   getaddrinfo: Callable = socket.getaddrinfo) -> Set[str]:
    """Every address `host` resolves to, or FetchRefused if any is non-public
    or the name does not resolve."""
    if not host:
        raise FetchRefused("URL has no host.")
    try:
        infos = getaddrinfo(host, port, proto=socket.IPPROTO_TCP)
    except (socket.gaierror, UnicodeError, OSError) as e:
        raise FetchRefused(f"Host {host!r} does not resolve.") from e
    addrs = {info[4][0] for info in infos}
    if not addrs:
        raise FetchRefused(f"Host {host!r} does not resolve.")
    bad = [a for a in addrs if not is_public_address(a)]
    if bad:
        raise FetchRefused(f"Host {host!r} resolves to a non-public address.")
    return {str(_normalise(ipaddress.ip_address(a.split("%", 1)[0]))) for a in addrs}


class _PinnedResponse:
    """The slice of a response fetch_pdf uses, over a urllib3 response."""

    def __init__(self, resp, pool):
        self._resp, self._pool = resp, pool
        self.status_code = resp.status
        self.headers = resp.headers

    @property
    def is_redirect(self) -> bool:
        return self.status_code in (301, 302, 303, 307, 308) and "Location" in self.headers

    def iter_content(self, size: int):
        return self._resp.stream(size)

    def close(self) -> None:
        try:
            self._resp.release_conn()
        finally:
            self._pool.close()


def pinned_get(url: str, ip: str, timeout: float, headers: dict) -> _PinnedResponse:
    """GET `url` over a connection to `ip`, verifying TLS against the URL's host."""
    parsed = urllib.parse.urlparse(url)
    pool = urllib3.HTTPSConnectionPool(
        ip, port=parsed.port or 443,
        server_hostname=parsed.hostname, assert_hostname=parsed.hostname,
        cert_reqs="CERT_REQUIRED", ca_certs=certifi.where(),
        timeout=urllib3.Timeout(total=timeout), retries=False,
    )
    path = (parsed.path or "/") + (f"?{parsed.query}" if parsed.query else "")
    try:
        resp = pool.urlopen("GET", path, headers={**headers, "Host": parsed.netloc},
                            redirect=False, preload_content=False, assert_same_host=False)
    except BaseException:
        pool.close()
        raise
    return _PinnedResponse(resp, pool)


def _ipv4_first(addrs: Set[str]) -> list:
    return sorted(addrs, key=lambda a: (":" in a, a))


def fetch_pdf(url: str, max_bytes: int, timeout: float = 30,
              user_agent: str = "BioRx/1.0",
              getaddrinfo: Callable = socket.getaddrinfo,
              get: Callable = pinned_get) -> bytes:
    """Download a PDF under the rules in the module docstring."""
    for _hop in range(MAX_REDIRECTS + 1):
        parsed = urllib.parse.urlparse(url)
        if parsed.scheme != "https":
            raise FetchRefused("PDF URL must use https.")
        allowed = resolve_public(parsed.hostname or "", parsed.port or 443, getaddrinfo)

        resp = None
        last_error: Optional[Exception] = None
        for ip in _ipv4_first(allowed):      # a host without IPv6 egress still works
            try:
                resp = get(url, ip=ip, timeout=timeout, headers={"User-Agent": user_agent})
                break
            except (urllib3.exceptions.HTTPError, OSError) as e:
                last_error = e
        if resp is None:
            logger.info("PDF fetch failed for %s: %s", url, last_error)
            raise FetchFailed("Could not reach the PDF host.") from last_error

        try:
            if resp.is_redirect:
                location = resp.headers.get("Location", "")
                if not location:
                    raise FetchFailed("Redirect without a location.")
                url = urllib.parse.urljoin(url, location)
                continue

            if resp.status_code >= 400:
                raise FetchFailed(f"The PDF host answered {resp.status_code}.")

            declared = resp.headers.get("Content-Length")
            if declared:
                try:
                    if int(declared) > max_bytes:
                        raise TooLarge()
                except ValueError:
                    pass   # malformed header: the streamed count below decides

            data = bytearray()
            try:
                for chunk in resp.iter_content(CHUNK):
                    data.extend(chunk)
                    if len(data) > max_bytes:
                        raise TooLarge()
            except (urllib3.exceptions.HTTPError, OSError) as e:
                raise FetchFailed("The PDF download was interrupted.") from e
            if not bytes(data[:5]) == b"%PDF-":
                raise NotAPdf()
            return bytes(data)
        finally:
            resp.close()

    raise FetchFailed(f"More than {MAX_REDIRECTS} redirects.")
