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

Time O(v + c) for v values and c loaded corrections (one dict lookup each).
"""

from collections.abc import Iterable
from dataclasses import dataclass, replace

from kaagaz.extraction.values import CARD_RECORD, VERIFIED, ExtractedValue


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
        if corrected is None or value.record_key == CARD_RECORD:
            applied.append(value)
        else:
            applied.append(
                replace(value, value_text=corrected, label=VERIFIED, label_reason="learned_correction")
            )
    return applied
