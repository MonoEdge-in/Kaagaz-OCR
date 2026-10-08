"""Start-up settings for the Gemini reader (PRD 5 section 9; G2, G6, G8, G9, F5, Q7, Q8).

Every one is checked by name at start-up. The key is a secret: no error and no repr
ever shows it, and a laptop or CI with OCR_ENGINE=rapidocr and MODEL_ENABLED=false
starts without one.
"""

import pytest

from kaagaz import settings as settings_module
from kaagaz.settings import Settings, SettingsError, is_gemini_model
from tests.settings_factory import make_settings
from tests.test_settings import shipped

KEY = "AIza" + "Q" * 35  # the shape of a Google API key; not a real one
GEMINI = {"OCR_ENGINE": "gemini", "GEMINI_API_KEY": KEY}
GEMINI_SETTINGS = [
    "OCR_MODEL",
    "GEMINI_BASE_URL",
    "OCR_MEDIA_RESOLUTION",
    "OCR_THINKING_LEVEL",
    "OCR_RETRY_MEDIA_RESOLUTION",
    "OCR_RETRY_THINKING_LEVEL",
    "OCR_PAGE_CONCURRENCY",
    "CPU_WORKERS",
    "MAX_CHECK_CROPS",
    "GEMINI_RPM",
    "READER_OUTAGE_FALLBACK_MINUTES",
]


def test_the_shipped_env_example_has_the_prds_starting_values_and_no_key() -> None:
    settings = Settings.from_env(shipped())
    assert settings.ocr_engine == "rapidocr"  # laptops and CI
    assert settings.gemini_api_key is None
    assert (settings.ocr_model, settings.gemini_base_url) == (
        "gemini-3.1-pro-preview",
        "https://generativelanguage.googleapis.com/v1beta",
    )
    assert (
        settings.ocr_media_resolution,
        settings.ocr_thinking_level,
        settings.ocr_retry_media_resolution,
        settings.ocr_retry_thinking_level,
    ) == ("high", "low", "ultra_high", "high")
    assert (
        settings.ocr_timeout_seconds,
        settings.ocr_page_concurrency,
        settings.cpu_workers,
        settings.max_check_crops,
        settings.reader_outage_fallback_minutes,
    ) == (120.0, 6, 2, 200, 15)
    assert settings.gemini_rpm >= 1


def test_the_test_settings_match_the_shipped_gemini_values() -> None:
    names = {name.lower() for name in GEMINI_SETTINGS} | {"gemini_api_key"}
    shipped_settings = Settings.from_env(shipped())
    made = make_settings()
    assert {name: getattr(made, name) for name in names} == {
        name: getattr(shipped_settings, name) for name in names
    }


@pytest.mark.parametrize("name", GEMINI_SETTINGS)
def test_a_missing_gemini_setting_stops_start_up_by_name(name: str) -> None:
    """No defaults in code: each is Vrushit's setting, so a missing one names itself."""
    with pytest.raises(SettingsError, match=name):
        Settings.from_env(shipped(**{name: None}))
    with pytest.raises(SettingsError, match=name):
        Settings.from_env(shipped(**{name: "  "}))


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("OCR_MEDIA_RESOLUTION", "ultra"),
        ("OCR_MEDIA_RESOLUTION", "HIGH"),
        ("OCR_MEDIA_RESOLUTION", "MEDIA_RESOLUTION_HIGH"),
        ("OCR_RETRY_MEDIA_RESOLUTION", "max"),
        ("OCR_THINKING_LEVEL", "none"),
        ("OCR_THINKING_LEVEL", "1024"),
        ("OCR_RETRY_THINKING_LEVEL", "highest"),
        ("OCR_PAGE_CONCURRENCY", "0"),
        ("OCR_PAGE_CONCURRENCY", "six"),
        ("CPU_WORKERS", "0"),
        ("CPU_WORKERS", "-2"),
        ("MAX_CHECK_CROPS", "0"),
        ("GEMINI_RPM", "0"),
        ("GEMINI_RPM", "1.5"),
        ("READER_OUTAGE_FALLBACK_MINUTES", "0"),
        ("READER_OUTAGE_FALLBACK_MINUTES", "15m"),
    ],
)
def test_a_bad_gemini_setting_stops_start_up_by_name(name: str, value: str) -> None:
    with pytest.raises(SettingsError, match=name):
        Settings.from_env(shipped(**{name: value}))


def test_each_media_resolution_and_thinking_level_is_accepted() -> None:
    assert sorted(settings_module.MEDIA_RESOLUTIONS) == ["high", "low", "medium", "ultra_high"]
    assert sorted(settings_module.THINKING_LEVELS) == ["high", "low", "medium", "minimal"]
    for value in settings_module.MEDIA_RESOLUTIONS:
        made = Settings.from_env(shipped(OCR_MEDIA_RESOLUTION=value, OCR_RETRY_MEDIA_RESOLUTION=value))
        assert (made.ocr_media_resolution, made.ocr_retry_media_resolution) == (value, value)
    for value in settings_module.THINKING_LEVELS:
        made = Settings.from_env(shipped(OCR_THINKING_LEVEL=value, OCR_RETRY_THINKING_LEVEL=value))
        assert (made.ocr_thinking_level, made.ocr_retry_thinking_level) == (value, value)


def test_gemini_reads_with_the_key_and_the_model_in_the_settings() -> None:
    settings = Settings.from_env(shipped(**GEMINI, OCR_MODEL="gemini-3.8-flash"))
    assert (settings.ocr_engine, settings.ocr_model, settings.gemini_api_key) == (
        "gemini",
        "gemini-3.8-flash",
        KEY,
    )


def test_gemini_without_a_key_stops_start_up() -> None:
    for empty in (None, "", "   "):
        with pytest.raises(SettingsError, match="GEMINI_API_KEY is not set"):
            Settings.from_env(shipped(OCR_ENGINE="gemini", GEMINI_API_KEY=empty))


@pytest.mark.parametrize(
    ("changes", "needs_key"),
    [
        # OCR_ENGINE=rapidocr and MODEL_ENABLED=false: no Gemini call anywhere, so no key.
        ({"MODEL_ENABLED": "false", "MODEL_CHAT": "gemini-3.1-pro-preview"}, False),
        ({"MODEL_ENABLED": "false", "MODEL_EMBED": "gemini-embedding-2"}, False),
        ({"MODEL_ENABLED": "true", "MODEL_CHAT": "qwen3:4b", "MODEL_EMBED": "qwen3-embedding:0.6b"}, False),
        ({"MODEL_ENABLED": "true", "MODEL_CHAT": "gemini-3.1-pro-preview"}, True),
        ({"MODEL_ENABLED": "true", "MODEL_EMBED": "gemini-embedding-2"}, True),
        ({"MODEL_ENABLED": "true", "MODEL_VISION": "gemini-3.8-flash"}, True),
    ],
)
def test_the_key_is_needed_only_when_gemini_is_called(changes: dict[str, str], needs_key: bool) -> None:
    without = shipped(OCR_ENGINE="rapidocr", GEMINI_API_KEY="", **changes)
    if needs_key:
        with pytest.raises(SettingsError, match="GEMINI_API_KEY is not set"):
            Settings.from_env(without)
    else:
        assert Settings.from_env(without).gemini_api_key is None
    assert Settings.from_env({**without, "GEMINI_API_KEY": KEY}).gemini_api_key == KEY


@pytest.mark.parametrize("bad", [KEY[:20] + " " + KEY[20:], KEY + "é", KEY + "\x00", KEY + "\r\nX-Evil: 1"])
def test_a_key_that_could_break_its_header_is_refused_without_showing_it(bad: str) -> None:
    with pytest.raises(SettingsError, match="GEMINI_API_KEY") as caught:
        Settings.from_env(shipped(**{**GEMINI, "GEMINI_API_KEY": bad}))
    assert KEY[4:] not in str(caught.value)


def test_repr_never_shows_the_key() -> None:
    settings = Settings.from_env(shipped(**GEMINI))
    assert settings.gemini_api_key == KEY
    assert KEY not in repr(settings)
    assert KEY not in repr(make_settings(gemini_api_key=KEY))


@pytest.mark.parametrize(
    ("url", "kept"),
    [
        (
            "https://generativelanguage.googleapis.com/v1beta",
            "https://generativelanguage.googleapis.com/v1beta",
        ),
        (
            "https://generativelanguage.googleapis.com/v1beta/",
            "https://generativelanguage.googleapis.com/v1beta",
        ),
        ("http://127.0.0.1:8099/v1beta", "http://127.0.0.1:8099/v1beta"),  # a fake server in tests
        ("http://localhost:9/v1beta", "http://localhost:9/v1beta"),  # the exit test's closed port
        ("http://[::1]:8099/v1beta", "http://[::1]:8099/v1beta"),
        ("https://127.0.0.1:1/v1beta", "https://127.0.0.1:1/v1beta"),
    ],
)
def test_the_base_url_is_https_or_a_loopback_host(url: str, kept: str) -> None:
    assert Settings.from_env(shipped(**GEMINI, GEMINI_BASE_URL=url)).gemini_base_url == kept


@pytest.mark.parametrize(
    "url",
    [
        "http://generativelanguage.googleapis.com/v1beta",  # the key would cross the network in the clear
        "http://10.0.0.5:8099/v1beta",
        "http://localhost.evil.example/v1beta",
        "ftp://generativelanguage.googleapis.com/v1beta",
        "generativelanguage.googleapis.com/v1beta",
        "https:///v1beta",
        "https://user:secret@generativelanguage.googleapis.com/v1beta",
        "https://generativelanguage.googleapis.com/v1beta?key=abc",
        "https://generativelanguage.googleapis.com/v1beta#models",
    ],
)
def test_any_other_base_url_stops_start_up_by_name(url: str) -> None:
    with pytest.raises(SettingsError, match="GEMINI_BASE_URL"):
        Settings.from_env(shipped(GEMINI_BASE_URL=url))


def test_a_gemini_page_needs_three_calls_inside_the_lease() -> None:
    """G6 and Q8: a page's read, and when cut off at the output limit its two halves,
    each up to OCR_TIMEOUT_SECONDS, all before the next heartbeat: 3 x 120 + 135 <= 1200."""
    prd = Settings.from_env(shipped(**GEMINI, OCR_TIMEOUT_SECONDS="120", QUEUE_LEASE_SECONDS="1200"))
    assert (prd.ocr_timeout_seconds, prd.queue_lease_seconds) == (120.0, 1200)
    fits = Settings.from_env(shipped(**GEMINI, OCR_TIMEOUT_SECONDS="355", QUEUE_LEASE_SECONDS="1200"))
    assert fits.ocr_timeout_seconds == 355.0
    with pytest.raises(SettingsError, match="3 x OCR_TIMEOUT_SECONDS") as caught:
        Settings.from_env(shipped(**GEMINI, OCR_TIMEOUT_SECONDS="356", QUEUE_LEASE_SECONDS="1200"))
    assert "QUEUE_LEASE_SECONDS" in str(caught.value)
    # The same timeout still fits one call for every other engine.
    assert (
        Settings.from_env(shipped(OCR_TIMEOUT_SECONDS="356", QUEUE_LEASE_SECONDS="1200")).ocr_engine
        == "rapidocr"
    )


def test_a_dpi_rule_doubles_the_gemini_calls_inside_the_lease() -> None:
    twice = {**GEMINI, "OCR_DPI_RULE": "text_height", "QUEUE_LEASE_SECONDS": "1200"}
    assert Settings.from_env(shipped(OCR_TIMEOUT_SECONDS="177", **twice)).ocr_timeout_seconds == 177.0
    with pytest.raises(SettingsError, match="6 x OCR_TIMEOUT_SECONDS"):
        Settings.from_env(shipped(OCR_TIMEOUT_SECONDS="178", **twice))


@pytest.mark.parametrize(
    ("name", "gemini"),
    [
        ("gemini-3.1-pro-preview", True),
        ("gemini-3.8-flash", True),
        ("gemini-embedding-2", True),
        ("qwen3:4b", False),
        ("qwen3-embedding:0.6b", False),
        ("Gemini-3.1-pro-preview", False),  # model IDs are lowercase; no guessing
        ("my-gemini-3", False),
    ],
)
def test_a_gemini_model_is_named_by_its_id(name: str, gemini: bool) -> None:
    assert is_gemini_model(name) is gemini
