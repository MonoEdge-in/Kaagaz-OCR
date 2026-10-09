"""The PRD 3 exit test in code (PRD 3 §2, steps 1 to 5; test-engineer amendment): the
exit files of tests/exit_fixtures.py read by the real pipeline on a real PostgreSQL
(``test_exit_files_db.read_files``), embedded, then asked through the whole API with
the real AnswerService. The model is ``ExitModel``: it embeds a text as its hashed
words, and rewrites and drafts in separate calls; a draft only cites the evidence
holding the words it looks for, as a model that copies correctly would.
"""

import re
import zlib
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import pytest
from sqlalchemy import text

from kaagaz.answering import draft, rewrite
from kaagaz.answering.service import AnswerService
from kaagaz.api.routers.chat import NOT_FOUND_TEXT
from kaagaz.db.engine import Database
from kaagaz.search.indexing import embed_pieces
from tests.exit_fixtures import PROFIT_VALUE
from tests.settings_factory import make_settings
from tests.test_api_support import CSRF, Api, Signed
from tests.test_exit_files_db import read_files

pytestmark = pytest.mark.db

# PRD 3's search: no whole documents or aspects (PRD 5 CX5, CX6).
SETTINGS = make_settings(answer_whole_doc_tokens=0, query_aspects_max=0)
NOW = datetime(2026, 10, 2, 6, 30, tzinfo=UTC)
FOLLOW_UP = "What were the expenses in September?"
BLOCK = re.compile(r'<evidence id="(\d+)"[^>]*>\n(.*?)\n</evidence>', re.DOTALL)
QUESTION = re.compile(r"<question>\n(.*?)\n</question>", re.DOTALL)
# A word of the question -> the text the draft must find, and the sentence it writes.
DRAFTS = {
    "profit": (PROFIT_VALUE, f"The profit in September was {PROFIT_VALUE}."),
    "expenses": ("Expenses of the month", "The expenses of the month Sept were checked by the accountant."),
    "Patil": ("Patil Garments", "The bill is to Patil Garments."),
}


class ExitModel:
    chat_model, embed_model = "fake-chat", SETTINGS.model_embed

    def __init__(self) -> None:
        self.rewrites: list[str] = []

    def embed(self, texts: list[str]) -> list[list[float]]:
        """Each word adds 1 at its hash, so texts that share words point the same way."""
        vectors = []
        for each in texts:
            vector = [0.0] * SETTINGS.embedding_dim
            for word in re.findall(r"\w+", each.casefold()) or ["empty"]:
                vector[zlib.crc32(word.encode()) % len(vector)] += 1.0
            vectors.append(vector)
        return vectors

    def chat_json(
        self, *, system: str, user: str, schema: Any, max_tokens: int | None = None
    ) -> dict[str, Any]:
        asked = QUESTION.findall(user)[0]
        if system == rewrite.SYSTEM_PROMPT:
            self.rewrites.append(user)
            return {"question": FOLLOW_UP if "profit" in user else asked}
        assert system == draft.SYSTEM_PROMPT
        wanted = next((cited for word, cited in DRAFTS.items() if word in asked), None)
        for n, body in BLOCK.findall(user):
            if wanted is not None and wanted[0] in body:
                return {"status": "answered", "sentences": [{"text": wanted[1], "evidence": [int(n)]}]}
        return {"status": "not_found", "sentences": []}

    def read_image(self, *, png: bytes, instruction: str) -> str:
        return ""


@dataclass
class World:
    api: Api
    signed: Signed
    ids: dict[str, str]
    model: ExitModel

    def ask(self, question: str, **body: Any) -> dict[str, Any]:
        reply = self.signed.client.post("/api/chat", json={"question": question, **body}, headers=CSRF)
        assert reply.status_code == 200, reply.text
        return dict(reply.json())


@pytest.fixture(scope="module")
def world(db: Database) -> World:
    files, model = read_files(db, model=True), ExitModel()
    for document in files.ids.values():
        embed_pieces(db, files.customer, document, model)
    answers = AnswerService(db=db, model=model, settings=SETTINGS, clock=lambda: NOW)
    api = Api(db, settings=SETTINGS, model=model, answers=answers)
    return World(api, api.sign_in(files.customer), files.ids, model)


def seen(answer: dict[str, Any]) -> set[str]:
    """The documents an answer cites or names as closest."""
    return {c["document_id"] for c in answer["citations"]} | {d["id"] for d in answer["closest_documents"]}


def test_step_1_shinde_finds_the_marathi_land_record_with_its_spot(world: World) -> None:
    reply = world.signed.client.get("/api/search", params={"q": "shinde"})

    found = {document["id"]: document["best"] for document in reply.json()["documents"]}
    best = found[world.ids["marathi_land_record"]]
    assert "शिंदे" in best["snippet"]
    assert best["page_from"] == 1 and best["regions"] and {r["page"] for r in best["regions"]} == {1}


def test_steps_2_and_3_profit_cites_pages_15_to_16_and_the_follow_up_is_rewritten(world: World) -> None:
    first = world.ask("What was the profit in September?")
    answer = first["answer"]["answer"]

    assert [s["text"] for s in answer["sentences"]] == [f"The profit in September was {PROFIT_VALUE}."]
    (citation,) = answer["citations"]
    assert citation["document_id"] == world.ids["report"]
    assert (citation["page_no"], citation["page_from"], citation["page_to"]) == (15, 15, 16)
    assert {region["page"] for region in citation["regions"]} == {15, 16}
    assert answer["rewritten_question"] is None

    second = world.ask("And the expenses?", conversation_id=first["conversation_id"])

    follow = second["answer"]["answer"]
    assert (follow["rewritten_question"], follow["normalized_question"]) == (FOLLOW_UP, FOLLOW_UP)
    assert (follow["status"], [s["text"] for s in follow["sentences"]]) == (
        "answered",
        [DRAFTS["expenses"][1]],
    )
    assert f"Answer: The profit in September was {PROFIT_VALUE}." in world.model.rewrites[-1]
    with world.api.db.customer(world.signed.customer_id) as conn:
        saved = conn.execute(
            text("SELECT rewritten_question FROM messages WHERE customer_id = :c AND id = :m"),
            {"c": world.signed.customer_id, "m": second["question"]["id"]},
        ).scalar_one()
    assert saved == FOLLOW_UP


def test_step_4_a_folder_scope_cites_only_that_folder_and_its_total_too(world: World) -> None:
    customer, user = world.signed.customer_id, world.signed.user_id
    suppliers = world.api.add_folder(customer, user, "Suppliers")
    year = world.api.add_folder(customer, user, "2026", suppliers)
    with world.api.db.customer(customer) as conn:
        conn.execute(
            text("UPDATE documents SET folder_id = :f WHERE customer_id = :c AND id = :d"),
            {"f": year, "c": customer, "d": world.ids["textile_invoice"]},
        )
    scope = {"folder_id": suppliers, "include_subfolders": True}

    for question in ("What is the total amount?", "Which bill is to Patil Garments?"):
        inside = world.ask(question, scope=scope)["answer"]["answer"]
        everywhere = world.ask(question)["answer"]["answer"]  # the positive control

        assert (inside["status"], seen(inside)) == ("answered", {world.ids["textile_invoice"]}), question
        assert seen(everywhere) - seen(inside) or everywhere["sentences"] != inside["sentences"], question
    total = world.ask("What is the total amount?", scope=scope)["answer"]["answer"]
    assert total["route"] == "fixed_query"
    assert [s["text"] for s in total["sentences"]] == ["The total amount was 11,800, from 1 record."]


def test_step_5_the_ceos_salary_is_not_found_and_names_the_closest_documents(world: World) -> None:
    first = world.ask("What is the CEO's salary?")
    answer = first["answer"]["answer"]

    assert (answer["status"], answer["citations"]) == ("not_found", [])
    assert answer["closest_documents"] and all(d["display_name"] for d in answer["closest_documents"])

    world.ask("And the CFO's salary?", conversation_id=first["conversation_id"])  # edge case 3
    prompt = world.model.rewrites[-1]
    assert "Question: What is the CEO" in prompt
    assert NOT_FOUND_TEXT not in prompt and "Answer:" not in prompt
