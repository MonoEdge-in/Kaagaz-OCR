"""Customer separation of the AI settings by task (0038): another organisation's defaults,
locks and members' choices are never read for a person, never shown and never written.

Row level security alone hides B's rows from A's connection and refuses a row written for
B; the foreign key refuses a choice of A's for B's user. The API's control: B's own saved
choice shows for B, and changes B's Ask, never A's. The reader choice (#398): A's Save cost
never decides how B's uploads are read.
"""

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from kaagaz.db import ai_settings, models
from kaagaz.db.engine import Database
from tests.settings_factory import make_settings
from tests.test_api_support import CSRF, Api
from tests.test_reader_choice_db import _job_reading, _upload, cost_saving, put, scan_of

pytestmark = pytest.mark.db

_INSERT = (
    "INSERT INTO ai_task_settings (customer_id, user_id, task, level, locked)"
    " VALUES (:c, CAST(:u AS uuid), 'ask', 'none', false)"
)


def test_another_organisations_settings_are_never_read_or_shown(db: Database) -> None:
    api = Api(db, settings=make_settings(ocr_engine="gemini"))
    a_admin, b_admin = api.new_admin(), api.new_admin()
    a_member, b_member = api.sign_in(a_admin.customer_id), api.sign_in(b_admin.customer_id)
    b_lock = {"tasks": {"ask": {"level": "none", "locked": True}}}
    assert b_admin.client.put("/api/ai-settings/organisation", json=b_lock, headers=CSRF).status_code == 200

    a_ask = {task["task"]: task for task in a_member.client.get("/api/ai-settings").json()["tasks"]}["ask"]
    assert (a_ask["organisation"], a_ask["locked"], a_ask["effective"]) == (None, False, "low")
    a_member.client.post("/api/chat", json={"question": "a?"}, headers=CSRF)
    assert api.answers.choices[-1] is not models.NO_MODEL
    # The control: B's own lock reaches B's member.
    b_member.client.post("/api/chat", json={"question": "b?"}, headers=CSRF)
    assert api.answers.choices[-1] is models.NO_MODEL

    with db.customer(a_admin.customer_id) as conn:
        assert ai_settings.rows(conn, b_admin.customer_id, b_member.user_id) == {}
        assert conn.execute(text("SELECT count(*) FROM ai_task_settings")).scalar_one() == 0
        changed = conn.execute(text("UPDATE ai_task_settings SET level = 'max' WHERE customer_id = :c"),
                               {"c": b_admin.customer_id})  # fmt: skip
        assert changed.rowcount == 0
    with db.customer(b_admin.customer_id) as conn:
        assert ai_settings.rows(conn, b_admin.customer_id, None) == {("ask", True): ("none", True)}


def test_one_organisations_reader_choice_never_reaches_another(db: Database) -> None:
    """#398: A's Save cost (locked) never decides how B's uploads are read, nor shows for B;
    the control: A's own member's upload reads as A chose."""
    api = Api(db, settings=make_settings(ocr_engine="gemini"))
    a_admin, b_admin = api.new_admin(), api.new_admin()
    a_member, b_member = api.sign_in(a_admin.customer_id), api.sign_in(b_admin.customer_id)
    for customer in (a_admin.customer_id, b_admin.customer_id):
        cost_saving(db, customer)
    assert put(a_admin, "organisation", {"level": "save", "locked": True}).status_code == 200

    b_scan = scan_of(b_member)
    assert (b_scan["organisation"], b_scan["locked"], b_scan["effective"]) == (None, False, "low")
    b_reading = _job_reading(db, b_member.customer_id, _upload(b_member)).reading
    assert b_reading is not None and (b_reading.reader, b_reading.routes) == ("ai", False)
    a_reading = _job_reading(db, a_member.customer_id, _upload(a_member)).reading
    assert a_reading is not None and (a_reading.reader, a_reading.routes) == ("save", True)  # the control
    with db.customer(b_admin.customer_id) as conn:
        assert ai_settings.rows(conn, a_admin.customer_id, None) == {}
        assert (
            conn.execute(text("SELECT count(*) FROM ai_task_settings WHERE level = 'save'")).scalar_one() == 0
        )


def test_a_row_for_another_organisation_or_its_user_is_refused(db: Database) -> None:
    api = Api(db)
    a, b = api.new_signed(), api.new_signed()
    with pytest.raises(DBAPIError), db.customer(a.customer_id) as conn:
        conn.execute(text(_INSERT), {"c": b.customer_id, "u": None})  # row level security's WITH CHECK
    with pytest.raises(DBAPIError), db.customer(a.customer_id) as conn:
        conn.execute(text(_INSERT), {"c": a.customer_id, "u": b.user_id})  # B's user under A: the key
    with db.customer(b.customer_id) as conn:
        assert conn.execute(text("SELECT count(*) FROM ai_task_settings")).scalar_one() == 0
