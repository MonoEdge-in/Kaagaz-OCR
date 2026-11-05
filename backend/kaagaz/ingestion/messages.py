"""What a customer is told when an upload is too large (PRD 1, U2), or would take the
organisation past its storage limit (#516, ``storage_full_message``).

Each message names the limit and the fix. 1 MB is 1024 x 1024 bytes, as the
settings count it. The size is rounded up and the limit down, so a file even
one byte over a 20 MB limit reads "21 MB", never "20 MB". The upload screen
says the same words before it sends a file (``tooLargeMessage`` in
``frontend/components/documents/logic.ts``).
"""

from kaagaz.ingestion.policy import Kind

MB = 1024 * 1024

_TOO_LARGE: dict[Kind, str] = {
    # The server never sends this one: the PDF limit is MAX_UPLOAD_MB (or the
    # customer's lower one), the largest of all, so an upload over it stops while
    # it is read, before its type is known (over_every_limit_message). It stays
    # so the words match the screen's, which checks by the file name.
    "pdf": "PDFs can be up to {limit} MB. This one is {size} MB. Split it or save it smaller and try again.",
    "image": (
        "Photos and scans can be up to {limit} MB. This one is {size} MB."
        " Save it at a lower resolution and try again."
    ),
    "sheet": (
        "Spreadsheets and CSV files can be up to {limit} MB. This one is {size} MB."
        " Split it into smaller files and try again."
    ),
}


def too_large_message(kind: Kind, size: int, limit: int) -> str:
    """A file over its own kind's limit. ``size`` and ``limit`` are in bytes."""
    return _TOO_LARGE[kind].format(limit=limit // MB, size=-(-size // MB))


def over_every_limit_message(limit: int) -> str:
    """A file larger than any upload may be, refused before its type is known.
    ``limit`` is the largest limit, in bytes."""
    return (
        f"Files can be up to {limit // MB} MB. This one is larger. Split it or save it smaller and try again."
    )


def storage_full_message(limit_gb: int) -> str:
    """A new file past the organisation's storage limit, ``limit_gb`` whole GB (#516)."""
    return (
        f"This organisation has used its {limit_gb} GB of storage. Delete files forever from the Trash"
        " to free space, or ask Kaagaz to raise the limit."
    )
