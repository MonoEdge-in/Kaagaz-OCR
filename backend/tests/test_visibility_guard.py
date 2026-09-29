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

from kaagaz.answering import fixed_queries
from kaagaz.db import admin, listing, review
from kaagaz.db.visibility import LIVE, LIVE_CURRENT, TRASHED
from kaagaz.search import hybrid

MODULES: tuple[ModuleType, ...] = (hybrid, fixed_queries, review, admin, listing)

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
    # Review and the admin numbers: not in Trash (every version counts).
    (review, "_QUEUE"): LIVE,
    (review, "_COUNT"): LIVE,
    (review, "_LOCK_VALUE"): LIVE,
    (admin, "_DOCUMENTS_BY_STATUS"): LIVE,
    (admin, "_COUNTS"): LIVE,
    # The document list: out of Trash by default, or only Trash (trashed=true).
    (listing, "_LIST"): LIVE,
    (listing, "_TRASH_LIST"): TRASHED,
}

# Statements that may see a document in Trash, and why.
BY_ID: dict[tuple[ModuleType, str], str] = {
    (review, "_ITEM"): "get_item reads back one value by id, just after lock_value (filtered) locked it",
    (
        listing,
        "_SELECT",
    ): "get_document: the detail, the file and the pages open any version, in Trash or not",
    (listing, "_VERSIONS"): "family_versions: the detail lists every version of its family, in Trash or not",
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
        listed = set(FILTERED) | set(BY_ID)
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
