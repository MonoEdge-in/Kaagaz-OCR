"""The Keycloak sign-in's OpenID Connect checks (#435), without a database: every way an ID
token, the discovery or the settings can be wrong is refused with its code."""

from typing import Any

import pytest

from kaagaz.api import keycloak
from kaagaz.api.auth import _KEYCLOAK_PURPOSE, KeycloakClaims, _sign, read_keycloak, sign_keycloak
from kaagaz.api.keycloak import KeycloakError
from kaagaz.settings import Settings, SettingsError
from tests.keycloak_fake import (
    CLIENT_ID,
    DISCOVERY,
    ISSUER,
    KID,
    FakeRealm,
    claims_for,
    jwk,
    keycloak_settings,
    other_key,
    realm_key,
    sign,
    use_realm,
)
from tests.test_settings import shipped

NOW = 1_800_000_000
ASKED_AT = NOW - 30  # the sign-in was started half a minute ago
NONCE = "made-up-nonce"
EMAIL = "Asha.Member@Example.test"
ORG = "made-up-organisation-id"


def check(token: str, settings: Settings | None = None) -> str:
    """The e-mail (the organisation: test_a_genuine_token_gives_its_e_mail_and_organisation)."""
    found = keycloak.verified_person(
        settings or keycloak_settings(), DISCOVERY, token, nonce=NONCE, asked_at=ASKED_AT, now=NOW
    )
    return found[0]


def refused_with(token: str) -> str:
    with pytest.raises(KeycloakError) as caught:
        check(token)
    return caught.value.code


@pytest.fixture(autouse=True)
def realm(monkeypatch: pytest.MonkeyPatch) -> FakeRealm:
    return use_realm(monkeypatch)


def test_a_genuine_token_gives_its_e_mail_and_organisation() -> None:
    token = sign(claims_for(EMAIL, NONCE, NOW, kaagaz_org=ORG))
    found = keycloak.verified_person(
        keycloak_settings(), DISCOVERY, token, nonce=NONCE, asked_at=ASKED_AT, now=NOW
    )
    assert found == ("asha.member@example.test", ORG)
    no_org = sign(claims_for(EMAIL, NONCE, NOW, kaagaz_org=7))  # not text: no organisation
    found = keycloak.verified_person(
        keycloak_settings(), DISCOVERY, no_org, nonce=NONCE, asked_at=ASKED_AT, now=NOW
    )
    assert found == ("asha.member@example.test", "")


@pytest.mark.parametrize(
    "changes",
    [
        {"nonce": "another-browser"},
        {"nonce": None},
        {"exp": NOW - keycloak.LEEWAY_SECONDS},  # expired, past the leeway
        {"exp": None},
        {"exp": "soon"},
        {"iat": NOW + keycloak.LEEWAY_SECONDS + 1},  # made in the future
        {"iat": None},
        {"aud": "another-client"},
        {"aud": ["another-client"]},
        {"aud": None},
        {"aud": [CLIENT_ID, "another-client"], "azp": None},  # several audiences, no azp
        {"azp": "another-client"},
        {"iss": "https://id.example.test/realms/another"},
        {"iss": None},
        {"auth_time": ASKED_AT - keycloak.LEEWAY_SECONDS - 1},  # a sign-in from before this one
        {"auth_time": None},
    ],
)
def test_a_token_with_a_wrong_claim_is_refused(changes: dict[str, Any]) -> None:
    assert refused_with(sign(claims_for(EMAIL, NONCE, NOW, **changes))) == "token"


@pytest.mark.parametrize("acr", ["1", "pwd", None, ["otp"]])
def test_a_token_without_the_two_step_level_is_refused(acr: object) -> None:
    assert refused_with(sign(claims_for(EMAIL, NONCE, NOW, acr=acr))) == "two_step"


def test_the_leeway_lets_a_token_a_little_past_its_end_through() -> None:
    late = claims_for(EMAIL, NONCE, NOW, exp=NOW - keycloak.LEEWAY_SECONDS + 1)
    assert check(sign(late)) == "asha.member@example.test"


def test_several_audiences_are_fine_when_azp_is_this_client() -> None:
    both = claims_for(EMAIL, NONCE, NOW, aud=["another-client", CLIENT_ID], azp=CLIENT_ID)
    assert check(sign(both)) == "asha.member@example.test"


@pytest.mark.parametrize(
    "header",
    [
        {"alg": "none", "kid": KID},
        {"alg": "HS256", "kid": KID},
        {"alg": "RS256", "kid": "unknown-key"},
        {"alg": "RS256"},  # no kid
    ],
)
def test_a_token_with_a_wrong_header_is_refused(header: dict[str, str]) -> None:
    assert refused_with(sign(claims_for(EMAIL, NONCE, NOW), header=header)) == "token"


def test_a_token_signed_by_another_key_is_refused() -> None:
    assert refused_with(sign(claims_for(EMAIL, NONCE, NOW), key=other_key())) == "token"


def test_a_changed_token_is_refused() -> None:
    head, _, signature = sign(claims_for(EMAIL, NONCE, NOW)).split(".")
    forged = sign(claims_for("someone.else@example.test", NONCE, NOW)).split(".")[1]
    assert refused_with(f"{head}.{forged}.{signature}") == "token"


@pytest.mark.parametrize("token", ["", "a.b", "a.b.c.d", "not base64!.e30.e30", "e30.e30.e30", "ü.e30.e30"])
def test_a_malformed_token_is_refused(token: str) -> None:
    assert refused_with(token) == "token"


def test_a_key_too_short_is_never_used(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(keycloak, "MIN_KEY_BITS", 4096)  # the realm's key has 2048
    assert refused_with(sign(claims_for(EMAIL, NONCE, NOW))) == "token"


def test_a_new_realm_key_is_fetched_once_when_its_kid_is_not_known(realm: FakeRealm) -> None:
    realm.keys = [jwk(other_key(), kid="old-key")]
    keycloak.endpoints(keycloak_settings())
    assert (
        refused_with(sign(claims_for(EMAIL, NONCE, NOW), key=other_key(), header={"alg": "RS256"})) == "token"
    )
    assert realm.key_fetches == 1  # cached, then not fetched again for a token with no kid
    realm.keys = [jwk(realm_key())]  # the realm rotated its key
    assert check(sign(claims_for(EMAIL, NONCE, NOW))) == "asha.member@example.test"
    assert realm.key_fetches == 2
    assert (
        refused_with(sign(claims_for(EMAIL, NONCE, NOW), header={"alg": "RS256", "kid": "gone"})) == "token"
    )
    assert realm.key_fetches == 3  # one more look for an unknown kid, never more


def test_an_encryption_key_with_the_same_kid_is_never_used(realm: FakeRealm) -> None:
    realm.keys = [{**jwk(realm_key()), "use": "enc"}]
    assert refused_with(sign(claims_for(EMAIL, NONCE, NOW))) == "token"


@pytest.mark.parametrize(
    "changes",
    [{"email_verified": False}, {"email_verified": "true"}, {"email_verified": None}, {"email": None}],
)
def test_no_verified_e_mail_is_refused(changes: dict[str, Any]) -> None:
    assert refused_with(sign(claims_for(EMAIL, NONCE, NOW, **changes))) == "email_unverified"


def test_discovery_for_another_issuer_is_refused(realm: FakeRealm) -> None:
    realm.discovery["issuer"] = "https://id.example.test/realms/another"
    with pytest.raises(KeycloakError, match="unreachable"):
        keycloak.endpoints(keycloak_settings())


@pytest.mark.parametrize(
    "url", ["file:///etc/passwd", "http://id.example.test/t", "ftp://id.example.test/t", 7]
)
def test_an_endpoint_that_is_not_https_is_refused(realm: FakeRealm, url: object) -> None:
    realm.discovery["token_endpoint"] = url
    with pytest.raises(KeycloakError, match="unreachable"):
        keycloak.endpoints(keycloak_settings())


def test_keycloak_down_is_unreachable(realm: FakeRealm) -> None:
    realm.down = True
    with pytest.raises(KeycloakError, match="unreachable"):
        keycloak.endpoints(keycloak_settings())


def test_the_discovery_is_kept_for_a_while(realm: FakeRealm) -> None:
    first = keycloak.endpoints(keycloak_settings())
    realm.down = True
    assert keycloak.endpoints(keycloak_settings()) == first


def test_the_sign_in_page_asks_for_pkce_the_nonce_and_a_fresh_two_step_sign_in() -> None:
    url = keycloak.authorize_url(keycloak_settings(), DISCOVERY, state="s", nonce="n", verifier="v" * 64)
    assert url.startswith(DISCOVERY["authorization_endpoint"] + "?")
    assert "code_challenge_method=S256" in url and "nonce=n" in url and "state=s" in url
    assert "prompt=login" in url and "max_age=0" in url and "acr_values=otp" in url
    assert "client_secret" not in url and "v" * 64 not in url


def test_sign_out_sends_the_client_and_comes_back_to_the_sign_in_page() -> None:
    url = keycloak.logout_url(keycloak_settings(), DISCOVERY)
    assert url == (
        DISCOVERY["end_session_endpoint"] + "?client_id=kaagaz-web&post_logout_redirect_uri="
        "https%3A%2F%2Fkaagaz.example.test%2Flogin%3Fsigned_out%3D1"
    )


# --- the settings ------------------------------------------------------------------------------

KEYCLOAK_ENV = {
    "KEYCLOAK_ENABLED": "true",
    "KEYCLOAK_ISSUER": ISSUER + "/",
    "KEYCLOAK_CLIENT_ID": CLIENT_ID,
    "KEYCLOAK_CLIENT_SECRET": "made-up-client-secret",
    "KEYCLOAK_REDIRECT_URI": "https://kaagaz.example.test/api/auth/keycloak/callback",
}


def test_keycloak_is_off_unless_set_to_true() -> None:
    settings = Settings.from_env(shipped())
    assert (settings.keycloak_enabled, settings.keycloak_issuer) == (False, "")


def test_keycloak_on_reads_its_settings() -> None:
    settings = Settings.from_env(shipped(**KEYCLOAK_ENV))
    assert settings.keycloak_enabled and settings.keycloak_issuer == ISSUER  # no trailing slash
    assert "made-up-client-secret" not in repr(settings)


@pytest.mark.parametrize("name", [name for name in KEYCLOAK_ENV if name != "KEYCLOAK_ENABLED"])
def test_keycloak_on_with_a_setting_missing_stops_start_up_by_name(name: str) -> None:
    with pytest.raises(SettingsError, match=name):
        Settings.from_env(shipped(**{**KEYCLOAK_ENV, name: ""}))


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("KEYCLOAK_ENABLED", "yes"),
        ("KEYCLOAK_ISSUER", "http://id.example.test/realms/kaagaz"),  # plain http off this laptop
        ("KEYCLOAK_ISSUER", "https://id.example.test/realms/kaagaz?x=1"),
        ("KEYCLOAK_REDIRECT_URI", "https://kaagaz.example.test/somewhere-else"),
        ("KEYCLOAK_REDIRECT_URI", "http://kaagaz.example.test/api/auth/keycloak/callback"),
    ],
)
def test_a_bad_keycloak_setting_stops_start_up_by_name(name: str, value: str) -> None:
    with pytest.raises(SettingsError, match=name):
        Settings.from_env(shipped(**{**KEYCLOAK_ENV, name: value}))


def test_a_realm_on_this_laptop_may_be_plain_http() -> None:
    local = {
        "KEYCLOAK_ISSUER": "http://localhost:8180/realms/kaagaz",
        "KEYCLOAK_REDIRECT_URI": "http://localhost:3000/api/auth/keycloak/callback",
    }
    assert Settings.from_env(shipped(**{**KEYCLOAK_ENV, **local})).keycloak_enabled


def test_the_keycloak_cookie_reads_back_only_with_every_field_of_its_type() -> None:
    secret = keycloak_settings().session_secret
    claims = KeycloakClaims(
        state="s", nonce="n", verifier="v", next_path="/admin", confirm=True, expires_at=NOW + 60
    )
    assert read_keycloak(sign_keycloak(claims, secret), secret, NOW) == claims
    fields = {"state": "s", "nonce": "n", "verifier": "v", "next": "/admin", "confirm": True, "exp": NOW + 60}
    for changed in ({"confirm": 1}, {"confirm": "yes"}, {"exp": NOW}, {"next": ""}):
        assert read_keycloak(_sign({**fields, **changed}, _KEYCLOAK_PURPOSE, secret), secret, NOW) is None
