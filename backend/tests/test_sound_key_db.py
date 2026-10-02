"""Python and SQL give the same sound keys (council 3 Oct 2026, PR 2b-1).

Migration 0008's ``kaagaz_sound_key_v1`` keys the pieces in the index, and
``sound_key`` keys the question, so they must agree on every word or a typed word
silently misses. Both run over the same ~200 texts: names, the glossary's
Devanagari terms through ``latin_spelling``, the council's spellings, whole lines,
and characters that lower-case differently (the Kelvin sign, a dotted capital I).
The query runs inside the customer's transaction, like every other.
"""

import pytest
from sqlalchemy import text

from kaagaz.answering import glossary
from kaagaz.chunking.script import latin_spelling, sound_keys
from kaagaz.db.engine import Database
from tests.db_support import new_customer
from tests.test_sound_key import DIFFERENT, KNOWN_MERGES, SAME

pytestmark = pytest.mark.db

NAMES = (
    "patil shinde gaikwad gayakwad gaekwad kulkarni kulakarni deshpande deshapande jadhav jadhao"
    " pawar more chavan chauhan thackeray thakare dnyaneshwar gyaneshwar kolheshwar koleshvar"
    " bhosale bhonsle deshmukh sawant kamble wagh waghmare shirke mahajan joshi apte gokhale"
    " phadke kharat salunkhe salunke lokhande gharat mhatre raut rane naik nayak bagwe kadam"
    " sharma verma gupta agarwal agrawal mishra tiwari tripathi chaturvedi yadav singh chaudhary"
    " choudhari qureshi siddiqui khan shaikh sheikh pathan iyer menon pillai reddy rao krushna"
    " krishna laxmi lakshmi vijay vijaya suresh ramesh ganesh ganpat ramchandra ramachandra"
)
WORDS = (
    "malak malik maalak bhogwatdar bhogvatdar bhogavatadar khatedar jamin zamin zameen jameen"
    " bhumi kshetra kshetraphal kshetrafal rakba satbara utara khasra khatauni ferfar akarni pik"
    " fasal shetkari kisan hektar ekar boja varas bijak bil pavti rasid kimmat keemat rakkam raqam"
    " ekun kharedidar kharidar vikreta grahak vikri bikri kharch nafa bhade kiraya pagar vetan khate"
    " shillak baki vyaj byaj thev jama thakbaki bakaya bharna bhugtan hapta kisht karj karz rin"
    " jaminicha malakache bhogavatadarache ahe aahe ahet kay kiti kon kuthe kevha kasa madhe madhye"
    " ani pan hota sanga dakhva kaha kab kaise wala wali gav ganv gaon taluka tahsil tehsil jilha"
    " zilla pichhle pichle kharif sarve sarvhe"
)
LINES = [
    "",
    "--",
    "INV-1042 gat 47",
    "A: जमीन मालकाचे नाव | B: रामचंद्र गणपत पाटील",
    "Gaikwad,\ngayakavad.\tBHOGWATDAR",
    # Each lower-cases to an ASCII letter in Python or PostgreSQL, yet is not one.
    "\N{KELVIN SIGN}ar \N{LATIN CAPITAL LETTER I WITH DOT ABOVE}nv \N{KELVIN SIGN}",
    "\N{LATIN SMALL LIGATURE FI}le \N{FULLWIDTH LATIN CAPITAL LETTER K}ar café naïve",
    "  leading and   trailing  ",
    "aa aaa aon ao anv ey eey dny gny ck cch x ph",
    "पाटील शिंदे",
]


def _texts() -> list[str]:
    terms = [term for group in glossary.EQUIVALENTS.values() for term in group]
    spelt = [latin_spelling(term) for term in terms]
    pairs = [word for _, group in SAME for word in group]
    pairs += [word for row in KNOWN_MERGES for word in row[1:]] + [word for row in DIFFERENT for word in row]
    return list(dict.fromkeys([*NAMES.split(), *WORDS.split(), *spelt, *pairs, *LINES]))


def test_python_and_sql_give_the_same_keys_for_every_word(db: Database) -> None:
    texts = _texts()
    assert len(texts) >= 200
    customer, _ = new_customer(db)
    with db.customer(customer) as conn:
        rows = conn.execute(
            text(
                "SELECT t, kaagaz_sound_key_v1(t) FROM unnest(CAST(:t AS text[])) WITH ORDINALITY AS u (t, n)"
                " ORDER BY n"
            ),
            {"t": texts},
        ).all()
        null = conn.execute(text("SELECT kaagaz_sound_key_v1(NULL)")).scalar_one()

    expected = [(t, " ".join(key for _, key in sound_keys(t))) for t in texts]
    assert [(str(row[0]), str(row[1])) for row in rows] == expected
    assert null is None  # STRICT


def test_the_sql_keys_are_the_hand_written_ones(db: Database) -> None:
    """The control: the parity is not two copies of one mistake."""
    customer, _ = new_customer(db)
    with db.customer(customer) as conn:
        key = conn.execute(
            text("SELECT kaagaz_sound_key_v1(:t)"),
            {"t": "Bhogwatdar, gaikwad\nzameen INV-1042 \N{KELVIN SIGN}ar"},
        ).scalar_one()
    assert key == "bogvtdr gikvd jmin inv ar"
