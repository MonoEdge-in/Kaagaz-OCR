"""ProcessDocument: the seven-step pipeline every upload runs (day-1-prd.html section 6).

Today only `register_upload` and `publish_document` do real work; the other five steps
are placeholders that Days 2 to 5 replace one at a time, so this workflow's shape does
not change as they do. Deterministic: no I/O, no randomness, no wall-clock reads — all of
that happens inside activities, called here only by name.
"""

from datetime import timedelta

from temporalio import workflow
from temporalio.common import RetryPolicy

from kaagaz_worker.contracts import FailInput, RegisterResult

# Retries start after 2 seconds, double each time, and stop after 5 attempts.
RETRY_POLICY = RetryPolicy(
    initial_interval=timedelta(seconds=2), backoff_coefficient=2.0, maximum_attempts=5
)
PLACEHOLDER_TIMEOUT = timedelta(minutes=5)
# In order. Each is a placeholder today; a later day replaces one at a time with real work.
PLACEHOLDER_STEPS = (
    "route_pages",
    "parse_pages",
    "extract_fields",
    "validate_fields",
    "index_document",
)


@workflow.defn
class ProcessDocument:
    @workflow.run
    async def run(self, upload_id: str) -> str:
        document_id: str | None = None
        try:
            registered = await workflow.execute_activity(
                "register_upload",
                upload_id,
                start_to_close_timeout=timedelta(minutes=5),
                retry_policy=RETRY_POLICY,
                result_type=RegisterResult,
            )
            document_id = registered.document_id

            for step in PLACEHOLDER_STEPS:
                await workflow.execute_activity(
                    step,
                    document_id,
                    start_to_close_timeout=PLACEHOLDER_TIMEOUT,
                    retry_policy=RETRY_POLICY,
                )

            await workflow.execute_activity(
                "publish_document",
                document_id,
                start_to_close_timeout=timedelta(minutes=1),
                retry_policy=RETRY_POLICY,
            )
            return "published"
        except Exception as error:
            await workflow.execute_activity(
                "mark_failed",
                FailInput(upload_id=upload_id, document_id=document_id, error=str(error)[:500]),
                start_to_close_timeout=timedelta(minutes=1),
            )
            raise

    @workflow.signal
    def review_finished(self) -> None:
        """Fixes the API's contract now. Days 3 to 4 add the review wait this resumes."""
