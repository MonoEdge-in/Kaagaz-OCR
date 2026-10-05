"""Every setting the backend reads, in one place.

Values come from environment variables (``.env.example`` lists them all).
There are deliberately no defaults for business limits, retry counts, lease
times or model names: they are Vrushit's settings (PRD section 6), so a
missing one stops the process at start-up with its name, never later with a
guess. Tests build ``Settings`` directly (``tests/settings_factory.py``).

PROVISIONAL: every value marked so in ``.env.example`` waits for Vrushit.
"""

import math
import os
import urllib.parse
from collections.abc import Mapping
from dataclasses import dataclass, field
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

# AR1: the worker renews a job's lease at most once in this many seconds
# (kaagaz/jobs/worker.py), so a heartbeat can wait this long; each margin below adds it.
LEASE_RENEW_SECONDS = 15
# AR1: seconds to spare between one OCR request (the longest step without a
# heartbeat) and the job's lease, or a slow page outlives the lease and a second
# worker starts the same document.
_OCR_LEASE_MARGIN_SECONDS = 120 + LEASE_RENEW_SECONDS
# The Host values the ocr server accepts (ALLOWED_HOSTS in kaagaz/ocr_server/app.py,
# which must never import this module; a test keeps the two the same).
OCR_SERVER_HOSTS = frozenset({"ocr:8080", "localhost:8080", "127.0.0.1:8080"})
# AR1: seconds to spare between the longest model step without a heartbeat (one
# chat_json: a call and its repair retry; or one embedding batch or vision crop, a
# single call) and the job's lease.
_MODEL_LEASE_MARGIN_SECONDS = 60 + LEASE_RENEW_SECONDS
# The size of the pieces' vector(1024) column (migration 0001): EMBEDDING_DIM must match it.
EMBEDDING_DIM = 1024
# Council 5 (PROVISIONAL): an invoice window's reply needs about this many tokens for each
# line item, taking one line item per _WINDOW_LINE_CHARS characters of the window, plus
# _WINDOW_FIELD_TOKENS for its nine fields. The window must fit MODEL_WINDOW_MAX_TOKENS.
_WINDOW_LINE_CHARS = 50
_WINDOW_LINE_TOKENS = 50
_WINDOW_FIELD_TOKENS = 300


def window_for_reply_tokens(max_tokens: int) -> tuple[int, int]:
    """The invoice window (characters, overlap) that a reply of ``max_tokens``
    (MODEL_WINDOW_MAX_TOKENS) can answer: as many 50-character line items as fit
    after the fields, and a quarter of that as overlap, both in whole line items.
    2048 gives 1700 and 400; a bigger model is one setting (MODEL_WINDOW_MAX_TOKENS,
    with MODEL_NUM_CTX), and the windows grow with it. (0, 0) if nothing fits."""
    line_items = max(0, (max_tokens - _WINDOW_FIELD_TOKENS) // _WINDOW_LINE_TOKENS)
    chars = line_items * _WINDOW_LINE_CHARS
    overlap = (chars // 4) // _WINDOW_LINE_CHARS * _WINDOW_LINE_CHARS
    return chars, overlap


# The model's context window (ML amendment 5, council fix 7). Every call keeps room
# for its system prompt (tests keep each prompt under MAX_SYSTEM_PROMPT_TOKENS; the
# card's is about 700), a reply and its one repair retry, and CHAT_OVERHEAD_TOKENS
# for the chat template and the repair message; at least MIN_INPUT_TOKENS must be
# left for the document. All PROVISIONAL.
CHAT_OVERHEAD_TOKENS = 100
MAX_SYSTEM_PROMPT_TOKENS = 1000
MIN_INPUT_TOKENS = 1024
# Council 4 Oct 2026, PROVISIONAL: the question translation call's timeout when unset.
TRANSLATE_TIMEOUT_SECONDS = 20.0


def _time_zone(name: str) -> str:
    """TIMEZONE, checked at start-up: the invoice checks' today is the date there (PRD 2 ML 6(j))."""
    try:
        ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError):
        raise SettingsError("TIMEZONE must be a time zone name such as Asia/Kolkata") from None
    return name


class SettingsError(Exception):
    """A setting is missing or malformed. The message names the variable only."""


@dataclass(frozen=True, slots=True)
class Settings:
    # Database. The app switches to ``db_app_role`` in every transaction, a role
    # that is not a superuser, does not own the tables and has no BYPASSRLS, so
    # row level security always applies (docs/architecture.md).
    database_url: str = field(repr=False)  # it can hold a password
    db_app_role: str

    # File storage (S3 API: SeaweedFS on the laptop, Amazon S3 in AWS).
    storage_endpoint_url: str | None
    storage_region: str
    storage_bucket: str
    storage_access_key: str | None = field(repr=False)
    storage_secret_key: str | None = field(repr=False)

    # Queue (SQS API: ElasticMQ on the laptop, Amazon SQS in AWS).
    queue_endpoint_url: str | None
    queue_url: str
    queue_region: str
    queue_lease_seconds: int
    queue_wait_seconds: int
    job_max_attempts: int

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

    # Scan reader engine: "paddleocr_vl" (the ocr container at ocr_url), "rapidocr",
    # or "none" (scans are marked needs_reader). OCR pages are drawn at ocr_dpi in
    # memory, never stored; the stored page picture stays at page_image_dpi.
    ocr_engine: str
    ocr_url: str
    ocr_timeout_seconds: float
    ocr_dpi: int
    # Council 5 Oct 2026 (PROVISIONAL; kaagaz/reading/ocr_dpi.py): how each OCR page's DPI is
    # chosen. "fixed" (when unset) draws at ocr_dpi; "text_height" from the page's text height,
    # with ocr_dpi as the ceiling; "text_height_no_scan_cap" the same without the scan cap.
    ocr_dpi_rule: str

    # The page route (PRD 2 R2 to R4). A PDF page's text layer is kept when it has at
    # least text_layer_min_chars letters and digits and text_layer_min_good_ratio good
    # characters, and is not a scan (an image over scan_image_coverage of the page with
    # little text); otherwise it goes to OCR. A picture whose grey spread is under
    # blank_page_stddev (and with almost no dark ink) is blank, never sent to OCR. A
    # page under low_res_dpi is flagged low resolution, never refused.
    text_layer_min_chars: int
    text_layer_min_good_ratio: float
    scan_image_coverage: float
    blank_page_stddev: float
    low_res_dpi: int

    # Local AI through Ollama's HTTP API. model_enabled=false runs everything
    # without AI: search without meaning vectors, answers quote the evidence.
    model_enabled: bool
    model_base_url: str
    model_chat: str
    model_embed: str
    model_vision: str | None
    embedding_dim: int
    model_timeout_seconds: float
    # PRD 2 model budgets (ML amendment 5): the context window sent with every
    # call, the reply budgets for the document card, for each invoice window and
    # for an answer, and the worker's timeout for those calls. The card reads at
    # most card_max_chars of the document (D1).
    model_num_ctx: int
    model_card_max_tokens: int
    model_window_max_tokens: int
    model_answer_max_tokens: int
    model_extract_timeout_seconds: float
    card_max_chars: int
    # PRD 2 E2: an invoice is read in windows of this many characters, each overlapping
    # the one before by extract_window_overlap, at most extract_max_windows of them.
    extract_window_chars: int
    extract_window_overlap: int
    extract_max_windows: int

    # Sign-in. The TOTP key encrypts the admins' two-step secrets
    # (kaagaz.api.secret_box); it must differ from the session secret, so one
    # leaked value does not give away both. Lockout: after login_max_failures
    # wrong attempts in a row the account is locked for login_lock_minutes.
    session_secret: str = field(repr=False)
    session_hours: int
    cookie_secure: bool
    totp_encryption_key: str = field(repr=False)
    login_max_failures: int
    login_lock_minutes: int
    admin_session_minutes: int

    # Trash (PRD 1 F5): days a document stays in Trash before the worker deletes it for good.
    trash_days: int

    # Read again (PRD 2 X1, security A8): how many a customer may start in one hour.
    reprocess_per_hour: int

    # Search (PRD 3 V2): hnsw.ef_search, the meaning index's candidate list, 1 to 1000.
    # Search inside documents (Q2): at most this many documents in a reply, 1 to 100.
    search_ef: int
    search_max_documents: int

    # Answers.
    answer_max_evidence: int
    answer_min_evidence_score: float
    # Q4: the pieces on each side of an evidence piece also given to the draft, 0 to 3.
    answer_neighbours: int
    # H1: the question and answer pairs a follow-up is rewritten with, 0 (no rewrite) to 10.
    chat_history_turns: int
    # Council 4 Oct 2026 (PROVISIONAL): an all-Latin question whose hits are mostly in another
    # script is also searched as the model's translation into that script. Off unless "true";
    # the translation call has its own timeout (TRANSLATE_TIMEOUT_SECONDS when unset).
    answer_translate_question: bool
    model_translate_timeout_seconds: float
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
        queue_lease_seconds = env.integer("QUEUE_LEASE_SECONDS")
        ocr_timeout_seconds = env.number("OCR_TIMEOUT_SECONDS")
        if not (math.isfinite(ocr_timeout_seconds) and ocr_timeout_seconds > 0):
            raise SettingsError("OCR_TIMEOUT_SECONDS must be a number above 0")
        # The values mirror kaagaz.reading.ocr_dpi.DPI_RULES (tests/test_settings.py keeps them the same).
        ocr_dpi_rule = (
            "fixed"
            if env.optional("OCR_DPI_RULE") is None
            else env.choice("OCR_DPI_RULE", {"fixed", "text_height", "text_height_no_scan_cap"})
        )
        # Any other rule may read a page twice (at a lowered DPI, then the ceiling) between heartbeats.
        ocr_calls = 1 if ocr_dpi_rule == "fixed" else 2
        if ocr_calls * ocr_timeout_seconds + _OCR_LEASE_MARGIN_SECONDS > queue_lease_seconds:
            raise SettingsError(
                f"{'2 x ' if ocr_calls == 2 else ''}OCR_TIMEOUT_SECONDS + {_OCR_LEASE_MARGIN_SECONDS}"
                " must not be more than QUEUE_LEASE_SECONDS"
            )
        ocr_engine = env.choice("OCR_ENGINE", {"paddleocr_vl", "rapidocr", "none"})
        ocr_url = env.url("OCR_URL")
        if (
            ocr_engine == "paddleocr_vl"
            and urllib.parse.urlsplit(ocr_url).netloc.lower() not in OCR_SERVER_HOSTS
        ):
            raise SettingsError(
                "OCR_URL must name ocr:8080, localhost:8080 or 127.0.0.1:8080 (the ocr server's hosts)"
            )
        model_num_ctx = env.positive("MODEL_NUM_CTX")
        card_max_tokens = env.positive("MODEL_CARD_MAX_TOKENS")
        window_max_tokens = env.positive("MODEL_WINDOW_MAX_TOKENS")
        answer_max_tokens = env.positive("MODEL_ANSWER_MAX_TOKENS")
        for name, tokens in (
            ("MODEL_CARD_MAX_TOKENS", card_max_tokens),
            ("MODEL_WINDOW_MAX_TOKENS", window_max_tokens),
            ("MODEL_ANSWER_MAX_TOKENS", answer_max_tokens),
        ):
            left = model_num_ctx - 2 * tokens - MAX_SYSTEM_PROMPT_TOKENS - CHAT_OVERHEAD_TOKENS
            if left < MIN_INPUT_TOKENS:
                raise SettingsError(
                    f"{name} must leave at least {MIN_INPUT_TOKENS} tokens of MODEL_NUM_CTX for the document"
                )
        # Empty means "what MODEL_WINDOW_MAX_TOKENS can answer" (window_for_reply_tokens);
        # a value set here overrides it, and is still checked below.
        auto_chars, auto_overlap = window_for_reply_tokens(window_max_tokens)
        set_chars = env.optional_integer("EXTRACT_WINDOW_CHARS")
        set_overlap = env.optional_integer("EXTRACT_WINDOW_OVERLAP")
        window_chars = auto_chars if set_chars is None else set_chars
        window_overlap = auto_overlap if set_overlap is None else set_overlap
        if window_chars < 1:
            raise SettingsError("EXTRACT_WINDOW_CHARS must be at least 1 (or MODEL_WINDOW_MAX_TOKENS larger)")
        if window_overlap >= window_chars:
            # Without this the windows would never move forward (plan 6).
            raise SettingsError("EXTRACT_WINDOW_OVERLAP must be less than EXTRACT_WINDOW_CHARS")
        line_items = -(-window_chars // _WINDOW_LINE_CHARS)
        if line_items * _WINDOW_LINE_TOKENS + _WINDOW_FIELD_TOKENS > window_max_tokens:
            raise SettingsError(
                "EXTRACT_WINDOW_CHARS is too long for MODEL_WINDOW_MAX_TOKENS: a window needs about"
                " 50 reply tokens for every 50 characters, plus 300"
            )
        embedding_dim = env.integer("EMBEDDING_DIM")
        if embedding_dim != EMBEDDING_DIM:
            raise SettingsError(f"EMBEDDING_DIM must be {EMBEDDING_DIM}, the size of the vector column")
        extract_timeout_seconds = env.number("MODEL_EXTRACT_TIMEOUT_SECONDS")
        if not (math.isfinite(extract_timeout_seconds) and extract_timeout_seconds > 0):
            raise SettingsError("MODEL_EXTRACT_TIMEOUT_SECONDS must be a number above 0")
        if 2 * extract_timeout_seconds + _MODEL_LEASE_MARGIN_SECONDS > queue_lease_seconds:
            raise SettingsError(
                f"2 x MODEL_EXTRACT_TIMEOUT_SECONDS + {_MODEL_LEASE_MARGIN_SECONDS}"
                " must not be more than QUEUE_LEASE_SECONDS"
            )
        model_timeout_seconds = env.number("MODEL_TIMEOUT_SECONDS")
        if not (math.isfinite(model_timeout_seconds) and model_timeout_seconds > 0):
            raise SettingsError("MODEL_TIMEOUT_SECONDS must be a number above 0")
        if model_timeout_seconds + _MODEL_LEASE_MARGIN_SECONDS > queue_lease_seconds:
            raise SettingsError(
                f"MODEL_TIMEOUT_SECONDS + {_MODEL_LEASE_MARGIN_SECONDS}"
                " must not be more than QUEUE_LEASE_SECONDS"
            )
        translate_timeout = TRANSLATE_TIMEOUT_SECONDS
        if env.optional("MODEL_TRANSLATE_TIMEOUT_SECONDS") is not None:
            translate_timeout = env.number("MODEL_TRANSLATE_TIMEOUT_SECONDS")
        if not (math.isfinite(translate_timeout) and translate_timeout > 0):
            raise SettingsError("MODEL_TRANSLATE_TIMEOUT_SECONDS must be a number above 0")
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
            queue_lease_seconds=queue_lease_seconds,
            queue_wait_seconds=env.integer("QUEUE_WAIT_SECONDS"),
            job_max_attempts=env.positive("JOB_MAX_ATTEMPTS"),
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
            ocr_engine=ocr_engine,
            ocr_url=ocr_url,
            ocr_timeout_seconds=ocr_timeout_seconds,
            ocr_dpi=env.positive("OCR_DPI"),
            ocr_dpi_rule=ocr_dpi_rule,
            text_layer_min_chars=env.positive("TEXT_LAYER_MIN_CHARS"),
            text_layer_min_good_ratio=env.share("TEXT_LAYER_MIN_GOOD_RATIO"),
            scan_image_coverage=env.share("SCAN_IMAGE_COVERAGE"),
            blank_page_stddev=env.not_negative("BLANK_PAGE_STDDEV"),
            low_res_dpi=env.positive("LOW_RES_DPI"),
            model_enabled=env.boolean("MODEL_ENABLED"),
            model_base_url=env.text("MODEL_BASE_URL"),
            model_chat=env.text("MODEL_CHAT"),
            model_embed=env.text("MODEL_EMBED"),
            model_vision=env.optional("MODEL_VISION"),
            embedding_dim=embedding_dim,
            model_timeout_seconds=model_timeout_seconds,
            model_num_ctx=model_num_ctx,
            model_card_max_tokens=card_max_tokens,
            model_window_max_tokens=window_max_tokens,
            model_answer_max_tokens=answer_max_tokens,
            model_extract_timeout_seconds=extract_timeout_seconds,
            card_max_chars=env.positive("CARD_MAX_CHARS"),
            extract_window_chars=window_chars,
            extract_window_overlap=window_overlap,
            extract_max_windows=env.positive("EXTRACT_MAX_WINDOWS"),
            session_secret=session_secret,
            session_hours=env.integer("SESSION_HOURS"),
            cookie_secure=env.boolean("COOKIE_SECURE"),
            totp_encryption_key=totp_encryption_key,
            login_max_failures=env.positive("LOGIN_MAX_FAILURES"),
            login_lock_minutes=env.positive("LOGIN_LOCK_MINUTES"),
            admin_session_minutes=env.positive("ADMIN_SESSION_MINUTES"),
            trash_days=env.integer("TRASH_DAYS"),
            reprocess_per_hour=env.positive("REPROCESS_PER_HOUR"),
            search_ef=env.between("SEARCH_EF", 1, 1000),
            search_max_documents=env.between("SEARCH_MAX_DOCUMENTS", 1, 100),
            answer_max_evidence=env.integer("ANSWER_MAX_EVIDENCE"),
            answer_min_evidence_score=env.number("ANSWER_MIN_EVIDENCE_SCORE"),
            answer_neighbours=env.between("ANSWER_NEIGHBOURS", 0, 3),
            chat_history_turns=env.between("CHAT_HISTORY_TURNS", 0, 10),
            answer_translate_question=env.optional("ANSWER_TRANSLATE_QUESTION") is not None
            and env.boolean("ANSWER_TRANSLATE_QUESTION"),
            model_translate_timeout_seconds=translate_timeout,
            timezone=_time_zone(env.text("TIMEZONE")),
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

    def url(self, name: str) -> str:
        value = self.text(name)
        if urllib.parse.urlsplit(value).scheme not in {"http", "https"}:
            raise SettingsError(f"{name} must be an http or https URL")
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

    def between(self, name: str, low: int, high: int) -> int:
        number = self.integer(name)
        if not low <= number <= high:
            raise SettingsError(f"{name} must be between {low} and {high}")
        return number

    def optional_integer(self, name: str) -> int | None:
        return None if self.optional(name) is None else self.integer(name)

    def number(self, name: str) -> float:
        value = self.text(name)
        try:
            return float(value)
        except ValueError:
            raise SettingsError(f"{name} must be a number") from None

    def share(self, name: str) -> float:
        """A number above 0 and at most 1."""
        value = self.number(name)
        if not (math.isfinite(value) and 0 < value <= 1):
            raise SettingsError(f"{name} must be a number above 0 and at most 1")
        return value

    def not_negative(self, name: str) -> float:
        value = self.number(name)
        if not (math.isfinite(value) and value >= 0):
            raise SettingsError(f"{name} must be a number of 0 or more")
        return value

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
