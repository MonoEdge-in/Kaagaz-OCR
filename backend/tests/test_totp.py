"""Two-step codes (PRD 1 A2): RFC 6238 with SHA-1, 6 digits, 30-second steps.

The reference values are RFC 6238 Appendix B (the SHA-1 column), whose seed is
the ASCII text "12345678901234567890". The window, reuse and bad-input tests use
the same seed, so every expected code is computed from those vectors' rules.
No database, no clock: every time is passed in.
"""

import hashlib
import hmac
import struct
from collections.abc import Callable

import pytest

from kaagaz.api import totp

RFC_SEED = b"12345678901234567890"

# RFC 6238 Appendix B, SHA-1: (T in Unix seconds, the 8-digit TOTP).
RFC_VECTORS = [
    (59, "94287082"),
    (1111111109, "07081804"),
    (1111111111, "14050471"),
    (1234567890, "89005924"),
    (2000000000, "69279037"),
    (20000000000, "65353130"),
]

NOW = 1_700_000_000  # an ordinary moment, well inside step 56_666_666
STEP = NOW // 30


# --- the RFC vectors -----------------------------------------------------------------


@pytest.mark.parametrize(("unix_time", "expected"), RFC_VECTORS)
def test_rfc_6238_appendix_b_sha1_vectors(unix_time: int, expected: str) -> None:
    assert totp.code_at(RFC_SEED, unix_time // 30, digits=8) == expected


@pytest.mark.parametrize(("unix_time", "expected"), RFC_VECTORS)
def test_six_digit_code_is_the_tail_of_the_rfc_vector(unix_time: int, expected: str) -> None:
    assert totp.code_at(RFC_SEED, unix_time // 30) == expected[-6:]


@pytest.mark.parametrize(("unix_time", "expected"), RFC_VECTORS)
def test_rfc_codes_match_at_their_own_time(unix_time: int, expected: str) -> None:
    assert totp.matching_step(RFC_SEED, expected[-6:], unix_time, None) == unix_time // 30


def test_the_step_is_packed_as_a_64_bit_big_endian_counter() -> None:
    step = 2**32 + 5  # a 32-bit counter would wrap this to 5
    mac = hmac.new(RFC_SEED, struct.pack(">Q", step), hashlib.sha1).digest()
    offset = mac[-1] & 0x0F
    number = struct.unpack(">I", mac[offset : offset + 4])[0] & 0x7FFFFFFF
    assert totp.code_at(RFC_SEED, step) == f"{number % 1_000_000:06d}"
    assert totp.code_at(RFC_SEED, step) != totp.code_at(RFC_SEED, 5)


def test_the_largest_64_bit_step_works_and_anything_outside_is_refused() -> None:
    assert len(totp.code_at(RFC_SEED, 2**64 - 1)) == 6
    for step in (-1, 2**64):
        with pytest.raises(ValueError):
            totp.code_at(RFC_SEED, step)


@pytest.mark.parametrize("digits", [5, 9])
def test_code_length_outside_6_to_8_is_refused(digits: int) -> None:
    with pytest.raises(ValueError):
        totp.code_at(RFC_SEED, 1, digits=digits)


# --- the secret ------------------------------------------------------------------------


def test_new_secret_is_20_random_bytes() -> None:
    first, second = totp.new_secret(), totp.new_secret()
    assert len(first) == 20
    assert len(second) == 20
    assert first != second


def test_b32_is_rfc_4648_without_padding() -> None:
    assert totp.b32(RFC_SEED) == "GEZDGNBVGY3TQOJQGEZDGNBVGY3TQOJQ"
    assert totp.b32(b"\x00") == "AA"


def test_grouped_splits_into_groups_of_four() -> None:
    assert totp.grouped("GEZDGNBVGY3TQOJQGEZDGNBVGY3TQOJQ") == "GEZD GNBV GY3T QOJQ GEZD GNBV GY3T QOJQ"
    assert totp.grouped("ABCDEF") == "ABCD EF"
    assert totp.grouped("") == ""


def test_otpauth_uri_is_the_key_link_for_a_qr_code() -> None:
    assert totp.otpauth_uri(RFC_SEED, "asha@sharma.in") == (
        "otpauth://totp/Kaagaz:asha%40sharma.in?secret=GEZDGNBVGY3TQOJQGEZDGNBVGY3TQOJQ"
        "&issuer=Kaagaz&algorithm=SHA1&digits=6&period=30"
    )
    # Anything in the e-mail that could break the link is encoded too.
    assert totp.otpauth_uri(b"\x00", "a+b/c?d&e f@x.in").startswith(
        "otpauth://totp/Kaagaz:a%2Bb%2Fc%3Fd%26e%20f%40x.in?secret=AA&"
    )


# --- the window: one step either side ---------------------------------------------


@pytest.mark.parametrize("offset", [-1, 0, 1])
def test_one_step_either_side_is_accepted(offset: int) -> None:
    code = totp.code_at(RFC_SEED, STEP + offset)
    assert totp.matching_step(RFC_SEED, code, NOW, None) == STEP + offset


@pytest.mark.parametrize("offset", [-2, 2])
def test_two_steps_away_is_refused(offset: int) -> None:
    code = totp.code_at(RFC_SEED, STEP + offset)
    window = {totp.code_at(RFC_SEED, STEP + near) for near in (-1, 0, 1)}
    assert code not in window  # so the refusal is about time, not a lucky match
    assert totp.matching_step(RFC_SEED, code, NOW, None) is None


def test_step_boundaries_follow_30_second_steps() -> None:
    start = STEP * 30
    code = totp.code_at(RFC_SEED, STEP)
    assert totp.matching_step(RFC_SEED, code, start, None) == STEP
    assert totp.matching_step(RFC_SEED, code, start + 29, None) == STEP
    assert totp.matching_step(RFC_SEED, code, start + 30 + 29, None) == STEP  # now step + 1: code is -1
    assert totp.matching_step(RFC_SEED, code, start + 60, None) is None  # now step + 2: code is -2


def test_the_first_steps_after_the_epoch_have_no_negative_step() -> None:
    assert totp.matching_step(RFC_SEED, totp.code_at(RFC_SEED, 0), 5, None) == 0


# --- reuse ----------------------------------------------------------------------------------


def test_a_code_for_the_last_accepted_step_is_refused() -> None:
    code = totp.code_at(RFC_SEED, STEP)
    assert totp.matching_step(RFC_SEED, code, NOW, STEP) is None


def test_a_code_older_than_the_last_accepted_step_is_refused() -> None:
    code = totp.code_at(RFC_SEED, STEP - 1)
    assert totp.matching_step(RFC_SEED, code, NOW, STEP) is None


def test_a_newer_code_after_the_last_accepted_step_is_accepted() -> None:
    code = totp.code_at(RFC_SEED, STEP + 1)
    assert totp.matching_step(RFC_SEED, code, NOW, STEP) == STEP + 1


def test_a_last_step_in_the_future_blocks_the_whole_window() -> None:
    for offset in (-1, 0, 1):
        code = totp.code_at(RFC_SEED, STEP + offset)
        assert totp.matching_step(RFC_SEED, code, NOW, STEP + 5) is None


# --- bad codes are refused, never an error ------------------------------------------------

# Digits that int() and \d accept but a code must not: U+0966 to U+096F and U+FF10 to U+FF19.
_DEVANAGARI = str.maketrans("0123456789", "".join(chr(0x0966 + n) for n in range(10)))
_FULLWIDTH = str.maketrans("0123456789", "".join(chr(0xFF10 + n) for n in range(10)))


def _good_code() -> str:
    return totp.code_at(RFC_SEED, STEP)


@pytest.mark.parametrize(
    "code",
    [
        "",
        "12345",
        "1234567",
        "12345a",
        "abcdef",
        "12 345",
        " 123456",
        "123456 ",
        "123456\n",
        "-12345",
        "+12345",
        "12.345",
        "1e5000",
        "123456789012345678901234567890",
    ],
)
def test_malformed_codes_are_refused_without_an_error(code: str) -> None:
    assert totp.matching_step(RFC_SEED, code, NOW, None) is None


@pytest.mark.parametrize(
    "variant",
    [
        pytest.param(lambda code: code.translate(_DEVANAGARI), id="devanagari-digits"),
        pytest.param(lambda code: code.translate(_FULLWIDTH), id="fullwidth-digits"),
        pytest.param(lambda code: code + "\n", id="trailing-newline"),
        pytest.param(lambda code: " " + code, id="leading-space"),
    ],
)
def test_the_right_code_in_non_ascii_or_padded_form_is_refused(variant: Callable[[str], str]) -> None:
    good = _good_code()
    changed = variant(good)
    assert changed != good
    assert totp.matching_step(RFC_SEED, changed, NOW, None) is None
    assert totp.matching_step(RFC_SEED, good, NOW, None) == STEP  # the plain form still works


def test_a_wrong_six_digit_code_is_refused() -> None:
    window = {totp.code_at(RFC_SEED, STEP + near) for near in (-1, 0, 1)}
    wrong = next(f"{n:06d}" for n in range(1_000_000) if f"{n:06d}" not in window)
    assert totp.matching_step(RFC_SEED, wrong, NOW, None) is None


def test_another_secret_does_not_match() -> None:
    code = _good_code()
    other = b"another-twenty-bytes"
    assert code not in {totp.code_at(other, STEP + near) for near in (-1, 0, 1)}
    assert totp.matching_step(other, code, NOW, None) is None
