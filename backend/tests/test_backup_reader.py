"""The backup reader (#664, Tushar, 18 Nov 2026): a page Mistral OCR reads no text on is read once
by the backup model the super admin chose.

How a map ends failed on Mistral today: Mistral answers a map with the whole page as a picture
(``![img-0.jpeg](img-0.jpeg)``), or refuses it (``reader_refused``: a 400, 413 or 422, a reply
``page_read`` can't read, or the page's time spent). Either way the page has no lines and nothing
falls back to another reader (#473), so a file whose every page is like that has no text at all:
the image and PDF readers raise ``ReadError("no_text")`` and the pipeline fails it for good.

With a backup reader set, only such a page is read again, once, by the chosen model, normal or
Ultra low cost, billed to the document like any page read. An outage, a key or credit problem, or
Mistral not set up still waits as before; a page Mistral read is never read again; a backup read
that fails leaves the page as Mistral left it.

No network and never a real key: Mistral and Gemini are fakes (``test_mistral_wiring``'s, the
Gemini one billing each reply as the real client does).
"""

import dataclasses
import io
import logging
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any, cast

import pytest

from kaagaz.ai import usage
from kaagaz.ai.openrouter import PageReply, ProviderNotAllowed
from kaagaz.ai.replies import ModelOutputError, ModelUnavailable
from kaagaz.ai.usage import ULC_SCAN
from kaagaz.db import models
from kaagaz.db.page_reads import PageKey
from kaagaz.ingestion import sniff
from kaagaz.ingestion.errors import BackendUnavailable
from kaagaz.jobs.reading_pause import DAILY_LIMIT, ReadingPaused
from kaagaz.jobs.worker import JobFailed
from kaagaz.reading.gemini_ocr import PROVIDER_NOT_ALLOWED, ULC_MARK, GeminiEngine, pixels_sha256
from kaagaz.reading.mistral_reader import BadReply, MistralUnavailable
from kaagaz.reading.ocr import (
    COMPACT_PROMPT_VERSION,
    BackupReader,
    DocumentPages,
    PageRead,
    UlcCeiling,
    UlcPrices,
    as_page_engine,
)
from kaagaz.reading.page_quality import NO_TEXT, READER_REFUSED
from kaagaz.reading.spans import ReadError
from kaagaz.reading.wiring import build_reader_socket
from kaagaz.scanning.gate import DocumentStatus
from kaagaz.settings import NO_THINKING
from tests.pdf_builder import make_pdf
from tests.picture_support import text_like_picture
from tests.settings_factory import make_settings
from tests.test_dynamic_reading import ledger
from tests.test_gemini_ocr import (
    MODEL,
    FakeCache,
    FakeClient,
    FakeFinder,
    FakeRapidOcr,
    line,
    page,
    reply,
    value,
)
from tests.test_mistral_wiring import (
    BY_4,
    CUSTOMER,
    DOCUMENT,
    OCR_4,
    FakeMistral,
    H,
    W,
    mistral_reply,
    on_mistral,
    texts,
)
from tests.test_model_choice_pipeline import _Reading
from tests.test_pipeline_gemini import SCANNED, TOTAL, FakeChecker, ScriptedReader
from tests.test_pipeline_gemini import page_read as pipeline_page
from tests.test_pipeline_gemini import rig as gemini_rig

FLASH = "google/gemini-3.5-flash-lite"
NORMAL = BackupReader(MODEL, "google-vertex", "low")
PRICES = UlcPrices(
    price_in_usd_per_m=0.30, price_out_usd_per_m=2.50, tokens_per_page_in=2500, per_call_usd=0.0
)
ULC = BackupReader(
    FLASH, "google-vertex", "minimal", ulc=UlcCeiling(usd=40 / 100 / 88, paise=40), ulc_prices=PRICES
)
MAP_TEXT = ["Survey No 42", "Village Wagholi"]


class BilledClient(FakeClient):
    """Gemini's client, each reply (and each refused one's tokens) billed in the usage scope as
    ``OpenRouterClient`` bills it."""

    def read_page(self, **call: Any) -> PageReply:
        sent = {"thinking": call["thinking_level"], "resolution": call["media_resolution"]}
        try:
            got = super().read_page(**call)
        except (ModelOutputError, ModelUnavailable) as error:
            if error.input_tokens or error.output_tokens:
                usage.record_call(
                    call["model"], "google-vertex", error.input_tokens, error.output_tokens, 0, 0.001, **sent
                )
            raise
        usage.record_call(
            call["model"], "google-vertex", got.input_tokens, got.output_tokens, 0, 0.002, **sent
        )
        return got


class Rig:
    """The scan reader with a Mistral client (None: not set up here), a Gemini client that must
    never be called unless scripted, a box finder and a local reader."""

    def __init__(self, mistral: FakeMistral | None, *gemini: Any, **settings: Any) -> None:
        self.mistral = mistral
        self.client = BilledClient(*gemini)
        self.local = FakeRapidOcr()
        self.engine = GeminiEngine(
            make_settings(**{"ocr_engine": "gemini", **settings}),
            client=self.client,
            pool=FakeFinder(),
            local=lambda: as_page_engine(self.local),
            mistral=mistral,
        )

    def read(self, pages: DocumentPages, page_no: int = 1) -> PageRead:
        with self.engine.document(pages):
            return self.engine.read_page(page(), page_no=page_no)


def map_reply(*, blocks: bool = False) -> dict[str, Any]:
    """Mistral's answer for a map: the whole page as one picture, no text (with ``blocks``, OCR
    4.1's shape, one image block; without, OCR 3's Markdown only)."""
    markdown = "![img-0.jpeg](img-0.jpeg)"
    answer: dict[str, Any] = {"markdown": markdown, "dimensions": {"dpi": 200, "width": W, "height": H}}
    if blocks:
        corners = {"top_left_x": 0, "top_left_y": 0, "bottom_right_x": W, "bottom_right_y": H}
        answer["blocks"] = [{"type": "image", "content": markdown, **corners}]
    return answer


def gemini_map() -> PageReply:
    return reply(value(*(line(text, order=index) for index, text in enumerate(MAP_TEXT))))


def with_backup(backup: BackupReader | None = NORMAL, **more: Any) -> DocumentPages:
    return on_mistral(FakeCache(), backup=backup, **more)


def png() -> bytes:
    """A one-page scan that is not blank (a blank page is never sent to a reader)."""
    buffer = io.BytesIO()
    text_like_picture(600, 800).save(buffer, format="PNG")
    return buffer.getvalue()


# --- what Mistral does with a map today ---------------------------------------------------------


@pytest.mark.parametrize("blocks", [False, True])
def test_mistral_reads_a_map_as_a_picture_with_no_text(blocks: bool) -> None:
    read = Rig(FakeMistral((map_reply(blocks=blocks), 1))).read(on_mistral(FakeCache()))
    assert (read.lines, read.issues, read.read_by) == ((), (NO_TEXT,), BY_4)


@pytest.mark.parametrize("script", [(map_reply(), 1), BadReply("refused_request")])
def test_a_map_on_mistral_alone_fails_the_file_with_no_text(script: Any) -> None:
    """The failure Tushar saw: no page of the file has a line, so the reader says no_text, which
    the pipeline fails for good (``pipeline._or_fail``)."""
    rig = Rig(FakeMistral(script, script))  # the image reader asks once more at full size
    socket = build_reader_socket(make_settings(), rig.engine)
    with rig.engine.document(on_mistral(FakeCache())), pytest.raises(ReadError) as failed:
        socket.read(sniff.PNG, png())
    assert failed.value.code == "no_text"


# --- the backup reader --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "script",
    [
        (map_reply(), 1),  # the map: a picture, no text
        (map_reply(blocks=True), 1),
        BadReply("refused_request"),  # a 400, 413 or 422
        ({"markdown": 5}, 1),  # a 200 page_read can't read (bad_reply)
    ],
)
def test_a_page_mistral_read_no_text_on_is_read_by_the_backup_reader(script: Any) -> None:
    rig = Rig(FakeMistral(script), gemini_map())
    read = rig.read(with_backup())
    assert texts(read) == MAP_TEXT
    assert (read.read_by, read.read_thinking) == (MODEL, "low")
    assert READER_REFUSED not in read.issues and NO_TEXT not in read.issues
    [call] = rig.client.calls
    assert (call.model, call.provider, call.thinking_level) == (MODEL, "google-vertex", "low")
    assert rig.local.pages == 0


def test_the_map_file_is_read_with_the_backup_reader() -> None:
    rig = Rig(FakeMistral((map_reply(), 1)), gemini_map())
    socket = build_reader_socket(make_settings(), rig.engine)
    with rig.engine.document(with_backup()):
        spans = socket.read(sniff.PNG, png())
    assert [span.text for span in spans] == MAP_TEXT


def test_a_page_mistral_read_is_never_read_again() -> None:
    rig = Rig(FakeMistral((mistral_reply("INVOICE 1042"), 1)))  # Gemini has nothing scripted
    read = rig.read(with_backup())
    assert (texts(read), read.read_by, rig.client.calls) == (["INVOICE 1042"], BY_4, [])


@pytest.mark.parametrize("script", [(map_reply(), 1), BadReply("refused_request")])
def test_off_nothing_changes(script: Any) -> None:
    rig = Rig(FakeMistral(script))
    read = rig.read(with_backup(None))
    assert (read.lines, read.read_by, rig.client.calls, rig.local.pages) == ((), BY_4, [], 0)


@pytest.mark.parametrize("code", ["mistral", "mistral_key"])
def test_an_outage_a_key_or_no_credit_still_waits(code: str) -> None:
    rig = Rig(FakeMistral(MistralUnavailable(code)))
    with pytest.raises(BackendUnavailable) as waited:
        rig.read(with_backup())
    assert str(waited.value) == code
    assert (rig.client.calls, rig.local.pages) == ([], 0)


def test_mistral_not_set_up_still_waits() -> None:
    rig = Rig(None)
    with pytest.raises(BackendUnavailable, match=r"^mistral_setup$"):
        rig.read(with_backup())
    assert rig.client.calls == []


def test_a_gemini_document_never_uses_the_backup_reader() -> None:
    rig = Rig(FakeMistral(), ModelOutputError("invalid_output"))
    read = rig.read(dataclasses.replace(with_backup(), scan_reader="openrouter"))
    assert READER_REFUSED in read.issues and read.read_by == "rapidocr"  # Gemini's own local read
    assert len(rig.client.calls) == 1 and rig.mistral is not None and rig.mistral.calls == []


# --- once a page, and a backup read that fails ------------------------------------------------


def test_a_backup_read_refused_leaves_the_page_as_mistral_left_it() -> None:
    # Changed on purpose (#664 review): only a refusal (or no text) keeps Mistral's page; a wait waits.
    rig = Rig(FakeMistral(BadReply("refused_request")), ModelOutputError("invalid_output"))
    read = rig.read(with_backup())
    assert (read.lines, read.issues, read.read_by) == ((), (READER_REFUSED,), BY_4)
    assert len(rig.client.calls) == 1
    assert rig.local.pages == 0  # #664 review: never read locally only to be thrown away


@pytest.mark.parametrize(
    ("backup", "failure"),
    [
        (NORMAL, ProviderNotAllowed(PROVIDER_NOT_ALLOWED)),  # outside OPENROUTER_PROVIDERS: nothing sent
        (NORMAL, ModelUnavailable("timeout")),  # the page's time ran out
        (dataclasses.replace(NORMAL, model=FLASH), ModelUnavailable("model_not_found")),  # withdrawn
        (ULC, ModelOutputError("output_truncated")),  # cut off at its ceiling's cap
        (ULC, ModelOutputError("invalid_output")),
        (dataclasses.replace(ULC, ulc_prices=None), None),  # no known price: nothing sent
    ],
)
def test_no_local_read_is_made_on_any_path_where_the_model_does_not_read(
    backup: BackupReader, failure: Exception | None
) -> None:
    """#664 review: every path where Gemini alone reads the page locally (a refusal, a timeout, a
    withdrawn model, Ultra low cost's ceiling) leaves the backup's page as Mistral left it, with
    no local read, its billed tokens kept."""
    rig = Rig(FakeMistral(BadReply("refused_request")), *([] if failure is None else [failure]))
    read = rig.read(with_backup(backup))
    assert (read.lines, read.issues, read.read_by) == ((), (READER_REFUSED,), BY_4)
    assert rig.local.pages == 0


def test_a_page_ultra_low_cost_refused_before_is_not_read_locally_again() -> None:
    """Its ULC_MARK (a billed refusal cached) is read again, never locally."""
    cache = FakeCache()
    key = PageKey(CUSTOMER, DOCUMENT, 1, pixels_sha256(page()), FLASH, f"{COMPACT_PROMPT_VERSION}+ulc40")
    cache.rows[key] = ({ULC_MARK: "cost_ceiling"}, 2400, 300)
    rig = Rig(FakeMistral(BadReply("refused_request")))  # Gemini has nothing scripted
    read = rig.read(on_mistral(cache, backup=ULC))
    assert (read.lines, read.read_by, rig.local.pages, rig.client.calls) == ((), BY_4, 0, [])
    assert key in cache.gets


@pytest.mark.parametrize(("failure", "code"), [("server_error", "gemini"), ("key_rejected", "gemini_key")])
def test_an_outage_or_key_problem_of_the_backup_reader_waits(failure: str, code: str) -> None:
    """#664 review: the document waits, as a Gemini document does, never failing for it."""
    rig = Rig(FakeMistral(BadReply("refused_request")), ModelUnavailable(failure))
    with pytest.raises(BackendUnavailable) as waited:
        rig.read(with_backup())
    assert str(waited.value) == code and rig.local.pages == 0


def test_past_the_outage_window_the_page_stays_as_mistral_left_it() -> None:
    """A backup reader down past READER_OUTAGE_FALLBACK_MINUTES: Gemini would read the page
    locally; the backup reader leaves it as Mistral did, and the document goes on."""
    refused = BadReply("refused_request")
    down = ModelUnavailable("server_error")
    rig = Rig(FakeMistral(refused, refused), down, down, reader_outage_fallback_minutes=1)
    with pytest.raises(BackendUnavailable, match=r"^gemini$"):
        rig.read(with_backup())  # the first try waits
    rig.client.clock += 61  # the next try, past the window
    read = rig.read(with_backup())
    assert (read.lines, read.issues, read.read_by) == ((), (READER_REFUSED,), BY_4)
    assert len(rig.client.calls) == 2
    assert rig.local.pages == 0  # #664 review: past the window, no local read either


def test_an_outage_of_the_backup_reader_leaves_the_document_waiting_not_failed() -> None:
    rig = Rig(FakeMistral((map_reply(), 1)), ModelUnavailable("server_error"))
    system = gemini_rig(rig.engine, checker=FakeChecker(), job_reading=job_reading(NORMAL))
    with pytest.raises(BackendUnavailable, match=r"^gemini$"):
        system.run(make_pdf([SCANNED]))
    assert system.status(system.jobs[0]) != DocumentStatus.FAILED


def test_no_time_left_the_page_stays_as_mistral_left_it() -> None:
    rig = Rig(FakeMistral(), ocr_timeout_seconds=60, ocr_seconds_per_page=60)
    pages = with_backup(page_count=1)
    with rig.engine.document(pages):
        rig.client.clock += 60  # the document's time is spent: Mistral refuses (mistral_no_time)
        read = rig.engine.read_page(page(), page_no=1)
    assert (read.lines, read.issues, read.read_by) == ((), (READER_REFUSED,), BY_4)
    assert rig.client.calls == []  # Gemini has no time either: nothing is sent
    assert rig.local.pages == 0  # #664 review: no local read made only to be thrown away
    assert {key.model for key in cast(FakeCache, pages.cache).gets} <= {BY_4}  # not even tried


def test_each_page_is_read_by_the_backup_reader_once() -> None:
    """The image and PDF readers read a page that gave nothing once more (at full size, or at the
    ceiling DPI): Mistral is asked again, the backup reader never."""
    refused = BadReply("refused_request")
    mistral = FakeMistral(refused, refused, (map_reply(), 1))
    rig = Rig(mistral, ModelOutputError("invalid_output"), gemini_map())
    with rig.engine.document(with_backup()):
        first = rig.engine.read_page(page(), page_no=1)
        again = rig.engine.read_page(page(), page_no=1)
        other = rig.engine.read_page(page(), page_no=2)  # another page has its own one try
    assert first.lines == () and again.lines == ()
    assert texts(other) == MAP_TEXT
    assert len(mistral.calls) == 3 and len(rig.client.calls) == 2
    # A new document (the next try, or "Read again") is a new reading: its pages may be tried again.
    rig.client.script.append(gemini_map())
    mistral.script.append((map_reply(), 1))
    assert texts(rig.read(with_backup())) == MAP_TEXT


def test_the_map_file_with_a_failing_backup_reader_still_fails_as_today() -> None:
    refused = BadReply("refused_request")
    rig = Rig(FakeMistral(refused, refused), ModelOutputError("invalid_output"))
    socket = build_reader_socket(make_settings(), rig.engine)
    with rig.engine.document(with_backup()), pytest.raises(ReadError, match="no_text"):
        socket.read(sniff.PNG, png())
    assert len(rig.client.calls) == 1


# --- billing ------------------------------------------------------------------------------------


def test_the_backup_read_is_billed_to_the_document_as_a_page_read() -> None:
    with ledger() as kept:
        Rig(FakeMistral((map_reply(), 1)), gemini_map()).read(with_backup())
    rows = {row.model: row for row in kept.rows}
    assert set(rows) == {BY_4, MODEL}
    mistral, backup = rows[BY_4], rows[MODEL]
    assert (mistral.kind, mistral.calls, mistral.pages) == ("scan", 1, 1)
    assert (backup.kind, backup.calls, backup.pages, backup.cached) == ("scan", 1, 1, False)
    assert (backup.input_tokens, backup.output_tokens, backup.thinking) == (1700, 4000, "low")
    assert [pages for pages, _usd in kept.live] == [1, 1]  # #478: both reads count on the day


def test_a_failed_backup_read_is_still_billed_and_its_tokens_kept_on_the_page() -> None:
    refused = ModelOutputError("invalid_output", input_tokens=1700, output_tokens=300)
    with ledger() as kept:
        read = Rig(FakeMistral(BadReply("refused_request")), refused).read(with_backup())
    [row] = kept.rows
    assert (row.model, row.calls, row.pages) == (MODEL, 1, 1)
    assert (read.input_tokens, read.output_tokens, read.read_by) == (1700, 300, BY_4)


def test_an_ultra_low_cost_backup_reader_reads_once_under_its_ceiling() -> None:
    compact = "g 0 l -\nt 40 100 70 900 Survey No 42\np 100 100 120 900 Village Wagholi"
    rig = Rig(FakeMistral((map_reply(), 1)), reply({"r": compact}, 2400, 300))
    with ledger() as kept:
        read = rig.read(with_backup(ULC))
    assert texts(read) == MAP_TEXT
    assert (read.read_by, read.read_thinking) == (FLASH, "minimal")
    [call] = rig.client.calls
    assert (call.model, call.thinking_level) == (FLASH, "minimal")
    backup = next(row for row in kept.rows if row.model == FLASH)
    assert (backup.kind, backup.pages) == (ULC_SCAN, 1)


def test_the_backup_read_is_logged_with_ids_and_never_the_text(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.INFO)
    Rig(FakeMistral((map_reply(), 1)), gemini_map()).read(with_backup())
    [record] = [record for record in caplog.records if getattr(record, "code", None) == "backup_reader"]
    ids = tuple(getattr(record, name, None) for name in ("customer_id", "document_id", "page_no", "model"))
    assert ids == (CUSTOMER, DOCUMENT, 1, MODEL)
    assert not any(text in record.getMessage() for text in MAP_TEXT)


# --- the pipeline -------------------------------------------------------------------------------


def job_reading(backup: BackupReader | None, user: str | None = "u1") -> _Reading:
    """The job's reading as the worker resolves it: Mistral OCR 4.1, with ``backup``."""
    lead = models.Agent(MODEL, "google-vertex", "Gemini")
    asked = _Reading(user, MODEL)
    asked.reading = dataclasses.replace(
        asked.reading,
        reading=models.Reading("single", lead, scan_reader="mistral", mistral_model=OCR_4),
        backup=backup,
    )
    return asked


def test_the_map_document_that_failed_on_mistral_is_ready_with_the_backup_reader() -> None:
    failing = gemini_rig(
        Rig(FakeMistral((map_reply(), 1))).engine, checker=FakeChecker(), job_reading=job_reading(None)
    )
    with pytest.raises(JobFailed) as failed:
        failing.run(make_pdf([SCANNED]))
    assert failed.value.code == "no_text" and failing.status(failing.jobs[0]) == DocumentStatus.FAILED
    rig = Rig(FakeMistral((map_reply(), 1)), gemini_map())
    system = gemini_rig(rig.engine, checker=FakeChecker(), job_reading=job_reading(NORMAL))
    job = system.run(make_pdf([SCANNED]))
    assert system.status(job) == DocumentStatus.READY
    [record] = system.pages(job)
    assert record.read_by == MODEL  # the page says who read it
    totals = system.totals(job)
    assert totals.config["read_model"] == BY_4 and totals.pages_gemini == 1  # the run is Mistral's
    assert len(rig.client.calls) == 1


def test_a_document_mistral_reads_whole_never_calls_the_backup_reader() -> None:
    rig = Rig(FakeMistral((mistral_reply("Total 1,180.00"), 1)))
    system = gemini_rig(rig.engine, checker=FakeChecker(), job_reading=job_reading(NORMAL))
    job = system.run(make_pdf([SCANNED]))
    assert system.status(job) == DocumentStatus.READY and rig.client.calls == []
    assert [record.read_by for record in system.pages(job)] == [BY_4]


def test_the_pipeline_passes_the_backup_reader_only_to_a_mistral_document() -> None:
    reader = ScriptedReader({1: pipeline_page(TOTAL, read_by=BY_4)})
    gemini_rig(reader, checker=FakeChecker(), job_reading=job_reading(NORMAL)).run(make_pdf([SCANNED]))
    (opened,) = reader.opened
    assert opened.backup == NORMAL
    on_gemini = job_reading(NORMAL)
    on_gemini.reading = dataclasses.replace(
        on_gemini.reading, reading=models.Reading("single", models.Agent(MODEL, "google-vertex", "Gemini"))
    )
    reader = ScriptedReader({1: pipeline_page(TOTAL)})
    gemini_rig(reader, checker=FakeChecker(), job_reading=on_gemini).run(make_pdf([SCANNED]))
    (opened,) = reader.opened
    assert opened.backup is None


def test_a_backup_reader_is_sent_only_levels_it_takes() -> None:
    plain = BackupReader("meta-llama/llama-4-scout", "groq", NO_THINKING, thinks=False)
    reader = ScriptedReader({1: pipeline_page(TOTAL, read_by=BY_4)})
    gemini_rig(reader, checker=FakeChecker(), job_reading=job_reading(plain)).run(make_pdf([SCANNED]))
    (opened,) = reader.opened
    assert plain.model in opened.plain  # no thinking levels: none sent, a read again too
    some = dataclasses.replace(NORMAL, levels=frozenset({"low", "high"}))
    reader = ScriptedReader({1: pipeline_page(TOTAL, read_by=BY_4)})
    gemini_rig(reader, checker=FakeChecker(), job_reading=job_reading(some)).run(make_pdf([SCANNED]))
    (opened,) = reader.opened
    assert opened.levels[MODEL] == {"low", "high"} and MODEL not in opened.plain


def test_a_paused_organisation_reads_nothing_not_even_the_backup_reader() -> None:
    @contextmanager
    def paused(customer_id: str, document_id: str, *, ai_pages: bool = True) -> Iterator[None]:
        raise ReadingPaused(DAILY_LIMIT)
        yield  # never: a paused document is not read

    rig = Rig(FakeMistral())
    system = gemini_rig(
        rig.engine, checker=FakeChecker(), job_reading=job_reading(NORMAL), reading_pause=paused
    )
    job = system.run(make_pdf([SCANNED]))
    assert system.totals(job).error_code == "paused_daily_limit"
    assert rig.mistral is not None and rig.mistral.calls == [] and rig.client.calls == []
