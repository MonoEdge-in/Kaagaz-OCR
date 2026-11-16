"""A person's WhatsApp number (#603): found by a keyed hash, kept sealed.

* ``number_hash``: HMAC-SHA256 of the number in E.164 (``+`` and its digits), keyed with
  WHATSAPP_NUMBER_KEY, so a stolen table names no number and the same number always finds its
  connection. WhatsApp gives a number as its digits (``wa_id``); Kaagaz's screen adds the ``+``.
* ``seal`` and ``open_number``: ``SecretBox`` (AES-GCM) with the same key, bound to the person
  (customer and user ids), so a sealed number copied onto another person's row does not open.
"""

import hashlib
import hmac
import re

from kaagaz.api.secret_box import SecretBox

_E164 = re.compile(r"\+?([1-9][0-9]{7,14})")


def e164(number: str) -> str | None:
    """``number`` as ``+`` and its 8 to 15 digits, or None when it is not one."""
    found = _E164.fullmatch(number.strip())
    return None if found is None else "+" + found.group(1)


def number_hash(key: str, number: str) -> bytes:
    """The 32-byte keyed hash a number's connection is found by. ``number`` in E.164."""
    return hmac.new(
        key.encode("utf-8"), b"kaagaz whatsapp number v1|" + number.encode("ascii"), hashlib.sha256
    ).digest()


def _bound(customer_id: str, user_id: str) -> str:
    return f"whatsapp:{customer_id}:{user_id}"


def seal(key: str, number: str, customer_id: str, user_id: str) -> str:
    return SecretBox(key).seal(number.encode("ascii"), aad=_bound(customer_id, user_id))


def open_number(key: str, sealed: str, customer_id: str, user_id: str) -> str:
    """The number sealed for this person; ``SecretBoxError`` for any other."""
    return SecretBox(key).open(sealed, aad=_bound(customer_id, user_id)).decode("ascii")
