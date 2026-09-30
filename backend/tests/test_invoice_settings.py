"""Start-up settings for the invoice windows (PRD 2 §8 and E2; plan 6)."""

import pytest

from kaagaz.settings import Settings, SettingsError, window_for_reply_tokens
from tests.settings_factory import make_settings
from tests.test_model_settings import shipped


def test_the_shipped_env_example_has_the_starting_values() -> None:
    settings = Settings.from_env(shipped())
    windows = (settings.extract_window_chars, settings.extract_window_overlap, settings.extract_max_windows)
    assert windows == (1700, 400, 10)


def test_the_test_settings_use_the_same_starting_values() -> None:
    settings = make_settings()
    windows = (settings.extract_window_chars, settings.extract_window_overlap, settings.extract_max_windows)
    assert windows == (1700, 400, 10)


def test_a_missing_max_windows_stops_start_up_by_name() -> None:
    with pytest.raises(SettingsError, match="EXTRACT_MAX_WINDOWS"):
        Settings.from_env(shipped(EXTRACT_MAX_WINDOWS=None))


@pytest.mark.parametrize("name", ["EXTRACT_WINDOW_CHARS", "EXTRACT_WINDOW_OVERLAP"])
def test_an_empty_or_missing_window_size_follows_the_reply_budget(name: str) -> None:
    """Decided 30 Sep 2026: the window follows MODEL_WINDOW_MAX_TOKENS, so a bigger
    model is one setting. 2048 gives the starting 1700 and 400."""
    for value in (None, ""):
        settings = Settings.from_env(shipped(**{name: value}))
        assert (settings.extract_window_chars, settings.extract_window_overlap) == (1700, 400)


def test_a_bigger_reply_budget_gives_bigger_windows() -> None:
    bigger = Settings.from_env(shipped(MODEL_NUM_CTX="16384", MODEL_WINDOW_MAX_TOKENS="4096"))
    assert (bigger.extract_window_chars, bigger.extract_window_overlap) == (3750, 900)  # 75 lines, a quarter


@pytest.mark.parametrize(
    ("max_tokens", "window"), [(2048, (1700, 400)), (4096, (3750, 900)), (349, (0, 0)), (1, (0, 0))]
)
def test_window_for_reply_tokens(max_tokens: int, window: tuple[int, int]) -> None:
    assert window_for_reply_tokens(max_tokens) == window


def test_a_reply_budget_too_small_for_one_line_stops_start_up() -> None:
    with pytest.raises(SettingsError, match="EXTRACT_WINDOW_CHARS must be at least 1"):
        Settings.from_env(shipped(MODEL_WINDOW_MAX_TOKENS="300"))


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


def test_an_unknown_time_zone_stops_start_up_by_name() -> None:
    """Final council 3: today for the invoice checks is the date in TIMEZONE (ML 6(j))."""
    with pytest.raises(SettingsError, match="TIMEZONE"):
        Settings.from_env(shipped(TIMEZONE="Asia/Kolkatta"))
    assert Settings.from_env(shipped(TIMEZONE="Asia/Kolkata")).timezone == "Asia/Kolkata"
