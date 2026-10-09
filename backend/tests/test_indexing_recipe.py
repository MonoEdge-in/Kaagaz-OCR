"""The embedding recipe and question format for each MODEL_EMBED (PRD 3 V3, PRD 5 M5)."""

import pytest

from kaagaz.search.indexing import query_input, recipe_for

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
