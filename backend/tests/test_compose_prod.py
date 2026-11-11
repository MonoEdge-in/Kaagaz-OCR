"""The EC2 server's compose file and Caddy's file (Deployment PRD, WP-B).

docker-compose.prod.yml is laid over docker-compose.yml. The files are read as
plain text, so the test needs no YAML library and no Docker. What Compose itself
makes of the two files together (the ``!reset`` tags, volumes merged by target
path) is checked by CI's "Production compose renders" step, which runs
``docker compose ... config``.
"""

import json
import re
from pathlib import Path

from kaagaz.api.upload_guard import MULTIPART_OVERHEAD

ROOT = Path(__file__).resolve().parents[2]
PROD = "docker-compose.prod.yml"
PROD_COMMAND = "docker compose -f docker-compose.yml -f docker-compose.prod.yml <command>"
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
    """PRD 5 section 7: no reader or model service, and the backend containers get no
    address for one (the shared x-backend block lists every name). The only networks are
    #477's three closed lines (test_each_service_is_only_on_its_closed_lines)."""
    text = (ROOT / "docker-compose.yml").read_text(encoding="utf-8")
    assert services("docker-compose.yml") == BASE_SERVICES
    assert re.findall(r"^([a-z-]+):", text, re.MULTILINE) == [
        "name",
        "x-backend",
        "services",
        "networks",
        "volumes",
    ]
    volumes = r"^volumes:\n  postgres-data:\n  seaweedfs-data:\n  elasticmq-data:\n  clamav-data:\n\Z"
    assert re.search(volumes, text, re.MULTILINE)
    backend = text[text.index("x-backend:") : text.index("\nservices:\n")]
    assert re.findall(r"^    ([A-Z_]+):", backend, re.MULTILINE) == [
        "DATABASE_URL",
        # Changed on purpose (#479, 3 Nov 2026): blanked for api and worker, so neither holds
        # the master password or (the workers) the super admin's login (tests/test_db_logins.py).
        "POSTGRES_PASSWORD",
        "OPS_DATABASE_URL",
        # #477: blanked too, so the .env line reaches only migrate, which sets its own.
        "STORAGE_ADMIN_ACCESS_KEY",
        "STORAGE_ADMIN_SECRET_KEY",
        "STORAGE_ENDPOINT_URL",
        "QUEUE_ENDPOINT_URL",
        "QUEUE_URL",
        "QUEUE_EXPRESS_URL",
        "WORKER_REPLICAS",
        "CLAMAV_HOST",
        "CLAMAV_PORT",
    ]
    shared = re.findall(r"^  ([a-z_]+):", backend, re.MULTILINE)
    assert shared == ["image", "build", "env_file", "environment"]
    # infra/ec2/deploy.sh builds and migrates a commit's images beside the running :local ones.
    assert "\n  image: kaagaz-backend:${KAAGAZ_TAG:-local}\n" in backend
    assert has(service("frontend"), "image: kaagaz-frontend:${KAAGAZ_TAG:-local}")


def test_the_prod_file_covers_every_service_and_adds_only_caddy() -> None:
    text = (ROOT / PROD).read_text(encoding="utf-8")
    assert PROD_COMMAND in text
    # A hand `up -d --build` would rebuild the live :local images (infra/ec2/deploy.sh, #246).
    assert "# Deploys go through infra/ec2/deploy.sh, never `up -d --build` by hand" in text
    assert services(PROD) == [*services("docker-compose.yml"), "caddy"]
    assert re.findall(r"^([a-z-]+):", text, re.MULTILINE) == ["x-logging", "services", "volumes"]
    assert re.search(r"^volumes:\n  caddy-data:\n  caddy-config:\n\Z", text, re.MULTILINE)


def test_the_laptops_api_port_is_reachable_from_the_laptop_only() -> None:
    """Review of #520: the image trusts X-Forwarded-For (backend/Dockerfile), so an API port open
    to the network would let anyone on it pick their own address, and rate bucket. The laptop
    stack publishes it on 127.0.0.1 only; on the server no API port is published at all."""
    api = service("api")
    published = re.findall(r'^      - "(.*)"$', api[api.index("    ports:") :], re.MULTILINE)
    assert published[:1] == ["127.0.0.1:8000:8000"]


def test_the_laptop_publishes_every_port_on_127_0_0_1_only() -> None:
    """A21: the stand-ins' ports (the database, storage, the queue, the scanner) and the website
    are reachable from the laptop itself, never from the network it is on."""
    for name in services("docker-compose.yml"):
        block = service(name)
        if "ports" in keys(block):
            published = re.findall(r'^      - "(.*)"$', block[block.index("    ports:") :], re.MULTILINE)
            assert published and all(port.startswith("127.0.0.1:") for port in published), name


UVICORN = (
    '["uvicorn", "kaagaz.api.main:app", "--host", "0.0.0.0", "--port", "8000",'
    ' "--no-server-header", "--timeout-keep-alive", "75"]'
)


def test_uvicorn_names_no_server_and_keeps_idle_connections_longer_than_caddy() -> None:
    """A5: no ``server: uvicorn`` header on a reply, and an idle connection kept 75 s, longer
    than Caddy keeps it (keepalive 60s in the Caddyfile), so Caddy closes it first."""
    assert has(service("api"), f"command: {UVICORN}")
    dockerfile = (ROOT / "backend" / "Dockerfile").read_text(encoding="utf-8")
    assert re.findall(r"^CMD (.*)$", dockerfile, re.MULTILINE) == [UVICORN]


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


def test_caddy_lets_through_a_file_at_the_upload_limit() -> None:
    """#569: a whole file up to MAX_UPLOAD_MB (a direct POST, not 8 MB parts) reaches the API,
    which refuses a bigger one with its own message, never Caddy's bare 413. In bytes: Caddy's MB
    is 10**6, the app's is 1024**2, and the API allows multipart overhead on top."""
    caddy = (ROOT / "infra" / "caddy" / "Caddyfile").read_text(encoding="utf-8")
    env = (ROOT / ".env.example").read_text(encoding="utf-8")
    cap = int(re.findall(r"^\s*max_size (\d+)MB$", caddy, re.MULTILINE)[0])
    limit = int(re.findall(r"^MAX_UPLOAD_MB=(\d+)$", env, re.MULTILINE)[0])
    assert cap * 10**6 > limit * 1024**2 + MULTIPART_OVERHEAD


def test_the_caddyfile_sends_api_unchanged_to_the_api_and_the_rest_to_the_frontend() -> None:
    text = (ROOT / "infra" / "caddy" / "Caddyfile").read_text(encoding="utf-8")
    lines = [line.strip() for line in text.splitlines() if line.strip() and not line.strip().startswith("#")]
    assert lines == [
        "{",
        "email {$ACME_EMAIL}",
        "}",
        "{$KAAGAZ_DOMAIN} {",
        "request_body {",
        # Changed on purpose (#569, 11 Nov 2026): above the 100 MB upload limit.
        "max_size 110MB",
        "}",
        # Changed on purpose (final polish, 16 Oct 2026): HSTS on the HTTPS site (security checklist).
        'header Strict-Transport-Security "max-age=31536000"',
        # handle, not handle_path: the API's routes are mounted under /api (kaagaz/api/app.py).
        # Changed on purpose (#246, 27 Oct 2026): A7, each upstream is tried again for 30 s
        # while a deploy restarts it, then a short "updating" note instead of a bare 502.
        "handle /api/* {",
        "reverse_proxy api:8000 {",
        "lb_try_duration 30s",
        "lb_try_interval 500ms",
        # Added on purpose (stability pass, 7 Nov 2026, A5): idle connections end here first,
        # before uvicorn's 75 s keep-alive, so Caddy never reuses one uvicorn just closed (a 502).
        "transport http {",
        "keepalive 60s",
        "}",
        "}",
        "}",
        "handle {",
        "reverse_proxy frontend:3000 {",
        "lb_try_duration 30s",
        "lb_try_interval 500ms",
        "}",
        "}",
        "handle_errors 502 503 504 {",
        'header Retry-After "60"',
        'respond "Kaagaz is updating. Please try again in a minute." 503',
        "}",
        "log {",
        "output stdout",
        # Changed on purpose (stability pass, 7 Nov 2026, A18): format json, filtered.
        "format filter {",
        "wrap json",
        "fields {",
        "request>uri query {",
        *[f"delete {name}" for name in PRIVATE_QUERY],
        "}",
        "request>headers>Referer delete",
        "}",
        "}",
        "}",
        "}",
    ]


# A18: the query parameters that can carry a question, a search, a document's words or a
# folder's name: q (/api/search, /api/documents, /api/stories match, and the /search and /chat
# pages), party (/api/documents), w (/api/documents/{id}/words), source_name
# (/api/folder-uploads/previous); review: property (/api/documents, a survey number read from a
# document), and a Google sign-in's code and state (/api/auth/oidc/google/callback).
PRIVATE_QUERY = ["q", "party", "w", "source_name", "property", "code", "state"]


def test_the_access_log_keeps_no_search_question_or_name() -> None:
    """A18: Caddy's access log is written with the query parameters above deleted from each
    address, and without the Referer header, which repeats a page's address (/search?q=...).
    The idle check (infra/ec2/idle-stop.sh) still reads the path and the Cookie header's name."""
    text = (ROOT / "infra" / "caddy" / "Caddyfile").read_text(encoding="utf-8")
    query = text[text.index("request>uri query {") : text.index("}", text.index("request>uri query {"))]
    assert re.findall(r"delete (\S+)", query) == PRIVATE_QUERY
    idle = (ROOT / "infra" / "ec2" / "idle-stop.sh").read_text(encoding="utf-8")
    assert 'path = str(request.get("uri", "")).split("?", 1)[0]' in idle
    assert 'any(str(name).lower() == "cookie" for name in headers)' in idle


def test_seaweedfs_is_healthy_only_once_its_s3_port_answers() -> None:
    """#439: the master port (9333) answered seconds before the S3 port, and migrate's
    ensure_bucket failed in that gap."""
    test = '["CMD", "wget", "-q", "-O", "/dev/null", "http://127.0.0.1:8333/healthz"]'
    assert f"\n      test: {test}\n" in service("seaweedfs")


def test_the_frontend_has_a_health_check_that_needs_no_curl() -> None:
    """#477: deploy.sh waits for it, so a broken website is never reported deployed."""
    frontend = service("frontend")
    assert re.search(
        r'^      test: \["CMD", "node", "-e", "fetch\(\'http://127\.0\.0\.1:3000/login\'', frontend, re.M
    )
    assert "process.exit(r.ok ? 0 : 1), () => process.exit(1)" in frontend


# #477: a container reaches only the services on a network it shares.
NETWORKS = {
    "postgres": ["data"],
    "seaweedfs": ["data"],
    "elasticmq": ["data"],
    "clamav": ["scan"],
    "migrate": ["data"],
    "api": ["data", "edge"],
    "worker": ["data", "scan"],
    "frontend": ["edge"],
}


def networks_of(block: str) -> list[str]:
    """The networks a service block lists, in order."""
    found = re.search(r"^    networks:\n((?:      - [a-z]+.*\n)+)", block, re.M)
    return re.findall(r"^      - ([a-z]+)", found.group(1), re.M) if found else []


def test_each_service_is_only_on_its_closed_lines() -> None:
    """data: the database, storage, queue and the backend; scan: the virus scanner and the
    worker; edge: Caddy, the API and the website. So the website never reaches the database."""
    text = (ROOT / "docker-compose.yml").read_text(encoding="utf-8")
    assert re.search(r"^networks:\n  data:\n  scan:\n  edge:\n\nvolumes:", text, re.M)
    # None internal: the API and worker call OpenRouter, and clamav fetches signatures.
    assert not re.search(r"^ +internal:", text, re.M)
    assert {name: networks_of(service(name)) for name in BASE_SERVICES} == NETWORKS
    assert networks_of(service("caddy", PROD)) == ["edge"]
    assert [name for name in services(PROD) if "networks" in keys(service(name, PROD))] == ["caddy"]


def test_only_migrate_gets_the_storage_admin_key() -> None:
    """#477: the app's key reads, writes and lists its own bucket; the admin key, which
    creates the bucket, is migrate's alone, and on the server only from its .env."""
    text = (ROOT / "docker-compose.yml").read_text(encoding="utf-8")
    shared = text[text.index("\n  environment: &backend-env\n") : text.index("\nservices:\n")]
    # env_file passes every .env line to api and worker; these blanks take those back.
    for name in ("POSTGRES_PASSWORD", "STORAGE_ADMIN_ACCESS_KEY", "STORAGE_ADMIN_SECRET_KEY"):
        assert f'\n    {name}: ""\n' in shared, name
    migrate = service("migrate")
    assert "    environment:\n      <<: *backend-env\n" in migrate
    assert re.findall(r"^      (STORAGE_[A-Z_]+): (.*)$", migrate, re.M) == [
        ("STORAGE_ACCESS_KEY", "${STORAGE_ADMIN_ACCESS_KEY:-kaagaz-local-admin}"),
        ("STORAGE_SECRET_KEY", "${STORAGE_ADMIN_SECRET_KEY:-kaagaz-local-admin-secret-laptop-only}"),
    ]
    # Not required by compose (`:?`), so logs, exec, pgdump.sh and idle-stop.sh still run
    # without them; migrate itself stops, naming them, before it migrates anything.
    assert re.findall(r"^      (STORAGE_[A-Z_]+): (.*)$", service("migrate", PROD), re.M) == [
        ("STORAGE_ACCESS_KEY", "${STORAGE_ADMIN_ACCESS_KEY:-}"),
        ("STORAGE_SECRET_KEY", "${STORAGE_ADMIN_SECRET_KEY:-}"),
    ]
    check = 'test -n "$$STORAGE_ACCESS_KEY" && test -n "$$STORAGE_SECRET_KEY" ||'
    assert migrate.index(check) < migrate.index("alembic upgrade head")
    assert "set STORAGE_ADMIN_ACCESS_KEY and STORAGE_ADMIN_SECRET_KEY in .env" in migrate
    for name in BASE_SERVICES:
        if name != "migrate":
            assert "STORAGE_ADMIN" not in service(name).replace("(STORAGE_ADMIN_ACCESS_KEY)", ""), name


def test_the_laptop_keys_file_scopes_the_app_key_to_its_bucket() -> None:
    keys_file = json.loads((ROOT / "infra" / "seaweedfs" / "s3.json").read_text(encoding="utf-8"))
    actions = {
        identity["credentials"][0]["accessKey"]: identity["actions"] for identity in keys_file["identities"]
    }
    assert actions == {
        "kaagaz-local": ["Read:kaagaz-local", "Write:kaagaz-local", "List:kaagaz-local"],
        "kaagaz-local-admin": ["Admin"],
    }
