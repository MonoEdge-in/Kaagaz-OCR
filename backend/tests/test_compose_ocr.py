"""The ocr service's networks in the compose files (security A4).

The files are read as plain text, as tests/test_clamav_settings.py does, so the
test needs no YAML library.
"""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def networks_of_ocr(name: str, service_name: str = "ocr") -> list[str]:
    """A service's ``networks: [..]`` list in one compose file."""
    text = (ROOT / name).read_text(encoding="utf-8")
    service = text[text.index(f"\n  {service_name}:\n") :]
    match = re.search(r"^    networks: \[([^\]]*)\]", service, re.MULTILINE)
    assert match is not None
    return [network.strip() for network in match.group(1).split(",")]


def test_ocr_sits_only_on_its_internal_network() -> None:
    text = (ROOT / "docker-compose.yml").read_text(encoding="utf-8")
    assert networks_of_ocr("docker-compose.yml") == ["ocr_net"]
    assert re.search(r"^  ocr_net:\n    internal: true$", text, re.MULTILINE)


def test_the_host_port_override_leaves_ocr_with_no_way_out() -> None:
    """ocr itself is not in the override, so it stays on ocr_net only; the relay
    publishes the port, on 127.0.0.1 only, from its own network."""
    text = (ROOT / "docker-compose.ocr-host.yml").read_text(encoding="utf-8")
    assert "\n  ocr:\n" not in text
    assert networks_of_ocr("docker-compose.ocr-host.yml", "ocr-relay") == ["ocr_net", "ocr_host"]
    assert re.findall(r'^      - "(.*)"$', text, re.MULTILINE) == ["127.0.0.1:8080:8080"]
    assert re.search(r"^  ocr_host: \{\}", text, re.MULTILINE)  # its own network, and not internal
