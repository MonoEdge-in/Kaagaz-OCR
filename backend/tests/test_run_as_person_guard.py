"""PRD 7 phase 1 (#583): every API request runs its database work as the signed-in person,
``Database.user``, so the per-person access rule of the later phases always knows who asks.

Every file of kaagaz/api is read with ``ast``. A ``.customer(...)`` or ``.system(...)`` call
(``Database.customer``, ``Database.system``: no person) may appear only in the reviewed
places below, each with why no signed-in person acts there. A new one fails, named.
"""

import ast
from pathlib import Path

import kaagaz

API = Path(kaagaz.__file__).parent / "api"

# "file under kaagaz/api:innermost function:method" → why it opens no person's transaction.
ALLOWED: dict[str, str] = {
    "app.py:unsafe_config:system": "start-up: whether the login is a superuser, before any request",
    "auth.py:load_user:customer": "finds whom a session or sign-in names; not proven until it returns",
    "auth.py:feature_on:system": "sign-up's switch (no user): the platform's alone",
    "emails.py:new_link:customer": "a link for the e-mail's recipient, not signed in (sign-up, reset)",
    "emails.py:notify:customer": "after the reply, the recipient's address; often not the actor",
    "routers/admin.py:create_user:system": "frees a stale sign-up's e-mail in any organisation (0063)",
    "routers/admin.py:tell_admins:customer": "after the reply, the admins' addresses; recipients only",
    "routers/admin.py:approve_identity:system": "whether the e-mail signs in a user of any organisation",
    "routers/auth.py:admin_enrol:customer": "two-step set-up from the enrol token, before a session",
    "routers/auth.py:signup:system": "sign-up: creates the organisation, no person yet",
    "routers/auth.py:signup:customer": "sign-up: the new admin's link and first read, before a session",
    "routers/auth.py:_check_password:system": "sign-in: finds the account by e-mail, any organisation",
    "routers/auth.py:_begin:customer": "counts an attempt on the account tried, before it is proven",
    "routers/auth.py:_code_problem:customer": "checks the two-step code of the account tried",
    "routers/auth.py:_succeeded:customer": "restarts the attempt count of the account tried",
    "routers/auth.py:_audit_failure:customer": "audits a failed attempt; it has no actor",
    "routers/email.py:confirm_email:customer": "e-mail link: the link names the user, nobody signed in",
    "routers/email.py:_send_link:system": "forgot password: finds the account by e-mail, any organisation",
    "routers/email.py:_send_link:customer": "forgot password: the found user's address, nobody signed in",
    "routers/email.py:_set_password:customer": "reset or invite link: the link's user, nobody signed in",
    "routers/email.py:_open_link:system": "finds a link by its token, any organisation",
    "routers/oidc.py:_sign_in:system": "Google sign-in: finds the connection, any organisation",
    "routers/oidc.py:_sign_in:customer": "Google sign-in: the found user, before a session",
    "routers/oidc.py:_connect:system": "whether this Google account is connected in any organisation",
    "routers/operator_models.py:_plan:customer": "the super admin, not a person of the organisation",
    "routers/operator_orgs.py:create_org:system": "the super admin frees a stale sign-up's e-mail (0063)",
}


class _Sites(ast.NodeVisitor):
    """Each ``<anything>.customer(...)`` and ``<anything>.system(...)`` call, keyed as ALLOWED is
    (its innermost function), with its line."""

    def __init__(self, file: str) -> None:
        self.file, self.functions = file, ["<module>"]
        self.found: list[tuple[str, int]] = []

    def visit_FunctionDef(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
        self.functions.append(node.name)
        self.generic_visit(node)
        self.functions.pop()

    visit_AsyncFunctionDef = visit_FunctionDef

    def visit_Call(self, node: ast.Call) -> None:
        if isinstance(node.func, ast.Attribute) and node.func.attr in ("customer", "system"):
            self.found.append((f"{self.file}:{self.functions[-1]}:{node.func.attr}", node.lineno))
        self.generic_visit(node)


def sites() -> list[tuple[str, int]]:
    found: list[tuple[str, int]] = []
    for path in sorted(API.rglob("*.py")):
        visitor = _Sites(path.relative_to(API).as_posix())
        visitor.visit(ast.parse(path.read_text(encoding="utf-8")))
        found += visitor.found
    return found


def test_the_api_opens_no_transaction_without_the_person_outside_the_reviewed_places() -> None:
    new = [f"kaagaz/api/{key} (line {line})" for key, line in sites() if key not in ALLOWED]
    assert not new, (
        "Open these as the signed-in person, context.db.user(user.customer_id, user.user_id),"
        " or, where no signed-in person acts, add them to ALLOWED with the reason:\n" + "\n".join(new)
    )


def test_every_reviewed_place_still_opens_one() -> None:
    """The list stays exact: a place that no longer opens one leaves it."""
    seen = {key for key, _ in sites()}
    assert [key for key in ALLOWED if key not in seen] == []
