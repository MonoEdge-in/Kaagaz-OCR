"""PRD 7 phase 1 (#583): every API request runs its database work as the signed-in person,
``Database.user``, so the per-person access rule of the later phases always knows who asks.

Every file of kaagaz/api is read with ``ast``. On a database (``db``, ``context.db``,
``self._db``, ``database``):

* ``.customer`` and ``.system`` (no person), called or only named, appear only in the reviewed
  places of ALLOWED, exactly as many times as listed, each with why no signed-in person acts.
* ``.user(...)`` names the signed-in user: ``(user.customer_id, user.user_id)``, or
  ``(customer_id, user.user_id)`` with ``customer_id`` bound only from ``user.customer_id``,
  where ``user`` is the route's signed-in user parameter and is never rebound. Any other shape,
  for example ``(user.customer_id, target_user_id)``, is in USER_EXCEPTIONS with its reason.

It covers kaagaz/api call sites only. The shared code a request reaches, the Ask service and
search, PgDocuments and the usage ledger, is test_ask_as_person_guard.py's and
test_ask_as_person_db.py's (PR 2, #585).
"""

import ast
import re
from collections import Counter
from pathlib import Path

import kaagaz

API = Path(kaagaz.__file__).parent / "api"
DATABASE = re.compile(r"(?:^|[._])(?:db|database)$")  # db, context.db, self._db, database

# "file under kaagaz/api:innermost function:method" → (how many, why no signed-in person acts).
ALLOWED: dict[str, tuple[int, str]] = {
    "app.py:unsafe_config:system": (1, "start-up: whether the login is a superuser, before any request"),
    "auth.py:load_user:customer": (1, "finds whom a session or sign-in names; not proven until it returns"),
    "auth.py:feature_on:system": (1, "sign-up's switch (no user): the platform's alone"),
    "emails.py:new_link:customer": (1, "a link for someone not signed in: sign-up, forgot password"),
    "emails.py:notify:customer": (1, "after the reply, the recipient's address; often not the actor"),
    "routers/admin.py:create_user:system": (1, "frees a stale sign-up's e-mail in any organisation (0063)"),
    "routers/admin.py:tell_admins:customer": (1, "after the reply, the admins' addresses; recipients only"),
    "routers/admin.py:approve_identity:system": (1, "whether the e-mail signs in a user of any organisation"),
    "routers/auth.py:admin_enrol:customer": (2, "two-step set-up from the enrol token, before a session"),
    "routers/auth.py:signup:system": (2, "sign-up: creates the organisation, no person yet"),
    "routers/auth.py:signup:customer": (2, "sign-up: the new admin's link and first read, before a session"),
    "routers/auth.py:_check_password:system": (1, "sign-in: finds the account by e-mail, any organisation"),
    "routers/auth.py:_account:customer": (1, "sign-in and code attempts on an account not yet proven"),
    "routers/email.py:confirm_email:customer": (1, "e-mail link: the link names the user, nobody signed in"),
    "routers/email.py:_send_link:system": (1, "forgot password: finds the account by e-mail, anywhere"),
    "routers/email.py:_send_link:customer": (1, "forgot password: the found user's address, not signed in"),
    "routers/email.py:_set_password:customer": (1, "reset or invite link: the link's user, nobody signed in"),
    "routers/email.py:_open_link:system": (1, "finds a link by its token, any organisation"),
    "routers/oidc.py:_sign_in:system": (1, "Google sign-in: finds the connection, any organisation"),
    "routers/oidc.py:_sign_in:customer": (3, "Google sign-in: the found user, before a session"),
    "routers/oidc.py:_connect:system": (1, "whether this Google account is connected in any organisation"),
    # Changed on purpose (PRD 7, #605): a locked files' Google confirm finds the account by its key.
    "routers/oidc.py:_locked_open:system": (1, "whether this Google account is the person's own connection"),
    "routers/operator_models.py:_plan:customer": (1, "the super admin, not a person of the organisation"),
    "routers/operator_orgs.py:create_org:system": (1, "the super admin frees a stale sign-up's e-mail"),
    # Changed on purpose (#603): Meta's webhook, which no person signs in to.
    # Changed on purpose (W3): the owner-change notice, "Link <code>", BLOCK and "not connected", each a
    # number with no organisation yet; the Google proof, as Google connect's; the Admin's save's sweep
    # and "WhatsApp was turned on" to Management and the admins, after the reply.
    "routers/whatsapp.py:receive:system": (2, "WhatsApp: a number's connection, owner change, Link, BLOCK"),
    "routers/oidc.py:_whatsapp_proof:system": (1, "WhatsApp's Google proof: finds the connection, anywhere"),
    "routers/whatsapp_account.py:save_whatsapp:system": (1, "the WhatsApp sweep's owner-rights function"),
    "routers/whatsapp_account.py:_tell_leaders:customer": (
        1,
        "after the reply, Management's and admins' addresses",
    ),
}

# "file under kaagaz/api:innermost function" → (how many, who the person is) for .user(...) calls
# not in the plain shapes above.
USER_EXCEPTIONS: dict[str, tuple[int, str]] = {
    "auth.py:_signed_in": (1, "the user the checked session names, loaded by load_user"),
    "auth.py:feature_on": (1, "user: require_feature's signed-in user (None: sign-up, on system)"),
    "routers/auth.py:_open_session": (1, "the user whose session starts, just proven by sign-in"),
    "routers/auth.py:_account": (1, "signed_in: the signed-in user's own re-proof (_prove)"),
    "routers/chat.py:_write_memory": (2, "after the reply, the asker: _remember passes user's ids"),
    "routers/documents.py:enqueue": (1, "the uploader: _ReadingQueue's requested_by is user.user_id"),
    "routers/oidc.py:_connect": (2, "the connect's signed-in user (current_user_allow_google_link)"),
    "routers/oidc.py:_locked_open": (1, "the confirm's signed-in user (_flow_user), PRD 7 #605"),
    "routers/words.py:add_my_word": (1, "me is user.user_id"),
    "routers/words.py:my_words_on_page": (1, "me is user.user_id"),
    # Changed on purpose (#603): the person whose connected number sent a signed message.
    "routers/whatsapp.py:receive": (1, "the connection kaagaz_whatsapp_lookup found for the sender"),
}

# The dependencies that give a route its signed-in user (kaagaz/api/auth.py).
SIGNED_IN = {
    "User",
    "CurrentUser",
    "AdminSession",
    "AdminWithTwoStep",
    "Admin",
    "Reviewer",
    "UserAllowGoogleLink",
    "UserAllowPasswordChange",
    "CanDeleteForever",
}
Function = ast.FunctionDef | ast.AsyncFunctionDef


def _signed_in_owner(chain: list[Function]) -> Function | None:
    """The nearest function taking the signed-in ``user`` (a SIGNED_IN annotation) that never
    rebinds it, nor any function between it and the call; else None."""
    for function in reversed(chain):
        params = {arg.arg: arg for arg in [*function.args.args, *function.args.kwonlyargs]}
        if "user" in params:
            annotation = params["user"].annotation
            stored = any(
                isinstance(node, ast.Name) and node.id == "user" and isinstance(node.ctx, ast.Store)
                for node in ast.walk(function)
            )
            ok = annotation is not None and ast.unparse(annotation) in SIGNED_IN and not stored
            return function if ok else None
    return None


def _from_user(owner: Function, chain: list[Function]) -> bool:
    """``customer_id`` is no parameter, and every binding of it in ``owner`` is ``user.customer_id``."""
    if any("customer_id" in {arg.arg for arg in [*f.args.args, *f.args.kwonlyargs]} for f in chain):
        return False
    stores = [
        node
        for node in ast.walk(owner)
        if isinstance(node, ast.Name) and node.id == "customer_id" and isinstance(node.ctx, ast.Store)
    ]
    bound: dict[int, str] = {}  # id of an assigned name → its value, for plain and tuple assignments
    for node in ast.walk(owner):
        if isinstance(node, ast.Assign) and len(node.targets) == 1:
            target, value = node.targets[0], node.value
            if isinstance(target, ast.Tuple) and isinstance(value, ast.Tuple):
                bound.update((id(n), ast.unparse(v)) for n, v in zip(target.elts, value.elts, strict=True))
            else:
                bound[id(target)] = ast.unparse(value)
    return bool(stores) and all(bound.get(id(name)) == "user.customer_id" for name in stores)


# Changed on purpose (PR 10b): the one keyword that leaves the person as they are (a REPEATABLE READ
# snapshot for the previews, kaagaz.db.engine). Any other keyword, or **kwargs, is refused.
SAME_PERSON_KEYWORDS = {"repeatable_read"}


def _plain_user_call(node: ast.Call, chain: list[Function]) -> bool:
    shape = [ast.unparse(arg) for arg in node.args]
    owner = _signed_in_owner(chain)
    if any(keyword.arg not in SAME_PERSON_KEYWORDS for keyword in node.keywords) or owner is None:
        return False
    if shape == ["user.customer_id", "user.user_id"]:
        return True
    return shape == ["customer_id", "user.user_id"] and _from_user(owner, chain)


class _Sites(ast.NodeVisitor):
    """``found``: every ``.customer``/``.system`` on a database, by ALLOWED's key; ``odd``: every
    ``.user`` on a database not called in a plain shape, by USER_EXCEPTIONS's key."""

    def __init__(self, file: str) -> None:
        self.file = file
        self.chain: list[Function] = []
        self.found: list[tuple[str, int]] = []
        self.odd: list[tuple[str, int]] = []
        self.plain: set[int] = set()  # ids of .user attributes called in a plain shape

    def _where(self) -> str:
        return f"{self.file}:{self.chain[-1].name if self.chain else '<module>'}"

    def visit_FunctionDef(self, node: Function) -> None:
        self.chain.append(node)
        self.generic_visit(node)
        self.chain.pop()

    visit_AsyncFunctionDef = visit_FunctionDef

    def visit_Call(self, node: ast.Call) -> None:
        func = node.func
        if isinstance(func, ast.Attribute) and func.attr == "user" and _plain_user_call(node, self.chain):
            self.plain.add(id(func))
        self.generic_visit(node)

    def visit_Attribute(self, node: ast.Attribute) -> None:
        if DATABASE.search(ast.unparse(node.value)):
            if node.attr in ("customer", "system"):
                self.found.append((f"{self._where()}:{node.attr}", node.lineno))
            elif node.attr == "user" and id(node) not in self.plain:
                self.odd.append((self._where(), node.lineno))
        self.generic_visit(node)


def scan() -> tuple[list[tuple[str, int]], list[tuple[str, int]]]:
    found: list[tuple[str, int]] = []
    odd: list[tuple[str, int]] = []
    for path in sorted(API.rglob("*.py")):
        visitor = _Sites(path.relative_to(API).as_posix())
        visitor.visit(ast.parse(path.read_text(encoding="utf-8")))
        found += visitor.found
        odd += visitor.odd
    return found, odd


def _differences(sites: list[tuple[str, int]], expected: dict[str, tuple[int, str]]) -> list[str]:
    """One line per key whose count differs from the reviewed one (0 when unlisted)."""
    counted = Counter(key for key, _ in sites)
    reviewed = Counter({key: count for key, (count, _) in expected.items()})
    return [
        f"kaagaz/api/{key}: {counted[key]} found (lines {[n for k, n in sites if k == key]}),"
        f" {reviewed[key]} reviewed"
        for key in sorted(set(counted) | set(reviewed))
        if counted[key] != reviewed[key]
    ]


def test_the_api_opens_no_transaction_without_the_person_outside_the_reviewed_places() -> None:
    found, _ = scan()
    wrong = _differences(found, ALLOWED)
    assert not wrong, (
        "Open these as the signed-in person, context.db.user(user.customer_id, user.user_id), or,"
        " where no signed-in person acts, review the count in ALLOWED with the reason:\n" + "\n".join(wrong)
    )


def test_every_person_transaction_names_the_signed_in_user() -> None:
    _, odd = scan()
    wrong = _differences(odd, USER_EXCEPTIONS)
    assert not wrong, (
        "Open these with the signed-in user's own ids, (user.customer_id, user.user_id), or, where"
        " the person is that user by another name, review the count in USER_EXCEPTIONS:\n" + "\n".join(wrong)
    )


def test_only_the_repeatable_read_keyword_keeps_a_call_plain() -> None:
    """Changed on purpose (PR 10b): ``repeatable_read=True`` keeps ``.user(...)`` plain; any other
    keyword, or ``**kwargs``, makes it one to review."""
    source = (
        "def route(user: User, context: Context) -> None:\n"
        "    context.db.user(user.customer_id, user.user_id, repeatable_read=True)\n"
        "    context.db.user(user.customer_id, user.user_id, as_of=1)\n"
        "    context.db.user(user.customer_id, user.user_id, **extra)\n"
    )
    visitor = _Sites("example.py")
    visitor.visit(ast.parse(source))
    assert visitor.odd == [("example.py:route", 3), ("example.py:route", 4)]
