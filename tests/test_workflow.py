"""ProcessDocument, run against Temporal's time-skipping test server: no real Postgres,
storage or Temporal server needed. Stand-in activities are registered under the same
names the workflow calls, so the workflow's own logic is what's under test here.
"""

from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass
from typing import Any

import pytest
from temporalio import activity
from temporalio.client import WorkflowFailureError
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Worker

from kaagaz_worker.contracts import FailInput, RegisterResult
from kaagaz_worker.workflow import ProcessDocument

TASK_QUEUE = "test-cpu"


@dataclass
class Calls:
    """What the stand-in activities were called with, in order."""

    steps: list[str]
    failed: FailInput | None = None


def build_activities(calls: Calls, *, fail_on: str | None = None) -> list[Callable[..., Any]]:
    @activity.defn(name="register_upload")
    async def register_upload(upload_id: str) -> RegisterResult:
        calls.steps.append(f"register_upload:{upload_id}")
        if fail_on == "register_upload":
            raise ValueError("boom")
        return RegisterResult(document_id="doc-1")

    def placeholder(name: str) -> Callable[..., Any]:
        @activity.defn(name=name)
        async def run(document_id: str) -> None:
            calls.steps.append(f"{name}:{document_id}")
            if fail_on == name:
                raise ValueError("boom")

        return run

    @activity.defn(name="publish_document")
    async def publish_document(document_id: str) -> None:
        calls.steps.append(f"publish_document:{document_id}")
        if fail_on == "publish_document":
            raise ValueError("boom")

    @activity.defn(name="mark_failed")
    async def mark_failed(fail: FailInput) -> None:
        calls.failed = fail

    return [
        register_upload,
        placeholder("route_pages"),
        placeholder("parse_pages"),
        placeholder("extract_fields"),
        placeholder("validate_fields"),
        placeholder("index_document"),
        publish_document,
        mark_failed,
    ]


@pytest.fixture
async def env() -> AsyncIterator[WorkflowEnvironment]:
    async with await WorkflowEnvironment.start_time_skipping() as environment:
        yield environment


async def test_every_step_runs_in_order_then_publishes(env: WorkflowEnvironment) -> None:
    calls = Calls(steps=[])
    async with Worker(
        env.client,
        task_queue=TASK_QUEUE,
        workflows=[ProcessDocument],
        activities=build_activities(calls),
    ):
        result = await env.client.execute_workflow(
            ProcessDocument.run, "upload-1", id="upload-upload-1", task_queue=TASK_QUEUE
        )
    assert result == "published"
    assert calls.steps == [
        "register_upload:upload-1",
        "route_pages:doc-1",
        "parse_pages:doc-1",
        "extract_fields:doc-1",
        "validate_fields:doc-1",
        "index_document:doc-1",
        "publish_document:doc-1",
    ]
    assert calls.failed is None


async def test_a_failed_step_marks_the_upload_failed_and_reraises(
    env: WorkflowEnvironment,
) -> None:
    calls = Calls(steps=[])
    async with Worker(
        env.client,
        task_queue=TASK_QUEUE,
        workflows=[ProcessDocument],
        activities=build_activities(calls, fail_on="extract_fields"),
    ):
        with pytest.raises(WorkflowFailureError):
            await env.client.execute_workflow(
                ProcessDocument.run, "upload-2", id="upload-upload-2", task_queue=TASK_QUEUE
            )
    assert calls.failed is not None
    assert calls.failed.upload_id == "upload-2"
    assert calls.failed.document_id == "doc-1"


async def test_a_failed_register_marks_the_upload_failed_with_no_document(
    env: WorkflowEnvironment,
) -> None:
    calls = Calls(steps=[])
    async with Worker(
        env.client,
        task_queue=TASK_QUEUE,
        workflows=[ProcessDocument],
        activities=build_activities(calls, fail_on="register_upload"),
    ):
        with pytest.raises(WorkflowFailureError):
            await env.client.execute_workflow(
                ProcessDocument.run, "upload-3", id="upload-upload-3", task_queue=TASK_QUEUE
            )
    assert calls.failed is not None
    assert calls.failed.document_id is None


async def test_the_review_finished_signal_is_defined() -> None:
    # Fixes the API's contract now, even though nothing waits on it until Day 4.
    assert hasattr(ProcessDocument, "review_finished")
