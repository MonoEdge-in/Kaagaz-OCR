"""Meta's WhatsApp Cloud API (#603, PRD 7 section 15): the replies the channel sends.

* ``send_text``: POST ``{WHATSAPP_GRAPH_URL}/{WHATSAPP_PHONE_NUMBER_ID}/messages`` with the access
  token: one text to one number, link previews off (a preview robot never opens a Kaagaz link).
  Files are never sent on WhatsApp: nothing here uploads one.
* ``typing`` (W5): the person's message marked read, with WhatsApp's typing indicator (it goes
  when the reply does, or after 25 seconds). Not a billed message.
* ``media`` and ``download`` (W5, a file the person sent): ``GET {WHATSAPP_GRAPH_URL}/{media id}``
  gives the file's address (it works for 5 minutes) and its size; then that address is read with
  the access token. Only an address Meta's Graph API gave is read, never one from a message, and
  only https (http only when WHATSAPP_GRAPH_URL is itself http, which Settings allows on a
  loopback host only, the fake WhatsApp's). No redirect is followed.
* ``send_buttons`` (W3): one text with up to 3 reply buttons, each an id and a title.
* ``send_list`` (W4): one text with a list of up to 10 rows, each an id, a title and a description.
* The templates Meta approved (W3), which may go outside WhatsApp's 24 hours, in English:
  ``send_code``, the authentication template with its copy-code button (Meta fixes its words);
  ``send_welcome``, WELCOME_BODY: the person, their sign-in email masked (``masked_email``, which
  someone who tricks a person into connecting can't choose) and the organisation, with [Start] and
  [Not me - disconnect] (whose payload names the connection, so an old welcome's tap ends only its
  own); ``welcome_buttons`` sends the same as a text with reply buttons inside WhatsApp's 24 hours;
  ``send_disconnected``, "you were disconnected", with nothing in it.
* No redirect is followed and no proxy is used (``build_opener``, as the OpenRouter and sign-in
  provider calls). TIMEOUT_SECONDS a call; at most MAX_REPLY_BYTES of the reply are read.
* An error status is ``WhatsAppError("refused", status)``; no reply, a timeout, or a broken
  connection or reply is ``WhatsAppError("unavailable")``. Neither carries the token, the number or
  the text.
"""

import http.client
import json
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from typing import BinaryIO

from kaagaz.ai.openrouter import build_opener
from kaagaz.settings import Settings

TIMEOUT_SECONDS = 10.0
MAX_REPLY_BYTES = 64 * 1024
_OPENER = build_opener()
# The templates' names at Meta (setup outside the code, item 6), and their language.
CODE_TEMPLATE = "kaagaz_connect_code"
WELCOME_TEMPLATE = "kaagaz_welcome"
DISCONNECTED_TEMPLATE = "kaagaz_disconnected"
LANGUAGE = "en"
START = "start"  # the welcome's [Start] payload
NOT_ME = "not_me:"  # the welcome's [Not me - disconnect] payload: this, then the connection's id
# The welcome template's approved English body, its {{1}} {{2}} {{3}} as {} here (the text form's too).
WELCOME_BODY = (
    "Connected to Kaagaz as {} ({}) at {}. You can ask about your files, get a file, or save documents"
    " to Kaagaz. You only get files you can already see in Kaagaz."
)
WELCOME_BUTTONS = ("Start", "Not me \u2013 disconnect")  # 20 characters at most each
# W5: the hosts a media address may be on besides the Graph API's own (Meta's media is served from
# lookaside.fbsbx.com; confirm against a real payload before release).
MEDIA_HOSTS = (".fbsbx.com", ".facebook.com")
_PARAMETER_CHARS = 60  # a name in a template, on one line (Meta refuses new lines in a parameter)


class WhatsAppError(Exception):
    def __init__(self, code: str, status: int | None = None) -> None:
        super().__init__(code)
        self.code = code
        self.status = status


@dataclass(frozen=True, slots=True)
class Media:
    """A file the person sent, as Meta's Graph API gives it: where to read it, and its size (None
    when Meta gave none)."""

    url: str
    size: int | None


class MetaWhatsApp:
    def __init__(self, graph_url: str, phone_number_id: str, access_token: str) -> None:
        base = graph_url.rstrip("/")  # as Settings keeps it
        self._graph = base
        self._messages = f"{base}/{phone_number_id}/messages"
        self._token = access_token

    @classmethod
    def from_settings(cls, settings: Settings) -> "MetaWhatsApp":
        """WHATSAPP_ENABLED's settings, all set (``Settings.from_env``), else ValueError."""
        graph, number, token = (
            settings.whatsapp_graph_url,
            settings.whatsapp_phone_number_id,
            settings.whatsapp_access_token,
        )
        if graph is None or number is None or token is None:
            raise ValueError("WhatsApp is not set up")
        return cls(graph, number, token)

    def send_text(self, to: str, body: str) -> None:
        """``body`` to the number ``to`` (E.164). O(len(body))."""
        self._post(to, {"type": "text", "text": {"preview_url": False, "body": body}})

    def send_buttons(self, to: str, body: str, buttons: Sequence[tuple[str, str]]) -> None:
        """``body`` with reply buttons, each (id, title): up to 3, as WhatsApp allows."""
        rows = [{"type": "reply", "reply": {"id": id_, "title": title}} for id_, title in buttons]
        interactive = {"type": "button", "body": {"text": body}, "action": {"buttons": rows}}
        self._post(to, {"type": "interactive", "interactive": interactive})

    def send_list(self, to: str, body: str, button: str, rows: Sequence[tuple[str, str, str]]) -> None:
        """``body`` with a list opened by ``button``: its rows, each (id, title, description), in
        one section (up to 10, as WhatsApp allows)."""
        items = [
            {"id": id_, "title": title, **({"description": description} if description else {})}
            for id_, title, description in rows
        ]
        action = {"button": button, "sections": [{"rows": items}]}
        interactive = {"type": "list", "body": {"text": body}, "action": action}
        self._post(to, {"type": "interactive", "interactive": interactive})

    def send_code(self, to: str, code: str) -> None:
        """Meta's authentication template: "<code> is your verification code." [Copy code]."""
        given = [{"type": "text", "text": code}]
        components: list[dict[str, object]] = [
            {"type": "body", "parameters": given},
            {"type": "button", "sub_type": "url", "index": "0", "parameters": given},
        ]
        self._template(to, CODE_TEMPLATE, components)

    def send_welcome(self, to: str, name: str, email: str, organisation: str, link_id: str) -> None:
        names = [{"type": "text", "text": _one_line(text)} for text in (name, email, organisation)]
        components: list[dict[str, object]] = [{"type": "body", "parameters": names}]
        for index, payload in enumerate((START, NOT_ME + link_id)):
            given = [{"type": "payload", "payload": payload}]
            components.append(
                {"type": "button", "sub_type": "quick_reply", "index": str(index), "parameters": given}
            )
        self._template(to, WELCOME_TEMPLATE, components)

    def welcome_buttons(self, to: str, name: str, email: str, organisation: str, link_id: str) -> None:
        """The welcome as a text with reply buttons, inside WhatsApp's 24 hours (no template needed)."""
        body = WELCOME_BODY.format(*(_one_line(text) for text in (name, email, organisation)))
        self.send_buttons(to, body, list(zip((START, NOT_ME + link_id), WELCOME_BUTTONS, strict=True)))

    def send_disconnected(self, to: str) -> None:
        self._template(to, DISCONNECTED_TEMPLATE, [])

    def typing(self, message_id: str) -> None:
        """WhatsApp's message ``message_id`` (its wamid) marked read, with the typing indicator."""
        payload = {
            "messaging_product": "whatsapp",
            "status": "read",
            "message_id": message_id,
            "typing_indicator": {"type": "text"},
        }
        self._read(self._call(self._messages, json.dumps(payload).encode("utf-8"), method="POST"))

    def media(self, media_id: str) -> Media:
        """Where the file ``media_id`` is, and its size. WhatsAppError("refused", status) when Meta
        has no such file (gone after its 7 days, or never), or gives an address that is not its
        own kind; WhatsAppError("unavailable") when Meta can't be reached."""
        raw = self._read(self._call(f"{self._graph}/{urllib.parse.quote(media_id, safe='')}"))
        try:
            found = json.loads(raw)
        except ValueError:
            raise WhatsAppError("unavailable") from None
        url = found.get("url") if isinstance(found, dict) else None
        size = found.get("file_size") if isinstance(found, dict) else None
        if not isinstance(url, str) or not self._meta_address(url):
            raise WhatsAppError("refused", 502)
        if isinstance(size, str) and size.isascii() and size.isdigit():
            size = int(size)
        return Media(url, size if isinstance(size, int) and size >= 0 else None)

    @contextmanager
    def download(self, url: str) -> Iterator[BinaryIO]:
        """The file at ``url`` (an address ``media`` gave), as a stream read with ``read(n)``.
        WhatsAppError as ``media``'s; a read that breaks raises OSError, which the upload treats as
        Meta unavailable."""
        if not self._meta_address(url):
            raise WhatsAppError("refused", 502)
        with self._call(url) as reply:
            yield reply

    def _meta_address(self, url: str) -> bool:
        """https, or http only while WHATSAPP_GRAPH_URL is http (Settings: loopback only); on the
        Graph API's own host or one of Meta's (MEDIA_HOSTS), so the access token never goes
        elsewhere; never a user, password or fragment."""
        parts = urllib.parse.urlsplit(url)
        allowed = {"https"} | ({"http"} if self._graph.startswith("http://") else set())
        host = (parts.hostname or "").lower()
        return (
            parts.scheme in allowed
            and bool(host)
            and (host == urllib.parse.urlsplit(self._graph).hostname or host.endswith(MEDIA_HOSTS))
            and parts.username is None
            and parts.password is None
            and not parts.fragment
        )

    def _template(self, to: str, name: str, components: list[dict[str, object]]) -> None:
        template = {"name": name, "language": {"code": LANGUAGE}, "components": components}
        self._post(to, {"type": "template", "template": template})

    def _post(self, to: str, message: dict[str, object]) -> None:
        """One message to the number ``to`` (E.164); WhatsAppError when it is not taken."""
        payload = {
            "messaging_product": "whatsapp",
            "recipient_type": "individual",
            "to": to.removeprefix("+"),
            **message,
        }
        self._read(self._call(self._messages, json.dumps(payload).encode("utf-8"), method="POST"))

    def _read(self, reply: http.client.HTTPResponse) -> bytes:
        """At most MAX_REPLY_BYTES of ``reply``, closed; a reply that breaks is unavailable."""
        try:
            with reply:
                return reply.read(MAX_REPLY_BYTES)
        except (OSError, ValueError, http.client.HTTPException):
            raise WhatsAppError("unavailable") from None

    def _call(self, url: str, body: bytes | None = None, *, method: str = "GET") -> http.client.HTTPResponse:
        """The open reply to one call with the access token; the caller closes it. WhatsAppError
        when it is not taken."""
        headers = {"Authorization": f"Bearer {self._token}"}
        if body is not None:
            headers["Content-Type"] = "application/json"
        # https, or http on a loopback host only (Settings: WHATSAPP_GRAPH_URL; _meta_address).
        request = urllib.request.Request(url, body, headers, method=method)  # noqa: S310 - checked above
        try:
            reply: http.client.HTTPResponse = _OPENER.open(request, timeout=TIMEOUT_SECONDS)
        except urllib.error.HTTPError as error:
            error.close()
            raise WhatsAppError("refused", error.code) from None
        except (OSError, ValueError, http.client.HTTPException):  # URLError, timeouts; a broken reply
            raise WhatsAppError("unavailable") from None
        return reply


def _one_line(text: str) -> str:
    return " ".join(text.split())[:_PARAMETER_CHARS]


def masked_email(email: str) -> str:
    """An email as its first letter, three dots and its domain: p, three dots, @gmail.com."""
    local, _, domain = email.partition("@")
    return f"{local[:1]}\u2022\u2022\u2022@{domain}"
