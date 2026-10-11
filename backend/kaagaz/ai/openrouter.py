"""The model client (PRD 5 G9): ``ModelClient`` over OpenRouter's REST API.

Every model PRD 5 names goes through OpenRouter (https://openrouter.ai/api/v1), Tushar's
decision of 8 October 2026 (council ``2026-10-08-prd5-openrouter.md``). ``OpenRouterClient``
implements ``ModelClient`` (``chat_json`` and ``embed``), and adds ``read_page`` for the
Gemini reader (G1, G2, G3). It uses exactly the models in the settings: ``OCR_MODEL``
reads pages (``read_page``), ``MODEL_CHAT`` answers ``chat_json`` and ``MODEL_EMBED``
embeds. No code here picks or switches a model.

**The key is a secret.** It goes in the ``Authorization: Bearer`` header only, added with
``add_unredirected_header`` so it is never copied onto a redirect, and redirects are
refused anyway. Never in the URL, a log, an error or a repr. The opener ignores
``HTTP_PROXY`` style variables, and the base URL is https (plain http only on a loopback
host, for the test fake: the settings refuse anything else, and so does this client). No
HTTP-Referer or X-Title header is sent. Error replies are never logged or kept: their
``error.message``, ``metadata.flagged_input`` and ``metadata.raw`` can hold page text and
are never read. Only ``metadata.error_type`` and ``metadata.provider_name`` are kept, and
only when they look like a short code (``_CODE``); a moderation ``reasons`` list counts as
present or not, its words are never kept. Every error this module raises carries a short
code only, raised ``from None``.

**Every request** carries ``PROVIDER`` (not a setting): Google Vertex only, no fallback to
another provider, every parameter honoured, no data collection, zero data retention. A page
read may name another provider (model choice, council 11 Oct 2026): ``provider(slug)`` is
the same block with ``only`` that one slug, and the client refuses a slug outside
OPENROUTER_PROVIDERS (``ProviderNotAllowed``).

**The request** (``POST /chat/completions``, ``stream: false``, G2). A system prompt is
``messages[0]`` with role system. A picture is a user message whose content is the
instruction text first, then the image as a ``data:image/png;base64`` URL. The page's
media resolution goes the way ``MEDIA_RESOLUTION_SHAPE`` says (``image_url.detail``, low or
high). Thinking is ``reasoning: {effort, exclude: true}``: the effort is the setting, and
the thinking itself never comes back. The output cap is ``max_completion_tokens``.
Structured output is ``response_format`` ``json_schema`` named ``page`` with ``strict:
STRICT_SCHEMA``. Temperature is left at the default (PRD 5 section 14). A repair turn sends
the first reply back with role assistant. ``python -m kaagaz.cli verify-models`` checks
each of these against the live API.

**Usage** (G9). Input tokens are ``usage.prompt_tokens``; output tokens are
``usage.completion_tokens``; ``completion_tokens_details.reasoning_tokens`` is the thinking
part of it. ``read_page`` returns them per page; ``usage()`` keeps the process's totals and
the summed ``usage.cost`` when replies carry one.

**Failures** (G6), each a code:

* 408, 429 and 5xx (500, 502, 503, 524, 529, ...), and a 200 whose ``finish_reason`` is
  ``error``: retried with exponential backoff and full jitter, honouring ``Retry-After``,
  inside the call's deadline; then ``ModelUnavailable`` ``timeout``, ``rate_limited`` or
  ``server_error``. A refused or dropped connection is retried the same way
  (``unreachable``); a reply that does not come before the deadline is ``timeout``.
* 400, 413, 422: ``ModelOutputError("bad_request")``: this request will never work, the
  reader reads the page locally (F2).
* 401, 402 (no credits) and any 403 that is not a refusal: ``ModelUnavailable
  ("key_rejected")``, logged at error once (code ``no_credits`` for a 402), and for
  ``KEY_LATCH_SECONDS`` every call fails at once with the same code, without a request: a
  wrong key or spent credit is a person's fix, never a tight loop.
* A 403 refusal or moderation block (``metadata.error_type`` in ``REFUSAL_ERROR_TYPES``, or
  a ``reasons`` list): ``ModelOutputError("blocked")``, no latch.
* 404: ``ModelUnavailable("model_not_found")`` (a wrong model ID in .env).
* A redirect: ``ModelUnavailable("redirect_refused")``; any other status ``http_error``.
* A reply that was blocked (``message.refusal``, ``finish_reason`` ``content_filter``, or a
  ``native_finish_reason`` in ``BLOCKED_FINISH_REASONS``): ``ModelOutputError("blocked")``.
* ``finish_reason`` ``length``: ``ModelOutputError("output_truncated")``, with no repair
  retry (a cut reply would be cut again).
* Every ``ModelOutputError`` raised after a reply carries the tokens billed for it
  (``input_tokens``, ``output_tokens``: this reply's usage, plus the first reply's when a
  repair was asked for), so a blocked, cut off or invalid reply is still counted (G9).
* Any other finish reason, or none: ``ModelOutputError("invalid_output")``.
* JSON that does not follow the schema: one repair retry, then
  ``ModelOutputError("invalid_output")``.

**Rate limit and circuit breaker** (G9) live in a ``ModelGate``, one per process
(``process_gate``), shared by every client the process builds: the reader's, the chat
client and the translator. Its limiter spaces calls ``60 * workers / MODEL_RPM`` seconds
apart, so the workers together stay under ``MODEL_RPM``. ``workers`` is the number of
processes that share the key: WORKER_REPLICAS copies of the worker plus the API (PRD 5 Q3,
V7), which ``process_gate`` uses unless told otherwise. After ``BREAKER_FAILURES`` failed
requests in a row (408, 429, 5xx, a mid-reply error or no connection) the breaker opens:
for ``BREAKER_OPEN_SECONDS`` every call fails at once with ``circuit_open``, then one call
is let through as a probe; its success closes the breaker, its failure opens it again.
``status()`` gives the reader's state for the operator's Health tab (U9) and the outage
clock (Q7). Everything here is thread-safe: pages of a document are read in parallel (G8).

The gate's clock, sleep and random number are injected, so tests run with a fake clock.
The clock is ``time.monotonic`` by default, and ``read_page``'s deadline is an absolute
time on it (``now()`` gives the current one).

Cost: one ``read_page`` is one request (two with a repair), plus retries inside its
deadline; ``embed`` makes ceil(n / 32) requests for n texts. Checking a reply is O(its
size).
"""

import base64
import copy
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
from kaagaz.settings import (
    DEFAULT_PROVIDER,
    MEDIA_RESOLUTION_SHAPE,
    MEDIA_RESOLUTIONS,
    PROVIDER_SLUG,
    SENDABLE_MEDIA_RESOLUTIONS,
    THINKING_LEVELS,
    Settings,
    is_gemini_model,
    valid_model_id,
)

API_KEY_HEADER = "Authorization"
CHAT_PATH = "/chat/completions"
EMBEDDINGS_PATH = "/embeddings"
KEY_PATH = "/key"

# Sent on every request, chat and embeddings alike (council 8 Oct 2026, security). Not a
# setting: Google Vertex only, never another provider, every parameter honoured or the
# request fails, no data collection and zero data retention.
PROVIDER: Mapping[str, Any] = {
    "only": ["google-vertex"],
    "allow_fallbacks": False,
    "require_parameters": True,
    "data_collection": "deny",
    "zdr": True,
}

# Embeddings drop only require_parameters: OpenRouter lists no supported parameters for any
# embedding endpoint (gemini-embedding-2 included), so requiring them matches no provider
# and every call is 404. Vertex only, no fallback, no data collection and zero data
# retention still hold, and embed() refuses any vector that is not EMBEDDING_DIM long.
EMBED_PROVIDER: Mapping[str, Any] = {**PROVIDER, "require_parameters": False}

# Google Vertex embeds one text per request: a request with two or more texts is served only
# by Google AI Studio, which EMBED_PROVIDER refuses (404 "No allowed providers"), found on the
# server 9 Oct 2026. So every text is its own request, each counted by the rate limiter.
# ponytail: one call per piece; raise MODEL_RPM for indexing speed, or batch again if Vertex
# ever serves batches.
EMBED_TEXTS_PER_REQUEST = 1

# Structured output (G3): response_format json_schema with strict set to this. Whether
# Google honours strict through OpenRouter is checked by verify-models (strict_schema).
STRICT_SCHEMA = True
# The schema's name in response_format.
SCHEMA_NAME = "page"

# Q8 (cost guard): an initial page read's output ceiling, thinking included. The PR 1
# measurement may set a tighter value; it is sent as max_completion_tokens. A reply that
# reaches it ends "length" (output_truncated), and the reader reads the page in two halves.
PAGE_MAX_OUTPUT_TOKENS = 32768
# chat_json thinks at this effort: the card, the invoice windows, answers
# and the follow-up rewrite want a short exact reply. gemini-3.1-pro-preview has no
# "minimal" level (Google's thinking page), so "low" is the least it accepts.
CHAT_THINKING_LEVEL = "low"
# A chat_json call with a reply budget asks for the budget plus this much room to think;
# the reply itself (completion_tokens less reasoning_tokens) is then held to the caller's
# budget.
CHAT_THINKING_TOKENS = 8192

# Backoff (G6): attempt n waits a random time up to min(BACKOFF_MAX_SECONDS,
# BACKOFF_BASE_SECONDS * 2**n) ("full jitter"), or the server's Retry-After if longer.
BACKOFF_BASE_SECONDS = 1.0
BACKOFF_MAX_SECONDS = 30.0
# A rejected key (401, 402, a 403 that is not a refusal) fails every call at once for this long.
KEY_LATCH_SECONDS = 300.0
# The circuit breaker (G9).
BREAKER_FAILURES = 5
BREAKER_OPEN_SECONDS = 60.0

# Google's native finish reasons that mean it declined to answer (G6: reader_refused).
BLOCKED_FINISH_REASONS = frozenset(
    {
        "SAFETY",
        "RECITATION",
        "BLOCKLIST",
        "PROHIBITED_CONTENT",
        "SPII",
        "IMAGE_SAFETY",
        "IMAGE_PROHIBITED_CONTENT",
        "IMAGE_RECITATION",
        "LANGUAGE",
    }
)
# A 403 with one of these metadata.error_type values is a refusal, not a key problem.
REFUSAL_ERROR_TYPES = frozenset({"refusal", "content_policy_violation"})
# Statuses retried with backoff, and the code once the deadline leaves no room. Every
# other 5xx is retried as server_error too.
_RETRY_STATUSES = {408: "timeout", 429: "rate_limited"}
_BAD_REQUEST_STATUSES = frozenset({400, 413, 422})
_KEY_STATUSES = {401: "key_rejected", 402: "no_credits", 403: "key_rejected"}
_LOOPBACK_HOSTS = frozenset({"localhost", "127.0.0.1", "::1"})
# Only these reply headers are kept by probe (verify-models).
_RATE_HEADER = re.compile(r"(x-)?ratelimit.*|retry-after", re.IGNORECASE)
# An error reply's body is read up to this much, for its two short codes only.
_ERROR_BODY_BYTES = 64 * 1024
# What a kept error_type or provider_name may look like: a short lower-case code, never text.
_CODE = re.compile(r"[a-z0-9_.-]{1,64}")

# Breaker states, and the reader states status() gives.
_CLOSED, _OPEN, _HALF_OPEN = "closed", "open", "half_open"
WORKING, FAILING, DOWN, KEY_PROBLEM = "working", "failing", "down", "key_problem"

_log = logging.getLogger(__name__)


class ProviderNotAllowed(ValueError):
    """A page read named a provider outside OPENROUTER_PROVIDERS, the processors Vrushit
    approved: nothing is sent. The message is the code ``provider_not_allowed`` only."""


@dataclass(frozen=True, slots=True)
class PageReply:
    """One page read (G1): the JSON object, its tokens (G9) and the finish reason (Google's
    native one when OpenRouter passes it on, else OpenRouter's)."""

    value: dict[str, Any]
    input_tokens: int
    output_tokens: int
    finish_reason: str


@dataclass(frozen=True, slots=True)
class Usage:
    """Tokens counted since the client was made: input, and output with thinking; and the
    summed cost in credits, or None when no reply carried one."""

    input_tokens: int
    output_tokens: int
    cost: float | None = None


@dataclass(frozen=True, slots=True)
class Probe:
    """One raw request's outcome, for verify-models only: the HTTP status (0 when no reply
    came), the error's ``metadata.error_type`` and ``provider_name`` (codes only, never its
    message), the rate-limit headers, the reply object of a 2xx reply, and the seconds it
    took."""

    status: int
    error_type: str | None
    provider_name: str | None
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


def provider(slug: str = DEFAULT_PROVIDER, *, embeddings: bool = False) -> dict[str, Any]:
    """A fresh copy of ``PROVIDER`` (``EMBED_PROVIDER`` for /embeddings) for one request body,
    with ``only`` the one provider ``slug``: no fallback, every parameter honoured, no data
    collection, zero data retention. A slug that is not ``PROVIDER_SLUG`` is refused."""
    if PROVIDER_SLUG.fullmatch(slug) is None:
        raise ValueError("a provider slug is lower-case letters, digits and dashes, at most 64")
    block = copy.deepcopy(dict(EMBED_PROVIDER if embeddings else PROVIDER))
    block["only"] = [slug]
    return block


def detail_level(name: str) -> str:
    """The ``image_url.detail`` for a media resolution setting (``MEDIA_RESOLUTION_SHAPE``
    "detail"): low or high. Any other level is refused, never sent as another one."""
    if name not in MEDIA_RESOLUTIONS:
        raise ValueError("unknown media resolution")
    if name not in SENDABLE_MEDIA_RESOLUTIONS[MEDIA_RESOLUTION_SHAPE]:
        raise ValueError("this media resolution cannot be sent with image_url.detail")
    return name


def reasoning(name: str) -> dict[str, Any]:
    """The ``reasoning`` field for a thinking level setting: its effort, thinking left out."""
    if name not in THINKING_LEVELS:
        raise ValueError("unknown thinking level")
    return {"effort": name, "exclude": True}


def response_format(schema: Mapping[str, Any], *, strict: bool = STRICT_SCHEMA) -> dict[str, Any]:
    """The ``response_format`` asking for JSON that follows ``schema`` (G3)."""
    return {
        "type": "json_schema",
        "json_schema": {"name": SCHEMA_NAME, "strict": strict, "schema": dict(schema)},
    }


def image_content(png: bytes, instruction: str, media_resolution: str | None) -> list[dict[str, Any]]:
    """A user message's content: the instruction first, then the PNG as a data URL with its
    ``detail`` (G2). ``media_resolution`` None sends no detail (verify-models only)."""
    image: dict[str, Any] = {"url": "data:image/png;base64," + base64.b64encode(png).decode("ascii")}
    if media_resolution is not None:
        image["detail"] = detail_level(media_resolution)
    return [{"type": "text", "text": instruction}, {"type": "image_url", "image_url": image}]


def chat_request(
    *,
    model: str,
    messages: list[dict[str, Any]],
    thinking: str,
    schema: Mapping[str, Any] | None,
    max_output_tokens: int | None,
    strict: bool = STRICT_SCHEMA,
    provider_slug: str = DEFAULT_PROVIDER,
) -> dict[str, Any]:
    """A /chat/completions body: the model, the messages, the thinking effort, the output
    cap, the schema and ``provider(provider_slug)``."""
    body: dict[str, Any] = {
        "model": model,
        "messages": messages,
        "stream": False,
        "reasoning": reasoning(thinking),
        "provider": provider(provider_slug),
    }
    if max_output_tokens is not None:
        body["max_completion_tokens"] = max_output_tokens
    if schema is not None:
        body["response_format"] = response_format(schema, strict=strict)
    return body


def page_request(
    *,
    model: str,
    png: bytes,
    instruction: str,
    schema: Mapping[str, Any] | None,
    media_resolution: str | None,
    thinking: str,
    max_output_tokens: int | None,
    strict: bool = STRICT_SCHEMA,
    provider_slug: str = DEFAULT_PROVIDER,
) -> dict[str, Any]:
    """A /chat/completions body for one picture: one user message, the instruction first,
    then the image."""
    content = image_content(png, instruction, media_resolution)
    return chat_request(
        model=model,
        messages=[{"role": "user", "content": content}],
        thinking=thinking,
        schema=schema,
        max_output_tokens=max_output_tokens,
        strict=strict,
        provider_slug=provider_slug,
    )


def _whole(value: object) -> int:
    """A token count, or 0 when it is missing or not a whole number."""
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else 0


def _usage(reply: Mapping[str, Any]) -> Mapping[str, Any]:
    usage = reply.get("usage")
    return usage if isinstance(usage, dict) else {}


def reply_usage(reply: Mapping[str, Any]) -> tuple[int, int]:
    """(input, output) tokens of one reply: prompt_tokens and completion_tokens."""
    usage = _usage(reply)
    return _whole(usage.get("prompt_tokens")), _whole(usage.get("completion_tokens"))


def reasoning_tokens(reply: Mapping[str, Any]) -> int:
    """The thinking tokens of one reply (completion_tokens_details.reasoning_tokens), or 0."""
    details = _usage(reply).get("completion_tokens_details")
    return _whole(details.get("reasoning_tokens")) if isinstance(details, dict) else 0


def reply_cost(reply: Mapping[str, Any]) -> float | None:
    """The reply's ``usage.cost`` in credits, or None when it carries none."""
    cost = _usage(reply).get("cost")
    if isinstance(cost, bool) or not isinstance(cost, int | float) or not math.isfinite(cost) or cost < 0:
        return None
    return float(cost)


def _choice(reply: Mapping[str, Any]) -> Mapping[str, Any]:
    choices = reply.get("choices")
    if not (isinstance(choices, list) and choices and isinstance(choices[0], dict)):
        raise ModelOutputError("invalid_output")
    return choices[0]


def finish_reasons(reply: Mapping[str, Any]) -> tuple[str | None, str | None]:
    """(OpenRouter's finish_reason, Google's native_finish_reason) of the first choice."""
    choices = reply.get("choices")
    if not (isinstance(choices, list) and choices and isinstance(choices[0], dict)):
        return None, None
    finish, native = choices[0].get("finish_reason"), choices[0].get("native_finish_reason")
    return (finish if isinstance(finish, str) else None, native if isinstance(native, str) else None)


def failed_mid_reply(reply: Mapping[str, Any]) -> bool:
    """The reply is a 200 whose generation failed part way (``finish_reason`` error)."""
    return finish_reasons(reply)[0] == "error"


def reply_text(reply: Mapping[str, Any]) -> tuple[str, str]:
    """(the reply's text, its finish reason: native when given, else OpenRouter's), or
    ``ModelOutputError`` when it was blocked, cut off or ended any other way than stop;
    ``ModelUnavailable("server_error")`` when it failed part way."""
    choice = _choice(reply)
    finish, native = finish_reasons(reply)
    message = choice.get("message")
    if not isinstance(message, dict):
        raise ModelOutputError("invalid_output")
    if message.get("refusal") is not None:
        raise ModelOutputError("blocked")
    if finish == "content_filter" or native in BLOCKED_FINISH_REASONS:
        raise ModelOutputError("blocked")
    if finish == "error":
        raise ModelUnavailable("server_error")
    if finish == "length":
        raise ModelOutputError("output_truncated")
    if finish != "stop":
        raise ModelOutputError("invalid_output")
    content = message.get("content")
    if not isinstance(content, str):
        raise ModelOutputError("invalid_output")
    return content, native or finish


def _billed_text(reply: Mapping[str, Any], before: tuple[int, int]) -> tuple[str, str]:
    """``reply_text``; its ``ModelOutputError`` carries this reply's usage plus ``before``
    (the tokens of the calls made before it for the same answer), so a blocked or cut off
    reply is still counted (G9)."""
    try:
        return reply_text(reply)
    except ModelOutputError as error:
        input_tokens, output_tokens = reply_usage(reply)
        raise replies.billed(error, before[0] + input_tokens, before[1] + output_tokens) from None


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


def _code(value: object) -> str | None:
    return value if isinstance(value, str) and _CODE.fullmatch(value) else None


@dataclass(frozen=True, slots=True)
class _ErrorCodes:
    """What is kept of an error reply: two short codes and whether it was a moderation block."""

    error_type: str | None = None
    provider_name: str | None = None
    moderated: bool = False


def _error_codes(error: urllib.error.HTTPError) -> _ErrorCodes:
    """The ``metadata.error_type`` and ``provider_name`` codes of an error reply, and whether
    it holds a moderation ``reasons`` list. The body is read here and dropped: its message,
    ``flagged_input`` and ``raw`` are never looked at, kept or logged."""
    try:
        raw = error.read(_ERROR_BODY_BYTES)
    except (OSError, HTTPException):
        return _ErrorCodes()
    finally:
        error.close()
    try:
        body = json.loads(raw)
    except (ValueError, RecursionError):
        return _ErrorCodes()
    inner = body.get("error") if isinstance(body, dict) else None
    metadata = inner.get("metadata") if isinstance(inner, dict) else None
    if not isinstance(metadata, dict):
        return _ErrorCodes()
    reasons = metadata.get("reasons")
    return _ErrorCodes(
        _code(metadata.get("error_type")),
        _code(metadata.get("provider_name")),
        isinstance(reasons, list) and bool(reasons),
    )


@dataclass(slots=True)
class _Reply:
    """One HTTP exchange: status 0 means no reply (``failure`` says why)."""

    status: int
    headers: "Message[str, str] | None"
    body: dict[str, Any] | None
    failure: str = ""
    codes: _ErrorCodes = _ErrorCodes()


class ModelGate:
    """What every model call of one process shares (G9): the limiter, the circuit breaker,
    the key latch and the reader's status, with the clock, sleep and random number they use
    (tests inject them). ``process_gate`` gives the process's one gate, and every client
    ``build_model_client`` makes uses it, so reading, chat and Health see the same state.
    Thread-safe."""

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
        """``workers``: the processes sharing MODEL_RPM (see the module docstring)."""
        if workers < 1:
            raise ValueError("workers must be at least 1")
        self.clock, self.sleep, self.random, self.wall_clock = clock, sleep, random, wall_clock
        self._lock = threading.Lock()
        # The limiter: calls start at least this many seconds apart.
        self.interval = 60.0 * workers / settings.model_rpm
        self._next_start = -math.inf
        self._breaker = _CLOSED
        self._failures = 0
        self._opened_at = 0.0
        self._probing = False
        self._down_since: float | None = None
        self._key_latched_at: float | None = None

    def status(self) -> tuple[str, float | None]:
        """(the reader's state, since when on the gate's clock) for Health (U9) and the
        outage clock (Q7). ``working`` (since None); ``failing``: the last requests failed,
        since the first of them; ``down``: the breaker is open or probing, since the first
        failure; ``key_problem``: a 401, 402 or key 403 within KEY_LATCH_SECONDS, since it
        came."""
        with self._lock:
            if self._key_latched_at is not None and self.clock() < self._key_latched_at + KEY_LATCH_SECONDS:
                return KEY_PROBLEM, self._key_latched_at
            if self._breaker != _CLOSED:
                return DOWN, self._down_since
            if self._failures:
                return FAILING, self._down_since
            return WORKING, None

    def admit(self) -> None:
        """Raises at once while the key is latched or the breaker is open; lets one probe
        through once the breaker has been open BREAKER_OPEN_SECONDS."""
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
        """When this request may start under the limiter; the slot is booked only when it
        starts before ``deadline`` (``ModelUnavailable("rate_limited")`` if not)."""
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
        """One request's outcome: any reply closes the breaker; a failure (408, 429, 5xx, a
        mid-reply error, no connection) counts towards opening it."""
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
                    _log.warning("model circuit open", extra={"code": "circuit_open"})
                self._breaker, self._opened_at = _OPEN, now

    def latch_key(self, code: str = "key_rejected") -> None:
        """A 401, 402 or key 403: every call fails at once for KEY_LATCH_SECONDS; logged once
        at error, with ``code`` (``no_credits`` for a 402)."""
        with self._lock:
            self._probing = False
            first = self._key_latched_at is None
            self._key_latched_at = self.clock()
        if first:
            _log.error("openrouter key rejected", extra={"code": code})


_PROCESS_GATES: dict[tuple[str, int, int], ModelGate] = {}
_PROCESS_GATES_LOCK = threading.Lock()


def process_gate(settings: Settings, *, workers: int | None = None) -> ModelGate:
    """This process's one gate for OPENROUTER_BASE_URL at MODEL_RPM over ``workers``, on the
    real clock: every client built for the product shares it. ``workers`` None is
    WORKER_REPLICAS + 1, the worker copies and the API (PRD 5 V7)."""
    if workers is None:
        workers = settings.worker_replicas + 1
    key = (settings.openrouter_base_url, settings.model_rpm, workers)
    with _PROCESS_GATES_LOCK:
        gate = _PROCESS_GATES.get(key)
        if gate is None:
            gate = _PROCESS_GATES[key] = ModelGate(settings, workers=workers)
        return gate


class OpenRouterClient:
    """``ModelClient`` over OpenRouter, with ``read_page`` for the reader (G1, G9)."""

    def __init__(
        self,
        settings: Settings,
        *,
        chat_timeout_seconds: float | None = None,
        gate: ModelGate | None = None,
    ) -> None:
        """``chat_timeout_seconds`` is for ``chat_json`` only (the worker passes
        MODEL_EXTRACT_TIMEOUT_SECONDS); ``embed`` uses MODEL_TIMEOUT_SECONDS. ``gate``: the
        limiter and breaker to share; without one the client has its own (``process_gate``
        gives the shared one)."""
        if settings.openrouter_api_key is None:
            raise ValueError("OPENROUTER_API_KEY is not set")
        parts = urllib.parse.urlsplit(settings.openrouter_base_url)
        secure = parts.scheme == "https" or (parts.scheme == "http" and parts.hostname in _LOOPBACK_HOSTS)
        if not (secure and parts.hostname) or parts.username or parts.password or parts.query:
            raise ValueError("openrouter_base_url must be https (http only on a loopback host)")
        others = (settings.model_chat, settings.model_embed)
        for model in (settings.ocr_model, *filter(is_gemini_model, others)):
            if not valid_model_id(model):
                raise ValueError("a model ID must be an OpenRouter author/slug with no :variant")
        self._key = settings.openrouter_api_key
        self._https = parts.scheme == "https"
        self._host_path = settings.openrouter_base_url.rstrip("/").split("://", 1)[1]
        self._timeout = settings.model_timeout_seconds
        self._chat_timeout = self._timeout if chat_timeout_seconds is None else chat_timeout_seconds
        self._default_chat_tokens = settings.model_answer_max_tokens
        self._dim = settings.embedding_dim
        self._providers = settings.openrouter_providers
        self.ocr_model = settings.ocr_model
        self.chat_model = settings.model_chat
        self.embed_model = settings.model_embed
        self.gate = ModelGate(settings) if gate is None else gate
        self._opener = build_opener()
        self._lock = threading.Lock()
        self._input_tokens = 0
        self._output_tokens = 0
        self._cost: float | None = None

    def __repr__(self) -> str:
        return f"OpenRouterClient(ocr_model={self.ocr_model!r}, chat_model={self.chat_model!r})"

    @property
    def interval(self) -> float:
        """The limiter's spacing between calls, in seconds."""
        return self.gate.interval

    def now(self) -> float:
        """The gate's clock (``time.monotonic`` unless a test injected one)."""
        return self.gate.clock()

    def usage(self) -> Usage:
        """Tokens and cost counted from this client's replies since it was made (G9)."""
        with self._lock:
            return Usage(self._input_tokens, self._output_tokens, self._cost)

    def status(self) -> tuple[str, float | None]:
        """The gate's ``status()``."""
        return self.gate.status()

    # The ModelClient protocol.

    def embed(self, texts: list[str]) -> list[list[float]]:
        """One vector per text, in order, ``EMBEDDING_DIM`` long (M5: dimensions 1024, so
        halfvec(1024) does not change). /embeddings, ``EMBED_TEXTS_PER_REQUEST`` texts a
        request."""
        vectors: list[list[float]] = []
        for start in range(0, len(texts), EMBED_TEXTS_PER_REQUEST):
            batch = texts[start : start + EMBED_TEXTS_PER_REQUEST]
            body = {
                "model": self.embed_model,
                "input": batch,
                "dimensions": self._dim,
                "encoding_format": "float",
                "provider": provider(embeddings=True),
            }
            reply = self._call(EMBEDDINGS_PATH, body, self.now() + self._timeout)
            data = reply.get("data")
            if not isinstance(data, list):
                raise ModelUnavailable("bad_response")
            vectors.extend(self._in_order(data, len(batch)))
        return vectors

    def chat_json(
        self, *, system: str, user: str, schema: dict[str, Any], max_tokens: int | None = None
    ) -> dict[str, Any]:
        """One JSON object following ``schema``, from MODEL_CHAT. A first reply that does not
        follow it goes back once for a repair; a second bad one raises ``invalid_output``. A
        reply longer than ``max_tokens`` (thinking not counted) raises ``output_truncated``
        with no repair."""
        messages: list[dict[str, Any]] = [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ]
        output_limit = self._default_chat_tokens if max_tokens is None else max_tokens
        body = chat_request(
            model=self.chat_model,
            messages=messages,
            thinking=CHAT_THINKING_LEVEL,
            schema=schema,
            max_output_tokens=output_limit + CHAT_THINKING_TOKENS,
        )
        content, spent = self._chat_reply(body, output_limit, (0, 0))
        value, problem = parse_reply(content, schema)
        if value is not None:
            return value
        messages.append({"role": "assistant", "content": content})
        messages.append({"role": "user", "content": REPAIR.format(problem=problem)})
        content, spent = self._chat_reply(body, output_limit, spent)
        value, _ = parse_reply(content, schema)
        if value is None:
            raise ModelOutputError("invalid_output", input_tokens=spent[0], output_tokens=spent[1])
        return value

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
        model: str | None = None,
        provider: str | None = None,
    ) -> PageReply:
        """One page read by ``model`` through ``provider`` (None: OCR_MODEL through
        DEFAULT_PROVIDER, as before model choice) as a JSON object following ``schema``,
        before ``deadline`` (an absolute time on ``now()``; every retry fits inside it). A
        provider outside OPENROUTER_PROVIDERS raises ``ProviderNotAllowed`` before anything is
        sent. A first reply that does not follow the schema is sent back once for a repair,
        with the page again; the tokens of both calls are counted."""
        if model is not None and not valid_model_id(model):
            raise ValueError("a model ID must be an OpenRouter author/slug with no :variant")
        if provider is not None and provider not in self._providers:
            raise ProviderNotAllowed("provider_not_allowed")
        body = page_request(
            model=self.ocr_model if model is None else model,
            png=png,
            instruction=instruction,
            schema=schema,
            media_resolution=media_resolution,
            thinking=thinking_level,
            max_output_tokens=PAGE_MAX_OUTPUT_TOKENS,
            provider_slug=DEFAULT_PROVIDER if provider is None else provider,
        )
        reply = self._call(CHAT_PATH, body, deadline)
        input_tokens, output_tokens = reply_usage(reply)
        content, finish = _billed_text(reply, (0, 0))
        value, problem = parse_reply(content, schema)
        if value is None:
            body["messages"].append({"role": "assistant", "content": content})
            body["messages"].append({"role": "user", "content": REPAIR.format(problem=problem)})
            reply = self._call(CHAT_PATH, body, deadline)
            content, finish = _billed_text(reply, (input_tokens, output_tokens))
            more_in, more_out = reply_usage(reply)
            input_tokens, output_tokens = input_tokens + more_in, output_tokens + more_out
            value, _ = parse_reply(content, schema)
            if value is None:
                raise ModelOutputError(
                    "invalid_output", input_tokens=input_tokens, output_tokens=output_tokens
                )
        return PageReply(value, input_tokens, output_tokens, finish)

    # verify-models' raw request.

    def probe(
        self, path: str, body: Mapping[str, Any] | None, *, send_key: bool = True, timeout: float
    ) -> Probe:
        """One request with no retry, limiter, breaker or latch, for verify-models only: what
        the live API answers to ``body`` (a POST), or to a GET when ``body`` is None.
        ``send_key=False`` sends no key."""
        started = self.now()
        reply = self._send(path, body, timeout, send_key=send_key)
        headers = {}
        if reply.headers is not None:
            headers = {
                name.lower(): value for name, value in reply.headers.items() if _RATE_HEADER.fullmatch(name)
            }
        codes = reply.codes
        return Probe(
            reply.status, codes.error_type, codes.provider_name, headers, reply.body, self.now() - started
        )

    # Internals.

    def _chat_reply(
        self, body: dict[str, Any], max_tokens: int | None, before: tuple[int, int]
    ) -> tuple[str, tuple[int, int]]:
        """(the reply's text, the tokens billed so far: ``before`` plus this reply's). Its
        ``ModelOutputError`` carries the same tokens."""
        reply = self._call(CHAT_PATH, body, self.now() + self._chat_timeout)
        text, _ = _billed_text(reply, before)
        input_tokens, output_tokens = reply_usage(reply)
        spent = (before[0] + input_tokens, before[1] + output_tokens)
        if max_tokens is not None and output_tokens - reasoning_tokens(reply) > max_tokens:
            raise ModelOutputError("output_truncated", input_tokens=spent[0], output_tokens=spent[1])
        return text, spent

    def _in_order(self, data: list[Any], count: int) -> list[list[float]]:
        """The batch's vectors sorted by ``index``, which must be exactly 0 to count - 1."""
        indexed: dict[int, object] = {}
        for item in data:
            index = item.get("index") if isinstance(item, dict) else None
            if isinstance(index, bool) or not isinstance(index, int) or index in indexed:
                raise ModelOutputError("embedding_count")
            indexed[index] = item
        if sorted(indexed) != list(range(count)):
            raise ModelOutputError("embedding_count")
        return [self._vector(indexed[index]) for index in range(count)]

    def _vector(self, item: object) -> list[float]:
        values = item.get("embedding") if isinstance(item, dict) else None
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
            code: str | None
            if 200 <= reply.status < 300:
                if reply.body is None:
                    gate.record(failed=False)
                    raise ModelUnavailable(reply.failure or "bad_response")
                if not failed_mid_reply(reply.body):
                    gate.record(failed=False)
                    self._count(reply.body)
                    return reply.body
                self._count(reply.body)  # a reply that failed part way may still be billed
                code = "server_error"
            elif reply.status in _KEY_STATUSES:
                if reply.status == 403 and (
                    reply.codes.moderated or reply.codes.error_type in REFUSAL_ERROR_TYPES
                ):
                    gate.record(failed=False)
                    raise ModelOutputError("blocked")
                gate.latch_key(_KEY_STATUSES[reply.status])
                raise ModelUnavailable("key_rejected")
            elif reply.status == 0:
                code = reply.failure
            else:
                code = _RETRY_STATUSES.get(reply.status) or ("server_error" if reply.status >= 500 else None)
            if code is None:  # the server answered: it is up, but this request is wrong
                gate.record(failed=False)
                if reply.status in _BAD_REQUEST_STATUSES:
                    raise ModelOutputError("bad_request")
                if reply.status == 404:
                    raise ModelUnavailable("model_not_found")
                if 300 <= reply.status < 400:
                    raise ModelUnavailable("redirect_refused")
                raise ModelUnavailable("http_error")
            gate.record(failed=True)
            if reply.status == 0 and code == "timeout":
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
        cost = reply_cost(reply)
        with self._lock:
            self._input_tokens += input_tokens
            self._output_tokens += output_tokens
            if cost is not None:
                self._cost = cost if self._cost is None else self._cost + cost

    def _send(
        self, path: str, body: Mapping[str, Any] | None, timeout: float, *, send_key: bool = True
    ) -> _Reply:
        """One POST (or a GET when ``body`` is None). Never raises for HTTP or the network:
        the outcome is a ``_Reply``."""
        data = None if body is None else json.dumps(body).encode("utf-8")
        headers = {} if body is None else {"Content-Type": "application/json"}
        method = "GET" if body is None else "POST"
        # The scheme is spelled out, so nothing but https (or http on a loopback host,
        # checked in __init__) can be opened.
        if self._https:
            request = urllib.request.Request(f"https://{self._host_path}{path}", data, headers, method=method)
        else:
            request = urllib.request.Request(f"http://{self._host_path}{path}", data, headers, method=method)
        if send_key:
            request.add_unredirected_header(API_KEY_HEADER, "Bearer " + self._key)
        try:
            with self._opener.open(request, timeout=timeout) as response:
                raw = response.read(replies.MAX_RESPONSE_BYTES + 1)
                status, reply_headers = response.status, response.headers
        except urllib.error.HTTPError as error:
            return _Reply(error.code, error.headers, None, codes=_error_codes(error))
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
