"""The TOTP secret at rest (PRD 1 A6): AES-GCM under a key derived from TOTP_ENCRYPTION_KEY.

The additional data is the user's id, so a sealed secret copied onto another
user's row does not open. Every failure is the same ``SecretBoxError``, whose
text never holds the key, the token or the plain secret.
"""

import base64

import pytest

from kaagaz.api.secret_box import SecretBox, SecretBoxError

KEY = "a-totp-encryption-key-for-tests-only-0000"
OTHER_KEY = "another-totp-encryption-key-for-tests-000"
USER = "11111111-1111-4111-8111-111111111111"
OTHER_USER = "33333333-3333-4333-8333-333333333333"
PLAIN = b"12345678901234567890"


def _raw(token: str) -> bytes:
    return base64.b64decode(token.removeprefix("v1:"), validate=True)


def _token(raw: bytes) -> str:
    return "v1:" + base64.b64encode(raw).decode("ascii")


def test_round_trip() -> None:
    box = SecretBox(KEY)
    assert box.open(box.seal(PLAIN, aad=USER), aad=USER) == PLAIN


def test_a_new_box_with_the_same_key_opens_it() -> None:
    token = SecretBox(KEY).seal(PLAIN, aad=USER)
    assert SecretBox(KEY).open(token, aad=USER) == PLAIN


def test_token_is_v1_then_base64_of_a_12_byte_nonce_and_the_ciphertext() -> None:
    token = SecretBox(KEY).seal(PLAIN, aad=USER)
    assert token.startswith("v1:")
    raw = _raw(token)
    assert len(raw) == 12 + len(PLAIN) + 16  # nonce, ciphertext, GCM tag
    assert PLAIN not in raw
    assert base64.b64encode(PLAIN).decode() not in token


def test_every_seal_uses_a_new_random_nonce() -> None:
    box = SecretBox(KEY)
    tokens = {box.seal(PLAIN, aad=USER) for _ in range(20)}
    assert len(tokens) == 20
    assert len({_raw(token)[:12] for token in tokens}) == 20


def test_another_users_id_does_not_open_it() -> None:
    box = SecretBox(KEY)
    token = box.seal(PLAIN, aad=USER)
    with pytest.raises(SecretBoxError):
        box.open(token, aad=OTHER_USER)


def test_a_wrong_key_does_not_open_it() -> None:
    token = SecretBox(KEY).seal(PLAIN, aad=USER)
    with pytest.raises(SecretBoxError):
        SecretBox(OTHER_KEY).open(token, aad=USER)


@pytest.mark.parametrize("position", [0, 11, 12, 12 + len(PLAIN) - 1, -1])
def test_any_changed_byte_is_refused(position: int) -> None:
    box = SecretBox(KEY)
    raw = bytearray(_raw(box.seal(PLAIN, aad=USER)))
    raw[position] ^= 0x01
    with pytest.raises(SecretBoxError):
        box.open(_token(bytes(raw)), aad=USER)


def test_a_cut_short_token_is_refused() -> None:
    box = SecretBox(KEY)
    raw = _raw(box.seal(PLAIN, aad=USER))
    for length in (0, 11, 12, 27, len(raw) - 1):
        with pytest.raises(SecretBoxError):
            box.open(_token(raw[:length]), aad=USER)


@pytest.mark.parametrize(
    "token",
    ["", "v1:", "v2:AAAA", "v1:not base64!", "v1:é", "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA", "V1:AAAA"],
)
def test_garbage_is_refused_with_the_one_error(token: str) -> None:
    with pytest.raises(SecretBoxError):
        SecretBox(KEY).open(token, aad=USER)


def test_the_error_text_never_holds_the_key_token_or_secret() -> None:
    box = SecretBox(KEY)
    token = box.seal(PLAIN, aad=USER)
    with pytest.raises(SecretBoxError) as caught:
        SecretBox(OTHER_KEY).open(token, aad=USER)
    text = str(caught.value) + repr(caught.value)
    for secret in (KEY, OTHER_KEY, token, token[3:], PLAIN.decode(), USER):
        assert secret not in text
    assert caught.value.__cause__ is None
    assert caught.value.__suppress_context__


def test_a_short_key_is_refused() -> None:
    with pytest.raises(ValueError):
        SecretBox("x" * 31)
