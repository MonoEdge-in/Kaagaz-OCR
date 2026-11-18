"""PRD 9 F-03 (D3, #680): a failed copy in Trash is deleted for good when the same file is uploaded
again, but only one the uploader can see. Another person's copy they can't see stays, and the
upload says only that a copy is hidden; another organisation's is never touched. With PRD 7's
database rule on and off: with it off, the view (``visible_documents``) is the only guard."""

import hashlib
import uuid

import pytest

from kaagaz.db.documents import PgDocuments
from kaagaz.db.engine import Database
from kaagaz.ingestion.ports import DocumentRecord
from tests.db_support import APP_ROLE, rule_off, rule_on
from tests.test_api_support import CSRF, Api, Signed
from tests.test_isolation_access_views import _expected, _walled
from tests.test_operator_auth_db import _owner

pytestmark = pytest.mark.db

_DATA = b"name,amount\nsalary,1\n"
_DELETES = "SELECT target_id FROM audit_log WHERE customer_id = :c AND action = 'document.delete'"


def _upload(signed: Signed) -> dict[str, object]:
    reply = signed.client.post(
        "/api/documents", files={"file": ("again.csv", _DATA, "text/csv")}, headers=CSRF
    )
    assert reply.status_code in (200, 201), reply.text
    return dict(reply.json())


@pytest.mark.parametrize("rule", ["on", "off"])
def test_a_failed_copy_in_trash_is_deleted_only_for_who_sees_it(db: Database, rule: str) -> None:
    api = Api(db)
    org = _walled(api)
    trashed = org.files["trashed"]  # in Salaries, a private folder: "named" sees it, "member" doesn't
    assert trashed not in _expected(db, org, org.people["member"].user_id)
    assert trashed in _expected(db, org, org.people["named"].user_id)
    sha = hashlib.sha256(_DATA).hexdigest()
    _owner(
        db,
        "UPDATE documents SET status = 'failed', sha256 = :h WHERE id = :d",
        {"h": sha, "d": trashed},
    )

    with rule_on() if rule == "on" else rule_off():
        hidden = _upload(org.people["member"])
        assert (hidden["duplicate"], hidden["document"], hidden.get("hidden")) == (True, None, True)
        assert _owner(db, "SELECT status FROM documents WHERE id = :d", {"d": trashed}) == [("failed",)]
        assert api.storage.has(org.c, trashed)
        # Its delete checks again, as for an upload whose lookup raced: the person must see the copy.
        member = org.people["member"].user_id
        record = DocumentRecord(org.c, str(uuid.uuid4()), sha, len(_DATA), "text", uploaded_by=member)
        documents = PgDocuments(Database(db.engine, APP_ROLE), storage=api.storage, user_id=member)
        assert documents.add_if_absent(record)[1] is False
        assert _owner(db, "SELECT count(*) FROM documents WHERE id = :d", {"d": trashed}) == [(1,)]

        other = _upload(api.new_signed())  # another organisation: its own new file
        assert other["duplicate"] is False
        assert _owner(db, "SELECT count(*) FROM documents WHERE id = :d", {"d": trashed}) == [(1,)]
        assert _owner(db, _DELETES, {"c": org.c}) == []

        seen = _upload(org.people["named"])
        assert seen["duplicate"] is False
        assert _owner(db, "SELECT count(*) FROM documents WHERE id = :d", {"d": trashed}) == [(0,)]
        assert not api.storage.has(org.c, trashed)
        assert _owner(db, _DELETES, {"c": org.c}) == [(trashed,)]
