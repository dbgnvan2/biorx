"""
Fetch a PDF from a URL without letting the URL reach internal services.

Purpose: SSRF-safe fetches for the web app: the PDF proxy, and the PDF text
         and abstract scraping behind POST /api/summaries (both take URLs a
         client can supply).
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
import time
import urllib.parse
from typing import Callable, Optional, Set

import certifi
import urllib3

logger = logging.getLogger(__name__)

MAX_REDIRECTS = 5
CHUNK = 64 * 1024
DEFAULT_PDF_MAX_BYTES = 100 * 1024 * 1024
DEFAULT_HTML_MAX_BYTES = 5 * 1024 * 1024
# The socket timeout applies per read; this bounds a whole download, so a
# server trickling one byte at a time cannot hold a worker indefinitely.
DEFAULT_DEADLINE_SECONDS = 120
# PDF readers accept the %PDF- header anywhere in the first 1024 bytes.
PDF_MAGIC_WINDOW = 1024

# Globally-routable by the stdlib's reckoning, but translate to IPv4 space
# that may be private: IPv4-compatible (deprecated) and NAT64 prefixes.
_TRANSLATED_V6 = [
    ipaddress.ip_network("::/96"),
    ipaddress.ip_network("64:ff9b::/96"),
    ipaddress.ip_network("64:ff9b:1::/48"),
]


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
    if isinstance(addr, ipaddress.IPv6Address) and any(addr in n for n in _TRANSLATED_V6):
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
    except UnicodeError as e:
        raise FetchRefused(f"Host {host!r} is not a valid name.") from e
    except OSError as e:
        # Fail closed (nothing is fetched), but as a retryable failure: a DNS
        # hiccup is not a policy decision (learnings P1).
        raise FetchFailed(f"Host {host!r} did not resolve.") from e
    addrs = {info[4][0] for info in infos}
    if not addrs:
        raise FetchFailed(f"Host {host!r} did not resolve.")
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
    host = parsed.hostname or ""
    host_header = f"[{host}]" if ":" in host else host
    if parsed.port:
        host_header += f":{parsed.port}"       # never the userinfo part of netloc
    try:
        resp = pool.urlopen("GET", path, headers={**headers, "Host": host_header},
                            redirect=False, preload_content=False, assert_same_host=False)
    except BaseException:
        pool.close()
        raise
    return _PinnedResponse(resp, pool)


def _ipv4_first(addrs: Set[str]) -> list:
    return sorted(addrs, key=lambda a: (":" in a, a))


def _fetch_public(url: str, max_bytes: int, timeout: float, headers: dict,
                  getaddrinfo: Callable, get: Callable,
                  deadline_seconds: float = DEFAULT_DEADLINE_SECONDS,
                  clock: Callable = time.monotonic):
    """The hop loop shared by fetch_pdf and fetch_html. Returns (bytes, headers)."""
    deadline = clock() + deadline_seconds
    for _hop in range(MAX_REDIRECTS + 1):
        if clock() > deadline:
            raise FetchFailed(f"The download took longer than {deadline_seconds:.0f} s.")
        try:
            parsed = urllib.parse.urlparse(url)
            port = parsed.port or 443
        except ValueError as e:                 # e.g. a port out of range
            raise FetchRefused("Malformed URL.") from e
        if parsed.scheme != "https":
            raise FetchRefused("URL must use https.")
        allowed = resolve_public(parsed.hostname or "", port, getaddrinfo)

        resp = None
        last_error: Optional[Exception] = None
        for ip in _ipv4_first(allowed):      # a host without IPv6 egress still works
            try:
                resp = get(url, ip=ip, timeout=timeout, headers=headers)
                break
            except (urllib3.exceptions.HTTPError, OSError) as e:
                last_error = e
            except (ValueError, UnicodeError) as e:
                raise FetchRefused("Malformed URL.") from e
        if resp is None:
            logger.info("Fetch failed for %s: %s", url, last_error)
            raise FetchFailed("Could not reach the host.") from last_error

        try:
            if resp.is_redirect:
                location = resp.headers.get("Location", "")
                if not location:
                    raise FetchFailed("Redirect without a location.")
                url = urllib.parse.urljoin(url, location)
                continue

            if resp.status_code >= 400:
                raise FetchFailed(f"The host answered {resp.status_code}.")

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
                    if clock() > deadline:
                        raise FetchFailed(f"The download took longer than {deadline_seconds:.0f} s.")
            except (urllib3.exceptions.HTTPError, OSError) as e:
                raise FetchFailed("The download was interrupted.") from e
            return bytes(data), resp.headers
        finally:
            resp.close()

    raise FetchFailed(f"More than {MAX_REDIRECTS} redirects.")


def fetch_pdf(url: str, max_bytes: int = DEFAULT_PDF_MAX_BYTES, timeout: float = 30,
              user_agent: str = "BioRx/1.0",
              getaddrinfo: Callable = socket.getaddrinfo,
              get: Callable = pinned_get) -> bytes:
    """Download a PDF under the rules in the module docstring."""
    data, _ = _fetch_public(url, max_bytes, timeout, {"User-Agent": user_agent},
                            getaddrinfo, get)
    if b"%PDF-" not in data[:PDF_MAGIC_WINDOW]:
        raise NotAPdf()
    return data


def fetch_html(url: str, headers: Optional[dict] = None, timeout: float = 20,
               max_bytes: int = DEFAULT_HTML_MAX_BYTES,
               getaddrinfo: Callable = socket.getaddrinfo,
               get: Callable = pinned_get) -> str:
    """Download a web page under the same rules (no PDF check), as text."""
    data, resp_headers = _fetch_public(url, max_bytes, timeout, dict(headers or {}),
                                       getaddrinfo, get)
    charset = "utf-8"
    ctype = resp_headers.get("Content-Type", "") or ""
    if "charset=" in ctype:
        charset = ctype.split("charset=", 1)[1].split(";")[0].strip().strip('"') or "utf-8"
    try:
        return data.decode(charset, errors="replace")
    except LookupError:
        return data.decode("utf-8", errors="replace")
