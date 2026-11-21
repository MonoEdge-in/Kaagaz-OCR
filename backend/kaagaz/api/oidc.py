"""Google sign-in is gone (21 Nov 2026): Keycloak is the only sign-in for company users.

Only the two switches below are left, always off, for the admin and operator screens that still
ask whether a provider is on (their Google rules go with the People clean-up).
"""

from kaagaz.settings import Settings


def enabled(settings: Settings, name: str) -> bool:
    return False


def enabled_providers(settings: Settings) -> list[str]:
    return []
