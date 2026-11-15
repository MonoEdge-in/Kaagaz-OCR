"""Who can see what: the rules that make walls safe (PRD 7 PR 10c, #601, Tushar, 15 Nov 2026).
NEEDS VRUSHIT'S APPROVAL: security.

* ``kaagaz_place_audience`` (0090) may be asked by Management and by the Head of the department
  asked about too, not only by an Admin: they may make a move that changes who can see a file
  (decision 18), and the move preview shows them who loses and who gains it. Its body is 0090's
  with the caller's check changed, by a checked replace.
* ``kaagaz_give_uploader(document)``: auto-filing gives the uploader the file on every live
  version when it filed it where they can't see it (PRD 7 section 5, "Uploads"), so nobody is
  left unable to see their own upload. Whether they can see it is the access view's own
  predicate (0090's ``DOCUMENT_RULE``, the rule written once); ``given_by`` is NULL (Kaagaz did,
  0084's comment). Only a transaction that names no person may run it (the worker: it reads no
  view, and auto-filing runs there); a person's transaction is refused, insufficient_privilege.
  The caller holds the tree lock; it takes the family lock (0090's ``_LOCK_FAMILY``), the 0085
  order. The house pattern: owner's rights, an empty search path, nothing for PUBLIC, EXECUTE to
  the app and the worker role alike (0083's equality test).

Catalog only. The downgrade puts back 0090's ``kaagaz_place_audience`` and drops the new function.

Revision ID: 0090b
Revises: 0090
"""

import importlib.util
from pathlib import Path
from types import ModuleType

from alembic import op

revision = "0090b"
down_revision = "0090"
branch_labels = None
depends_on = None


def _load(name: str) -> ModuleType:
    path = Path(__file__).with_name(name)
    spec = importlib.util.spec_from_file_location(f"migration_{path.stem}_for_0090b", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_0090 = _load("0090_access_api.py")
_literal = _0090._literal
_replaced = _0090._replaced
ROLES = _0090.ROLES

PLACE = "kaagaz_place_audience(uuid, uuid, boolean)"
_PLACE_0090 = _0090.FUNCTIONS[PLACE].replace("CREATE FUNCTION", "CREATE OR REPLACE FUNCTION", 1)
PLACE_AUDIENCE = _replaced(
    _PLACE_0090,
    _0090._ADMIN_ONLY,
    """
        IF NOT coalesce((SELECT a.admin OR a.management OR p_department = ANY (a.heads)
                         FROM public.kaagaz_access() a), false) THEN
            RAISE EXCEPTION 'only an Admin, Management, or the Head of the department'
                USING ERRCODE = 'insufficient_privilege';
        END IF;""",
)
PLACE_COMMENT = (
    "Whether each active person sees the files of a place (department, private folder, Department only"
    " as given), for the lose-access lists and the move previews (PRD 7, #601). An Admin, Management, or"
    " the Head of the department asked about."
)

GIVE = "kaagaz_give_uploader(uuid)"
GIVE_UPLOADER = f"""
    CREATE FUNCTION public.kaagaz_give_uploader(p_document uuid) RETURNS integer
    LANGUAGE plpgsql SECURITY DEFINER SET search_path = '' AS $$
    DECLARE
        v_customer uuid := public.kaagaz_current_customer();
        v_given integer;
    BEGIN
        IF (public.kaagaz_access()).asked THEN
            RAISE EXCEPTION 'only Kaagaz itself' USING ERRCODE = 'insufficient_privilege';
        END IF;
        {_0090._LOCK_FAMILY}
        WITH live AS (
            SELECT v.id FROM public.documents v
            JOIN public.documents x ON x.customer_id = v.customer_id AND x.family_id = v.family_id
            WHERE x.customer_id = v_customer AND x.id = p_document AND v.deleted_at IS NULL
        ), given AS (
            INSERT INTO public.document_shares AS ds (customer_id, document_id, user_id, given_by)
            SELECT v_customer, l.id, a.person, NULL
            FROM public.documents d
            CROSS JOIN LATERAL public.kaagaz_access_of(d.uploaded_by) a
            CROSS JOIN live l
            WHERE d.customer_id = v_customer AND d.id = p_document AND a.person IS NOT NULL
            AND NOT coalesce({_0090.DOCUMENT_RULE}, false)
            ON CONFLICT DO NOTHING RETURNING ds.document_id
        )
        SELECT count(*) INTO v_given FROM given;
        RETURN v_given;
    END
    $$
    """
GIVE_COMMENT = (
    "Auto-filing (PRD 7, #601): the file given to its uploader on every live version when they can't"
    " see it where it was filed; how many versions were given. Only a transaction with no person (the"
    " worker); the caller holds the tree lock."
)

UPGRADE = [
    PLACE_AUDIENCE,
    f"COMMENT ON FUNCTION public.{PLACE} IS {_literal(PLACE_COMMENT)}",
    GIVE_UPLOADER,
    f"COMMENT ON FUNCTION public.{GIVE} IS {_literal(GIVE_COMMENT)}",
    f"REVOKE ALL ON FUNCTION public.{GIVE} FROM PUBLIC",
    f"GRANT EXECUTE ON FUNCTION public.{GIVE} TO {ROLES}",
]
DOWNGRADE = [
    f"DROP FUNCTION IF EXISTS public.{GIVE}",
    _PLACE_0090,
    f"COMMENT ON FUNCTION public.{PLACE} IS {_literal(_0090.FUNCTION_COMMENTS[PLACE])}",
]


def upgrade() -> None:
    for statement in UPGRADE:
        op.execute(statement)


def downgrade() -> None:
    for statement in DOWNGRADE:
        op.execute(statement)
