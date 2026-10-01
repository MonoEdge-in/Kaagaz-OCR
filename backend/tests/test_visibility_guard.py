"""Every statement that joins ``documents d`` for search, chat, review, the admin
numbers or the document list carries its visibility fragment (PRD 1 F5 and F6, plan 9.7).

The lists are explicit on purpose. A new statement that joins ``documents d`` in
one of these modules fails the last test until it is added here: to FILTERED
with its fragment, or to BY_ID with the reason it may see a document in Trash.

The fragment must be written literally in each statement, once for every
``documents d`` it joins, so every CTE of a fixed query filters on its own.
"""

import re
import unittest
from types import ModuleType

from sqlalchemy import TextClause

from kaagaz.answering import fixed_queries, unchecked
from kaagaz.db import admin, chat, listing, pipeline_store, review
from kaagaz.db.visibility import LISTED, LIVE, LIVE_CURRENT, TRASHED_LISTED
from kaagaz.search import hybrid

MODULES: tuple[ModuleType, ...] = (
    hybrid,
    fixed_queries,
    unchecked,
    review,
    admin,
    listing,
    pipeline_store,
    chat,
)

# (module, constant name) -> the fragment it must carry.
FILTERED: dict[tuple[ModuleType, str], str] = {
    # Search and chat: the current version of a read document, not in Trash.
    (hybrid, "_WORD"): LIVE_CURRENT,
    (hybrid, "_FUZZY"): LIVE_CURRENT,
    (hybrid, "_MEANING"): LIVE_CURRENT,
    (hybrid, "_DETAILS"): LIVE_CURRENT,
    # The numbers in answers: the same, in every CTE.
    (fixed_queries, "_AVAILABLE_FIELDS"): LIVE_CURRENT,
    (fixed_queries, "_TOP_GROUP"): LIVE_CURRENT,
    (fixed_queries, "_TOTAL"): LIVE_CURRENT,
    (fixed_queries, "_EXTREME_VALUE"): LIVE_CURRENT,
    (fixed_queries, "_COUNT_DOCUMENTS"): LIVE_CURRENT,
    (fixed_queries, "_VALUE_REFS"): LIVE_CURRENT,
    # The documents an unchecked figure points to (decided 30 Sep 2026): the same.
    (unchecked, "_CARD_DOCUMENTS"): LIVE_CURRENT,
    # Review: the values of the version answers use, and nothing else.
    (review, "_QUEUE"): LIVE_CURRENT,
    (review, "_COUNT"): LIVE_CURRENT,
    (review, "_LOCK_VALUE"): LIVE_CURRENT,
    # The admin numbers: each family once, by its current version; open review
    # items as the queue shows them.
    (admin, "_DOCUMENTS_BY_STATUS"): LISTED,
    (admin, "_COUNTS"): LIVE_CURRENT,
    # The document list: each family's current version, out of Trash by default,
    # or only Trash (trashed=true).
    (listing, "_LIST"): LISTED,
    (listing, "_TRASH_LIST"): TRASHED_LISTED,
    # Links (PRD 2 D5): only to a current, ready document out of Trash, never an older
    # version or one still being read (replacing AR5's any state, PROVISIONAL).
    (pipeline_store, "_LINK_TARGETS"): LIVE_CURRENT,
    (pipeline_store, "_LIVE_TARGETS"): LIVE_CURRENT,
    # A citation's page, for its preview: out of Trash, any version, as an older
    # version's citation keeps its text and link (views.answer_with_source_states).
    (chat, "_CITED_PAGES"): LIVE,
}

# Statements that count every version on purpose, and why.
ALL_VERSIONS: dict[tuple[ModuleType, str], str] = {
    (admin, "_KEPT"): "the admin numbers show how many older versions and Trash families are kept",
}

# Statements that may see a document in Trash, and why.
BY_ID: dict[tuple[ModuleType, str], str] = {
    (review, "_ITEM"): "get_item reads back one value by id, just after lock_value (filtered) locked it",
    (
        listing,
        "_SELECT",
    ): "get_document: the detail, the file and the pages open any version, in Trash or not",
    (listing, "_VERSIONS"): "family_versions: the detail lists every version of its family, in Trash or not",
    (
        pipeline_store,
        "_LOCK_LINKED",
    ): "locks the link targets and old partners by id in one order; filters nothing",
}

_JOINS_DOCUMENTS = re.compile(r"\bdocuments d\b")


def _sql(module: ModuleType, name: str) -> str:
    constant = getattr(module, name)
    if isinstance(constant, TextClause):
        return constant.text
    assert isinstance(constant, str), f"{module.__name__}.{name} is not SQL"
    return constant


class VisibilityGuardTest(unittest.TestCase):
    def test_each_filtered_statement_has_its_fragment_once_per_documents_join(self) -> None:
        for (module, name), fragment in FILTERED.items():
            with self.subTest(statement=f"{module.__name__}.{name}"):
                sql = _sql(module, name)
                joins = len(_JOINS_DOCUMENTS.findall(sql))
                self.assertGreater(joins, 0)
                self.assertEqual(sql.count(fragment), joins)

    def test_the_by_id_statements_have_no_trash_filter(self) -> None:
        for module, name in BY_ID:
            with self.subTest(statement=f"{module.__name__}.{name}"):
                sql = _sql(module, name)
                self.assertTrue(_JOINS_DOCUMENTS.search(sql))
                self.assertNotIn(LIVE, sql)

    def test_every_statement_that_joins_documents_is_listed(self) -> None:
        listed = set(FILTERED) | set(BY_ID) | set(ALL_VERSIONS)
        for module in MODULES:
            with self.subTest(module=module.__name__):
                found = {
                    (module, name)
                    for name, value in vars(module).items()
                    if not name.startswith("__")  # not the module's docstring
                    and isinstance(value, str | TextClause)
                    and _JOINS_DOCUMENTS.search(_sql(module, name))
                }
                self.assertEqual(found, {key for key in listed if key[0] is module})
