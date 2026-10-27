"""How the assistant talks (PRD 6 package A; build spec 24 Oct 2026, decisions 2, 9 and 14).

Code writes every word of an answer but the model's at most 3 cited sentences:

* **The opening** (``opening``): a fixed English line of at most 12 words, no emoji. The
  first answer of a conversation greets by the time of day in TIMEZONE (05:00 to 11:59
  morning, 12:00 to 16:59 afternoon, else evening) and the asker's first name
  (``first_name``) when ``users.display_name`` gives a plain one; later answers get the next
  ``LEAD_INS`` line not used yet in the conversation, then none. A line is never used twice in
  a conversation (the earlier answers' stored ``opening`` is the line's id). The name is
  only ever put here, after the answer: it never reaches a prompt, a log or the history.
* **The soft not-found** (decision 9): ``SOFT_CLOSEST`` when there are closest documents,
  else ``SOFT_NOTHING``; the same words as ``frontend/components/chat/answer.ts`` (a test
  reads both). No "sorry", no "couldn't" (scan-review-ux-rules 6). #410 (Vrushit's UAT, 27 Oct
  2026): when the question names words no document searched has (``missing``), the line says
  which, then ``SOFT_SOME`` when there are closest documents (``soft_line``).
* **Each document's title and why** (``title``, ``why``): from its card (type, identifier,
  names, date) and the question's words, never a digit in ``why``.
* **The final number check** (``unsupported``, decision 2): every number, and every fraction
  word ("half", "one-fifth"), in a string must be one the caller allows for it: a figure as
  printed in the evidence the string cites, a page, a number of the question or one a fixed
  query gave. Anything else drops the string (fail closed), with a reason code.

Time: O(n) in the text checked; ``title`` and ``why`` are O(card + question).
"""

import re
import unicodedata
from collections.abc import Collection, Mapping, Sequence
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any
from zoneinfo import ZoneInfo

from kaagaz.answering.sanitize import clean_text
from kaagaz.chunking.script import ascii_digits

# Changed on purpose (#410): nothing decides "every part"; a closest document holds some of the
# question's words (evidence.closest_documents), so the line says that and no more.
SOFT_SOME = "These documents match some of your words."
SOFT_CLOSEST = f"No answer to the whole question was found. {SOFT_SOME}"
SOFT_NOTHING = "Nothing in your documents matches this yet."

FOUND = "Here's what I found."  # after the first answer's greeting, when it answers
# Later answers' lead-ins, in the order they are used; the found-only ones skip a not-found answer.
LEAD_INS: tuple[tuple[str, bool], ...] = (
    ("Sure.", False),
    ("Here they are.", True),
    ("Got it.", False),
    ("Here's what your documents say.", True),
    ("All right.", False),
    ("Of course.", False),
)
MAX_OPENING_WORDS = 12
MAX_DOCUMENTS = 10  # listed under an answer; the screen counts the rest
_TITLES = frozenset({"mr", "mrs", "ms", "dr", "shri", "smt", "sri", "श्री", "श्रीमती"})
_MONTHS = (
    "January", "February", "March", "April", "May", "June",
    "July", "August", "September", "October", "November", "December",
)  # fmt: skip
_TYPES = {
    "invoice": "Invoice",
    "receipt": "Receipt",
    "bank_statement": "Bank statement",
    "land_record": "Land record",
    "contract": "Contract",
    "register": "Register",
    "form": "Form",
    "letter": "Letter",
}
# The identifier a title names, by kind, in this order (never a PAN, GSTIN or account number).
_TITLE_IDS = {
    "invoice_number": "",
    "survey_number": "Survey ",
    "khata_number": "Khata ",
    "challan_number": "Challan ",
    "po_number": "PO ",
    "reference_number": "Ref ",
}
_ROLES = {"owner": "owner", "buyer": "buyer", "seller": "seller", "holder": "holder", "employer": "employer"}
_MAX_PART = 60  # characters of a name or identifier in a title or why
_WORD = re.compile(r"(?:[^\W\d_]|[ऀ-ॣ॰-ॿ])+")  # a Devanagari word keeps its marks
_NUMBER = re.compile(r"\d+(?:[.,]\d+)*")
_FRACTION = re.compile(
    r"\b(?:(?:one|two|three|four|five|six|seven|eight|nine|ten)[-\s])?"
    r"(?:half|halves|thirds?|quarters?|fourths?|fifths?|sixths?|sevenths?|eighths?|ninths?|tenths?)\b",
    re.IGNORECASE,
)


def first_name(display_name: str | None) -> str | None:
    """The first word of ``display_name`` (``clean_text``) after any title, when it is 2 to 30
    letters of one script, Latin or Devanagari (with its marks); None for an e-mail address,
    a name with a digit, an initial, or anything else. O(n)."""
    text = clean_text(display_name or "")
    if not text or "@" in text or any(ch.isdigit() for ch in text):
        return None
    words = [word for word in text.split() if word.casefold().rstrip(".") not in _TITLES]
    if not words or not 2 <= len(words[0]) <= 30:
        return None
    name = words[0]
    latin = all(ch.isalpha() and "LATIN" in unicodedata.name(ch, "") for ch in name)
    devanagari = all("ऀ" <= ch <= "ॿ" and unicodedata.category(ch)[0] in "LM" for ch in name)
    if latin:
        return name[0].upper() + name[1:]
    return name if devanagari else None


def greeting(now: datetime, zone: ZoneInfo) -> str:
    """ "Good morning" (05:00 to 11:59), "Good afternoon" (12:00 to 16:59) or "Good evening"
    in ``zone``. ``now`` must know its timezone."""
    if now.tzinfo is None:
        raise ValueError("the clock must give a timezone-aware time")
    hour = now.astimezone(zone).hour
    return "Good morning" if 5 <= hour < 12 else "Good afternoon" if 12 <= hour < 17 else "Good evening"


def opening(
    *, found: bool, display_name: str | None, earlier: Sequence[str | None], now: datetime, zone: ZoneInfo
) -> str | None:
    """The answer's opening line (the module docstring). ``found``: it answers (not a
    not-found answer); ``earlier``: the openings of the conversation's earlier answers, oldest
    first (none: this is its first answer). O(e)."""
    if not earlier:
        name = first_name(display_name)
        hello = f"{greeting(now, zone)}, {name}." if name else f"{greeting(now, zone)}."
        return f"{hello} {FOUND}" if found else hello
    used = set(earlier)
    return next(
        (line for line, found_only in LEAD_INS if line not in used and (found or not found_only)), None
    )


def soft_line(closest: bool, missing: Sequence[str] = ()) -> str:
    """Decision 9's not-found line: with closest documents or without. #410: ``missing``, the
    question's words no document searched has, are named first ("No document searched has the
    word “SBH”."). O(m)."""
    if not missing:
        return SOFT_CLOSEST if closest else SOFT_NOTHING
    quoted = [f"“{word}”" for word in missing]
    words = f"word {quoted[0]}" if len(quoted) == 1 else f"words {', '.join(quoted[:-1])} or {quoted[-1]}"
    line = f"No document searched has the {words}."
    return f"{line} {SOFT_SOME}" if closest else line


def title(document_name: str, card: Mapping[str, Any] | None) -> str:
    """ "Invoice INV-2041", "Land record, Survey 45/2": the card's type and first identifier
    of a ``_TITLE_IDS`` kind, as printed; without one, the type and the document's name. The
    place, once land facts give one (package D), follows. O(card)."""
    card = card if isinstance(card, Mapping) else {}
    kind = _TYPES.get(str(card.get("doc_type")), "")
    name = clean_text(document_name)[:_MAX_PART].strip()
    found = {
        str(item.get("kind")): clean_text(str(item.get("value") or ""))[:_MAX_PART].strip()
        for item in reversed(_list(card.get("identifiers")))
    }
    for id_kind, prefix in _TITLE_IDS.items():
        if found.get(id_kind):
            identifier = f"{prefix}{found[id_kind]}"
            return (
                f"{kind} {identifier}" if kind == "Invoice" else ", ".join(filter(None, (kind, identifier)))
            )
    return ", ".join(filter(None, (kind, name))) or name


def why(
    question: str,
    card: Mapping[str, Any] | None,
    *,
    fields: Sequence[str] = (),
    words: Sequence[str] = (),
    dated: bool = False,
) -> str:
    """Why a document is listed, in a few words and no digit: the card's people the question
    names, with their role; the ``fields`` it gives as printed; the question's ``words`` it
    holds; its month when the question is ``dated``; its type when the question names it.
    "Owner Vrushit Patel, mentions lease and godown". "" when nothing can be said. O(card + q)."""
    card = card if isinstance(card, Mapping) else {}
    asked = {word.casefold() for word in _WORD.findall(question) if len(word) >= 3}
    parts: list[str] = []
    for party in _list(card.get("parties")):
        names = [clean_text(str(party.get(key) or ""))[:_MAX_PART] for key in ("name", "name_latin")]
        if any(asked & {w.casefold() for w in _WORD.findall(name)} for name in names):
            role = _ROLES.get(str(party.get("role")), "")
            parts.append(f"{role} {names[0]}".strip())
    parts += [f"{field.replace('_', ' ')} as printed" for field in dict.fromkeys(fields)]
    said = [word for word in dict.fromkeys(words) if word.casefold() in asked]
    if said:
        parts.append("mentions " + " and ".join(said[:3]))
    month = _month(card.get("document_date")) if dated else None
    if month:
        parts.append(f"dated {month}")
    kind = _TYPES.get(str(card.get("doc_type")), "")
    if kind and kind.split()[0].casefold() in asked:
        parts.insert(0, kind.casefold())
    text = ", ".join(part for part in parts if part and not any(ch.isdigit() for ch in part))
    return text[:1].upper() + text[1:]


def numbers(text: str) -> set[str]:
    """The numbers of ``text`` by value (digits of any script; "1,20,000" is 120000 and
    "12.50" is 12.5; "12.08.2026" is 12, 8 and 2026) and its fraction words, casefolded. O(n)."""
    found = {re.sub(r"[-\s]+", "-", match.casefold()) for match in _FRACTION.findall(text)}
    for token in _NUMBER.findall(ascii_digits(text)):
        plain = token.replace(",", "")
        for part in plain.split(".") if plain.count(".") > 1 else [plain]:
            try:
                found.add(format(Decimal(part).normalize(), "f"))
            except InvalidOperation:
                found.add(part)
    return found


def unsupported(text: str, allowed: Collection[str]) -> str | None:
    """Decision 2's final check: "fraction" or "number" when ``text`` holds a fraction word or
    a number not in ``allowed`` (``numbers`` of what backs it), else None. O(n)."""
    missing = numbers(text) - set(allowed)
    if not missing:
        return None
    return "number" if any(token[:1].isdigit() for token in missing) else "fraction"


def _month(dated: object) -> str | None:
    """The month name of a card's ``document_date`` ({"date": "2026-09-12"}), or None."""
    if not isinstance(dated, Mapping):
        return None
    try:
        return _MONTHS[date.fromisoformat(str(dated.get("date"))).month - 1]
    except ValueError:
        return None


def _list(value: object) -> list[Mapping[str, Any]]:
    return [item for item in value if isinstance(item, Mapping)] if isinstance(value, list) else []
