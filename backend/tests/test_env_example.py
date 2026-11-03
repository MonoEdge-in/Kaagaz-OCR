"""#246 G6: every setting the backend reads is in .env.example, and a new one has a default.

The server's .env is written by hand and the deploy runs unattended. A new setting the code
requires stops the new api and worker at start-up, AFTER its migration has run (the deploy
migrates first): the health check fails and the release is rolled back. So the settings that
stop start-up when missing are frozen at today's list; a new setting must ship with a safe
default in kaagaz/settings.py (and its value in .env.example), so an older .env still starts.
"""

import re
from pathlib import Path

from kaagaz.settings import Settings, SettingsError
from tests.test_settings import shipped

BACKEND = Path(__file__).resolve().parents[1]
ENV_EXAMPLE = BACKEND.parent / ".env.example"
# Read only by docker compose (the database container and the AWS CLI), never by the backend.
NOT_BACKEND = {
    "POSTGRES_USER",
    "POSTGRES_PASSWORD",
    "POSTGRES_DB",
    "AWS_ACCESS_KEY_ID",
    "AWS_SECRET_ACCESS_KEY",
    # migrate's storage admin key, read by docker-compose.yml only (#477).
    "STORAGE_ADMIN_ACCESS_KEY",
    "STORAGE_ADMIN_SECRET_KEY",
}
# The settings with no default on 27 Oct 2026: business limits, retry counts, lease times and
# model names are Vrushit's (kaagaz/settings.py). Never add to this list: give the new
# setting a default instead.
REQUIRED = {
    "ADMIN_SESSION_MINUTES",
    "ALLOWED_TYPES",
    "ANSWER_MAX_EVIDENCE",
    "ANSWER_MIN_EVIDENCE_SCORE",
    "ANSWER_NEIGHBOURS",
    "BLANK_PAGE_STDDEV",
    "CARD_MAX_CHARS",
    "CHAT_HISTORY_TURNS",
    "COOKIE_SECURE",
    "CPU_WORKERS",
    "CSV_MAX_CELLS",
    "DATABASE_URL",
    "DB_APP_ROLE",
    "EMBEDDING_DIM",
    "EXTRACT_MAX_WINDOWS",
    "JOB_MAX_ATTEMPTS",
    "LOGIN_LOCK_MINUTES",
    "LOGIN_MAX_FAILURES",
    "LOG_LEVEL",
    "LOW_RES_DPI",
    "MAX_CHECK_CROPS",
    "MAX_IMAGE_MB",
    "MAX_IMAGE_PIXELS",
    "MAX_PAGES",
    "MAX_SHEET_MB",
    "MAX_UPLOAD_MB",
    "MODEL_ANSWER_MAX_TOKENS",
    "MODEL_CARD_MAX_TOKENS",
    "MODEL_EMBED",
    "MODEL_ENABLED",
    "MODEL_EXTRACT_TIMEOUT_SECONDS",
    "MODEL_RPM",
    "MODEL_TIMEOUT_SECONDS",
    "OCR_DPI",
    "OCR_ENGINE",
    "OCR_MEDIA_RESOLUTION",
    "OCR_PAGE_CONCURRENCY",
    "OCR_RETRY_MEDIA_RESOLUTION",
    "OCR_RETRY_THINKING_LEVEL",
    "OCR_TIMEOUT_SECONDS",
    "OPENROUTER_BASE_URL",
    "PAGE_IMAGE_DPI",
    "QUEUE_LEASE_SECONDS",
    "QUEUE_REGION",
    "QUEUE_URL",
    "QUEUE_WAIT_SECONDS",
    "RATE_LIMIT_PER_MINUTE",
    "READER_OUTAGE_FALLBACK_MINUTES",
    "REPROCESS_PER_HOUR",
    "SCANNER",
    "SCANNER_TIMEOUT_SECONDS",
    "SCAN_IMAGE_COVERAGE",
    "SEARCH_EF",
    "SEARCH_MAX_DOCUMENTS",
    "SESSION_HOURS",
    "SESSION_SECRET",
    "STORAGE_BUCKET",
    "STORAGE_REGION",
    "TEXT_LAYER_MIN_CHARS",
    "TEXT_LAYER_MIN_GOOD_RATIO",
    "TIMEZONE",
    "TOTP_ENCRYPTION_KEY",
    "TRASH_DAYS",
    "XLSX_MAX_CELLS",
    "XLSX_MAX_ENTRIES",
    "XLSX_MAX_MEMBER_BYTES",
    "XLSX_MAX_TEXT_CHARS",
    "XLSX_MAX_TOTAL_BYTES",
}


def _read_by_settings() -> set[str]:
    """Every variable name kaagaz/settings.py reads (``env.<kind>("NAME" ...)``)."""
    source = (BACKEND / "kaagaz" / "settings.py").read_text(encoding="utf-8")
    return set(re.findall(r'\benv\.\w+\(\s*"([A-Z][A-Z0-9_]*)"', source))


def _listed() -> set[str]:
    text = ENV_EXAMPLE.read_text(encoding="utf-8")
    return set(re.findall(r"^([A-Z][A-Z0-9_]*)=", text, re.MULTILINE))


def test_every_setting_the_backend_reads_is_in_env_example() -> None:
    read = _read_by_settings()
    assert len(read) > 100  # the pattern still finds them
    assert sorted(read - _listed()) == []
    assert sorted(_listed() - read - NOT_BACKEND) == []  # and nothing stale is left there


def test_only_the_frozen_list_stops_start_up_when_missing() -> None:
    """A setting is required when the shipped .env.example without it does not start."""
    required = set()
    for name in shipped():
        try:
            Settings.from_env(shipped(**{name: None}))
        except SettingsError:
            required.add(name)
    new = sorted(required - REQUIRED)
    assert new == [], (
        f"give {new} a safe default in kaagaz/settings.py: an older server .env must still start"
    )
    assert sorted(REQUIRED - required) == []  # one got a default: take it off the list too
