"""What every model client shares: its two errors and how a JSON reply is checked.

``kaagaz.ai.client`` (Ollama) and ``kaagaz.ai.gemini`` (PRD 5 G9) both raise these
errors and check replies the same way, and ``client.build_model_client`` builds
either. They live here so the two modules import this one and never each other
(``tests/test_import_graph.py``). ``kaagaz.ai.client`` re-exports every name, so
callers keep importing from it.

Two errors, because callers treat them differently:

* ``ModelUnavailable``: no usable HTTP reply. Safe to retry later; callers carry
  on without the model.
* ``ModelOutputError``: the model replied, but its content is unusable.

Cost: ``schema_problem`` and ``parse_reply`` are O(size of the reply).
"""

import json
from collections.abc import Callable, Mapping
from typing import Any

# Texts per embedding request: one CPU-sized model call, and no round trip per piece.
EMBED_BATCH_SIZE = 32

# A reply larger than this is refused. 32 vectors of 1024 floats as JSON are
# under 1 MB; a chat reply is a few kilobytes.
MAX_RESPONSE_BYTES = 16 * 1024 * 1024

# The repair message: the problem names a schema path, never the reply's text.
REPAIR = "Your reply was not valid: {problem}. Reply again with only the JSON object that follows the schema."


class ModelUnavailable(Exception):
    """No usable reply from the model server. ``code`` is short and safe to log."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


class ModelOutputError(Exception):
    """The model replied with unusable content. ``code`` is short and safe to log."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


_JSON_TYPES: dict[str, Callable[[object], bool]] = {
    "object": lambda value: isinstance(value, dict),
    "array": lambda value: isinstance(value, list),
    "string": lambda value: isinstance(value, str),
    "integer": lambda value: isinstance(value, int) and not isinstance(value, bool),
    "number": lambda value: isinstance(value, int | float) and not isinstance(value, bool),
    "boolean": lambda value: isinstance(value, bool),
    "null": lambda value: value is None,
}


def schema_problem(value: object, schema: Mapping[str, Any], path: str = "$") -> str | None:
    """What is wrong with ``value`` under ``schema``, or None if nothing is.

    Checks the JSON Schema keywords our callers use: ``type`` (a name or a list
    of names), ``enum``, ``required``, ``properties`` and ``items``; other
    keywords are ignored. The problem names a path from the schema, never the
    value, so it holds no document text. O(size of value).
    """
    expected = schema.get("type")
    if expected is not None:
        names = [expected] if isinstance(expected, str) else list(expected)
        unknown = [name for name in names if name not in _JSON_TYPES]
        if unknown:
            raise ValueError("unsupported schema type")
        if not any(_JSON_TYPES[name](value) for name in names):
            return f"{path} must be {' or '.join(names)}"
    if "enum" in schema and value not in schema["enum"]:
        return f"{path} must be one of the allowed values"
    if isinstance(value, dict):
        for key in schema.get("required", []):
            if key not in value:
                return f"{path}.{key} is missing"
        for key, sub_schema in schema.get("properties", {}).items():
            if key in value:
                problem = schema_problem(value[key], sub_schema, f"{path}.{key}")
                if problem is not None:
                    return problem
    items = schema.get("items")
    if isinstance(value, list) and isinstance(items, Mapping):
        for index, item in enumerate(value):
            problem = schema_problem(item, items, f"{path}[{index}]")
            if problem is not None:
                return problem
    return None


def parse_reply(content: str, schema: Mapping[str, Any]) -> tuple[dict[str, Any] | None, str]:
    """The reply as an object that follows the schema, else (None, the problem)."""
    try:
        value = json.loads(content)
    except ValueError:
        return None, "the reply is not JSON"
    if not isinstance(value, dict):
        return None, "the reply must be one JSON object"
    try:
        json.dumps(value, ensure_ascii=False).encode("utf-8")
    except UnicodeEncodeError:  # JSON can escape half a character (a lone surrogate, "\ud800")
        return None, "the reply holds characters that are not valid text"
    problem = schema_problem(value, schema)
    if problem is not None:
        return None, problem
    return value, ""
