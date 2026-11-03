"""Every setting the backend reads, in one place.

Values come from environment variables (``.env.example`` lists them all).
There are deliberately no defaults for business limits, retry counts, lease
times or model names: they are Vrushit's settings (PRD section 6), so a
missing one stops the process at start-up with its name, never later with a
guess. Tests build ``Settings`` directly (``tests/settings_factory.py``).

A variable this module no longer reads is ignored, so an older ``.env`` still
starts (the deploy runs unattended). A value that names something removed stops
start-up by name: OCR_ENGINE's old layout reader, or with MODEL_ENABLED a
MODEL_CHAT or MODEL_EMBED that is not an OpenRouter model ID (PRD 5 section 7).

PROVISIONAL: every value marked so in ``.env.example`` waits for Vrushit.
"""

import math
import os
import re
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
# PRD 5 G6: the most Gemini calls one page can take between heartbeats: the read, and when
# the reply is cut off at the output limit, its top and bottom halves.
_GEMINI_PAGE_CALLS = 3
# PRD 5 G2 and F5: the media_resolution and thinking_level a page is read with.
MEDIA_RESOLUTIONS = frozenset({"low", "medium", "high", "ultra_high"})
THINKING_LEVELS = frozenset({"minimal", "low", "medium", "high"})
# A model with no thinking levels (catalogue ``efforts`` empty; Tushar, 18 Oct 2026) is read
# and asked at this "level": the request carries no ``reasoning`` field at all.
NO_THINKING = "none"
# How a page's media resolution reaches the model through OpenRouter (council 8 Oct 2026).
# "detail": the image part's image_url.detail, which takes low or high only, so only those
# two levels can be sent: OCR_MEDIA_RESOLUTION and OCR_RETRY_MEDIA_RESOLUTION refuse medium
# and ultra_high at start-up, by name, and never send another level instead. "passthrough"
# (Google's own media_resolution level sent through, all four levels) is the one other
# value, for when verify-models shows OpenRouter passes it on; it is not built until then.
MEDIA_RESOLUTION_SHAPE = "detail"
SENDABLE_MEDIA_RESOLUTIONS = {"detail": frozenset({"low", "high"})}
# An OpenRouter model ID: author/slug, such as google/gemini-3.1-pro-preview. A ":variant"
# (":free", ":floor", ...) is refused: it changes the routing and the data policy. Each part
# is 1 to 128 characters and starts with a letter or a digit, so "." and ".." are never a
# part (the ID goes in a URL path). 0023's CHECKs hold the same pattern.
MODEL_ID = re.compile(r"[a-z0-9][a-z0-9-]{0,127}/[A-Za-z0-9][A-Za-z0-9._-]{0,127}")
# An OpenRouter provider slug, such as google-vertex or azure (model choice, council 11 Oct 2026).
PROVIDER_SLUG = re.compile(r"[a-z0-9-]{1,64}")
# The provider every call used before model choice, and OPENROUTER_PROVIDERS when unset.
DEFAULT_PROVIDER = "google-vertex"
# OCR_MODEL and MODEL_CHAT when unset (Tushar, 16 Oct 2026): with no super admin setting,
# Gemini 3.8 Flash scans and answers; embeddings keep MODEL_EMBED.
DEFAULT_MODEL = "google/gemini-3.8-flash"
# The model vendors (the part of a model id before "/") the operator may allow, a ceiling
# apart from the providers (features 25-27, council 15 Oct 2026, S1). Tushar decided on 15
# Oct 2026 that Sonnet 5.5 is allowed by default, so the default is google and anthropic;
# Tushar, 18 Oct 2026: qwen and meta-llama too (Qwen VL and Llama models for organisations).
# A server with OPENROUTER_MODEL_VENDORS set keeps its own list.
DEFAULT_MODEL_VENDORS = frozenset({"google", "anthropic", "qwen", "meta-llama"})
MODEL_VENDOR = re.compile(r"[a-z0-9][a-z0-9-]{0,127}")  # MODEL_ID's part before "/"
# OPENROUTER_BASE_URL may be plain http only on these hosts (a fake server in tests, or the
# exit test's closed port); anywhere else the key would cross the network in the clear.
_LOOPBACK_HOSTS = frozenset({"localhost", "127.0.0.1", "::1"})
# The key goes in a request header: printable ASCII with no space, so it can never split one.
_API_KEY = re.compile(r"[\x21-\x7e]{1,256}")
# AR1: seconds to spare between the longest model step without a heartbeat (one
# chat_json: a call and its repair retry; or one embedding batch, a single call) and the
# job's lease.
_MODEL_LEASE_MARGIN_SECONDS = 60 + LEASE_RENEW_SECONDS
# The size of the pieces' vector(1024) column (migration 0001): EMBEDDING_DIM must match it.
EMBEDDING_DIM = 1024
# Council 5 (PROVISIONAL): an invoice window's reply needs about this many tokens for each
# line item, taking one line item per _WINDOW_LINE_CHARS characters of the window, plus
# _WINDOW_FIELD_TOKENS for its nine fields. The window must fit INVOICE_REPLY_TOKENS.
_WINDOW_LINE_CHARS = 50
_WINDOW_LINE_TOKENS = 50
_WINDOW_FIELD_TOKENS = 300
# PRD 5 M1: an invoice window's reply budget, capped as it was (2048), so the windows are
# 1700 characters with a 400 character overlap unless EXTRACT_WINDOW_* say otherwise.
INVOICE_REPLY_TOKENS = 2048


def window_for_reply_tokens(max_tokens: int) -> tuple[int, int]:
    """The invoice window (characters, overlap) that a reply of ``max_tokens``
    (INVOICE_REPLY_TOKENS) can answer: as many 50-character line items as fit after the
    fields, and a quarter of that as overlap, both in whole line items. 2048 gives 1700
    and 400. (0, 0) if nothing fits."""
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


def is_gemini_model(name: str) -> bool:
    """A Google Gemini model ID on OpenRouter, such as google/gemini-3.1-pro-preview or
    google/gemini-embedding-2 (PRD 5 M1, M5; council 8 Oct 2026): calling it goes through
    OpenRouter and needs OPENROUTER_API_KEY. The name stays: the reader's tracks use it."""
    return name.startswith("google/gemini-")


def valid_model_id(name: str) -> bool:
    """``name`` is an OpenRouter model ID (``MODEL_ID``): author/slug, with no ":variant"."""
    return MODEL_ID.fullmatch(name) is not None


def _openrouter_base_url(value: str) -> str:
    """OPENROUTER_BASE_URL: https, or http on a loopback host only; no user, query or
    fragment, since the key is sent to it. Kept without a trailing slash."""
    parts = urllib.parse.urlsplit(value)
    if parts.username is not None or parts.password is not None or parts.query or parts.fragment:
        raise SettingsError("OPENROUTER_BASE_URL must have no user, password, query or fragment")
    return _https_url(value, "OPENROUTER_BASE_URL")


def _app_url(value: str) -> str:
    """APP_URL: an https origin (http only on a loopback host), with no user, path, query or
    fragment, since every emailed link starts with it. Kept without a trailing slash."""
    return _https_url(value, "APP_URL", "")


def _redirect_uri(value: str, provider: str) -> str:
    """{PROVIDER}_REDIRECT_URI (#457): where the provider sends the browser back, exactly as
    registered with it. https (http only on a loopback host), no user, query or fragment, and
    the path /api/auth/oidc/<provider>/callback."""
    return _https_url(value, f"{provider.upper()}_REDIRECT_URI", f"/api/auth/oidc/{provider}/callback")


_LOOPBACK_NOTE = " (http only for localhost, 127.0.0.1 or ::1)"
# Each setting's own words when ``_https_url`` refuses it (a redirect URI's name its path).
_URL_MESSAGES = {
    "OPENROUTER_BASE_URL": "OPENROUTER_BASE_URL must be an https URL" + _LOOPBACK_NOTE,
    "APP_URL": "APP_URL must be an https origin such as https://kaagaz.example.com" + _LOOPBACK_NOTE,
}


def _https_url(value: str, name: str, path: str | None = None) -> str:
    """``value`` when it is https (http only on a loopback host) with no user, password, query
    or fragment and, when ``path`` is given, exactly that path (``""``: an origin, "/" too);
    kept without a trailing slash. Else ``SettingsError`` in ``name``'s own words."""
    parts = urllib.parse.urlsplit(value)
    host = parts.hostname or ""
    secure = parts.scheme == "https" and bool(host)
    paths = {"", "/"} if path == "" else {path}
    if (
        parts.username is not None
        or parts.password is not None
        or parts.query
        or parts.fragment
        or (path is not None and parts.path not in paths)
        or not (secure or (parts.scheme == "http" and host in _LOOPBACK_HOSTS))
    ):
        raise SettingsError(
            _URL_MESSAGES.get(name) or f"{name} must be an https URL ending {path}" + _LOOPBACK_NOTE
        )
    return value.rstrip("/")


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

    # Scan reader engine: "gemini" (PRD 5, the model in ocr_model), "rapidocr", or "none"
    # (scans are marked needs_reader). OCR pages are drawn at ocr_dpi in memory, never
    # stored; the stored page picture stays at page_image_dpi.
    ocr_engine: str
    ocr_timeout_seconds: float
    ocr_dpi: int
    # Council 5 Oct 2026 (PROVISIONAL; kaagaz/reading/ocr_dpi.py): how each OCR page's DPI is
    # chosen. "fixed" (when unset) draws at ocr_dpi; "text_height" from the page's text height,
    # with ocr_dpi as the ceiling; "text_height_no_scan_cap" the same without the scan cap.
    ocr_dpi_rule: str

    # PRD 5, the Gemini reader, through OpenRouter (council 8 Oct 2026). OCR_ENGINE=gemini
    # reads every ocr page with ocr_model, the OpenRouter model ID in .env, unless the
    # operator chose another model for the organisation (model choice, council 11 Oct 2026,
    # kaagaz.db.models.resolve), through openrouter_base_url with openrouter_api_key: a
    # secret repr never shows, needed only when a model is called (OCR_ENGINE=gemini, or
    # MODEL_ENABLED). A page is sent at ocr_media_resolution with
    # ocr_thinking_level (G2); "Read this page again" (F5) uses the retry pair. Both
    # resolutions are limited to what MEDIA_RESOLUTION_SHAPE can send. ocr_page_concurrency
    # pages of a document are read at a time and RapidOCR work runs on cpu_workers threads
    # (G8). At most max_check_crops values of a document are read again by the CPU checker
    # (V1). Production RPM admission is shared by API and worker processes in PostgreSQL
    # (migration 0046); model_rpm is its 1,200 RPM ceiling and the isolated-tool fallback.
    # Pages are read locally once the reader has been down for
    # reader_outage_fallback_minutes (Q7).
    # Fast scan (council 16 Oct 2026): a Dynamic document of at most careful_early_max_pages
    # pages starts each page's Careful read with its first read (0: never); optional, 0 since
    # Cost saving (16 Oct 2026): the early read is a whole careful read paid for every page.
    ocr_model: str
    openrouter_api_key: str | None = field(repr=False)
    openrouter_base_url: str
    # Model choice (council 11 Oct 2026): the OpenRouter providers a model may be called
    # through, the ceiling the operator's choices stay under. Adding one is Vrushit's approval
    # of a new data processor. Optional: unset is DEFAULT_PROVIDER only; set, it must include
    # DEFAULT_PROVIDER (chat, embeddings and OCR_MODEL are called through it).
    openrouter_providers: frozenset[str]
    # Features 25-27 (S1): the model vendors a chosen model may come from, checked on its
    # id; a model of another vendor is never used, even when enabled and verified.
    # Optional: unset is DEFAULT_MODEL_VENDORS.
    openrouter_model_vendors: frozenset[str]
    ocr_media_resolution: str
    ocr_thinking_level: str
    ocr_retry_media_resolution: str
    ocr_retry_thinking_level: str
    ocr_page_concurrency: int
    careful_early_max_pages: int
    cpu_workers: int
    max_check_crops: int
    model_rpm: int
    reader_outage_fallback_minutes: int

    # PRD 5 E, the queue and bulk upload; each takes the PRD's value (section 9) when unset.
    # worker_replicas copies of the worker run (Q3); a customer has at most
    # max_active_per_customer documents being read at once (Q5). A document of at most
    # express_max_pages pages goes to queue_express_url, the express lane (Q4); None: no
    # express lane, every job goes to queue_url. One drop holds at most max_files_per_batch
    # files (Q1). The two model price settings are gone (model
    # choice, council 11 Oct 2026): the spend comes from the model_usage ledger and the
    # catalogue's prices; an older .env that still sets them starts as before.
    worker_replicas: int
    max_active_per_customer: int
    queue_express_url: str | None
    express_max_pages: int
    max_files_per_batch: int

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

    # AI through OpenRouter (PRD 5 G9, M1, M5): model_chat answers and model_embed embeds,
    # both OpenRouter model IDs. model_enabled=false runs everything without AI: search
    # without meaning vectors, answers quote the evidence.
    model_enabled: bool
    model_chat: str
    model_embed: str
    embedding_dim: int
    model_timeout_seconds: float
    # PRD 2 model budgets (ML amendment 5): the reply budgets for the document card and
    # for an answer (an invoice window's is INVOICE_REPLY_TOKENS), and the worker's
    # timeout for those calls. The card reads at most card_max_chars of the document (D1).
    model_card_max_tokens: int
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
    # Anyone may create an organisation and its first admin at /signup (16 Oct 2026).
    # On unless "false"; off, POST /auth/signup is 404 and the sign-in pages hide the link.
    self_signup_enabled: bool

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
    # Ask speed (24 Oct 2026): a first question's rewrite and plan call must end within this many
    # seconds, 1 to 60; past it the question is answered without its plan and aspects. A
    # follow-up's call keeps MODEL_TIMEOUT_SECONDS. Optional: 4 when unset.
    answer_plan_timeout_seconds: float
    # PRD 5 context and memory (sections CX and M; council 9 Oct 2026). Optional: unset is
    # the PRD's value, so an older .env keeps working. model_context_tokens is the chat
    # model's context window (M1), the one every budget uses. The chat's documents are given
    # whole when they add up to
    # answer_whole_doc_tokens or fewer, and a document's map reads its whole text under the
    # same limit (CX2, CX5; 0 turns both off). At most topic_max_per_doc topics a document
    # (CX3); a topic pulls in at most topic_expand_max_pieces pieces a document, and a
    # question is searched as at most query_aspects_max aspects (CX6). The chat summary and
    # memory are rewritten every chat_summary_every turns (M3).
    model_context_tokens: int
    answer_whole_doc_tokens: int
    # Faster Swift (24 Oct 2026): a Swift or Low answer reads at most this many tokens of
    # documents, whole documents or evidence alike (Precision keeps the full budget); 0: no cap.
    # PROVISIONAL: 30k, from the review's estimate that a smaller prompt saves 2 to 5 s on a
    # large scope; measure on the server's timing lines and tune.
    answer_swift_input_tokens: int
    # Read in parts (#381, lost in the middle): a Precision answer whose evidence is more than
    # answer_parts_tokens is read as at most answer_parts_max parts at once, each asked for the
    # facts that answer the question, then drafted from the pieces they name (answering/parts.py);
    # 0 turns it off. Swift never reads in parts. PROVISIONAL: 24k and 5 (a 120k whole scope in
    # five parts); measure recall with scripts/eval_position_recall.py and tune.
    answer_parts_tokens: int
    answer_parts_max: int
    topic_max_per_doc: int
    topic_expand_max_pieces: int
    query_aspects_max: int
    chat_summary_every: int
    timezone: str

    # API.
    rate_limit_per_minute: int
    chat_questions_per_minute: int  # chat questions a user may ask a minute (security, 15 Oct 2026)
    log_level: str
    # Tushar (16 Oct 2026): a document's reading budget is this many seconds per page (1 page =
    # 1 min, 30 pages = 30 min), never less than OCR_TIMEOUT_SECONDS (gemini_ocr).
    ocr_seconds_per_page: int = 60
    # Cost saving (16 Oct 2026, ``ocr.careful_plan``): a page is read again through its hard
    # lines only when they are at most cost_saving_hard_lines_max (1-60) and at most
    # cost_saving_hard_share (0.05-1.0) of its lines; each crop reaches cost_saving_crop_pad
    # (0.1-2.0) of the line's height past its sides (``gemini_ocr.crop_boxes``). Optional.
    cost_saving_hard_lines_max: int = 12
    cost_saving_hard_share: float = 0.4
    cost_saving_crop_pad: float = 0.5
    # Routing each page by its content (#384, ``reading.page_route``): for an account whose
    # reader choice is "Save cost" (#398, the scan level save), each scanned page is read by
    # the local reader first and keeps that read when it is clean. page_routing is the
    # platform's permission for that choice (on by default; off: no account may choose it and
    # a saved one reads every page with the AI); the thresholds are ``page_route.RouteRules``'s,
    # and at most page_route_region_pages_max pages of a document are read through their
    # regions (#385).
    page_routing: bool = True
    page_route_clean_confidence: float = 0.95
    page_route_min_words: int = 8
    page_route_min_line_px: float = 16.0
    page_route_height_spread: float = 0.45
    page_route_table_share: float = 0.3
    page_route_region_lines_max: int = 6
    page_route_region_share: float = 0.15
    page_route_region_pages_max: int = 25
    # PostgreSQL timeouts for each process, in seconds (#246 A1; 0 is no limit), set on every
    # connection the API or the worker opens (``make_engine``); the CLI and the migrations set
    # none. PROVISIONAL, Vrushit's values. Optional, so an older .env still starts. The worker's
    # are long on purpose: a cut statement or lock wait hands the job back to be read again,
    # and its saves (pieces, page layout) and sweeps can run for minutes on a big document.
    # Idle in a transaction: a session that holds a transaction open doing nothing this long
    # is ended, so a stuck process cannot hold its locks (and block a migration) for ever;
    # model calls run outside any transaction.
    db_statement_timeout_seconds: int = 120  # the API
    db_lock_timeout_seconds: int = 60  # the API
    db_worker_statement_timeout_seconds: int = 900
    db_worker_lock_timeout_seconds: int = 300
    db_idle_in_transaction_timeout_seconds: int = 900  # both
    # Email (#457 phase 0): Amazon SES from EMAIL_FROM, in SES_REGION. Off unless
    # EMAIL_ENABLED=true; on, EMAIL_FROM and APP_URL are required. APP_URL is the https origin
    # the links in an email open (http only on a loopback host).
    email_enabled: bool = False
    email_from: str | None = None
    ses_region: str = "ap-south-1"
    app_url: str | None = None
    # The super admin's address for operations alerts (#478: an organisation's reading paused),
    # sent through the same mailer while EMAIL_ENABLED is on. Optional: unset, none is sent.
    ops_alert_email: str | None = None
    # Sign in with Google (#457 phase 1). Off unless GOOGLE_SIGNIN_ENABLED=true; on, the
    # client id, secret and redirect URI are required. The redirect URI is https (http only
    # on a loopback host) and ends /api/auth/oidc/google/callback.
    google_signin_enabled: bool = False
    google_client_id: str | None = None
    google_client_secret: str | None = field(default=None, repr=False)
    google_redirect_uri: str | None = None
    # Mistral OCR, the second page reader the operator may choose per organisation (#473, Tushar,
    # 2 Nov 2026). Optional: unset, Mistral can't be chosen. The key is a secret repr never shows,
    # checked as OPENROUTER_API_KEY is. mistral_zdr_approved: zero data retention confirmed with
    # Mistral (council 2 Nov 2026); off unless MISTRAL_ZDR_APPROVED=true.
    mistral_api_key: str | None = field(default=None, repr=False)
    mistral_zdr_approved: bool = False

    @property
    def mistral_ready(self) -> bool:
        """Mistral is set up on this server: its key and zero data retention, both."""
        return self.mistral_api_key is not None and self.mistral_zdr_approved

    @classmethod
    def from_env(cls, environ: Mapping[str, str] | None = None) -> "Settings":
        env = _Env(os.environ if environ is None else environ)
        session_secret = env.secret("SESSION_SECRET")
        # Off unless set to true, as SELF_SIGNUP_ENABLED.
        email_enabled = env.optional("EMAIL_ENABLED") is not None and env.boolean("EMAIL_ENABLED")
        google_on = env.optional("GOOGLE_SIGNIN_ENABLED") is not None and env.boolean("GOOGLE_SIGNIN_ENABLED")
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
        ocr_engine = env.choice("OCR_ENGINE", {"gemini", "rapidocr", "none"})
        # Any other rule may read a page twice (at a lowered DPI, then the ceiling) between
        # heartbeats, and a Gemini page may take three calls (_GEMINI_PAGE_CALLS).
        ocr_calls = 1 if ocr_dpi_rule == "fixed" else 2
        if ocr_engine == "gemini":
            ocr_calls *= _GEMINI_PAGE_CALLS
        if ocr_calls * ocr_timeout_seconds + _OCR_LEASE_MARGIN_SECONDS > queue_lease_seconds:
            times = f"{ocr_calls} x " if ocr_calls > 1 else ""
            raise SettingsError(
                f"{times}OCR_TIMEOUT_SECONDS + {_OCR_LEASE_MARGIN_SECONDS}"
                " must not be more than QUEUE_LEASE_SECONDS"
            )
        model_context_tokens = env.at_least("MODEL_CONTEXT_TOKENS", 1, default=200_000)
        card_max_tokens = env.positive("MODEL_CARD_MAX_TOKENS")
        answer_max_tokens = env.positive("MODEL_ANSWER_MAX_TOKENS")
        for name, tokens in (
            ("MODEL_CARD_MAX_TOKENS", card_max_tokens),
            ("MODEL_ANSWER_MAX_TOKENS", answer_max_tokens),
        ):
            left = model_context_tokens - 2 * tokens - MAX_SYSTEM_PROMPT_TOKENS - CHAT_OVERHEAD_TOKENS
            if left < MIN_INPUT_TOKENS:
                raise SettingsError(
                    f"{name} must leave at least {MIN_INPUT_TOKENS} tokens of MODEL_CONTEXT_TOKENS"
                    " for the document"
                )
        # Empty means "what INVOICE_REPLY_TOKENS can answer" (window_for_reply_tokens);
        # a value set here overrides it, and is still checked below.
        auto_chars, auto_overlap = window_for_reply_tokens(INVOICE_REPLY_TOKENS)
        set_chars = env.optional_integer("EXTRACT_WINDOW_CHARS")
        set_overlap = env.optional_integer("EXTRACT_WINDOW_OVERLAP")
        window_chars = auto_chars if set_chars is None else set_chars
        window_overlap = auto_overlap if set_overlap is None else set_overlap
        if window_chars < 1:
            raise SettingsError("EXTRACT_WINDOW_CHARS must be at least 1")
        if window_overlap >= window_chars:
            # Without this the windows would never move forward (plan 6).
            raise SettingsError("EXTRACT_WINDOW_OVERLAP must be less than EXTRACT_WINDOW_CHARS")
        line_items = -(-window_chars // _WINDOW_LINE_CHARS)
        if line_items * _WINDOW_LINE_TOKENS + _WINDOW_FIELD_TOKENS > INVOICE_REPLY_TOKENS:
            raise SettingsError(
                f"EXTRACT_WINDOW_CHARS is too long for an invoice window's {INVOICE_REPLY_TOKENS}"
                " token reply: a window needs about 50 reply tokens for every 50 characters, plus 300"
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
        # PRD 5 CX and M: unset is the PRD's value. The whole documents (CX5) must fit the
        # context window with the answer's reply, its repair retry and the system prompt.
        model_chat = env.optional("MODEL_CHAT") or DEFAULT_MODEL
        whole_doc_tokens = env.at_least("ANSWER_WHOLE_DOC_TOKENS", 0, default=120_000)
        prompt_tokens = MAX_SYSTEM_PROMPT_TOKENS + CHAT_OVERHEAD_TOKENS
        room = 2 * answer_max_tokens + prompt_tokens
        if whole_doc_tokens + room > model_context_tokens:
            raise SettingsError(
                f"ANSWER_WHOLE_DOC_TOKENS + 2 x MODEL_ANSWER_MAX_TOKENS + {prompt_tokens}"
                " must not be more than MODEL_CONTEXT_TOKENS"
            )
        model_enabled = env.boolean("MODEL_ENABLED")
        model_embed = env.text("MODEL_EMBED")
        openrouter_api_key = env.optional("OPENROUTER_API_KEY")
        ocr_model = env.optional("OCR_MODEL") or DEFAULT_MODEL
        if not valid_model_id(ocr_model):
            raise SettingsError(
                "OCR_MODEL must be an OpenRouter model ID such as google/gemini-3.1-pro-preview (no :variant)"
            )
        for name, model in (("MODEL_CHAT", model_chat), ("MODEL_EMBED", model_embed)):
            if model_enabled and not valid_model_id(model):
                raise SettingsError(
                    f"{name} must be an OpenRouter model ID such as google/gemini-3.1-pro-preview"
                    " (no :variant) when MODEL_ENABLED is true"
                )
        providers = (
            frozenset({DEFAULT_PROVIDER})
            if env.optional("OPENROUTER_PROVIDERS") is None
            else env.words("OPENROUTER_PROVIDERS")
        )
        if any(PROVIDER_SLUG.fullmatch(slug) is None for slug in providers):
            raise SettingsError(
                "OPENROUTER_PROVIDERS must list provider slugs such as google-vertex,"
                " each lower-case letters, digits and dashes, at most 64"
            )
        if DEFAULT_PROVIDER not in providers:
            # Chat, embeddings and the platform's own model (resolve's fallback) all go there.
            raise SettingsError(f"OPENROUTER_PROVIDERS must include {DEFAULT_PROVIDER}")
        vendors = (
            DEFAULT_MODEL_VENDORS
            if env.optional("OPENROUTER_MODEL_VENDORS") is None
            else env.words("OPENROUTER_MODEL_VENDORS")
        )
        if any(MODEL_VENDOR.fullmatch(name) is None for name in vendors):
            raise SettingsError(
                "OPENROUTER_MODEL_VENDORS must list model vendors such as google,"
                " each lower-case letters, digits and dashes, at most 128"
            )
        if openrouter_api_key is None and (ocr_engine == "gemini" or model_enabled):
            raise SettingsError(
                "OPENROUTER_API_KEY is not set: OCR_ENGINE=gemini, or MODEL_ENABLED, needs it"
            )
        if openrouter_api_key is not None and _API_KEY.fullmatch(openrouter_api_key) is None:
            raise SettingsError("OPENROUTER_API_KEY must be printable ASCII with no spaces")
        mistral_api_key = env.optional("MISTRAL_API_KEY")
        if mistral_api_key is not None and _API_KEY.fullmatch(mistral_api_key) is None:
            raise SettingsError("MISTRAL_API_KEY must be printable ASCII with no spaces")
        queue_url = env.text("QUEUE_URL")
        queue_express_url = (
            None if env.optional("QUEUE_EXPRESS_URL") is None else env.url("QUEUE_EXPRESS_URL")
        )
        if queue_express_url == queue_url:
            raise SettingsError("QUEUE_EXPRESS_URL must name a queue other than QUEUE_URL")
        return cls(
            database_url=env.text("DATABASE_URL"),
            db_app_role=env.text("DB_APP_ROLE"),
            storage_endpoint_url=env.optional("STORAGE_ENDPOINT_URL"),
            storage_region=env.text("STORAGE_REGION"),
            storage_bucket=env.text("STORAGE_BUCKET"),
            storage_access_key=env.optional("STORAGE_ACCESS_KEY"),
            storage_secret_key=env.optional("STORAGE_SECRET_KEY"),
            queue_endpoint_url=env.optional("QUEUE_ENDPOINT_URL"),
            queue_url=queue_url,
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
            ocr_timeout_seconds=ocr_timeout_seconds,
            ocr_dpi=env.positive("OCR_DPI"),
            ocr_dpi_rule=ocr_dpi_rule,
            ocr_model=ocr_model,
            openrouter_api_key=openrouter_api_key,
            openrouter_base_url=_openrouter_base_url(env.text("OPENROUTER_BASE_URL")),
            openrouter_providers=providers,
            openrouter_model_vendors=vendors,
            ocr_media_resolution=env.media_resolution("OCR_MEDIA_RESOLUTION"),
            # Tushar (16 Oct 2026): the built-in scan default is Gemini 3.8 Flash at high.
            ocr_thinking_level=env.choice("OCR_THINKING_LEVEL", set(THINKING_LEVELS))
            if env.optional("OCR_THINKING_LEVEL")
            else "high",
            ocr_retry_media_resolution=env.media_resolution("OCR_RETRY_MEDIA_RESOLUTION"),
            ocr_retry_thinking_level=env.choice("OCR_RETRY_THINKING_LEVEL", set(THINKING_LEVELS)),
            ocr_page_concurrency=env.positive("OCR_PAGE_CONCURRENCY"),
            careful_early_max_pages=env.at_least("CAREFUL_EARLY_MAX_PAGES", 0, default=0),
            ocr_seconds_per_page=env.positive_or("OCR_SECONDS_PER_PAGE", 60),
            cost_saving_hard_lines_max=env.between_or("COST_SAVING_HARD_LINES_MAX", 1, 60, default=12),
            cost_saving_hard_share=env.number_in("COST_SAVING_HARD_SHARE", 0.05, 1.0, default=0.4),
            cost_saving_crop_pad=env.number_in("COST_SAVING_CROP_PAD", 0.1, 2.0, default=0.5),
            page_routing=env.optional("PAGE_ROUTING") is None or env.boolean("PAGE_ROUTING"),
            page_route_clean_confidence=env.number_in("PAGE_ROUTE_CLEAN_CONFIDENCE", 0.5, 1.0, default=0.95),
            page_route_min_words=env.between_or("PAGE_ROUTE_MIN_WORDS", 1, 1000, default=8),
            page_route_min_line_px=env.number_in("PAGE_ROUTE_MIN_LINE_PX", 1.0, 200.0, default=16.0),
            page_route_height_spread=env.number_in("PAGE_ROUTE_HEIGHT_SPREAD", 0.05, 5.0, default=0.45),
            page_route_table_share=env.number_in("PAGE_ROUTE_TABLE_SHARE", 0.0, 1.0, default=0.3),
            page_route_region_lines_max=env.between_or("PAGE_ROUTE_REGION_LINES_MAX", 0, 60, default=6),
            page_route_region_share=env.number_in("PAGE_ROUTE_REGION_SHARE", 0.0, 1.0, default=0.15),
            page_route_region_pages_max=env.between_or("PAGE_ROUTE_REGION_PAGES_MAX", 0, 1000, default=25),
            db_statement_timeout_seconds=env.at_least("DB_STATEMENT_TIMEOUT_SECONDS", 0, default=120),
            db_lock_timeout_seconds=env.at_least("DB_LOCK_TIMEOUT_SECONDS", 0, default=60),
            db_worker_statement_timeout_seconds=env.at_least(
                "DB_WORKER_STATEMENT_TIMEOUT_SECONDS", 0, default=900
            ),
            db_worker_lock_timeout_seconds=env.at_least("DB_WORKER_LOCK_TIMEOUT_SECONDS", 0, default=300),
            db_idle_in_transaction_timeout_seconds=env.at_least(
                "DB_IDLE_IN_TRANSACTION_TIMEOUT_SECONDS", 0, default=900
            ),
            cpu_workers=env.positive("CPU_WORKERS"),
            max_check_crops=env.positive("MAX_CHECK_CROPS"),
            model_rpm=env.between("MODEL_RPM", 1, 1200),
            reader_outage_fallback_minutes=env.positive("READER_OUTAGE_FALLBACK_MINUTES"),
            worker_replicas=env.positive_or("WORKER_REPLICAS", 3),
            max_active_per_customer=env.positive_or("MAX_ACTIVE_PER_CUSTOMER", 2),
            queue_express_url=queue_express_url,
            express_max_pages=env.positive_or("EXPRESS_MAX_PAGES", 3),
            max_files_per_batch=env.positive_or("MAX_FILES_PER_BATCH", 100),
            text_layer_min_chars=env.positive("TEXT_LAYER_MIN_CHARS"),
            text_layer_min_good_ratio=env.share("TEXT_LAYER_MIN_GOOD_RATIO"),
            scan_image_coverage=env.share("SCAN_IMAGE_COVERAGE"),
            blank_page_stddev=env.not_negative("BLANK_PAGE_STDDEV"),
            low_res_dpi=env.positive("LOW_RES_DPI"),
            model_enabled=model_enabled,
            model_chat=model_chat,
            model_embed=model_embed,
            embedding_dim=embedding_dim,
            model_timeout_seconds=model_timeout_seconds,
            model_card_max_tokens=card_max_tokens,
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
            # Off unless set to true (security review, 16 Oct 2026): no e-mail is proven yet.
            self_signup_enabled=env.optional("SELF_SIGNUP_ENABLED") is not None
            and env.boolean("SELF_SIGNUP_ENABLED"),
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
            answer_plan_timeout_seconds=env.number_in("ANSWER_PLAN_TIMEOUT_SECONDS", 1.0, 60.0, default=4.0),
            model_context_tokens=model_context_tokens,
            answer_whole_doc_tokens=whole_doc_tokens,
            answer_swift_input_tokens=env.at_least("ANSWER_SWIFT_INPUT_TOKENS", 0, default=30_000),
            answer_parts_tokens=env.at_least("ANSWER_PARTS_TOKENS", 0, default=24_000),
            answer_parts_max=env.between_or("ANSWER_PARTS_MAX", 2, 8, default=5),
            topic_max_per_doc=env.at_least("TOPIC_MAX_PER_DOC", 1, default=40),
            topic_expand_max_pieces=env.at_least("TOPIC_EXPAND_MAX_PIECES", 0, default=12),
            query_aspects_max=env.at_least("QUERY_ASPECTS_MAX", 0, default=6),
            chat_summary_every=env.at_least("CHAT_SUMMARY_EVERY", 1, default=6),
            timezone=_time_zone(env.text("TIMEZONE")),
            rate_limit_per_minute=env.integer("RATE_LIMIT_PER_MINUTE"),
            chat_questions_per_minute=env.at_least("CHAT_QUESTIONS_PER_MINUTE", 1, default=20),
            log_level=env.text("LOG_LEVEL"),
            email_enabled=email_enabled,
            email_from=env.text("EMAIL_FROM") if email_enabled else env.optional("EMAIL_FROM"),
            ses_region=env.optional("SES_REGION") or "ap-south-1",
            app_url=_app_url(env.text("APP_URL")) if email_enabled else env.optional("APP_URL"),
            ops_alert_email=env.optional("OPS_ALERT_EMAIL"),
            google_signin_enabled=google_on,
            google_client_id=env.text("GOOGLE_CLIENT_ID") if google_on else env.optional("GOOGLE_CLIENT_ID"),
            google_client_secret=env.text("GOOGLE_CLIENT_SECRET")
            if google_on
            else env.optional("GOOGLE_CLIENT_SECRET"),
            google_redirect_uri=_redirect_uri(env.text("GOOGLE_REDIRECT_URI"), "google")
            if google_on
            else env.optional("GOOGLE_REDIRECT_URI"),
            mistral_api_key=mistral_api_key,
            mistral_zdr_approved=env.optional("MISTRAL_ZDR_APPROVED") is not None
            and env.boolean("MISTRAL_ZDR_APPROVED"),
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

    def between_or(self, name: str, low: int, high: int, *, default: int) -> int:
        """``between``, or ``default`` when unset or empty."""
        return default if self.optional(name) is None else self.between(name, low, high)

    def number_in(self, name: str, low: float, high: float, *, default: float) -> float:
        """An optional number from ``low`` to ``high``: ``default`` when unset or empty."""
        if self.optional(name) is None:
            return default
        value = self.number(name)
        if not (math.isfinite(value) and low <= value <= high):
            raise SettingsError(f"{name} must be a number from {low} to {high}")
        return value

    def optional_integer(self, name: str) -> int | None:
        return None if self.optional(name) is None else self.integer(name)

    def positive_or(self, name: str, default: int) -> int:
        """``positive``, or ``default`` when unset or empty."""
        return default if self.optional(name) is None else self.positive(name)

    def at_least(self, name: str, low: int, *, default: int) -> int:
        """An optional whole number of at least ``low``: ``default`` when unset or empty."""
        number = self.optional_integer(name)
        number = default if number is None else number
        if number < low:
            raise SettingsError(f"{name} must be at least {low}")
        return number

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

    def media_resolution(self, name: str) -> str:
        """One of MEDIA_RESOLUTIONS that MEDIA_RESOLUTION_SHAPE can send. A level it cannot
        send stops start-up by name: it is never quietly sent as another level."""
        value = self.choice(name, set(MEDIA_RESOLUTIONS))
        sendable = SENDABLE_MEDIA_RESOLUTIONS[MEDIA_RESOLUTION_SHAPE]
        if value not in sendable:
            raise SettingsError(
                f"{name}={value} cannot be sent through OpenRouter: with the media resolution shape"
                f" {MEDIA_RESOLUTION_SHAPE!r} it must be one of {sorted(sendable)}"
            )
        return value

    def words(self, name: str) -> frozenset[str]:
        words = frozenset(part.strip() for part in self.text(name).split(",") if part.strip())
        if not words:
            raise SettingsError(f"{name} must list at least one value")
        return words
