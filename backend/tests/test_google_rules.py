"""The domain rule and the passing link (#507), without a database: Google's ``hd`` is never
trusted alone, the email must be verified, and letter case does not count."""

import pytest

from kaagaz.db.google_rules import (
    domain_ok,
    email_domain,
    kept_hosted_domain,
    ordered,
    passing_link,
    personal,
)

DOMAINS = ("sharmatraders.com", "sharma.in")


def test_no_domains_lets_any_account_in() -> None:
    assert domain_ok((), None, "ramesh@gmail.com")
    assert domain_ok([], None, None, email_verified=False)


@pytest.mark.parametrize(
    ("hosted", "email", "verified"),
    [
        (None, "ramesh@sharmatraders.com", True),  # no hd: a personal account
        ("sharmatraders.com", "ramesh@sharmatraders.com", False),  # an unverified email
        ("sharmatraders.com", "ramesh@gmail.com", True),  # the email's domain outside
        ("other.com", "ramesh@sharmatraders.com", True),  # hd outside
        ("sharmatraders.com", None, True),  # no email
        ("sharmatraders.com", "no-at-sign", True),
        ("sharmatraders.com", "ramesh@sharmatraders.com.evil.io", True),
    ],
)
def test_the_domain_rule_refuses(hosted: str | None, email: str | None, verified: bool) -> None:
    assert not domain_ok(DOMAINS, hosted, email, email_verified=verified)


def test_the_domain_rule_passes_both_in_the_list_in_any_case() -> None:
    assert domain_ok(DOMAINS, "sharmatraders.com", "ramesh@sharmatraders.com")
    assert domain_ok(DOMAINS, "SharmaTraders.COM", "Ramesh@SHARMATRADERS.com")
    assert domain_ok(["SHARMA.IN"], "sharma.in", "a@sharma.in")
    assert domain_ok(DOMAINS, "sharma.in", "ramesh@sharmatraders.com")  # both listed, may differ


def test_a_passing_link_is_approved_and_inside_the_domains() -> None:
    assert passing_link("approved", (), None, "ramesh@gmail.com")
    assert not passing_link("pending", (), None, "ramesh@gmail.com")
    assert passing_link("approved", DOMAINS, "sharmatraders.com", "ramesh@sharmatraders.com")
    assert not passing_link("approved", DOMAINS, None, "ramesh@sharmatraders.com")


def test_a_link_keeps_hd_only_with_a_verified_email() -> None:
    """So the stored link passes exactly when Google's claims would: an unverified email's link
    keeps no ``hd`` and, while the organisation lists domains, never passes."""
    assert kept_hosted_domain("SharmaTraders.com", True) == "sharmatraders.com"
    assert kept_hosted_domain("sharmatraders.com", False) is None
    assert kept_hosted_domain(None, True) is None
    stored = kept_hosted_domain("sharmatraders.com", False)
    assert not passing_link("approved", DOMAINS, stored, "ramesh@sharmatraders.com")
    assert not domain_ok(DOMAINS, "sharmatraders.com", "ramesh@sharmatraders.com", email_verified=False)


def test_personal_domains_and_the_helpers() -> None:
    assert all(personal(d) for d in ("gmail.com", "GMAIL.com", "yahoo.co.in", "yahoo.com", "proton.me"))
    assert not any(personal(d) for d in ("sharmatraders.com", "myyahoo.com", "outlook.in"))
    assert email_domain("A@B.Com") == "b.com"
    assert email_domain("a@") is None
    assert ordered(["member", "admin", "member"]) == ["admin", "member"]
