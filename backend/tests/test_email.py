"""Email (#457 phase 0) without a database: the settings, the SES adapter against a stubbed
boto3 client, the in-memory mailer and the email texts.

The routes and the links themselves: tests/test_api_email.py.
"""

import inspect
import re
from collections.abc import Callable
from unittest import mock

import boto3
import pytest
from botocore import UNSIGNED
from botocore.config import Config
from botocore.stub import Stubber

from kaagaz.adapters.email import MemoryMailer, SesMailer
from kaagaz.api import emails
from kaagaz.settings import Settings, SettingsError
from tests.test_settings import shipped

SENDER = "no-reply@example.com"


# --- settings ---------------------------------------------------------------------------


def test_email_is_off_unless_true_and_then_needs_a_sender_and_the_app_url() -> None:
    off = Settings.from_env(shipped())
    assert (off.email_enabled, off.email_from, off.app_url, off.ses_region) == (
        False,
        None,
        None,
        "ap-south-1",
    )
    with pytest.raises(SettingsError, match="EMAIL_FROM"):
        Settings.from_env(shipped(EMAIL_ENABLED="true", APP_URL="https://kaagaz.example.com"))
    with pytest.raises(SettingsError, match="APP_URL"):
        Settings.from_env(shipped(EMAIL_ENABLED="true", EMAIL_FROM=SENDER))

    on = Settings.from_env(
        shipped(EMAIL_ENABLED="true", EMAIL_FROM=SENDER, APP_URL="https://kaagaz.example.com/")
    )
    assert (on.email_enabled, on.email_from, on.app_url) == (True, SENDER, "https://kaagaz.example.com")


@pytest.mark.parametrize(
    "url",
    [
        "http://kaagaz.example.com",  # plain http off the laptop: links would travel in the clear
        "https://kaagaz.example.com/app",  # an origin only
        "https://kaagaz.example.com/?a=1",
        "https://user@kaagaz.example.com",
        "kaagaz.example.com",
    ],
)
def test_app_url_is_an_https_origin(url: str) -> None:
    with pytest.raises(SettingsError, match="APP_URL"):
        Settings.from_env(shipped(EMAIL_ENABLED="true", EMAIL_FROM=SENDER, APP_URL=url))


def test_app_url_may_be_plain_http_on_the_laptop() -> None:
    settings = Settings.from_env(
        shipped(EMAIL_ENABLED="true", EMAIL_FROM=SENDER, APP_URL="http://localhost:3000")
    )
    assert settings.app_url == "http://localhost:3000"


# --- the adapters -----------------------------------------------------------------------


def test_ses_mailer_sends_one_plain_text_email_from_the_sender() -> None:
    client = boto3.client("sesv2", region_name="ap-south-1", config=Config(signature_version=UNSIGNED))
    with (
        Stubber(client) as stub,
        mock.patch("kaagaz.adapters.email.boto3.client", return_value=client) as made,
    ):
        stub.add_response(
            "send_email",
            {"MessageId": "m-1"},
            {
                "FromEmailAddress": SENDER,
                "Destination": {"ToAddresses": ["ramesh@example.com"]},
                "Content": {
                    "Simple": {
                        "Subject": {"Data": "Confirm your email"},
                        "Body": {"Text": {"Data": "Hello"}},
                    }
                },
            },
        )
        SesMailer("ap-south-1", SENDER).send(
            to="ramesh@example.com", subject="Confirm your email", text="Hello"
        )
        stub.assert_no_pending_responses()
    assert made.call_args.args == ("sesv2",)
    assert made.call_args.kwargs["region_name"] == "ap-south-1"


def test_memory_mailer_keeps_what_was_sent() -> None:
    mailer = MemoryMailer()
    mailer.send(to="ramesh@example.com", subject="S", text="T")
    assert [(m.to, m.subject, m.text) for m in mailer.sent] == [("ramesh@example.com", "S", "T")]


# --- the texts --------------------------------------------------------------------------

TEMPLATES = (
    emails.confirm_email,
    emails.invite,
    emails.reset_password,
    emails.password_changed,
    emails.password_was_reset,
    emails.two_step_on,
    emails.two_step_off,
    emails.two_step_reset,
    # Changed on purpose (#457 phase 1): a connected Google account's notices, and the admins'.
    emails.google_connected,
    emails.google_waiting,
    emails.google_approved,
    emails.google_declined,
    emails.google_disconnected,
    emails.connection_waiting,
)
# What an email may be told: never a document, its name or content, never an id.
ALLOWED_ARGUMENTS = {"name", "organisation", "link"}
_UUID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")


@pytest.mark.parametrize("template", TEMPLATES, ids=lambda template: template.__name__)
def test_no_email_can_be_given_a_document_and_none_holds_an_id(
    template: Callable[..., tuple[str, str]],
) -> None:
    parameters = inspect.signature(template).parameters
    assert set(parameters) <= ALLOWED_ARGUMENTS
    values = {
        "name": "Ramesh",
        "organisation": "Sharma Traders",
        "link": "https://kaagaz.example.com/x#t=abc",
    }
    subject, text = template(**{key: values[key] for key in parameters})
    assert subject and text
    assert "\n" not in subject
    assert not _UUID.search(subject + text)
    assert "<" not in text  # plain text only


def test_a_security_notice_has_no_link() -> None:
    for notice in (
        emails.password_changed,
        emails.password_was_reset,
        emails.two_step_on,
        emails.two_step_off,
        emails.two_step_reset,
        emails.google_connected,
        emails.google_waiting,
        emails.google_approved,
        emails.google_declined,
        emails.google_disconnected,
    ):
        _, text = notice(name="Ramesh")
        assert "http" not in text


def test_an_email_to_an_address_not_yet_proven_is_never_given_a_name() -> None:
    """Confirm, invite and reset go to an address nobody has proven yet, so a name someone
    typed (at sign-up, say, with another person's address) never rides on them (#457 review)."""
    for template in (emails.confirm_email, emails.invite, emails.reset_password):
        assert "name" not in inspect.signature(template).parameters, template.__name__
        _, text = template(
            **{"organisation": "Sharma Traders", "link": "https://kaagaz.example.com/x#t=abc"}
            if template is emails.invite
            else {"link": "https://kaagaz.example.com/x#t=abc"}
        )
        assert text.startswith("Hello,\n\n")
