"""Workers get their own database role and login (PRD 7 decision 15, #587, Tushar, 14 Nov 2026;
NEEDS VRUSHIT'S APPROVAL: security configuration).

Until now the API and the workers shared one login (``kaagaz_api``) and one role
(``kaagaz_app``). PRD 7 will hold ``kaagaz_app`` to a per-person rule (PR 9) that the workers,
who act for the whole organisation, must not be held to; so they get their own pair now, while
the two roles may still do exactly the same.

* ``kaagaz_worker`` (NOLOGIN, made as 0001 made ``kaagaz_app``) gets every right ``kaagaz_app``
  holds in this database today, copied from the catalog: each table, sequence and column grant,
  each function (the owner-rights ones included) and the schema. Every row level security
  policy is for PUBLIC (none names a role), so the worker role is held by the organisation
  rule exactly as the app role is. A GRANT this login may not make only warns, and a server's
  catalog may hold hand-made grants CI never sees, so the migration then refuses to finish if
  ``kaagaz_app`` holds any right (on a relation, column, function, schema, the database, a type
  or in default privileges) that ``kaagaz_worker`` lacks, and names it.
  tests/test_isolation_logins.py fails if the two roles' rights ever differ, so a later
  migration cannot grant to one and forget the other.
* ``kaagaz_jobs`` (the workers' login, JOBS_DATABASE_URL) is limited as 0067's logins are, and
  may only switch into ``kaagaz_worker`` (``WITH INHERIT FALSE``: no right of its own). Its
  CONNECTION LIMIT is PROVISIONAL (Vrushit's numbers): 65, the pools (15 each) of 3 workers
  (WORKER_REPLICAS on the server) and one CLI run, with 5 to spare. It is set only when the login
  is made, so a limit raised by hand is kept. It caps what this login opens, not the database
  server: the owner's sessions (migrate, backups, psql) are outside every login's limit. No
  password here: it is set by hand (docs/running-locally.md, "Limited database logins"); until then nothing logs
  in with it and the workers keep ``kaagaz_api``, so nothing changes on release day.
* Amazon RDS, as 0067: a role is made with every limit (CREATE ROLE may name the NO... forms of
  SUPERUSER, REPLICATION and BYPASSRLS); an existing one, roles being server-wide, is altered
  only with what a non-superuser owner may name. The migration then refuses to finish if either
  role is a superuser, replicates or skips row level security; if ``kaagaz_jobs`` is a member of
  any role but ``kaagaz_worker``, or reaches ``kaagaz_app``, ``kaagaz_operator`` or the owner;
  or if ``kaagaz_api`` or ``kaagaz_ops`` reaches ``kaagaz_worker``.

The downgrade revokes every right ``kaagaz_worker`` holds in this database. The two roles stay,
as 0001's and 0067's do: other databases on the server may use them. Drop them by hand once no
database grants anything to ``kaagaz_worker`` (``DROP ROLE kaagaz_jobs, kaagaz_worker``).

Revision ID: 0083
Revises: 0082
"""

from alembic import op

revision = "0083"
down_revision = "0082"
branch_labels = None
depends_on = None

APP_ROLE, WORKER_ROLE, JOBS_LOGIN = "kaagaz_app", "kaagaz_worker", "kaagaz_jobs"
JOBS_CONNECTION_LIMIT = 65
# Each role: what CREATE ROLE gives it, and what ALTER ROLE sets on one that exists (PostgreSQL
# 16+ refuses SUPERUSER, REPLICATION and BYPASSRLS there to an owner who lacks them, 0067; the
# connection limit is left as it is).
_JOBS = "LOGIN NOINHERIT NOCREATEDB NOCREATEROLE"
ROLES = {
    WORKER_ROLE: (
        "NOLOGIN NOSUPERUSER NOBYPASSRLS NOCREATEDB NOCREATEROLE",
        "NOLOGIN NOCREATEDB NOCREATEROLE",
    ),
    JOBS_LOGIN: (f"{_JOBS} NOREPLICATION NOBYPASSRLS CONNECTION LIMIT {JOBS_CONNECTION_LIMIT}", _JOBS),
}


def _role(name: str, made: str, altered: str) -> str:
    """Make the role with every limit, or set them on an existing one as far as this owner may;
    then refuse to go on if it is a superuser, replicates or skips row level security."""
    return f"""
        DO $$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{name}') THEN
                CREATE ROLE {name} {made};
            ELSE
                ALTER ROLE {name} {altered};
            END IF;
            IF EXISTS (
                SELECT 1 FROM pg_roles
                WHERE rolname = '{name}' AND (rolsuper OR rolreplication OR rolbypassrls)
            ) THEN
                RAISE EXCEPTION '% must not be a superuser, replicate or bypass row level security',
                    '{name}';
            END IF;
        END
        $$
        """


def _each_right(statement: str, role: str) -> str:
    """Run ``statement`` (a ``format()`` text given the right, its column list, the object's kind
    and its name) for every right ``role`` holds in this database: on a table, sequence or view,
    a column, a function or a schema."""
    return f"""
        DO $$
        DECLARE
            held record;
        BEGIN
            FOR held IN
                SELECT CASE c.relkind WHEN 'S' THEN 'SEQUENCE' ELSE 'TABLE' END AS kind,
                    c.oid::regclass::text AS name, '' AS columns, a.privilege_type AS right_
                FROM pg_class c, aclexplode(c.relacl) a WHERE a.grantee = '{role}'::regrole
                UNION ALL
                SELECT 'TABLE', t.attrelid::regclass::text, format('(%I)', t.attname), a.privilege_type
                FROM pg_attribute t, aclexplode(t.attacl) a WHERE a.grantee = '{role}'::regrole
                UNION ALL
                SELECT 'ROUTINE', p.oid::regprocedure::text, '', a.privilege_type
                FROM pg_proc p, aclexplode(p.proacl) a WHERE a.grantee = '{role}'::regrole
                UNION ALL
                SELECT 'SCHEMA', quote_ident(n.nspname), '', a.privilege_type
                FROM pg_namespace n, aclexplode(n.nspacl) a WHERE a.grantee = '{role}'::regrole
            LOOP
                EXECUTE format('{statement}', held.right_, held.columns, held.kind, held.name);
            END LOOP;
        END
        $$
        """


def _rights(role: str) -> str:
    """Every right ``role`` holds in this database, one row each: (where, what)."""
    grantee = f"a.grantee = '{role}'::regrole"
    return f"""
        SELECT 'relation ' || c.oid::regclass::text, a.privilege_type
        FROM pg_class c, aclexplode(c.relacl) a WHERE {grantee}
        UNION ALL
        SELECT 'column ' || t.attrelid::regclass::text || '.' || quote_ident(t.attname), a.privilege_type
        FROM pg_attribute t, aclexplode(t.attacl) a WHERE {grantee}
        UNION ALL
        SELECT 'function ' || p.oid::regprocedure::text, a.privilege_type
        FROM pg_proc p, aclexplode(p.proacl) a WHERE {grantee}
        UNION ALL
        SELECT 'schema ' || quote_ident(n.nspname), a.privilege_type
        FROM pg_namespace n, aclexplode(n.nspacl) a WHERE {grantee}
        UNION ALL
        SELECT 'database ' || quote_ident(d.datname), a.privilege_type
        FROM pg_database d, aclexplode(d.datacl) a WHERE d.datname = current_database() AND {grantee}
        UNION ALL
        SELECT 'type ' || y.oid::regtype::text, a.privilege_type
        FROM pg_type y, aclexplode(y.typacl) a WHERE {grantee}
        UNION ALL
        SELECT 'default privileges ' || f.defaclobjtype::text || ' of ' || f.defaclrole::regrole::text
            || ' in ' || f.defaclnamespace::regnamespace::text, a.privilege_type
        FROM pg_default_acl f, aclexplode(f.defaclacl) a WHERE {grantee}
        """


UPGRADE = [
    _role(WORKER_ROLE, *ROLES[WORKER_ROLE]),
    # The owner may switch into it, as into kaagaz_app (0001): the laptop's, CI's and tests' login.
    f"DO $$ BEGIN EXECUTE format('GRANT {WORKER_ROLE} TO %I', current_user); END $$",
    _role(JOBS_LOGIN, *ROLES[JOBS_LOGIN]),
    f"GRANT {WORKER_ROLE} TO {JOBS_LOGIN} WITH INHERIT FALSE, SET TRUE",
    _each_right(f"GRANT %s %s ON %s %s TO {WORKER_ROLE}", APP_ROLE),
    f"""
    DO $$
    DECLARE
        missing text;
    BEGIN
        SELECT string_agg(m.what || ' ' || m.privilege, ', ' ORDER BY m.what, m.privilege) INTO missing
        FROM (({_rights(APP_ROLE)}) EXCEPT ({_rights(WORKER_ROLE)})) m(what, privilege);
        IF missing IS NOT NULL THEN
            RAISE EXCEPTION '{WORKER_ROLE} lacks what {APP_ROLE} may do: %', missing;
        END IF;
    END
    $$
    """,
    f"""
    DO $$
    BEGIN
        IF EXISTS (
            SELECT 1 FROM pg_auth_members
            WHERE member = '{JOBS_LOGIN}'::regrole AND roleid <> '{WORKER_ROLE}'::regrole
        ) OR pg_has_role('{JOBS_LOGIN}', '{APP_ROLE}', 'MEMBER')
            OR pg_has_role('{JOBS_LOGIN}', 'kaagaz_operator', 'MEMBER')
            OR pg_has_role('{JOBS_LOGIN}', current_user, 'MEMBER')
            OR pg_has_role('kaagaz_api', '{WORKER_ROLE}', 'MEMBER')
            OR pg_has_role('kaagaz_ops', '{WORKER_ROLE}', 'MEMBER') THEN
            RAISE EXCEPTION '{JOBS_LOGIN} must reach only {WORKER_ROLE}, and no other login reach it';
        END IF;
    END
    $$
    """,
]

DOWNGRADE = [_each_right(f"REVOKE %s %s ON %s %s FROM {WORKER_ROLE}", WORKER_ROLE)]


def upgrade() -> None:
    for statement in UPGRADE:
        op.execute(statement)


def downgrade() -> None:
    for statement in DOWNGRADE:
        op.execute(statement)
