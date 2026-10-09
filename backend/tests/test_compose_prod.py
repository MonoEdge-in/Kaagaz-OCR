"""The EC2 server's compose file and Caddy's file (Deployment PRD, WP-B).

docker-compose.prod.yml is laid over docker-compose.yml. The files are read as
plain text, so the test needs no YAML library and no Docker. What Compose itself
makes of the two files together (the ``!reset`` tags, volumes merged by target
path) is checked by CI's "Production compose renders" step, which runs
``docker compose ... config``.
"""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PROD = "docker-compose.prod.yml"
PROD_COMMAND = "docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d --build"
LOGGING = 'x-logging: &logging\n  driver: json-file\n  options:\n    max-size: "20m"\n    max-file: "5"\n'
ONE_SHOT = {"migrate"}  # runs once per deploy and exits
CLEARED = {"postgres", "seaweedfs", "elasticmq", "clamav", "api", "frontend"}
# PRD 5 section 7: the scan reader and the model run outside the stack, so these are all.
BASE_SERVICES = ["postgres", "seaweedfs", "elasticmq", "clamav", "migrate", "api", "worker", "frontend"]
_NEXT_KEY = re.compile(r"^ {0,2}[a-z]", re.MULTILINE)  # the next service, or a top-level key


def service(name: str, file: str = "docker-compose.yml") -> str:
    """One service's block in a compose file, up to the next service or top-level key."""
    text = (ROOT / file).read_text(encoding="utf-8")
    block = text[text.index(f"\n  {name}:\n") + 1 :]
    following = _NEXT_KEY.search(block, block.index("\n") + 1)
    return block[: following.start()] if following else block


def keys(block: str) -> set[str]:
    """A service block's own keys (four spaces in)."""
    return set(re.findall(r"^    ([a-z_]+):", block, re.MULTILINE))


def services(file: str) -> list[str]:
    """The service names in one compose file, in order."""
    text = (ROOT / file).read_text(encoding="utf-8")
    block = text[text.index("\nservices:\n") + len("\nservices:\n") :]
    following = re.search(r"^[a-z]", block, re.MULTILINE)  # the next top-level key
    block = block[: following.start()] if following else block
    return re.findall(r"^  ([a-z-]+):$", block, re.MULTILINE)


def has(block: str, line: str) -> bool:
    """Whether a service block holds ``line`` (four spaces in), comments after it allowed."""
    return re.search(rf"^    {re.escape(line)}( #.*)?$", block, re.MULTILINE) is not None


def test_the_base_file_runs_only_the_app_and_its_stand_ins() -> None:
    """PRD 5 section 7: no reader or model service, no extra network, and the backend
    containers get no address for one (the shared x-backend block lists every name)."""
    text = (ROOT / "docker-compose.yml").read_text(encoding="utf-8")
    assert services("docker-compose.yml") == BASE_SERVICES
    assert re.findall(r"^([a-z-]+):", text, re.MULTILINE) == ["name", "x-backend", "services", "volumes"]
    volumes = r"^volumes:\n  postgres-data:\n  seaweedfs-data:\n  elasticmq-data:\n  clamav-data:\n\Z"
    assert re.search(volumes, text, re.MULTILINE)
    backend = text[text.index("x-backend:") : text.index("\nservices:\n")]
    assert re.findall(r"^    ([A-Z_]+):", backend, re.MULTILINE) == [
        "DATABASE_URL",
        "STORAGE_ENDPOINT_URL",
        "QUEUE_ENDPOINT_URL",
        "QUEUE_URL",
        "QUEUE_EXPRESS_URL",
        "WORKER_REPLICAS",
        "CLAMAV_HOST",
        "CLAMAV_PORT",
    ]
    assert re.findall(r"^  ([a-z_]+):", backend, re.MULTILINE) == ["image", "build", "env_file", "environment"]
    assert "networks" not in keys(service("worker"))


def test_the_prod_file_covers_every_service_and_adds_only_caddy() -> None:
    text = (ROOT / PROD).read_text(encoding="utf-8")
    assert PROD_COMMAND in text
    assert services(PROD) == [*services("docker-compose.yml"), "caddy"]
    assert re.findall(r"^([a-z-]+):", text, re.MULTILINE) == ["x-logging", "services", "volumes"]
    assert re.search(r"^volumes:\n  caddy-data:\n  caddy-config:\n\Z", text, re.MULTILINE)


def test_only_caddy_publishes_ports_and_only_80_and_443() -> None:
    """Every service that publishes a port in the base file has it cleared by this one,
    so caddy's are the only ports left."""
    published = {name for name in services("docker-compose.yml") if "ports" in keys(service(name))}
    assert published == CLEARED
    cleared = {name for name in services(PROD) if has(service(name, PROD), "ports: !reset []")}
    assert cleared == CLEARED
    assert {name for name in services(PROD) if "ports" in keys(service(name, PROD))} == CLEARED | {"caddy"}
    caddy = service("caddy", PROD)
    assert re.findall(r'^      - "(.*)"$', caddy[: caddy.index("    volumes:")], re.MULTILINE) == [
        "80:80",
        "443:443",
    ]


def test_every_long_running_service_restarts_and_every_log_is_rotated() -> None:
    assert LOGGING in (ROOT / PROD).read_text(encoding="utf-8")
    base = services("docker-compose.yml")
    for name in services(PROD):
        assert has(service(name, PROD), "logging: *logging"), name
        restarts = [
            has(service(name, file), "restart: unless-stopped")
            for file in ("docker-compose.yml", PROD)
            if file == PROD or name in base
        ]
        if name in ONE_SHOT:
            assert not any(restarts), name
        else:
            assert restarts.count(True) == 1, name  # set once, in the base file or this one


def test_seaweedfs_reads_the_servers_own_keys_file() -> None:
    """Same target as the base file's mount, so Compose puts this source in its place."""
    target = "/etc/seaweedfs/s3.json:ro"
    assert has(service("seaweedfs"), f"  - ./infra/seaweedfs/s3.json:{target}")
    seaweedfs = service("seaweedfs", PROD)
    assert re.findall(r"^      - (.*)$", seaweedfs, re.MULTILINE) == [
        "${SEAWEEDFS_S3_CONFIG:?set SEAWEEDFS_S3_CONFIG in .env}:" + target
    ]


def test_caddy_is_pinned_and_gets_its_values_from_the_servers_env() -> None:
    caddy = service("caddy", PROD)
    assert re.search(r"^    image: caddy:2@sha256:[0-9a-f]{64}$", caddy, re.MULTILINE)
    volumes = caddy[caddy.index("    volumes:") : caddy.index("    environment:")]
    assert re.findall(r"^      - (\S+)", volumes, re.MULTILINE) == [
        "caddy-data:/data",
        "caddy-config:/config",
        "./infra/caddy/Caddyfile:/etc/caddy/Caddyfile:ro",
    ]
    # No defaults: compose stops, naming the value, when the server's .env lacks one.
    assert re.findall(r"^      ([A-Z_]+): (.*)$", caddy, re.MULTILINE) == [
        ("KAAGAZ_DOMAIN", "${KAAGAZ_DOMAIN:?set KAAGAZ_DOMAIN in .env}"),
        ("ACME_EMAIL", "${ACME_EMAIL:?set ACME_EMAIL in .env}"),
    ]
    assert "    depends_on:\n      - api\n      - frontend\n" in caddy
    assert has(caddy, "restart: unless-stopped")


def test_the_caddyfile_sends_api_unchanged_to_the_api_and_the_rest_to_the_frontend() -> None:
    text = (ROOT / "infra" / "caddy" / "Caddyfile").read_text(encoding="utf-8")
    lines = [line.strip() for line in text.splitlines() if line.strip() and not line.strip().startswith("#")]
    assert lines == [
        "{",
        "email {$ACME_EMAIL}",
        "}",
        "{$KAAGAZ_DOMAIN} {",
        "request_body {",
        "max_size 60MB",
        "}",
        # handle, not handle_path: the API's routes are mounted under /api (kaagaz/api/app.py).
        "handle /api/* {",
        "reverse_proxy api:8000",
        "}",
        "handle {",
        "reverse_proxy frontend:3000",
        "}",
        "log {",
        "output stdout",
        "format json",
        "}",
        "}",
    ]
