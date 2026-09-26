"""register_upload, publish_document and mark_failed, against real Postgres.

Each activity class takes a `Deps` built from the `sessions` fixture, so it reads and
writes inside the same transaction the test rolls back.
"""

from typing import cast
from uuid import UUID

import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from kaagaz_worker.activities.publish import PublishActivities
from kaagaz_worker.activities.register import RegisterActivities
from kaagaz_worker.contracts import FailInput
from kaagaz_worker.deps import Deps

pytestmark = pytest.mark.db


class FakeSettings:
    """Only the field these activities read."""

    placeholder_step_seconds = 0.0


def deps_for(sessions: async_sessionmaker[AsyncSession]) -> Deps:
    # Neither activity under test reads `.engine` or `.storage`; only `.sessions` and,
    # indirectly through the class the placeholder tests use, `.settings`.
    return Deps(
        settings=cast("object", FakeSettings()),  # type: ignore[arg-type]
        engine=cast(AsyncEngine, None),
        sessions=sessions,
        storage=cast("object", None),  # type: ignore[arg-type]
    )


async def make_upload(sessions: async_sessionmaker[AsyncSession]) -> str:
    async with sessions() as session:
        result = await session.execute(
            sa.text(
                "INSERT INTO uploads (original_filename, content_type, size_bytes,"
                " incoming_key, uploaded_by) VALUES (:name, :type, :size, :key, :who)"
                " RETURNING id"
            ),
            {
                "name": "invoice.pdf",
                "type": "application/pdf",
                "size": 10,
                "key": "incoming/x/invoice.pdf",
                "who": "Tushar",
            },
        )
        upload_id: UUID = result.scalar_one()
        await session.commit()
        return str(upload_id)


async def test_register_upload_creates_a_document_and_marks_the_upload_registered(
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    upload_id = await make_upload(sessions)
    activities = RegisterActivities(deps_for(sessions))

    result = await activities.register_upload(upload_id)

    async with sessions() as session:
        upload = (
            await session.execute(
                sa.text("SELECT status, document_id FROM uploads WHERE id = :id"),
                {"id": UUID(upload_id)},
            )
        ).one()
        assert upload.status == "registered"
        assert str(upload.document_id) == result.document_id

        document = (
            await session.execute(
                sa.text("SELECT status, original_filename FROM documents WHERE id = :id"),
                {"id": UUID(result.document_id)},
            )
        ).one()
        assert document.status == "processing"
        assert document.original_filename == "invoice.pdf"


async def test_publish_document_sets_the_document_published(
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    upload_id = await make_upload(sessions)
    document_id = (
        await RegisterActivities(deps_for(sessions)).register_upload(upload_id)
    ).document_id

    await PublishActivities(deps_for(sessions)).publish_document(document_id)

    async with sessions() as session:
        row = (
            await session.execute(
                sa.text("SELECT status, current_step FROM documents WHERE id = :id"),
                {"id": UUID(document_id)},
            )
        ).one()
        assert row.status == "published"
        assert row.current_step == "published"


async def test_mark_failed_marks_the_upload_and_document_failed(
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    upload_id = await make_upload(sessions)
    document_id = (
        await RegisterActivities(deps_for(sessions)).register_upload(upload_id)
    ).document_id
    # Undo the "registered" status this activity would otherwise leave alone.
    async with sessions() as session:
        await session.execute(
            sa.text("UPDATE uploads SET status = 'uploaded' WHERE id = :id"),
            {"id": UUID(upload_id)},
        )
        await session.commit()

    await PublishActivities(deps_for(sessions)).mark_failed(
        FailInput(upload_id=upload_id, document_id=document_id, error="OCR engine unavailable")
    )

    async with sessions() as session:
        upload_status = (
            await session.execute(
                sa.text("SELECT status, error FROM uploads WHERE id = :id"), {"id": UUID(upload_id)}
            )
        ).one()
        assert upload_status.status == "failed"
        assert upload_status.error == "OCR engine unavailable"

        document_status: str = (
            await session.execute(
                sa.text("SELECT status FROM documents WHERE id = :id"), {"id": UUID(document_id)}
            )
        ).scalar_one()
        assert document_status == "failed"
