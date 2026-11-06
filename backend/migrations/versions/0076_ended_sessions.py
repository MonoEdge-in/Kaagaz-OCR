"""Logout ends this browser's session on the server (#502 item 1, #520, Tushar 6 Nov 2026).
NEEDS VRUSHIT'S APPROVAL: schema and security configuration.

A session cookie is signed and kept by no server table, so logout only cleared the browser's
copy: a copied cookie kept working until it expired. Each session cookie now carries its own
random id (``sid``, ``api/auth.py``); logout writes that id here with the cookie's own expiry,
and every request's user query (``db/users.session_user``) refuses an id written here, in the
same statement (one primary-key probe). The user's other browsers stay signed in. Sign out
everywhere and the operator's logout keep their ``session_version`` raise.

* ``ended_sessions``: one row per ended session, the customer's (RLS ``customer_only``, a key
  on ``(customer_id, user_id)`` to ``users``, so a row never hangs under another customer's
  user and goes with its user). The app may read and add rows only (0067's ``kaagaz_api``
  reaches them through ``kaagaz_app``); it never changes or deletes one.
* ``kaagaz_sweep_ended_sessions(before)``: the worker's hourly sweep (with the abandoned
  uploads, ``jobs/purge.py``) deletes the rows whose cookie expired by ``before`` and returns
  how many; nothing else. Owner rights (SECURITY DEFINER, the empty search path), the app only.

The downgrade drops both; ids written are lost, so a cookie logged out before it works again
until its own expiry (the behaviour before 0076).
"""

from alembic import op

revision = "0076"
down_revision = "0075"
branch_labels = None
depends_on = None

APP_ROLE = "kaagaz_app"

UPGRADE = [
    """
    CREATE TABLE ended_sessions (
        customer_id uuid NOT NULL,
        session_id uuid NOT NULL,
        user_id uuid NOT NULL,
        ended_at timestamptz NOT NULL DEFAULT now(),
        expires_at timestamptz NOT NULL,
        PRIMARY KEY (customer_id, session_id),
        FOREIGN KEY (customer_id, user_id) REFERENCES users (customer_id, id) ON DELETE CASCADE
    )
    """,
    "CREATE INDEX ended_sessions_expiry ON ended_sessions (expires_at)",
    "CREATE INDEX ended_sessions_user ON ended_sessions (customer_id, user_id)",
    f"GRANT SELECT, INSERT ON ended_sessions TO {APP_ROLE}",
    "ALTER TABLE ended_sessions ENABLE ROW LEVEL SECURITY",
    """
    CREATE POLICY customer_only ON ended_sessions
    USING (customer_id = kaagaz_current_customer())
    WITH CHECK (customer_id = kaagaz_current_customer())
    """,
    """
    CREATE FUNCTION kaagaz_sweep_ended_sessions(p_before timestamptz) RETURNS integer
    LANGUAGE sql SECURITY DEFINER SET search_path = '' AS
    $$
        WITH gone AS (DELETE FROM public.ended_sessions WHERE expires_at <= p_before RETURNING 1)
        SELECT count(*)::integer FROM gone
    $$
    """,
    "REVOKE ALL ON FUNCTION kaagaz_sweep_ended_sessions(timestamptz) FROM PUBLIC",
    f"GRANT EXECUTE ON FUNCTION kaagaz_sweep_ended_sessions(timestamptz) TO {APP_ROLE}",
    "COMMENT ON TABLE ended_sessions IS 'Sessions ended by logout before their cookie expired (0076):"
    " a request with one of these ids is signed out. Swept once the cookie has expired.'",
    "COMMENT ON COLUMN ended_sessions.customer_id IS 'The organisation of the user who logged out.'",
    "COMMENT ON COLUMN ended_sessions.session_id IS 'The random id inside the session cookie that was ended.'",
    "COMMENT ON COLUMN ended_sessions.user_id IS 'The user whose session it was.'",
    "COMMENT ON COLUMN ended_sessions.ended_at IS 'When the user logged out.'",
    "COMMENT ON COLUMN ended_sessions.expires_at IS 'When the cookie would have expired; the row is swept after it.'",
    "COMMENT ON POLICY customer_only ON ended_sessions IS 'A row is seen and written only by its own customer.'",
    "COMMENT ON FUNCTION public.kaagaz_sweep_ended_sessions(timestamptz) IS"
    " 'The worker''s sweep: deletes the ended sessions whose cookie expired by p_before; returns how many.'",
]

DOWNGRADE = [
    "DROP FUNCTION kaagaz_sweep_ended_sessions(timestamptz)",
    "DROP TABLE ended_sessions",
]


def upgrade() -> None:
    for statement in UPGRADE:
        op.execute(statement)


def downgrade() -> None:
    for statement in DOWNGRADE:
        op.execute(statement)
