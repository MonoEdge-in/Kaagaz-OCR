"""Apply this customer's remembered corrections to new values.

When a reviewer fixes a value and ticks "remember", the fix is kept as
(field, value as read, corrected value) for that customer only
(``learned_corrections``; CLAUDE.md "Nothing crosses customers"). Before a new
document's values are saved, any value whose field and raw reading match a
remembered fix exactly takes the corrected text and is labelled verified with
the reason ``learned_correction``. Nothing looser than an exact match is
used, so a fix is never applied by guesswork. The raw reading is kept as it
was, so the change can always be seen. A value of the document card is never
changed: the model chose its field, so it stays needs review (council fix 4).
Nor is a value read by the local reader (a page the model did not read, or ran out of time
on) or whose spot the reader never pinned: a remembered fix never makes those verified
(council 16 Oct 2026; CLAUDE.md "never guess").

Time O(v + c) for v values and c loaded corrections (one dict lookup each).
"""

from collections.abc import Iterable
from dataclasses import dataclass, replace

from kaagaz.extraction.labels import BOX_UNANCHORED, LOCAL_READER
from kaagaz.extraction.values import CARD_RECORD, VERIFIED, ExtractedValue

# Values a remembered fix leaves as they are (needs review, with their reason).
_NEVER_FIXED = frozenset({LOCAL_READER, BOX_UNANCHORED})


@dataclass(frozen=True, slots=True)
class LearnedCorrection:
    field: str
    raw_value: str
    corrected_value: str


def apply_learned(
    values: Iterable[ExtractedValue], learned: Iterable[LearnedCorrection]
) -> list[ExtractedValue]:
    fixes = {(item.field, item.raw_value): item.corrected_value for item in learned}
    applied = []
    for value in values:
        corrected = fixes.get((value.field, value.value_raw))
        if corrected is None or value.record_key == CARD_RECORD or value.label_reason in _NEVER_FIXED:
            applied.append(value)
        else:
            applied.append(
                replace(value, value_text=corrected, label=VERIFIED, label_reason="learned_correction")
            )
    return applied
