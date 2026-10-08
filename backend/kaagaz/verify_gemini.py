"""``python -m kaagaz.cli verify-gemini``: the PRD 5 verify items, checked live.

PRD 5 says every request field, response field and model ID is checked against the
live Gemini API or the installed package before use (section 14), and never
guessed. This command does that on the server where the paid key lives (council
question 6): it uses no database, file storage or queue; the key comes from the
settings and is printed only as ``set`` or ``missing``; every picture is made up
here, in memory (lines of a made-up invoice drawn with Pillow's own font), so no
customer page is ever sent.

It prints one line per item, ``VERIFY <id> PASS|FAIL|MANUAL <evidence>``, and exits
1 when any item FAILs. MANUAL means a person must read the evidence and act (the
rate limit is read in AI Studio). The 14 items:

1. ``schema_shape``: structured output, shape A (``responseMimeType`` with
   ``responseJsonSchema``) and shape B (``responseFormat``); PASS when the shape
   requests use (``gemini.STRUCTURED_OUTPUT``) gives schema-following JSON.
2. ``thinking_levels``: each thinking level on OCR_MODEL; PASS when the levels we
   send (OCR_THINKING_LEVEL, OCR_RETRY_THINKING_LEVEL, chat's) are accepted.
3. ``media_resolution_tokens``: the image at ``high`` and ``ultra_high`` (the read
   again level, F5); PASS when both are accepted and ultra_high costs more.
4. ``usage_keys``: ``usageMetadata`` has the counts the client bills (G9).
5. ``max_tokens_finish``: a tiny ``maxOutputTokens`` ends ``MAX_TOKENS``, which the
   client turns into ``output_truncated`` (G6); shows whether thinking counts in it.
6. ``default_temperature``: the page read 3 times at Google's default temperature
   through ``read_page``; PASS when every run is exact.
7. ``embedding_dims``: ``batchEmbedContents`` at output dimensionality 1024 (M5):
   the vectors' length and norm.
8. ``model_ids``: one call to each model ID (OCR_MODEL, a Gemini MODEL_CHAT and each
   ``--model``) answers 200; a made-up model ID answers 404 (``model_not_found``).
9. ``no_key_status``: a request with no key answers 401 or 403 (``key_rejected``).
10. ``rate_limit_headers``: the rate-limit headers Google sends, if any; MANUAL:
    set GEMINI_RPM from AI Studio.
11. ``rapidocr_detection_only``: RapidOCR's text finder alone (G5) finds boxes and
    no text: ``Global.use_rec`` and ``Global.use_cls`` off, on its own instance.
12. ``rapidocr_recognition_only``: RapidOCR's reader alone (V1) reads one line's crop:
    ``Global.use_det`` and ``Global.use_cls`` off, on its own instance.
13. ``detection_model_sha``: each detection model file in RapidOCR's package has the
    SHA-256 the installed RapidOCR lists for it (it is in the image, never fetched).
14. ``parallel_latency``: 6 pages read at once through one client (G8), and the
    seconds each took, against one read alone.

Cost: about 20 Gemini calls with a small picture, a few cents on the Pro model.
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

from kaagaz.ai import gemini
from kaagaz.ai.gemini import CHAT_THINKING_LEVEL, GeminiClient, Probe, page_request, reply_text
from kaagaz.ai.replies import ModelOutputError, ModelUnavailable, parse_reply
from kaagaz.settings import EMBEDDING_DIM, Settings, is_gemini_model

PASS, FAIL, MANUAL = "PASS", "FAIL", "MANUAL"
# The embedding model M5 names; used when MODEL_EMBED is not a Gemini model yet (question 4).
GEMINI_EMBED_MODEL = "gemini-embedding-2"
BOGUS_MODEL = "gemini-0.0-not-a-model"
TEMPERATURE_RUNS = 3
PARALLEL_CALLS = 6
TINY_OUTPUT_TOKENS = 16
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
}
EMBED_TEXTS = ["Made-up invoice 4471, total 12,345.00", "मराठी नमुना मजकूर"]
_TEXT_ONLY = {
    "contents": [{"role": "user", "parts": [{"text": "Reply with the word OK."}]}],
    "generationConfig": {"thinkingConfig": {"thinkingLevel": gemini.thinking_level(CHAT_THINKING_LEVEL)}},
}
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


def _outcome(probe: Probe) -> tuple[bool, str]:
    """(the reply is schema-following JSON, a short description of what came back)."""
    if probe.status == 0:
        return False, "no reply"
    if probe.reply is None:
        return False, f"{probe.status} {probe.error_status or 'no JSON'}"
    try:
        text, _ = reply_text(probe.reply)
    except ModelOutputError as error:
        return False, f"{probe.status} {error.code}"
    value, problem = parse_reply(text, SCHEMA)
    return (value is not None, f"{probe.status} json" if value is not None else f"{probe.status} {problem}")


def _usage(probe: Probe) -> dict[str, Any]:
    usage = probe.reply.get("usageMetadata") if probe.reply is not None else None
    return usage if isinstance(usage, dict) else {}


def _finish(probe: Probe) -> str | None:
    candidates = probe.reply.get("candidates") if probe.reply is not None else None
    if isinstance(candidates, list) and candidates and isinstance(candidates[0], dict):
        finish = candidates[0].get("finishReason")
        return finish if isinstance(finish, str) else None
    return None


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
        self.key_set = settings.gemini_api_key is not None
        self.page = made_up_page()
        self.timer = timer
        self.timeout = settings.ocr_timeout_seconds
        gemini_chat = [settings.model_chat] if is_gemini_model(settings.model_chat) else []
        self.models = list(dict.fromkeys([settings.ocr_model, *gemini_chat, *models]))
        if embed_model is None:
            embed_model = (
                settings.model_embed if is_gemini_model(settings.model_embed) else GEMINI_EMBED_MODEL
            )
        self.embed_model = embed_model
        # The no-key probe never sends a key: this client holds a placeholder.
        self.keyless = GeminiClient(dataclasses.replace(settings, gemini_api_key="no-key-sent"))
        self.client: GeminiClient | None = None
        self.embedder: GeminiClient | None = None
        if self.key_set:
            self.client = GeminiClient(settings)
            self.embedder = GeminiClient(dataclasses.replace(settings, model_embed=embed_model))
        self.first: Probe | None = None  # shape A's reply, for usage_keys and the headers
        self.headers: set[str] = set()
        self.one_read_seconds: float | None = None

    def items(self) -> list[tuple[str, Callable[[], Finding]]]:
        return [
            ("schema_shape", self.schema_shape),
            ("thinking_levels", self.thinking_levels),
            ("media_resolution_tokens", self.media_resolution_tokens),
            ("usage_keys", self.usage_keys),
            ("max_tokens_finish", self.max_tokens_finish),
            ("default_temperature", self.default_temperature),
            ("embedding_dims", self.embedding_dims),
            ("model_ids", self.model_ids),
            ("no_key_status", self.no_key_status),
            ("rate_limit_headers", self.rate_limit_headers),
            ("rapidocr_detection_only", self.rapidocr_detection_only),
            ("rapidocr_recognition_only", self.rapidocr_recognition_only),
            ("detection_model_sha", self.detection_model_sha),
            ("parallel_latency", self.parallel_latency),
        ]

    # Gemini.

    def _live(self) -> GeminiClient:
        """The client with the key; items call it only after ``_no_key`` found one."""
        if self.client is None:
            raise RuntimeError("GEMINI_API_KEY missing")
        return self.client

    def _probe(self, body: Mapping[str, Any], model: str | None = None) -> Probe:
        path = f"/models/{model or self.settings.ocr_model}:generateContent"
        probe = self._live().probe(path, body, timeout=self.timeout)
        self.headers.update(probe.headers)
        return probe

    def _page_body(self, **options: Any) -> dict[str, Any]:
        values: dict[str, Any] = {
            "png": self.page.png,
            "instruction": INSTRUCTION,
            "schema": SCHEMA,
            "media_resolution": self.settings.ocr_media_resolution,
            "thinking": self.settings.ocr_thinking_level,
            "max_output_tokens": None,
        }
        return page_request(**{**values, **options})

    def _no_key(self, item: str) -> Finding | None:
        return None if self.key_set else Finding(item, FAIL, "GEMINI_API_KEY missing")

    def schema_shape(self) -> Finding:
        if (missing := self._no_key("schema_shape")) is not None:
            return missing
        results = {}
        for shape in sorted(gemini.STRUCTURED_OUTPUT_SHAPES):
            probe = self._probe(self._page_body(shape=shape))
            if shape == "A":
                self.first = probe
            results[shape] = _outcome(probe)
        used = gemini.STRUCTURED_OUTPUT
        evidence = " ".join(f"{shape}={said}" for shape, (_, said) in results.items())
        if results[used][0]:
            return Finding("schema_shape", PASS, f"{evidence}; requests use {used}")
        working = [shape for shape, (ok, _) in results.items() if ok]
        advice = f"set STRUCTURED_OUTPUT to {working[0]}" if working else "no shape worked"
        return Finding("schema_shape", FAIL, f"{evidence}; requests use {used}: {advice}")

    def thinking_levels(self) -> Finding:
        if (missing := self._no_key("thinking_levels")) is not None:
            return missing
        accepted, said = set(), []
        for level in ("minimal", "low", "medium", "high"):
            probe = self._probe(self._page_body(thinking=level))
            ok, description = _outcome(probe)
            thoughts = _usage(probe).get("thoughtsTokenCount")
            said.append(f"{level}={description}" + (f" thoughts={thoughts}" if ok else ""))
            if ok:
                accepted.add(level)
        needed = {
            self.settings.ocr_thinking_level,
            self.settings.ocr_retry_thinking_level,
            CHAT_THINKING_LEVEL,
        }
        verdict = PASS if needed <= accepted else FAIL
        return Finding("thinking_levels", verdict, f"{' '.join(said)}; we send {','.join(sorted(needed))}")

    def media_resolution_tokens(self) -> Finding:
        if (missing := self._no_key("media_resolution_tokens")) is not None:
            return missing
        prompts: dict[str, int | None] = {}
        said = []
        for level in ("high", "ultra_high"):
            probe = self._probe(self._page_body(media_resolution=level))
            ok, description = _outcome(probe)
            count = _usage(probe).get("promptTokenCount")
            prompts[level] = count if ok and isinstance(count, int) else None
            said.append(f"{level}={description} prompt={count}")
        high, ultra = prompts["high"], prompts["ultra_high"]
        if high is None or ultra is None:
            return Finding("media_resolution_tokens", FAIL, " ".join(said))
        verdict = PASS if ultra > high else FAIL
        return Finding("media_resolution_tokens", verdict, f"{' '.join(said)} delta={ultra - high}")

    def usage_keys(self) -> Finding:
        if (missing := self._no_key("usage_keys")) is not None:
            return missing
        if self.first is None or self.first.reply is None:
            return Finding("usage_keys", FAIL, "no reply to read usageMetadata from")
        usage = _usage(self.first)
        counts = {name: usage.get(name) for name in ("promptTokenCount", "candidatesTokenCount")}
        thoughts = usage.get("thoughtsTokenCount")
        total = usage.get("totalTokenCount")
        whole = [value for value in (*counts.values(), total) if isinstance(value, int)]
        if len(whole) != 3:
            return Finding("usage_keys", FAIL, f"keys {','.join(sorted(usage)) or 'none'}")
        summed = sum(value for value in (*counts.values(), thoughts) if isinstance(value, int))
        evidence = (
            f"keys {','.join(sorted(usage))};"
            f" thoughtsTokenCount {'present' if thoughts is not None else 'absent'};"
            f" total {'=' if summed == total else '!='} prompt+candidates+thoughts"
        )
        return Finding("usage_keys", PASS, evidence)

    def max_tokens_finish(self) -> Finding:
        if (missing := self._no_key("max_tokens_finish")) is not None:
            return missing
        probe = self._probe(self._page_body(max_output_tokens=TINY_OUTPUT_TOKENS))
        finish = _finish(probe)
        usage = _usage(probe)
        candidates, thoughts = usage.get("candidatesTokenCount", 0), usage.get("thoughtsTokenCount", 0)
        evidence = f"status {probe.status} finish {finish} candidates {candidates} thoughts {thoughts}"
        if isinstance(candidates, int) and isinstance(thoughts, int):
            inside = "yes" if candidates + thoughts <= TINY_OUTPUT_TOKENS else "no"
            evidence += f"; thoughts inside maxOutputTokens {inside}"
        try:
            if probe.reply is not None:
                reply_text(probe.reply)
        except ModelOutputError as error:
            if error.code == "output_truncated":
                return Finding("max_tokens_finish", PASS, f"{evidence}; client: output_truncated")
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
            raise RuntimeError("GEMINI_API_KEY missing")
        try:
            vectors = self.embedder.embed(EMBED_TEXTS)
        except (ModelUnavailable, ModelOutputError) as error:
            return Finding("embedding_dims", FAIL, f"{self.embed_model}: {error.code}")
        norms = ", ".join(f"{math.sqrt(sum(v * v for v in vector)):.4f}" for vector in vectors)
        dims = sorted({len(vector) for vector in vectors})
        verdict = PASS if dims == [EMBEDDING_DIM] else FAIL
        return Finding(
            "embedding_dims", verdict, f"{self.embed_model}: {len(vectors)} x {dims} norms {norms}"
        )

    def model_ids(self) -> Finding:
        if (missing := self._no_key("model_ids")) is not None:
            return missing
        said, ok = [], True
        for model in self.models:
            probe = self._probe(_TEXT_ONLY, model)
            said.append(f"{model}={probe.status}")
            ok = ok and probe.status == 200
        bogus = self._probe(_TEXT_ONLY, BOGUS_MODEL)
        said.append(f"{BOGUS_MODEL}={bogus.status} {bogus.error_status or ''}".rstrip())
        ok = ok and bogus.status == 404
        return Finding("model_ids", PASS if ok else FAIL, " ".join(said) + "; a 404 is model_not_found")

    def no_key_status(self) -> Finding:
        path = f"/models/{self.settings.ocr_model}:generateContent"
        probe = self.keyless.probe(path, _TEXT_ONLY, send_key=False, timeout=self.timeout)
        evidence = f"status {probe.status} {probe.error_status or ''}".rstrip()
        if probe.status in (401, 403):
            return Finding("no_key_status", PASS, f"{evidence}; client: key_rejected")
        return Finding("no_key_status", FAIL, evidence)

    def rate_limit_headers(self) -> Finding:
        names = ",".join(sorted(self.headers)) or "none"
        evidence = f"headers {names}; set GEMINI_RPM (now {self.settings.gemini_rpm}) from AI Studio's limits"
        return Finding("rate_limit_headers", MANUAL, evidence)

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
            f" one alone {alone} s; limiter {client.interval:.2f} s apart"
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
    out.write(f"KEY GEMINI_API_KEY {'set' if verifier.key_set else 'missing'}\n")
    out.write(f"MODELS ocr {settings.ocr_model} chat {settings.model_chat} embed {verifier.embed_model}\n")
    findings = []
    for _, check in verifier.items():
        finding = check()
        findings.append(finding)
        out.write(f"VERIFY {finding.item} {finding.verdict} {finding.evidence}\n")
    counts = {verdict: sum(1 for f in findings if f.verdict == verdict) for verdict in (PASS, FAIL, MANUAL)}
    out.write(f"SUMMARY {counts[PASS]} PASS, {counts[FAIL]} FAIL, {counts[MANUAL]} MANUAL\n")
    return 1 if counts[FAIL] else 0
