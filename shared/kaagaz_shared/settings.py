"""Every setting the API and the worker read, loaded from the environment and `.env`.

The values live in `.env` (copied from `.env.example`), never in code, so moving to
AWS is a settings change rather than a code change. Nothing here has a default: a
missing setting stops the app at start-up instead of quietly pointing at the wrong
host.

Secrets are `SecretStr`, so they print as `**********` in logs and error messages.
Read one with `.get_secret_value()` only where it's handed to the library that needs it.
"""

from functools import lru_cache

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # Database
    database_url: SecretStr

    # File storage (SeaweedFS on the laptop, Amazon S3 in AWS)
    s3_endpoint_url: str
    s3_public_endpoint_url: str
    s3_access_key: str
    s3_secret_key: SecretStr
    s3_region: str
    s3_bucket: str
    cors_allowed_origins: list[str]

    # Workflow engine
    temporal_address: str
    temporal_namespace: str
    temporal_ui_url: str
    task_queue_cpu: str
    task_queue_gpu: str

    # API
    dev_auth_secret: SecretStr
    max_upload_mb: int
    placeholder_step_seconds: float
    log_level: str


@lru_cache
def get_settings() -> Settings:
    """The settings, read once per process."""
    return Settings()
