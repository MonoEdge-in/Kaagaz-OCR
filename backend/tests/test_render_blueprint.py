"""render.yaml runs what docker-compose.yml runs and sets every setting (docs/running-locally.md, section 7).

Plain text, as tests/test_compose_prod.py reads the compose files: no YAML library.
"""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def _read(name: str) -> str:
    return (ROOT / name).read_text(encoding="utf-8")


def _flat(text: str) -> str:
    return " ".join(text.split())


def _compose_service(name: str) -> str:
    """One service's block of docker-compose.yml, up to the next service."""
    text = _read("docker-compose.yml")
    block = text[text.index(f"\n  {name}:\n") + 1 :]
    following = re.search(r"^  [a-z]", block[3:], re.MULTILINE)
    return block[: following.start() + 3] if following else block


def _compose_command(name: str) -> str:
    match = re.search(r"^    command: \[(.*)\]$", _compose_service(name), re.MULTILINE)
    assert match is not None
    return " ".join(re.findall(r'"([^"]*)"', match.group(1)))


def test_the_backend_commands_are_composes() -> None:
    blueprint = _flat(_read("render.yaml"))
    assert f"exec {_compose_command('api')}'" in blueprint
    assert f"exec {_compose_command('worker')}'" in blueprint
    migrate = re.search(r"- >-\n(.*?)\n    depends_on:", _compose_service("migrate"), re.DOTALL)
    assert migrate is not None
    assert f"{_flat(migrate.group(1))}'" in blueprint  # the api's preDeployCommand
    assert '- key: PORT # Render sends the web traffic here\n        value: "8000"' in _read("render.yaml")


def test_the_stand_ins_use_composes_images_command_and_scanner_settings() -> None:
    def image(name: str) -> str:
        match = re.search(r"^    image: (\S+)$", _compose_service(name), re.MULTILINE)
        assert match is not None
        return match.group(1)

    blueprint = _read("render.yaml")
    assert f"url: docker.io/{image('clamav')}\n" in blueprint
    assert f"FROM {image('elasticmq')}\n" in _read("infra/render/elasticmq.Dockerfile")
    assert f"FROM {image('seaweedfs')}\n" in _read("infra/render/seaweedfs.Dockerfile")

    seaweedfs = re.search(r"^    command: (server .*)$", _compose_service("seaweedfs"), re.MULTILINE)
    assert seaweedfs is not None
    assert f"exec /entrypoint.sh {seaweedfs.group(1)}\n" in _read("infra/render/seaweedfs-start.sh")

    compose_scanner = re.findall(r'^      (CLAMD_CONF_\w+): "([^"]*)"', _compose_service("clamav"), re.M)
    render_scanner = re.findall(r'\{ key: (CLAMD_CONF_\w+), value: "([^"]*)" \}', blueprint)
    assert render_scanner == compose_scanner and len(compose_scanner) == 17


def test_every_setting_is_in_the_blueprint() -> None:
    names = set(re.findall(r'env\.\w+\("([A-Z0-9_]+)"', _read("backend/kaagaz/settings.py")))
    blueprint = _read("render.yaml")
    keys = set(re.findall(r"key: ([A-Z0-9_]+)", blueprint))
    exported = set(re.findall(r"([A-Z_]+)=http", blueprint))  # the URLs the start commands build
    set_here = keys | exported
    listed = re.search(r"# The rest come from \.env\.example.*?:\n(.*?)\n  - name: kaagaz-", blueprint, re.S)
    assert listed is not None
    from_env_example = set(re.findall(r"[A-Z][A-Z0-9_]+", listed.group(1)))

    assert names <= set_here | from_env_example
    assert from_env_example <= names  # no stale name in the list
    assert not from_env_example & set_here


def test_the_prd3_defaults_match_the_doc() -> None:
    blueprint = _read("render.yaml")
    doc = _read("docs/running-locally.md")
    for name, value in (
        ("SEARCH_EF", "100"),
        ("SEARCH_MAX_DOCUMENTS", "20"),
        ("ANSWER_NEIGHBOURS", "1"),
        ("CHAT_HISTORY_TURNS", "3"),
    ):
        assert f'- key: {name}\n        value: "{value}"' in blueprint
        assert f"`{name}={value}`" in doc


def test_the_doc_names_every_render_service() -> None:
    section = _read("docs/running-locally.md").split("## 7. Deploy on Render")[1]
    names = set(re.findall(r"name: (kaagaz-[a-z]+)$", _read("render.yaml"), re.MULTILINE))
    assert len(names) == 8  # 6 services, the database and the settings group
    for name in names:
        assert f"`{name}`" in section
