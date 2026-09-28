"""
Summary and paper storage rules in src/db.py.

Spec: docs/implementation_plan_2026-09-28_review_fixes.md#A1, #M26, #A8, #M27, #A11, #A13
"""
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest

from src.db import Database, SummaryDowngradeRefused, SummaryNotSaved


@pytest.fixture
def db(tmp_path):
    d = Database(str(tmp_path / "t.db"))
    yield d
    d.close()


def _paper(db, doi="10.1/x", **extra):
    return db.insert_paper({"doi": doi, "title": "T", "canonical_id": f"doi:{doi}", **extra})


# ── A1 / M26: an abstract never replaces a full-text summary ─────────────────

def test_a1_abstract_cannot_replace_full_text(db):
    pid = _paper(db)
    db.insert_summary(pid, summary_text="", key_findings=["paid finding"],
                      methodology="m", conclusions="c", model_version="m1",
                      source_text="full_text")
    with pytest.raises(SummaryDowngradeRefused):
        db.insert_summary(pid, summary_text="attacker text", source_text="abstract")
    kept = db.get_summary(pid)
    assert kept["key_findings"] == ["paid finding"] and kept["source_text"] == "full_text"


def test_a1_full_text_may_replace_abstract(db):
    pid = _paper(db)
    db.insert_summary(pid, summary_text="the abstract", source_text="abstract")
    db.insert_summary(pid, summary_text="", key_findings=["f"], source_text="full_text")
    assert db.get_summary(pid)["source_text"] == "full_text"


# ── A8: a failed write raises instead of returning None ─────────────────────

def test_a8_db_error_raises_summary_not_saved(db, monkeypatch):
    pid = _paper(db)

    class LockedConn:
        def __init__(self, real):
            self._real = real

        def cursor(self):
            raise sqlite3.OperationalError("database is locked")

        def execute(self, *a, **k):
            raise sqlite3.OperationalError("database is locked")

        def __getattr__(self, name):
            return getattr(self._real, name)

    real = db.conn
    monkeypatch.setattr(type(db), "conn", property(lambda self: LockedConn(real)))
    with pytest.raises(SummaryNotSaved, match="locked"):
        db.insert_summary(pid, summary_text="x", source_text="abstract")


# ── M27: only a UNIQUE clash means "already stored" ──────────────────────────

def test_m27_not_null_is_error_not_duplicate(db, caplog):
    import logging
    db.conn.execute("CREATE TRIGGER no_null_title BEFORE INSERT ON papers "
                    "WHEN NEW.title IS NULL BEGIN SELECT RAISE(ABORT, "
                    "'NOT NULL constraint failed: papers.title'); END")
    with caplog.at_level(logging.ERROR, logger="src.db"):
        with pytest.raises(sqlite3.IntegrityError):
            db.insert_paper({"doi": "10.1/nt", "canonical_id": "doi:10.1/nt"})
    assert any("NOT NULL" in r.getMessage() for r in caplog.records)


def test_m27_duplicate_still_returns_none(db):
    assert _paper(db, "10.1/d") is not None
    assert _paper(db, "10.1/d") is None


def test_a1_guard_sees_a_summary_committed_by_another_connection(tmp_path):
    """Gate 2026-09-28 batch 2, finding 4: the guard is one statement, so a
    full-text summary another worker committed is never overwritten."""
    path = str(tmp_path / "shared.db")
    a, b = Database(path), Database(path)
    pid = a.insert_paper({"doi": "10.1/c", "title": "T", "canonical_id": "doi:10.1/c"})
    b.insert_summary(pid, summary_text="", key_findings=["from b"], source_text="full_text")
    with pytest.raises(SummaryDowngradeRefused):
        a.insert_summary(pid, summary_text="abstract from a", source_text="abstract")
    assert a.get_summary(pid)["key_findings"] == ["from b"]
    a.close()
    b.close()


def test_a1_abstract_insert_on_a_new_paper_still_works(db):
    pid = _paper(db, "10.1/new")
    db.insert_summary(pid, summary_text="the abstract", source_text="abstract")
    db.insert_summary(pid, summary_text="the abstract, again", source_text="abstract")
    assert db.get_summary(pid)["summary_text"] == "the abstract, again"
