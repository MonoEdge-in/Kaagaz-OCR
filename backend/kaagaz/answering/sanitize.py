"""Strip anything that is not plain text from what an answer shows.

Document text is untrusted (CLAUDE.md): a link, an image or a bit of markup
inside a document must never reach an answer, even though the page shows it
as plain text anyway. This runs on every sentence and every snippet, whether
it came from a template, the model or a quoted line.

Removed: markdown images, markdown links (their visible text stays, the
target goes), HTML tags, URLs (http, https, ftp, www., javascript:, data:,
vbscript:), control characters and the invisible direction marks that can
reorder text on screen. Runs of spaces become one space.

``strip_ignorable`` drops every Default_Ignorable_Code_Point (Unicode's list:
the format characters, category Cf, plus the combining grapheme joiner, the
Hangul and Mongolian fillers and variation selectors, and the variation
selectors U+FE00 to U+FE0F and U+E0100 to U+E01EF). They draw nothing, so a
check that reads text (verify.py, evidence.py) reads it without them.

Time is O(n) in the text length: a fixed number of regular expression passes.
"""

import re
import unicodedata

SNIPPET_MAX = 300

_MD_IMAGE = re.compile(r"!\[[^\]]*\]\([^)]*\)")
_MD_LINK = re.compile(r"\[([^\]]*)\]\([^)]*\)")
_HTML_TAG = re.compile(r"<[^>]*>")
_URL = re.compile(r"(?:\b(?:https?|ftp)://|\bwww\.)\S+", re.IGNORECASE)
_SCHEME = re.compile(r"\b(?:javascript|data|vbscript):\S*", re.IGNORECASE)
# C0 and C1 controls except tab and newlines (turned into spaces below), and the
# bidirectional overrides and isolates (U+202A to U+202E, U+2066 to U+2069).
_CONTROL = re.compile("[\x00-\x08\x0b\x0c\x0e-\x1f\x7f-\x9f‪-‮⁦-⁩]")
_SPACES = re.compile(r"\s+")
# The Default_Ignorable_Code_Points that are not category Cf.
_IGNORABLE = re.compile(
    "[\u034f\u115f\u1160\u17b4\u17b5\u180b-\u180f\u3164\ufe00-\ufe0f\uffa0\U000e0100-\U000e01ef]"
)


def clean_text(text: str) -> str:
    """Plain text only: no links, images, markup or control characters."""
    text = _CONTROL.sub("", text)
    text = _MD_IMAGE.sub("", text)
    text = _MD_LINK.sub(r"\1", text)
    text = _HTML_TAG.sub("", text)
    text = _URL.sub("", text)
    text = _SCHEME.sub("", text)
    return _SPACES.sub(" ", text).strip()


def clean_snippet(text: str) -> str:
    """``clean_text``, cut to at most ``SNIPPET_MAX`` characters (an ellipsis marks a cut)."""
    text = clean_text(text)
    if len(text) <= SNIPPET_MAX:
        return text
    return text[: SNIPPET_MAX - 1].rstrip() + "…"


def strip_ignorable(text: str) -> str:
    """``text`` without its default-ignorable code points (the module docstring). O(n)."""
    if text.isascii():
        return text  # none of them is ASCII
    text = _IGNORABLE.sub("", text)
    return "".join(ch for ch in text if unicodedata.category(ch) != "Cf")
