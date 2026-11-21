"""Email (#457 phase 0) without a database: the settings, the SES adapter against a stubbed
boto3 client, the in-memory mailer and the email texts.

The routes and the links themselves: tests/test_api_email.py.
"""

import inspect
import logging
import re
from collections.abc import Callable
from types import SimpleNamespace
from typing import cast
from unittest import mock

import boto3
import pytest
from botocore import UNSIGNED
from botocore.config import Config
from botocore.exceptions import ClientError, EndpointConnectionError
from botocore.stub import Stubber

from kaagaz.adapters.email import MemoryMailer, SesMailer
from kaagaz.api import emails
from kaagaz.api.context import AppContext
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
    emails.two_step_reset,
    # Changed on purpose (#603): WhatsApp connected, and turned on for the organisation.
    emails.whatsapp_connected,
    emails.whatsapp_turned_on,
    emails.access_given,  # Changed on purpose (PRD 7, #601): the kind of access only
)
# What an email may be told: never a document, its name or content, never an id.

# Changed on purpose (PRD 7, #601): who gave access to whom, and its kind (a fixed list).
ALLOWED_ARGUMENTS = {"name", "organisation", "link", "google", "admin", "person", "kind"}
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
        "google": True,
        "admin": "Sunita",
        "person": "Ravi",
        "kind": "file",
    }
    subject, text = template(**{key: values[key] for key in parameters})
    assert subject and text
    assert "\n" not in subject
    assert not _UUID.search(subject + text)
    assert "<" not in text  # plain text only


def test_a_security_notice_has_no_link() -> None:
    for notice in (
        emails.two_step_reset,
        emails.whatsapp_connected,  # Changed on purpose (#603)
    ):
        _, text = notice(name="Ramesh")
        assert "http" not in text


def test_an_email_to_an_address_not_yet_proven_is_never_given_a_name() -> None:
    """Confirm goes to an address nobody has proven yet, so a name someone
    typed (at sign-up, say, with another person's address) never rides on them (#457 review)."""
    templates: tuple[Callable[..., tuple[str, str]], ...] = (emails.confirm_email,)
    for template in templates:
        assert "name" not in inspect.signature(template).parameters, template.__name__
        _, text = template(link="https://kaagaz.example.com/x#t=abc")
        assert text.startswith("Hello,\n\n")


class _Refusing(MemoryMailer):
    def __init__(self, error: Exception) -> None:
        super().__init__()
        self.error = error

    def send(self, *, to: str, subject: str, text: str) -> None:
        raise self.error


@pytest.mark.parametrize(
    ("error", "said"),
    [
        (
            ClientError({"Error": {"Code": "MessageRejected", "Message": "no"}}, "SendEmail"),
            "MessageRejected",
        ),
        (
            EndpointConnectionError(endpoint_url="https://email.ap-south-1.amazonaws.com"),
            "EndpointConnectionError",
        ),
    ],
)
def test_a_failed_send_says_why_and_an_invite_is_an_error(
    caplog: pytest.LogCaptureFixture, error: Exception, said: str
) -> None:
    """SES's own error code, else the exception's type; never the address."""
    context = cast(
        AppContext, SimpleNamespace(mailer=_Refusing(error), settings=SimpleNamespace(email_enabled=True))
    )
    caplog.set_level(logging.WARNING, logger="kaagaz.api.emails")
    for purpose in ("invite", "reset"):
        assert emails.send(context, "ramesh@example.com", purpose, ("S", "T")) is False
    [invite, reset] = caplog.records
    assert invite.getMessage() == f'{{"event":"email_failed","purpose":"invite","error":"{said}"}}'
    assert (invite.levelno, reset.levelno) == (logging.ERROR, logging.WARNING)
    assert "ramesh" not in caplog.text


def test_access_given_names_the_kind_only_and_who() -> None:
    """PRD 7 (#601): never a file, a folder or a department by name; "themselves" for an Admin's own."""
    for kind in emails.ACCESS_KINDS:
        _, text = emails.access_given("Ramesh", "Sharma Traders", "Sunita", None, kind)
        assert "Sunita gave themselves " + emails.ACCESS_KINDS[kind] in text
        assert "http" not in text
    _, text = emails.access_given("Ramesh", "Sharma Traders", "Sunita", "Ravi", "management")
    assert "Sunita gave Ravi Management" in text
