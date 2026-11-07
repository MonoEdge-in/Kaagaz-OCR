"""A fake OpenRouter API for tests: a stdlib HTTP server on 127.0.0.1 (PRD 5: never the
real API).

``OpenRouterFake`` answers each request with the next reply scripted for the last part of
its path (``completions``, ``embeddings``, ``endpoints``, ``key``), or, when none is
scripted, with ``responder(request)`` if one is set. A request nothing answers gets 599,
and ``unanswered`` counts it, so a test can assert it saw only what it scripted. Every
request is recorded (method, path, headers with lower-case names, body), and
``max_in_flight`` is the most requests it was answering at once.

The builders below make the replies the client must handle: a chat reply with its finish
and native finish reasons, usage with reasoning tokens and cost, error replies whose
message, ``flagged_input`` and ``raw`` hold ``SECRET`` (page text that must never be
logged or kept), 402, a 403 refusal and a moderation block, ``content_filter``,
``length``, ``finish_reason: "error"``, embeddings in shuffled index order, an endpoints
listing, the key's limits, a redirect, a slow reply, an oversized body and a closed port; and a
streamed reply (``stream_reply``, #535): server-sent events, one ``data:`` chunk a piece of text.
"""

import json
import socket
import threading
import time
from collections.abc import Callable, Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

KEY = "sk-or-v1-" + "f" * 64  # the shape of an OpenRouter key; not a real one
API_PATH = "/api/v1"
# Page text an error reply can carry (message, metadata.flagged_input, metadata.raw).
SECRET = "SECRET-PAGE-TEXT Rs 98,765 do not log"


@dataclass
class Reply:
    """One scripted reply. ``body``: an object (sent as JSON) or raw bytes. ``delay``:
    seconds to wait before answering (cut short when the fake shuts down). ``trickle``: the
    headers go at once, then a space every ``trickle`` seconds for ``delay`` seconds, then
    the body (a reply kept alive while a model thinks). ``status`` 0: the connection is closed
    once the request is read, with no reply at all (#488 review: dropped after the send).
    ``stream`` (#535): sent as ``text/event-stream``, each chunk written on its own, ``every``
    seconds apart (cut short when the fake shuts down); ``sent`` counts the chunks written."""

    status: int = 200
    body: object = None
    headers: dict[str, str] = field(default_factory=dict)
    delay: float = 0.0
    trickle: float = 0.0
    stream: list[bytes] | None = None
    every: float = 0.0
    sent: int = 0


@dataclass(frozen=True)
class Recorded:
    method: str
    path: str
    headers: dict[str, str]
    body: Any  # the parsed JSON, or the raw bytes when it is not JSON

    @property
    def action(self) -> str:
        """The last part of the path: completions, embeddings, endpoints or key."""
        return self.path.rstrip("/").rpartition("/")[2]


class OpenRouterFake:
    def __init__(self) -> None:
        self.url = ""  # the OPENROUTER_BASE_URL to use, ending in /api/v1
        self.requests: list[Recorded] = []
        self.responder: Callable[[Recorded], Reply] | None = None
        self.unanswered = 0
        self.max_in_flight = 0
        self._in_flight = 0
        self._scripts: dict[str, list[Reply]] = {}
        self._lock = threading.Lock()
        self.release = threading.Event()

    def script(self, action: str, *replies: Reply) -> None:
        """Queue replies for requests whose path ends ``/<action>``, in order."""
        with self._lock:
            self._scripts.setdefault(action, []).extend(replies)

    def bodies(self, action: str = "completions") -> list[Any]:
        return [request.body for request in self.requests if request.action == action]

    def assert_key_in_header_only(self, key: str) -> None:
        """Every request carried the key as ``Authorization: Bearer``, and never in its path,
        its body or any other header."""
        assert self.requests
        for request in self.requests:
            assert request.headers.get("authorization") == "Bearer " + key
            assert key not in request.path
            assert key not in json.dumps(request.body, default=str)
            others = {name: value for name, value in request.headers.items() if name != "authorization"}
            assert key not in json.dumps(others)

    def _answer(self, request: Recorded) -> Reply:
        with self._lock:
            queue = self._scripts.get(request.action)
            if queue:
                return queue.pop(0)
        if self.responder is not None:
            return self.responder(request)
        with self._lock:
            self.unanswered += 1
        return Reply(599, {"error": {"code": 599, "message": "unscripted"}})

    def _enter(self) -> None:
        with self._lock:
            self._in_flight += 1
            self.max_in_flight = max(self.max_in_flight, self._in_flight)

    def _leave(self) -> None:
        with self._lock:
            self._in_flight -= 1


def _handler(fake: OpenRouterFake) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        def _serve(self) -> None:
            fake._enter()
            try:
                length = int(self.headers.get("Content-Length") or 0)
                raw = self.rfile.read(length) if length else b""
                try:
                    body: Any = json.loads(raw) if raw else None
                except ValueError:
                    body = raw
                headers = {name.lower(): value for name, value in self.headers.items()}
                request = Recorded(self.command, self.path, headers, body)
                with fake._lock:
                    fake.requests.append(request)
                reply = fake._answer(request)
                if reply.status == 0:
                    self.close_connection = True
                    return
                if reply.delay and not reply.trickle:
                    fake.release.wait(reply.delay)
                if reply.stream is not None:
                    self._stream(reply)
                    return
                payload = reply.body if isinstance(reply.body, bytes) else json.dumps(reply.body).encode()
                try:
                    self.send_response(reply.status)
                    self.send_header("Content-Type", "application/json")
                    if not reply.trickle:
                        self.send_header("Content-Length", str(len(payload)))
                    for name, value in reply.headers.items():
                        self.send_header(name, value)
                    self.end_headers()
                    if reply.trickle:  # no length: the body ends when the connection closes
                        ends = time.monotonic() + reply.delay
                        while time.monotonic() < ends and not fake.release.wait(reply.trickle):
                            self.wfile.write(b" ")
                            self.wfile.flush()
                    self.wfile.write(payload)
                except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
                    pass  # the client gave up first (the deadline tests)
            finally:
                fake._leave()

        def _stream(self, reply: Reply) -> None:
            """#535: the chunks as server-sent events; the body ends when the connection closes."""
            self.close_connection = True
            try:
                self.send_response(reply.status)
                self.send_header("Content-Type", "text/event-stream")
                self.end_headers()
                for chunk in reply.stream or []:
                    if reply.sent and reply.every and fake.release.wait(reply.every):
                        return
                    self.wfile.write(chunk)
                    self.wfile.flush()
                    reply.sent += 1
            except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
                pass  # the client stopped reading (a cancelled answer)

        do_POST = _serve
        do_GET = _serve

        def log_message(self, format: str, *args: Any) -> None:
            pass

    return Handler


@contextmanager
def running_fake() -> Iterator[OpenRouterFake]:
    fake = OpenRouterFake()
    server = ThreadingHTTPServer(("127.0.0.1", 0), _handler(fake))
    server.daemon_threads = True
    thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.05})
    thread.start()
    fake.url = f"http://127.0.0.1:{server.server_port}{API_PATH}"
    try:
        yield fake
    finally:
        fake.release.set()
        server.shutdown()
        server.server_close()
        thread.join()


def closed_port_url() -> str:
    """A loopback base URL nothing listens on (the exit test's outage)."""
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    return f"http://127.0.0.1:{port}{API_PATH}"


# Reply builders.


def usage(
    prompt: int = 1500, completion: int = 500, reasoning: int | None = 200, cost: float | None = None
) -> dict[str, Any]:
    """OpenRouter's usage: completion_tokens with reasoning_tokens inside it, and cost."""
    counts: dict[str, Any] = {
        "prompt_tokens": prompt,
        "completion_tokens": completion,
        "total_tokens": prompt + completion,
    }
    if reasoning is not None:
        counts["completion_tokens_details"] = {"reasoning_tokens": reasoning}
    if cost is not None:
        counts["cost"] = cost
    return counts


def chat_reply(
    content: str | None,
    *,
    finish: str | None = "stop",
    native: str | None = "STOP",
    counts: dict[str, Any] | None = None,
    refusal: str | None = None,
) -> Reply:
    """A /chat/completions reply whose first choice holds ``content``."""
    message: dict[str, Any] = {"role": "assistant", "content": content, "refusal": refusal}
    choice: dict[str, Any] = {"index": 0, "message": message, "finish_reason": finish}
    if native is not None:
        choice["native_finish_reason"] = native
    body = {
        "id": "gen-made-up",
        "object": "chat.completion",
        "model": "google/gemini-3.1-pro-preview",
        "provider": "Google",
        "choices": [choice],
        "usage": counts or usage(),
    }
    return Reply(200, body)


def json_reply(value: object, **options: Any) -> Reply:
    return chat_reply(json.dumps(value, ensure_ascii=False), **options)


def content_filtered(native: str | None = "SAFETY") -> Reply:
    return chat_reply(None, finish="content_filter", native=native, counts=usage(1500, 0, 0))


def cut_off(content: str = '{"status": "answered", "lines": ["Total') -> Reply:
    return chat_reply(content, finish="length", native="MAX_TOKENS")


def failed_part_way() -> Reply:
    """A 200 whose generation failed part way: finish_reason error."""
    return chat_reply("", finish="error", native=None, counts=usage(1500, 0, 0))


def embed_reply(vectors: Sequence[Sequence[float]], *, shuffled: bool = True) -> Reply:
    """An /embeddings reply, its items in shuffled (reversed, then rotated) index order."""
    data = [
        {"object": "embedding", "embedding": list(vector), "index": i} for i, vector in enumerate(vectors)
    ]
    if shuffled and len(data) > 1:
        data.reverse()
        data = data[1:] + data[:1]
    return Reply(200, {"object": "list", "data": data, "usage": {"prompt_tokens": 7, "total_tokens": 7}})


def error_reply(
    status: int,
    *,
    error_type: str | None = None,
    provider_name: str | None = None,
    reasons: list[str] | None = None,
    headers: dict[str, str] | None = None,
) -> Reply:
    """An error reply like OpenRouter's. Its message, flagged_input and raw hold ``SECRET``:
    page text that must never be logged, kept or shown."""
    metadata: dict[str, Any] = {"flagged_input": SECRET, "raw": {"error": {"message": SECRET}}}
    if error_type is not None:
        metadata["error_type"] = error_type
    if provider_name is not None:
        metadata["provider_name"] = provider_name
    if reasons is not None:
        metadata["reasons"] = reasons
        metadata["model_slug"] = "google/gemini-3.1-pro-preview"
    body = {"error": {"code": status, "message": SECRET, "metadata": metadata}}
    return Reply(status, body, headers or {})


def no_credits() -> Reply:
    return error_reply(402, error_type="payment_required")


def refused_403() -> Reply:
    return error_reply(403, error_type="refusal", provider_name="google-vertex")


def moderated_403() -> Reply:
    return error_reply(403, reasons=["violence"], provider_name="google-vertex")


def rate_limited(retry_after: str | None = None) -> Reply:
    return error_reply(
        429,
        error_type="rate_limit_exceeded",
        headers={} if retry_after is None else {"Retry-After": retry_after},
    )


def endpoints_reply(
    model: str,
    *,
    tags: Sequence[str] = ("google-vertex", "google-ai-studio"),
    parameters: Sequence[str] = (
        "response_format",
        "reasoning",
        "max_completion_tokens",
        "structured_outputs",
    ),
    zdr: bool | None = None,
) -> Reply:
    """GET /models/{author}/{slug}/endpoints: one endpoint per tag."""
    endpoints = []
    for tag in tags:
        endpoint: dict[str, Any] = {
            "name": f"{tag} | {model}",
            "model_id": model,
            "provider_name": "Google" if tag.startswith("google-vertex") else "Google AI Studio",
            "tag": tag,
            "status": 0,
            "supported_parameters": list(parameters),
        }
        if zdr is not None:
            endpoint["zdr"] = zdr
        endpoints.append(endpoint)
    return Reply(200, {"data": {"id": model, "name": model, "endpoints": endpoints}})


def key_reply(
    limit: float | None = 100.0, remaining: float | None = 87.5, usage_so_far: float = 12.5
) -> Reply:
    """GET /key: the key's credit limits. Its label is made of the key's own characters, so
    it must never be printed."""
    data = {
        "label": KEY[:12] + "..." + KEY[-3:],
        "limit": limit,
        "limit_remaining": remaining,
        "usage": usage_so_far,
        "is_free_tier": False,
    }
    return Reply(200, {"data": data})


def redirect_to(url: str) -> Reply:
    return Reply(302, b"", {"Location": url})


def slow(reply: Reply, seconds: float) -> Reply:
    return Reply(reply.status, reply.body, reply.headers, seconds)


def trickled(reply: Reply, seconds: float, every: float = 0.05) -> Reply:
    """``reply`` after ``seconds`` of a space every ``every`` seconds (headers first)."""
    return Reply(reply.status, reply.body, reply.headers, seconds, every)


def oversized(size: int) -> Reply:
    return Reply(200, b'{"choices": "' + b"x" * size + b'"}')


def _event(chunk: dict[str, Any]) -> bytes:
    return b"data: " + json.dumps(chunk, ensure_ascii=False).encode() + b"\n\n"


def stream_reply(
    parts: Sequence[str],
    *,
    finish: str | None = "stop",
    native: str | None = "STOP",
    counts: dict[str, Any] | None = None,
    every: float = 0.0,
    error: bool = False,
    with_usage: bool = True,
) -> Reply:
    """#535: a streamed /chat/completions reply as OpenRouter sends it: a keep-alive comment, one
    chunk a piece of ``parts`` (``delta.content``), then the finish reasons with the usage, then
    ``[DONE]``. ``finish`` None: the stream just stops after the text. ``error``: the last chunk is
    a mid-stream error (finish_reason error). ``with_usage`` False: the last chunk has no usage."""
    head = {"id": "gen-made-up", "object": "chat.completion.chunk", "model": "google/gemini-3.1-pro-preview"}
    chunks = [b": OPENROUTER PROCESSING\n\n"]
    for part in parts:
        delta = {"index": 0, "delta": {"role": "assistant", "content": part}, "finish_reason": None}
        chunks.append(_event({**head, "provider": "Google", "choices": [delta]}))
    if error:
        last = {"index": 0, "delta": {"content": ""}, "finish_reason": "error"}
        chunks.append(_event({**head, "error": {"code": 502, "message": SECRET}, "choices": [last]}))
    elif finish is not None:
        last = {"index": 0, "delta": {"content": ""}, "finish_reason": finish}
        if native is not None:
            last["native_finish_reason"] = native
        tail = {"usage": counts or usage()} if with_usage else {}
        chunks.append(_event({**head, "provider": "Google", "choices": [last], **tail}))
        chunks.append(b"data: [DONE]\n\n")
    return Reply(200, stream=chunks, every=every)


def json_stream(value: object, size: int = 7, **options: Any) -> Reply:
    """``value`` as JSON, streamed ``size`` characters a chunk (cut anywhere, mid-string too)."""
    text = json.dumps(value, ensure_ascii=False)
    return stream_reply([text[i : i + size] for i in range(0, len(text), size)], **options)
