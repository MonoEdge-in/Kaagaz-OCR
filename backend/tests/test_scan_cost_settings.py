"""The scan cost settings without a database (#463, Tushar, 31 Oct 2026): the ``Reading``
carries the organisation's picture detail and Careful pair, Ensemble reads at its scan level,
the reader sends them (None: the env's) on every path, a saved detail keys the page cache
apart, and the ledger records what a page read was sent and nothing for other work. The
SQL, the routes and the end-to-end check: test_scan_cost_settings_db."""

import dataclasses
from typing import Any, cast

from kaagaz.ai import usage
from kaagaz.db import models
from kaagaz.reading.gemini_ocr import CROPS_SCHEMA
from kaagaz.reading.ocr import READER_PROMPT_VERSION, DocumentPages
from tests.openrouter_fake import OpenRouterFake, json_reply
from tests.settings_factory import make_settings
from tests.test_ai_resolve import FakeConn, document, row
from tests.test_cost_saving import HARD, WORDS, Client, first_reply, reader
from tests.test_dynamic_reading import CAREFUL, STANDARD, ledger
from tests.test_fast_scan import inline_pages
from tests.test_gemini_ocr import FakeCache, page, reply, value
from tests.test_openrouter_client import GOOD, SCHEMA, Clock, _client, _read, fake

__all__ = ["fake"]  # the fixture, used by name
GEMINI = make_settings(ocr_engine="gemini")
SAVED: dict[str, Any] = {
    "scan_resolution": "low",
    "careful_thinking": "minimal",
    "careful_resolution": "high",
    "page_max_output_tokens": None,
}
LEAD = "google/gemini-3.8-flash"


def sent(client: Client) -> list[tuple[str, str, str, str]]:
    """Each call as (model, thinking, picture detail, page or crops)."""
    return [
        (
            r["model"],
            r["thinking_level"],
            r["media_resolution"],
            "crops" if r["schema"] is CROPS_SCHEMA else "page",
        )
        for r in client.requests
    ]


# --- the Reading -------------------------------------------------------------------------


def test_the_reading_carries_the_effective_picture_detail_and_careful_pair() -> None:
    rows = [row(LEAD, default=True, efforts=("minimal", "low", "high"))]
    for mode in ("single", "dynamic"):
        conn = FakeConn(scan=rows, settings={**SAVED, "dynamic": True}, document=document(mode=mode))
        reading = models.resolve_scan(conn.conn(), "c", "d", settings=GEMINI)
        assert (reading.resolution, reading.careful_thinking, reading.careful_resolution) == (
            "low",
            "minimal",
            "high",
        ), mode
    nothing = models.resolve_scan(FakeConn(scan=rows, document=document()).conn(), "c", "d", settings=GEMINI)
    assert (nothing.resolution, nothing.careful_thinking, nothing.careful_resolution) == (None, None, None)


def test_dynamic_keeps_a_minimal_first_read_and_reads_low_otherwise() -> None:
    every = [row(LEAD, default=True, efforts=("minimal", "low", "medium", "high"))]
    for level, first in (("minimal", "minimal"), ("medium", "low"), ("high", "low"), (None, "low")):
        conn = FakeConn(
            scan=every, settings={"scan_thinking": level, "dynamic": True}, document=document(mode="dynamic")
        )
        assert models.resolve_scan(conn.conn(), "c", "d", settings=GEMINI).thinking == first, level
    # The estimate's Reading follows the same rule (no document, the default model).
    # Changed on purpose (#473): the effective settings carry the page reader and Mistral model.
    allowed = {
        "scan_thinking": "minimal",
        "dynamic": True,
        **dict.fromkeys(SAVED),
        "scan_reader": None,
        "mistral_model": None,
    }
    assert models.scope_reading(cast(Any, every), allowed, settings=GEMINI).thinking == "minimal"
    assert (
        models.scope_reading(cast(Any, every), {**allowed, "dynamic": False}, settings=GEMINI).careful is None
    )


def test_ensemble_reads_at_the_organisations_scan_level_and_detail_never_above() -> None:
    rows = [row(LEAD, default=True), row(CAREFUL)]
    settings = {"ensemble": True, "scan_thinking": "low", "dynamic": True, **SAVED}
    reading = models.resolve_ensemble(
        FakeConn(scan=rows, settings=settings, document=document(LEAD, "dynamic")).conn(),
        "c",
        "d",
        settings=GEMINI,
    )
    assert (reading.thinking, reading.resolution) == ("low", "low")
    plain = models.resolve_ensemble(
        FakeConn(scan=rows, settings={"ensemble": True}, document=document(LEAD)).conn(),
        "c",
        "d",
        settings=GEMINI,
    )
    assert (plain.thinking, plain.resolution) == (GEMINI.ocr_thinking_level, None)  # the env's


# --- the reader --------------------------------------------------------------------------


def test_every_careful_path_sends_the_careful_pair_and_the_first_read_its_detail() -> None:
    client = Client({STANDARD: first_reply(HARD)}, crops=WORDS)
    pages = dataclasses.replace(
        inline_pages(3), resolution="low", careful_thinking="medium", careful_resolution="low"
    )
    one = reader(client)
    with one.document(pages):
        one.read_page(page(), page_no=1)
    assert sent(client) == [(STANDARD, "low", "low", "page"), (CAREFUL, "medium", "low", "crops")]


def test_without_saved_values_the_env_is_sent_as_before() -> None:
    client = Client({STANDARD: first_reply(HARD)}, crops=WORDS)
    one = reader(client)
    with one.document(inline_pages(3)):
        one.read_page(page(), page_no=1)
    settings = make_settings()
    assert sent(client) == [
        (STANDARD, "low", settings.ocr_media_resolution, "page"),
        (CAREFUL, settings.ocr_retry_thinking_level, settings.ocr_media_resolution, "crops"),
    ]


def test_a_page_read_again_sends_the_careful_pair_and_a_saved_detail_is_cached_apart() -> None:
    client = Client({STANDARD: reply(value())})
    cache = FakeCache()
    pages = DocumentPages(
        customer_id="c",
        document_id="d",
        cache=cache,
        model=STANDARD,
        read_again=frozenset({1}),
        resolution="low",
        careful_thinking="minimal",
        careful_resolution="low",
    )
    one = reader(client)
    with one.document(pages):
        one.read_page(page(), page_no=1)
    assert sent(client)[0] == (STANDARD, "minimal", "low", "page")
    (key,) = cache.puts
    assert key.prompt_version == f"{READER_PROMPT_VERSION}+low"  # the env's detail is high
    with one.document(dataclasses.replace(pages, resolution=None, read_again=frozenset())):
        one.read_page(page(), page_no=1)
    assert cache.puts[-1].prompt_version == READER_PROMPT_VERSION  # old entries stay valid


# --- the ledger --------------------------------------------------------------------------


def test_the_ledger_keeps_a_page_reads_settings_and_none_for_other_work() -> None:
    with ledger() as kept:
        usage.record_call(LEAD, None, 1, 1, 0, 0.1, thinking="minimal", resolution="low")
        usage.record_call(LEAD, None, 1, 1, 0, 0.1, thinking="high", resolution="low")
        with usage.billed_as("map"):
            usage.record_call(LEAD, None, 1, 1, 0, 0.1, thinking="low", resolution="high")
    got = sorted((r.kind, r.thinking, r.resolution, r.calls) for r in kept.rows)
    assert got == [("map", None, None, 1), ("scan", "high", "low", 1), ("scan", "minimal", "low", 1)]


def test_the_page_call_records_what_it_sent_and_a_chat_call_nothing(fake: OpenRouterFake) -> None:
    fake.script("completions", json_reply(GOOD), json_reply(GOOD))
    client = _client(fake.url, Clock())
    with ledger() as kept:
        _read(client, thinking_level="minimal", media_resolution="low")
        with usage.billed_as("map"):
            client.chat_json(system="s", user="u", schema=SCHEMA)
    got = sorted((r.kind, r.thinking, r.resolution) for r in kept.rows)
    assert got == [("map", None, None), ("scan", "minimal", "low")]
