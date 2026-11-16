"""English document words and their Marathi and Hindi equivalents, for English
questions about Devanagari documents (council 2 Oct 2026, PR2 C and D).

``EQUIVALENTS`` maps an English keyword, as ``hinglish.normalise`` gives it,
to at most three NFC Devanagari words: code constants of Devanagari letters
and marks only (tests/test_answering_glossary.py checks it), so ``word_query``
can hand them to ``to_tsquery`` safely; a word printed two ways in Latin letters
also lists its other spelling, ASCII lower case letters only (gat and gut, #625).
A term of ``PREFIX_MIN`` code points or more matches a word that starts with it (भोगवटादार finds भोगवटादाराचे,
``:*`` in SQL); a shorter one a whole word only (कुल is not in कुलकर्णी, कर
not in करार). Words are runs of letters and marks (L and M), not regex word
characters, which split a Devanagari word at its vowel sign. ``GENERIC`` words are on
nearly every form, so ``present`` is False for them; they are still searched.

Only hybrid.py's word list, the evidence gate and the extractive draft use
this module; ``normalise``, ``plan_fixed`` and the rewrite guard never do.
ponytail: a model translating the question is the upgrade, behind a flag,
once the cross-language eval exists. PROVISIONAL: Tushar's first list.

Romanised Marathi and Hindi (council 3 Oct 2026, PR 2b-2, PROVISIONAL). People
type the words in English letters (malak, jamin, kshetra). ``ROMANISED`` maps the
sound key (kaagaz/chunking/script.py) of each spelling in ``_ROMANISED_SPELLINGS``
to an ``EQUIVALENTS`` key, so a typed word reuses that key's Devanagari words.
``concept`` looks a keyword up in this order: an English key, the exact sound
key, then the longest Romanised key of ``PREFIX_MIN`` letters or more that the
word's key starts with ("jaminicha" is jamin, land). Every Romanised key has 3
letters or more; kar ("do" in Hindi, "tax" in Marathi) and kul ("total" in
hinglish.py, "tenant" in Marathi) are never keys, nor the six known merges of
different words (varas and varsha are both vrs). A key another common word shares
(bki: bakaya and baki; bde: bhade and bade; brn: bharna and baran) is in
``MERGED_KEYS`` too, so only the listed spelling stands for its concept. Invariants
in tests/test_answering_romanised.py.

``ROMANISED_STOP`` (question words and fillers: ahe, kiti, madhe...) are left out
of the keywords by ``content_words`` too, never by ``normalise``.

``sounds_like``: a keyword matches a word of ``sound_words(text)`` (the text's
Latin spelling) when their sound keys are equal, or, for a word typed with 4
letters or more, one key starts with the other; a key under 3 letters, or one
of ``MERGED_KEYS``, needs the Latin spelling exactly. Never for a GENERIC word.
``strict`` (the answer check) allows equal keys only.

Time: ``equivalents`` and ``concept`` O(r) for r Romanised keys; ``present`` O(t)
for t characters; ``word_query`` O(k) for k keywords; ``sound_words`` O(t);
``sounds_like`` O(w) for w words.
"""

import unicodedata
from collections.abc import Sequence

from kaagaz.chunking.script import SOUND_KEY_MIN, latin_spelling, sound_key, sound_keys

PREFIX_MIN = 4  # code points: a shorter term matches whole words only

EQUIVALENTS: dict[str, tuple[str, ...]] = {
    # land records (7/12, 8A, mutation)
    "owner": ("भोगवटादार", "मालक", "खातेदार"),
    "owners": ("भोगवटादार", "मालक", "खातेदार"),
    "owns": ("भोगवटादार", "मालक"),
    "land": ("जमीन", "जमिन", "भूमि"),
    "area": ("क्षेत्र", "क्षेत्रफळ", "रकबा"),
    "survey": ("सर्वे", "सर्व्हे"),
    # #625: land records print the survey number both ways in Latin letters (गट), so each finds the other.
    "gat": ("गट", "gut"),
    "gut": ("गट", "gat"),
    "crop": ("पीक", "पिके", "फसल"),
    "tenant": ("कूळ", "किरायेदार"),
    "mutation": ("फेरफार",),
    "assessment": ("आकारणी",),
    "revenue": ("महसूल", "राजस्व"),
    "farmer": ("शेतकरी", "किसान"),
    "hectare": ("हेक्टर",),
    "acre": ("एकर",),
    "village": ("गाव", "गांव", "ग्राम"),
    "taluka": ("तालुका", "तहसील"),
    "district": ("जिल्हा", "जिला"),
    # people and forms
    "name": ("नाव", "नाम"),
    "father": ("वडील", "पिता"),
    "address": ("पत्ता",),
    "number": ("क्रमांक", "नंबर"),
    "date": ("दिनांक", "तारीख"),
    # invoices and receipts
    "invoice": ("बीजक", "देयक", "बिल"),
    "invoices": ("बीजक", "देयक", "बिल"),
    "receipt": ("पावती", "रसीद"),
    "tax": ("कर", "जीएसटी"),
    "total": ("एकूण", "कुल", "योग"),
    "amount": ("रक्कम", "राशि", "रकम"),
    "price": ("किंमत", "कीमत"),
    "rate": ("दर",),
    "quantity": ("नग", "मात्रा"),
    "seller": ("विक्रेता",),
    "buyer": ("खरेदीदार", "खरीदार", "क्रेता"),
    "customer": ("ग्राहक",),
    "payment": ("भरणा", "भुगतान"),
    "sales": ("विक्री", "बिक्री"),
    "profit": ("नफा", "लाभ", "मुनाफा"),
    "expense": ("खर्च",),
    "rent": ("भाडे", "किराया"),
    "salary": ("पगार", "वेतन"),
    # banks and loans
    "bank": ("बँक", "बैंक"),
    "account": ("खाते", "खाता"),
    "balance": ("शिल्लक", "शेष"),
    "loan": ("कर्ज", "ऋण"),
    "interest": ("व्याज", "ब्याज"),
    "deposit": ("ठेव", "जमा"),
    "due": ("थकबाकी", "बकाया"),
}

# On nearly every form: finding an equivalent of one says nothing (the module docstring).
GENERIC = frozenset({"name", "number", "date", "total", "village", "taluka", "district", "amount"})

# Marathi and Hindi question words and fillers typed in English letters (council 3 Oct 2026,
# PR 2b, PROVISIONAL): never a sound term in search. "pan" ("but") is not one, as a PAN is
# asked for in English; "kar" is, as in a question it is mostly Hindi "do" ("check kar").
ROMANISED_STOP = frozenset(
    {"ahe", "aahe", "ahet", "kay", "kiti", "kon", "kuthe", "kevha", "kasa", "cha", "chi", "che", "chya"}
    | {"la", "na", "ne", "var", "madhe", "madhye", "ani", "hota", "hoti", "hote", "sanga", "dakhva"}
    | {"kaha", "kab", "kaise", "wala", "wali", "kar", "karo"}
)


# Romanised spelling -> EQUIVALENTS key (the module docstring). The ML reviewer's list,
# less the words whose keys are under 3 letters (gav, pik, nafa, jama, boja), in
# MERGED_KEYS (varas, rasid), shared with another concept (baki: balance or due) or an
# English word (shillak is silk), or that would need a new English key (satbara, hapta).
_ROMANISED_SPELLINGS: dict[str, str] = {
    # land records
    "malak": "owner",
    "malik": "owner",
    "bhogvatdar": "owner",
    "khatedar": "owner",
    "jamin": "land",
    "bhumi": "land",
    "kshetra": "area",
    "kshetrafal": "area",
    "rakba": "area",
    "ferfar": "mutation",
    "akarni": "assessment",
    "fasal": "crop",
    "shetkari": "farmer",
    "kisan": "farmer",
    "hektar": "hectare",
    "ekar": "acre",
    "tahsil": "taluka",
    "jilha": "district",
    "zilla": "district",
    # invoices
    "bijak": "invoice",
    "bil": "invoice",
    "pavti": "receipt",
    "kimmat": "price",
    "rakkam": "amount",
    "ekun": "total",
    "kharedidar": "buyer",
    "kharidar": "buyer",
    "vikreta": "seller",
    "grahak": "customer",
    "vikri": "sales",
    "bikri": "sales",
    "kharch": "expense",
    "bhade": "rent",
    "kiraya": "rent",
    "pagar": "salary",
    "vetan": "salary",
    # banks
    "khate": "account",
    "vyaj": "interest",
    "byaj": "interest",
    "thev": "deposit",
    "thakbaki": "due",
    "bakaya": "due",
    "bharna": "payment",
    "bhugtan": "payment",
    "karj": "loan",
    "rin": "loan",
}
ROMANISED: dict[str, str] = {sound_key(spelling): key for spelling, key in _ROMANISED_SPELLINGS.items()}
_BY_LENGTH = sorted((key for key in ROMANISED if len(key) >= PREFIX_MIN), key=len, reverse=True)
# Keys of different words: the six known merges (kaagaz/chunking/script.py), and three a
# Romanised key shares with another word (baki and bakaya, bade and bhade, baran and bharna).
# Each needs its exact spelling, in concept and in sounds_like.
MERGED_KEYS = frozenset({"bti", "hti", "sti", "bor", "vrs", "rsid", "bki", "bde", "brn"})


def content_words(keywords: Sequence[str]) -> list[str]:
    """``keywords`` without the Romanised stop words; ``normalise`` itself never changes. O(k)."""
    return [keyword for keyword in keywords if keyword not in ROMANISED_STOP]


def concept(keyword: str) -> str | None:
    """The EQUIVALENTS key a keyword stands for (the module docstring's order); None. O(r)."""
    if keyword in EQUIVALENTS:
        return keyword
    key = sound_key(keyword)
    if key in ROMANISED and (key not in MERGED_KEYS or keyword in _ROMANISED_SPELLINGS):
        return ROMANISED[key]
    return next((ROMANISED[start] for start in _BY_LENGTH if key.startswith(start)), None)


def is_romanised(keyword: str) -> bool:
    """True for a keyword that stands for an English key without being one (malak). O(r)."""
    return keyword not in EQUIVALENTS and concept(keyword) is not None


def equivalents(keyword: str) -> tuple[str, ...]:
    """The Devanagari equivalents of one ``normalise`` keyword; () for a word not listed."""
    key = concept(keyword)
    return EQUIVALENTS.get(key, ()) if key else ()


def present(keyword: str, text: str) -> bool:
    """True when an equivalent of ``keyword`` is in ``text`` (the module docstring's
    rule); always False for a GENERIC keyword or one not listed. O(t)."""
    terms = () if concept(keyword) in GENERIC else equivalents(keyword)
    if not terms:
        return False
    words = _words(unicodedata.normalize("NFC", text).casefold())  # a Latin term (gut) in any case
    return any(_matches(term, word) for term in terms for word in words)


def english_for(text: str) -> set[str]:
    """The English keys one of whose words is in ``text`` (GENERIC ones too), by the same
    match as ``present``: the English an answer may use for that text. O(t * g) for g words."""
    words = _words(unicodedata.normalize("NFC", text).casefold())
    return {key for key, terms in EQUIVALENTS.items() if any(_matches(t, w) for t in terms for w in words)}


def sound_words(text: str) -> list[tuple[str, str]]:
    """(word, sound key) of each word of ``text``'s Latin spelling. O(t)."""
    return sound_keys(latin_spelling(text))


def sounds_like(keyword: str, words: Sequence[tuple[str, str]], *, strict: bool = False) -> bool:
    """True when ``keyword`` sounds like one of ``words`` (``sound_words``; the module
    docstring's rule). ``strict``: equal keys only, no prefix (the answer check: Ramchandran
    is not रामचंद्र). Never for a word with a digit or a GENERIC word. O(w)."""
    if any(ch.isdigit() for ch in keyword) or concept(keyword) in GENERIC:
        return False
    key, typed = sound_key(keyword), keyword.casefold()
    for word, other in words:
        if min(len(key), len(other)) < SOUND_KEY_MIN or {key, other} & MERGED_KEYS:
            if typed == word.casefold():
                return True
        else:
            prefix = (
                not strict and len(typed) >= PREFIX_MIN and (other.startswith(key) or key.startswith(other))
            )
            if key == other or prefix:
                return True
    return False


def word_query(keywords: list[str]) -> str:
    """A ``to_tsquery('simple', ...)`` text ORing every equivalent of ``keywords`` once,
    with ``:*`` on terms of PREFIX_MIN code points or more; "" when none is listed."""
    terms = dict.fromkeys(term for keyword in keywords for term in equivalents(keyword))
    return " | ".join(f"{term}:*" if len(term) >= PREFIX_MIN else term for term in terms)


def _matches(term: str, word: str) -> bool:
    return word.startswith(term) if len(term) >= PREFIX_MIN else word == term


def _words(text: str) -> set[str]:
    """The runs of letters and marks (categories L and M) in ``text``. O(t)."""
    words: set[str] = set()
    current: list[str] = []
    for ch in text:
        if unicodedata.category(ch)[0] in "LM":
            current.append(ch)
        elif current:
            words.add("".join(current))
            current = []
    if current:
        words.add("".join(current))
    return words
