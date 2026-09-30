"""Start-up settings for the scan reader (PRD 2 P3, P5, R5) and the lease check (AR1)."""

from pathlib import Path

import pytest

from kaagaz.settings import Settings, SettingsError

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
    for name, value in changes.items():
        if value is None:
            env.pop(name)
        else:
            env[name] = value
    return env


def test_the_shipped_env_example_parses_with_the_scan_reader_defaults() -> None:
    settings = Settings.from_env(shipped())
    assert (settings.ocr_engine, settings.ocr_url, settings.ocr_timeout_seconds, settings.ocr_dpi) == (
        "rapidocr",
        "http://localhost:8080",
        900.0,
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
    minutes to spare, a slow page outlives the lease and a second worker starts."""
    fits = Settings.from_env(shipped(OCR_TIMEOUT_SECONDS="1080", QUEUE_LEASE_SECONDS="1200"))
    assert fits.ocr_timeout_seconds == 1080.0
    for timeout, lease in (("1081", "1200"), ("900", "900")):
        with pytest.raises(SettingsError, match="OCR_TIMEOUT_SECONDS") as caught:
            Settings.from_env(shipped(OCR_TIMEOUT_SECONDS=timeout, QUEUE_LEASE_SECONDS=lease))
        assert "QUEUE_LEASE_SECONDS" in str(caught.value)
