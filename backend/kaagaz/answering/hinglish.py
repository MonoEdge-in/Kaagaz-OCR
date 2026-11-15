"""Understand a Hinglish question: Hindi typed in English letters.

"Kiska profit sabse jyada tha last month?" becomes "whose profit highest last
month". A fixed word map, never a model, so the same question always means
the same thing and Tushar can read every rule here.

Steps, in order:

0. Devanagari digits become ASCII digits (``fold_digits``), and a question with
   Devanagari letters is spelled in Latin letters first (``latin_spelling``,
   PRD 3 W3), so "पाटील का बिल" reads as "patil ka bil" and is not dropped.
1. Lower case, then split into words (letters and digits; a hyphen, slash or
   dot inside a word keeps it whole, so "INV-1042" stays one word).
2. Replace the longest known phrase first (three words, then two, then one),
   so "sabse jyada" is "highest", not "most highest", and "kitne bill" is
   "count invoices", not "how much invoice".
3. Drop Hindi filler words ("tha", "hai", "ka"...).

``keywords`` are the words that say what the question is about: the English
text without question words, filler and date words ("last month" is a date,
not a topic; ``dates.py`` reads it). ``hinglish`` is True when any Hindi word
was understood, so the chat can show "Hinglish understood as: ...".

PROVISIONAL: the Hinglish rules are Vrushit's (docs/architecture.md).

Time is O(n) in the number of words: at most three dictionary lookups each.
"""

import re
from dataclasses import dataclass

from kaagaz.chunking.script import fold_digits, has_devanagari_letters, latin_spelling

# Hindi phrase -> English. Longest phrases are tried first.
_HINDI_PHRASES: dict[tuple[str, ...], str] = {
    ("sab", "se", "jyada"): "highest",
    ("sab", "se", "zyada"): "highest",
    ("sab", "se", "kam"): "lowest",
    ("sabse", "jyada"): "highest",
    ("sabse", "zyada"): "highest",
    ("sabse", "zyaada"): "highest",
    ("sabse", "jada"): "highest",
    ("sabse", "adhik"): "highest",
    ("sabse", "kam"): "lowest",
    ("sab", "se"): "most",
    ("kitne", "bill"): "count invoices",
    ("kitne", "bil"): "count invoices",
    ("is", "mahine"): "this month",
    ("is", "mahina"): "this month",
    ("is", "saal"): "this year",
    ("is", "varsh"): "this year",
    ("last", "mahine"): "last month",
    ("last", "mahina"): "last month",
}

_HINDI_WORDS: dict[str, str] = {
    "kiska": "whose",
    "kiski": "whose",
    "kiske": "whose",
    "kaun": "who",
    "kon": "who",
    "kisne": "who",
    "sabse": "most",
    "jyada": "highest",
    "zyada": "highest",
    "jada": "highest",
    "adhik": "highest",
    "zyaada": "highest",
    "kam": "lowest",
    "kitna": "how much",
    "kitne": "how much",
    "kitni": "how much",
    "kul": "total",
    "jod": "total",
    "pichle": "last",
    "pichhle": "last",
    "pichla": "last",
    "pichli": "last",
    "mahine": "month",
    "mahina": "month",
    "saal": "year",
    "varsh": "year",
    "din": "days",
    "bil": "invoice",
    "rakam": "amount",
    "paisa": "amount",
    "rashi": "amount",
    "munafa": "profit",
    "labh": "profit",
    "fayda": "profit",
    "bikri": "sales",
    "becha": "sales",
    "kharcha": "expense",
    "kharch": "expense",
}

# English words the map also normalises (they do not make a question Hinglish).
_ENGLISH_WORDS: dict[str, str] = {
    "bill": "invoice",
    "bills": "invoices",
    "sale": "sales",
    "expenses": "expense",
}

# Hindi filler, dropped. "the" and "me" are here because they are Hindi words too
# ("were", "in"); in English questions they carry no meaning either.
_HINDI_STOP = frozenset(
    {"tha", "thi", "hai", "hain", "ka", "ki", "ke", "mein", "se", "ko", "kya", "batao", "bataiye"}
    | {"dikhao", "hua", "hui", "hue"}
)
_SHARED_STOP = frozenset({"the", "me"})

# Not topics: question words and English filler. The last line (#622, production 15 Nov 2026):
# request words ("Can you find me ... in any other document", "which document have ... name"),
# which matched most pieces by sound and named "Can" and "find" in the not-found line.
_NOT_KEYWORDS = frozenset(
    {"a", "an", "is", "was", "were", "are", "be", "been", "of", "in", "on", "at", "for", "to"}
    | {"from", "by", "and", "or", "with", "what", "which", "who", "whose", "whom", "when", "where"}
    | {"how", "much", "many", "my", "our", "us", "we", "i", "you", "show", "tell", "give", "list"}
    | {"please", "did", "do", "does", "has", "have", "had", "there", "that", "it", "its", "any"}
    | {"can", "find", "seen", "if", "other", "document", "documents", "name"}
)

# Date words (dates.py reads them). A year and the number in "last 30 days" are
# date words too; see _date_positions.
DATE_WORDS = frozenset(
    {"last", "this", "month", "months", "year", "years", "day", "days", "week", "ago"}
    | {"january", "february", "march", "april", "may", "june", "july", "august", "september"}
    | {"october", "november", "december", "jan", "feb", "mar", "apr", "jun", "jul", "aug"}
    | {"sep", "sept", "oct", "nov", "dec"}
)

# Words that are never a topic, for other modules: Hindi filler, the shared stop words and
# the question and English filler words (kaagaz/search/hybrid.py keys none of them by sound).
STOP_WORDS = _HINDI_STOP | _SHARED_STOP | _NOT_KEYWORDS

_WORD = re.compile(r"[a-z0-9]+(?:[-/.][a-z0-9]+)*")
_YEAR = re.compile(r"(?:19|20)[0-9]{2}")
_LONGEST_PHRASE = max(len(phrase) for phrase in _HINDI_PHRASES)


@dataclass(frozen=True, slots=True)
class NormalisedQuestion:
    english: str
    keywords: list[str]
    hinglish: bool

    @property
    def words(self) -> list[str]:
        return self.english.split()


def normalise(question: str) -> NormalisedQuestion:
    """The question in plain English words, its topic words, and whether it was Hinglish."""
    question = fold_digits(question)
    if has_devanagari_letters(question):
        question = latin_spelling(question)
    words = _WORD.findall(question.casefold())
    out: list[str] = []
    hinglish = False
    i = 0
    while i < len(words):
        replaced = False
        for size in range(_LONGEST_PHRASE, 1, -1):
            phrase = tuple(words[i : i + size])
            if len(phrase) == size and phrase in _HINDI_PHRASES:
                out.extend(_HINDI_PHRASES[phrase].split())
                hinglish = True
                i += size
                replaced = True
                break
        if replaced:
            continue
        word = words[i]
        i += 1
        if word in _HINDI_STOP:
            hinglish = True
        elif word in _SHARED_STOP:
            pass
        elif word in _HINDI_WORDS:
            out.extend(_HINDI_WORDS[word].split())
            hinglish = True
        else:
            out.extend(_ENGLISH_WORDS.get(word, word).split())
    dates = _date_positions(out)
    keywords: list[str] = []
    for position, word in enumerate(out):
        if position in dates or word in _NOT_KEYWORDS or word in keywords:
            continue
        keywords.append(word)
    return NormalisedQuestion(english=" ".join(out), keywords=keywords, hinglish=hinglish)


def _date_positions(words: list[str]) -> set[int]:
    """Positions of date words: month and period words, years, and N in "last N days"."""
    positions: set[int] = set()
    for position, word in enumerate(words):
        next_word = words[position + 1] if position + 1 < len(words) else ""
        days_count = word.isdigit() and next_word in {"days", "day"}
        if word in DATE_WORDS or _YEAR.fullmatch(word) or days_count:
            positions.add(position)
    return positions
