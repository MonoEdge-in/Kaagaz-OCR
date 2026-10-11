"""Start-up settings for the Gemini reader through OpenRouter (PRD 5 section 9; G2, G6, G8,
G9, F5, Q7, Q8; council 8 Oct 2026).

Every one is checked by name at start-up. The key is a secret: no error and no repr
ever shows it, and a laptop or CI with OCR_ENGINE=rapidocr and MODEL_ENABLED=false
starts without one.
"""

import pytest

from kaagaz import settings as settings_module
from kaagaz.settings import Settings, SettingsError, is_gemini_model, valid_model_id
from tests.settings_factory import make_settings
from tests.test_settings import shipped

KEY = "sk-or-v1-" + "q" * 64  # the shape of an OpenRouter key; not a real one
GEMINI = {"OCR_ENGINE": "gemini", "OPENROUTER_API_KEY": KEY}
PRO = "google/gemini-3.1-pro-preview"
READER_SETTINGS = [
    "OCR_MODEL",
    "OPENROUTER_BASE_URL",
    "OCR_MEDIA_RESOLUTION",
    "OCR_THINKING_LEVEL",
    "OCR_RETRY_MEDIA_RESOLUTION",
    "OCR_RETRY_THINKING_LEVEL",
    "OCR_PAGE_CONCURRENCY",
    "CPU_WORKERS",
    "MAX_CHECK_CROPS",
    "MODEL_RPM",
    "READER_OUTAGE_FALLBACK_MINUTES",
]


def test_the_shipped_env_example_has_the_prds_starting_values_and_no_key() -> None:
    settings = Settings.from_env(shipped())
    assert settings.ocr_engine == "rapidocr"  # laptops and CI
    assert settings.openrouter_api_key is None
    assert (settings.ocr_model, settings.openrouter_base_url) == (PRO, "https://openrouter.ai/api/v1")
    assert (
        settings.ocr_media_resolution,
        settings.ocr_thinking_level,
        settings.ocr_retry_media_resolution,
        settings.ocr_retry_thinking_level,
    ) == ("high", "low", "high", "high")  # the "detail" shape sends low or high only
    assert (
        settings.ocr_timeout_seconds,
        settings.ocr_page_concurrency,
        settings.cpu_workers,
        settings.max_check_crops,
        settings.reader_outage_fallback_minutes,
    ) == (120.0, 6, 2, 200, 15)
    assert settings.model_rpm >= 1


def test_the_test_settings_match_the_shipped_reader_values() -> None:
    names = {name.lower() for name in READER_SETTINGS} | {"openrouter_api_key"}
    shipped_settings = Settings.from_env(shipped())
    made = make_settings()
    assert {name: getattr(made, name) for name in names} == {
        name: getattr(shipped_settings, name) for name in names
    }


@pytest.mark.parametrize("name", READER_SETTINGS)
def test_a_missing_reader_setting_stops_start_up_by_name(name: str) -> None:
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
        ("OCR_MEDIA_RESOLUTION", "auto"),
        ("OCR_RETRY_MEDIA_RESOLUTION", "max"),
        ("OCR_THINKING_LEVEL", "none"),
        ("OCR_THINKING_LEVEL", "1024"),
        ("OCR_RETRY_THINKING_LEVEL", "highest"),
        ("OCR_PAGE_CONCURRENCY", "0"),
        ("OCR_PAGE_CONCURRENCY", "six"),
        ("CPU_WORKERS", "0"),
        ("CPU_WORKERS", "-2"),
        ("MAX_CHECK_CROPS", "0"),
        ("MODEL_RPM", "0"),
        ("MODEL_RPM", "1.5"),
        ("READER_OUTAGE_FALLBACK_MINUTES", "0"),
        ("READER_OUTAGE_FALLBACK_MINUTES", "15m"),
    ],
)
def test_a_bad_reader_setting_stops_start_up_by_name(name: str, value: str) -> None:
    with pytest.raises(SettingsError, match=name):
        Settings.from_env(shipped(**{name: value}))


def test_the_sendable_media_resolutions_and_every_thinking_level_are_accepted() -> None:
    assert sorted(settings_module.MEDIA_RESOLUTIONS) == ["high", "low", "medium", "ultra_high"]
    assert sorted(settings_module.THINKING_LEVELS) == ["high", "low", "medium", "minimal"]
    assert settings_module.MEDIA_RESOLUTION_SHAPE == "detail"
    for value in ("low", "high"):
        made = Settings.from_env(shipped(OCR_MEDIA_RESOLUTION=value, OCR_RETRY_MEDIA_RESOLUTION=value))
        assert (made.ocr_media_resolution, made.ocr_retry_media_resolution) == (value, value)
    for value in settings_module.THINKING_LEVELS:
        made = Settings.from_env(shipped(OCR_THINKING_LEVEL=value, OCR_RETRY_THINKING_LEVEL=value))
        assert (made.ocr_thinking_level, made.ocr_retry_thinking_level) == (value, value)


@pytest.mark.parametrize("name", ["OCR_MEDIA_RESOLUTION", "OCR_RETRY_MEDIA_RESOLUTION"])
@pytest.mark.parametrize("value", ["medium", "ultra_high"])
def test_a_level_the_detail_shape_cannot_send_stops_start_up_and_is_never_downgraded(
    name: str, value: str
) -> None:
    """image_url.detail takes low or high only: medium and ultra_high are real Gemini
    levels, but sending them as something else would be a quiet downgrade."""
    with pytest.raises(SettingsError, match=f"{name}={value} cannot be sent") as caught:
        Settings.from_env(shipped(**{name: value}))
    assert "['high', 'low']" in str(caught.value)


def test_gemini_reads_with_the_key_and_the_model_in_the_settings() -> None:
    settings = Settings.from_env(shipped(**GEMINI, OCR_MODEL="google/gemini-3.8-flash"))
    assert (settings.ocr_engine, settings.ocr_model, settings.openrouter_api_key) == (
        "gemini",
        "google/gemini-3.8-flash",
        KEY,
    )


@pytest.mark.parametrize(
    "model",
    [
        "gemini-3.1-pro-preview",  # Google's own ID, not OpenRouter's
        "google/gemini-3.1-pro-preview:free",  # a variant changes routing and data policy
        "google/gemini-3.1-pro-preview:floor",
        "Google/gemini-3.1-pro-preview",
        "google/gemini 3",
        "google/../keys",
        "google/",
    ],
)
def test_ocr_model_must_be_an_openrouter_model_id(model: str) -> None:
    with pytest.raises(SettingsError, match="OCR_MODEL must be an OpenRouter model ID"):
        Settings.from_env(shipped(**GEMINI, OCR_MODEL=model))


@pytest.mark.parametrize("name", ["MODEL_CHAT", "MODEL_EMBED"])
def test_a_model_with_a_variant_stops_start_up_by_name(name: str) -> None:
    with pytest.raises(SettingsError, match=f"{name} must be an OpenRouter model ID"):
        Settings.from_env(
            shipped(**GEMINI, MODEL_ENABLED="true", **{name: "google/gemini-3.1-pro-preview:free"})
        )


@pytest.mark.parametrize("name", ["MODEL_CHAT", "MODEL_EMBED"])
def test_any_openrouter_model_may_answer_or_embed(name: str) -> None:
    """Model choice (council 11 Oct 2026): the google/gemini- gate is gone; any OpenRouter
    model ID starts."""
    on = shipped(**GEMINI, MODEL_ENABLED="true", **{name: "anthropic/claude-sonnet-5.5"})
    assert getattr(Settings.from_env(on), name.lower()) == "anthropic/claude-sonnet-5.5"


def test_the_providers_are_google_vertex_unless_set() -> None:
    assert Settings.from_env(shipped()).openrouter_providers == {"google-vertex"}
    assert Settings.from_env(shipped(OPENROUTER_PROVIDERS="")).openrouter_providers == {"google-vertex"}
    both = Settings.from_env(shipped(OPENROUTER_PROVIDERS="google-vertex, azure"))
    assert both.openrouter_providers == {"google-vertex", "azure"}


@pytest.mark.parametrize("value", ["Azure", "google vertex", "google-vertex,amazon_bedrock", "a" * 65, ","])
def test_a_provider_that_is_not_a_slug_stops_start_up_by_name(value: str) -> None:
    with pytest.raises(SettingsError, match="OPENROUTER_PROVIDERS"):
        Settings.from_env(shipped(OPENROUTER_PROVIDERS=value))


@pytest.mark.parametrize("value", ["azure", "azure, amazon-bedrock"])
def test_providers_without_google_vertex_stop_start_up(value: str) -> None:
    """R5: chat, embeddings and OCR_MODEL (resolve's fallback) all go through google-vertex."""
    with pytest.raises(SettingsError, match="OPENROUTER_PROVIDERS must include google-vertex"):
        Settings.from_env(shipped(OPENROUTER_PROVIDERS=value))


def test_gemini_without_a_key_stops_start_up() -> None:
    for empty in (None, "", "   "):
        with pytest.raises(SettingsError, match="OPENROUTER_API_KEY is not set"):
            Settings.from_env(shipped(OCR_ENGINE="gemini", OPENROUTER_API_KEY=empty))


@pytest.mark.parametrize(
    ("changes", "needs_key"),
    [
        # OCR_ENGINE=rapidocr and MODEL_ENABLED=false: no model call anywhere, so no key.
        ({"MODEL_ENABLED": "false", "MODEL_CHAT": PRO}, False),
        ({"MODEL_ENABLED": "false", "MODEL_EMBED": "google/gemini-embedding-2"}, False),
        ({"MODEL_ENABLED": "true", "MODEL_CHAT": PRO, "MODEL_EMBED": "google/gemini-embedding-2"}, True),
    ],
)
def test_the_key_is_needed_only_when_a_model_is_called(changes: dict[str, str], needs_key: bool) -> None:
    without = shipped(OCR_ENGINE="rapidocr", OPENROUTER_API_KEY="", **changes)
    if needs_key:
        with pytest.raises(SettingsError, match="OPENROUTER_API_KEY is not set"):
            Settings.from_env(without)
    else:
        assert Settings.from_env(without).openrouter_api_key is None
    assert Settings.from_env({**without, "OPENROUTER_API_KEY": KEY}).openrouter_api_key == KEY


@pytest.mark.parametrize("bad", [KEY[:20] + " " + KEY[20:], KEY + "é", KEY + "\x00", KEY + "\r\nX-Evil: 1"])
def test_a_key_that_could_break_its_header_is_refused_without_showing_it(bad: str) -> None:
    with pytest.raises(SettingsError, match="OPENROUTER_API_KEY") as caught:
        Settings.from_env(shipped(**{**GEMINI, "OPENROUTER_API_KEY": bad}))
    assert KEY[9:] not in str(caught.value)


def test_repr_never_shows_the_key() -> None:
    settings = Settings.from_env(shipped(**GEMINI))
    assert settings.openrouter_api_key == KEY
    assert KEY not in repr(settings)
    assert KEY not in repr(make_settings(openrouter_api_key=KEY))


@pytest.mark.parametrize(
    ("url", "kept"),
    [
        ("https://openrouter.ai/api/v1", "https://openrouter.ai/api/v1"),
        ("https://openrouter.ai/api/v1/", "https://openrouter.ai/api/v1"),
        ("http://127.0.0.1:8099/api/v1", "http://127.0.0.1:8099/api/v1"),  # a fake server in tests
        ("http://localhost:9/api/v1", "http://localhost:9/api/v1"),  # the exit test's closed port
        ("http://[::1]:8099/api/v1", "http://[::1]:8099/api/v1"),
        ("https://127.0.0.1:1/api/v1", "https://127.0.0.1:1/api/v1"),
    ],
)
def test_the_base_url_is_https_or_a_loopback_host(url: str, kept: str) -> None:
    assert Settings.from_env(shipped(**GEMINI, OPENROUTER_BASE_URL=url)).openrouter_base_url == kept


@pytest.mark.parametrize(
    "url",
    [
        "http://openrouter.ai/api/v1",  # the key would cross the network in the clear
        "http://10.0.0.5:8099/api/v1",
        "http://localhost.evil.example/api/v1",
        "ftp://openrouter.ai/api/v1",
        "openrouter.ai/api/v1",
        "https:///api/v1",
        "https://user:secret@openrouter.ai/api/v1",
        "https://openrouter.ai/api/v1?key=abc",
        "https://openrouter.ai/api/v1#models",
    ],
)
def test_any_other_base_url_stops_start_up_by_name(url: str) -> None:
    with pytest.raises(SettingsError, match="OPENROUTER_BASE_URL"):
        Settings.from_env(shipped(OPENROUTER_BASE_URL=url))


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
        ("google/gemini-3.1-pro-preview", True),
        ("google/gemini-3.8-flash", True),
        ("google/gemini-embedding-2", True),
        ("gemini-3.1-pro-preview", False),  # Google's own ID: not an OpenRouter one
        ("local-chat:4b", False),
        ("local-embed:0.6b", False),
        ("Google/gemini-3.1-pro-preview", False),  # model IDs are lowercase; no guessing
        ("google/gemma-3-27b-it", False),
        ("openai/gpt-5", False),
        ("my-google/gemini-3", False),
    ],
)
def test_a_gemini_model_is_named_by_its_openrouter_id(name: str, gemini: bool) -> None:
    assert is_gemini_model(name) is gemini


@pytest.mark.parametrize(
    ("name", "valid"),
    [
        ("google/gemini-3.1-pro-preview", True),
        ("google/gemini-embedding-2", True),
        ("x-ai/grok_4.1", True),
        ("google/gemini-3.1-pro-preview:free", False),
        ("gemini-3.1-pro-preview", False),
        ("google/gemini/3", False),
        ("local-chat:4b", False),
    ],
)
def test_a_model_id_is_author_slash_slug_with_no_variant(name: str, valid: bool) -> None:
    assert valid_model_id(name) is valid
