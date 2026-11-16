"""Meta's WhatsApp Cloud API (#603, PRD 7 section 15): the replies the channel sends.

* ``send_text``: POST ``{WHATSAPP_GRAPH_URL}/{WHATSAPP_PHONE_NUMBER_ID}/messages`` with the access
  token: one text to one number, link previews off (a preview robot never opens a Kaagaz link).
  Files are never sent on WhatsApp, so there is no media call.
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
import urllib.request
from collections.abc import Sequence

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
_PARAMETER_CHARS = 60  # a name in a template, on one line (Meta refuses new lines in a parameter)


class WhatsAppError(Exception):
    def __init__(self, code: str, status: int | None = None) -> None:
        super().__init__(code)
        self.code = code
        self.status = status


class MetaWhatsApp:
    def __init__(self, graph_url: str, phone_number_id: str, access_token: str) -> None:
        base = graph_url.rstrip("/")  # as Settings keeps it
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
        headers = {"Authorization": f"Bearer {self._token}", "Content-Type": "application/json"}
        # https, or http on a loopback host only (Settings: WHATSAPP_GRAPH_URL).
        request = urllib.request.Request(  # noqa: S310 - the scheme is checked by Settings
            self._messages, json.dumps(payload).encode("utf-8"), headers, method="POST"
        )
        try:
            with _OPENER.open(request, timeout=TIMEOUT_SECONDS) as reply:
                reply.read(MAX_REPLY_BYTES)
        except urllib.error.HTTPError as error:
            error.close()
            raise WhatsAppError("refused", error.code) from None
        except (OSError, ValueError, http.client.HTTPException):  # URLError, timeouts; a broken reply
            raise WhatsAppError("unavailable") from None


def _one_line(text: str) -> str:
    return " ".join(text.split())[:_PARAMETER_CHARS]


def masked_email(email: str) -> str:
    """An email as its first letter, three dots and its domain: p, three dots, @gmail.com."""
    local, _, domain = email.partition("@")
    return f"{local[:1]}\u2022\u2022\u2022@{domain}"
