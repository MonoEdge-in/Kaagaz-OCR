"""A page read's output limit (#463, Tushar, 31 Oct 2026; 0064) without a database: the
organisation's ``page_max_output_tokens`` rides on the ``Reading`` and reaches
max_completion_tokens on every page read of its documents (the first read, Careful's whole
page and words, a page read again, Ensemble's), the default is PAGE_MAX_OUTPUT_TOKENS, a reply
cut off at it is still read in halves and counted on the ledger as a limit hit, and the
operator's saves refuse a limit outside 1024 to 65536 before a code is used up. The SQL and
the tokens figures: test_page_output_limit_db."""

import dataclasses
from typing import Any

import pytest

from kaagaz.ai.openrouter import PAGE_MAX_OUTPUT_TOKENS
from kaagaz.ai.replies import ModelOutputError
from kaagaz.db import models
from kaagaz.reading.gemini_ocr import CROPS_SCHEMA
from kaagaz.reading.ocr import DocumentPages
from tests.openrouter_fake import OpenRouterFake, cut_off, json_reply
from tests.settings_factory import make_settings
from tests.test_ai_resolve import FakeConn, document, row
from tests.test_cost_saving import HARD, WORDS, Client, first_reply, reader
from tests.test_dynamic_reading import CAREFUL, STANDARD, ledger
from tests.test_fast_scan import inline_pages
from tests.test_gemini_ocr import CUSTOMER, DOCUMENT, FakeCache, build, page, reply, value
from tests.test_openrouter_client import Clock, _client, _read, fake
from tests.test_operator_models import CID, DEFAULTS_BODY, ORG_BODY, _Offline, offline

__all__ = ["fake", "offline"]  # the fixtures, used by name
GEMINI = make_settings(ocr_engine="gemini")
LIMIT = 4096


def limits(client: Client) -> list[tuple[str, str, int | None]]:
    return [
        (r["model"], "crops" if r["schema"] is CROPS_SCHEMA else "page", r.get("max_output_tokens"))
        for r in client.requests
    ]


def test_the_reading_carries_the_organisations_limit_ensembles_too() -> None:
    rows = [row(STANDARD, default=True), row(CAREFUL)]
    settings = {"page_max_output_tokens": LIMIT, "ensemble": True}
    conn = FakeConn(scan=rows, settings=settings, document=document(STANDARD))
    assert models.resolve_scan(conn.conn(), "c", "d", settings=GEMINI).page_max_output_tokens == LIMIT
    assert models.resolve_ensemble(conn.conn(), "c", "d", settings=GEMINI).page_max_output_tokens == LIMIT
    plain = FakeConn(scan=rows, document=document())
    assert models.resolve_scan(plain.conn(), "c", "d", settings=GEMINI).page_max_output_tokens is None


def test_every_page_read_of_the_document_is_sent_its_limit() -> None:
    # The first read and Careful's words (crops).
    client = Client({STANDARD: first_reply(HARD)}, crops=WORDS)
    one = reader(client)
    with one.document(dataclasses.replace(inline_pages(3), max_output_tokens=LIMIT)):
        one.read_page(page(), page_no=1)
    assert limits(client) == [(STANDARD, "page", LIMIT), (CAREFUL, "crops", LIMIT)]

    # Careful's second reading of a long document (its words again) and a page read again.
    whole = Client({STANDARD: first_reply(HARD), CAREFUL: reply(value())}, crops=WORDS)
    two = reader(whole)
    pages = DocumentPages(
        customer_id=CUSTOMER,
        document_id=DOCUMENT,
        cache=FakeCache(),
        model=STANDARD,
        careful=(CAREFUL, "google-vertex"),
        memo={},
        max_output_tokens=LIMIT,
    )
    with two.document(pages):
        two.read_page(page(), page_no=1)
    with two.document(dataclasses.replace(pages, escalate=frozenset({1}))):
        two.read_page(page(), page_no=1)
    with two.document(dataclasses.replace(pages, memo=None, read_again=frozenset({1}))):
        two.read_page(page(), page_no=1)
    assert {limit for _model, _kind, limit in limits(whole)} == {LIMIT}
    assert [model for model, _kind, _limit in limits(whole)] == [STANDARD, CAREFUL, STANDARD]
    # Careful's whole page: a page-wide problem reads it whole.
    poor = Client({STANDARD: first_reply(HARD, quality="poor"), CAREFUL: reply(value())})
    five = reader(poor)
    with five.document(dataclasses.replace(inline_pages(3), max_output_tokens=LIMIT)):
        five.read_page(page(), page_no=1)
    assert limits(poor) == [(STANDARD, "page", LIMIT), (CAREFUL, "page", LIMIT)]

    # Ensemble's other models read inside the same document, with the same limit.
    others = Client({STANDARD: reply(value()), CAREFUL: reply(value())})
    three = reader(others)
    with three.document(
        dataclasses.replace(
            pages, cache=FakeCache(), memo=None, careful=None, others=((CAREFUL, "google-vertex"),)
        )
    ):
        three.read_page(page(), page_no=1)
    assert sorted(limits(others)) == [(CAREFUL, "page", LIMIT), (STANDARD, "page", LIMIT)]

    # Nothing set: the client is sent None, which is PAGE_MAX_OUTPUT_TOKENS (below).
    plain = Client({STANDARD: reply(value())})
    four = reader(plain)
    with four.document(
        dataclasses.replace(pages, cache=FakeCache(), memo=None, careful=None, max_output_tokens=None)
    ):
        four.read_page(page(), page_no=1)
    assert limits(plain) == [(STANDARD, "page", None)]


def test_a_reply_cut_off_at_the_limit_is_still_read_in_halves() -> None:
    built = build(ModelOutputError("output_truncated"), reply(value()), reply(value()))
    pages = DocumentPages(
        customer_id=CUSTOMER, document_id=DOCUMENT, cache=FakeCache(), max_output_tokens=2048
    )
    with built.engine.document(pages):
        read = built.engine.read_page(page(), page_no=1)
    assert len(built.client.calls) == 3  # the whole page, then its two halves
    assert {call.max_output_tokens for call in built.client.calls} == {2048}
    assert read.read_by == built.settings.ocr_model  # never failed for it


def test_the_client_sends_the_limit_and_counts_a_cut_off_reply_as_a_limit_hit(fake: OpenRouterFake) -> None:
    fake.script("completions", json_reply({"status": "answered", "lines": []}), cut_off())
    client = _client(fake.url, Clock())
    with ledger() as kept:
        _read(client, max_output_tokens=LIMIT)
        with pytest.raises(ModelOutputError) as cut:
            _read(client, thinking_level="high")
    assert cut.value.code == "output_truncated"
    sent = [request.body["max_completion_tokens"] for request in fake.requests]
    assert sent == [LIMIT, PAGE_MAX_OUTPUT_TOKENS]
    hits = sorted((row.thinking, row.calls, row.limit_hits) for row in kept.rows)
    assert hits == [("high", 1, 1), ("low", 1, 0)]


@pytest.mark.parametrize("limit", [1023, 65537, 0, -1])
def test_a_limit_outside_1024_to_65536_is_refused_before_a_code_is_used(
    offline: _Offline, limit: int
) -> None:
    for path, body in (
        (f"/api/operator/orgs/{CID}/ai", ORG_BODY),
        ("/api/operator/ai-defaults", DEFAULTS_BODY),
    ):
        sent: dict[str, Any] = {**body, "page_max_output_tokens": limit, "code": offline.code()}
        reply_ = offline.client.put(path, json=sent, headers=offline.headers)
        assert (reply_.status_code, reply_.json()["error"]["code"]) == (400, "limit_out_of_range"), path
    assert offline.calls == []


def test_the_limit_is_saved_and_shown_with_the_servers(offline: _Offline) -> None:
    path = f"/api/operator/orgs/{CID}/ai"
    put = offline.client.put(path, json={**ORG_BODY, "code": offline.code()}, headers=offline.headers)
    assert put.status_code == 200, put.text
    [(_, args, _kwargs)] = [call for call in offline.calls if call[0] == "set_org_ai"]
    assert args[2].page_max_output_tokens == ORG_BODY["page_max_output_tokens"]
    got = offline.client.get("/api/operator/ai-defaults", headers=offline.headers).json()
    assert got["env"]["page_max_output_tokens"] == PAGE_MAX_OUTPUT_TOKENS
    assert "page_max_output_tokens" in got["orgs_inheriting"]
