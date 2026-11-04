"""The model catalogue refresh (model choice, council 11 Oct 2026, MC1) against the fake
OpenRouter API, from a small slice of OpenRouter's real lists (``fixtures/openrouter_models
.json``): each filter rule drops a model, hostile JSON is refused, a list that can't be
read changes nothing, and the daily sweep. The SQL itself: ``test_model_catalog_db.py``."""

import ast
import copy
import importlib.util
import io
import json
import logging
import re
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any, cast

import pytest
from sqlalchemy.exc import OperationalError

from kaagaz import cli
from kaagaz.ai.openrouter import OpenRouterClient, Probe
from kaagaz.db.engine import Database
from kaagaz.jobs import catalog
from kaagaz.jobs.catalog import CatalogModel, CatalogUnavailable, RefreshCatalog, Refreshed, candidate, fetch
from kaagaz.settings import DEFAULT_MODEL_VENDORS, MODEL_ID
from kaagaz.worker_main import catalog_refresh
from tests.openrouter_fake import API_PATH, KEY, OpenRouterFake, Recorded, Reply, error_reply, running_fake
from tests.settings_factory import make_settings

FIXTURE = json.loads((Path(__file__).parent / "fixtures" / "openrouter_models.json").read_text("utf-8"))
TODAY = date(2026, 10, 11)
NOW = datetime(2026, 10, 11, 9, 0, tzinfo=UTC)
EFFORTS = frozenset({"low", "high"})
PRO, SONNET, LUNA = "google/gemini-3.1-pro-preview", "anthropic/claude-sonnet-5.5", "openai/gpt-6-luna"
FLASH = "google/gemini-3.8-flash"  # features 25-27 (F2): made up, the real id is not known yet
GOOD: dict[str, Any] = FIXTURE["models"]["data"][0]


def listed(request: Recorded) -> Reply:
    """OpenRouter's three lists from the fixture."""
    if request.action == "models":
        return Reply(200, FIXTURE["models"])
    if request.action == "zdr":
        return Reply(200, FIXTURE["zdr"])
    if request.action == "endpoints":
        model = request.path.removeprefix(API_PATH + "/models/").removesuffix("/endpoints")
        return Reply(200, {"data": {"id": model, "endpoints": FIXTURE["endpoints"].get(model, [])}})
    return error_reply(404)


@pytest.fixture
def fake(monkeypatch: pytest.MonkeyPatch) -> Iterator[OpenRouterFake]:
    monkeypatch.setattr(catalog, "ENDPOINTS_PAUSE_SECONDS", 0.0)  # the pause itself: its own test
    with running_fake() as server:
        server.responder = listed
        yield server


def _client(fake: OpenRouterFake, **overrides: Any) -> tuple[OpenRouterClient, Any]:
    settings = make_settings(openrouter_api_key=KEY, openrouter_base_url=fake.url, **overrides)
    return OpenRouterClient(settings), settings


def test_each_filter_rule_drops_a_model_and_only_an_approved_zdr_provider_counts(
    fake: OpenRouterFake,
) -> None:
    client, settings = _client(fake)
    models = {model.id: model for model in fetch(client, settings, today=TODAY).models}
    # Rules 1-4 and 6 (and a router with no fixed price) leave a model out altogether.
    # Changed on purpose (features 25-27, 15 Oct 2026): a model that may only answer (text in
    # and out, low thinking) is a candidate too, and the fixture has Gemini 3.8 Flash (F2).
    scan_only = {PRO, SONNET, LUNA, FLASH, "example/not-zdr-1", "example/endpoint-down-1"}
    assert set(models) == scan_only | {"example/text-only-1", "example/low-effort-only-1"}
    eligible = {model_id for model_id, model in models.items() if model.eligible}
    assert eligible == {PRO, SONNET, FLASH}  # rule 5: a ZDR endpoint of google-vertex, status 0
    chat = {model_id for model_id, model in models.items() if model.chat_eligible}
    assert chat == {PRO, SONNET, FLASH}  # the same endpoint rule; text-only-1 has no ZDR endpoint
    assert models[PRO].providers == ("google-vertex",)  # the base slug of google-vertex/global
    assert models[SONNET].providers == ("google-vertex",)  # bedrock lacks structured_outputs
    assert models[LUNA].providers == ("azure",)  # listed, but azure is not approved yet
    assert models["example/not-zdr-1"].providers == ()
    assert models["example/endpoint-down-1"].providers == ()
    assert (models[PRO].price_in_usd_per_m, models[PRO].price_out_usd_per_m) == (2.0, 12.0)
    assert models[LUNA].price_in_usd_per_m == 0.1
    assert models[SONNET].expires_on == date(2027, 6, 30)
    assert models[SONNET].efforts == ("low", "medium", "high", "max")
    assert models[PRO].name == "Google: Gemini 3.1 Pro Preview"
    # Only GETs, the key in its header only, and only the candidates' endpoint listings.
    assert {request.method for request in fake.requests} == {"GET"}
    fake.assert_key_in_header_only(KEY)
    asked = sorted(r.path for r in fake.requests if r.action == "endpoints")
    assert asked == sorted(f"{API_PATH}/models/{model}/endpoints" for model in models)


def test_azure_counts_once_it_is_in_openrouter_providers(fake: OpenRouterFake) -> None:
    # Changed on purpose (features 25-27, 15 Oct 2026): openai must be an allowed vendor too (S1).
    providers, vendors = frozenset({"google-vertex", "azure"}), frozenset({"google", "anthropic", "openai"})
    client, settings = _client(fake, openrouter_providers=providers, openrouter_model_vendors=vendors)
    eligible = {model.id for model in fetch(client, settings, today=TODAY).models if model.eligible}
    assert eligible == {PRO, SONNET, LUNA, FLASH}


def test_a_vendor_outside_openrouter_model_vendors_is_never_eligible(fake: OpenRouterFake) -> None:
    """Features 25-27, S1: an approved provider does not approve every vendor's model."""
    client, settings = _client(fake, openrouter_model_vendors=frozenset({"google"}))
    models = fetch(client, settings, today=TODAY).models
    assert {model.id for model in models if model.eligible or model.chat_eligible} == {PRO, FLASH}


def test_the_configured_efforts_decide_rule_6(fake: OpenRouterFake) -> None:
    client, settings = _client(fake, ocr_thinking_level="minimal", ocr_retry_thinking_level="low")
    # Changed on purpose (features 25-27, 15 Oct 2026): every model that may answer is saved
    # too, so rule 6 is read from the scan flag.
    scans = {model.id for model in fetch(client, settings, today=TODAY).models if model.eligible}
    assert scans == {FLASH}  # low-effort-only-1 passes rule 6 now, but has no ZDR endpoint
    by_id = {item["id"]: item for item in FIXTURE["models"]["data"]}
    levels = frozenset({"minimal", "low"})
    low_only = candidate(by_id["example/low-effort-only-1"], efforts=levels, today=TODAY)
    pro = candidate(by_id[PRO], efforts=levels, today=TODAY)
    assert low_only is not None and low_only.eligible
    assert pro is not None and (pro.eligible, pro.chat_eligible) == (False, True)  # no minimal: answers only


def _with(**changes: Any) -> dict[str, Any]:
    item = copy.deepcopy(GOOD)
    item.update(changes)
    return item


@pytest.mark.parametrize(
    "item",
    [
        None,
        [GOOD],
        "google/gemini-3.1-pro-preview",
        _with(id=None),
        _with(id=42),
        _with(id="../../key"),
        _with(id="google/gemini 3; DROP TABLE model_catalog"),
        _with(id="google/gemini-3.1-pro-preview:floor"),
        _with(id="Google/Gemini"),
        _with(architecture="image"),
        _with(architecture={"input_modalities": "image", "output_modalities": ["text"]}),
        _with(architecture={"input_modalities": ["image", 7], "output_modalities": ["text"]}),
        _with(supported_parameters="response_format,structured_outputs"),
        _with(pricing={"prompt": "NaN", "completion": "0.000012"}),
        _with(pricing={"prompt": "Infinity", "completion": "0.000012"}),
        _with(pricing={"prompt": "1e300", "completion": "0.000012"}),
        _with(pricing={"prompt": "-0.000002", "completion": "0.000012"}),
        _with(pricing={"prompt": True, "completion": "0.000012"}),
        _with(pricing={"prompt": "two dollars", "completion": "0.000012"}),
        _with(pricing={"prompt": "0.000002"}),
        _with(pricing=None),
        _with(reasoning={"supported_efforts": ["low", "high", "HIGH"]}),
        _with(reasoning={"supported_efforts": ["low", "high", "x" * 17]}),
        _with(reasoning={"supported_efforts": ["low", "high", *(f"e{chr(97 + n)}" for n in range(15))]}),
        _with(reasoning={"supported_efforts": "low,high"}),
        # Changed on purpose (Tushar 18 Oct: models without thinking levels): reasoning=None
        # is now a model with no thinking levels (below); a reasoning that is not an object
        # is still malformed.
        _with(reasoning="high"),
        _with(expiration_date="soon"),
        _with(expiration_date=20271231),
    ],
)
def test_hostile_or_malformed_json_is_left_out(item: object) -> None:
    assert candidate(item, efforts=EFFORTS, today=TODAY) is None


TEXT_ONLY = {"input_modalities": ["text"], "output_modalities": ["text"]}


@pytest.mark.parametrize("reasoning", [None, {}, {"supported_efforts": None}, {"supported_efforts": []}])
def test_a_model_with_no_thinking_levels_may_scan_and_answer_with_empty_efforts(reasoning: object) -> None:
    """Tushar, 18 Oct 2026: rule 6 is optional; such a model is saved with no efforts."""
    scan = candidate(_with(reasoning=reasoning), efforts=EFFORTS, today=TODAY)
    assert scan is not None and scan.efforts == () and (scan.eligible, scan.chat_eligible) == (True, True)
    chat = candidate(_with(reasoning=reasoning, architecture=TEXT_ONLY), efforts=EFFORTS, today=TODAY)
    assert chat is not None and (chat.eligible, chat.chat_eligible) == (False, True)


def test_a_model_listing_efforts_must_still_list_the_ones_we_send() -> None:
    lacking = candidate(_with(reasoning={"supported_efforts": ["medium"]}), efforts=EFFORTS, today=TODAY)
    assert lacking is None  # neither low and high for scans, nor low for answers
    no_low = {"supported_efforts": ["medium", "high"]}
    assert candidate(_with(reasoning=no_low, architecture=TEXT_ONLY), efforts=EFFORTS, today=TODAY) is None


def test_qwen_and_meta_llama_pass_the_vendor_rule_by_default() -> None:
    """Tushar, 18 Oct 2026: OPENROUTER_MODEL_VENDORS unset allows these four vendors."""
    assert make_settings().openrouter_model_vendors == DEFAULT_MODEL_VENDORS
    assert {"google", "anthropic", "qwen", "meta-llama"} == DEFAULT_MODEL_VENDORS


def test_a_name_loses_control_and_format_characters_and_is_clamped() -> None:
    hostile = "Gemini" + chr(0x202E) + "\u0000\x1b[31m" + "x" * 500
    model = candidate(_with(name=hostile), efforts=EFFORTS, today=TODAY)
    assert model is not None
    assert model.name.startswith("Gemini[31m") and len(model.name) == catalog.MAX_NAME
    assert all(char.isprintable() for char in model.name)
    for empty in (None, "", chr(0x200B) + chr(0x202E), 7):
        named = candidate(_with(name=empty), efforts=EFFORTS, today=TODAY)
        assert named is not None and named.name == GOOD["id"]


def test_a_price_is_per_million_and_must_fit_the_column() -> None:
    model = candidate(_with(pricing={"prompt": 0.000002, "completion": "0"}), efforts=EFFORTS, today=TODAY)
    assert model is not None and (model.price_in_usd_per_m, model.price_out_usd_per_m) == (2.0, 0.0)
    too_much = _with(pricing={"prompt": "1", "completion": "0"})  # $1,000,000 a million: over numeric(12,6)
    assert candidate(too_much, efforts=EFFORTS, today=TODAY) is None


def test_a_price_a_request_and_an_image_are_dollars_and_unknown_when_left_out_or_bad() -> None:
    """#488 review (R3): pricing.request and pricing.image are dollars a call (a string, as
    OpenRouter sends them, or a number). The fixture's lists send neither, and nothing says one
    left out is free, so one left out or not a finite number at least 0 is None (not known); the
    model stays a candidate."""
    tokens = {"prompt": "0.0000003", "completion": "0.0000025"}
    model = candidate(
        _with(pricing={**tokens, "request": "0.0001", "image": 0.001238}), efforts=EFFORTS, today=TODAY
    )
    assert model is not None and (model.price_request_usd, model.price_image_usd) == (0.0001, 0.001238)
    free = candidate(_with(pricing={**tokens, "request": "0", "image": "0"}), efforts=EFFORTS, today=TODAY)
    assert free is not None and (free.price_request_usd, free.price_image_usd) == (0.0, 0.0)
    for bad in (None, "NaN", "Infinity", "-0.01", True, "a dollar", [1]):
        given = {} if bad is None else {"request": bad, "image": bad}
        model = candidate(_with(pricing={**tokens, **given}), efforts=EFFORTS, today=TODAY)
        assert model is not None and (model.price_request_usd, model.price_image_usd) == (None, None), bad
    assert GOOD["pricing"].keys() == {"prompt", "completion"}  # OpenRouter's lists as faked: neither


def test_a_model_expiring_within_30_days_is_left_out() -> None:
    edge = TODAY + catalog.EXPIRY_MARGIN
    assert candidate(_with(expiration_date=edge.isoformat()), efforts=EFFORTS, today=TODAY) is None
    later = (edge + timedelta(days=1)).isoformat()
    model = candidate(_with(expiration_date=later), efforts=EFFORTS, today=TODAY)
    assert model is not None and model.expires_on == edge + timedelta(days=1)


@pytest.mark.parametrize(
    ("endpoint", "zdr_tag"),
    [
        (
            {"tag": "Google_Vertex", "status": 0, "supported_parameters": ["structured_outputs"]},
            "Google_Vertex",
        ),
        ({"tag": "x" * 65, "status": 0, "supported_parameters": ["structured_outputs"]}, "x" * 65),
        (
            {"tag": "google-vertex", "status": True, "supported_parameters": ["structured_outputs"]},
            "google-vertex",
        ),
        (
            {"tag": "google-vertex", "status": "0", "supported_parameters": ["structured_outputs"]},
            "google-vertex",
        ),
        (
            {"tag": "google-vertex", "status": 0, "supported_parameters": "structured_outputs"},
            "google-vertex",
        ),
        ({"tag": 7, "status": 0, "supported_parameters": ["structured_outputs"]}, "google-vertex"),
        ("google-vertex", "google-vertex"),
    ],
)
def test_a_malformed_endpoint_or_slug_never_counts(endpoint: object, zdr_tag: str) -> None:
    reply = {"data": {"endpoints": [endpoint]}}
    assert catalog.zdr_slugs(PRO, reply, frozenset({(PRO, zdr_tag)})) == []


def test_more_than_16_providers_count_as_none(fake: OpenRouterFake) -> None:
    tags = [f"provider-{n}" for n in range(17)]

    def many(request: Recorded) -> Reply:
        if request.action == "zdr":
            return Reply(200, {"data": [{"model_id": PRO, "tag": tag} for tag in tags]})
        if request.action == "endpoints":
            endpoints = [
                {"tag": tag, "status": 0, "supported_parameters": ["structured_outputs"]} for tag in tags
            ]
            return Reply(200, {"data": {"endpoints": endpoints}})
        return listed(request)

    fake.responder = many
    client, settings = _client(fake, openrouter_providers=frozenset(tags))
    pro = next(model for model in fetch(client, settings, today=TODAY).models if model.id == PRO)
    assert (pro.providers, pro.eligible) == ((), False)


@pytest.mark.parametrize(
    ("broken", "code"),
    [
        ("models", "status_500"),
        ("zdr", "status_500"),
        ("models-shape", "models_list"),
        ("zdr-shape", "zdr_list"),
    ],
)
def test_a_list_that_cannot_be_read_changes_nothing(fake: OpenRouterFake, broken: str, code: str) -> None:
    def failing(request: Recorded) -> Reply:
        action = broken.removesuffix("-shape")
        if request.action == action:
            return Reply(200, {"data": "nope"}) if broken.endswith("-shape") else error_reply(500)
        return listed(request)

    fake.responder = failing
    client, settings = _client(fake)
    untouched = cast(Database, None)  # save is never reached: no database is needed
    with pytest.raises(CatalogUnavailable) as raised:
        catalog.refresh(untouched, client, settings, today=TODAY)
    assert raised.value.code == code


@pytest.mark.parametrize("broken", [Reply(500, {}), Reply(200, {"data": "nope"})], ids=["status", "shape"])
def test_one_models_endpoints_that_cannot_be_read_skip_that_model_only(
    fake: OpenRouterFake, broken: Reply
) -> None:
    """R7: the others are saved; the skipped one is named, so its row is left as it was."""

    def one_broken(request: Recorded) -> Reply:
        if request.action == "endpoints" and request.path.endswith(f"/{LUNA}/endpoints"):
            return broken
        return listed(request)

    fake.responder = one_broken
    client, settings = _client(fake)
    fetched = fetch(client, settings, today=TODAY)
    assert fetched.skipped == (LUNA,)
    # Changed on purpose (features 25-27, 15 Oct 2026): answer-only candidates and Flash too.
    assert {model.id for model in fetched.models} == {
        PRO,
        SONNET,
        FLASH,
        "example/not-zdr-1",
        "example/endpoint-down-1",
        "example/text-only-1",
        "example/low-effort-only-1",
    }


class _Lists:
    """A Prober with ``count`` candidates: records each GET's path and time limit."""

    def __init__(self, count: int) -> None:
        self.items = [{**GOOD, "id": f"example/model-{n}"} for n in range(count)]
        self.asked: list[tuple[str, float]] = []

    def probe(self, path: str, body: object, *, timeout: float) -> Probe:
        self.asked.append((path, timeout))
        reply: dict[str, Any] = {"data": self.items}
        if path == catalog.ZDR_PATH:
            reply = {"data": []}
        elif path != catalog.MODELS_PATH:
            reply = {"data": {"endpoints": []}}
        return Probe(200, None, None, {}, reply, 0.0)


def test_the_refresh_asks_at_most_200_candidates_each_get_short_and_paced() -> None:
    """R7: a long list never holds the worker: 200 candidates at most, each GET at most
    GET_TIMEOUT_SECONDS, and a pause between two endpoint requests (no 429 storm)."""
    lists, pauses = _Lists(205), list[float]()
    fetched = fetch(lists, make_settings(), today=TODAY, pause=pauses.append)
    endpoints = [path for path, _ in lists.asked if path.endswith("/endpoints")]
    assert (len(fetched.models), len(endpoints)) == (catalog.MAX_CANDIDATES, 200)
    assert {timeout for _, timeout in lists.asked} == {catalog.GET_TIMEOUT_SECONDS} == {15.0}
    assert pauses == [catalog.ENDPOINTS_PAUSE_SECONDS] * 199


def test_0023_checks_a_model_id_with_the_settings_own_pattern() -> None:
    """R8: the same pattern in Python and in the CHECKs, so the database never refuses an id
    the refresh let through."""
    path = Path(__file__).resolve().parents[1] / "migrations" / "versions" / "0023_model_choice.py"
    spec = importlib.util.spec_from_file_location("migration_0023", path)
    assert spec is not None and spec.loader is not None
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    assert f"^{MODEL_ID.pattern}$" == migration.MODEL_ID
    assert candidate(_with(id="google/.."), efforts=EFFORTS, today=TODAY) is None


class _Saved:
    """A database whose one transaction keeps the rows it was sent."""

    def __init__(self) -> None:
        self.sent: list[object] = []

    @contextmanager
    def system(self) -> Iterator["_Saved"]:
        yield self

    def execute(self, statement: object, params: object) -> None:
        self.sent.append(params)


def test_one_bad_entry_is_left_out_before_the_save_never_aborting_it() -> None:
    """R8: every model is checked against 0023's CHECKs before the transaction."""
    good = CatalogModel(PRO, "Gemini", 2.0, 12.0, ("google-vertex",), ("low",), None, True)
    bad = [
        CatalogModel("google/..", "Dots", 1.0, 1.0, (), (), None, False),
        CatalogModel("google/ok", "", 1.0, 1.0, (), (), None, False),
        CatalogModel("google/ok", "Dear", 1e7, 1.0, (), (), None, False),
        CatalogModel("google/ok", "Bad slug", 1.0, 1.0, ("Azure",), (), None, False),
    ]
    saved = _Saved()
    catalog.save(cast(Database, saved), [good, *bad])
    upserted, not_listed = saved.sent
    assert [row["id"] for row in cast(list[dict[str, Any]], upserted)] == [PRO]
    assert not_listed == {"ids": [PRO]}


def test_refresh_models_without_a_list_says_nothing_changed(
    fake: OpenRouterFake, capsys: pytest.CaptureFixture[str]
) -> None:
    fake.responder = lambda request: error_reply(503)
    _, settings = _client(fake)
    out = io.StringIO()
    assert cli.main(["refresh-models"], db=cast(Database, None), settings=settings, out=out) == 2
    assert "nothing was changed" in capsys.readouterr().err
    assert out.getvalue() == ""


def test_refresh_models_needs_the_key(capsys: pytest.CaptureFixture[str]) -> None:
    out = io.StringIO()
    assert cli.main(["refresh-models"], db=cast(Database, None), settings=make_settings(), out=out) == 2
    assert "OPENROUTER_API_KEY" in capsys.readouterr().err


# --- the daily sweep ----------------------------------------------------------------------


class _Jobs:
    def __init__(self) -> None:
        self.turns = 0

    def run_once(self) -> bool:
        self.turns += 1
        return False


class _Clock:
    def __init__(self) -> None:
        self.now = NOW

    def __call__(self) -> datetime:
        return self.now


class _Lock:
    """A sweep lock that is this copy's (``mine``) or another's; the names asked for."""

    def __init__(self, mine: bool) -> None:
        self.mine = mine
        self.names: list[str] = []

    @contextmanager
    def __call__(self, name: str) -> Iterator[bool]:
        self.names.append(name)
        yield self.mine


class _Refresh:
    def __init__(self) -> None:
        self.calls = 0

    def __call__(self) -> Refreshed:
        self.calls += 1
        return Refreshed(3, 2)


def test_the_sweep_runs_on_the_first_turn_then_once_a_day_under_its_lock() -> None:
    jobs, clock, refresh, lock = _Jobs(), _Clock(), _Refresh(), _Lock(True)
    sweep = RefreshCatalog(jobs, refresh, clock, lock=lock)
    sweep.run_once()
    clock.now += timedelta(hours=23)
    sweep.run_once()
    clock.now += timedelta(hours=1)
    sweep.run_once()
    assert (refresh.calls, jobs.turns, lock.names) == (2, 3, ["refresh_catalog"] * 2)


def test_a_failed_pass_is_tried_again_in_an_hour_not_a_day() -> None:
    """R7: a refresh that failed comes back after RETRY_AFTER_FAILURE."""
    jobs, clock, calls = _Jobs(), _Clock(), []

    def fails_once() -> Refreshed:
        calls.append(clock.now)
        if len(calls) == 1:
            raise CatalogUnavailable("status_503")
        return Refreshed(3, 2)

    sweep = RefreshCatalog(jobs, fails_once, clock)
    sweep.run_once()
    clock.now += timedelta(minutes=59)
    sweep.run_once()
    clock.now += timedelta(minutes=1)
    sweep.run_once()  # the retry, an hour on
    clock.now += timedelta(hours=23)
    sweep.run_once()  # done: the next is a day after it
    assert calls == [NOW, NOW + timedelta(hours=1)]


def test_a_worker_started_within_a_day_of_the_last_refresh_waits_for_it() -> None:
    """R7: restarts never refresh again and again; an old or empty catalogue refreshes now."""
    jobs, clock, refresh = _Jobs(), _Clock(), _Refresh()
    fresh = RefreshCatalog(jobs, refresh, clock, last_refreshed=lambda: NOW - timedelta(hours=20))
    fresh.run_once()
    clock.now += timedelta(hours=3, minutes=59)
    fresh.run_once()
    assert refresh.calls == 0
    clock.now += timedelta(minutes=1)
    fresh.run_once()  # a day after the last refresh
    assert refresh.calls == 1
    RefreshCatalog(jobs, refresh, _Clock(), last_refreshed=lambda: None).run_once()  # empty
    RefreshCatalog(jobs, refresh, _Clock(), last_refreshed=lambda: NOW - timedelta(days=2)).run_once()
    assert refresh.calls == 3


def test_the_sweep_skips_without_the_lock_and_in_keyless_mode() -> None:
    jobs, refresh = _Jobs(), _Refresh()
    RefreshCatalog(jobs, refresh, _Clock(), lock=_Lock(False)).run_once()
    RefreshCatalog(jobs, None, _Clock()).run_once()
    assert (refresh.calls, jobs.turns) == (0, 2)
    assert (
        catalog_refresh(make_settings(ocr_engine="rapidocr", model_enabled=False), cast(Database, None))
        is None
    )
    gemini = make_settings(ocr_engine="gemini", openrouter_api_key=KEY)
    assert catalog_refresh(gemini, cast(Database, None)) is not None


@pytest.mark.parametrize(
    "error", [CatalogUnavailable("status_503"), OperationalError("SELECT 1", {}, Exception("down"))]
)
def test_a_failed_refresh_is_logged_by_code_and_the_jobs_go_on(
    error: Exception, caplog: pytest.LogCaptureFixture
) -> None:
    def fails() -> Refreshed:
        raise error

    jobs = _Jobs()
    caplog.set_level(logging.INFO, logger="kaagaz.worker")
    RefreshCatalog(jobs, fails, _Clock()).run_once()
    assert jobs.turns == 1
    [record] = caplog.records
    assert record.getMessage() == "model catalogue not refreshed"
    assert record.__dict__["code"] in {"status_503", "OperationalError"}


# --- no model names in code (MC1) ----------------------------------------------------------

KAAGAZ = Path(__file__).resolve().parents[1] / "kaagaz"
# A model's name: an OpenRouter author/slug naming one of the families, or a family's
# versioned name. Settings may name the defaults (the only exception).
MODEL_NAME = re.compile(r"(?i)\b[a-z0-9-]+/[a-z0-9.-]*(gemini|claude|gpt|llama|mistral|grok|deepseek)")


def _code_strings(path: Path) -> Iterator[tuple[int, str]]:
    """Every string in the module's code, docstrings left out (they explain, they don't choose)."""
    tree = ast.parse(path.read_text("utf-8"))
    docstrings = {
        id(node.body[0].value)
        for node in ast.walk(tree)
        if isinstance(node, ast.Module | ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef)
        and node.body
        and isinstance(node.body[0], ast.Expr)
        and isinstance(node.body[0].value, ast.Constant)
    }
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str) and id(node) not in docstrings:
            yield node.lineno, node.value


def test_no_model_name_is_written_in_the_code_outside_the_settings() -> None:
    found = [
        f"{path.relative_to(KAAGAZ)}:{line}"
        for path in sorted(KAAGAZ.rglob("*.py"))
        if path.name != "settings.py"
        for line, value in _code_strings(path)
        if MODEL_NAME.search(value)
    ]
    assert found == []


def test_the_model_name_pattern_finds_names_and_spares_other_slashes() -> None:
    for name in (PRO, SONNET, LUNA, "google/gemini-embedding-2", "meta-llama/llama-4", "use google/gemini-x"):
        assert MODEL_NAME.search(name), name
    for kept in (
        "/chat/completions",
        "application/json",
        "google-vertex",
        "kaagaz/not-a-model-0",
        "image/png",
    ):
        assert not MODEL_NAME.search(kept), kept
