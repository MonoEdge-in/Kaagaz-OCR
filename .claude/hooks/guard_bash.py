#!/usr/bin/env python3
"""PreToolUse guard for shell commands.

It denies what this repository never allows, and asks first before risky commands.

Denied outright:
- anything that creates commits or rewrites history (commit, push, merge, rebase,
  cherry-pick, revert, am, reset --hard, filter-branch, filter-repo). People commit, Claude doesn't.
- recursive deletes of the project, a home directory, a drive or a system directory
- piping a download straight into an interpreter, and writing to a raw disk

Asked first:
- discarding work (git clean -f, git restore, git checkout --, git branch -D, stash drop)
- dependency changes (uv add/remove, pip install and friends)
- deleting local data (docker compose down -v, docker volume rm, prune, alembic downgrade,
  DROP, TRUNCATE, DELETE without WHERE)
- cloud and publishing commands (aws, terraform apply/destroy, docker push, gh release), sudo

Reads the pending tool call as JSON on stdin and prints a decision as JSON. Makes no
network calls and touches no files. If the input can't be parsed, it allows the call.
"""

import json
import re
import shlex
import sys

COMMITTING = {
    "commit",
    "push",
    "merge",
    "rebase",
    "cherry-pick",
    "revert",
    "am",
    "filter-branch",
    "filter-repo",
}
GIT_OPTIONS_WITH_VALUE = {"-C", "-c", "--git-dir", "--work-tree", "--namespace"}
WRAPPERS = {"sudo", "doas", "env", "time", "nohup", "nice", "command", "exec", "xargs", "stdbuf"}
DANGEROUS_RM_TARGETS = re.compile(
    r"^(/|/\*|~|~/|~/\*|\$HOME/?\*?|\$\{HOME\}/?|\.|\./|\./\*|\.\.|\.\./|\*|\.\*|"
    r"[A-Za-z]:[\\/]?\*?|/[a-z]/?\*?|"
    r"/(bin|boot|dev|etc|home|lib|lib64|opt|proc|root|sbin|srv|sys|usr|var|Users|System|Library)/?\*?)$"
)
PIPE_TO_INTERPRETER = re.compile(
    r"\b(curl|wget|iwr|irm|invoke-webrequest|invoke-restmethod)\b[^\n]*\|\s*(sudo\s+)?"
    r"((ba|z|k|da)?sh|python3?|node|perl|ruby|iex|invoke-expression)\b",
    re.IGNORECASE,
)
RAW_DISK = re.compile(
    r"(\bof=/dev/|>\s*/dev/(sd[a-z]|nvme\d|hd[a-z]|disk\d)|\bmkfs\b|\bformat\s+[A-Za-z]:)", re.I
)
SQL_DESTRUCTIVE = re.compile(
    r"\b(drop\s+(database|schema|table)|truncate\s+(table\s+)?[\w.\"]+)", re.I
)
SQL_DELETE_ALL = re.compile(r"\bdelete\s+from\s+[\w.\"]+\s*(;|$|[\"'])", re.I)


def segments(command: str) -> list[str]:
    """Split a command line into simple commands, including $(...) and backtick substitutions."""
    parts = re.split(r"\|\||&&|;|\n|\|", command)
    parts += re.findall(r"\$\(([^()]*)\)", command) + re.findall(r"`([^`]*)`", command)
    return [part.strip() for part in parts if part.strip()]


def words(segment: str) -> list[str]:
    try:
        tokens = shlex.split(segment, posix=True)
    except ValueError:
        tokens = segment.split()
    # Skip leading VAR=value assignments and wrappers such as sudo or env.
    while tokens and (re.match(r"^[A-Za-z_][A-Za-z0-9_]*=", tokens[0]) or tokens[0] in WRAPPERS):
        tokens = tokens[1:]
        while tokens and tokens[0].startswith("-") and tokens[0] != "--":
            tokens = tokens[1:]  # the wrapper's own flags, e.g. env -i
    return tokens


def program_name(token: str) -> str:
    name = re.split(r"[\\/]", token)[-1].lower()
    return name[:-4] if name.endswith(".exe") else name


def git_subcommand(args: list[str]) -> tuple[str, list[str]]:
    index = 0
    while index < len(args) and args[index].startswith("-"):
        option = args[index]
        index += 2 if option in GIT_OPTIONS_WITH_VALUE else 1
    if index >= len(args):
        return "", []
    return args[index], args[index + 1 :]


def check_git(args: list[str], deny: list[str], ask: list[str]) -> None:
    sub, rest = git_subcommand(args)
    if sub in COMMITTING:
        deny.append(
            f"`git {sub}` creates commits or rewrites history; the person you work with does this"
        )
    elif sub == "reset" and "--hard" in rest:
        deny.append("`git reset --hard` throws away work and rewrites the branch")
    elif sub == "clean" and any(re.match(r"^-[A-Za-z]*f", arg) or arg == "--force" for arg in rest):
        ask.append("`git clean -f` permanently deletes untracked files")
    elif sub == "restore" or (sub == "checkout" and "--" in rest):
        ask.append(f"`git {sub}` can discard uncommitted changes")
    elif sub == "branch" and any(arg in ("-D", "--delete", "-d") for arg in rest):
        ask.append("deletes a branch")
    elif sub == "stash" and rest[:1] in (["drop"], ["clear"]):
        ask.append("deletes stashed work")
    elif sub == "add" and any(arg in ("-f", "--force") for arg in rest):
        ask.append("force-adds ignored files, which may include secrets or local data")


def check_rm(args: list[str], deny: list[str], ask: list[str]) -> None:
    flags = [arg for arg in args if arg.startswith("-") and arg != "--"]
    targets = [arg for arg in args if not arg.startswith("-")]
    recursive = any(
        flag in ("--recursive", "-Recurse") or (not flag.startswith("--") and "r" in flag.lower())
        for flag in flags
    )
    if not recursive:
        return
    for target in targets:
        if DANGEROUS_RM_TARGETS.match(target):
            deny.append(
                f"a recursive delete of '{target}' would destroy the project, "
                "a home directory or the system"
            )
        elif target.rstrip("/\\") in (".git", "./.git"):
            ask.append("deletes the repository's history (.git)")


def check_dependencies(program: str, args: list[str], ask: list[str]) -> None:
    first = args[:1]
    adds = (
        (program == "uv" and (first in (["add"], ["remove"]) or args[:2] == ["pip", "install"]))
        or (program in ("pip", "pip3") and first == ["install"])
        or (program in ("python", "python3", "py") and args[:3] == ["-m", "pip", "install"])
        or (program in ("poetry", "pipenv", "conda") and first in (["add"], ["install"]))
        or (
            program in ("npm", "pnpm", "yarn", "bun")
            and first in (["add"], ["install"], ["i"])
            and len(args) > 1
        )
    )
    if adds:
        ask.append("changes dependencies; Vrushit approves every new library and its licence")


def check_segment(segment: str, deny: list[str], ask: list[str]) -> None:
    tokens = words(segment)
    if not tokens:
        return
    program, args = program_name(tokens[0]), tokens[1:]
    lowered = [arg.lower() for arg in args]

    if segment.lstrip().startswith(("sudo ", "doas ")):
        ask.append("runs with elevated privileges")
    if program == "git":
        check_git(args, deny, ask)
    elif program in ("rm", "remove-item", "del", "rmdir"):
        check_rm(args, deny, ask)
    elif program == "docker":
        if (
            lowered[:1] == ["compose"]
            and "down" in lowered
            and ("-v" in lowered or "--volumes" in lowered)
        ):
            ask.append(
                "`docker compose down -v` deletes the local database, files and Temporal history"
            )
        elif lowered[:2] in (["volume", "rm"], ["volume", "prune"], ["system", "prune"]):
            ask.append("deletes Docker volumes or data")
        elif lowered[:1] == ["push"]:
            ask.append("publishes an image")
    elif program == "alembic" or (
        program == "uv" and "alembic" in lowered and "downgrade" in lowered
    ):
        if "downgrade" in lowered:
            ask.append("`alembic downgrade` drops tables and data")
    elif program == "terraform" and lowered[:1] in (["apply"], ["destroy"], ["import"]):
        ask.append(f"`terraform {lowered[0]}` changes real cloud infrastructure")
    elif program == "aws":
        ask.append("runs an AWS command against a real account")
    elif program == "gh" and lowered[:2] == ["pr", "merge"]:
        deny.append("`gh pr merge` merges on GitHub, which creates commits; Vrushit merges")
    elif program == "gh" and lowered[:2] in (["release", "create"], ["repo", "delete"]):
        ask.append("publishes or deletes on GitHub")
    check_dependencies(program, args, ask)

    if SQL_DESTRUCTIVE.search(segment):
        ask.append(
            "destructive SQL (DROP or TRUNCATE): confirm the database, and that nothing is lost"
        )
    if SQL_DELETE_ALL.search(segment):
        ask.append("a DELETE with no WHERE clause")


def decide(command: str) -> tuple[str, str] | None:
    deny: list[str] = []
    ask: list[str] = []
    if PIPE_TO_INTERPRETER.search(command):
        deny.append(
            "pipes a download straight into an interpreter; download it, read it and check it first"
        )
    if RAW_DISK.search(command):
        deny.append("writes to or formats a raw disk")
    for segment in segments(command):
        check_segment(segment, deny, ask)
    if deny:
        return "deny", (
            "Blocked by guard_bash: "
            + "; ".join(dict.fromkeys(deny))
            + ". Don't work around this. Say what you needed, and let the person run it."
        )
    if ask:
        return "ask", "guard_bash: " + "; ".join(dict.fromkeys(ask))
    return None


def main() -> int:
    try:
        call = json.load(sys.stdin)
    except (json.JSONDecodeError, ValueError):
        print("guard_bash: couldn't read the hook input; allowing the call", file=sys.stderr)
        return 0
    if call.get("tool_name") not in ("Bash", "PowerShell"):
        return 0
    command = (call.get("tool_input") or {}).get("command") or ""
    result = decide(command) if command.strip() else None
    if result:
        decision, reason = result
        print(
            json.dumps(
                {
                    "hookSpecificOutput": {
                        "hookEventName": "PreToolUse",
                        "permissionDecision": decision,
                        "permissionDecisionReason": reason,
                    }
                }
            )
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
