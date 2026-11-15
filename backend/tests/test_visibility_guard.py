"""Every statement that joins ``documents d`` for search, chat, review, the admin
numbers, the document list or the missing-vector pass carries its visibility
fragment (PRD 1 F5 and F6, plan 9.7).

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

from kaagaz.answering import clarify, exhaustive, fixed_queries, hard_to_read, owners, unchecked
from kaagaz.db import admin, listing, models, pipeline_store, review, usage
from kaagaz.db.visibility import LISTED, LIVE, LIVE_CURRENT, TRASHED_LISTED
from kaagaz.search import hybrid, indexing, story_search

MODULES: tuple[ModuleType, ...] = (
    hybrid,
    story_search,
    clarify,
    exhaustive,
    fixed_queries,
    owners,
    unchecked,
    hard_to_read,
    review,
    admin,
    listing,
    pipeline_store,
    indexing,
    models,
    usage,
)

# (module, constant name) -> the fragment it must carry.
FILTERED: dict[tuple[ModuleType, str], str] = {
    # Search and chat: the current version of a read document, not in Trash.
    (hybrid, "_WORD"): LIVE_CURRENT,
    (hybrid, "_SOUND"): LIVE_CURRENT,  # Changed on purpose (#619): the word list's sound rung
    (hybrid, "_FUZZY"): LIVE_CURRENT,
    (hybrid, "_MEANING"): LIVE_CURRENT,
    (hybrid, "_MEANING_EXACT"): LIVE_CURRENT,
    (hybrid, "_DETAILS"): LIVE_CURRENT,
    (hybrid, "_NEIGHBOURS"): LIVE_CURRENT,  # Q4: the pieces next to an answer's hits
    # PRD 5: whole documents (CX5), topic and section pieces (CX6) and cards (M2).
    (hybrid, "_WHOLE_SIZE"): LIVE_CURRENT,
    (hybrid, "_WHOLE"): LIVE_CURRENT,
    (hybrid, "_TOPIC_PIECES"): LIVE_CURRENT,
    (hybrid, "_SECTION_PIECES"): LIVE_CURRENT,  # in the sections' step and the pieces' step
    (hybrid, "_CARDS"): LIVE_CURRENT,
    # Every document holding a word, and its matching pieces (16 Oct 2026): the same.
    (exhaustive, "_DOCUMENTS"): LIVE_CURRENT,
    (exhaustive, "_PIECES"): LIVE_CURRENT,
    # Their shared scope clause, and "Also mentions" (17 Oct 2026): the same.
    (exhaustive, "_IN_SCOPE"): LIVE_CURRENT,
    (exhaustive, "_MENTIONS"): LIVE_CURRENT,
    # Story matches (S3): the family's current version of a story, read and out of Trash.
    (story_search, "_STORIES"): LIVE_CURRENT,
    # Clarifying questions (PRD 6 B1): names and places only of the documents chat may cite.
    (clarify, "_CANDIDATES"): LIVE_CURRENT,
    (clarify, "_NEAR"): LIVE_CURRENT,  # B2: the places of a district
    # The numbers in answers: the same, in every CTE.
    (fixed_queries, "_AVAILABLE_FIELDS"): LIVE_CURRENT,
    (fixed_queries, "_TOP_GROUP"): LIVE_CURRENT,
    (fixed_queries, "_FIGURES"): LIVE_CURRENT,  # PRD 6 C: the records behind a total
    (fixed_queries, "_EXTREME_VALUE"): LIVE_CURRENT,
    (fixed_queries, "_COUNT_DOCUMENTS"): LIVE_CURRENT,
    (fixed_queries, "_COUNT_INVOICES"): LIVE_CURRENT,  # #228: the invoices table's rows
    (fixed_queries, "_VALUE_REFS"): LIVE_CURRENT,
    # Owner and place questions (PRD 6 E): the same, for each key and value they read.
    (owners, "_RESOLVE"): LIVE_CURRENT,
    (owners, "_OWNED"): LIVE_CURRENT,
    (owners, "_KEYED"): LIVE_CURRENT,
    (owners, "_SHARES_AS_PRINTED"): LIVE_CURRENT,
    # Changed on purpose (#239, 27 Oct 2026): every land record of a list question (PRD 6 E).
    (owners, "_EVERY_RECORD"): LIVE_CURRENT,
    # The documents an unchecked figure points to (decided 30 Sep 2026): the same.
    (unchecked, "_CARD_DOCUMENTS"): LIVE_CURRENT,
    # The pages a not-found answer names as not read well (PRD 5 F6): the same.
    (hard_to_read, "_FLAGGED_PAGES"): LIVE_CURRENT,
    # Review queue: current-version values only. The direct resolve lock may also
    # see an older ready version opened in its document viewer.
    (review, "_QUEUE"): LIVE_CURRENT,
    (review, "_COUNT"): LIVE_CURRENT,
    (review, "_QUEUE_FOR_DOCUMENT"): LIVE_CURRENT,
    (review, "_COUNT_FOR_DOCUMENT"): LIVE_CURRENT,
    (review, "_LOCK_VALUE"): LIVE,
    # Changed on purpose (#239, 27 Oct 2026): Verify all's chunks lock what the queue lists.
    (review, "_NEXT_CHUNK"): LIVE_CURRENT,
    (review, "_NEXT_CHUNK_ALL"): LIVE_CURRENT,
    (review, "_NEXT_CHUNK_FOR_DOCUMENT"): LIVE_CURRENT,
    # The admin numbers: each family once, by its current version; open review
    # items as the queue shows them.
    (admin, "_DOCUMENTS_BY_STATUS"): LISTED,
    (admin, "_COUNTS"): LIVE_CURRENT,
    # The failed jobs list (PRD 3 Z1): each family once, by its current version.
    (admin, "_FAILED"): LISTED,
    (admin, "_FAILED_COUNT"): LISTED,
    # The document list: each family's current version, out of Trash by default,
    # or only Trash (trashed=true).
    (listing, "_LIST"): LISTED,
    (listing, "_TRASH_LIST"): TRASHED_LISTED,
    # A batch (PRD 5 Q1) and its size for the upload's cap: the uploader's files out of Trash.
    (listing, "_BATCH"): LIVE,
    (listing, "_BATCH_SIZE"): LIVE,
    # The detail's related documents (PRD 3 Y2): only a current, ready one out of Trash.
    (listing, "_LINKS"): LIVE_CURRENT,
    # Links (PRD 2 D5): only to a current, ready document out of Trash, never an older
    # version or one still being read (replacing AR5's any state, PROVISIONAL).
    (pipeline_store, "_LINK_TARGETS"): LIVE_CURRENT,
    (pipeline_store, "_LIVE_TARGETS"): LIVE_CURRENT,
    # The worker's missing vectors: only pieces search can find, never Trash or an older version.
    (indexing, "_MISSING"): LIVE_CURRENT,
    # Changed on purpose (PRD 1, 0041): the dashboard's department counts, each family once.
    (listing, "_DASHBOARD_DEPARTMENTS"): LISTED,
}

# Statements that count every version on purpose, and why.
ALL_VERSIONS: dict[tuple[ModuleType, str], str] = {
    (admin, "_KEPT"): "the admin numbers show how many older versions and Trash families are kept",
    # Changed on purpose (#239, 27 Oct 2026): #386's numbers per member.
    (admin, "_MEMBERS"): "a member's uploads in a window: each file they uploaded, a version or later"
    " put in Trash included",
    # Changed on purpose (PRD 1, 0041).
    (listing, "_DASHBOARD_TILES"): "the dashboard counts current families, and new versions as versions",
}

# Statements that may see a document in Trash, and why.
BY_ID: dict[tuple[ModuleType, str], str] = {
    (review, "_ITEM"): "get_item reads back one value by id, just after lock_value (filtered) locked it",
    (
        listing,
        "_SELECT",
    ): "get_document: the detail, the file and the pages open any version, in Trash or not",
    (listing, "_VERSIONS"): "family_versions: the detail lists every version of its family, in Trash or not",
    (listing, "_WORD_LINES"): "word_lines: your words on a page of the document the detail opens, as it does",
    (
        pipeline_store,
        "_LOCK_LINKED",
    ): "locks the link targets and old partners by id in one order; filters nothing",
    # Changed on purpose (features 25-27, 15 Oct 2026): 0023's models._USABLE joined documents;
    # resolve_scan now reads the document's choice by id alone (_DOCUMENT, no join to list).
    (
        usage,
        "_REPORT",
    ): "the spend names its documents by id: one in Trash or an older version was still billed",
}

_JOINS_DOCUMENTS = re.compile(r"\bdocuments d\b")
_EXISTS = re.compile(r"\bEXISTS\s*\(")
_CUSTOMER_FILTER = "customer_id = :"


def _bracketed(sql: str, start: int) -> str:
    """The text inside the bracket that opens at ``start``, up to the one that closes it."""
    depth = 0
    for index in range(start, len(sql)):
        depth += {"(": 1, ")": -1}.get(sql[index], 0)
        if depth == 0:
            return sql[start : index + 1]
    raise AssertionError("an unclosed bracket")


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

    def test_each_statement_and_each_exists_in_it_filters_on_the_customer(self) -> None:
        """The second lock next to row level security (A7a): ``customer_id = :`` in every
        listed statement, and again inside each EXISTS, which reads a table of its own."""
        for module, name in set(FILTERED) | set(BY_ID) | set(ALL_VERSIONS):
            with self.subTest(statement=f"{module.__name__}.{name}"):
                sql = _sql(module, name)
                self.assertIn(_CUSTOMER_FILTER, sql)
                for found in _EXISTS.finditer(sql):
                    self.assertIn(_CUSTOMER_FILTER, _bracketed(sql, found.end() - 1))

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
