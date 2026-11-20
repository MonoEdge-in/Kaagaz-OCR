"""#697: Upload as new file deletes forever only the uploader's own organisation's copy in Trash.
Another organisation's copy of the same bytes is never touched: its upload is its own new file."""

import pytest

from kaagaz.db.engine import Database
from tests.test_api_folder_upload_files import ResumableStorage
from tests.test_api_support import Api
from tests.test_api_trash_replace import exists, in_trash, upload

pytestmark = pytest.mark.db


def test_another_organisations_copy_in_trash_is_never_touched(db: Database) -> None:
    api = Api(db, storage=ResumableStorage())
    owner = api.new_admin("Owner")
    old = in_trash(api, owner)
    other = api.new_admin("Other")

    reply = upload(other, replace_trash=True)

    assert (reply.status_code, reply.json()["duplicate"]) == (201, False)
    assert exists(api, old)
    assert api.storage.has(owner.customer_id, old)
    assert "document.replace_from_trash" not in api.audit_actions(owner.customer_id)
    assert "document.replace_from_trash" not in api.audit_actions(other.customer_id)
