"""The OpenID Connect core (kaagaz/api/oidc.py, #457 phase 1) against a made-up Google."""

import hashlib
import urllib.parse

import pytest

from kaagaz.api import oidc
from kaagaz.api.auth import _b64url
from kaagaz.api.oidc import OidcError
from kaagaz.db import google_rules
from tests import oidc_fake
from tests.oidc_fake import CLIENT_ID, FakeGoogle, claims_for, sign, use_google

NOW = 1_793_000_000
NONCE = "made-up-nonce"
VERIFIER = "v" * 64
GOOGLE = oidc.PROVIDERS["google"]
SETTINGS = oidc_fake.google_settings()


def _identity(token: str, *, nonce: str = NONCE, now: int = NOW) -> oidc.Identity:
    return oidc.verified_identity(GOOGLE, SETTINGS, token, nonce=nonce, now=now)


def _refused(token: str, code: str) -> None:
    with pytest.raises(OidcError) as refused:
        _identity(token)
    assert refused.value.code == code


def test_only_google_is_a_provider_and_it_is_off_until_switched_on() -> None:
    assert set(oidc.PROVIDERS) == {"google"}
    assert oidc.enabled(SETTINGS, "google") is True
    assert oidc.enabled(oidc_fake.google_settings(google_signin_enabled=False), "google") is False
    assert oidc.enabled(SETTINGS, "microsoft") is False
    assert oidc.enabled(SETTINGS, "apple") is False
    assert oidc.enabled_providers(SETTINGS) == ["google"]
    assert oidc.enabled_providers(oidc_fake.google_settings(google_signin_enabled=False)) == []


def test_authorize_url_has_pkce_select_account_and_scopes() -> None:
    url = oidc.authorize_url(GOOGLE, SETTINGS, state="s", nonce="n", verifier=VERIFIER)
    parts = urllib.parse.urlsplit(url)
    query = dict(urllib.parse.parse_qsl(parts.query))
    assert f"{parts.scheme}://{parts.netloc}{parts.path}" == "https://accounts.google.com/o/oauth2/v2/auth"
    assert query == {
        "response_type": "code",
        "client_id": CLIENT_ID,
        "redirect_uri": oidc_fake.REDIRECT_URI,
        "scope": "openid email profile",
        "state": "s",
        "nonce": "n",
        "prompt": "select_account",
        "code_challenge": _b64url(hashlib.sha256(VERIFIER.encode()).digest()),
        "code_challenge_method": "S256",
    }
    assert "secret" not in url  # the client secret only ever goes to the token endpoint


@pytest.mark.parametrize("issuer", ["https://accounts.google.com", "accounts.google.com"])
def test_good_token_gives_identity_with_normalised_issuer(
    issuer: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    use_google(monkeypatch)
    token = sign(claims_for("made-up-sub", "Ramesh.K@Gmail.com", NONCE, NOW, iss=issuer))
    assert _identity(token) == oidc.Identity(
        provider="google",
        issuer="https://accounts.google.com",
        subject="made-up-sub",
        email="ramesh.k@gmail.com",
        email_verified=True,
    )


def test_an_unverified_or_missing_email_is_kept_as_such(monkeypatch: pytest.MonkeyPatch) -> None:
    use_google(monkeypatch)
    unverified = _identity(sign(claims_for("s", "a@example.com", NONCE, NOW, email_verified=False)))
    missing = _identity(sign(claims_for("s", "a@example.com", NONCE, NOW, email=None, email_verified=None)))
    assert (unverified.email, unverified.email_verified) == ("a@example.com", False)
    assert (missing.email, missing.email_verified) == (None, False)


def test_bad_signature_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    use_google(monkeypatch)
    _refused(sign(claims_for("s", "a@example.com", NONCE, NOW), key=oidc_fake.other_key()), "bad_token")


def test_unknown_kid_refetches_once_then_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    google = use_google(monkeypatch)
    header = {"alg": "RS256", "kid": "made-up-key-2"}
    _refused(sign(claims_for("s", "a@example.com", NONCE, NOW), header=header), "bad_token")
    assert google.key_fetches == 2  # the cached list, then once more for a new key


def test_a_new_google_key_is_found_by_fetching_again(monkeypatch: pytest.MonkeyPatch) -> None:
    google = use_google(monkeypatch)
    _identity(sign(claims_for("s", "a@example.com", NONCE, NOW)))  # caches the one key
    google.keys = [oidc_fake.jwk(oidc_fake.other_key(), "made-up-key-2")]
    token = sign(
        claims_for("s", "a@example.com", NONCE, NOW),
        key=oidc_fake.other_key(),
        header={"alg": "RS256", "kid": "made-up-key-2"},
    )
    assert _identity(token).subject == "s"
    assert google.key_fetches == 2


def test_only_rs256_is_accepted(monkeypatch: pytest.MonkeyPatch) -> None:
    use_google(monkeypatch)
    _refused(
        sign(claims_for("s", "a@example.com", NONCE, NOW), header={"alg": "none", "kid": "k"}), "bad_token"
    )
    _refused("not-a-token", "bad_token")


def test_wrong_iss_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    use_google(monkeypatch)
    _refused(sign(claims_for("s", "a@example.com", NONCE, NOW, iss="https://evil.example.com")), "bad_token")


def test_wrong_aud_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    use_google(monkeypatch)
    _refused(sign(claims_for("s", "a@example.com", NONCE, NOW, aud="another-client")), "bad_token")


def test_azp_mismatch_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    use_google(monkeypatch)
    _refused(sign(claims_for("s", "a@example.com", NONCE, NOW, azp="another-client")), "bad_token")
    # aud listing others needs azp to be this client
    _refused(sign(claims_for("s", "a@example.com", NONCE, NOW, aud=[CLIENT_ID, "x"], azp=None)), "bad_token")


def test_expired_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    use_google(monkeypatch)
    _refused(sign(claims_for("s", "a@example.com", NONCE, NOW, exp=NOW - 61)), "expired")
    assert _identity(sign(claims_for("s", "a@example.com", NONCE, NOW, exp=NOW - 59))).subject == "s"


def test_future_iat_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    use_google(monkeypatch)
    _refused(sign(claims_for("s", "a@example.com", NONCE, NOW, iat=NOW + 61)), "expired")


def test_wrong_nonce_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    use_google(monkeypatch)
    _refused(sign(claims_for("s", "a@example.com", "another-nonce", NOW)), "wrong_nonce")
    _refused(sign(claims_for("s", "a@example.com", NONCE, NOW, nonce=None)), "wrong_nonce")


def test_a_missing_subject_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    use_google(monkeypatch)
    _refused(sign(claims_for("", "a@example.com", NONCE, NOW)), "bad_token")
    _refused(sign(claims_for("s", "a@example.com", NONCE, NOW, sub=None)), "bad_token")


def _code(google: FakeGoogle, verifier: str = VERIFIER) -> str:
    token = sign(claims_for("s", "a@example.com", NONCE, NOW))
    return google.issue(token, _b64url(hashlib.sha256(verifier.encode()).digest()))


def test_the_code_is_swapped_with_the_secret_and_the_verifier(monkeypatch: pytest.MonkeyPatch) -> None:
    google = use_google(monkeypatch)
    token = oidc.id_token_for(GOOGLE, SETTINGS, _code(google), VERIFIER)
    assert _identity(token).subject == "s"
    assert google.token_forms[0]["client_secret"] == oidc_fake.CLIENT_SECRET


def test_code_used_twice_refused_by_token_endpoint(monkeypatch: pytest.MonkeyPatch) -> None:
    google = use_google(monkeypatch)
    code = _code(google)
    oidc.id_token_for(GOOGLE, SETTINGS, code, VERIFIER)
    with pytest.raises(OidcError) as again:
        oidc.id_token_for(GOOGLE, SETTINGS, code, VERIFIER)
    assert again.value.code == "refused"


def test_wrong_verifier_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    google = use_google(monkeypatch)
    with pytest.raises(OidcError) as refused:
        oidc.id_token_for(GOOGLE, SETTINGS, _code(google), "w" * 64)
    assert refused.value.code == "refused"


def test_google_down_is_unavailable(monkeypatch: pytest.MonkeyPatch) -> None:
    use_google(monkeypatch, FakeGoogle(down=True))
    with pytest.raises(OidcError) as down:
        oidc.id_token_for(GOOGLE, SETTINGS, "code-1", VERIFIER)
    assert down.value.code == "unavailable"


def test_errors_never_carry_token_or_email(monkeypatch: pytest.MonkeyPatch) -> None:
    use_google(monkeypatch)
    token = sign(claims_for("made-up-sub", "ramesh@example.com", "another-nonce", NOW))
    with pytest.raises(OidcError) as refused:
        _identity(token)
    assert str(refused.value) == "wrong_nonce"
    assert refused.value.args == ("wrong_nonce",)


# --- the organisation's domains (#507) ---------------------------------------------------------


def test_hd_is_read_in_lower_case_and_only_as_text(monkeypatch: pytest.MonkeyPatch) -> None:
    use_google(monkeypatch)
    work = _identity(sign(claims_for("s", "a@sharmatraders.com", NONCE, NOW, hd=" SharmaTraders.COM ")))
    personal = _identity(sign(claims_for("s", "a@gmail.com", NONCE, NOW)))
    odd = _identity(sign(claims_for("s", "a@x.com", NONCE, NOW, hd=["x.com"])))
    assert (work.hosted_domain, personal.hosted_domain, odd.hosted_domain) == (
        "sharmatraders.com",
        None,
        None,
    )


def _who(email: str | None, hd: str | None, verified: bool = True) -> oidc.Identity:
    return oidc.Identity(
        provider="google",
        issuer="https://accounts.google.com",
        subject="s",
        email=email,
        email_verified=verified,
        hosted_domain=hd,
    )


DOMAINS = ("sharmatraders.com", "sharma.in")


@pytest.mark.parametrize(
    ("who", "domains", "passes"),
    [
        (_who("ramesh@gmail.com", None), (), True),  # no domains: any account
        (_who(None, None, False), (), True),
        (_who("ramesh@sharmatraders.com", "sharmatraders.com"), DOMAINS, True),
        (_who("ramesh@sharma.in", "sharma.in"), DOMAINS, True),
        (_who("ramesh@sharmatraders.com", "sharmatraders.com"), ("SharmaTraders.com",), True),  # case
        (_who("ramesh@sharmatraders.com", None), DOMAINS, False),  # no hd: a personal account
        (_who("ramesh@sharmatraders.com", "sharmatraders.com", False), DOMAINS, False),  # unverified
        (_who("ramesh@gmail.com", "sharmatraders.com"), DOMAINS, False),  # the email's domain outside
        (_who("ramesh@sharmatraders.com", "evil.com"), DOMAINS, False),  # hd outside
        (_who(None, "sharmatraders.com"), DOMAINS, False),  # hd alone is never enough
        (_who("ramesh@sharmatraders.com.evil.com", "sharmatraders.com"), DOMAINS, False),
    ],
)
def test_domain_ok_needs_hd_and_the_verified_emails_domain(
    who: oidc.Identity, domains: tuple[str, ...], passes: bool
) -> None:
    """Google's claims now, through the one rule every path uses (``google_rules``)."""
    verified = who.email_verified
    assert google_rules.domain_ok(domains, who.hosted_domain, who.email, email_verified=verified) is passes


def test_email_domain() -> None:
    assert google_rules.email_domain("Ramesh@SharmaTraders.com") == "sharmatraders.com"
    assert google_rules.email_domain(None) is None
    assert google_rules.email_domain("no-at-sign") is None
