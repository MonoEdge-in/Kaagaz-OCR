"""publish_document: Day 1's other real step. mark_failed: the one failure path, run when
any earlier step in the workflow fails for good.
"""

from uuid import UUID

from temporalio import activity

from kaagaz_shared.models import AuditLog, Document, Upload
from kaagaz_worker.contracts import FailInput
from kaagaz_worker.deps import Deps


class PublishActivities:
    def __init__(self, deps: Deps) -> None:
        self._deps = deps

    @activity.defn(name="publish_document")
    async def publish_document(self, document_id: str) -> None:
        async with self._deps.sessions() as session:
            document = await session.get(Document, UUID(document_id))
            if document is None:
                return
            document.status = "published"
            document.current_step = "published"
            session.add(
                AuditLog(
                    actor="worker",
                    action="document.published",
                    entity_type="document",
                    entity_id=document.id,
                    details={},
                )
            )
            await session.commit()

    @activity.defn(name="mark_failed")
    async def mark_failed(self, fail: FailInput) -> None:
        async with self._deps.sessions() as session:
            upload = await session.get(Upload, UUID(fail.upload_id))
            if upload is not None and upload.status not in ("registered", "duplicate"):
                upload.status = "failed"
                upload.error = fail.error
            if fail.document_id is not None:
                document = await session.get(Document, UUID(fail.document_id))
                if document is not None:
                    document.status = "failed"
            session.add(
                AuditLog(
                    actor="worker",
                    action="document.failed",
                    entity_type="upload",
                    entity_id=UUID(fail.upload_id),
                    details={"error": fail.error},
                )
            )
            await session.commit()
