"""Strip anything that is not plain text from what an answer shows.

Document text is untrusted (CLAUDE.md): a link, an image or a bit of markup
inside a document must never reach an answer, even though the page shows it
as plain text anyway. This runs on every sentence and every snippet, whether
it came from a template, the model or a quoted line.

Removed: markdown images, markdown links (their visible text stays, the
target goes), HTML tags, URLs (http, https, ftp, www., javascript:, data:,
vbscript:), control characters and the invisible direction marks that can
reorder text on screen. Runs of spaces become one space.

Time is O(n) in the text length: a fixed number of regular expression passes.
"""

import re

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
