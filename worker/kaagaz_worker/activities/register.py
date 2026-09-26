"""register_upload: Day 1's first real step. Creates the documents row for an upload.

Fingerprinting and duplicate detection are Day 2 (day-1-prd.html section 6's "Becomes
real on" column); today every upload becomes its own document.
"""

from uuid import UUID

from temporalio import activity
from temporalio.exceptions import ApplicationError

from kaagaz_shared.models import AuditLog, Document, Upload
from kaagaz_worker.contracts import RegisterResult
from kaagaz_worker.deps import Deps


class RegisterActivities:
    def __init__(self, deps: Deps) -> None:
        self._deps = deps

    @activity.defn(name="register_upload")
    async def register_upload(self, upload_id: str) -> RegisterResult:
        async with self._deps.sessions() as session:
            upload = await session.get(Upload, UUID(upload_id))
            if upload is None:
                raise ApplicationError(
                    f"Upload {upload_id} does not exist.", type="BadInput", non_retryable=True
                )

            document = Document(
                original_filename=upload.original_filename,
                content_type=upload.content_type,
                size_bytes=upload.size_bytes,
                storage_key=upload.incoming_key,
                uploaded_by=upload.uploaded_by,
                status="processing",
            )
            session.add(document)
            await session.flush()

            upload.document_id = document.id
            upload.status = "registered"
            session.add(
                AuditLog(
                    actor="worker",
                    action="document.registered",
                    entity_type="document",
                    entity_id=document.id,
                    details={"upload_id": upload_id},
                )
            )
            await session.commit()
            return RegisterResult(document_id=str(document.id))
