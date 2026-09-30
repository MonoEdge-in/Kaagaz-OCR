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
        ("CARD_MAX_CHARS", "0"),
    ],
)
def test_a_bad_model_budget_stops_start_up_by_name(name: str, value: str) -> None:
    with pytest.raises(SettingsError, match=name):
        Settings.from_env(shipped(**{name: value}))


@pytest.mark.parametrize("name", ["MODEL_CARD_MAX_TOKENS", "MODEL_WINDOW_MAX_TOKENS"])
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
    its repair retry. With less than a minute to spare, a second worker could start
    the same document (AR1)."""
    fits = Settings.from_env(shipped(MODEL_EXTRACT_TIMEOUT_SECONDS="570", QUEUE_LEASE_SECONDS="1200"))
    assert fits.model_extract_timeout_seconds == 570.0
    for timeout, lease in (("571", "1200"), ("300", "600")):
        with pytest.raises(SettingsError, match="MODEL_EXTRACT_TIMEOUT_SECONDS") as caught:
            Settings.from_env(shipped(MODEL_EXTRACT_TIMEOUT_SECONDS=timeout, QUEUE_LEASE_SECONDS=lease))
        assert "QUEUE_LEASE_SECONDS" in str(caught.value)
