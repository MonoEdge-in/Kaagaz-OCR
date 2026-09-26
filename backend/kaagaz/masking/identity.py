"""Mask full Aadhaar and PAN numbers in text.

PROVISIONAL: the masking rule is Vrushit's (Sprint 1 PRD question 1). This is
the rule the council recommended, built so real documents can be read safely
until he writes his own. It implements the pipeline's IdentityMasker port.

What is masked (every example is made up):

* **PAN**: five letters, four digits, one letter (``ABCDE1234F``). Matched
  anywhere, with no word boundaries, because a GSTIN (``27ABCDE1234F1Z5``)
  holds the full PAN inside it. Case does not matter. Zero-width or soft
  hyphen characters hidden between the characters do not stop a match.
* **PAN written with gaps**: ``ABCDE 1234 F`` or ``ABCDE-1234-F``, upper case
  only and standing on its own, so ordinary words are not caught.
* **Aadhaar**: twelve digits starting 2 to 9, in one run or split by up to
  three separators between groups (spaces, tabs, dots, hyphens, dashes,
  Unicode spaces, zero-width characters), in any mix. A digit run longer than
  twelve is left alone (it is some other number).
* **Aadhaar in scientific notation**: ``2.34567890123E11``, the way some
  spreadsheet tools store a 12-digit number.

How: every letter and digit of a match becomes ``X`` except the last four,
and separators stay, so the text keeps its length and every position after
it still points at the right place.

Over-masking is deliberate: a 12-digit e-way bill or a mobile number with
its 91 prefix gets masked too. Missing a real number would break a never-cut
rule. No checksum filter is used, because a mistyped number fails the
checksum but still exposes the person.

Known gaps, for Vrushit (PRD question 1): a number split across two cells is
not caught (each cell is masked on its own), and full-width or look-alike
characters are not matched (documents are English only at launch). The
uploaded file itself is stored as it arrived; only the text taken from it is
masked.

Only ASCII digits count as digits, so Devanagari numerals are not matched.

Time is O(n) in the text length: a fixed number of regular expression passes.
"""

import re

# Characters that can sit between the groups of an Aadhaar number.
_SEP = r"[ \t.\-  -‍‐-― ⁠﻿­]"
# Invisible characters that can hide inside a PAN.
_ZW = r"[​-‍⁠﻿­]?"

_PAN = re.compile(rf"[A-Za-z](?:{_ZW}[A-Za-z]){{4}}{_ZW}[0-9](?:{_ZW}[0-9]){{3}}{_ZW}[A-Za-z]")
_PAN_SPACED = re.compile(r"(?<![A-Za-z0-9])[A-Z]{5}[ \-]{1,2}[0-9]{4}[ \-]{0,2}[A-Z](?![A-Za-z0-9])")
_AADHAAR = re.compile(rf"(?<![0-9])[2-9][0-9]{{3}}{_SEP}{{0,3}}[0-9]{{4}}{_SEP}{{0,3}}[0-9]{{4}}(?![0-9])")
_AADHAAR_SCIENTIFIC = re.compile(r"(?<![0-9.])[2-9]\.[0-9]{0,11}[Ee]\+?11(?![0-9])")

_PATTERNS = (_PAN, _PAN_SPACED, _AADHAAR, _AADHAAR_SCIENTIFIC)
_KEEP = 4  # characters kept visible at the end of each match


def _hide(match: re.Match[str]) -> str:
    """Replace letters and digits with X, keeping separators and the last 4."""
    text = match.group(0)
    hidden_part, visible_part = text[:-_KEEP], text[-_KEEP:]
    return "".join("X" if ch.isalnum() else ch for ch in hidden_part) + visible_part


class IndianIdMasker:
    """Masks PAN and Aadhaar numbers. Safe to run twice: masked text has no
    matches left."""

    def mask(self, text: str) -> str:
        for pattern in _PATTERNS:
            text = pattern.sub(_hide, text)
        return text
