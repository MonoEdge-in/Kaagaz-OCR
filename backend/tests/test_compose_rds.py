"""Amazon RDS on a server (12 Nov 2026): opt-in through RDS_DATABASE_URL in the server's .env.

Read as text, like tests/test_compose_prod.py: Docker Compose itself renders the files in CI.
"""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
LOCAL = (
    "postgresql://${POSTGRES_USER:-kaagaz}:${POSTGRES_PASSWORD:-kaagaz}@postgres:5432/${POSTGRES_DB:-kaagaz}"
)


def test_rds_replaces_the_postgres_container_only_when_set_and_the_app_login_still_wins() -> None:
    base = (ROOT / "docker-compose.yml").read_text(encoding="utf-8")
    # api and worker: the app's limited login, else RDS, else the container, as today.
    assert f"    DATABASE_URL: ${{APP_DATABASE_URL:-${{RDS_DATABASE_URL:-{LOCAL}}}}}\n" in base
    # migrate: the owner on RDS, else the owner in the container.
    assert f"      DATABASE_URL: ${{RDS_DATABASE_URL:-{LOCAL}}}\n" in base
    # The address itself never sits in a container's environment.
    assert '\n    RDS_DATABASE_URL: "" ' in base


def test_the_nightly_dump_reads_rds_without_putting_its_password_on_a_command_line() -> None:
    script = (ROOT / "infra" / "ec2" / "pgdump.sh").read_text(encoding="utf-8")
    assert 'rds_url="$(env_value RDS_DATABASE_URL)"' in script
    # The address goes to python on stdin and to the client as PG* variables, never as an argument.
    assert '\' <<<"$rds_url")' in script
    assert "sys.stdin.read()" in script
    assert "-e PGHOST -e PGPORT -e PGUSER -e PGPASSWORD" in script
    # RDS is PostgreSQL 18.6: pg_dump refuses a newer server, so the client is 18, pinned.
    assert "\nRDS_CLIENT_IMAGE=pgvector/pgvector:0.8.7-pg18\n" in script
    assert '"$rds_url"' not in script.replace('<<<"$rds_url"', "").replace('[[ -n "$rds_url" ]]', "")
    assert '  rds pg_dump -Fc >"$partial"\n' in script
    assert "rds pg_dumpall -l postgres --roles-only --no-role-passwords | seal roles.sql" in script
    # Without it, the container's dump, exactly as before.
    assert 'exec -T postgres \\\n  pg_dump -U "$pg_user" -Fc "$pg_db" >"$partial"\n' in script


def test_the_alert_containers_never_get_the_rds_address() -> None:
    for name in ("disk-alert.sh", "ops-alert.sh"):
        script = (ROOT / "infra" / "ec2" / name).read_text(encoding="utf-8")
        assert "-e OPS_DATABASE_URL= -e RDS_DATABASE_URL= " in script, name
