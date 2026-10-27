"""Customer separation for empty-folder pruning (``folders.prune_empty``, Vrushit UAT 3): one
company's prune, even handed another company's empty folder Kaagaz made, never deletes it."""

import pytest
from sqlalchemy import text

from kaagaz.db.engine import Database
from kaagaz.db.folders import prune_empty
from tests.test_autofile_db import folders, organisation, read, upload

pytestmark = pytest.mark.db

_UNFILE = "UPDATE documents SET folder_id = NULL WHERE customer_id = :c"


def test_a_prune_never_deletes_another_companys_empty_folder(db: Database) -> None:
    a, a_admin = organisation(db)
    b, b_admin = organisation(db)
    for customer, admin in ((a, a_admin), (b, b_admin)):
        read(db, customer, upload(db, customer, admin))
        with db.customer(customer) as conn:  # every folder Kaagaz made, empty now
            conn.execute(text(_UNFILE), {"c": customer})
    b_folders = [f[0] for f in folders(db, b)]
    a_folders = [f[0] for f in folders(db, a)]

    with db.customer(a) as conn:
        assert prune_empty(conn, a, b_folders) == []
        assert sorted(prune_empty(conn, a, a_folders)) == sorted(a_folders)  # the control

    assert sorted(f[0] for f in folders(db, b)) == sorted(b_folders)
    assert folders(db, a) == []
