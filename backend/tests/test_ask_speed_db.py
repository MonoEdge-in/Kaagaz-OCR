"""Ask speed (24 Oct 2026) on a real PostgreSQL: the fused search (CX6's aspects, the
translation) runs each text's lists in its own transaction, SEARCH_FAN_OUT at once, and ranks
as when they ran one after another; a first question's early search (service step 19) opens
transactions of its own. ``watched``: no statement runs on a thread other than the one that
opened its transaction. The same steps with fakes are in test_ask_speed.py."""

import dataclasses
import json
import logging
import threading
from collections.abc import Callable, Iterator
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

    at_once = retriever.search_translated(customer, "leave", axis(5), ASPECTS, 10, EVERYTHING, user_id=None)
    threads = set(opened)
    monkeypatch.setattr(hybrid, "SEARCH_FAN_OUT", 1)
    opened.clear()
    in_turn = retriever.search_translated(customer, "leave", axis(5), ASPECTS, 10, EVERYTHING, user_id=None)

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


# #535 (Ask speed, PR 2): with aspects, the early search's lists for the question as asked are
# fused with the aspects' as the fused search fuses them, so only the aspects are embedded and
# searched: the same evidence, one embedded text and one search of the question fewer.
AXES = {"holidays": axis(1), "casual leave": axis(0), "maternity leave": axis(2), "salary": axis(3)}
QUESTIONS = [
    ("How many leaves do I get?", ["holidays", "casual leave", "maternity leave"]),
    ("What is my salary and notice period?", ["salary", "notice period"]),
    ("Tell me about casual leave", ["casual leave"]),
    ("What are the leave rules?", ["holidays", "casual leave", "maternity leave", "salary"]),
]


class AspectModel(Model):
    """``Model`` whose rewrite call lists ``aspects``; each aspect is embedded on its piece's axis
    (any other text on the leave policy's); ``embedded`` and ``evidence`` keep each embed call's
    texts and each draft's evidence blocks, in the prompt's order."""

    def __init__(self, aspects: list[str]) -> None:
        self.aspects = aspects
        self.embedded: list[list[str]] = []
        self.evidence: list[list[tuple[int, str]]] = []

    def embed(self, texts: list[str]) -> list[list[float]]:
        self.embedded.append(list(texts))
        return [AXES.get(text, axis(5)) for text in texts]

    def chat_json(self, *, system: str, user: str, schema: Any, **_: Any) -> dict[str, Any]:
        if system == rewrite.SYSTEM_PROMPT:
            return {**super().chat_json(system=system, user=user, schema=schema), "aspects": self.aspects}
        self.evidence.append(list(evidence_blocks(user).items()))
        return super().chat_json(system=system, user=user, schema=schema)


@pytest.mark.parametrize(("question", "aspects"), QUESTIONS)
def test_aspects_reuse_the_early_search_with_the_same_evidence_and_fewer_calls(
    db: Database, monkeypatch: pytest.MonkeyPatch, question: str, aspects: list[str]
) -> None:
    customer = _letter(db)
    settings = make_settings(model_embed="test-embed", answer_whole_doc_tokens=0)
    lists: list[str] = []
    real_lists = hybrid._lists

    def counted(conn: Any, customer_id: str, text_: str, *args: Any) -> Any:
        lists.append(text_)
        return real_lists(conn, customer_id, text_, *args)

    monkeypatch.setattr(hybrid, "_lists", counted)

    def ask() -> tuple[str, AspectModel, int]:
        model = AspectModel(aspects)
        lists.clear()
        answer = AnswerService(db=db, model=model, settings=settings, clock=lambda: NOW).answer(
            customer, question
        )
        assert answer.status == "answered"
        return json.dumps(answer.to_json(), sort_keys=True), model, len(lists)

    reused, model, searched = ask()  # this change

    real_early = AnswerService._early
    monkeypatch.setattr(  # before it: the early search's lists dropped, so the question searched again
        AnswerService, "_early", lambda self, *args: dataclasses.replace(real_early(self, *args), lists=None)
    )
    before, old, searched_before = ask()

    def one_after_another(self: AnswerService, rewriting: Callable[[], Any], *_: Any) -> tuple[Any, None]:
        return rewriting(), None

    monkeypatch.setattr(AnswerService, "_overlapped", one_after_another)
    alone, sequential, searched_alone = ask()  # no early search at all

    assert model.evidence == old.evidence == sequential.evidence  # the same ids, texts and order
    assert reused == before == alone  # the same answer, byte for byte
    assert searched == searched_before - 1 == searched_alone == 1 + len(aspects)
    assert [len(texts) for texts in model.embedded] == [1, len(aspects)]  # the question once
    assert [len(texts) for texts in old.embedded] == [1, 1 + len(aspects)]
