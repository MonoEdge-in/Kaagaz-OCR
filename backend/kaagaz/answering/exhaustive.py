"""Questions that ask how many, or which, documents hold a word (Tushar, 16 Oct 2026).

"How many documents mention Jalna?", "Which files contain the word Jalna?", "List all
documents with Jalna road", "Kitne documents me Jalna hai?", "Jalna kiti documents madhe
ahe?": the search route sees only its top hits, so it under-counts these. They are
answered from every document in scope instead, by one fixed query.

**The question** (``exhaustive_term``, a fixed word list, never a model): it must have an
asking word (how many, which, list, all, count, kitne, kaun se, kiti, konte...), a
document word (document, file, kagad...) and a holding word (contain, mention, have, with,
word, me, madhe, hai, ahe...). Every other word, after the stop words, must be one run of
one to four words: the word or phrase asked for. A word or phrase in quotes is taken as
asked, and then nothing else may be left over. No route when a word is left elsewhere
("which documents have invoice number 1042"), the question names a date, or it asks for a
highest, lowest or total, a "not" or "without", or (in Devanagari) a "what".

The same in Devanagari (16 Oct 2026): "कितने दस्तावेज़ों में जालना है?", "जालना किती
कागदपत्रांमध्ये आहे?". Words are split at spaces (``spaced_words``: a word keeps its vowel
signs and virama), compared without the nukta or the joiners (``_fold``: दस्तावेज़ and
दस्तावेज are one word), and Devanagari digits read as ASCII digits. The word asked for is
kept as typed; the answer's wording stays English, as for every question.

**The count** (``PgExhaustive``): a document counts when one of its saved pieces holds the
phrase as whole words, upper or lower case, ``pieces.tsv_simple @@
phraseto_tsquery('simple', term)``: the word index search already keeps (text and its Latin
spelling, no stemming, any script), inside ``db.customer`` and the scope, current, ready
documents out of Trash only. The first ``MAX_LISTED`` documents by name are listed with
the pages the phrase is on, each citing its first matching piece; the rest are counted.

A phrase with Devanagari letters is also matched by its Latin spelling (``latin_term``, the
``latin_spelling`` recipe ``text_latin`` is stored with): जालना finds जालना and a Latin text
spelling "jalana". It does not find "Jalna": that recipe keeps every middle vowel, and only
the fuzzy sound keys (``tsv_sound``) join the two, which merge other words (Jalan), so an
exact count never uses them. Nor is a Latin phrase spelled in Devanagari: no helper does
that. So जालना and Jalna count the documents written each way.

**A type** (17 Oct 2026, ``exhaustive_query``): "how many land documents of Jalna i have":
the words before a document word followed by of, from, in, for or about name the document
type (``doc_types``: land is land_record), counted inside it; a word no type knows is left
out. "How many documents of Jalna" routes with no holding word.

**Also mentions** (17 Oct 2026, ``key_terms``, ``PgExhaustive.mentions``): under any other
answer, the documents not shown that hold the question's key terms, by the same word index.

Time: two statements over the word index; then O(p * t) for the p matching pieces of the
listed documents, of t characters.
"""

import re
import unicodedata
from collections.abc import Sequence
from itertools import pairwise
from typing import Any, Protocol, get_args

from sqlalchemy import text

from kaagaz.answering import glossary
from kaagaz.answering.dates import mentions_date
from kaagaz.answering.evidence import Hit
from kaagaz.answering.format import indian_number
from kaagaz.answering.hinglish import DATE_WORDS, STOP_WORDS, normalise
from kaagaz.answering.sanitize import clean_text
from kaagaz.chunking.script import fold_digits, has_devanagari_letters, latin_spelling, spaced_words
from kaagaz.db.engine import Database
from kaagaz.search.scope import DocType, Scope

MAX_LISTED = 50  # documents named in one answer; the rest are counted
MAX_TERM_WORDS = 4
MAX_TERM_CHARS = 100
MAX_PAGES_NAMED = 6  # a document on more pages says how many

_NUKTA = "़"


def _fold(word: str) -> str:
    """``word`` as the word lists compare it: lower case, without the nukta (ज़ is ज, फ़ is
    फ, typed as one code point or two) or the joiners (ZWJ, ZWNJ). O(n)."""
    bare = unicodedata.normalize("NFD", word.casefold()).replace(_NUKTA, "")
    return unicodedata.normalize("NFC", bare.replace("‌", "").replace("‍", ""))


def _words(*groups: set[str]) -> frozenset[str]:
    return frozenset(_fold(word) for group in groups for word in group)


# Each group in English, Hinglish and Romanised Marathi, then in Devanagari: Hindi, then
# Marathi. Marathi joins "in" to its noun (कागदपत्रांमध्ये, "in the papers"): such a word is
# a document word and a holding word.
_JOINED = {"कागदपत्रांमध्ये", "कागदपत्रात", "दस्तऐवजांमध्ये", "दस्तऐवजात", "फायलींमध्ये", "फाइल्समध्ये"}
_ASK = _words(
    {"which", "list", "all", "every", "count", "number"},
    {"kitne", "kitni", "kitna", "kaun", "kaunse", "kaunsa", "kaunsi", "kon", "konse", "konsa", "konsi"},
    {"kis", "kin", "kiti", "konte", "kontya", "konti", "konta", "sab", "sabhi", "saare", "sare"},
    {"sagle", "saglya", "sarv", "sarva"},
    {"कितने", "कितनी", "कितना", "कौन", "कौनसे", "कौनसा", "कौनसी", "कौन-से", "कौन-सा", "कौन-सी"},
    {"किन", "किस", "सभी", "सारे", "सब", "सूची", "लिस्ट", "गिनती", "गिनो", "गिनिए"},
    {"किती", "कोणते", "कोणती", "कोणत्या", "कोणता", "सर्व", "सगळे", "सगळ्या", "यादी"},
)
_THINGS = _words(
    {"document", "documents", "doc", "docs", "file", "files", "paper", "papers"},
    {"kagaz", "kaagaz", "kagad", "kagadpatra", "kagadpatre", "dastavej", "dastavez"},
    {"दस्तावेज़", "दस्तावेज़ों", "कागज़", "कागज़ों", "कागज़ात", "फ़ाइल", "फ़ाइलें", "फ़ाइलों"},
    {"फाईल", "फाईलें", "फाईलों", "डॉक्युमेंट", "डॉक्यूमेंट", "डॉक्युमेंट्स", "डॉक्यूमेंट्स"},
    {"कागद", "कागदपत्र", "कागदपत्रे", "कागदपत्रां", "दस्तऐवज", "दस्तऐवजां", "फाइल्स", "फायली"},
    _JOINED,
)
_HOLDS = _words(
    {"contain", "contains", "containing", "contained", "mention", "mentions", "mentioning"},
    {"mentioned", "have", "has", "having", "with", "include", "includes", "including"},
    {"appear", "appears", "appearing", "word", "words", "name", "named", "term", "phrase"},
    {"say", "says", "saying", "me", "mein", "madhe", "madhye", "hai", "hain", "ahe", "aahe"},
    {"ahet", "likha", "lihilay", "lihile"},
    {"में", "मे", "है", "हैं", "लिखा", "लिखी", "लिखे", "शामिल", "आता", "आती", "आते", "नाम"},
    {"ज़िक्र", "उल्लेख", "जिनमें", "जिसमें", "वाले", "वाली", "वाला", "शब्द"},
    {"आहे", "आहेत", "मध्ये", "मधे", "मधी", "असलेले", "असलेल्या", "असलेली", "लिहिले", "लिहिलेले"},
    {"लिहिलंय", "नाव", "ज्यात", "ज्यामध्ये", "ज्यांमध्ये"},
    _JOINED,
)
# A highest, lowest or total; a "not" or "without" (they ask for the other documents); a
# Devanagari "what" (क्या, काय: "सभी दस्तावेज़ों में रकम क्या है" asks what the amount is).
_NOT_ROUTED = _words(
    {"highest", "lowest", "most", "least", "maximum", "minimum", "max", "min", "largest"},
    {"smallest", "biggest", "top", "total", "sum", "sabse", "jyada", "zyada", "kul"},
    {"not", "without", "except", "nahi", "nahin", "nai"},
    {"सबसे", "ज़्यादा", "अधिक", "अधिकतम", "न्यूनतम", "कम", "कुल", "जोड़", "योग"},
    {"सर्वात", "सर्वाधिक", "जास्त", "कमी", "एकूण", "बेरीज"},
    {"नहीं", "नही", "बिना", "बगैर", "नाही", "शिवाय", "नसलेले", "नसलेल्या", "क्या", "काय"},
)
_FILLER = _words(
    {"how", "many", "the", "a", "an", "of", "do", "does", "are", "is", "there", "about"},
    {"का", "की", "के", "को", "से", "ने", "पर", "और", "या", "कि", "जो", "जिन", "था", "थी", "थे"},
    {"बताओ", "बताइए", "बताइये", "दिखाओ", "दिखाइए", "हुआ", "हुई", "हुए"},
    {"चा", "ची", "चे", "च्या", "ला", "ना", "वर", "आणि", "कोण", "सांगा", "दाखवा", "असा", "असे"},
    {"होता", "होती", "होते"},
)
_FRAME = _ASK | _THINGS | _HOLDS | _FILLER | STOP_WORDS | glossary.ROMANISED_STOP
# A phrase in straight or curly quotes, opened after a space and closed before one.
_QUOTED = re.compile(
    r"""(?:^|(?<=\s))["'\u201c\u2018]([^"'\u201c\u2018\u201d\u2019]{1,100})["'\u201d\u2019](?=\s|[?.!,]|$)"""
)


def exhaustive_term(question: str) -> str | None:
    """The word or phrase a how-many or which-documents question asks for, as typed; None
    for any other question (the module docstring). O(n)."""
    asked = exhaustive_query(question)
    return None if asked is None else asked[0]


# "how many land documents of Jalna": a document word then one of these holds the phrase
# after it, and the words before the document word name its type (Tushar, 17 Oct 2026).
_PREPS = _words({"of", "from", "in", "for", "about"})
_MAX_TYPE_WORDS = 3
_DOC_TYPES = [kind for kind in get_args(DocType) if kind != "other"]


def doc_types(words: Sequence[str]) -> tuple[str, ...]:
    """The ``documents.doc_type`` values the words name: a word (as ``normalise`` reads it,
    so "bill" is invoice; without a plural "s") that is part of a type's name (land is
    land_record, statement is bank_statement). None known: empty. O(w)."""
    found: list[str] = []
    for word in words:
        for english in (normalise(word).english or _fold(word)).split():
            stem = english.removesuffix("s")
            found += [kind for kind in _DOC_TYPES if stem in kind.split("_") and kind not in found]
    return tuple(found)


def _thing_of(folded: Sequence[str]) -> int | None:
    """Where a document word stands just before "of", "from", "in", "for" or "about". O(n)."""
    return next((i for i in range(len(folded) - 1) if folded[i] in _THINGS and folded[i + 1] in _PREPS), None)


def exhaustive_query(question: str) -> tuple[str, tuple[str, ...]] | None:
    """``exhaustive_term``'s phrase and the document types the question names before a
    document word followed by "of" ("how many land documents of Jalna i have": land_record,
    ``doc_types``; a type word known to none is left out and the phrase counted alone), or
    no type; None for any other question. With that "documents of" shape no holding word
    is needed ("how many documents of Jalna"). O(n)."""
    question = fold_digits(question)
    quoted = _QUOTED.search(question)
    outside = question if quoted is None else question[: quoted.start()] + " " + question[quoted.end() :]
    words = spaced_words(outside)
    folded = [_fold(word) for word in words]
    asked = bool(_ASK.intersection(folded)) or ("how", "many") in pairwise(folded)
    at = _thing_of(folded)
    if not asked or not _THINGS.intersection(folded) or not (_HOLDS.intersection(folded) or at is not None):
        return None
    if _NOT_ROUTED.intersection(folded):
        return None
    left = [i for i, word in enumerate(folded) if word not in _FRAME]
    if quoted is not None:
        term = " ".join(spaced_words(quoted[1]))
        return (term, ()) if term and not left and len(term) <= MAX_TERM_CHARS else None
    kinds: tuple[str, ...] = ()
    if at is not None:
        before = [i for i in left if i < at]
        if before and len(before) <= _MAX_TYPE_WORDS and before == list(range(at - len(before), at)):
            kinds, left = doc_types([words[i] for i in before]), [i for i in left if i > at]
    if not left or left != list(range(left[0], left[-1] + 1)) or len(left) > MAX_TERM_WORDS:
        return None
    if mentions_date(normalise(" ".join(folded))):
        return None
    term = " ".join(words[i] for i in left)
    return (term, kinds) if len(term) <= MAX_TERM_CHARS else None


# #617 item 5: "which" and "list" words (not "all" or "number", which also ask about one value).
_WHICH = _words(
    {"which", "list"},
    {"kaun", "kaunse", "kaunsa", "kaunsi", "kon", "konse", "konsa", "konsi", "kis", "kin"},
    {"konte", "kontya", "konti", "konta"},
    {"कौन", "कौनसे", "कौनसा", "कौनसी", "कौन-से", "कौन-सा", "कौन-सी", "किन", "किस", "सूची", "लिस्ट"},
    {"कोणते", "कोणती", "कोणत्या", "कोणता", "यादी"},
)


def asks_documents(question: str) -> bool:
    """Whether the question asks which (or how many) documents: a "which" or "list" word, or "how
    many", and a document word, in any script ("which document have krishna jindal name",
    "कोणत्या कागदपत्रात गट ६१ आहे"). The search then answers from documents, each once
    (#617 item 5). O(n)."""
    folded = [_fold(word) for word in spaced_words(fold_digits(question))]
    asked = bool(_WHICH.intersection(folded)) or ("how", "many") in pairwise(folded)
    return asked and bool(_THINGS.intersection(folded))


def latin_term(term: str) -> str:
    """The phrase's Latin spelling (``latin_spelling``, the recipe ``pieces.text_latin`` is
    stored with) when it has Devanagari letters, so जालना also finds a Latin text that spells
    it "jalana"; else the phrase itself. O(n)."""
    return latin_spelling(term) if has_devanagari_letters(term) else term


class Exhaustive(Protocol):
    def documents_with(
        self, customer_id: str, term: str, scope: Scope, listed: int
    ) -> tuple[int, list[list[Hit]]]:
        """How many documents in ``scope`` hold ``term`` as whole words, and the matching
        pieces of the first ``listed`` of them by name, document by document, each in
        reading order."""
        ...


class Mentions(Protocol):
    def mentions(
        self, customer_id: str, terms: Sequence[str], scope: Scope, exclude: Sequence[str], listed: int
    ) -> tuple[int, list[dict[str, Any]]]:
        """How many documents in ``scope``, but ``exclude``, hold any of ``terms`` as whole
        words, and the first ``listed`` of them, most matching pieces first, then by name:
        ``mention_row``'s shape."""
        ...


# The pieces of the scope's current, ready documents out of Trash (hybrid.py's scope predicates).
_IN_SCOPE = (
    " FROM pieces p JOIN documents d ON d.customer_id = p.customer_id AND d.id = p.document_id"
    " WHERE p.customer_id = :c AND d.deleted_at IS NULL AND d.is_current AND d.status = 'ready'"
    " AND (CAST(:s_folders AS uuid[]) IS NULL OR d.folder_id = ANY(CAST(:s_folders AS uuid[])))"
    " AND (CAST(:s_tags AS uuid[]) IS NULL OR d.id IN (SELECT dt.document_id FROM document_tags dt"
    " WHERE dt.customer_id = :c AND dt.tag_id = ANY(CAST(:s_tags AS uuid[])) GROUP BY dt.document_id"
    " HAVING count(DISTINCT dt.tag_id) = cardinality(CAST(:s_tags AS uuid[]))))"
    " AND (CAST(:s_doc_types AS text[]) IS NULL OR d.doc_type = ANY(CAST(:s_doc_types AS text[])))"
    " AND (CAST(:s_detected_type AS text) IS NULL OR d.detected_type = CAST(:s_detected_type AS text))"
    " AND (CAST(:s_documents AS uuid[]) IS NULL OR p.document_id = ANY(CAST(:s_documents AS uuid[])))"
    " AND (CAST(:s_from AS date) IS NULL AND CAST(:s_to AS date) IS NULL"
    " OR coalesce(d.document_date, (d.created_at AT TIME ZONE CAST(:s_tz AS text))::date)"
    " BETWEEN coalesce(CAST(:s_from AS date), '-infinity') AND coalesce(CAST(:s_to AS date), 'infinity'))"
)
# Of the scope's documents that hold the phrase, the first :n by name (then id), and how many
# in all: the window counts the groups before LIMIT.
_DOCUMENTS = text(
    "SELECT p.document_id, count(*) OVER () AS total"
    + _IN_SCOPE
    + " AND p.tsv_simple @@ (phraseto_tsquery('simple', :w) || phraseto_tsquery('simple', :l))"
    " GROUP BY p.document_id, d.display_name"
    " ORDER BY lower(d.display_name), p.document_id LIMIT :n"
)
# "Also mentions" (Tushar, 17 Oct 2026): the scope's documents but :exclude holding any of up
# to three phrases (each also by its Latin spelling), most matching pieces first, then by
# name; each with its pages and its first matching piece's page and text; and how many in all.
_MENTIONS = text(
    "SELECT g.document_id, g.display_name, g.pages, f.page_from, f.text, g.total FROM ("  # noqa: S608 - constants
    "SELECT p.document_id, d.display_name, count(*) AS hits, min(p.seq) AS first_seq,"
    " array_agg(DISTINCT p.page_from ORDER BY p.page_from) AS pages, count(*) OVER () AS total"
    + _IN_SCOPE
    + " AND p.tsv_simple @@ (phraseto_tsquery('simple', :w1) || phraseto_tsquery('simple', :l1)"
    " || phraseto_tsquery('simple', :w2) || phraseto_tsquery('simple', :l2)"
    " || phraseto_tsquery('simple', :w3) || phraseto_tsquery('simple', :l3))"
    " AND NOT p.document_id = ANY(CAST(:exclude AS uuid[]))"
    " GROUP BY p.document_id, d.display_name"
    " ORDER BY count(*) DESC, lower(d.display_name), p.document_id LIMIT :n) g"
    " CROSS JOIN LATERAL (SELECT q.page_from, q.text FROM pieces q WHERE q.customer_id = :c"
    " AND q.document_id = g.document_id AND q.seq = g.first_seq LIMIT 1) f"
    " ORDER BY g.hits DESC, lower(g.display_name), g.document_id"
)

# The listed documents' matching pieces, in hybrid._DETAILS's columns, in reading order. The
# ids came from _DOCUMENTS in this transaction, inside the scope.
_PIECES = text(
    "SELECT p.id, p.document_id, d.display_name, d.detected_type, p.page_from, p.page_to, p.text,"
    " p.regions, CASE WHEN EXISTS (SELECT 1 FROM invoices i WHERE i.customer_id = :c"
    " AND i.document_id = p.document_id AND i.check_status = 'failed') THEN 'needs_review' ELSE p.label END,"
    " d.doc_type, p.seq, p.context_header"
    " FROM pieces p"
    " JOIN documents d ON d.customer_id = p.customer_id AND d.id = p.document_id"
    " CROSS JOIN (SELECT phraseto_tsquery('simple', :w) || phraseto_tsquery('simple', :l)) AS w (q)"
    " WHERE p.customer_id = :c AND d.deleted_at IS NULL AND d.is_current AND d.status = 'ready'"
    " AND p.document_id = ANY(CAST(:documents AS uuid[])) AND p.tsv_simple @@ w.q"
    " ORDER BY p.document_id, p.seq"
)


class PgExhaustive:
    """``Exhaustive`` over PostgreSQL, inside ``db.customer``: two statements."""

    def __init__(self, db: Database) -> None:
        self._db = db

    def documents_with(
        self, customer_id: str, term: str, scope: Scope, listed: int
    ) -> tuple[int, list[list[Hit]]]:
        from kaagaz.search.hybrid import FusedPiece, _hit

        params = {"c": customer_id, "w": term, "l": latin_term(term)}
        with self._db.customer(customer_id) as conn:
            rows = conn.execute(_DOCUMENTS, params | scope.params() | {"n": max(1, listed)}).all()
            if not rows:
                return 0, []
            shown = [str(row[0]) for row in rows]
            found: dict[str, list[Hit]] = {document: [] for document in shown}
            # ponytail: every matching piece of the listed documents is read, for the pages;
            # a word on thousands of pieces reads them all. Count pages in SQL if that bites.
            for row in conn.execute(_PIECES, params | {"documents": shown}):
                found[str(row[1])].append(_hit(row, FusedPiece(str(row[0]), 0.0, None, None, None)))
        return int(rows[0][1]), [found[document] for document in shown]

    def mentions(
        self, customer_id: str, terms: Sequence[str], scope: Scope, exclude: Sequence[str], listed: int
    ) -> tuple[int, list[dict[str, Any]]]:
        """``Mentions`` over PostgreSQL, inside ``db.customer``: one statement."""
        if not terms:
            return 0, []
        three = [*terms[:3], *[terms[0]] * (3 - len(terms[:3]))]  # an unused slot repeats the first
        params: dict[str, Any] = {"c": customer_id, "exclude": list(exclude), "n": max(1, listed)}
        for i, term in enumerate(three, start=1):
            params |= {f"w{i}": term, f"l{i}": latin_term(term)}
        with self._db.customer(customer_id) as conn:
            rows = conn.execute(_MENTIONS, params | scope.params()).all()
        if not rows:
            return 0, []
        return int(rows[0][5]), [
            mention_row(str(r[0]), r[1], list(r[2]), int(r[3]), r[4], terms) for r in rows
        ]


MAX_MENTION_TERMS = 3
MAX_MENTIONED = 50
# Never a key term: the question's frame and fillers, highest/total/not, date words, the words
# that bound a figure or a span ("over 2007 to 2026", #410), and the glossary's document words
# in every script (village, जिल्हा, malak): on most forms.
_NOT_TERMS = (
    _FRAME
    | _NOT_ROUTED
    | _words(
        set(DATE_WORDS), set(glossary.EQUIVALENTS), {w for ws in glossary.EQUIVALENTS.values() for w in ws}
    )
    | _words({"over", "above", "under", "below", "between", "than", "since", "until", "till", "upto"})
)


def key_terms(question: str, evidence: Sequence[str] = ()) -> list[str]:
    """Up to MAX_MENTION_TERMS words of the question, as typed, that name what it is about
    (a name or a place: Jalna, जालना, Patil): letters only, three or more, not a frame or
    glossary word nor a Romanised one (``glossary.concept``), nor one the Hinglish map reads as
    another word ("Kiska" is whose, #410); words the cited ``evidence`` holds first. ponytail:
    a plain English word (deed, road) passes too; a stop list grows when one proves noisy. O(n + e)."""
    seen: set[str] = set()
    found: list[str] = []
    for word in spaced_words(fold_digits(question)):
        folded = _fold(word)
        if len(folded) < 3 or folded in seen or folded in _NOT_TERMS:
            continue
        if not all(unicodedata.category(ch)[0] in "LM" for ch in folded):
            continue
        if not has_devanagari_letters(folded) and (
            glossary.concept(folded) is not None or normalise(folded).english != folded
        ):
            continue
        seen.add(folded)
        found.append(word)
    held = _fold(" ".join(evidence))
    found.sort(key=lambda word: _fold(word) not in held)  # stable: the question's order otherwise
    return found[:MAX_MENTION_TERMS]


def mention_row(
    document_id: str, name: str, pages: list[int], page: int, first: str, terms: Sequence[str]
) -> dict[str, Any]:
    """One "Also mentions" document: its first matching piece's page, the pages that hold a
    term, and two lines of that piece from the first that holds one (any case; else its
    first two), each ``clean_text``. O(t)."""
    lines = [line for line in first.splitlines() if line.strip()]
    wanted = [_fold(term) for term in terms] + [_fold(latin_term(term)) for term in terms]
    at = next((i for i, line in enumerate(lines) if any(w in _fold(line) for w in wanted)), 0)
    return {
        "document_id": document_id,
        "document_name": clean_text(name),
        "page_no": page,
        "pages": pages,
        "snippet": "\n".join(clean_text(line).strip() for line in lines[at : at + 2])[:300],
    }


def pages_with(hits: Sequence[Hit], term: str) -> list[int]:
    """The pages of ``hits`` the phrase is on: a one-page piece's page; for a piece across
    pages, the pages of its lines that hold the phrase (each line has its region), or all its
    pages when none does alone (the phrase crosses a line, or matched its Latin spelling). O(t)."""
    wanted = " ".join(term.split()).casefold()
    pages: set[int] = set()
    for hit in hits:
        if hit.page_from == hit.page_to:
            pages.add(hit.page_from)
            continue
        lines = hit.text.splitlines()
        found = {
            int(region["page"])
            for line, region in zip(lines, hit.regions, strict=False)
            if len(lines) == len(hit.regions) and wanted in " ".join(line.split()).casefold()
            if isinstance(region, dict) and isinstance(region.get("page"), int)
        }
        pages |= found or set(range(hit.page_from, hit.page_to + 1))
    return sorted(pages)


def count_sentence(term: str, total: int, kinds: Sequence[str] = ()) -> str:
    """'3 documents mention "Jalna".', or with one type asked for, '3 land records mention
    "Jalna".' (``indian_number`` for the count)."""
    kind = kinds[0].replace("_", " ") if len(kinds) == 1 else "document"
    if total == 1:
        return f'1 {kind} mentions "{term}".'
    return f'{indian_number(total)} {kind}s mention "{term}".'


def document_sentence(name: str, pages: Sequence[int]) -> str:
    """'bill.pdf: page 3.', 'bill.pdf: pages 3, 7 and 9.', or 'bill.pdf: 14 pages.'"""
    if len(pages) == 1:
        return f"{name}: page {pages[0]}."
    if len(pages) > MAX_PAGES_NAMED:
        return f"{name}: {len(pages)} pages."
    return f"{name}: pages {', '.join(map(str, pages[:-1]))} and {pages[-1]}."


def more_text(listed: int, more: int) -> str:
    """'50 are listed here; 12 more also mention it.'"""
    also = "1 more also mentions it" if more == 1 else f"{indian_number(more)} more also mention it"
    return f"{indian_number(listed)} {'is' if listed == 1 else 'are'} listed here; {also}."
