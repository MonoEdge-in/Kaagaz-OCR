"""Story matching rules (kaagaz/search/story_match.py, council 3 Oct 2026 and its review):
the words, each way a word matches, the bands with the number and one-word rules,
meaning, ties, order, the review's false-"match" probes, and the speed.
Pure: no database, no model. Expected values are written by hand."""

import time
from collections.abc import Iterator, Sequence

import pytest

from kaagaz.chunking.script import sound_key
from kaagaz.search import story_match as m
from kaagaz.search.story_match import Candidate, QueryWord

STORY = "papa ne 2018 me jo daregaon wali jamin ka 7/12 diya tha, bank loan ke liye"
# (question word, story word). Trigram 0.5+, both 4+ letters (kotharud, daregav); the same
# sound key (jameen, zamin and jamin are jmin; bil); a key of 4+ letters starting a story
# key at most 2 letters longer (jmin, jminic; bogvt, bogvtdr); a glossary alternative,
# exactly (land as jamin, father as pita); digits with "-", "/" and "." as one.
MATCH = (
    "jamin:jamin daregav:daregaon kotharud:kothrud jameen:jamin zamin:jamin bil:bill jamin:jaminicha"
    " bhogwat:bhogwatdar land:jamin father:pita 7/12:7-12 7.12:7/12"
)
# The review's probes: a key under 4 letters never starts another (lon, lonvl; bil, bilkul,
# bilvr; krd, krdmom), nor one 3+ letters longer (jmin, jminicy); an alternative never
# by trigram or sound (bhumi, bhumika), and none under 4 letters (rin, nam); a 3-letter
# story word never by trigram (name, nam); a separator counts (712, 1/2); no digit by
# trigram; letters against a word with a digit; not letters only.
NO_MATCH = (
    "loan:lone loan:lonavala loan:london bill:bilkul bill:bhilwara card:cardamom jamin:jaminichya"
    " land:bhumika loan:rin loan:rinku name:nam 712:7/12 1/2:12 2018:2019 loan:loan2 e-mail:email"
)


def _numbers(seed: int) -> Iterator[float]:
    """A fixed sequence in [0, 1), the same every run (a linear congruential generator)."""
    while True:
        seed = (seed * 1103515245 + 12345) % 2**31
        yield seed / 2**31


def _word(numbers: Iterator[float], letters: str, most: int) -> str:
    return "".join(letters[int(next(numbers) * len(letters))] for _ in range(1 + int(next(numbers) * most)))


def words(question: str) -> list[tuple[str, tuple[str, ...]]]:
    return [(word.word, word.alternatives) for word in m.question_words(question)]


def ranked(
    question: str,
    *stories: str,
    meaning: Sequence[float | None] = (),
    threshold: float | None = None,
    mode: m.Mode = "cards",
    most: int = m.MATCHES_MAX,
) -> list[tuple[str, str]]:
    cosines = list(meaning) or [None] * len(stories)
    candidates = [Candidate(str(i), f"doc {i}", None, story, cosines[i]) for i, story in enumerate(stories)]
    found = m.rank(m.question_words(question), candidates, threshold=threshold, mode=mode, most=most)
    return [(match.document_id, match.band) for match in found]


def test_the_words() -> None:
    assert words("Papa ne jo Daregaon wali jamin ka 7/12 di thi, kiti?") == [
        ("papa", ()),
        ("daregaon", ()),
        ("jamin", ("bhumi",)),  # the Romanised glossary's exact alternative
        ("7/12", ()),
    ]
    # Generic words and one-digit words never count; alternatives are 4+ letters only.
    assert words("the land papers of 7 kagaz file") == [("land", ("jamin", "bhumi"))]
    # Changed on purpose (#622): "name" is a request word now, never a word of the question.
    assert words("loan tax name") == [("loan", ("karja",)), ("tax", ("jiesati",))]
    assert words("दरेगाव जमीन ७/१२ जमीन") == [("daregav", ()), ("jamin", ("bhumi",)), ("7/12", ())]
    assert words("INV-1042 inv-1042 2 11") == [("inv-1042", ()), ("11", ())]
    assert len(m.question_words(" ".join(f"word{chr(97 + i)}x" for i in range(20)))) == m.WORDS_MAX == 12
    assert m.question_words("the of and ka ne wali ahe papers documents agreement") == []


def test_one_word_against_one_story_word() -> None:
    assert m.trigrams("ab") == frozenset({"  a", " ab", "ab "})
    assert m.similarity("kotharud", "kothrud") == pytest.approx(6 / 11)
    for pairs, expected in ((MATCH, True), (NO_MATCH, False)):
        for pair in pairs.split():
            form, story_word = pair.split(":")
            assert m.form_matches(form, story_word) is expected, pair


def _reference(asked: QueryWord, story_word: str) -> bool:
    """Step 2 written plainly, one pair at a time, to check the matcher's tables."""
    word = asked.word
    if story_word in (word, *asked.alternatives):
        return True
    if any(c.isdigit() for c in word + story_word):
        same = word.replace("-", "/").replace(".", "/") == story_word.replace("-", "/").replace(".", "/")
        return any(c.isdigit() for c in word) and any(c.isdigit() for c in story_word) and same
    if not (word.isalpha() and word.isascii() and story_word.isalpha() and story_word.isascii()):
        return False
    if min(len(word), len(story_word)) >= 4 and m.similarity(word, story_word) >= 0.5:
        return True
    key, other = sound_key(word), sound_key(story_word)
    prefix = len(key) >= 4 and other.startswith(key) and len(other) <= len(key) + 2
    return len(key) >= 3 and (other == key or prefix)


def test_the_matchers_tables_agree_with_the_plain_rules() -> None:
    numbers = _numbers(5)
    pool = [_word(numbers, "aabdeghijklmnoprstuvwxyz", 9) for _ in range(600)]
    pool += ["jamin", "jaminicha", "bhogwatdar", "dnyanesh", "jnanesh", "7/12", "7-12", "712", "x-ray", "kar"]
    asked = [
        QueryWord(word, m._alternatives(word)) for word in [*pool[:40], "land", "father", "7.12", "dnyan"]
    ]
    for story_word in pool:
        for word in asked:
            assert m.form_matches(word.word, story_word) is _reference(word, story_word), (
                word.word,
                story_word,
            )


def _band(matched: list[bool], exact: list[bool] | None = None, word: str = "word") -> str | None:
    words = [QueryWord(f"{word}x{'y' * i}" if len(matched) > 1 else word) for i in range(len(matched))]
    return m.word_band(words, matched, exact or [False] * len(matched))


def test_bands_by_coverage_numbers_and_the_one_word_rule() -> None:
    yes, no = True, False
    assert _band([yes, yes]) == _band([yes, yes, yes, no]) == "match"  # 1.0, 0.75
    assert _band([yes, no]) == _band([yes, yes, no, no]) == _band([yes, yes, no]) == "maybe"  # 0.5, 0.67
    assert _band([no, no]) is _band([yes, no, no, no]) is None  # 0.25
    assert _band([yes, yes], word="2018") == "maybe"  # numbers alone: "maybe" at most
    assert _band([yes], [yes], "loan") == "maybe"  # the word itself, 4+ letters, in the story
    assert _band([yes], [no], "loan") is None  # by trigram, sound or an alternative only
    assert _band([yes], [yes], "car") is _band([yes], [yes], "2018") is None
    assert ranked("loan", STORY) == ranked("Daregaon", STORY) == [("0", "maybe")]
    assert ranked("lion", STORY) == ranked("2018", STORY) == ranked("the of ka", STORY) == []
    assert ranked("loan", "Rinku ki rin pustika") == ranked("father papers", "Pita ji ke papers") == []
    assert ranked("2018 2019", STORY) == ranked("5000 2023", "5000 rupaye 2023 me diye") == [("0", "maybe")]
    assert ranked("1/2 acre", "12 acre ka plot") == ranked("bank 7", STORY) == [("0", "maybe")]
    assert ranked("2 11", "2 11 ka hisab") == []
    assert ranked("7-12 daregaon", STORY) == [("0", "match")]
    assert ranked("712 daregaon", STORY) == [("0", "maybe")]


@pytest.mark.parametrize(
    ("question", "story"),
    [
        ("loan papers", "Rinku ke school papers"),
        ("tax papers", "Papa ne sign kar ke papers diye"),
        ("property tax", "Kaam kar diya, property ka naksha"),
        ("name change", "Mera nam change hua"),
        ("father papers", "Pita ji ke papers"),
        ("land papers", "Bhumika ke papers"),
        ("bill papers", "Bilkul sahi papers"),
        ("loan papers", "Lonavala flat papers"),
        ("loan agreement", "London trip agreement"),
        ("bill payment", "Bhilwara se kapda aaya"),
        ("pan card", "Cardamom ki bori ka hisab"),
    ],
)
def test_the_reviews_probes_are_never_a_match(question: str, story: str) -> None:
    assert ("0", "match") not in ranked(question, story)


def test_a_story_found_by_a_glossary_word_never_outranks_the_real_one() -> None:
    assert ranked("loan papers", "Rinku ke school papers", STORY) == [("1", "maybe")]


def test_meaning_band_is_maybe_at_most() -> None:
    assert m.meaning_band(0.9, None) is None  # uncalibrated: OFF
    assert m.meaning_band(0.5, 0.6) is m.meaning_band(None, 0.6) is None
    assert m.meaning_band(0.7, 0.6) == m.meaning_band(0.99, 0.6) == "maybe"  # never "match"


def test_the_worked_example() -> None:
    other = "dukan ka bijli bill march 2024, shinde electricals ko cash me diya"
    assert ranked("daregaon jamin papa loan", STORY, other) == [("0", "match")]
    assert ranked("Daregaon wali jameen ka 7/12", STORY, other) == [("0", "match")]
    assert ranked("the land paper dad gave for the bank loan", STORY, other) == [("0", "maybe")]
    assert ranked("bank statement", STORY, other) == [("0", "maybe")]
    assert ranked("bijli bill shinde", STORY, other) == [("1", "match")]
    assert ranked("pan card ki copy", STORY, other) == []


def test_ties_order_and_at_most_three() -> None:
    assert ranked("bank loan", STORY, "bank loan papers") == [("0", "maybe"), ("1", "maybe")]  # a top tie
    assert ranked("bank loan papa", STORY, "bank loan papers") == [("0", "match"), ("1", "maybe")]
    stories = ["gold loan", "bank loan", "loan papers 2018", "loan", "gold bank loan"]
    assert ranked("gold bank loan", *stories) == [("4", "match"), ("0", "maybe"), ("1", "maybe")]
    # As many words, more of them exact: still no margin, so neither is "match".
    assert ranked("daregaon jamin", STORY, "daregav jamin") == [("0", "maybe"), ("1", "maybe")]
    assert len(ranked("loan", *stories)) == m.MATCHES_MAX == 3
    assert m.rank(m.question_words("loan"), [], threshold=None) == []
    assert m.rank([], [Candidate("0", "a", None, STORY, 0.9)], threshold=None) == []


def test_meaning_counts_only_when_calibrated() -> None:
    house = ("sale deed of the house", "kharedikhat", "bijli bill")
    assert ranked(*house, meaning=[0.9, 0.2]) == []
    assert ranked(*house, meaning=[0.9, 0.2], threshold=0.6) == [("0", "maybe")]  # never "match"
    assert ranked(*house, meaning=[0.7, 0.69], threshold=0.6) == [("0", "maybe"), ("1", "maybe")]
    # Words and meaning: the better band; the one word "match" first.
    found = ranked("bijli bill", "kharedikhat", "bijli bill", meaning=[0.9, None], threshold=0.6)
    assert found == [("1", "match"), ("0", "maybe")]


def test_at_most_one_match_after_meaning_too() -> None:
    """The recurring documents (council 3 Oct 2026): this year's receipt and last
    year's tell nearly the same story. Only a story that matched strictly more words
    than every other read keeps "match", in both modes."""
    fees = ("rohit school fees 2023 pehla term", "rohit school fees 2024 dusra term")
    for mode in ("cards", "stories"):
        assert ranked("rohit school fees 2024", *fees, mode=mode) == [("1", "match"), ("0", "maybe")]
        assert ranked("rohit school fees", *fees, mode=mode) == [("0", "maybe"), ("1", "maybe")]
    # A third story as good by meaning alone never takes "match" from the words.
    found = ranked("rohit fees 2024", *fees, "rohit", meaning=[None, None, 0.95], threshold=0.6)
    assert found[0] == ("1", "match") and sorted(found[1:]) == [("0", "maybe"), ("2", "maybe")]


def test_story_mode_one_word_rule() -> None:
    """Mode "stories": one word is still "maybe" at most; exact with 3+ letters, or by
    trigram or sound with 5+ letters; never by a glossary alternative or a number."""
    for word, story, cards, stories in [
        ("car", "car insurance", [], [("0", "maybe")]),  # exact, 3 letters
        ("jameen", STORY, [], [("0", "maybe")]),  # sound, 6 letters
        ("daregav", STORY, [], [("0", "maybe")]),  # trigram, 7 letters
        ("loan", STORY, [("0", "maybe")], [("0", "maybe")]),  # exact: both modes
        ("bil", "dukan ka bill", [], []),  # sound, 3 letters
        ("zamin", STORY, [], [("0", "maybe")]),  # sound, 5 letters
        ("land", STORY, [], []),  # only the alternative jamin
        ("2018", STORY, [], []),  # a number alone
    ]:
        assert ranked(word, story) == cards, word
        assert ranked(word, story, mode="stories") == stories, word


def test_story_mode_orders_a_band_by_idf_and_shows_up_to_ten() -> None:
    # "loan" is in every story, "muthoot" in one: the rarer word's story comes first,
    # though the fixed order (and the side cards) put story 0 first.
    stories = ["bank loan", "home loan bank", "muthoot loan", "car loan bank"]
    assert ranked("muthoot loan bank", *stories) == [("0", "maybe"), ("1", "maybe"), ("2", "maybe")]
    found = ranked("muthoot loan bank", *stories, mode="stories", most=10)
    assert found == [("2", "maybe"), ("0", "maybe"), ("1", "maybe"), ("3", "maybe")]
    assert ranked("muthoot loan", *stories, mode="stories")[0] == ("2", "match")
    assert m.idf(4, 4) < m.idf(4, 1) and m.idf(1, 1) > 0  # IDF only orders: never below 0
    many = [f"loan {i}" for i in range(15)]
    assert len(ranked("loan", *many, mode="stories", most=10)) == m.MOST_MAX == 10
    assert len(ranked("loan", *many, mode="stories", most=1)) == 1
    assert len(ranked("loan", *many, most=50)) == 10  # never past MOST_MAX


def test_a_match_holds_no_score_and_no_story() -> None:
    story = [Candidate("d", "7-12.pdf", "land_record", STORY, None)]
    (found,) = m.rank(m.question_words("daregaon jamin"), story, threshold=None)
    assert found == m.StoryMatch("d", "7-12.pdf", "land_record", "match")
    assert set(m.StoryMatch.__slots__) == {"document_id", "display_name", "doc_type", "band"}


def test_500_stories_of_1000_characters_rank_in_under_1_5_s() -> None:
    """A vocabulary of 5,000 words, a few common and most rare; the best of three runs."""
    numbers = _numbers(3)
    vocabulary = list(dict.fromkeys(_word(numbers, "kramjdpntsvlgbiu", 8) for _ in range(9000)))[:5000]
    stories = []
    for index in range(500):  # a word's rank is 5000 ** u: few words are common, most are rare
        text = " ".join(vocabulary[int(5000 ** next(numbers)) - 1] for _ in range(220))
        stories.append(Candidate(str(index), "d", None, f"{index} 7/12 {text}"[:1000], None))
    asked = m.question_words("daregaon jamin papa loan bank 7/12 2018 kharedikhat pune ghar nondani rasid")
    for mode in ("cards", "stories"):
        times = []
        for _ in range(3):
            start = time.perf_counter()
            m.rank(asked, stories, threshold=None, mode=mode, most=m.MOST_MAX)
            times.append(time.perf_counter() - start)
        assert min(times) < 1.5, mode  # about 0.2 s on the laptop: room for a slow CI runner
