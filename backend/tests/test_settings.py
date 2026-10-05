"""Start-up settings for the scan reader (PRD 2 P3, P5, R5) and the lease check (AR1)."""

from pathlib import Path

import pytest

from kaagaz import settings as settings_module
from kaagaz.ocr_server.app import ALLOWED_HOSTS
from kaagaz.reading import ocr_dpi
from kaagaz.settings import Settings, SettingsError
from tests.settings_factory import make_settings

ENV_EXAMPLE = Path(__file__).resolve().parents[2] / ".env.example"
OCR_SETTINGS = ["OCR_ENGINE", "OCR_URL", "OCR_TIMEOUT_SECONDS", "OCR_DPI"]


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


def test_the_shipped_env_example_parses_with_the_scan_reader_defaults() -> None:
    settings = Settings.from_env(shipped())
    assert (settings.ocr_engine, settings.ocr_url, settings.ocr_timeout_seconds, settings.ocr_dpi) == (
        "rapidocr",
        "http://localhost:8080",
        300.0,  # 5 minutes a page (decided 30 Sep 2026)
        300,
    )
    assert (settings.queue_lease_seconds, settings.page_image_dpi) == (1200, 150)


@pytest.mark.parametrize("name", OCR_SETTINGS)
def test_a_missing_scan_reader_setting_stops_start_up_by_name(name: str) -> None:
    with pytest.raises(SettingsError, match=name):
        Settings.from_env(shipped(**{name: None}))


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("OCR_ENGINE", "paddleocr"),
        ("OCR_URL", "ftp://ocr:8080"),
        ("OCR_URL", "ocr:8080"),
        ("OCR_TIMEOUT_SECONDS", "0"),
        ("OCR_TIMEOUT_SECONDS", "-5"),
        ("OCR_TIMEOUT_SECONDS", "nan"),
        ("OCR_TIMEOUT_SECONDS", "inf"),
        ("OCR_TIMEOUT_SECONDS", "ten"),
        ("OCR_DPI", "0"),
    ],
)
def test_a_bad_scan_reader_setting_stops_start_up_by_name(name: str, value: str) -> None:
    with pytest.raises(SettingsError, match=name):
        Settings.from_env(shipped(**{name: value}))


@pytest.mark.parametrize("engine", ["paddleocr_vl", "rapidocr", "none"])
def test_each_engine_name_is_accepted(engine: str) -> None:
    assert Settings.from_env(shipped(OCR_ENGINE=engine)).ocr_engine == engine


def test_one_ocr_request_must_end_well_inside_the_lease() -> None:
    """The longest step without a heartbeat is one OCR request: with less than two
    minutes to spare, plus the 15 s the worker may wait to renew a lease, a slow page
    outlives the lease and a second worker starts."""
    fits = Settings.from_env(shipped(OCR_TIMEOUT_SECONDS="1065", QUEUE_LEASE_SECONDS="1200"))
    assert fits.ocr_timeout_seconds == 1065.0
    for timeout, lease in (("1066", "1200"), ("900", "900")):
        with pytest.raises(SettingsError, match="OCR_TIMEOUT_SECONDS") as caught:
            Settings.from_env(shipped(OCR_TIMEOUT_SECONDS=timeout, QUEUE_LEASE_SECONDS=lease))
        assert "QUEUE_LEASE_SECONDS" in str(caught.value)


@pytest.mark.parametrize(
    "url",
    [
        "http://ocr:9000",
        "http://evil.example:8080",
        "http://ocr",
        "https://localhost:8443",
        "http://[::1]:8080",
    ],
)
def test_paddleocr_vl_needs_a_host_the_ocr_server_accepts(url: str) -> None:
    """The server answers 421 to any other Host, so a wrong URL would fail every scan."""
    with pytest.raises(SettingsError, match="OCR_URL"):
        Settings.from_env(shipped(OCR_ENGINE="paddleocr_vl", OCR_URL=url))


@pytest.mark.parametrize("url", ["http://ocr:8080", "http://localhost:8080/", "http://127.0.0.1:8080"])
def test_each_host_the_ocr_server_accepts_is_allowed(url: str) -> None:
    assert Settings.from_env(shipped(OCR_ENGINE="paddleocr_vl", OCR_URL=url)).ocr_url == url


def test_the_host_is_checked_only_for_paddleocr_vl_and_matches_the_server() -> None:
    assert (
        Settings.from_env(shipped(OCR_ENGINE="rapidocr", OCR_URL="http://elsewhere:1")).ocr_engine
        == "rapidocr"
    )
    assert settings_module.OCR_SERVER_HOSTS == ALLOWED_HOSTS


def test_the_ocr_dpi_rule_is_fixed_unless_set_to_one_the_readers_know() -> None:
    """Council 5 Oct 2026: OCR_DPI_RULE is optional, so an older .env keeps today's DPI."""
    unset = shipped()
    unset.pop("OCR_DPI_RULE", None)
    assert Settings.from_env(unset).ocr_dpi_rule == "fixed"
    assert Settings.from_env(shipped(OCR_DPI_RULE=" ")).ocr_dpi_rule == "fixed"  # empty is unset
    for rule in sorted(ocr_dpi.DPI_RULES):
        assert Settings.from_env(shipped(OCR_DPI_RULE=rule)).ocr_dpi_rule == rule
    for bad in ("auto", "Text_Height", "400"):
        with pytest.raises(SettingsError, match="OCR_DPI_RULE"):
            Settings.from_env(shipped(OCR_DPI_RULE=bad))


ROUTE_SETTINGS = [
    "TEXT_LAYER_MIN_CHARS",
    "TEXT_LAYER_MIN_GOOD_RATIO",
    "SCAN_IMAGE_COVERAGE",
    "BLANK_PAGE_STDDEV",
    "LOW_RES_DPI",
    "REPROCESS_PER_HOUR",
]


def test_the_shipped_env_example_has_the_page_route_starting_values() -> None:
    settings = Settings.from_env(shipped())
    assert (
        settings.text_layer_min_chars,
        settings.text_layer_min_good_ratio,
        settings.scan_image_coverage,
        settings.blank_page_stddev,
        settings.low_res_dpi,
        settings.reprocess_per_hour,
    ) == (40, 0.9, 0.5, 3.0, 150, 20)


@pytest.mark.parametrize("name", ROUTE_SETTINGS)
def test_a_missing_page_route_setting_stops_start_up_by_name(name: str) -> None:
    with pytest.raises(SettingsError, match=name):
        Settings.from_env(shipped(**{name: None}))


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("TEXT_LAYER_MIN_CHARS", "0"),
        ("TEXT_LAYER_MIN_GOOD_RATIO", "0"),
        ("TEXT_LAYER_MIN_GOOD_RATIO", "1.5"),
        ("TEXT_LAYER_MIN_GOOD_RATIO", "nan"),
        ("SCAN_IMAGE_COVERAGE", "-0.1"),
        ("SCAN_IMAGE_COVERAGE", "inf"),
        ("BLANK_PAGE_STDDEV", "-1"),
        ("BLANK_PAGE_STDDEV", "nan"),
        ("LOW_RES_DPI", "0"),
        ("REPROCESS_PER_HOUR", "0"),
    ],
)
def test_a_bad_page_route_setting_stops_start_up_by_name(name: str, value: str) -> None:
    with pytest.raises(SettingsError, match=name):
        Settings.from_env(shipped(**{name: value}))


# The settings that are secrets (council security note 7): repr never shows them.
SECRET_FIELDS = [
    "database_url",
    "storage_access_key",
    "storage_secret_key",
    "session_secret",
    "totp_encryption_key",
]


def test_repr_shows_none_of_the_five_secrets() -> None:
    canaries = {name: f"canary-{name}-{'z' * 32}" for name in SECRET_FIELDS}
    text = repr(make_settings(**canaries))
    assert [name for name, value in canaries.items() if value in text] == []
    assert "db_app_role='kaagaz_app'" in text  # the other settings still show


def test_the_shipped_embedding_dim_is_the_vector_column_size() -> None:
    assert Settings.from_env(shipped()).embedding_dim == 1024


@pytest.mark.parametrize("value", ["0", "768", "1023", "1536"])
def test_an_embedding_dim_other_than_1024_stops_start_up(value: str) -> None:
    """The pieces' vector(1024) column (migration 0001) holds no other size."""
    with pytest.raises(SettingsError, match="EMBEDDING_DIM must be 1024"):
        Settings.from_env(shipped(EMBEDDING_DIM=value))


def test_search_ef_ships_at_100_and_must_be_1_to_1000() -> None:
    """PRD 3 V2: hnsw.ef_search takes 1 to 1000; a value outside stops start-up by name."""
    assert Settings.from_env(shipped()).search_ef == 100
    assert Settings.from_env(shipped(SEARCH_EF="1000")).search_ef == 1000
    for bad in ("0", "1001", "-5", "ten", None):
        with pytest.raises(SettingsError, match="SEARCH_EF"):
            Settings.from_env(shipped(SEARCH_EF=bad))


def test_search_max_documents_ships_at_20_and_must_be_1_to_100() -> None:
    """PRD 3 Q2: at most SEARCH_MAX_DOCUMENTS documents per reply; a value outside stops start-up by name."""
    assert Settings.from_env(shipped()).search_max_documents == 20
    assert Settings.from_env(shipped(SEARCH_MAX_DOCUMENTS="100")).search_max_documents == 100
    for bad in ("0", "101", "twenty", None):
        with pytest.raises(SettingsError, match="SEARCH_MAX_DOCUMENTS"):
            Settings.from_env(shipped(SEARCH_MAX_DOCUMENTS=bad))


def test_answer_neighbours_ships_at_1_and_must_be_0_to_3() -> None:
    """PRD 3 Q4: the pieces on each side of an evidence piece; a value outside stops start-up by name."""
    assert Settings.from_env(shipped()).answer_neighbours == 1
    for bad in ("4", "-1", "one", None):
        with pytest.raises(SettingsError, match="ANSWER_NEIGHBOURS"):
            Settings.from_env(shipped(ANSWER_NEIGHBOURS=bad))


def test_chat_history_turns_ships_at_3_and_must_be_0_to_10() -> None:
    """PRD 3 H1: the pairs a follow-up is rewritten with; a value outside stops start-up by name."""
    assert Settings.from_env(shipped()).chat_history_turns == 3
    for bad in ("11", "-1", "three", None):
        with pytest.raises(SettingsError, match="CHAT_HISTORY_TURNS"):
            Settings.from_env(shipped(CHAT_HISTORY_TURNS=bad))
