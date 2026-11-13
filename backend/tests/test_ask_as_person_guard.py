"""PRD 7 phase 1, PR 2 (#585): every transaction the Ask service and search open for a question
runs as the asking person, ``Database.user``, on every thread it runs on.

Every file of kaagaz/answering and kaagaz/search is read with ``ast``. On a database (``db``,
``self._db``):

* ``.customer`` and ``.system`` (no person), called or only named, appear only in the reviewed
  places of ALLOWED, exactly as many times as listed, each with why no signed-in person acts.
* A person's transaction, ``.user(...)`` or ``user_or_customer(db, ...)`` (kaagaz/db/engine.py),
  called or handed to ``partial`` for another thread, names ``(customer_id, user_id)``: both
  parameters of the function it is in, never rebound there, so the person is the one its caller
  passed (``AnswerService.answer``'s ``user_id`` reaches every port, mypy checks the ports'
  signatures). Any other shape is in USER_EXCEPTIONS with its reason.

And on kaagaz/api, whose routes always know the signed-in user (test_run_as_person_guard.py):
``user_or_customer`` is never used there; every ``usage_scope`` saves the ledger as the person
(``as_person=True``); every question goes to the Ask service with the signed-in user's id
(``user_id=user.user_id``), so its transactions run as that person, the streamed ones too.
"""

import ast
from collections import Counter
from pathlib import Path

import kaagaz
from tests.test_run_as_person_guard import DATABASE

ROOT = Path(kaagaz.__file__).parent
PACKAGES = ("answering", "search")
PERSON = "user_or_customer"  # kaagaz.db.engine's: Database.user, or customer with no person

# "file under kaagaz:innermost function:method" → (how many, why no signed-in person acts).
ALLOWED: dict[str, tuple[int, str]] = {
    "search/indexing.py:embed_pieces:customer": (3, "a document's vectors: the reading worker and reembed"),
    "search/indexing.py:embed_missing:system": (1, "the worker's vector sweep lists every customer"),
    "search/indexing.py:embed_missing:customer": (1, "the worker's vector sweep, one customer at a time"),
    "search/story_vectors.py:embed_missing_stories:system": (1, "the worker's story sweep lists the stories"),
}

# "file under kaagaz:innermost function" → (how many, who the person is) for person transactions
# not in the plain shape.
USER_EXCEPTIONS: dict[str, tuple[int, str]] = {
    "search/story_vectors.py:_write": (1, "the story's own author, from the story key it is given"),
    "search/story_vectors.py:embed_missing_stories": (1, "the worker's sweep: each story as its author"),
}

Function = ast.FunctionDef | ast.AsyncFunctionDef


def _plain(args: list[ast.expr], function: Function | None) -> bool:
    """``(customer_id, user_id)``, both parameters of ``function`` and never rebound in it."""
    if function is None or [ast.unparse(arg) for arg in args] != ["customer_id", "user_id"]:
        return False
    params = {arg.arg for arg in [*function.args.args, *function.args.kwonlyargs]}
    stored = {
        node.id
        for node in ast.walk(function)
        if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store)
    }
    return {"customer_id", "user_id"} <= params and not {"customer_id", "user_id"} & stored


class _Sites(ast.NodeVisitor):
    """``found``: every ``.customer``/``.system`` on a database, by ALLOWED's key; ``odd``: every
    person transaction not opened in the plain shape, by USER_EXCEPTIONS's key."""

    def __init__(self, file: str) -> None:
        self.file = file
        self.chain: list[Function] = []
        self.found: list[tuple[str, int]] = []
        self.odd: list[tuple[str, int]] = []
        self.plain: set[int] = set()  # ids of the nodes naming a person transaction in the plain shape

    def _where(self) -> str:
        return f"{self.file}:{self.chain[-1].name if self.chain else '<module>'}"

    def visit_FunctionDef(self, node: Function) -> None:
        self.chain.append(node)
        self.generic_visit(node)
        self.chain.pop()

    visit_AsyncFunctionDef = visit_FunctionDef

    def visit_Call(self, node: ast.Call) -> None:
        target, args = node.func, list(node.args)
        if isinstance(target, ast.Name) and target.id == "partial" and args:
            target, args = args[0], args[1:]  # partial(opener, ...): opened later, on another thread
        named = isinstance(target, ast.Name) and target.id == PERSON
        if named and args and DATABASE.search(ast.unparse(args[0])):
            args = args[1:]  # user_or_customer(db, ...)
        person = named or (isinstance(target, ast.Attribute) and target.attr == "user")
        if person and not node.keywords and _plain(args, self.chain[-1] if self.chain else None):
            self.plain.add(id(target))
        self.generic_visit(node)

    def visit_Attribute(self, node: ast.Attribute) -> None:
        if DATABASE.search(ast.unparse(node.value)):
            if node.attr in ("customer", "system"):
                self.found.append((f"{self._where()}:{node.attr}", node.lineno))
            elif node.attr == "user" and id(node) not in self.plain:
                self.odd.append((self._where(), node.lineno))
        self.generic_visit(node)

    def visit_Name(self, node: ast.Name) -> None:
        if node.id == PERSON and id(node) not in self.plain:
            self.odd.append((self._where(), node.lineno))


def scan() -> tuple[list[tuple[str, int]], list[tuple[str, int]]]:
    found: list[tuple[str, int]] = []
    odd: list[tuple[str, int]] = []
    for package in PACKAGES:
        for path in sorted((ROOT / package).rglob("*.py")):
            visitor = _Sites(path.relative_to(ROOT).as_posix())
            visitor.visit(ast.parse(path.read_text(encoding="utf-8")))
            found += visitor.found
            odd += visitor.odd
    return found, odd


def _differences(sites: list[tuple[str, int]], expected: dict[str, tuple[int, str]]) -> list[str]:
    """One line per key whose count differs from the reviewed one (0 when unlisted)."""
    counted = Counter(key for key, _ in sites)
    reviewed = Counter({key: count for key, (count, _) in expected.items()})
    return [
        f"kaagaz/{key}: {counted[key]} found (lines {[n for k, n in sites if k == key]}),"
        f" {reviewed[key]} reviewed"
        for key in sorted(set(counted) | set(reviewed))
        if counted[key] != reviewed[key]
    ]


def test_ask_and_search_open_no_transaction_without_the_person_outside_the_reviewed_places() -> None:
    found, _ = scan()
    wrong = _differences(found, ALLOWED)
    assert not wrong, (
        "Open these as the asker, user_or_customer(self._db, customer_id, user_id), or, where no"
        " signed-in person acts, review the count in ALLOWED with the reason:\n" + "\n".join(wrong)
    )


def test_every_person_transaction_names_the_asker_its_caller_passed() -> None:
    _, odd = scan()
    wrong = _differences(odd, USER_EXCEPTIONS)
    assert not wrong, (
        "Open these with the parameters (customer_id, user_id) the caller passed, or, where the"
        " person is another, review the count in USER_EXCEPTIONS:\n" + "\n".join(wrong)
    )


def _api_trees() -> list[tuple[str, ast.Module]]:
    api = ROOT / "api"
    return [
        (p.relative_to(ROOT).as_posix(), ast.parse(p.read_text(encoding="utf-8"))) for p in api.rglob("*.py")
    ]


def test_the_api_asks_and_bills_as_the_signed_in_user() -> None:
    wrong: list[str] = []
    for file, tree in _api_trees():
        for node in ast.walk(tree):
            if isinstance(node, ast.Name) and node.id == PERSON:
                wrong.append(f"kaagaz/{file}:{node.lineno}: {PERSON} (the API knows the user: Database.user)")
            if not isinstance(node, ast.Call):
                continue
            keywords = {k.arg: ast.unparse(k.value) for k in node.keywords}
            name = ast.unparse(node.func)
            if name == "usage_scope" and keywords.get("as_person") != "True":
                wrong.append(f"kaagaz/{file}:{node.lineno}: usage_scope without as_person=True")
            if name == "context.answers.answer" and keywords.get("user_id") != "user.user_id":
                wrong.append(f"kaagaz/{file}:{node.lineno}: answer without user_id=user.user_id")
    assert not wrong, "\n".join(wrong)
