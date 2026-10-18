"""``python -m kaagaz.cli verify-models``: the PRD 5 verify items, checked live through
OpenRouter (council 8 Oct 2026).

PRD 5 says every request field, response field and model ID is checked against the live
API or the installed package before use (section 14), and never guessed. This command
does that on the server where the key lives (council question 6): it uses no database
(but ``--record``'s one row, written by ``cli``), file storage or queue; the key comes from
the settings and is printed only as ``set`` or ``missing``; the key's own label is never
printed, only its credit numbers; every picture is made up here, in memory (lines of a
made-up invoice drawn with Pillow's own font), so no customer page is ever sent. Every
request carries ``openrouter.PROVIDER``, or for a ``--model`` item the same block with
the provider its endpoint rule found.

It prints one line per item, ``VERIFY <id> PASS|FAIL|INFO <evidence>``, and exits 1 when
any item FAILs. INFO means a person reads the evidence and decides. The items:

1. ``endpoints``: ``GET /models/{author}/{slug}/endpoints`` for each page model
   (OCR_MODEL, MODEL_CHAT when it is a model ID, and each ``--model``): google-vertex is listed,
   and its supported parameters include response_format, reasoning and
   max_completion_tokens (or max_tokens, which Vertex lists; OpenRouter takes either);
   its ZDR field is shown when the listing has one.
2. ``model_calls``: one 200 from each of those models, with the provider block.
3. ``strict_schema``: structured output with ``strict`` true and false; PASS when the value
   requests use (``openrouter.STRICT_SCHEMA``) gives schema-following JSON.
4. ``reasoning_levels``: each ``reasoning.effort``; PASS when the levels we send are
   accepted and ``reasoning_tokens`` is above 0 at high; INFO when they are accepted but
   no thinking is reported (Google Vertex reports 0, live 11 Oct 2026: whether the effort
   reaches Google is a question for a person); FAIL when one is refused.
5. ``detail_tokens``: ``prompt_tokens`` with no detail, low and high; PASS when high costs
   more than low (the detail reaches Google), INFO when it does not (then
   ``MEDIA_RESOLUTION_SHAPE`` "passthrough" is the question for Vrushit).
6. ``usage_fields``: the counts the client bills (G9); whether ``completion_tokens``
   includes the reasoning tokens, and whether ``cost`` is present.
7. ``max_tokens_finish``: a tiny ``max_completion_tokens`` ends ``length`` (whatever the
   provider's native reason: Google's is ``MAX_TOKENS``), which the client turns into
   ``output_truncated`` (G6).
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

Then, for each ``--model`` (model choice, council 11 Oct 2026, MC1, MC2), the items a
model must pass before the operator can enable it, each through the provider the endpoint
rule found, with the reader's own ``INSTRUCTION`` and ``PAGE_SCHEMA`` where a page is read:

* ``model_endpoint``: the catalogue's endpoint rule (``jobs/catalog``): a zero-data-
  retention endpoint with status 0, of a provider in OPENROUTER_PROVIDERS, listing
  structured_outputs and the parameters of item 1.
* ``model_reply``: a 200, and the provider that served it: the reply's own ``provider``
  (what the ledger keeps), else the ``provider_name`` OpenRouter's ``/generation`` gives
  (a zero-data-retention call may not be kept there to look up).
* ``model_page_schema``: the made-up page read with the real ``PAGE_SCHEMA``, and the reply
  follows it (``schema_problem``).
* ``model_made_up_page``: that read has every line of the page exactly.
* ``model_boxes``: every line it gave is pinned to the page's own boxes by G5's rule
  (the reader's ``pinned``); each box's best IoU is shown.
* ``model_devanagari``: INFO: no Devanagari page is drawn, since no Devanagari font is
  bundled (Pillow's own is Latin only, and a font would be a new dependency).
* ``model_efforts``: OCR_THINKING_LEVEL and OCR_RETRY_THINKING_LEVEL are accepted, each
  giving JSON; its ``reasoning_tokens`` are shown, not required (``reasoning_levels``).
* ``model_cost``: the page read's ``usage.cost`` is above 0.
* ``model_truncation``: a tiny output cap ends ``length``, which the client calls
  ``output_truncated``; ``model_token_cap``: it gave at most that many tokens.
* ``model_refusal``: local, no call: a refusal in each shape (``message.refusal``,
  ``content_filter``, a native ``refusal``) is ``blocked`` for the client.

A ``--model`` with no thinking levels (Tushar, 18 Oct 2026: ``GET /models`` lists no
``reasoning.supported_efforts`` for it, ``catalog.offered_efforts``; read once, only when
there is a ``--model``) is checked the way it is called: every request to it has no
``reasoning`` field, its endpoint need not list ``reasoning``, and ``model_efforts`` and
``chat_levels`` PASS as "n/a" after one read with no reasoning gives JSON. A model whose
efforts can't be read is checked as one with levels (the strict way).

``--record`` (exactly one ``--model``): when every one of these but the INFO passes and
no item of the whole run FAILs, the command writes the pass for ``READER_PROMPT_VERSION``
and the page read's tokens in and out (``record``, as the owner, with a ``platform_audit``
row ``model.verified``: ``cli``). Otherwise nothing is written.

``--chat`` (features 25-27, council 15 Oct 2026, C11) checks each ``--model`` for drafting
answers instead: ``model_endpoint``, ``model_reply``, then ``chat_schema`` (a made-up
question with two made-up pieces of evidence, the answer's own SYSTEM_PROMPT and
ANSWER_SCHEMA: the reply follows it and the answer can read it), ``chat_levels`` (low,
medium and high each give such a reply), ``chat_cites`` (it answers from piece 1 only,
with its total) and ``model_refusal``. ``--chat --record`` writes the pass for
ANSWER_PROMPT_HASH (``chat_verified_*``, platform_audit ``model.chat_verified``) the same
way. Deploy runbook: after a migration or a changed answer prompt, run it for each answer
model, or answers stay on MODEL_CHAT.

Cost: about 25 model calls with a small picture, a few cents on the Pro model, and about 8
more per ``--model``.
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
    CHAT_THINKING_TOKENS,
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
from kaagaz.answering import draft
from kaagaz.jobs import catalog
from kaagaz.reading.ocr import READER_PROMPT_VERSION
from kaagaz.reading.wiring import gemini_reader_module
from kaagaz.settings import DEFAULT_PROVIDER, EMBEDDING_DIM, NO_THINKING, Settings, valid_model_id

PASS, FAIL, INFO = "PASS", "FAIL", "INFO"
# A model ID no provider serves (made up, with no model's name in it).
BOGUS_MODEL = "kaagaz/not-a-model-0"
TEMPERATURE_RUNS = 3
PARALLEL_CALLS = 6
TINY_OUTPUT_TOKENS = 16
EFFORTS = ("minimal", "low", "medium", "high")
# What the page reader sends, so the endpoint must list each as supported; and one of the
# output caps (Vertex lists max_tokens; OpenRouter takes either name).
NEEDED_PARAMETERS = frozenset({"response_format", "reasoning"})
MAX_TOKEN_PARAMETERS = frozenset({"max_completion_tokens", "max_tokens"})
GENERATION_PATH = "/generation"
# /generation may lag the reply by a moment: asked this many times, a second apart.
GENERATION_TRIES = 3
# Only these shapes of a reply id and a provider name are used or printed.
_REPLY_ID = re.compile(r"[A-Za-z0-9_-]{1,128}")
_PROVIDER_NAME = re.compile(r"[A-Za-z0-9 ._-]{1,64}")
# A refusal in each shape a provider sends it (model_refusal): the client must say blocked.
REFUSALS: dict[str, dict[str, Any]] = {
    "message.refusal": {"message": {"content": None, "refusal": "No."}, "finish_reason": "stop"},
    "content_filter": {"message": {"content": None}, "finish_reason": "content_filter"},
    "native refusal": {
        "message": {"content": "{}"},
        "finish_reason": "stop",
        "native_finish_reason": "refusal",
    },
}
# The --model items --record needs to PASS (model_devanagari is INFO).
REQUIRED = (
    "model_endpoint",
    "model_reply",
    "model_page_schema",
    "model_made_up_page",
    "model_boxes",
    "model_efforts",
    "model_cost",
    "model_truncation",
    "model_token_cap",
    "model_refusal",
)
# The --model --chat items --record needs to PASS (features 25-27, C11).
CHAT_REQUIRED = ("model_endpoint", "model_reply", "chat_schema", "chat_levels", "chat_cites", "model_refusal")
CHAT_LEVELS = ("low", "medium", "high")
# A made-up question and two made-up pieces of evidence (chat_cites): only piece 1 answers it.
CHAT_QUESTION = "What is the total of invoice 4471?"
CHAT_EVIDENCE = ("Invoice 4471\nTotal Rs 12,345.00", "Invoice 5520\nTotal Rs 8,100.00")
CHAT_TOTAL = "12,345.00"
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
    value, said = _parsed(probe, SCHEMA)
    return value is not None, said


def _parsed(probe: Probe, schema: Mapping[str, Any]) -> tuple[dict[str, Any] | None, str]:
    """(the reply's object when it follows ``schema``, a short description of what came back)."""
    if probe.status == 0:
        return None, "no reply"
    if probe.reply is None:
        return None, _said(probe)
    try:
        text, _ = reply_text(probe.reply)
    except (ModelOutputError, ModelUnavailable) as error:
        return None, f"{probe.status} {error.code}"
    value, problem = parse_reply(text, schema)
    return value, f"{probe.status} json" if value is not None else f"{probe.status} {problem}"


def _lacking(parameters: Iterable[str], *, thinks: bool = True) -> list[str]:
    """The page reader's parameters an endpoint does not list: NEEDED_PARAMETERS (reasoning
    only for a model that ``thinks``), and one of MAX_TOKEN_PARAMETERS (named
    max_completion_tokens when neither is there)."""
    listed = set(parameters)
    lacking = sorted(NEEDED_PARAMETERS - listed - (set() if thinks else {"reasoning"}))
    return lacking if MAX_TOKEN_PARAMETERS & listed else [*lacking, "max_completion_tokens"]


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
        sleep: Callable[[float], None] = time.sleep,
        chat: bool = False,
    ) -> None:
        self.settings = settings
        self.key_set = settings.openrouter_api_key is not None
        self.page = made_up_page()
        self.timer = timer
        self.sleep = sleep
        self.timeout = settings.ocr_timeout_seconds
        chat_model = [settings.model_chat] if valid_model_id(settings.model_chat) else []
        wanted = list(dict.fromkeys([settings.ocr_model, *chat_model, *models]))
        self.models = [model for model in wanted if valid_model_id(model)]
        self.bad_models = [model for model in wanted if not valid_model_id(model)]
        self.checks = [ModelCheck(self, model, chat=chat) for model in dict.fromkeys(models)]
        if embed_model is None:
            embed_model = settings.model_embed
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
        self._efforts: dict[str, tuple[str, ...] | None] | None = None  # GET /models, read once

    def thinks(self, model: str) -> bool:
        """False only for a ``--model`` that ``GET /models`` lists with no thinking levels
        (read once, on the first ask); True for any other, or when the list can't be read."""
        if model not in {check.model for check in self.checks} or not self.key_set:
            return True
        if self._efforts is None:
            listing = self._probe(catalog.MODELS_PATH, None).reply
            data = listing.get("data") if listing is not None else None
            self._efforts = {
                item["id"]: catalog.offered_efforts(item)
                for item in (data if isinstance(data, list) else [])
                if isinstance(item, dict) and isinstance(item.get("id"), str)
            }
        return self._efforts.get(model) != ()

    def _level(self, model: str, thinking: str = CHAT_THINKING_LEVEL) -> str:
        """``thinking``, or NO_THINKING (no reasoning field) for a model with no levels."""
        return thinking if self.thinks(model) else NO_THINKING

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
            listing = self._probe(f"/models/{model}/endpoints", None)
            good, evidence = self._listing(listing, thinks=self.thinks(model))
            said.append(f"{model}: {evidence}")
            ok = ok and good
        return Finding("endpoints", PASS if ok else FAIL, "; ".join(said))

    def _listing(self, probe: Probe, *, thinks: bool = True) -> tuple[bool, str]:
        data = probe.reply.get("data") if probe.reply is not None else None
        endpoints = data.get("endpoints") if isinstance(data, dict) else None
        if not isinstance(endpoints, list):
            return False, f"status {_said(probe)}, no endpoints list"
        # Any approved processor (OPENROUTER_PROVIDERS, google-vertex by default), not Vertex
        # alone: a Qwen or Llama model is served elsewhere (Tushar, 18 Oct 2026).
        allowed = self.settings.openrouter_providers
        vertex = [
            endpoint
            for endpoint in endpoints
            if isinstance(endpoint, dict)
            and isinstance(endpoint.get("tag"), str)
            and endpoint["tag"].partition("/")[0] in allowed
        ]
        if not vertex:
            return False, f"{len(endpoints)} endpoints, {'/'.join(sorted(allowed))} not listed"
        listed = "/".join(sorted({endpoint["tag"].partition("/")[0] for endpoint in vertex}))
        parameters = {
            name
            for endpoint in vertex
            if isinstance(endpoint.get("supported_parameters"), list)
            for name in endpoint["supported_parameters"]
            if isinstance(name, str)
        }
        lacking = _lacking(parameters, thinks=thinks)
        zdr = sorted(
            {f"{key}={value}" for endpoint in vertex for key, value in endpoint.items() if "zdr" in key}
        )
        evidence = (
            f"{listed} listed; parameters {'all supported' if not lacking else 'lack ' + ','.join(lacking)};"
            f" {' '.join(zdr) if zdr else 'no zdr field'}"
        )
        return not lacking, evidence

    def model_calls(self) -> Finding:
        if (missing := self._no_key("model_calls")) is not None:
            return missing
        said, ok = self._bad_models(), not self.bad_models
        for model in self.models:
            probe = self._probe(CHAT_PATH, _text_only(model, thinking=self._level(model)))
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
        verdict = FAIL if not needed <= accepted else PASS if high_reasoning > 0 else INFO
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
            # Any provider's native reason: Google says MAX_TOKENS, others their own.
            if error.code == "output_truncated" and finish == "length":
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


class ModelCheck:
    """The ``--model`` items for one model (see the module notes), in order: each live item
    after ``model_endpoint`` goes through the provider it found, and fails with no request
    when it found none. ``tokens`` is the page read's (input, output) for ``--record``."""

    def __init__(self, verifier: Verifier, model: str, *, chat: bool = False) -> None:
        self.verifier = verifier
        self.model = model
        self.chat = chat
        self.provider: str | None = None
        self.read: Probe | None = None  # the PAGE_SCHEMA read
        self.value: dict[str, Any] | None = None
        self.capped: Probe | None = None  # the tiny output cap's reply
        self.tokens: tuple[int, int] | None = None
        self.answer: dict[str, Any] | None = None  # chat_schema's reply

    def items(self) -> list[tuple[str, Callable[[], Finding]]]:
        if self.chat:
            return [
                ("model_endpoint", self.endpoint),
                ("model_reply", self.reply),
                ("chat_schema", self.chat_schema),
                ("chat_levels", self.chat_levels),
                ("chat_cites", self.chat_cites),
                ("model_refusal", self.refusal),
            ]
        return [
            ("model_endpoint", self.endpoint),
            ("model_reply", self.reply),
            ("model_page_schema", self.page_schema),
            ("model_made_up_page", self.made_up_page),
            ("model_boxes", self.boxes),
            ("model_devanagari", self.devanagari),
            ("model_efforts", self.efforts),
            ("model_cost", self.cost),
            ("model_truncation", self.truncation),
            ("model_token_cap", self.token_cap),
            ("model_refusal", self.refusal),
        ]

    def _found(self, item: str, verdict: str, evidence: str) -> Finding:
        return Finding(item, verdict, f"{self.model}: {evidence}")

    def _cannot(self, item: str) -> Finding | None:
        """The FAIL of a live item that can't be asked: no key, a bad ID, no provider."""
        if not self.verifier.key_set:
            return self._found(item, FAIL, "OPENROUTER_API_KEY missing")
        if not valid_model_id(self.model):
            return self._found(item, FAIL, "not an OpenRouter model ID")
        if item != "model_endpoint" and self.provider is None:
            return self._found(item, FAIL, "no usable endpoint (model_endpoint)")
        return None

    def _page(self, schema: Mapping[str, Any], instruction: str, **options: Any) -> Probe:
        """The made-up page read by this model through its provider."""
        settings = self.verifier.settings
        values: dict[str, Any] = {
            "model": self.model,
            "png": self.verifier.page.png,
            "instruction": instruction,
            "schema": schema,
            "media_resolution": settings.ocr_media_resolution,
            "thinking": settings.ocr_thinking_level,
            "max_output_tokens": openrouter.PAGE_MAX_OUTPUT_TOKENS,
            "provider_slug": self.provider,
        }
        values = {**values, **options}
        values["thinking"] = self.verifier._level(self.model, values["thinking"])
        return self.verifier._probe(CHAT_PATH, page_request(**values))

    def endpoint(self) -> Finding:
        if (cannot := self._cannot("model_endpoint")) is not None:
            return cannot
        listing = self.verifier._probe(f"/models/{self.model}/endpoints", None)
        zdr = self.verifier._probe(catalog.ZDR_PATH, None)
        try:
            endpoints = catalog.zdr_slugs(self.model, listing.reply, catalog.zdr_endpoints(zdr.reply))
        except catalog.CatalogUnavailable as error:
            evidence = f"status {_said(listing)}, zdr {_said(zdr)}: {error.code}"
            return self._found("model_endpoint", FAIL, evidence)
        allowed = self.verifier.settings.openrouter_providers
        usable = [
            slug
            for slug, parameters in endpoints
            if slug in allowed
            and catalog.STRUCTURED in parameters
            and not _lacking(parameters, thinks=self.verifier.thinks(self.model))
        ]
        said = f"ZDR endpoints {','.join(slug for slug, _ in endpoints) or 'none'}"
        if not usable:
            evidence = f"{said}; none of OPENROUTER_PROVIDERS with structured output and the parameters"
            return self._found("model_endpoint", FAIL, evidence)
        self.provider = usable[0]
        return self._found("model_endpoint", PASS, f"{said}; reads through {self.provider}")

    def reply(self) -> Finding:
        if (cannot := self._cannot("model_reply")) is not None:
            return cannot
        body = _text_only(
            self.model, self.provider or DEFAULT_PROVIDER, thinking=self.verifier._level(self.model)
        )
        probe = self.verifier._probe(CHAT_PATH, body)
        reply_id = probe.reply.get("id") if probe.reply is not None else None
        named = probe.reply.get("provider") if probe.reply is not None else None
        name = named if isinstance(named, str) and _PROVIDER_NAME.fullmatch(named) else None
        lookup = probe.status == 200 and isinstance(reply_id, str) and _REPLY_ID.fullmatch(reply_id)
        if name is None and lookup:
            for attempt in range(GENERATION_TRIES):
                if attempt:
                    self.verifier.sleep(1.0)
                name = _provider_name(self.verifier._probe(f"{GENERATION_PATH}?id={reply_id}", None))
                if name is not None:
                    break
        verdict = PASS if probe.status == 200 and name is not None else FAIL
        return self._found("model_reply", verdict, f"{_said(probe)}; provider_name {name or 'not given'}")

    def page_schema(self) -> Finding:
        if (cannot := self._cannot("model_page_schema")) is not None:
            return cannot
        reader = gemini_reader_module()
        self.read = self._page(reader.PAGE_SCHEMA, reader.INSTRUCTION)
        self.value, said = _parsed(self.read, reader.PAGE_SCHEMA)
        if self.value is None or self.read.reply is None:
            return self._found("model_page_schema", FAIL, said)
        self.tokens = openrouter.reply_usage(self.read.reply)
        evidence = f"{said} follows PAGE_SCHEMA; tokens in {self.tokens[0]} out {self.tokens[1]}"
        return self._found("model_page_schema", PASS, evidence)

    def _lines(self, item: str) -> tuple[list[Any] | None, Finding | None]:
        """The page read's checked lines, or the item's FAIL."""
        if self.value is None:
            return None, self._found(item, FAIL, "no page read (model_page_schema)")
        try:
            return list(gemini_reader_module().checked(self.value).lines), None
        except ModelOutputError as error:
            return None, self._found(item, FAIL, error.code)

    def made_up_page(self) -> Finding:
        lines, failed = self._lines("model_made_up_page")
        if lines is None:
            return failed or self._found("model_made_up_page", FAIL, "no lines")
        exact = sum(1 for line, truth in zip(lines, TRUTH, strict=False) if _same(line.text, truth))
        verdict = PASS if exact == len(TRUTH) == len(lines) else FAIL
        return self._found(
            "model_made_up_page", verdict, f"{exact}/{len(TRUTH)} lines exact, {len(lines)} read"
        )

    def boxes(self) -> Finding:
        lines, failed = self._lines("model_boxes")
        if lines is None:
            return failed or self._found("model_boxes", FAIL, "no lines")
        width, height = self.verifier.page.image.size
        truth = [(float(x0), float(y0), float(x1), float(y1)) for x0, y0, x1, y1 in self.verifier.page.boxes]
        placed = gemini_reader_module().pinned(lines, truth, width, height)
        pinned = sum(1 for line in placed if line.box_source == "found")
        best = [
            max((_iou(box, (line.x0, line.y0, line.x1, line.y1)) for line in placed), default=0.0)
            for box in truth
        ]
        verdict = PASS if lines and pinned == len(lines) else FAIL
        evidence = f"pinned {pinned}/{len(lines)}; best IoU per line {' '.join(f'{iou:.2f}' for iou in best)}"
        return self._found("model_boxes", verdict, evidence)

    def devanagari(self) -> Finding:
        evidence = (
            "not drawn: no Devanagari font is bundled (Pillow's own font is Latin only, and a font"
            " would be a new dependency); measure it on a real Hindi or Marathi page"
        )
        return self._found("model_devanagari", INFO, evidence)

    def efforts(self) -> Finding:
        if (cannot := self._cannot("model_efforts")) is not None:
            return cannot
        settings = self.verifier.settings
        if not self.verifier.thinks(self.model):  # one mode: read once with no reasoning field
            good, description = _outcome(self._page(SCHEMA, INSTRUCTION, max_output_tokens=None))
            evidence = f"n/a, no thinking levels: read with no reasoning field {description}"
            return self._found("model_efforts", PASS if good else FAIL, evidence)
        ok, said = True, []
        for level in sorted({settings.ocr_thinking_level, settings.ocr_retry_thinking_level}):
            probe = self._page(SCHEMA, INSTRUCTION, thinking=level, max_output_tokens=None)
            good, description = _outcome(probe)
            thought = reasoning_tokens(probe.reply) if probe.reply is not None else 0
            ok = ok and good
            said.append(f"{level}={description} reasoning={thought}")
        return self._found("model_efforts", PASS if ok else FAIL, " ".join(said))

    def cost(self) -> Finding:
        if (cannot := self._cannot("model_cost")) is not None:
            return cannot
        cost = reply_cost(self.read.reply) if self.read is not None and self.read.reply is not None else None
        verdict = PASS if cost is not None and cost > 0 else FAIL
        return self._found("model_cost", verdict, f"usage.cost {_number(cost)} on the page read")

    def truncation(self) -> Finding:
        if (cannot := self._cannot("model_truncation")) is not None:
            return cannot
        self.capped = probe = self._page(SCHEMA, INSTRUCTION, max_output_tokens=TINY_OUTPUT_TOKENS)
        finish, native = finish_reasons(probe.reply) if probe.reply is not None else (None, None)
        evidence = f"status {_said(probe)} finish {finish} native {native}"
        try:
            if probe.reply is not None:
                reply_text(probe.reply)
        except ModelOutputError as error:
            if error.code == "output_truncated" and finish == "length":
                return self._found("model_truncation", PASS, f"{evidence}; client: output_truncated")
            evidence += f"; client: {error.code}"
        except ModelUnavailable as error:
            evidence += f"; client: {error.code}"
        return self._found("model_truncation", FAIL, evidence)

    def token_cap(self) -> Finding:
        if (cannot := self._cannot("model_token_cap")) is not None:
            return cannot
        given = _usage(self.capped).get("completion_tokens")
        if isinstance(given, bool) or not isinstance(given, int):
            return self._found("model_token_cap", FAIL, "no completion_tokens (model_truncation)")
        verdict = PASS if given <= TINY_OUTPUT_TOKENS else FAIL
        return self._found("model_token_cap", verdict, f"completion {given} at a cap of {TINY_OUTPUT_TOKENS}")

    # Answering (features 25-27, C11): the answer's own prompt and schema, made-up evidence.

    def _answer(self, thinking: str) -> Probe:
        """The made-up question answered by this model at ``thinking``, with the answer's
        SYSTEM_PROMPT and ANSWER_SCHEMA as a draft sends them."""
        blocks = "\n".join(
            f'<evidence id="{n}">\n{text}\n</evidence>' for n, text in enumerate(CHAT_EVIDENCE, 1)
        )
        user = f"Question:\n<question>\n{CHAT_QUESTION}\n</question>\n\nEvidence:\n{blocks}"
        thinking = self.verifier._level(self.model, thinking)
        body = chat_request(
            model=self.model,
            messages=[{"role": "system", "content": draft.SYSTEM_PROMPT}, {"role": "user", "content": user}],
            thinking=thinking,
            schema=draft.ANSWER_SCHEMA,
            max_output_tokens=self.verifier.settings.model_answer_max_tokens
            + CHAT_THINKING_TOKENS.get(thinking, 0),
            provider_slug=self.provider or DEFAULT_PROVIDER,
        )
        return self.verifier._probe(CHAT_PATH, body)

    def chat_schema(self) -> Finding:
        if (cannot := self._cannot("chat_schema")) is not None:
            return cannot
        self.answer, said = _parsed(self._answer(CHAT_THINKING_LEVEL), draft.ANSWER_SCHEMA)
        if self.answer is None:
            return self._found("chat_schema", FAIL, said)
        try:
            draft.checked(self.answer)
        except ModelOutputError as error:
            return self._found("chat_schema", FAIL, f"{said}; the answer reads it as {error.code}")
        return self._found("chat_schema", PASS, f"{said} follows ANSWER_SCHEMA")

    def chat_levels(self) -> Finding:
        if (cannot := self._cannot("chat_levels")) is not None:
            return cannot
        if not self.verifier.thinks(self.model):  # one mode: asked once with no reasoning field
            value, description = _parsed(self._answer(NO_THINKING), draft.ANSWER_SCHEMA)
            evidence = f"n/a, no thinking levels: asked with no reasoning field {description}"
            return self._found("chat_levels", PASS if value is not None else FAIL, evidence)
        ok, said = True, []
        for level in CHAT_LEVELS:
            value, description = _parsed(self._answer(level), draft.ANSWER_SCHEMA)
            ok = ok and value is not None
            said.append(f"{level}={description}")
        return self._found("chat_levels", PASS if ok else FAIL, " ".join(said))

    def chat_cites(self) -> Finding:
        if (cannot := self._cannot("chat_cites")) is not None:
            return cannot
        if self.answer is None:
            return self._found("chat_cites", FAIL, "no answer (chat_schema)")
        try:
            answered = draft.checked(self.answer)
        except ModelOutputError as error:
            return self._found("chat_cites", FAIL, error.code)
        cited = sorted({n for sentence in answered.sentences for n in sentence.evidence})
        total = any(CHAT_TOTAL in sentence.text for sentence in answered.sentences)
        verdict = PASS if answered.status == "answered" and cited == [1] and total else FAIL
        evidence = (
            f"{answered.status}, cites {cited or 'nothing'}, the right total {'yes' if total else 'no'}"
        )
        return self._found("chat_cites", verdict, evidence)

    def refusal(self) -> Finding:
        said, ok = [], True
        for shape, choice in REFUSALS.items():
            try:
                reply_text({"choices": [choice]})
                code = "text"
            except (ModelOutputError, ModelUnavailable) as error:
                code = error.code
            ok = ok and code == "blocked"
            said.append(f"{shape}={code}")
        return self._found("model_refusal", PASS if ok else FAIL, f"{' '.join(said)}; local, no call")


def _provider_name(probe: Probe) -> str | None:
    """``data.provider_name`` of a /generation reply, when it has a safe shape."""
    data = probe.reply.get("data") if probe.reply is not None else None
    name = data.get("provider_name") if isinstance(data, dict) else None
    return name if isinstance(name, str) and _PROVIDER_NAME.fullmatch(name) else None


def _iou(a: tuple[float, float, float, float], b: tuple[float, float, float, float]) -> float:
    """Intersection over union of two (x0, y0, x1, y1) boxes; 0 when either has no area."""
    width = min(a[2], b[2]) - max(a[0], b[0])
    height = min(a[3], b[3]) - max(a[1], b[1])
    union = (a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1])
    if width <= 0 or height <= 0 or union <= 0:
        return 0.0
    return width * height / (union - width * height)


def _text_only(
    model: str, provider: str = DEFAULT_PROVIDER, *, thinking: str = CHAT_THINKING_LEVEL
) -> dict[str, Any]:
    """A one-line chat request to ``model``, with the provider block for ``provider``."""
    return chat_request(
        model=model,
        messages=[{"role": "user", "content": "Reply with the word OK."}],
        thinking=thinking,
        schema=None,
        max_output_tokens=None,
        provider_slug=provider,
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
    sleep: Callable[[float], None] = time.sleep,
    record: Callable[[str, int, int], None] | None = None,
    chat: bool = False,
    record_chat: Callable[[str], None] | None = None,
) -> int:
    """Check every item, print one line each and a summary; 1 when any FAILs, else 0.
    ``record`` (``--record``, exactly one model): called with (model, tokens in, tokens
    out) only when every REQUIRED item of that model PASSes and no item FAILs; else
    nothing is written. ``chat`` (``--chat``, features 25-27): each ``--model`` is checked
    for answering (CHAT_REQUIRED) instead, and ``record_chat`` (``--chat --record``) is
    called with the model the same way."""
    verifier = Verifier(settings, models=models, embed_model=embed_model, timer=timer, sleep=sleep, chat=chat)
    out.write(f"KEY OPENROUTER_API_KEY {'set' if verifier.key_set else 'missing'}\n")
    out.write(f"MODELS ocr {settings.ocr_model} chat {settings.model_chat} embed {verifier.embed_model}\n")
    findings = [_said_finding(out, item) for _, item in verifier.items()]
    passed: dict[str, set[str]] = {}
    for check in verifier.checks:
        mine = [_said_finding(out, item) for _, item in check.items()]
        passed[check.model] = {finding.item for finding in mine if finding.verdict == PASS}
        findings += mine
    counts = {verdict: sum(1 for f in findings if f.verdict == verdict) for verdict in (PASS, FAIL, INFO)}
    out.write(f"SUMMARY {counts[PASS]} PASS, {counts[FAIL]} FAIL, {counts[INFO]} INFO\n")
    if record_chat is not None:
        [check] = verifier.checks
        failed = [item for item in CHAT_REQUIRED if item not in passed[check.model]]
        failed += [f.item for f in findings if f.verdict == FAIL and f.item not in failed]
        if failed:
            out.write(f"NOT RECORDED {check.model}: {','.join(failed)} did not pass\n")
            return 1
        record_chat(check.model)
        out.write(f"RECORDED {check.model} for answers, prompt {draft.ANSWER_PROMPT_HASH}\n")
    if record is not None:
        [check] = verifier.checks
        failed = [item for item in REQUIRED if item not in passed[check.model]]
        failed += [
            finding.item for finding in findings if finding.verdict == FAIL and finding.item not in failed
        ]
        if failed or check.tokens is None:
            out.write(f"NOT RECORDED {check.model}: {','.join(failed)} did not pass\n")
            return 1
        record(check.model, *check.tokens)
        out.write(
            f"RECORDED {check.model} prompt {READER_PROMPT_VERSION}"
            f" tokens per page in {check.tokens[0]} out {check.tokens[1]}\n"
        )
    return 1 if counts[FAIL] else 0


def _said_finding(out: TextIO, item: Callable[[], Finding]) -> Finding:
    finding = item()
    out.write(f"VERIFY {finding.item} {finding.verdict} {finding.evidence}\n")
    return finding
