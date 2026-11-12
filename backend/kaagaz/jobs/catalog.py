"""The model catalogue (model choice, council 11 Oct 2026, MC1, MC3): which OpenRouter
models a scan may be read with, refreshed from OpenRouter's live lists, never a list of
names in code.

``fetch`` reads ``GET /models``, then for each candidate ``GET /models/{id}/endpoints``,
and ``GET /endpoints/zdr`` once, through the client's ``probe`` (no proxy, no redirect, the
key in its header only), each GET held to ``GET_TIMEOUT_SECONDS``, the first
``MAX_CANDIDATES`` candidates only, ``ENDPOINTS_PAUSE_SECONDS`` apart (no burst of
requests, no 429 storm). A model is **eligible** when it passes every rule (facts file,
"Catalogue filter rules"):

1. its id is an OpenRouter model ID with no ``:variant`` (``MODEL_ID``);
2. it takes image input and returns text only;
3. it lists ``response_format`` and ``structured_outputs``;
4. it has no expiration date, or one more than ``EXPIRY_MARGIN`` (30 days) away;
5. one of its endpoints is a zero-data-retention one (in ``/endpoints/zdr``) with
   ``status`` 0 that lists ``structured_outputs``, whose base slug (the tag before any
   ``/``) is in OPENROUTER_PROVIDERS, the processors Vrushit approved;
6. its ``reasoning.supported_efforts`` include OCR_THINKING_LEVEL and
   OCR_RETRY_THINKING_LEVEL, or it lists no efforts at all (Tushar, 18 Oct 2026: "no
   thinking levels", saved as an empty list and always called with no reasoning parameter);
7. its vendor, the part of its id before "/", is in OPENROUTER_MODEL_VENDORS (features
   25-27, S1; default google, anthropic, qwen and meta-llama, Tushar's decisions of 15 and
   18 Oct 2026).

A model is **chat eligible** (features 25-27, C11), may draft answers, by rules 1, 3, 4,
5 and 7 and, for 2 and 6, text in and text out only and CHAT_EFFORT among its efforts (or
no efforts at all).

A ``verify-models --record`` pass is kept apart (``verified_*`` for scans,
``chat_verified_*`` for answers, written by the owner): the operator can enable only an
eligible, verified model (0023, 0026).

**Everything OpenRouter sends is untrusted.** An id must match ``MODEL_ID`` before it is
put in a path; a name loses its control and format characters and is clamped to
``MAX_NAME``; a price must be a finite number at least 0 that fits ``numeric(12,6)``; a
provider slug must match ``PROVIDER_SLUG`` and an effort ``EFFORT``, at most ``MAX_LIST``
of each. A model breaking any of these is left out, as if not listed.

``save`` upserts the models that pass the scan or the answer rules but 5 and 7 (their
``eligible`` and ``chat_eligible`` from those) into ``model_catalog``, writing the refresh
columns only, and sets both false on every other row: rows are never deleted. A
candidate whose endpoint list can't be read is skipped, its row left as it was. A refresh
that cannot read ``/models`` or ``/endpoints/zdr`` changes nothing (``CatalogUnavailable``),
so an outage never makes the catalogue empty.

``RefreshCatalog`` runs it between jobs once every ``REFRESH_EVERY`` (a day), under the
sweep lock (PRD 5 Q3), and again after ``RETRY_AFTER_FAILURE`` (an hour) when a pass
failed; a worker that starts within a day of the last refresh (the newest ``seen_at``)
waits for the rest of that day. ``python -m kaagaz.cli refresh-models`` runs it now. Logs
hold codes and counts only.

Cost per refresh: 2 + C requests for the C candidates (C at most MAX_CANDIDATES), at least
(C - 1) x ENDPOINTS_PAUSE_SECONDS apart, and one statement per candidate.
"""

import logging
import math
import re
import time
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any, Protocol

from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from kaagaz.ai.openrouter import Probe
from kaagaz.db import models
from kaagaz.db.engine import Database, unavailable
from kaagaz.ingestion.errors import BackendUnavailable
from kaagaz.jobs.fair_share import SweepLock, no_lock
from kaagaz.jobs.worker import RunsJobs
from kaagaz.settings import MODEL_ID, PROVIDER_SLUG, Settings, valid_model_id

logger = logging.getLogger("kaagaz.worker")

MODELS_PATH = "/models"
ZDR_PATH = "/endpoints/zdr"
REFRESH_EVERY = timedelta(days=1)
# A pass that failed is tried again this soon, not a day later.
RETRY_AFTER_FAILURE = timedelta(hours=1)
# Each GET's own time limit: a slow OpenRouter never holds the worker for MODEL_TIMEOUT_SECONDS
# a request.
GET_TIMEOUT_SECONDS = 15.0
# The most candidates whose endpoints are asked for (OpenRouter lists a few dozen today).
MAX_CANDIDATES = 200
# The pause between two candidates' endpoint requests.
ENDPOINTS_PAUSE_SECONDS = 0.25
EXPIRY_MARGIN = timedelta(days=30)
# 0023's CHECKs: a name of 1 to 200 characters, at most 16 providers and efforts, prices
# in numeric(12,6).
MAX_NAME = 200
MAX_LIST = 16
MAX_PRICE_PER_M = 999_999.999999
TOKENS_PER_M = 1_000_000
STRUCTURED = "structured_outputs"
NEEDED_PARAMETERS = frozenset({"response_format", STRUCTURED})
# 0023's CHECK on model_catalog.efforts.
EFFORT = re.compile(r"[a-z]{1,16}")
# The level every chat_json call may think at (openrouter.CHAT_THINKING_LEVEL): an answer
# model must take it (features 25-27, C11).
CHAT_EFFORT = "low"


class CatalogUnavailable(Exception):
    """A list could not be read (``code``: the status or the failure); nothing was changed."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


class Prober(Protocol):
    """``OpenRouterClient.probe``: one request, its outcome."""

    def probe(self, path: str, body: Mapping[str, Any] | None, *, timeout: float) -> Probe: ...


@dataclass(frozen=True, slots=True)
class CatalogModel:
    """One model as saved: its refresh columns."""

    id: str
    name: str
    price_in_usd_per_m: float
    price_out_usd_per_m: float
    providers: tuple[str, ...]  # its ZDR base slugs with structured output, in OpenRouter's order
    efforts: tuple[str, ...]
    expires_on: date | None
    eligible: bool
    chat_eligible: bool = False  # features 25-27, C11: may draft answers
    # #488 review (R3): pricing.request and pricing.image, dollars a call; None: not known.
    price_request_usd: float | None = None
    price_image_usd: float | None = None


@dataclass(frozen=True, slots=True)
class Fetched:
    """What ``fetch`` found: the candidates read, and the ids of those skipped because their
    endpoint list could not be read (their rows are left as they are)."""

    models: list[CatalogModel]
    skipped: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class Refreshed:
    saved: int  # candidates saved
    eligible: int
    skipped: int = 0  # candidates whose endpoints could not be read, left as they were


def _strings(value: object) -> list[str] | None:
    """A list of strings, or None when it is not one."""
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        return None
    return value


def _price_per_m(value: object) -> float | None:
    """OpenRouter's USD per token (a string or a number) as USD per million tokens, or None
    when it is not a finite number at least 0 that fits numeric(12,6)."""
    if isinstance(value, bool) or not isinstance(value, str | int | float):
        return None
    try:
        per_token = float(value)
    except ValueError:
        return None
    per_m = round(per_token * TOKENS_PER_M, 6)
    return per_m if math.isfinite(per_m) and 0 <= per_m <= MAX_PRICE_PER_M else None


def _price_per_call(value: object) -> float | None:
    """OpenRouter's USD a request or an image (a string or a number), or None when it is not a
    finite number at least 0: not known. The caller passes 0 for one left out of a model's
    pricing: OpenRouter leaves out a charge it doesn't make (#488, 4 Nov 2026: every Gemini on
    the test server had no pricing.request, so none could read with Ultra low cost)."""
    if isinstance(value, bool) or not isinstance(value, str | int | float):
        return None
    try:
        usd = float(value)
    except ValueError:
        return None
    return usd if math.isfinite(usd) and usd >= 0 else None


def _name(value: object, model_id: str) -> str:
    """The display name without control or format characters, clamped; the id when none is left."""
    shown = "".join(char for char in value if char.isprintable()).strip() if isinstance(value, str) else ""
    return shown[:MAX_NAME].strip() or model_id


def _valid_list(values: Iterable[str], shape: Callable[[str], bool]) -> tuple[str, ...] | None:
    """The values once each, in order; None when one has the wrong shape or there are too many."""
    unique = tuple(dict.fromkeys(values))
    return unique if len(unique) <= MAX_LIST and all(map(shape, unique)) else None


def _expires(value: object, today: date) -> tuple[bool, date | None]:
    """(passes rule 4, the date): no date, or one more than EXPIRY_MARGIN away."""
    if value is None:
        return True, None
    if not isinstance(value, str):
        return False, None
    try:
        when = date.fromisoformat(value[:10])
    except ValueError:
        return False, None
    return when > today + EXPIRY_MARGIN, when


def candidate(item: object, *, efforts: frozenset[str], today: date) -> CatalogModel | None:
    """One ``/models`` item as a model with no providers yet, when every value has its
    shape and it passes rules 1, 3 and 4 and either the scan rules 2 and 6 (``eligible``
    True here: a scan candidate) or the answer rules (``chat_eligible`` True here: text in
    and out only, CHAT_EFFORT among its efforts); else None. ``fetch`` then decides both
    flags from its endpoints and the vendor."""
    if not isinstance(item, dict):
        return None
    model_id = item.get("id")
    if not isinstance(model_id, str) or MODEL_ID.fullmatch(model_id) is None:
        return None  # rule 1
    architecture = item.get("architecture")
    architecture = architecture if isinstance(architecture, dict) else {}
    inputs, outputs = (
        _strings(architecture.get("input_modalities")),
        _strings(architecture.get("output_modalities")),
    )
    if inputs is None or outputs is None or set(outputs) != {"text"}:
        return None  # rule 2: text out only
    parameters = _strings(item.get("supported_parameters"))
    if parameters is None or not set(parameters) >= NEEDED_PARAMETERS:
        return None  # rule 3
    fresh, expires_on = _expires(item.get("expiration_date"), today)
    if not fresh:
        return None  # rule 4
    shown = offered_efforts(item)
    if shown is None:
        return None
    # Rule 6 (Tushar, 18 Oct 2026): a model with no thinking levels is called with none.
    scan = "image" in inputs and (not shown or efforts <= set(shown))  # rules 2 and 6
    chat = "text" in inputs and (not shown or CHAT_EFFORT in shown)  # features 25-27, C11
    if not (scan or chat):
        return None
    pricing = item.get("pricing")
    pricing = pricing if isinstance(pricing, dict) else {}
    price_in, price_out = _price_per_m(pricing.get("prompt")), _price_per_m(pricing.get("completion"))
    if price_in is None or price_out is None:
        return None
    return CatalogModel(
        id=model_id,
        name=_name(item.get("name"), model_id),
        price_in_usd_per_m=price_in,
        price_out_usd_per_m=price_out,
        providers=(),
        efforts=shown,
        expires_on=expires_on,
        eligible=scan,
        chat_eligible=chat,
        price_request_usd=_price_per_call(pricing.get("request", 0)),  # left out: not charged
        price_image_usd=_price_per_call(pricing.get("image", 0)),
    )


def offered_efforts(item: Mapping[str, Any]) -> tuple[str, ...] | None:
    """A ``/models`` item's ``reasoning.supported_efforts``, once each: () when it lists none
    (no ``reasoning``, or no efforts in it: no thinking levels, called with no reasoning
    parameter); None when they have the wrong shape (the model is left out)."""
    reasoning = item.get("reasoning")
    if reasoning is None:
        return ()
    if not isinstance(reasoning, dict):
        return None
    listed = reasoning.get("supported_efforts")
    if listed is None:
        return ()
    offered = _strings(listed)
    return None if offered is None else _valid_list(offered, _effort)


def _effort(value: str) -> bool:
    return EFFORT.fullmatch(value) is not None


def _slug(value: str) -> bool:
    return PROVIDER_SLUG.fullmatch(value) is not None


def zdr_endpoints(reply: object) -> frozenset[tuple[str, str]]:
    """``GET /endpoints/zdr`` as {(model id, endpoint tag)}; malformed entries are left out."""
    data = reply.get("data") if isinstance(reply, dict) else None
    if not isinstance(data, list):
        raise CatalogUnavailable("zdr_list")
    return frozenset(
        (entry["model_id"], entry["tag"])
        for entry in data
        if isinstance(entry, dict)
        and isinstance(entry.get("model_id"), str)
        and isinstance(entry.get("tag"), str)
    )


def zdr_slugs(
    model_id: str, reply: object, zdr: frozenset[tuple[str, str]]
) -> list[tuple[str, frozenset[str]]]:
    """(base slug, its supported parameters) of each endpoint of ``model_id`` in
    ``GET /models/{id}/endpoints`` that is in ``zdr`` with ``status`` 0 and a well-formed
    slug, in OpenRouter's order. Rule 5 without its parameter and ceiling checks, which
    the catalogue and verify-models each add."""
    data = reply.get("data") if isinstance(reply, dict) else None
    endpoints = data.get("endpoints") if isinstance(data, dict) else None
    if not isinstance(endpoints, list):
        raise CatalogUnavailable("endpoints_list")
    found = []
    for endpoint in endpoints:
        if not isinstance(endpoint, dict):
            continue
        tag, status = endpoint.get("tag"), endpoint.get("status")
        parameters = _strings(endpoint.get("supported_parameters"))
        if not isinstance(tag, str) or (model_id, tag) not in zdr or parameters is None:
            continue
        if isinstance(status, bool) or status != 0:
            continue
        slug = tag.partition("/")[0]
        if _slug(slug):
            found.append((slug, frozenset(parameters)))
    return found


def _get(client: Prober, path: str) -> dict[str, Any]:
    probe = client.probe(path, None, timeout=GET_TIMEOUT_SECONDS)
    if probe.status != 200 or probe.reply is None:
        raise CatalogUnavailable(f"status_{probe.status}")
    return probe.reply


def fetch(
    client: Prober, settings: Settings, *, today: date, pause: Callable[[float], None] = time.sleep
) -> Fetched:
    """The candidates (rules 1 to 4 and 6, the first MAX_CANDIDATES) with their providers
    and ``eligible`` (rule 5); one whose endpoint list can't be read is skipped.
    ``CatalogUnavailable`` when ``/models`` or ``/endpoints/zdr`` can't be read. ``pause``
    waits ENDPOINTS_PAUSE_SECONDS between two endpoint requests."""
    listing = _get(client, MODELS_PATH).get("data")
    if not isinstance(listing, list):
        raise CatalogUnavailable("models_list")
    efforts = frozenset({settings.ocr_thinking_level, settings.ocr_retry_thinking_level})
    found: dict[str, CatalogModel] = {}
    for item in listing:
        model = candidate(item, efforts=efforts, today=today)
        if model is not None:
            found.setdefault(model.id, model)  # the first of a repeated id
            if len(found) == MAX_CANDIDATES:
                break
    zdr = zdr_endpoints(_get(client, ZDR_PATH))
    models, skipped = [], []
    for index, model in enumerate(found.values()):
        if index:
            pause(ENDPOINTS_PAUSE_SECONDS)
        try:
            endpoints = zdr_slugs(model.id, _get(client, f"/models/{model.id}/endpoints"), zdr)
        except CatalogUnavailable as error:
            logger.warning("model endpoints not read, model left as it was", extra={"code": error.code})
            skipped.append(model.id)
            continue
        providers = _valid_list((slug for slug, parameters in endpoints if STRUCTURED in parameters), _slug)
        providers = providers or ()  # more than MAX_LIST: none counts
        # Rule 5, and S1: a vendor outside OPENROUTER_MODEL_VENDORS is never eligible.
        allowed = any(slug in settings.openrouter_providers for slug in providers) and (
            model.id.split("/", 1)[0] in settings.openrouter_model_vendors
        )
        models.append(
            CatalogModel(
                model.id,
                model.name,
                model.price_in_usd_per_m,
                model.price_out_usd_per_m,
                providers,
                model.efforts,
                model.expires_on,
                model.eligible and allowed,
                model.chat_eligible and allowed,
                model.price_request_usd,
                model.price_image_usd,
            )
        )
    return Fetched(models, tuple(skipped))


_UPSERT = text(
    "INSERT INTO model_catalog (id, name, price_in_usd_per_m, price_out_usd_per_m, providers, efforts,"
    " expires_on, eligible, chat_eligible, price_request_usd, price_image_usd, seen_at)"
    " VALUES (:id, :name, :price_in, :price_out, CAST(:providers AS text[]), CAST(:efforts AS text[]),"
    " :expires_on, :eligible, :chat_eligible, :price_request, :price_image, now())"
    " ON CONFLICT (id) DO UPDATE SET name = EXCLUDED.name, price_in_usd_per_m = EXCLUDED.price_in_usd_per_m,"
    " price_out_usd_per_m = EXCLUDED.price_out_usd_per_m, price_request_usd = EXCLUDED.price_request_usd,"
    " price_image_usd = EXCLUDED.price_image_usd, providers = EXCLUDED.providers,"
    " efforts = EXCLUDED.efforts, expires_on = EXCLUDED.expires_on, eligible = EXCLUDED.eligible,"
    " chat_eligible = EXCLUDED.chat_eligible, seen_at = EXCLUDED.seen_at"
)
_NOT_LISTED = text(
    "UPDATE model_catalog SET eligible = false, chat_eligible = false"
    " WHERE (eligible OR chat_eligible) AND id <> ALL(CAST(:ids AS text[]))"
)


def save(db: Database, models: Sequence[CatalogModel], *, untouched: Sequence[str] = ()) -> None:
    """Upsert ``models`` (the refresh columns only) and set every other row ineligible but
    the ``untouched`` ones (skipped candidates, left as they are), in one transaction. A
    database that does not answer is BackendUnavailable("database"). Each model is checked
    against 0023's CHECKs (``valid``) before the transaction, and one that fails is left
    out: one bad entry never aborts the whole save."""
    models = [model for model in models if valid(model)]
    rows = [
        {
            "id": model.id,
            "name": model.name,
            "price_in": model.price_in_usd_per_m,
            "price_out": model.price_out_usd_per_m,
            "providers": list(model.providers),
            "efforts": list(model.efforts),
            "expires_on": model.expires_on,
            "eligible": model.eligible,
            "chat_eligible": model.chat_eligible,
            "price_request": model.price_request_usd,
            "price_image": model.price_image_usd,
        }
        for model in models
    ]
    with unavailable(), db.system() as conn:
        if rows:
            conn.execute(_UPSERT, rows)
        conn.execute(_NOT_LISTED, {"ids": [model.id for model in models] + list(untouched)})


def valid(model: CatalogModel) -> bool:
    """``model`` fits 0023's model_catalog CHECKs: its id, name, prices, providers and efforts
    (0070's prices a call: not known, or at least 0, as ``_price_per_call`` makes them)."""
    return (
        valid_model_id(model.id)
        and 1 <= len(model.name) <= MAX_NAME
        and all(
            0 <= price <= MAX_PRICE_PER_M for price in (model.price_in_usd_per_m, model.price_out_usd_per_m)
        )
        and _valid_list(model.providers, _slug) == model.providers
        and _valid_list(model.efforts, _effort) == model.efforts
        and all(
            price is None or (math.isfinite(price) and price >= 0)
            for price in (model.price_request_usd, model.price_image_usd)
        )
    )


def refresh(
    db: Database,
    client: Prober,
    settings: Settings,
    *,
    today: date,
    pause: Callable[[float], None] = time.sleep,
) -> Refreshed:
    """Fetch and save the catalogue (see the module notes)."""
    fetched = fetch(client, settings, today=today, pause=pause)
    save(db, fetched.models, untouched=fetched.skipped)
    check_env_models(db, settings)
    return Refreshed(
        len(fetched.models), sum(model.eligible for model in fetched.models), len(fetched.skipped)
    )


_CATALOGUE_HELD = text("SELECT EXISTS (SELECT 1 FROM model_catalog)")
_ENV_MODELS = text(
    "SELECT id, eligible, chat_eligible, price_in_usd_per_m, price_out_usd_per_m, price_request_usd,"
    " price_image_usd, efforts FROM model_catalog WHERE id = ANY(:ids)"
)


def check_env_models(db: Database, settings: Settings) -> list[tuple[str, str, str]]:
    """Production QA L2: (setting, model, ``missing`` or ``not_eligible``) for each model .env
    names that the catalogue does not hold, or holds as not eligible for its use: OCR_MODEL to
    read scans, and MODEL_CHAT to answer while MODEL_ENABLED. Each is logged at error with its
    model id, so a withdrawn model is named once a refresh sees it, not found 404 by 404.
    After each refresh and at worker start; a database error is logged by its type and checks
    nothing (never a failed refresh or a worker that won't start). Review of #564: before the
    first refresh (no catalogue row at all) nothing is checked, logged once at info, never an
    ERROR from every replica at start; while OCR_ULTRA_LOW_COST is on, OCR_MODEL (its lead unless a
    priced Gemini default is chosen) that can't lead it (``models.ulc_why``) is ``ulc_not_ready``,
    logged the same way (start-up goes on). Two statements."""
    wanted = [("OCR_MODEL", settings.ocr_model, "eligible")]
    if settings.model_enabled:
        wanted.append(("MODEL_CHAT", settings.model_chat, "chat_eligible"))
    try:
        with unavailable(), db.system() as conn:
            if not conn.execute(_CATALOGUE_HELD).scalar_one():
                logger.info("env models not checked", extra={"code": "catalogue_empty"})
                return []
            ids = [model for _, model, _ in wanted]
            rows = {row["id"]: row for row in conn.execute(_ENV_MODELS, {"ids": ids}).mappings()}
    except (SQLAlchemyError, BackendUnavailable) as error:
        logger.warning("env models not checked", extra={"code": type(error).__name__})
        return []
    problems = []
    for setting, model, column in wanted:
        row = rows.get(model)
        code = "missing" if row is None else None if row[column] else "not_eligible"
        if code is not None:
            problems.append((setting, model, code))
    ocr = rows.get(settings.ocr_model)
    if settings.ocr_ultra_low_cost and ocr is not None and models.ulc_why(ocr) is not None:
        problems.append(("OCR_ULTRA_LOW_COST", settings.ocr_model, "ulc_not_ready"))
    for setting, model, code in problems:
        logger.error("env model not usable", extra={"code": code, "model": model, "where": setting})
    return problems


def last_refreshed(db: Database) -> datetime | None:
    """When a refresh last saved the catalogue (its newest ``seen_at``); None when empty."""
    with unavailable(), db.system() as conn:
        latest: datetime | None = conn.execute(text("SELECT max(seen_at) FROM model_catalog")).scalar_one()
    return latest


class RefreshCatalog:
    """Wraps the worker: before a turn, runs ``refresh`` when ``REFRESH_EVERY`` has passed
    on ``clock`` since the last pass, or RETRY_AFTER_FAILURE since a pass that failed. The
    first turn runs it, unless ``last_refreshed`` (the catalogue's newest ``seen_at``) is
    less than REFRESH_EVERY ago: then it waits until that is a day old, so restarts never
    refresh again and again. ``refresh`` None (OCR_ENGINE is not gemini: keyless mode reads
    no scan with a model) does nothing. A database error or a list that can't be read is
    logged by its type or code, and the jobs go on. The pass runs under
    ``lock("refresh_catalog")``: a copy that cannot get it skips it, and waits a day. O(1)
    per turn, plus one refresh a day."""

    def __init__(
        self,
        worker: RunsJobs,
        refresh: Callable[[], Refreshed] | None,
        clock: Callable[[], datetime],
        *,
        lock: SweepLock = no_lock,
        last_refreshed: Callable[[], datetime | None] | None = None,
    ) -> None:
        self._worker = worker
        self._refresh = refresh
        self._clock = clock
        self._lock = lock
        self._last_refreshed = last_refreshed
        self._next: datetime | None = None

    def run_once(self) -> bool:
        now = self._clock()
        if self._refresh is not None:
            if self._next is None:
                self._next = self._first_due(now)
            if now >= self._next:
                self._next = now + RETRY_AFTER_FAILURE  # noted first: a pass that fails retries in an hour
                if self._pass(self._refresh):
                    self._next = now + REFRESH_EVERY
        return self._worker.run_once()

    def _first_due(self, now: datetime) -> datetime:
        """Now, unless the catalogue was refreshed less than REFRESH_EVERY ago."""
        if self._last_refreshed is None:
            return now
        try:
            latest = self._last_refreshed()
        except (SQLAlchemyError, BackendUnavailable) as error:
            logger.warning("model catalogue age not read", extra={"code": type(error).__name__})
            return now
        if latest is None or latest + REFRESH_EVERY <= now:
            return now
        return latest + REFRESH_EVERY

    def _pass(self, refresh: Callable[[], Refreshed]) -> bool:
        """True when the pass is done (or another copy holds the lock); False when it failed."""
        try:
            with self._lock("refresh_catalog") as mine:
                done = refresh() if mine else None
        except (SQLAlchemyError, BackendUnavailable) as error:
            logger.error("model catalogue not refreshed", extra={"code": type(error).__name__})
            return False
        except CatalogUnavailable as error:
            logger.warning("model catalogue not refreshed", extra={"code": error.code})
            return False
        if done is not None:
            logger.info(
                "model catalogue: %d models, %d eligible, %d skipped", done.saved, done.eligible, done.skipped
            )
        return True
