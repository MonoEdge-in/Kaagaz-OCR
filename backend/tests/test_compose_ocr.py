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


GPU_COMMAND = (
    "docker compose -f docker-compose.yml -f docker-compose.gpu.yml "
    "--profile paddle --profile ollama up -d --build"
)
_NEXT_KEY = re.compile(r"^ {0,2}[a-z]", re.MULTILINE)  # the next service, or a top-level key
GPU_RESERVATION = (
    "    deploy:\n"
    "      resources:\n"
    "        reservations:\n"
    "          devices:\n"
    "            - driver: nvidia\n"
    "              count: 1\n"
    "              capabilities: [gpu]\n"
)


def service(name: str, file: str = "docker-compose.yml") -> str:
    """One service's block in a compose file, up to the next service or top-level key."""
    text = (ROOT / file).read_text(encoding="utf-8")
    block = text[text.index(f"\n  {name}:\n") + 1 :]
    following = _NEXT_KEY.search(block, block.index("\n") + 1)
    return block[: following.start()] if following else block


def keys(block: str) -> set[str]:
    """A service block's own keys (four spaces in)."""
    return set(re.findall(r"^    ([a-z_]+):", block, re.MULTILINE))


def environment(block: str) -> dict[str, str]:
    """A service block's ``environment:`` mapping, values without quotes or comments."""
    section = re.search(r"^    environment:\n((?:      .*\n|\s*#.*\n)*)", block, re.MULTILINE)
    assert section is not None
    pairs = re.findall(r"^      ([A-Za-z_]+): (\S+)", section.group(1), re.MULTILINE)
    return {name: value.strip('"') for name, value in pairs}


def test_the_gpu_file_reserves_the_gpu_for_ocr_and_ollama_only() -> None:
    text = (ROOT / "docker-compose.gpu.yml").read_text(encoding="utf-8")
    assert GPU_COMMAND in text
    assert re.findall(r"^  ([a-z-]+):$", text, re.MULTILINE) == ["ocr", "ollama", "api", "worker"]
    assert re.findall(r"^([a-z]+):", text, re.MULTILINE) == ["services"]  # no networks, volumes
    for name in ("ocr", "ollama"):
        assert GPU_RESERVATION in service(name, "docker-compose.gpu.yml")
    assert text.count("driver: nvidia") == 2
    assert re.findall(r"capabilities: (.*)", text) == ["[gpu]", "[gpu]"]  # the GPU only, never "all"
    for name in ("api", "worker"):
        assert "deploy" not in keys(service(name, "docker-compose.gpu.yml"))


def test_the_gpu_file_changes_only_what_the_gpu_needs_on_ocr() -> None:
    """Every security setting stays docker-compose.yml's: the override never names ports,
    networks, env_file, cap_add, security_opt, read_only or user, so they are not changed."""
    ocr = service("ocr", "docker-compose.gpu.yml")
    assert keys(ocr) <= {"image", "build", "environment", "deploy", "mem_limit", "ulimits"}
    assert re.search(r"^    image: kaagaz-ocr-gpu:local", ocr, re.MULTILINE)
    assert re.search(r"^      args:\n        PADDLE_EXTRA: paddle-gpu", ocr, re.MULTILINE)
    assert re.search(r"^    mem_limit: 8g$", ocr, re.MULTILINE)
    assert environment(ocr) == {"OCR_DEVICE": "gpu:0", "FLAGS_allocator_strategy": "auto_growth"}


def test_the_gpu_file_closes_ollamas_port_and_keeps_its_models_loaded() -> None:
    ollama = service("ollama", "docker-compose.gpu.yml")
    assert re.search(r"^    ports: !reset \[\]$", ollama, re.MULTILINE)
    assert keys(ollama) == {"ports", "environment", "deploy"}
    assert environment(ollama) == {
        "OLLAMA_NUM_PARALLEL": "1",
        "OLLAMA_MAX_LOADED_MODELS": "2",
        "OLLAMA_KEEP_ALIVE": "-1",
    }


def test_the_gpu_file_points_the_worker_at_paddle_and_both_at_ollama() -> None:
    worker = environment(service("worker", "docker-compose.gpu.yml"))
    api = environment(service("api", "docker-compose.gpu.yml"))
    assert worker == {"OCR_ENGINE": "paddleocr_vl", "OCR_DPI": "400", "MODEL_BASE_URL": "http://ollama:11434"}
    assert api == {"MODEL_BASE_URL": "http://ollama:11434"}
    # The shared block sets MODEL_BASE_URL by name too, so the override's wins (compose
    # merges environment by name).
    assert re.search(r"^    MODEL_BASE_URL: ", (ROOT / "docker-compose.yml").read_text("utf-8"), re.M)


def test_the_base_ocr_service_keeps_every_security_setting() -> None:
    ocr = service("ocr")
    for line in (
        "    read_only: true",
        "    cap_drop: [ALL]",
        '    security_opt: ["no-new-privileges:true"]',
        '    user: "10001:10001"',
        "    networks: [ocr_net]",
        "    ulimits:\n      core:\n        soft: 0\n        hard: 0",
    ):
        assert f"\n{line}\n" in ocr, line
    assert not keys(ocr) & {"ports", "env_file", "cap_add", "privileged", "network_mode", "devices"}


def test_the_default_compose_files_have_no_gpu() -> None:
    for name in ("docker-compose.yml", "docker-compose.ocr-host.yml"):
        text = (ROOT / name).read_text(encoding="utf-8").lower()
        for word in ("nvidia", "devices:", "gpus", "deploy:", "ocr_device"):
            assert word not in text, (name, word)
