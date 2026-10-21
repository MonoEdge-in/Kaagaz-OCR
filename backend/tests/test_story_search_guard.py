"""A story is never evidence (council 3 Oct 2026): the code that answers never imports
the story code, not even through another module.

Every ``kaagaz`` module the answer service, the chat route, the views of saved
messages, the rewrite (Turns), the evidence gate, verify and the prompts reach
through their imports, followed to the end, is read with ``ast``: none of them is
a story module. So a story can't reach an answer, a message, a Turn, the gate,
verify, the prompt or ``closest_documents`` by an import someone adds later.
"""

import ast
from pathlib import Path

import kaagaz

ROOT = Path(kaagaz.__file__).parent
# Where answering starts: every module of kaagaz/answering (the service, the gate in
# evidence, verify, the draft's prompt, the rewrite's Turns), the model client, the chat
# route, the views of saved messages and the messages' SQL.
STARTS = (
    *sorted(f"kaagaz.answering.{path.stem}" for path in (ROOT / "answering").glob("*.py")),
    "kaagaz.ai.client",
    "kaagaz.api.routers.chat",
    "kaagaz.api.views",
    "kaagaz.db.chat",
)
STORY = {"search.story_match", "search.story_search", "search.story_vectors", "db.document_stories"}
STORY_MODULES = {f"kaagaz.{name}" for name in (*STORY, "api.routers.stories")}


def _path(module: str) -> Path | None:
    """The file of a kaagaz module or package, None for a name in a module."""
    base = ROOT.parent.joinpath(*module.split("."))
    return next((path for path in (base.with_suffix(".py"), base / "__init__.py") if path.is_file()), None)


def _imports(module: str) -> set[str]:
    """The kaagaz modules ``module`` imports, at any depth of its code."""
    path = _path(module)
    assert path is not None, module
    found: set[str] = set()
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            found.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            found.add(node.module)
            found.update(f"{node.module}.{alias.name}" for alias in node.names)
    return {name for name in found if name.startswith("kaagaz") and _path(name) is not None}


def reached(*starts: str) -> set[str]:
    seen: set[str] = set()
    todo = list(starts)
    while todo:
        if (module := todo.pop()) not in seen:
            seen.add(module)
            todo.extend(_imports(module) - seen)
    return seen


def test_answering_never_reaches_a_story_module() -> None:
    modules = reached(*STARTS)
    assert len(modules) > 20  # the walk really followed the imports
    assert "kaagaz.search.hybrid" in modules
    assert modules & STORY_MODULES == set()


def test_the_guard_sees_a_story_import() -> None:
    """The control: the routes that do use stories are seen to reach them. Changed on purpose
    (PRD 1, activity/search): GET /api/search is keyword only (Q1); the stories routes remain."""
    assert {"kaagaz.search.story_match", "kaagaz.search.story_search"} <= reached(
        "kaagaz.api.routers.stories"
    )
    assert "kaagaz.search.story_search" not in reached("kaagaz.api.routers.search")
