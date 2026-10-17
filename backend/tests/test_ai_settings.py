"""AI settings by task, the feature switches and the platform test model (0038), without a
database: the rule (switch, ceiling, lock, member; nothing saved is today's behaviour), the
scan resolver's mapping, Ask at None (no model call), the worker's Scanning with AI switch,
the local reader for a scan at None, and the area query's test model. The SQL, the API and
the isolation: test_ai_settings_db, test_isolation_ai_task_settings, test_migration_0038_db."""

from typing import Any

import pytest
from PIL import Image

from kaagaz.answering.service import AnswerService
from kaagaz.db import ai_settings, models
from kaagaz.db.ai_settings import resolve
from kaagaz.reading.ocr import DocumentPages
from kaagaz.worker_main import FEATURE_OFF_DEFER_SECONDS, admission
from tests.settings_factory import make_settings
from tests.test_ai_resolve import FLASH, PRO, FakeConn, row, scan
from tests.test_answering_golden import (
    NOW,
    UNUSED_DB,
    A,
    FakeFixedQueries,
    FakeModel,
    FakeRetriever,
    FakeUnchecked,
    piece,
)
from tests.test_answering_golden import SETTINGS as ANSWER_SETTINGS
from tests.test_gemini_ocr import CUSTOMER, DOCUMENT, FakeCache, build
from tests.test_worker_defer import A1, MemoryLocks, Run, fair_share, working

GEMINI = make_settings(ocr_engine="gemini", ocr_thinking_level="high")
STEPS = ai_settings.STEPS


# --- the rule ---------------------------------------------------------------------------------


def test_nothing_saved_is_todays_level_and_not_chosen() -> None:
    task = resolve("scan", {}, STEPS, "low")
    assert (task.effective, task.mine, task.organisation, task.locked, task.chosen) == (
        "low",
        None,
        None,
        False,
        False,
    )


@pytest.mark.parametrize(
    ("saved", "allowed", "effective"),
    [
        ({("ask", True): ("high", False)}, STEPS, "high"),  # the organisation's default
        ({("ask", True): ("high", False), ("ask", False): ("none", False)}, STEPS, "none"),  # mine wins
        ({("ask", True): ("low", True), ("ask", False): ("max", False)}, STEPS, "low"),  # locked wins
        ({("ask", False): ("max", False)}, STEPS[:2], "low"),  # the ceiling caps the choice
        ({("ask", True): ("max", True)}, STEPS[:2], "low"),  # and the locked default
        ({("ask", False): ("high", False)}, ("none",), "none"),  # a switch that leaves None only
    ],
)
def test_effective_is_the_lowest_of_ceiling_lock_and_choice(
    saved: ai_settings.Saved, allowed: tuple[str, ...], effective: str
) -> None:
    task = resolve("ask", saved, allowed, "low")
    assert (task.effective, task.chosen) == (effective, True)


def test_the_view_never_names_a_model_or_a_price() -> None:
    view = resolve("scan", {("scan", True): ("high", True)}, STEPS, "low").view()
    assert view == {
        "task": "scan",
        "effective": "high",
        "mine": None,
        "organisation": "high",
        "locked": True,
        "allowed": list(STEPS),
        "available": True,
    }


def test_the_ceilings() -> None:
    dynamic = {"scan_thinking": None, "dynamic": True, "chat_levels": ["low"], "chat_default_level": "low"}
    assert ai_settings.scan_ceiling(dynamic, GEMINI) == (STEPS, "low")
    assert ai_settings.scan_ceiling({**dynamic, "dynamic": False}, GEMINI) == (STEPS, "high")
    assert ai_settings.scan_ceiling({**dynamic, "scan_thinking": "medium"}, GEMINI) == (STEPS[:2], "low")
    assert ai_settings.scan_ceiling(dynamic, make_settings(ocr_engine="rapidocr")) == (("none",), "none")
    assert ai_settings.ask_ceiling(dynamic) == (STEPS[:2], "low")
    assert ai_settings.ask_ceiling({"chat_levels": ["low", "high"], "chat_default_level": "high"}) == (
        STEPS,
        "high",
    )
    assert ai_settings.grant_ceiling(True, False) == (("off", "on"), "off")
    assert ai_settings.grant_ceiling(False, True) == (("off",), "off")


def test_ask_thinking_is_capped_never_raised() -> None:
    assert ai_settings.ask_thinking("low", ["low", "high"], None) == "low"
    assert ai_settings.ask_thinking("high", ["low", "high"], None) == "high"
    assert ai_settings.ask_thinking("max", ["low", "high"], "high") == "high"
    assert ai_settings.ask_thinking("low", ["low", "high"], "high") == "low"  # the request is capped
    assert ai_settings.ask_thinking("high", ["low", "medium", "high"], "medium") == "medium"
    assert ai_settings.ask_thinking("low", ["medium", "high"], None) == "medium"  # the nearest allowed


def test_max_answers_on_the_strongest_model_that_takes_the_level() -> None:
    rows = [
        row("google/cheap", default=True, area="chat"),
        row("google/dear", price=(10, 40), area="chat"),
        row("google/dearest-no-high", price=(20, 80), efforts=["low"], area="chat"),
    ]
    assert models.strongest_chat(rows, "high") == ["google/dear"]
    assert models.strongest_chat([], "high") is None


# --- the scan resolver -------------------------------------------------------------------------


def _document(*saved: tuple[bool, str, bool], read_model: str | None = None, mode: str = "dynamic") -> Any:
    return {"read_model": read_model, "read_mode": mode, "scan_settings": [list(item) for item in saved]}


DYNAMIC_ON = {"scan_thinking": "high", "dynamic": True}
TWO_TIERS = [row(FLASH, default=True), row(PRO, price=(10, 40))]


def test_scan_none_reads_with_the_local_reader_even_for_an_explicit_choice() -> None:
    for read_model in (None, PRO):
        conn = FakeConn(
            scan=TWO_TIERS,
            settings=DYNAMIC_ON,
            document=_document((True, "none", False), read_model=read_model),
        )
        assert scan(conn, GEMINI).mode == "local"


def test_scan_levels_map_to_todays_readings() -> None:
    low = scan(
        FakeConn(
            scan=TWO_TIERS, settings=DYNAMIC_ON, document=_document((False, "low", False), mode="single")
        ),
        GEMINI,
    )
    assert (low.mode, low.thinking) == ("dynamic", "low")
    high = scan(
        FakeConn(scan=TWO_TIERS, settings=DYNAMIC_ON, document=_document((True, "high", False))), GEMINI
    )
    assert (high.mode, high.thinking, high.careful) == ("single", "high", None)
    top = scan(
        FakeConn(scan=TWO_TIERS, settings=DYNAMIC_ON, document=_document((True, "max", False))), GEMINI
    )
    assert (top.mode, top.thinking, top.careful and top.careful.model) == ("dynamic", "high", PRO)


def test_max_never_turns_on_a_dynamic_the_super_admin_took_away() -> None:
    top = scan(
        FakeConn(
            scan=TWO_TIERS,
            settings={**DYNAMIC_ON, "dynamic": False},
            document=_document((True, "max", False)),
        ),
        GEMINI,
    )
    assert (top.mode, top.thinking) == ("single", "high")


def test_an_explicit_read_with_wins_over_a_level_above_none() -> None:
    reading = scan(
        FakeConn(
            scan=TWO_TIERS,
            settings=DYNAMIC_ON,
            document=_document((True, "max", False), read_model=PRO, mode="single"),
        ),
        GEMINI,
    )
    assert (reading.mode, reading.lead.model) == ("single", PRO)


def test_nothing_saved_reads_as_before() -> None:
    reading = scan(FakeConn(scan=TWO_TIERS, settings=DYNAMIC_ON, document=_document()), GEMINI)
    assert (reading.mode, reading.thinking) == ("dynamic", "low")


def test_a_scan_level_above_the_ceiling_is_capped() -> None:
    capped = scan(
        FakeConn(
            scan=TWO_TIERS,
            settings={"scan_thinking": "medium", "dynamic": True},
            document=_document((False, "max", False)),
        ),
        GEMINI,
    )
    assert capped.mode == "dynamic" and capped.thinking == "low"  # max -> low (Automatic)


def test_the_area_query_uses_the_test_model_for_every_organisation_while_it_is_set() -> None:
    for column, statement in (
        ("t.scan_model AS model", models._SCAN),
        ("t.chat_model AS model", models._CHAT),
    ):
        sql = str(statement)
        assert column + ", true AS is_default FROM platform_model_override t" in sql
        assert sql.count("NOT EXISTS (SELECT 1 FROM test)") == 2  # neither the own list nor the platform's


def test_the_cost_saving_and_ensemble_switches_are_inside_the_ceiling() -> None:
    sql = str(models._SETTINGS)
    assert "COALESCE(w.cost_saving, true) AS dynamic" in sql
    assert "COALESCE(s.ensemble, false) AND COALESCE(w.ensemble, true) AS ensemble" in sql


# --- Ask at None ---------------------------------------------------------------------------------


def test_ask_at_none_calls_no_model_and_quotes_the_lines() -> None:
    model = FakeModel()
    service = AnswerService(
        db=UNUSED_DB,
        model=model,
        settings=ANSWER_SETTINGS,
        clock=lambda: NOW,
        retriever=FakeRetriever({A: [piece("deed.pdf", "The shop on Jalna road is leased to Patil.")]}),
        fixed_queries=FakeFixedQueries({A: []}),
        unchecked=FakeUnchecked(),
    )
    answer = service.answer(A, "Who leased the shop on Jalna road?", choice=models.NO_MODEL)
    assert (model.prompts, model.embedded) == ([], [])
    assert answer.status == "answered"
    service.answer(A, "Who leased the shop on Jalna road?")
    assert model.prompts  # the control: the same service calls the model without the setting


# --- Scanning with AI switched off ----------------------------------------------------------------


def test_scanning_switched_off_hands_every_job_back_uncounted_and_asks_again_each_job() -> None:
    asked: list[bool] = []
    state = {"on": False}

    def scanning_on() -> bool:
        asked.append(state["on"])
        return state["on"]

    locks = MemoryLocks()
    run = Run(A1, admit=admission(working, fair_share(locks).slot, scanning_on=scanning_on), locks=locks)
    assert run.worker.run_once()
    assert (run.ran, run.tries, run.queue.deferred) == ([], [], [(A1, FEATURE_OFF_DEFER_SECONDS)])
    state["on"] = True  # switched back on: the job continues, its first counted try
    run.clock.advance(FEATURE_OFF_DEFER_SECONDS)
    assert run.worker.run_once()
    assert (run.ran, run.tries, asked) == ([A1], [(A1, 1)], [False, True])


# --- the local reader for a scan at None -----------------------------------------------------------


def test_a_document_read_at_none_calls_no_model() -> None:
    built = build()  # any model call fails: nothing is scripted
    image = Image.new("RGB", (300, 120), "white")
    pages = DocumentPages(customer_id=CUSTOMER, document_id=DOCUMENT, cache=FakeCache(), local=True)
    with built.engine.document(pages):
        read = built.engine.read_page(image, page_no=2)
    assert (built.client.calls, built.local.pages, read.page_no) == ([], 1, 2)
    assert "read_locally" not in read.issues and "reader_refused" not in read.issues  # never upgraded
