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
  schema>, "think": false, "options": {"temperature": 0}}`` replies
  ``{"message": {"role": "assistant", "content": "<text>"}}``.
* Images go in a message's ``images`` list as base64 strings (no data: prefix).
* Sending ``"think": false`` to a model without thinking is accepted.

Cost: ``embed`` makes ceil(n / 32) requests for n texts and holds O(n * dim)
floats. ``chat_json`` makes at most two requests; checking a reply against the
schema is O(size of the reply).
"""

import base64
import json
import math
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable, Mapping
from http.client import HTTPException
from typing import Any, Protocol

from kaagaz.settings import Settings

# Texts per embedding request: one CPU-sized model call, and no round trip per piece.
EMBED_BATCH_SIZE = 32

# A reply larger than this is refused. 32 vectors of 1024 floats as JSON are
# under 1 MB; a chat reply is a few kilobytes.
MAX_RESPONSE_BYTES = 16 * 1024 * 1024

_REPAIR = (
    "Your reply was not valid: {problem}. Reply again with only the JSON object that follows the schema."
)


class ModelUnavailable(Exception):
    """No usable reply from the model server. ``code`` is short and safe to log."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


class ModelOutputError(Exception):
    """The model replied with unusable content. ``code`` is short and safe to log."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


class ModelClient(Protocol):
    chat_model: str
    embed_model: str

    def embed(self, texts: list[str]) -> list[list[float]]:
        """One vector per text, in order."""
        ...

    def chat_json(self, *, system: str, user: str, schema: dict[str, Any]) -> dict[str, Any]:
        """One JSON object that follows ``schema``."""
        ...

    def read_image(self, *, png: bytes, instruction: str) -> str:
        """The text the vision model reads in ``png``; empty if it sees none."""
        ...


_JSON_TYPES: dict[str, Callable[[object], bool]] = {
    "object": lambda value: isinstance(value, dict),
    "array": lambda value: isinstance(value, list),
    "string": lambda value: isinstance(value, str),
    "integer": lambda value: isinstance(value, int) and not isinstance(value, bool),
    "number": lambda value: isinstance(value, int | float) and not isinstance(value, bool),
    "boolean": lambda value: isinstance(value, bool),
    "null": lambda value: value is None,
}


def schema_problem(value: object, schema: Mapping[str, Any], path: str = "$") -> str | None:
    """What is wrong with ``value`` under ``schema``, or None if nothing is.

    Checks the JSON Schema keywords our callers use: ``type`` (a name or a list
    of names), ``enum``, ``required``, ``properties`` and ``items``; other
    keywords are ignored. The problem names a path from the schema, never the
    value, so it holds no document text. O(size of value).
    """
    expected = schema.get("type")
    if expected is not None:
        names = [expected] if isinstance(expected, str) else list(expected)
        unknown = [name for name in names if name not in _JSON_TYPES]
        if unknown:
            raise ValueError("unsupported schema type")
        if not any(_JSON_TYPES[name](value) for name in names):
            return f"{path} must be {' or '.join(names)}"
    if "enum" in schema and value not in schema["enum"]:
        return f"{path} must be one of the allowed values"
    if isinstance(value, dict):
        for key in schema.get("required", []):
            if key not in value:
                return f"{path}.{key} is missing"
        for key, sub_schema in schema.get("properties", {}).items():
            if key in value:
                problem = schema_problem(value[key], sub_schema, f"{path}.{key}")
                if problem is not None:
                    return problem
    items = schema.get("items")
    if isinstance(value, list) and isinstance(items, Mapping):
        for index, item in enumerate(value):
            problem = schema_problem(item, items, f"{path}[{index}]")
            if problem is not None:
                return problem
    return None


def _parse_reply(content: str, schema: Mapping[str, Any]) -> tuple[dict[str, Any] | None, str]:
    """The reply as an object that follows the schema, else (None, the problem)."""
    try:
        value = json.loads(content)
    except ValueError:
        return None, "the reply is not JSON"
    if not isinstance(value, dict):
        return None, "the reply must be one JSON object"
    problem = schema_problem(value, schema)
    if problem is not None:
        return None, problem
    return value, ""


class OllamaClient:
    """``ModelClient`` over Ollama's HTTP API, with the standard library only."""

    def __init__(self, settings: Settings) -> None:
        base_url = settings.model_base_url.rstrip("/")
        if urllib.parse.urlsplit(base_url).scheme not in {"http", "https"}:
            raise ValueError("model_base_url must be an http or https URL")
        self._base_url = base_url
        self._timeout = settings.model_timeout_seconds
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
            reply = self._post("/api/embed", {"model": self.embed_model, "input": batch})
            embeddings = reply.get("embeddings")
            if not isinstance(embeddings, list):
                raise ModelUnavailable("bad_response")
            if len(embeddings) != len(batch):
                raise ModelOutputError("embedding_count")
            vectors.extend(self._vector(item) for item in embeddings)
        return vectors

    def chat_json(self, *, system: str, user: str, schema: dict[str, Any]) -> dict[str, Any]:
        """If the first reply does not follow the schema, the problem goes back to
        the model once so it can repair it. A second bad reply raises
        ``ModelOutputError("invalid_output")``."""
        messages: list[dict[str, Any]] = [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ]
        content = self._chat(self.chat_model, messages, schema)
        value, problem = _parse_reply(content, schema)
        if value is not None:
            return value
        messages.append({"role": "assistant", "content": content})
        messages.append({"role": "user", "content": _REPAIR.format(problem=problem)})
        value, _ = _parse_reply(self._chat(self.chat_model, messages, schema), schema)
        if value is None:
            raise ModelOutputError("invalid_output")
        return value

    def read_image(self, *, png: bytes, instruction: str) -> str:
        if self._vision_model is None:
            raise ModelUnavailable("no_vision_model")
        image = base64.b64encode(png).decode("ascii")
        messages = [{"role": "user", "content": instruction, "images": [image]}]
        return self._chat(self._vision_model, messages, None).strip()

    def _chat(self, model: str, messages: list[dict[str, Any]], schema: dict[str, Any] | None) -> str:
        body: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "stream": False,
            "think": False,
            "options": {"temperature": 0},
        }
        if schema is not None:
            body["format"] = schema
        reply = self._post("/api/chat", body)
        message = reply.get("message")
        content = message.get("content") if isinstance(message, dict) else None
        if not isinstance(content, str):
            raise ModelUnavailable("bad_response")
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

    def _post(self, path: str, body: dict[str, Any]) -> dict[str, Any]:
        request = urllib.request.Request(  # noqa: S310 - the scheme is checked to be http(s) in __init__
            self._base_url + path,
            data=json.dumps(body).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with self._opener.open(request, timeout=self._timeout) as response:
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


def build_model_client(settings: Settings) -> ModelClient | None:
    """The configured model client, or None when the product runs without AI."""
    if not settings.model_enabled:
        return None
    return OllamaClient(settings)
