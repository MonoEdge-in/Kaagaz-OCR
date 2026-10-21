"""File management (Phase 2 PRD 1) stage 3, without a database: what an activity row may
say (A5 and the security review's allow-list), the cursor, the A3 lookup's parameters, and
the export's lines (A6). Against PostgreSQL: test_file_management_activity_db.py and
test_isolation_file_activity.py.
"""

import uuid
from datetime import UTC, datetime
from typing import Any, cast

import pytest
from sqlalchemy import Connection, RowMapping

from kaagaz.api import views
from kaagaz.api.routers import admin
from kaagaz.db import audit

WHEN = datetime(2026, 10, 19, 10, 30, 0, 123456, tzinfo=UTC)
DOCUMENT, FOLDER, ANITA = str(uuid.uuid4()), str(uuid.uuid4()), str(uuid.uuid4())
DERIVED = {"folder_path", "parent_path", "from_name", "to_name"}


def _row(action: str, details: dict[str, Any] | None = None, **columns: Any) -> RowMapping:
    row = {
        "id": 7,
        "action": action,
        "actor_user_id": ANITA,
        "target_type": "document",
        "target_id": DOCUMENT,
        "details": details or {},
        "created_at": WHEN,
        "folder_id": FOLDER,
        "family_id": DOCUMENT,
        "document_name": "Sale Deed Shinde.pdf",
        "document_version_no": 2,
        "document_exists": True,
        **columns,
    }
    return cast(RowMapping, row)


NAMES = audit.Names({ANITA: "Anita"}, {FOLDER: "Land & Legal / Land records"})


def test_a5_a_members_row_keeps_only_the_allowed_details_with_their_names() -> None:
    upload = views.audit_entry(
        _row("document.upload", {"read_model": "m", "family_id": DOCUMENT}), NAMES, full=False
    )
    assert upload["details"] == {}
    move = views.audit_entry(_row("document.move", {"folder_id": FOLDER, "by": "x"}), NAMES, full=False)
    assert move["details"] == {"folder_id": FOLDER, "folder_path": "Land & Legal / Land records"}
    owner = views.audit_entry(_row("document.owner_change", {"from": None, "to": ANITA}), NAMES, full=False)
    assert owner["details"] == {"from": None, "to": ANITA, "from_name": None, "to_name": "Anita"}
    label = views.audit_entry(
        _row("document.label_change", {"from": "internal", "to": "restricted"}), full=False
    )
    assert label["details"] == {"from": "internal", "to": "restricted"}
    for action, allowed in audit.SHOWN_DETAILS.items():
        everything = dict.fromkeys([*allowed, "secret", "read_model", "budget_usd"], "v")
        shown = views.audit_entry(_row(action, everything), NAMES, full=False)["details"]
        assert set(shown) <= set(allowed) | DERIVED, action
    assert views.audit_entry(_row("user.create", {"role": "admin"}), NAMES, full=False)["details"] == {}


def test_a5_names_are_read_when_shown_and_a_deleted_document_says_so() -> None:
    entry = views.audit_entry(_row("document.view"), NAMES, full=False)
    assert entry["actor_name"] == "Anita" and entry["actor_id"] == ANITA
    assert entry["document"] == {
        "id": DOCUMENT,
        "name": "Sale Deed Shinde.pdf",
        "version_no": 2,
        "deleted": False,
    }
    assert entry["folder"] == {"id": FOLDER, "path": "Land & Legal / Land records"}
    gone = views.audit_entry(
        _row("document.delete", document_exists=False, document_name=None), NAMES, full=True
    )
    assert gone["document"] is not None
    assert (gone["document"]["name"], gone["document"]["deleted"]) == ("a deleted document", True)
    folder = views.audit_entry(
        _row("folder.rename", target_type="folder", target_id=FOLDER), NAMES, full=False
    )
    assert folder["document"] is None


def test_a5_the_admins_full_row_keeps_every_detail_but_money() -> None:
    entry = views.audit_entry(_row("user.create", {"role": "admin", "budget_usd": 1.5}), NAMES, full=True)
    assert entry["details"] == {"role": "admin"}


def test_a5_the_cursor_is_the_last_rows_moment_and_id_exactly(monkeypatch: pytest.MonkeyPatch) -> None:
    asked: list[Any] = []
    rows = [_row("document.view", id=n) for n in (9, 8, 7)]

    def entries(conn: Any, customer: str, filters: audit.Filters, **kwargs: Any) -> list[RowMapping]:
        asked.append(kwargs["cursor"])
        return rows[: kwargs["limit"]]

    monkeypatch.setattr(audit, "entries", entries)
    monkeypatch.setattr(audit, "names", lambda *args: NAMES)
    first = views.timeline(cast(Connection, None), "c", audit.Filters(), limit=2)
    assert [item["id"] for item in first["items"]] == [9, 8]
    views.timeline(cast(Connection, None), "c", audit.Filters(), limit=2, cursor=first["next_cursor"])
    assert asked == [None, (WHEN, 8)]  # microseconds kept
    assert views.timeline(cast(Connection, None), "c", audit.Filters(), limit=3)["next_cursor"] is None


class _Capture:
    def __init__(self) -> None:
        self.params: list[dict[str, Any]] = []

    def execute(self, statement: object, params: dict[str, Any]) -> None:
        self.params.append(params)


@pytest.mark.parametrize(
    ("target_type", "target_id", "document", "folder"),
    [
        ("document", DOCUMENT, DOCUMENT, None),
        ("folder", FOLDER, None, FOLDER),
        ("user", ANITA, None, None),
        ("document", "not-an-id", None, None),
        (None, None, None, None),
    ],
)
def test_a3_record_looks_up_a_documents_place_and_keeps_its_call(
    target_type: str | None, target_id: str | None, document: str | None, folder: str | None
) -> None:
    conn = _Capture()
    audit.record(
        cast(Connection, conn),
        "c",
        actor_user_id=None,
        action="document.view",
        target_type=target_type,
        target_id=target_id,
    )
    (params,) = conn.params
    assert (params["document"], params["folder"], params["tid"]) == (document, folder, target_id)


def test_a6_the_export_line_has_the_six_columns_and_is_formula_safe() -> None:
    entry = views.audit_entry(
        _row("document.label_change", {"from": "internal", "to": "public"}), NAMES, full=True
    )
    entry["actor_name"] = "=HYPERLINK()"
    line = admin._csv_text([admin.AUDIT_CSV_COLUMNS, admin._audit_line(entry)]).splitlines()
    assert line[0] == "When,Who,Action,Document,Folder,Details"
    assert line[1].startswith(f"{WHEN.isoformat()},'=HYPERLINK(),document.label_change,Sale Deed Shinde.pdf,")
    assert line[1].endswith('"{""from"": ""internal"", ""to"": ""public""}"')
