"""AI settings by task and the platform feature switches (Tushar, 17 Oct 2026; 0038).

One rule everywhere::

    effective = min(platform switch, super admin ceiling, organisation default when locked,
                    member's choice)

The ceiling is what the caller already loads (``kaagaz.db.models.effective_settings``, the
auto-filing grant), given as the ``allowed`` levels, lowest first. Levels are ordered
none < low < high < max, and off < on. With no row for a task (``Task.chosen`` false) the
caller keeps today's behaviour exactly; ``effective`` then says what today's is.

The cost-saving and Ensemble switches live inside ``effective_settings`` (so every caller of
the ceiling sees them); scan_ai is the worker's (``worker_main.admission``), ask the chat
route's, and review, my_words and self_signup their routers' (``api.auth.require_feature``).
Each also reads the organisation's own switch (0039) in the same statement: on only while both
are on.

Time and space O(1): one indexed statement for a customer's organisation rows and one user's
rows (at most 8), one for the switches (at most 8).
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from sqlalchemy import Connection, text

from kaagaz.db import audit
from kaagaz.settings import Settings

TASKS = ("scan", "ask", "ensemble", "autofile")
FEATURES = (
    "scan_ai", "ask", "ensemble", "autofile", "cost_saving", "review", "my_words", "self_signup",
    "version_spotting",
)  # fmt: skip
STEPS = ("none", "low", "high", "max")  # scan and ask
SWITCH = ("off", "on")  # ensemble and autofile
_ORDER = {level: n for levels in (STEPS, SWITCH) for n, level in enumerate(levels)}

# (task, organisation row?) -> (level, locked)
Saved = dict[tuple[str, bool], tuple[str, bool]]

_ROWS = text(
    "SELECT user_id IS NULL AS organisation, task, level, locked FROM ai_task_settings"
    " WHERE customer_id = :c AND (user_id IS NULL OR user_id = CAST(:u AS uuid))"
)
_UPSERT = (
    "INSERT INTO ai_task_settings (customer_id, user_id, task, level, locked, updated_by)"
    " VALUES (:c, CAST(:u AS uuid), :t, :l, :k, :by) ON CONFLICT {target} DO UPDATE"
    " SET level = EXCLUDED.level, locked = EXCLUDED.locked, updated_by = EXCLUDED.updated_by,"
    " updated_at = now()"
)
_SAVE_ORGANISATION = text(_UPSERT.format(target="(customer_id, task) WHERE user_id IS NULL"))
_SAVE_MINE = text(_UPSERT.format(target="(customer_id, user_id, task) WHERE user_id IS NOT NULL"))
_CLEAR = text(
    "DELETE FROM ai_task_settings WHERE customer_id = :c AND task = :t"
    " AND user_id IS NOT DISTINCT FROM CAST(:u AS uuid)"
)
# The platform's switches and the organisation's own (0039) in one statement each.
_SWITCHES = text(
    "SELECT feature, enabled FROM feature_switches"
    " UNION ALL SELECT feature, enabled FROM org_feature_switches WHERE customer_id = CAST(:c AS uuid)"
)
_SWITCH = text(
    "SELECT (SELECT enabled FROM feature_switches WHERE feature = :f),"
    " (SELECT enabled FROM org_feature_switches WHERE customer_id = CAST(:c AS uuid) AND feature = :f)"
)


@dataclass(frozen=True, slots=True)
class Task:
    """One task for one person: ``effective`` what runs, ``mine`` their own choice,
    ``organisation`` the default and ``locked``; ``allowed`` the levels the ceiling and the
    switch leave, lowest first; ``available`` false when the switch or the ceiling removed
    the task; ``chosen`` whether a row decided it (false: today's behaviour)."""

    task: str
    effective: str
    mine: str | None
    organisation: str | None
    locked: bool
    allowed: tuple[str, ...]
    available: bool
    chosen: bool

    def view(self) -> dict[str, Any]:
        """The API's shape: never a model name, a price or a cost."""
        return {
            "task": self.task,
            "effective": self.effective,
            "mine": self.mine,
            "organisation": self.organisation,
            "locked": self.locked,
            "allowed": list(self.allowed),
            "available": self.available,
        }


def lowest(*levels: str) -> str:
    """The lowest of ``levels`` (all of one kind)."""
    return min(levels, key=_ORDER.__getitem__)


def rows(conn: Connection, customer_id: str, user_id: str | None) -> Saved:
    """The organisation's rows and ``user_id``'s own (None: the organisation's only). On a
    ``Database.customer()`` connection of ``customer_id``. One statement."""
    found = conn.execute(_ROWS, {"c": customer_id, "u": user_id}).mappings().all()
    return {(row["task"], bool(row["organisation"])): (row["level"], bool(row["locked"])) for row in found}


def from_json(task: str, items: Sequence[Sequence[Any]] | None) -> Saved:
    """``Saved`` from [organisation?, level, locked] triples (the scan's own SQL)."""
    return {(task, bool(item[0])): (str(item[1]), bool(item[2])) for item in items or ()}


def save(
    conn: Connection,
    customer_id: str,
    task: Task,
    *,
    user_id: str | None,
    level: str | None,
    locked: bool,
    by: str,
) -> None:
    """Save ``user_id``'s own choice (None: the organisation's default and ``locked``) for
    ``task`` as ``level`` (None: remove it), with one audit row (who, the task, the old and
    new levels) when it changed. The caller has checked the role, the lock and ``allowed``.
    On a ``Database.customer()`` connection of ``customer_id``. Two statements at most."""
    old, old_locked = (task.mine, False) if user_id is not None else (task.organisation, task.locked)
    if (old, old_locked) == (level, locked and level is not None):
        return
    params = {"c": customer_id, "u": user_id, "t": task.task, "l": level, "k": locked, "by": by}
    if level is None:
        conn.execute(_CLEAR, params)
    else:
        conn.execute(_SAVE_MINE if user_id is not None else _SAVE_ORGANISATION, params)
    audit.record(
        conn,
        customer_id,
        actor_user_id=by,
        action="ai_settings.mine" if user_id is not None else "ai_settings.organisation",
        target_type="ai_task",
        target_id=task.task,
        details={"task": task.task, "old": old, "new": level, "locked": locked and level is not None},
    )


def switches(conn: Connection, customer_id: str) -> dict[str, bool]:
    """Every feature for ``customer_id``: on only while the platform's switch and the
    organisation's own (0039) are on (no row: on). On a ``Database.customer()`` connection.
    One statement."""
    found = dict.fromkeys(FEATURES, True)
    for feature, enabled in conn.execute(_SWITCHES, {"c": customer_id}):
        found[str(feature)] &= bool(enabled)
    return found


def switch_on(conn: Connection, feature: str, customer_id: str | None) -> bool:
    """``feature`` for ``customer_id``: on only while the platform's switch and the
    organisation's own (0039) are on; no row is on, and the platform's off always wins.
    None (self sign-up, no organisation yet): the platform's alone. On a
    ``Database.customer()`` connection of ``customer_id``. One statement."""
    platform, organisation = conn.execute(_SWITCH, {"f": feature, "c": customer_id}).one()
    return platform is not False and organisation is not False


def resolve(task: str, saved: Saved, allowed: Sequence[str], default: str, *, available: bool = True) -> Task:
    """``task`` by the rule (module notes): the organisation's level when locked, else the
    member's, else the organisation's, else ``default`` (today's), never above
    ``allowed[-1]``. O(1)."""
    org, mine = saved.get((task, True)), saved.get((task, False))
    locked = org is not None and org[1]
    chosen = org[0] if org is not None and locked else mine[0] if mine is not None else None
    if chosen is None and org is not None:
        chosen = org[0]
    effective = lowest(chosen or default, allowed[-1])
    return Task(
        task,
        effective,
        None if mine is None else mine[0],
        None if org is None else org[0],
        locked,
        tuple(allowed),
        available,
        chosen is not None,
    )


def scan_ceiling(ceiling: Mapping[Any, Any], settings: Settings) -> tuple[tuple[str, ...], str]:
    """(allowed, today's level) for scanning: High and Max only when the organisation's scan
    thinking is high; today is Low (Automatic) with Dynamic, else the single read's level.
    Keyless (no reading model): None only."""
    if settings.ocr_engine != "gemini":  # reading.ocr.GEMINI, not imported: the worker loads this early
        return ("none",), "none"
    top = (ceiling["scan_thinking"] or settings.ocr_thinking_level) == "high"
    allowed = STEPS if top else STEPS[:2]
    return allowed, "low" if ceiling["dynamic"] or not top else "high"


def ask_ceiling(ceiling: Mapping[Any, Any]) -> tuple[tuple[str, ...], str]:
    """(allowed, today's level) for Ask: High and Max only when high thinking is allowed;
    today is the organisation's default level (high, else low)."""
    top = "high" in (ceiling["chat_levels"] or ["low"])
    today = "high" if ceiling["chat_default_level"] == "high" else "low"
    return (STEPS if top else STEPS[:2]), today


def ask_thinking(level: str, levels: Sequence[str], asked: str | None) -> str:
    """The thinking level an Ask level answers at: Low low, High and Max high (the nearest
    the organisation allows when it lacks it: ``levels`` low to high); the request's own
    level when it is lower (capped, never raised). O(1)."""
    order = ("low", "medium", "high")
    target = "low" if level == "low" else "high"
    if target not in levels:
        target = levels[0] if target == "low" else levels[-1]
    if asked is not None and order.index(asked) < order.index(target):
        return asked
    return target


def grant_ceiling(granted: bool, today_on: bool) -> tuple[tuple[str, ...], str]:
    """(allowed, today's level) for Ensemble and auto-filing: On only with the grant."""
    return (SWITCH if granted else SWITCH[:1]), "on" if granted and today_on else "off"


def task_on(
    conn: Connection, customer_id: str, user_id: str | None, task: str, granted: bool, today_on: bool
) -> bool:
    """Ensemble or auto-filing is on for ``user_id`` (``granted``: the ceiling with its
    switch; ``today_on``: the behaviour with no row). One statement."""
    allowed, default = grant_ceiling(granted, today_on)
    return resolve(task, rows(conn, customer_id, user_id), allowed, default).effective == "on"
