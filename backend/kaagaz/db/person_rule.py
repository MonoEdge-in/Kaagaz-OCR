"""The per-person database rule's switch (PRD 7, #606, 0091): ``python -m kaagaz.cli person-rule
on|off|status [--customer ID | --all]``; and the start-up checks' read of the login (``login_risks``).

The switch is ``kaagaz_person_rule``, the owner's only (no grant, no policy): one row for each
organisation the rule is on for, a NULL ``for_customer_id`` for every organisation; no row, off.
So these run on the owner's own login, never a SET ROLE (the migrate container).

Turning it on refuses unless the workers already run as ``kaagaz_worker`` (every worker report
of the last 10 minutes came from it, and there is at least one: ``reader_status.role``) and the
API login can't switch into it: else the workers would read nothing, or the API everything.
Turning it off needs nothing: it is the way back, with no deploy and no migration, and the views
still keep each person to what they may see.
"""

from collections.abc import Sequence

from sqlalchemy import Connection, text

WORKER_ROLE = "kaagaz_worker"

_WORKERS = text(
    "SELECT count(*), count(*) FILTER (WHERE role <> :w) FROM reader_status"
    " WHERE seen_at > now() - interval '10 minutes'"
)
_API_REACHES_WORKER = text(
    "SELECT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'kaagaz_api')"
    " AND pg_has_role('kaagaz_api', :w, 'MEMBER')"
)
_ON = text(
    "INSERT INTO kaagaz_person_rule (for_customer_id, turned_on_by) VALUES (CAST(:c AS uuid), :by)"
    " ON CONFLICT DO NOTHING"
)
_OFF_ONE = text("DELETE FROM kaagaz_person_rule WHERE for_customer_id = CAST(:c AS uuid)")
_OFF_ALL = text("DELETE FROM kaagaz_person_rule")
_STATUS = text(
    "SELECT for_customer_id, turned_on_at, turned_on_by FROM kaagaz_person_rule"
    " ORDER BY for_customer_id NULLS FIRST"
)


class Refused(Exception):
    """Turning the rule on is not safe yet; the message says why."""


def turn_on(conn: Connection, customer_id: str | None, by: str) -> None:
    """On for ``customer_id`` (None: every organisation), after the two checks (module notes).
    ``conn`` is the owner's own. Three statements."""
    reports, others = conn.execute(_WORKERS, {"w": WORKER_ROLE}).one()
    if not reports:
        raise Refused("no worker reported in the last 10 minutes: start the workers first")
    if others:
        raise Refused(
            f"{others} worker report(s) of the last 10 minutes came from another role than {WORKER_ROLE}:"
            " set JOBS_DATABASE_URL and restart the workers first (docs/running-locally.md)"
        )
    if conn.execute(_API_REACHES_WORKER, {"w": WORKER_ROLE}).scalar():
        raise Refused(f"the API login kaagaz_api can switch into {WORKER_ROLE}: revoke it first")
    conn.execute(_ON, {"c": customer_id, "by": by})


def turn_off(conn: Connection, customer_id: str | None) -> int:
    """Off for ``customer_id``, or every row with None (the way back). How many rows went."""
    if customer_id is None:
        return int(conn.execute(_OFF_ALL).rowcount or 0)
    return int(conn.execute(_OFF_ONE, {"c": customer_id}).rowcount or 0)


def status(conn: Connection) -> Sequence[tuple[str | None, str, str]]:
    """Each row: (the organisation, None for every one; when; by whom)."""
    return [
        (None if row[0] is None else str(row[0]), row[1].isoformat(), str(row[2]))
        for row in conn.execute(_STATUS)
    ]


# The start-up checks (PRD 7 decision 22): the rule holds a login only while it is neither a
# superuser, nor skips row level security, nor owns the tables (no table has FORCE), nor can switch
# into the worker role (held by the organisation rule alone) or the operator's.
_LOGIN = text(
    "SELECT r.rolsuper, r.rolbypassrls,"
    " pg_has_role(session_user,"
    " (SELECT c.relowner FROM pg_class c WHERE c.oid = 'public.documents'::regclass), 'MEMBER'),"
    " pg_has_role(session_user, :w, 'MEMBER'), pg_has_role(session_user, 'kaagaz_operator', 'MEMBER')"
    " FROM pg_roles r WHERE r.rolname = session_user"
)
LOGIN_CODES = ("superuser_login", "bypassrls_login", "owner_login", "worker_reachable", "operator_reachable")


def login_risks(conn: Connection) -> list[str]:
    """The codes (LOGIN_CODES) of what the connection's login could do past the rule. One read."""
    row = conn.execute(_LOGIN, {"w": WORKER_ROLE}).one()
    return [code for code, risky in zip(LOGIN_CODES, row, strict=True) if risky]
