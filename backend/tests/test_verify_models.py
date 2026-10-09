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


def openrouter_like(request: Recorded) -> Reply:
    if "authorization" not in request.headers:
        return error_reply(401, error_type="authentication")
    if request.action == "key":
        return key_reply()
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
    if "response_format" in body:
        return slow(json_reply({"lines": list(TRUTH)}, counts=counts), 0.05)
    return chat_reply("OK", counts=counts)


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


def test_every_item_passes_against_an_api_that_answers_as_documented(fake: OpenRouterFake) -> None:
    code, text, lines = _run(_settings(fake), "--model", FLASH)
    assert list(lines) == ITEMS
    verdicts = {item: verdict for item, (verdict, _) in lines.items()}
    assert verdicts == {item: "INFO" if item == "key_limit" else "PASS" for item in ITEMS}, text
    assert code == 0
    assert text.startswith("KEY OPENROUTER_API_KEY set\n")
    assert "SUMMARY 15 PASS, 0 FAIL, 1 INFO" in text
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
    assert lines["unknown_model"][1] == "google/gemini-0.0-not-a-model=404; client: model_not_found"
    assert lines["key_limit"][1].startswith("limit 100.0000 limit_remaining 87.5000 usage 12.5000")
    assert lines["default_temperature"][1].startswith("3/3 runs exact; runs identical yes")
    assert lines["parallel_latency"][1].startswith("6/6 ok;")
    assert fake.max_in_flight > 1  # the six reads overlapped
    assert fake.unanswered == 0


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
