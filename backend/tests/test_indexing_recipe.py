"""The embedding recipe and question format for each MODEL_EMBED (PRD 3 V3, PRD 5 M5)."""

from typing import Any

import pytest

from kaagaz.search.indexing import embed_question, query_input, recipe_for, recipe_tag, section_input

QWEN_FORMAT = (
    "Instruct: Given a question about business documents, retrieve passages that answer it"
    "\nQuery:how many leaves do I get?"
)


@pytest.mark.parametrize("model", ["google/gemini-embedding-2", "google/gemini-embedding-001"])
def test_a_gemini_embedding_model_gets_the_plain_question_with_recipe_gem1(model: str) -> None:
    assert recipe_for(model) == "gem1"
    assert query_input("how many leaves do I get?", model) == "how many leaves do I get?"


@pytest.mark.parametrize("model", ["qwen3-embedding:0.6b", "google/embeddinggemma", "gemini-embedding-2", ""])
def test_any_other_model_keeps_the_model_card_format_with_recipe_ctx1(model: str) -> None:
    assert recipe_for(model) == "ctx1"
    assert query_input("how many leaves do I get?", model) == QWEN_FORMAT


def test_a_caller_that_passes_no_model_gets_todays_format() -> None:
    assert query_input("how many leaves do I get?") == QWEN_FORMAT


def test_m5_the_recipe_tag_names_each_models_recipe() -> None:
    assert recipe_tag("google/gemini-embedding-2") == "google/gemini-embedding-2#gem1"
    assert recipe_tag("qwen3-embedding:0.6b") == "qwen3-embedding:0.6b#ctx1"


class Recorder:
    """A model that records what it embeds."""

    chat_model = "unused"

    def __init__(self, embed_model: str) -> None:
        self.embed_model = embed_model
        self.texts: list[str] = []

    def embed(self, texts: list[str]) -> list[list[float]]:
        self.texts += texts
        return [[1.0]] * len(texts)

    def chat_json(
        self, *, system: str, user: str, schema: dict[str, Any], max_tokens: int | None = None
    ) -> dict[str, Any]:
        raise AssertionError("never chats")


@pytest.mark.parametrize(
    ("model", "sent"), [("google/gemini-embedding-2", "how many leaves?"), ("qwen3-embedding:0.6b", None)]
)
def test_m5_a_question_is_embedded_in_its_models_format(model: str, sent: str | None) -> None:
    recorder = Recorder(model)
    assert embed_question(recorder, "how many leaves?") == [1.0]
    assert recorder.texts == [sent or query_input("how many leaves?")]
    assert recorder.texts[0].startswith("Instruct:") is (sent is None)


def test_cx2_a_section_is_embedded_as_its_path_then_its_summary() -> None:
    assert (
        section_input(("Offer", "Leave"), "Twelve holidays.") == "Section: Offer > Leave\n\nTwelve holidays."
    )
    assert section_input((), "Twelve holidays.") == "Twelve holidays."
