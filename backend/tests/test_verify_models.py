"""``python -m kaagaz.cli verify-models`` against the fake OpenRouter API (never the real one).

``openrouter_like`` answers the way the council's fact-check says OpenRouter does: the
endpoints listing names google-vertex with the parameters we send, strict structured
output, every reasoning effort but minimal, more prompt tokens at detail high, ``length``
with native ``MAX_TOKENS`` at a tiny output cap, 404 for an unknown model and 401 with no
key. RapidOCR is the installed package (the ocr extra, as in CI).
"""

import base64
import io
import json
import math
from collections.abc import Iterator
from typing import Any

import pytest

from kaagaz import cli, verify_models
from kaagaz.ai import openrouter
from kaagaz.reading.gemini_ocr import GEMINI_OCR_PROMPT_VERSION, PAGE_SCHEMA
from kaagaz.settings import Settings
from kaagaz.verify_models import TRUTH, listed_sha256, made_up_page
from tests.openrouter_fake import (
    API_PATH,
    KEY,
    SECRET,
    OpenRouterFake,
    Recorded,
    Reply,
    chat_reply,
    embed_reply,
    endpoints_reply,
    error_reply,
    json_reply,
    key_reply,
    running_fake,
    slow,
    usage,
)
from tests.settings_factory import make_settings

PRO = "google/gemini-3.1-pro-preview"
FLASH = "google/gemini-3.8-flash"
OTHER = "openai/gpt-6-luna"  # a model that is not Gemini
KNOWN = {PRO, FLASH, "google/gemini-embedding-2"}
ITEMS = [
    "endpoints",
    "model_calls",
    "strict_schema",
    "reasoning_levels",
    "detail_tokens",
    "usage_fields",
    "max_tokens_finish",
    "embedding_dims",
    "no_key_status",
    "unknown_model",
    "key_limit",
    "default_temperature",
    "rapidocr_detection_only",
    "rapidocr_recognition_only",
    "detection_model_sha",
    "parallel_latency",
]
# The --model items (model choice, council 11 Oct 2026), after the ones above.
MODEL_ITEMS = [
    "model_endpoint",
    "model_reply",
    "model_page_schema",
    "model_made_up_page",
    "model_boxes",
    "model_devanagari",
    "model_efforts",
    "model_cost",
    "model_truncation",
    "model_token_cap",
    "model_refusal",
]
PROVIDER_BLOCK = {
    "only": ["google-vertex"],
    "allow_fallbacks": False,
    "require_parameters": True,
    "data_collection": "deny",
    "zdr": True,
}
EMBED_PROVIDER_BLOCK = {**PROVIDER_BLOCK, "require_parameters": False}
_PROMPT = {None: 1290, "low": 300, "high": 1120}
_REASONING = {"low": 50, "medium": 200, "high": 800}


def _detail(body: dict[str, Any]) -> tuple[bool, str | None]:
    """(the request holds a picture, its image_url.detail)."""
    content = body["messages"][0]["content"]
    if not isinstance(content, list):
        return False, None
    return True, content[1]["image_url"].get("detail")


def page_value() -> dict[str, Any]:
    """The made-up page as PAGE_SCHEMA's JSON, each line's box_2d its own box in 0..1000."""
    page = made_up_page()
    width, height = page.image.size
    lines = [
        {
            "order": index,
            "kind": "paragraph",
            "text": text,
            "box_2d": [y0 / height * 1000, x0 / width * 1000, y1 / height * 1000, x1 / width * 1000],
            "unsure": [],
            "continues_on_next_page": False,
            "table": None,
        }
        for index, (text, (x0, y0, x1, y1)) in enumerate(zip(TRUTH, page.boxes, strict=True))
    ]
    page_facts = {"quality": "good", "handwritten": False, "scripts": ["latin"], "issues": []}
    return {"page": page_facts, "lines": lines}


def zdr_reply(*pairs: tuple[str, str]) -> Reply:
    """GET /endpoints/zdr: the zero-data-retention endpoints, as (model id, tag)."""
    return Reply(200, {"data": [{"model_id": model, "tag": tag, "name": tag} for model, tag in pairs]})


def openrouter_like(request: Recorded) -> Reply:
    if "authorization" not in request.headers:
        return error_reply(401, error_type="authentication")
    if request.action == "key":
        return key_reply()
    if request.action == "zdr":
        return zdr_reply(*((model, "google-vertex") for model in sorted(KNOWN)))
    if request.action.startswith("generation?id="):
        return Reply(200, {"data": {"id": "gen-made-up", "provider_name": "Google", "total_cost": 0.0012}})
    if request.action == "endpoints":
        model = request.path.removeprefix(API_PATH + "/models/").removesuffix("/endpoints")
        return endpoints_reply(model, zdr=True) if model in KNOWN else error_reply(404)
    body = request.body
    if body.get("provider") != (EMBED_PROVIDER_BLOCK if request.action == "embeddings" else PROVIDER_BLOCK):
        return error_reply(400, error_type="bad_request")
    if body["model"] not in KNOWN:
        return error_reply(404)
    if request.action == "embeddings":
        side = 1 / math.sqrt(body["dimensions"])
        return embed_reply([[side] * body["dimensions"] for _ in body["input"]])
    effort = body["reasoning"]["effort"]
    if effort == "minimal":
        return error_reply(400, error_type="bad_request")
    picture, detail = _detail(body)
    thought = _REASONING[effort]
    counts = usage((_PROMPT[detail] if picture else 0) + 40, 30 + thought, thought, cost=0.0012)
    if body.get("max_completion_tokens") == verify_models.TINY_OUTPUT_TOKENS:
        return chat_reply('{"lin', finish="length", native="MAX_TOKENS", counts=usage(1160, 16, 12))
    if body.get("response_format", {}).get("json_schema", {}).get("schema") == PAGE_SCHEMA:
        return json_reply(page_value(), counts=counts)
    if "response_format" in body:
        return slow(json_reply({"lines": list(TRUTH)}, counts=counts), 0.05)
    return chat_reply("OK", counts=counts)


@pytest.fixture
def refusal_blocked(monkeypatch: pytest.MonkeyPatch) -> None:
    """A native ``refusal`` (a Claude refusal) is blocked, as the facts file asks of the
    client (Track B's mapping); without it, model_refusal FAILs."""
    monkeypatch.setattr(openrouter, "BLOCKED_FINISH_REASONS", openrouter.BLOCKED_FINISH_REASONS | {"refusal"})


@pytest.fixture
def fake() -> Iterator[OpenRouterFake]:
    with running_fake() as server:
        server.responder = openrouter_like
        yield server


def _settings(fake: OpenRouterFake, **overrides: Any) -> Settings:
    values: dict[str, Any] = {
        "openrouter_api_key": KEY,
        "openrouter_base_url": fake.url,
        "ocr_engine": "gemini",
        "model_rpm": 10**9,
        "ocr_timeout_seconds": 10.0,
    }
    return make_settings(**{**values, **overrides})


def _run(
    settings: Settings, *args: str, command: str = "verify-models"
) -> tuple[int, str, dict[str, tuple[str, str]]]:
    out = io.StringIO()
    code = cli.main([command, *args], settings=settings, out=out, db=None)
    text = out.getvalue()
    lines = {}
    for line in text.splitlines():
        if line.startswith("VERIFY "):
            _, item, verdict, evidence = [*line.split(" ", 3), ""][:4]
            lines[item] = (verdict, evidence)
    return code, text, lines


@pytest.mark.usefixtures("refusal_blocked")
def test_every_item_passes_against_an_api_that_answers_as_documented(fake: OpenRouterFake) -> None:
    code, text, lines = _run(_settings(fake), "--model", FLASH)
    assert list(lines) == ITEMS + MODEL_ITEMS
    verdicts = {item: verdict for item, (verdict, _) in lines.items()}
    info = {"key_limit", "model_devanagari"}
    assert verdicts == {item: "INFO" if item in info else "PASS" for item in ITEMS + MODEL_ITEMS}, text
    assert code == 0
    assert text.startswith("KEY OPENROUTER_API_KEY set\n")
    assert "SUMMARY 25 PASS, 0 FAIL, 2 INFO" in text
    assert lines["endpoints"][1] == (
        f"{PRO}: google-vertex listed; parameters all supported; zdr=True;"
        f" {FLASH}: google-vertex listed; parameters all supported; zdr=True"
    )
    assert lines["model_calls"][1] == f"{PRO}=200 {FLASH}=200; with the provider block"
    assert lines["strict_schema"][1] == "strict=true:200 json strict=false:200 json; requests use strict=true"
    assert "minimal=400 bad_request" in lines["reasoning_levels"][1]
    assert "reasoning at high 800" in lines["reasoning_levels"][1]
    assert "detail changes prompt tokens yes; high-low=820" in lines["detail_tokens"][1]
    assert "completion_tokens includes reasoning yes; cost present 0.0012" in lines["usage_fields"][1]
    assert lines["max_tokens_finish"][1].endswith(
        "finish length native MAX_TOKENS completion 16 reasoning 12; client: output_truncated"
    )
    assert lines["embedding_dims"][1].startswith(
        "google/gemini-embedding-2: 2 x [1024] finite, norms 1.0000, 1.0000"
    )
    assert lines["no_key_status"][1] == "status 401 authentication; client: key_rejected"
    assert lines["unknown_model"][1] == "kaagaz/not-a-model-0=404; client: model_not_found"
    assert lines["key_limit"][1].startswith("limit 100.0000 limit_remaining 87.5000 usage 12.5000")
    assert lines["default_temperature"][1].startswith("3/3 runs exact; runs identical yes")
    assert lines["parallel_latency"][1].startswith("6/6 ok;")
    assert fake.max_in_flight > 1  # the six reads overlapped
    assert fake.unanswered == 0
    assert lines["model_endpoint"][1] == f"{FLASH}: ZDR endpoints google-vertex; reads through google-vertex"
    assert lines["model_reply"][1] == f"{FLASH}: 200; provider_name Google"
    assert lines["model_page_schema"][1].startswith(f"{FLASH}: 200 json follows PAGE_SCHEMA; tokens in ")
    assert lines["model_made_up_page"][1] == f"{FLASH}: 3/3 lines exact, 3 read"
    assert lines["model_boxes"][1].startswith(f"{FLASH}: pinned 3/3; best IoU per line 1.00 1.00 1.00")
    assert "no Devanagari font is bundled" in lines["model_devanagari"][1]
    assert lines["model_efforts"][1] == f"{FLASH}: high=200 json reasoning=800 low=200 json reasoning=50"
    assert lines["model_cost"][1] == f"{FLASH}: usage.cost 0.0012 on the page read"
    assert lines["model_token_cap"][1] == f"{FLASH}: completion 16 at a cap of 16"
    assert lines["model_refusal"][1] == (
        f"{FLASH}: message.refusal=blocked content_filter=blocked native refusal=blocked; local, no call"
    )


@pytest.mark.parametrize("limit", [None, "unlimited"])
def test_key_limit_fails_when_openrouter_has_no_numeric_credit_limit(
    fake: OpenRouterFake, limit: object
) -> None:
    def without_limit(request: Recorded) -> Reply:
        if request.action == "key":
            return Reply(200, {"data": {"limit": limit, "limit_remaining": 5, "usage": 0}})
        return openrouter_like(request)

    fake.responder = without_limit
    code, _, lines = _run(_settings(fake))
    assert code == 1
    assert lines["key_limit"] == (
        "FAIL",
        "credit limit is missing or not numeric; set a per-key credit limit on openrouter.ai",
    )


def test_the_retired_verify_gemini_command_name_is_not_accepted() -> None:
    with pytest.raises(SystemExit):
        cli._parser().parse_args(["verify-gemini"])


def test_every_request_carries_the_provider_block(fake: OpenRouterFake) -> None:
    _run(_settings(fake))
    posts = [request for request in fake.requests if request.method == "POST"]
    assert {request.action for request in posts} == {"completions", "embeddings"}
    assert all(
        request.body["provider"]
        == (EMBED_PROVIDER_BLOCK if request.action == "embeddings" else PROVIDER_BLOCK)
        for request in posts
    )
    assert {request.action for request in fake.requests if request.method == "GET"} == {"endpoints", "key"}


def test_the_key_and_its_label_are_never_printed_and_the_key_goes_only_in_its_header(
    fake: OpenRouterFake,
) -> None:
    _, text, _ = _run(_settings(fake))
    assert KEY not in text
    assert "sk-or-v1" not in text  # the key's label (GET /key) is made of the key's characters
    keyed = [request for request in fake.requests if "authorization" in request.headers]
    assert all(request.headers["authorization"] == "Bearer " + KEY for request in keyed)
    assert all(KEY not in request.path and KEY not in json.dumps(request.body) for request in fake.requests)
    assert len(fake.requests) - len(keyed) == 1  # the no-key probe only
    for request in fake.requests:
        assert not {"http-referer", "referer", "x-title"} & set(request.headers)


def test_only_made_up_pictures_are_sent(fake: OpenRouterFake) -> None:
    _run(_settings(fake))
    page = made_up_page()
    sent = {
        part["image_url"]["url"]
        for body in fake.bodies()
        for message in body["messages"]
        if isinstance(message["content"], list)
        for part in message["content"]
        if part["type"] == "image_url"
    }
    assert sent == {"data:image/png;base64," + base64.b64encode(page.png).decode("ascii")}


def test_a_missing_key_fails_the_live_items_and_still_checks_the_rest(fake: OpenRouterFake) -> None:
    code, text, lines = _run(_settings(fake, openrouter_api_key=None, ocr_engine="rapidocr"))
    assert code == 1
    assert text.startswith("KEY OPENROUTER_API_KEY missing\n")
    local = {"no_key_status", "rapidocr_detection_only", "rapidocr_recognition_only", "detection_model_sha"}
    for item in ITEMS:
        verdict, evidence = lines[item]
        if item in local:
            assert verdict == "PASS", (item, evidence)
        else:
            assert (verdict, evidence) == ("FAIL", "OPENROUTER_API_KEY missing")
    assert [request.headers.get("authorization") for request in fake.requests] == [None]


def test_a_strict_schema_the_api_refuses_fails_with_the_one_line_fix(fake: OpenRouterFake) -> None:
    def no_strict(request: Recorded) -> Reply:
        body = request.body if isinstance(request.body, dict) else {}
        if body.get("response_format", {}).get("json_schema", {}).get("strict") is True:
            return error_reply(400, error_type="bad_request")
        return openrouter_like(request)

    fake.responder = no_strict
    code, _, lines = _run(_settings(fake))
    assert code == 1
    assert lines["strict_schema"] == (
        "FAIL",
        "strict=true:400 bad_request strict=false:200 json;"
        " requests use strict=true: set STRICT_SCHEMA to False",
    )


def test_a_detail_that_changes_nothing_is_info_for_a_person(fake: OpenRouterFake) -> None:
    def ignores_detail(request: Recorded) -> Reply:
        reply = openrouter_like(request)
        body = request.body if isinstance(request.body, dict) else {}
        if request.action == "completions" and "response_format" in body and isinstance(reply.body, dict):
            reply.body["usage"]["prompt_tokens"] = 1330
        return reply

    fake.responder = ignores_detail
    _, _, lines = _run(_settings(fake))
    verdict, evidence = lines["detail_tokens"]
    assert verdict == "INFO"
    assert "detail changes prompt tokens no" in evidence
    assert evidence.endswith("detail is not passed on (MEDIA_RESOLUTION_SHAPE)")


@pytest.mark.parametrize(
    ("listing", "said"),
    [
        (endpoints_reply(PRO, tags=("google-ai-studio",)), "1 endpoints, google-vertex not listed"),
        (
            endpoints_reply(PRO, parameters=("reasoning", "max_completion_tokens")),
            "google-vertex listed; parameters lack response_format; no zdr field",
        ),
        (error_reply(404), "status 404, no endpoints list"),
    ],
)
def test_an_endpoint_listing_without_what_we_need_fails(
    fake: OpenRouterFake, listing: Reply, said: str
) -> None:
    fake.script("endpoints", listing)
    code, _, lines = _run(_settings(fake))
    assert code == 1
    assert lines["endpoints"] == ("FAIL", f"{PRO}: {said}")


def test_a_model_id_that_is_not_openrouters_fails_without_a_request(fake: OpenRouterFake) -> None:
    code, _, lines = _run(_settings(fake), "--model", "gemini-3.8-flash", "--model", "google/gemini-3:free")
    assert code == 1
    assert lines["endpoints"][1].startswith(
        "gemini-3.8-flash=not an OpenRouter model ID; google/gemini-3:free=not an OpenRouter model ID;"
    )
    assert lines["model_calls"][0] == "FAIL"
    assert all("gemini-3.8-flash" not in request.path for request in fake.requests)
    assert all("gemini-3:free" not in json.dumps(request.body) for request in fake.requests)


def test_a_cut_reply_without_googles_max_tokens_fails(fake: OpenRouterFake) -> None:
    def no_native(request: Recorded) -> Reply:
        body = request.body if isinstance(request.body, dict) else {}
        if body.get("max_completion_tokens") == verify_models.TINY_OUTPUT_TOKENS:
            return chat_reply('{"lin', finish="length", native=None)
        return openrouter_like(request)

    fake.responder = no_native
    _, _, lines = _run(_settings(fake))
    assert lines["max_tokens_finish"][0] == "FAIL"


def test_no_reasoning_tokens_at_high_fails(fake: OpenRouterFake) -> None:
    def no_reasoning(request: Recorded) -> Reply:
        reply = openrouter_like(request)
        if isinstance(reply.body, dict) and "usage" in reply.body:
            reply.body["usage"].pop("completion_tokens_details", None)
        return reply

    fake.responder = no_reasoning
    _, _, lines = _run(_settings(fake))
    assert lines["reasoning_levels"][0] == "FAIL"
    assert "reasoning at high 0" in lines["reasoning_levels"][1]
    assert "includes reasoning unknown" in lines["usage_fields"][1]


def test_an_inexact_read_fails_default_temperature(fake: OpenRouterFake) -> None:
    reads = {"n": 0}

    def one_wrong(request: Recorded) -> Reply:
        body = request.body if isinstance(request.body, dict) else {}
        if "response_format" in body and body.get("max_completion_tokens", 0) > 1000:
            reads["n"] += 1
            if reads["n"] == 2:
                return json_reply({"lines": [TRUTH[0], "Total Rs 12,346.00", TRUTH[2]]})
        return openrouter_like(request)

    fake.responder = one_wrong
    code, _, lines = _run(_settings(fake))
    assert code == 1
    assert lines["default_temperature"][0] == "FAIL"
    assert lines["default_temperature"][1].startswith("2/3 runs exact; runs identical no")


def test_a_reader_outage_fails_the_live_items_with_codes_only(fake: OpenRouterFake) -> None:
    fake.responder = lambda request: error_reply(400, error_type="bad_request")
    code, text, lines = _run(_settings(fake))
    assert code == 1
    assert lines["default_temperature"][1].startswith("0/3 runs exact")
    assert "bad_request" in lines["default_temperature"][1]
    assert lines["parallel_latency"][1].startswith("0/6 ok (bad_request)")
    assert lines["no_key_status"][0] == "FAIL"
    assert SECRET not in text  # error bodies are never printed


def test_rapidocrs_model_list_is_read_as_file_and_sha() -> None:
    listing = [
        "onnxruntime:",
        "  PP-OCRv6:",
        "    det:",
        "      multi_PP-OCRv6_det_small:",
        "        model_dir: https://example.invalid/v3.9.2/onnx/PP-OCRv6/det/PP-OCRv6_det_small.onnx",
        "        SHA256: " + "a" * 64,
        "mnn:",
        "      multi_PP-OCRv6_det_small:",
        "        model_dir: https://example.invalid/v3.9.2/mnn/PP-OCRv6/det/PP-OCRv6_det_small.mnn",
        "        SHA256: " + "b" * 64,
    ]
    assert listed_sha256(listing) == {"PP-OCRv6_det_small.onnx": "a" * 64}


def test_the_made_up_page_holds_every_line_inside_the_picture() -> None:
    page = made_up_page()
    width, height = page.image.size
    assert len(page.boxes) == len(TRUTH)
    for left, top, right, bottom in page.boxes:
        assert 8 <= left < right <= width - 8 and 8 <= top < bottom <= height - 8
    assert page.png.startswith(b"\x89PNG")


# --- the --model items and --record (model choice, council 11 Oct 2026) ------------------


def _model_run(
    fake: OpenRouterFake,
    *models: str,
    record: Any = None,
    sleep: Any = None,
    **overrides: Any,
) -> tuple[int, str, dict[str, tuple[str, str]]]:
    out = io.StringIO()
    code = verify_models.run(
        _settings(fake, **overrides),
        out,
        models=models,
        sleep=sleep or (lambda seconds: None),
        record=record,
    )
    lines = {}
    for line in out.getvalue().splitlines():
        if line.startswith("VERIFY "):
            _, item, verdict, evidence = [*line.split(" ", 3), ""][:4]
            lines[item] = (verdict, evidence)
    return code, out.getvalue(), lines


@pytest.mark.usefixtures("refusal_blocked")
def test_record_writes_the_pass_and_the_page_tokens_when_every_required_item_passes(
    fake: OpenRouterFake,
) -> None:
    recorded: list[tuple[str, int, int]] = []
    code, text, _ = _model_run(fake, FLASH, record=lambda *row: recorded.append(row))
    assert code == 0, text
    [(model, tokens_in, tokens_out)] = recorded
    assert model == FLASH and tokens_in > 0 and tokens_out > 0
    assert f"RECORDED {FLASH} prompt {GEMINI_OCR_PROMPT_VERSION} tokens per page in {tokens_in}" in text


def _without_cost(request: Recorded) -> Reply:
    reply = openrouter_like(request)
    if isinstance(reply.body, dict) and isinstance(reply.body.get("usage"), dict):
        reply.body["usage"].pop("cost", None)
    return reply


def _boxes_elsewhere(request: Recorded) -> Reply:
    """The page read with every box at the page's bottom right, where no line is."""
    reply = openrouter_like(request)
    body = request.body if isinstance(request.body, dict) else {}
    if body.get("response_format", {}).get("json_schema", {}).get("schema") == PAGE_SCHEMA:
        value = page_value()
        for line in value["lines"]:
            line["box_2d"] = [960, 960, 990, 990]
        return json_reply(value, counts=usage(cost=0.0012))
    return reply


@pytest.mark.usefixtures("refusal_blocked")
@pytest.mark.parametrize(
    ("responder", "failed"),
    [(_without_cost, "model_cost"), (_boxes_elsewhere, "model_boxes")],
)
def test_record_writes_nothing_when_a_required_item_fails(
    fake: OpenRouterFake, responder: Any, failed: str
) -> None:
    fake.responder = responder
    recorded: list[tuple[str, int, int]] = []
    code, text, lines = _model_run(fake, FLASH, record=lambda *row: recorded.append(row))
    assert (code, recorded) == (1, [])
    assert lines[failed][0] == "FAIL"
    assert f"NOT RECORDED {FLASH}: {failed} did not pass" in text


def test_a_native_refusal_the_client_does_not_block_fails_model_refusal_and_is_not_recorded(
    fake: OpenRouterFake, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(openrouter, "BLOCKED_FINISH_REASONS", openrouter.BLOCKED_FINISH_REASONS - {"refusal"})
    recorded: list[tuple[str, int, int]] = []
    _, text, lines = _model_run(fake, FLASH, record=lambda *row: recorded.append(row))
    assert lines["model_refusal"] == (
        "FAIL",
        f"{FLASH}: message.refusal=blocked content_filter=blocked native refusal=text; local, no call",
    )
    assert recorded == [] and f"NOT RECORDED {FLASH}: model_refusal did not pass" in text


@pytest.mark.parametrize(
    "zdr",
    [
        zdr_reply((PRO, "google-vertex")),  # not a ZDR endpoint of this model
        zdr_reply((FLASH, "azure")),  # a ZDR endpoint, but of a provider outside OPENROUTER_PROVIDERS
    ],
)
def test_a_model_with_no_allowed_zdr_endpoint_fails_and_is_never_read(
    fake: OpenRouterFake, zdr: Reply
) -> None:
    def listed(request: Recorded) -> Reply:
        if request.action == "zdr":
            return zdr
        if request.action == "endpoints" and FLASH in request.path:
            return endpoints_reply(FLASH, tags=("google-vertex", "azure"))
        return openrouter_like(request)

    fake.responder = listed
    code, _, lines = _model_run(fake, FLASH)
    assert code == 1
    assert lines["model_endpoint"][0] == "FAIL"
    assert "none of OPENROUTER_PROVIDERS" in lines["model_endpoint"][1]
    for item in ("model_reply", "model_page_schema", "model_efforts", "model_cost", "model_truncation"):
        assert lines[item] == ("FAIL", f"{FLASH}: no usable endpoint (model_endpoint)")
    pages = [body for body in fake.bodies() if body["model"] == FLASH and "response_format" in body]
    assert pages == []  # only model_calls' one line of text was sent to it


def test_an_azure_endpoint_counts_once_azure_is_an_approved_provider(fake: OpenRouterFake) -> None:
    azure = {**PROVIDER_BLOCK, "only": ["azure"]}

    def azure_only(request: Recorded) -> Reply:
        if request.action == "endpoints" and FLASH in request.path:
            return endpoints_reply(FLASH, tags=("azure",))
        if request.action == "zdr":
            return zdr_reply((FLASH, "azure"))
        if isinstance(request.body, dict) and request.body.get("provider") == azure:
            body = {**request.body, "provider": PROVIDER_BLOCK}  # the fake answers google-vertex's block
            return openrouter_like(Recorded(request.method, request.path, request.headers, body))
        return openrouter_like(request)

    fake.responder = azure_only
    allowed = frozenset({"google-vertex", "azure"})
    _, _, lines = _model_run(fake, FLASH, openrouter_providers=allowed)
    assert lines["model_endpoint"] == ("PASS", f"{FLASH}: ZDR endpoints azure; reads through azure")
    assert lines["model_page_schema"][0] == "PASS"
    pages = [body for body in fake.bodies() if body["model"] == FLASH and "response_format" in body]
    assert pages and all(body["provider"] == azure for body in pages)


def test_max_tokens_stands_for_max_completion_tokens_in_an_endpoint_listing(fake: OpenRouterFake) -> None:
    fake.script(
        "endpoints",
        endpoints_reply(PRO, parameters=("response_format", "reasoning", "max_tokens", "structured_outputs")),
    )
    _, _, lines = _run(_settings(fake))
    assert lines["endpoints"][1].startswith(f"{PRO}: google-vertex listed; parameters all supported")


def test_a_cut_reply_with_another_providers_native_reason_passes_for_a_model_that_is_not_gemini(
    fake: OpenRouterFake,
) -> None:
    def own_native(request: Recorded) -> Reply:
        body = request.body if isinstance(request.body, dict) else {}
        if body.get("max_completion_tokens") == verify_models.TINY_OUTPUT_TOKENS:
            return chat_reply(
                '{"lin', finish="length", native="max_output_tokens", counts=usage(1160, 16, 12)
            )
        if body.get("model") == OTHER:  # the fake answers for its known models
            request = Recorded(request.method, request.path, request.headers, {**body, "model": PRO})
        return openrouter_like(request)

    fake.responder = own_native
    _, _, lines = _run(_settings(fake, ocr_model=OTHER))
    assert lines["max_tokens_finish"][0] == "PASS", lines["max_tokens_finish"]
    _, _, lines = _run(_settings(fake))  # Google's model must still name MAX_TOKENS
    assert lines["max_tokens_finish"][0] == "FAIL"


def test_a_reply_with_no_provider_name_fails_model_reply_after_three_tries(fake: OpenRouterFake) -> None:
    def no_name(request: Recorded) -> Reply:
        if request.action.startswith("generation"):
            return error_reply(404)
        return openrouter_like(request)

    fake.responder = no_name
    waits: list[float] = []
    _, _, lines = _model_run(fake, FLASH, sleep=waits.append)
    assert lines["model_reply"] == ("FAIL", f"{FLASH}: 200; provider_name not given")
    assert waits == [1.0, 1.0]
    assert len([r for r in fake.requests if r.action.startswith("generation")]) == 3


def test_the_devanagari_page_is_info_with_its_reason_and_record_does_not_need_it(
    fake: OpenRouterFake,
) -> None:
    _, _, lines = _model_run(fake, FLASH)
    verdict, evidence = lines["model_devanagari"]
    assert verdict == "INFO"
    assert "no Devanagari font is bundled" in evidence
    assert "model_devanagari" not in verify_models.REQUIRED


@pytest.mark.parametrize("models", [[], ["--model", FLASH, "--model", PRO]])
def test_record_checks_exactly_one_model(fake: OpenRouterFake, models: list[str]) -> None:
    code, text, _ = _run(_settings(fake), *models, "--record")
    assert code == 2 and text == ""
    assert fake.requests == []
