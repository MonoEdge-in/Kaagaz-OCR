"""The rule for a name a person types: a folder, a tag or a document's new name.

Why one rule: control characters are removed (they can break a screen or a
log line) and the ends are trimmed. Nothing else changes, so "GST 2026/27"
stays as typed; there is no path strip. A name that is empty after that, or
longer than its limit, is refused, never cut, so nobody gets a name they did
not type. The limits match the database checks (migration 0002).

An uploaded file's name has its own rule (``routers.documents.display_name``):
there the path is stripped and the name is cut, because nobody typed it.
"""

import re

FOLDER_NAME_MAX = 100
TAG_NAME_MAX = 40
DOCUMENT_NAME_MAX = 255

CONTROL_CHARACTERS = re.compile(r"[\x00-\x1f\x7f]")


def clean_name(name: str, *, max_length: int) -> str | None:
    """``name`` without control characters and trimmed; None when that leaves
    nothing or more than ``max_length`` characters. O(len(name))."""
    cleaned = CONTROL_CHARACTERS.sub("", name).strip()
    return cleaned if 1 <= len(cleaned) <= max_length else None


def clean_document_name(name: str) -> str | None:
    """A document's new name (PATCH): the same rule, 1 to 255 characters. O(len(name))."""
    return clean_name(name, max_length=DOCUMENT_NAME_MAX)
