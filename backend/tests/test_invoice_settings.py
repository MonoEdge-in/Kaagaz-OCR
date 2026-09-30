"""Start-up settings for the invoice windows (PRD 2 §8 and E2; plan 6)."""

import pytest

from kaagaz.settings import Settings, SettingsError
from tests.settings_factory import make_settings
from tests.test_model_settings import shipped

WINDOW_SETTINGS = ["EXTRACT_WINDOW_CHARS", "EXTRACT_WINDOW_OVERLAP", "EXTRACT_MAX_WINDOWS"]


def test_the_shipped_env_example_has_the_starting_values() -> None:
    settings = Settings.from_env(shipped())
    windows = (settings.extract_window_chars, settings.extract_window_overlap, settings.extract_max_windows)
    assert windows == (1700, 400, 10)


def test_the_test_settings_use_the_same_starting_values() -> None:
    settings = make_settings()
    windows = (settings.extract_window_chars, settings.extract_window_overlap, settings.extract_max_windows)
    assert windows == (1700, 400, 10)


@pytest.mark.parametrize("name", WINDOW_SETTINGS)
def test_each_missing_setting_stops_start_up_by_name(name: str) -> None:
    with pytest.raises(SettingsError, match=name):
        Settings.from_env(shipped(**{name: None}))


def test_an_overlap_as_large_as_the_window_is_refused() -> None:
    """Without this the windows would never move forward (plan 6)."""
    message = "EXTRACT_WINDOW_OVERLAP must be less than EXTRACT_WINDOW_CHARS"
    for overlap in ("1700", "1701"):
        with pytest.raises(SettingsError, match=message):
            Settings.from_env(shipped(EXTRACT_WINDOW_OVERLAP=overlap))
    assert Settings.from_env(shipped(EXTRACT_WINDOW_OVERLAP="1699")).extract_window_overlap == 1699


def test_a_window_too_long_for_its_reply_budget_is_refused() -> None:
    """Council 5 (PROVISIONAL): a window's reply needs about 50 tokens per line item, one line
    per 50 characters, plus 300 for the fields. 1,700 characters need 34 * 50 + 300 = 2,000 of
    MODEL_WINDOW_MAX_TOKENS 2,048; 1,750 need 2,050."""
    message = "EXTRACT_WINDOW_CHARS is too long for MODEL_WINDOW_MAX_TOKENS"
    with pytest.raises(SettingsError, match=message):
        Settings.from_env(shipped(EXTRACT_WINDOW_CHARS="1750"))
    with pytest.raises(SettingsError, match=message):
        Settings.from_env(shipped(EXTRACT_WINDOW_CHARS="6000"))
    bigger = shipped(EXTRACT_WINDOW_CHARS="3700", MODEL_NUM_CTX="16384", MODEL_WINDOW_MAX_TOKENS="4000")
    assert Settings.from_env(bigger).extract_window_chars == 3700  # 74 * 50 + 300 = 4,000


@pytest.mark.parametrize(
    ("name", "value", "message"),
    [
        ("EXTRACT_WINDOW_CHARS", "0", "EXTRACT_WINDOW_CHARS must be at least 1"),
        ("EXTRACT_MAX_WINDOWS", "0", "EXTRACT_MAX_WINDOWS must be at least 1"),
        ("EXTRACT_WINDOW_OVERLAP", "-1", "EXTRACT_WINDOW_OVERLAP must not be negative"),
        ("EXTRACT_WINDOW_CHARS", "6k", "EXTRACT_WINDOW_CHARS must be a whole number"),
    ],
)
def test_bad_values_are_refused_by_name(name: str, value: str, message: str) -> None:
    with pytest.raises(SettingsError, match=message):
        Settings.from_env(shipped(**{name: value}))


def test_no_overlap_is_allowed() -> None:
    assert Settings.from_env(shipped(EXTRACT_WINDOW_OVERLAP="0")).extract_window_overlap == 0
