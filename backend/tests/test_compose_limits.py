"""#246 A2: memory limits on the server's containers, and none on postgres.

Read as text, as tests/test_compose_prod.py does; CI's "Production compose renders" step
checks what Compose itself makes of them.
"""

import re

from tests.test_compose_prod import PROD, ROOT, has, keys, service, services

# PROVISIONAL, Vrushit's budget. Changed on purpose (stability pass, 7 Nov 2026, A15): the
# workers 4g to 3g, and the frontend, seaweedfs and elasticmq limited too.
LIMITS = {
    "seaweedfs": "1g",
    "elasticmq": "512m",
    "clamav": "4g",
    "api": "3g",
    "worker": "3g",
    "frontend": "1g",
}
# The app's own containers (A19): no capability, and no setuid gaining one.
HARDENED = {"migrate", "api", "worker", "frontend"}


def _limit(name: str) -> str | None:
    found = re.findall(r"^    mem_limit: (\S+)", service(name, PROD), re.MULTILINE)
    return found[0] if found else None


def test_every_service_but_postgres_and_caddy_has_a_limit() -> None:
    # postgres among the rest: the kernel's OOM killer then has no reason to pick it.
    assert {name: _limit(name) for name in services(PROD) if _limit(name)} == LIMITS
    assert "mem_limit" not in (ROOT / "docker-compose.yml").read_text(encoding="utf-8")  # laptops: none


def test_a_limited_container_never_swaps_past_its_limit_and_postgres_is_the_last_one_killed() -> None:
    """A15: memswap_limit equal to mem_limit, so a container at its limit is stopped, not swapped
    until the whole server crawls; and the kernel picks anything before postgres."""
    for name in LIMITS:
        assert re.findall(r"^    memswap_limit: (\S+)", service(name, PROD), re.MULTILINE) == [
            LIMITS[name]
        ], name
    assert has(service("postgres", PROD), "oom_score_adj: -900")
    assert "16 GB" in (ROOT / PROD).read_text(encoding="utf-8")  # the totals are written down


def test_postgres_gets_a_gigabyte_of_buffers_and_shared_memory_on_the_server() -> None:
    """A17: the image's default /dev/shm is 64 MB, which a parallel query outgrows."""
    postgres = service("postgres", PROD)
    assert has(postgres, "shm_size: 1g")
    assert has(
        postgres, 'command: ["postgres", "-c", "shared_buffers=1GB", "-c", "maintenance_work_mem=512MB"]'
    )
    assert "command" not in keys(service("postgres"))  # no laptop flags for this one to drop


def test_streams_and_the_database_get_time_to_finish_when_stopped() -> None:
    """A16: an answer being written finishes before api stops; postgres and the queue write out
    what they hold. The worker's own 600 s is in docker-compose.yml."""
    for name, grace in (("api", "200s"), ("postgres", "60s"), ("elasticmq", "60s")):
        assert has(service(name, PROD), f"stop_grace_period: {grace}"), name


def test_each_worker_has_a_cpu_and_a_half() -> None:
    """A20: a worker reading a big scan can't starve api and postgres of the CPU."""
    assert has(service("worker", PROD), 'cpus: "1.5"')


def test_the_apps_own_containers_drop_every_capability() -> None:
    """A19: they run as a user (backend/Dockerfile, frontend/Dockerfile) on ports above 1024, so
    they need none. clamav, postgres and caddy start as root and are left as they are."""
    for name in services(PROD):
        block = service(name, PROD)
        hardened = has(block, "security_opt: [no-new-privileges:true]") and has(block, "cap_drop: [ALL]")
        assert hardened == (name in HARDENED), name
