"""#479: the app's and the super admin's own database logins (0067), and where they reach.

The API and the workers open ``DATABASE_URL``: in Compose the app's login (``kaagaz_api``, from
``APP_DATABASE_URL``, a name only Compose reads) once ``.env`` sets it, for every backend service
but migrate, which keeps the owner's. ``OPS_DATABASE_URL`` (only the API's operator engine,
``kaagaz_ops``) falls back to ``DATABASE_URL`` while unset, so nothing changes on release day.
The workers never build the operator engine, and api and worker never get ``POSTGRES_PASSWORD``.
The workers' own login (``kaagaz_jobs``, #587, 0083) reaches the worker alone, from
``JOBS_DATABASE_URL``, together with its role (``DB_WORKER_ROLE``).
What the logins may do in PostgreSQL: tests/test_isolation_logins.py.
"""

import json
import shutil
import subprocess
from pathlib import Path
from typing import Any
from unittest import mock

import pytest

from kaagaz import cli, worker_main
from kaagaz.api import app as api_app
from kaagaz.db.engine import Database, OperatorDatabase, make_engine
from kaagaz.settings import Settings
from tests.settings_factory import make_settings
from tests.test_api_support import ApiFakeQueue, ApiFakeStorage, FakeAnswers
from tests.test_compose_prod import PROD, ROOT
from tests.test_settings import shipped

OWNER = "postgresql://kaagaz:owner-pass@localhost:1/kaagaz"
APP = "postgresql://kaagaz_api:app-pass@localhost:1/kaagaz"
OPS = "postgresql://kaagaz_ops:ops-pass@localhost:1/kaagaz"
JOBS = "postgresql://kaagaz_jobs:jobs-pass@localhost:1/kaagaz"
DOCKER = shutil.which("docker")


def test_the_operator_login_falls_back_to_database_url_while_unset_or_empty() -> None:
    for env in (shipped(), shipped(OPS_DATABASE_URL=None)):  # empty, then unset
        settings = Settings.from_env(env)
        assert settings.ops_database_url == settings.database_url


def test_the_operator_login_is_its_own_once_set() -> None:
    settings = Settings.from_env(shipped(DATABASE_URL=APP, OPS_DATABASE_URL=OPS))
    assert (settings.database_url, settings.ops_database_url) == (APP, OPS)


def _api_engines(settings: Settings) -> tuple[list[str], Any, Any]:
    """The URLs the API opened, the app, and the engine its operator part was given."""
    with (
        mock.patch.object(api_app, "make_engine", wraps=make_engine) as made,
        mock.patch.object(api_app, "OperatorDatabase", wraps=OperatorDatabase) as operator,
    ):
        app = api_app.create_app(
            settings, storage=ApiFakeStorage(), queue=ApiFakeQueue(), answers=FakeAnswers()
        )
    return [call.args[0] for call in made.call_args_list], app, operator.call_args.args[0]


def test_the_api_opens_the_app_login_and_the_operator_login_apart() -> None:
    urls, app, operator_engine = _api_engines(make_settings(database_url=APP, ops_database_url=OPS))
    assert urls == [APP, OPS]
    assert operator_engine is not app.state.kaagaz.db.engine


def test_on_release_day_the_api_keeps_one_engine_for_both() -> None:
    urls, app, operator_engine = _api_engines(make_settings(database_url=OWNER, ops_database_url=OWNER))
    assert urls == [OWNER]
    assert operator_engine is app.state.kaagaz.db.engine


def test_the_worker_opens_only_the_app_login() -> None:
    settings = make_settings(database_url=APP, ops_database_url=OPS)
    with (
        mock.patch.object(worker_main, "make_engine") as made,
        mock.patch.object(worker_main, "Database", side_effect=RuntimeError("stop here")),
        pytest.raises(RuntimeError, match="stop here"),
    ):
        worker_main._serve(settings)
    assert [call.args[0] for call in made.call_args_list] == [APP]


def test_the_worker_role_is_the_app_role_while_unset_or_empty_and_its_own_once_set() -> None:
    """#587 (0083): nothing changes until a server names the workers' role."""
    for env in (shipped(), shipped(DB_WORKER_ROLE=None)):  # empty, then unset
        assert Settings.from_env(env).db_worker_role == "kaagaz_app"
    assert Settings.from_env(shipped(DB_WORKER_ROLE="kaagaz_worker")).db_worker_role == "kaagaz_worker"


def test_the_worker_and_the_cli_take_the_worker_role_and_the_api_keeps_the_app_role() -> None:
    settings = make_settings(database_url=JOBS, ops_database_url=OPS, db_worker_role="kaagaz_worker")
    stop = RuntimeError("stop here")
    with (
        mock.patch.object(worker_main, "make_engine"),
        mock.patch.object(worker_main, "Database", side_effect=stop) as worker,
        mock.patch.object(cli, "make_engine"),
        mock.patch.object(cli, "Database", side_effect=stop) as tool,
    ):
        with pytest.raises(RuntimeError, match="stop here"):
            worker_main._serve(settings)
        with pytest.raises(RuntimeError, match="stop here"):
            cli.main(["refresh-models"], settings=settings, environ={})
    assert [worker.call_args.args[1], tool.call_args.args[1]] == ["kaagaz_worker"] * 2
    with mock.patch.object(api_app, "Database", wraps=Database) as api:
        api_app.create_app(settings, storage=ApiFakeStorage(), queue=ApiFakeQueue(), answers=FakeAnswers())
    assert api.call_args.args[1] == "kaagaz_app"


def _rendered(tmp_path: Path, **env: str) -> dict[str, dict[str, str]]:
    """Every service's environment, as Compose makes it from the two server files and a
    made-up ``.env`` (as CI's "Production compose renders" step)."""
    assert DOCKER is not None
    server = {
        "KAAGAZ_DOMAIN": "kaagaz.example.test",
        "ACME_EMAIL": "admin@example.test",
        "SEAWEEDFS_S3_CONFIG": "/home/ubuntu/kaagaz-secrets/s3.json",
        "STORAGE_ADMIN_ACCESS_KEY": "made-up-admin",  # migrate's, required on the server (#477)
        "STORAGE_ADMIN_SECRET_KEY": "made-up-admin-secret",
        # Required by docker-compose.prod.yml since Amazon SQS (#547).
        "QUEUE_URL": "https://sqs.ap-south-1.amazonaws.com/000000000000/kaagaz-jobs",
        "QUEUE_EXPRESS_URL": "https://sqs.ap-south-1.amazonaws.com/000000000000/kaagaz-jobs-express",
    }
    lines = [f"{name}={value}" for name, value in {**server, **env}.items()]
    (tmp_path / ".env").write_text("\n".join(lines) + "\n", encoding="utf-8")
    files = ["-f", str(ROOT / "docker-compose.yml"), "-f", str(ROOT / PROD)]
    command = [DOCKER, "compose", "--project-directory", str(tmp_path), *files, "config", "--format", "json"]
    done = subprocess.run(command, capture_output=True, check=True, timeout=60)  # noqa: S603 - fixed arguments
    services = json.loads(done.stdout)["services"]
    return {name: service.get("environment", {}) for name, service in services.items()}


MASTER = "a1b2c3d4e5f6a7b8c9d0e1f2"  # a made-up owner password, to find anywhere it leaks
OWNER_ENV = {
    "POSTGRES_USER": "kaagaz",
    "POSTGRES_PASSWORD": MASTER,
    "POSTGRES_DB": "kaagaz",
    "DATABASE_URL": f"postgresql://kaagaz:{MASTER}@localhost:5432/kaagaz",
}
OWNER_IN_DOCKER = f"postgresql://kaagaz:{MASTER}@postgres:5432/kaagaz"
APP_IN_DOCKER = "postgresql://kaagaz_api:app-pass@postgres:5432/kaagaz"
OPS_IN_DOCKER = "postgresql://kaagaz_ops:ops-pass@postgres:5432/kaagaz"


@pytest.mark.skipif(DOCKER is None, reason="needs the docker command (CI has it)")
def test_after_the_rollout_only_migrate_holds_the_master_password(tmp_path: Path) -> None:
    env = _rendered(tmp_path, **OWNER_ENV, APP_DATABASE_URL=APP_IN_DOCKER, OPS_DATABASE_URL=OPS_IN_DOCKER)
    assert env["migrate"]["DATABASE_URL"] == OWNER_IN_DOCKER
    for name in ("api", "worker"):
        assert MASTER not in json.dumps(env[name]), name
        assert not env[name].get("POSTGRES_PASSWORD"), name
        assert env[name]["DATABASE_URL"] == APP_IN_DOCKER, name
    assert env["api"]["OPS_DATABASE_URL"] == OPS_IN_DOCKER
    assert not env["worker"]["OPS_DATABASE_URL"]  # the workers never get the super admin's login
    assert not env["migrate"]["OPS_DATABASE_URL"]


@pytest.mark.skipif(DOCKER is None, reason="needs the docker command (CI has it)")
def test_on_release_day_every_backend_service_keeps_the_owner_login(tmp_path: Path) -> None:
    env = _rendered(tmp_path, **OWNER_ENV)
    backend = ("migrate", "api", "worker")
    assert {name: env[name]["DATABASE_URL"] for name in backend} == dict.fromkeys(backend, OWNER_IN_DOCKER)
    assert not env["api"]["OPS_DATABASE_URL"]  # unset: Settings falls back to DATABASE_URL
    assert not env["api"]["POSTGRES_PASSWORD"]  # read by no backend code


JOBS_PASSWORD = "9f8e7d6c5b4a39281706f5e4"  # a made-up workers' login password, to find where it leaks


@pytest.mark.skipif(DOCKER is None, reason="needs the docker command (CI has it)")
def test_the_workers_login_and_role_reach_only_the_worker_and_only_together(tmp_path: Path) -> None:
    """#587 (0083): JOBS_DATABASE_URL alone moves the workers to ``kaagaz_jobs`` and, with it, to
    ``kaagaz_worker``, so the login and the role never disagree; without it they keep the app's
    login and role. No other service gets the login, which can switch into the worker role."""
    env = _rendered(tmp_path, **OWNER_ENV, APP_DATABASE_URL=APP_IN_DOCKER, OPS_DATABASE_URL=OPS_IN_DOCKER)
    assert (env["worker"]["DATABASE_URL"], env["worker"]["DB_WORKER_ROLE"]) == (APP_IN_DOCKER, "")
    jobs = f"postgresql://kaagaz_jobs:{JOBS_PASSWORD}@postgres:5432/kaagaz"
    env = _rendered(tmp_path, **OWNER_ENV, APP_DATABASE_URL=APP_IN_DOCKER, JOBS_DATABASE_URL=jobs)
    assert (env["worker"]["DATABASE_URL"], env["worker"]["DB_WORKER_ROLE"]) == (jobs, "kaagaz_worker")
    assert [name for name in env if JOBS_PASSWORD in json.dumps(env[name])] == ["worker"]
    assert [name for name in env if env[name].get("DB_WORKER_ROLE")] == ["worker"]


ADMIN_SECRET = "f0e1d2c3b4a5968778695a4b"  # a made-up storage admin secret, to find where it leaks


@pytest.mark.skipif(DOCKER is None, reason="needs the docker command (CI has it)")
def test_master_password_only_in_migrate_and_postgres(tmp_path: Path) -> None:
    """Over every service compose renders, the master password and the storage admin key
    reach only the containers that use them (#477, #479), whatever else .env holds."""
    env = _rendered(
        tmp_path,
        **OWNER_ENV,
        APP_DATABASE_URL=APP_IN_DOCKER,
        STORAGE_ADMIN_ACCESS_KEY="kaagaz-admin-made-up",
        STORAGE_ADMIN_SECRET_KEY=ADMIN_SECRET,
    )
    assert len(env) == 9  # the eight of docker-compose.yml and caddy
    assert sorted(name for name in env if MASTER in json.dumps(env[name])) == ["migrate", "postgres"]
    assert [name for name in env if ADMIN_SECRET in json.dumps(env[name])] == ["migrate"]
    assert env["migrate"]["STORAGE_SECRET_KEY"] == ADMIN_SECRET
