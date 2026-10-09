"""What the API needs from file storage, the queue and the answer service.

Why Protocols: the API is tested with in-memory fakes, and the real adapters
(``kaagaz.adapters``) and ``kaagaz.answering.service.AnswerService`` are only
imported by ``create_app``'s default wiring. These are the exact methods the
API calls, nothing more.
"""

from collections.abc import Sequence
from typing import Any, Protocol

from kaagaz.answering.memory import Memory
from kaagaz.answering.rewrite import Turn
from kaagaz.ingestion.ports import BlobStorage, JobQueue
from kaagaz.search.scope import Scope


class ApiStorage(BlobStorage, Protocol):
    """Original files plus page images. ``get`` and ``get_page_image`` raise
    BlobNotFound; ``ping`` raises BackendUnavailable."""

    def get_page_image(self, customer_id: str, document_id: str, page_no: int) -> bytes: ...

    def ping(self) -> None: ...


class ApiQueue(JobQueue, Protocol):
    def ping(self) -> None: ...

    def depth(self) -> dict[str, int]:
        """The jobs waiting in each lane, ``{"express": n, "main": n}`` (PRD 5 U9);
        BackendUnavailable when the queue does not answer."""
        ...


class AnswerLike(Protocol):
    def to_json(self) -> dict[str, Any]: ...


class Answers(Protocol):
    def answer(
        self,
        customer_id: str,
        question: str,
        *,
        scope: Scope,
        history: Sequence[Turn],
        user_id: str,
        memory: Memory,
    ) -> AnswerLike:
        """``question`` is the stripped question text; ``scope`` is resolved for this customer;
        ``history`` is the conversation's earlier turns, oldest first; ``user_id`` is the
        asker, whose own words (My words) only may help; ``memory`` is the conversation's
        (PRD 5 M3), its documents still live, for the follow-up rewrite only."""
        ...

    def remember(self, turns: Sequence[Turn], memory: Memory, cited: Sequence[str]) -> Memory | None:
        """M3: the conversation's new summary and memory from ``memory`` and the ``turns``
        since, oldest first, with the documents ``cited`` in them; None to write nothing."""
        ...
