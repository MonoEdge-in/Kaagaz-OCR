"""Greetings, thanks and "what can you do" (Tushar, 24 Oct 2026: "hi, hello you know how to
answer; what can Kaagaz do, give a list; only these few general questions are answered in
general"). A short message that is only small talk is answered here at once, with no search
and no model, so it can never show an error or hide an outage (KZ-006 applies to real
questions only). Anything with a number, a word outside these phrases, or more than
``MAX_WORDS`` words goes to Ask as before ("help me find invoice 42", "hello patil").

Answers are in English (CLAUDE.md), the greeting word mirrored ("Namaste, Tushar."). The
capability list says only what the product does today, each checked in the code.
"""

import re
from typing import Any, Literal

Kind = Literal["greet", "thanks", "who", "help"]

MAX_WORDS = 7
_WORD = re.compile(r"[\wऀ-ॿ]+")
_FILLER = frozenset(
    {"kaagaz", "kagaz", "kagaaz", "ji", "please", "pls", "there", "sir", "madam", "bhai", "dear", "team"}
)
_GREET = frozenset(
    {
        "hi",
        "hii",
        "hiii",
        "hello",
        "helo",
        "hey",
        "heya",
        "namaste",
        "namaskar",
        "नमस्ते",
        "नमस्कार",
        "good morning",
        "good afternoon",
        "good evening",
        "gm",
    }
)
_THANKS = frozenset(
    {
        "thanks",
        "thank you",
        "thankyou",
        "thx",
        "ty",
        "dhanyavad",
        "dhanyawad",
        "shukriya",
        "धन्यवाद",
        "ok thanks",
        "great thanks",
        "thanks a lot",
    }
)
_WHO = frozenset(
    {
        "who are you",
        "what are you",
        "what is kaagaz",
        "what is kagaz",
        "kaagaz kya hai",
        "kagaz kya hai",
        "tum kaun ho",
        "aap kaun ho",
        "who r u",
    }
)
_HELP = frozenset(
    {
        "help",
        "what can you do",
        "what all can you do",
        "what can kaagaz do",
        "what can kagaz do",
        "what does kaagaz do",
        "how does this work",
        "how do i use this",
        "how to use",
        "how to use kaagaz",
        "kya kar sakte ho",
        "aap kya kar sakte ho",
        "tum kya kar sakte ho",
        "what are your features",
        "features",
    }
)

# What Kaagaz does today (each in the code: answering, hinglish.py, the readers, intent.py,
# Search, Read clearly, word review, versions, My words).
CAN_DO = (
    "Answer questions about your documents, showing the page and spot each answer comes from.",
    "Understand questions in English and Hinglish, and read documents in English, Hindi and Marathi.",
    "Find totals, counts and the highest or lowest amounts across your invoices and registers.",
    "Read scans and photos, and mark any word it was not sure about for you to check.",
    "Find documents by a name, number or word in Search.",
    "Spot a new version of a document and show what changed.",
    "Show a whole document as clear text with Read clearly.",
    "Learn your own words for things in My words.",
)


def kind(question: str) -> Kind | None:
    """What small talk ``question`` is, or None for anything else (the module docstring)."""
    if any(ch.isdigit() for ch in question):
        return None
    every = _WORD.findall(question.casefold())
    words = [word for word in every if word not in _FILLER]
    if not words or len(every) > MAX_WORDS:
        return None
    for phrase in (" ".join(every), " ".join(words)):  # "what can kaagaz do", or "hi kaagaz" as "hi"
        for table, found in ((_HELP, "help"), (_WHO, "who"), (_THANKS, "thanks"), (_GREET, "greet")):
            if phrase in table:
                return found  # type: ignore[return-value]
    # One greeting first, then help, who or thanks: "hi, what can you do".
    for size in (2, 1):
        head, rest = " ".join(words[:size]), " ".join(words[size:])
        if head in _GREET and rest:
            if rest in _HELP:
                return "help"
            if rest in _WHO:
                return "who"
            if rest in _THANKS:
                return "thanks"
    return None


def _greeting_word(question: str) -> str:
    """The asker's own greeting word, for the reply's first word: Namaste, Namaskar or Hello."""
    words = _WORD.findall(question.casefold())
    for word in words:
        if word in ("namaste", "नमस्ते"):
            return "Namaste"
        if word in ("namaskar", "नमस्कार"):
            return "Namaskar"
    return "Hello"


def sentences(talk: Kind, question: str, first_name: str | None) -> list[str]:
    """The reply, sentence by sentence; for help, a line then the CAN_DO list."""
    name = f", {first_name}" if first_name else ""
    if talk == "greet":
        return [
            f"{_greeting_word(question)}{name}. Ask me anything about your documents, and I will show you "
            "the page each answer comes from."
        ]
    if talk == "thanks":
        return ["You are welcome. Ask me anything else about your documents."]
    if talk == "who":
        return [
            "I am Kaagaz. I read your documents in English, Hindi and Marathi, and answer questions about "
            "them with the page each answer comes from."
        ]
    return ["Here is what I can do for you:", *CAN_DO]


def answer(talk: Kind, question: str, first_name: str | None, suggestions: list[str]) -> dict[str, Any]:
    """The stored answer (frontend/lib/types.ts Answer, v2): answered, no sources, no model,
    route ``small_talk``, with up to three questions to ask with one press."""
    return {
        "version": 2,
        "status": "answered",
        "label": None,
        "opening": None,
        "documents": [],
        "assumptions": [],
        "sentences": [{"text": text, "citations": []} for text in sentences(talk, question, first_name)],
        "citations": [],
        "closest_documents": [],
        "route": "small_talk",
        "normalized_question": question,
        "model_used": False,
        "rewritten_question": None,
        "understood_words": [],
        "reading_note": None,
        "unreadable_pages": [],
        "unreadable_page_count": 0,
        "related_documents": [],
        "suggestions": suggestions[:3],
    }
