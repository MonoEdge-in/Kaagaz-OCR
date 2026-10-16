"""The embedding recipe for each MODEL_EMBED, and the plain question (PRD 3 V3, PRD 5 M5)."""

from typing import Any

import pytest

from kaagaz.search.indexing import embed_question, recipe_for, recipe_tag, section_input


@pytest.mark.parametrize("model", ["google/gemini-embedding-2", "google/gemini-embedding-001"])
def test_a_gemini_embedding_model_has_recipe_gem1(model: str) -> None:
    assert recipe_for(model) == "gem1"
    assert recipe_tag(model) == f"{model}#gem1"


@pytest.mark.parametrize("model", ["local-embed:0.6b", "google/embeddinggemma", "gemini-embedding-2", ""])
def test_any_other_model_name_has_recipe_ctx1_as_old_vectors_do(model: str) -> None:
    assert recipe_for(model) == "ctx1"


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
        self,
        *,
        system: str,
        user: str,
        schema: dict[str, Any],
        max_tokens: int | None = None,
        model: str | None = None,
        thinking: str | None = None,
        provider: str | None = None,
        deadline: float | None = None,
        temperature: float | None = None,
    ) -> dict[str, Any]:
        raise AssertionError("never chats")


def test_m5_a_question_is_embedded_as_plain_text() -> None:
    recorder = Recorder("google/gemini-embedding-2")
    assert embed_question(recorder, "how many leaves?") == [1.0]
    assert recorder.texts == ["how many leaves?"]


def test_cx2_a_section_is_embedded_as_its_path_then_its_summary() -> None:
    assert (
        section_input(("Offer", "Leave"), "Twelve holidays.") == "Section: Offer > Leave\n\nTwelve holidays."
    )
    assert section_input((), "Twelve holidays.") == "Twelve holidays."
