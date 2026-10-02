"""The kaagaz modules import each other without a cycle (R6 finding 6).

Every import counts, at the top of a module or inside a function, since a lazy
import only hides a cycle. Imports under ``if TYPE_CHECKING:`` are left out:
they never run. ast only: nothing is imported. O(lines of kaagaz).
"""

import ast
from pathlib import Path

PACKAGE = Path(__file__).resolve().parents[1] / "kaagaz"


def _modules() -> dict[str, Path]:
    modules = {}
    for path in PACKAGE.rglob("*.py"):
        parts = path.relative_to(PACKAGE.parent).with_suffix("").parts
        modules[".".join(parts[:-1] if parts[-1] == "__init__" else parts)] = path
    return modules


def _is_type_checking(node: ast.If) -> bool:
    test = node.test
    return (isinstance(test, ast.Name) and test.id == "TYPE_CHECKING") or (
        isinstance(test, ast.Attribute) and test.attr == "TYPE_CHECKING"
    )


def _imported(tree: ast.AST, package: str) -> set[str]:
    """The names each import statement can load, outside ``if TYPE_CHECKING:``."""
    names: set[str] = set()
    stack: list[ast.AST] = [tree]
    while stack:
        node = stack.pop()
        if isinstance(node, ast.If) and _is_type_checking(node):
            stack.extend(node.orelse)
            continue
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            base = node.module or ""
            if node.level:
                parent = package.split(".")[: len(package.split(".")) - node.level + 1]
                base = ".".join([*parent, base] if base else parent)
            names.add(base)
            names.update(f"{base}.{alias.name}" for alias in node.names)
        stack.extend(ast.iter_child_nodes(node))
    return names


def _graph() -> dict[str, set[str]]:
    modules = _modules()
    graph = {}
    for name, path in modules.items():
        package = name if path.name == "__init__.py" else name.rpartition(".")[0]
        tree = ast.parse(path.read_text(encoding="utf-8"))
        graph[name] = {other for other in _imported(tree, package) if other in modules and other != name}
    return graph


def _a_cycle(graph: dict[str, set[str]]) -> list[str]:
    """One cycle as a path that ends where it starts, or [] when there is none."""
    done: set[str] = set()
    for start in sorted(graph):
        if start in done:
            continue
        path = [start]
        on_path = {start}
        todo = [iter(sorted(graph[start]))]
        while todo:
            following = next(todo[-1], None)
            if following is None:
                finished = path.pop()
                on_path.discard(finished)
                done.add(finished)
                todo.pop()
                continue
            if following in on_path:
                return [*path[path.index(following) :], following]
            if following not in done:
                path.append(following)
                on_path.add(following)
                todo.append(iter(sorted(graph[following])))
    return []


def test_the_kaagaz_import_graph_has_no_cycle() -> None:
    cycle = _a_cycle(_graph())
    assert not cycle, " -> ".join(cycle)


def test_the_cycle_check_finds_a_cycle_and_reads_inside_functions() -> None:
    tree = ast.parse("def late() -> None:\n    from kaagaz.b import x\n")
    assert _imported(tree, "kaagaz") >= {"kaagaz.b", "kaagaz.b.x"}
    assert _a_cycle({"a": {"b"}, "b": {"c"}, "c": {"a"}, "d": set()}) == ["a", "b", "c", "a"]
    assert _a_cycle({"a": {"b"}, "b": set()}) == []


def test_imports_for_type_checking_only_are_left_out() -> None:
    tree = ast.parse("from typing import TYPE_CHECKING\nif TYPE_CHECKING:\n    from kaagaz.b import x\n")
    assert "kaagaz.b" not in _imported(tree, "kaagaz")
