"""The WhatsApp channel never uses the workers' login or role (#603, PRD 7 section 15 "The guard
tests"): the worker sees nothing through PRD 7's views and works for the whole organisation, so
WhatsApp, which answers one person, must never run as it.

Every file of the channel (kaagaz/channel, kaagaz/channel_main.py), its SQL (kaagaz/db/whatsapp.py)
and the webhook (kaagaz/api/routers/whatsapp.py) is read with ``ast``:

* none names the workers' role, setting or login (``db_worker_role``, ``DB_WORKER_ROLE``,
  ``kaagaz_worker``, ``JOBS_DATABASE_URL``);
* none opens an organisation transaction with no person (``.customer`` on a database) or picks one
  (``user_or_customer``): only ``.user``, and ``.system`` for the owner-rights functions;
* the process builds its one Database with ``settings.db_app_role``.
"""

import ast
from pathlib import Path

import kaagaz

PACKAGE = Path(kaagaz.__file__).parent
FILES = sorted(
    [
        *(PACKAGE / "channel").rglob("*.py"),
        PACKAGE / "channel_main.py",
        PACKAGE / "db" / "whatsapp.py",
        PACKAGE / "db" / "whatsapp_chat.py",  # W4: the chat's SQL
        PACKAGE / "db" / "whatsapp_upload.py",  # W5: the uploads' and the folder browser's SQL
        PACKAGE / "api" / "routers" / "whatsapp.py",
    ]
)
WORKER = ("db_worker_role", "DB_WORKER_ROLE", "kaagaz_worker", "JOBS_DATABASE_URL")


def _names(tree: ast.AST) -> list[str]:
    """Every identifier, attribute and string in ``tree``."""
    found: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Name):
            found.append(node.id)
        elif isinstance(node, ast.Attribute):
            found.append(node.attr)
        elif isinstance(node, ast.Constant) and isinstance(node.value, str):
            found.append(node.value)
    return found


def test_the_channel_never_names_the_workers_role_or_login() -> None:
    assert len(FILES) >= 7
    for path in FILES:
        names = _names(ast.parse(path.read_text(encoding="utf-8")))
        assert [name for name in names if any(worker in name for worker in WORKER)] == [], path


def test_the_channel_opens_no_transaction_without_its_person() -> None:
    for path in FILES:
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.Attribute) and node.attr in ("customer", "user_or_customer"):
                raise AssertionError(f"{path.name}:{node.lineno}: {ast.unparse(node)}")


def test_the_channel_process_builds_its_database_in_the_apps_role() -> None:
    tree = ast.parse((PACKAGE / "channel_main.py").read_text(encoding="utf-8"))
    databases = [
        node for node in ast.walk(tree) if isinstance(node, ast.Call) and ast.unparse(node.func) == "Database"
    ]
    assert [ast.unparse(node.args[1]) for node in databases] == ["settings.db_app_role"]


def test_the_channel_has_no_sign_in_policy_switch_the_company_sign_in_is_the_only_one() -> None:
    tree = ast.parse((PACKAGE / "channel_main.py").read_text(encoding="utf-8"))
    channels = [
        node for node in ast.walk(tree) if isinstance(node, ast.Call) and ast.unparse(node.func) == "Channel"
    ]
    assert len(channels) == 1
    assert "keycloak_enabled" not in {keyword.arg for keyword in channels[0].keywords}
