"""The SQL guard's scanner (PRD 7, #599; tests/test_sql_access_guard.py): every SQL text in
``kaagaz`` by its owner (a top-level constant, function or class of a module), which tables it
reads directly, whether it reads a view, and whether ``kaagaz/api`` reaches it.

No module is imported. An owner's own string constants are its SQL (keywords in capitals, as every
statement here writes them, so prose such as a prompt's "from pages" is not); adjacent literals and
the literal parts of an f-string are one constant each, so "FROM pages" is always inside one. The
module constants and imported constants it names add their texts when it asks whether a view is
read (``SEEN_DOCUMENT``, ``LIVE_SUGGESTION``, a fragment), never when it asks which tables are read
directly: a fragment's own reads are its own entry.

Reached from the API: a name-based call graph from every top-level function, constant and class of
``kaagaz/api`` (``from kaagaz.x import y``, ``import kaagaz.x as m``, ``m.y``, and names within a
module). It over-reaches (a name is a whole class; one call reaches every use of a name), which
asks for more entries, never fewer."""

import ast
import re
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

import kaagaz

ROOT = Path(kaagaz.__file__).parent
# PRD 7 appendix A: the 27 tables that point at a document (page_rotations: #600, 0088).
CONTENT = frozenset(
    [
        "pages",
        "reader_outputs",
        "pieces",
        "extracted_values",
        "corrections",
        "invoices",
        "invoice_lines",
        "document_links",
        "page_reads",
        "document_sections",
        "document_topics",
        "ensemble_readings",
        "word_changes",
        "version_suggestions",
        "value_keys",
        "table_cells",
        "page_blocks",
        "document_tags",
        "processing_runs",
        "model_usage",
        "ensemble_runs",
        "source_pins",
        "learning_removals",
        "organization_word_suggestions",
        "user_word_spots",
        "document_stories",
        "page_rotations",
        # Changed on purpose (#648, 0098): the version finder's pairs and groups point at documents.
        "version_pairs",
        "version_group_members",
        # Changed on purpose (#648, 0100): a link's snapshot points at its family.
        "version_links",
    ]
)
PLACES = frozenset({"documents", "folders", "trashed_folders"})
# A table named after a keyword: ONLY, brackets (a bracketed join), quotes and the schema allowed;
# never a function (``FROM unnest(``).
_NAME = r'(?:ONLY\s+)?\(*\s*"?(?:public"?\s*\.\s*"?)?([a-z_]+)"?(?!\s*\()'
_SOURCE = r"(?<!DELETE )\b(?:FROM|JOIN|USING)\s+" + _NAME
_WRITE = r"\b(?:UPDATE|DELETE\s+FROM)\s+" + _NAME
_FROM = r"(?<!DELETE )\bFROM\b"
# Where a FROM list ends, at its own depth of brackets.
_END = re.compile(
    r"\b(?:WHERE|JOIN|LEFT|RIGHT|INNER|FULL|CROSS|GROUP|ORDER|LIMIT|OFFSET|HAVING|UNION|EXCEPT|INTERSECT"
    r"|RETURNING|ON|WINDOW|FOR|SET|LATERAL)\b",
    re.IGNORECASE,
)
_ITEM = re.compile(r"\s*" + _NAME, re.IGNORECASE)
# SQL written in small letters: read too, but only in a string that is plainly a statement.
_LOWER_SQL = re.compile(
    r"\bselect\b[\s\S]*\bfrom\b|\bupdate\s+\w+\s+set\b|\bdelete\s+from\b|\binsert\s+into\b"
)


@dataclass(frozen=True, slots=True)
class Owner:
    file: str  # relative to kaagaz/, e.g. "db/listing.py"
    name: str
    reads: frozenset[str]  # the tables its own SQL reads directly
    view: bool  # it, or a constant it names (at any depth), reads visible_documents


def _listed(sql: str, start: int) -> list[str]:
    """The items of the FROM list that starts at ``start`` (``a x, b y, c z``), up to where it ends."""
    items, depth, begin, at = [], 0, start, start
    while at < len(sql):
        char = sql[at]
        if char == "(":
            depth += 1
        elif char == ")":
            depth -= 1
            if depth < 0:
                break
        elif depth == 0 and char == ",":
            items.append(sql[begin:at])
            begin = at + 1
        elif depth == 0 and sql[at - 1].isspace() and _END.match(sql, at):
            break
        at += 1
    items.append(sql[begin:at])
    return items


def reads(sql: str, tables: frozenset[str] = CONTENT | PLACES) -> set[str]:
    """The tables ``sql`` reads directly: after FROM, JOIN and USING (every item of a FROM list,
    ONLY, quotes and a bracketed join too), and the target of an UPDATE or DELETE that returns
    rows (RETURNING); not a DELETE's or an UPDATE's target otherwise. Keywords in capitals, or in
    small letters in a string that is plainly a statement (``_LOWER_SQL``)."""
    flags = re.IGNORECASE if _LOWER_SQL.search(sql) else 0
    found = {m.group(1) for m in re.finditer(_SOURCE, sql, flags)}
    for start in re.finditer(_FROM, sql, flags):
        for item in _listed(sql, start.end()):
            if (named := _ITEM.match(item)) is not None:
                found.add(named.group(1))
    if re.search(r"\bRETURNING\b", sql, flags):
        found |= {m.group(1) for m in re.finditer(_WRITE, sql, flags)}
    return {name.lower() for name in found} & tables


def _name(node: ast.stmt) -> str | None:
    if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
        return node.name
    targets = getattr(node, "targets", None) or [getattr(node, "target", None)]
    return next((t.id for t in targets if isinstance(t, ast.Name)), None)


def _module(path: Path) -> str:
    return "kaagaz." + ".".join(path.relative_to(ROOT).with_suffix("").parts).removesuffix(".__init__")


def _strings(node: ast.AST) -> list[str]:
    return [n.value for n in ast.walk(node) if isinstance(n, ast.Constant) and isinstance(n.value, str)]


def _parse() -> dict[str, ast.Module]:
    return {
        path.relative_to(ROOT).as_posix(): ast.parse(path.read_text(encoding="utf-8"))
        for path in sorted(ROOT.rglob("*.py"))
    }


def _graph(trees: dict[str, ast.Module]) -> dict[tuple[str, str], set[tuple[str, str]]]:
    """Each (file, top-level name) to the (file, name) pairs it names."""
    files = {_module(ROOT / rel): rel for rel in trees}
    edges: dict[tuple[str, str], set[tuple[str, str]]] = {}
    for rel, tree in trees.items():
        package = (
            _module(ROOT / rel) if rel.endswith("__init__.py") else _module(ROOT / rel).rsplit(".", 1)[0]
        )
        alias: dict[str, tuple[str, str | None]] = {}  # local name: (file, name in it, or None: the module)
        for n in ast.walk(tree):
            if isinstance(n, ast.ImportFrom):
                base = n.module or ""
                if n.level:
                    parts = package.split(".")[: len(package.split(".")) - n.level + 1]
                    base = ".".join(parts) + (f".{base}" if base else "")
                for a in n.names:
                    if f"{base}.{a.name}" in files:
                        alias[a.asname or a.name] = (files[f"{base}.{a.name}"], None)
                    elif base in files:
                        alias[a.asname or a.name] = (files[base], a.name)
            elif isinstance(n, ast.Import):
                for a in n.names:
                    if a.name in files:
                        alias[a.asname or a.name.split(".")[0]] = (files[a.name], None)
        tops = {name for node in tree.body if (name := _name(node))}
        for node in tree.body:
            name = _name(node)
            if not name:
                continue
            out = edges.setdefault((rel, name), set())
            for n in ast.walk(node):
                if isinstance(n, ast.Name):
                    if n.id in tops and n.id != name:
                        out.add((rel, n.id))
                    target = alias.get(n.id)
                    if target is not None and target[1] is not None:
                        out.add((target[0], target[1]))
                if isinstance(n, ast.Attribute) and isinstance(n.value, ast.Name):
                    target = alias.get(n.value.id)
                    if target is not None and target[1] is None:
                        out.add((target[0], n.attr))
    return edges


def _closure(
    edges: dict[tuple[str, str], set[tuple[str, str]]],
    start: set[tuple[str, str]],
    within: set[tuple[str, str]] | None = None,
) -> set[tuple[str, str]]:
    """What ``start`` names at any depth (only through ``within``, when given)."""
    seen: set[tuple[str, str]] = set()
    todo = list(start)
    while todo:
        key = todo.pop()
        if key not in seen:
            seen.add(key)
            todo.extend(k for k in edges.get(key, ()) if within is None or k in within)
    return seen


def owners(tables: frozenset[str] = CONTENT | PLACES) -> Iterator[tuple[Owner, bool]]:
    """Every owner whose own SQL reads documents, folders, trashed_folders or a content table
    (or, PR 9a #606, any of ``tables``) directly, and whether kaagaz/api reaches it."""
    trees = _parse()
    edges = _graph(trees)
    reached = _closure(edges, {key for key in edges if key[0].startswith("api/")})
    nodes = {(rel, name): node for rel, tree in trees.items() for node in tree.body if (name := _name(node))}
    texts = {key: _strings(node) for key, node in nodes.items()}
    constants = {key for key, node in nodes.items() if isinstance(node, ast.Assign | ast.AnnAssign)}
    for key, own in texts.items():
        direct = set().union(*(reads(sql, tables) for sql in own)) if own else set()
        if not direct:
            continue
        named = _closure(edges, {key}, constants)
        view = any("visible_documents" in sql for each in named for sql in texts.get(each, ()))
        yield Owner(key[0], key[1], frozenset(direct), view), key in reached


def calling(prefix: str) -> set[tuple[str, str]]:
    """PR 9a (#606): every (file, name) whose own SQL calls a function named ``prefix...(``."""
    found = re.compile(rf"\b{re.escape(prefix)}[a-z_]*\(")
    return {
        (rel, name)
        for rel, tree in _parse().items()
        for node in tree.body
        if (name := _name(node)) and any(found.search(sql) for sql in _strings(node))
    }


# PR 9a (#606): a statement that reads "not in this covered table" as gone or empty fails open once
# the database rule hides a row of it.
_NOT_IN = re.compile(r"NOT EXISTS \(SELECT 1 FROM (?:public\.)?([a-z_]+)", re.IGNORECASE)


def not_exists_readers() -> dict[tuple[str, str], frozenset[str]]:
    """Every (file, name) the API reaches whose own SQL has ``NOT EXISTS (SELECT 1 FROM <covered>``,
    with those tables."""
    trees = _parse()
    nodes = {(rel, name): node for rel, tree in trees.items() for node in tree.body if (name := _name(node))}
    found = {}
    for owner, by_api in owners():
        tables = {
            m.group(1) for sql in _strings(nodes[(owner.file, owner.name)]) for m in _NOT_IN.finditer(sql)
        }
        if by_api and tables & (CONTENT | PLACES):
            found[(owner.file, owner.name)] = frozenset(tables & (CONTENT | PLACES))
    return found


# PR 9a (#606): an organisation-wide count, sum or max, or a "LEFT JOIN <covered> ... IS NULL", that
# names no document: once the rule hides rows it counts fewer, or reads a hidden row as missing.
_AGGREGATE = re.compile(r"\b(?:count|sum|max)\s*\(", re.IGNORECASE)
_BY_ID = re.compile(r"(?:document_id|\bid)\s*=\s*(?::|CAST\s*\(\s*:|ANY)", re.IGNORECASE)
_NULL_JOIN = re.compile(
    r"LEFT JOIN (?:public\.)?(" + "|".join(sorted(CONTENT | PLACES)) + r")\s+(\w+)\b.*?\b\2\.\w+ IS NULL",
    re.IGNORECASE | re.DOTALL,
)


def aggregate_readers() -> set[tuple[str, str]]:
    """Every (file, name) the API reaches that reads a covered table directly with a count, sum or
    max and no document id, or a LEFT JOIN of one tested for NULL."""
    trees = _parse()
    nodes = {(rel, name): node for rel, tree in trees.items() for node in tree.body if (name := _name(node))}
    found = set()
    for owner, by_api in owners():
        joined = " ".join(_strings(nodes[(owner.file, owner.name)]))
        if by_api and (
            (_AGGREGATE.search(joined) and not _BY_ID.search(joined)) or _NULL_JOIN.search(joined)
        ):
            found.add((owner.file, owner.name))
    return found


# The worker's own entry points (#595 condition 1).
_WORKER_FILES = ("worker_main.py", "cli.py", "pipeline.py", "ops_alert.py", "worker_heartbeat.py")
_VIEWS = ("visible_documents", "visible_folders", "visible_trashed_folders")


def worker_view_readers() -> set[tuple[str, str]]:
    """Every (file, name) the worker reaches, at any depth and across modules, whose own text names a
    view. Empty while no worker statement reads one."""
    trees = _parse()
    edges = _graph(trees)
    start = {key for key in edges if key[0].startswith("jobs/") or key[0] in _WORKER_FILES}
    nodes = {(rel, name): node for rel, tree in trees.items() for node in tree.body if (name := _name(node))}
    return {
        key
        for key in _closure(edges, start)
        if key in nodes and any(view in sql for sql in _strings(nodes[key]) for view in _VIEWS)
    }


_ROUTE_METHODS = frozenset({"get", "post", "put", "patch", "delete"})
# PRD 7 decision 20 (#601): what asks for an admin session.
_ADMIN_SESSION = frozenset({("api/auth.py", "require_admin_session"), ("api/auth.py", "AdminSession")})


def _is_route(decorator: ast.expr) -> bool:
    return (
        isinstance(decorator, ast.Call)
        and isinstance(decorator.func, ast.Attribute)
        and decorator.func.attr in _ROUTE_METHODS
        and isinstance(decorator.func.value, ast.Name)
    )


def routes() -> dict[tuple[str, str], tuple[set[tuple[str, str]], bool]]:
    """Each route of kaagaz/api (a function under ``@<router>.get`` and the like) to what it reaches
    at any depth, and whether it asks for an admin session itself: its router's dependency (the
    decorator names the router), or an ``AdminSession`` or ``require_admin_session`` named in the
    route (not in a helper, which may have a branch for others). A name-based rule: one named on
    one branch of the route counts too."""
    trees = _parse()
    edges = _graph(trees)
    found = {}
    for rel, tree in trees.items():
        if not rel.startswith("api/"):
            continue
        for node in tree.body:
            if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef) and any(
                map(_is_route, node.decorator_list)
            ):
                key = (rel, node.name)
                own = edges.get(key, set())
                routers = {(rel, d.func.value.id) for d in node.decorator_list if _is_route(d)}  # type: ignore[attr-defined]
                admin = bool(own & _ADMIN_SESSION) or any(
                    edges.get(r, set()) & _ADMIN_SESSION for r in routers
                )
                found[key] = (_closure(edges, {key}), admin)
    return found
