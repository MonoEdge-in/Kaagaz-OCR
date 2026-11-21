"""The platform operator's models, AI settings and AI spend (model choice, council 11 Oct
2026, MC2, MC3, MS1, MS2, SP3, SP4; features 25-27, council 15 Oct 2026, C10). Every route
needs the operator cookie (``current_operator`` on the router); only ``OperatorDatabase``
here, and each call is one ``kaagaz_op_*`` function (``kaagaz.db.operator_models``), which
writes its own audit rows.

* ``GET /operator/models``: the catalogue, from OpenRouter's live lists (``jobs/catalog``),
  with ``verified`` for the reader's current prompt (``READER_PROMPT_VERSION``),
  ``chat_verified`` for the answer prompt (``ANSWER_PROMPT_HASH``) and ``vendor_allowed``
  (its maker is in OPENROUTER_MODEL_VENDORS; outside it, it is never used), and the real
  cost of each model's scanned pages and answered questions over the last 30 days.
* ``PUT /operator/models/{model}`` {enabled, code} (step-up, as 0016's): enable or disable
  one model on the platform. Only an eligible model verified for scanning or answering
  can be enabled; one an organisation or the platform defaults still name can't be
  disabled.
* ``GET`` and ``PUT /operator/ai-defaults`` (PUT with a fresh code): the platform defaults
  every organisation that has not been customised follows, per area, and how many
  organisations follow each area now.
* ``GET`` and ``PUT /operator/orgs/{id}/ai`` (PUT with a fresh code): one organisation's
  own AI settings (an empty list or a null field follows the platform defaults), its
  Ensemble switch and its monthly budget in dollars (the operator's alone, never in the
  organisation's own log or screens; #478: reaching it pauses reading). Any number of scanning models (0037):
  Ensemble asked with fewer than two in effect is saved off and the PUT says
  ``ensemble_off``.
* The scan cost settings (#463, Tushar, 31 Oct 2026; 0064): both AI settings carry
  ``scan_resolution`` (the picture detail of first reads), ``careful_thinking`` and
  ``careful_resolution`` (Careful's re-reads and "Read this page again"), each null to
  inherit; a scan level may be minimal (an answer level may not); and
  ``page_max_output_tokens``, a page read's output limit, 1024 to 65536 (400
  ``limit_out_of_range``); ``env`` names the server's four, and the defaults count the
  organisations inheriting each. A save reaches what is
  read from then on (new scans, Careful's re-reads, pages read again), never text already read.
* ``POST /operator/scan-cost`` {customer_id, scan_thinking, scan_resolution, careful_thinking,
  careful_resolution, page_max_output_tokens, dynamic}, each null for what the save would
  inherit (#463 review: Dynamic too, under the Cost saving switches; no code: it changes
  nothing): the approximate cost of a page at those settings, measured on every
  organisation's ledger of the last LEDGER_DAYS days, never guessed (null when nothing was
  read so). The model is the scope's default (``usable_models``, ``scope_reading``: the
  same rules as a scan); the share read again is the scope's own. ``quality``: the
  share of the scope's pages read clean (read_quality good) at these settings and at max
  settings (the same model, its highest thinking, high picture detail), from the pages' own
  settings; ``accuracy``: an organisation's latest done check at exactly these settings;
  ``tokens``: the scope's first reads' average tokens a page, the 99th percentile of a first
  read's output tokens (null under 20 pages), the limit in effect and the scope's reads that
  hit it (#463 review: every figure the scope's, a page counted once).
* ``POST /operator/orgs/{id}/accuracy-check`` {the estimate's fields, pages: 20, 50 or 100,
  code} (a fresh code): a worker job (``kaagaz.jobs.accuracy``) reads the organisation's recent
  model-read pages at the draft's settings and at max, and keeps the share of words and of
  numbers that matched, never text; billed to it as ``accuracy_check``, never its budget. A
  queue that can't take the job ends the check as failed (#463 review).
  ``GET .../accuracy-check/latest``: its latest check, any settings (404 with none); one
  whose job stopped beating is failed.
* Who reads scanned pages (#473, Tushar, 2 Nov 2026; 0065): both AI settings carry
  ``scan_reader`` (openrouter or mistral) and ``mistral_model``, each null to inherit (the
  platform's: openrouter and mistral-ocr-4-1). Mistral can be newly chosen only when the server
  has MISTRAL_API_KEY and MISTRAL_ZDR_APPROVED=true (409 ``mistral_not_set_up``, before a code
  is used); a save that keeps an already saved Mistral goes through (review, 2 Nov 2026), and a
  Mistral model may be kept while Gemini reads. ``env`` names the server's reader and
  model, ``mistral_ready``, ``mistral_missing`` (key or zdr), ``mistral_models`` (the code's
  list with each price a page) and ``mistral_status`` (whether Mistral stopped, from the runs
  that waited on it in the last day, every organisation's together). The key never leaves the
  server. The estimate and the accuracy check take both fields, each null for what the save
  would inherit: with Mistral in effect the estimate prices Mistral's first reads
  ("mistral/<id>" at none and page; never Careful) and says ``fixed_price`` with Mistral's
  ``fixed_usd_per_page`` (its measured figures still shown when its ledger has any), and the
  check's model is "mistral/<id>" at Mistral's one setting (none, page; review, 2 Nov 2026), so
  its job reads "your settings" with Mistral; it takes no Gemini level test, and is refused
  (409 ``mistral_not_set_up``) while Mistral is not set up here.
* The reading pause (#478, Tushar, 3 Nov 2026; 0066): both AI settings carry
  ``daily_page_limit``, the pages AI may read for an organisation in one India day, 1 to
  MAX_PAGES_A_DAY (400 ``pages_out_of_range``), null to inherit (the platform's null: no
  limit); the defaults count the organisations inheriting it. When an organisation's budget
  is set, reaching it pauses reading too (no other switch); Ask keeps working. ``GET
  .../orgs/{id}/ai`` adds ``reading_today``: its pages read today and their dollars (the
  India day).
* Ultra low cost (#488, Tushar, 4 Nov 2026; 0070): ``scan_reader`` may be ``ultra_low_cost``, and
  both AI settings carry ``ulc_ceiling_paise``, the most one read of a page may cost, 20 to 55
  paise (400 ``ceiling_out_of_range``), null to inherit (the platform's null: 40); the defaults
  count the organisations inheriting it. Choosing it, or keeping it in effect, needs its lead
  (the default scan model in effect when it is a Gemini with a known price, else OCR_MODEL) to be
  a Gemini in the catalogue with a known price that takes minimal thinking (409
  ``ulc_needs_gemini``), both checked before a code is used. The estimate and the accuracy check
  take the draft's ceiling too: with Ultra low cost in effect the estimate adds ``ulc``
  {ceiling_paise, ceiling_usd, pages, usd_per_page, top_usd_per_page, limit_hits}, measured on the
  scope's ``ulc_scan`` ledger rows of the last LEDGER_DAYS days (``kaagaz_op_ulc_cost``; null
  otherwise), and the check's model is "ulc/<paise>/<model>" at minimal thinking.
* The backup reader (#664, Tushar, 18 Nov 2026; 0101): both AI settings carry ``backup_reader``
  (off, openrouter or ultra_low_cost) and ``backup_model``, null to inherit as one (the platform's
  null: off); ``orgs_inheriting.backup_reader`` counts the organisations following the platform's.
  A model exactly with a way to read (400 ``backup_reader_incomplete``), an OpenRouter model ID
  (400), in the catalogue, eligible to scan, enabled and verified, as the scanning models (409
  ``unavailable``, ``not_verified``), and for Ultra low cost a Gemini with a known price that takes
  minimal thinking (409 ``ulc_needs_gemini``, with the model's reason), each before a code is used
  except the save's own. It reads only a page Mistral read no text on; it changes no estimate.
* ``GET /operator/usage?from&to``: tokens and dollars per organisation and model, days in
  TIMEZONE with both ends included (this month so far when left out), at most
  ``MAX_DAYS``. Never a user or a document: the function has no column for either.

* ``GET /operator/feature-switches``: every product feature, on or off for the whole
  product, and how many organisations use it now. ``PUT .../{feature}`` {enabled, code}
  (step-up): flip it for every organisation; the list comes back (0038).
* ``GET /operator/orgs/{id}/feature-switches``: one organisation's switches, each
  ``{feature, enabled, platform_enabled}`` (no self sign-up). ``PUT .../{feature}``
  {enabled, code} (step-up): flip it for that organisation only; the list comes back. A
  feature runs there only while both switches are on (0039).
* ``GET`` and ``PUT`` {hours, code} ``/operator/session-timeout`` and
  ``/operator/orgs/{id}/session-timeout`` (PUT with a fresh code): how long an
  organisation's admins and members stay signed in with no activity, 1 to 24 hours; the
  organisation's, else the platform's, else none (``default_hours``: SESSION_HOURS, the
  session's own end, which stays the cap) (0040).
* ``GET`` and ``PUT`` {on, code} ``/operator/google-signin`` (PUT with a fresh code): ``{on}``,
  Google sign-in for every organisation that follows the platform (true by default; the server's
  GOOGLE_SIGNIN_ENABLED stays the master switch). A change signs out their people with an
  approved connection; an organisation with its own value is untouched. Audited
  ``google.platform_set`` (0074, #507; one organisation's: ``/operator/orgs/{id}/google-signin``).
* ``GET`` and ``PUT`` {model_rpm, documents_at_once, pages_at_once, code} ``/operator/speed``
  (PUT with a fresh code): the platform's model requests a minute, documents read at once and
  pages of one document read at once, each null for the server's setting and never above it
  (``most``: MODEL_RPM, WORKER_REPLICAS, OCR_PAGE_CONCURRENCY) (package S, 0053).
* ``GET`` and ``PUT`` {max_files, max_gb, max_folders, code} ``/operator/folder-upload-limits``
  and ``/operator/orgs/{id}/folder-upload-limits`` (PUT with a fresh code): the most files, GB
  and folders one folder upload may hold; the organisation's own, else the platform's, else
  ``defaults`` (50,000 files, 5 GB, 10,000 folders), each at most 200,000, 20 and 50,000
  (400 before a code is used up). An organisation's all null follows the platform (0071, #484).
* ``GET`` and ``PUT`` {storage_gb, code} ``/operator/storage-limit`` and
  ``/operator/orgs/{id}/storage-limit`` (PUT with a fresh code): the most gigabytes an
  organisation's documents may hold (every version and the Trash); the organisation's own, else
  the platform's, else ``default_gb`` (50), 1 to ``most_gb`` (10,000) (400 before a code is used
  up). Null follows the platform (an organisation's) or the default (the platform's). The reply:
  ``storage_gb`` (this level's own), ``platform_gb``, ``effective_gb``, ``default_gb``,
  ``most_gb`` and ``used_bytes`` (the organisation's documents now; 0 for the platform)
  (0061d, merged by 0075, #516).
* ``GET``, ``POST`` {scan_model, chat_model, note, code} and ``DELETE`` {code}
  ``/operator/test-model`` (POST and DELETE with a fresh code): the platform test model, one
  allowed model for every organisation's scanning and answering, and its undo (every
  organisation back as it was), with what ran on it since it started (pages, questions, the
  cost OpenRouter reported, reading seconds a page). A model must be enabled, eligible and
  verified for its use, and its maker and one provider inside the server's lists (0038).

A refused change is said in plain words; an unknown organisation or model gets 404.
"""

import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal
from typing import Annotated, Any, Literal
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import RowMapping

from kaagaz.ai.openrouter import PAGE_MAX_OUTPUT_TOKENS
from kaagaz.answering.draft import ANSWER_PROMPT_HASH
from kaagaz.api.auth import CurrentOperator, OperatorSession, current_operator
from kaagaz.api.context import AppContext, get_context, get_operator_db
from kaagaz.api.errors import ApiError
from kaagaz.api.routers.auth import MAX_CODE
from kaagaz.api.routers.documents import day_start
from kaagaz.api.routers.operator_orgs import StepUp, StepUpBody, _check, _step_up
from kaagaz.api.views import iso
from kaagaz.db import models
from kaagaz.db import operator_models as models_sql
from kaagaz.db.engine import OperatorDatabase
from kaagaz.db.folder_uploads import (
    DEFAULT_MAX_FILES,
    DEFAULT_MAX_FOLDERS,
    DEFAULT_MAX_GB,
    MOST_FILES,
    MOST_FOLDERS,
    MOST_GB,
)
from kaagaz.db.models import (
    LEDGER_DAYS,
    MAX_AGENTS,
    MINIMAL,
    SCAN_COST_FIELDS,
    ULC_CEILING_PAISE,
    ULC_DEFAULT_PAISE,
    ULTRA_LOW_COST,
    vendor,
)
from kaagaz.db.operator_models import AiChoice
from kaagaz.db.operator_orgs import Outcome
from kaagaz.db.reading_days import india_day
from kaagaz.db.storage_limits import DEFAULT_STORAGE_GB, MOST_STORAGE_GB
from kaagaz.ingestion.ports import Job
from kaagaz.jobs import accuracy
from kaagaz.reading.mistral_ocr import (
    DEFAULT_MISTRAL_MODEL,
    MISTRAL,
    MISTRAL_OCR_MODELS,
    MISTRAL_RESOLUTION,
    MISTRAL_THINKING,
    OPENROUTER,
    read_by,
)
from kaagaz.reading.ocr import READER_PROMPT_VERSION
from kaagaz.settings import Settings, valid_model_id

router = APIRouter(prefix="/operator", dependencies=[Depends(current_operator)])

Context = Annotated[AppContext, Depends(get_context)]
OperatorDb = Annotated[OperatorDatabase, Depends(get_operator_db)]

MAX_DAYS = 366
MAX_ORG_MODELS = 64
# numeric(12,2): the largest budget the column holds.
MAX_BUDGET = Decimal("9999999999.99")
TOKENS_LOW = 1024  # #463: the least output limit a page read may have
MAX_PAGES_A_DAY = 1_000_000  # #478: the most a daily page limit may be (0066's CHECK)
# The outcomes kaagaz.db.operator_orgs' _check does not know, and their replies (C10).
_REFUSALS = {
    "invalid": (400, "bad_request", "The RPM value is outside the allowed limit."),
    "not_eligible": (409, "not_eligible", "This model does not pass the catalogue's rules now."),
    "not_verified": (
        409,
        "not_verified",
        "A model has not passed verify-models for the current prompt. Run it with --record first.",
    ),
    "chat_not_verified": (
        409,
        "chat_not_verified",
        "An answering model has not passed verify-models for the current answer prompt."
        " Run it with --record first.",
    ),
    "in_use": (
        409,
        "in_use",
        "An organisation or the platform defaults still use this model. Remove it there first.",
    ),
    "unavailable": (
        409,
        "unavailable",
        "One of these models is not enabled on the platform now, or can't be used for this.",
    ),
    "default_not_allowed": (
        409,
        "default_not_allowed",
        "Choose each default from its own list: a scan model, an answering model and a thinking level.",
    ),
    "level_not_supported": (
        409,
        "level_not_supported",
        "A chosen model does not accept one of these thinking levels. Choose levels every model accepts.",
    ),
    # #463, an accuracy check.
    "check_running": (409, "check_running", "A check of this organisation is running. Wait for it to end."),
    "rate_limited": (
        429,
        "rate_limited",
        "Too many accuracy checks in the last hour. Try again later.",
    ),
    "no_pages": (400, "no_pages", "This organisation has no page read by AI to check yet."),
    "limit_out_of_range": (
        400,
        "limit_out_of_range",
        f"Choose an output limit from {TOKENS_LOW} to {PAGE_MAX_OUTPUT_TOKENS} tokens.",
    ),
    "pages_out_of_range": (
        400,
        "pages_out_of_range",
        f"Choose a daily page limit from 1 to {MAX_PAGES_A_DAY:,} pages, or leave it empty.",
    ),
    # #473: Mistral chosen on a server without its key or zero data retention confirmed.
    "mistral_not_set_up": (409, "mistral_not_set_up", "Mistral isn't set up on this server yet."),
    # #488: Ultra low cost's ceiling a page, and its lead.
    "ceiling_out_of_range": (
        400,
        "ceiling_out_of_range",
        "Choose from ₹0.20 to ₹0.55 a page, or leave it empty.",
    ),
    "ulc_needs_gemini": (
        409,
        "ulc_needs_gemini",
        "Ultra low cost needs a Gemini model with a known price that can read with minimal thinking.",
    ),
    # #664: the backup reader, a way to read with its model, or off with none.
    "backup_reader_incomplete": (
        400,
        "backup_reader_incomplete",
        "Choose the backup reader's model, or turn the backup reader off.",
    ),
}

Level = Literal["low", "medium", "high"]
ScanLevel = Literal["minimal", "low", "medium", "high"]  # #463: minimal for scanning only
Resolution = Literal["low", "high"]  # #463: the picture detail OpenRouter can send (settings)
# #473: who reads scanned pages (mistral_ocr.OPENROUTER, MISTRAL) and the Mistral models
# (MISTRAL_OCR_MODELS' ids; migration 0065's CHECKs list the same).
ScanReader = Literal["openrouter", "mistral", "ultra_low_cost"]  # #488: Ultra low cost (0070)
MistralModelId = Literal["mistral-ocr-4-1", "mistral-ocr-2512"]
# #664 (0101): how the backup reader reads a page Mistral read no text on, or off (0101's CHECK).
BackupReaderWay = Literal["off", "openrouter", "ultra_low_cost"]
# #463: a page read's output limit, in tokens (TOKENS_LOW to openrouter.PAGE_MAX_OUTPUT_TOKENS).
OutputTokens = Annotated[int, Field(strict=True)]
# #478: a daily page limit (1 to MAX_PAGES_A_DAY, checked by ``_choice``).
DailyPages = Annotated[int, Field(strict=True)]
# #488: Ultra low cost's ceiling a page in paise (ULC_CEILING_PAISE, checked by ``_ceiling``).
UlcPaise = Annotated[int, Field(strict=True)]
# #463: the estimate's organisation with nothing of its own, so its connection sees the platform
# defaults alone (row level security shows no organisation this id).
NO_ORGANISATION = str(uuid.UUID(int=0))
ModelId = Annotated[str, Field(max_length=257)]  # MODEL_ID: 128 + "/" + 128 at most
ModelList = Annotated[list[ModelId], Field(max_length=MAX_ORG_MODELS)]
Levels = Annotated[list[Level], Field(min_length=1, max_length=3)]


class EnableBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool
    code: str = Field(min_length=1, max_length=MAX_CODE)


class OrgAiBody(BaseModel):
    """One organisation's own settings: an empty list or a null field follows the
    platform defaults; with a fresh code."""

    model_config = ConfigDict(extra="forbid")

    scan_models: ModelList
    scan_default: ModelId | None
    chat_models: ModelList
    chat_default: ModelId | None
    scan_thinking: ScanLevel | None
    scan_resolution: Resolution | None
    careful_thinking: ScanLevel | None
    careful_resolution: Resolution | None
    page_max_output_tokens: OutputTokens | None
    scan_reader: ScanReader | None  # #473
    mistral_model: MistralModelId | None
    daily_page_limit: DailyPages | None  # #478
    ulc_ceiling_paise: UlcPaise | None  # #488
    backup_reader: BackupReaderWay | None  # #664: null, the platform's (with its model)
    backup_model: ModelId | None
    chat_levels: Levels | None
    chat_default_level: Level | None
    dynamic: bool | None
    ensemble: bool
    ai_budget_usd: Decimal | None = Field(gt=0, le=MAX_BUDGET, decimal_places=2)
    code: str = Field(min_length=1, max_length=MAX_CODE)


Feature = Literal[
    "scan_ai",
    "ask",
    "ensemble",
    "autofile",
    "cost_saving",
    "review",
    "my_words",
    "self_signup",
    "version_spotting",
    "land_facts",  # PRD 6 (0052)
    "nlq_clarify",
    "nlq_learning",
    "whatsapp",  # #603 (0097)
]
# Sign-up makes a new organisation: one has no switch of its own for it (0039).
OrgFeature = Literal[
    "scan_ai",
    "ask",
    "ensemble",
    "autofile",
    "cost_saving",
    "review",
    "my_words",
    "version_spotting",
    "land_facts",
    "nlq_clarify",
    "nlq_learning",
    "whatsapp",
]


class TimeoutBody(BaseModel):
    """The sign-in idle timeout in whole hours, 1 to 24 (0040); null: the default (the platform's)
    or follow the platform (an organisation's). Strict: 1.5, "3" and true are refused."""

    model_config = ConfigDict(extra="forbid")

    hours: Annotated[int, Field(strict=True, ge=1, le=24)] | None
    code: str = Field(min_length=1, max_length=MAX_CODE)


class FolderLimitsBody(BaseModel):
    """The folder upload limits (0071, #484), each null for "not set" (the default, or follow the
    platform), never above its ceiling. Strict: 1.5, "3" and true are refused."""

    model_config = ConfigDict(extra="forbid")

    max_files: Annotated[int, Field(strict=True, ge=1, le=MOST_FILES)] | None
    max_gb: Annotated[int, Field(strict=True, ge=1, le=MOST_GB)] | None
    max_folders: Annotated[int, Field(strict=True, ge=1, le=MOST_FOLDERS)] | None
    code: str = Field(min_length=1, max_length=MAX_CODE)


class StorageLimitBody(BaseModel):
    """The storage limit in whole GB (0061d, #516), 1 to 10,000 (the routes check it: #559);
    null: the default (the platform's) or follow the platform (an organisation's). Strict: 1.5,
    "3" and true are refused."""

    model_config = ConfigDict(extra="forbid")

    storage_gb: Annotated[int, Field(strict=True)] | None
    code: str = Field(min_length=1, max_length=MAX_CODE)


class SpeedBody(BaseModel):
    """The speed controls (package S, 0053), each null for the server's setting and never above
    it (the route checks). Strict: 1.5, "3" and true are refused."""

    model_config = ConfigDict(extra="forbid")

    model_rpm: Annotated[int, Field(strict=True, ge=1)] | None
    documents_at_once: Annotated[int, Field(strict=True, ge=1)] | None
    pages_at_once: Annotated[int, Field(strict=True, ge=1)] | None
    code: str = Field(min_length=1, max_length=MAX_CODE)


class SwitchBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool
    code: str = Field(min_length=1, max_length=MAX_CODE)


class TestModelBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    scan_model: ModelId | None = None
    chat_model: ModelId | None = None
    note: str | None = Field(default=None, max_length=500)
    code: str = Field(min_length=1, max_length=MAX_CODE)


class DefaultsBody(BaseModel):
    """The platform defaults (no Ensemble, no budget: those are an organisation's own);
    empty model lists use OCR_MODEL and MODEL_CHAT, a null scan level OCR_THINKING_LEVEL, a
    null picture detail or Careful pair OCR_MEDIA_RESOLUTION and OCR_RETRY_* (#463)."""

    model_config = ConfigDict(extra="forbid")

    scan_models: ModelList
    scan_default: ModelId | None
    chat_models: ModelList
    chat_default: ModelId | None
    scan_thinking: ScanLevel | None
    scan_resolution: Resolution | None
    careful_thinking: ScanLevel | None
    careful_resolution: Resolution | None
    page_max_output_tokens: OutputTokens | None
    scan_reader: ScanReader | None  # #473: null, openrouter
    mistral_model: MistralModelId | None  # null, DEFAULT_MISTRAL_MODEL
    daily_page_limit: DailyPages | None  # #478: null, no limit
    ulc_ceiling_paise: UlcPaise | None  # #488: null, ULC_DEFAULT_PAISE
    backup_reader: BackupReaderWay | None  # #664: null, off
    backup_model: ModelId | None
    chat_levels: Levels
    chat_default_level: Level
    dynamic: bool
    code: str = Field(min_length=1, max_length=MAX_CODE)


class ScanSettingsBody(BaseModel):
    """#463: a draft's scan settings (SCAN_COST_FIELDS) and Cost saving (#463 review), each
    null for what the save would inherit (an organisation's: the platform's, else the
    server's; the platform's: the server's, its Dynamic its saved one); #488 review: each left
    out is the organisation's (or the platform's) own effective value."""

    model_config = ConfigDict(extra="forbid")

    scan_thinking: ScanLevel | None = None
    scan_resolution: Resolution | None = None
    careful_thinking: ScanLevel | None = None
    careful_resolution: Resolution | None = None
    page_max_output_tokens: OutputTokens | None = None
    dynamic: bool | None = None
    # #473: the draft's reader and Mistral model (null: inherited), as the scan settings are.
    scan_reader: ScanReader | None = None
    mistral_model: MistralModelId | None = None
    ulc_ceiling_paise: UlcPaise | None = None  # #488


class ScanCostBody(ScanSettingsBody):
    """#463: the scan settings to price; ``customer_id`` null prices the platform defaults."""

    customer_id: uuid.UUID | None = None


class AccuracyCheckBody(ScanSettingsBody):
    """#463: the scan settings to check, the pages to read (20, 50 or 100) and a fresh code."""

    pages: Literal[20, 50, 100] = 20
    code: str = Field(min_length=1, max_length=MAX_CODE)


class ModelRateBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    rpm: int | None = Field(default=None, ge=1, le=600)
    code: str = Field(min_length=1, max_length=MAX_CODE)


class UserModelRateBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    rpm: int | None = Field(default=None, ge=1, le=120)
    code: str = Field(min_length=1, max_length=MAX_CODE)


@router.get("/models")
def list_models(operator: OperatorSession, db: OperatorDb, context: Context) -> dict[str, Any]:
    """Only the models Kaagaz can use (Tushar, 18 Oct 2026: "remove the models not feasible
    for this product"): eligible to scan or to answer, or still enabled, so it can be
    turned off. A model failing a rule (no zero-data-retention provider, no image input...)
    stays in model_catalog but is not listed."""
    with db.operator() as conn:
        rows = models_sql.list_models(conn, operator.operator_id)
    usable = (row for row in rows if row["eligible"] or row["chat_eligible"] or row["enabled_at"] is not None)
    return {"items": [_model(row, context.settings) for row in usable]}


@router.put("/models/{model:path}")
def set_model_enabled(
    model: str, body: EnableBody, operator: OperatorSession, db: OperatorDb, context: Context
) -> dict[str, Any]:
    """Enable or disable one model on the platform (MC2): a fresh code from the operator's
    own authenticator first."""
    if not valid_model_id(model):
        raise ApiError(404, "not_found")
    step_up = _step_up(db, context, operator, body.code)
    with db.operator() as conn:
        outcome = models_sql.set_model_enabled(
            conn,
            operator.operator_id,
            model,
            enabled=body.enabled,
            scan_prompt=READER_PROMPT_VERSION,
            chat_prompt=ANSWER_PROMPT_HASH,
            step=step_up.step,
        )
    _refuse(db, operator, outcome, step_up)
    return {"model": {"id": model, "enabled": body.enabled, "changed": outcome == "done"}}


@router.get("/ai-defaults")
def get_ai_defaults(operator: OperatorSession, db: OperatorDb, context: Context) -> dict[str, Any]:
    """The platform defaults and how many organisations follow each area now (D8)."""
    with db.operator() as conn:
        row = models_sql.ai_defaults(conn, operator.operator_id)
        status = models_sql.mistral_status(conn, operator.operator_id)
    if row is None or status is None:  # the operator was disabled meanwhile
        raise ApiError(401, "not_signed_in")
    return {
        "defaults": _ai(row),
        "env": {**_env(context.settings), "mistral_status": _mistral_status(status)},
        "orgs_inheriting": {
            area: int(row[f"orgs_inheriting_{area}"])
            for area in (
                "scan_models",
                "chat_models",
                "scan_thinking",
                "chat_levels",
                "dynamic",
                "scan_resolution",
                "careful_thinking",
                "careful_resolution",
                "page_max_output_tokens",
                "scan_reader",  # #473
                "mistral_model",
                "daily_page_limit",  # #478
                "ulc_ceiling_paise",  # #488
                "backup_reader",  # #664, with its model
            )
        },
    }


@router.put("/ai-defaults")
def set_ai_defaults(
    body: DefaultsBody, operator: OperatorSession, db: OperatorDb, context: Context
) -> dict[str, Any]:
    """Replace the platform defaults (C10): every organisation still on them follows."""

    def saved_reader() -> str | None:
        with db.operator() as conn:
            row = models_sql.ai_defaults(conn, operator.operator_id)
        return None if row is None else row["scan_reader"]

    choice = _choice(body, context.settings, saved_reader)
    _needs_gemini(db, operator, body, context.settings, platform=None)
    _backup_needs_gemini(db, operator, body)
    step_up = _step_up(db, context, operator, body.code)
    with db.operator() as conn:
        outcome = models_sql.set_ai_defaults(
            conn,
            operator.operator_id,
            choice,
            scan_prompt=READER_PROMPT_VERSION,
            chat_prompt=ANSWER_PROMPT_HASH,
            step=step_up.step,
        )
    _refuse(db, operator, outcome, step_up)
    return get_ai_defaults(operator, db, context)


@router.get("/orgs/{customer_id}/ai")
def get_org_ai(
    customer_id: uuid.UUID, operator: OperatorSession, db: OperatorDb, context: Context
) -> dict[str, Any]:
    """The organisation's own settings, the platform defaults it follows where it has none,
    its Ensemble switch, the most models that work together, its budget, and (#478) its pages read
    today and their dollars."""
    with db.operator() as conn:
        own = models_sql.org_ai(conn, operator.operator_id, str(customer_id))
        defaults = models_sql.ai_defaults(conn, operator.operator_id)
        status = models_sql.mistral_status(conn, operator.operator_id)
        today = models_sql.reading_today(
            conn, operator.operator_id, str(customer_id), india_day(context.clock())
        )
    if own is None or defaults is None or status is None or today is None:
        raise ApiError(404, "not_found")
    return {
        "own": _ai(own),
        "defaults": _ai(defaults),
        "env": {**_env(context.settings), "mistral_status": _mistral_status(status)},
        "ensemble": bool(own["ensemble"]),
        "max_agents": MAX_AGENTS,
        "ai_budget_usd": _number(own["ai_budget_usd"]),
        "reading_today": {"pages": int(today["pages"]), "cost_usd": _number(today["cost_usd"])},
    }


@router.put("/orgs/{customer_id}/ai")
def set_org_ai(
    customer_id: uuid.UUID, body: OrgAiBody, operator: OperatorSession, db: OperatorDb, context: Context
) -> dict[str, Any]:
    """Replace the organisation's AI settings, Ensemble switch and budget (C10, SP4)."""

    def saved_reader() -> str | None:
        with db.operator() as conn:
            row = models_sql.org_ai(conn, operator.operator_id, str(customer_id))
        return None if row is None else row["scan_reader"]

    choice = _choice(body, context.settings, saved_reader)
    _needs_gemini(db, operator, body, context.settings, platform=_platform(db, operator, body))
    _backup_needs_gemini(db, operator, body)
    step_up = _step_up(db, context, operator, body.code)
    with db.operator() as conn:
        outcome = models_sql.set_org_ai(
            conn,
            operator.operator_id,
            str(customer_id),
            choice,
            ensemble=body.ensemble,
            budget=body.ai_budget_usd,
            scan_prompt=READER_PROMPT_VERSION,
            chat_prompt=ANSWER_PROMPT_HASH,
            step=step_up.step,
        )
    _refuse(db, operator, outcome.code, step_up)
    saved = get_org_ai(customer_id, operator, db, context)
    # Ensemble with fewer than two scanning models in effect is saved off, not refused (0037).
    return {**saved, "ensemble_off": body.ensemble and not saved["ensemble"]}


@dataclass(frozen=True, slots=True)
class _Plan:
    """A draft's scan settings resolved as a scan would read them (#463): ``first`` and
    ``careful`` (None: Dynamic off) as a page read sends them, ``best`` the max settings'
    (thinking, picture detail) for the same lead, ``asked`` the check's settings (every one a
    value), and the ``reading`` (its lead and Careful with their catalogue levels)."""

    first: models_sql.Sent
    careful: models_sql.Sent | None
    best: tuple[str, str]
    asked: models_sql.CheckSettings
    reading: models.Reading


def _plan(
    db: OperatorDatabase,
    operator: CurrentOperator,
    context: AppContext,
    customer: str | None,
    draft_fields: ScanCostBody | AccuracyCheckBody,
) -> _Plan:
    """The draft's ``_Plan`` for ``customer`` (None: the platform defaults); 404 for an unknown
    organisation (or an operator disabled meanwhile)."""
    settings = context.settings
    _limit(draft_fields.page_max_output_tokens)
    _ceiling(draft_fields.ulc_ceiling_paise)
    if customer is not None:
        with db.operator() as conn:
            if models_sql.org_ai(conn, operator.operator_id, customer) is None:
                raise ApiError(404, "not_found")
    # The scope's models and settings as a scan resolves them, on its own customer connection;
    # #463 review: the platform's own values from the same read (no organisation's counts).
    scope = customer or NO_ORGANISATION
    with context.db.customer(scope) as conn:
        rows = models.usable_models(conn, scope, settings=settings)
        allowed = models.effective_settings(conn, scope, settings=settings)
        platform = (
            allowed
            if customer is None
            else models.effective_settings(conn, NO_ORGANISATION, settings=settings)
        )
    # A field sent null is what the save would inherit: an organisation's the platform's; the
    # platform's the server's. #488 review: one left out is the scope's own (its effective value).
    given = draft_fields.model_fields_set
    draft: dict[str, Any] = {
        name: allowed[name]
        if name not in given
        else getattr(draft_fields, name) or (platform[name] if customer is not None else None)
        # #473: the reader too; #488: and Ultra low cost's ceiling.
        for name in (*SCAN_COST_FIELDS, "scan_reader", "mistral_model", "ulc_ceiling_paise")
    }
    draft = {**models.with_env_ulc(draft, settings)}  # #564 [V]: a platform draft's null is the server's
    # #463 review: the draft's Cost saving (null: the platform's), never past the switches.
    dynamic = (
        allowed["dynamic"]
        if "dynamic" not in given
        else platform["dynamic"]
        if draft_fields.dynamic is None
        else draft_fields.dynamic
    )
    draft["dynamic"] = bool(dynamic and allowed["cost_saving"])
    reading = models.scope_reading(rows, {**allowed, **draft}, settings=settings)
    lead = reading.lead.model
    careful = None
    careful_thinking = draft["careful_thinking"] or settings.ocr_retry_thinking_level
    careful_resolution = draft["careful_resolution"] or settings.ocr_retry_media_resolution
    if reading.careful is not None:  # #463 review: the level Careful's own model is sent
        sent = models.fit_level(reading.careful, careful_thinking, settings.ocr_retry_thinking_level)
        careful = (reading.careful.model, sent, careful_resolution)
    resolution = draft["scan_resolution"] or settings.ocr_media_resolution
    first = (lead, reading.thinking, resolution)
    asked_first = (lead, draft["scan_thinking"] or settings.ocr_thinking_level, resolution)
    if reading.scan_reader == MISTRAL:  # #473: Mistral reads every page at its one setting, alone
        first, careful = (read_by(reading.mistral_model), MISTRAL_THINKING, MISTRAL_RESOLUTION), None
        asked_first = first  # review, 2 Nov 2026: a Gemini level in the draft never hides its check
    if reading.ulc is not None:  # #488: once, at minimal, under its ceiling: never a Gemini scan's
        first = (accuracy.ulc_model(reading.ulc.paise, lead), MINIMAL, resolution)
        asked_first, draft["dynamic"] = first, False
    asked = (
        *asked_first,
        careful_thinking,
        careful_resolution,
        draft["page_max_output_tokens"] or PAGE_MAX_OUTPUT_TOKENS,
        draft["dynamic"],
    )
    lead_row = next((row for row in rows if row["id"] == lead), None)
    best = (models.max_thinking(lead_row), models.MAX_RESOLUTION)
    return _Plan(first, careful, best, asked, reading)


@router.post("/scan-cost")
def scan_cost(
    body: ScanCostBody, operator: OperatorSession, db: OperatorDb, context: Context
) -> dict[str, Any]:
    """#463: the approximate cost of a page at the draft's scan settings (module notes). The
    first read and, while Dynamic is on, Careful's re-read are each priced at what they would
    really be sent (model, thinking after Dynamic's rule, picture detail); the total is the
    first read plus the share read again times the re-read, or the first read alone. With
    ``quality`` (the scope's clean pages at these and at max settings) and, for an
    organisation, ``accuracy``: its latest done check at exactly these settings. #473: with
    Mistral in effect, ``fixed_price`` and Mistral's price a page."""
    customer = None if body.customer_id is None else str(body.customer_id)
    plan = _plan(db, operator, context, customer, body)
    first, careful = plan.first, plan.careful
    mistral = plan.reading.scan_reader == MISTRAL
    ceiling = plan.reading.ulc
    with db.operator() as conn:
        row = models_sql.scan_cost(conn, operator.operator_id, customer, first, careful, plan.best)
        check = (
            None
            if customer is None
            else models_sql.accuracy_check(conn, operator.operator_id, customer, plan.asked)
        )
        measured = (
            None
            if ceiling is None
            else models_sql.ulc_cost(conn, operator.operator_id, customer, plan.reading.lead.model)
        )
    if row is None or (ceiling is not None and measured is None):
        raise ApiError(401, "not_signed_in")
    ulc = None
    if ceiling is not None and measured is not None:
        ulc = {
            "ceiling_paise": ceiling.paise,
            "ceiling_usd": ceiling.usd,
            "pages": int(measured["pages"]),
            "usd_per_page": _per_page(measured["cost_usd"], measured["pages"]),
            "top_usd_per_page": _number(measured["top_usd_per_page"]),
            "limit_hits": int(measured["limit_hits"]),
        }
    first_usd = _per_page(row["first_usd"], row["first_pages"])
    first_read = _read(first, first_usd, row["first_pages"])
    re_read = None
    total = first_usd
    if careful is not None:
        share = None if not row["scan_calls"] else int(row["careful_calls"]) / int(row["scan_calls"])
        careful_usd = _per_page(row["careful_usd"], row["careful_pages"])
        re_read = {**_read(careful, careful_usd, row["careful_pages"]), "share": share}
        if first_usd is not None and share is not None and careful_usd is not None:
            total = first_usd + share * careful_usd
    token_pages = int(row["token_pages"])
    tokens = {
        "input_per_page": None if not token_pages else round(row["tokens_in"] / token_pages),
        "output_per_page": None if not token_pages else round(row["tokens_out"] / token_pages),
        "output_p99": None if row["output_p99"] is None else int(row["output_p99"]),
        "limit": plan.asked[5],
        "limit_hits": int(row["limit_hits"]),
        "pages": token_pages,
    }
    quality = {
        "clean_share": _share(row["clean_pages"], row["quality_pages"]),
        "pages": int(row["quality_pages"]),
        "max_clean_share": _share(row["max_clean_pages"], row["max_quality_pages"]),
        "max_pages": int(row["max_quality_pages"]),
    }
    return {
        "days": LEDGER_DAYS,
        "first_read": first_read,
        "re_read": re_read,
        "usd_per_page": total,
        "quality": quality,
        "tokens": tokens,
        "accuracy": None if check is None else _check_view(check),
        # #473: Mistral's fixed price a page; Gemini's is measured only.
        "fixed_price": mistral,
        "fixed_usd_per_page": MISTRAL_OCR_MODELS[plan.reading.mistral_model].usd_per_page
        if mistral
        else None,
        "ulc": ulc,  # #488: null unless Ultra low cost reads
    }


@router.post("/orgs/{customer_id}/accuracy-check", status_code=202)
def start_accuracy_check(
    customer_id: uuid.UUID,
    body: AccuracyCheckBody,
    operator: OperatorSession,
    db: OperatorDb,
    context: Context,
) -> dict[str, Any]:
    """#463: check the organisation's accuracy at the draft's scan settings against max settings
    (a fresh code first): a worker job reads up to ``pages`` of its recent model-read pages both
    ways and compares the words and numbers. 202 with the check's id; 400
    ``level_not_supported`` (the lead does not take the scan level, or Careful's model its
    level, #463 review; never for Mistral) or ``no_pages``, 409 ``check_running`` or
    ``mistral_not_set_up`` (#473), 429 ``rate_limited`` (ACCURACY_CHECKS_PER_HOUR on the
    platform)."""
    customer = str(customer_id)
    plan = _plan(db, operator, context, customer, body)
    lead, careful = plan.reading.lead, plan.reading.careful
    if plan.reading.scan_reader == MISTRAL:  # #473 review: Mistral takes no Gemini level
        if not context.settings.mistral_ready:
            raise ApiError(*_REFUSALS["mistral_not_set_up"])
    elif (lead.efforts and plan.asked[1] not in lead.efforts) or (
        careful is not None and careful.efforts and plan.asked[3] not in careful.efforts
    ):
        raise ApiError(400, "level_not_supported", _REFUSALS["level_not_supported"][2])
    step_up = _step_up(db, context, operator, body.code)
    with db.operator() as conn:
        outcome = models_sql.start_accuracy_check(
            conn, operator.operator_id, customer, plan.asked, body.pages, step=step_up.step
        )
    _refuse(db, operator, outcome.code, step_up)
    check_id = str(outcome.result_id)
    # The check's own job: its id is the message's document id too (no document is read whole).
    try:
        context.queue.enqueue(Job(customer, check_id, check_id=check_id))
    except Exception:
        accuracy.finish(context.db, customer, check_id, "failed")  # #463 review: never left running
        raise
    return {"id": check_id}


@router.get("/orgs/{customer_id}/accuracy-check/latest")
def latest_accuracy_check(
    customer_id: uuid.UUID, operator: OperatorSession, db: OperatorDb
) -> dict[str, Any]:
    """#463: the organisation's latest accuracy check, at any settings; 404 with none."""
    with db.operator() as conn:
        check = models_sql.accuracy_check(conn, operator.operator_id, str(customer_id))
    if check is None:
        raise ApiError(404, "not_found")
    return _check_view(check)


def _check_view(row: RowMapping) -> dict[str, Any]:
    """An accuracy check as the console shows it: settings and numbers, never text."""
    return {
        "id": str(row["id"]),
        "status": row["status"],
        "settings": {name: row[name] for name in ("model", *SCAN_COST_FIELDS)},
        "pages_checked": int(row["pages_checked"]),
        "pages_failed": int(row["pages_failed"]),
        "word_match": row["word_match"],
        "numbers_match": row["numbers_match"],
        "max_pages": int(row["max_pages"]),
        "cost_usd": _number(row["cost_usd"]),
        "started_at": iso(row["started_at"]),
        "finished_at": None if row["finished_at"] is None else iso(row["finished_at"]),
    }


def _limit(tokens: int | None) -> None:
    """400 ``limit_out_of_range`` for a page read's output limit outside 1024 to 65536."""
    if tokens is not None and not TOKENS_LOW <= tokens <= PAGE_MAX_OUTPUT_TOKENS:
        raise ApiError(*_REFUSALS["limit_out_of_range"])


def _ceiling(paise: int | None) -> None:
    """#488: 400 ``ceiling_out_of_range`` for Ultra low cost's ceiling outside 20 to 55 paise (0073)."""
    low, high = ULC_CEILING_PAISE
    if paise is not None and not low <= paise <= high:
        raise ApiError(*_REFUSALS["ceiling_out_of_range"])


def _platform(db: OperatorDatabase, operator: CurrentOperator, body: OrgAiBody) -> RowMapping | None:
    """#488: the platform defaults an organisation's save inherits from (its reader, its default
    scan model), read only when Ultra low cost may read after it (none for Gemini or Mistral)."""
    if body.scan_reader not in (None, ULTRA_LOW_COST):
        return None
    with db.operator() as conn:
        row = models_sql.ai_defaults(conn, operator.operator_id)
    if row is None:  # the operator was disabled meanwhile
        raise ApiError(401, "not_signed_in")
    return row


def _needs_gemini(
    db: OperatorDatabase,
    operator: CurrentOperator,
    body: OrgAiBody | DefaultsBody,
    settings: Settings,
    *,
    platform: RowMapping | None,
) -> None:
    """#488: 409 ``ulc_needs_gemini`` when Ultra low cost would read after the save (``body``'s
    reader, else the ``platform`` defaults' an organisation inherits) and its lead (as
    ``models._ulc_reading`` picks it: the default scan model in effect when a Gemini with a known
    price, else OCR_MODEL) is not a catalogue Gemini with a known price that takes minimal
    thinking. One catalogue read, only while Ultra low cost would read. Review of #564: with no
    reader saved, OCR_ULTRA_LOW_COST's (``models.with_env_ulc``)."""
    reader = (
        body.scan_reader
        or (None if platform is None else platform["scan_reader"])
        or (ULTRA_LOW_COST if settings.ocr_ultra_low_cost else None)
    )
    if reader != ULTRA_LOW_COST:
        return
    default = body.scan_default
    if not body.scan_models and platform is not None:
        default = platform["scan_default"]
    with db.operator() as conn:
        catalogue = {row["id"]: row for row in models_sql.list_models(conn, operator.operator_id)}
    chosen = catalogue.get(default or "")
    lead = chosen
    if lead is None or not models.ulc_priced(lead):
        lead = catalogue.get(settings.ocr_model)
    if lead is None or not models.ulc_priced(lead) or MINIMAL not in lead["efforts"]:
        status, code, message = _REFUSALS["ulc_needs_gemini"]
        why = None if chosen is None else models.ulc_why(chosen)
        if chosen is not None and why is not None:  # the default scan model's reason
            message = f"{message} The default scan model ({chosen['name']}): {why}"
        raise ApiError(status, code, message)


def _share(part: int, whole: int) -> float | None:
    """``part`` of ``whole``; null with none."""
    return None if not whole else int(part) / int(whole)


def _per_page(usd: Decimal | None, pages: int) -> float | None:
    """The measured dollars a page: null with no page (never guessed)."""
    return None if usd is None or not pages else float(usd) / int(pages)


def _read(sent: models_sql.Sent, usd_per_page: float | None, pages: int) -> dict[str, Any]:
    model, thinking, resolution = sent
    return {
        "model": model,
        "thinking": thinking,
        "resolution": resolution,
        "usd_per_page": usd_per_page,
        "pages": int(pages),
    }


@router.get("/feature-switches")
def get_feature_switches(operator: OperatorSession, db: OperatorDb) -> list[dict[str, Any]]:
    """Every product feature, on or off for every organisation, and how many use it now."""
    with db.operator() as conn:
        rows = models_sql.feature_switches(conn, operator.operator_id)
    return [
        {
            "feature": row["feature"],
            "enabled": bool(row["enabled"]),
            "organisations_using": int(row["organisations_using"]),
        }
        for row in rows
    ]


@router.put("/feature-switches/{feature}")
def set_feature_switch(
    feature: Feature, body: SwitchBody, operator: OperatorSession, db: OperatorDb, context: Context
) -> list[dict[str, Any]]:
    """Flip one feature for every organisation (a fresh code first); the list comes back."""
    step_up = _step_up(db, context, operator, body.code)
    with db.operator() as conn:
        outcome = models_sql.set_feature_switch(
            conn, operator.operator_id, feature, enabled=body.enabled, step=step_up.step
        )
    _refuse(db, operator, outcome, step_up)
    return get_feature_switches(operator, db)


@router.get("/orgs/{customer_id}/feature-switches")
def get_org_feature_switches(
    customer_id: uuid.UUID, operator: OperatorSession, db: OperatorDb
) -> list[dict[str, Any]]:
    """One organisation's switches, each with the platform's (off there wins)."""
    with db.operator() as conn:
        rows = models_sql.org_feature_switches(conn, operator.operator_id, str(customer_id))
    if not rows:
        raise ApiError(404, "not_found")
    return [
        {
            "feature": row["feature"],
            "enabled": bool(row["enabled"]),
            "platform_enabled": bool(row["platform_enabled"]),
        }
        for row in rows
    ]


@router.put("/orgs/{customer_id}/feature-switches/{feature}")
def set_org_feature_switch(
    customer_id: uuid.UUID,
    feature: OrgFeature,
    body: SwitchBody,
    operator: OperatorSession,
    db: OperatorDb,
    context: Context,
) -> list[dict[str, Any]]:
    """Flip one feature for one organisation (a fresh code first); its list comes back."""
    step_up = _step_up(db, context, operator, body.code)
    with db.operator() as conn:
        outcome = models_sql.set_org_feature_switch(
            conn, operator.operator_id, str(customer_id), feature, enabled=body.enabled, step=step_up.step
        )
    _refuse(db, operator, outcome, step_up)
    return get_org_feature_switches(customer_id, operator, db)


@router.get("/session-timeout")
def get_session_timeout(operator: OperatorSession, db: OperatorDb, context: Context) -> dict[str, Any]:
    """The platform's sign-in idle timeout in hours (null: the default, SESSION_HOURS)."""
    row = _timeout(db, operator, None)
    return {"hours": row["platform_hours"], "default_hours": context.settings.session_hours}


@router.put("/session-timeout")
def set_session_timeout(
    body: TimeoutBody, operator: OperatorSession, db: OperatorDb, context: Context
) -> dict[str, Any]:
    """Set it (a fresh code first); every organisation without its own follows at its next request."""
    step_up = _step_up(db, context, operator, body.code)
    with db.operator() as conn:
        outcome = models_sql.set_session_timeout(
            conn, operator.operator_id, None, body.hours, step=step_up.step
        )
    _refuse(db, operator, outcome, step_up)
    return get_session_timeout(operator, db, context)


@router.get("/orgs/{customer_id}/session-timeout")
def get_org_session_timeout(
    customer_id: uuid.UUID, operator: OperatorSession, db: OperatorDb, context: Context
) -> dict[str, Any]:
    """One organisation's own idle timeout (null: follow the platform), with the platform's."""
    row = _timeout(db, operator, str(customer_id))
    return {
        "hours": row["org_hours"],
        "platform_hours": row["platform_hours"],
        "default_hours": context.settings.session_hours,
    }


@router.put("/orgs/{customer_id}/session-timeout")
def set_org_session_timeout(
    customer_id: uuid.UUID, body: TimeoutBody, operator: OperatorSession, db: OperatorDb, context: Context
) -> dict[str, Any]:
    """Set one organisation's (null: follow the platform), a fresh code first."""
    step_up = _step_up(db, context, operator, body.code)
    with db.operator() as conn:
        outcome = models_sql.set_session_timeout(
            conn, operator.operator_id, str(customer_id), body.hours, step=step_up.step
        )
    _refuse(db, operator, outcome, step_up)
    return get_org_session_timeout(customer_id, operator, db, context)


_FOLDER_DEFAULTS = {
    "max_files": DEFAULT_MAX_FILES,
    "max_gb": DEFAULT_MAX_GB,
    "max_folders": DEFAULT_MAX_FOLDERS,
}


def _folder_limits(db: OperatorDatabase, operator: CurrentOperator, customer_id: str | None) -> RowMapping:
    with db.operator() as conn:
        row = models_sql.folder_upload_limits(conn, operator.operator_id, customer_id)
    if row is None:  # an unknown organisation, or the operator was disabled meanwhile
        raise ApiError(404, "not_found") if customer_id else ApiError(401, "not_signed_in")
    return row


def _limits_of(row: RowMapping, prefix: str) -> dict[str, Any]:
    return {name: row[f"{prefix}_{name}"] for name in _FOLDER_DEFAULTS}


@router.get("/folder-upload-limits")
def get_folder_upload_limits(operator: OperatorSession, db: OperatorDb) -> dict[str, Any]:
    """The platform's folder upload limits (null: the default)."""
    return {**_limits_of(_folder_limits(db, operator, None), "platform"), "defaults": _FOLDER_DEFAULTS}


@router.put("/folder-upload-limits")
def set_folder_upload_limits(
    body: FolderLimitsBody, operator: OperatorSession, db: OperatorDb, context: Context
) -> dict[str, Any]:
    """Set them (a fresh code first); every organisation without its own follows at its next upload."""
    step_up = _step_up(db, context, operator, body.code)
    with db.operator() as conn:
        outcome = models_sql.set_folder_upload_limits(
            conn, operator.operator_id, None, body.max_files, body.max_gb, body.max_folders, step=step_up.step
        )
    _refuse(db, operator, outcome, step_up)
    return get_folder_upload_limits(operator, db)


@router.get("/orgs/{customer_id}/folder-upload-limits")
def get_org_folder_upload_limits(
    customer_id: uuid.UUID, operator: OperatorSession, db: OperatorDb
) -> dict[str, Any]:
    """One organisation's own limits (null: follow the platform), with the platform's and the defaults."""
    row = _folder_limits(db, operator, str(customer_id))
    return {
        "own": _limits_of(row, "org"),
        "platform": _limits_of(row, "platform"),
        "defaults": _FOLDER_DEFAULTS,
    }


@router.put("/orgs/{customer_id}/folder-upload-limits")
def set_org_folder_upload_limits(
    customer_id: uuid.UUID,
    body: FolderLimitsBody,
    operator: OperatorSession,
    db: OperatorDb,
    context: Context,
) -> dict[str, Any]:
    """Set one organisation's (all null: follow the platform), a fresh code first."""
    step_up = _step_up(db, context, operator, body.code)
    with db.operator() as conn:
        outcome = models_sql.set_folder_upload_limits(
            conn,
            operator.operator_id,
            str(customer_id),
            body.max_files,
            body.max_gb,
            body.max_folders,
            step=step_up.step,
        )
    _refuse(db, operator, outcome, step_up)
    return get_org_folder_upload_limits(customer_id, operator, db)


def _storage_limit(
    db: OperatorDatabase, operator: CurrentOperator, customer_id: str | None
) -> dict[str, Any]:
    """This level's storage limit with the platform's, the one in effect, the default, the most
    and the bytes used (#516; module notes)."""
    with db.operator() as conn:
        row = models_sql.storage_limit(conn, operator.operator_id, customer_id)
    if row is None:  # an unknown organisation, or the operator was disabled meanwhile
        raise ApiError(404, "not_found") if customer_id else ApiError(401, "not_signed_in")
    own, platform = row["org_storage_gb"], row["platform_storage_gb"]
    storage = platform if customer_id is None else own
    return {
        "storage_gb": storage,
        "platform_gb": platform,
        "effective_gb": storage or platform or DEFAULT_STORAGE_GB,
        "default_gb": DEFAULT_STORAGE_GB,
        "most_gb": MOST_STORAGE_GB,
        "used_bytes": int(row["used_bytes"]),
    }


def _check_storage_gb(body: StorageLimitBody) -> None:
    """The limit asked is 1 to MOST_STORAGE_GB, checked before the code is used. #559 (UAT ST-03):
    outside it, the words the screen says (StorageLimit.tsx), not "The request is not valid."."""
    if body.storage_gb is not None and not 1 <= body.storage_gb <= MOST_STORAGE_GB:
        raise ApiError(
            400, "bad_request", f"Enter a whole number from 1 to {MOST_STORAGE_GB:,}, or leave it empty."
        )


@router.get("/storage-limit")
def get_storage_limit(operator: OperatorSession, db: OperatorDb) -> dict[str, Any]:
    """The platform's storage limit (null: the default)."""
    return _storage_limit(db, operator, None)


@router.put("/storage-limit")
def set_storage_limit(
    body: StorageLimitBody, operator: OperatorSession, db: OperatorDb, context: Context
) -> dict[str, Any]:
    """Set it (a fresh code first); every organisation without its own follows at its next upload."""
    _check_storage_gb(body)
    step_up = _step_up(db, context, operator, body.code)
    with db.operator() as conn:
        outcome = models_sql.set_storage_limit(
            conn, operator.operator_id, None, body.storage_gb, step=step_up.step
        )
    _refuse(db, operator, outcome, step_up)
    return _storage_limit(db, operator, None)


@router.get("/orgs/{customer_id}/storage-limit")
def get_org_storage_limit(
    customer_id: uuid.UUID, operator: OperatorSession, db: OperatorDb
) -> dict[str, Any]:
    """One organisation's own limit (null: follow the platform) and the bytes its documents hold."""
    return _storage_limit(db, operator, str(customer_id))


@router.put("/orgs/{customer_id}/storage-limit")
def set_org_storage_limit(
    customer_id: uuid.UUID,
    body: StorageLimitBody,
    operator: OperatorSession,
    db: OperatorDb,
    context: Context,
) -> dict[str, Any]:
    """Set one organisation's (null: follow the platform), a fresh code first."""
    _check_storage_gb(body)
    step_up = _step_up(db, context, operator, body.code)
    with db.operator() as conn:
        outcome = models_sql.set_storage_limit(
            conn, operator.operator_id, str(customer_id), body.storage_gb, step=step_up.step
        )
    _refuse(db, operator, outcome, step_up)
    return _storage_limit(db, operator, str(customer_id))


_SPEED_WORDS = {
    "model_rpm": "model requests per minute",
    "documents_at_once": "documents read at the same time",
    "pages_at_once": "pages of one document read at the same time",
}


def _speed_most(settings: Settings) -> dict[str, int]:
    """The most this server allows for each speed control, set when it is deployed."""
    return {
        "model_rpm": settings.model_rpm,
        "documents_at_once": settings.worker_replicas,
        "pages_at_once": settings.ocr_page_concurrency,
    }


@router.get("/speed")
def get_speed(operator: OperatorSession, db: OperatorDb, context: Context) -> dict[str, Any]:
    """Each speed control: the saved value (null: the server's setting) and the server's own."""
    with db.operator() as conn:
        row = models_sql.speed(conn, operator.operator_id)
    if row is None:  # the operator was disabled meanwhile
        raise ApiError(401, "not_signed_in")
    return {name: {"value": row[name], "most": most} for name, most in _speed_most(context.settings).items()}


@router.put("/speed")
def set_speed(body: SpeedBody, operator: OperatorSession, db: OperatorDb, context: Context) -> dict[str, Any]:
    """Set all three (null: the server's setting), each at most the server's own (400 before a
    code is used up), with a fresh code; the next admission, job or page uses them."""
    for name, most in _speed_most(context.settings).items():
        value = getattr(body, name)
        if value is not None and value > most:
            raise ApiError(
                400, "bad_request", f"The most this server allows for {_SPEED_WORDS[name]} is {most}."
            )
    step_up = _step_up(db, context, operator, body.code)
    with db.operator() as conn:
        outcome = models_sql.set_speed(
            conn,
            operator.operator_id,
            body.model_rpm,
            body.documents_at_once,
            body.pages_at_once,
            step=step_up.step,
        )
    _refuse(db, operator, outcome, step_up)
    return get_speed(operator, db, context)


def _timeout(db: OperatorDatabase, operator: CurrentOperator, customer_id: str | None) -> RowMapping:
    with db.operator() as conn:
        row = models_sql.session_timeout(conn, operator.operator_id, customer_id)
    if row is None:  # an unknown organisation, or the operator was disabled meanwhile
        raise ApiError(404, "not_found") if customer_id else ApiError(401, "not_signed_in")
    return row


@router.get("/test-model")
def get_test_model(operator: OperatorSession, db: OperatorDb) -> dict[str, Any]:
    """The platform test model, and what ran on it since it started (real spend rows only)."""
    with db.operator() as conn:
        row = models_sql.current_test_model(conn, operator.operator_id)
    if row is None:
        return {
            "active": False,
            "scan_model": None,
            "chat_model": None,
            "note": None,
            "started_at": None,
            "since": None,
        }
    return {
        "active": True,
        "scan_model": row["scan_model"],
        "chat_model": row["chat_model"],
        "note": row["note"],
        "started_at": iso(row["started_at"]),
        "since": {
            "pages": int(row["pages"]),
            "questions": int(row["questions"]),
            "cost_usd": _number(row["cost_usd"]),  # what OpenRouter reported; null: none reported
            "avg_seconds_per_page": row["seconds_per_page"],
        },
    }


@router.post("/test-model")
def start_test_model(
    body: TestModelBody, operator: OperatorSession, db: OperatorDb, context: Context
) -> dict[str, Any]:
    """Every organisation scans with ``scan_model`` and answers with ``chat_model`` (either
    may be null: that area stays each organisation's own) until the undo."""
    named = [model for model in (body.scan_model, body.chat_model) if model is not None]
    if not named or not all(valid_model_id(model) for model in named):
        raise ApiError(400, "bad_request", "Choose a scanning model, an answering model or both.")
    with db.operator() as conn:
        catalogue = {row["id"]: row for row in models_sql.list_models(conn, operator.operator_id)}
    if not all(_callable(catalogue.get(model), context.settings) for model in named):
        raise ApiError(*_REFUSALS["unavailable"])
    step_up = _step_up(db, context, operator, body.code)
    with db.operator() as conn:
        outcome = models_sql.set_test_model(
            conn,
            operator.operator_id,
            scan_model=body.scan_model,
            chat_model=body.chat_model,
            note=body.note,
            scan_prompt=READER_PROMPT_VERSION,
            chat_prompt=ANSWER_PROMPT_HASH,
            step=step_up.step,
        )
    _refuse(db, operator, outcome, step_up)
    return get_test_model(operator, db)


@router.delete("/test-model")
def undo_test_model(
    body: StepUpBody, operator: OperatorSession, db: OperatorDb, context: Context
) -> dict[str, Any]:
    """Every organisation back to its own models, exactly as before (a fresh code first)."""
    step_up = _step_up(db, context, operator, body.code)
    with db.operator() as conn:
        outcome = models_sql.clear_test_model(conn, operator.operator_id, step=step_up.step)
    _refuse(db, operator, outcome, step_up)
    return get_test_model(operator, db)


def _callable(row: RowMapping | None, settings: Settings) -> bool:
    """The model is in the catalogue, its maker in OPENROUTER_MODEL_VENDORS and one of its
    providers in OPENROUTER_PROVIDERS (S1), so the app can call it."""
    return (
        row is not None
        and vendor(row["id"]) in settings.openrouter_model_vendors
        and any(slug in settings.openrouter_providers for slug in row["providers"])
    )


@router.get("/orgs/{customer_id}/model-rates")
def get_org_model_rates(customer_id: uuid.UUID, operator: OperatorSession, db: OperatorDb) -> dict[str, Any]:
    """The organisation RPM limit and each user's explicit override; no customer content."""
    with db.operator() as conn:
        rows = models_sql.model_rates(conn, operator.operator_id, str(customer_id))
    if not rows:
        raise ApiError(404, "not_found")
    return {
        "organisation_rpm": rows[0]["org_rpm"],
        "users": [
            {"id": str(row["user_id"]), "email": row["email"], "rpm": row["user_rpm"]}
            for row in rows
            if row["user_id"] is not None
        ],
    }


@router.put("/orgs/{customer_id}/model-rates")
def set_org_model_rate(
    customer_id: uuid.UUID, body: ModelRateBody, operator: OperatorSession, db: OperatorDb, context: Context
) -> dict[str, Any]:
    """Set an organisation cap (1-600 RPM), or clear it to use fair platform sharing."""
    step_up = _step_up(db, context, operator, body.code)
    with db.operator() as conn:
        outcome = models_sql.set_model_rate(
            conn, operator.operator_id, str(customer_id), None, body.rpm, step_up.step
        )
    _refuse(db, operator, outcome, step_up)
    return get_org_model_rates(customer_id, operator, db)


@router.put("/orgs/{customer_id}/users/{user_id}/model-rate")
def set_user_model_rate(
    customer_id: uuid.UUID,
    user_id: uuid.UUID,
    body: UserModelRateBody,
    operator: OperatorSession,
    db: OperatorDb,
    context: Context,
) -> dict[str, Any]:
    """Set a user cap (1-120 RPM), or null to inherit its organisation's cap."""
    step_up = _step_up(db, context, operator, body.code)
    with db.operator() as conn:
        outcome = models_sql.set_model_rate(
            conn, operator.operator_id, str(customer_id), str(user_id), body.rpm, step_up.step
        )
    _refuse(db, operator, outcome, step_up)
    return get_org_model_rates(customer_id, operator, db)


@router.get("/usage")
def get_usage(
    operator: OperatorSession,
    db: OperatorDb,
    context: Context,
    start: Annotated[str | None, Query(alias="from", max_length=10)] = None,
    end: Annotated[str | None, Query(alias="to", max_length=10)] = None,
) -> dict[str, Any]:
    """Spend per organisation and model (SP3): ids, names of organisations and models,
    numbers. No user, no document."""
    zone = context.settings.timezone
    today = context.clock().astimezone(ZoneInfo(zone)).date()
    first, last = _day(start, today.replace(day=1)), _day(end, today)
    if last < first or (last - first).days >= MAX_DAYS:
        raise ApiError(400, "bad_request", f"Choose up to {MAX_DAYS} days, the first on or before the last.")
    with db.operator() as conn:
        rows = models_sql.usage(
            conn,
            operator.operator_id,
            day_start(first.isoformat(), zone),
            day_start((last + timedelta(days=1)).isoformat(), zone),
        )
    return {"from": first.isoformat(), "to": last.isoformat(), "items": [_usage(row) for row in rows]}


def _refuse(
    db: OperatorDatabase, operator: CurrentOperator, code: str, step_up: StepUp | None = None
) -> None:
    """Nothing for ``done`` and ``unchanged``; the reply for every other outcome."""
    _check(db, operator, Outcome(code, None), step_up)
    if code in _REFUSALS:
        status, error, message = _REFUSALS[code]
        raise ApiError(status, error, message)


def _day(value: str | None, default: date) -> date:
    if value is None:
        return default
    try:
        return date.fromisoformat(value)
    except ValueError:
        raise ApiError(400, "bad_request", "Give dates as YYYY-MM-DD.") from None


def _number(value: Decimal | float | None) -> float | None:
    return None if value is None else float(value)


def _choice(body: OrgAiBody | DefaultsBody, settings: Settings, saved: Callable[[], str | None]) -> AiChoice:
    """The save's models and levels, after every named model is an OpenRouter model ID
    (400) and Mistral is newly chosen only on a server where it is set up (#473: 409
    ``mistral_not_set_up``; ``saved()`` the reader saved now, asked only then), both before a
    code is used up."""
    named = [*body.scan_models, *body.chat_models, body.scan_default, body.chat_default, body.backup_model]
    if not all(valid_model_id(model) for model in named if model is not None):
        raise ApiError(400, "bad_request", "A model is not an OpenRouter model ID.")
    _limit(body.page_max_output_tokens)
    if body.daily_page_limit is not None and not 1 <= body.daily_page_limit <= MAX_PAGES_A_DAY:
        raise ApiError(*_REFUSALS["pages_out_of_range"])  # #478, before a code is used up
    _ceiling(body.ulc_ceiling_paise)  # #488, before a code is used up
    if (body.backup_model is not None) != (body.backup_reader in (OPENROUTER, ULTRA_LOW_COST)):
        raise ApiError(*_REFUSALS["backup_reader_incomplete"])  # #664, before a code is used up
    if body.scan_reader == MISTRAL and not settings.mistral_ready and saved() != MISTRAL:
        raise ApiError(*_REFUSALS["mistral_not_set_up"])
    return AiChoice(
        scan_models=body.scan_models,
        scan_default=body.scan_default,
        chat_models=body.chat_models,
        chat_default=body.chat_default,
        scan_thinking=body.scan_thinking,
        chat_levels=body.chat_levels,
        chat_default_level=body.chat_default_level,
        dynamic=body.dynamic,
        scan_resolution=body.scan_resolution,
        careful_thinking=body.careful_thinking,
        careful_resolution=body.careful_resolution,
        page_max_output_tokens=body.page_max_output_tokens,
        scan_reader=body.scan_reader,
        mistral_model=body.mistral_model,
        daily_page_limit=body.daily_page_limit,
        ulc_ceiling_paise=body.ulc_ceiling_paise,
        backup_reader=body.backup_reader,
        backup_model=body.backup_model,
    )


def _backup_needs_gemini(
    db: OperatorDatabase, operator: CurrentOperator, body: OrgAiBody | DefaultsBody
) -> None:
    """#664: 409 ``ulc_needs_gemini`` when the backup reader is Ultra low cost and its model is not
    a catalogue Gemini with a known price that takes minimal thinking (Ultra low cost's own rule,
    with the model's reason). One catalogue read, only then; before a code is used up."""
    if body.backup_reader != ULTRA_LOW_COST:
        return
    with db.operator() as conn:
        catalogue = {row["id"]: row for row in models_sql.list_models(conn, operator.operator_id)}
    chosen = catalogue.get(body.backup_model or "")
    if chosen is None:  # one not in the catalogue is the save's own ``unavailable``
        return
    why = models.ulc_why(chosen)
    if why:
        status, code, message = _REFUSALS["ulc_needs_gemini"]
        raise ApiError(status, code, f"{message} The backup reader ({chosen['name']}): {why}")


def _ai(row: RowMapping) -> dict[str, Any]:
    """An organisation's own settings or the platform defaults, as saved."""
    return {
        "scan_models": list(row["scan_models"]),
        "scan_default": row["scan_default"],
        "chat_models": list(row["chat_models"]),
        "chat_default": row["chat_default"],
        "scan_thinking": row["scan_thinking"],
        "scan_resolution": row["scan_resolution"],
        "careful_thinking": row["careful_thinking"],
        "careful_resolution": row["careful_resolution"],
        "page_max_output_tokens": row["page_max_output_tokens"],
        "scan_reader": row["scan_reader"],  # #473
        "mistral_model": row["mistral_model"],
        "daily_page_limit": row["daily_page_limit"],  # #478
        "ulc_ceiling_paise": row["ulc_ceiling_paise"],  # #488
        "backup_reader": row["backup_reader"],  # #664
        "backup_model": row["backup_model"],
        "chat_levels": None if row["chat_levels"] is None else list(row["chat_levels"]),
        "chat_default_level": row["chat_default_level"],
        "dynamic": row["dynamic"],
    }


def _env(settings: Settings) -> dict[str, Any]:
    """What the server uses when the platform defaults name no model or scan setting. #473: the
    code's reader and Mistral model, whether Mistral is set up here and what is missing (``key``
    first, else ``zdr``; never the key itself), and the Mistral models with their price a page.
    #564 [V]: the reader is OCR_ULTRA_LOW_COST's while it is on, read at ``switch_ceiling_paise``
    (OCR_PAGE_CEILING_PAISE; null while off) where no reader is saved; a saved Ultra low cost with
    no ceiling reads at ``ulc_ceiling_paise`` (ULC_DEFAULT_PAISE), switch or not (review of #564)."""
    missing = "key" if settings.mistral_api_key is None else None if settings.mistral_zdr_approved else "zdr"
    return {
        "scan_model": settings.ocr_model,
        "chat_model": settings.model_chat,
        "scan_thinking": settings.ocr_thinking_level,
        "scan_resolution": settings.ocr_media_resolution,  # #463
        "careful_thinking": settings.ocr_retry_thinking_level,
        "careful_resolution": settings.ocr_retry_media_resolution,
        "page_max_output_tokens": PAGE_MAX_OUTPUT_TOKENS,
        # #564 [V]: production's OCR_ULTRA_LOW_COST and OCR_PAGE_CEILING_PAISE, while on.
        "scan_reader": ULTRA_LOW_COST if settings.ocr_ultra_low_cost else OPENROUTER,
        "ulc_ceiling_paise": ULC_DEFAULT_PAISE,
        "switch_ceiling_paise": settings.ocr_page_ceiling_paise if settings.ocr_ultra_low_cost else None,
        "mistral_model": DEFAULT_MISTRAL_MODEL,
        "mistral_ready": settings.mistral_ready,
        "mistral_missing": missing,
        "mistral_models": [
            {"id": m.id, "name": m.name, "usd_per_page": m.usd_per_page, "has_blocks": m.has_blocks}
            for m in MISTRAL_OCR_MODELS.values()
        ],
    }


def _mistral_status(row: RowMapping) -> dict[str, Any]:
    """#473: whether Mistral stopped reading pages: ``code`` mistral_key (a key or credit problem),
    mistral_setup (chosen but not set up here) or mistral (not answering), from the newest wait;
    ``since`` when the first waiting document waited; how many documents wait. No worker memory:
    the runs that waited on it say so."""
    code = row["code"]
    return {
        "stopped": int(row["documents_waiting"]) > 0,
        "code": None if code is None else code.removesuffix("_unavailable"),
        "since": None if row["since"] is None else iso(row["since"]),
        "documents_waiting": int(row["documents_waiting"]),
    }


def _model(row: RowMapping, settings: Settings) -> dict[str, Any]:
    return {
        "id": row["id"],
        "name": row["name"],
        "price_in_usd_per_m": float(row["price_in_usd_per_m"]),
        "price_out_usd_per_m": float(row["price_out_usd_per_m"]),
        "providers": list(row["providers"]),
        "efforts": list(row["efforts"]),
        "expires_on": None if row["expires_on"] is None else row["expires_on"].isoformat(),
        "eligible": bool(row["eligible"]),
        "seen_at": iso(row["seen_at"]),
        "verified": row["verified_prompt_version"] == READER_PROMPT_VERSION,
        "verified_at": None if row["verified_at"] is None else iso(row["verified_at"]),
        "tokens_per_page_in": row["tokens_per_page_in"],
        "tokens_per_page_out": row["tokens_per_page_out"],
        # MC3: the average tokens a page took in the last 30 days of the ledger (every
        # organisation's, numbers only); null with none.
        "ledger_tokens_in": _number(row["ledger_tokens_in"]),
        "ledger_tokens_out": _number(row["ledger_tokens_out"]),
        # The real cost OpenRouter reported over the same 30 days (0033): scanned pages and
        # answered questions, those with a reported cost, that cost (null: none reported)
        # and the calls whose cost is not known (never guessed from the list price).
        "scan_pages": int(row["scan_pages"]),
        "scan_pages_costed": int(row["scan_pages_costed"]),
        "scan_cost_usd": _number(row["scan_cost_usd"]),
        "scan_cost_unknown_calls": int(row["scan_cost_unknown_calls"]),
        "answer_questions": int(row["answer_questions"]),
        "answer_questions_costed": int(row["answer_questions_costed"]),
        "answer_cost_usd": _number(row["answer_cost_usd"]),
        "answer_cost_unknown_calls": int(row["answer_cost_unknown_calls"]),
        "enabled_at": None if row["enabled_at"] is None else iso(row["enabled_at"]),
        # C11: may it answer questions, and did verify-models pass the answer prompt.
        "chat_eligible": bool(row["chat_eligible"]),
        "chat_verified": row["chat_verified_prompt"] == ANSWER_PROMPT_HASH,
        # S1: a maker outside OPENROUTER_MODEL_VENDORS is never used, even when enabled
        # (google and anthropic by default, Tushar 15 Oct 2026).
        "vendor_allowed": vendor(row["id"]) in settings.openrouter_model_vendors,
        # #488: can it lead Ultra low cost, and the one reason it can't (null: it can).
        "ulc_ready": models.ulc_why(row) is None,
        "ulc_why": models.ulc_why(row),
        # #647: the parameters its approved endpoints take (null: not read yet).
        "parameters": None if row["parameters"] is None else list(row["parameters"]),
    }


def _usage(row: RowMapping) -> dict[str, Any]:
    """One organisation's use of one model: never a user or a document."""
    return {
        "organisation_id": str(row["customer_id"]),
        "organisation_name": row["customer_name"],
        "model": row["model"],
        "calls": int(row["calls"]),
        "cached_calls": int(row["cached_calls"]),
        "input_tokens": int(row["input_tokens"]),
        "output_tokens": int(row["output_tokens"]),
        "reasoning_tokens": int(row["reasoning_tokens"]),
        "cost_usd": _number(row["cost_usd"]),
        "cost_estimated": bool(row["cost_estimated"]),
        "cost_part_unknown": bool(row["cost_part_unknown"]),  # the sum counts the known costs only
    }
