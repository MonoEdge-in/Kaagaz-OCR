"""Start-up settings for the model budgets (PRD 2 ML amendment 5, architect AR1) and the card (D1)."""

from pathlib import Path

import pytest

from kaagaz.settings import Settings, SettingsError

ENV_EXAMPLE = Path(__file__).resolve().parents[2] / ".env.example"
MODEL_SETTINGS = [
    "MODEL_NUM_CTX",
    "MODEL_CARD_MAX_TOKENS",
    "MODEL_WINDOW_MAX_TOKENS",
    "MODEL_EXTRACT_TIMEOUT_SECONDS",
    "CARD_MAX_CHARS",
]


def shipped(**changes: str | None) -> dict[str, str]:
    """.env.example as a mapping, with usable secrets (both placeholders are too
    short on purpose), then ``changes`` (None removes a setting)."""
    env = {"SESSION_SECRET": "x" * 32, "TOTP_ENCRYPTION_KEY": "y" * 32}
    for line in ENV_EXAMPLE.read_text(encoding="utf-8").splitlines():
        if line.strip() and not line.startswith("#"):
            name, _, value = line.partition("=")
            env.setdefault(name.strip(), value.strip())
    for changed, new_value in changes.items():
        if new_value is None:
            env.pop(changed)
        else:
            env[changed] = new_value
    return env


def test_the_shipped_env_example_parses_with_the_model_budgets() -> None:
    settings = Settings.from_env(shipped())
    budgets = (
        settings.model_num_ctx,
        settings.model_card_max_tokens,
        settings.model_window_max_tokens,
        settings.model_extract_timeout_seconds,
        settings.card_max_chars,
    )
    assert budgets == (8192, 1024, 2048, 300.0, 12000)
    assert settings.queue_lease_seconds == 1200


@pytest.mark.parametrize("name", MODEL_SETTINGS)
def test_a_missing_model_budget_stops_start_up_by_name(name: str) -> None:
    with pytest.raises(SettingsError, match=name):
        Settings.from_env(shipped(**{name: None}))


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("MODEL_NUM_CTX", "0"),
        ("MODEL_NUM_CTX", "8k"),
        ("MODEL_CARD_MAX_TOKENS", "0"),
        ("MODEL_WINDOW_MAX_TOKENS", "-1"),
        ("MODEL_EXTRACT_TIMEOUT_SECONDS", "0"),
        ("MODEL_EXTRACT_TIMEOUT_SECONDS", "-5"),
        ("MODEL_EXTRACT_TIMEOUT_SECONDS", "nan"),
        ("MODEL_EXTRACT_TIMEOUT_SECONDS", "inf"),
        ("MODEL_EXTRACT_TIMEOUT_SECONDS", "ten"),
        ("MODEL_TIMEOUT_SECONDS", "-1"),
        ("MODEL_TIMEOUT_SECONDS", "nan"),
        ("CARD_MAX_CHARS", "0"),
    ],
)
def test_a_bad_model_budget_stops_start_up_by_name(name: str, value: str) -> None:
    with pytest.raises(SettingsError, match=name):
        Settings.from_env(shipped(**{name: value}))


@pytest.mark.parametrize(
    "name", ["MODEL_CARD_MAX_TOKENS", "MODEL_WINDOW_MAX_TOKENS", "MODEL_ANSWER_MAX_TOKENS"]
)
def test_a_reply_budget_must_leave_1024_tokens_for_the_document(name: str) -> None:
    """Council fix 7: after the system prompt (at most 1000 tokens), a reply and its one
    repair retry, and 100 for the chat template, 1024 tokens stay for the document.
    8192 - 2 x 3034 - 1000 - 100 = 1024."""
    assert Settings.from_env(shipped(MODEL_NUM_CTX="8192", **{name: "3034"}))
    with pytest.raises(SettingsError, match=name) as caught:
        Settings.from_env(shipped(MODEL_NUM_CTX="8192", **{name: "3035"}))
    assert "MODEL_NUM_CTX" in str(caught.value)


def test_a_4096_window_with_a_2047_token_reply_is_refused() -> None:
    """It passes "under half the window", but leaves the document 2 tokens."""
    with pytest.raises(SettingsError, match="MODEL_CARD_MAX_TOKENS"):
        Settings.from_env(
            shipped(MODEL_NUM_CTX="4096", MODEL_CARD_MAX_TOKENS="2047", MODEL_WINDOW_MAX_TOKENS="100")
        )


def test_a_card_call_and_its_repair_must_end_well_inside_the_lease() -> None:
    """The longest step without a heartbeat after OCR is one chat_json: a call and
    its repair retry. With less than a minute to spare, plus the 15 s the worker may
    wait to renew a lease, a second worker could start the same document (AR1)."""
    fits = Settings.from_env(shipped(MODEL_EXTRACT_TIMEOUT_SECONDS="562.5", QUEUE_LEASE_SECONDS="1200"))
    assert fits.model_extract_timeout_seconds == 562.5
    for timeout, lease in (("563", "1200"), ("300", "600")):
        with pytest.raises(SettingsError, match="MODEL_EXTRACT_TIMEOUT_SECONDS") as caught:
            # OCR_TIMEOUT_SECONDS fits both leases, so only the model check can refuse.
            shipped_env = shipped(
                MODEL_EXTRACT_TIMEOUT_SECONDS=timeout, QUEUE_LEASE_SECONDS=lease, OCR_TIMEOUT_SECONDS="300"
            )
            Settings.from_env(shipped_env)
        assert "QUEUE_LEASE_SECONDS" in str(caught.value)


def test_the_answer_reply_budget_is_a_start_up_setting() -> None:
    """MODEL_ANSWER_MAX_TOKENS ships as 512 (PROVISIONAL), and a missing one stops start-up by name."""
    env = shipped()
    assert env.get("MODEL_ANSWER_MAX_TOKENS") == "512"
    del env["MODEL_ANSWER_MAX_TOKENS"]
    with pytest.raises(SettingsError, match="MODEL_ANSWER_MAX_TOKENS"):
        Settings.from_env(env)


def test_one_embedding_batch_or_vision_crop_must_end_well_inside_the_lease() -> None:
    """One embedding batch or one vision crop is a single model call (MODEL_TIMEOUT_SECONDS)
    between two heartbeats, so it gets the same margin as a card call (AR1)."""
    fits = Settings.from_env(shipped(MODEL_TIMEOUT_SECONDS="1125", QUEUE_LEASE_SECONDS="1200"))
    assert fits.model_timeout_seconds == 1125.0
    with pytest.raises(SettingsError, match="MODEL_TIMEOUT_SECONDS") as caught:
        Settings.from_env(shipped(MODEL_TIMEOUT_SECONDS="1126", QUEUE_LEASE_SECONDS="1200"))
    assert "QUEUE_LEASE_SECONDS" in str(caught.value)


def test_the_question_translation_is_off_unless_set_and_has_its_own_timeout() -> None:
    """Council 4 Oct 2026: ANSWER_TRANSLATE_QUESTION is off when unset (an older .env), and
    MODEL_TRANSLATE_TIMEOUT_SECONDS is 20 (PROVISIONAL)."""
    shipped_values = Settings.from_env(shipped())
    unset = Settings.from_env(shipped(ANSWER_TRANSLATE_QUESTION=None, MODEL_TRANSLATE_TIMEOUT_SECONDS=None))
    for settings in (shipped_values, unset):
        assert (settings.answer_translate_question, settings.model_translate_timeout_seconds) == (False, 20.0)
    on = Settings.from_env(shipped(ANSWER_TRANSLATE_QUESTION="true", MODEL_TRANSLATE_TIMEOUT_SECONDS="5"))
    assert (on.answer_translate_question, on.model_translate_timeout_seconds) == (True, 5.0)
    with pytest.raises(SettingsError, match="ANSWER_TRANSLATE_QUESTION"):
        Settings.from_env(shipped(ANSWER_TRANSLATE_QUESTION="yes"))
    for bad in ("0", "-1", "nan", "soon"):
        with pytest.raises(SettingsError, match="MODEL_TRANSLATE_TIMEOUT_SECONDS"):
            Settings.from_env(shipped(MODEL_TRANSLATE_TIMEOUT_SECONDS=bad))
