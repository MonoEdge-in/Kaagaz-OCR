"""Meta's WhatsApp Cloud API (#603, PRD 7 section 15): the replies the channel sends.

* ``send_text``: POST ``{WHATSAPP_GRAPH_URL}/{WHATSAPP_PHONE_NUMBER_ID}/messages`` with the access
  token: one text to one number, link previews off (a preview robot never opens a Kaagaz link).
  Files are never sent on WhatsApp, so there is no media call.
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

from kaagaz.ai.openrouter import build_opener
from kaagaz.settings import Settings

TIMEOUT_SECONDS = 10.0
MAX_REPLY_BYTES = 64 * 1024
_OPENER = build_opener()


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
        payload = {
            "messaging_product": "whatsapp",
            "recipient_type": "individual",
            "to": to.removeprefix("+"),
            "type": "text",
            "text": {"preview_url": False, "body": body},
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
