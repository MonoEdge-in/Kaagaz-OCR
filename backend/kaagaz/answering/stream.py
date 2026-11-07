"""Streaming Ask (#535): what the answer sends while it is worked out, and its streamed draft.

**The rule above all: no fake or unchecked words.** Nothing here sends the model's raw text. The
answer sends real stages, the real sources, and only sentences that already passed the checks
the full answer runs on them (``AnswerService``'s ``_Preview``: ``verify.py``, then decision 2's
final check); the full answer, worked out and saved as ``POST /api/chat`` does, is the truth
(``done``), and the streamed sentences only a preview of it.

``Events``: one streamed answer's outlet, set in ``EVENTS`` for the thread working it out
(routers/chat.py). ``emit`` sends an event when there is one, and does nothing otherwise, so
``POST /api/chat``, Home's ready answers and every test without it answer exactly as before.
``Events.cancel`` (ANSWER_STREAM_MAX_SECONDS passed) makes the next ``emit`` or piece of the
model's text raise ``StreamCancelled``: the model's stream is closed and the answer is never
saved. ``Events.detach`` (the client went away) only stops the events: the answer is finished
and saved, as ``POST /api/chat``'s is (Tushar, 16 Oct 2026).

``Events.saving`` holds the save: the cap waits while the answer is written, and once it is
committed the answer is finished (``done`` goes out, its background tasks run); a cancelled
answer is never written.

``draft_streamed``: ``draft.model_draft`` itself with its first call streamed
(``OpenRouterClient.chat_json_stream``); ``SentenceParser`` reads the draft's ``sentences``
array as it comes, and each complete sentence object goes to ``offer`` (none after one too long,
so the preview keeps the answer's order); then every sentence of the draft is offered (``offer``
sends each once). A reply that is not the draft's JSON is drafted once more without streaming,
after ``stage: checking``.

Time: O(n) over the reply's text; nothing here touches the database.
"""

import json
import logging
import threading
from collections.abc import Callable, Iterator, Sequence
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import replace
from functools import partial
from typing import Any, Protocol, runtime_checkable

from kaagaz.ai.client import ModelClient, ModelOutputError
from kaagaz.answering.draft import MAX_SENTENCE_CHARS, MAX_SENTENCES, Draft, model_draft
from kaagaz.answering.evidence import Evidence

_log = logging.getLogger(__name__)


class StreamCancelled(Exception):
    """The streamed answer was stopped: past ANSWER_STREAM_MAX_SECONDS (its timing line's
    outcome, ``answer_timeout``)."""

    code = "answer_timeout"


class Events:
    """One streamed answer's outlet: ``post(name, data)`` hands an event to the response. A
    stage is sent once, when it changes. Thread-safe: ``cancel`` and ``detach`` come from the
    response's side, everything else from the answer's thread."""

    def __init__(self, post: Callable[[str, dict[str, Any]], None]) -> None:
        self._post = post
        self._cancelled = threading.Event()
        self._detached = threading.Event()
        self._stage: str | None = None
        self._lock = threading.Lock()  # held from the save's check to its commit (``saving``)
        self._saved = False
        self.landing: dict[str, str] = {}  # the ``started`` event's ids, for the logs

    def cancel(self) -> bool:
        """Stop the answer: nothing more is worked out, sent or saved. False when it is too late:
        the answer is saved (it waits while a save is under way), so it is finished."""
        with self._lock:
            if self._saved:
                return False
            self._cancelled.set()
            return True

    @contextmanager
    def saving(self) -> Iterator[None]:
        """Around the answer's save transaction: ``StreamCancelled`` at its start once cancelled,
        and no cancel until it ends; ended without an error, the answer is saved for good."""
        with self._lock:
            self.check()
            yield
            self._saved = True

    def detach(self) -> None:
        """Stop the events only: the client has gone; the answer is finished and saved."""
        self._detached.set()

    def check(self) -> None:
        """``StreamCancelled`` once cancelled."""
        if self._cancelled.is_set():
            raise StreamCancelled

    def send(self, name: str, data: dict[str, Any]) -> None:
        self.check()
        if name == "started":
            self.landing = {key: str(value) for key, value in data.items()}
        if self._detached.is_set():
            return
        if name == "stage":
            if data.get("stage") == self._stage:
                return
            self._stage = data.get("stage")
        self._post(name, data)


EVENTS: ContextVar[Events | None] = ContextVar("answer_events", default=None)


def emit(name: str, data: dict[str, Any]) -> None:
    """Send an event when the answer is streamed; nothing otherwise."""
    events = EVENTS.get()
    if events is not None:
        events.send(name, data)


@contextmanager
def saving() -> Iterator[None]:
    """``Events.saving`` when the answer is streamed; nothing otherwise."""
    events = EVENTS.get()
    if events is None:
        yield
        return
    with events.saving():
        yield


@runtime_checkable
class StreamingModel(ModelClient, Protocol):
    """A model client that can also stream a JSON reply (``OpenRouterClient``)."""

    def chat_json_stream(
        self,
        *,
        system: str,
        user: str,
        schema: dict[str, Any],
        on_text: Callable[[str], None],
        max_tokens: int | None = None,
        model: str | None = None,
        thinking: str | None = None,
        provider: str | None = None,
        deadline: float | None = None,
    ) -> dict[str, Any]: ...


class SentenceParser:
    """The draft's top-level ``sentences`` array read as the reply streams in: each sentence
    object, once its closing brace has come, goes to ``found`` with its place in the array, as
    ``json.loads`` reads it. Strings are skipped with their escapes, so a brace, a bracket or the
    word "sentences" inside a sentence's text is text; anything before the first brace (a code
    fence) is ignored. O(n) over the reply."""

    def __init__(self, found: Callable[[int, object], None]) -> None:
        self._found = found
        self._text = ""
        self._at = 0
        self._depth = 0
        self._in_string = False
        self._escaped = False
        self._string_start = 0
        self._last_key = ""  # the last string closed at the top level (a key, or a value)
        self._in_sentences = False
        self._start = -1  # where the open sentence object starts
        self._count = 0

    def feed(self, text: str) -> None:
        self._text += text
        for i in range(self._at, len(self._text)):
            ch = self._text[i]
            if self._in_string:
                if self._escaped:
                    self._escaped = False
                elif ch == "\\":
                    self._escaped = True
                elif ch == '"':
                    self._in_string = False
                    if self._depth == 1:
                        self._last_key = self._text[self._string_start : i + 1]
                continue
            if ch == '"':
                self._in_string, self._string_start = True, i
            elif ch in "{[":
                self._depth += 1
                if ch == "[" and self._depth == 2:
                    self._in_sentences = self._last_key == '"sentences"'
                elif ch == "{" and self._depth == 3 and self._in_sentences:
                    self._start = i
            elif ch in "}]":
                if ch == "}" and self._depth == 3 and self._start >= 0:
                    self._sentence(self._text[self._start : i + 1])
                    self._start = -1
                elif ch == "]" and self._depth == 2:
                    self._in_sentences = False
                self._depth -= 1
        self._at = len(self._text)

    def _sentence(self, raw: str) -> None:
        try:
            value = json.loads(raw)
        except ValueError:
            value = None
        self._found(self._count, value)
        self._count += 1


class _NotJson(Exception):
    """The streamed reply was not the draft's JSON (``draft_streamed``'s fallback)."""


def draft_streamed(
    model: StreamingModel,
    question: str,
    evidence: Sequence[Evidence],
    *,
    max_tokens: int,
    offer: Callable[[object], None],
    cards: Sequence[str] = (),
    chat_model: str | None = None,
    thinking: str | None = None,
    provider: str | None = None,
    deadline: float | None = None,
) -> Draft:
    """``model_draft`` itself, its first call streamed (``chat_json_stream``): the same prompt,
    options, cut of a sentence too long and repair. While the reply comes, each complete sentence
    object of the first MAX_SENTENCES goes to ``offer``, until one is too long (it is cut in code
    once the reply is whole, so a later sentence must not go before its parts); then every
    sentence of the draft is offered in order (``offer`` sends each once). A reply that is not the
    draft's JSON is drafted once more without streaming, after ``stage: checking``, logged
    (``stream_invalid_output``) and counted in ``redrafts``. Raises as ``model_draft`` does, and
    ``StreamCancelled`` once the answer is stopped."""
    events = EVENTS.get()
    held = False

    def found(place: int, raw: object) -> None:
        nonlocal held
        text = raw.get("text") if isinstance(raw, dict) else None
        held = held or (isinstance(text, str) and len(text) > MAX_SENTENCE_CHARS)
        if not held and place < MAX_SENTENCES:
            offer(raw)

    parser = SentenceParser(found)

    def heard(text: str) -> None:
        if events is not None:
            events.check()  # stopped: the client's connection to the model is closed at once
        parser.feed(text)

    def first(**options: Any) -> dict[str, Any]:
        try:
            return model.chat_json_stream(on_text=heard, **options)
        except ModelOutputError as error:
            if error.code == "invalid_output":
                raise _NotJson from None
            raise

    drafting = partial(
        model_draft,
        model,
        question,
        evidence,
        max_tokens=max_tokens,
        cards=cards,
        chat_model=chat_model,
        thinking=thinking,
        provider=provider,
        deadline=deadline,
    )
    try:
        draft = drafting(first=first)
    except _NotJson:
        _log.warning(
            "streamed draft not JSON, drafted again unstreamed", extra={"code": "stream_invalid_output"}
        )
        emit("stage", {"stage": "checking"})
        again = drafting()
        draft = replace(again, redrafts=again.redrafts + 1)
    for sentence in draft.sentences:
        offer({"text": sentence.text, "evidence": sentence.evidence})
    return draft
