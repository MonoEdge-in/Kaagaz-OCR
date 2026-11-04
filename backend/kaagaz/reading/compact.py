"""shortcut: builder B's stand-in (#488) for builder A's compact reply (``{"r": string}``), with
A's exact names and signatures, so ``cli measure-compact`` imports them; the merge takes A's
real module."""

from typing import Any


def encode_compact(result: dict[str, Any]) -> str:
    """A cached reply (``page_reads.result``) as the compact reply's ``r`` (builder A's)."""
    raise NotImplementedError("builder A's encoder")


def decode_compact(text: str) -> dict[str, Any]:
    """The compact reply's ``r`` as a reply of ``result``'s shape (builder A's)."""
    raise NotImplementedError("builder A's decoder")
