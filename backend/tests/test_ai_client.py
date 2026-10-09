"""The model client's helpers: the token estimates, the input budget, the schema check,
and ``build_model_client`` with the model off. The client itself is tested against a fake
OpenRouter API (``tests/test_openrouter_client.py``)."""

from typing import Any

from kaagaz.ai.client import (
    build_model_client,
    estimate_tokens,
    estimate_tokens_cautiously,
    input_token_budget,
    schema_problem,
)
from kaagaz.settings import CHAT_OVERHEAD_TOKENS
from kaagaz.worker_main import worker_model
from tests.settings_factory import make_settings

SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "status": {"type": "string", "enum": ["answered", "not_found"]},
        "sentences": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "text": {"type": "string"},
                    "evidence": {"type": "array", "items": {"type": "integer"}},
                },
                "required": ["text", "evidence"],
            },
        },
    },
    "required": ["status", "sentences"],
}
GOOD = {"status": "answered", "sentences": [{"text": "Total is 5,000.", "evidence": [1]}]}


def test_build_model_client_is_none_without_ai() -> None:
    assert build_model_client(make_settings(model_enabled=False)) is None
    assert worker_model(make_settings(model_enabled=False)) is None


def test_the_token_estimate_is_ascii_over_4_and_other_characters_over_1_5() -> None:
    assert estimate_tokens("") == 0
    assert estimate_tokens("abcd") == 1
    assert estimate_tokens("abcde") == 2  # 1.25, rounded up
    assert estimate_tokens("पाटील") == 4  # 5 code points / 1.5 = 3.3
    assert estimate_tokens("ab पा") == 3  # 3 / 4 + 2 / 1.5 = 2.08


def test_the_cautious_estimate_counts_a_letter_of_any_other_script_as_a_whole_token() -> None:
    """PR C (PROVISIONAL), for the answer's evidence: letters and vowel signs of any script but
    ASCII are one token each; ASCII stays a quarter and digits and signs two thirds."""
    assert estimate_tokens_cautiously("") == 0
    assert estimate_tokens_cautiously("abcde") == 2  # as estimate_tokens
    assert (
        estimate_tokens_cautiously("\u092a\u093e\u091f\u0940\u0932") == 5
    )  # 3 letters and 2 vowel signs (it was 4)
    assert estimate_tokens_cautiously("ab \u092a\u093e") == 3  # 3 / 4 + 2 = 2.75
    for word in (
        "\u0ab0\u0aae\u0ac7\u0ab6",
        "\u0bb0\u0bbe\u0bae\u0ba9\u0bcd",
        "\u0645\u062d\u0645\u062f",
    ):  # Gujarati, Tamil, Arabic: the same rule
        assert estimate_tokens_cautiously(word) == len(word)
    assert (
        estimate_tokens_cautiously("\u0967\u0968\u0969 \u20b9") == 3
    )  # 3 Devanagari digits and the rupee sign: 35 / 12
    mixed = "Grand Total \u0915\u0941\u0932 \u0930\u0915\u092e \u0967,\u0967\u096e \u0ab0\u0aae\u0ac7\u0ab6"
    assert estimate_tokens_cautiously(mixed) > estimate_tokens(mixed)


def test_the_input_budget_leaves_room_for_a_reply_its_repair_and_the_system_prompt() -> None:
    system = "x" * 400  # 100 tokens
    budget = input_token_budget(num_ctx=8192, max_tokens=1024, system=system)
    assert budget == 8192 - 2 * 1024 - 100 - CHAT_OVERHEAD_TOKENS
    assert input_token_budget(num_ctx=1000, max_tokens=600, system="") == 0


def test_schema_problem_checks_types_strictly() -> None:
    assert schema_problem(GOOD, SCHEMA) is None
    assert schema_problem(
        {"status": "answered", "sentences": [{"text": "x", "evidence": [True]}]}, SCHEMA
    ) == ("$.sentences[0].evidence[0] must be integer")
    assert schema_problem({"sentences": []}, SCHEMA) == "$.status is missing"
    assert schema_problem({"n": 1.5}, {"properties": {"n": {"type": ["integer", "null"]}}}) == (
        "$.n must be integer or null"
    )
