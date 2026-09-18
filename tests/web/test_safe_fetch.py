"""
SSRF-safe PDF fetch (src/safe_fetch.py).

Spec:  docs/web_parity_spec_2026-09-17.md#SEC-2
Found by /security-review on 2026-09-17: the proxy checked only the first URL,
followed redirects, missed link-local and CGNAT ranges, and failed open when a
name did not resolve. No network: resolution and the pinned HTTP call are
injected. (The live path was exercised against Europe PMC links on 2026-09-17.)
"""
import socket
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

import pytest
import urllib3

from src import safe_fetch
from src.safe_fetch import FetchFailed, FetchRefused, NotAPdf, TooLarge, fetch_pdf

PDF = b"%PDF-1.7\n" + b"x" * 100
PUBLIC = "93.184.216.34"


def dns(table):
    """A getaddrinfo stand-in: host -> list of IPs (or an exception)."""
    def getaddrinfo(host, port, proto=0):
        value = table.get(host)
        if value is None:
            raise socket.gaierror("no such host")
        family = socket.AF_INET6 if ":" in value[0] else socket.AF_INET
        return [(family, socket.SOCK_STREAM, proto, "", (ip, port)) for ip in value]
    return getaddrinfo


class FakeResp:
    def __init__(self, status=200, headers=None, body=PDF):
        self.status_code = status
        self.headers = headers or {}
        self._body = body
        self.closed = False

    @property
    def is_redirect(self):
        return self.status_code in (301, 302, 303, 307, 308) and "Location" in self.headers

    def iter_content(self, size, deadline=None, clock=None):
        for i in range(0, len(self._body), size):
            yield self._body[i:i + size]

    def close(self):
        self.closed = True


def http(routes, unreachable_ips=()):
    """A pinned_get stand-in: url -> FakeResp. Records (url, ip) per call."""
    calls = []

    def get(url, ip, timeout, headers):
        calls.append((url, ip))
        if ip in unreachable_ips or url not in routes:
            raise urllib3.exceptions.NewConnectionError(None, "unreachable")
        return routes[url]
    get.calls = calls
    return get


def fetch(url, routes, table, unreachable_ips=(), **kw):
    get = http(routes, unreachable_ips)
    data = fetch_pdf(url, max_bytes=kw.pop("max_bytes", 10_000), getaddrinfo=dns(table),
                     get=get, **kw)
    return data, get


# ── The happy path ────────────────────────────────────────────────────────────

def test_public_pdf_is_returned():
    data, get = fetch("https://pub.example/a.pdf",
                      {"https://pub.example/a.pdf": FakeResp()}, {"pub.example": [PUBLIC]})
    assert data == PDF
    assert get.calls == [("https://pub.example/a.pdf", PUBLIC)]


def test_public_redirect_is_followed_and_rechecked():
    routes = {"https://doi.org/10.1/x": FakeResp(302, {"Location": "https://pub.example/x.pdf"}),
              "https://pub.example/x.pdf": FakeResp()}
    data, get = fetch("https://doi.org/10.1/x", routes,
                      {"doi.org": [PUBLIC], "pub.example": ["151.101.1.1"]})
    assert data == PDF
    # Each hop is pinned to the address its own name resolved to.
    assert get.calls == [("https://doi.org/10.1/x", PUBLIC),
                         ("https://pub.example/x.pdf", "151.101.1.1")]


# ── SSRF: every hop, every range, fail closed ────────────────────────────────

@pytest.mark.parametrize("internal", [
    "169.254.169.254",      # cloud metadata (link-local)
    "127.0.0.1", "10.0.0.5", "172.16.0.1", "192.168.1.1",
    "100.64.0.1",           # CGNAT
    "0.0.0.0",
    "::1", "fd00::1", "fe80::1",
    "::ffff:127.0.0.1",     # IPv4-mapped loopback
])
def test_redirect_to_internal_address_is_refused(internal):
    routes = {"https://evil.example/p.pdf": FakeResp(302, {"Location": "https://internal.example/"}),
              "https://internal.example/": FakeResp()}
    with pytest.raises(FetchRefused):
        fetch("https://evil.example/p.pdf", routes,
              {"evil.example": [PUBLIC], "internal.example": [internal]})


def test_redirect_to_http_is_refused():
    routes = {"https://evil.example/p.pdf":
              FakeResp(302, {"Location": "http://169.254.169.254/latest/meta-data/"})}
    with pytest.raises(FetchRefused, match="https"):
        fetch("https://evil.example/p.pdf", routes, {"evil.example": [PUBLIC]})


def test_the_internal_host_is_never_requested():
    routes = {"https://evil.example/p.pdf": FakeResp(302, {"Location": "https://meta.example/"}),
              "https://meta.example/": FakeResp()}
    get = http(routes)
    with pytest.raises(FetchRefused):
        fetch_pdf("https://evil.example/p.pdf", 10_000,
                  getaddrinfo=dns({"evil.example": [PUBLIC], "meta.example": ["169.254.169.254"]}),
                  get=get)
    assert get.calls == [("https://evil.example/p.pdf", PUBLIC)]


def test_unresolvable_host_fails_closed_as_retryable():
    """Nothing is fetched (closed), but a DNS failure is a 502-class
    FetchFailed, not a 403 policy refusal (P1: transient is not terminal)."""
    get = http({})
    with pytest.raises(FetchFailed, match="did not resolve"):
        fetch_pdf("https://nowhere.example/p.pdf", 10_000, getaddrinfo=dns({}), get=get)
    assert get.calls == []


def test_host_with_one_private_address_among_public_is_refused():
    with pytest.raises(FetchRefused):
        fetch("https://mixed.example/p.pdf", {"https://mixed.example/p.pdf": FakeResp()},
              {"mixed.example": [PUBLIC, "10.0.0.1"]})


def test_connection_is_pinned_to_a_checked_address():
    """Rebinding: the fetch never does its own DNS lookup — it connects to an
    address that passed the check, IPv4 first."""
    table = {"pub.example": ["2606:4700::1111", PUBLIC]}
    _, get = fetch("https://pub.example/p.pdf", {"https://pub.example/p.pdf": FakeResp()}, table)
    assert get.calls == [("https://pub.example/p.pdf", PUBLIC)]


def test_falls_back_to_the_next_checked_address():
    table = {"pub.example": ["2606:4700::1111", PUBLIC]}
    data, get = fetch("https://pub.example/p.pdf", {"https://pub.example/p.pdf": FakeResp()},
                      table, unreachable_ips={PUBLIC})
    assert data == PDF
    assert [ip for _, ip in get.calls] == [PUBLIC, "2606:4700::1111"]


def test_unreachable_everywhere_is_a_fetch_failure():
    with pytest.raises(FetchFailed, match="reach"):
        fetch("https://pub.example/p.pdf", {"https://pub.example/p.pdf": FakeResp()},
              {"pub.example": [PUBLIC]}, unreachable_ips={PUBLIC})


def test_too_many_redirects():
    routes = {f"https://pub.example/{i}": FakeResp(302, {"Location": f"https://pub.example/{i+1}"})
              for i in range(10)}
    with pytest.raises(FetchFailed, match="redirects"):
        fetch("https://pub.example/0", routes, {"pub.example": [PUBLIC]})


# ── Size and content ─────────────────────────────────────────────────────────

def test_chunked_body_over_the_cap_is_abandoned():
    body = b"%PDF-" + b"x" * 5000
    resp = FakeResp(body=body)
    with pytest.raises(TooLarge):
        fetch("https://pub.example/p.pdf", {"https://pub.example/p.pdf": resp},
              {"pub.example": [PUBLIC]}, max_bytes=1000)
    assert resp.closed


def test_declared_length_over_the_cap_is_refused_before_reading():
    resp = FakeResp(headers={"Content-Length": "999999"})
    with pytest.raises(TooLarge):
        fetch("https://pub.example/p.pdf", {"https://pub.example/p.pdf": resp},
              {"pub.example": [PUBLIC]}, max_bytes=1000)


def test_malformed_content_length_does_not_crash():
    resp = FakeResp(headers={"Content-Length": "lots"})
    data, _ = fetch("https://pub.example/p.pdf", {"https://pub.example/p.pdf": resp},
                    {"pub.example": [PUBLIC]})
    assert data == PDF


def test_html_landing_page_is_not_a_pdf():
    resp = FakeResp(headers={"Content-Type": "text/html"}, body=b"<!doctype html><html>")
    with pytest.raises(NotAPdf):
        fetch("https://pub.example/p", {"https://pub.example/p": resp}, {"pub.example": [PUBLIC]})


def test_upstream_error_status_is_a_fetch_failure():
    with pytest.raises(FetchFailed, match="404"):
        fetch("https://pub.example/p.pdf", {"https://pub.example/p.pdf": FakeResp(404)},
              {"pub.example": [PUBLIC]})


def test_is_public_address_basics():
    assert safe_fetch.is_public_address(PUBLIC)
    assert safe_fetch.is_public_address("2606:4700::1111")
    for ip in ("169.254.169.254", "::ffff:10.0.0.1", "not-an-ip", "224.0.0.1"):
        assert not safe_fetch.is_public_address(ip), ip


# ── Edges found by the second security pass (2026-09-17) ──────────────────────

@pytest.mark.parametrize("ip", ["64:ff9b::a00:1", "::a00:1", "64:ff9b:1::1"])
def test_nat64_and_ipv4_compatible_forms_are_not_public(ip):
    assert not safe_fetch.is_public_address(ip)


def test_out_of_range_port_is_refused_not_a_500():
    with pytest.raises(FetchRefused, match="Malformed"):
        fetch("https://pub.example:99999/p.pdf", {}, {"pub.example": [PUBLIC]})


def test_host_header_never_carries_userinfo(monkeypatch):
    seen = {}

    def fake_urlopen(self, method, path, headers=None, **kw):
        seen["headers"] = headers
        raise urllib3.exceptions.NewConnectionError(None, "stop here")
    monkeypatch.setattr(urllib3.HTTPSConnectionPool, "urlopen", fake_urlopen)
    with pytest.raises(urllib3.exceptions.NewConnectionError):
        safe_fetch.pinned_get("https://user:pw@pub.example:8443/x", ip=PUBLIC,
                              timeout=1, headers={})
    assert seen["headers"]["Host"] == "pub.example:8443"


def test_fetch_html_applies_the_same_rules_and_decodes():
    page = FakeResp(headers={"Content-Type": "text/html; charset=latin-1"},
                    body="<p>caf\xe9</p>".encode("latin-1"))
    get = http({"https://pub.example/a": page})
    text = safe_fetch.fetch_html("https://pub.example/a", getaddrinfo=dns({"pub.example": [PUBLIC]}),
                                 get=get)
    assert text == "<p>caf\xe9</p>"
    with pytest.raises(FetchRefused):
        safe_fetch.fetch_html("https://meta.example/",
                              getaddrinfo=dns({"meta.example": ["169.254.169.254"]}), get=get)


# ── Re-sweep of the fix commit (2026-09-17) ───────────────────────────────────

def test_pinned_get_connects_to_the_ip_and_verifies_the_hostname(monkeypatch):
    """The rebinding defence rests on this: the pool's host is the checked IP,
    and TLS is verified against the URL's hostname with CA checks on."""
    built = {}

    class FakePool:
        def __init__(self, host, **kw):
            built.update(host=host, **kw)

        def urlopen(self, method, path, headers=None, **kw):
            built.update(path=path, headers=headers, urlopen_kw=kw)
            return type("R", (), {"status": 200, "headers": {}})()

        def close(self):
            pass
    monkeypatch.setattr(urllib3, "HTTPSConnectionPool", FakePool)
    safe_fetch.pinned_get("https://pub.example/a/b.pdf?x=1", ip=PUBLIC, timeout=5, headers={})
    assert built["host"] == PUBLIC
    assert built["server_hostname"] == "pub.example"
    assert built["assert_hostname"] == "pub.example"
    assert built["cert_reqs"] == "CERT_REQUIRED"
    assert built["path"] == "/a/b.pdf?x=1"
    assert built["headers"]["Host"] == "pub.example"
    assert built["urlopen_kw"]["redirect"] is False


def test_overall_deadline_stops_a_trickling_server():
    ticks = iter(range(0, 1000, 50))          # each clock() call is 50 s later
    body = b"%PDF-" + b"x" * (safe_fetch.CHUNK * 3)
    get = http({"https://pub.example/p.pdf": FakeResp(body=body)})
    with pytest.raises(FetchFailed, match="longer than"):
        safe_fetch._fetch_public("https://pub.example/p.pdf", 10**9, 5, {},
                                 dns({"pub.example": [PUBLIC]}), get,
                                 deadline_seconds=120, clock=lambda: next(ticks))


def test_pdf_magic_may_follow_leading_bytes():
    body = b"\n\r junk " + b"%PDF-1.4 rest"
    data, _ = fetch("https://pub.example/p.pdf",
                    {"https://pub.example/p.pdf": FakeResp(body=body)}, {"pub.example": [PUBLIC]})
    assert data == body


def test_deadline_holds_against_a_real_trickling_server():
    """Re-sweep 2: the deadline was only checked after a full 64 KiB chunk, so
    a server sending a byte every few hundred ms held the reader until the
    whole body arrived. Real socket, real clock; plain HTTP pool injected in
    place of the TLS one (the read loop is the same _PinnedResponse)."""
    import threading
    import time as _time

    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.bind(("127.0.0.1", 0))
    srv.listen(1)
    port = srv.getsockname()[1]
    stop = threading.Event()

    def serve():
        conn, _ = srv.accept()
        conn.recv(4096)
        conn.sendall(b"HTTP/1.1 200 OK\r\nContent-Length: 1000\r\n\r\n%PDF-")
        try:
            while not stop.is_set():
                conn.sendall(b"x")
                _time.sleep(0.2)
        except OSError:
            pass
        finally:
            conn.close()
    threading.Thread(target=serve, daemon=True).start()

    def get(url, ip, timeout, headers):
        pool = urllib3.HTTPConnectionPool("127.0.0.1", port=port, retries=False,
                                          timeout=urllib3.Timeout(total=timeout))
        resp = pool.urlopen("GET", "/", redirect=False, preload_content=False)
        return safe_fetch._PinnedResponse(resp, pool, read_timeout=timeout)

    start = _time.monotonic()
    try:
        with pytest.raises(FetchFailed, match="longer than"):
            safe_fetch._fetch_public("https://pub.example/p.pdf", 10**6, 1.0, {},
                                     dns({"pub.example": [PUBLIC]}), get,
                                     deadline_seconds=1.5)
    finally:
        stop.set()
        srv.close()
    elapsed = _time.monotonic() - start
    assert elapsed < 3.0, f"deadline 1.5 s but the read ran {elapsed:.1f} s"
