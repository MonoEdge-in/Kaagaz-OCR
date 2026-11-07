"""Streaming Ask's pieces without a database (#535): the draft's sentences read as the reply
streams in, the streamed draft and its one unstreamed fallback, and the outlet's rules."""

import json
from collections.abc import Callable
from typing import Any

import pytest

from kaagaz.ai.client import ModelOutputError
from kaagaz.answering import draft
from kaagaz.answering.stream import EVENTS, Events, SentenceParser, StreamCancelled, draft_streamed, emit

REPLY = {
    "status": "answered",
    "sentences": [
        {"text": 'A "quoted" {brace} [bracket] and the word sentences: \\ too.', "evidence": [1]},
        {"text": "The total is Rs. 1,20,000.", "evidence": [1, 2]},
    ],
}


def _parsed(chunks: list[str]) -> list[tuple[int, object]]:
    found: list[tuple[int, object]] = []
    parser = SentenceParser(lambda place, value: found.append((place, value)))
    for chunk in chunks:
        parser.feed(chunk)
    return found


@pytest.mark.parametrize("size", [1, 2, 3, 7, 1000])
def test_each_sentence_comes_whole_and_in_order_however_the_reply_is_cut(size: int) -> None:
    text = json.dumps(REPLY)
    found = _parsed([text[i : i + size] for i in range(0, len(text), size)])
    assert found == list(enumerate(REPLY["sentences"]))


def test_a_sentence_comes_once_its_closing_brace_has_come_and_not_before() -> None:
    found: list[tuple[int, object]] = []
    parser = SentenceParser(lambda place, value: found.append((place, value)))
    text = json.dumps(REPLY)
    end = text.index("[1]}") + 4  # the first sentence's closing brace (not the one in its text)
    parser.feed(text[: end - 1])
    assert found == []
    parser.feed(text[end - 1 : end])
    assert found == [(0, REPLY["sentences"][0])]


def test_only_the_top_level_sentences_count_and_a_fence_or_not_found_gives_none() -> None:
    other = {
        "status": "answered",
        "notes": [{"text": "not a sentence"}],
        "sentences": [{"text": "x", "evidence": []}],
    }
    assert _parsed(["```json\n", json.dumps(other), "\n```"]) == [(0, {"text": "x", "evidence": []})]
    assert _parsed([json.dumps({"status": "not_found", "sentences": []})]) == []
    assert _parsed(['{"status": "sentences", "x": [{"a": 1}]}']) == []


class StreamModel:
    """A streaming model: ``chat_json_stream`` hands out ``text`` in pieces, then parses it as the
    client would; ``chat_json`` (the fallback) gives ``fallback``."""

    chat_model = "google/gemini-3.1-pro-preview"
    embed_model = "google/gemini-embedding-2"

    def __init__(self, text: str, fallback: dict[str, Any] | None = None) -> None:
        self.text, self.fallback = text, fallback
        self.calls: list[str] = []

    def embed(self, texts: list[str]) -> list[list[float]]:
        raise AssertionError("not used")

    def chat_json_stream(self, *, on_text: Callable[[str], None], **kwargs: Any) -> dict[str, Any]:
        self.calls.append("stream")
        for i in range(0, len(self.text), 5):
            on_text(self.text[i : i + 5])
        try:
            value = json.loads(self.text)
        except ValueError:
            raise ModelOutputError("invalid_output") from None
        return dict(value)

    def chat_json(self, **kwargs: Any) -> dict[str, Any]:
        self.calls.append("plain")
        assert kwargs["system"] == draft.SYSTEM_PROMPT and kwargs["schema"] == draft.ANSWER_SCHEMA
        assert self.fallback is not None
        return self.fallback


def _events() -> tuple[Events, list[tuple[str, dict[str, Any]]]]:
    sent: list[tuple[str, dict[str, Any]]] = []
    return Events(lambda name, data: sent.append((name, data))), sent


def _drafted(model: "StreamModel") -> tuple[draft.Draft, list[object], list[tuple[str, dict[str, Any]]]]:
    events, sent = _events()
    offered: list[object] = []
    token = EVENTS.set(events)
    try:
        drafted = draft_streamed(model, "q", [], max_tokens=100, offer=offered.append)
    finally:
        EVENTS.reset(token)
    return drafted, offered, sent


def test_the_streamed_draft_offers_each_sentence_as_it_comes_then_the_drafts_in_order() -> None:
    model = StreamModel(json.dumps(REPLY))
    drafted, offered, sent = _drafted(model)
    assert drafted == draft.checked(REPLY) and model.calls == ["stream"] and sent == []
    final = [{"text": s.text, "evidence": s.evidence} for s in drafted.sentences]
    assert offered == [*REPLY["sentences"], *final]  # the preview sends each text once


def test_bad_json_is_drafted_once_more_unstreamed_after_saying_checking() -> None:
    model = StreamModel('{"status": "answered", "sentences": [{"text": "cut', fallback=REPLY)
    drafted, offered, sent = _drafted(model)
    assert model.calls == ["stream", "plain"]
    assert sent == [("stage", {"stage": "checking"})]
    assert drafted == draft.checked(REPLY)
    assert offered == REPLY["sentences"]  # the final draft's, offered in turn


def test_a_long_sentence_is_cut_in_code_with_no_second_call_and_its_parts_offered() -> None:
    first = "The total is Rs. 1,20,000 for the work. " * 5
    second = (
        "It was paid on 12 August 2026 by cheque from the account of Sharma Traders in Pune,"
        " and the receipt was signed by the manager on that day."
    )
    sentence: dict[str, Any] = {"text": first + second, "evidence": [1]}
    long = {"status": "answered", "sentences": [sentence]}
    assert len(sentence["text"]) > draft.MAX_SENTENCE_CHARS
    model = StreamModel(json.dumps(long))
    drafted, offered, sent = _drafted(model)
    assert model.calls == ["stream"] and sent == [] and drafted.redrafts == 0
    assert len(drafted.sentences) == 2 and all(
        len(s.text) <= draft.MAX_SENTENCE_CHARS for s in drafted.sentences
    )
    # The long one as it streamed (the preview's shape check refuses it), then its parts.
    assert offered == [sentence, *({"text": s.text, "evidence": [1]} for s in drafted.sentences)]


def test_a_long_sentence_that_cannot_be_cut_is_asked_for_once_more_as_unstreamed() -> None:
    long = json.dumps({"status": "answered", "sentences": [{"text": "x" * 301, "evidence": [1]}]})
    model = StreamModel(long, fallback=REPLY)
    drafted, _, sent = _drafted(model)
    assert model.calls == ["stream", "plain"] and sent == []  # the repair, as model_draft asks it
    assert drafted.redrafts == 1 and drafted.sentences == draft.checked(REPLY).sentences


def test_another_model_failure_is_not_drafted_again() -> None:
    class Truncated(StreamModel):
        def chat_json_stream(self, *, on_text: Callable[[str], None], **kwargs: Any) -> dict[str, Any]:
            raise ModelOutputError("output_truncated")

    with pytest.raises(ModelOutputError) as caught:
        draft_streamed(Truncated("", fallback=REPLY), "q", [], max_tokens=100, offer=lambda _: None)
    assert caught.value.code == "output_truncated"


def test_a_cancelled_answer_stops_the_stream_at_its_next_piece() -> None:
    events, _ = _events()
    events.cancel()
    token = EVENTS.set(events)
    try:
        with pytest.raises(StreamCancelled):
            draft_streamed(StreamModel(json.dumps(REPLY)), "q", [], max_tokens=100, offer=lambda _: None)
        with pytest.raises(StreamCancelled):
            emit("stage", {"stage": "writing"})
    finally:
        EVENTS.reset(token)


def test_a_stage_is_sent_once_when_it_changes_and_nothing_is_sent_without_a_stream() -> None:
    events, sent = _events()
    emit("stage", {"stage": "searching"})  # no stream: nothing, no error
    token = EVENTS.set(events)
    try:
        for stage in ("searching", "searching", "writing", "checking", "checking"):
            emit("stage", {"stage": stage})
    finally:
        EVENTS.reset(token)
    assert [data["stage"] for _, data in sent] == ["searching", "writing", "checking"]
