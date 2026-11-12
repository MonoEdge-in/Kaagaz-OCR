"""The super admin's operations alerts (#478 review, 3 Nov 2026): an organisation's reading paused
(kaagaz.jobs.reading_pause) and the server's disk nearly full (``kaagaz.cli disk-alert``, #480).

One rule for both: an alert is sent to OPS_ALERT_EMAIL from EMAIL_FROM through the SES mailer
when both are set, whether EMAIL_ENABLED is on or off. EMAIL_ENABLED is for the emails people get
(their links need APP_URL); an alert has no link, and the server's own alerts should not wait on
people's emails being switched on. A failure is logged once, as ``ops_alert_failed`` with its
purpose and SES's code or the error's type (never the address), and never raised: the caller is
told by ``send_ops_alert``'s answer. ``NoCredentialsError`` there is a container that cannot reach
the server's role (docs/running-locally.md, "Alerts").
"""

import json
import logging

from botocore.exceptions import BotoCoreError, ClientError

from kaagaz.adapters.email import Mailer, SesMailer
from kaagaz.settings import Settings

_log = logging.getLogger(__name__)


def missing(settings: Settings) -> str | None:
    """What to set before an alert can be sent, or None when it can be."""
    if settings.ops_alert_email is None:
        return "set OPS_ALERT_EMAIL in .env: the address the server's alerts go to"
    if settings.email_from is None:
        return "set EMAIL_FROM in .env: the SES address the alert is sent from"
    return None


def send_ops_alert(
    settings: Settings, subject: str, text: str, *, purpose: str, mailer: Mailer | None = None
) -> bool:
    """Email the alert (``mailer``, else SES from EMAIL_FROM); whether it was sent. Not sent when
    ``missing`` names a setting, or SES refused it (logged with ``purpose``)."""
    to, sender = settings.ops_alert_email, settings.email_from
    if to is None or sender is None:  # ``missing``'s two
        return False
    try:
        (mailer or SesMailer(settings.ses_region, sender)).send(to=to, subject=subject, text=text)
    except (BotoCoreError, ClientError) as error:
        code = error.response.get("Error", {}).get("Code") if isinstance(error, ClientError) else None
        failed = {"event": "ops_alert_failed", "purpose": purpose, "error": code or type(error).__name__}
        # In the message too: the log's JSON lines print ``code`` but not ``purpose`` or ``error``.
        _log.error(json.dumps(failed, separators=(",", ":")), extra={**failed, "code": "ops_alert_failed"})
        return False
    return True
