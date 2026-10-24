"""The EC2 server scripts and systemd units in infra/ec2 (EC2 PRD, WP-E).

The files are read as plain text, as tests/test_compose_prod.py reads the
compose files. What the scripts do when run (a new commit, a failed build or
migration, a failed health check, a rollback, an unchanged commit, a held lock,
a failed dump) is checked by hand with stub commands or in a throwaway Linux
container; the pull request has the steps.
"""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
EC2 = ROOT / "infra" / "ec2"
COMPOSE_FILES = "(-f docker-compose.yml -f docker-compose.prod.yml)"
DEPLOY_KEY_SSH = "ssh -i /home/ubuntu/.ssh/kaagaz_deploy -o IdentitiesOnly=yes"
DC = 'docker compose "${COMPOSE[@]}"'


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
    lock = (
        'exec 9>"$LOCK_FILE"\n'
        "if ! flock -n 9; then\n"
        '  if [[ "$rollback" == true ]]; then\n'
        '    echo "a deploy is running; try --rollback again in a few minutes" >&2\n'
        "    exit 1\n"
        "  fi\n"
        "  exit 0\n"
        "fi\n"
    )
    assert lock in script  # the timer leaves quietly; an operator's --rollback is told
    assert script.index("flock -n 9") < script.index("git ")  # held before anything else runs
    assert script.index("flock -n 9") < script.index("docker ")
    assert re.findall(r"git fetch .*", script) == ["git fetch --quiet origin production; then"]
    assert re.findall(r"git reset .*", script) == ["git reset --quiet --hard origin/production; then"]
    assert f"\nCOMPOSE={COMPOSE_FILES}\n" in script


def test_every_script_runs_the_base_and_prod_files_with_no_profiles() -> None:
    """PRD 5 section 7: the stack is two files and every service runs without a profile."""
    for name in ("deploy.sh", "pgdump.sh", "idle-stop.sh"):
        script = text(name)
        files = re.findall(r"-f (docker-compose[\w.]*)", script)
        assert files == ["docker-compose.yml", "docker-compose.prod.yml"], name
        assert "--profile" not in script and "PROFILES" not in script, name


def deploy_steps() -> str:
    """deploy.sh's new-commit branch, where it builds, migrates and switches."""
    script = text("deploy.sh")
    start = script.index('\nif [[ "$target" != "$deployed" ]]; then\n')
    return script[start : script.index("\nfi\n", start)]


def test_deploy_builds_commit_images_and_migrates_before_touching_the_app() -> None:
    """24 Oct 2026: migrate failed after api, worker and frontend were recreated, and the site went down."""
    steps = deploy_steps()
    build = steps.index(f'  if ! KAAGAZ_TAG="$target" {DC} build; then\n')
    migrate = steps.index(f'  if ! KAAGAZ_TAG="$target" {DC} run --rm -T migrate; then\n')
    switch = steps.index(f'  if tag_images "$target" local && {DC} up -d --remove-orphans')
    assert steps.index("git reset ") < build < migrate < switch
    # Nothing before the migration starts, recreates or retags a running container.
    before = steps[:migrate]
    assert "up -d" not in before and "roll_back_to" not in before and 'tag_images "$target"' not in before
    assert "--build" not in text("deploy.sh")  # compose never rebuilds the running images in place


def test_a_failed_build_or_migrate_stops_before_the_switch() -> None:
    steps = deploy_steps()
    failed_build = (
        '  if ! KAAGAZ_TAG="$target" docker compose "${COMPOSE[@]}" build; then\n'
        '    report "$target" failed\n'
        "    exit 1\n"
        "  fi\n"
    )
    failed_migrate = (
        '  if ! KAAGAZ_TAG="$target" docker compose "${COMPOSE[@]}" run --rm -T migrate; then\n'
        '    echo "migrate failed; site still on ${current:-the running images}" >&2\n'
        '    report "$target" failed\n'
        "    exit 1\n"
        "  fi\n"
    )
    assert failed_build in steps and failed_migrate in steps
    assert steps.index(failed_migrate) < steps.index('tag_images "$target" local')
    # Not recorded, so the next run tries the commit again; the site was never touched.
    assert steps.index(failed_migrate) < steps.index('>"$DEPLOYED_FILE.tmp"')


def test_a_failed_health_check_rolls_back_to_the_live_images() -> None:
    script = text("deploy.sh")
    assert "\nAPPS=(api worker frontend)\n" in script and "\nHEALTH_TIMEOUT_SECONDS=180\n" in script
    assert (
        "wait_for_api() {\n"
        "  local waited=0\n"
        "  until api_healthy; do\n"
        "    if ((waited >= HEALTH_TIMEOUT_SECONDS)); then\n"
        "      return 1\n"
        "    fi\n"
    ) in script
    assert "docker inspect -f '{{.State.Health.Status}}' \"$id\"" in script
    # migrate is skipped: an older image would refuse the newer database.
    assert (
        "roll_back_to() {\n"
        f'  tag_images "$1" local && {DC} up -d --no-deps "${{APPS[@]}}" && wait_for_api\n'
        "}\n"
    ) in script
    steps = deploy_steps()
    gate = (
        f'  if tag_images "$target" local && {DC} up -d --remove-orphans && wait_for_api; then\n'
        "    healthy=true\n"
        "  fi\n"
    )
    rollback = (
        '  if [[ -n "$current" ]] && roll_back_to "$current"; then\n'
        '    report "$target" "rolled back to $current"\n'
        "    exit 1\n"
        "  fi\n"
    )
    assert gate in steps and rollback in steps
    assert steps.index(gate) < steps.index('  if [[ "$healthy" == true ]]; then\n') < steps.index(rollback)


def test_deploy_records_the_commit_only_after_the_switch() -> None:
    """Recorded after the health check either way, so a rolled-back release is not
    switched to again every 5 minutes."""
    script = text("deploy.sh")
    assert "\nDEPLOYED_FILE=/home/ubuntu/.kaagaz-deployed\n" in script
    steps = deploy_steps()
    record = steps.index('printf \'%s\\n\' "$target" >"$DEPLOYED_FILE.tmp"')
    move = steps.index('mv -f "$DEPLOYED_FILE.tmp" "$DEPLOYED_FILE"')
    assert steps.index("    healthy=true\n") < record < move
    assert record < steps.index('report "$target" deployed') and record < steps.index("roll_back_to")
    assert script.count('>"$DEPLOYED_FILE') == 1  # written nowhere else
    unchanged = script.index('docker compose "${COMPOSE[@]}" up -d; then')
    assert unchanged < script.index('report "$target" unchanged')


def test_deploy_keeps_the_live_and_previous_images_in_the_state_file() -> None:
    script = text("deploy.sh")
    assert "\nIMAGES_FILE=/home/ubuntu/.kaagaz-images\n" in script
    assert '{ read -r current && read -r previous; } <"$IMAGES_FILE" || true' in script
    assert script.count('>"$IMAGES_FILE.tmp"') == 1  # only save_images writes it
    # The first deploy with commit tags names the running images after the deployed commit.
    assert '[[ -z "$current" && -n "$deployed" ]] && tag_images local "$deployed"; then' in script
    steps = deploy_steps()
    good = steps[steps.index('  if [[ "$healthy" == true ]]; then\n') :]
    assert good.startswith(
        '  if [[ "$healthy" == true ]]; then\n'
        '    if [[ "$current" != "$target" ]]; then\n'
        '      previous="$current"\n'
        "    fi\n"
        '    save_images "$target" "$previous"\n'
        '    prune_images "$target" "$previous"\n'
        "    prune_build_cache\n"
        '    report "$target" deployed\n'
    )


def test_the_image_prune_never_removes_the_live_or_previous_images() -> None:
    """24 Oct 2026: `docker image prune -f` had removed the previous image, so there was no rollback."""
    script = text("deploy.sh")
    keep = '      if [[ "$tag" != local && "$tag" != "$1" && "$tag" != "$2" ]]; then\n'
    start = script.index("prune_images() {\n")
    prune = script[start : script.index("\n}\n", start)]
    assert keep in prune
    assert "done < <(docker image ls --format '{{.Tag}}' \"$repo\")" in prune
    # Only commit tags of the two app images are removed, and only after a healthy switch.
    assert re.findall(r"docker image rm .*", script) == [
        'docker image rm "$repo:$tag" >/dev/null || echo "could not remove $repo:$tag; carrying on" >&2'
    ]
    assert "for repo in kaagaz-backend kaagaz-frontend; do" in prune
    assert script.count("prune_images") == 2  # defined once, called once, on the good path


def test_deploy_rollback_puts_the_previous_images_back_and_never_downgrades() -> None:
    script = text("deploy.sh")
    assert "\n  --rollback) rollback=true ;;\n" in script
    start = script.index('\nif [[ "$rollback" == true ]]; then\n  if [[ -z "$previous" ]]; then\n')
    branch = script[start : script.index("\nfi\n", script.index("  exit 0\n", start))]
    assert branch.index('roll_back_to "$previous"') < branch.index('save_images "$previous" "$current"')
    assert branch.endswith('  report "$current" "rolled back to $previous"\n  exit 0')
    assert start < script.index("git fetch")  # the checkout and the database are left alone
    assert "migrate" not in branch and "git " not in branch
    code = "\n".join(line for line in script.splitlines() if not line.lstrip().startswith("#")).lower()
    assert "downgrade" not in code and "alembic" not in code  # no schema change but compose's own migrate


def test_deploy_prints_one_line_per_run() -> None:
    script = text("deploy.sh")
    assert re.findall(r"report (\S+) (.+)", script) == [
        ('"$current"', "failed"),  # --rollback: the previous images are not healthy
        ('"$current"', '"rolled back to $previous"'),
        ('"${deployed:-none}"', "failed"),
        ('"$target"', "failed"),  # git reset
        ('"$target"', "failed"),  # build
        ('"$target"', "failed"),  # migrate
        ('"$target"', "deployed"),
        ('"$target"', '"rolled back to $current"'),
        ('"$target"', "failed"),  # not healthy, and nothing to roll back to
        ('"$target"', "failed"),  # plain up
        ('"$target"', "unchanged"),
    ]
    assert 'printf \'%s %s\\n\' "$1" "$2"' in script
    # Nothing else on stdout: every other line goes to stderr (the journal still has it).
    assert all(line.endswith(">&2") for line in re.findall(r"\becho .*", script))
    assert re.findall(r"printf .*", script) == [
        'printf \'%s %s\\n\' "$1" "$2"',
        'printf \'%s\\n%s\\n\' "$1" "$2" >"$IMAGES_FILE.tmp"',
        'printf \'%s\\n\' "$target" >"$DEPLOYED_FILE.tmp"',
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
    build = script.index('KAAGAZ_TAG="$target" docker compose "${COMPOSE[@]}" build;')
    low = script.index("  free_gb=\"$(df --output=avail -BG / | tail -n 1 | tr -dc '0-9' || true)\"\n")
    check = script.index('  if [[ -n "$free_gb" ]] && ((free_gb < MIN_FREE_GB)); then\n    echo "low disk: ')
    before = script.index("    prune_build_cache\n  fi\n")
    after = script.index(
        '    prune_images "$target" "$previous"\n    prune_build_cache\n    report "$target" deployed'
    )
    assert script.index("git reset ") < low < check < before < build < after


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
