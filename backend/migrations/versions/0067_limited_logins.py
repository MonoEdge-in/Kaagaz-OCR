"""Two limited database logins, so the master password leaves the app (#479, Tushar,
3 Nov 2026; NEEDS VRUSHIT'S APPROVAL: security configuration).

Until now the API and the workers logged in as the owner, which skips row level security,
and switched into ``kaagaz_app`` in every transaction: one missed switch, and every
customer's rows were visible.

* ``kaagaz_api`` (the API and the workers, APP_DATABASE_URL) may only switch into
  ``kaagaz_app``; ``kaagaz_ops`` (the API's super admin engine, OPS_DATABASE_URL) only into
  ``kaagaz_operator``. Both are NOINHERIT with ``WITH INHERIT FALSE``, so they hold no right
  of their own: a transaction that forgot ``SET LOCAL ROLE`` reads nothing. No superuser,
  creating databases or roles, replication or skipping the rules, and a CONNECTION LIMIT
  (PROVISIONAL, Vrushit's numbers): ``kaagaz_api`` 80, for the pools (15 each) of the API,
  3 workers (WORKER_REPLICAS on the server) and one CLI run, with 5 to spare; ``kaagaz_ops``
  15, its one pool. Roles belong to the whole server, so each is made only if missing (as 0001's
  ``kaagaz_app``), then its attributes are set either way. No password here: it is set by
  hand with ``\\password`` (docs/running-locally.md, "Limited database logins"); until then
  neither can log in.
* Managed PostgreSQL (Amazon RDS, 12 Nov 2026): the owner there is not a superuser (it is a
  member of ``rds_superuser``), and PostgreSQL 16 and later refuse ``ALTER ROLE`` from anyone
  who does not hold SUPERUSER, REPLICATION or BYPASSRLS to so much as name that attribute,
  ``NO...`` included. ``CREATE ROLE`` may name them as ``NO...`` (only granting one is
  refused), so a new login gets all of ``LIMITED`` when it is made; an existing one is altered
  with the three only when the owner is a superuser (laptops, CI). Either way the migration
  then refuses to finish if a login has any of the three, so the rule above holds everywhere.
* Temporary tables are refused to everyone but the owner (``REVOKE TEMPORARY ... FROM
  PUBLIC``): no code makes one.
* 0001's ``kaagaz_login_lookup`` and ``kaagaz_create_customer``, never made again since,
  get the empty search path with every name schema-qualified, as every later SECURITY
  DEFINER function has (tests/test_isolation_logins.py checks them all). CREATE OR REPLACE
  keeps their grants (kaagaz_app only).

The app already may only add to ``audit_log`` (0001: SELECT, INSERT) and has no right on
``platform_audit`` (0015), so neither changes.

The downgrade gives temporary tables back to PUBLIC and the two functions their 0001 search
path. The logins stay, with their one mode, as 0001's role does: other databases on the
server may use them. Drop them by hand once nothing logs in with them
(``DROP ROLE kaagaz_api, kaagaz_ops``).

Revision ID: 0067
Revises: 0066 (reading pause)
"""

from alembic import op

revision = "0067"
PREVIOUS = "0066"
down_revision = PREVIOUS
branch_labels = None
depends_on = None

# Each login, the one mode it may switch into, and its connection limit.
LOGINS = {"kaagaz_api": ("kaagaz_app", 80), "kaagaz_ops": ("kaagaz_operator", 15)}
# What every login is: given whole when CREATE ROLE makes it (any owner may name these there).
LIMITED = "LOGIN NOINHERIT NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS"
# ALTER ROLE on an existing login: what any owner may name, and what only a superuser may
# (PostgreSQL 16+: naming SUPERUSER, REPLICATION or BYPASSRLS needs that attribute oneself).
ALTERABLE = "LOGIN NOINHERIT NOCREATEDB NOCREATEROLE"
SUPERUSER_ONLY = "NOSUPERUSER NOREPLICATION NOBYPASSRLS"
OWNER_FUNCTIONS = ("kaagaz_login_lookup", "kaagaz_create_customer")


# 0001's two functions, as 0001 wrote them (so the downgrade restores their text exactly),
# with ``{schema}`` before each table and ``{path}`` the search path.
_LOGIN_LOOKUP = """
    CREATE OR REPLACE FUNCTION public.kaagaz_login_lookup(p_email text)
    RETURNS TABLE (user_id uuid, customer_id uuid, password_hash text, role text, disabled boolean)
    LANGUAGE sql STABLE SECURITY DEFINER SET search_path = {path} AS
    $$
        SELECT u.id, u.customer_id, u.password_hash, u.role, u.disabled
        FROM {schema}users u
        WHERE u.email = lower(p_email)
    $$
    """
_CREATE_CUSTOMER = """
    CREATE OR REPLACE FUNCTION public.kaagaz_create_customer(
        p_name text, p_admin_email text, p_admin_name text, p_password_hash text
    ) RETURNS uuid
    LANGUAGE plpgsql SECURITY DEFINER SET search_path = {path} AS
    $$
    DECLARE
        new_id uuid;
    BEGIN
        INSERT INTO {schema}customers (name) VALUES (p_name) RETURNING id INTO new_id;
        INSERT INTO {schema}users (customer_id, email, display_name, role, password_hash)
        VALUES (new_id, lower(p_admin_email), p_admin_name, 'admin', p_password_hash);
        RETURN new_id;
    END
    $$
    """


def _functions(schema: str, path: str) -> list[str]:
    return [body.format(schema=schema, path=path) for body in (_LOGIN_LOOKUP, _CREATE_CUSTOMER)]


def _login(login: str, limit: int) -> str:
    """Make the login with every limit, or set them on an existing one as far as this owner
    may; then refuse to go on if it is a superuser, replicates or skips row level security."""
    return f"""
        DO $$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{login}') THEN
                CREATE ROLE {login} {LIMITED} CONNECTION LIMIT {limit};
            ELSIF (SELECT rolsuper FROM pg_roles WHERE rolname = current_user) THEN
                ALTER ROLE {login} {ALTERABLE} CONNECTION LIMIT {limit};
            ELSE
                ALTER ROLE {login} {ALTERABLE} CONNECTION LIMIT {limit};
            END IF;
            IF EXISTS (
                SELECT 1 FROM pg_roles
                WHERE rolname = '{login}' AND (rolsuper OR rolreplication OR rolbypassrls)
            ) THEN
                RAISE EXCEPTION '% must not be a superuser, replicate or bypass row level security',
                    '{login}';
            END IF;
        END
        $$
        """


UPGRADE = [
    *[
        statement
        for login, (mode, limit) in LOGINS.items()
        for statement in (
            _login(login, limit),
            f"GRANT {mode} TO {login} WITH INHERIT FALSE, SET TRUE",
        )
    ],
    "DO $$ BEGIN EXECUTE format('REVOKE TEMPORARY ON DATABASE %I FROM PUBLIC', current_database()); END $$",
    *_functions("public.", "''"),
]

DOWNGRADE = [
    *_functions("", "public, pg_temp"),
    "DO $$ BEGIN EXECUTE format('GRANT TEMPORARY ON DATABASE %I TO PUBLIC', current_database()); END $$",
]


def upgrade() -> None:
    for statement in UPGRADE:
        op.execute(statement)


def downgrade() -> None:
    for statement in DOWNGRADE:
        op.execute(statement)
