"""The local ClamAV must run strict (deny by default).

Without these, clamd answers OK for a file it could not fully look inside
(too big, too deep, encrypted), and macros are not reported. The compose file
is read as plain text so the test needs no YAML library.
"""

import re
from pathlib import Path

from kaagaz.ingestion import unzip
from kaagaz.ingestion.policy import ZIP_MAX_BYTES

COMPOSE = Path(__file__).resolve().parents[2] / "docker-compose.yml"
ENV_EXAMPLE = COMPOSE.parent / ".env.example"
MIB = 1024 * 1024

# Changed on purpose (#522, security review 6 Nov 2026, Vrushit approves): an encrypted zip
# is no longer an alert, so a zip with one password-protected file is not refused whole; that
# file is refused on its own when the zip is unpacked (unzip.ENCRYPTED). AlertEncrypted would
# turn the archive alert back on, so it is off too; an encrypted document is still reported.
REQUIRED = {
    "AlertExceedsMax": "yes",
    "AlertEncrypted": "no",
    "AlertEncryptedArchive": "no",
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


def _mib(value: str) -> int:
    assert value.endswith("M"), value
    return int(value[:-1]) * MIB


def test_clamd_accepts_a_whole_upload() -> None:
    # A stream cut short by clamd's own limit would never be fully scanned.
    size = _clamd_settings()["StreamMaxLength"]
    limit = re.findall(r"^MAX_UPLOAD_MB=(\d+)$", ENV_EXAMPLE.read_text(encoding="utf-8"), re.MULTILINE)
    assert _mib(size) > int(limit[0]) * MIB


def test_clamd_can_scan_the_largest_zip_the_app_takes_and_every_file_in_it() -> None:
    """#522: clamd's limits are at least the app's zip limits, so they can't drift apart: a zip
    clamd stops reading, or a file in it clamd skips, would be unscannable (or worse, unread)."""
    settings = _clamd_settings()
    assert _mib(settings["StreamMaxLength"]) >= ZIP_MAX_BYTES  # the zip itself, sent whole
    assert _mib(settings["MaxFileSize"]) >= ZIP_MAX_BYTES  # the zip, and any one file in it
    assert _mib(settings["MaxScanSize"]) >= ZIP_MAX_BYTES  # all it unpacks to, at least the zip
    assert int(settings["MaxFiles"]) >= unzip.MAX_ENTRIES  # every entry a zip may hold
