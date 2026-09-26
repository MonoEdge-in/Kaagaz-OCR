"""Settings come from the environment, cover every app setting, and keep secrets hidden."""

from pathlib import Path

import pytest
from pydantic import ValidationError

from kaagaz_shared.settings import DatabaseSettings, Settings, get_settings

ROOT = Path(__file__).resolve().parents[1]

# Used only by the postgres container in docker-compose.yml, never by the app.
COMPOSE_ONLY = {"POSTGRES_USER", "POSTGRES_PASSWORD", "POSTGRES_DB"}

VALUES = {
    "DATABASE_URL": "postgresql+asyncpg://user:db-password@localhost:5432/db",
    "S3_ENDPOINT_URL": "http://storage:8333",
    "S3_PUBLIC_ENDPOINT_URL": "http://localhost:8333",
    "S3_ACCESS_KEY": "access",
    "S3_SECRET_KEY": "storage-secret",
    "S3_REGION": "us-east-1",
    "S3_BUCKET": "bucket",
    "CORS_ALLOWED_ORIGINS": '["http://localhost:8000"]',
    "TEMPORAL_ADDRESS": "temporal:7233",
    "TEMPORAL_NAMESPACE": "default",
    "TEMPORAL_UI_URL": "http://localhost:8233",
    "TASK_QUEUE_CPU": "cpu",
    "TASK_QUEUE_GPU": "gpu",
    "DEV_AUTH_SECRET": "auth-secret",
    "MAX_UPLOAD_MB": "50",
    "PLACEHOLDER_STEP_SECONDS": "3",
    "LOG_LEVEL": "INFO",
}


@pytest.fixture
def environment(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Every setting in the environment, and no .env file in the working directory."""
    monkeypatch.chdir(tmp_path)
    for name, value in VALUES.items():
        monkeypatch.setenv(name, value)


def example_setting_names() -> set[str]:
    lines = (ROOT / ".env.example").read_text(encoding="utf-8").splitlines()
    return {line.split("=", 1)[0] for line in lines if line and not line.startswith("#")}


def test_every_app_setting_in_env_example_is_a_field() -> None:
    fields = {name.upper() for name in Settings.model_fields}
    assert fields == example_setting_names() - COMPOSE_ONLY


@pytest.mark.usefixtures("environment")
def test_values_come_from_the_environment_with_their_types() -> None:
    settings = Settings()
    assert settings.s3_endpoint_url == "http://storage:8333"
    assert settings.cors_allowed_origins == ["http://localhost:8000"]
    assert settings.max_upload_mb == 50
    assert settings.placeholder_step_seconds == 3.0


@pytest.mark.usefixtures("environment")
def test_secrets_never_show_in_text() -> None:
    settings = Settings()
    shown = repr(settings) + str(settings)
    for secret in ("db-password", "storage-secret", "auth-secret"):
        assert secret not in shown
    assert settings.dev_auth_secret.get_secret_value() == "auth-secret"


def test_database_settings_need_only_the_database_url(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # Alembic and the database tests run in CI with DATABASE_URL and nothing else.
    monkeypatch.chdir(tmp_path)
    for name in VALUES:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("DATABASE_URL", VALUES["DATABASE_URL"])
    settings = DatabaseSettings()
    assert settings.database_url.get_secret_value() == VALUES["DATABASE_URL"]
    assert "db-password" not in repr(settings)
    with pytest.raises(ValidationError, match="s3_bucket"):
        Settings()


@pytest.mark.usefixtures("environment")
def test_a_missing_setting_stops_start_up(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("TEMPORAL_ADDRESS")
    with pytest.raises(ValidationError, match="temporal_address"):
        Settings()


@pytest.mark.usefixtures("environment")
def test_values_are_read_from_a_dot_env_file(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.delenv("S3_BUCKET")
    (tmp_path / ".env").write_text("S3_BUCKET=from-dot-env\n", encoding="utf-8")
    assert Settings().s3_bucket == "from-dot-env"


@pytest.mark.usefixtures("environment")
def test_get_settings_reads_once_per_process() -> None:
    get_settings.cache_clear()
    assert get_settings() is get_settings()
    get_settings.cache_clear()
