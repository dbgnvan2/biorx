"""
Tests for the suite-wide network guard in tests/conftest.py.

A guard nobody tests can stop working quietly. The first version raised on a
network attempt but could be defeated by code that catches exceptions — the
abstract recovery chain does, by design — so these run real inner test sessions
and check the outcome, not just that an exception was raised.
"""
import shutil
import socket
from pathlib import Path

import pytest

pytest_plugins = ["pytester"]

CONFTEST = Path(__file__).parent / "conftest.py"


def _session(pytester, body: str):
    shutil.copy(CONFTEST, pytester.path / "conftest.py")
    pytester.makepyfile(test_inner=body)
    return pytester.runpytest_subprocess("-q", "-p", "no:cacheprovider")


def test_a_direct_network_attempt_fails_the_test(pytester):
    result = _session(pytester, """
import socket
def test_reaches_out():
    socket.create_connection(("example.org", 80), timeout=2)
""")
    result.assert_outcomes(failed=1)


def test_a_swallowed_network_attempt_still_fails_the_test(pytester):
    """The defeat of the first version: code under test catches the exception."""
    result = _session(pytester, """
import socket
def test_swallows_it():
    try:
        socket.getaddrinfo("api.openalex.org", 443)
    except Exception:
        pass
""")
    result.assert_outcomes(passed=1, errors=1)
    result.stdout.fnmatch_lines(["*tried to reach the network*api.openalex.org*"])


def test_loopback_is_allowed(pytester):
    result = _session(pytester, """
import socket
def test_local_server():
    server = socket.socket(); server.bind(("127.0.0.1", 0)); server.listen(1)
    client = socket.socket(); client.connect(server.getsockname())
    client.close(); server.close()
""")
    result.assert_outcomes(passed=1)


def test_the_guard_restores_socket_functions_after_the_session(pytester):
    """Monkeypatched module state must not leak past the session."""
    result = _session(pytester, """
import socket
def test_patched_during_session():
    assert socket.socket.connect.__name__ == "_guarded_connect"
""")
    result.assert_outcomes(passed=1)


def test_this_session_is_guarded():
    assert socket.getaddrinfo.__name__ == "_guarded_getaddrinfo"
