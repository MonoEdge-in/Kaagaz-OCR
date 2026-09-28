"""The local ClamAV must run strict (deny by default).

Without these, clamd answers OK for a file it could not fully look inside
(too big, too deep, encrypted), and macros are not reported. The compose file
is read as plain text so the test needs no YAML library.
"""

import re
from pathlib import Path

COMPOSE = Path(__file__).resolve().parents[2] / "docker-compose.yml"

REQUIRED = {
    "AlertExceedsMax": "yes",
    "AlertEncrypted": "yes",
    "AlertEncryptedArchive": "yes",
    "AlertEncryptedDoc": "yes",
    "AlertOLE2Macros": "yes",
    "AlertBrokenExecutables": "yes",
    "HeuristicAlerts": "yes",
}


def _clamd_settings() -> dict[str, str]:
    text = COMPOSE.read_text(encoding="utf-8")
    return dict(re.findall(r'^\s+CLAMD_CONF_(\w+):\s*"?([^"\s#]+)"?', text, re.MULTILINE))


def test_clamd_reports_what_it_cannot_scan_and_macros() -> None:
    settings = _clamd_settings()
    missing = {key: value for key, value in REQUIRED.items() if settings.get(key) != value}
    assert missing == {}


def test_clamd_accepts_a_whole_upload() -> None:
    # A stream cut short by clamd's own limit would never be fully scanned.
    size = _clamd_settings()["StreamMaxLength"]
    assert size.endswith("M")
    assert int(size[:-1]) > 50  # MAX_UPLOAD_MB in .env.example
