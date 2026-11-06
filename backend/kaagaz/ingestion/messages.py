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
    # Changed on purpose (#522 PR 3): sent now. A zip's limit (the organisation's folder
    # upload limit) is the largest, so a PDF over its own limit is read to the end and
    # named; past every limit an upload still stops while it is read
    # (over_every_limit_message). The words match the screen's, which checks by the name;
    # an old Word or PowerPoint file (ole2) has the PDF limit too (#522 PR 2).
    "pdf": (
        "PDF, Word and PowerPoint files can be up to {limit} MB. This one is {size} MB."
        " Split it or save it smaller and try again."
    ),
    "image": (
        "Photos and scans can be up to {limit} MB. This one is {size} MB."
        " Save it at a lower resolution and try again."
    ),
    "sheet": (
        "Spreadsheets, CSV and text files can be up to {limit} MB. This one is {size} MB."
        " Split it into smaller files and try again."
    ),
    # #522 PR 3: the organisation's folder upload limit (or the PDF or sheet limit, when larger).
    # #522 PR 3: at most ZIP_MAX_BYTES (500 MB), named in GB when it is whole GB.
    "zip": "Zip files can be up to {limit}. Unzip it on your computer and upload the folder instead.",
}


def too_large_message(kind: Kind, size: int, limit: int) -> str:
    """A file over its own kind's limit. ``size`` and ``limit`` are in bytes."""
    if kind == "zip":
        return _TOO_LARGE[kind].format(limit=_megabytes_or_gigabytes(limit))
    return _TOO_LARGE[kind].format(limit=limit // MB, size=-(-size // MB))


def _megabytes_or_gigabytes(limit: int) -> str:
    """ "1 GB" for a whole number of GB, else "N MB", rounded down."""
    gigabytes, rest = divmod(limit, 1024 * MB)
    return f"{gigabytes} GB" if gigabytes and not rest else f"{limit // MB} MB"


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
