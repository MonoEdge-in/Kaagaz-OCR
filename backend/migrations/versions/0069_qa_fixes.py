"""Product QA fixes (4 Nov 2026): two operator functions changed in place, each from its
previous text with each change made exactly once, keeping its grants, SECURITY DEFINER and empty
search path.

* ``kaagaz_op_set_admin_two_step`` (0068): turning "require two-step for admins" on also raises
  ``session_version`` for that organisation's admins whose two-step is off, in the same
  transaction (QA U1). They are signed out at their next call, and the next sign-in offers the
  set-up, instead of a session that every route refuses. Turning it off signs nobody out.
* ``kaagaz_op_mistral_status`` (0065): a document in the Trash waits on nothing, so only runs
  of documents out of the Trash are counted (QA P9).

The downgrade puts both 0068's and 0065's bodies and comments back. Nothing is lost.

Revision ID: 0069
Revises: 0068 (audit keeping)
"""

import importlib.util
from pathlib import Path
from types import ModuleType

from alembic import op

PREVIOUS = "0068"
revision = "0069"
down_revision = PREVIOUS
branch_labels = None
depends_on = None


def _load(name: str) -> ModuleType:
    path = Path(__file__).with_name(name)
    spec = importlib.util.spec_from_file_location(f"migration_{name[:4]}_for_0069", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_0064 = _load("0064_scan_cost_settings.py")
_0065 = _load("0065_scan_reader.py")
_0068 = _load("0068_audit_keeping.py")
_changed = _0064._changed
_literal = _0064._literal

SET_TWO_STEP = "kaagaz_op_set_admin_two_step(uuid, uuid, boolean, bigint)"
MISTRAL_STATUS = _0065.MISTRAL_STATUS
_REPLACE = ("CREATE FUNCTION", "CREATE OR REPLACE FUNCTION")  # in place: grants and comment stay

OLD = {
    SET_TWO_STEP: (_0068.FUNCTIONS[SET_TWO_STEP][1], _0068.FUNCTION_COMMENTS["kaagaz_op_set_admin_two_step"]),
    MISTRAL_STATUS: (_0065.MISTRAL_STATUS_SQL, _0065.MISTRAL_STATUS_COMMENT),
}
NEW = {
    SET_TWO_STEP: (
        _changed(
            OLD[SET_TWO_STEP][0],
            [
                (
                    "WHERE c.id = p_customer;\n",
                    "WHERE c.id = p_customer;\n"
                    "        IF p_required THEN  -- signed out: the next sign-in offers the set-up\n"
                    "            UPDATE public.users AS u SET session_version = u.session_version + 1\n"
                    "            WHERE u.customer_id = p_customer AND u.role = 'admin' AND u.totp_enabled_at IS NULL;\n"
                    "        END IF;\n",
                )
            ],
        ),
        _changed(
            OLD[SET_TWO_STEP][1],
            [
                (
                    " stop requiring it, with a fresh code.",
                    " stop requiring it, with a fresh code. Turning it on"
                    " signs out its admins without two-step.",
                )
            ],
        ),
    ),
    MISTRAL_STATUS: (
        _changed(
            OLD[MISTRAL_STATUS][0],
            [
                (
                    "FROM public.processing_runs r\n",
                    "FROM public.processing_runs r\n"
                    "        JOIN public.documents d ON d.customer_id = r.customer_id AND d.id = r.document_id\n"
                    "             AND d.deleted_at IS NULL\n",
                )
            ],
        ),
        _changed(
            OLD[MISTRAL_STATUS][1],
            [("the documents still waiting", "the documents out of the Trash still waiting")],
        ),
    ),
}


def _statements(functions: dict[str, tuple[str, str]]) -> list[str]:
    return [
        statement
        for signature, (body, comment) in functions.items()
        for statement in (
            _changed(body, [_REPLACE]),
            f"COMMENT ON FUNCTION public.{signature} IS {_literal(comment)}",
        )
    ]


def upgrade() -> None:
    for statement in _statements(NEW):
        op.execute(statement)


def downgrade() -> None:
    for statement in _statements(OLD):
        op.execute(statement)
