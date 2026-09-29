"""Every setting the backend reads, in one place.

Values come from environment variables (``.env.example`` lists them all).
There are deliberately no defaults for business limits, retry counts, lease
times or model names: they are Vrushit's settings (PRD section 6), so a
missing one stops the process at start-up with its name, never later with a
guess. Tests build ``Settings`` directly (``tests/settings_factory.py``).

PROVISIONAL: every value marked so in ``.env.example`` waits for Vrushit.
"""

import os
from collections.abc import Mapping
from dataclasses import dataclass


class SettingsError(Exception):
    """A setting is missing or malformed. The message names the variable only."""


@dataclass(frozen=True, slots=True)
class Settings:
    # Database. The app switches to ``db_app_role`` in every transaction, a role
    # that is not a superuser, does not own the tables and has no BYPASSRLS, so
    # row level security always applies (docs/architecture.md).
    database_url: str
    db_app_role: str

    # File storage (S3 API: SeaweedFS on the laptop, Amazon S3 in AWS).
    storage_endpoint_url: str | None
    storage_region: str
    storage_bucket: str
    storage_access_key: str | None
    storage_secret_key: str | None

    # Queue (SQS API: ElasticMQ on the laptop, Amazon SQS in AWS).
    queue_endpoint_url: str | None
    queue_url: str
    queue_region: str
    queue_lease_seconds: int
    queue_wait_seconds: int

    # Virus scanner: "clamav" (clamd over TCP) or "eicar_dev" (laptop only:
    # finds the EICAR test file and nothing else; never use it with real files).
    scanner: str
    clamav_host: str | None
    clamav_port: int | None
    scanner_timeout_seconds: float

    # Upload limits, by file type (PRD 1, U2). max_upload_bytes is also the PDF limit.
    max_upload_bytes: int
    max_image_bytes: int  # PNG, JPEG, TIFF
    max_sheet_bytes: int  # XLSX, CSV
    allowed_types: frozenset[str]

    # Reader limits.
    csv_max_cells: int
    xlsx_max_entries: int
    xlsx_max_member_bytes: int
    xlsx_max_total_bytes: int
    xlsx_max_cells: int
    xlsx_max_text_chars: int
    max_pages: int
    page_image_dpi: int
    max_image_pixels: int

    # Scan reader engine: "rapidocr" or "none" (scans are marked needs_reader).
    ocr_engine: str

    # Local AI through Ollama's HTTP API. model_enabled=false runs everything
    # without AI: search without meaning vectors, answers quote the evidence.
    model_enabled: bool
    model_base_url: str
    model_chat: str
    model_embed: str
    model_vision: str | None
    embedding_dim: int
    model_timeout_seconds: float

    # Sign-in. The TOTP key encrypts the admins' two-step secrets
    # (kaagaz.api.secret_box); it must differ from the session secret, so one
    # leaked value does not give away both. Lockout: after login_max_failures
    # wrong attempts in a row the account is locked for login_lock_minutes.
    session_secret: str
    session_hours: int
    cookie_secure: bool
    totp_encryption_key: str
    login_max_failures: int
    login_lock_minutes: int
    admin_session_minutes: int

    # Trash (PRD 1 F5): days a document stays in Trash before the worker deletes it for good.
    trash_days: int

    # Answers.
    answer_max_evidence: int
    answer_min_evidence_score: float
    timezone: str

    # API.
    rate_limit_per_minute: int
    log_level: str

    @classmethod
    def from_env(cls, environ: Mapping[str, str] | None = None) -> "Settings":
        env = _Env(os.environ if environ is None else environ)
        session_secret = env.secret("SESSION_SECRET")
        totp_encryption_key = env.secret("TOTP_ENCRYPTION_KEY")
        if totp_encryption_key == session_secret:
            raise SettingsError("TOTP_ENCRYPTION_KEY must differ from SESSION_SECRET")
        return cls(
            database_url=env.text("DATABASE_URL"),
            db_app_role=env.text("DB_APP_ROLE"),
            storage_endpoint_url=env.optional("STORAGE_ENDPOINT_URL"),
            storage_region=env.text("STORAGE_REGION"),
            storage_bucket=env.text("STORAGE_BUCKET"),
            storage_access_key=env.optional("STORAGE_ACCESS_KEY"),
            storage_secret_key=env.optional("STORAGE_SECRET_KEY"),
            queue_endpoint_url=env.optional("QUEUE_ENDPOINT_URL"),
            queue_url=env.text("QUEUE_URL"),
            queue_region=env.text("QUEUE_REGION"),
            queue_lease_seconds=env.integer("QUEUE_LEASE_SECONDS"),
            queue_wait_seconds=env.integer("QUEUE_WAIT_SECONDS"),
            scanner=env.choice("SCANNER", {"clamav", "eicar_dev"}),
            clamav_host=env.optional("CLAMAV_HOST"),
            clamav_port=env.optional_integer("CLAMAV_PORT"),
            scanner_timeout_seconds=env.number("SCANNER_TIMEOUT_SECONDS"),
            max_upload_bytes=env.integer("MAX_UPLOAD_MB") * 1024 * 1024,
            max_image_bytes=env.integer("MAX_IMAGE_MB") * 1024 * 1024,
            max_sheet_bytes=env.integer("MAX_SHEET_MB") * 1024 * 1024,
            allowed_types=env.words("ALLOWED_TYPES"),
            csv_max_cells=env.integer("CSV_MAX_CELLS"),
            xlsx_max_entries=env.integer("XLSX_MAX_ENTRIES"),
            xlsx_max_member_bytes=env.integer("XLSX_MAX_MEMBER_BYTES"),
            xlsx_max_total_bytes=env.integer("XLSX_MAX_TOTAL_BYTES"),
            xlsx_max_cells=env.integer("XLSX_MAX_CELLS"),
            xlsx_max_text_chars=env.integer("XLSX_MAX_TEXT_CHARS"),
            max_pages=env.integer("MAX_PAGES"),
            page_image_dpi=env.integer("PAGE_IMAGE_DPI"),
            max_image_pixels=env.integer("MAX_IMAGE_PIXELS"),
            ocr_engine=env.choice("OCR_ENGINE", {"rapidocr", "none"}),
            model_enabled=env.boolean("MODEL_ENABLED"),
            model_base_url=env.text("MODEL_BASE_URL"),
            model_chat=env.text("MODEL_CHAT"),
            model_embed=env.text("MODEL_EMBED"),
            model_vision=env.optional("MODEL_VISION"),
            embedding_dim=env.integer("EMBEDDING_DIM"),
            model_timeout_seconds=env.number("MODEL_TIMEOUT_SECONDS"),
            session_secret=session_secret,
            session_hours=env.integer("SESSION_HOURS"),
            cookie_secure=env.boolean("COOKIE_SECURE"),
            totp_encryption_key=totp_encryption_key,
            login_max_failures=env.positive("LOGIN_MAX_FAILURES"),
            login_lock_minutes=env.positive("LOGIN_LOCK_MINUTES"),
            admin_session_minutes=env.positive("ADMIN_SESSION_MINUTES"),
            trash_days=env.integer("TRASH_DAYS"),
            answer_max_evidence=env.integer("ANSWER_MAX_EVIDENCE"),
            answer_min_evidence_score=env.number("ANSWER_MIN_EVIDENCE_SCORE"),
            timezone=env.text("TIMEZONE"),
            rate_limit_per_minute=env.integer("RATE_LIMIT_PER_MINUTE"),
            log_level=env.text("LOG_LEVEL"),
        )


class _Env:
    def __init__(self, environ: Mapping[str, str]) -> None:
        self._environ = environ

    def optional(self, name: str) -> str | None:
        value = self._environ.get(name, "").strip()
        return value or None

    def text(self, name: str) -> str:
        value = self.optional(name)
        if value is None:
            raise SettingsError(f"{name} is not set")
        return value

    def secret(self, name: str) -> str:
        value = self.text(name)
        if len(value) < 32:
            raise SettingsError(f"{name} must be at least 32 characters")
        return value

    def integer(self, name: str) -> int:
        value = self.text(name)
        try:
            number = int(value)
        except ValueError:
            raise SettingsError(f"{name} must be a whole number") from None
        if number < 0:
            raise SettingsError(f"{name} must not be negative")
        return number

    def positive(self, name: str) -> int:
        number = self.integer(name)
        if number < 1:
            raise SettingsError(f"{name} must be at least 1")
        return number

    def optional_integer(self, name: str) -> int | None:
        return None if self.optional(name) is None else self.integer(name)

    def number(self, name: str) -> float:
        value = self.text(name)
        try:
            return float(value)
        except ValueError:
            raise SettingsError(f"{name} must be a number") from None

    def boolean(self, name: str) -> bool:
        value = self.text(name).lower()
        if value not in {"true", "false"}:
            raise SettingsError(f"{name} must be true or false")
        return value == "true"

    def choice(self, name: str, allowed: set[str]) -> str:
        value = self.text(name)
        if value not in allowed:
            raise SettingsError(f"{name} must be one of {sorted(allowed)}")
        return value

    def words(self, name: str) -> frozenset[str]:
        words = frozenset(part.strip() for part in self.text(name).split(",") if part.strip())
        if not words:
            raise SettingsError(f"{name} must list at least one value")
        return words
