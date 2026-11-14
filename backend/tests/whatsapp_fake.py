"""A fake WhatsApp for tests (#603): Meta is never called (PRD 7 section 15, "The fake WhatsApp
server").

* ``WhatsAppFake``: a stdlib HTTP server on 127.0.0.1 playing Meta's Graph API. ``POST
  /<version>/<number id>/messages`` is recorded and answered as Meta does, or with the next of
  ``failures`` (a status; 0 closes the connection with no reply). ``texts()``: (to, body) of each
  text sent, in order; ``sent()`` (W3): (to, what) of every message: a text's body, a template's
  name and its parameters, or the buttons' text and ids. Files are never sent on WhatsApp, so there
  is no media endpoint.
* ``whatsapp_settings``: settings with WhatsApp on, pointed at the fake.
* ``delivery``: the JSON Meta posts for messages; ``post``: posts it to the webhook signed with
  the app secret as Meta does (or with the ``signature`` given).
* ``connect``: a live connection for a person and WhatsApp on for their organisation, as W3's
  connecting will leave them.
* ``Echo`` (W4): a chat that answers every message PLACEHOLDER, for the tests of the channel's
  queue, which are about when and to whom a reply goes, not what it says. ``sent()`` shows a
  list as (its text, its row ids).
"""

import hashlib
import hmac
import json
import threading
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

from sqlalchemy import text

from kaagaz.adapters.whatsapp import CODE_TEMPLATE
from kaagaz.channel.numbers import number_hash, seal
from kaagaz.channel.outgoing import Outgoing
from kaagaz.db.engine import Database
from kaagaz.db.whatsapp import Linked, Waiting
from kaagaz.settings import Settings
from tests.settings_factory import make_settings

NUMBER_ID = "1234567890"  # the Kaagaz number's id, made up
TOKEN = "fake-access-token-" + "t" * 20
APP_SECRET = "fake-app-secret-" + "s" * 16
VERIFY_TOKEN = "fake-verify-token"
NUMBER_KEY = "fake-number-key-" + "k" * 32
BUSINESS_NUMBER = "15550001111"  # the Kaagaz number, made up
VERSION = "/v0.0"
WEBHOOK = "/api/whatsapp/webhook"
APP_URL = "https://kaagaz.test"  # where the bot's links open
PLACEHOLDER = "Kaagaz got your message."


class Echo:
    def reply(self, linked: Linked, message: Waiting) -> Outgoing:
        return Outgoing(PLACEHOLDER)


class WhatsAppFake:
    def __init__(self) -> None:
        self.url = ""  # WHATSAPP_GRAPH_URL
        self.requests: list[tuple[str, dict[str, str], Any]] = []  # (path, headers, body)
        self.failures: list[int] = []
        self.lock = threading.Lock()

    def texts(self) -> list[tuple[str, str]]:
        with self.lock:
            return [
                (body["to"], body["text"]["body"]) for _, _, body in self.requests if body["type"] == "text"
            ]

    def sent(self) -> list[tuple[str, Any]]:
        with self.lock:
            return [(body["to"], _what(body)) for _, _, body in self.requests]

    def code_to(self, number: str) -> str:
        """The last connect code the bot sent to ``number``."""
        codes = [what[1][0] for to, what in self.sent() if to == number[1:] and what[0] == CODE_TEMPLATE]
        return str(codes[-1])


def _what(body: dict[str, Any]) -> Any:
    """A text's body; (a template's name, its parameters in order); or (the text, the button ids)."""
    if body["type"] == "text":
        return body["text"]["body"]
    if body["type"] == "template":
        given = [part for component in body["template"]["components"] for part in component["parameters"]]
        return (body["template"]["name"], [part.get("text") or part.get("payload") for part in given])
    interactive = body["interactive"]
    if interactive["type"] == "list":
        rows = [row["id"] for section in interactive["action"]["sections"] for row in section["rows"]]
        return (interactive["body"]["text"], rows)
    return (interactive["body"]["text"], [row["reply"]["id"] for row in interactive["action"]["buttons"]])


def _handler(fake: WhatsAppFake) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:
            raw = self.rfile.read(int(self.headers.get("Content-Length") or 0))
            headers = {name.lower(): value for name, value in self.headers.items()}
            with fake.lock:
                status = fake.failures.pop(0) if fake.failures else 200
                if status == 200:
                    fake.requests.append((self.path, headers, json.loads(raw)))
            if status == 0:
                self.close_connection = True
                return
            reply: dict[str, object] = {"error": {"code": status}}
            if status == 200:
                reply = {"messages": [{"id": f"wamid.out.{uuid.uuid4().hex}"}]}
            payload = json.dumps(reply).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def log_message(self, format: str, *args: Any) -> None:
            pass

    return Handler


@contextmanager
def running_fake() -> Iterator[WhatsAppFake]:
    fake = WhatsAppFake()
    server = ThreadingHTTPServer(("127.0.0.1", 0), _handler(fake))
    server.daemon_threads = True
    thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.05})
    thread.start()
    fake.url = f"http://127.0.0.1:{server.server_port}{VERSION}"
    try:
        yield fake
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


def whatsapp_settings(fake: WhatsAppFake | None = None, **overrides: Any) -> Settings:
    return make_settings(
        whatsapp_enabled=True,
        whatsapp_graph_url=fake.url if fake is not None else "http://127.0.0.1:9" + VERSION,
        whatsapp_phone_number_id=NUMBER_ID,
        whatsapp_access_token=TOKEN,
        whatsapp_app_secret=APP_SECRET,
        whatsapp_verify_token=VERIFY_TOKEN,
        whatsapp_number_key=NUMBER_KEY,
        whatsapp_business_number=BUSINESS_NUMBER,
        **{"app_url": APP_URL, **overrides},
    )


def new_number() -> str:
    """A made-up number in E.164 (+1 555, a range no real phone has), new each call."""
    return "+1555" + str(uuid.uuid4().int)[:7]


def message(
    number: str, sent_at: datetime, body: str = "hello", *, wamid: str | None = None
) -> dict[str, Any]:
    """One text message as Meta delivers it."""
    return {
        "from": number.removeprefix("+"),
        "id": wamid or f"wamid.{uuid.uuid4().hex}",
        "timestamp": str(int(sent_at.timestamp())),
        "type": "text",
        "text": {"body": body},
    }


def delivery(*messages: dict[str, Any], number_id: str = NUMBER_ID) -> dict[str, Any]:
    value = {
        "messaging_product": "whatsapp",
        "metadata": {"display_phone_number": "15550001111", "phone_number_id": number_id},
        "messages": list(messages),
    }
    return {
        "object": "whatsapp_business_account",
        "entry": [{"id": "0", "changes": [{"field": "messages", "value": value}]}],
    }


def signature(body: bytes, secret: str = APP_SECRET) -> str:
    return "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


def post(client: Any, payload: Any, *, sign: str | None = None) -> Any:
    """``payload`` to the webhook as Meta posts it: no cookie, no CSRF header, signed."""
    body = payload if isinstance(payload, bytes) else json.dumps(payload).encode()
    headers = {"Content-Type": "application/json", "X-Hub-Signature-256": sign or signature(body)}
    return client.post(WEBHOOK, content=body, headers=headers)


def connect(
    db: Database,
    customer_id: str,
    user_id: str,
    number: str,
    *,
    roles: tuple[str, ...] = ("member", "reviewer"),
) -> str:
    """A live connection of ``number`` to the person, and WhatsApp on for their organisation.
    The connection's id."""
    with db.user(customer_id, user_id) as conn:
        conn.execute(
            text(
                "INSERT INTO whatsapp_org_settings (customer_id, enabled, roles) VALUES (:c, true, :r)"
                " ON CONFLICT (customer_id) DO UPDATE SET enabled = true, roles = EXCLUDED.roles"
            ),
            {"c": customer_id, "r": list(roles)},
        )
        version = conn.execute(
            text("SELECT session_version FROM users WHERE customer_id = :c AND id = :u"),
            {"c": customer_id, "u": user_id},
        ).scalar_one()
        return str(
            conn.execute(
                text(
                    "INSERT INTO whatsapp_links (customer_id, user_id, phone_hash, phone_sealed, phone_last4,"
                    " session_version) VALUES (:c, :u, :h, :s, right(:n, 4), :v) RETURNING id"
                ),
                {
                    "c": customer_id,
                    "u": user_id,
                    "h": number_hash(NUMBER_KEY, number),
                    "s": seal(NUMBER_KEY, number, customer_id, user_id),
                    "n": number,
                    "v": version,
                },
            ).scalar_one()
        )
