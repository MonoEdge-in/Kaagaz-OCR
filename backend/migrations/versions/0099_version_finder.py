"""The version finder: possible versions among the files an organisation already has (V17, #648,
V-B; Tushar, 16 Nov 2026). NEEDS VRUSHIT'S APPROVAL: schema.

0099 follows 0098 (WhatsApp's transcribe spend); it was 0098 after 0093 on its branch, renumbered
at merge (lead's order, 16 Nov 2026: 0094 search, 0095 #652, 0096 #651, 0097 and 0098 WhatsApp).
``kaagaz_clear_derived`` is spliced from 0093's body: none of 0094 to 0098 replaces it.

* ``version_finder_runs``: one row per organisation, the worker's one look through its files (so
  it never runs twice), with where it got to (``after_id``, the documents looked at in id order) so
  it resumes after a restart, and when it finished.
* ``version_pairs``: two current files the finder scored as likely versions of each other (V3 and
  V4's signals, as spotting scores them), the lower id first, with the scores: the evidence a
  person is shown ("94% the same text and the names match").
* ``version_group_members``: each file the finder put in a group (connected pairs, at most 20
  files), its place in the suggested order (by the document's own date, else the upload date; the
  last is the suggested latest) and what a person decided: ``open``, ``later`` (left for later),
  ``kept_apart`` ("Not versions", undone back to open) or ``linked`` (V-A's link). A file is in at
  most one group. A group is its members' ``group_id``; it has no row of its own. Its evidence is
  the pairs between two of its members.
* Every key holds the organisation (as 0084's tables). Pairs and members go with either file
  (CASCADE from ``documents``); ``decided_by`` points at the person with NO ACTION (a user is never
  deleted; ``kaagaz_finish_customer_delete`` refuses while a document is left).
* Row level security with ``customer_only``. The app and the worker role alike (0083's equality
  test): runs read, added and only their progress columns updated; pairs read and added; members
  read, added and only their decision columns updated. None is ever deleted by the app.
  **NEEDS VRUSHIT'S APPROVAL** (after PRD 7 PR 9, #606): neither carries 0091's ``person_only``
  policy (``test_isolation_person_rule.EXEMPT``). The worker writes both with no person, which no
  person rule holds; the API reads pairs only joined to ``visible_documents`` for both files, and
  must see a group's member it can't see to hide the whole group (all or nothing), which a person
  rule on members would hide instead, showing the rest.
* ``kaagaz_clear_derived`` (0093) also deletes a version's pairs and memberships when it is
  locked: they are worked out from its content (PRD 7 appendix B).

Cost: catalog only, and ``kaagaz_clear_derived`` replaced. The keys hold ``documents``, ``users``
and ``customers`` SHARE ROW EXCLUSIVE for a moment while the empty tables are created.

The downgrade puts 0093's ``kaagaz_clear_derived`` back and drops the tables; their rows are lost.

Revision ID: 0099
Revises: 0098 (WhatsApp's transcribe spend)
"""

import importlib.util
from pathlib import Path
from types import ModuleType

from alembic import op

revision = "0099"
down_revision = "0098"
branch_labels = None
depends_on = None


def _load(name: str) -> ModuleType:
    path = Path(__file__).with_name(name)
    spec = importlib.util.spec_from_file_location(f"migration_{path.stem}_for_0099", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_0093 = _load("0093_locked_files.py")
_literal = _0093._literal
ROLES = ("kaagaz_app", "kaagaz_worker")
POLICY = "customer_id = kaagaz_current_customer()"
MAX_GROUP = 20  # kaagaz.versions.MAX_GROUP
STATES = ("open", "later", "kept_apart", "linked")
_STATES = ", ".join(f"'{state}'" for state in STATES)

RUNS = """
    CREATE TABLE version_finder_runs (
        customer_id uuid PRIMARY KEY REFERENCES customers (id) ON DELETE CASCADE,
        started_at timestamptz NOT NULL DEFAULT now(),
        after_id uuid NULL,
        looked_at integer NOT NULL DEFAULT 0 CONSTRAINT version_finder_runs_looked_at CHECK (looked_at >= 0),
        finished_at timestamptz NULL,
        groups integer NULL CONSTRAINT version_finder_runs_groups CHECK (groups >= 0)
    )
    """
PAIRS = """
    CREATE TABLE version_pairs (
        customer_id uuid NOT NULL REFERENCES customers (id) ON DELETE CASCADE,
        first_id uuid NOT NULL,
        second_id uuid NOT NULL,
        text_overlap real NULL CONSTRAINT version_pairs_text_overlap CHECK (text_overlap BETWEEN 0 AND 1),
        text_overlap_exact boolean NULL,
        name_similarity real NULL CONSTRAINT version_pairs_name_similarity CHECK (name_similarity BETWEEN 0 AND 1),
        shared_identifier text NULL,
        found_at timestamptz NOT NULL DEFAULT now(),
        PRIMARY KEY (customer_id, first_id, second_id),
        CONSTRAINT version_pairs_order CHECK (first_id < second_id),
        FOREIGN KEY (customer_id, first_id) REFERENCES documents (customer_id, id) ON DELETE CASCADE,
        FOREIGN KEY (customer_id, second_id) REFERENCES documents (customer_id, id) ON DELETE CASCADE
    )
    """
MEMBERS = f"""
    CREATE TABLE version_group_members (
        customer_id uuid NOT NULL REFERENCES customers (id) ON DELETE CASCADE,
        document_id uuid NOT NULL,
        group_id uuid NOT NULL,
        position smallint NOT NULL CONSTRAINT version_group_members_position CHECK (position BETWEEN 1 AND {MAX_GROUP}),
        state text NOT NULL DEFAULT 'open' CONSTRAINT version_group_members_state CHECK (state IN ({_STATES})),
        decided_by uuid NULL,
        decided_at timestamptz NULL,
        PRIMARY KEY (customer_id, document_id),
        CONSTRAINT version_group_members_place UNIQUE (customer_id, group_id, position),
        CONSTRAINT version_group_members_decided CHECK ((decided_by IS NULL) = (decided_at IS NULL)),
        FOREIGN KEY (customer_id, document_id) REFERENCES documents (customer_id, id) ON DELETE CASCADE,
        FOREIGN KEY (customer_id, decided_by) REFERENCES users (customer_id, id)
    )
    """
TABLE_COMMENTS = {
    "version_finder_runs": "The version finder's one look through an organisation's files (V17, #648): never"
    " twice, resumed where it got to.",
    "version_pairs": "Two current files the version finder scored as likely versions of each other (V17), the"
    " lower id first: the evidence shown.",
    "version_group_members": "A file in a group of possible versions the version finder found (V17), its place"
    " in the suggested order and what a person decided. A file is in one group at most.",
}
COMMENTS = {
    "version_finder_runs": {
        "customer_id": "The organisation; one run each.",
        "started_at": "When the run began; files uploaded since are spotting's, never the finder's.",
        "after_id": "The last document looked at, in id order; the run resumes after it. NULL: none yet.",
        "looked_at": "How many documents the run has looked at.",
        "finished_at": "When the groups were made; the run never starts again. NULL: still running.",
        "groups": "How many groups the run made. NULL until it finished.",
    },
    "version_pairs": {
        "customer_id": "The organisation.",
        "first_id": "documents.id (same customer) of the pair's file with the lower id.",
        "second_id": "documents.id (same customer) of the other file.",
        "text_overlap": "The share of the text in common, 0 to 1 (V3); NULL when either has under 20 words.",
        "text_overlap_exact": "true: counted exactly; false: the MinHash estimate (over Compare's limits).",
        "name_similarity": "pg_trgm similarity of the two name keys, 0 to 1 (V2).",
        "shared_identifier": "The identifier kind of a related-documents link of the same type, or NULL.",
        "found_at": "When the finder scored it.",
    },
    "version_group_members": {
        "customer_id": "The organisation.",
        "document_id": "documents.id (same customer) of the file, its family's current version when found.",
        "group_id": "The group; its members share it. The group has no row of its own.",
        "position": "Its place in the suggested order, from 1 (oldest, by its own date, else the upload date);"
        " the last is the suggested latest.",
        "state": "open; later (left for later); kept_apart (Not versions, can be undone); linked (V-A's link).",
        "decided_by": "users.id (same customer) of who decided last. NULL: never decided.",
        "decided_at": "When they decided. NULL: never decided.",
    },
}
GRANTS = {
    "version_finder_runs": "SELECT, INSERT, UPDATE (after_id, looked_at, finished_at, groups)",
    "version_pairs": "SELECT, INSERT",
    "version_group_members": "SELECT, INSERT, UPDATE (state, decided_by, decided_at)",
}

# 0093's function (the newest that replaces it: none of 0094 to 0098 does), with each version's
# pairs and memberships deleted after its suggestions.
_SUGGESTIONS = "            AND (x.document_id = p_version OR x.candidate_family_id = v_family);\n"
_OURS = (
    "        DELETE FROM public.version_pairs x WHERE x.customer_id = v_customer\n"
    "            AND (x.first_id = p_version OR x.second_id = p_version);\n"
    "        DELETE FROM public.version_group_members x WHERE x.customer_id = v_customer"
    " AND x.document_id = p_version;\n"
)
_REPLACE = (
    "CREATE FUNCTION public.kaagaz_clear_derived",
    "CREATE OR REPLACE FUNCTION public.kaagaz_clear_derived",
)
if _0093.CLEAR_DERIVED.count(_SUGGESTIONS) != 1 or _0093.CLEAR_DERIVED.count(_REPLACE[0]) != 1:
    raise RuntimeError("0093's kaagaz_clear_derived is not as 0099 expects")
OLD_CLEAR_DERIVED = _0093.CLEAR_DERIVED.replace(*_REPLACE)
CLEAR_DERIVED = OLD_CLEAR_DERIVED.replace(_SUGGESTIONS, _SUGGESTIONS + _OURS)

UPGRADE = [
    RUNS,
    PAIRS,
    MEMBERS,
    *[
        statement
        for table, comment in TABLE_COMMENTS.items()
        for statement in (
            f"COMMENT ON TABLE {table} IS {_literal(comment)}",
            *[
                f"COMMENT ON COLUMN {table}.{column} IS {_literal(text)}"
                for column, text in COMMENTS[table].items()
            ],
            f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY",
            f"CREATE POLICY customer_only ON {table} USING ({POLICY}) WITH CHECK ({POLICY})",
            f"COMMENT ON POLICY customer_only ON {table} IS 'A row is seen and written only by its own customer.'",
            *[f"GRANT {GRANTS[table]} ON {table} TO {role}" for role in ROLES],
        )
    ],
    # The pairs of a version being deleted or locked, from its second side too (the key covers the first).
    "CREATE INDEX version_pairs_second ON version_pairs (customer_id, second_id)",
    CLEAR_DERIVED,
]
DOWNGRADE = [
    OLD_CLEAR_DERIVED,
    "DROP TABLE IF EXISTS version_group_members",
    "DROP TABLE IF EXISTS version_pairs",
    "DROP TABLE IF EXISTS version_finder_runs",
]


def upgrade() -> None:
    for statement in UPGRADE:
        op.execute(statement)


def downgrade() -> None:
    for statement in DOWNGRADE:
        op.execute(statement)
