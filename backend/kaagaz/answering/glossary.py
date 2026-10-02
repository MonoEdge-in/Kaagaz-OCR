"""English document words and their Marathi and Hindi equivalents, for English
questions about Devanagari documents (council 2 Oct 2026, PR2 C and D).

``EQUIVALENTS`` maps an English keyword, written as ``hinglish.normalise``
gives it (lower case; "bill" is already "invoice"), to at most three NFC
Devanagari words. They are code constants, never user text: each one is
Devanagari letters and marks only (no digit, danda, space, quote or tsquery
operator; tests/test_answering_glossary.py checks this), so ``word_query`` can
hand them to ``to_tsquery`` safely.

How a term matches (``present`` and ``word_query`` agree):

* a term of ``PREFIX_MIN`` code points or more matches a word that starts
  with it, so भोगवटादार finds भोगवटादाराचे (``:*`` in the word list);
* a shorter term matches a whole word only, so कुल is not found in कुलकर्णी
  and कर not in करार.

Words are runs of letters and marks (categories L and M), not ``\\w``: a
Devanagari vowel sign is a mark, and ``\\w`` would split a word at it.

``GENERIC`` words are on nearly every form (name, number, date, total,
village...), so finding their equivalent says nothing about the question:
``present`` is False for them. They are still searched for.

Only the search word list (kaagaz/search/hybrid.py), the evidence gate and the
extractive draft use this module. ``normalise``, ``plan_fixed`` and the
rewrite guard never do, so their output does not change.

ponytail: a curated list; a model translating the question is the upgrade,
behind a flag, once the cross-language eval exists. PROVISIONAL: the words are
Tushar's first list, for Vrushit to review.

Time: ``equivalents`` is O(1); ``present`` is O(t) for t characters of text;
``word_query`` is O(k) for k keywords.
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
