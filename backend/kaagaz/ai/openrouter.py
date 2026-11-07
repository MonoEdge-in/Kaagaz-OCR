"""The model client (PRD 5 G9): ``ModelClient`` over OpenRouter's REST API.

Every model PRD 5 names goes through OpenRouter (https://openrouter.ai/api/v1), Tushar's
decision of 8 October 2026 (council ``2026-10-08-prd5-openrouter.md``). ``OpenRouterClient``
implements ``ModelClient`` (``chat_json`` and ``embed``), and adds ``read_page`` for the
Gemini reader (G1, G2, G3). ``read_page`` reads with the model and provider its caller
names (the document's, ``kaagaz.db.models.resolve``; model choice, council 11 Oct 2026),
or OCR_MODEL; ``MODEL_CHAT`` answers ``chat_json`` unless its caller names the answer's
chosen model (``kaagaz.db.models.resolve_chat``, features 25-27) and ``MODEL_EMBED`` embeds.
No code here picks or switches a model.

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
OPENROUTER_PROVIDERS (``ProviderNotAllowed``): a page read's or an answer draft's, and
DEFAULT_PROVIDER for any other ``chat_json`` and ``embed`` (the settings require it;
checked again here).

**The request** (``POST /chat/completions``, ``stream: false``, G2; Ask's draft streams, below). A
system prompt is
``messages[0]`` with role system. A picture is a user message whose content is the
instruction text first, then the image as a ``data:image/png;base64`` URL. The page's
media resolution goes the way ``MEDIA_RESOLUTION_SHAPE`` says (``image_url.detail``, low or
high). Thinking is ``reasoning: {effort, exclude: true}``: the effort is the setting, and
the thinking itself never comes back. The output cap is ``max_completion_tokens``.
Structured output is ``response_format`` ``json_schema`` named ``page`` with ``strict:
STRICT_SCHEMA``. Temperature is left at the default (PRD 5 section 14), but for a ``chat_json``
call that names one (Ask's question rewrite and translation, 16 Oct 2026), sent to a Google
model only. A repair turn sends
the first reply back with role assistant. ``python -m kaagaz.cli verify-models`` checks
each of these against the live API.

**Streaming** (#535, Ask's draft only: ``chat_json_stream``). The same request with ``stream:
true``; the reply comes as server-sent events, each ``delta.content`` handed to the caller as it
arrives, and is put back together as the reply ``stream: false`` would have given (its text, finish
reasons, ``provider`` and the last chunk's ``usage``), so every check, the ledger, the limiter, the
breaker and the latch are the same code. Its time is not one flat timeout: a streamed reply may go
on while it keeps coming, up to the caller's deadline (else ANSWER_STREAM_MAX_SECONDS), and fails as
``timeout`` only when nothing at all arrives for MODEL_TIMEOUT_SECONDS (the socket's read timeout;
OpenRouter's ": OPENROUTER PROCESSING" keep-alive lines count as something). A Precision draft that
thinks for 75 s while tokens and keep-alives arrive is never cut at 60 s. Two more differences: no
repair turn (a reply that
does not follow the schema is ``invalid_output``; the caller drafts again without streaming), and a
reply that already handed out text is never sent again: a failure after it raises at once. The
ledger is never told 0: the request asks for the usage (``usage: {include: true}``, OpenRouter's
usage accounting, given in the last chunk); a stream that still ends without it is counted at its
worst case (``_worst_usage``: its input at most its UTF-8 bytes, its whole output limit), with no
cost, so the ledger prices those tokens and marks the cost estimated (db/usage.py), and logs
``stream_usage_missing``.

**Usage** (G9). Input tokens are ``usage.prompt_tokens``; output tokens are
``usage.completion_tokens``; ``completion_tokens_details.reasoning_tokens`` is the thinking
part of it. ``read_page`` returns them per page; ``usage()`` keeps the process's totals and
the summed ``usage.cost`` when replies carry one. Every billed reply (a blocked, cut off,
mid-reply error or repaired one too) also goes to the current usage scope's ledger
(``kaagaz.ai.usage.record_call``, SP1) with its model, the reply's ``provider`` and its
thinking tokens and cost; a page read with the thinking level and picture detail it was sent
at (#463).

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
* 404: ``ModelUnavailable("model_not_found")`` (a wrong model ID in .env, or a model
  OpenRouter withdrew).
* A redirect: ``ModelUnavailable("redirect_refused")``; any other status ``http_error``.
  For OCR_MODEL, either one makes the reader's Health report ``key_problem`` for
  KEY_LATCH_SECONDS (``ModelGate.health``), with no latch and no change to ``status``
  (issue 407, ``ModelGate.reader_unfixable``).
* A reply that was blocked (``message.refusal``, ``finish_reason`` ``content_filter``, or a
  ``native_finish_reason`` in ``BLOCKED_FINISH_REASONS``): ``ModelOutputError("blocked")``.
* ``finish_reason`` ``length``: ``ModelOutputError("output_truncated")``, with no repair
  retry (a cut reply would be cut again).
* Every ``ModelOutputError`` raised after a reply carries the tokens billed for it
  (``input_tokens``, ``output_tokens``: this reply's usage, plus the first reply's when a
  repair was asked for), so a blocked, cut off or invalid reply is still counted (G9).
* Any other finish reason, or none: ``ModelOutputError("invalid_output")``.
* #488 (Ultra low cost): a page read with a ``PageBudget`` is never sent past it. Each call's
  ``max_completion_tokens`` is what the money left buys after its input (``PageBudget.cap``),
  every billed reply is charged the larger of its ``usage.cost`` and its tokens at the
  catalogue's prices (or above them, once a reply was billed above them: #488 review), a billed
  mid-reply error is retried only with a limit still affordable, and the repair only when its
  input and a limit of MIN_CAP_TOKENS are; otherwise ``ModelOutputError("cost_ceiling")``,
  counted as a limit hit, nothing sent. A call that may have been billed with no usage to say
  how much (a reply with none, a 200 that is not JSON, a connection lost once the request was
  sent; never one that failed while connecting or sending) is charged its worst case, its input
  estimate and its whole output limit, so nothing is sent after it that the ceiling can't pay.
* JSON that does not follow the schema, or that the caller's ``validate`` refuses (a
  ``read_page`` reply that breaks what the schema can't say): one repair retry, then
  ``ModelOutputError("invalid_output")``. One repair at most, whichever check failed.

**Rate limit and circuit breaker** (G9) live in a ``ModelGate``, one per process
(``process_gate``), shared by every client the process builds: the reader's, the chat
client and the translator. Production requests queue in PostgreSQL and receive round-robin
admission by organisation, then user, through the platform's 1,200 RPM schedule. There is
no burst allowance. Organisation RPM caps are optional (1-600); user caps are optional
(1-120) and remain inside the organisation's cap. Missing overrides use fair sharing, so a
single active organisation can use the platform allowance while concurrent organisations
take turns. A short lease removes tickets from crashed processes. The database connection
is required for production model calls. Gates without one retain the local ``MODEL_RPM``
limiter for isolated tooling and unit tests; no worker-count division provides distributed
enforcement. Each model has its own breaker
(council 11 Oct 2026, FINAL 2): after ``BREAKER_FAILURES`` failed requests in a row to it
(408, 429, 5xx, a mid-reply error or no connection) it opens: for ``BREAKER_OPEN_SECONDS``
every call to that model fails at once with ``circuit_open``, then one call is let through
as a probe; its success closes the breaker, its failure opens it again. Other models keep
working. The key latch is the process's: one key. ``status()`` gives the worst model's
state for the operator's Health tab (U9) and the worker's pause (Q7). A model whose last
failure is more than ``FAILURE_GAP_SECONDS`` old is not counted there, and a failure that
long after the one before starts its count again: a model no longer called (a document's
chosen model, read and done) never keeps the reader failing or down. Everything here is
thread-safe: pages of a document are read in parallel (G8).

The gate's clock, sleep and random number are injected, so tests run with a fake clock.
The clock is ``time.monotonic`` by default, and ``read_page``'s deadline is an absolute
time on it (``now()`` gives the current one). ``LIMITER_WAIT`` (ask precision PR 1a, D):
while a caller holds a one-item list in it, every limiter sleep adds its seconds there
(threads started with ``copy_context`` add to the same list); it changes nothing else.

Cost: one ``read_page`` is one request (two with a repair), plus retries inside its
deadline; ``embed`` makes n requests for n texts, EMBED_FAN_OUT at once through the one
limiter (fast scan, council 16 Oct 2026). Checking a reply is O(its size).
"""

import base64
import contextlib
import contextvars
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
import uuid
from collections.abc import Callable, Mapping
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import UTC, datetime
from email.message import Message
from http.client import HTTPException, HTTPMessage
from typing import IO, Any

from kaagaz.ai import replies, usage
from kaagaz.ai.replies import REPAIR, ModelOutputError, ModelUnavailable, parse_reply
from kaagaz.db.engine import Database
from kaagaz.settings import (
    DEFAULT_PROVIDER,
    MEDIA_RESOLUTION_SHAPE,
    MEDIA_RESOLUTIONS,
    NO_THINKING,
    PROVIDER_SLUG,
    SENDABLE_MEDIA_RESOLUTIONS,
    THINKING_LEVELS,
    Settings,
    valid_model_id,
)

API_KEY_HEADER = "Authorization"
# The limiter's waits in seconds, for the answer timing log (the module docstring).
LIMITER_WAIT: contextvars.ContextVar[list[float] | None] = contextvars.ContextVar(
    "limiter_wait", default=None
)
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
# Fast scan (council 16 Oct 2026): at most this many embedding requests at once, through the
# one limiter (never a gate of its own: OpenRouter's limit is the key's); 16 since Ready first
# (16 Oct 2026): their starts stay MODEL_RPM apart, so a higher MODEL_RPM is what speeds them.
EMBED_FAN_OUT = 16

# Structured output (G3): response_format json_schema with strict set to this. Whether
# Google honours strict through OpenRouter is checked by verify-models (strict_schema).
STRICT_SCHEMA = True
# The schema's name in response_format.
SCHEMA_NAME = "page"

# Q8 (cost guard): an initial page read's output ceiling, thinking included. The PR 1
# measurement may set a tighter value; it is sent as max_completion_tokens. A reply that
# reaches it ends "length" (output_truncated), and the reader reads the page in two halves.
# 65536 (scan reliability, 16 Oct 2026): Gemini 3.8 Flash's own ceiling on every Google
# endpoint; at high thinking a dense handwritten page passed 32768, and its halves then ran
# out of the page's time. Only the tokens generated are billed.
PAGE_MAX_OUTPUT_TOKENS = 65536
# #488 (Ultra low cost): a page read with a ``PageBudget`` is never sent with an output limit
# below this (a page's compact reply needs more), and its code when it would be.
MIN_CAP_TOKENS = 256
COST_CEILING = "cost_ceiling"
# #488: a repair turn takes in the page again, the first reply and the problem: their tokens at
# most their UTF-8 bytes, with this share more for the turns' own wrapping.
REPAIR_INPUT_MARGIN = 1.05
# chat_json thinks at this effort: the card, the invoice windows, answers
# and the follow-up rewrite want a short exact reply. gemini-3.1-pro-preview has no
# "minimal" level (Google's thinking page), so "low" is the least it accepts. An answer's
# draft may think at the level its asker chose (features 25-27, C7).
CHAT_THINKING_LEVEL = "low"
# A chat_json call with a reply budget asks for the budget plus this much room to think, by
# thinking level; the reply itself (completion_tokens less reasoning_tokens) is then held to
# the caller's budget.
CHAT_THINKING_TOKENS = {"low": 8192, "medium": 16384, "high": 32768}
# The vendor whose models honour a schema's maxLength (Google's); response_format leaves it
# out for every other vendor's model, whose structured output may refuse it (features 25-27,
# C7). The answer's sentences are capped after the reply anyway (draft._parse).
MAX_LENGTH_VENDOR = "google"
# Ask's steady search (16 Oct 2026): only these vendors' models get a chat_json ``temperature``.
# Anthropic's refuse a temperature other than 1 while thinking, and with require_parameters
# a model whose endpoint lists no temperature would be routed nowhere.
TEMPERATURE_VENDORS = frozenset({"google"})

# Backoff (G6): attempt n waits a random time up to min(BACKOFF_MAX_SECONDS,
# BACKOFF_BASE_SECONDS * 2**n) ("full jitter"), or the server's Retry-After if longer.
BACKOFF_BASE_SECONDS = 1.0
BACKOFF_MAX_SECONDS = 30.0
# A rejected key (401, 402, a 403 that is not a refusal) fails every call at once for this long.
KEY_LATCH_SECONDS = 300.0
# The circuit breaker (G9).
BREAKER_FAILURES = 5
BREAKER_OPEN_SECONDS = 60.0
# A model's failures older than this do not count (council 11 Oct 2026 review, R2):
# status() leaves out a model idle since its last failure for this long, and a failure
# this long after the one before starts the count again. 15 minutes: an outage that goes
# on keeps failing more often than that (each open breaker lets a probe through every
# BREAKER_OPEN_SECONDS), so only a model nobody calls any more is left out.
FAILURE_GAP_SECONDS = 15 * 60.0

# Native finish reasons that mean the model declined to answer (G6: reader_refused):
# Google's, and Anthropic's ``refusal`` (model choice, council 11 Oct 2026). OpenAI's
# refusal comes as ``content_filter`` or ``message.refusal`` (``reply_text``).
BLOCKED_FINISH_REASONS = frozenset(
    {
        "refusal",
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
# A reply's ``provider`` as the ledger keeps it (0023's model_usage.provider_name CHECK).
_PROVIDER_NAME = re.compile(r"[A-Za-z0-9 ._-]{1,64}")

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


class PageBudget:
    """#488 (Ultra low cost): the money left for one read of a page, held over every call made
    for it. ``usd_left`` starts at the page's ceiling; ``price_in`` and ``price_out`` are the
    lead's catalogue prices in dollars a token; ``input_estimate`` the tokens a call with the
    page takes in, at most (the reader's estimate); ``per_call`` what every call costs besides its
    tokens (OpenRouter's pricing.request and pricing.image; #488 review). Each token is taken to
    cost ``rate_in`` and ``rate_out``: the prices, or more (``rates``, what the document's replies
    were billed so far; #488 review). Before each call ``cap`` gives its output limit: what is
    left after its price a call and its input, at the rates, so the call can never cost more than
    is left (None, below MIN_CAP_TOKENS: nothing is sent). Each billed reply is charged the larger
    of its reported cost and its price a call and tokens at the rates (``charge``), so a cost the
    reply leaves out is still counted; a cost above its price a call and tokens at the prices
    raises the rates. A call that may have been billed but says no usage (none in the reply, a 200
    that is not JSON, a connection lost after the send) is charged its worst case: its price a
    call, its input estimate and its whole output limit (``charge_unknown``). One page's calls
    are made one after another, so there is no lock. O(1) each."""

    def __init__(
        self,
        usd: float,
        price_in: float,
        price_out: float,
        input_estimate: int,
        *,
        rates: tuple[float, float] = (0.0, 0.0),
        per_call: float = 0.0,
    ) -> None:
        self.usd = usd
        self.usd_left = usd
        self.per_call = per_call
        self.price_in = price_in
        self.price_out = price_out
        self.rate_in, self.rate_out = max(price_in, rates[0]), max(price_out, rates[1])
        self.input_estimate = input_estimate
        self.input_tokens = 0
        self.output_tokens = 0
        self.page_input: int | None = None  # the first call's input tokens it said: the page's own

    @property
    def spent(self) -> float:
        return self.usd - self.usd_left

    def page_tokens(self) -> int:
        """A call with the page's input, at most: the estimate, or what its first call took in."""
        return max(self.input_estimate, self.page_input or 0)

    def worst(self, input_tokens: int | None, cap: int) -> float:
        """The most a call taking in ``input_tokens`` (None: ``page_tokens``) with output limit
        ``cap`` can cost: its price a call, and its tokens at the rates."""
        tokens_in = self.page_tokens() if input_tokens is None else input_tokens
        return self.per_call + tokens_in * self.rate_in + cap * self.rate_out

    def cap(self, input_tokens: int | None = None) -> int | None:
        """The output limit of a call that takes in ``input_tokens`` (None: ``page_tokens``), at
        most PAGE_MAX_OUTPUT_TOKENS; None when it is below MIN_CAP_TOKENS."""
        if self.rate_out <= 0:
            return None
        cap = min(
            PAGE_MAX_OUTPUT_TOKENS, math.floor((self.usd_left - self.worst(input_tokens, 0)) / self.rate_out)
        )
        while cap >= MIN_CAP_TOKENS and self.worst(input_tokens, cap) > self.usd_left:
            cap -= 1  # a float's last bit never lets a call past what is left
        return cap if cap >= MIN_CAP_TOKENS else None

    def charge(self, input_tokens: int, output_tokens: int, cost: float | None) -> None:
        """One billed reply that says its tokens: the larger of ``cost`` (its usage.cost) and its
        price a call and tokens at the rates, taken off what is left. A cost above its price a call
        and tokens at the prices is then put on each kind of token in turn (on its tokens, or, with
        none, as the same share of the price), so the rates are at least what the provider charges
        for either."""
        self.usd_left -= max(cost or 0.0, self.worst(input_tokens, output_tokens))
        tokens = input_tokens * self.price_in + output_tokens * self.price_out
        if cost is not None and tokens > 0 and cost > self.per_call + tokens:
            over, scale = cost - self.per_call - tokens, (cost - self.per_call) / tokens
            self.rate_in = max(
                self.rate_in, self.price_in + over / input_tokens if input_tokens else self.price_in * scale
            )
            self.rate_out = max(
                self.rate_out,
                self.price_out + over / output_tokens if output_tokens else self.price_out * scale,
            )
        self.input_tokens += input_tokens
        self.output_tokens += output_tokens
        if self.page_input is None:
            self.page_input = input_tokens

    def charge_unknown(self, worst: float) -> None:
        """One call that may have been billed with no usage to say how much: ``worst``, what it
        could cost at most (``worst()`` before it was sent), taken off what is left."""
        self.usd_left -= worst


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


def response_format(
    schema: Mapping[str, Any], *, strict: bool = STRICT_SCHEMA, max_length: bool = True
) -> dict[str, Any]:
    """The ``response_format`` asking for JSON that follows ``schema`` (G3), sent with no
    ``maxItems``: Google Vertex refuses the document card's schema with it (400
    INVALID_ARGUMENT, live 11 Oct 2026) and takes it without. No ``minItems`` either
    (Anthropic refuses it, gemini_ocr's schema note).
    Every caller checks its own lists after the reply (card ``_capped``, invoice lines,
    memory, answer sentences). Hashes of our schemas
    (``ANSWER_PROMPT_HASH``) are taken before this, so leaving keys out never changes them.
    ``max_length`` False leaves every ``maxLength`` out too (a model not of
    MAX_LENGTH_VENDOR)."""
    left_out = {"maxItems", "minItems"} if max_length else {"maxItems", "minItems", "maxLength"}
    return {
        "type": "json_schema",
        "json_schema": {"name": SCHEMA_NAME, "strict": strict, "schema": _without(schema, left_out)},
    }


def _without(schema: Any, keys: set[str]) -> Any:
    """``schema`` with every one of ``keys`` left out, at any depth; nothing else changes."""
    if isinstance(schema, Mapping):
        return {key: _without(value, keys) for key, value in schema.items() if key not in keys}
    if isinstance(schema, list):
        return [_without(value, keys) for value in schema]
    return schema


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
    stream: bool = False,
) -> dict[str, Any]:
    """A /chat/completions body: the model, the messages, the thinking effort, the output
    cap, the schema and ``provider(provider_slug)``. ``thinking`` NO_THINKING (a model with
    no thinking levels, Tushar 18 Oct 2026) sends no ``reasoning`` field at all. ``stream``:
    Ask's draft only (#535)."""
    body: dict[str, Any] = {
        "model": model,
        "messages": messages,
        "stream": stream,
        "provider": provider(provider_slug),
    }
    if stream:
        body["usage"] = {"include": True}  # the tokens and cost in the last chunk (the module docstring)
    if thinking != NO_THINKING:
        body["reasoning"] = reasoning(thinking)
    if max_output_tokens is not None:
        body["max_completion_tokens"] = max_output_tokens
    if schema is not None:
        google = model.split("/", 1)[0] == MAX_LENGTH_VENDOR
        body["response_format"] = response_format(schema, strict=strict, max_length=google)
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


def _says_usage(reply: Mapping[str, Any]) -> bool:
    """#488 review: the reply says both its token counts (``reply_usage`` reads 0 for one left out)."""
    usage = _usage(reply)
    return all(
        isinstance(usage.get(name), int) and not isinstance(usage.get(name), bool)
        for name in ("prompt_tokens", "completion_tokens")
    )


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


def _page_text(
    reply: Mapping[str, Any], before: tuple[int, int], model: str, sent: tuple[str, str]
) -> tuple[str, str]:
    """``_billed_text`` of a page read; a reply cut off at the output limit is counted on the
    ledger as a limit hit (#463) before its ``output_truncated`` goes up."""
    try:
        return _billed_text(reply, before)
    except ModelOutputError as error:
        if error.code == "output_truncated":
            usage.record_limit_hit(model, thinking=sent[0], resolution=sent[1])
        raise


def _valid_reply(
    content: str, schema: Mapping[str, Any], validate: Callable[[dict[str, Any]], str | None] | None
) -> tuple[dict[str, Any] | None, str]:
    """``parse_reply``, then ``validate`` on a value that follows the schema: (the value,
    ""), or (None, the problem in words for the repair turn)."""
    value, problem = parse_reply(content, schema)
    if value is not None and validate is not None:
        refused = validate(value)
        if refused is not None:
            return None, refused
    return value, problem


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


# The most one read of a reply body asks for.
_READ_CHUNK_BYTES = 64 * 1024


def _read_until(response: Any, ends: float, limit: int) -> bytes | None:
    """The body, at most ``limit`` bytes, or None once ``ends`` (on time.monotonic) has passed.
    The socket timeout only bounds each read, so a reply that keeps trickling in (whitespace
    sent while a model thinks) would otherwise outlast the deadline without end (stuck
    reading, 16 Oct 2026). O(body)."""
    chunks: list[bytes] = []
    size = 0
    while size < limit:
        if time.monotonic() >= ends:
            return None
        chunk = response.read1(min(_READ_CHUNK_BYTES, limit - size))
        if not chunk:
            break
        chunks.append(chunk)
        size += len(chunk)
    return b"".join(chunks)


def _read_stream(
    response: Any, ends: float, limit: int, on_text: Callable[[str], None], texts: list[str]
) -> dict[str, Any] | str:
    """#535: a streamed reply (server-sent events), each ``delta.content`` handed to ``on_text``
    as it comes ("" for a line with none: a keep-alive comment or an event's blank line, so the
    caller may stop the reply by raising), put back together as the reply ``stream: false``
    gives: its text, ``refusal``, finish reasons, ``id``, ``model``, ``provider`` and the last
    ``usage``. An ``error`` chunk is a reply that failed part way (finish_reason error). A
    failure code instead: "timeout" once ``ends`` has passed, "response_too_large" past
    ``limit`` bytes, "bad_response" for a chunk that is not a JSON object. ``texts`` gets each
    piece of text as it comes, so a reply that fails part way still says how much it gave (the
    ledger's worst case, ``OpenRouterClient._bill_partial``). An error chunk is logged with its
    codes only (``code``, ``error_type``, ``provider_name``): its message can hold page text (the
    module docstring, G6), so it is never read. O(body)."""
    reply: dict[str, Any] = {}
    choice: dict[str, Any] = {"index": 0, "finish_reason": None}
    refusal: object = None
    size = 0
    while True:
        if time.monotonic() >= ends:
            return "timeout"
        line = response.readline(limit + 1 - size)
        if not line:
            break
        size += len(line)
        if size > limit:
            return "response_too_large"
        text = ""
        if line.startswith(b"data:"):
            data = line[5:].strip()
            if data == b"[DONE]":
                break
            try:
                chunk = json.loads(data)
            except (ValueError, RecursionError):
                return "bad_response"
            if not isinstance(chunk, dict):
                return "bad_response"
            reply.update({key: chunk[key] for key in ("id", "model", "provider", "usage") if key in chunk})
            choices = chunk.get("choices")
            first = (
                choices[0] if isinstance(choices, list) and choices and isinstance(choices[0], dict) else {}
            )
            delta = first.get("delta")
            delta = delta if isinstance(delta, dict) else {}
            if isinstance(delta.get("content"), str):
                text = delta["content"]
                texts.append(text)
            if delta.get("refusal") is not None:
                refusal = delta["refusal"]
            choice.update(
                {key: first[key] for key in ("finish_reason", "native_finish_reason") if first.get(key)}
            )
            if "error" in chunk:
                choice["finish_reason"] = "error"
                _log_error_chunk(chunk["error"])
        on_text(text)
    choice["message"] = {"role": "assistant", "content": "".join(texts), "refusal": refusal}
    return {**reply, "choices": [choice]}


def _worst_usage(body: Mapping[str, Any], content: str) -> dict[str, Any]:
    """#535: a streamed reply's usage when it gave none, at its worst case (as #488 charges a call
    with no usage): the input at most the UTF-8 bytes of its messages, the output its whole
    ``max_completion_tokens``, of which all but ``content``'s bytes count as thinking (so the
    caller's own reply budget is checked on the text alone). No cost: the ledger prices them."""
    messages = body.get("messages")
    said = json.dumps(messages, ensure_ascii=False) if isinstance(messages, list) else ""
    limit = _whole(body.get("max_completion_tokens"))
    text = len(content.encode("utf-8"))
    output = max(limit, text)
    return {
        "prompt_tokens": len(said.encode("utf-8")),
        "completion_tokens": output,
        "completion_tokens_details": {"reasoning_tokens": output - text},
    }


def _log_error_chunk(error: object) -> None:
    """#535: a streamed reply's error chunk, logged by its short codes only (``_read_stream``)."""
    found = error if isinstance(error, dict) else {}
    metadata = found.get("metadata")
    metadata = metadata if isinstance(metadata, dict) else {}
    _log.warning(
        "streamed reply failed part way",
        extra={
            "code": "server_error",
            "error_code": _code(str(found.get("code"))) if found.get("code") is not None else None,
            "error_type": _code(metadata.get("error_type")),
            "provider_name": _code(metadata.get("provider_name")),
        },
    )


@dataclass(slots=True)
class _Reply:
    """One HTTP exchange: status 0 means no reply (``failure`` says why; ``sent`` False when the
    request never all reached the server: it failed while connecting or sending)."""

    status: int
    headers: "Message[str, str] | None"
    body: dict[str, Any] | None
    failure: str = ""
    codes: _ErrorCodes = _ErrorCodes()
    sent: bool = True

    @property
    def maybe_billed(self) -> bool:
        """#488 review: no usage to read, but a provider may have billed it: a 2xx whose body
        could not be read, or no reply to a request that was sent."""
        return (200 <= self.status < 300 and self.body is None) or (self.status == 0 and self.sent)


# status(): the worst state wins.
_STATE_RANK = {WORKING: 0, FAILING: 1, DOWN: 2, KEY_PROBLEM: 3}


@dataclass(slots=True)
class _Breaker:
    """One model's circuit breaker (council 11 Oct 2026, FINAL 2), under its gate's lock."""

    state: str = _CLOSED
    failures: int = 0
    opened_at: float = 0.0
    probing: bool = False
    down_since: float | None = None
    last_failure_at: float | None = None

    def status(self, now: float) -> tuple[str, float | None]:
        """``down`` while open (for BREAKER_OPEN_SECONDS) or probing; ``failing`` after a
        recent failure (an open breaker past BREAKER_OPEN_SECONDS too: the next call is
        let through); ``working`` with no failure in FAILURE_GAP_SECONDS."""
        if self.last_failure_at is None or now - self.last_failure_at > FAILURE_GAP_SECONDS:
            return WORKING, None
        if (self.state == _OPEN and now < self.opened_at + BREAKER_OPEN_SECONDS) or (
            self.state == _HALF_OPEN and self.probing
        ):
            return DOWN, self.down_since
        return FAILING, self.down_since


class ModelGate:
    """What every model call of one process shares (G9): the limiter, the key latch, a
    circuit breaker per model and the reader's status, with the clock, sleep and random
    number they use (tests inject them). The limiter and the latch are the process's (one
    key); each model has its own breaker, so a model that fails leaves the others working
    (council 11 Oct 2026, FINAL 2). ``process_gate`` gives the process's one gate, and every
    client ``build_model_client`` makes uses it, so reading, chat and Health see the same
    state. Thread-safe."""

    def __init__(
        self,
        settings: Settings,
        *,
        workers: int = 1,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
        random: Callable[[], float] = random_module.random,
        wall_clock: Callable[[], datetime] = lambda: datetime.now(UTC),
        database: Database | None = None,
    ) -> None:
        """``workers``: only used by the local fallback limiter when no database is set."""
        if workers < 1:
            raise ValueError("workers must be at least 1")
        self.clock, self.sleep, self.random, self.wall_clock = clock, sleep, random, wall_clock
        self.database = database
        self.platform_rpm = settings.model_rpm
        self._lock = threading.Lock()
        # The limiter: calls start at least this many seconds apart.
        self.interval = 60.0 * workers / settings.model_rpm
        self._next_start = -math.inf
        self._breakers: dict[str, _Breaker] = {}
        self._key_latched_at: float | None = None
        # Issue 407: OCR_MODEL, the platform's reader, answered 404 or a redirect (``reader_unfixable``).
        self._reader_model = settings.ocr_model
        self._reader_unfixable_at: float | None = None

    def status(self) -> tuple[str, float | None]:
        """(the reader's state, since when on the gate's clock) for Health (U9) and the
        outage clock (Q7): the worst model's (council 11 Oct 2026, FINAL 2). ``working``
        (since None); ``failing``: a model's last requests failed, since the first of them
        (an open breaker past BREAKER_OPEN_SECONDS too); ``down``: a model's breaker is
        open or probing, since its first failure; ``key_problem``: a 401, 402 or key 403
        within KEY_LATCH_SECONDS, since it came. A model with no failure in
        FAILURE_GAP_SECONDS counts as working. Two models in the worst state give the
        earlier since. The worker's admission, the stuck sweep and the upgrade sweep act on
        it; Health reads ``health``."""
        with self._lock:
            now = self.clock()
            if self._key_latched_at is not None and now < self._key_latched_at + KEY_LATCH_SECONDS:
                return KEY_PROBLEM, self._key_latched_at
            states = [breaker.status(now) for breaker in self._breakers.values()]
        worst = max((state for state, _ in states), key=_STATE_RANK.__getitem__, default=WORKING)
        since = [at for state, at in states if state == worst and at is not None]
        return worst, min(since) if since else None

    def health(self) -> tuple[str, float | None]:
        """What the worker reports for Health (U9): ``status``, or ``key_problem`` while OCR_MODEL's
        404 or redirect is fresh (``reader_unfixable``, issue 407), so the operator is asked to act.
        Only the report: nothing is handed back for it, so an organisation reading with another
        model goes on."""
        with self._lock:
            unfixable = self._reader_unfixable_at
            fresh = unfixable is not None and self.clock() < unfixable + KEY_LATCH_SECONDS
        state = self.status()
        return (KEY_PROBLEM, unfixable) if fresh and state[0] != KEY_PROBLEM else state

    def admit(self, model: str) -> None:
        """Raises at once while the key is latched or ``model``'s breaker is open; lets one
        probe through once it has been open BREAKER_OPEN_SECONDS."""
        with self._lock:
            now = self.clock()
            if self._key_latched_at is not None and now < self._key_latched_at + KEY_LATCH_SECONDS:
                raise ModelUnavailable("key_rejected")
            breaker = self._breakers.setdefault(model, _Breaker())
            if breaker.state == _OPEN and now >= breaker.opened_at + BREAKER_OPEN_SECONDS:
                breaker.state = _HALF_OPEN
            if breaker.state == _OPEN or (breaker.state == _HALF_OPEN and breaker.probing):
                raise ModelUnavailable("circuit_open")
            if breaker.state == _HALF_OPEN:
                breaker.probing = True

    def take_slot(self, deadline: float, model: str) -> float:
        """When this request may start under the limiter; the slot is booked only when it
        starts before ``deadline``. Production waits use the shared database scheduler; if
        admission cannot happen by the deadline, ``ModelUnavailable("rate_limited")`` is
        raised and this model's half-open probe is released."""
        if self.database is not None:
            customer_id, user_id = usage.current_rate_scope()
            request_id = str(uuid.uuid4())
            waited = 0.0
            try:
                while True:
                    wait = self.database.model_rate_slot(
                        request_id,
                        customer_id,
                        user_id,
                        max(0.0, deadline - self.clock()),
                        self.platform_rpm,
                    )
                    if wait < 0:
                        raise ModelUnavailable("rate_limited")
                    if wait == 0:
                        counter = LIMITER_WAIT.get()
                        if counter is not None:
                            counter[0] += waited
                        return self.clock()
                    if self.clock() + wait >= deadline:
                        self.database.model_rate_slot(
                            request_id, customer_id, user_id, 0.0, self.platform_rpm
                        )
                        raise ModelUnavailable("rate_limited")
                    # Renew the ticket well inside its lease, even for a long user or
                    # organisation cooldown.
                    wait = min(wait, 2.0)
                    self.sleep(wait)
                    waited += wait
            except Exception:  # noqa: BLE001 - release any stranded ticket and fail closed.
                self.release_probe(model)
                with contextlib.suppress(Exception):
                    self.database.model_rate_slot(request_id, customer_id, user_id, 0.0, self.platform_rpm)
                raise ModelUnavailable("rate_limited") from None
        with self._lock:
            start = max(self.clock(), self._next_start)
            if start >= deadline:
                self._breakers.setdefault(model, _Breaker()).probing = False
                raise ModelUnavailable("timeout")
            self._next_start = start + self.interval
            return start

    def release_probe(self, model: str) -> None:
        with self._lock:
            self._breakers.setdefault(model, _Breaker()).probing = False

    def record(self, model: str, *, failed: bool) -> None:
        """One request's outcome for ``model``: any reply closes its breaker (and ends a key
        latch, and OCR_MODEL's own ends ``reader_unfixable``); a failure (408, 429, 5xx, a
        mid-reply error, no connection) counts towards
        opening it, unless the one before is more than FAILURE_GAP_SECONDS old (the count
        starts again). Another model's breaker is not touched."""
        with self._lock:
            breaker = self._breakers.setdefault(model, _Breaker())
            breaker.probing = False
            if not failed:
                breaker.state, breaker.failures, breaker.down_since = _CLOSED, 0, None
                breaker.last_failure_at = None
                self._key_latched_at = None
                if model == self._reader_model:
                    self._reader_unfixable_at = None
                return
            now = self.clock()
            if breaker.last_failure_at is not None and now - breaker.last_failure_at > FAILURE_GAP_SECONDS:
                breaker.failures, breaker.down_since = 0, None
            breaker.last_failure_at = now
            breaker.failures += 1
            if breaker.down_since is None:
                breaker.down_since = now
            if breaker.state == _HALF_OPEN or breaker.failures >= BREAKER_FAILURES:
                if breaker.state != _OPEN:
                    _log.warning("model circuit open", extra={"code": "circuit_open"})
                breaker.state, breaker.opened_at = _OPEN, now

    def latch_key(self, code: str = "key_rejected") -> None:
        """A 401, 402 or key 403: every call fails at once for KEY_LATCH_SECONDS, whatever
        the model (one key); logged once at error, with ``code`` (``no_credits`` for a 402)."""
        with self._lock:
            for breaker in self._breakers.values():
                breaker.probing = False
            first = self._key_latched_at is None
            self._key_latched_at = self.clock()
        if first:
            _log.error("openrouter key rejected", extra={"code": code})

    def reader_unfixable(self, model: str, code: str) -> None:
        """Issue 407: a 404 or a redirect (``code``) for OCR_MODEL, the platform's reader. No
        wait fixes it and its documents wait for a person (gemini_ocr KEY_CODES), so ``health``
        says ``key_problem`` for KEY_LATCH_SECONDS: Health asks for action. ``status`` is not
        changed and no call is refused, so admission, the sweeps, chat and the organisations
        reading with another model go on. Another model's (one organisation's choice, read
        locally) changes nothing. Logged once at error."""
        if model != self._reader_model:
            return
        with self._lock:
            first = self._reader_unfixable_at is None
            self._reader_unfixable_at = self.clock()
        if first:
            _log.error("reading model unusable", extra={"code": code})


_PROCESS_GATES: dict[tuple[str, int, int, int], ModelGate] = {}
_PROCESS_GATES_LOCK = threading.Lock()


def process_gate(
    settings: Settings, *, workers: int | None = None, database: Database | None = None
) -> ModelGate:
    """This process's one gate for OPENROUTER_BASE_URL at MODEL_RPM over ``workers``, on the
    real clock: every client built for the product shares it. ``workers`` None is
    WORKER_REPLICAS + 1, the worker copies and the API (PRD 5 V7)."""
    if workers is None:
        workers = settings.worker_replicas + 1
    key = (
        settings.openrouter_base_url,
        settings.model_rpm,
        workers,
        0 if database is None else id(database.engine),
    )
    with _PROCESS_GATES_LOCK:
        gate = _PROCESS_GATES.get(key)
        if gate is None:
            gate = _PROCESS_GATES[key] = ModelGate(settings, workers=workers, database=database)
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
        others = (settings.model_chat, settings.model_embed) if settings.model_enabled else ()
        for model in (settings.ocr_model, *others):
            if not valid_model_id(model):
                raise ValueError("a model ID must be an OpenRouter author/slug with no :variant")
        self._key = settings.openrouter_api_key
        self._https = parts.scheme == "https"
        self._host_path = settings.openrouter_base_url.rstrip("/").split("://", 1)[1]
        self._timeout = settings.model_timeout_seconds
        self._chat_timeout = self._timeout if chat_timeout_seconds is None else chat_timeout_seconds
        self._stream_timeout = settings.answer_stream_max_seconds  # a streamed reply's own limit (#535)
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
        self._check_provider(DEFAULT_PROVIDER)
        step = EMBED_TEXTS_PER_REQUEST
        batches = [texts[start : start + step] for start in range(0, len(texts), step)]
        if not batches:
            return []
        # Fast scan (council 16 Oct 2026): the requests at the same time, EMBED_FAN_OUT at
        # most, each through the one gate (its limiter still spaces their starts) in its own
        # copy of the context (billing, SP1); the vectors stay in the texts' order, and the
        # first failure in that order is raised.
        with ThreadPoolExecutor(min(EMBED_FAN_OUT, len(batches))) as pool:
            futures = [
                pool.submit(contextvars.copy_context().run, self._embed_batch, batch) for batch in batches
            ]
        return [vector for future in futures for vector in future.result()]

    def _embed_batch(self, batch: list[str]) -> list[list[float]]:
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
        return self._in_order(data, len(batch))

    def chat_json(
        self,
        *,
        system: str,
        user: str,
        schema: dict[str, Any],
        max_tokens: int | None = None,
        model: str | None = None,
        thinking: str | None = None,
        provider: str | None = None,
        deadline: float | None = None,
        temperature: float | None = None,
    ) -> dict[str, Any]:
        """One JSON object following ``schema``, from ``model`` at ``thinking`` through
        ``provider`` (None: MODEL_CHAT, CHAT_THINKING_LEVEL, DEFAULT_PROVIDER; features
        25-27, C7), with CHAT_THINKING_TOKENS of the level to think in. A provider outside
        OPENROUTER_PROVIDERS raises ``ProviderNotAllowed`` before anything is sent. A first
        reply that does not follow it goes back once for a repair; a second bad one raises
        ``invalid_output``. A reply longer than ``max_tokens`` (thinking not counted) raises
        ``output_truncated`` with no repair. ``deadline`` (on ``time.monotonic()``): both
        tries end by then (``timeout`` once it has passed); None, each has the chat
        timeout. ``temperature``: sent only to a model of TEMPERATURE_VENDORS; None, the
        default."""
        body, output_limit, ends = self._chat_body(
            system, user, schema, max_tokens, model, thinking, provider, deadline, temperature
        )
        messages = body["messages"]
        content, spent = self._chat_reply(body, output_limit, (0, 0), ends)
        value, problem = parse_reply(content, schema)
        if value is not None:
            return value
        messages.append({"role": "assistant", "content": content})
        messages.append({"role": "user", "content": REPAIR.format(problem=problem)})
        content, spent = self._chat_reply(body, output_limit, spent, ends)
        value, _ = parse_reply(content, schema)
        if value is None:
            raise ModelOutputError("invalid_output", input_tokens=spent[0], output_tokens=spent[1])
        return value

    def chat_json_stream(
        self,
        *,
        system: str,
        user: str,
        schema: dict[str, Any],
        on_text: Callable[[str], None],
        max_tokens: int | None = None,
        model: str | None = None,
        thinking: str | None = None,
        provider: str | None = None,
        deadline: float | None = None,
    ) -> dict[str, Any]:
        """``chat_json`` streamed (#535, Ask's draft; the module docstring): the same request with
        ``stream: true``, each piece of the reply's text handed to ``on_text`` as it comes (""
        for a line with none, so the caller may stop the reply by raising: the connection is
        closed and nothing more is read). It ends by ``deadline`` (None: ANSWER_STREAM_MAX_SECONDS
        from now), and fails as ``timeout`` sooner only when nothing arrives for the chat timeout
        (MODEL_TIMEOUT_SECONDS). No repair turn: a reply that does not follow ``schema`` raises
        ``invalid_output``, with its tokens."""
        body, output_limit, ends = self._chat_body(
            system, user, schema, max_tokens, model, thinking, provider, deadline, None, stream=True
        )
        content, spent = self._chat_reply(body, output_limit, (0, 0), ends, on_text=on_text)
        value, _ = parse_reply(content, schema)
        if value is None:
            raise ModelOutputError("invalid_output", input_tokens=spent[0], output_tokens=spent[1])
        return value

    def _chat_body(
        self,
        system: str,
        user: str,
        schema: dict[str, Any],
        max_tokens: int | None,
        model: str | None,
        thinking: str | None,
        provider: str | None,
        deadline: float | None,
        temperature: float | None,
        *,
        stream: bool = False,
    ) -> tuple[dict[str, Any], int, float | None]:
        """``chat_json``'s request body, the reply's own budget and the deadline on ``now()``
        (None: each call's chat timeout); ValueError or ``ProviderNotAllowed`` before anything
        is sent."""
        if model is not None and not valid_model_id(model):
            raise ValueError("a model ID must be an OpenRouter author/slug with no :variant")
        slug = DEFAULT_PROVIDER if provider is None else provider
        self._check_provider(slug)
        level = CHAT_THINKING_LEVEL if thinking is None else thinking
        if level not in CHAT_THINKING_TOKENS and level != NO_THINKING:
            raise ValueError("unknown thinking level")
        ends = None if deadline is None else self.now() + (deadline - time.monotonic())
        messages: list[dict[str, Any]] = [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ]
        output_limit = self._default_chat_tokens if max_tokens is None else max_tokens
        chosen = self.chat_model if model is None else model
        body = chat_request(
            model=chosen,
            messages=messages,
            thinking=level,
            schema=schema,
            max_output_tokens=output_limit + CHAT_THINKING_TOKENS.get(level, 0),
            provider_slug=slug,
            stream=stream,
        )
        if temperature is not None and chosen.split("/", 1)[0] in TEMPERATURE_VENDORS:
            body["temperature"] = temperature
        return body, output_limit, ends

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
        validate: Callable[[dict[str, Any]], str | None] | None = None,
        max_output_tokens: int | None = None,
        budget: PageBudget | None = None,
    ) -> PageReply:
        """One page read by ``model`` through ``provider`` (None: OCR_MODEL through
        DEFAULT_PROVIDER, as before model choice) as a JSON object following ``schema``,
        before ``deadline`` (an absolute time on ``now()``; every retry fits inside it). A
        provider outside OPENROUTER_PROVIDERS raises ``ProviderNotAllowed`` before anything is
        sent. A first reply that does not follow the schema, or that ``validate`` refuses (it
        gives the problem in words, None when the value is good), is sent back once for a
        repair, with the page again; the tokens of both calls are counted. ``max_output_tokens``
        (#463: the organisation's limit; None: PAGE_MAX_OUTPUT_TOKENS) is sent as
        max_completion_tokens; a reply that reaches it raises ``output_truncated`` as before,
        counted on the ledger as a limit hit. #488: with a ``budget`` (Ultra low cost) every
        call is held under it (``_read_within``)."""
        if model is not None and not valid_model_id(model):
            raise ValueError("a model ID must be an OpenRouter author/slug with no :variant")
        self._check_provider(DEFAULT_PROVIDER if provider is None else provider)
        limit = PAGE_MAX_OUTPUT_TOKENS if max_output_tokens is None else max_output_tokens
        if budget is not None:
            cap = budget.cap()
            if cap is None:
                raise ModelOutputError(COST_CEILING)  # nothing sent
            limit = min(limit, cap)
        body = page_request(
            model=self.ocr_model if model is None else model,
            png=png,
            instruction=instruction,
            schema=schema,
            media_resolution=media_resolution,
            thinking=thinking_level,
            max_output_tokens=limit,
            provider_slug=DEFAULT_PROVIDER if provider is None else provider,
        )
        sent = (thinking_level, media_resolution)  # #463: the ledger's settings of a page read
        if budget is not None:
            return self._read_within(body, schema, validate, deadline, sent, budget)
        reply = self._call(CHAT_PATH, body, deadline, sent=sent)
        input_tokens, output_tokens = reply_usage(reply)
        content, finish = _page_text(reply, (0, 0), str(body["model"]), sent)
        value, problem = _valid_reply(content, schema, validate)
        if value is None:
            body["messages"].append({"role": "assistant", "content": content})
            body["messages"].append({"role": "user", "content": REPAIR.format(problem=problem)})
            reply = self._call(CHAT_PATH, body, deadline, sent=sent)
            content, finish = _page_text(reply, (input_tokens, output_tokens), str(body["model"]), sent)
            more_in, more_out = reply_usage(reply)
            input_tokens, output_tokens = input_tokens + more_in, output_tokens + more_out
            value, _ = _valid_reply(content, schema, validate)
            if value is None:
                raise ModelOutputError(
                    "invalid_output", input_tokens=input_tokens, output_tokens=output_tokens
                )
        return PageReply(value, input_tokens, output_tokens, finish)

    def _read_within(
        self,
        body: dict[str, Any],
        schema: Mapping[str, Any],
        validate: Callable[[dict[str, Any]], str | None] | None,
        deadline: float,
        sent: tuple[str, str],
        budget: PageBudget,
    ) -> PageReply:
        """#488: ``read_page`` held under ``budget``: each billed reply is charged to it (a
        mid-reply error too, retried only with an output limit it still affords, ``_call``);
        the one repair is sent only when the budget affords its input (the page, the reply and
        the problem, at most their bytes, REPAIR_INPUT_MARGIN more) and an output limit of at
        least MIN_CAP_TOKENS, else ``ModelOutputError("cost_ceiling")``, counted on the ledger
        as a limit hit. The reply's tokens, and any error's, are every billed call's."""
        model = str(body["model"])
        try:
            reply = self._call(CHAT_PATH, body, deadline, sent=sent, budget=budget)
            content, finish = _page_text(reply, (0, 0), model, sent)
            value, problem = _valid_reply(content, schema, validate)
            if value is None:
                repair = REPAIR.format(problem=problem)
                page = max(reply_usage(reply)[0], budget.page_tokens())  # #488 review: never 0 unsaid
                said = page + len(content.encode("utf-8")) + len(repair.encode("utf-8"))
                tokens_in = math.ceil(REPAIR_INPUT_MARGIN * said)
                cap = budget.cap(tokens_in)
                if cap is None:
                    usage.record_limit_hit(model, thinking=sent[0], resolution=sent[1])
                    raise ModelOutputError(COST_CEILING)
                body["messages"].append({"role": "assistant", "content": content})
                body["messages"].append({"role": "user", "content": repair})
                body["max_completion_tokens"] = cap
                reply = self._call(CHAT_PATH, body, deadline, sent=sent, budget=budget, tokens_in=tokens_in)
                content, finish = _page_text(reply, (0, 0), model, sent)
                value, _ = _valid_reply(content, schema, validate)
                if value is None:
                    raise ModelOutputError("invalid_output")
        except ModelOutputError as error:
            tokens = {"input_tokens": budget.input_tokens, "output_tokens": budget.output_tokens}
            raise ModelOutputError(error.code, **tokens) from None
        except ModelUnavailable as error:
            tokens = {"input_tokens": budget.input_tokens, "output_tokens": budget.output_tokens}
            raise ModelUnavailable(error.code, **tokens) from None
        return PageReply(value, budget.input_tokens, budget.output_tokens, finish)

    def _check_provider(self, slug: str) -> None:
        """``ProviderNotAllowed`` unless ``slug`` is in OPENROUTER_PROVIDERS: nothing is sent."""
        if slug not in self._providers:
            raise ProviderNotAllowed("provider_not_allowed")

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
        self,
        body: dict[str, Any],
        max_tokens: int | None,
        before: tuple[int, int],
        deadline: float | None,
        *,
        on_text: Callable[[str], None] | None = None,
    ) -> tuple[str, tuple[int, int]]:
        """(the reply's text, the tokens billed so far: ``before`` plus this reply's), by
        ``deadline`` on ``now()`` (None: the chat timeout from now). Its
        ``ModelOutputError`` carries the same tokens. ``on_text``: a streamed reply's (#535), whose
        limit with no ``deadline`` is ANSWER_STREAM_MAX_SECONDS, the chat timeout being its idle one."""
        whole = self._chat_timeout if on_text is None else self._stream_timeout
        ends = self.now() + whole if deadline is None else deadline
        reply = self._call(CHAT_PATH, body, ends, on_text=on_text)
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

    def _call(
        self,
        path: str,
        body: Mapping[str, Any],
        deadline: float,
        *,
        sent: tuple[str, str] | None = None,
        budget: PageBudget | None = None,
        tokens_in: int | None = None,
        on_text: Callable[[str], None] | None = None,
    ) -> dict[str, Any]:
        """One request with the latch, the body's model's breaker, the limiter and retries
        (G6, G9). Returns the reply object of a 200; raises a coded error otherwise. ``sent``:
        a page read's (thinking, picture detail), for the ledger (#463). #488: each billed reply
        is charged to ``budget``, and a retry is sent with the output limit it still affords,
        or not at all (``cost_ceiling``, a limit hit): never past the page's ceiling; ``tokens_in``
        the request's input at most (None: the page's, ``PageBudget.cap``). #488 review: a call
        that may have been billed with no usage to say so (``_Reply.maybe_billed``, a reply with
        none) is charged its worst case. ``on_text`` (#535): a streamed reply's pieces of text; once
        one went out, a failure is raised at once, never retried (the text would go out twice), and
        whatever the caller raises from it releases the breaker's probe and goes up as it is."""
        gate = self.gate
        model = str(body["model"])
        attempt = 0
        worst = 0.0
        handed_out = False

        def heard(text: str) -> None:
            nonlocal handed_out
            handed_out = handed_out or bool(text)
            if on_text is not None:
                on_text(text)

        while True:
            if budget is not None and attempt:
                cap = budget.cap(tokens_in)
                if cap is None:
                    if sent is not None:
                        usage.record_limit_hit(model, thinking=sent[0], resolution=sent[1])
                    raise ModelOutputError(COST_CEILING)
                body = {**body, "max_completion_tokens": min(cap, int(body["max_completion_tokens"]))}
            if budget is not None:
                worst = budget.worst(tokens_in, int(body["max_completion_tokens"]))
            gate.admit(model)
            start = gate.take_slot(deadline, model)
            ahead = start - gate.clock()
            if ahead > 0:
                gate.sleep(ahead)
                waited = LIMITER_WAIT.get()
                if waited is not None:
                    waited[0] += ahead
            remaining = deadline - gate.clock()
            if remaining <= 0:
                gate.release_probe(model)
                raise ModelUnavailable("timeout")
            try:
                reply = self._send(
                    path,
                    body,
                    remaining,
                    on_text=None if on_text is None else heard,
                    idle=None if on_text is None else self._chat_timeout,
                )
            except BaseException:  # the caller stopped a streamed reply (#535): no outcome to record
                gate.release_probe(model)
                raise
            if budget is not None and reply.maybe_billed:
                budget.charge_unknown(worst)
            code: str | None
            if 200 <= reply.status < 300:
                if reply.body is None:
                    gate.record(model, failed=False)
                    raise ModelUnavailable(reply.failure or "bad_response")
                if not failed_mid_reply(reply.body):
                    gate.record(model, failed=False)
                    self._count(reply.body, model, sent, budget, worst)
                    return reply.body
                self._count(
                    reply.body, model, sent, budget, worst
                )  # a reply that failed part way may still be billed
                code = "server_error"
            elif reply.status in _KEY_STATUSES:
                if reply.status == 403 and (
                    reply.codes.moderated or reply.codes.error_type in REFUSAL_ERROR_TYPES
                ):
                    gate.record(model, failed=False)
                    raise ModelOutputError("blocked")
                gate.latch_key(_KEY_STATUSES[reply.status])
                raise ModelUnavailable("key_rejected")
            elif reply.status == 0:
                code = reply.failure
            else:
                code = _RETRY_STATUSES.get(reply.status) or ("server_error" if reply.status >= 500 else None)
            if code is None:  # the server answered: it is up, but this request is wrong
                gate.record(model, failed=False)
                if reply.status in _BAD_REQUEST_STATUSES:
                    raise ModelOutputError("bad_request")
                if reply.status == 404:
                    gate.reader_unfixable(model, "model_not_found")
                    raise ModelUnavailable("model_not_found")
                if 300 <= reply.status < 400:
                    gate.reader_unfixable(model, "redirect_refused")
                    raise ModelUnavailable("redirect_refused")
                raise ModelUnavailable("http_error")
            if reply.status == 0 and code == "timeout":
                # Our own deadline ran out (a slow page at high thinking), not the model failing:
                # never counted on the breaker, so slow pages cannot open it and make every other
                # page of the document wait for its lease (scan reliability, 16 Oct 2026).
                gate.release_probe(model)
                raise ModelUnavailable("timeout")
            gate.record(model, failed=True)
            if handed_out:  # #535: never sent again once a streamed reply handed out text
                raise ModelUnavailable(code)
            wait = gate.random() * min(BACKOFF_MAX_SECONDS, BACKOFF_BASE_SECONDS * 2**attempt)
            asked = _retry_after(reply.headers, gate.wall_clock())
            if asked is not None:
                wait = max(wait, asked)
            if gate.clock() + wait >= deadline:
                raise ModelUnavailable(code)
            gate.sleep(wait)
            attempt += 1

    def _count(
        self,
        reply: Mapping[str, Any],
        model: str,
        sent: tuple[str, str] | None = None,
        budget: PageBudget | None = None,
        worst: float = 0.0,
    ) -> None:
        """Count one billed reply (G9): this client's totals, and the current usage scope's
        ledger (SP1, ``kaagaz.ai.usage``) under the model asked for and the provider the
        reply names, if it looks like a name; #488: and charge it to ``budget`` (``worst``, the
        most the call could cost, when the reply does not say its tokens). Every billed reply
        comes here: a blocked, cut off or repaired one too."""
        input_tokens, output_tokens = reply_usage(reply)
        cost = reply_cost(reply)
        if budget is not None:
            if _says_usage(reply):
                budget.charge(input_tokens, output_tokens, cost)
            else:
                budget.charge_unknown(max(cost or 0.0, worst))
        named = reply.get("provider")
        provider_name = named if isinstance(named, str) and _PROVIDER_NAME.fullmatch(named) else None
        thinking, resolution = sent or (None, None)
        usage.record_call(
            model,
            provider_name,
            input_tokens,
            output_tokens,
            reasoning_tokens(reply),
            cost,
            thinking=thinking,
            resolution=resolution,
        )
        with self._lock:
            self._input_tokens += input_tokens
            self._output_tokens += output_tokens
            if cost is not None:
                self._cost = cost if self._cost is None else self._cost + cost

    def _bill_partial(self, body: Mapping[str, Any] | None, texts: list[str]) -> None:
        """#535 (review): a streamed reply that ended part way (stopped by the cap, timed out, cut
        off, malformed or too large) was billed for what it generated, with no usage to say how
        much: one ledger row at its worst case (``_worst_usage``), never 0, never missing."""
        if body is None:
            return
        _log.warning(
            "streamed reply ended part way, billed at its worst case", extra={"code": "stream_partial"}
        )
        self._count({"usage": _worst_usage(body, "".join(texts))}, str(body["model"]))

    def _send(
        self,
        path: str,
        body: Mapping[str, Any] | None,
        timeout: float,
        *,
        send_key: bool = True,
        on_text: Callable[[str], None] | None = None,
        idle: float | None = None,
    ) -> _Reply:
        """One POST (or a GET when ``body`` is None). Never raises for HTTP or the network:
        the outcome is a ``_Reply``. ``on_text``: a reply sent as ``text/event-stream`` is read
        as it comes (``_read_stream``, #535); whatever ``on_text`` raises goes up. ``idle``: each
        wait on the socket (connecting, the headers, every read) ends after this long with
        nothing (a ``timeout``), while ``timeout`` stays the whole exchange's limit."""
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
        ends = time.monotonic() + timeout
        try:
            with self._opener.open(
                request, timeout=timeout if idle is None else min(idle, timeout)
            ) as response:
                if on_text is not None and response.headers.get_content_type() == "text/event-stream":
                    texts: list[str] = []
                    try:
                        streamed = _read_stream(response, ends, replies.MAX_RESPONSE_BYTES, on_text, texts)
                    except BaseException:  # the caller stopped it (the cap), or the socket failed
                        self._bill_partial(body, texts)
                        raise
                    if isinstance(streamed, str):
                        self._bill_partial(body, texts)
                        if streamed == "timeout":
                            return _Reply(0, None, None, "timeout")
                        return _Reply(response.status, response.headers, None, streamed)
                    if body is not None and not _says_usage(streamed):
                        _log.warning("streamed reply without usage", extra={"code": "stream_usage_missing"})
                        content = streamed["choices"][0]["message"]["content"]
                        streamed["usage"] = _worst_usage(body, content)
                    return _Reply(response.status, response.headers, streamed)
                raw = _read_until(response, ends, replies.MAX_RESPONSE_BYTES + 1)
                if raw is None:
                    return _Reply(0, None, None, "timeout")
                status, reply_headers = response.status, response.headers
        except urllib.error.HTTPError as error:
            return _Reply(error.code, error.headers, None, codes=_error_codes(error))
        except urllib.error.URLError as error:
            # urllib raises it only while connecting or sending: the request never all arrived.
            failure = "timeout" if isinstance(error.reason, TimeoutError) else "unreachable"
            return _Reply(0, None, None, failure, sent=False)
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
