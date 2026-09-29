"""Two-step sign-in codes for admins (PRD 1 A2): RFC 6238 TOTP, stdlib only.

Why these rules:

* **HMAC-SHA1, 6 digits, 30-second steps** are what Google and Microsoft
  Authenticator use by default, so a secret typed in by hand just works.
* **The step is a 64-bit big-endian counter** (RFC 4226), so the codes match
  the RFC 6238 Appendix B vectors, including the year-2603 one.
* **One step either side is accepted** (PRD A2), for a phone clock a little off.
* **Replays are refused**: ``matching_step`` only accepts a step later than the
  last accepted one; the caller stores the step it returns with a conditional
  UPDATE (``db.users.accept_totp_step``), so two requests cannot both use it.
* **The otpauth link** (``otpauth_uri``) is the key in the "Key Uri Format"
  that Google Authenticator reads from a QR code. It holds the secret, so it
  is only ever sent to the admin turning 2FA on: never logged or audited.
* **Only ASCII 0 to 9, exactly 6 of them** is a code. ``int()`` and ``\\d``
  would accept Devanagari or fullwidth digits, so neither is used; the compare
  is ``hmac.compare_digest`` on ASCII bytes.

Pure: no I/O and no clock; the caller passes the Unix time. Every function is
O(1): ``matching_step`` computes 3 HMACs. Nothing here logs, and a code or a
secret never appears in an error message.
"""

import base64
import hashlib
import hmac
import re
import secrets
import struct
from urllib.parse import quote

STEP_SECONDS = 30
DIGITS = 6
SECRET_BYTES = 20  # 160 bits, the RFC 4226 recommendation for SHA-1
WINDOW = 1  # steps accepted either side of now
ISSUER = "Kaagaz"  # the name the authenticator app shows above the code

_CODE = re.compile(r"[0-9]{6}")  # a character range, so ASCII digits only
_MAX_STEP = 2**64 - 1


def new_secret() -> bytes:
    """A fresh random secret for one admin: 20 bytes from ``secrets``. O(1)."""
    return secrets.token_bytes(SECRET_BYTES)


def b32(secret: bytes) -> str:
    """The secret as authenticator apps take it: RFC 4648 base32, no ``=`` padding."""
    return base64.b32encode(secret).decode("ascii").rstrip("=")


def grouped(b32: str) -> str:
    """Base32 text in groups of 4 separated by spaces, for typing by hand."""
    return " ".join(b32[start : start + 4] for start in range(0, len(b32), 4))


def otpauth_uri(secret: bytes, account: str) -> str:
    """The key as an ``otpauth://totp/`` link, for a QR code. The account (the
    e-mail) is percent-encoded, ``@`` included; the secret is ``b32``. O(length)."""
    return (
        f"otpauth://totp/{ISSUER}:{quote(account, safe='')}?secret={b32(secret)}"
        f"&issuer={ISSUER}&algorithm=SHA1&digits={DIGITS}&period={STEP_SECONDS}"
    )


def code_at(secret: bytes, step: int, digits: int = DIGITS) -> str:
    """The HOTP code (RFC 4226) for one step: HMAC-SHA1 over ``>Q``, then dynamic truncation.

    Raises ValueError for a step outside 0 to 2**64 - 1 or digits outside 6 to 8. O(1).
    """
    if not 0 <= step <= _MAX_STEP:
        raise ValueError("step must fit in 64 bits")
    if not 6 <= digits <= 8:
        raise ValueError("digits must be 6 to 8")
    mac = hmac.new(secret, struct.pack(">Q", step), hashlib.sha1).digest()
    offset = mac[-1] & 0x0F
    number = struct.unpack(">I", mac[offset : offset + 4])[0] & 0x7FFFFFFF
    return str(number % 10**digits).zfill(digits)


def matching_step(secret: bytes, code: str, now_unix: int, last_step: int | None) -> int | None:
    """The step this code belongs to, or None.

    None when the code is not exactly 6 ASCII digits, matches no step within one
    of now, or matches only steps at or before ``last_step`` (already used). All
    3 candidate steps are compared, so the time taken does not depend on which
    one matched; when two match, the later one is returned. O(1).
    """
    if not code.isascii() or _CODE.fullmatch(code) is None:
        return None
    given = code.encode("ascii")
    current = now_unix // STEP_SECONDS
    matched: int | None = None
    for step in range(current - WINDOW, current + WINDOW + 1):
        if step < 0 or step > _MAX_STEP or (last_step is not None and step <= last_step):
            continue
        if hmac.compare_digest(code_at(secret, step).encode("ascii"), given):
            matched = step
    return matched
