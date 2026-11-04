"""Folders nest 100 deep (#484, Tushar, 3 Nov 2026): every recursive walk over folders is
bounded at 101, none at 6, so no level past the 5th is cut off. No database."""

import re
from pathlib import Path

from kaagaz.db.folders import MAX_DEPTH

KAAGAZ = Path(__file__).resolve().parents[1] / "kaagaz"


def test_max_depth_is_100() -> None:
    assert MAX_DEPTH == 100


def test_every_recursive_folder_walk_is_bounded_at_101() -> None:
    found = 0
    for path in KAAGAZ.rglob("*.py"):
        source = path.read_text(encoding="utf-8")
        for match in re.finditer(r"WITH RECURSIVE.*?depth < (\d+)", source, re.DOTALL):
            found += 1
            assert match.group(1) == "101", f"{path.name}: depth < {match.group(1)}"
    assert found >= 9  # folders.py 4 (one holds two walks), listing.py 3, audit.py 1, autofile.py 1
