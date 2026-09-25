#!/usr/bin/env python3
"""PreToolUse guard: keep secrets out of Claude's context and out of git.

- Read, Edit, Write, NotebookEdit and Grep: deny when the target is a secret file.
- Bash and PowerShell: deny commands that name a secret file; ask before dumping
  every environment variable into the transcript.
- Templates (.env.example, .env.sample, .env.template) are allowed, and so is
  passing a file to --env-file, which doesn't print its contents.

Reads the pending tool call as JSON on stdin and prints a decision as JSON. Makes no
network calls and touches no files. If the input can't be parsed, it allows the call.
"""

import json
import re
import shlex
import sys

TEMPLATE_SUFFIXES = (".example", ".sample", ".template")
SECRET_NAMES = {
    ".envrc",
    ".netrc",
    ".git-credentials",
    ".pypirc",
    "credentials",
    "credentials.json",
    "secrets.json",
    "secrets.yaml",
    "secrets.yml",
    "secrets.toml",
}
SECRET_SUFFIXES = (
    ".pem",
    ".key",
    ".p12",
    ".pfx",
    ".jks",
    ".keystore",
    ".tfstate",
    ".tfstate.backup",
)
KEY_PREFIXES = ("id_rsa", "id_dsa", "id_ecdsa", "id_ed25519")
SECRET_DIRS = {".ssh", ".aws", ".gnupg", ".kube", ".azure", "secrets", ".secrets"}
SERVICE_ACCOUNT = re.compile(r"^service[-_]?account.*\.json$|^serviceaccountkey.*\.json$", re.I)
ENV_DUMP = re.compile(
    r"^\s*(printenv|env|set|export\s+-p|get-childitem\s+env:|gci\s+env:|dir\s+env:|ls\s+env:)\s*$",
    re.I,
)
ADVICE = (
    "Secrets never enter Claude's context or git. If a value is needed, ask the person to set it. "
    "To document a new setting, add a placeholder to .env.example."
)


def is_env_file(name: str) -> bool:
    lower = name.lower()
    if lower == ".env":
        return True
    if lower.startswith(".env."):
        return not lower.endswith(TEMPLATE_SUFFIXES)
    return lower.endswith(".env")


def secret_reason(path: str) -> str | None:
    parts = [
        part
        for part in re.split(r"[\\/]", path.strip().strip("'\""))
        if part not in ("", ".", "..")
    ]
    if not parts:
        return None
    name = parts[-1]
    lower = name.lower()
    if is_env_file(name):
        return f"'{name}' is an environment file that holds secrets"
    if lower in SECRET_NAMES or (lower.startswith(KEY_PREFIXES) and not lower.endswith(".pub")):
        return f"'{name}' is a credentials or key file"
    if lower.endswith(SECRET_SUFFIXES):
        return f"'{name}' looks like a private key, keystore or Terraform state"
    if SERVICE_ACCOUNT.match(name):
        return f"'{name}' looks like a cloud service-account key"
    for folder in parts[:-1]:
        if folder.lower() in SECRET_DIRS:
            return f"'{path}' is inside a secrets folder ('{folder}')"
    return None


def check_command(command: str) -> tuple[str, str] | None:
    if ENV_DUMP.match(command):
        return (
            "ask",
            "dumps every environment variable into the transcript, which may expose secrets",
        )
    spaced = re.sub(r"(\|\||&&|[|;&<>()`])", r" \1 ", command)
    try:
        words = shlex.split(spaced, posix=True)
    except ValueError:
        words = spaced.split()
    skip_next = False
    for word in words:
        if skip_next:
            skip_next = False
            continue
        if word == "--env-file":
            skip_next = True
            continue
        if word.startswith("--env-file="):
            continue
        # A path assigned to a variable or option, such as FILE=.env or --path=.env.
        reason = secret_reason(word.rsplit("=", 1)[-1])
        if reason:
            return "deny", f"the command names a secret file: {reason}"
    return None


def emit(decision: str, reason: str) -> None:
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


def main() -> int:
    try:
        call = json.load(sys.stdin)
    except (json.JSONDecodeError, ValueError):
        print("guard_secrets: couldn't read the hook input; allowing the call", file=sys.stderr)
        return 0
    tool = call.get("tool_name", "")
    tool_input = call.get("tool_input") or {}

    if tool in ("Read", "Edit", "Write", "NotebookEdit", "Grep"):
        for key in ("file_path", "notebook_path", "path", "glob"):
            reason = secret_reason(str(tool_input.get(key) or ""))
            if reason:
                emit("deny", f"Blocked by guard_secrets: {reason}. {ADVICE}")
                return 0
    elif tool in ("Bash", "PowerShell"):
        result = check_command(str(tool_input.get("command") or ""))
        if result:
            decision, reason = result
            prefix = "Blocked by guard_secrets" if decision == "deny" else "guard_secrets"
            emit(decision, f"{prefix}: {reason}. {ADVICE}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
