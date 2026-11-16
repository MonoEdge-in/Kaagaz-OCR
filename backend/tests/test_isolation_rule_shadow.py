"""The shadow diff harness's own test (PRD 7 PR 9a, #606): a planted statement that reads fewer rows
under the rule is reported, with its counts; one that reads the same is not; and the real statement
still runs, in its own transaction, with the rule on."""

import pytest
from sqlalchemy import text

from kaagaz.db.engine import Database, make_engine
from tests import rule_shadow
from tests.db_support import APP_ROLE, database_url
from tests.test_api_support import Api
from tests.test_isolation_access_views import _all, _expected, _walled

pytestmark = [pytest.mark.db, pytest.mark.usefixtures("the_rule_on")]


def test_a_statement_that_reads_fewer_under_the_rule_is_reported(db: Database) -> None:
    org = _walled(Api(db))
    plain = org.people["plain"].user_id
    engine = make_engine(database_url())
    rule_shadow.install(engine)
    before = len(rule_shadow.found)
    try:
        with Database(engine, APP_ROLE).user(org.c, plain) as conn:
            seen = {str(r[0]) for r in conn.execute(text("SELECT id FROM documents"))}
            visible = conn.execute(text("SELECT count(*) FROM visible_documents")).scalar()
    finally:
        engine.dispose()
    assert seen == _expected(db, org, plain)
    planted = [row for row in rule_shadow.found[before:] if row[3] == "SELECT id FROM documents"]
    assert [(off, on) for _, off, on, _ in planted] == [(len(_all(db, org.c)), len(seen))]
    assert visible == len(seen)
    assert not [row for row in rule_shadow.found[before:] if "visible_documents" in row[3]]
