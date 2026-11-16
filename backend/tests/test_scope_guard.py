"""Every statement that reads documents for search, chat or a number carries the whole
scope (PRD 3 H2): each s_* parameter as often as in hybrid._WORD, once per ``documents d``
it joins, like the visibility guard (tests/test_visibility_guard.py)."""

import re
from types import ModuleType

import pytest

from kaagaz.answering import exhaustive, fixed_queries, unchecked
from kaagaz.search import hybrid
from kaagaz.search.scope import EVERYTHING

SCOPED = [
    *(
        (hybrid, name)
        for name in ("_WORD", "_FUZZY", "_MEANING", "_MEANING_EXACT", "_VISIBLE_VECTORS", "_NEIGHBOURS")
    ),
    *(
        (fixed_queries, name)
        for name in ("_AVAILABLE_FIELDS", "_TOP_GROUP", "_FIGURES", "_EXTREME_VALUE", "_COUNT_DOCUMENTS")
    ),
    (fixed_queries, "_VALUE_REFS"),
    (unchecked, "_CARD_DOCUMENTS"),
    (exhaustive, "_DOCUMENTS"),  # every document holding a word (16 Oct 2026); _PIECES takes its ids
    # #617 part 1: a scope's documents, and the pieces on an asked identifier's pages.
    (hybrid, "_SCOPE_DOCUMENTS"),
    (hybrid, "_IDENTIFIED"),
]


@pytest.mark.parametrize(("module", "name"), SCOPED, ids=[name for _, name in SCOPED])
def test_each_scoped_statement_has_every_scope_predicate_per_documents_join(
    module: ModuleType, name: str
) -> None:
    sql, one = getattr(module, name).text, hybrid._WORD.text
    joins = len(re.findall(r"\b(?:visible_)?documents d\b", sql))  # #602: or the view
    assert joins > 0
    for param in EVERYTHING.params():
        assert len(re.findall(rf":{param}\b", sql)) == joins * len(re.findall(rf":{param}\b", one)), param
