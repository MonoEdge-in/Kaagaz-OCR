"""The platform operator's tokens and cookie (super admin PR 2). Pure: no database."""

import base64
import hashlib
import hmac
import json
from typing import Any

import pytest
from fastapi import Response

from kaagaz.api.auth import (
    OPERATOR_COOKIE,
    OperatorClaims,
    SessionClaims,
    read_operator,
    read_session,
    set_operator_cookie,
    sign_operator,
    sign_session,
)
from tests.settings_factory import make_settings
from tests.test_api_auth import _offline_client
from tests.test_api_support import NOW

SECRET = make_settings().session_secret
OID, OTHER = "11111111-1111-4111-8111-111111111111", "33333333-3333-4333-8333-333333333333"
CID = "22222222-2222-4222-8222-222222222222"
AT = int(NOW.timestamp())
OPERATOR = OperatorClaims(operator_id=OID, session_version=2, expires_at=AT + 60)


def _signed(payload: dict[str, Any], purpose: bytes) -> str:
    body = base64.urlsafe_b64encode(json.dumps(payload, separators=(",", ":"), sort_keys=True).encode())
    text = body.rstrip(b"=").decode()
    mac = hmac.new(SECRET.encode(), purpose + b"\x00" + text.encode(), hashlib.sha256).digest()
    return text + "." + base64.urlsafe_b64encode(mac).rstrip(b"=").decode()


def _operator(**changes: Any) -> dict[str, Any]:
    return {"oid": OID, "ver": 2, "exp": AT + 60, **changes}


def test_an_operator_token_is_exactly_oid_ver_exp_under_the_operator_purpose() -> None:
    token = sign_operator(OPERATOR, SECRET)
    assert token == _signed(_operator(), b"operator")
    assert read_operator(token, SECRET, AT) == OPERATOR
    assert read_operator(token, SECRET, AT + 60) is None  # the end is exclusive
    assert read_operator(token, SECRET + "x", AT) is None


def test_operator_and_customer_tokens_never_read_as_each_other() -> None:
    claims = SessionClaims(
        user_id=OID, customer_id=CID, role="admin", scope="admin", session_version=2, expires_at=AT + 60
    )
    customer = {"uid": OID, "cid": CID, "role": "admin", "scope": "admin", "ver": 2, "exp": AT + 60}
    assert read_operator(sign_session(claims, SECRET), SECRET, AT) is None
    assert read_session(sign_operator(OPERATOR, SECRET), SECRET, AT) is None
    assert read_operator(_signed(_operator(), b"session"), SECRET, AT) is None  # right fields, other purpose
    assert read_session(_signed(customer, b"operator"), SECRET, AT) is None
    assert read_session(_signed({**customer, "scope": "operator"}, b"session"), SECRET, AT) is None
    enrol = sign_operator(
        OperatorClaims(operator_id=OID, session_version=2, expires_at=AT + 60, sealed_secret="v1:x"), SECRET
    )
    assert read_operator(enrol, SECRET, AT) is None
    assert read_operator(sign_operator(OPERATOR, SECRET), SECRET, AT, enrol=True) is None
    assert read_operator(enrol, SECRET, AT, enrol=True) is not None


@pytest.mark.parametrize(
    "payload",
    [
        _operator(cid=CID),
        _operator(scope="operator"),
        {"oid": OID, "ver": 2},
        _operator(oid="not-an-id"),
        _operator(ver=True),
        _operator(ver=0),
        _operator(exp="soon"),
    ],
)
def test_an_operator_token_with_a_wrong_extra_or_missing_claim_is_refused(payload: dict[str, Any]) -> None:
    assert read_operator(_signed(payload, b"operator"), SECRET, AT) is None


@pytest.mark.parametrize("secure", [False, True])
def test_the_operator_cookie_is_httponly_strict_for_api_operator_and_30_minutes(secure: bool) -> None:
    response = Response()
    claims = OperatorClaims(operator_id=OID, session_version=1, expires_at=AT + 1800)
    set_operator_cookie(response, claims, make_settings(cookie_secure=secure), now=AT)
    cookie = response.headers["set-cookie"]
    assert cookie.startswith(f"{OPERATOR_COOKIE}=")
    for flag in ("HttpOnly", "Max-Age=1800", "Path=/api/operator", "SameSite=strict"):
        assert flag in cookie
    assert ("Secure" in cookie) is secure


def test_a_forged_operator_cookie_is_refused_without_the_database() -> None:
    forged = sign_operator(OPERATOR, SECRET + "x")
    reply = _offline_client().get("/api/operator/me", headers={"Cookie": f"{OPERATOR_COOKIE}={forged}"})
    assert (reply.status_code, reply.json()["error"]["code"]) == (401, "not_signed_in")


def test_operator_requests_are_rate_limited_per_operator() -> None:
    client = _offline_client(rate_limit_per_minute=1)

    def login(oid: str) -> int:
        token = sign_operator(OperatorClaims(operator_id=oid, session_version=1, expires_at=AT + 60), SECRET)
        headers = {"X-Requested-With": "kaagaz", "Cookie": f"{OPERATOR_COOKIE}={token}"}
        return client.post("/api/operator/login", json={}, headers=headers).status_code

    assert login(OID) == 400  # refused before the database (no body), but counted for op:OID
    assert login(OID) == 429
    assert login(OTHER) == 400  # another operator has its own bucket
