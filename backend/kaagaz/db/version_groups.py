"""The groups of possible versions the version finder stored (V17, #648, V-B), as one person sees
them, and their decisions: "Not versions" (kept apart, undone back), and "Decide later". Nothing
here links a file (V-A does).

What a person is shown, read through ``visible_documents`` only (PRD 7), on a ``Database.user``
connection: of each group, the files they can see that are live (current, ready, so never locked,
and out of Trash) and not decided (open or left for later), and of those only the files with a
pair to another such file, with those pairs as the reasons. So no file they can't see, and no
reason naming one, is ever shown: a file joined to the others only through a hidden one is left
out too. A group with under 2 such files is not shown. Its files keep the finder's order (the
last is the suggested latest); its files kept apart that they can see are listed with it, for
Undo.
"""

from collections.abc import Sequence
from typing import Any

from sqlalchemy import Connection, RowMapping, text

from kaagaz import versions
from kaagaz.db import audit

# The most groups one list returns (the rest: ``more``).
MAX_GROUPS = 200
UNDECIDED = ("open", "later")
ACTIONS = {"kept_apart": "version.kept_apart", "open": "version.kept_apart_undone", "later": "version.later"}
# What each decision may change: Not versions an undecided file, Decide later an undecided one
# (again), Undo a file kept apart.
_FROM = {"kept_apart": UNDECIDED, "later": UNDECIDED, "open": ("kept_apart",)}

# The members this person can see that are live, every group or one (:g).
_MEMBERS = text(
    "SELECT g.group_id::text, g.document_id::text, g.position, g.state, d.display_name, d.folder_id,"
    " d.doc_type, d.document_date, d.created_at, d.page_count"
    " FROM version_group_members g"
    " JOIN visible_documents d ON d.customer_id = g.customer_id AND d.id = g.document_id"
    " WHERE g.customer_id = :c AND (CAST(:g AS uuid) IS NULL OR g.group_id = CAST(:g AS uuid))"
    " AND d.deleted_at IS NULL AND d.is_current AND d.status = 'ready'"
    " ORDER BY g.group_id, g.position"
)
# The pairs between two members of one group that this person can see both of.
_PAIRS = text(
    "SELECT a.group_id::text, p.first_id::text, p.second_id::text, p.text_overlap, p.text_overlap_exact,"
    " p.name_similarity, p.shared_identifier FROM version_pairs p"
    " JOIN version_group_members a ON a.customer_id = p.customer_id AND a.document_id = p.first_id"
    " JOIN version_group_members b ON b.customer_id = p.customer_id AND b.document_id = p.second_id"
    " AND b.group_id = a.group_id"
    " JOIN visible_documents v1 ON v1.customer_id = p.customer_id AND v1.id = p.first_id"
    " JOIN visible_documents v2 ON v2.customer_id = p.customer_id AND v2.id = p.second_id"
    " WHERE p.customer_id = :c AND (CAST(:g AS uuid) IS NULL OR a.group_id = CAST(:g AS uuid))"
)
# One decision on files of one group that this person can see and are live, each from what it may be.
_DECIDE = text(
    "UPDATE version_group_members g SET state = :to, decided_by = :by, decided_at = now()"
    " FROM visible_documents d WHERE g.customer_id = :c AND g.group_id = :g"
    " AND g.document_id = ANY(CAST(:ids AS uuid[])) AND g.state = ANY(CAST(:was AS text[]))"
    " AND d.customer_id = g.customer_id AND d.id = g.document_id"
    " AND d.deleted_at IS NULL AND d.is_current AND d.status = 'ready'"
    " RETURNING g.document_id::text"
)


class Refused(Exception):
    """Not found: a group or file this person isn't shown (404, as an id that never was)."""


def _reason(pair: RowMapping, other: str) -> dict[str, Any]:
    """One reason in V6's plain words ("about 94% the same text", "same name and same survey number")."""
    overlap = None if pair["text_overlap"] is None else float(pair["text_overlap"])
    estimated = overlap is not None and pair["text_overlap_exact"] is not True
    name = None if pair["name_similarity"] is None else float(pair["name_similarity"])
    return {
        "with": other,
        "text_overlap": overlap,
        "text_overlap_estimated": estimated,
        "name_similarity": name,
        "shared_identifier": pair["shared_identifier"],
        "words": versions.reasons(overlap, name, pair["shared_identifier"], estimated=estimated),
    }


def _group(
    group_id: str, members: Sequence[RowMapping], pairs: Sequence[RowMapping]
) -> dict[str, Any] | None:
    """One group as shown (module notes), or None when under 2 of its files are."""
    undecided = {m["document_id"] for m in members if m["state"] in UNDECIDED}
    shown = [p for p in pairs if p["first_id"] in undecided and p["second_id"] in undecided]
    reasons: dict[str, list[dict[str, Any]]] = {}
    for pair in shown:
        reasons.setdefault(pair["first_id"], []).append(_reason(pair, pair["second_id"]))
        reasons.setdefault(pair["second_id"], []).append(_reason(pair, pair["first_id"]))
    files = [m for m in members if m["document_id"] in reasons]
    if len(files) < 2:
        return None
    return {
        "id": group_id,
        "files": [
            {
                "id": m["document_id"],
                "name": m["display_name"],
                "folder_id": None if m["folder_id"] is None else str(m["folder_id"]),
                "doc_type": m["doc_type"],
                "document_date": None if m["document_date"] is None else m["document_date"].isoformat(),
                "uploaded_at": m["created_at"].isoformat(),
                "page_count": m["page_count"],
                "position": position,
                "state": m["state"],
                "reasons": sorted(reasons[m["document_id"]], key=lambda r: r["with"]),
            }
            for position, m in enumerate(files, 1)
        ],
        "suggested_latest_id": files[-1]["document_id"],
        "kept_apart": [
            {"id": m["document_id"], "name": m["display_name"]} for m in members if m["state"] == "kept_apart"
        ],
    }


def groups(conn: Connection, customer_id: str, group_id: str | None = None) -> list[dict[str, Any]]:
    """The groups shown to this person (module notes), those with a file not yet left for later
    first, at most MAX_GROUPS + 1 (the caller says ``more``); with ``group_id``, that one alone (none
    when it isn't shown). Two statements, O(m + p) for the organisation's m members and p pairs."""
    params = {"c": customer_id, "g": group_id}
    members: dict[str, list[RowMapping]] = {}
    for row in conn.execute(_MEMBERS, params).mappings():
        members.setdefault(row["group_id"], []).append(row)
    pairs: dict[str, list[RowMapping]] = {}
    for row in conn.execute(_PAIRS, params).mappings():
        pairs.setdefault(row["group_id"], []).append(row)
    found = [shown for gid, rows in members.items() if (shown := _group(gid, rows, pairs.get(gid, [])))]
    found.sort(key=lambda g: (all(f["state"] == "later" for f in g["files"]), g["id"]))
    return found[: MAX_GROUPS + 1]


def decide(
    conn: Connection, customer_id: str, group_id: str, document_ids: Sequence[str], *, to: str, by: str
) -> None:
    """Not versions (``kept_apart``), Decide later (``later``) or Undo of Not versions (``open``) for
    files of one group, all or none, with one ``version.kept_apart``, ``version.later`` or
    ``version.kept_apart_undone`` row each. Not versions and Decide later take only files the group
    shows this person; Undo, files kept apart they can see that are live. Raises Refused for any
    other (nothing is written: the caller's transaction rolls back). O(m + p) for the group."""
    wanted = sorted(set(document_ids))
    if to != "open":
        shown = groups(conn, customer_id, group_id)
        if not shown or not set(wanted) <= {f["id"] for f in shown[0]["files"]}:
            raise Refused
    params = {"c": customer_id, "g": group_id, "ids": wanted, "was": list(_FROM[to]), "to": to, "by": by}
    done = sorted(conn.execute(_DECIDE, params).scalars())
    if done != wanted:
        raise Refused
    for document in done:
        audit.record(
            conn,
            customer_id,
            actor_user_id=by,
            action=ACTIONS[to],
            target_type="document",
            target_id=document,
            details={"group_id": group_id},
        )
