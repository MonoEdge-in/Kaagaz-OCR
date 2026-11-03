"""#479: the app's and the super admin's own database logins (0067), and where they reach.

The API and the workers open ``DATABASE_URL``: in Compose the app's login (``kaagaz_api``, from
``APP_DATABASE_URL``, a name only Compose reads) once ``.env`` sets it, for every backend service
but migrate, which keeps the owner's. ``OPS_DATABASE_URL`` (only the API's operator engine,
``kaagaz_ops``) falls back to ``DATABASE_URL`` while unset, so nothing changes on release day.
The workers never build the operator engine, and api and worker never get ``POSTGRES_PASSWORD``.
What the two logins may do in PostgreSQL: tests/test_isolation_logins.py.
"""

import json
import shutil
import subprocess
from pathlib import Path
from typing import Any
from unittest import mock

import pytest

from kaagaz import worker_main
from kaagaz.api import app as api_app
from kaagaz.db.engine import OperatorDatabase, make_engine
from kaagaz.settings import Settings
from tests.settings_factory import make_settings
from tests.test_api_support import ApiFakeQueue, ApiFakeStorage, FakeAnswers
from tests.test_compose_prod import PROD, ROOT
from tests.test_settings import shipped

OWNER = "postgresql://kaagaz:owner-pass@localhost:1/kaagaz"
APP = "postgresql://kaagaz_api:app-pass@localhost:1/kaagaz"
OPS = "postgresql://kaagaz_ops:ops-pass@localhost:1/kaagaz"
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


def _rendered(tmp_path: Path, **env: str) -> dict[str, dict[str, str]]:
    """Each backend service's environment, as Compose makes it from the two server files and
    a made-up ``.env`` (as CI's "Production compose renders" step)."""
    assert DOCKER is not None
    server = {
        "KAAGAZ_DOMAIN": "kaagaz.example.test",
        "ACME_EMAIL": "admin@example.test",
        "SEAWEEDFS_S3_CONFIG": "/home/ubuntu/kaagaz-secrets/s3.json",
        "STORAGE_ADMIN_ACCESS_KEY": "made-up-admin",  # migrate's, required on the server (#477)
        "STORAGE_ADMIN_SECRET_KEY": "made-up-admin-secret",
    }
    lines = [f"{name}={value}" for name, value in {**server, **env}.items()]
    (tmp_path / ".env").write_text("\n".join(lines) + "\n", encoding="utf-8")
    files = ["-f", str(ROOT / "docker-compose.yml"), "-f", str(ROOT / PROD)]
    command = [DOCKER, "compose", "--project-directory", str(tmp_path), *files, "config", "--format", "json"]
    done = subprocess.run(command, capture_output=True, check=True, timeout=60)  # noqa: S603 - fixed arguments
    services = json.loads(done.stdout)["services"]
    return {name: services[name]["environment"] for name in ("migrate", "api", "worker")}


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
    assert {name: env[name]["DATABASE_URL"] for name in env} == dict.fromkeys(env, OWNER_IN_DOCKER)
    assert not env["api"]["OPS_DATABASE_URL"]  # unset: Settings falls back to DATABASE_URL
    assert not env["api"]["POSTGRES_PASSWORD"]  # read by no backend code
