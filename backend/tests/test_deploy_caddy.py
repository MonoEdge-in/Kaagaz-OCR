"""#246 D8: deploy.sh hands Caddy a changed Caddyfile.

Caddy's container mounts infra/caddy/Caddyfile, but a checkout replaces a changed file with a
new one, which a running container never sees: a Caddyfile change used to wait for the next
reboot. Read as text, as tests/test_ec2_scripts.py reads the script; what it does when run (no
record yet, the same file, a changed file, a reload that fails, a deploy, a failed migration,
a rollback, a held commit) was checked with stub commands, and the reload command itself
against a real Caddy (the pull request has the steps).
"""

import re

from tests.test_ec2_scripts import DC, code, deploy_steps, text

RELOAD = (
    "reload_caddy() {\n"
    '  if cmp -s infra/caddy/Caddyfile "$CADDY_FILE"; then\n'
    "    return 0\n"
    "  fi\n"
    f"  if ! {DC} exec -T caddy \\\n"
    "    sh -c 'cat >/tmp/Caddyfile && caddy reload --config /tmp/Caddyfile --adapter caddyfile' \\\n"
    "    <infra/caddy/Caddyfile >/dev/null; then\n"
    '    echo "caddy reload failed; Caddy keeps its previous config" >&2\n'
    "    return 0\n"
    "  fi\n"
    '  cp -f infra/caddy/Caddyfile "$CADDY_FILE" ||'
    ' echo "could not record $CADDY_FILE; caddy reloads again next run" >&2\n'
    '  echo "caddy reloaded the changed Caddyfile" >&2\n'
    "}\n"
)


def test_a_changed_caddyfile_is_reloaded_gracefully_and_recorded_only_once_loaded() -> None:
    script = text("deploy.sh")
    assert "\nCADDY_FILE=/home/ubuntu/.kaagaz-caddyfile\n" in script
    # Compared with the copy last loaded, handed over on stdin (the mounted file may be the old
    # one), reloaded in place (no restart: open connections finish; a file Caddy refuses leaves
    # its running config), and never fatal: every failure is a line on stderr and a return 0.
    assert RELOAD in script


def test_it_runs_once_per_run_after_everything_is_up_and_never_in_a_deploy_or_rollback() -> None:
    script = text("deploy.sh")
    lines = code(script)
    assert re.findall(r"^\s*reload_caddy$", lines, re.MULTILINE) == ["reload_caddy"]
    rest = script[script.index("\n# :local follows the recorded live images") :]
    up = (
        f'\nif ! {DC} up -d --no-deps "${{INFRA[@]}}" "${{APPS[@]}}"; then\n'
        '  report "$target" failed\n  exit 1\nfi\n'
    )
    assert rest.index(up) + len(up) == rest.index("reload_caddy\n")
    assert "reload_caddy" not in deploy_steps()
    rollback = script[
        script.index('\nif [[ "$rollback" == true ]]; then\n  if [[ -z "$previous" ]]; then\n') :
    ]
    assert "reload_caddy" not in rollback[: rollback.index("\nfi\n", rollback.index("  exit 0\n"))]
    # Caddy only: the one exec, into caddy.
    assert re.findall(r"exec -T (\w+)", lines) == ["caddy"]
