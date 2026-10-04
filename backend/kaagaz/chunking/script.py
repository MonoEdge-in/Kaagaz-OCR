"""The script of a text: how much of it is Devanagari, whether an OCR reading
of it looks sound, and a Latin spelling of it for search.

* ``devanagari_share`` and ``is_devanagari`` (PRD 2 L2). A text is Devanagari
  when at least half of its letters are in U+0900 to U+097F. Letters are the
  Unicode categories L and M, so vowel signs (matras) count; digits,
  punctuation and spaces do not.
* ``ocr_script_suspect`` (security amendment A3, with ML amendment 1(a)'s
  exact rule). On Marathi pages PaddleOCR-VL wrote letters from other scripts
  (Arabic, Bengali) into Devanagari words, and it gives no confidence. So a
  reading is suspect when a letter or mark is neither ASCII nor in U+0900 to
  U+097F, or when its Devanagari is malformed (``malformed_devanagari``). The
  danda and the digits are not letters, so they are fine. ``ocr_suspect_count``
  counts those letters and malformed signs, so two readings of a page can be
  compared (``kaagaz/reading/ocr.py``).
* ``has_devanagari_letters`` (ML amendment 1(b)). PROVISIONAL for PRD 2: an
  OCR value with any Devanagari letter is needs review. Digits and the danda
  do not count. ``has_other_script_letters`` is the same for any script but Latin, by
  ``letter_script``: the first word of a letter's Unicode name.
* ``fold_digits`` (L4). Devanagari digits become ASCII digits for parsing and
  for quote matching. It is one character for one, so offsets still hold. The
  printed text (``value_text``) is never changed.
* ``ascii_digits`` (council 2 Oct 2026, PR2 E). Every Unicode decimal digit
  (Devanagari, Gujarati, any category Nd) as its ASCII digit, for checking
  numbers; one character for one. ``fold_digits`` stays Devanagari only:
  ``hinglish.normalise`` uses it, and its output must not change.
* ``latin_spelling`` (L3, ML amendment 8's recipe, PROVISIONAL). For each run
  of Devanagari letters: NFC, then IAST with indic-transliteration. The final
  "a" is dropped only when the run ends in a bare consonant (a nukta is
  allowed) that does not follow a virama, decided on the Devanagari side, so
  पाटील gives patil and कृष्ण keeps krishna. A final visarga is left aside
  for this (PROVISIONAL), so रामः gives ram, as राम does. Then, in this
  order: "ch" to "chh" (through a placeholder) and "c" to "ch"; ś and ṣ to
  "sh"; ṛ and ṝ to "ri"; ṃ to "m" before p, b or m, else "n"; "~" and m̐ to "n"; ḥ dropped.
  Last, NFKD, drop the marks (Mn) and format characters (Cf, the joiners),
  and lower-case the whole text. Middle vowels are never dropped (kulakarni),
  so name matching must stay fuzzy. Other text is kept as it is.
  PROVISIONAL: IAST has no letters for the candra vowels of English words, so
  before it they become plain vowels (ॉ and ऑ as o, ॅ and ऍ as e, ॲ as a) and
  the abbreviation sign ॰ is dropped. So कॉपी gives "kopi". The danda and
  double danda (। ॥) become ".".
* ``word_latin_spelling`` (council 4 Oct 2026, PR C). One word of any one
  script indic-transliteration reads (its Unicode script name, lower-cased, is
  one of its non-Latin schemes: Gujarati, Bengali, Tamil, Oriya...), turned
  into Devanagari by it, then ``latin_spelling``. None for a word of another
  script (Arabic) or of two scripts. No table of ours names a script.
* ``languages_without_model`` (L2). With no model, a Devanagari piece means
  "other" (Hindi and Marathi cannot be told apart), a piece whose letters are
  mostly ASCII means "en", and any other script means "other".
* ``sound_key`` and ``sound_keys`` (council 3 Oct 2026, PR 2b, the ML
  reviewer's rules, PROVISIONAL). People type a Marathi or Hindi word the way
  it sounds: bhogwatdar, bhogvatdar, and ``latin_spelling`` gives bhogavatadar.
  The sound key gives all three one form, ``bogvtdr``. In this order: keep the
  ASCII letters only, then lower-case them; a starting dny or gny is jn; ph is
  f, x is ks, ck is k; a c not before h is k (``latin_spelling``'s c is only
  ever ch); w, z, q are v, j, k; ay is ai; a consonant's h (one or more) goes
  (bh, sh, chh); a consonant, r and u is the consonant, r and i (krushna is
  krishna); ee is i, oo is u, au is o; a doubled letter is one; a final aon,
  ao or anv is av (gaon and ganv are gav); a final ey is e (shindey); and
  every a but a first letter goes. Known merges of different words, all with
  keys of 4 letters or fewer: bati/batti, hati/hatti, sathi/sati, boar/bor,
  varas/varsha, rasid/rashid. A key under ``SOUND_KEY_MIN`` letters (kar and
  khar are both kr) is never matched. Only the first ``SOUND_WORD_MAX``
  letters of a word are keyed, so a key costs O(1) per word however long the
  word is. ``sound_keys`` keys each run of ASCII letters of a text, except in
  a part (between ASCII spaces, tabs or line breaks) with a digit: "INV-1042"
  is an identifier, matched as written elsewhere, never as "inv". Migration
  0011's SQL function ``kaagaz_sound_key_v1`` gives the same keys joined by
  spaces (tests/test_sound_key_db.py).

Every function is O(n) time in the characters of the text; ``latin_spelling``
and ``sound_keys`` take O(n) memory, the others O(1).
"""

import re
import unicodedata
from collections.abc import Sequence

from kaagaz.chunking.piece import Piece

DEVANAGARI_MIN_SHARE = 0.5  # L2: "at least half its letters"

_CONSONANTS = frozenset(
    [chr(c) for c in range(0x0915, 0x093A)]
    + [chr(c) for c in range(0x0958, 0x0960)]
    + [chr(c) for c in range(0x0978, 0x0980)]
)
_VOWEL_SIGNS = frozenset(
    [chr(c) for c in (0x093A, 0x093B, 0x094E, 0x094F, 0x0962, 0x0963)]
    + [chr(c) for c in range(0x093E, 0x094D)]
    + [chr(c) for c in range(0x0955, 0x0958)]
)
_NUKTA = "\u093c"
_VIRAMA = "\u094d"
_VISARGA = "\u0903"
_JOINERS = "\u200c\u200d"  # ZWNJ and ZWJ

_DIGITS = str.maketrans("०१२३४५६७८९", "0123456789")
# PROVISIONAL: the candra vowels as the plain vowels IAST can spell, ॰ dropped, । and ॥ as ".".
_BEFORE_IAST = str.maketrans(
    {
        "\u0949": "\u094b",
        "\u0911": "\u0913",
        "\u0945": "\u0947",
        "\u090d": "\u090f",
        "\u0972": "\u0905",
        "\u0970": None,
        "\u0964": ".",  # the danda and double danda end a sentence (council)
        "\u0965": ".",
    }
)

# A run of Devanagari letters and signs (with the joiners), or a run of the
# block's other characters: the danda, the digits and the abbreviation sign.
_RUN = re.compile(r"(?P<letters>[\u0900-\u0963\u0971-\u097f\u200c\u200d]+)|[\u0964-\u0970]+")
# The inherent "a", maybe before a nukta or joiner, and before a final visarga (dropped later).
_FINAL_A = re.compile(r"a([\u093c\u200c\u200d]*)(?=\u1e25?\Z)")
_ANUSVARA_BEFORE_LIP = re.compile("\N{LATIN SMALL LETTER M WITH DOT BELOW}(?=[pbm])")
_PLACEHOLDER = "\x00"  # never in the IAST spelling of a Devanagari run
_NON_ASCII_DIGIT = re.compile(r"(?![0-9])\d")  # in a str pattern, \d is every decimal digit (Nd)

SOUND_KEY_MIN = 3  # letters: a shorter sound key is never matched (kar and khar are both kr)
SOUND_WORD_MAX = 40  # letters of a word that are keyed; the rest is left out
_SPACES = re.compile("[ \t\n\r\f\v]+")  # ASCII only, as migration 0009 splits a text
_ASCII_DIGIT = re.compile("[0-9]")
_ASCII_LETTERS = re.compile("[A-Za-z]+")  # ranges are code points: the Kelvin sign is not a K
_NOT_ASCII_LETTER = re.compile("[^A-Za-z]")
_SOUND_FOLD = str.maketrans("wzq", "vjk")
_SOUND_START = re.compile(r"\A(?:dny|gny)")  # dnyaneshwar is jnaneshvar
_SOUND_C = re.compile(r"c(?!h)")
_SOUND_ASPIRATE = re.compile(r"([bcdfgjklmnpqrstv])h+")
_SOUND_RI = re.compile(r"([bcdfgjklmnpqrstv]r)u")
_SOUND_DOUBLE = re.compile(r"(.)\1+")
_SOUND_AV = re.compile(r"a(?:on|o|nv)\Z")
_SOUND_EY = re.compile(r"ey\Z")


def devanagari_share(text: str) -> float:
    """Devanagari letters divided by all letters (categories L and M); 0.0 with no letters."""
    letters = devanagari = 0
    for ch in text:
        if unicodedata.category(ch)[0] in "LM":
            letters += 1
            devanagari += "\u0900" <= ch <= "\u097f"
    return devanagari / letters if letters else 0.0


def is_devanagari(text: str) -> bool:
    """L2: at least half of the letters are Devanagari."""
    return devanagari_share(text) >= DEVANAGARI_MIN_SHARE


def has_devanagari_letters(text: str) -> bool:
    """True when any letter or sign (L or M) is in U+0900 to U+097F. Digits and the danda are not letters."""
    return any("\u0900" <= ch <= "\u097f" and unicodedata.category(ch)[0] in "LM" for ch in text)


def letter_script(ch: str) -> str | None:
    """The script of a letter or mark (L or M): the first word of its Unicode name (LATIN,
    DEVANAGARI, GUJARATI, TAMIL, ARABIC...), from the Unicode database, never a table of ours.
    None for any other character, and for a mark of no script of its own (COMBINING ...)."""
    if unicodedata.category(ch)[0] not in "LM":
        return None
    first = unicodedata.name(ch, "").partition(" ")[0]
    return None if first in ("", "COMBINING") else first


def has_other_script_letters(text: str) -> bool:
    """True when any letter or mark is of a script other than Latin (council 4 Oct 2026). O(n)."""
    return not text.isascii() and any(letter_script(ch) not in (None, "LATIN") for ch in text)


def malformed_devanagari(text: str) -> int:
    """How many vowel signs and viramas do not directly follow a consonant or a nukta.
    Two vowel signs in a row count too, because the second follows a vowel sign."""
    count = 0
    previous = ""
    for ch in text:
        if (ch in _VOWEL_SIGNS or ch == _VIRAMA) and not (previous in _CONSONANTS or previous == _NUKTA):
            count += 1
        previous = ch
    return count


def ocr_suspect_count(text: str) -> int:
    """How many letters and marks are neither ASCII nor Devanagari, plus ``malformed_devanagari``."""
    foreign = sum(
        1
        for ch in text
        if ch >= "\x80" and not "\u0900" <= ch <= "\u097f" and unicodedata.category(ch)[0] in "LM"
    )
    return foreign + malformed_devanagari(text)


def ocr_script_suspect(text: str) -> bool:
    """True when a letter or mark is neither ASCII nor Devanagari, or the Devanagari is malformed."""
    return ocr_suspect_count(text) > 0


def fold_digits(text: str) -> str:
    """Devanagari digits as ASCII digits; every other character unchanged, so the length is the same."""
    return text.translate(_DIGITS)


def ascii_digits(text: str) -> str:
    """Every decimal digit (category Nd) as its ASCII digit; every other character unchanged."""
    if text.isascii():
        return text
    return _NON_ASCII_DIGIT.sub(lambda match: str(unicodedata.decimal(match[0])), text)


def latin_spelling(text: str) -> str:
    """L3: ``text`` with every Devanagari run spelled in Latin letters, all lower-cased."""
    # Imported here: indic-transliteration takes about a second to import.
    from indic_transliteration import sanscript

    def spell(match: re.Match[str]) -> str:
        run = unicodedata.normalize("NFC", match[0]).translate(_BEFORE_IAST)
        latin = str(sanscript.transliterate(run, sanscript.DEVANAGARI, sanscript.IAST))
        if match["letters"] is not None:
            latin = _letters_to_latin(run, latin)
        return "".join(
            ch for ch in unicodedata.normalize("NFKD", latin) if unicodedata.category(ch) not in ("Mn", "Cf")
        )

    return _RUN.sub(spell, text).lower()


def word_latin_spelling(word: str) -> str | None:
    """The module docstring's ``word_latin_spelling``: ``word``'s Latin spelling, or None. O(n)."""
    from indic_transliteration import sanscript  # slow to import, as in latin_spelling

    scripts = {letter_script(ch) for ch in word}
    if len(scripts) != 1:
        return None
    script = scripts.pop()
    scheme = None if script is None else sanscript.SCHEMES.get(script.lower())
    if script is None or scheme is None or scheme.is_roman:
        return None
    text = unicodedata.normalize("NFC", word)
    if script != "DEVANAGARI":
        text = str(sanscript.transliterate(text, script.lower(), sanscript.DEVANAGARI))
    return latin_spelling(text)


def sound_key(word: str) -> str:
    """The sound key of one word (the module docstring's rules, in order); "" with no
    ASCII letter. O(n) to strip the word, then O(1): at most ``SOUND_WORD_MAX`` letters."""
    w = _NOT_ASCII_LETTER.sub("", word).lower()[:SOUND_WORD_MAX]
    if not w:
        return ""
    w = _SOUND_START.sub("jn", w)
    w = w.replace("ph", "f").replace("x", "ks").replace("ck", "k")
    w = _SOUND_C.sub("k", w)
    w = w.translate(_SOUND_FOLD).replace("ay", "ai")
    w = _SOUND_ASPIRATE.sub(r"\1", w)
    w = _SOUND_RI.sub(r"\1i", w)
    w = w.replace("ee", "i").replace("oo", "u").replace("au", "o")
    w = _SOUND_DOUBLE.sub(r"\1", w)
    w = _SOUND_AV.sub("av", w)
    w = _SOUND_EY.sub("e", w)
    return w[0] + w[1:].replace("a", "")


def sound_keys(text: str) -> list[tuple[str, str]]:
    """(word, sound key) for each run of ASCII letters in ``text``, in order, but those of a
    part with a digit (the module docstring). O(n)."""
    parts = [part for part in _SPACES.split(text) if not _ASCII_DIGIT.search(part)]
    return [(word, sound_key(word)) for part in parts for word in _ASCII_LETTERS.findall(part)]


def languages_without_model(pieces: Sequence[Piece]) -> tuple[str, ...]:
    """L2 with no model: ("en",) and/or ("other",), in that order; () when no piece has letters.
    O(n) in the characters of the pieces."""
    found = set()
    for piece in pieces:
        letters = ascii_letters = 0
        for ch in piece.text:
            if unicodedata.category(ch)[0] in "LM":
                letters += 1
                ascii_letters += ch.isascii()
        if letters:
            found.add("en" if not is_devanagari(piece.text) and 2 * ascii_letters >= letters else "other")
    return tuple(language for language in ("en", "other") if language in found)


def _letters_to_latin(run: str, iast: str) -> str:
    """ML 8's final "a" rule and letter map, in its order, on one run's IAST spelling."""
    if _ends_in_bare_consonant(run):
        iast = _FINAL_A.sub(r"\1", iast)
    iast = iast.replace("ch", _PLACEHOLDER).replace("c", "ch").replace(_PLACEHOLDER, "chh")
    iast = iast.replace("\N{LATIN SMALL LETTER S WITH ACUTE}", "sh")
    iast = iast.replace("\N{LATIN SMALL LETTER S WITH DOT BELOW}", "sh")
    iast = iast.replace("\N{LATIN SMALL LETTER R WITH DOT BELOW}", "ri")
    iast = iast.replace("\N{LATIN SMALL LETTER R WITH DOT BELOW AND MACRON}", "ri")
    iast = _ANUSVARA_BEFORE_LIP.sub("m", iast).replace("\N{LATIN SMALL LETTER M WITH DOT BELOW}", "n")
    iast = iast.replace("~", "n").replace("m\N{COMBINING CANDRABINDU}", "n")
    return iast.replace("\N{LATIN SMALL LETTER H WITH DOT BELOW}", "")


def _ends_in_bare_consonant(run: str) -> bool:
    """The run's last letter is a consonant (a nukta or a visarga may follow it) that does not
    follow a virama."""
    letters = run.rstrip(_JOINERS).removesuffix(_VISARGA).rstrip(_JOINERS)
    if letters.endswith(_NUKTA):
        letters = letters[:-1]
    if not letters or letters[-1] not in _CONSONANTS:
        return False
    return not letters[:-1].rstrip(_JOINERS).endswith(_VIRAMA)
