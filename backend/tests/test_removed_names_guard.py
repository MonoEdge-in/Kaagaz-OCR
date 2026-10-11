"""PRD 5 section 7: no tracked file still names the removed readers, the removed
model servers or their settings. Migration history is the only exception."""

import re
import shutil
import subprocess
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
REMOVED = re.compile(
    r"(?i:paddle|ollama|qwen|\bgpu\b)|OCR_URL|MODEL_BASE_URL|MODEL_VISION|MODEL_NUM_CTX"
    r"|MODEL_WINDOW_MAX_TOKENS|kaagaz\.ai\.gemini"
)
ALLOWED_PREFIXES = (
    "backend/migrations/versions/",
    "backend/tests/test_migration_0006_db.py",
    "backend/tests/test_migration_0013_db.py",
    "backend/tests/test_removed_names_guard.py",
    "frontend/package-lock.json",
)


def hits() -> list[str]:
    git = shutil.which("git")
    assert git is not None, "git is not on PATH"
    listed = subprocess.run([git, "ls-files", "-z"], cwd=REPO, capture_output=True, check=True)
    found: list[str] = []
    for path in sorted(filter(None, listed.stdout.decode("utf-8").split("\0"))):
        if path.startswith(ALLOWED_PREFIXES) or not (REPO / path).is_file():
            continue
        data = (REPO / path).read_bytes()
        if b"\0" in data:
            continue  # a binary file
        for line_no, line in enumerate(data.decode("utf-8", errors="replace").splitlines(), 1):
            if REMOVED.search(line):
                found.append(f"{path}:{line_no}")
    return found


def test_no_tracked_file_names_a_removed_reader_or_setting() -> None:
    found = hits()
    assert not found, "Removed names are still in:\n" + "\n".join(found)


def test_the_pattern_finds_each_removed_name_and_spares_the_kept_ones() -> None:
    removed = ["PaddleOCR-VL", "ollama", "Qwen3", "the GPU server", "OCR_URL", "MODEL_BASE_URL"]
    removed += ["MODEL_VISION", "MODEL_NUM_CTX", "MODEL_WINDOW_MAX_TOKENS", "from kaagaz.ai.gemini import"]
    kept = ["kaagaz.ai.openrouter", "processing_runs.model_vision", "gemini_ocr", "gpus=all"]
    assert [name for name in removed if not REMOVED.search(name)] == []
    assert [name for name in kept if REMOVED.search(name)] == []
