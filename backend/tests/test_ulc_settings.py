"""Ultra low cost's settings (#488, production port): OCR_ULTRA_LOW_COST and
OCR_PAGE_CEILING_PAISE, checked at start-up by name, off and 55 paise when unset, and the
worker's start-up check of OCR_MODEL (``worker_main.ulc_check``)."""

import contextlib
import logging
from collections.abc import Iterator
from typing import Any
from unittest import mock

import pytest

from kaagaz import worker_main
from kaagaz.db.engine import Database
from kaagaz.settings import Settings, SettingsError
from tests.settings_factory import make_settings
from tests.test_settings import shipped

KEY = "sk-or-v1-" + "q" * 64  # the shape of an OpenRouter key; not a real one
GEMINI = {"OCR_ENGINE": "gemini", "OPENROUTER_API_KEY": KEY}


def test_488_shipped_and_unset_it_is_off_at_55_paise() -> None:
    example = shipped(**GEMINI)
    assert (example["OCR_ULTRA_LOW_COST"], example["OCR_PAGE_CEILING_PAISE"]) == ("false", "55")
    for env in (example, shipped(**GEMINI, OCR_ULTRA_LOW_COST=None, OCR_PAGE_CEILING_PAISE=None)):
        settings = Settings.from_env(env)
        assert (settings.ocr_ultra_low_cost, settings.ocr_page_ceiling_paise) == (False, 55)
    assert make_settings().ocr_ultra_low_cost is False


@pytest.mark.parametrize(("value", "on"), [("true", True), ("false", False), ("TRUE", True), ("", False)])
def test_488_the_switch_reads_true_or_false(value: str, on: bool) -> None:
    assert Settings.from_env(shipped(**GEMINI, OCR_ULTRA_LOW_COST=value)).ocr_ultra_low_cost is on


@pytest.mark.parametrize("paise", ["20", "40", "55"])
def test_488_a_ceiling_from_20_to_55_paise_is_taken(paise: str) -> None:
    settings = Settings.from_env(shipped(**GEMINI, OCR_ULTRA_LOW_COST="true", OCR_PAGE_CEILING_PAISE=paise))
    assert (settings.ocr_ultra_low_cost, settings.ocr_page_ceiling_paise) == (True, int(paise))


@pytest.mark.parametrize(
    ("changes", "message"),
    [
        ({"OCR_ULTRA_LOW_COST": "yes"}, "OCR_ULTRA_LOW_COST must be true or false"),
        ({"OCR_ULTRA_LOW_COST": "1"}, "OCR_ULTRA_LOW_COST must be true or false"),
        ({"OCR_PAGE_CEILING_PAISE": "19"}, "OCR_PAGE_CEILING_PAISE must be between 20 and 55"),
        ({"OCR_PAGE_CEILING_PAISE": "56"}, "OCR_PAGE_CEILING_PAISE must be between 20 and 55"),
        ({"OCR_PAGE_CEILING_PAISE": "0.4"}, "OCR_PAGE_CEILING_PAISE must be a whole number"),
        ({"OCR_PAGE_CEILING_PAISE": "-30"}, "OCR_PAGE_CEILING_PAISE must not be negative"),
    ],
)
def test_488_a_bad_value_stops_start_up_by_name(changes: dict[str, str], message: str) -> None:
    with pytest.raises(SettingsError, match=f"^{message}$"):
        Settings.from_env(shipped(**GEMINI, **changes))


def test_488_on_without_a_model_reading_the_pages_stops_start_up() -> None:
    with pytest.raises(SettingsError, match="OCR_ULTRA_LOW_COST=true needs OCR_ENGINE=gemini"):
        Settings.from_env(shipped(OCR_ENGINE="rapidocr", OCR_ULTRA_LOW_COST="true"))
    assert (
        Settings.from_env(shipped(OCR_ENGINE="rapidocr", OCR_ULTRA_LOW_COST="false")).ocr_ultra_low_cost
        is False
    )


# The worker's start-up check of OCR_MODEL.


class _Db:
    """A database whose catalogue answers ``problem`` (models.ulc_not_ready is patched)."""

    @contextlib.contextmanager
    def system(self) -> Iterator[None]:
        yield None


def _check(settings: Settings, problem: str | None) -> Any:
    with mock.patch("kaagaz.db.models.ulc_not_ready", return_value=problem) as asked:
        worker_main.ulc_check(settings, _Db())  # type: ignore[arg-type]
    return asked


def test_488_off_the_worker_never_asks_the_catalogue() -> None:
    assert not _check(make_settings(), "never asked").called


def test_488_on_a_ready_ocr_model_starts_and_an_unready_one_stops_start_up_saying_why() -> None:
    on = make_settings(ocr_engine="gemini", openrouter_api_key=KEY, ocr_ultra_low_cost=True)
    assert _check(on, None).called
    with pytest.raises(worker_main.UlcNotReady, match="can't take minimal thinking"):
        _check(on, "OCR_MODEL google/x can't take minimal thinking (catalogue efforts: ['low'])")


def test_488_the_worker_stops_with_one_critical_line_naming_the_fix(caplog: pytest.LogCaptureFixture) -> None:
    why = "OCR_MODEL google/x can't take minimal thinking (catalogue efforts: ['low'])"
    with (
        mock.patch.object(Settings, "from_env", return_value=make_settings()),
        mock.patch.object(worker_main, "configure_logging"),
        mock.patch.object(worker_main, "_serve", side_effect=worker_main.UlcNotReady(why)),
        caplog.at_level(logging.DEBUG, "kaagaz.worker"),
        pytest.raises(SystemExit) as ended,
    ):
        worker_main.main()
    assert ended.value.code == 1
    [record] = [record for record in caplog.records if record.name == "kaagaz.worker"]
    assert (record.levelname, record.getMessage(), record.__dict__["code"]) == (
        "CRITICAL",
        f"worker cannot start: OCR_ULTRA_LOW_COST: {why}",
        "ulc_not_ready",
    )


def test_488_the_check_runs_before_the_worker_takes_a_job() -> None:
    class Stop(Exception):
        pass

    order: list[str] = []
    with (
        mock.patch.object(worker_main, "make_engine"),
        mock.patch.object(worker_main, "Database", return_value=mock.Mock(spec=Database)),
        mock.patch.object(worker_main, "ulc_check", side_effect=lambda *_: order.append("check")),
        mock.patch.object(worker_main, "S3Storage", side_effect=Stop),
        pytest.raises(Stop),
    ):
        worker_main._serve(make_settings())
    assert order == ["check"]
