"""The one model client: a local Ollama behind a small interface.

Everything else in the backend depends on ``ModelClient`` only, so tests use
fakes and the product runs with no AI at all (``MODEL_ENABLED=false`` gives
``build_model_client(...) is None``, and callers fall back: search without
meaning vectors, answers that quote the evidence).

Document text never leaves the laptop or our own server: the base URL comes
from settings, and the opener ignores ``HTTP_PROXY`` style variables, so text
cannot leak to a proxy. Prompts are the callers' job (they fence document text
as data). This client never logs a prompt, a reply or an image, and its errors
carry short codes only.

Two errors, because callers treat them differently:

* ``ModelUnavailable``: no usable HTTP reply (down, refused, timeout, a non-2xx
  status, a body that is not a JSON object, or a reply without the expected
  envelope). Safe to retry later; callers carry on without the model.
* ``ModelOutputError``: the model replied, but its content is unusable: chat
  content that is not a JSON object matching the schema even after one repair
  retry, or embeddings of the wrong count, dimension or values.

The Ollama API shapes below are from Ollama's API documentation as known when
this was written. UNVERIFIED against a running server (Ollama is not installed
on the build laptop; tests use a local fake):

* ``POST /api/embed`` ``{"model", "input": [..]}`` replies ``{"embeddings": [[..]]}``.
* ``POST /api/chat`` ``{"model", "messages", "stream": false, "format": <JSON
  schema>, "think": false, "options": {"temperature": 0, "seed": 0, "num_ctx":
  n, "num_predict": n}}`` replies ``{"message": {"role": "assistant",
  "content": "<text>"}, "prompt_eval_count": n, "eval_count": n}``.
* Images go in a message's ``images`` list as base64 strings (no data: prefix).
* Sending ``"think": false`` to a model without thinking is accepted.

**Budgets** (PRD 2 ML amendment 5). Every call sends the same fixed options:
temperature 0, seed 0 and the context window ``MODEL_NUM_CTX``; a caller that
gives ``max_tokens`` also sends it as ``num_predict``. When the reply carries
its token counts, two guards run before the reply is read:

* ``context_overflow``: prompt and reply together filled the window, so Ollama
  cut the prompt or shifted it out (``prompt_eval_count + eval_count >= num_ctx``);
* ``output_truncated``: the reply stopped at its budget (``eval_count >=
  max_tokens``, or the reply says ``"done_reason": "length"``, council fix 10).

Either raises ``ModelOutputError`` with no repair retry: a cut reply would be
cut again. UNVERIFIED: that Ollama 0.34.4 sends both counts on a
non-streaming chat reply, and whether a cached prompt prefix is left out of
``prompt_eval_count`` (that would only make the first guard fire later).
``estimate_tokens`` (``estimate_tokens_cautiously`` for the answer's evidence) and
``input_token_budget`` let callers cut their input to fit before calling. The
worker's chat calls (the card and the invoice windows) may use their own timeout,
``MODEL_EXTRACT_TIMEOUT_SECONDS``.

Cost: ``embed`` makes ceil(n / 32) requests for n texts and holds O(n * dim)
floats. ``chat_json`` makes at most two requests; checking a reply against the
schema is O(size of the reply). ``estimate_tokens`` is O(n) in the text.
"""

import base64
import json
import math
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Mapping
from http.client import HTTPException
from typing import Any, Protocol

from kaagaz.ai.gemini import GeminiClient
from kaagaz.ai.replies import EMBED_BATCH_SIZE as EMBED_BATCH_SIZE
from kaagaz.ai.replies import MAX_RESPONSE_BYTES as MAX_RESPONSE_BYTES
from kaagaz.ai.replies import REPAIR as _REPAIR
from kaagaz.ai.replies import ModelOutputError as ModelOutputError
from kaagaz.ai.replies import ModelUnavailable as ModelUnavailable
from kaagaz.ai.replies import parse_reply as _parse_reply
from kaagaz.ai.replies import schema_problem as schema_problem
from kaagaz.settings import CHAT_OVERHEAD_TOKENS, Settings, is_gemini_model


class ModelClient(Protocol):
    chat_model: str
    embed_model: str

    def embed(self, texts: list[str]) -> list[list[float]]:
        """One vector per text, in order."""
        ...

    def chat_json(
        self, *, system: str, user: str, schema: dict[str, Any], max_tokens: int | None = None
    ) -> dict[str, Any]:
        """One JSON object that follows ``schema``, in at most ``max_tokens`` reply tokens."""
        ...

    def read_image(self, *, png: bytes, instruction: str) -> str:
        """The text the vision model reads in ``png``; empty if it sees none."""
        ...


def estimate_tokens(text: str) -> int:
    """About how many tokens ``text`` is (ML amendment 5): an ASCII character is a
    quarter of a token, any other (Devanagari) two thirds of one. Rounded up. O(n)."""
    other = sum(1 for ch in text if ch >= "\x80")
    return -(-(3 * (len(text) - other) + 8 * other) // 12)  # ascii / 4 + other / 1.5, rounded up


def estimate_tokens_cautiously(text: str) -> int:
    """``estimate_tokens``, but a letter or mark that is not ASCII (Unicode categories L
    and M: the letters and vowel signs of Devanagari, Gujarati, Tamil, Arabic... alike) is
    one whole token. Never below ``estimate_tokens``. Rounded up. O(n).
    PROVISIONAL (council 4 Oct 2026, PR C), for the answer's evidence: a small model's
    tokenizer splits the letters of most scripts into more than two thirds of a token
    each, so evidence counted the first way could fill the window; counting more only
    leaves out more low-ranked evidence (``draft.fit_evidence``). The card and invoice
    windows keep ``estimate_tokens``."""
    if text.isascii():
        return estimate_tokens(text)
    twelfths = sum(3 if ch < "\x80" else 12 if unicodedata.category(ch)[0] in "LM" else 8 for ch in text)
    return -(-twelfths // 12)


def input_token_budget(*, num_ctx: int, max_tokens: int, system: str) -> int:
    """How many tokens the user message may take so the call fits ``num_ctx``: the room
    left after the system prompt, the reply, and a repair retry (which sends the first
    reply back and gets a second one), less ``CHAT_OVERHEAD_TOKENS``. Never below 0."""
    return max(0, num_ctx - 2 * max_tokens - estimate_tokens(system) - CHAT_OVERHEAD_TOKENS)


def _count(reply: Mapping[str, Any], key: str) -> int | None:
    """A token count from the reply, or None when it is missing or not a whole number."""
    value = reply.get(key)
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else None


class OllamaClient:
    """``ModelClient`` over Ollama's HTTP API, with the standard library only."""

    def __init__(self, settings: Settings, *, chat_timeout_seconds: float | None = None) -> None:
        """``chat_timeout_seconds`` is for ``chat_json`` only (the worker passes
        MODEL_EXTRACT_TIMEOUT_SECONDS); everything else uses MODEL_TIMEOUT_SECONDS."""
        base_url = settings.model_base_url.rstrip("/")
        if urllib.parse.urlsplit(base_url).scheme not in {"http", "https"}:
            raise ValueError("model_base_url must be an http or https URL")
        self._base_url = base_url
        self._timeout = settings.model_timeout_seconds
        self._chat_timeout = self._timeout if chat_timeout_seconds is None else chat_timeout_seconds
        self._num_ctx = settings.model_num_ctx
        self._dim = settings.embedding_dim
        self._vision_model = settings.model_vision
        self.chat_model = settings.model_chat
        self.embed_model = settings.model_embed
        # An empty ProxyHandler turns off proxies from the environment.
        self._opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))

    def embed(self, texts: list[str]) -> list[list[float]]:
        vectors: list[list[float]] = []
        for start in range(0, len(texts), EMBED_BATCH_SIZE):
            batch = texts[start : start + EMBED_BATCH_SIZE]
            reply = self._post("/api/embed", {"model": self.embed_model, "input": batch}, self._timeout)
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
        """If the first reply does not follow the schema, the problem goes back to
        the model once so it can repair it. A second bad reply raises
        ``ModelOutputError("invalid_output")``; a reply that reached a token limit
        raises at once (the module docstring)."""
        messages: list[dict[str, Any]] = [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ]
        content = self._chat(self.chat_model, messages, schema, max_tokens, self._chat_timeout)
        value, problem = _parse_reply(content, schema)
        if value is not None:
            return value
        messages.append({"role": "assistant", "content": content})
        messages.append({"role": "user", "content": _REPAIR.format(problem=problem)})
        content = self._chat(self.chat_model, messages, schema, max_tokens, self._chat_timeout)
        value, _ = _parse_reply(content, schema)
        if value is None:
            raise ModelOutputError("invalid_output")
        return value

    def read_image(self, *, png: bytes, instruction: str) -> str:
        if self._vision_model is None:
            raise ModelUnavailable("no_vision_model")
        image = base64.b64encode(png).decode("ascii")
        messages = [{"role": "user", "content": instruction, "images": [image]}]
        return self._chat(self._vision_model, messages, None, None, self._timeout).strip()

    def _chat(
        self,
        model: str,
        messages: list[dict[str, Any]],
        schema: dict[str, Any] | None,
        max_tokens: int | None,
        timeout: float,
    ) -> str:
        options: dict[str, Any] = {"temperature": 0, "seed": 0, "num_ctx": self._num_ctx}
        if max_tokens is not None:
            options["num_predict"] = max_tokens
        body: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "stream": False,
            "think": False,
            "options": options,
        }
        if schema is not None:
            body["format"] = schema
        reply = self._post("/api/chat", body, timeout)
        message = reply.get("message")
        content = message.get("content") if isinstance(message, dict) else None
        if not isinstance(content, str):
            raise ModelUnavailable("bad_response")
        prompt_tokens, reply_tokens = _count(reply, "prompt_eval_count"), _count(reply, "eval_count")
        if (
            prompt_tokens is not None
            and reply_tokens is not None
            and prompt_tokens + reply_tokens >= self._num_ctx
        ):
            raise ModelOutputError("context_overflow")
        at_budget = max_tokens is not None and reply_tokens is not None and reply_tokens >= max_tokens
        if at_budget or reply.get("done_reason") == "length":
            raise ModelOutputError("output_truncated")
        return content

    def _vector(self, item: object) -> list[float]:
        if not isinstance(item, list):
            raise ModelOutputError("embedding_value")
        if len(item) != self._dim:
            raise ModelOutputError("embedding_dim")
        vector: list[float] = []
        for number in item:
            if isinstance(number, bool) or not isinstance(number, int | float) or not math.isfinite(number):
                raise ModelOutputError("embedding_value")
            vector.append(float(number))
        return vector

    def _post(self, path: str, body: dict[str, Any], timeout: float) -> dict[str, Any]:
        request = urllib.request.Request(  # noqa: S310 - the scheme is checked to be http(s) in __init__
            self._base_url + path,
            data=json.dumps(body).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with self._opener.open(request, timeout=timeout) as response:
                raw = response.read(MAX_RESPONSE_BYTES + 1)
        except urllib.error.HTTPError as error:
            error.close()
            raise ModelUnavailable("http_error") from None
        except urllib.error.URLError as error:
            code = "timeout" if isinstance(error.reason, TimeoutError) else "unreachable"
            raise ModelUnavailable(code) from None
        except TimeoutError:
            raise ModelUnavailable("timeout") from None
        except (OSError, HTTPException):
            raise ModelUnavailable("unreachable") from None
        if len(raw) > MAX_RESPONSE_BYTES:
            raise ModelUnavailable("response_too_large")
        try:
            reply = json.loads(raw)
        except ValueError:
            raise ModelUnavailable("bad_response") from None
        if not isinstance(reply, dict):
            raise ModelUnavailable("bad_response")
        return reply


class _ChatAndEmbed:
    """Two clients as one ``ModelClient`` (PRD 5 question 4): chat and pictures go to
    one, embeddings to the other, so search vectors stay on their own model until M5."""

    def __init__(self, chat: ModelClient, embed: ModelClient) -> None:
        self._chat, self._embed = chat, embed
        self.chat_model, self.embed_model = chat.chat_model, embed.embed_model

    def embed(self, texts: list[str]) -> list[list[float]]:
        return self._embed.embed(texts)

    def chat_json(
        self, *, system: str, user: str, schema: dict[str, Any], max_tokens: int | None = None
    ) -> dict[str, Any]:
        return self._chat.chat_json(system=system, user=user, schema=schema, max_tokens=max_tokens)

    def read_image(self, *, png: bytes, instruction: str) -> str:
        return self._chat.read_image(png=png, instruction=instruction)


def build_model_client(
    settings: Settings, *, chat_timeout_seconds: float | None = None
) -> ModelClient | None:
    """The configured model client, or None when the product runs without AI.
    ``chat_timeout_seconds``: see ``OllamaClient``.

    PRD 5 G9 and question 4: with MODEL_ENABLED and a Gemini MODEL_CHAT, chat_json and
    read_image go to ``GeminiClient`` (MODEL_CHAT and OCR_MODEL); embed stays on Ollama
    unless MODEL_EMBED is a Gemini model too, which M5 switches in pull request 3 after
    its Hindi and Marathi search test. Ollama models alone give ``OllamaClient`` as before.
    """
    if not settings.model_enabled:
        return None
    gemini_chat, gemini_embed = is_gemini_model(settings.model_chat), is_gemini_model(settings.model_embed)
    if not (gemini_chat or gemini_embed):
        return OllamaClient(settings, chat_timeout_seconds=chat_timeout_seconds)
    gemini = GeminiClient(settings, chat_timeout_seconds=chat_timeout_seconds)
    if gemini_chat and gemini_embed:
        return gemini
    ollama = OllamaClient(settings, chat_timeout_seconds=chat_timeout_seconds)
    return _ChatAndEmbed(gemini, ollama) if gemini_chat else _ChatAndEmbed(ollama, gemini)
