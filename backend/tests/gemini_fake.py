"""A fake Gemini API for tests: a stdlib HTTP server on 127.0.0.1 (PRD 5: never the real API).

``GeminiFake`` answers each request with the next reply scripted for its action
(the part of the path after the colon: ``generateContent``, ``batchEmbedContents``),
or, when none is scripted, with ``responder(request)`` if one is set. A request
nothing answers gets 599, and ``unanswered`` counts it, so a test can assert it
saw only what it scripted. Every request is recorded (method, path, headers with
lower-case names, body), and ``max_in_flight`` is the most requests it was
answering at once.

The builders below make the replies the client must handle: a page or text reply
with usage, an embedding reply, errors with their ``error.status``, blocked and cut
off replies, a redirect, a slow reply, an oversized body and a closed port.
"""

import json
import socket
import threading
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

KEY = "AIza" + "F" * 35  # the shape of a Google API key; not a real one
API_PATH = "/v1beta"


@dataclass
class Reply:
    """One scripted reply. ``body``: an object (sent as JSON) or raw bytes. ``delay``:
    seconds to wait before answering (cut short when the fake shuts down)."""

    status: int = 200
    body: object = None
    headers: dict[str, str] = field(default_factory=dict)
    delay: float = 0.0


@dataclass(frozen=True)
class Recorded:
    method: str
    path: str
    headers: dict[str, str]
    body: Any  # the parsed JSON, or the raw bytes when it is not JSON


class GeminiFake:
    def __init__(self) -> None:
        self.url = ""  # the GEMINI_BASE_URL to use, ending in /v1beta
        self.requests: list[Recorded] = []
        self.responder: Callable[[Recorded], Reply] | None = None
        self.unanswered = 0
        self.max_in_flight = 0
        self._in_flight = 0
        self._scripts: dict[str, list[Reply]] = {}
        self._lock = threading.Lock()
        self.release = threading.Event()

    def script(self, action: str, *replies: Reply) -> None:
        """Queue replies for requests whose path ends ``:<action>``, in order."""
        with self._lock:
            self._scripts.setdefault(action, []).extend(replies)

    def bodies(self, action: str = "generateContent") -> list[Any]:
        return [request.body for request in self.requests if request.path.endswith(":" + action)]

    def assert_key_in_header_only(self, key: str) -> None:
        """Every request carried the key in x-goog-api-key, and never in its path or body."""
        assert self.requests
        for request in self.requests:
            assert request.headers.get("x-goog-api-key") == key
            assert key not in request.path
            assert key not in json.dumps(request.body, default=str)

    def _answer(self, request: Recorded) -> Reply:
        action = request.path.rpartition(":")[2]
        with self._lock:
            queue = self._scripts.get(action)
            if queue:
                return queue.pop(0)
        if self.responder is not None:
            return self.responder(request)
        with self._lock:
            self.unanswered += 1
        return Reply(599, {"error": {"status": "UNSCRIPTED"}})

    def _enter(self) -> None:
        with self._lock:
            self._in_flight += 1
            self.max_in_flight = max(self.max_in_flight, self._in_flight)

    def _leave(self) -> None:
        with self._lock:
            self._in_flight -= 1


def _handler(fake: GeminiFake) -> type[BaseHTTPRequestHandler]:
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
                if reply.delay:
                    fake.release.wait(reply.delay)
                payload = reply.body if isinstance(reply.body, bytes) else json.dumps(reply.body).encode()
                try:
                    self.send_response(reply.status)
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Content-Length", str(len(payload)))
                    for name, value in reply.headers.items():
                        self.send_header(name, value)
                    self.end_headers()
                    self.wfile.write(payload)
                except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
                    pass  # the client gave up first (the deadline tests)
            finally:
                fake._leave()

        do_POST = _serve
        do_GET = _serve

        def log_message(self, format: str, *args: Any) -> None:
            pass

    return Handler


@contextmanager
def running_fake() -> Iterator[GeminiFake]:
    fake = GeminiFake()
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


def usage(prompt: int = 1500, candidates: int = 300, thoughts: int | None = 200) -> dict[str, int]:
    counts = {"promptTokenCount": prompt, "candidatesTokenCount": candidates}
    if thoughts is not None:
        counts["thoughtsTokenCount"] = thoughts
    counts["totalTokenCount"] = prompt + candidates + (thoughts or 0)
    return counts


def text_reply(
    text: str, *, finish: str = "STOP", counts: dict[str, int] | None = None, thought: str | None = None
) -> Reply:
    """A generateContent reply with ``text`` (and a thought part first, when given)."""
    parts: list[dict[str, Any]] = [] if thought is None else [{"text": thought, "thought": True}]
    parts.append({"text": text})
    candidate = {"content": {"role": "model", "parts": parts}, "finishReason": finish, "index": 0}
    return Reply(200, {"candidates": [candidate], "usageMetadata": counts or usage()})


def json_reply(value: object, **options: Any) -> Reply:
    return text_reply(json.dumps(value, ensure_ascii=False), **options)


def blocked_prompt(reason: str = "PROHIBITED_CONTENT") -> Reply:
    return Reply(200, {"promptFeedback": {"blockReason": reason}, "usageMetadata": usage(candidates=0)})


def embed_reply(vectors: list[list[float]]) -> Reply:
    return Reply(200, {"embeddings": [{"values": vector} for vector in vectors]})


def error_reply(status: int, error_status: str, *, headers: dict[str, str] | None = None) -> Reply:
    """An error reply like Google's, with a message that must never be logged or kept."""
    body = {"error": {"code": status, "message": "SECRET-ERROR-MESSAGE do not log", "status": error_status}}
    return Reply(status, body, headers or {})


def rate_limited(retry_after: str | None = None) -> Reply:
    return error_reply(
        429, "RESOURCE_EXHAUSTED", headers={} if retry_after is None else {"Retry-After": retry_after}
    )


def redirect_to(url: str) -> Reply:
    return Reply(302, b"", {"Location": url})


def slow(reply: Reply, seconds: float) -> Reply:
    return Reply(reply.status, reply.body, reply.headers, seconds)


def oversized(size: int) -> Reply:
    return Reply(200, b'{"candidates": "' + b"x" * size + b'"}')
