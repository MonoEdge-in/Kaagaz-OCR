"""The job queue keeps its messages across a restart (EC2 PRD WP-C).

elasticmq.conf turns message storage on, and docker-compose.yml mounts a named
volume where the storage file lives. Plain text, as tests/test_compose_prod.py
reads the files: no YAML library.
"""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CONF = ROOT / "infra" / "elasticmq" / "elasticmq.conf"
# Checked with Docker: this image (the native one) restores the stored messages
# after a restart, and has the wget its healthcheck uses.
IMAGE = "softwaremill/elasticmq-native:1.7.1"


def _service(name: str) -> str:
    """One service's block of docker-compose.yml, up to the next service."""
    text = (ROOT / "docker-compose.yml").read_text(encoding="utf-8")
    block = text[text.index(f"\n  {name}:\n") + 1 :]
    following = re.search(r"^  [a-z]", block[3:], re.MULTILINE)
    return block[: following.start() + 3] if following else block


def _storage_block() -> str:
    match = re.search(r"^messages-storage \{\n(.*?)^\}", CONF.read_text(encoding="utf-8"), re.M | re.S)
    assert match is not None
    return match.group(1)


def test_message_storage_is_on_in_a_file_under_data() -> None:
    storage = _storage_block()
    assert re.search(r"^  enabled = true$", storage, re.MULTILINE)
    assert re.search(r'^  uri = "jdbc:h2:/data/elasticmq\.db"$', storage, re.MULTILINE)


def test_compose_mounts_the_named_volume_where_the_messages_are_stored() -> None:
    elasticmq = _service("elasticmq")
    assert re.search(rf"^    image: {re.escape(IMAGE)}$", elasticmq, re.MULTILINE)
    section = re.search(r"^    volumes:\n((?:      - .*\n)+)", elasticmq, re.MULTILINE)
    assert section is not None
    mounts = re.findall(r"^      - (\S+)", section.group(1), re.MULTILINE)
    assert mounts == ["./infra/elasticmq/elasticmq.conf:/opt/elasticmq.conf:ro", "elasticmq-data:/data"]

    text = (ROOT / "docker-compose.yml").read_text(encoding="utf-8")
    volumes = text[text.index("\nvolumes:\n") :]
    assert re.search(r"^  elasticmq-data:$", volumes, re.MULTILINE)


def test_the_queues_and_the_healthcheck_are_unchanged() -> None:
    conf = CONF.read_text(encoding="utf-8")
    assert re.findall(r"^  ([a-z-]+) \{", conf, re.MULTILINE) == [
        "kaagaz-jobs",
        "kaagaz-jobs-failed",
        "kaagaz-jobs-express",  # PRD 5 Q4: the express lane, with its own failure bin
        "kaagaz-jobs-express-failed",
    ]
    for lane in ("kaagaz-jobs", "kaagaz-jobs-express"):
        block = re.search(rf"^  {lane} \{{\n(.*?)^  \}}", conf, re.MULTILINE | re.DOTALL)
        assert block is not None, lane
        assert "receiveMessageWait = 0 seconds\n" in block.group(1), lane
        assert f'name = "{lane}-failed"\n      maxReceiveCount = 10\n' in block.group(1), lane
    healthcheck = '["CMD", "wget", "-q", "-O", "/dev/null", "http://127.0.0.1:9324/health"]'
    assert f"\n      test: {healthcheck}\n" in _service("elasticmq")
