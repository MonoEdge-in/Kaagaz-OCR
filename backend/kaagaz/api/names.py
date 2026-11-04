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
import unicodedata

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


# Folder upload (#484, council 3 Nov 2026): a folder or file name from a person's computer is
# one path segment that can't hide or reorder what is shown. It is refused, never changed, when
# it holds a control, format, surrogate, private-use or unassigned character or a line or
# paragraph separator (the categories below), "/" or "\", or is "." or "..". NFC first (a Mac
# sends NFD), so "é" is one name however it was typed. ZWNJ and ZWJ (U+200C, U+200D) stay:
# Devanagari needs them. One rule for folder and file names.
_UNSAFE_CATEGORIES = frozenset({"Cc", "Cf", "Cs", "Co", "Cn", "Zl", "Zp"})


def _unsafe(char: str) -> bool:
    return char in "/\\" or (unicodedata.category(char) in _UNSAFE_CATEGORIES and char not in "\u200c\u200d")


def upload_name(name: str, *, max_length: int) -> str | None:
    """``name`` NFC-normalised and trimmed; None when it is unsafe (above), empty, "." or "..",
    or longer than ``max_length``. The whole name is checked, ends too, before it is trimmed
    (strip() would drop a trailing line feed or paragraph separator). O(len(name))."""
    normal = unicodedata.normalize("NFC", name)
    cleaned = normal.strip()
    if any(map(_unsafe, normal)) or cleaned in (".", ".."):
        return None
    return cleaned if 1 <= len(cleaned) <= max_length else None


def shown_upload_name(name: str, *, max_length: int) -> str:
    """A refused upload name as it can still be listed: NFC, each unsafe character U+FFFD, cut
    to ``max_length``, never empty. O(len(name))."""
    shown = "".join("\ufffd" if _unsafe(char) else char for char in unicodedata.normalize("NFC", name))
    return shown.strip()[:max_length] or "\ufffd"


# An organisation's name (UI audit G12, 16 Oct 2026): "asd" once became an admin screen's
# title. At least ORG_NAME_MIN characters, not one character over and over ("aaa"), and
# not an obvious test word. Checked where an organisation is made: sign-up and the
# operator's console. Folded case, so "TEST" is refused too.
ORG_NAME_MIN = 2
PLACEHOLDER_ORG_NAMES = frozenset(
    {"asd", "asdf", "asdfgh", "qwe", "qwerty", "test", "tests", "testing", "test org", "abc", "xyz"}
    | {"foo", "bar", "foobar", "dummy", "sample", "null", "none", "na", "n/a", "xxx", "temp"}
)
ORG_NAME_PROBLEM = "Enter the organisation's real name, at least 2 characters. For example: Sharma Traders."


def org_name_ok(name: str) -> bool:
    """False for a name too short, one repeated character, or a placeholder. O(len(name))."""
    folded = " ".join(name.split()).casefold()
    return len(folded) >= ORG_NAME_MIN and len(set(folded)) > 1 and folded not in PLACEHOLDER_ORG_NAMES
