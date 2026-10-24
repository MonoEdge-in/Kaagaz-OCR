"""Ask speed (24 Oct 2026) on a real PostgreSQL: the fused search (CX6's aspects, the
translation) runs each text's lists in its own transaction, SEARCH_FAN_OUT at once, and ranks
as when they ran one after another; a first question's early search (service step 19) opens
transactions of its own. ``watched``: no statement runs on a thread other than the one that
opened its transaction. The same steps with fakes are in test_ask_speed.py."""

import logging
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

import pytest
from sqlalchemy import Connection

from kaagaz.answering import rewrite
from kaagaz.answering.evidence import PgRetriever
from kaagaz.answering.service import AnswerService
from kaagaz.db.engine import Database
from kaagaz.search import hybrid
from kaagaz.search.scope import EVERYTHING
from tests.db_support import new_customer
from tests.settings_factory import make_settings
from tests.test_answering_clarify import plan
from tests.test_answering_golden import NOW, evidence_blocks
from tests.test_search_db import RECIPE, add_document, add_pieces, axis

pytestmark = pytest.mark.db

PIECES = [
    ("Leave policy: how many leaves you get", axis(5)),
    ("Casual leave: 8 days a year", axis(0)),
    ("Holidays: 12 paid holidays a year", axis(1)),
    ("Maternity leave: 26 weeks", axis(2)),
    ("Salary: Rs. 50,000 a month", axis(3)),
    ("Notice period: 30 days", axis(4)),
]
ASPECTS = [
    ("holidays", axis(1)),
    ("casual leave", axis(0)),
    ("maternity leave", axis(2)),
    ("salary", axis(3)),
]


class OneThread:
    """A connection that notes each statement run on a thread other than the one that opened it."""

    def __init__(self, conn: Connection, crossed: list[int]) -> None:
        self._conn, self._crossed, self._owner = conn, crossed, threading.get_ident()

    def execute(self, *args: Any, **kwargs: Any) -> Any:
        if threading.get_ident() != self._owner:
            self._crossed.append(threading.get_ident())
        return self._conn.execute(*args, **kwargs)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._conn, name)


def watched(db: Database, monkeypatch: pytest.MonkeyPatch) -> tuple[list[int], list[int]]:
    """``db``'s customer and user transactions for this test: the thread that opened each, and
    the threads of statements run off it (none expected)."""
    opened: list[int] = []
    crossed: list[int] = []

    def watch(name: str) -> None:
        real = getattr(db, name)

        @contextmanager
        def each(*args: str) -> Iterator[Any]:
            with real(*args) as conn:
                opened.append(threading.get_ident())
                yield OneThread(conn, crossed)

        monkeypatch.setattr(db, name, each)

    watch("customer")
    watch("user")
    return opened, crossed


def _letter(db: Database) -> str:
    customer, _ = new_customer(db)
    add_pieces(db, customer, add_document(db, customer, name="offer-letter.pdf"), list(PIECES))
    return customer


def test_aspects_searched_at_once_rank_as_one_after_another(
    db: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    customer = _letter(db)
    retriever = PgRetriever(db, recipe=RECIPE, ef_search=100, neighbours=0)
    opened, crossed = watched(db, monkeypatch)

    at_once = retriever.search_translated(customer, "leave", axis(5), ASPECTS, 10, EVERYTHING)
    threads = set(opened)
    monkeypatch.setattr(hybrid, "SEARCH_FAN_OUT", 1)
    opened.clear()
    in_turn = retriever.search_translated(customer, "leave", axis(5), ASPECTS, 10, EVERYTHING)

    # Five texts: more than one thread at once, each in transactions of its own, then the details.
    assert len(threads) > 2 and len(set(opened)) == 2 and crossed == []
    assert len(at_once.hits) == len(PIECES)
    assert list(at_once.hits) == list(in_turn.hits)  # each SearchHit whole: its place and every score
    assert at_once.original == in_turn.original


class Model:
    """Embeds every text on one axis; the rewrite call gives the question as asked with no aspects
    and an empty plan; the draft quotes its first evidence block."""

    chat_model, embed_model = "fake-chat", "fake-embed"

    def embed(self, texts: list[str]) -> list[list[float]]:
        return [axis(0) for _ in texts]

    def chat_json(self, *, system: str, user: str, schema: Any, **_: Any) -> dict[str, Any]:
        if system == rewrite.SYSTEM_PROMPT:
            question = user.split("<question>\n", 1)[1].split("\n</question>", 1)[0]
            return {"question": question, "aspects": [], "plan": plan()}
        n, text = next(iter(evidence_blocks(user).items()))
        return {"status": "answered", "sentences": [{"text": text, "evidence": [n]}]}


def test_the_early_search_opens_its_own_transactions_on_its_own_thread(
    db: Database, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    customer = _letter(db)
    settings = make_settings(model_embed="test-embed", answer_whole_doc_tokens=0)
    answers = AnswerService(db=db, model=Model(), settings=settings, clock=lambda: NOW)
    opened, crossed = watched(db, monkeypatch)
    caplog.set_level(logging.INFO, logger="kaagaz.answering.timing")

    answer = answers.answer(customer, "How many days of casual leave?")

    assert answer.status == "answered" and [c.ref_id for c in answer.citations]
    assert threading.get_ident() in opened and len(set(opened)) > 1 and crossed == []
    (line,) = [r.getMessage() for r in caplog.records if r.name == "kaagaz.answering.timing"]
    assert " overlap=1 " in line  # the early search was the answer's
