"""One WhatsApp reply (#603 W4): what the chat answers and ``Channel`` sends, one message per answer
(each outgoing message is billed on its own)."""

from collections.abc import Callable
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class Row:
    """A list row: its id (sent back when tapped), title (24 characters) and description (72)."""

    id: str
    title: str
    description: str = ""


@dataclass(frozen=True, slots=True)
class Outgoing:
    """A text; with up to 3 reply buttons, each (id, title of 20 characters); or a list of up to 10
    ``rows``, opened by ``button``.

    ``documents``: every document the reply names, checked again just before each try to send it
    (``Chat.recheck``). ``link``: the document whose preview card it is, audited only once the reply
    was sent. ``lang``: its language, for the "couldn't find" that stands in for it. ``later``: work
    to do once it was sent (Ask's memory). W5: ``finished``, the person's other messages it answers
    too (the files sent with this one), done with it; ``notice``, a saved file's "ready" notice,
    which goes not at all (an empty ``body``) once its file is hidden, locked or deleted. W6:
    ``heard``, a voice note's "I heard: ..." line, which ``body`` starts with and the "couldn't find"
    standing in for it keeps."""

    body: str
    buttons: tuple[tuple[str, str], ...] = ()
    rows: tuple[Row, ...] = ()
    button: str = ""
    documents: tuple[str, ...] = ()
    link: str | None = None
    lang: str = "en"
    later: tuple[Callable[[], None], ...] = ()
    finished: tuple[str, ...] = ()
    notice: bool = False
    heard: str = ""
