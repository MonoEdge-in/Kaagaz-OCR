"""The sound key (council 3 Oct 2026, PR 2b-1): spellings of one Marathi or Hindi word
typed the way it sounds get one key, and the search's sound query is built from keys only.

Every expected key is written by hand, never computed by the code under test. The
words here are the council's development set: the Romanised eval must use other
spellings (ENGINEERING_STANDARDS 3.7, leakage).
"""

import pytest

from kaagaz.chunking.script import SOUND_KEY_MIN, latin_spelling, sound_key, sound_keys
from kaagaz.search.hybrid import SOUND_TERMS_MAX, sound_query

# Each group is one word: every spelling, and latin_spelling's of the Devanagari, has the key.
SAME = [
    ("bogvtdr", ["bhogwatdar", "bhogvatdar", "bhogavatadar", latin_spelling("भोगवटादार")]),
    ("gikvd", ["gaikwad", "gayakavad", "Gaikwad", latin_spelling("गायकवाड")]),
    ("jmin", ["jamin", "zamin", "zameen", latin_spelling("जमीन")]),
    ("krj", ["karja", "qarza", latin_spelling("कर्ज")]),
    ("rkm", ["rakam", "rakkam", latin_spelling("रक्कम")]),
    ("gv", ["gav", "ganv", "gaon", latin_spelling("गाव"), latin_spelling("गांव")]),
    ("sinde", ["shinde", "shindey", latin_spelling("शिंदे")]),
    ("krisn", ["krushna", "krishna", latin_spelling("कृष्ण")]),
]

# The six known merges of different words with keys of 3 letters or more (the ML
# reviewer's 259-word vocabulary). Documented, not fixed: all are 4 letters or fewer.
KNOWN_MERGES = [
    ("bti", "bati", "batti"),
    ("hti", "hati", "hatti"),
    ("sti", "sathi", "sati"),
    ("bor", "boar", "bor"),
    ("vrs", "varas", "varsha"),
    ("rsid", "rasid", "rashid"),
]

# Different words that must keep different keys.
DIFFERENT = [
    ("malak", "mal"),
    ("jamin", "jama"),
    ("kshetra", "khata"),
    ("rakkam", "rakba"),
    ("karj", "kharch"),
    ("patil", "pawar"),
    ("shinde", "shende"),
    ("varas", "vyaj"),
    ("pavti", "pagar"),
    ("satbara", "sathi"),
]


@pytest.mark.parametrize(("key", "spellings"), SAME)
def test_spellings_of_one_word_share_its_key(key: str, spellings: list[str]) -> None:
    assert [sound_key(spelling) for spelling in spellings] == [key] * len(spellings)


@pytest.mark.parametrize(("key", "first", "second"), KNOWN_MERGES)
def test_the_known_merges_are_still_the_only_ones_of_their_words(key: str, first: str, second: str) -> None:
    assert (sound_key(first), sound_key(second)) == (key, key)
    assert len(key) <= 4


@pytest.mark.parametrize(("first", "second"), DIFFERENT)
def test_different_words_keep_different_keys(first: str, second: str) -> None:
    assert sound_key(first) != sound_key(second)


@pytest.mark.parametrize(
    ("word", "key"),
    [
        ("dnyaneshwar", "jnnesvr"),  # a starting dny is jn
        ("gnyan", "jnn"),
        ("phadke", "fdke"),  # ph is f
        ("laxmi", "lksmi"),  # x is ks
        ("thackeray", "tkeri"),  # ck is k; ay is ai
        ("chavan", "cvn"),  # c before h stays (latin_spelling's ch), its h goes
        ("kulkarni", "kulkrni"),
        ("pichhle", "picle"),  # chh is one c
        ("wagh", "vg"),  # w is v; a consonant's h goes
        ("deshmukh", "desmuk"),
        ("prushth", "prist"),  # a consonant, r and u is r and i
        ("jadhao", "jdv"),  # a final ao is av
        ("pune", "pune"),
        ("aahe", "ahe"),  # a doubled letter is one, and a first a stays
        ("ahe", "ahe"),
        ("INV-1042", "inv"),  # ASCII letters only; digits never keyed
        ("\N{KELVIN SIGN}ar", "ar"),  # the Kelvin sign is not a K
        ("\N{LATIN CAPITAL LETTER I WITH DOT ABOVE}nv", "nv"),  # nor is a dotted capital I an I
        ("पाटील", ""),  # no ASCII letter, no key
        ("", ""),
    ],
)
def test_each_rule(word: str, key: str) -> None:
    assert sound_key(word) == key


def test_short_keys_merge_different_words_so_they_are_never_matched() -> None:
    assert {sound_key(word) for word in ("kar", "khar", "khara")} == {"kr"}  # do, salt, true
    assert len("kr") < SOUND_KEY_MIN
    assert sound_query("kar khar khara gav mal") == ""


def test_sound_keys_keys_each_run_of_ascii_letters() -> None:
    assert sound_keys("Gaikwad,\ngayakavad. 47 पाटील") == [("Gaikwad", "gikvd"), ("gayakavad", "gikvd")]


def test_the_sound_query_matches_by_prefix_both_ways_for_words_of_four_letters() -> None:
    assert sound_query("jaminicha malak kon?") == "jminic:* | mlk:* | kon | jmini | jmin | jmi"
    assert sound_query("Bhogwatdar") == "bogvtdr:* | bogvtd | bogvt | bogv | bog"


def test_the_sound_query_leaves_out_identifiers_and_anything_but_letters() -> None:
    assert sound_query("gat48 INV-1042 ४७ malak") == "mlk:*"
    assert sound_query("'mlk' | !x & y:* <-> (z)") == "mlk"  # nothing but [a-z] terms survive


def test_the_sound_query_keeps_every_word_before_any_shorter_start_and_at_most_twelve_terms() -> None:
    words = "bhogwatdar gaikwad kshetra satbara jaminicha rakkam karja shinde"
    terms = sound_query(words).split(" | ")
    assert terms[:8] == ["bogvtdr:*", "gikvd:*", "ksetr:*", "stbr:*", "jminic:*", "rkm:*", "krj:*", "sinde:*"]
    assert len(terms) == SOUND_TERMS_MAX == 12
