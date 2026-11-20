"""#713: a Stop that lands in the worker's gaps, on the pipeline's in-memory fakes (the stores' own
SQL and the end-to-end cases: tests/test_pipeline_db.py). A Stop sets the file ``failed`` with
``stopped`` and ends its runs still running (db/trash.py ``stop_reading``)."""

import unittest
from datetime import UTC, datetime
from typing import Any
from unittest import mock

from kaagaz.ingestion import sniff
from kaagaz.scanning.gate import DocumentStatus
from tests.fakes import FakePreparer, PresetSocket
from tests.support import PDF_BYTES
from tests.test_locked_pipeline import _SPANS
from tests.test_pipeline import RecordingSocket, _pictured
from tests.test_pipeline_runs import Rig, beat, rig

_NOW = datetime(2026, 11, 20, tzinfo=UTC)


def _stop(system: Rig, customer_id: str, document_id: str) -> None:
    """What a Stop commits: the status, and every run still running ended failed."""
    system.statuses.set_status(customer_id, document_id, DocumentStatus.FAILED)
    system.runs.close_running(customer_id, document_id, status="failed", error_code="stopped", now=_NOW)


class StopInTheGapsTest(unittest.TestCase):
    def test_a_stop_between_the_workers_check_and_its_run_is_seen_and_nothing_is_read(self) -> None:
        socket = RecordingSocket({sniff.PDF: _SPANS})
        system = rig(socket=socket, preparer=FakePreparer({sniff.PDF: _pictured(pages=1)}))
        job = system.job(PDF_BYTES)
        start = system.runs.start

        def stop_then_start(customer_id: str, document_id: str, *, now: datetime) -> str:
            _stop(system, customer_id, document_id)  # it closed no run of this delivery: none yet
            return start(customer_id, document_id, now=now)

        with mock.patch.object(system.runs, "start", stop_then_start):
            system.processor(job, beat)

        self.assertEqual((socket.reads, system.store.saves), ([], 0))  # never sent anywhere
        self.assertIs(system.statuses.get_status(job.customer_id, job.document_id), DocumentStatus.FAILED)
        self.assertEqual(
            [(run.status, run.error_code) for run in system.runs_of(job)], [("superseded", None)]
        )

    def test_a_stop_just_before_needs_reader_is_written_keeps_it_stopped(self) -> None:
        """The file has no reader: a Stop after require_clean is never turned into needs_reader (the
        write is fenced by the run, which the Stop closed)."""
        system = rig(socket=PresetSocket({}), preparer=FakePreparer({sniff.PDF: _pictured(pages=1)}))
        job = system.job(PDF_BYTES)
        save = system.store.save

        def save_then_stop(*args: Any, **kwargs: Any) -> None:
            save(*args, **kwargs)
            _stop(system, job.customer_id, job.document_id)

        with mock.patch.object(system.store, "save", save_then_stop):
            system.processor(job, beat)

        self.assertIs(system.statuses.get_status(job.customer_id, job.document_id), DocumentStatus.FAILED)
        self.assertEqual(
            [(run.status, run.error_code) for run in system.runs_of(job)], [("failed", "stopped")]
        )
