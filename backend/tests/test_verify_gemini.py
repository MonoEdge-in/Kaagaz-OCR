"""``python -m kaagaz.cli verify-gemini`` against the fake Gemini API (never the real one).

``gemini_like`` answers the way the council's fact-check says Gemini does: shape A
structured output, every thinking level but minimal, more prompt tokens at
ultra_high, MAX_TOKENS at a tiny output cap, 404 for an unknown model and 403 with
no key. RapidOCR is the installed package (the ocr extra, as in CI).
"""

import base64
import io
import json
import math
from collections.abc import Iterator
from typing import Any

import pytest

from kaagaz import cli, verify_gemini
from kaagaz.settings import Settings
from kaagaz.verify_gemini import TRUTH, listed_sha256, made_up_page
from tests.gemini_fake import (
    KEY,
    GeminiFake,
    Recorded,
    Reply,
    embed_reply,
    error_reply,
    json_reply,
    running_fake,
    slow,
    text_reply,
    usage,
)
from tests.settings_factory import make_settings

KNOWN = {"gemini-3.1-pro-preview", "gemini-3.8-flash", "gemini-embedding-2"}
ITEMS = [
    "schema_shape",
    "thinking_levels",
    "media_resolution_tokens",
    "usage_keys",
    "max_tokens_finish",
    "default_temperature",
    "embedding_dims",
    "model_ids",
    "no_key_status",
    "rate_limit_headers",
    "rapidocr_detection_only",
    "rapidocr_recognition_only",
    "detection_model_sha",
    "parallel_latency",
]
_PROMPT = {"MEDIA_RESOLUTION_HIGH": 1120, "MEDIA_RESOLUTION_ULTRA_HIGH": 2240}


def gemini_like(request: Recorded) -> Reply:
    if "x-goog-api-key" not in request.headers:
        return error_reply(403, "PERMISSION_DENIED")
    model, _, action = request.path.removeprefix("/v1beta/models/").partition(":")
    if model not in KNOWN:
        return error_reply(404, "NOT_FOUND")
    if action == "batchEmbedContents":
        side = 1 / math.sqrt(request.body["requests"][0]["outputDimensionality"])
        return embed_reply([[side] * 1024 for _ in request.body["requests"]])
    config = request.body["generationConfig"]
    if "responseFormat" in config or config["thinkingConfig"]["thinkingLevel"] == "MINIMAL":
        return error_reply(400, "INVALID_ARGUMENT")
    parts = request.body["contents"][0]["parts"]
    level = parts[1]["media_resolution"]["level"] if len(parts) > 1 else None
    counts = usage(_PROMPT.get(level or "", 0) + 40, 30, 120)
    if config.get("maxOutputTokens") == verify_gemini.TINY_OUTPUT_TOKENS:
        return text_reply('{"lin', finish="MAX_TOKENS", counts=usage(1160, 4, 12))
    if "responseJsonSchema" in config:
        return slow(json_reply({"lines": list(TRUTH)}, counts=counts), 0.05)
    return text_reply("OK", counts=counts)


@pytest.fixture
def fake() -> Iterator[GeminiFake]:
    with running_fake() as server:
        server.responder = gemini_like
        yield server


def _settings(fake: GeminiFake, **overrides: Any) -> Settings:
    values: dict[str, Any] = {
        "gemini_api_key": KEY,
        "gemini_base_url": fake.url,
        "ocr_engine": "gemini",
        "gemini_rpm": 10**9,
        "ocr_timeout_seconds": 10.0,
    }
    return make_settings(**{**values, **overrides})


def _run(settings: Settings, *args: str) -> tuple[int, str, dict[str, tuple[str, str]]]:
    out = io.StringIO()
    code = cli.main(["verify-gemini", *args], settings=settings, out=out, db=None)
    text = out.getvalue()
    lines = {}
    for line in text.splitlines():
        if line.startswith("VERIFY "):
            _, item, verdict, evidence = [*line.split(" ", 3), ""][:4]
            lines[item] = (verdict, evidence)
    return code, text, lines


def test_every_item_passes_against_an_api_that_answers_as_documented(fake: GeminiFake) -> None:
    code, text, lines = _run(_settings(fake), "--model", "gemini-3.8-flash")
    assert list(lines) == ITEMS
    verdicts = {item: verdict for item, (verdict, _) in lines.items()}
    assert verdicts == {item: "MANUAL" if item == "rate_limit_headers" else "PASS" for item in ITEMS}, text
    assert code == 0
    assert text.startswith("KEY GEMINI_API_KEY set\n")
    assert "SUMMARY 13 PASS, 0 FAIL, 1 MANUAL" in text
    assert lines["schema_shape"][1].startswith("A=200 json B=400 INVALID_ARGUMENT; requests use A")
    assert "minimal=400 INVALID_ARGUMENT" in lines["thinking_levels"][1]
    assert "delta=1120" in lines["media_resolution_tokens"][1]
    assert "thoughts inside maxOutputTokens yes; client: output_truncated" in lines["max_tokens_finish"][1]
    assert lines["default_temperature"][1].startswith("3/3 runs exact; runs identical yes")
    assert "gemini-embedding-2: 2 x [1024] norms 1.0000, 1.0000" in lines["embedding_dims"][1]
    assert lines["model_ids"][1].startswith(
        "gemini-3.1-pro-preview=200 gemini-3.8-flash=200 gemini-0.0-not-a-model=404 NOT_FOUND"
    )
    assert lines["no_key_status"][1] == "status 403 PERMISSION_DENIED; client: key_rejected"
    assert lines["parallel_latency"][1].startswith("6/6 ok;")
    assert fake.max_in_flight > 1  # the six reads overlapped
    assert fake.unanswered == 0


def test_the_key_is_printed_as_set_only_and_sent_only_in_its_header(fake: GeminiFake) -> None:
    _, text, _ = _run(_settings(fake))
    assert KEY not in text
    keyed = [request for request in fake.requests if "x-goog-api-key" in request.headers]
    assert all(request.headers["x-goog-api-key"] == KEY for request in keyed)
    assert all(KEY not in request.path and KEY not in json.dumps(request.body) for request in fake.requests)
    assert len(fake.requests) - len(keyed) == 1  # the no-key probe only


def test_only_made_up_pictures_are_sent(fake: GeminiFake) -> None:
    _run(_settings(fake))
    page = made_up_page()
    sent = {
        part["inline_data"]["data"]
        for body in (request.body for request in fake.requests if request.path.endswith(":generateContent"))
        for part in body["contents"][0]["parts"]
        if "inline_data" in part
    }
    assert sent == {base64.b64encode(page.png).decode("ascii")}


def test_a_missing_key_fails_the_live_items_and_still_checks_the_rest(fake: GeminiFake) -> None:
    code, text, lines = _run(_settings(fake, gemini_api_key=None, ocr_engine="rapidocr"))
    assert code == 1
    assert text.startswith("KEY GEMINI_API_KEY missing\n")
    for item in ITEMS:
        verdict, evidence = lines[item]
        if item in {
            "no_key_status",
            "rapidocr_detection_only",
            "rapidocr_recognition_only",
            "detection_model_sha",
        }:
            assert verdict == "PASS", (item, evidence)
        elif item == "rate_limit_headers":
            assert verdict == "MANUAL"
        else:
            assert (verdict, evidence) == ("FAIL", "GEMINI_API_KEY missing")
    assert [request.headers.get("x-goog-api-key") for request in fake.requests] == [None]


def test_a_shape_the_api_refuses_fails_with_the_one_line_fix(fake: GeminiFake) -> None:
    def shape_b_only(request: Recorded) -> Reply:
        config = request.body.get("generationConfig", {}) if isinstance(request.body, dict) else {}
        if "responseJsonSchema" in config:
            return error_reply(400, "INVALID_ARGUMENT")
        if "responseFormat" in config:
            return json_reply({"lines": list(TRUTH)})
        return gemini_like(request)

    fake.responder = shape_b_only
    code, _, lines = _run(_settings(fake))
    assert code == 1
    assert lines["schema_shape"] == (
        "FAIL",
        "A=400 INVALID_ARGUMENT B=200 json; requests use A: set STRUCTURED_OUTPUT to B",
    )


def test_an_inexact_read_fails_default_temperature(fake: GeminiFake) -> None:
    reads = {"n": 0}

    def one_wrong(request: Recorded) -> Reply:
        body = request.body if isinstance(request.body, dict) else {}
        config = body.get("generationConfig", {})
        if (
            "responseJsonSchema" in config
            and "maxOutputTokens" in config
            and config["maxOutputTokens"] > 1000
        ):
            reads["n"] += 1
            if reads["n"] == 2:
                return json_reply({"lines": [TRUTH[0], "Total Rs 12,346.00", TRUTH[2]]})
        return gemini_like(request)

    fake.responder = one_wrong
    code, _, lines = _run(_settings(fake))
    assert code == 1
    assert lines["default_temperature"][0] == "FAIL"
    assert lines["default_temperature"][1].startswith("2/3 runs exact; runs identical no")


def test_a_reader_outage_fails_the_live_items_with_codes_only(fake: GeminiFake) -> None:
    fake.responder = lambda request: error_reply(400, "INVALID_ARGUMENT")
    code, text, lines = _run(_settings(fake))
    assert code == 1
    assert lines["default_temperature"][1].startswith("0/3 runs exact")
    assert "bad_request" in lines["default_temperature"][1]
    assert lines["parallel_latency"][1].startswith("0/6 ok (bad_request)")
    assert "SECRET-ERROR-MESSAGE" not in text  # error bodies are never printed


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
