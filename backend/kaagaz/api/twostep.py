"""Small helpers both sign-ins share (customers' ``routers/auth.py``, operators'
``routers/operator.py``): the one sign-in refusal, a typed code, Unix time and the
TOTP secret box. Pure except ``refused``, which reads the lock minutes. O(1)."""

from datetime import datetime
from functools import cache

from kaagaz.api.context import AppContext
from kaagaz.api.errors import ApiError, bad_credentials
from kaagaz.api.secret_box import SecretBox


def refused(context: AppContext) -> ApiError:
    """The one 401 ``bad_credentials`` for every failed sign-in."""
    return bad_credentials(context.settings.login_lock_minutes)


def digits(code: str | None) -> str:
    """The code without the spaces people type ("123 456"); "" when none was sent."""
    return "" if code is None else "".join(code.split())


def unix(moment: datetime) -> int:
    return int(moment.timestamp())


@cache
def box(key_text: str) -> SecretBox:
    """One SecretBox per key, so the key derivation runs once."""
    return SecretBox(key_text)
