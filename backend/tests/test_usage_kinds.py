"""Features 25-27 (council 15 Oct 2026, C8): the ledger's new kinds of work. ``billed_as``
bills Dynamic's careful re-reads, Ensemble's other page reads and other answer drafts apart
from ``scan`` and ``chat``, also from threads started with ``copy_context().run``; a call
that fails after it was billed still counts; an inner scope is never billed as an outer
``billed_as``. No database: the fake from test_model_choice_contracts stands in. The
operator's daily split: test_migration_0026_db."""

import contextvars
import threading

import pytest

from kaagaz.ai import usage
from tests.test_model_choice_contracts import CUSTOMER, DOCUMENT, USER, _Conn, _db

FLASH, PRO = "google/gemini-3.8-flash", "google/gemini-3.1-pro-preview"


def test_the_new_kinds_are_ledger_kinds() -> None:
    assert {"careful", "ensemble_scan", "ensemble_chat"} <= set(usage.KINDS)
    with pytest.raises(ValueError, match="kind"), usage.billed_as("dearer"):
        pass


def test_careful_re_reads_are_never_billed_as_scan_and_reach_page_threads() -> None:
    fake, db = _db(_Conn())
    with usage.usage_scope(db, customer_id=CUSTOMER, user_id=USER, kind="scan", document_id=DOCUMENT):
        usage.record_call(FLASH, "google-vertex", 100, 50, 0, 0.01)  # the Standard read
        with usage.billed_as("careful"):
            threads = [
                threading.Thread(
                    target=contextvars.copy_context().run, args=(usage.record_call, PRO, None, 10, 5, 0, 0.1)
                )
                for _ in range(4)
            ]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join()
        with usage.billed_as("ensemble_scan"):
            usage.record_call(FLASH, None, 7, 3, 0, 0.02)
        usage.record_call(FLASH, None, 1, 1, 0, 0.0, cached=True)  # the scope's own kind again
    [rows] = fake.conn.sent
    assert sorted((row["kind"], row["model"], row["cached"], row["calls"]) for row in rows) == [
        ("careful", PRO, False, 4),
        ("ensemble_scan", FLASH, False, 1),
        ("scan", FLASH, False, 1),
        ("scan", FLASH, True, 1),
    ]
    assert all(row["document_id"] == DOCUMENT and row["user_id"] == USER for row in rows)


def test_a_failed_call_is_still_counted_when_the_work_fails() -> None:
    """The reply was billed before the work failed: its row is saved as the scope ends."""
    fake, db = _db(_Conn())
    with (
        pytest.raises(RuntimeError),
        usage.usage_scope(db, customer_id=CUSTOMER, user_id=USER, kind="chat"),
        usage.billed_as("ensemble_chat"),
    ):
        usage.record_call(PRO, None, 10, 5, 0, 0.1)
        raise RuntimeError("the draft was refused after it was billed")
    [rows] = fake.conn.sent
    assert [(row["kind"], row["calls"]) for row in rows] == [("ensemble_chat", 1)]


def test_an_inner_scope_is_billed_as_its_own_kind() -> None:
    fake, db = _db(_Conn())
    with (
        usage.usage_scope(db, customer_id=CUSTOMER, user_id=USER, kind="chat"),
        usage.billed_as("ensemble_chat"),
    ):
        with usage.usage_scope(db, customer_id=CUSTOMER, user_id=USER, kind="memory"):
            usage.record_call(FLASH, None, 1, 1, 0, 0.0)
        usage.record_call(FLASH, None, 1, 1, 0, 0.0)
    inner, outer = fake.conn.sent
    assert [row["kind"] for row in inner] == ["memory"]
    assert [row["kind"] for row in outer] == ["ensemble_chat"]
