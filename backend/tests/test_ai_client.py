"""The Ollama client against a fake Ollama: a stdlib HTTP server on 127.0.0.1.

Ollama itself is not installed on the build laptop, so these tests pin the
request shapes the client sends and how it treats every kind of reply.
"""

import base64
import json
import socket
import threading
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

import pytest

from kaagaz.ai.client import (
    ModelOutputError,
    ModelUnavailable,
    OllamaClient,
    build_model_client,
    schema_problem,
)
from kaagaz.settings import Settings
from tests.settings_factory import make_settings

SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "status": {"type": "string", "enum": ["answered", "not_found"]},
        "sentences": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "text": {"type": "string"},
                    "evidence": {"type": "array", "items": {"type": "integer"}},
                },
                "required": ["text", "evidence"],
            },
        },
    },
    "required": ["status", "sentences"],
}
GOOD = {"status": "answered", "sentences": [{"text": "Total is 5,000.", "evidence": [1]}]}


class FakeOllama:
    """Replies are scripted as (status, body, slow); requests are recorded as (path, body)."""

    def __init__(self) -> None:
        self.url = ""
        self.replies: list[tuple[int, object, bool]] = []
        self.requests: list[tuple[str, dict[str, Any]]] = []
        self.release = threading.Event()

    def chat(self, content: str) -> None:
        self.replies.append((200, {"message": {"role": "assistant", "content": content}}, False))


@pytest.fixture
def ollama() -> Iterator[FakeOllama]:
    fake = FakeOllama()

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:
            length = int(self.headers["Content-Length"])
            fake.requests.append((self.path, json.loads(self.rfile.read(length))))
            status, body, slow = fake.replies.pop(0)
            if slow:
                fake.release.wait(5)
            payload = body if isinstance(body, bytes) else json.dumps(body).encode()
            try:
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)
            except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
                pass  # the client gave up first (the timeout test)

        def log_message(self, format: str, *args: Any) -> None:
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.05})
    thread.start()
    fake.url = f"http://127.0.0.1:{server.server_port}"
    yield fake
    fake.release.set()
    server.shutdown()
    server.server_close()
    thread.join()


def _settings(fake: FakeOllama, **overrides: Any) -> Settings:
    values: dict[str, Any] = {
        "model_enabled": True,
        "model_base_url": fake.url,
        "embedding_dim": 4,
        "model_timeout_seconds": 5.0,
    }
    return make_settings(**{**values, **overrides})


def _vectors(n: int) -> dict[str, Any]:
    return {"embeddings": [[float(i), 0.5, 0.25, 1.0] for i in range(n)]}


def test_embed_batches_by_32_and_keeps_order(ollama: FakeOllama) -> None:
    ollama.replies += [(200, _vectors(32), False), (200, _vectors(1), False)]
    client = OllamaClient(_settings(ollama))

    vectors = client.embed([f"piece {i}" for i in range(33)])

    assert len(vectors) == 33
    assert vectors[0] == [0.0, 0.5, 0.25, 1.0]
    assert vectors[31][0] == 31.0
    assert vectors[32][0] == 0.0
    assert [path for path, _ in ollama.requests] == ["/api/embed", "/api/embed"]
    first, second = (body for _, body in ollama.requests)
    assert first == {"model": "qwen3-embedding:0.6b", "input": [f"piece {i}" for i in range(32)]}
    assert second["input"] == ["piece 32"]


def test_embed_of_nothing_makes_no_request(ollama: FakeOllama) -> None:
    assert OllamaClient(_settings(ollama)).embed([]) == []
    assert ollama.requests == []


def test_embedding_of_the_wrong_dimension_is_refused(ollama: FakeOllama) -> None:
    ollama.replies.append((200, {"embeddings": [[0.1, 0.2, 0.3]]}, False))
    with pytest.raises(ModelOutputError) as caught:
        OllamaClient(_settings(ollama)).embed(["one"])
    assert caught.value.code == "embedding_dim"


def test_embedding_count_and_values_are_checked(ollama: FakeOllama) -> None:
    ollama.replies.append((200, _vectors(1), False))
    with pytest.raises(ModelOutputError) as caught:
        OllamaClient(_settings(ollama)).embed(["one", "two"])
    assert caught.value.code == "embedding_count"

    ollama.replies.append((200, {"embeddings": [[0.1, "x", 0.3, 0.4]]}, False))
    with pytest.raises(ModelOutputError) as caught:
        OllamaClient(_settings(ollama)).embed(["one"])
    assert caught.value.code == "embedding_value"


def test_chat_json_sends_the_schema_and_returns_the_object(ollama: FakeOllama) -> None:
    ollama.chat(json.dumps(GOOD))
    client = OllamaClient(_settings(ollama))

    assert client.chat_json(system="rules", user="question", schema=SCHEMA) == GOOD

    path, body = ollama.requests[0]
    assert path == "/api/chat"
    assert body == {
        "model": "qwen3:4b",
        "messages": [{"role": "system", "content": "rules"}, {"role": "user", "content": "question"}],
        "stream": False,
        "think": False,
        "options": {"temperature": 0},
        "format": SCHEMA,
    }


@pytest.mark.parametrize(
    ("bad", "problem"),
    [
        ("not json at all", "the reply is not JSON"),
        (json.dumps({"status": "maybe", "sentences": []}), "$.status must be one of the allowed values"),
        (
            json.dumps({"status": "answered", "sentences": [{"text": "x", "evidence": ["1"]}]}),
            "$.sentences[0].evidence[0] must be integer",
        ),
    ],
)
def test_chat_json_repairs_a_bad_reply_once(ollama: FakeOllama, bad: str, problem: str) -> None:
    ollama.chat(bad)
    ollama.chat(json.dumps(GOOD))

    result = OllamaClient(_settings(ollama)).chat_json(system="rules", user="question", schema=SCHEMA)

    assert result == GOOD
    assert len(ollama.requests) == 2
    messages = ollama.requests[1][1]["messages"]
    assert [m["role"] for m in messages] == ["system", "user", "assistant", "user"]
    assert messages[2]["content"] == bad
    assert problem in messages[3]["content"]


def test_chat_json_gives_up_after_two_bad_replies(ollama: FakeOllama) -> None:
    ollama.chat("[1, 2]")
    ollama.chat(json.dumps({"status": "answered"}))
    with pytest.raises(ModelOutputError) as caught:
        OllamaClient(_settings(ollama)).chat_json(system="s", user="u", schema=SCHEMA)
    assert caught.value.code == "invalid_output"
    assert len(ollama.requests) == 2


def test_a_slow_model_times_out(ollama: FakeOllama) -> None:
    ollama.replies.append((200, _vectors(1), True))
    client = OllamaClient(_settings(ollama, model_timeout_seconds=0.3))
    with pytest.raises(ModelUnavailable) as caught:
        client.embed(["one"])
    assert caught.value.code == "timeout"


def test_a_refused_connection_is_unavailable() -> None:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    settings = make_settings(model_enabled=True, model_base_url=f"http://127.0.0.1:{port}")
    with pytest.raises(ModelUnavailable) as caught:
        OllamaClient(settings).embed(["one"])
    assert caught.value.code == "unreachable"


@pytest.mark.parametrize(
    ("reply", "code"),
    [
        ((500, {"error": "model crashed"}, False), "http_error"),
        ((404, {"error": "model not found"}, False), "http_error"),
        ((200, b"<html>", False), "bad_response"),
        ((200, {"no": "message"}, False), "bad_response"),
    ],
)
def test_bad_http_replies_are_unavailable(
    ollama: FakeOllama, reply: tuple[int, object, bool], code: str
) -> None:
    ollama.replies.append(reply)
    with pytest.raises(ModelUnavailable) as caught:
        OllamaClient(_settings(ollama)).chat_json(system="s", user="u", schema=SCHEMA)
    assert caught.value.code == code
    assert str(caught.value) == code  # never the server's text


def test_proxy_variables_are_ignored(ollama: FakeOllama, monkeypatch: pytest.MonkeyPatch) -> None:
    """Document text must never reach a proxy from the environment."""
    for name in ("HTTP_PROXY", "http_proxy"):
        monkeypatch.setenv(name, "http://127.0.0.1:9")
    for name in ("NO_PROXY", "no_proxy"):
        monkeypatch.delenv(name, raising=False)
    ollama.replies.append((200, _vectors(1), False))
    assert len(OllamaClient(_settings(ollama)).embed(["one"])) == 1


def test_read_image_without_a_vision_model_is_unavailable(ollama: FakeOllama) -> None:
    with pytest.raises(ModelUnavailable) as caught:
        OllamaClient(_settings(ollama)).read_image(png=b"\x89PNG", instruction="Read the amount.")
    assert caught.value.code == "no_vision_model"
    assert ollama.requests == []


def test_read_image_sends_the_image_to_the_vision_model(ollama: FakeOllama) -> None:
    ollama.chat("  1,23,456.00\n")
    client = OllamaClient(_settings(ollama, model_vision="qwen2.5vl:3b"))

    assert client.read_image(png=b"\x89PNG", instruction="Read the amount.") == "1,23,456.00"

    body = ollama.requests[0][1]
    assert body["model"] == "qwen2.5vl:3b"
    assert body["messages"] == [
        {
            "role": "user",
            "content": "Read the amount.",
            "images": [base64.b64encode(b"\x89PNG").decode("ascii")],
        }
    ]
    assert "format" not in body


def test_build_model_client_is_none_without_ai() -> None:
    assert build_model_client(make_settings(model_enabled=False)) is None
    client = build_model_client(make_settings(model_enabled=True))
    assert isinstance(client, OllamaClient)
    assert (client.chat_model, client.embed_model) == ("qwen3:4b", "qwen3-embedding:0.6b")


def test_only_http_base_urls_are_accepted() -> None:
    with pytest.raises(ValueError, match="http"):
        OllamaClient(make_settings(model_enabled=True, model_base_url="file:///etc/passwd"))


def test_schema_problem_checks_types_strictly() -> None:
    assert schema_problem(GOOD, SCHEMA) is None
    assert schema_problem(
        {"status": "answered", "sentences": [{"text": "x", "evidence": [True]}]}, SCHEMA
    ) == ("$.sentences[0].evidence[0] must be integer")
    assert schema_problem({"sentences": []}, SCHEMA) == "$.status is missing"
    assert schema_problem({"n": 1.5}, {"properties": {"n": {"type": ["integer", "null"]}}}) == (
        "$.n must be integer or null"
    )
