"""The shadow diff harness (PRD 7 PR 9a, #606, the plan's third net): which statements read fewer rows
once the per-person database rule is on, found by running the tests.

KAAGAZ_RULE_SHADOW=<file> turns it on for the test session (tests/conftest.py). Every SELECT (or
WITH ... SELECT) the app runs as kaagaz_app with a person set is run twice in its own transaction:
first under a savepoint with the rule switched off (the owner's login, the tests', deletes the
switch's rows there; the role is put back), its rows counted and rolled back; then for real, with
the rule on. Each statement whose two counts differ, or that the rule refused (42501), is written
to the file, one line a statement and test: test, rows off, rows on, the statement's first 160
characters. The file is the audit table attached to the PR; each line is then checked against the
SQL guard's verdicts (FEWER_OK) by hand. Only reads: a write's own check is the rule's (42501).
"""

import re
from collections.abc import Sequence
from typing import Any

from sqlalchemy import event
from sqlalchemy.engine import Engine

_READ = re.compile(r"^\s*(SELECT|WITH)\b", re.IGNORECASE)
_SKIP = re.compile(r"set_config|pg_advisory|kaagaz_person_rule|pg_stat_activity", re.IGNORECASE)
_OFF = "SAVEPOINT kaagaz_rule_shadow; RESET ROLE; DELETE FROM kaagaz_person_rule; SET LOCAL ROLE kaagaz_app"
_PERSON = "SELECT current_user = 'kaagaz_app' AND coalesce(current_setting('kaagaz.user_id', true), '') <> ''"

test_name = "(none)"
found: list[tuple[str, int, int | str, str]] = []


def install(engine: Engine) -> None:
    """Shadow every person's read on ``engine`` (module notes)."""
    shadows: dict[int, int] = {}

    @event.listens_for(engine, "before_cursor_execute")
    def before(conn: Any, cursor: Any, statement: str, parameters: Any, context: Any, many: bool) -> None:
        if many or not _READ.match(statement) or _SKIP.search(statement):
            return
        cursor.execute(_PERSON)
        if not cursor.fetchone()[0]:
            return
        cursor.execute(_OFF)
        off = None
        try:
            cursor.execute(statement, parameters)
            off = len(cursor.fetchall()) if cursor.description else cursor.rowcount
        except Exception:  # noqa: BLE001, S110 - the real statement says what is wrong
            pass
        finally:
            cursor.execute("ROLLBACK TO SAVEPOINT kaagaz_rule_shadow; RELEASE SAVEPOINT kaagaz_rule_shadow")
        if off is not None:
            shadows[id(cursor)] = off

    @event.listens_for(engine, "after_cursor_execute")
    def after(conn: Any, cursor: Any, statement: str, parameters: Any, context: Any, many: bool) -> None:
        off = shadows.pop(id(cursor), None)
        if off is not None and cursor.rowcount != off:
            found.append((test_name, off, cursor.rowcount, " ".join(statement.split())[:160]))

    @event.listens_for(engine, "handle_error")
    def refused(context: Any) -> None:
        off = shadows.pop(id(context.cursor), None) if context.cursor is not None else None
        if off is not None and getattr(context.original_exception, "sqlstate", None) == "42501":
            found.append((test_name, off, "42501", " ".join(context.statement.split())[:160]))


def write(path: str, rows: Sequence[tuple[str, int, int | str, str]]) -> None:
    with open(path, "w", encoding="utf-8") as out:
        out.write("test\toff\ton\tstatement\n")
        for row in sorted(set(rows), key=lambda r: (r[3], r[0])):
            out.write("\t".join(str(part) for part in row) + "\n")
