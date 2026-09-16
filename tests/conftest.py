"""
Suite-wide guards.

No test may reach the network. Every external call in this repo is stubbed in
its tests, and a test that is not stubbed does not fail — it just becomes slow
and flaky, passing or failing on the state of someone else's server. That is
exactly how the N2 gate found `test_a_paper_with_no_text_at_all_is_an_error`
racing a live Europe PMC lookup (learnings P34: a test inherits the side
effects of the real code it runs). Stubbing each new call site by hand is the
guard that depends on every future test remembering; this one does not.

Loopback and Unix-domain sockets are allowed: the test client, a local server
started by a test, and SQLite never leave the machine.
"""
import logging
import os
import socket
from pathlib import Path

import pytest

logger = logging.getLogger(__name__)

# ── Real-artifact fingerprint guard ──────────────────────────────────────────
# A class-level patch in a test (gui_module.Database) can be defeated by a
# second import spelling (src.db.Database). Fingerprinting the real artifacts
# catches any escape regardless of how it occurred (P28/P6).
#
# ~/preprints/ is fingerprinted as a whole tree so that WAL/SHM files,
# source_cache.db, and PDFs are all covered (P5 siblings, P6 WAL mode).
#
# filters.json: the guard watches <repo_root>/filters.json via __file__-relative
# path. The app writes Path("filters.json") CWD-relative — these coincide when
# pytest CWD is the repo root, which is always true for the standard invocation
# `pytest tests/` from the repo root. Running pytest from another directory
# would leave a gap; this is documented rather than worked around (P19).

_REPO_ROOT      = Path(__file__).parent.parent
_REAL_PREPRINTS = Path("~/preprints").expanduser()
_REAL_FILTERS   = _REPO_ROOT / "filters.json"


def _fingerprint_dir(path: Path) -> dict | None:
    """Snapshot {relative_path: (mtime_ns, size)} for every file in a directory tree.

    Returns None when the snapshot is incomplete — either the rglob walk itself
    failed or an individual file could not be stat'd. None means "cannot certify
    clean" (P2/P31 — a partial snapshot must not pass as clean).
    """
    if not path.exists():
        return {}
    out: dict = {}
    failed: list = []
    try:
        for p in sorted(path.rglob("*")):
            if p.is_file():
                try:
                    s = p.stat()
                    out[str(p.relative_to(path))] = (s.st_mtime_ns, s.st_size)
                except OSError as exc:
                    failed.append((str(p), exc))
    except OSError as exc:
        logger.error("artifact guard: rglob walk of %s failed: %s", path, exc)
        return None
    if failed:
        for fp, exc in failed:
            logger.error("artifact guard: could not stat %s: %s", fp, exc)
        return None  # partial snapshot — cannot certify clean
    return out


def _fingerprint_file(path: Path):
    try:
        s = path.stat()
        return (s.st_mtime_ns, s.st_size)
    except FileNotFoundError:
        return None


@pytest.fixture(scope="session", autouse=True)
def _guard_real_artifacts(tmp_path_factory):
    """Redirect BIORX_DB_PATH to a temp path and fail if any real artifact changes.
    source_cache.db (SearchCache) is excluded — SearchCache has no callers so
    ~/preprints/source_cache.db is never written by production code."""
    tmp_db = tmp_path_factory.mktemp("db") / "test.db"

    prev_db = os.environ.get("BIORX_DB_PATH")
    os.environ["BIORX_DB_PATH"] = str(tmp_db)

    before_preprints = _fingerprint_dir(_REAL_PREPRINTS)
    before_filters   = _fingerprint_file(_REAL_FILTERS)

    yield

    if prev_db is None:
        os.environ.pop("BIORX_DB_PATH", None)
    else:
        os.environ["BIORX_DB_PATH"] = prev_db

    after_preprints = _fingerprint_dir(_REAL_PREPRINTS)
    after_filters   = _fingerprint_file(_REAL_FILTERS)

    escapes = []
    if before_preprints is None or after_preprints is None:
        escapes.append(
            "~/preprints/ scan was incomplete (OSError during rglob) — "
            "cannot certify the directory was not modified"
        )
    elif before_preprints != after_preprints:
        changed = set(after_preprints) - set(before_preprints)
        changed |= {k for k in before_preprints if before_preprints[k] != after_preprints.get(k)}
        escapes.append(f"~/preprints/ was modified (files: {sorted(changed)})")
    if before_filters != after_filters:
        escapes.append(f"filters.json was modified: {_REAL_FILTERS}")
    if escapes:
        pytest.fail(
            "a test escaped artifact isolation (class-level patch bypassed?):\n"
            + "\n".join(escapes),
            pytrace=False,
        )

_LOCAL_HOSTS = {"127.0.0.1", "::1", "localhost", "0.0.0.0", ""}

_real_connect = socket.socket.connect
_real_connect_ex = socket.socket.connect_ex
_real_getaddrinfo = socket.getaddrinfo


class NetworkAccessInTest(RuntimeError):
    """Raised when a test tries to leave the machine."""


# Every attempt is recorded as well as raised. Raising alone is not enough:
# code under test that deliberately catches per-source failures (the abstract
# recovery chain does exactly that) swallows the exception, and the test passes
# as if nothing happened. The first version of this guard was defeated that way.
_violations = []


def _host_of(address) -> str:
    if isinstance(address, tuple) and address:
        return str(address[0])
    return ""          # AF_UNIX paths and anything non-IP are local


def _check(address):
    if isinstance(address, (str, bytes)):
        return                                    # Unix-domain socket
    host = _host_of(address)
    if host not in _LOCAL_HOSTS:
        _violations.append(repr(address))
        raise NetworkAccessInTest(
            f"a test tried to reach the network ({address!r}). Stub the call "
            "instead — see tests/conftest.py."
        )


def _guarded_connect(self, address):
    _check(address)
    return _real_connect(self, address)


def _guarded_connect_ex(self, address):
    _check(address)
    return _real_connect_ex(self, address)


def _guarded_getaddrinfo(host, *args, **kwargs):
    if host is not None and str(host) not in _LOCAL_HOSTS:
        _violations.append(str(host))
        raise NetworkAccessInTest(
            f"a test tried to resolve {host!r}. Stub the call instead — see "
            "tests/conftest.py."
        )
    return _real_getaddrinfo(host, *args, **kwargs)


@pytest.fixture(scope="session", autouse=True)
def _no_network_in_tests():
    socket.socket.connect = _guarded_connect
    socket.socket.connect_ex = _guarded_connect_ex
    socket.getaddrinfo = _guarded_getaddrinfo
    try:
        yield
    finally:
        # Restore module-level state however the session ends.
        socket.socket.connect = _real_connect
        socket.socket.connect_ex = _real_connect_ex
        socket.getaddrinfo = _real_getaddrinfo


@pytest.fixture(autouse=True)
def _fail_the_test_that_touched_the_network(request):
    """Fail at teardown if the test tried to reach the network, even when the
    code under test caught the exception the guard raised."""
    _violations.clear()
    yield
    call = getattr(request.node, "_guard_call_report", None)
    already_failed = call is not None and call.failed
    if _violations and not already_failed \
            and not request.node.get_closest_marker("allows_network_attempt"):
        attempted = sorted(set(_violations))
        _violations.clear()
        pytest.fail(
            "this test tried to reach the network, and something caught the "
            f"guard's exception: {attempted}. Stub the call.",
            pytrace=False,
        )


def pytest_configure(config):
    config.addinivalue_line(
        "markers",
        "allows_network_attempt: the test deliberately exercises the network guard",
    )


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(item, call):
    """Keep the call-phase report so the teardown check can tell whether the
    test already failed on the guard's exception — no need to report it twice."""
    outcome = yield
    report = outcome.get_result()
    if report.when == "call":
        item._guard_call_report = report
