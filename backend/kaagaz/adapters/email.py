"""Sending email (#457 phase 0): Amazon SES in Mumbai through boto3, plain text only.

``Mailer`` is the one thing the API asks of it: send one email to one address. ``SesMailer``
calls SES's v2 ``send_email`` from the one sending address (``EMAIL_FROM``; the server's role
may send from that address only). ``MemoryMailer`` keeps what was sent, for tests.

A failure raises whatever boto3 raised; the caller (kaagaz/api/emails.py) logs it with the
email's purpose only, never the address, and goes on.
"""

from dataclasses import dataclass, field
from typing import Protocol

import boto3
from botocore.config import Config

# One short try: a person is waiting on the reply (PROVISIONAL).
_CONFIG = Config(connect_timeout=5, read_timeout=10, retries={"max_attempts": 2})


class Mailer(Protocol):
    def send(self, *, to: str, subject: str, text: str) -> None: ...


class SesMailer:
    def __init__(self, region: str, sender: str) -> None:
        self._client = boto3.client("sesv2", region_name=region, config=_CONFIG)
        self._sender = sender

    def send(self, *, to: str, subject: str, text: str) -> None:
        self._client.send_email(
            FromEmailAddress=self._sender,
            Destination={"ToAddresses": [to]},
            Content={"Simple": {"Subject": {"Data": subject}, "Body": {"Text": {"Data": text}}}},
        )


@dataclass(frozen=True, slots=True)
class SentEmail:
    to: str
    subject: str
    text: str


@dataclass
class MemoryMailer:
    sent: list[SentEmail] = field(default_factory=list)

    def send(self, *, to: str, subject: str, text: str) -> None:
        self.sent.append(SentEmail(to, subject, text))
