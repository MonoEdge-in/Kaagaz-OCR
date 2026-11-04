"""Reading under Ultra low cost (#488, production port, OCR_ULTRA_LOW_COST): each page in the
compact reply, at minimal thinking, with every call held under the page's ceiling.

Ported from Testing-Env's tests (#508, #510). The Gemini reader over the real OpenRouter client
against the loopback fake (never the real API), so the ceiling the client enforces (the output
limit of each call, the repair, a billed mid-reply error) is what is tested. Production's
catalogue has no request or image price, so each call keeps ULC_CALL_RESERVE_SHARE of the
ceiling back for one, and the reply's usage.cost is charged when it says more (shortcut, until
the Testing-Env release lands). The proof at the end bills hidden prices a call too.
"""

import contextlib
import dataclasses
import json
import logging
import math
import random
from collections.abc import Iterator
from typing import Any

import pytest
from PIL import Image

from kaagaz.ai.client import estimate_tokens
from kaagaz.ai.openrouter import ModelGate, OpenRouterClient, PageBudget
from kaagaz.ai.replies import ModelUnavailable
from kaagaz.db.models import Agent, Reading
from kaagaz.ingestion.errors import BackendUnavailable
from kaagaz.pipeline import JobReading
from kaagaz.reading.compact import COMPACT_SCHEMA, INSTRUCTION_COMPACT
from kaagaz.reading.gemini_ocr import (
    G3_PAGE_INPUT_TOKENS,
    INSTRUCTION,
    ULC_CALL_RESERVE_SHARE,
    ULC_INPUT_TOKENS,
    GeminiEngine,
    as_result,
    checked,
    pixels_sha256,
    png_of,
)
from kaagaz.reading.ocr import (
    COMPACT_PROMPT_VERSION,
    INR_PER_USD,
    DocumentPages,
    UlcCeiling,
    UlcPrices,
    as_page_engine,
)
from kaagaz.reading.page_quality import READER_REFUSED
from tests.openrouter_fake import (
    KEY,
    OpenRouterFake,
    Recorded,
    Reply,
    chat_reply,
    closed_port_url,
    json_reply,
    running_fake,
)
from tests.openrouter_fake import (
    usage as counts,
)
from tests.pdf_builder import make_pdf
from tests.settings_factory import make_settings
from tests.test_dynamic_reading import ledger
from tests.test_gemini_ocr import CUSTOMER, DOCUMENT, FakeCache, FakeFinder, FakeRapidOcr, line, value
from tests.test_pipeline_gemini import SCANNED, TOTAL, FakeChecker, ScriptedReader, page_read, rig

FLASH = "google/gemini-3.5-flash-lite"
PRO = "google/gemini-3.1-pro-preview"
PRICES = UlcPrices(price_in_usd_per_m=0.30, price_out_usd_per_m=2.50, tokens_per_page_in=2500)
# The first call's input estimate: the catalogue's tokens a page in, 15% more, never below ULC_INPUT_TOKENS.
FIRST = max(math.ceil(2500 * 1.15), ULC_INPUT_TOKENS)
PAGE_R = "g 0 l -\nt 40 100 70 900 Survey 42\np 100 100 120 900 Owner Ramesh"


@pytest.fixture
def fake() -> Iterator[OpenRouterFake]:
    with running_fake() as server:
        yield server


class Rig:
    """The reader on a fresh gate (no waits between retries) and a fake local reader."""

    def __init__(
        self, fake: OpenRouterFake, *found: tuple[float, float, float, float], url: str = ""
    ) -> None:
        settings = make_settings(
            openrouter_api_key=KEY,
            openrouter_base_url=url or fake.url,
            ocr_model=FLASH,
            ocr_timeout_seconds=5.0,
            model_timeout_seconds=5.0,
            model_rpm=10**9,
        )
        gate = ModelGate(settings, workers=1, sleep=lambda _seconds: None, random=lambda: 0.0)
        self.local = FakeRapidOcr()
        self.finder = FakeFinder(*found)
        self.engine = GeminiEngine(
            settings,
            client=OpenRouterClient(settings, gate=gate),
            pool=self.finder,
            local=lambda: as_page_engine(self.local),
        )

    def read(self, pages: DocumentPages, page_no: int = 1, image: Image.Image | None = None) -> Any:
        with self.engine.document(pages):
            return self.engine.read_page(image or page(), page_no=page_no)


def page(shade: int = 255) -> Image.Image:
    return Image.new("RGB", (1000, 2000), (shade, shade, shade))


def ceiling(paise: int = 55) -> UlcCeiling:
    return UlcCeiling(usd=paise / 100 / INR_PER_USD, paise=paise)


def ulc(
    cache: FakeCache | None = None, *, paise: int = 55, prices: UlcPrices | None = PRICES, **more: Any
) -> DocumentPages:
    fields: dict[str, Any] = {
        "model": FLASH,
        "provider": "google-vertex",
        "thinking": "minimal",
        "page_count": 1,
    }
    return DocumentPages(
        customer_id=CUSTOMER,
        document_id=DOCUMENT,
        cache=cache or FakeCache(),
        ulc=ceiling(paise),
        ulc_prices=prices,
        **{**fields, **more},
    )


def compact(
    r: str = PAGE_R, prompt: int = 2400, completion: int = 300, cost: float | None = None, **options: Any
) -> Reply:
    return json_reply({"r": r}, counts=counts(prompt, completion, 0, cost), **options)


def reserve(usd: float) -> float:
    """What each call keeps back for a price a call that is not known (shortcut)."""
    return ULC_CALL_RESERVE_SHARE * usd


def cap_for(left: float, tokens_in: int, prices: UlcPrices = PRICES, *, usd: float | None = None) -> int:
    """The output limit a call taking in ``tokens_in`` gets with ``left`` dollars left of a
    ceiling of ``usd`` (None: ``left``, the page's first call)."""
    kept = reserve(left if usd is None else usd)
    return math.floor(
        (left - (kept + tokens_in * (prices.price_in_usd_per_m / 1e6))) / (prices.price_out_usd_per_m / 1e6)
    )


def spent(prompt: int, completion: int, prices: UlcPrices = PRICES) -> float:
    return (prompt * prices.price_in_usd_per_m + completion * prices.price_out_usd_per_m) / 1e6


# One call, the compact reply, minimal thinking.


def test_488_a_page_is_read_in_one_compact_call_at_minimal_thinking(fake: OpenRouterFake) -> None:
    fake.script("completions", compact())
    read = Rig(fake).read(ulc())
    [body] = fake.bodies()
    assert body["reasoning"] == {"effort": "minimal", "exclude": True}
    assert body["response_format"]["json_schema"]["schema"] == COMPACT_SCHEMA
    assert body["messages"][0]["content"][0]["text"] == INSTRUCTION_COMPACT
    assert body["messages"][0]["content"][1]["image_url"]["detail"] == "high"
    assert body["max_completion_tokens"] == cap_for(ceiling().usd, FIRST)
    assert [item.text for item in read.lines] == ["Survey 42", "Owner Ramesh"]
    assert read.read_by == FLASH
    assert (read.input_tokens, read.output_tokens) == (2400, 300)


@pytest.mark.parametrize("asked", ["high", "low", None])
def test_488_effort_minimal_is_always_sent_also_for_a_page_read_again(
    fake: OpenRouterFake, asked: str | None
) -> None:
    fake.script("completions", compact(), compact())
    rig = Rig(fake)
    rig.read(ulc(thinking=asked))
    rig.read(ulc(thinking=asked, read_again=frozenset({1})))
    assert [body["reasoning"] for body in fake.bodies()] == [{"effort": "minimal", "exclude": True}] * 2
    assert [body["messages"][0]["content"][1]["image_url"]["detail"] for body in fake.bodies()] == [
        "high"
    ] * 2


def test_488_a_lead_that_cannot_think_minimal_is_refused_with_nothing_sent(
    fake: OpenRouterFake, caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level(logging.WARNING), ledger() as kept:
        read = Rig(fake).read(ulc(plain=frozenset({FLASH})))
    assert fake.requests == [] and kept.rows == []
    assert (read.read_by, read.issues[0]) == ("rapidocr", READER_REFUSED)
    assert [record.code for record in caplog.records if hasattr(record, "code")] == ["thinking_fallback"]


def test_488_careful_ensemble_routing_and_regions_are_never_used(fake: OpenRouterFake) -> None:
    fake.script("completions", compact())
    cache = FakeCache()
    rig = Rig(fake)
    read = rig.read(
        ulc(
            cache,
            careful=(PRO, "google-vertex"),
            escalate=frozenset({1}),
            memo={},
            inline_careful=True,
            escalated={},
            others=((PRO, "google-vertex"),),
            route_pages=True,
            routed={},
            read_again=frozenset({1}),
            read_again_regions={1: ((0.0, 0.0, 1.0, 0.5),)},
        )
    )
    [body] = fake.bodies()
    assert body["model"] == FLASH and body["messages"][0]["content"][0]["text"] == INSTRUCTION_COMPACT
    assert rig.local.pages == 0  # no local read first either (Save's routing)
    assert read.others == () and read.read_by == FLASH


# The cache.


def test_488_cache_keys_never_cross_with_the_g3_reads_or_another_ceiling(fake: OpenRouterFake) -> None:
    cache, image = FakeCache(), page()
    sha = pixels_sha256(image)
    rig = Rig(fake)
    fake.script("completions", json_reply(value(line("g3 read"))))
    with rig.engine.document(dataclasses.replace(ulc(cache), ulc=None, ulc_prices=None, thinking=None)):
        rig.engine.read_page(image, page_no=1)  # a g3 read of the very page, cached
    [g3] = cache.puts
    assert g3.prompt_version == "g3"
    fake.script("completions", compact())
    read = rig.read(ulc(cache), image=image)
    assert [item.text for item in read.lines] == ["Survey 42", "Owner Ramesh"]  # the g3 row was not used
    assert cache.puts[1].prompt_version == f"{COMPACT_PROMPT_VERSION}+ulc55"
    assert (cache.puts[1].image_sha256, cache.puts[1].model) == (sha, FLASH)
    fake.script("completions", compact(PAGE_R.replace("Ramesh", "Suresh")))
    assert rig.read(ulc(cache, paise=30), image=image).lines[1].text == "Owner Suresh"  # another ceiling
    assert cache.puts[2].prompt_version == "c1+ulc30"
    assert len(fake.requests) == 3
    with ledger() as kept:
        again = rig.read(ulc(cache), image=image)  # the c1 read of ₹0.55, from the cache
    assert len(fake.requests) == 3 and again.cached
    assert [(row.kind, row.cached, row.calls) for row in kept.rows] == [("scan", True, 1)]
    with rig.engine.document(dataclasses.replace(ulc(cache), ulc=None, ulc_prices=None, thinking=None)):
        assert rig.engine.read_page(image, page_no=1).lines[0].text == "g3 read"  # off: the g3 row
    assert rig.engine.version == f"{FLASH};prompt=g3"


def test_488_version_names_the_compact_prompt(fake: OpenRouterFake) -> None:
    fake.script("completions", compact())
    rig = Rig(fake)
    rig.read(ulc())
    assert rig.engine.version == f"{FLASH};prompt=c1"


def test_488_a_read_with_a_made_up_box_is_cached_and_its_line_stays_unpinned_from_the_cache(
    fake: OpenRouterFake,
) -> None:
    # A page is never billed again for one bad box. The decoder's box of a row with none spans
    # the page's width (x0 0, x1 1000), and a cached row with one is not pinned.
    cache = FakeCache()
    fake.script("completions", compact("g 0 l -\np 100 100 90 900 upside down\np 300 100 320 900 Fine"))
    rig = Rig(fake, (100.0, 100.0, 900.0, 150.0))  # a found box inside the made-up one, none at "Fine"
    first = rig.read(ulc(cache))
    assert [(item.text, item.box_source) for item in first.lines] == [
        ("100 100 90 900 upside down", "model"),
        ("Fine", "model"),
    ]
    assert len(cache.puts) == 1
    again = rig.read(ulc(cache))
    assert again.cached and len(fake.requests) == 1
    assert [(item.text, item.box_source) for item in again.lines] == [
        (item.text, "model") for item in first.lines
    ]


def test_488_a_reply_with_no_header_is_never_cached(fake: OpenRouterFake) -> None:
    cache = FakeCache()
    fake.script("completions", compact(""), compact("p 100 100 120 900 no header"))
    rig = Rig(fake, (100.0, 200.0, 900.0, 240.0))
    assert rig.read(ulc(cache)).lines == ()
    read = rig.read(ulc(cache))
    assert [(item.text, item.box_source) for item in read.lines] == [("no header", "model")]
    assert cache.puts == [] and len(fake.requests) == 2


def test_488_the_compact_cache_row_is_the_g3_shape(fake: OpenRouterFake) -> None:
    cache = FakeCache()
    fake.script("completions", compact())
    Rig(fake).read(ulc(cache))
    [(row, tokens_in, tokens_out)] = cache.rows.values()
    assert row == as_result(checked(row))  # the page cache's own shape: g3 code reads it as is
    assert (tokens_in, tokens_out) == (2400, 300)
    assert INSTRUCTION not in json.dumps(row)


# The ceiling.


def test_488_a_cut_off_reply_is_one_call_no_halves_and_a_local_read(fake: OpenRouterFake) -> None:
    cut = chat_reply(
        '{"r": "g 0 l -\\np 1', finish="length", native="MAX_TOKENS", counts=counts(2400, 1473, 0)
    )
    fake.script("completions", cut)
    cache = FakeCache()
    with ledger() as kept:
        read = Rig(fake).read(ulc(cache))
    assert len(fake.requests) == 1
    assert (read.read_by, read.issues[0]) == ("rapidocr", READER_REFUSED)
    assert (read.input_tokens, read.output_tokens) == (2400, 1473)
    assert list(cache.rows.values()) == [({"ulc_refused": "cost_ceiling"}, 2400, 1473)]  # a mark, not a read
    [row] = kept.rows
    assert (row.kind, row.calls, row.input_tokens, row.output_tokens) == ("scan", 1, 2400, 1473)


def test_488_a_repair_it_cannot_afford_is_not_sent(
    fake: OpenRouterFake, caplog: pytest.LogCaptureFixture
) -> None:
    first_cap = cap_for(ceiling().usd, FIRST)
    fake.script("completions", chat_reply("not JSON", counts=counts(2400, first_cap - 10, 0)))
    with ledger() as kept, caplog.at_level(logging.WARNING):
        read = Rig(fake).read(ulc())
    assert len(fake.requests) == 1
    assert (read.read_by, read.issues[0]) == ("rapidocr", READER_REFUSED)
    assert [record.code for record in caplog.records if hasattr(record, "code")][-1] == "cost_ceiling"
    [row] = kept.rows
    assert (row.kind, row.calls) == ("scan", 1)


def test_488_a_repair_it_can_afford_is_sent_once_within_what_is_left(fake: OpenRouterFake) -> None:
    fake.script(
        "completions", chat_reply("not JSON", counts=counts(2400, 20, 0)), compact(prompt=2420, completion=30)
    )
    read = Rig(fake).read(ulc())
    first, repair = fake.bodies()
    assert len(repair["messages"]) == 3 and repair["messages"][1]["content"] == "not JSON"
    usd = ceiling().usd
    left = usd - spent(2400, 20)
    # The repair's input counts the page at no less than its estimate.
    estimate = math.ceil(1.05 * (FIRST + len(b"not JSON") + len(repair["messages"][2]["content"].encode())))
    assert (
        repair["max_completion_tokens"] == cap_for(left, estimate, usd=usd) < first["max_completion_tokens"]
    )
    assert [item.text for item in read.lines] == ["Survey 42", "Owner Ramesh"]
    assert (read.input_tokens, read.output_tokens) == (4820, 50)


def test_488_a_billed_mid_reply_error_is_charged_and_retried_only_within_the_ceiling(
    fake: OpenRouterFake,
) -> None:
    usd = ceiling().usd
    first_cap = cap_for(usd, FIRST)
    error = chat_reply("", finish="error", native=None, counts=counts(2400, 100, 0))
    fake.script("completions", error, compact())
    read = Rig(fake).read(ulc())
    first, retry = fake.bodies()
    assert retry["max_completion_tokens"] == cap_for(usd - spent(2400, 100), FIRST, usd=usd)
    assert retry["max_completion_tokens"] < first["max_completion_tokens"] == first_cap
    assert read.read_by == FLASH and (read.input_tokens, read.output_tokens) == (4800, 400)
    # A billed error that leaves too little is never retried: the page is read locally, charged.
    fake.requests.clear()
    fake.script(
        "completions", chat_reply("", finish="error", native=None, counts=counts(2400, first_cap - 5, 0))
    )
    with ledger() as kept:
        read = Rig(fake).read(ulc())
    assert len(fake.requests) == 1
    assert (read.read_by, read.issues[0]) == ("rapidocr", READER_REFUSED)
    assert (read.input_tokens, read.output_tokens) == (2400, first_cap - 5)
    [row] = kept.rows
    assert (row.kind, row.calls) == ("scan", 1)


def test_488_a_page_whose_output_limit_would_be_below_256_is_never_sent(fake: OpenRouterFake) -> None:
    dear = UlcPrices(price_in_usd_per_m=1.0, price_out_usd_per_m=10.0, tokens_per_page_in=2500)
    assert cap_for(ceiling(20).usd, FIRST, dear) < 256
    with ledger() as kept:
        read = Rig(fake).read(ulc(paise=20, prices=dear))
    assert fake.requests == [] and kept.rows == []
    assert (read.read_by, read.issues[0]) == ("rapidocr", READER_REFUSED)


def test_488_a_lead_with_no_known_price_is_never_sent(fake: OpenRouterFake) -> None:
    for prices in (None, UlcPrices(price_in_usd_per_m=0.3, price_out_usd_per_m=0.0)):
        read = Rig(fake).read(ulc(prices=prices))
        assert (read.read_by, read.issues[0]) == ("rapidocr", READER_REFUSED)
    assert fake.requests == []


def test_488_a_price_a_call_not_known_is_reserved_from_every_calls_ceiling(fake: OpenRouterFake) -> None:
    # shortcut (production has no request or image price): each call's output limit is what is
    # left after its input and ULC_CALL_RESERVE_SHARE of the ceiling, never refused for it.
    fake.script("completions", compact())
    read = Rig(fake).read(ulc())
    [body] = fake.bodies()
    usd, price_in, price_out = (
        ceiling().usd,
        PRICES.price_in_usd_per_m / 1e6,
        PRICES.price_out_usd_per_m / 1e6,
    )
    assert body["max_completion_tokens"] == math.floor((0.9 * usd - FIRST * price_in) / price_out)
    assert read.read_by == FLASH


def test_488_a_budget_charges_the_reported_cost_or_the_tokens_never_the_reserve() -> None:
    price_in, price_out, usd = 0.3e-6, 2.5e-6, ceiling().usd
    budget = PageBudget(usd, price_in, price_out, FIRST, reserve=reserve(usd))
    assert budget.worst(None, 100) == pytest.approx(reserve(usd) + FIRST * price_in + 100 * price_out)
    listed = 2400 * price_in + 300 * price_out
    budget.charge(2400, 300, None)  # no cost said: its tokens at the prices
    assert budget.spent == pytest.approx(listed)
    budget.charge(2400, 300, listed / 2)  # a cost below its tokens: its tokens
    assert budget.spent == pytest.approx(2 * listed)
    assert (budget.rate_in, budget.rate_out) == (price_in, price_out)
    image = 0.0004  # a price a call the catalogue does not know, in the reported cost
    budget.charge(2400, 300, listed + image)
    assert budget.spent == pytest.approx(3 * listed + image)
    assert budget.rate_in > price_in and budget.rate_out > price_out  # learned for the next calls


def test_488_the_input_estimate_is_the_largest_page_seen_in_the_document(fake: OpenRouterFake) -> None:
    fake.script("completions", compact(prompt=2000), compact(prompt=1800), compact())
    rig = Rig(fake)
    with rig.engine.document(ulc(page_count=3, prices=dataclasses.replace(PRICES, tokens_per_page_in=None))):
        for page_no in (1, 2, 3):
            rig.engine.read_page(page(250 + page_no), page_no=page_no)
    caps = [body["max_completion_tokens"] for body in fake.bodies()]
    usd = ceiling().usd
    assert caps == [
        cap_for(usd, ULC_INPUT_TOKENS),
        cap_for(usd, math.ceil(2000 * 1.05)),
        cap_for(usd, math.ceil(2000 * 1.05)),
    ]


def test_488_a_page_billed_past_its_ceiling_is_logged_with_numbers_only(
    fake: OpenRouterFake, caplog: pytest.LogCaptureFixture
) -> None:
    fake.script("completions", compact(cost=0.01))  # the provider billed more than its price says
    with caplog.at_level(logging.WARNING):
        Rig(fake).read(ulc())
    [breach] = [
        vars(record) for record in caplog.records if getattr(record, "code", None) == "ulc_ceiling_breached"
    ]
    assert (breach["customer_id"], breach["document_id"], breach["page_no"]) == (CUSTOMER, DOCUMENT, 1)
    assert breach["ceiling_usd"] == pytest.approx(ceiling().usd)
    assert breach["spent_usd"] == pytest.approx(0.01)
    assert "Survey" not in json.dumps(breach, default=str)


def test_488_the_ledger_bills_every_page_read_as_scan(fake: OpenRouterFake) -> None:
    # Production's reports and SQL know scan only (no migration): a page read under Ultra low cost
    # is a scan row, its reported cost in it.
    fake.script("completions", compact(cost=0.001))
    with ledger() as kept:
        Rig(fake).read(ulc())
    [row] = kept.rows
    assert (row.kind, row.model, row.calls, row.cost_usd, row.cached) == ("scan", FLASH, 1, 0.001, False)


# A call whose cost is not known, the estimate before any page is seen, a price above the
# catalogue's, a page not sent again.


def test_488_a_reply_with_no_usage_is_charged_its_worst_case(fake: OpenRouterFake) -> None:
    # Usage left out is never $0. The reply is charged the page's estimate and its whole output
    # limit, which leaves no repair affordable: one call, then the local reader.
    fake.script("completions", chat_reply("not JSON", counts={"note": 0}), compact())
    read = Rig(fake).read(ulc())
    assert len(fake.requests) == 1
    assert (read.read_by, read.issues[0]) == ("rapidocr", READER_REFUSED)


@pytest.mark.parametrize("reply", [Reply(200, b'{"choices": ['), Reply(0)], ids=["not_json", "dropped"])
def test_488_a_call_that_may_have_been_billed_is_charged_and_never_sent_again(
    fake: OpenRouterFake, reply: Reply
) -> None:
    # A 200 whose body can't be read, or a connection dropped once the request was sent, may
    # have been billed in full: it is charged the worst case, never sent again past the ceiling, and
    # the page is read locally, not left to wait (a wait would read it again, billed again).
    fake.script("completions", reply, compact())
    read = Rig(fake).read(ulc())
    assert len(fake.requests) == 1
    assert (read.read_by, read.issues[0]) == ("rapidocr", READER_REFUSED)


def test_488_a_refused_connection_is_never_charged() -> None:
    # A request refused before it reached a provider was never billed: nothing is charged, and
    # the reader takes it as the outage it always was.
    settings = make_settings(
        openrouter_api_key=KEY, openrouter_base_url=closed_port_url(), ocr_model=FLASH, model_rpm=10**9
    )
    client = OpenRouterClient(
        settings, gate=ModelGate(settings, workers=1, sleep=lambda _s: None, random=lambda: 0.0)
    )
    budget = PageBudget(ceiling().usd, 0.3e-6, 2.5e-6, FIRST)
    with pytest.raises(ModelUnavailable):
        client.read_page(
            png=png_of(page()),
            instruction=INSTRUCTION_COMPACT,
            schema=COMPACT_SCHEMA,
            media_resolution="high",
            thinking_level="minimal",
            deadline=client.now() + 0.3,
            model=FLASH,
            provider="google-vertex",
            budget=budget,
        )
    assert budget.spent == 0


def test_488_ulc_input_tokens_is_the_compact_prompt_and_the_measured_image() -> None:
    prompt = estimate_tokens(INSTRUCTION_COMPACT) + estimate_tokens(json.dumps(COMPACT_SCHEMA))
    image = G3_PAGE_INPUT_TOKENS - estimate_tokens(INSTRUCTION)
    assert math.ceil(1.15 * (prompt + image)) == ULC_INPUT_TOKENS
    assert 2500 < ULC_INPUT_TOKENS < 3200


def test_488_before_any_page_is_seen_the_estimate_is_never_below_ulc_input_tokens(
    fake: OpenRouterFake,
) -> None:
    fake.script("completions", compact())
    Rig(fake).read(ulc(prices=dataclasses.replace(PRICES, tokens_per_page_in=1000)))
    [body] = fake.bodies()
    assert body["max_completion_tokens"] == cap_for(ceiling().usd, ULC_INPUT_TOKENS)


def test_488_a_cost_above_the_price_raises_the_prices_for_the_rest_of_the_document(
    fake: OpenRouterFake,
) -> None:
    # Page 1 is billed 30% above the catalogue's prices and a price a call on top; from then on
    # the document's calls are held at what the provider really charged, so a page billed the
    # same way, up to its output limit, stays within its ceiling.
    markup, image, usd = 1.3, 0.0008, ceiling().usd
    fake.script(
        "completions",
        compact(cost=markup * spent(2400, 300) + image),
        compact(prompt=2400, completion=10, cost=markup * spent(2400, 10) + image),
    )
    rig = Rig(fake)
    with rig.engine.document(ulc(page_count=2)):
        rig.engine.read_page(page(251), page_no=1)
        rig.engine.read_page(page(252), page_no=2)
    cap = fake.bodies()[1]["max_completion_tokens"]
    assert cap < cap_for(usd, math.ceil(2400 * 1.05))
    assert markup * spent(2400, cap) + image <= usd


def test_488_a_page_read_locally_after_a_billed_call_is_not_sent_again_until_read_again(
    fake: OpenRouterFake,
) -> None:
    cut = chat_reply('{"r": "g 0', finish="length", native="MAX_TOKENS", counts=counts(2400, 1473, 0))
    fake.script("completions", cut)
    cache, rig = FakeCache(), Rig(fake)
    assert rig.read(ulc(cache)).read_by == "rapidocr"
    with ledger() as kept:
        waited = rig.read(ulc(cache))
    assert len(fake.requests) == 1 and kept.rows == []
    assert (waited.read_by, waited.issues[0], waited.input_tokens) == ("rapidocr", READER_REFUSED, 0)
    fake.script("completions", compact())
    assert rig.read(ulc(cache, read_again=frozenset({1}))).read_by == FLASH
    assert len(fake.requests) == 2
    assert rig.read(ulc(cache)).cached


def test_488_no_boxes_are_found_for_a_page_refused_before_any_call(fake: OpenRouterFake) -> None:
    rig = Rig(fake)
    rig.read(ulc(plain=frozenset({FLASH})))
    rig.read(ulc(prices=None))
    assert rig.finder.sizes == [] and fake.requests == []


# The proof: a page's billed cost never passes its ceiling, but by what a surprise cost.

OUTCOMES = (
    "ok",
    "bad_json",
    "no_r",
    "length",
    "error",
    "blocked",
    "garbage",
    "no_usage",
    "not_json",
    "dropped",
    "down",
)
WEIGHTS = (6, 3, 2, 2, 2, 1, 1, 2, 1, 1, 1)


class Provider:
    """A provider billing as a real one would, and worse: each call's input near ``base`` tokens
    (a retry of the page: the same request, the same input; a repair: the page's, plus its reply
    and problem at two bytes a token), its output at most the call's max_completion_tokens, at
    ``markup`` times the catalogue's prices, plus ``per_call`` (a request or image price the
    catalogue does not know). A random outcome each time: usage.cost says what it billed (an
    honest provider, at markup 1 with a price a call within the reserve, may say less, or
    nothing); "no_usage" leaves usage out, "not_json" is a 200 whose body is not JSON, "dropped"
    closes the connection once the request is read, all three billed all the same; "down" is a
    503, never billed. ``billed`` is what the page's calls cost; ``told`` whether a reply has said
    its tokens and cost yet."""

    def __init__(
        self,
        rng: random.Random,
        prices: UlcPrices,
        base: int,
        markup: float,
        per_call: float,
        *,
        honest: bool,
    ) -> None:
        self.rng, self.prices, self.base, self.markup, self.per_call = rng, prices, base, markup, per_call
        self.honest = honest
        self.billed: list[float] = []
        self.page_prompt: int | None = None
        self.told = False
        self.repairs = self.retries = self.unknown = 0

    def next_page(self) -> None:
        self.billed.clear()
        self.page_prompt = None

    def __call__(self, request: Recorded) -> Reply:
        rng, body = self.rng, request.body
        cap = body["max_completion_tokens"]
        outcome = rng.choices(OUTCOMES, WEIGHTS)[0]
        if outcome == "down":
            return Reply(503, {"error": {"code": 503, "message": "down"}})
        if len(body["messages"]) == 3:  # the repair turn: the page, the reply and the problem again
            self.repairs += 1
            said = sum(len(message["content"].encode()) for message in body["messages"][1:])
            prompt = (self.page_prompt or self.base) + said // 2
        else:
            self.retries += self.page_prompt is not None
            if self.page_prompt is None:
                self.page_prompt = rng.randint(math.ceil(self.base * 0.96), self.base)
            prompt = self.page_prompt
        completion = cap if outcome == "length" else rng.randint(0, cap)
        true = self.markup * spent(prompt, completion, self.prices) + self.per_call
        self.billed.append(true)
        if outcome in ("no_usage", "not_json", "dropped"):
            self.unknown += 1
            if outcome == "not_json":
                return Reply(200, b'{"choices": [')
            if outcome == "dropped":
                return Reply(0)
            return chat_reply(rng.choice(["{not json", json.dumps({"r": PAGE_R})]), counts={"note": 0})
        self.told = True
        cost = rng.choice([None, true, true * rng.random()]) if self.honest else true
        use = counts(prompt, completion, rng.randint(0, completion), cost)
        if outcome == "length":
            return chat_reply('{"r": "g 0', finish="length", native="MAX_TOKENS", counts=use)
        if outcome == "error":
            return chat_reply("", finish="error", native=None, counts=use)
        if outcome == "blocked":
            return chat_reply(None, finish="content_filter", native="SAFETY", counts=use)
        if outcome == "bad_json":
            return chat_reply("{not json", counts=use)
        if outcome == "no_r":
            return chat_reply('{"text": "x"}', counts=use)
        if outcome == "garbage":
            return json_reply({"r": "".join(rng.choice("pgt 0123?\n>xyz") for _ in range(30))}, counts=use)
        return json_reply({"r": PAGE_R}, counts=use)


def test_488_a_pages_billed_cost_never_passes_its_ceiling(fake: OpenRouterFake) -> None:
    """Half the documents' providers are honest (input within the first estimate, billed at the
    catalogue's prices, any price a call within the reserve): every page holds. The others may
    take in up to half as much again as the first estimate, bill up to 30% above the prices and
    add a price a call up to twice the reserve of a 20 paise page, always saying what they billed
    when they say usage: once a reply has told the reader the document's input and prices, every
    page holds; before that, a page passes its ceiling only by what the surprise cost (its input
    above the first estimate, the markup, and each call's price above the reserve). Usage left
    out, a 200 that is not JSON and a dropped connection are no surprise: they are charged their
    worst case. Ceilings from 20 to 55 paise."""
    rng = random.Random(4880)  # noqa: S311 - a fixed seed draws test prices and replies, not a secret
    pages_checked = held = passed = repairs = retries = unknown = per_call_pages = 0
    closest = 0.0
    for _document in range(150):
        prices = UlcPrices(
            price_in_usd_per_m=rng.uniform(0.01, 0.6),
            price_out_usd_per_m=rng.uniform(0.2, 12.0),
            tokens_per_page_in=rng.choice([None, rng.randint(300, 4000)]),
        )
        paise = rng.randint(20, 55)
        usd = ceiling(paise).usd
        known = prices.tokens_per_page_in
        first = max(math.ceil(known * 1.15), ULC_INPUT_TOKENS) if known else ULC_INPUT_TOKENS
        honest = rng.random() < 0.5
        base = rng.randint(first // 2, first if honest else first * 3 // 2)
        markup = 1.0 if honest else rng.choice([1.0, rng.uniform(1.0, 1.3)])
        top = reserve(usd) if honest else 2 * reserve(ceiling(20).usd)
        per_call = rng.choice([0.0, rng.uniform(0.0, top)])
        provider = Provider(rng, prices, base, markup, per_call, honest=honest)
        fake.responder = provider
        rig = Rig(fake)
        with rig.engine.document(ulc(paise=paise, prices=prices, page_count=4)):
            for page_no in range(1, rng.randint(2, 5)):
                provider.next_page()
                told = provider.told
                with contextlib.suppress(BackendUnavailable):  # the breaker opened: the page waits
                    rig.engine.read_page(page(200 + page_no), page_no=page_no)
                bill = sum(provider.billed)
                calls = len(provider.billed)
                surprise = markup * (
                    usd + max(0, base - first) * prices.price_in_usd_per_m / 1e6
                ) + calls * max(0.0, per_call - reserve(usd))
                assert bill <= (usd if told or honest else surprise) * (1 + 1e-9), (
                    paise,
                    prices,
                    provider.billed,
                )
                if told:
                    held += 1
                    closest = max(closest, bill / usd)
                passed += bill > usd * (1 + 1e-9)
                per_call_pages += per_call > 0 and bill > 0
                pages_checked += 1
        repairs, retries = repairs + provider.repairs, retries + provider.retries
        unknown += provider.unknown
    # Not a proof by luck: pages came close to their ceiling, surprises did cost, and repairs,
    # retries, replies of unknown cost and hidden prices a call were sent.
    assert pages_checked > 250 and held > 50 and closest > 0.85, (pages_checked, held, closest)
    assert passed > 0
    assert repairs >= 10 and retries >= 5 and unknown >= 20, (repairs, retries, unknown)
    assert per_call_pages > 30, per_call_pages


# The pipeline hands the reading's ceiling and the lead's prices, taken once at the job's start,
# to the reader; off, it hands none.


@pytest.mark.parametrize("on", [True, False])
def test_488_the_pipeline_passes_the_ceiling_and_the_prices_to_the_reader(on: bool) -> None:
    lead = Agent(FLASH, "google-vertex", "Flash")
    reading = (
        Reading("single", lead, thinking="minimal", notes=("ultra_low_cost",), ulc=ceiling(40))
        if on
        else Reading("single", lead, thinking="low")
    )
    prices = PRICES if on else None
    reader = ScriptedReader({1: page_read(TOTAL)})
    system = rig(
        reader,
        checker=FakeChecker(),
        job_reading=lambda customer, document: JobReading(
            None, FLASH, "google-vertex", reading=reading, ulc_prices=prices
        ),
    )
    system.run(make_pdf([SCANNED]))
    [opened] = reader.opened
    assert (opened.ulc, opened.ulc_prices) == ((ceiling(40), PRICES) if on else (None, None))
