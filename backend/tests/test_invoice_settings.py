"""Start-up settings for the invoice windows (PRD 2 §8 and E2; plan 6)."""

import pytest

from kaagaz.settings import Settings, SettingsError
from tests.settings_factory import make_settings
from tests.test_model_settings import shipped

WINDOW_SETTINGS = ["EXTRACT_WINDOW_CHARS", "EXTRACT_WINDOW_OVERLAP", "EXTRACT_MAX_WINDOWS"]


def test_the_shipped_env_example_has_the_starting_values() -> None:
    settings = Settings.from_env(shipped())
    windows = (settings.extract_window_chars, settings.extract_window_overlap, settings.extract_max_windows)
    assert windows == (6000, 1000, 10)


def test_the_test_settings_use_the_same_starting_values() -> None:
    settings = make_settings()
    windows = (settings.extract_window_chars, settings.extract_window_overlap, settings.extract_max_windows)
    assert windows == (6000, 1000, 10)


@pytest.mark.parametrize("name", WINDOW_SETTINGS)
def test_each_missing_setting_stops_start_up_by_name(name: str) -> None:
    with pytest.raises(SettingsError, match=name):
        Settings.from_env(shipped(**{name: None}))


def test_an_overlap_as_large_as_the_window_is_refused() -> None:
    """Without this the windows would never move forward (plan 6)."""
    message = "EXTRACT_WINDOW_OVERLAP must be less than EXTRACT_WINDOW_CHARS"
    for overlap in ("6000", "6001"):
        with pytest.raises(SettingsError, match=message):
            Settings.from_env(shipped(EXTRACT_WINDOW_OVERLAP=overlap))
    assert Settings.from_env(shipped(EXTRACT_WINDOW_OVERLAP="5999")).extract_window_overlap == 5999


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
