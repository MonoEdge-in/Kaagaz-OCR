"""The Keycloak admin client (#737) against a fake HTTP opener: what it asks for, and that every
failure is one plain error with no secret, token or reply in it."""

import json
import urllib.error
import urllib.request
from collections.abc import Callable
from dataclasses import replace
from typing import Any

import pytest

from kaagaz.api import keycloak_admin as kc
from tests.settings_factory import make_settings

ISSUER = "https://id.example.test/realms/kaagaz-test"
SECRET = "a-test-provisioner-secret-of-32-chars00"
SETTINGS = make_settings(
    keycloak_enabled=True,
    keycloak_issuer=ISSUER,
    keycloak_client_id="kaagaz-web",
    keycloak_redirect_uri="https://app.example.test/api/auth/keycloak/callback",
    keycloak_admin_client_id="kaagaz-provisioner",
    keycloak_admin_client_secret=SECRET,
)
ADMIN = "https://id.example.test/admin/realms/kaagaz-test"
Reply = tuple[int, Any, dict[str, str]]


class _Response:
    def __init__(self, status: int, body: Any, headers: dict[str, str]) -> None:
        self.status = status
        self._body = b"" if body is None else json.dumps(body).encode()
        self.headers = headers

    def read(self, limit: int = -1) -> bytes:
        return self._body

    def __enter__(self) -> "_Response":
        return self

    def __exit__(self, *args: object) -> None:
        return None


class _Opener:
    """Answers each request by (method, path-without-query) from ``routes``; records them all."""

    def __init__(self) -> None:
        self.sent: list[tuple[str, str, Any, dict[str, str]]] = []
        self.routes: dict[tuple[str, str], Reply | Callable[[], Reply]] = {
            ("POST", f"{ISSUER}/protocol/openid-connect/token"): (
                200,
                {"access_token": "tok", "expires_in": 60},
                {},
            )
        }

    def open(self, request: urllib.request.Request, timeout: float) -> _Response:
        assert timeout == kc.TIMEOUT_SECONDS
        url = request.full_url
        data = request.data
        body = data.decode() if isinstance(data, bytes) else None
        self.sent.append((str(request.get_method()), url, body, dict(request.header_items())))
        route = self.routes.get((str(request.get_method()), url.split("?")[0]))
        if route is None:
            raise urllib.error.HTTPError(url, 404, "no", None, None)  # type: ignore[arg-type]
        status, payload, headers = route() if callable(route) else route
        if status >= 400:
            raise urllib.error.HTTPError(url, status, "no", None, None)  # type: ignore[arg-type]
        return _Response(status, payload, headers)

    def calls(self) -> list[tuple[str, str]]:
        return [(method, url.removeprefix(ADMIN)) for method, url, _, _ in self.sent if url.startswith(ADMIN)]


@pytest.fixture
def opener(monkeypatch: pytest.MonkeyPatch) -> _Opener:
    fake = _Opener()
    monkeypatch.setattr(kc, "_OPENER", fake)
    monkeypatch.setattr(kc, "_token", None)
    return fake


def test_the_token_comes_from_the_issuers_token_endpoint_by_client_credentials_and_is_reused(
    opener: _Opener,
) -> None:
    opener.routes[("GET", f"{ADMIN}/users")] = (200, [], {})
    kc.find_user_by_email(SETTINGS, "a@x.test")
    kc.find_user_by_email(SETTINGS, "a@x.test")
    tokens = [s for s in opener.sent if s[1].endswith("/token")]
    assert len(tokens) == 1
    assert (
        tokens[0][2] == f"grant_type=client_credentials&client_id=kaagaz-provisioner&client_secret={SECRET}"
    )
    assert all(s[3]["Authorization"] == "Bearer tok" for s in opener.sent if s[1].startswith(ADMIN))


def test_find_user_by_email_asks_for_an_exact_match_and_checks_the_reply(opener: _Opener) -> None:
    opener.routes[("GET", f"{ADMIN}/users")] = (
        200,
        [{"id": "other", "email": "ab@x.test"}, {"id": "abc-123", "email": "A@X.test"}],
        {},
    )
    assert kc.find_user_by_email(SETTINGS, "a@x.test") == "abc-123"
    assert opener.sent[-1][1].endswith("/users?email=a%40x.test&exact=true")
    opener.routes[("GET", f"{ADMIN}/users")] = (200, [{"id": "other", "email": "ab@x.test"}], {})
    assert kc.find_user_by_email(SETTINGS, "a@x.test") is None


def test_create_user_sends_the_profile_and_returns_the_id_from_the_location_header(opener: _Opener) -> None:
    opener.routes[("POST", f"{ADMIN}/users")] = (201, None, {"Location": f"{ADMIN}/users/new-id-1"})
    assert kc.create_user(SETTINGS, email="a@x.test", name="Asha Rao Iyer") == "new-id-1"
    assert json.loads(str(opener.sent[-1][2])) == {
        "username": "a@x.test",
        "email": "a@x.test",
        "firstName": "Asha",
        "lastName": "Rao Iyer",
        "enabled": True,
        "emailVerified": True,
        "requiredActions": ["UPDATE_PASSWORD", "CONFIGURE_TOTP"],
    }
    kc.create_user(SETTINGS, email="b@x.test", name="Mono")  # one word: both names are filled
    assert json.loads(str(opener.sent[-1][2]))["lastName"] == "Mono"


def test_a_taken_email_and_a_missing_location_are_refused(opener: _Opener) -> None:
    opener.routes[("POST", f"{ADMIN}/users")] = (409, None, {})
    with pytest.raises(kc.AlreadyExists):
        kc.create_user(SETTINGS, email="a@x.test", name="A")
    opener.routes[("POST", f"{ADMIN}/users")] = (201, None, {})
    with pytest.raises(kc.KeycloakAdminError):
        kc.create_user(SETTINGS, email="a@x.test", name="A")


def test_the_setup_email_asks_for_both_actions_for_twelve_hours(opener: _Opener) -> None:
    opener.routes[("PUT", f"{ADMIN}/users/u-1/execute-actions-email")] = (204, None, {})
    kc.send_setup_email(SETTINGS, "u-1")
    method, url, body, _ = opener.sent[-1]
    assert (method, json.loads(str(body))) == ("PUT", ["UPDATE_PASSWORD", "CONFIGURE_TOTP"])
    assert "client_id=kaagaz-web" in url and "lifespan=43200" in url
    assert "redirect_uri=https%3A%2F%2Fapp.example.test%2Fapi%2Fauth%2Fkeycloak%2Fcallback" in url


def test_remove_otp_deletes_only_otp_credentials(opener: _Opener) -> None:
    opener.routes[("GET", f"{ADMIN}/users/u-1/credentials")] = (
        200,
        [{"id": "c-pass", "type": "password"}, {"id": "c-otp", "type": "otp"}],
        {},
    )
    opener.routes[("DELETE", f"{ADMIN}/users/u-1/credentials/c-otp")] = (204, None, {})
    kc.remove_otp(SETTINGS, "u-1")
    assert opener.calls() == [("GET", "/users/u-1/credentials"), ("DELETE", "/users/u-1/credentials/c-otp")]


def test_enable_disable_delete_and_unlock_use_the_right_calls(opener: _Opener) -> None:
    opener.routes[("PUT", f"{ADMIN}/users/u-1")] = (204, None, {})
    opener.routes[("DELETE", f"{ADMIN}/users/u-1")] = (204, None, {})
    opener.routes[("DELETE", f"{ADMIN}/attack-detection/brute-force/users/u-1")] = (204, None, {})
    kc.set_enabled(SETTINGS, "u-1", False)
    assert json.loads(str(opener.sent[-1][2])) == {"enabled": False}
    kc.delete_user(SETTINGS, "u-1")
    kc.clear_lock(SETTINGS, "u-1")
    assert opener.calls()[-2:] == [
        ("DELETE", "/users/u-1"),
        ("DELETE", "/attack-detection/brute-force/users/u-1"),
    ]
    opener.routes[("DELETE", f"{ADMIN}/users/u-1")] = (404, None, {})  # already gone is fine
    kc.delete_user(SETTINGS, "u-1")


def test_an_expired_token_is_asked_for_once_more(opener: _Opener) -> None:
    answers = iter([(401, None, {}), (200, [], {})])
    opener.routes[("GET", f"{ADMIN}/users")] = lambda: next(answers)
    assert kc.find_user_by_email(SETTINGS, "a@x.test") is None
    assert len([s for s in opener.sent if s[1].endswith("/token")]) == 2


@pytest.mark.parametrize("status", [401, 403, 500])
def test_every_failure_is_one_plain_error_with_no_secret_in_it(
    opener: _Opener, status: int, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level("DEBUG")
    opener.routes[("GET", f"{ADMIN}/users")] = (status, None, {})
    with pytest.raises(kc.KeycloakAdminError) as caught:
        kc.find_user_by_email(SETTINGS, "a@x.test")
    assert caught.value.message == kc.UNREACHABLE
    assert SECRET not in str(caught.value) and "tok" not in str(caught.value)
    assert SECRET not in caplog.text and "a@x.test" not in caplog.text


def test_a_network_error_and_a_bad_token_reply_are_the_same_plain_error(
    opener: _Opener, monkeypatch: pytest.MonkeyPatch
) -> None:
    opener.routes[("POST", f"{ISSUER}/protocol/openid-connect/token")] = (200, {"access_token": 5}, {})
    with pytest.raises(kc.KeycloakAdminError):
        kc.find_user_by_email(SETTINGS, "a@x.test")

    def offline(*args: Any, **kwargs: Any) -> None:
        raise OSError("down")

    monkeypatch.setattr(opener, "open", offline)
    with pytest.raises(kc.KeycloakAdminError) as caught:
        kc.find_user_by_email(SETTINGS, "a@x.test")
    assert caught.value.message == kc.UNREACHABLE


def test_nothing_is_sent_without_the_provisioner_settings_or_with_an_odd_issuer(opener: _Opener) -> None:
    with pytest.raises(kc.KeycloakAdminError, match="not set up"):
        kc.find_user_by_email(make_settings(), "a@x.test")
    odd = replace(SETTINGS, keycloak_issuer="https://id.example.test/other")
    with pytest.raises(kc.KeycloakAdminError, match="not set up"):
        kc.find_user_by_email(odd, "a@x.test")
    assert opener.sent == []


def test_an_id_that_is_not_an_id_is_never_put_in_a_url(opener: _Opener) -> None:
    for bad in ("../x", "a/b", "", "a b", "x" * 300):
        with pytest.raises(kc.KeycloakAdminError):
            kc.delete_user(SETTINGS, bad)
    assert opener.sent == []
