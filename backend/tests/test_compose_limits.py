"""#246 A2: memory limits on the server's api, worker and clamav, and none on postgres.

Read as text, as tests/test_compose_prod.py does; CI's "Production compose renders" step
checks what Compose itself makes of them.
"""

import re

from tests.test_compose_prod import PROD, ROOT, service, services

LIMITS = {"api": "3g", "worker": "4g", "clamav": "4g"}  # PROVISIONAL, Vrushit's budget


def _limit(name: str) -> str | None:
    found = re.findall(r"^    mem_limit: (\S+)", service(name, PROD), re.MULTILINE)
    return found[0] if found else None


def test_api_worker_and_clamav_have_a_generous_limit_and_nothing_else_has_one() -> None:
    # postgres among the rest: the kernel's OOM killer then has no reason to pick it.
    assert {name: _limit(name) for name in services(PROD) if _limit(name)} == LIMITS
    assert "mem_limit" not in (ROOT / "docker-compose.yml").read_text(encoding="utf-8")  # laptops: none
