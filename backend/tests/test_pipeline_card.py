"""The model stage in the pipeline (PRD 2 D1 to D6, ML 2, ML 10), with a fake chat model.

The card is read after the rule values and before the labels; its facts, its
links and the context headers' type and date are saved with the run's model
numbers. With the model off or down, the document is saved exactly as with no
model at all.
"""

import io
import unittest
from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import Any

from kaagaz.ai.client import ModelUnavailable
from kaagaz.extraction.card import CARD_PROMPT_HASH
from kaagaz.extraction.invoice import WindowSettings
from kaagaz.extraction.understanding import ModelBudget
from kaagaz.extraction.values import LinkIdentifier
from kaagaz.ingestion import sniff
from kaagaz.ingestion.ports import Job
from kaagaz.ingestion.service import UploadService
from kaagaz.pipeline import DocumentContent, DocumentProcessor, Progress, RunTotals
from kaagaz.reading.spans import BoxLocation, PageInfo, Span
from kaagaz.scanning.gate import DocumentStatus, ScanGate, Verdict
from tests.fakes import (
    FakeChat,
    FakePrepared,
    FakePreparer,
    FakeScanner,
    MemoryDocuments,
    MemoryPageImages,
    MemoryPipelineStore,
    MemoryProgress,
    MemoryQueue,
    MemoryRuns,
    MemoryStatuses,
    MemoryStorage,
    ModelDown,
    PresetSocket,
    fake_sheet_pages,
)
from tests.settings_factory import make_settings
from tests.support import PDF_BYTES, SeqIds
from tests.test_extraction_card import fact, reply
from tests.test_pipeline import PIPELINE_POLICY

CUSTOMER = "cust-a"
T0 = datetime(2026, 9, 30, 10, 0, tzinfo=UTC)
CONFIG = {"build": "test"}
NO_TIME = {"seconds_reading": 0.0, "seconds_after_reading": 0.0}  # the timer stands at 0
BUDGET = ModelBudget(card_max_chars=12000, card_max_tokens=1024, num_ctx=8192, invoice_windows=None)


def line(text: str, y: float) -> Span:
    return Span(text, text, BoxLocation(1, 0.1, y, 0.9, y + 0.02), origin="text_layer")


SPANS = [
    line("Sharma Traders", 0.05),
    line("Invoice No: INV 17", 0.10),
    line("Date: 12/08/2026", 0.15),
    line("Grand Total: Rs. 1,180.00", 0.30),
]
CARD = reply(
    doc_type="receipt",
    parties=[{"role": "seller", "name_quote": "Sharma Traders"}],
    identifiers=[{"kind": "invoice_number", "value_quote": "INV 17"}],
    document_date={"value_quote": "12/08/2026"},
    key_facts=[fact("net_payable", "Rs. 1,180.00", "Grand Total:"), fact("discount", "10")],
    title="Receipt INV 17 from Sharma Traders",
    summary="Sharma Traders was paid Rs. 1,180.00.",
)


@dataclass
class Rig:
    upload: UploadService
    store: MemoryPipelineStore
    runs: MemoryRuns
    progress: MemoryProgress
    statuses: MemoryStatuses
    processor: DocumentProcessor
    beats: list[int]

    def read(self) -> Job:
        job = Job(CUSTOMER, self.upload.accept(CUSTOMER, io.BytesIO(PDF_BYTES)).document_id)
        self.processor(job, lambda: self.beats.append(1))
        return job

    def content(self, job: Job) -> DocumentContent:
        content = self.store.content(job.customer_id, job.document_id)
        assert content is not None
        return content

    def read_content(self) -> DocumentContent:
        return self.content(self.read())

    def totals(self, job: Job) -> RunTotals:
        (run,) = self.runs.for_document(job.customer_id, job.document_id)
        assert run.totals is not None
        return run.totals


def rig(chat: FakeChat | None = None, **model: Any) -> Rig:
    """One text-layer PDF page, read by both readers, with the runs and progress kept."""
    statuses, storage = MemoryStatuses(), MemoryStorage()
    repo = MemoryDocuments(statuses)
    runs = MemoryRuns(repo)
    repo.runs = runs
    statuses.runs = runs
    store, progress = MemoryPipelineStore(runs), MemoryProgress(repo, statuses)
    preparer = FakePreparer({sniff.PDF: FakePrepared([PageInfo(1, 612.0, 792.0, "pt")], {})})
    processor = DocumentProcessor(
        gate=ScanGate(storage, FakeScanner(Verdict.CLEAN), storage, statuses),
        statuses=statuses,
        documents=repo,
        socket=PresetSocket({sniff.PDF: SPANS}, {sniff.PDF: SPANS}),
        prepare=preparer.prepare,
        sheet_pages=fake_sheet_pages,
        page_images=MemoryPageImages(),
        store=store,
        model_errors=(ModelDown,),
        runs=runs,
        progress=progress,
        clock=lambda: T0,
        timer=lambda: 0.0,
        run_config=CONFIG,
        **{**({"chat": chat, "chat_model": "chat-tag", "model_budget": BUDGET} if chat else {}), **model},
    )
    upload = UploadService(PIPELINE_POLICY, repo, storage, MemoryQueue(), new_id=SeqIds())
    return Rig(upload, store, runs, progress, statuses, processor, [])


def labels(content: DocumentContent) -> list[tuple[str, str, str, str]]:
    return [(v.record_key, v.field, v.label, v.label_reason) for v in content.values]


class ModelOnTest(unittest.TestCase):
    def test_the_card_facts_links_and_context_are_saved(self) -> None:
        chat = FakeChat(CARD)
        system = rig(chat)
        job = system.read()
        content = system.content(job)

        facts = content.facts
        self.assertEqual(
            (facts.doc_type, facts.languages, facts.summary, facts.document_date),
            ("receipt", ("en",), "Sharma Traders was paid Rs. 1,180.00.", date(2026, 8, 12)),
        )
        assert facts.card is not None
        (run,) = system.runs.for_document(CUSTOMER, job.document_id)
        self.assertEqual((facts.card["prompt"], facts.card["model"]), (CARD_PROMPT_HASH, "fake-chat"))
        self.assertEqual(facts.card["run_id"], next(iter(system.runs.rows)))
        self.assertEqual(run.status, "ready")
        # D5: the labelled invoice number is the document's own; with a card it links.
        self.assertEqual(content.links, (LinkIdentifier("invoice_number", "inv17"),))
        # C4: the card's type and date reach every piece's header.
        self.assertTrue(content.pieces)
        for piece in content.pieces:
            self.assertIn("Type: receipt | Date: 2026-08-12", piece.context_header)
        self.assertEqual(len(chat.calls), 1)

    def test_rule_values_keep_their_labels_and_card_values_are_never_verified(self) -> None:
        """ML 2: each list goes to its own labeller; Lead's resolution 1: card values are
        needs review. The card's invoice number is a rule field already, so it is not
        stored twice (and leaves the card JSON)."""
        content = rig(FakeChat(CARD)).read_content()
        self.assertEqual(
            labels(content),
            [
                ("document", "invoice_number", "verified", "readers_agree"),
                ("document", "invoice_date", "verified", "readers_agree"),
                ("document", "total_amount", "verified", "readers_agree"),
                ("card", "party_name", "needs_review", "model_chosen"),
                ("card", "document_date", "needs_review", "model_chosen"),
                ("card", "net_payable", "needs_review", "model_chosen"),
            ],
        )
        net = next(v for v in content.values if v.field == "net_payable")
        self.assertEqual((net.value_text, net.label_location), ("Rs. 1,180.00", SPANS[3].location))
        assert content.facts.card is not None
        self.assertEqual(content.facts.card["identifiers"], [])

    def test_the_run_records_the_chat_model_and_its_quotes(self) -> None:
        system = rig(FakeChat(CARD))
        totals = system.totals(system.read())
        self.assertEqual(
            (totals.status, totals.error_code, totals.model_chat, totals.model_calls),
            ("ready", None, "chat-tag", 1),
        )
        self.assertEqual((totals.quotes_returned, totals.dropped_quotes), (5, 1))  # "10" is made up
        # Changed on purpose (fast scan, council 16 Oct 2026): the card's own seconds too.
        self.assertEqual(
            totals.config,
            {
                **CONFIG,
                "loose_quotes": 0,
                "ambiguous_quotes": 0,
                "summary_dropped": 0,
                **NO_TIME,
                "seconds_card": 0.0,
                # Ready first (16 Oct 2026): ready before the card, then understood.
                "seconds_to_ready": 0.0,
                "seconds_after_ready": 0.0,
            },
        )
        self.assertEqual((totals.values_verified, totals.values_needs_review), (3, 3))

    def test_progress_says_understanding_and_the_heartbeat_beats_after_the_call_and_the_searches(
        self,
    ) -> None:
        on, off = rig(FakeChat(CARD)), rig()
        on_job, _ = on.read(), off.read()
        steps = [p for _, _, p in on.progress.history if p.step == "understanding"]
        # Ready first: the card comes after Ready, so only the steps before it are written (the
        # last says when it was ready); the beats after the call, the searches and the 2nd save.
        self.assertEqual(steps, [Progress("understanding", 0, 2)] * 2)
        self.assertEqual([p.ready_at for p in steps], [None, T0])
        self.assertEqual(len(on.beats) - len(off.beats), 3)
        self.assertEqual(on.statuses.get_status(CUSTOMER, on_job.document_id), DocumentStatus.READY)

    def test_a_chat_model_needs_its_budget(self) -> None:
        with self.assertRaises(ValueError):
            rig(FakeChat(CARD), model_budget=None)


class ModelOffOrDownTest(unittest.TestCase):
    """With the model off, or down, the saved document is exactly the no-model one."""

    def model_off(self) -> DocumentContent:
        return rig().read_content()

    def test_model_off_has_no_card_no_type_no_links_and_languages_from_the_text(self) -> None:
        system = rig()
        job = system.read()
        content = system.content(job)
        self.assertEqual(
            (content.facts.card, content.facts.doc_type, content.facts.languages, content.links),
            (None, None, ("en",), ()),
        )
        self.assertNotIn("Type:", content.pieces[0].context_header)
        totals = system.totals(job)
        self.assertEqual((totals.model_chat, totals.model_calls, totals.quotes_returned), (None, 0, 0))
        self.assertEqual(totals.config, {**CONFIG, **NO_TIME})

    def test_the_model_down_gives_the_model_off_result_and_the_run_keeps_the_code(self) -> None:
        down = rig(FakeChat(ModelUnavailable("timeout")))
        job = down.read()
        self.assertEqual(down.content(job), self.model_off())
        totals = down.totals(job)
        self.assertEqual(
            (totals.status, totals.error_code, totals.model_chat, totals.model_calls),
            ("ready", "model_timeout", "chat-tag", 1),
        )
        self.assertEqual(down.statuses.get_status(CUSTOMER, job.document_id), DocumentStatus.READY)


class ModelBudgetTest(unittest.TestCase):
    def test_the_budget_comes_from_the_settings_with_the_invoice_windows(self) -> None:
        settings = make_settings(card_max_chars=9000, model_card_max_tokens=900, model_context_tokens=6000)
        self.assertEqual(
            ModelBudget.from_settings(settings),
            ModelBudget(9000, 900, 6000, WindowSettings.from_settings(settings)),
        )
