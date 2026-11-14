"""What Meta's webhook delivers (#603): its signature, and the messages in it.

* ``signed``: ``X-Hub-Signature-256`` is ``sha256=`` and the HMAC-SHA256 of the raw body with the
  app secret, in hex. Checked over the bytes as they came (Meta writes Hindi and Marathi as
  ``\\u`` escapes, so JSON written again would not match), in constant time, before anything is
  read.
* ``messages``: the messages to our own number (``metadata.phone_number_id``) in the
  ``messages`` changes; anything else (delivery receipts, another number, a shape not known) is
  left out. Each keeps its id, sender, Meta's time, kind, its text or the id of the button or row
  tapped, a media id, and whether WhatsApp marked it forwarded. Never logged.
"""

import hashlib
import hmac
from dataclasses import dataclass
from datetime import UTC, datetime

from kaagaz.channel.numbers import e164

KINDS = frozenset({"text", "interactive", "button", "image", "document", "audio", "video"})
MEDIA = frozenset({"image", "document", "audio", "video"})
MAX_BODY_CHARS = 4096  # whatsapp_messages.body's limit; WhatsApp's own for a text
MAX_ID_CHARS = 128  # whatsapp_messages.wamid's and media_id's


@dataclass(frozen=True, slots=True)
class Inbound:
    wamid: str
    number: str  # E.164
    kind: str  # one of KINDS, or "other"
    body: str | None
    media_id: str | None
    forwarded: bool
    sent_at: datetime


def signed(secret: str, body: bytes, header: str | None) -> bool:
    """Whether ``header`` is Meta's signature of ``body`` with ``secret``. O(len(body))."""
    if header is None or not header.startswith("sha256="):
        return False
    expected = hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected.encode("ascii"), header.removeprefix("sha256=").encode("utf-8"))


def messages(payload: object, phone_number_id: str) -> list[Inbound]:
    """The well-formed messages to ``phone_number_id`` in a webhook's parsed JSON."""
    found: list[Inbound] = []
    for entry in _items(_get(payload, "entry")):
        for change in _items(_get(entry, "changes")):
            value = _get(change, "value")
            if (
                _get(change, "field") != "messages"
                or _get(_get(value, "metadata"), "phone_number_id") != phone_number_id
            ):
                continue
            for message in _items(_get(value, "messages")):
                inbound = _inbound(message)
                if inbound is not None:
                    found.append(inbound)
    return found


def _inbound(message: object) -> Inbound | None:
    wamid, sender, stamp, kind = (_get(message, key) for key in ("id", "from", "timestamp", "type"))
    if not (isinstance(wamid, str) and 0 < len(wamid) <= MAX_ID_CHARS and isinstance(sender, str)):
        return None
    if not (isinstance(stamp, str) and stamp.isascii() and stamp.isdigit() and len(stamp) <= 12):
        return None
    number = e164(sender)
    if number is None:
        return None
    kind = kind if isinstance(kind, str) and kind in KINDS else "other"
    part = _get(message, kind)
    media_id = _get(part, "id") if kind in MEDIA else None
    context = _get(message, "context")
    return Inbound(
        wamid=wamid,
        number=number,
        kind=kind,
        body=_body(kind, part),
        media_id=media_id if isinstance(media_id, str) and 0 < len(media_id) <= MAX_ID_CHARS else None,
        forwarded=_get(context, "forwarded") is True or _get(context, "frequently_forwarded") is True,
        sent_at=datetime.fromtimestamp(int(stamp), UTC),
    )


def _body(kind: str, part: object) -> str | None:
    """The text typed, the id of the button or list row tapped, or a template button's payload."""
    if kind == "text":
        value = _get(part, "body")
    elif kind == "interactive":
        value = _get(_get(part, "button_reply"), "id") or _get(_get(part, "list_reply"), "id")
    elif kind == "button":
        value = _get(part, "payload")
    else:
        return None
    return value[:MAX_BODY_CHARS] if isinstance(value, str) else None


def _get(value: object, key: str) -> object:
    return value.get(key) if isinstance(value, dict) else None


def _items(value: object) -> list[object]:
    return value if isinstance(value, list) else []
