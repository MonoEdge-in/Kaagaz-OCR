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


def reads(sql: str) -> set[str]:
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
    return {name.lower() for name in found} & (CONTENT | PLACES)


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


def owners() -> Iterator[tuple[Owner, bool]]:
    """Every owner whose own SQL reads documents, folders, trashed_folders or a content table
    directly, and whether kaagaz/api reaches it."""
    trees = _parse()
    edges = _graph(trees)
    reached = _closure(edges, {key for key in edges if key[0].startswith("api/")})
    nodes = {(rel, name): node for rel, tree in trees.items() for node in tree.body if (name := _name(node))}
    texts = {key: _strings(node) for key, node in nodes.items()}
    constants = {key for key, node in nodes.items() if isinstance(node, ast.Assign | ast.AnnAssign)}
    for key, own in texts.items():
        direct = set().union(*(reads(sql) for sql in own)) if own else set()
        if not direct:
            continue
        named = _closure(edges, {key}, constants)
        view = any("visible_documents" in sql for each in named for sql in texts.get(each, ()))
        yield Owner(key[0], key[1], frozenset(direct), view), key in reached


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
