"""``python -m kaagaz.cli verify-models``: the PRD 5 verify items, checked live through
OpenRouter (council 8 Oct 2026).

PRD 5 says every request field, response field and model ID is checked against the live
API or the installed package before use (section 14), and never guessed. This command
does that on the server where the key lives (council question 6): it uses no database,
file storage or queue; the key comes from the settings and is printed only as ``set`` or
``missing``; the key's own label is never printed, only its credit numbers; every picture
is made up here, in memory (lines of a made-up invoice drawn with Pillow's own font), so
no customer page is ever sent. Every request carries ``openrouter.PROVIDER``.

It prints one line per item, ``VERIFY <id> PASS|FAIL|INFO <evidence>``, and exits 1 when
any item FAILs. INFO means a person reads the evidence and decides. The items:

1. ``endpoints``: ``GET /models/{author}/{slug}/endpoints`` for each page model
   (OCR_MODEL, a google/gemini- MODEL_CHAT and each ``--model``): google-vertex is listed,
   and its supported parameters include response_format, reasoning and
   max_completion_tokens; its ZDR field is shown when the listing has one.
2. ``model_calls``: one 200 from each of those models, with the provider block.
3. ``strict_schema``: structured output with ``strict`` true and false; PASS when the value
   requests use (``openrouter.STRICT_SCHEMA``) gives schema-following JSON.
4. ``reasoning_levels``: each ``reasoning.effort``; PASS when the levels we send are
   accepted and ``reasoning_tokens`` is above 0 at high.
5. ``detail_tokens``: ``prompt_tokens`` with no detail, low and high; PASS when high costs
   more than low (the detail reaches Google), INFO when it does not (then
   ``MEDIA_RESOLUTION_SHAPE`` "passthrough" is the question for Vrushit).
6. ``usage_fields``: the counts the client bills (G9); whether ``completion_tokens``
   includes the reasoning tokens, and whether ``cost`` is present.
7. ``max_tokens_finish``: a tiny ``max_completion_tokens`` ends ``length`` with native
   ``MAX_TOKENS``, which the client turns into ``output_truncated`` (G6).
8. ``embedding_dims``: ``/embeddings`` at ``dimensions`` 1024 (M5) gives 1024-long finite
   vectors: their length and norm.
9. ``no_key_status``: a request with no key answers 401 (``key_rejected``).
10. ``unknown_model``: a made-up model ID answers 404 or 400.
11. ``key_limit``: ``GET /key``: the key's credit limit, what is left and what is used
    (numbers only); INFO.
12. ``default_temperature``: the page read 3 times at the default temperature through
    ``read_page``; PASS when every run is exact.
13. ``rapidocr_detection_only``: RapidOCR's text finder alone (G5) finds boxes and no
    text: ``Global.use_rec`` and ``Global.use_cls`` off, on its own instance.
14. ``rapidocr_recognition_only``: RapidOCR's reader alone (V1) reads one line's crop:
    ``Global.use_det`` and ``Global.use_cls`` off, on its own instance.
15. ``detection_model_sha``: each detection model file in RapidOCR's package has the
    SHA-256 the installed RapidOCR lists for it (it is in the image, never fetched).
16. ``parallel_latency``: 6 pages read at once through one client at MODEL_RPM (G8), and
    the seconds each took, against one read alone.

Cost: about 25 model calls with a small picture, a few cents on the Pro model.
"""

import dataclasses
import hashlib
import io
import math
import re
import statistics
import time
from collections.abc import Callable, Iterable, Mapping, Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from typing import Any, TextIO

from PIL import Image, ImageDraw, ImageFont

from kaagaz.ai import openrouter
from kaagaz.ai.openrouter import (
    CHAT_PATH,
    CHAT_THINKING_LEVEL,
    EMBEDDINGS_PATH,
    KEY_PATH,
    OpenRouterClient,
    Probe,
    chat_request,
    finish_reasons,
    page_request,
    reasoning_tokens,
    reply_cost,
    reply_text,
)
from kaagaz.ai.replies import ModelOutputError, ModelUnavailable, parse_reply
from kaagaz.settings import EMBEDDING_DIM, Settings, is_gemini_model, valid_model_id

PASS, FAIL, INFO = "PASS", "FAIL", "INFO"
# The embedding model M5 names; used when MODEL_EMBED is not a Gemini model yet (question 4).
GEMINI_EMBED_MODEL = "google/gemini-embedding-2"
BOGUS_MODEL = "google/gemini-0.0-not-a-model"
TEMPERATURE_RUNS = 3
PARALLEL_CALLS = 6
TINY_OUTPUT_TOKENS = 16
EFFORTS = ("minimal", "low", "medium", "high")
# What the page reader sends, so google-vertex must list each as supported.
NEEDED_PARAMETERS = frozenset({"response_format", "reasoning", "max_completion_tokens"})
VERTEX = "google-vertex"
# A made-up invoice: nothing here belongs to anyone.
TRUTH = ("INVOICE 4471", "Total Rs 12,345.00", "Date 08-10-2026")
INSTRUCTION = (
    "Copy each line of text in this image exactly as printed, top to bottom, one string per line."
    " Do not correct, translate or add anything."
)
SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {"lines": {"type": "array", "items": {"type": "string"}}},
    "required": ["lines"],
    "additionalProperties": False,
}
EMBED_TEXTS = ["Made-up invoice 4471, total 12,345.00", "मराठी नमुना मजकूर"]
_SPACES = re.compile(r"\s+")
# RapidOCR's own model list: a line naming a file, then the line with its SHA256.
_LISTED_FILE = re.compile(r"model_dir:\s*\S*/onnx/\S*/([^/\s]+\.onnx)\s*$")
_LISTED_SHA = re.compile(r"SHA256:\s*([0-9a-f]{64})\s*$")


@dataclass(frozen=True, slots=True)
class Finding:
    item: str
    verdict: str
    evidence: str


@dataclass(frozen=True, slots=True)
class MadeUpPage:
    """The made-up page: its PNG, the picture, and each line's box in pixels."""

    png: bytes
    image: Image.Image
    boxes: tuple[tuple[int, int, int, int], ...]


def made_up_page() -> MadeUpPage:
    """Black lines of TRUTH on white, in Pillow's own font (no installed font needed)."""
    image = Image.new("RGB", (900, 360), "white")
    draw = ImageDraw.Draw(image)
    font = ImageFont.load_default(size=40)
    boxes = []
    for index, line in enumerate(TRUTH):
        origin = (40, 40 + index * 100)
        draw.text(origin, line, fill="black", font=font)
        left, top, right, bottom = draw.textbbox(origin, line, font=font)
        boxes.append((int(left), int(top), int(right), int(bottom)))
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return MadeUpPage(buffer.getvalue(), image, tuple(boxes))


def _same(read: str, truth: str) -> bool:
    return _SPACES.sub(" ", read).strip() == truth


def _said(probe: Probe) -> str:
    """The status and, for an error, its error_type code (never its message)."""
    return f"{probe.status} {probe.error_type}" if probe.error_type else str(probe.status)


def _outcome(probe: Probe) -> tuple[bool, str]:
    """(the reply is schema-following JSON, a short description of what came back)."""
    if probe.status == 0:
        return False, "no reply"
    if probe.reply is None:
        return False, _said(probe)
    try:
        text, _ = reply_text(probe.reply)
    except (ModelOutputError, ModelUnavailable) as error:
        return False, f"{probe.status} {error.code}"
    value, problem = parse_reply(text, SCHEMA)
    return (value is not None, f"{probe.status} json" if value is not None else f"{probe.status} {problem}")


def _usage(probe: Probe | None) -> dict[str, Any]:
    usage = probe.reply.get("usage") if probe is not None and probe.reply is not None else None
    return usage if isinstance(usage, dict) else {}


def _number(value: object) -> str:
    """A credit number for printing, or ``none``: never any other text."""
    if isinstance(value, bool) or not isinstance(value, int | float) or not math.isfinite(value):
        return "none"
    return f"{float(value):.4f}"


class Verifier:
    """Runs the items in order; each returns one ``Finding``."""

    def __init__(
        self,
        settings: Settings,
        *,
        models: Sequence[str],
        embed_model: str | None,
        timer: Callable[[], float],
    ) -> None:
        self.settings = settings
        self.key_set = settings.openrouter_api_key is not None
        self.page = made_up_page()
        self.timer = timer
        self.timeout = settings.ocr_timeout_seconds
        gemini_chat = [settings.model_chat] if is_gemini_model(settings.model_chat) else []
        wanted = list(dict.fromkeys([settings.ocr_model, *gemini_chat, *models]))
        self.models = [model for model in wanted if valid_model_id(model)]
        self.bad_models = [model for model in wanted if not valid_model_id(model)]
        if embed_model is None:
            embed_model = (
                settings.model_embed if is_gemini_model(settings.model_embed) else GEMINI_EMBED_MODEL
            )
        self.embed_model = embed_model
        # The no-key probe never sends a key: this client holds a placeholder.
        self.keyless = OpenRouterClient(dataclasses.replace(settings, openrouter_api_key="no-key-sent"))
        self.client: OpenRouterClient | None = None
        self.embedder: OpenRouterClient | None = None
        if self.key_set:
            self.client = OpenRouterClient(settings)
            if valid_model_id(embed_model):
                self.embedder = OpenRouterClient(dataclasses.replace(settings, model_embed=embed_model))
        self.first: Probe | None = None  # the STRICT_SCHEMA reply
        self.high: Probe | None = None  # the high effort reply, for usage_fields
        self.one_read_seconds: float | None = None

    def items(self) -> list[tuple[str, Callable[[], Finding]]]:
        return [
            ("endpoints", self.endpoints),
            ("model_calls", self.model_calls),
            ("strict_schema", self.strict_schema),
            ("reasoning_levels", self.reasoning_levels),
            ("detail_tokens", self.detail_tokens),
            ("usage_fields", self.usage_fields),
            ("max_tokens_finish", self.max_tokens_finish),
            ("embedding_dims", self.embedding_dims),
            ("no_key_status", self.no_key_status),
            ("unknown_model", self.unknown_model),
            ("key_limit", self.key_limit),
            ("default_temperature", self.default_temperature),
            ("rapidocr_detection_only", self.rapidocr_detection_only),
            ("rapidocr_recognition_only", self.rapidocr_recognition_only),
            ("detection_model_sha", self.detection_model_sha),
            ("parallel_latency", self.parallel_latency),
        ]

    # OpenRouter.

    def _live(self) -> OpenRouterClient:
        """The client with the key; items call it only after ``_no_key`` found one."""
        if self.client is None:
            raise RuntimeError("OPENROUTER_API_KEY missing")
        return self.client

    def _probe(self, path: str, body: Mapping[str, Any] | None) -> Probe:
        return self._live().probe(path, body, timeout=self.timeout)

    def _page(self, **options: Any) -> Probe:
        values: dict[str, Any] = {
            "model": self.settings.ocr_model,
            "png": self.page.png,
            "instruction": INSTRUCTION,
            "schema": SCHEMA,
            "media_resolution": self.settings.ocr_media_resolution,
            "thinking": self.settings.ocr_thinking_level,
            "max_output_tokens": None,
        }
        return self._probe(CHAT_PATH, page_request(**{**values, **options}))

    def _no_key(self, item: str) -> Finding | None:
        return None if self.key_set else Finding(item, FAIL, "OPENROUTER_API_KEY missing")

    def _bad_models(self) -> list[str]:
        return [f"{model}=not an OpenRouter model ID" for model in self.bad_models]

    def endpoints(self) -> Finding:
        if (missing := self._no_key("endpoints")) is not None:
            return missing
        said, ok = self._bad_models(), not self.bad_models
        for model in self.models:
            good, evidence = self._listing(self._probe(f"/models/{model}/endpoints", None))
            said.append(f"{model}: {evidence}")
            ok = ok and good
        return Finding("endpoints", PASS if ok else FAIL, "; ".join(said))

    def _listing(self, probe: Probe) -> tuple[bool, str]:
        data = probe.reply.get("data") if probe.reply is not None else None
        endpoints = data.get("endpoints") if isinstance(data, dict) else None
        if not isinstance(endpoints, list):
            return False, f"status {_said(probe)}, no endpoints list"
        vertex = [
            endpoint
            for endpoint in endpoints
            if isinstance(endpoint, dict)
            and isinstance(endpoint.get("tag"), str)
            and (endpoint["tag"] == VERTEX or endpoint["tag"].startswith(VERTEX + "/"))
        ]
        if not vertex:
            return False, f"{len(endpoints)} endpoints, {VERTEX} not listed"
        parameters = {
            name
            for endpoint in vertex
            if isinstance(endpoint.get("supported_parameters"), list)
            for name in endpoint["supported_parameters"]
            if isinstance(name, str)
        }
        lacking = sorted(NEEDED_PARAMETERS - parameters)
        zdr = sorted(
            {f"{key}={value}" for endpoint in vertex for key, value in endpoint.items() if "zdr" in key}
        )
        evidence = (
            f"{VERTEX} listed; parameters {'all supported' if not lacking else 'lack ' + ','.join(lacking)};"
            f" {' '.join(zdr) if zdr else 'no zdr field'}"
        )
        return not lacking, evidence

    def model_calls(self) -> Finding:
        if (missing := self._no_key("model_calls")) is not None:
            return missing
        said, ok = self._bad_models(), not self.bad_models
        for model in self.models:
            probe = self._probe(CHAT_PATH, _text_only(model))
            said.append(f"{model}={_said(probe)}")
            ok = ok and probe.status == 200
        return Finding("model_calls", PASS if ok else FAIL, " ".join(said) + "; with the provider block")

    def strict_schema(self) -> Finding:
        if (missing := self._no_key("strict_schema")) is not None:
            return missing
        results = {}
        for strict in (True, False):
            probe = self._page(strict=strict)
            if strict == openrouter.STRICT_SCHEMA:
                self.first = probe
            results[strict] = _outcome(probe)
        used = openrouter.STRICT_SCHEMA
        evidence = " ".join(f"strict={str(strict).lower()}:{said}" for strict, (_, said) in results.items())
        if results[used][0]:
            return Finding("strict_schema", PASS, f"{evidence}; requests use strict={str(used).lower()}")
        working = [strict for strict, (ok, _) in results.items() if ok]
        advice = f"set STRICT_SCHEMA to {working[0]}" if working else "neither worked"
        return Finding(
            "strict_schema", FAIL, f"{evidence}; requests use strict={str(used).lower()}: {advice}"
        )

    def reasoning_levels(self) -> Finding:
        if (missing := self._no_key("reasoning_levels")) is not None:
            return missing
        accepted, said = set(), []
        high_reasoning = 0
        for level in EFFORTS:
            probe = self._page(thinking=level)
            ok, description = _outcome(probe)
            thought = reasoning_tokens(probe.reply) if probe.reply is not None else 0
            said.append(f"{level}={description}" + (f" reasoning={thought}" if ok else ""))
            if ok:
                accepted.add(level)
            if level == "high":
                self.high, high_reasoning = probe, thought
        needed = {
            self.settings.ocr_thinking_level,
            self.settings.ocr_retry_thinking_level,
            CHAT_THINKING_LEVEL,
        }
        verdict = PASS if needed <= accepted and high_reasoning > 0 else FAIL
        evidence = f"{' '.join(said)}; we send {','.join(sorted(needed))}; reasoning at high {high_reasoning}"
        return Finding("reasoning_levels", verdict, evidence)

    def detail_tokens(self) -> Finding:
        if (missing := self._no_key("detail_tokens")) is not None:
            return missing
        prompts: dict[str, int | None] = {}
        said = []
        for detail in (None, "low", "high"):
            probe = self._page(media_resolution=detail)
            ok, description = _outcome(probe)
            count = _usage(probe).get("prompt_tokens")
            name = detail or "none"
            prompts[name] = count if ok and isinstance(count, int) else None
            said.append(f"{name}={description} prompt={count}")
        low, high = prompts["low"], prompts["high"]
        if None in prompts.values() or low is None or high is None:
            return Finding("detail_tokens", FAIL, " ".join(said))
        changes = "yes" if len(set(prompts.values())) > 1 else "no"
        evidence = f"{' '.join(said)}; detail changes prompt tokens {changes}; high-low={high - low}"
        if high > low:
            return Finding("detail_tokens", PASS, evidence)
        return Finding("detail_tokens", INFO, evidence + "; detail is not passed on (MEDIA_RESOLUTION_SHAPE)")

    def usage_fields(self) -> Finding:
        if (missing := self._no_key("usage_fields")) is not None:
            return missing
        probe = self.high or self.first
        usage = _usage(probe)
        prompt, completion = usage.get("prompt_tokens"), usage.get("completion_tokens")
        if (
            probe is None
            or probe.reply is None
            or not (isinstance(prompt, int) and isinstance(completion, int))
        ):
            return Finding("usage_fields", FAIL, f"keys {','.join(sorted(usage)) or 'none'}")
        thought = reasoning_tokens(probe.reply)
        if thought == 0:
            includes = "unknown (no reasoning tokens reported)"
        else:
            includes = "yes" if completion >= thought else "no"
        cost = reply_cost(probe.reply)
        evidence = (
            f"keys {','.join(sorted(usage))}; prompt {prompt} completion {completion} reasoning {thought};"
            f" completion_tokens includes reasoning {includes};"
            f" cost {'present ' + _number(cost) if cost is not None else 'absent'}"
        )
        return Finding("usage_fields", PASS, evidence)

    def max_tokens_finish(self) -> Finding:
        if (missing := self._no_key("max_tokens_finish")) is not None:
            return missing
        probe = self._page(max_output_tokens=TINY_OUTPUT_TOKENS)
        finish, native = finish_reasons(probe.reply) if probe.reply is not None else (None, None)
        usage = _usage(probe)
        thought = reasoning_tokens(probe.reply) if probe.reply is not None else 0
        evidence = (
            f"status {_said(probe)} finish {finish} native {native}"
            f" completion {usage.get('completion_tokens')} reasoning {thought}"
        )
        try:
            if probe.reply is not None:
                reply_text(probe.reply)
        except ModelOutputError as error:
            if error.code == "output_truncated" and native == "MAX_TOKENS":
                return Finding("max_tokens_finish", PASS, f"{evidence}; client: output_truncated")
        except ModelUnavailable as error:
            evidence += f"; client: {error.code}"
        return Finding("max_tokens_finish", FAIL, evidence)

    def _read(self) -> tuple[list[str] | None, str, float]:
        """(the lines read, or None, what happened, seconds) for one read_page."""
        client = self._live()
        started = self.timer()
        try:
            page = client.read_page(
                png=self.page.png,
                instruction=INSTRUCTION,
                schema=SCHEMA,
                media_resolution=self.settings.ocr_media_resolution,
                thinking_level=self.settings.ocr_thinking_level,
                deadline=client.now() + self.timeout,
            )
        except (ModelUnavailable, ModelOutputError) as error:
            return None, error.code, self.timer() - started
        lines = [str(line) for line in page.value["lines"]]
        return lines, f"in={page.input_tokens} out={page.output_tokens}", self.timer() - started

    def default_temperature(self) -> Finding:
        if (missing := self._no_key("default_temperature")) is not None:
            return missing
        runs = [self._read() for _ in range(TEMPERATURE_RUNS)]
        self.one_read_seconds = runs[0][2]
        exact = sum(
            1
            for lines, _, _ in runs
            if lines is not None and len(lines) == len(TRUTH) and all(map(_same, lines, TRUTH))
        )
        identical = "yes" if len({tuple(lines or ()) for lines, _, _ in runs}) == 1 else "no"
        said = ", ".join(description for _, description, _ in runs)
        evidence = f"{exact}/{TEMPERATURE_RUNS} runs exact; runs identical {identical}; tokens {said}"
        return Finding("default_temperature", PASS if exact == TEMPERATURE_RUNS else FAIL, evidence)

    def embedding_dims(self) -> Finding:
        if (missing := self._no_key("embedding_dims")) is not None:
            return missing
        if self.embedder is None:
            return Finding("embedding_dims", FAIL, f"{self.embed_model}: not an OpenRouter model ID")
        try:
            vectors = self.embedder.embed(EMBED_TEXTS)
        except (ModelUnavailable, ModelOutputError) as error:
            return Finding("embedding_dims", FAIL, f"{self.embed_model}: {error.code}")
        norms = ", ".join(f"{math.sqrt(sum(v * v for v in vector)):.4f}" for vector in vectors)
        dims = sorted({len(vector) for vector in vectors})
        verdict = PASS if dims == [EMBEDDING_DIM] else FAIL
        evidence = f"{self.embed_model}: {len(vectors)} x {dims} finite, norms {norms}; {EMBEDDINGS_PATH}"
        return Finding("embedding_dims", verdict, evidence)

    def no_key_status(self) -> Finding:
        probe = self.keyless.probe(
            CHAT_PATH, _text_only(self.settings.ocr_model), send_key=False, timeout=self.timeout
        )
        evidence = f"status {_said(probe)}"
        if probe.status == 401:
            return Finding("no_key_status", PASS, f"{evidence}; client: key_rejected")
        return Finding("no_key_status", FAIL, evidence)

    def unknown_model(self) -> Finding:
        if (missing := self._no_key("unknown_model")) is not None:
            return missing
        probe = self._probe(CHAT_PATH, _text_only(BOGUS_MODEL))
        evidence = f"{BOGUS_MODEL}={_said(probe)}"
        if probe.status == 404:
            return Finding("unknown_model", PASS, f"{evidence}; client: model_not_found")
        if probe.status == 400:
            return Finding("unknown_model", PASS, f"{evidence}; client: bad_request")
        return Finding("unknown_model", FAIL, evidence)

    def key_limit(self) -> Finding:
        if (missing := self._no_key("key_limit")) is not None:
            return missing
        probe = self._probe(KEY_PATH, None)
        data = probe.reply.get("data") if probe.reply is not None else None
        if not isinstance(data, dict):
            return Finding("key_limit", FAIL, f"status {_said(probe)}")
        if _number(data.get("limit")) == "none":
            return Finding(
                "key_limit",
                FAIL,
                "credit limit is missing or not numeric; set a per-key credit limit on openrouter.ai",
            )
        # Numbers only: the key's label is made of the key's own characters.
        evidence = (
            f"limit {_number(data.get('limit'))} limit_remaining {_number(data.get('limit_remaining'))}"
            f" usage {_number(data.get('usage'))}; set a per-key credit limit on openrouter.ai"
        )
        return Finding("key_limit", INFO, evidence)

    def parallel_latency(self) -> Finding:
        if (missing := self._no_key("parallel_latency")) is not None:
            return missing
        client = self._live()
        started = self.timer()
        with ThreadPoolExecutor(max_workers=PARALLEL_CALLS) as pool:
            runs = list(pool.map(lambda _: self._read(), range(PARALLEL_CALLS)))
        wall = self.timer() - started
        good = sum(1 for lines, _, _ in runs if lines is not None)
        seconds = [taken for _, _, taken in runs]
        alone = f"{self.one_read_seconds:.1f}" if self.one_read_seconds is not None else "not measured"
        failures = ",".join(sorted({said for lines, said, _ in runs if lines is None}))
        evidence = (
            f"{good}/{PARALLEL_CALLS} ok{' (' + failures + ')' if failures else ''}; wall {wall:.1f} s;"
            f" each median {statistics.median(seconds):.1f} s max {max(seconds):.1f} s;"
            f" one alone {alone} s; MODEL_RPM {self.settings.model_rpm},"
            f" limiter {client.interval:.2f} s apart"
        )
        return Finding("parallel_latency", PASS if good == PARALLEL_CALLS else FAIL, evidence)

    # RapidOCR, the installed package.

    def rapidocr_detection_only(self) -> Finding:
        engine = _rapidocr({"Global.use_cls": False, "Global.use_rec": False})
        if engine is None:
            return Finding("rapidocr_detection_only", FAIL, "rapidocr is not installed (the ocr extra)")
        result = engine(self.page.image)
        boxes = getattr(result, "boxes", None)
        found = 0 if boxes is None else len(boxes)
        has_text = getattr(result, "txts", None) is not None
        evidence = (
            f"{type(result).__name__}: {found} boxes for {len(TRUTH)} lines,"
            f" text {'yes' if has_text else 'none'};"
            " params Global.use_rec=False Global.use_cls=False"
        )
        verdict = PASS if found >= len(TRUTH) and not has_text else FAIL
        return Finding("rapidocr_detection_only", verdict, evidence)

    def rapidocr_recognition_only(self) -> Finding:
        engine = _rapidocr({"Global.use_det": False, "Global.use_cls": False})
        if engine is None:
            return Finding("rapidocr_recognition_only", FAIL, "rapidocr is not installed (the ocr extra)")
        left, top, right, bottom = self.page.boxes[1]
        crop = self.page.image.crop((left - 8, top - 8, right + 8, bottom + 8))
        result = engine(crop)
        texts: Sequence[object] = getattr(result, "txts", None) or ()
        scores: Sequence[float] = getattr(result, "scores", None) or ()
        exact = len(texts) == 1 and _same(str(texts[0]), TRUTH[1])
        score = f"{float(scores[0]):.3f}" if scores else "none"
        evidence = (
            f"{type(result).__name__}: {'exact' if exact else 'differs'} score {score};"
            " params Global.use_det=False Global.use_cls=False"
        )
        return Finding("rapidocr_recognition_only", PASS if exact else FAIL, evidence)

    def detection_model_sha(self) -> Finding:
        folder = _rapidocr_folder()
        if folder is None:
            return Finding("detection_model_sha", FAIL, "rapidocr is not installed (the ocr extra)")
        listed = listed_sha256((folder / "default_models.yaml").read_text(encoding="utf-8").splitlines())
        files = sorted((folder / "models").glob("*det*.onnx"))
        if not files:
            return Finding("detection_model_sha", FAIL, "no detection model in rapidocr/models")
        said, ok = [], True
        for path in files:
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
            match = listed.get(path.name) == digest
            ok = ok and match
            said.append(f"{path.name} {digest[:16]} {'matches' if match else 'does not match'} the list")
        return Finding("detection_model_sha", PASS if ok else FAIL, "; ".join(said))


def _text_only(model: str) -> dict[str, Any]:
    """A one-line chat request to ``model``, with the provider block."""
    return chat_request(
        model=model,
        messages=[{"role": "user", "content": "Reply with the word OK."}],
        thinking=CHAT_THINKING_LEVEL,
        schema=None,
        max_output_tokens=None,
    )


def listed_sha256(lines: Iterable[str]) -> dict[str, str]:
    """RapidOCR's model list (default_models.yaml) as {onnx file name: SHA-256}: each
    ``model_dir`` line names a file, and the ``SHA256`` line after it is its hash."""
    listed: dict[str, str] = {}
    name: str | None = None
    for line in lines:
        if (file_match := _LISTED_FILE.search(line)) is not None:
            name = file_match.group(1)
        elif name is not None and (sha_match := _LISTED_SHA.search(line)) is not None:
            listed.setdefault(name, sha_match.group(1))
            name = None
    return listed


def _rapidocr(params: dict[str, Any]) -> Any:
    """A RapidOCR instance with ``params``, or None without the ocr extra."""
    try:
        from rapidocr import RapidOCR  # lazy: an optional extra
    except ImportError:
        return None
    return RapidOCR(params={"Global.log_level": "error", **params})


def _rapidocr_folder() -> Path | None:
    try:
        import rapidocr  # lazy: an optional extra
    except ImportError:
        return None
    return Path(str(rapidocr.__file__)).parent


def run(
    settings: Settings,
    out: TextIO,
    *,
    models: Sequence[str] = (),
    embed_model: str | None = None,
    timer: Callable[[], float] = time.perf_counter,
) -> int:
    """Check every item, print one line each and a summary; 1 when any FAILs, else 0."""
    verifier = Verifier(settings, models=models, embed_model=embed_model, timer=timer)
    out.write(f"KEY OPENROUTER_API_KEY {'set' if verifier.key_set else 'missing'}\n")
    out.write(f"MODELS ocr {settings.ocr_model} chat {settings.model_chat} embed {verifier.embed_model}\n")
    findings = []
    for _, check in verifier.items():
        finding = check()
        findings.append(finding)
        out.write(f"VERIFY {finding.item} {finding.verdict} {finding.evidence}\n")
    counts = {verdict: sum(1 for f in findings if f.verdict == verdict) for verdict in (PASS, FAIL, INFO)}
    out.write(f"SUMMARY {counts[PASS]} PASS, {counts[FAIL]} FAIL, {counts[INFO]} INFO\n")
    return 1 if counts[FAIL] else 0
