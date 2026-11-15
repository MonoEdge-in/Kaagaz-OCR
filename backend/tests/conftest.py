"""pytest only: tag customer isolation tests with the ``isolation`` marker.

CI runs ``pytest -m isolation`` first and on its own (ci.yml). Every test in a
module named ``test_isolation_*.py`` gets the marker, so an isolation test
cannot be forgotten. The tests themselves are plain unittest and do not import
pytest, so ``python -m unittest`` runs them too.

Changing what CI selects is Vrushit's call; this file needs his OK before any
pull request.
"""

import os
from collections.abc import Iterator
from typing import Any

import pytest

from kaagaz.db.engine import Database


def pytest_configure(config: Any) -> None:
    config.addinivalue_line("markers", "isolation: proves one customer cannot reach another customer's data")


def pytest_runtest_setup(item: Any) -> None:
    from tests import rule_shadow

    rule_shadow.test_name = item.nodeid


def pytest_collection_modifyitems(items: list[Any]) -> None:
    for item in items:
        module_name = item.module.__name__.rsplit(".", 1)[-1]
        if module_name.startswith("test_isolation_"):
            item.add_marker("isolation")


@pytest.fixture(scope="session")
def db() -> Iterator[Database]:
    """A real database, migrated once per session (tests marked ``db`` only). PRD 7 (#606): the
    per-person database rule is on for every organisation, unless KAAGAZ_TEST_PERSON_RULE=off (the
    baseline run)."""
    from tests.db_support import database_url, make_database, migrate, person_rule

    url = database_url()
    migrate(url)
    database = make_database(url)
    person_rule(database, on=os.environ.get("KAAGAZ_TEST_PERSON_RULE", "on") != "off")
    shadow = os.environ.get("KAAGAZ_RULE_SHADOW", "")
    if shadow:  # PRD 7 (#606): the shadow diff harness (tests/rule_shadow.py)
        from tests import rule_shadow

        rule_shadow.install(database.engine)
    yield database
    if shadow:
        rule_shadow.write(shadow, rule_shadow.found)
    database.engine.dispose()
