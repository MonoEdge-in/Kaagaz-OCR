"""Who each model call is billed to (model choice, council 11 Oct 2026, SP1).

``usage_scope`` opens a scope for one piece of work (a document's reading, a chat answer, a
search): the customer, the user who asked (None for work no user asked for), the kind of
work and the document or conversation. The model client calls ``record_call`` for every
reply it is billed for, and the calls add up in the scope that is current: a ContextVar, so
a thread started with ``contextvars.copy_context().run`` adds to the same scope (its tallies
are kept under a lock). A call with no scope open is not recorded (verify-models, tests).

When the scope ends, with or without an error (the calls were billed either way), its
tallies are saved in one INSERT in ``db.customer()``: one row per (model, provider, cached).
A failure to save logs ``usage_not_saved`` and never fails the work. A scope opened inside
another is its own scope; the outer one is current again when it ends.

Time: O(1) a call; one statement a scope.
"""

import contextvars
import logging
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field

from sqlalchemy.exc import SQLAlchemyError

from kaagaz.db.engine import Database, unavailable
from kaagaz.db.usage import UsageRow, insert_usage
from kaagaz.ingestion.errors import BackendUnavailable

# The kinds of work a ledger row is billed as (0023's CHECK allows ^[a-z_]{1,32}$).
KINDS = ("scan", "upgrade", "card", "invoice", "map", "embed", "chat", "memory", "search", "story")

_log = logging.getLogger(__name__)


@dataclass(slots=True)
class _Tally:
    calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    reasoning_tokens: int = 0
    cost_usd: float | None = 0.0  # None once a call came without a cost


@dataclass(slots=True)
class UsageScope:
    """One piece of work's model calls, tallied by (model, cached), with the last
    provider OpenRouter named for each."""

    customer_id: str
    user_id: str | None
    kind: str
    document_id: str | None = None
    conversation_id: str | None = None
    _tallies: dict[tuple[str, bool], _Tally] = field(default_factory=dict, repr=False)
    _providers: dict[tuple[str, bool], str | None] = field(default_factory=dict, repr=False)
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def add(
        self,
        model: str,
        provider: str | None,
        input_tokens: int,
        output_tokens: int,
        reasoning_tokens: int,
        cost_usd: float | None,
        *,
        cached: bool = False,
    ) -> None:
        """Count one call (thread-safe)."""
        key = (model, cached)
        with self._lock:
            tally = self._tallies.setdefault(key, _Tally())
            tally.calls += 1
            tally.input_tokens += input_tokens
            tally.output_tokens += output_tokens
            tally.reasoning_tokens += reasoning_tokens
            tally.cost_usd = None if cost_usd is None or tally.cost_usd is None else tally.cost_usd + cost_usd
            if provider is not None or key not in self._providers:
                self._providers[key] = provider

    def rows(self) -> list[UsageRow]:
        """The ledger rows so far, one per (model, cached)."""
        with self._lock:
            return [
                UsageRow(
                    customer_id=self.customer_id,
                    user_id=self.user_id,
                    document_id=self.document_id,
                    conversation_id=self.conversation_id,
                    kind=self.kind,
                    model=model,
                    provider_name=self._providers[(model, cached)],
                    cached=cached,
                    calls=tally.calls,
                    input_tokens=tally.input_tokens,
                    output_tokens=tally.output_tokens,
                    reasoning_tokens=tally.reasoning_tokens,
                    cost_usd=tally.cost_usd,
                )
                for (model, cached), tally in self._tallies.items()
            ]


_CURRENT: contextvars.ContextVar[UsageScope | None] = contextvars.ContextVar("usage_scope", default=None)


@contextmanager
def usage_scope(
    db: Database,
    *,
    customer_id: str,
    user_id: str | None,
    kind: str,
    document_id: str | None = None,
    conversation_id: str | None = None,
) -> Iterator[UsageScope]:
    """Bill the model calls made inside to this work; saved when it ends. ``kind`` is one
    of KINDS (ValueError otherwise)."""
    if kind not in KINDS:
        raise ValueError("unknown usage kind")
    scope = UsageScope(customer_id, user_id, kind, document_id, conversation_id)
    token = _CURRENT.set(scope)
    try:
        yield scope
    finally:
        _CURRENT.reset(token)
        rows = scope.rows()
        if rows:
            try:
                with unavailable(), db.customer(customer_id) as conn:
                    insert_usage(conn, rows)
            except (SQLAlchemyError, BackendUnavailable):
                _log.warning("usage not saved", extra={"code": "usage_not_saved"})


def record_call(
    model: str,
    provider: str | None,
    input_tokens: int,
    output_tokens: int,
    reasoning_tokens: int,
    cost_usd: float | None,
    *,
    cached: bool = False,
) -> None:
    """Count one billed reply (or, with ``cached``, one page taken from the page cache, at
    cost 0) in the current scope; nothing when no scope is open."""
    scope = _CURRENT.get()
    if scope is not None:
        scope.add(model, provider, input_tokens, output_tokens, reasoning_tokens, cost_usd, cached=cached)
