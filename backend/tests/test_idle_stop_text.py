"""The idle stop script and its systemd units (infra/ec2, EC2 PRD WP-G), read as plain text.

The script itself runs only on the EC2 server; these checks pin what it must ask and what
it must never touch.
"""

from pathlib import Path

EC2 = Path(__file__).resolve().parents[2] / "infra" / "ec2"
SCRIPT = (EC2 / "idle-stop.sh").read_text(encoding="utf-8")


def unit_lines(name: str) -> list[str]:
    text = (EC2 / name).read_text(encoding="utf-8")
    return [line for line in text.splitlines() if line and not line.startswith("#")]


def test_the_script_is_strict_bash() -> None:
    assert SCRIPT.startswith("#!/usr/bin/env bash\n")
    assert "\nset -euo pipefail\n" in SCRIPT


def test_the_script_checks_all_five_conditions() -> None:
    for needed in (
        "</proc/uptime",  # 1. up at least 20 minutes
        "MIN_UPTIME_SECONDS=1200",
        "logs --since 15m --no-log-prefix caddy",  # 2. no signed-in use in 15 minutes
        'path.startswith("/api/")',
        'path == "/api/health"',
        '"cookie"',
        'QUEUES="kaagaz-jobs kaagaz-jobs-express"',  # 3. both lanes are empty (PRD 5 Q4)
        'QUEUE="http://127.0.0.1:9324/000000000000/$name"',
        "exec -T elasticmq wget -qO- ",
        '"$QUEUE?Action=GetQueueAttributes&AttributeName.1=All"',
        'values["ApproximateNumberOfMessages"]',
        'values["ApproximateNumberOfMessagesNotVisible"]',
        "DEPLOY_LOCK=/tmp/kaagaz-deploy.lock",  # 4. no deploy running
        'flock -n "$lock_fd"',
        "KEEP_ON_FILE=/etc/kaagaz/no-idle-stop",  # 5. the manual switch
    ):
        assert needed in SCRIPT, needed


def test_the_script_has_a_dry_run_and_powers_off_once() -> None:
    assert "--dry-run) dry_run=true ;;" in SCRIPT
    assert SCRIPT.splitlines().count("systemctl poweroff") == 1  # the only command that stops it
    assert SCRIPT.rstrip().endswith(
        'if [[ "$dry_run" == true ]]; then\n'
        '  echo "idle-stop: dry run, would stop: $reason"\n'
        "  exit 0\n"
        "fi\n"
        'echo "idle-stop: stopping: $reason"\n'
        "systemctl poweroff"
    )


def test_the_script_never_asks_the_database() -> None:
    lowered = SCRIPT.lower()
    for word in ("postgres", "psql", "database_url", "pg_"):
        assert word not in lowered, word


def test_the_service_runs_the_script_once_as_root() -> None:
    lines = unit_lines("kaagaz-idle.service")
    for needed in ("Type=oneshot", "User=root", "ExecStart=/home/ubuntu/kaagaz/infra/ec2/idle-stop.sh"):
        assert needed in lines, needed


def test_the_timer_values_are_exact() -> None:
    timer = [line for line in unit_lines("kaagaz-idle.timer") if line.startswith("On")]
    assert timer == ["OnBootSec=20min", "OnUnitActiveSec=1min"]
    assert "WantedBy=timers.target" in unit_lines("kaagaz-idle.timer")
