"""The Gemini client (PRD 5 G9): ``ModelClient`` over Google's Gemini REST API.

``GeminiClient`` implements ``chat_json``, ``embed`` and ``read_image`` like the
Ollama client, and adds ``read_page`` for the Gemini reader (G1, G2, G3). It
uses exactly the models in the settings: ``OCR_MODEL`` reads pictures
(``read_page`` and ``read_image``), ``MODEL_CHAT`` answers ``chat_json`` and
``MODEL_EMBED`` embeds. No code here picks or switches a model.

**The key is a secret.** It goes in the ``x-goog-api-key`` header only, added with
``add_unredirected_header`` so it is never copied onto a redirect, and redirects
are refused anyway. Never in the URL, a log, an error or a repr. The opener
ignores ``HTTP_PROXY`` style variables, and the base URL is https (plain http only
on a loopback host, for the test fake: the settings refuse anything else, and so
does this client). Error replies are never logged or kept: only their
``error.status`` word is read, and only by ``probe`` (``kaagaz.verify_gemini``).
Every error this module raises carries a short code only, raised ``from None``.

**The request** (``generateContent`` on v1beta, G2). The instruction text comes
before the image. The image is ``inline_data`` (``image/png``, base64) with its
own ``media_resolution`` level (``MEDIA_RESOLUTION_HIGH`` for the setting
``high``). ``generationConfig.thinkingConfig.thinkingLevel`` is the setting in
capitals. Temperature is left at Google's default (PRD 5 section 14: Google
recommends the default 1.0 for Gemini 3). Structured output is one request shape,
``STRUCTURED_OUTPUT``: ``python -m kaagaz.cli verify-gemini`` tries both known
shapes against the live API, and switching is that one constant.

**Usage** (G9). Input tokens are ``usageMetadata.promptTokenCount``; output tokens
are ``candidatesTokenCount`` plus ``thoughtsTokenCount``, because thinking is
billed as output. ``read_page`` returns them per page and ``usage()`` keeps the
process's totals.

**Failures** (G6), each a code:

* 429, and 5xx: retried with exponential backoff and full jitter, honouring
  ``Retry-After``, inside the call's deadline; then ``ModelUnavailable``
  ``rate_limited`` or ``server_error``. A refused or dropped connection is retried
  the same way (``unreachable``); a reply that does not come before the deadline
  is ``timeout``.
* 400: ``ModelOutputError("bad_request")``: this request will never work, the
  reader reads the page locally (F2).
* 401 or 403: ``ModelUnavailable("key_rejected")``, logged at error once, and for
  ``KEY_LATCH_SECONDS`` every call fails at once with the same code, without a
  request: a wrong key or a spent cap is a person's fix, never a tight loop.
* 404: ``ModelUnavailable("model_not_found")`` (a wrong model ID in .env).
* A redirect: ``ModelUnavailable("redirect_refused")``; any other status
  ``http_error``.
* A blocked reply (``promptFeedback.blockReason``, or a finish reason in
  ``BLOCKED_FINISH_REASONS``): ``ModelOutputError("blocked")``.
* ``MAX_TOKENS``: ``ModelOutputError("output_truncated")``, with no repair retry
  (a cut reply would be cut again).
* Any other finish reason, or none: ``ModelOutputError("invalid_output")``.
* JSON that does not follow the schema: one repair retry, as the Ollama client
  does, then ``ModelOutputError("invalid_output")``.

**Rate limit and circuit breaker** (G9) live in a ``GeminiGate``, one per process
(``process_gate``), shared by every client the process builds: the reader's, the
chat client and the translator. Its limiter spaces calls ``60 * workers /
GEMINI_RPM`` seconds apart, so the workers together stay under ``GEMINI_RPM``.
``workers`` is the number of processes that share the key; it is 1 until
WORKER_REPLICAS exists (PRD 5 Q3 adds it in pull request 2, and passes it to
``process_gate`` then). After ``BREAKER_FAILURES`` failed requests in a row (429,
5xx or no connection) the breaker opens: for ``BREAKER_OPEN_SECONDS`` every call
fails at once with ``circuit_open``, then one call is let through as a probe; its
success closes the breaker, its failure opens it again. ``status()`` gives the
reader's state for the operator's Health tab (U9) and the outage clock (Q7).
Everything here is thread-safe: pages of a document are read in parallel (G8).

The gate's clock, sleep and random number are injected, so tests run with a fake
clock. The clock is ``time.monotonic`` by default, and ``read_page``'s deadline is
an absolute time on it (``now()`` gives the current one).

Cost: one ``read_page`` is one request (two with a repair), plus retries inside
its deadline; ``embed`` makes ceil(n / 32) requests for n texts. Checking a reply
is O(its size).
"""

import base64
import email.utils
import json
import logging
import math
import random as random_module
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from email.message import Message
from http.client import HTTPException, HTTPMessage
from typing import IO, Any

from kaagaz.ai import replies
from kaagaz.ai.replies import REPAIR, ModelOutputError, ModelUnavailable, parse_reply
from kaagaz.settings import MEDIA_RESOLUTIONS, THINKING_LEVELS, Settings, is_gemini_model

API_KEY_HEADER = "x-goog-api-key"

# Structured output (G3): the generationConfig fields that ask for JSON following a
# schema. Google's documents show more than one shape, so verify-gemini tries both
# against the live API (item schema_shape); STRUCTURED_OUTPUT is the one requests use.
# "A": responseMimeType with responseJsonSchema (a JSON Schema, which our schemas are).
# "B": responseFormat with a text type, a mime type and the schema.
STRUCTURED_OUTPUT_SHAPES: dict[str, Callable[[Mapping[str, Any]], dict[str, Any]]] = {
    "A": lambda schema: {"responseMimeType": "application/json", "responseJsonSchema": dict(schema)},
    "B": lambda schema: {
        "responseFormat": {"type": "text", "mimeType": "application/json", "schema": dict(schema)}
    },
}
STRUCTURED_OUTPUT = "A"

# Q8 (cost guard): the most output tokens, thinking included (Google counts thought
# tokens inside maxOutputTokens), one page read may take: Gemini 3 Pro's documented
# output limit. Vrushit sets the tighter cap from the PR 1 measure table (question 8);
# it is this one constant. A reply that reaches it ends MAX_TOKENS (output_truncated),
# and the reader reads the page in two halves (G6).
PAGE_MAX_OUTPUT_TOKENS = 65536
# chat_json and read_image think at this level: the card, the invoice windows, answers
# and the follow-up rewrite want a short exact reply. gemini-3.1-pro-preview has no
# "minimal" level (Google's thinking page), so "low" is the least it accepts.
CHAT_THINKING_LEVEL = "low"
# Thinking tokens count inside maxOutputTokens, so a chat_json call with a reply budget
# asks for the budget plus this much room to think; the reply itself is then held to
# the caller's budget (candidatesTokenCount), as the Ollama client holds it.
CHAT_THINKING_TOKENS = 8192

# Backoff (G6): attempt n waits a random time up to min(BACKOFF_MAX_SECONDS,
# BACKOFF_BASE_SECONDS * 2**n) ("full jitter"), or the server's Retry-After if longer.
BACKOFF_BASE_SECONDS = 1.0
BACKOFF_MAX_SECONDS = 30.0
# A rejected key (401, 403) fails every call at once for this long.
KEY_LATCH_SECONDS = 300.0
# The circuit breaker (G9).
BREAKER_FAILURES = 5
BREAKER_OPEN_SECONDS = 60.0

# Finish reasons that mean Google declined to answer (G6: reader_refused).
BLOCKED_FINISH_REASONS = frozenset(
    {"SAFETY", "RECITATION", "BLOCKLIST", "PROHIBITED_CONTENT", "SPII", "IMAGE_SAFETY"}
)
_RETRY_STATUSES = {429: "rate_limited"}  # and every 5xx: server_error
_KEY_STATUSES = frozenset({401, 403})
# A model ID goes into the URL path: letters, digits, dots, dashes and underscores only.
_MODEL_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}")
_LOOPBACK_HOSTS = frozenset({"localhost", "127.0.0.1", "::1"})
# Only these reply headers are kept by probe (verify-gemini's rate-limit item).
_RATE_HEADER = re.compile(r"(x-)?ratelimit.*|retry-after", re.IGNORECASE)
# An error reply's body is read up to this much, for its error.status word only.
_ERROR_BODY_BYTES = 64 * 1024
_ERROR_STATUS = re.compile(r"[A-Z][A-Z_]{0,63}")

# Breaker states, and the reader states status() gives.
_CLOSED, _OPEN, _HALF_OPEN = "closed", "open", "half_open"
WORKING, FAILING, DOWN, KEY_PROBLEM = "working", "failing", "down", "key_problem"

_log = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class PageReply:
    """One page read (G1): the JSON object, its tokens (G9) and Google's finish reason."""

    value: dict[str, Any]
    input_tokens: int
    output_tokens: int
    finish_reason: str


@dataclass(frozen=True, slots=True)
class Usage:
    """Tokens counted since the client was made: input, and output with thinking."""

    input_tokens: int
    output_tokens: int


@dataclass(frozen=True, slots=True)
class Probe:
    """One raw request's outcome, for verify-gemini only: the HTTP status (0 when no
    reply came), the error's ``status`` word (never its message), the rate-limit
    headers, the reply object of a 2xx reply, and the seconds it took."""

    status: int
    error_status: str | None
    headers: dict[str, str]
    reply: dict[str, Any] | None
    seconds: float


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """Refuses every redirect: urllib then raises the 3xx as an HTTPError."""

    def redirect_request(
        self,
        req: urllib.request.Request,
        fp: IO[bytes],
        code: int,
        msg: str,
        headers: HTTPMessage,
        newurl: str,
    ) -> urllib.request.Request | None:
        return None


def build_opener() -> urllib.request.OpenerDirector:
    """No proxy from the environment (an empty ProxyHandler) and no redirects."""
    return urllib.request.build_opener(urllib.request.ProxyHandler({}), _NoRedirect())


def media_resolution_level(name: str) -> str:
    """The API's level for a setting value: ``high`` gives ``MEDIA_RESOLUTION_HIGH``."""
    if name not in MEDIA_RESOLUTIONS:
        raise ValueError("unknown media resolution")
    return f"MEDIA_RESOLUTION_{name.upper()}"


def thinking_level(name: str) -> str:
    """The API's thinking level for a setting value: ``low`` gives ``LOW``."""
    if name not in THINKING_LEVELS:
        raise ValueError("unknown thinking level")
    return name.upper()


def image_part(png: bytes, media_resolution: str) -> dict[str, Any]:
    """An image part: the PNG inline, with its own media resolution (G2)."""
    return {
        "inline_data": {"mime_type": "image/png", "data": base64.b64encode(png).decode("ascii")},
        "media_resolution": {"level": media_resolution_level(media_resolution)},
    }


def page_request(
    *,
    png: bytes,
    instruction: str,
    schema: Mapping[str, Any] | None,
    media_resolution: str,
    thinking: str,
    max_output_tokens: int | None,
    shape: str = STRUCTURED_OUTPUT,
) -> dict[str, Any]:
    """A generateContent body for one picture: the instruction first, then the image."""
    config: dict[str, Any] = {"thinkingConfig": {"thinkingLevel": thinking_level(thinking)}}
    if max_output_tokens is not None:
        config["maxOutputTokens"] = max_output_tokens
    if schema is not None:
        config.update(STRUCTURED_OUTPUT_SHAPES[shape](schema))
    parts = [{"text": instruction}, image_part(png, media_resolution)]
    return {"contents": [{"role": "user", "parts": parts}], "generationConfig": config}


def _whole(value: object) -> int:
    """A token count, or 0 when it is missing or not a whole number."""
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else 0


def reply_usage(reply: Mapping[str, Any]) -> tuple[int, int]:
    """(input, output) tokens of one reply: prompt, and candidates plus thoughts."""
    usage = reply.get("usageMetadata")
    if not isinstance(usage, dict):
        return 0, 0
    output = _whole(usage.get("candidatesTokenCount")) + _whole(usage.get("thoughtsTokenCount"))
    return _whole(usage.get("promptTokenCount")), output


def reply_text(reply: Mapping[str, Any]) -> tuple[str, str]:
    """(the reply's text, its finish reason), or ``ModelOutputError`` when it was
    blocked, cut off or ended any other way than STOP. Thought parts are left out."""
    feedback = reply.get("promptFeedback")
    if isinstance(feedback, dict) and feedback.get("blockReason") not in (None, "BLOCK_REASON_UNSPECIFIED"):
        raise ModelOutputError("blocked")
    candidates = reply.get("candidates")
    if not (isinstance(candidates, list) and candidates and isinstance(candidates[0], dict)):
        raise ModelOutputError("invalid_output")
    candidate = candidates[0]
    finish = candidate.get("finishReason")
    if finish in BLOCKED_FINISH_REASONS:
        raise ModelOutputError("blocked")
    if finish == "MAX_TOKENS":
        raise ModelOutputError("output_truncated")
    if finish != "STOP":
        raise ModelOutputError("invalid_output")
    content = candidate.get("content")
    parts = content.get("parts") if isinstance(content, dict) else None
    if not isinstance(parts, list):
        raise ModelOutputError("invalid_output")
    texts = []
    for part in parts:
        if not isinstance(part, dict):
            raise ModelOutputError("invalid_output")
        text = part.get("text")
        if part.get("thought") is True or text is None:
            continue
        if not isinstance(text, str):
            raise ModelOutputError("invalid_output")
        texts.append(text)
    return "".join(texts), finish


def _retry_after(headers: "Message[str, str] | None", now: datetime) -> float | None:
    """Seconds the server asked us to wait (whole seconds or an HTTP date), or None."""
    value = headers.get("Retry-After") if headers is not None else None
    if not value:
        return None
    value = value.strip()
    if value.isdigit():
        return float(value)
    try:
        when = email.utils.parsedate_to_datetime(value)
    except (TypeError, ValueError):
        return None
    if when.tzinfo is None:
        return None
    return max(0.0, (when - now).total_seconds())


def _error_status(error: urllib.error.HTTPError) -> str | None:
    """The ``error.status`` word of an error reply (such as ``PERMISSION_DENIED``), or
    None. The body is read here and dropped: its message is never kept or logged."""
    try:
        raw = error.read(_ERROR_BODY_BYTES)
    except (OSError, HTTPException):
        return None
    finally:
        error.close()
    try:
        body = json.loads(raw)
    except (ValueError, RecursionError):
        return None
    inner = body.get("error") if isinstance(body, dict) else None
    status = inner.get("status") if isinstance(inner, dict) else None
    return status if isinstance(status, str) and _ERROR_STATUS.fullmatch(status) else None


@dataclass(slots=True)
class _Reply:
    """One HTTP exchange: status 0 means no reply (``failure`` says why)."""

    status: int
    headers: "Message[str, str] | None"
    body: dict[str, Any] | None
    failure: str = ""
    error_status: str | None = None


class GeminiGate:
    """What every Gemini call of one process shares (G9): the limiter, the circuit
    breaker, the key latch and the reader's status, with the clock, sleep and random
    number they use (tests inject them). ``process_gate`` gives the process's one gate,
    and every client ``build_model_client`` makes uses it, so reading, chat and Health
    see the same state. Thread-safe."""

    def __init__(
        self,
        settings: Settings,
        *,
        workers: int = 1,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
        random: Callable[[], float] = random_module.random,
        wall_clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        """``workers``: the processes sharing GEMINI_RPM (see the module docstring)."""
        if workers < 1:
            raise ValueError("workers must be at least 1")
        self.clock, self.sleep, self.random, self.wall_clock = clock, sleep, random, wall_clock
        self._lock = threading.Lock()
        # The limiter: calls start at least this many seconds apart.
        self.interval = 60.0 * workers / settings.gemini_rpm
        self._next_start = -math.inf
        self._breaker = _CLOSED
        self._failures = 0
        self._opened_at = 0.0
        self._probing = False
        self._down_since: float | None = None
        self._key_latched_at: float | None = None

    def status(self) -> tuple[str, float | None]:
        """(the reader's state, since when on the gate's clock) for Health (U9) and
        the outage clock (Q7). ``working`` (since None); ``failing``: the last requests
        failed, since the first of them; ``down``: the breaker is open or probing,
        since the first failure; ``key_problem``: a 401 or 403 within KEY_LATCH_SECONDS,
        since it came."""
        with self._lock:
            if self._key_latched_at is not None and self.clock() < self._key_latched_at + KEY_LATCH_SECONDS:
                return KEY_PROBLEM, self._key_latched_at
            if self._breaker != _CLOSED:
                return DOWN, self._down_since
            if self._failures:
                return FAILING, self._down_since
            return WORKING, None

    def admit(self) -> None:
        """Raises at once while the key is latched or the breaker is open; lets one
        probe through once the breaker has been open BREAKER_OPEN_SECONDS."""
        with self._lock:
            now = self.clock()
            if self._key_latched_at is not None and now < self._key_latched_at + KEY_LATCH_SECONDS:
                raise ModelUnavailable("key_rejected")
            if self._breaker == _OPEN and now >= self._opened_at + BREAKER_OPEN_SECONDS:
                self._breaker = _HALF_OPEN
            if self._breaker == _OPEN or (self._breaker == _HALF_OPEN and self._probing):
                raise ModelUnavailable("circuit_open")
            if self._breaker == _HALF_OPEN:
                self._probing = True

    def take_slot(self, deadline: float) -> float:
        """When this request may start under the limiter; the slot is booked only when
        it starts before ``deadline`` (``ModelUnavailable("rate_limited")`` if not)."""
        with self._lock:
            start = max(self.clock(), self._next_start)
            if start >= deadline:
                self._probing = False
                raise ModelUnavailable("rate_limited")
            self._next_start = start + self.interval
            return start

    def release_probe(self) -> None:
        with self._lock:
            self._probing = False

    def record(self, *, failed: bool) -> None:
        """One request's outcome: any reply closes the breaker; a failure (429, 5xx,
        no connection) counts towards opening it."""
        with self._lock:
            self._probing = False
            if not failed:
                self._breaker, self._failures, self._down_since = _CLOSED, 0, None
                self._key_latched_at = None
                return
            now = self.clock()
            self._failures += 1
            if self._down_since is None:
                self._down_since = now
            if self._breaker == _HALF_OPEN or self._failures >= BREAKER_FAILURES:
                if self._breaker != _OPEN:
                    _log.warning("gemini circuit open", extra={"code": "circuit_open"})
                self._breaker, self._opened_at = _OPEN, now

    def latch_key(self) -> None:
        """A 401 or 403: every call fails at once for KEY_LATCH_SECONDS; logged once."""
        with self._lock:
            self._probing = False
            first = self._key_latched_at is None
            self._key_latched_at = self.clock()
        if first:
            _log.error("gemini key rejected", extra={"code": "key_rejected"})


_PROCESS_GATES: dict[tuple[str, int, int], GeminiGate] = {}
_PROCESS_GATES_LOCK = threading.Lock()


def process_gate(settings: Settings, *, workers: int = 1) -> GeminiGate:
    """This process's one gate for GEMINI_BASE_URL at GEMINI_RPM over ``workers``, on
    the real clock: every client built for the product shares it."""
    key = (settings.gemini_base_url, settings.gemini_rpm, workers)
    with _PROCESS_GATES_LOCK:
        gate = _PROCESS_GATES.get(key)
        if gate is None:
            gate = _PROCESS_GATES[key] = GeminiGate(settings, workers=workers)
        return gate


class GeminiClient:
    """``ModelClient`` over the Gemini REST API, with ``read_page`` for the reader (G1, G9)."""

    def __init__(
        self,
        settings: Settings,
        *,
        chat_timeout_seconds: float | None = None,
        gate: GeminiGate | None = None,
    ) -> None:
        """``chat_timeout_seconds`` is for ``chat_json`` only (the worker passes
        MODEL_EXTRACT_TIMEOUT_SECONDS); ``embed`` and ``read_image`` use
        MODEL_TIMEOUT_SECONDS. ``gate``: the limiter and breaker to share; without
        one the client has its own (``process_gate`` gives the shared one)."""
        if settings.gemini_api_key is None:
            raise ValueError("GEMINI_API_KEY is not set")
        parts = urllib.parse.urlsplit(settings.gemini_base_url)
        secure = parts.scheme == "https" or (parts.scheme == "http" and parts.hostname in _LOOPBACK_HOSTS)
        if not (secure and parts.hostname) or parts.username or parts.password or parts.query:
            raise ValueError("gemini_base_url must be https (http only on a loopback host)")
        others = (settings.model_chat, settings.model_embed)
        for model in (settings.ocr_model, *filter(is_gemini_model, others)):
            if _MODEL_ID.fullmatch(model) is None:
                raise ValueError("a model ID may hold letters, digits, dots, dashes and underscores only")
        self._key = settings.gemini_api_key
        self._https = parts.scheme == "https"
        self._host_path = settings.gemini_base_url.rstrip("/").split("://", 1)[1]
        self._timeout = settings.model_timeout_seconds
        self._chat_timeout = self._timeout if chat_timeout_seconds is None else chat_timeout_seconds
        self._dim = settings.embedding_dim
        self._media_resolution = settings.ocr_media_resolution
        self.ocr_model = settings.ocr_model
        self.chat_model = settings.model_chat
        self.embed_model = settings.model_embed
        self.gate = GeminiGate(settings) if gate is None else gate
        self._opener = build_opener()
        self._lock = threading.Lock()
        self._input_tokens = 0
        self._output_tokens = 0

    def __repr__(self) -> str:
        return f"GeminiClient(ocr_model={self.ocr_model!r}, chat_model={self.chat_model!r})"

    @property
    def interval(self) -> float:
        """The limiter's spacing between calls, in seconds."""
        return self.gate.interval

    def now(self) -> float:
        """The gate's clock (``time.monotonic`` unless a test injected one)."""
        return self.gate.clock()

    def usage(self) -> Usage:
        """Tokens counted from this client's replies since it was made (G9)."""
        with self._lock:
            return Usage(self._input_tokens, self._output_tokens)

    def status(self) -> tuple[str, float | None]:
        """The gate's ``status()``."""
        return self.gate.status()

    # The ModelClient protocol.

    def embed(self, texts: list[str]) -> list[list[float]]:
        """One vector per text, in order, ``EMBEDDING_DIM`` long (M5: output
        dimensionality 1024, so halfvec(1024) does not change). batchEmbedContents,
        32 texts a request."""
        vectors: list[list[float]] = []
        path = f"/models/{self.embed_model}:batchEmbedContents"
        for start in range(0, len(texts), replies.EMBED_BATCH_SIZE):
            batch = texts[start : start + replies.EMBED_BATCH_SIZE]
            requests = [
                {
                    "model": f"models/{self.embed_model}",
                    "content": {"parts": [{"text": text}]},
                    "outputDimensionality": self._dim,
                }
                for text in batch
            ]
            reply = self._call(path, {"requests": requests}, self.now() + self._timeout)
            embeddings = reply.get("embeddings")
            if not isinstance(embeddings, list):
                raise ModelUnavailable("bad_response")
            if len(embeddings) != len(batch):
                raise ModelOutputError("embedding_count")
            vectors.extend(self._vector(item) for item in embeddings)
        return vectors

    def chat_json(
        self, *, system: str, user: str, schema: dict[str, Any], max_tokens: int | None = None
    ) -> dict[str, Any]:
        """One JSON object following ``schema``, from MODEL_CHAT. A first reply that does
        not follow it goes back once for a repair; a second bad one raises
        ``invalid_output``. A reply longer than ``max_tokens`` (thinking not counted)
        raises ``output_truncated`` with no repair."""
        config: dict[str, Any] = {
            "thinkingConfig": {"thinkingLevel": thinking_level(CHAT_THINKING_LEVEL)},
            **STRUCTURED_OUTPUT_SHAPES[STRUCTURED_OUTPUT](schema),
        }
        if max_tokens is not None:
            config["maxOutputTokens"] = max_tokens + CHAT_THINKING_TOKENS
        contents: list[dict[str, Any]] = [{"role": "user", "parts": [{"text": user}]}]
        body = {
            "systemInstruction": {"parts": [{"text": system}]},
            "contents": contents,
            "generationConfig": config,
        }
        path = f"/models/{self.chat_model}:generateContent"
        content = self._chat_reply(path, body, max_tokens)
        value, problem = parse_reply(content, schema)
        if value is not None:
            return value
        contents.append({"role": "model", "parts": [{"text": content}]})
        contents.append({"role": "user", "parts": [{"text": REPAIR.format(problem=problem)}]})
        content = self._chat_reply(path, body, max_tokens)
        value, _ = parse_reply(content, schema)
        if value is None:
            raise ModelOutputError("invalid_output")
        return value

    def read_image(self, *, png: bytes, instruction: str) -> str:
        """The text OCR_MODEL reads in ``png`` (a value's crop), at OCR_MEDIA_RESOLUTION."""
        body = page_request(
            png=png,
            instruction=instruction,
            schema=None,
            media_resolution=self._media_resolution,
            thinking=CHAT_THINKING_LEVEL,
            max_output_tokens=None,
        )
        reply = self._call(f"/models/{self.ocr_model}:generateContent", body, self.now() + self._timeout)
        text, _ = reply_text(reply)
        return text.strip()

    # The reader's call (G1, G2, G3).

    def read_page(
        self,
        *,
        png: bytes,
        instruction: str,
        schema: Mapping[str, Any],
        media_resolution: str,
        thinking_level: str,
        deadline: float,
    ) -> PageReply:
        """One page read by OCR_MODEL as a JSON object following ``schema``, before
        ``deadline`` (an absolute time on ``now()``; every retry fits inside it). A first
        reply that does not follow the schema is sent back once for a repair, with the
        page again; the tokens of both calls are counted."""
        body = page_request(
            png=png,
            instruction=instruction,
            schema=schema,
            media_resolution=media_resolution,
            thinking=thinking_level,
            max_output_tokens=PAGE_MAX_OUTPUT_TOKENS,
        )
        path = f"/models/{self.ocr_model}:generateContent"
        reply = self._call(path, body, deadline)
        input_tokens, output_tokens = reply_usage(reply)
        content, finish = reply_text(reply)
        value, problem = parse_reply(content, schema)
        if value is None:
            body["contents"].append({"role": "model", "parts": [{"text": content}]})
            body["contents"].append({"role": "user", "parts": [{"text": REPAIR.format(problem=problem)}]})
            reply = self._call(path, body, deadline)
            more_in, more_out = reply_usage(reply)
            input_tokens, output_tokens = input_tokens + more_in, output_tokens + more_out
            content, finish = reply_text(reply)
            value, _ = parse_reply(content, schema)
            if value is None:
                raise ModelOutputError("invalid_output")
        return PageReply(value, input_tokens, output_tokens, finish)

    # verify-gemini's raw request.

    def probe(self, path: str, body: Mapping[str, Any], *, send_key: bool = True, timeout: float) -> Probe:
        """One request with no retry, limiter, breaker or latch, for verify-gemini only:
        what the live API answers to ``body`` (``send_key=False`` sends no key)."""
        started = self.now()
        reply = self._send(path, body, timeout, send_key=send_key)
        headers = {}
        if reply.headers is not None:
            headers = {
                name.lower(): value for name, value in reply.headers.items() if _RATE_HEADER.fullmatch(name)
            }
        return Probe(reply.status, reply.error_status, headers, reply.body, self.now() - started)

    # Internals.

    def _chat_reply(self, path: str, body: dict[str, Any], max_tokens: int | None) -> str:
        reply = self._call(path, body, self.now() + self._chat_timeout)
        text, _ = reply_text(reply)
        usage = reply.get("usageMetadata")
        reply_tokens = _whole(usage.get("candidatesTokenCount")) if isinstance(usage, dict) else 0
        if max_tokens is not None and reply_tokens > max_tokens:
            raise ModelOutputError("output_truncated")
        return text

    def _vector(self, item: object) -> list[float]:
        values = item.get("values") if isinstance(item, dict) else None
        if not isinstance(values, list):
            raise ModelOutputError("embedding_value")
        if len(values) != self._dim:
            raise ModelOutputError("embedding_dim")
        vector: list[float] = []
        for number in values:
            if isinstance(number, bool) or not isinstance(number, int | float) or not math.isfinite(number):
                raise ModelOutputError("embedding_value")
            vector.append(float(number))
        return vector

    def _call(self, path: str, body: Mapping[str, Any], deadline: float) -> dict[str, Any]:
        """One request with the latch, the breaker, the limiter and retries (G6, G9).
        Returns the reply object of a 200; raises a coded error otherwise."""
        gate = self.gate
        attempt = 0
        while True:
            gate.admit()
            start = gate.take_slot(deadline)
            if start > gate.clock():
                gate.sleep(start - gate.clock())
            remaining = deadline - gate.clock()
            if remaining <= 0:
                gate.release_probe()
                raise ModelUnavailable("timeout")
            reply = self._send(path, body, remaining)
            if 200 <= reply.status < 300:
                gate.record(failed=False)
                if reply.body is None:
                    raise ModelUnavailable(reply.failure or "bad_response")
                self._count(reply.body)
                return reply.body
            if reply.status in _KEY_STATUSES:
                gate.latch_key()
                raise ModelUnavailable("key_rejected")
            code = _RETRY_STATUSES.get(reply.status) or ("server_error" if reply.status >= 500 else None)
            if reply.status == 0:
                code = reply.failure
            if code is None:  # the server answered: it is up, but this request is wrong
                gate.record(failed=False)
                if reply.status == 400:
                    raise ModelOutputError("bad_request")
                if reply.status == 404:
                    raise ModelUnavailable("model_not_found")
                if 300 <= reply.status < 400:
                    raise ModelUnavailable("redirect_refused")
                raise ModelUnavailable("http_error")
            gate.record(failed=True)
            if code == "timeout":
                raise ModelUnavailable("timeout")
            wait = gate.random() * min(BACKOFF_MAX_SECONDS, BACKOFF_BASE_SECONDS * 2**attempt)
            asked = _retry_after(reply.headers, gate.wall_clock())
            if asked is not None:
                wait = max(wait, asked)
            if gate.clock() + wait >= deadline:
                raise ModelUnavailable(code)
            gate.sleep(wait)
            attempt += 1

    def _count(self, reply: Mapping[str, Any]) -> None:
        input_tokens, output_tokens = reply_usage(reply)
        with self._lock:
            self._input_tokens += input_tokens
            self._output_tokens += output_tokens

    def _send(self, path: str, body: Mapping[str, Any], timeout: float, *, send_key: bool = True) -> _Reply:
        """One POST. Never raises for HTTP or the network: the outcome is a ``_Reply``."""
        data = json.dumps(body).encode("utf-8")
        headers = {"Content-Type": "application/json"}
        # The scheme is spelled out, so nothing but https (or http on a loopback host,
        # checked in __init__) can be opened.
        if self._https:
            request = urllib.request.Request(f"https://{self._host_path}{path}", data, headers, method="POST")
        else:
            request = urllib.request.Request(f"http://{self._host_path}{path}", data, headers, method="POST")
        if send_key:
            request.add_unredirected_header(API_KEY_HEADER, self._key)
        try:
            with self._opener.open(request, timeout=timeout) as response:
                raw = response.read(replies.MAX_RESPONSE_BYTES + 1)
                status, reply_headers = response.status, response.headers
        except urllib.error.HTTPError as error:
            return _Reply(error.code, error.headers, None, error_status=_error_status(error))
        except urllib.error.URLError as error:
            failure = "timeout" if isinstance(error.reason, TimeoutError) else "unreachable"
            return _Reply(0, None, None, failure)
        except TimeoutError:
            return _Reply(0, None, None, "timeout")
        except (OSError, HTTPException):
            return _Reply(0, None, None, "unreachable")
        if len(raw) > replies.MAX_RESPONSE_BYTES:
            return _Reply(status, reply_headers, None, "response_too_large")
        try:
            reply = json.loads(raw)
        except (ValueError, RecursionError):
            return _Reply(status, reply_headers, None, "bad_response")
        if not isinstance(reply, dict):
            return _Reply(status, reply_headers, None, "bad_response")
        return _Reply(status, reply_headers, reply)
