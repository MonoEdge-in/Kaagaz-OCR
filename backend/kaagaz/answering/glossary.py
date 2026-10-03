"""English document words and their Marathi and Hindi equivalents, for English
questions about Devanagari documents (council 2 Oct 2026, PR2 C and D).

``EQUIVALENTS`` maps an English keyword, as ``hinglish.normalise`` gives it,
to at most three NFC Devanagari words: code constants of Devanagari letters
and marks only (tests/test_answering_glossary.py checks it), so ``word_query``
can hand them to ``to_tsquery`` safely. A term of ``PREFIX_MIN`` code points
or more matches a word that starts with it (भोगवटादार finds भोगवटादाराचे,
``:*`` in SQL); a shorter one a whole word only (कुल is not in कुलकर्णी, कर
not in करार). Words are runs of letters and marks (L and M), not regex word
characters, which split a Devanagari word at its vowel sign. ``GENERIC`` words are on
nearly every form, so ``present`` is False for them; they are still searched.

Only hybrid.py's word list, the evidence gate and the extractive draft use
this module; ``normalise``, ``plan_fixed`` and the rewrite guard never do.
ponytail: a model translating the question is the upgrade, behind a flag,
once the cross-language eval exists. PROVISIONAL: Tushar's first list.

Time: ``equivalents`` O(1); ``present`` O(t) for t characters; ``word_query``
O(k) for k keywords.
"""

import unicodedata

PREFIX_MIN = 4  # code points: a shorter term matches whole words only

EQUIVALENTS: dict[str, tuple[str, ...]] = {
    # land records (7/12, 8A, mutation)
    "owner": ("भोगवटादार", "मालक", "खातेदार"),
    "owners": ("भोगवटादार", "मालक", "खातेदार"),
    "owns": ("भोगवटादार", "मालक"),
    "land": ("जमीन", "जमिन", "भूमि"),
    "area": ("क्षेत्र", "क्षेत्रफळ", "रकबा"),
    "survey": ("सर्वे", "सर्व्हे"),
    "gat": ("गट",),
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


def equivalents(keyword: str) -> tuple[str, ...]:
    """The Devanagari equivalents of one ``normalise`` keyword; () for a word not listed."""
    return EQUIVALENTS.get(keyword, ())


def present(keyword: str, text: str) -> bool:
    """True when an equivalent of ``keyword`` is in ``text`` (the module docstring's
    rule); always False for a GENERIC keyword or one not listed. O(t)."""
    terms = () if keyword in GENERIC else equivalents(keyword)
    if not terms:
        return False
    words = _words(unicodedata.normalize("NFC", text))
    return any(_matches(term, word) for term in terms for word in words)


def english_for(text: str) -> set[str]:
    """The English keys one of whose words is in ``text`` (GENERIC ones too), by the same
    match as ``present``: the English an answer may use for that text. O(t * g) for g words."""
    words = _words(unicodedata.normalize("NFC", text))
    return {key for key, terms in EQUIVALENTS.items() if any(_matches(t, w) for t in terms for w in words)}


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
