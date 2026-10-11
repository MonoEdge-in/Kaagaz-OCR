"""The model catalogue (model choice, council 11 Oct 2026, MC1, MC3): which OpenRouter
models a scan may be read with, refreshed from OpenRouter's live lists, never a list of
names in code.

``fetch`` reads ``GET /models``, then for each candidate ``GET /models/{id}/endpoints``,
and ``GET /endpoints/zdr`` once, through the client's ``probe`` (no proxy, no redirect, the
key in its header only). A model is **eligible** when it passes every rule (facts file,
"Catalogue filter rules"):

1. its id is an OpenRouter model ID with no ``:variant`` (``MODEL_ID``);
2. it takes image input and returns text only;
3. it lists ``response_format`` and ``structured_outputs``;
4. it has no expiration date, or one more than ``EXPIRY_MARGIN`` (30 days) away;
5. one of its endpoints is a zero-data-retention one (in ``/endpoints/zdr``) with
   ``status`` 0 that lists ``structured_outputs``, whose base slug (the tag before any
   ``/``) is in OPENROUTER_PROVIDERS, the processors Vrushit approved;
6. its ``reasoning.supported_efforts`` include OCR_THINKING_LEVEL and
   OCR_RETRY_THINKING_LEVEL.

Rule 7, a ``verify-models --record`` pass, is kept apart (``verified_*``, written by the
owner): the operator can enable only an eligible, verified model (0023).

**Everything OpenRouter sends is untrusted.** An id must match ``MODEL_ID`` before it is
put in a path; a name loses its control and format characters and is clamped to
``MAX_NAME``; a price must be a finite number at least 0 that fits ``numeric(12,6)``; a
provider slug must match ``PROVIDER_SLUG`` and an effort ``EFFORT``, at most ``MAX_LIST``
of each. A model breaking any of these is left out, as if not listed.

``save`` upserts the models that pass rules 1 to 4 and 6 (their ``eligible`` from rule 5)
into ``model_catalog``, writing the refresh columns only, and sets ``eligible`` false on
every other row: rows are never deleted. A refresh that cannot read every list changes
nothing (``CatalogUnavailable``), so an outage never makes the catalogue empty.

``RefreshCatalog`` runs it between jobs once every ``REFRESH_EVERY`` (a day), under the
sweep lock (PRD 5 Q3); ``python -m kaagaz.cli refresh-models`` runs it now. Logs hold
codes and counts only.

Cost per refresh: 2 + C requests for the C candidates, and one statement per candidate.
"""

import logging
import math
import re
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any, Protocol

from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from kaagaz.ai.openrouter import Probe
from kaagaz.db.engine import Database, unavailable
from kaagaz.ingestion.errors import BackendUnavailable
from kaagaz.jobs.fair_share import SweepLock, no_lock
from kaagaz.jobs.worker import RunsJobs
from kaagaz.settings import MODEL_ID, PROVIDER_SLUG, Settings

logger = logging.getLogger("kaagaz.worker")

MODELS_PATH = "/models"
ZDR_PATH = "/endpoints/zdr"
REFRESH_EVERY = timedelta(days=1)
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


@dataclass(frozen=True, slots=True)
class Refreshed:
    saved: int  # candidates saved
    eligible: int


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
    """One ``/models`` item as a model with no providers yet, when it passes rules 1 to 4
    and 6 and every value has its shape; else None."""
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
    if inputs is None or "image" not in inputs or outputs is None or set(outputs) != {"text"}:
        return None  # rule 2
    parameters = _strings(item.get("supported_parameters"))
    if parameters is None or not set(parameters) >= NEEDED_PARAMETERS:
        return None  # rule 3
    fresh, expires_on = _expires(item.get("expiration_date"), today)
    if not fresh:
        return None  # rule 4
    reasoning = item.get("reasoning")
    offered = _strings(reasoning.get("supported_efforts")) if isinstance(reasoning, dict) else None
    shown = None if offered is None else _valid_list(offered, _effort)
    if shown is None or not efforts <= set(shown):
        return None  # rule 6
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
        eligible=False,
    )


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


def _get(client: Prober, path: str, timeout: float) -> dict[str, Any]:
    probe = client.probe(path, None, timeout=timeout)
    if probe.status != 200 or probe.reply is None:
        raise CatalogUnavailable(f"status_{probe.status}")
    return probe.reply


def fetch(client: Prober, settings: Settings, *, today: date) -> list[CatalogModel]:
    """The candidates (rules 1 to 4 and 6) with their providers and ``eligible`` (rule 5).
    ``CatalogUnavailable`` when any list can't be read."""
    timeout = settings.model_timeout_seconds
    listing = _get(client, MODELS_PATH, timeout).get("data")
    if not isinstance(listing, list):
        raise CatalogUnavailable("models_list")
    efforts = frozenset({settings.ocr_thinking_level, settings.ocr_retry_thinking_level})
    found: dict[str, CatalogModel] = {}
    for item in listing:
        model = candidate(item, efforts=efforts, today=today)
        if model is not None:
            found.setdefault(model.id, model)  # the first of a repeated id
    zdr = zdr_endpoints(_get(client, ZDR_PATH, timeout))
    models = []
    for model in found.values():
        endpoints = zdr_slugs(model.id, _get(client, f"/models/{model.id}/endpoints", timeout), zdr)
        providers = _valid_list((slug for slug, parameters in endpoints if STRUCTURED in parameters), _slug)
        providers = providers or ()  # more than MAX_LIST: none counts
        eligible = any(slug in settings.openrouter_providers for slug in providers)
        models.append(
            CatalogModel(
                model.id,
                model.name,
                model.price_in_usd_per_m,
                model.price_out_usd_per_m,
                providers,
                model.efforts,
                model.expires_on,
                eligible,
            )
        )
    return models


_UPSERT = text(
    "INSERT INTO model_catalog (id, name, price_in_usd_per_m, price_out_usd_per_m, providers, efforts,"
    " expires_on, eligible, seen_at)"
    " VALUES (:id, :name, :price_in, :price_out, CAST(:providers AS text[]), CAST(:efforts AS text[]),"
    " :expires_on, :eligible, now())"
    " ON CONFLICT (id) DO UPDATE SET name = EXCLUDED.name, price_in_usd_per_m = EXCLUDED.price_in_usd_per_m,"
    " price_out_usd_per_m = EXCLUDED.price_out_usd_per_m, providers = EXCLUDED.providers,"
    " efforts = EXCLUDED.efforts, expires_on = EXCLUDED.expires_on, eligible = EXCLUDED.eligible,"
    " seen_at = EXCLUDED.seen_at"
)
_NOT_LISTED = text(
    "UPDATE model_catalog SET eligible = false WHERE eligible AND id <> ALL(CAST(:ids AS text[]))"
)


def save(db: Database, models: Sequence[CatalogModel]) -> None:
    """Upsert ``models`` (the refresh columns only) and set every other row ineligible, in
    one transaction. A database that does not answer is BackendUnavailable("database")."""
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
        }
        for model in models
    ]
    with unavailable(), db.system() as conn:
        if rows:
            conn.execute(_UPSERT, rows)
        conn.execute(_NOT_LISTED, {"ids": [model.id for model in models]})


def refresh(db: Database, client: Prober, settings: Settings, *, today: date) -> Refreshed:
    """Fetch and save the catalogue (see the module notes)."""
    models = fetch(client, settings, today=today)
    save(db, models)
    return Refreshed(len(models), sum(model.eligible for model in models))


class RefreshCatalog:
    """Wraps the worker: before a turn, runs ``refresh`` when ``REFRESH_EVERY`` has passed
    on ``clock`` since the last pass; the first turn runs it. ``refresh`` None (OCR_ENGINE
    is not gemini: keyless mode reads no scan with a model) does nothing. The time is noted
    first, so a pass that fails waits a day too (ponytail: no sooner retry; the catalogue
    is a day staler at worst). A database error or a list that can't be read is logged by
    its type or code, and the jobs go on. The pass runs under ``lock("refresh_catalog")``:
    a copy that cannot get it skips it. O(1) per turn, plus one refresh a day."""

    def __init__(
        self,
        worker: RunsJobs,
        refresh: Callable[[], Refreshed] | None,
        clock: Callable[[], datetime],
        *,
        lock: SweepLock = no_lock,
    ) -> None:
        self._worker = worker
        self._refresh = refresh
        self._clock = clock
        self._lock = lock
        self._next: datetime | None = None

    def run_once(self) -> bool:
        now = self._clock()
        if self._refresh is not None and (self._next is None or now >= self._next):
            self._next = now + REFRESH_EVERY
            self._pass(self._refresh)
        return self._worker.run_once()

    def _pass(self, refresh: Callable[[], Refreshed]) -> None:
        try:
            with self._lock("refresh_catalog") as mine:
                done = refresh() if mine else None
        except (SQLAlchemyError, BackendUnavailable) as error:
            logger.error("model catalogue not refreshed", extra={"code": type(error).__name__})
        except CatalogUnavailable as error:
            logger.warning("model catalogue not refreshed", extra={"code": error.code})
        else:
            if done is not None:
                logger.info("model catalogue: %d models, %d eligible", done.saved, done.eligible)
