"""The EC2 server scripts and systemd units in infra/ec2 (EC2 PRD, WP-E).

The files are read as plain text, as tests/test_compose_prod.py reads the
compose files. What the scripts do when run (a new commit, a failed build, an
unchanged commit, a held lock, a failed dump) is checked by hand in a
throwaway Linux container; the pull request has the steps.
"""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
EC2 = ROOT / "infra" / "ec2"
COMPOSE_FILES = "(-f docker-compose.yml -f docker-compose.prod.yml)"
DEPLOY_KEY_SSH = "ssh -i /home/ubuntu/.ssh/kaagaz_deploy -o IdentitiesOnly=yes"


def text(name: str) -> str:
    return (EC2 / name).read_text(encoding="utf-8")


def unit(name: str) -> dict[str, str]:
    """A unit file's settings as section.key -> value."""
    settings: dict[str, str] = {}
    section = ""
    for line in text(name).splitlines():
        if heading := re.fullmatch(r"\[(\w+)\]", line):
            section = heading.group(1)
        elif setting := re.fullmatch(r"(\w+)=(.*)", line):
            settings[f"{section}.{setting.group(1)}"] = setting.group(2)
    return settings


def section(settings: dict[str, str], name: str) -> dict[str, str]:
    return {key: value for key, value in settings.items() if key.startswith(f"{name}.")}


def test_deploy_holds_the_lock_and_follows_only_production() -> None:
    script = text("deploy.sh")
    assert script.startswith("#!/usr/bin/env bash\n")
    assert "\nset -euo pipefail\n" in script
    assert "\nKAAGAZ_DIR=/home/ubuntu/kaagaz\n" in script and '\ncd "$KAAGAZ_DIR"\n' in script
    assert "\nLOCK_FILE=/tmp/kaagaz-deploy.lock\n" in script
    assert 'exec 9>"$LOCK_FILE"\nif ! flock -n 9; then\n  exit 0\nfi\n' in script
    assert script.index("flock -n 9") < script.index("git ")  # held before anything else runs
    assert re.findall(r"git fetch .*", script) == ["git fetch --quiet origin production; then"]
    assert re.findall(r"git reset .*", script) == ["git reset --quiet --hard origin/production; then"]
    assert f"\nCOMPOSE={COMPOSE_FILES}\n" in script


def test_every_script_runs_the_base_and_prod_files_with_no_profiles() -> None:
    """PRD 5 section 7: the stack is two files and every service runs without a profile."""
    for name in ("deploy.sh", "pgdump.sh", "idle-stop.sh"):
        script = text(name)
        assert re.findall(r"-f (docker-compose[\w.]*)", script) == ["docker-compose.yml", "docker-compose.prod.yml"], name
        assert "--profile" not in script and "PROFILES" not in script, name


def test_deploy_records_the_commit_only_after_a_good_build() -> None:
    script = text("deploy.sh")
    assert "\nDEPLOYED_FILE=/home/ubuntu/.kaagaz-deployed\n" in script
    build = script.index(
        'docker compose "${COMPOSE[@]}" up -d --build --remove-orphans; then\n'
        '    report "$target" failed\n'
        "    exit 1\n"
    )
    record = script.index('printf \'%s\\n\' "$target" >"$DEPLOYED_FILE.tmp"')
    assert build < record < script.index('mv -f "$DEPLOYED_FILE.tmp" "$DEPLOYED_FILE"')
    assert record < script.index("docker image prune -f") < script.index('report "$target" deployed')
    assert script.count('>"$DEPLOYED_FILE') == 1  # written nowhere else
    unchanged = script.index('docker compose "${COMPOSE[@]}" up -d; then')
    assert unchanged < script.index('report "$target" unchanged')


def test_deploy_prints_one_line_per_run() -> None:
    script = text("deploy.sh")
    outcomes = ["failed", "failed", "failed", "deployed", "failed", "unchanged"]  # the last one: plain up
    assert re.findall(r"report \S+ (\w+)", script) == outcomes
    assert 'printf \'%s %s\\n\' "$1" "$2"' in script
    # Nothing else on stdout: every other line goes to stderr (the journal still has it).
    assert [line.strip() for line in re.findall(r"^ *echo .*", script, re.MULTILINE)] == [
        'echo "docker builder prune failed; carrying on" >&2',
        'echo "low disk: $free_gb GB free; a build needs about $MIN_FREE_GB GB" >&2',
        'echo "docker image prune failed; the deploy itself is done" >&2',
    ]


def test_deploy_prunes_old_build_cache_and_never_volumes() -> None:
    """A full disk cut a pip download short and failed a deploy (g4dn, 4 Oct 2026)."""
    script = text("deploy.sh")
    assert "\nBUILD_CACHE_KEEP=168h\nMIN_FREE_GB=20\n" in script
    prune = (
        "prune_build_cache() {\n"
        '  if ! docker builder prune -f --filter "until=$BUILD_CACHE_KEEP" >/dev/null; then\n'
        '    echo "docker builder prune failed; carrying on" >&2\n'
        "  fi\n"
        "}\n"
    )
    assert prune in script  # bounded (7 days), not --all, and never fatal
    assert re.findall(r"if ! (docker \w+ prune.*)", script) == [
        'docker builder prune -f --filter "until=$BUILD_CACHE_KEEP" >/dev/null; then',
        "docker image prune -f >/dev/null; then",
    ]
    code = "\n".join(line for line in script.splitlines() if not line.lstrip().startswith("#"))
    assert "volume" not in code.lower() and "system prune" not in code and "--all" not in code
    assert code.count("prune_build_cache") == 3  # defined once, called twice
    build = script.index("up -d --build --remove-orphans")
    low = script.index("  free_gb=\"$(df --output=avail -BG / | tail -n 1 | tr -dc '0-9' || true)\"\n")
    check = script.index('  if [[ -n "$free_gb" ]] && ((free_gb < MIN_FREE_GB)); then\n    echo "low disk: ')
    before = script.index("    prune_build_cache\n  fi\n")
    after = script.index('  fi\n  prune_build_cache\n  report "$target" deployed\n')
    assert script.index("git reset ") < low < check < before < build
    assert build < script.index("docker image prune -f") < after


def test_pgdump_writes_a_private_custom_format_dump_and_keeps_seven_days() -> None:
    script = text("pgdump.sh")
    assert script.startswith("#!/usr/bin/env bash\n")
    assert "\nset -euo pipefail\n" in script
    assert f"\nCOMPOSE={COMPOSE_FILES}\n" in script
    assert 'exec -T postgres \\\n  pg_dump -U "$pg_user" -Fc "$pg_db" >"$partial"\n' in script
    assert "\nBACKUP_DIR=/home/ubuntu/backups\n" in script and '\nmkdir -p "$BACKUP_DIR"\n' in script
    assert '\nday="$(date -u +%Y%m%d)"\ndump="$BACKUP_DIR/kaagaz-$day.dump"\n' in script
    assert "\numask 077\n" in script and '\nchmod 600 "$partial"\n' in script
    assert script.index('>"$partial"') < script.index('mv -f "$partial" "$dump"')
    assert "trap 'rm -f \"$partial\"' EXIT" in script  # a failed dump leaves no file behind
    assert "\nKEEP_DAYS=7\n" in script and 'date -u -d "$((KEEP_DAYS - 1)) days ago"' in script
    assert script.index('mv -f "$partial" "$dump"') < script.index('rm -f -- "$old"')


def test_pgdump_never_runs_or_prints_the_env_file() -> None:
    script = text("pgdump.sh")
    assert not re.search(r"^ *(source|\.|eval) ", script, re.MULTILINE)
    assert "done <.env\n" in script  # read line by line as text
    assert 'pg_user="$(env_value POSTGRES_USER)"' in script and 'pg_db="$(env_value POSTGRES_DB)"' in script
    printed = re.findall(r"^ *(?:echo|printf) .*", script, re.MULTILINE)
    assert printed == [
        "  printf '%s' \"$value\"",  # env_value's result, caught by $(...) and never shown
        '  echo "pg_dump wrote nothing; no dump saved" >&2',
        'echo "saved $dump"',
    ]


def test_the_units_run_as_ubuntu_on_the_prd_schedule() -> None:
    assert section(unit("kaagaz-deploy.service"), "Service") == {
        "Service.Type": "oneshot",
        "Service.User": "ubuntu",
        "Service.WorkingDirectory": "/home/ubuntu/kaagaz",
        "Service.TimeoutStartSec": "infinity",
        "Service.Environment": f'"GIT_SSH_COMMAND={DEPLOY_KEY_SSH}"',
        "Service.ExecStart": "/home/ubuntu/kaagaz/infra/ec2/deploy.sh",
    }
    assert section(unit("kaagaz-pgdump.service"), "Service") == {
        "Service.Type": "oneshot",
        "Service.User": "ubuntu",
        "Service.WorkingDirectory": "/home/ubuntu/kaagaz",
        "Service.ExecStart": "/home/ubuntu/kaagaz/infra/ec2/pgdump.sh",
    }
    for name, schedule in (
        ("kaagaz-deploy.timer", {"Timer.OnBootSec": "2min", "Timer.OnUnitActiveSec": "5min"}),
        ("kaagaz-pgdump.timer", {"Timer.OnCalendar": "*-*-* 20:00:00 UTC", "Timer.Persistent": "true"}),
    ):
        timer = unit(name)
        assert section(timer, "Timer") == schedule
        assert section(timer, "Install") == {"Install.WantedBy": "timers.target"}
