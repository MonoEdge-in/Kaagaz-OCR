"""Upload limits.

There are deliberately no default values. The maximum size and the allowed
file types are Vrushit's decision (PRD section 6, "Upload limits"), so every
caller, including every test, must pass them in.

Each file type has its own size limit, checked after its type is sniffed
(PRD 1, U2): a PDF, a photo or scan, or a spreadsheet (its "kind"). The
customer's own limit (``max_bytes``) can only lower a type's limit, never
raise it.
"""

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Literal

from kaagaz.ingestion import sniff

Kind = Literal["pdf", "image", "sheet"]

# The kind of each type that has a size limit. Old .xls and .doc files (ole2)
# have none in PRD U2, so they can never be allowed.
KIND_OF: Mapping[str, Kind] = {
    sniff.PDF: "pdf",
    sniff.PNG: "image",
    sniff.JPEG: "image",
    sniff.TIFF: "image",
    sniff.TEXT: "sheet",
    sniff.ZIP: "sheet",
}


def limits_by_type(*, pdf: int, image: int, sheet: int) -> dict[str, int]:
    """A limit for every type in KIND_OF, from the limit of its kind."""
    by_kind: dict[Kind, int] = {"pdf": pdf, "image": image, "sheet": sheet}
    return {detected: by_kind[kind] for detected, kind in KIND_OF.items()}


@dataclass(frozen=True, slots=True)
class UploadPolicy:
    max_bytes: int  # the customer's own limit, or the platform's when it has none
    allowed_types: frozenset[str]
    type_limits: Mapping[str, int]  # detected type -> its own limit in bytes

    def __post_init__(self) -> None:
        if self.max_bytes <= 0:
            raise ValueError("max_bytes must be positive")
        if not self.allowed_types:
            raise ValueError("allowed_types must not be empty")
        unknown = self.allowed_types - sniff.KNOWN_TYPES
        if unknown:
            raise ValueError(f"unknown file types: {sorted(unknown)}")
        no_kind = self.type_limits.keys() - KIND_OF.keys()
        if no_kind:
            raise ValueError(f"file types with no size group: {sorted(no_kind)}")
        if any(limit <= 0 for limit in self.type_limits.values()):
            raise ValueError("type limits must be positive")
        missing = self.allowed_types - self.type_limits.keys()
        if missing:
            raise ValueError(f"allowed file types with no size limit: {sorted(missing)}")

    def limit_for(self, detected_type: str) -> int:
        """The largest file of this type: its own limit, lowered to ``max_bytes``. O(1)."""
        limit = self.type_limits.get(detected_type)
        if limit is None:
            raise ValueError(f"no size limit for file type {detected_type!r}")
        return min(limit, self.max_bytes)

    @property
    def largest_limit(self) -> int:
        """The largest file of any allowed type: the most an upload may be. O(types)."""
        return max(self.limit_for(detected) for detected in self.allowed_types)
