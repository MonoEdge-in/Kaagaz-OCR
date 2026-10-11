"""The model catalogue refresh (model choice, council 11 Oct 2026, MC1) against the fake
OpenRouter API, from a small slice of OpenRouter's real lists (``fixtures/openrouter_models
.json``): each filter rule drops a model, hostile JSON is refused, a list that can't be
read changes nothing, and the daily sweep. The SQL itself: ``test_model_catalog_db.py``."""

import ast
import copy
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
from kaagaz.ai.openrouter import OpenRouterClient
from kaagaz.db.engine import Database
from kaagaz.jobs import catalog
from kaagaz.jobs.catalog import CatalogUnavailable, RefreshCatalog, Refreshed, candidate, fetch
from kaagaz.worker_main import catalog_refresh
from tests.openrouter_fake import API_PATH, KEY, OpenRouterFake, Recorded, Reply, error_reply, running_fake
from tests.settings_factory import make_settings

FIXTURE = json.loads((Path(__file__).parent / "fixtures" / "openrouter_models.json").read_text("utf-8"))
TODAY = date(2026, 10, 11)
NOW = datetime(2026, 10, 11, 9, 0, tzinfo=UTC)
EFFORTS = frozenset({"low", "high"})
PRO, SONNET, LUNA = "google/gemini-3.1-pro-preview", "anthropic/claude-sonnet-5.5", "openai/gpt-6-luna"
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
def fake() -> Iterator[OpenRouterFake]:
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
    models = {model.id: model for model in fetch(client, settings, today=TODAY)}
    # Rules 1-4 and 6 (and a router with no fixed price) leave a model out altogether.
    assert set(models) == {PRO, SONNET, LUNA, "example/not-zdr-1", "example/endpoint-down-1"}
    eligible = {model_id for model_id, model in models.items() if model.eligible}
    assert eligible == {PRO, SONNET}  # rule 5: a ZDR endpoint of google-vertex, status 0
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
    client, settings = _client(fake, openrouter_providers=frozenset({"google-vertex", "azure"}))
    eligible = {model.id for model in fetch(client, settings, today=TODAY) if model.eligible}
    assert eligible == {PRO, SONNET, LUNA}


def test_the_configured_efforts_decide_rule_6(fake: OpenRouterFake) -> None:
    client, settings = _client(fake, ocr_thinking_level="minimal", ocr_retry_thinking_level="low")
    assert {model.id for model in fetch(client, settings, today=TODAY)} == {"example/low-effort-only-1"}


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
        _with(reasoning=None),
        _with(expiration_date="soon"),
        _with(expiration_date=20271231),
    ],
)
def test_hostile_or_malformed_json_is_left_out(item: object) -> None:
    assert candidate(item, efforts=EFFORTS, today=TODAY) is None


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
    pro = next(model for model in fetch(client, settings, today=TODAY) if model.id == PRO)
    assert (pro.providers, pro.eligible) == ((), False)


@pytest.mark.parametrize(
    ("broken", "code"),
    [
        ("models", "status_500"),
        ("zdr", "status_500"),
        ("endpoints", "status_500"),
        ("models-shape", "models_list"),
        ("zdr-shape", "zdr_list"),
        ("endpoints-shape", "endpoints_list"),
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
