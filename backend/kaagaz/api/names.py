"""The rule for a name a person types: a folder or a tag.

Why one rule: control characters are removed (they can break a screen or a
log line) and the ends are trimmed. Nothing else changes, so "GST 2026/27"
stays as typed; there is no path strip. A name that is empty after that, or
longer than its limit, is refused, never cut, so nobody gets a name they did
not type. The limits match the database checks (migration 0002).
"""

import re

FOLDER_NAME_MAX = 100
TAG_NAME_MAX = 40

_CONTROL = re.compile(r"[\x00-\x1f\x7f]")


def clean_name(name: str, *, max_length: int) -> str | None:
    """``name`` without control characters and trimmed; None when that leaves
    nothing or more than ``max_length`` characters. O(len(name))."""
    cleaned = _CONTROL.sub("", name).strip()
    return cleaned if 1 <= len(cleaned) <= max_length else None
