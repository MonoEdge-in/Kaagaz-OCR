"""The backup steps in docs/running-locally.md match docker-compose.yml (PRD 3 N2).

The file store's path and both volume names are read from the compose file, so
a change there that the doc does not follow fails here. Plain text, as
tests/test_compose_ocr.py reads it: no YAML library.
"""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def _service(name: str) -> str:
    """One service's block of docker-compose.yml, up to the next service."""
    text = (ROOT / "docker-compose.yml").read_text(encoding="utf-8")
    block = text[text.index(f"\n  {name}:\n") + 1 :]
    following = re.search(r"^  [a-z]", block[3:], re.MULTILINE)
    return block[: following.start() + 3] if following else block


def _backup_section() -> str:
    text = (ROOT / "docs" / "running-locally.md").read_text(encoding="utf-8")
    return text[text.index("## 6. Backup and restore") :]


def test_the_doc_copies_the_path_and_names_the_volumes_compose_uses() -> None:
    seaweedfs = _service("seaweedfs")
    data_dir = re.search(r"^    command: server -dir=(\S+) ", seaweedfs, re.MULTILINE)
    assert data_dir is not None
    files_volume = re.search(rf"^      - ([a-z-]+):{re.escape(data_dir.group(1))}$", seaweedfs, re.MULTILINE)
    database_volume = re.search(r"^      - ([a-z-]+):/var/lib/postgresql/data$", _service("postgres"), re.M)
    assert files_volume is not None and database_volume is not None
    assert (data_dir.group(1), files_volume.group(1), database_volume.group(1)) == (
        "/data",
        "seaweedfs-data",
        "postgres-data",
    )

    section = _backup_section()
    assert f"docker compose cp seaweedfs:{data_dir.group(1)} " in section
    assert f"seaweedfs:{data_dir.group(1)}\n" in section  # the restore copies back to the same path
    assert f"`{files_volume.group(1)}`" in section and f"`{database_volume.group(1)}`" in section
    assert "docker compose stop api worker" in section


def test_git_ignores_the_dump_and_the_copied_files() -> None:
    ignored = (ROOT / ".gitignore").read_text(encoding="utf-8").splitlines()
    assert {"*.dump", "backup-files/"} <= set(ignored)
