"""Reading under Ultra low cost (#488, Tushar, 4 Nov 2026): each page in the compact reply, at
minimal thinking, with every call held under the page's ceiling.

The Gemini reader over the real OpenRouter client against the loopback fake (never the real
API), so the ceiling the client enforces (the output limit of each call, the repair, a billed
mid-reply error) is what is tested. The fake bills what a provider would: never more output than
the call's max_completion_tokens, and a page's input within the reader's estimate (the image's
tokens hardly change between pages of one document); ``test_488_a_page_billed_past_its_ceiling``
shows what happens when that estimate is wrong.
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

from kaagaz.ai import usage
from kaagaz.ai.openrouter import ModelGate, OpenRouterClient
from kaagaz.ingestion.errors import BackendUnavailable
from kaagaz.reading.compact import COMPACT_SCHEMA, INSTRUCTION_COMPACT
from kaagaz.reading.gemini_ocr import INSTRUCTION, GeminiEngine, as_result, checked, pixels_sha256
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
    json_reply,
    running_fake,
)
from tests.openrouter_fake import (
    usage as counts,
)
from tests.settings_factory import make_settings
from tests.test_dynamic_reading import ledger
from tests.test_gemini_ocr import CUSTOMER, DOCUMENT, FakeCache, FakeFinder, FakeRapidOcr, line, value

FLASH = "google/gemini-3.5-flash-lite"
PRO = "google/gemini-3.1-pro-preview"
PRICES = UlcPrices(price_in_usd_per_m=0.30, price_out_usd_per_m=2.50, tokens_per_page_in=2500)
PAGE_R = "g 0 l -\nt 40 100 70 900 Survey 42\np 100 100 120 900 Owner Ramesh"


@pytest.fixture
def fake() -> Iterator[OpenRouterFake]:
    with running_fake() as server:
        yield server


class Rig:
    """The reader on a fresh gate (no waits between retries) and a fake local reader."""

    def __init__(self, fake: OpenRouterFake) -> None:
        settings = make_settings(
            openrouter_api_key=KEY,
            openrouter_base_url=fake.url,
            ocr_model=FLASH,
            ocr_timeout_seconds=5.0,
            model_timeout_seconds=5.0,
            model_rpm=10**9,
        )
        gate = ModelGate(settings, workers=1, sleep=lambda _seconds: None, random=lambda: 0.0)
        self.local = FakeRapidOcr()
        self.engine = GeminiEngine(
            settings,
            client=OpenRouterClient(settings, gate=gate),
            pool=FakeFinder(),
            local=lambda: as_page_engine(self.local),
        )

    def read(self, pages: DocumentPages, page_no: int = 1, image: Image.Image | None = None) -> Any:
        with self.engine.document(pages):
            return self.engine.read_page(image or page(), page_no=page_no)


def page(shade: int = 255) -> Image.Image:
    return Image.new("RGB", (1000, 2000), (shade, shade, shade))


def ceiling(paise: int = 40) -> UlcCeiling:
    return UlcCeiling(usd=paise / 100 / INR_PER_USD, paise=paise)


def ulc(
    cache: FakeCache | None = None, *, paise: int = 40, prices: UlcPrices | None = PRICES, **more: Any
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


def cap_for(usd: float, tokens_in: int, prices: UlcPrices = PRICES) -> int:
    return math.floor(
        (usd - tokens_in * prices.price_in_usd_per_m / 1e6) / (prices.price_out_usd_per_m / 1e6)
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
    assert body["max_completion_tokens"] == cap_for(ceiling().usd, math.ceil(2500 * 1.15))
    assert [item.text for item in read.lines] == ["Survey 42", "Owner Ramesh"]
    assert (read.read_by, read.read_thinking, read.read_resolution) == (FLASH, "minimal", "high")
    assert (read.input_tokens, read.output_tokens, read.read_output_tokens) == (2400, 300, 300)


@pytest.mark.parametrize("asked", ["high", "low", None])
def test_488_effort_minimal_is_always_sent_also_for_a_page_read_again(
    fake: OpenRouterFake, asked: str | None
) -> None:
    fake.script("completions", compact(), compact())
    rig = Rig(fake)
    rig.read(ulc(thinking=asked, careful_thinking="high", careful_resolution="low"))
    rig.read(
        ulc(thinking=asked, careful_thinking="high", careful_resolution="low", read_again=frozenset({1}))
    )
    assert [body["reasoning"] for body in fake.bodies()] == [{"effort": "minimal", "exclude": True}] * 2
    assert [body["messages"][0]["content"][1]["image_url"]["detail"] for body in fake.bodies()] == [
        "high"
    ] * 2


@pytest.mark.parametrize(
    "more", [{"plain": frozenset({FLASH})}, {"levels": {FLASH: frozenset({"low", "high"})}}]
)
def test_488_a_lead_that_cannot_think_minimal_is_refused_with_nothing_sent(
    fake: OpenRouterFake, more: dict[str, Any], caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level(logging.WARNING):
        read = Rig(fake).read(ulc(**more))
    assert fake.requests == []
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
    assert cache.puts[1].prompt_version == f"{COMPACT_PROMPT_VERSION}+ulc40"
    assert (cache.puts[1].image_sha256, cache.puts[1].model) == (sha, FLASH)
    fake.script("completions", compact(PAGE_R.replace("Ramesh", "Suresh")))
    assert rig.read(ulc(cache, paise=30), image=image).lines[1].text == "Owner Suresh"  # another ceiling
    assert cache.puts[2].prompt_version == "c1+ulc30"
    assert len(fake.requests) == 3
    with ledger() as kept:
        again = rig.read(ulc(cache), image=image)  # the c1 read of ₹0.40, from the cache
    assert len(fake.requests) == 3 and again.cached
    assert [(row.kind, row.cached, row.calls) for row in kept.rows] == [("ulc_scan", True, 1)]
    with rig.engine.document(dataclasses.replace(ulc(cache), ulc=None, ulc_prices=None, thinking=None)):
        assert rig.engine.read_page(image, page_no=1).lines[0].text == "g3 read"  # off: the g3 row
    assert rig.engine.version == f"{FLASH};prompt=g3"


def test_488_version_names_the_compact_prompt(fake: OpenRouterFake) -> None:
    fake.script("completions", compact())
    rig = Rig(fake)
    rig.read(ulc())
    assert rig.engine.version == f"{FLASH};prompt=c1"


def test_488_a_read_with_a_made_up_box_is_not_cached(fake: OpenRouterFake) -> None:
    cache = FakeCache()
    fake.script("completions", compact("g 0 l -\np 100 100 90 900 upside down box"))
    read = Rig(fake).read(ulc(cache))
    assert [(item.text, item.box_source) for item in read.lines] == [("upside down box", "model")]
    assert cache.puts == []  # its line would pin on a cached read: it is read again next time


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
    assert cache.puts == []
    [row] = kept.rows
    assert (row.kind, row.calls, row.limit_hits, row.pages, row.thinking) == ("ulc_scan", 1, 1, 1, "minimal")


def test_488_a_repair_it_cannot_afford_is_not_sent(
    fake: OpenRouterFake, caplog: pytest.LogCaptureFixture
) -> None:
    first_cap = cap_for(ceiling().usd, math.ceil(2500 * 1.15))
    fake.script("completions", chat_reply("not JSON", counts=counts(2400, first_cap - 10, 0)))
    with ledger() as kept, caplog.at_level(logging.WARNING):
        read = Rig(fake).read(ulc())
    assert len(fake.requests) == 1
    assert (read.read_by, read.issues[0]) == ("rapidocr", READER_REFUSED)
    assert [record.code for record in caplog.records if hasattr(record, "code")][-1] == "cost_ceiling"
    [row] = kept.rows
    assert (row.calls, row.limit_hits) == (1, 1)


def test_488_a_repair_it_can_afford_is_sent_once_within_what_is_left(fake: OpenRouterFake) -> None:
    fake.script(
        "completions", chat_reply("not JSON", counts=counts(2400, 20, 0)), compact(prompt=2420, completion=30)
    )
    read = Rig(fake).read(ulc())
    first, repair = fake.bodies()
    assert len(repair["messages"]) == 3 and repair["messages"][1]["content"] == "not JSON"
    left = ceiling().usd - spent(2400, 20)
    estimate = math.ceil(1.05 * (2400 + len(b"not JSON") + len(repair["messages"][2]["content"].encode())))
    assert repair["max_completion_tokens"] == cap_for(left, estimate) < first["max_completion_tokens"]
    assert [item.text for item in read.lines] == ["Survey 42", "Owner Ramesh"]
    assert (read.input_tokens, read.output_tokens) == (4820, 50)


def test_488_a_billed_mid_reply_error_is_charged_and_retried_only_within_the_ceiling(
    fake: OpenRouterFake,
) -> None:
    first_cap = cap_for(ceiling().usd, math.ceil(2500 * 1.15))
    error = chat_reply("", finish="error", native=None, counts=counts(2400, 100, 0))
    fake.script("completions", error, compact())
    read = Rig(fake).read(ulc())
    first, retry = fake.bodies()
    assert retry["max_completion_tokens"] == cap_for(ceiling().usd - spent(2400, 100), math.ceil(2500 * 1.15))
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
    assert (row.kind, row.calls, row.limit_hits, row.pages) == ("ulc_scan", 1, 1, 1)


def test_488_a_page_whose_output_limit_would_be_below_256_is_never_sent(fake: OpenRouterFake) -> None:
    dear = UlcPrices(price_in_usd_per_m=1.0, price_out_usd_per_m=10.0, tokens_per_page_in=2500)
    assert cap_for(ceiling(20).usd, math.ceil(2500 * 1.15), dear) < 256
    with ledger() as kept:
        read = Rig(fake).read(ulc(paise=20, prices=dear))
    assert fake.requests == []
    assert (read.read_by, read.issues[0]) == ("rapidocr", READER_REFUSED)
    # Still a limit hit, on a row of its own: no call, 0 tokens, cost 0 (0023's CHECK: calls >= 1).
    [row] = kept.rows
    assert (row.kind, row.calls, row.input_tokens, row.output_tokens, row.cost_usd) == (
        "ulc_scan",
        1,
        0,
        0,
        0.0,
    )
    assert (row.limit_hits, row.pages, row.thinking, row.resolution) == (1, 1, "minimal", "high")
    assert kept.live == [(1, 0.0)]  # one page of the day, costing nothing


def test_488_a_lead_with_no_known_price_is_never_sent(fake: OpenRouterFake) -> None:
    for prices in (None, UlcPrices(price_in_usd_per_m=0.3, price_out_usd_per_m=0.0)):
        with ledger() as kept:
            read = Rig(fake).read(ulc(prices=prices))
        assert (read.read_by, read.issues[0]) == ("rapidocr", READER_REFUSED)
        assert [(row.kind, row.calls, row.limit_hits, row.cost_usd) for row in kept.rows] == [
            ("ulc_scan", 1, 1, 0.0)
        ]
    assert fake.requests == []


def test_488_a_lead_that_cannot_think_minimal_is_no_limit_hit(fake: OpenRouterFake) -> None:
    with ledger() as kept:
        Rig(fake).read(ulc(plain=frozenset({FLASH})))
    assert kept.rows == []  # refused for its thinking, not to stay within the ceiling


def test_488_the_input_estimate_is_the_largest_page_seen_in_the_document(fake: OpenRouterFake) -> None:
    fake.script("completions", compact(prompt=2000), compact(prompt=1800), compact())
    rig = Rig(fake)
    with rig.engine.document(ulc(page_count=3, prices=dataclasses.replace(PRICES, tokens_per_page_in=None))):
        for page_no in (1, 2, 3):
            rig.engine.read_page(page(250 + page_no), page_no=page_no)
    caps = [body["max_completion_tokens"] for body in fake.bodies()]
    usd = ceiling().usd
    assert caps == [
        cap_for(usd, 3000),
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


def test_488_the_ledger_bills_every_page_read_as_ulc_scan(fake: OpenRouterFake) -> None:
    fake.script("completions", compact(cost=0.001))
    with ledger() as kept:
        Rig(fake).read(ulc())
    [row] = kept.rows
    assert (row.kind, row.model, row.calls, row.pages, row.thinking, row.resolution) == (
        "ulc_scan",
        FLASH,
        1,
        1,
        "minimal",
        "high",
    )
    assert "ulc_scan" in usage.KINDS and "ulc_scan" in usage.PAGE_READ_KINDS


# The proof: a page's billed cost never passes its ceiling.

OUTCOMES = ("ok", "bad_json", "no_r", "length", "error", "blocked", "garbage")


class Provider:
    """A provider billing at ``prices``: each call's input near ``base`` tokens (a repair: the
    page's, plus its reply and problem at two bytes a token), its output at most the call's
    max_completion_tokens, its reported cost none, the price or less; a random outcome each
    time. ``billed`` is what the page's calls cost, each the larger of reported and priced."""

    def __init__(self, rng: random.Random, prices: UlcPrices, base: int) -> None:
        self.rng, self.prices, self.base = rng, prices, base
        self.billed: list[float] = []
        self.first_prompt: int | None = None
        self.repairs = self.retries = 0

    def next_page(self) -> None:
        self.billed.clear()
        self.first_prompt = None

    def __call__(self, request: Recorded) -> Reply:
        rng, body = self.rng, request.body
        cap = body["max_completion_tokens"]
        self.retries += bool(self.billed) and len(body["messages"]) == 1
        if len(body["messages"]) == 3:  # the repair turn: the page, the reply and the problem again
            self.repairs += 1
            said = sum(len(message["content"].encode()) for message in body["messages"][1:])
            prompt = (self.first_prompt or 0) + said // 2
        else:
            prompt = rng.randint(math.ceil(self.base * 0.96), self.base)
            if self.first_prompt is None:
                self.first_prompt = prompt
        outcome = rng.choice(OUTCOMES)
        completion = cap if outcome == "length" else rng.randint(0, cap)
        true = (prompt * self.prices.price_in_usd_per_m + completion * self.prices.price_out_usd_per_m) / 1e6
        cost = rng.choice([None, true, true * rng.random()])
        self.billed.append(max(cost or 0.0, true))
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
    rng = random.Random(4880)  # noqa: S311 - a fixed seed draws test prices and replies, not a secret
    pages_checked, repairs, retries, closest = 0, 0, 0, 0.0
    for _document in range(100):
        prices = UlcPrices(
            price_in_usd_per_m=rng.uniform(0.01, 1.5),
            price_out_usd_per_m=rng.uniform(0.2, 12.0),
            tokens_per_page_in=rng.choice([None, rng.randint(300, 4000)]),
        )
        paise = rng.randint(20, 40)
        known = prices.tokens_per_page_in
        base = rng.randint(int(0.5 * (known or 3000)), int(1.15 * known) if known else 3000)
        provider = Provider(rng, prices, base)
        fake.responder = provider
        rig = Rig(fake)
        with rig.engine.document(ulc(paise=paise, prices=prices, page_count=4)):
            for page_no in range(1, rng.randint(2, 5)):
                provider.next_page()
                with contextlib.suppress(BackendUnavailable):  # the breaker opened: the page waits
                    rig.engine.read_page(page(200 + page_no), page_no=page_no)
                limit = ceiling(paise).usd * (1 + 1e-9)
                assert sum(provider.billed) <= limit, (paise, prices, provider.billed)
                closest = max(closest, sum(provider.billed) / ceiling(paise).usd)
                pages_checked += 1
        repairs, retries = repairs + provider.repairs, retries + provider.retries
    # Not a proof by luck: pages came close to their ceiling, and repairs and retries were sent.
    assert pages_checked > 250 and closest > 0.9, (pages_checked, closest)
    assert repairs >= 10 and retries >= 5, (repairs, retries)


def test_488_the_compact_cache_row_is_the_g3_shape(fake: OpenRouterFake) -> None:
    cache = FakeCache()
    fake.script("completions", compact())
    Rig(fake).read(ulc(cache))
    [(row, tokens_in, tokens_out)] = cache.rows.values()
    assert row == as_result(checked(row))  # the page cache's own shape: g3 code reads it as is
    assert (tokens_in, tokens_out) == (2400, 300)
    assert INSTRUCTION not in json.dumps(row)
