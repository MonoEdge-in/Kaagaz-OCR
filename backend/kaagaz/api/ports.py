"""What the API needs from file storage, the queue and the answer service.

Why Protocols: the API is tested with in-memory fakes, and the real adapters
(``kaagaz.adapters``) and ``kaagaz.answering.service.AnswerService`` are only
imported by ``create_app``'s default wiring. These are the exact methods the
API calls, nothing more.
"""

from typing import Any, Protocol

from kaagaz.ingestion.ports import BlobStorage, JobQueue


class ApiStorage(BlobStorage, Protocol):
    """Original files plus page images. ``get`` and ``get_page_image`` raise
    BlobNotFound; ``ping`` raises BackendUnavailable."""

    def get_page_image(self, customer_id: str, document_id: str, page_no: int) -> bytes: ...

    def ping(self) -> None: ...


class ApiQueue(JobQueue, Protocol):
    def ping(self) -> None: ...


class AnswerLike(Protocol):
    def to_json(self) -> dict[str, Any]: ...


class Answers(Protocol):
    def answer(self, customer_id: str, question: str) -> AnswerLike:
        """``question`` is already masked."""
        ...
