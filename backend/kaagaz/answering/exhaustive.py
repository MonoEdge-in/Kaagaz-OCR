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

Time: two statements over the word index; then O(p * t) for the p matching pieces of the
listed documents, of t characters.
"""

import re
import unicodedata
from collections.abc import Sequence
from itertools import pairwise
from typing import Protocol

from sqlalchemy import text

from kaagaz.answering import glossary
from kaagaz.answering.dates import mentions_date
from kaagaz.answering.evidence import Hit
from kaagaz.answering.format import indian_number
from kaagaz.answering.hinglish import STOP_WORDS, normalise
from kaagaz.chunking.script import fold_digits, has_devanagari_letters, latin_spelling, spaced_words
from kaagaz.db.engine import Database
from kaagaz.search.scope import Scope

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
    {"how", "many", "the", "a", "an", "of", "do", "does", "are", "is", "there"},
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
    question = fold_digits(question)
    quoted = _QUOTED.search(question)
    outside = question if quoted is None else question[: quoted.start()] + " " + question[quoted.end() :]
    words = spaced_words(outside)
    folded = [_fold(word) for word in words]
    asked = bool(_ASK.intersection(folded)) or ("how", "many") in pairwise(folded)
    if not asked or not _THINGS.intersection(folded) or not _HOLDS.intersection(folded):
        return None
    if _NOT_ROUTED.intersection(folded):
        return None
    left = [i for i, word in enumerate(folded) if word not in _FRAME]
    if quoted is not None:
        term = " ".join(spaced_words(quoted[1]))
        return term if term and not left and len(term) <= MAX_TERM_CHARS else None
    if not left or left != list(range(left[0], left[-1] + 1)) or len(left) > MAX_TERM_WORDS:
        return None
    if mentions_date(normalise(" ".join(folded))):
        return None
    term = " ".join(words[i] for i in left)
    return term if len(term) <= MAX_TERM_CHARS else None


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


# Of the scope's documents that hold the phrase, the first :n by name (then id), and how many
# in all: the window counts the groups before LIMIT.
_DOCUMENTS = text(
    "SELECT p.document_id, count(*) OVER () AS total"
    " FROM pieces p"
    " JOIN documents d ON d.customer_id = p.customer_id AND d.id = p.document_id"
    " CROSS JOIN (SELECT phraseto_tsquery('simple', :w) || phraseto_tsquery('simple', :l)) AS w (q)"
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
    " AND p.tsv_simple @@ w.q"
    " GROUP BY p.document_id, d.display_name"
    " ORDER BY lower(d.display_name), p.document_id LIMIT :n"
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


def count_sentence(term: str, total: int) -> str:
    """'3 documents mention "Jalna".' (``indian_number`` for the count)."""
    if total == 1:
        return f'1 document mentions "{term}".'
    return f'{indian_number(total)} documents mention "{term}".'


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
