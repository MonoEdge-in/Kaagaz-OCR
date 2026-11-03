"""The EC2 server scripts and systemd units in infra/ec2 (EC2 PRD, WP-E).

The files are read as plain text, as tests/test_compose_prod.py reads the
compose files. What the scripts do when run (a new commit, a failed build or
migration, a failed health check, a rollback, an unchanged commit, a held lock,
a failed dump) is checked by hand with stub commands or in a throwaway Linux
container; the pull request has the steps.
"""

import re
from pathlib import Path

from tests.test_compose_prod import PROD, services

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
    for name in ("deploy.sh", "pgdump.sh", "idle-stop.sh", "disk-alert.sh"):
        script = text(name)
        files = re.findall(r"-f (docker-compose[\w.]*)", script)
        assert files == ["docker-compose.yml", "docker-compose.prod.yml"], name
        assert "--profile" not in script and "PROFILES" not in script, name


def deploy_steps() -> str:
    """deploy.sh's new-commit branch, where it builds, migrates and switches."""
    script = text("deploy.sh")
    start = script.index('\nif [[ "$target" != "$deployed" && "$target" != "$rolled_back" ]]; then\n')
    return script[start : script.index("\nfi\n", start)]


def code(script: str) -> str:
    """A script without its comment lines."""
    return "\n".join(line for line in script.splitlines() if not line.lstrip().startswith("#"))


SWITCH = (
    f'  if tag_images "$target" local && {DC} up -d --no-deps "${{INFRA[@]}}" "${{APPS[@]}}" &&\n'
    "    wait_for_app; then\n"
)


def test_the_service_lists_are_every_compose_service_but_migrate() -> None:
    script = text("deploy.sh")
    infra = re.findall(r"^INFRA=\((.*)\)$", script, re.MULTILINE)
    apps = re.findall(r"^APPS=\((.*)\)$", script, re.MULTILINE)
    assert apps == ["api worker frontend"] and len(infra) == 1
    listed = infra[0].split() + apps[0].split()
    assert sorted([*listed, "migrate"]) == sorted(
        services(PROD)
    )  # every service, read from the compose files


def test_no_up_runs_migrate_through_depends_on() -> None:
    """24 Oct 2026: an `up -d` that reaches migrate runs it with :local; after a rollback over a
    migration that older image fails on the newer database and leaves the app stopped."""
    lines = code(text("deploy.sh"))
    ups = re.findall(r"up -d.*", lines)
    assert ups == [
        'up -d --no-deps "${APPS[@]}" && wait_for_app',  # roll_back_to
        'up -d --no-deps "${INFRA[@]}" "${APPS[@]}" &&',  # the switch
        'up -d --no-deps "${INFRA[@]}" "${APPS[@]}"; then',  # every other run
    ]
    assert not re.search(r"up -d *($|;|&&)", lines)  # no bare up -d
    assert "--remove-orphans" not in lines  # a service the release before needs is never removed
    # Only step 2 runs migrate, from the new image.
    assert [line.strip() for line in re.findall(r".*migrate.*", lines)] == [
        f'if ! KAAGAZ_TAG="$target" {DC} run --rm -T migrate; then',
        'echo "migrate failed; site still on ${current:-the running images}" >&2',
    ]


def test_deploy_builds_commit_images_and_migrates_before_touching_the_app() -> None:
    """24 Oct 2026: migrate failed after api, worker and frontend were recreated, and the site went down."""
    steps = deploy_steps()
    build = steps.index(f'  if ! KAAGAZ_TAG="$target" {DC} build; then\n')
    migrate = steps.index(f'  if ! KAAGAZ_TAG="$target" {DC} run --rm -T migrate; then\n')
    assert steps.index("git reset ") < build < migrate < steps.index(SWITCH)
    # Nothing before the migration starts, recreates or retags a running container.
    before = steps[:migrate]
    assert "up -d" not in before and "roll_back_to" not in before and 'tag_images "$target"' not in before
    assert "--build" not in text("deploy.sh")  # compose never rebuilds the running images in place


def test_a_failed_build_or_migrate_stops_before_the_switch() -> None:
    steps = deploy_steps()
    failed_build = (
        f'  if ! KAAGAZ_TAG="$target" {DC} build; then\n    report "$target" failed\n    exit 1\n  fi\n'
    )
    failed_migrate = (
        f'  if ! KAAGAZ_TAG="$target" {DC} run --rm -T migrate; then\n'
        '    echo "migrate failed; site still on ${current:-the running images}" >&2\n'
        '    report "$target" failed\n'
        "    exit 1\n"
        "  fi\n"
    )
    assert failed_build in steps and failed_migrate in steps
    assert steps.index(failed_migrate) < steps.index('tag_images "$target" local')
    # Not recorded, so the next run tries the commit again; the site was never touched.
    assert steps.index(failed_migrate) < steps.index('save_state "$target"')


def test_a_failed_health_check_holds_the_commit_and_rolls_back() -> None:
    script = text("deploy.sh")
    assert "\nHEALTH_TIMEOUT_SECONDS=180\n" in script
    # #477: the website too, so a broken frontend is never reported deployed.
    assert (
        "wait_for_app() {\n"
        "  local waited=0\n"
        "  until healthy api && healthy frontend; do\n"
        "    if ((waited >= HEALTH_TIMEOUT_SECONDS)); then\n"
        "      return 1\n"
        "    fi\n"
    ) in script
    assert f'  id="$({DC} ps -q "$1")" || return 1\n' in script
    assert "docker inspect -f '{{.State.Health.Status}}' \"$id\"" in script
    assert (
        "roll_back_to() {\n"
        f'  tag_images "$1" local && {DC} up -d --no-deps "${{APPS[@]}}" && wait_for_app\n'
        "}\n"
    ) in script
    steps = deploy_steps()
    # Held before the rollback starts: if the run is cut short, the next one puts :local back.
    hold = '  printf \'%s\\n\' "$target" >"$ROLLED_BACK_FILE"\n'
    rollback = (
        '  if [[ -n "$current" ]] && roll_back_to "$current"; then\n'
        '    report "$target" "rolled back to $current"\n'
        "    exit 1\n"
        "  fi\n"
    )
    assert steps.index(SWITCH) < steps.index(hold) < steps.index(rollback)
    assert '\nif [[ "$target" != "$deployed" && "$target" != "$rolled_back" ]]; then\n' in script


def test_the_deployed_record_is_written_only_after_a_health_check_passes() -> None:
    script = text("deploy.sh")
    assert "\nDEPLOYED_FILE=/home/ubuntu/.kaagaz-deployed\n" in script
    assert "\nIMAGES_FILE=/home/ubuntu/.kaagaz-images\n" in script
    assert "\nROLLED_BACK_FILE=/home/ubuntu/.kaagaz-rolled-back\n" in script
    assert (
        "save_state() {\n"
        '  printf \'%s\\n%s\\n\' "$1" "$2" >"$IMAGES_FILE.tmp"\n'
        '  mv -f "$IMAGES_FILE.tmp" "$IMAGES_FILE"\n'
        '  printf \'%s\\n\' "$1" >"$DEPLOYED_FILE.tmp"\n'
        '  mv -f "$DEPLOYED_FILE.tmp" "$DEPLOYED_FILE"\n'
        "}\n"
    ) in script
    assert script.count('>"$DEPLOYED_FILE') == 1 and script.count('>"$IMAGES_FILE') == 1  # only there
    # Each save_state follows a passed health check.
    assert re.findall(r"save_state \".*", script) == [
        'save_state "$previous" "$current"',  # --rollback, after roll_back_to (ends in wait_for_app)
        'save_state "$current" ""',  # first run, api healthy
        'save_state "$target" "$previous"',  # the switch passed wait_for_app
    ]
    rollback = script[script.index('  if ! roll_back_to "$previous"; then\n') :]
    assert rollback.index("    exit 1\n") < rollback.index('save_state "$previous" "$current"')
    assert (
        '    if [[ -n "$deployed" ]] && healthy api && tag_images local "$deployed"; then\n'
        '      current="$deployed"\n'
        '      save_state "$current" ""\n'
        "    else\n"
        '      echo "no previous release to roll back to: no $DEPLOYED_FILE, or api is not healthy" >&2\n'
    ) in script
    assert (
        SWITCH + '    if [[ "$current" != "$target" ]]; then\n'
        '      previous="$current"\n'
        "    fi\n"
        '    save_state "$target" "$previous"\n'
        '    prune_images "$target" "$previous"\n'
        "    prune_build_cache\n"
        '    report "$target" deployed\n'
    ) in deploy_steps()


def test_every_other_run_puts_local_back_on_the_recorded_live_images_first() -> None:
    """Undoes a hand `dc build` and finishes a rollback that was cut short."""
    script = text("deploy.sh")
    assert '{ read -r current && read -r previous; } <"$IMAGES_FILE" || true' in script
    rest = script[script.index("\n# :local follows the recorded live images") :]
    retag = (
        '\nif [[ -n "$current" ]] && ! tag_images "$current" local; then\n'
        '  report "$target" failed\n'
        "  exit 1\n"
        "fi\n"
    )
    up = f'\nif ! {DC} up -d --no-deps "${{INFRA[@]}}" "${{APPS[@]}}"; then\n'
    held = (
        '\nif [[ "$target" == "$rolled_back" && "$target" != "$deployed" ]]; then\n  report "$target" held\n'
    )
    assert rest.index(retag) < rest.index(up) < rest.index(held)


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
    assert branch.index('roll_back_to "$previous"') < branch.index('save_state "$previous" "$current"')
    # The production commit the checkout is on is held, so the timer does not deploy it again.
    assert branch.index('git rev-parse HEAD >"$ROLLED_BACK_FILE"') < branch.index("save_state")
    assert branch.endswith('  report "$current" "rolled back to $previous"\n  exit 0')
    assert start < script.index("git fetch")  # the checkout and the database are left alone
    assert "migrate" not in branch and "git fetch" not in branch and "git reset" not in branch
    lines = code(script).lower()
    assert "downgrade" not in lines and "alembic" not in lines  # no schema change but compose's own migrate


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
        ('"$target"', "failed"),  # retag to the live images
        ('"$target"', "failed"),  # up
        ('"$target"', "held"),  # a rolled-back commit
        ('"$target"', "unchanged"),
    ]
    assert 'printf \'%s %s\\n\' "$1" "$2"' in script
    # Nothing else on stdout: every other line goes to stderr (the journal still has it).
    assert all(line.endswith(">&2") for line in re.findall(r"\becho .*", script))
    assert re.findall(r"printf .*", script) == [
        'printf \'%s %s\\n\' "$1" "$2"',
        'printf \'%s\\n%s\\n\' "$1" "$2" >"$IMAGES_FILE.tmp"',
        'printf \'%s\\n\' "$1" >"$DEPLOYED_FILE.tmp"',
        'printf \'%s\\n\' "$target" >"$ROLLED_BACK_FILE"',
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
    assert 'bucket="$(env_value BACKUP_BUCKET)"' in script and 'cert="$(env_value BACKUP_CERT)"' in script
    printed = re.findall(r"^ *(?:echo|printf) .*", script, re.MULTILINE)
    assert printed == [
        "  printf '%s' \"$value\"",  # env_value's result, caught by $(...) and never shown
        '  echo "a deploy held $LOCK_FILE for ${LOCK_WAIT_SECONDS}s; no dump saved" >&2',
        '  echo "pg_dump wrote nothing; no dump saved" >&2',
        'echo "saved $dump"',
        '  echo "BACKUP_BUCKET or BACKUP_CERT is not set in .env; nothing copied off the server" >&2',
        'printf \'%s\\n\' "$day" | send "done"',  # the day, never a setting
        'echo "copied the backup of $day off the server"',
    ]


def test_pgdump_copies_three_encrypted_parts_off_the_server_then_done() -> None:
    """#476: the dump, the logins and every stored file, each encrypted to BACKUP_CERT
    (openssl cms, the private key offline) and streamed to the bucket; done goes last."""
    script = text("pgdump.sh")
    # The whole run holds the deploy lock, so no deploy or idle stop cuts it.
    assert "\nLOCK_FILE=/tmp/kaagaz-deploy.lock\n" in script
    assert script.index('exec 9>"$LOCK_FILE"') < script.index('if ! flock -w "$LOCK_WAIT_SECONDS" 9; then')
    assert code(script).index("flock -w") < code(script).index("docker ")
    # Without both settings, the local dump only, as before (nothing changes on release day).
    off = script.index('if [[ -z "$bucket" || -z "$cert" ]]; then')
    assert script.index('echo "saved $dump"') < off < script.index("seal kaagaz.dump.cms")
    assert '  openssl cms -encrypt -binary -aes256 -stream -outform DER "$cert" | send "$1"\n' in script
    assert (
        "  docker run --rm -i --network host kaagaz-backend:local \\\n"
        '    python -m kaagaz.cli backup-upload --bucket "$bucket" --key "$day/$1"\n'
    ) in script
    steps = [
        'seal kaagaz.dump.cms <"$dump"',
        f"{DC} exec -T postgres \\\n"
        '  pg_dumpall -U "$pg_user" --roles-only --no-role-passwords | seal roles.sql.cms',
        f"{DC} run --rm -T --no-deps worker \\\n  python -m kaagaz.cli backup-files | seal files.tar.cms",
        'printf \'%s\\n\' "$day" | send "done"',
    ]
    places = [script.index(step) for step in steps]
    assert places == sorted(places)
    assert "--no-deps worker" in script  # a plain run would start migrate (deploy.sh, 24 Oct 2026)


def test_the_restore_drill_restores_into_throwaways_and_checks_through_the_customer_filter() -> None:
    """#476: the versions compose runs, roles before the dump, files into an empty bucket, and
    the check as the app role with one customer set (CLAUDE.md: no query without the filter)."""
    script = text("restore-drill.sh")
    assert script.startswith("#!/usr/bin/env bash\n") and "\nset -euo pipefail\n" in script
    compose = (ROOT / "docker-compose.yml").read_text(encoding="utf-8")
    for image in ("pgvector/pgvector:0.8.7-pg17", "chrislusf/seaweedfs:4.48"):
        assert f"image: {image}\n" in compose and f"={image}\n" in script
    assert "\ntrap cleanup EXIT\n" in script and 'rm -rf "$work"' in script
    assert 'aws s3 cp --quiet "s3://$bucket/$day/done" "$work/done"' in script
    assert script.index("/done") < script.index("for part in kaagaz.dump roles.sql files.tar; do")
    assert 'openssl cms -decrypt -binary -inform DER -inkey "$key" -out "$work/$part"' in script
    roles = script.index('psql -q -U "$superuser" -d postgres <"$work/roles.sql"')
    restore = script.index('pg_restore -U "$superuser" -d postgres --create --exit-on-error /tmp/kaagaz.dump')
    files = script.index("python -m kaagaz.cli restore-files <")
    assert roles < restore < files
    assert "--no-owner" not in script and "--no-acl" not in script  # owner and grants as they were
    check = script[script.index("<<'SQL'\n") : script.index("\nSQL\n")]
    assert check.index("SET LOCAL ROLE kaagaz_app;") < check.index("set_config('kaagaz.customer_id'")
    assert check.index("set_config('kaagaz.customer_id'") < check.index("FROM documents")
    assert "-p " not in code(script)  # no host port: the throwaways meet on their own network
    assert script.rstrip().endswith('echo "DRILL PASS $day"')


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


def test_the_disk_alert_emails_over_80_percent_at_most_once_a_day() -> None:
    """#480: no CloudWatch agent; the backend's own SES mailer sends it."""
    script = text("disk-alert.sh")
    assert script.startswith("#!/usr/bin/env bash\n") and "\nset -euo pipefail\n" in script
    assert "\nLIMIT_PERCENT=80\n" in script and "\nSENT_FILE=/home/ubuntu/.kaagaz-disk-alert\n" in script
    assert "used=\"$(df --output=pcent / | tail -n 1 | tr -dc '0-9')\"" in script
    send = f'{DC} run --rm -T --no-deps worker \\\n  python -m kaagaz.cli disk-alert --used "$used"\n'
    skip = 'if [[ -f "$SENT_FILE" && "$(cat "$SENT_FILE")" == "$day" ]]; then'
    record = 'printf \'%s\\n\' "$day" >"$SENT_FILE"'
    assert script.index("((used <= LIMIT_PERCENT))") < script.index(skip) < script.index(send)
    assert script.index(send) < script.index(record)  # recorded only once it was sent
    assert section(unit("kaagaz-disk.service"), "Service") == {
        "Service.Type": "oneshot",
        "Service.User": "ubuntu",
        "Service.WorkingDirectory": "/home/ubuntu/kaagaz",
        "Service.ExecStart": "/home/ubuntu/kaagaz/infra/ec2/disk-alert.sh",
    }
    timer = unit("kaagaz-disk.timer")
    assert section(timer, "Timer") == {"Timer.OnBootSec": "15min", "Timer.OnUnitActiveSec": "1h"}
    assert section(timer, "Install") == {"Install.WantedBy": "timers.target"}
