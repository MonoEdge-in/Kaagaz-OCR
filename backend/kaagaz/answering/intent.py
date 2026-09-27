"""Decide whether a question is a fixed query, and which one.

Numbers come from fixed, tested queries, never from a model writing SQL
(CLAUDE.md). This module only picks one of the four queries in
``fixed_queries.py`` and fills in its field names. It returns a plan only
when every part is certain:

1. Exactly one aggregate word: highest, lowest, total or count.
2. The metric (what is summed or compared) resolves through the synonym table
   to exactly one field this customer has. Two candidate fields present (say
   both ``profit`` and ``net_profit``) is a guess, so no plan: adding or
   comparing two different measures would give a wrong number.
3. "Highest or lowest by party" (whose, who, party, vendor...) also needs at
   least one party field. The party and the date only *find* a record, and
   an invoice and a register name them differently, so every candidate the
   customer has is passed on; the query then uses a record only when it
   holds exactly one of them (fixed_queries.py).
4. A date range in the question needs at least one date field, and a
   question that mentions a date we could not read gets no plan.
5. Every topic word is understood. "Kiska profit sabse jyada tha Pune
   branch me?" is not answered as if "Pune branch" was never said.

Anything else takes the search route, which says "not found" when the
documents do not answer. Never a guessed field (CLAUDE.md never-do 6).

PROVISIONAL: the synonym table and the router rules are Vrushit's
(docs/system-design/03). ``expense`` and the date fields are additions to the
spec's table; say so in the pull request.

Time is O(w + f): one pass over the w words, set lookups against f fields.
"""

from dataclasses import dataclass
from datetime import date
from itertools import pairwise
from typing import Literal

from kaagaz.answering.hinglish import NormalisedQuestion

Aggregate = Literal["highest", "lowest", "total", "count"]

_HIGHEST = frozenset({"highest", "most", "maximum", "max", "largest", "biggest", "top"})
_LOWEST = frozenset({"lowest", "least", "minimum", "min", "smallest"})
_TOTAL = frozenset({"total", "sum"})
_COUNT = frozenset({"count"})

# Topic word -> concept; concept -> candidate fields, in the spec's order.
_METRIC_WORDS = {
    "profit": "profit",
    "profits": "profit",
    "amount": "amount",
    "amounts": "amount",
    "sales": "sales",
    "expense": "expense",
}
_METRIC_FIELDS = {
    "profit": ("profit", "net_profit"),
    "amount": ("total_amount", "amount"),
    "sales": ("sales", "total_sales"),
    "expense": ("expense", "expenses", "total_expense"),
}
_GROUP_WORDS = frozenset(
    {"who", "whose", "party", "parties", "vendor", "vendors", "customer", "customers"}
    | {"supplier", "suppliers", "seller", "sellers"}
)
_GROUP_FIELDS = ("party_name", "name", "customer_name", "vendor")
_DATE_FIELDS = ("invoice_date", "date", "bill_date", "month")
_THINGS = frozenset({"invoice", "invoices", "document", "documents", "files", "file"})
# Topic words that add nothing to a fixed query.
_FILLER = frozenset({"value", "overall", "all", "made", "earned", "got", "number"})


@dataclass(frozen=True, slots=True)
class FixedPlan:
    aggregate: Aggregate
    metric: str | None  # None for count
    group_fields: tuple[str, ...]  # set only for highest or lowest by party
    date_fields: tuple[str, ...]  # empty: no date filter (and never for count)
    start: date | None
    end: date | None


def plan_fixed(
    normalised: NormalisedQuestion,
    date_range: tuple[date, date] | None,
    mentions_date: bool,
    available_fields: set[str],
) -> FixedPlan | None:
    """A fixed-query plan, or None to take the search route."""
    words = normalised.words
    aggregate = _aggregate(words)
    if aggregate is None or (mentions_date and date_range is None):
        return None

    metric_concepts = {_METRIC_WORDS[word] for word in words if word in _METRIC_WORDS}
    group_asked = any(word in _GROUP_WORDS for word in words)
    things_asked = any(word in _THINGS for word in words)
    known = _HIGHEST | _LOWEST | _TOTAL | _COUNT | set(_METRIC_WORDS) | _GROUP_WORDS | _THINGS | _FILLER
    if any(word not in known for word in normalised.keywords):
        return None

    start, end = date_range if date_range is not None else (None, None)
    date_fields: tuple[str, ...] = ()
    if date_range is not None or aggregate == "count":
        date_fields = _present(_DATE_FIELDS, available_fields)
        if not date_fields:
            return None

    if aggregate == "count":
        if not things_asked or metric_concepts or group_asked:
            return None
        return FixedPlan("count", None, (), date_fields, start, end)

    if not metric_concepts and "total" in words:
        metric_concepts = {"amount"}  # "kul kitna": the total amount
    if len(metric_concepts) != 1:
        return None
    metric = _one_field(_METRIC_FIELDS[metric_concepts.pop()], available_fields)
    if metric is None:
        return None

    group_fields: tuple[str, ...] = ()
    if group_asked:
        if aggregate == "total":
            return None  # "whose total" is a ranking, not one total: not a question we can pin down
        group_fields = _present(_GROUP_FIELDS, available_fields)
        if not group_fields:
            return None
    return FixedPlan(aggregate, metric, group_fields, date_fields, start, end)


def _aggregate(words: list[str]) -> Aggregate | None:
    """The one aggregate asked for. "how much" means a total unless highest or lowest is asked."""
    highest = any(word in _HIGHEST for word in words)
    lowest = any(word in _LOWEST for word in words)
    if highest and lowest:
        return None
    if highest:
        return "highest"
    if lowest:
        return "lowest"
    if any(word in _COUNT for word in words) or _has_phrase(words, "how", "many"):
        return "count"
    if any(word in _TOTAL for word in words) or _has_phrase(words, "how", "much"):
        return "total"
    return None


def _has_phrase(words: list[str], first: str, second: str) -> bool:
    return any(a == first and b == second for a, b in pairwise(words))


def _one_field(candidates: tuple[str, ...], available: set[str]) -> str | None:
    present = _present(candidates, available)
    return present[0] if len(present) == 1 else None


def _present(candidates: tuple[str, ...], available: set[str]) -> tuple[str, ...]:
    return tuple(field for field in candidates if field in available)
