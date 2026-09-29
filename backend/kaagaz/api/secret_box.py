"""Encrypts the admins' TOTP secrets at rest (PRD 1 A6).

Why this shape (council record, WP1.3 corrected design):

* **AES-GCM** from ``cryptography`` (approved in PRD 1 section 9), 256-bit key,
  a random 96-bit nonce per seal, so the same secret never seals the same way.
* **The key is derived with HKDF-SHA256** (info ``kaagaz totp-secret v1``) from
  ``TOTP_ENCRYPTION_KEY``, so the setting can be any 32+ character text and
  the derived key is used for this one purpose only.
* **The additional data is the user's id**: a sealed secret copied onto another
  user's row does not open.
* **Stored as** ``"v1:" + base64(nonce || ciphertext and tag)``; the ``v1``
  leaves room to change the scheme later without guessing.

Every failure raises the one ``SecretBoxError`` with a fixed message: never
the key, the token or the secret. Sealing and opening are O(length).
"""

import base64
import binascii
import secrets

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

_PREFIX = "v1:"
_INFO = b"kaagaz totp-secret v1"
_NONCE_BYTES = 12
_TAG_BYTES = 16
_MIN_KEY_CHARS = 32


class SecretBoxError(Exception):
    """A sealed value did not open: wrong key, wrong user, changed or malformed."""

    def __init__(self) -> None:
        super().__init__("the sealed value did not open")


class SecretBox:
    def __init__(self, key_text: str) -> None:
        if len(key_text) < _MIN_KEY_CHARS:
            raise ValueError("the key must be at least 32 characters")
        key = HKDF(algorithm=hashes.SHA256(), length=32, salt=None, info=_INFO).derive(
            key_text.encode("utf-8")
        )
        self._aead = AESGCM(key)

    def seal(self, plain: bytes, *, aad: str) -> str:
        """Encrypt ``plain`` bound to ``aad`` (the user's id)."""
        nonce = secrets.token_bytes(_NONCE_BYTES)
        sealed = self._aead.encrypt(nonce, plain, aad.encode("utf-8"))
        return _PREFIX + base64.b64encode(nonce + sealed).decode("ascii")

    def open(self, token: str, *, aad: str) -> bytes:
        """The plain bytes of a token sealed with this key for this ``aad``, else SecretBoxError."""
        if not token.startswith(_PREFIX) or not token.isascii():
            raise SecretBoxError
        try:
            raw = base64.b64decode(token[len(_PREFIX) :], validate=True)
        except (binascii.Error, ValueError):
            raise SecretBoxError from None
        if len(raw) < _NONCE_BYTES + _TAG_BYTES:
            raise SecretBoxError
        try:
            return self._aead.decrypt(raw[:_NONCE_BYTES], raw[_NONCE_BYTES:], aad.encode("utf-8"))
        except InvalidTag:
            raise SecretBoxError from None
