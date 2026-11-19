"""``python -m kaagaz.cli schema-check`` and ``ensure-bucket``: the deploy's step 2
(infra/ec2/deploy.sh). The deploy never migrates, so it asks whether the database is on the
new image's migration head and stops when it is not; the migration itself is run by hand.
"""

import io
from typing import IO

import pytest

from kaagaz.cli import SCHEMA_AHEAD, SCHEMA_BEHIND, _schema_check, _schema_verdict, main
from kaagaz.db.engine import Database

# The revisions a stand-in image has files for, and the head of that line.
KNOWN = {"0001", "0002", "0003"}
HEADS = {"0003"}


def test_a_database_on_the_head_matches() -> None:
    assert _schema_verdict({"0003"}, HEADS, KNOWN) == (0, "schema matches: ['0003']")


def test_a_database_short_of_the_head_is_behind() -> None:
    code, line = _schema_verdict({"0002"}, HEADS, KNOWN)
    assert code == SCHEMA_BEHIND
    assert line == "database is BEHIND: at ['0002'], image needs ['0003']"


def test_an_empty_database_is_behind_by_every_migration() -> None:
    code, line = _schema_verdict(set(), HEADS, KNOWN)
    assert code == SCHEMA_BEHIND
    assert line == "database is BEHIND: at [], image needs ['0003']"


def test_a_database_with_a_revision_this_image_lacks_is_ahead() -> None:
    """A rollback's database: the older image has no file for 0004 (deploy.sh, ASSUMPTION)."""
    code, line = _schema_verdict({"0004"}, HEADS, KNOWN)
    assert code == SCHEMA_AHEAD
    assert line == "database is AHEAD of this image: at ['0004'], image knows up to ['0003']"


def test_one_known_head_and_one_unknown_is_ahead() -> None:
    code, _ = _schema_verdict({"0002", "0004"}, HEADS, KNOWN)
    assert code == SCHEMA_AHEAD


def test_schema_check_without_a_database_url_stops_with_the_cli_error_code() -> None:
    code = main(["schema-check"], environ={}, out=io.StringIO())
    assert code == 2


@pytest.mark.db
def test_schema_check_reads_the_real_database_and_finds_it_on_the_head(db: Database) -> None:
    """End to end: it resolves alembic.ini beside the package, connects read-only with the
    same driver migrations/env.py uses, and reads ``alembic_version``. It takes the ``db``
    fixture only to be sure the database has been migrated to head first."""
    from tests.db_support import database_url

    assert db is not None
    out = io.StringIO()
    assert _schema_check(database_url(), out) == 0
    assert out.getvalue().startswith("schema matches: [")


def test_ensure_bucket_makes_the_bucket_and_says_so() -> None:
    class FakeStorage:
        def __init__(self) -> None:
            self.made = 0

        def ensure_bucket(self) -> None:
            self.made += 1

        def write_tar(self, out: IO[bytes]) -> int:
            raise AssertionError("ensure-bucket writes no tar")

        def read_tar(self, source: IO[bytes]) -> int:
            raise AssertionError("ensure-bucket reads no tar")

    files = FakeStorage()
    out = io.StringIO()
    assert main(["ensure-bucket"], file_store=files, out=out, environ={}) == 0
    assert files.made == 1
    assert out.getvalue() == "storage bucket: ready\n"
