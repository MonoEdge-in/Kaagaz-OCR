"""The fixed, tested queries that every number in an answer comes from.

CLAUDE.md: numbers come from fixed queries, never from an AI writing SQL. The
queries run over ``extracted_values``, one row per value (field, text,
number, date, page and region). A **record** is one (document_id,
record_key): one spreadsheet row, or one invoice's labelled values. The
field names are bound parameters, never SQL identifiers, so a field name can
only ever be compared with, never run.

Rules shared by every query (never a guess, CLAUDE.md never-do 6):

* Only documents with status ``ready``, always inside ``db.customer`` and
  always ``WHERE customer_id = :customer_id`` (the second lock next to row
  level security).
* Only the current version of a family, and nothing in Trash (PRD 1 F5 and
  F6): the ``LIVE_CURRENT`` text (kaagaz/db/visibility.py) is written out in
  every CTE that reads values, and in the lookup of the cited values.
* Only documents inside the chat's scope (PRD 3 H2, ``scoped``): the
  ``Scope.params()`` predicates of kaagaz/search/hybrid.py, written out in the
  same places, so a document outside the scope never changes a number.
* The metric is one field. The party and the date are each a list of
  candidate fields (``party_name``, ``name``...), because an invoice and a
  register name them differently; the record's own value is used.
* A record takes part only if it holds exactly one metric value, exactly one
  value among the party candidates, and exactly one among the date
  candidates, as the query needs them. Two ``profit`` values in one row, or
  both ``date`` and ``invoice_date``, would be a guess, so that record is
  left out. The metric must have a number.
* A labelled document (an invoice) counts once: its ``document`` record is
  used, and the ``page:{p}:line:{n}`` records (a field printed again further
  on, kaagaz/extraction/labelled.py) are left out of the total, the top party,
  the extreme value and the document count. Spreadsheet rows (``sheet:...``)
  count as records of their own, except the rows of a field the document also
  has in its ``document`` record (a spreadsheet read as an invoice, PRD 2 E4):
  those rows are left out of the same four, so the invoice's total is counted
  once, never beside its cells.
* The document card's values (record ``card``, PRD 2 D3) never take part, nor
  count as available fields: the model chose their fields (security amendment
  A1, council fix 9).
* An invoice's line items (the ``line:{n}`` records, PRD 2 E4) are never used,
  not even in the list of fields questions can name. A line's ``amount`` field
  would make "amount" (``total_amount`` or ``amount``, kaagaz/answering/intent.py)
  ambiguous for every customer with invoices, and "total amount" questions would
  stop reaching a fixed query (PRD 2 plan conflict 1). The invoice's own total is
  its ``document`` record's.
* PROVISIONAL (PRD 6 C2): a sheet row is the sheet's printed total, never a record, when one of
  its cells (any column, read from the row's piece, so a label under no usable header counts)
  is or starts with total, totals, grand total, net total or sub total (any case), or एकूण,
  एकुण, कुल or योग, and its one figure equals the sum of its sheet's other rows. The highest,
  the lowest and a party's sum leave it out; ``figures`` returns it marked beside its sheet's
  matching rows when no range is asked, so no total is added up over them (answering/totals.py).
  A row that reads like a total but does not add up ("Total Solutions Pvt Ltd", a sub total) is
  a doubtful record: never dropped, it withholds a total or a ranking (``looks_total``); a
  highest or lowest leaves it out of the value shown and says so (``totals``, needs review).
  Each sheet's other rows are summed once. Decided when asked: stored values need no migration.
  Known limit: a party named "Total ..." whose figure happens to equal the others' sum is taken
  as the sheet's total.
* The date filter is ``[start, end)`` on the record's own date value. No
  date candidates means no date filter.
* Party names are grouped ignoring case and repeated spaces; the name shown
  is the one written in the group's largest record.

A metric value of an invoice whose check failed is never verified, nor is its
cited value (PRD 3 edge case 10), whatever its own reading label says.

Every row carries the values it used (``values``, at most
``MAX_CITED_VALUES``, each with its page and region) for the citations, and
``verified`` over *every* value used, cited or not; a highest or lowest also carries
``unsure``, every value compared (any row's, shown or not) that is not verified, or a record
whose figure is not one number (C2). A party's sum is asked for only once the numbers rule
holds over every record (answering/service.py), so every value it uses is verified.

A total is not summed here (PRD 6 C, answering/totals.py): ``figures`` lists every
record the question matches: an ``invoices`` row (dated by ``invoice_date``) when
it names invoices, else any record of a sheet, or a document's own record, that
holds the metric (a row leaving it empty too); with a range, also an undated one.

Each call is one transaction with two statements: the aggregate, then one
lookup of the cited values' pages and regions (no N+1). Time is O(v) in the
customer's values of the fields asked, through the (customer_id, field)
index; space is O(r) in the result rows (``figures``: in the matching records).
"""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Any, Protocol

from sqlalchemy import Connection, TextClause, text

from kaagaz.db.engine import Database
from kaagaz.search.scope import EVERYTHING, Scope

MAX_CITED_VALUES = 20  # PROVISIONAL: how many sources one number shows
_TOP_ROWS = 5  # enough to see a tie for first place


@dataclass(frozen=True, slots=True)
class ValueRef:
    """One stored value, with where it sits, for a citation."""

    value_id: str
    document_id: str
    document_name: str
    detected_type: str
    page_no: int
    region: dict[str, Any]
    field: str
    value_text: str
    label: str
    label_page_no: int | None = None  # where its label is, when located (PRD 2 D3, PRD 3 H4)
    label_region: dict[str, Any] | None = None
    label_text: str | None = None  # a map value's label as read (0054); NULL before it


@dataclass(frozen=True, slots=True)
class FixedRow:
    group: str | None  # the party name (top_group only)
    number: Decimal  # the sum, the single value, or the count
    records: int  # how many records went into it
    verified: bool  # every value used is verified
    values: list[ValueRef]  # the values cited, at most MAX_CITED_VALUES
    # Highest or lowest (PRD 6 C2): every value compared, of any row, that is not verified or
    # not one number (at most MAX_CITED_VALUES), the same on each row.
    unsure: tuple[ValueRef, ...] = ()
    totals: tuple[ValueRef, ...] = ()  # C2: compared values that read like a total, left out


@dataclass(frozen=True, slots=True)
class Figure:
    """One record a total question matches (PRD 6 C): its figure as printed, or why it has none."""

    value: ValueRef | None  # its one figure; with none, its first value; None: nothing to cite
    amount: Decimal | None  # the figure's number, only when the record holds exactly one figure
    printed: int  # how many values of the metric the record holds
    verified: bool  # that one figure is verified, and its invoice's check did not fail
    dated: bool  # it has the one readable date a date range needs (True without a range)
    date_verified: bool  # that date is verified (True without a range)
    grouped: bool = True  # it names one party, as a ranking by party needs (True without one)
    party_verified: bool = True  # that party name is verified (True without one)
    printed_total: bool = False  # C2: a sheet's printed total row, never a record
    looks_total: bool = False  # C2: reads like a total but does not add up: doubtful


class FixedQueries(Protocol):
    def scoped(self, scope: Scope) -> "FixedQueries":
        """The same queries over the documents inside ``scope`` only (PRD 3 H2)."""
        ...

    def available_fields(self, customer_id: str, fields: Sequence[str]) -> set[str]: ...

    def top_group(
        self,
        customer_id: str,
        metric: str,
        group_fields: Sequence[str],
        date_fields: Sequence[str],
        start: date | None,
        end: date | None,
        descending: bool,
    ) -> list[FixedRow]: ...

    def figures(
        self,
        customer_id: str,
        metric: str,
        date_fields: Sequence[str],
        start: date | None,
        end: date | None,
        *,
        invoices: bool,
        group_fields: Sequence[str] = (),
    ) -> list[Figure]: ...

    def count_documents(
        self, customer_id: str, start: date | None, end: date | None, *, date_fields: Sequence[str]
    ) -> list[FixedRow]: ...

    def count_invoices(self, customer_id: str, start: date | None, end: date | None) -> list[FixedRow]: ...

    def extreme_value(
        self,
        customer_id: str,
        metric: str,
        date_fields: Sequence[str],
        start: date | None,
        end: date | None,
        descending: bool,
    ) -> list[FixedRow]: ...


# Which of the fields asked the customer has: one EXISTS each, which stops at the
# first value (the (customer_id, field) index), never a DISTINCT over every value.
_AVAILABLE_FIELDS = text(
    """
    SELECT c.field
    FROM unnest(CAST(:candidates AS text[])) AS c(field)
    WHERE EXISTS (
        SELECT 1
        FROM extracted_values v
        JOIN documents d ON d.customer_id = v.customer_id AND d.id = v.document_id
        WHERE v.customer_id = :customer_id AND d.deleted_at IS NULL AND d.is_current AND d.status = 'ready'
          AND (CAST(:s_folders AS uuid[]) IS NULL OR d.folder_id = ANY(CAST(:s_folders AS uuid[])))
          AND (CAST(:s_tags AS uuid[]) IS NULL OR d.id IN (SELECT dt.document_id FROM document_tags dt
            WHERE dt.customer_id = :customer_id AND dt.tag_id = ANY(CAST(:s_tags AS uuid[]))
            GROUP BY dt.document_id HAVING count(DISTINCT dt.tag_id) = cardinality(CAST(:s_tags AS uuid[]))))
          AND (CAST(:s_doc_types AS text[]) IS NULL OR d.doc_type = ANY(CAST(:s_doc_types AS text[])))
          AND (CAST(:s_detected_type AS text) IS NULL OR d.detected_type = CAST(:s_detected_type AS text))
          AND (CAST(:s_documents AS uuid[]) IS NULL OR d.id = ANY(CAST(:s_documents AS uuid[])))
          AND (CAST(:s_from AS date) IS NULL AND CAST(:s_to AS date) IS NULL
            OR coalesce(d.document_date, (d.created_at AT TIME ZONE CAST(:s_tz AS text))::date)
            BETWEEN coalesce(CAST(:s_from AS date), '-infinity')
            AND coalesce(CAST(:s_to AS date), 'infinity'))
          AND v.field = c.field
          AND v.record_key <> 'card'
          AND v.record_key NOT LIKE 'line:%')
    """
)

_TOP_GROUP = text(
    """
    WITH labelled AS (  -- C2, PROVISIONAL: the rows of the metric's sheets that read like a total
        SELECT p.customer_id, p.document_id,
               'sheet:' || (p.regions -> 0 ->> 'page') || ':row:' || (p.regions -> 0 ->> 'row')
                   AS record_key
        FROM pieces p
        WHERE p.customer_id = :customer_id AND p.regions -> 0 ->> 'kind' = 'cell'
          AND p.document_id IN (SELECT o.document_id FROM extracted_values o
              WHERE o.customer_id = :customer_id AND o.field = :metric AND o.record_key LIKE 'sheet:%')
          AND lower(p.text)  -- a cell ("A: Total Rs. | C: 3,500") that is or starts with a total word
              ~ '(^|[|] )[a-z]{1,3}: ((grand|net|sub)[[:space:]-]*)?totals?([^a-z]|$)'
                '|(^|[|] )[a-z]{1,3}: (एकूण|एकुण|कुल|योग)([[:space:]:.,;()/0-9-]|$)'
    ), sums AS (  -- each sheet's other rows, summed once
        SELECT t.customer_id, t.document_id, split_part(t.record_key, ':row:', 1) AS sheet,
               sum(t.value_number) AS total
        FROM extracted_values t
        WHERE t.customer_id = :customer_id AND t.field = :metric AND t.record_key LIKE 'sheet:%'
          AND t.document_id IN (SELECT l.document_id FROM labelled l)
          AND NOT EXISTS (SELECT 1 FROM labelled x WHERE x.customer_id = :customer_id
              AND x.document_id = t.document_id AND x.record_key = t.record_key)
        GROUP BY t.customer_id, t.document_id, split_part(t.record_key, ':row:', 1)
    ), totals AS (  -- printed: its one figure is the sum of its sheet's other rows; else doubtful
        SELECT l.customer_id, l.document_id, l.record_key,
               coalesce(count(t.id) = 1 AND min(t.value_number) = min(s.total), false) AS printed
        FROM labelled l
        LEFT JOIN extracted_values t ON t.customer_id = l.customer_id AND t.document_id = l.document_id
            AND t.record_key = l.record_key AND t.field = :metric
        LEFT JOIN sums s ON s.customer_id = l.customer_id AND s.document_id = l.document_id
            AND s.sheet = split_part(l.record_key, ':row:', 1)
        GROUP BY l.customer_id, l.document_id, l.record_key
    ), metric AS (
        SELECT v.document_id, v.record_key, (array_agg(v.id))[1] AS value_id,
               (array_agg(v.value_number))[1] AS amount,
               bool_and(v.label = 'verified') AND NOT EXISTS (SELECT 1 FROM invoices i
                   WHERE i.customer_id = :customer_id AND i.document_id = v.document_id
                   AND i.check_status = 'failed') AS verified
        FROM extracted_values v
        JOIN documents d ON d.customer_id = v.customer_id AND d.id = v.document_id
        WHERE v.customer_id = :customer_id AND d.deleted_at IS NULL AND d.is_current AND d.status = 'ready'
          AND (CAST(:s_folders AS uuid[]) IS NULL OR d.folder_id = ANY(CAST(:s_folders AS uuid[])))
          AND (CAST(:s_tags AS uuid[]) IS NULL OR d.id IN (SELECT dt.document_id FROM document_tags dt
            WHERE dt.customer_id = :customer_id AND dt.tag_id = ANY(CAST(:s_tags AS uuid[]))
            GROUP BY dt.document_id HAVING count(DISTINCT dt.tag_id) = cardinality(CAST(:s_tags AS uuid[]))))
          AND (CAST(:s_doc_types AS text[]) IS NULL OR d.doc_type = ANY(CAST(:s_doc_types AS text[])))
          AND (CAST(:s_detected_type AS text) IS NULL OR d.detected_type = CAST(:s_detected_type AS text))
          AND (CAST(:s_documents AS uuid[]) IS NULL OR d.id = ANY(CAST(:s_documents AS uuid[])))
          AND (CAST(:s_from AS date) IS NULL AND CAST(:s_to AS date) IS NULL
            OR coalesce(d.document_date, (d.created_at AT TIME ZONE CAST(:s_tz AS text))::date)
            BETWEEN coalesce(CAST(:s_from AS date), '-infinity')
            AND coalesce(CAST(:s_to AS date), 'infinity'))
          AND v.field = :metric AND v.record_key NOT LIKE 'page:%'
          AND v.record_key <> 'card'
          AND v.record_key NOT LIKE 'line:%'
          AND NOT (v.record_key LIKE 'sheet:%' AND EXISTS (
              SELECT 1 FROM extracted_values o
              WHERE o.customer_id = :customer_id AND o.document_id = v.document_id
                AND o.field = v.field AND o.record_key = 'document'))
          AND NOT EXISTS (SELECT 1 FROM totals x WHERE x.customer_id = :customer_id
              AND x.document_id = v.document_id AND x.record_key = v.record_key AND x.printed)
        GROUP BY v.document_id, v.record_key
        HAVING count(*) = 1 AND bool_and(v.value_number IS NOT NULL)
    ), grp AS (
        SELECT v.document_id, v.record_key, (array_agg(v.id))[1] AS value_id,
               (array_agg(v.value_text))[1] AS name, bool_and(v.label = 'verified') AS verified
        FROM extracted_values v
        JOIN documents d ON d.customer_id = v.customer_id AND d.id = v.document_id
        WHERE v.customer_id = :customer_id AND d.deleted_at IS NULL AND d.is_current AND d.status = 'ready'
          AND (CAST(:s_folders AS uuid[]) IS NULL OR d.folder_id = ANY(CAST(:s_folders AS uuid[])))
          AND (CAST(:s_tags AS uuid[]) IS NULL OR d.id IN (SELECT dt.document_id FROM document_tags dt
            WHERE dt.customer_id = :customer_id AND dt.tag_id = ANY(CAST(:s_tags AS uuid[]))
            GROUP BY dt.document_id HAVING count(DISTINCT dt.tag_id) = cardinality(CAST(:s_tags AS uuid[]))))
          AND (CAST(:s_doc_types AS text[]) IS NULL OR d.doc_type = ANY(CAST(:s_doc_types AS text[])))
          AND (CAST(:s_detected_type AS text) IS NULL OR d.detected_type = CAST(:s_detected_type AS text))
          AND (CAST(:s_documents AS uuid[]) IS NULL OR d.id = ANY(CAST(:s_documents AS uuid[])))
          AND (CAST(:s_from AS date) IS NULL AND CAST(:s_to AS date) IS NULL
            OR coalesce(d.document_date, (d.created_at AT TIME ZONE CAST(:s_tz AS text))::date)
            BETWEEN coalesce(CAST(:s_from AS date), '-infinity')
            AND coalesce(CAST(:s_to AS date), 'infinity'))
          AND v.field = ANY(CAST(:group_fields AS text[])) AND v.record_key NOT LIKE 'page:%'
          AND v.record_key <> 'card'
          AND v.record_key NOT LIKE 'line:%'
          AND NOT (v.record_key LIKE 'sheet:%' AND EXISTS (
              SELECT 1 FROM extracted_values o
              WHERE o.customer_id = :customer_id AND o.document_id = v.document_id
                AND o.field = :metric AND o.record_key = 'document'))
        GROUP BY v.document_id, v.record_key
        HAVING count(*) = 1 AND bool_and(btrim(v.value_text) <> '')
    ), dated AS (
        SELECT v.document_id, v.record_key, (array_agg(v.id))[1] AS value_id,
               bool_and(v.label = 'verified') AS verified
        FROM extracted_values v
        JOIN documents d ON d.customer_id = v.customer_id AND d.id = v.document_id
        WHERE v.customer_id = :customer_id AND d.deleted_at IS NULL AND d.is_current AND d.status = 'ready'
          AND (CAST(:s_folders AS uuid[]) IS NULL OR d.folder_id = ANY(CAST(:s_folders AS uuid[])))
          AND (CAST(:s_tags AS uuid[]) IS NULL OR d.id IN (SELECT dt.document_id FROM document_tags dt
            WHERE dt.customer_id = :customer_id AND dt.tag_id = ANY(CAST(:s_tags AS uuid[]))
            GROUP BY dt.document_id HAVING count(DISTINCT dt.tag_id) = cardinality(CAST(:s_tags AS uuid[]))))
          AND (CAST(:s_doc_types AS text[]) IS NULL OR d.doc_type = ANY(CAST(:s_doc_types AS text[])))
          AND (CAST(:s_detected_type AS text) IS NULL OR d.detected_type = CAST(:s_detected_type AS text))
          AND (CAST(:s_documents AS uuid[]) IS NULL OR d.id = ANY(CAST(:s_documents AS uuid[])))
          AND (CAST(:s_from AS date) IS NULL AND CAST(:s_to AS date) IS NULL
            OR coalesce(d.document_date, (d.created_at AT TIME ZONE CAST(:s_tz AS text))::date)
            BETWEEN coalesce(CAST(:s_from AS date), '-infinity')
            AND coalesce(CAST(:s_to AS date), 'infinity'))
          AND v.field = ANY(CAST(:date_fields AS text[])) AND v.record_key NOT LIKE 'page:%'
          AND v.record_key <> 'card'
          AND v.record_key NOT LIKE 'line:%'
          AND NOT (v.record_key LIKE 'sheet:%' AND EXISTS (
              SELECT 1 FROM extracted_values o
              WHERE o.customer_id = :customer_id AND o.document_id = v.document_id
                AND o.field = :metric AND o.record_key = 'document'))
        GROUP BY v.document_id, v.record_key
        HAVING count(*) = 1 AND bool_and(v.value_date IS NOT NULL AND (
            CAST(:start AS date) IS NULL
            OR (v.value_date >= CAST(:start AS date) AND v.value_date < CAST(:end AS date))))
    ), records AS (
        SELECT m.document_id, m.record_key, m.value_id AS metric_id, m.amount,
               g.value_id AS group_id, g.name,
               lower(regexp_replace(btrim(g.name), '[[:space:]]+', ' ', 'g')) AS name_key,
               dt.value_id AS date_id,
               m.verified AND g.verified AND coalesce(dt.verified, true) AS verified
        FROM metric m
        JOIN grp g ON g.document_id = m.document_id AND g.record_key = m.record_key
        LEFT JOIN dated dt ON dt.document_id = m.document_id AND dt.record_key = m.record_key
        WHERE cardinality(CAST(:date_fields AS text[])) = 0 OR dt.value_id IS NOT NULL
    )
    SELECT (array_agg(r.name ORDER BY r.amount DESC, r.document_id, r.record_key))[1] AS name,
           sum(r.amount) AS number, count(*) AS records,
           bool_and(r.verified) AS verified,
           array_agg(r.metric_id::text ORDER BY r.amount DESC, r.document_id, r.record_key) AS metric_ids,
           array_agg(r.group_id::text ORDER BY r.amount DESC, r.document_id, r.record_key) AS group_ids,
           array_remove(array_agg(r.date_id::text ORDER BY r.amount DESC, r.document_id, r.record_key),
                        NULL) AS date_ids
    FROM records r
    GROUP BY r.name_key
    ORDER BY (CASE WHEN CAST(:descending AS boolean) THEN -1 ELSE 1 END) * sum(r.amount), r.name_key
    LIMIT :limit
    """
)

# PRD 6 C: every record a total question matches, one row each, with its one figure or why it
# has none (the module docstring). A sheet's rows are left out when the document's own record
# holds the metric (an invoice counts once); page, line and card records never match. C2: a
# sheet's printed total row comes marked, beside its sheet's matching rows, with no range;
# ``grouped``: the record names one party (``group_fields``, a ranking's), or none is asked.
_FIGURES = text(
    """
    WITH labelled AS (  -- C2, PROVISIONAL: the rows of the metric's sheets that read like a total
        SELECT p.customer_id, p.document_id,
               'sheet:' || (p.regions -> 0 ->> 'page') || ':row:' || (p.regions -> 0 ->> 'row')
                   AS record_key
        FROM pieces p
        WHERE p.customer_id = :customer_id AND p.regions -> 0 ->> 'kind' = 'cell'
          AND p.document_id IN (SELECT o.document_id FROM extracted_values o
              WHERE o.customer_id = :customer_id AND o.field = :metric AND o.record_key LIKE 'sheet:%')
          AND lower(p.text)  -- a cell ("A: Total Rs. | C: 3,500") that is or starts with a total word
              ~ '(^|[|] )[a-z]{1,3}: ((grand|net|sub)[[:space:]-]*)?totals?([^a-z]|$)'
                '|(^|[|] )[a-z]{1,3}: (एकूण|एकुण|कुल|योग)([[:space:]:.,;()/0-9-]|$)'
    ), sums AS (  -- each sheet's other rows, summed once
        SELECT t.customer_id, t.document_id, split_part(t.record_key, ':row:', 1) AS sheet,
               sum(t.value_number) AS total
        FROM extracted_values t
        WHERE t.customer_id = :customer_id AND t.field = :metric AND t.record_key LIKE 'sheet:%'
          AND t.document_id IN (SELECT l.document_id FROM labelled l)
          AND NOT EXISTS (SELECT 1 FROM labelled x WHERE x.customer_id = :customer_id
              AND x.document_id = t.document_id AND x.record_key = t.record_key)
        GROUP BY t.customer_id, t.document_id, split_part(t.record_key, ':row:', 1)
    ), totals AS (  -- printed: its one figure is the sum of its sheet's other rows; else doubtful
        SELECT l.customer_id, l.document_id, l.record_key,
               coalesce(count(t.id) = 1 AND min(t.value_number) = min(s.total), false) AS printed
        FROM labelled l
        LEFT JOIN extracted_values t ON t.customer_id = l.customer_id AND t.document_id = l.document_id
            AND t.record_key = l.record_key AND t.field = :metric
        LEFT JOIN sums s ON s.customer_id = l.customer_id AND s.document_id = l.document_id
            AND s.sheet = split_part(l.record_key, ':row:', 1)
        GROUP BY l.customer_id, l.document_id, l.record_key
    ), tables AS (
        SELECT DISTINCT v.document_id, split_part(v.record_key, ':row:', 1) AS part, d.display_name
        FROM extracted_values v
        JOIN documents d ON d.customer_id = v.customer_id AND d.id = v.document_id
        WHERE v.customer_id = :customer_id AND d.deleted_at IS NULL AND d.is_current AND d.status = 'ready'
          AND (CAST(:s_folders AS uuid[]) IS NULL OR d.folder_id = ANY(CAST(:s_folders AS uuid[])))
          AND (CAST(:s_tags AS uuid[]) IS NULL OR d.id IN (SELECT dt.document_id FROM document_tags dt
            WHERE dt.customer_id = :customer_id AND dt.tag_id = ANY(CAST(:s_tags AS uuid[]))
            GROUP BY dt.document_id HAVING count(DISTINCT dt.tag_id) = cardinality(CAST(:s_tags AS uuid[]))))
          AND (CAST(:s_doc_types AS text[]) IS NULL OR d.doc_type = ANY(CAST(:s_doc_types AS text[])))
          AND (CAST(:s_detected_type AS text) IS NULL OR d.detected_type = CAST(:s_detected_type AS text))
          AND (CAST(:s_documents AS uuid[]) IS NULL OR d.id = ANY(CAST(:s_documents AS uuid[])))
          AND (CAST(:s_from AS date) IS NULL AND CAST(:s_to AS date) IS NULL
            OR coalesce(d.document_date, (d.created_at AT TIME ZONE CAST(:s_tz AS text))::date)
            BETWEEN coalesce(CAST(:s_from AS date), '-infinity')
            AND coalesce(CAST(:s_to AS date), 'infinity'))
          AND NOT CAST(:invoices AS boolean)
          AND v.field = :metric AND v.record_key NOT LIKE 'page:%'
          AND v.record_key <> 'card'
          AND v.record_key NOT LIKE 'line:%'
    ), invoiced AS (
        SELECT i.document_id, i.invoice_date, d.display_name
        FROM invoices i
        JOIN documents d ON d.customer_id = i.customer_id AND d.id = i.document_id
        WHERE i.customer_id = :customer_id AND d.deleted_at IS NULL AND d.is_current AND d.status = 'ready'
          AND (CAST(:s_folders AS uuid[]) IS NULL OR d.folder_id = ANY(CAST(:s_folders AS uuid[])))
          AND (CAST(:s_tags AS uuid[]) IS NULL OR d.id IN (SELECT dt.document_id FROM document_tags dt
            WHERE dt.customer_id = :customer_id AND dt.tag_id = ANY(CAST(:s_tags AS uuid[]))
            GROUP BY dt.document_id HAVING count(DISTINCT dt.tag_id) = cardinality(CAST(:s_tags AS uuid[]))))
          AND (CAST(:s_doc_types AS text[]) IS NULL OR d.doc_type = ANY(CAST(:s_doc_types AS text[])))
          AND (CAST(:s_detected_type AS text) IS NULL OR d.detected_type = CAST(:s_detected_type AS text))
          AND (CAST(:s_documents AS uuid[]) IS NULL OR d.id = ANY(CAST(:s_documents AS uuid[])))
          AND (CAST(:s_from AS date) IS NULL AND CAST(:s_to AS date) IS NULL
            OR coalesce(d.document_date, (d.created_at AT TIME ZONE CAST(:s_tz AS text))::date)
            BETWEEN coalesce(CAST(:s_from AS date), '-infinity')
            AND coalesce(CAST(:s_to AS date), 'infinity'))
          AND CAST(:invoices AS boolean)
    ), records AS (
        SELECT v.document_id, v.record_key,
               count(*) FILTER (WHERE v.field = :metric) AS printed,
               (array_agg(v.id) FILTER (WHERE v.field = :metric))[1] AS figure_id,
               (array_agg(v.value_number) FILTER (WHERE v.field = :metric))[1] AS amount,
               coalesce(bool_and(v.label = 'verified') FILTER (WHERE v.field = :metric), false)
                   AS figure_verified,
               count(*) FILTER (WHERE v.field = ANY(CAST(:date_fields AS text[]))) AS dates,
               (array_agg(v.value_date) FILTER (WHERE v.field = ANY(CAST(:date_fields AS text[]))))[1] AS day,
               coalesce(bool_and(v.label = 'verified') FILTER (
                   WHERE v.field = ANY(CAST(:date_fields AS text[]))), false) AS date_verified,
               (array_agg(v.id ORDER BY array_position(ARRAY['invoice_number', 'total_amount', 'invoice_date',
                   'seller_name', 'buyer_name']::text[], v.field), v.page_no, v.id))[1] AS first_id,
               count(*) FILTER (WHERE v.field = ANY(CAST(:group_fields AS text[]))) AS parties,
               coalesce(bool_and(btrim(v.value_text) <> '') FILTER (
                   WHERE v.field = ANY(CAST(:group_fields AS text[]))), false) AS named,
               coalesce(bool_and(v.label = 'verified') FILTER (
                   WHERE v.field = ANY(CAST(:group_fields AS text[]))), false) AS party_verified
        FROM extracted_values v
        WHERE v.customer_id = :customer_id
          AND ((v.document_id, split_part(v.record_key, ':row:', 1)) IN (
                SELECT t.document_id, t.part FROM tables t)
            OR (v.record_key = 'document' AND v.document_id IN (SELECT i.document_id FROM invoiced i)))
          AND NOT (v.record_key LIKE 'sheet:%' AND EXISTS (
              SELECT 1 FROM extracted_values o
              WHERE o.customer_id = :customer_id AND o.document_id = v.document_id
                AND o.field = :metric AND o.record_key = 'document'))
        GROUP BY v.document_id, v.record_key
    ), matching AS (
        SELECT r.document_id, r.record_key, t.display_name, r.printed, r.figure_id, r.amount,
               r.figure_verified, r.first_id, r.dates = 1 AND r.day IS NOT NULL AS dated, r.day,
               r.date_verified,
               EXISTS (SELECT 1 FROM totals x WHERE x.customer_id = :customer_id AND x.printed
                   AND x.document_id = r.document_id AND x.record_key = r.record_key) AS printed_total,
               EXISTS (SELECT 1 FROM totals x WHERE x.customer_id = :customer_id AND NOT x.printed
                   AND x.document_id = r.document_id AND x.record_key = r.record_key) AS looks_total,
               cardinality(CAST(:group_fields AS text[])) = 0 OR (r.parties = 1 AND r.named) AS grouped,
               cardinality(CAST(:group_fields AS text[])) = 0 OR r.party_verified AS party_verified
        FROM records r
        JOIN tables t ON t.document_id = r.document_id AND t.part = split_part(r.record_key, ':row:', 1)
        UNION ALL
        SELECT i.document_id, 'document', i.display_name, coalesce(r.printed, 0), r.figure_id, r.amount,
               coalesce(r.figure_verified, false), r.first_id, i.invoice_date IS NOT NULL, i.invoice_date,
               coalesce(r.dates = 1 AND r.date_verified AND r.day = i.invoice_date, false), false, false,
               cardinality(CAST(:group_fields AS text[])) = 0 OR coalesce(r.parties = 1 AND r.named, false),
               cardinality(CAST(:group_fields AS text[])) = 0 OR coalesce(r.party_verified, false)
        FROM invoiced i
        LEFT JOIN records r ON r.document_id = i.document_id AND r.record_key = 'document'
    ), kept AS (
        SELECT m.* FROM matching m
        WHERE NOT m.printed_total AND (CAST(:start AS date) IS NULL OR NOT m.dated
           OR (m.day >= CAST(:start AS date) AND m.day < CAST(:end AS date)))
    ), listed AS (  -- C2: the printed total row of a sheet, beside its rows that match, with no range
        SELECT * FROM kept
        UNION ALL
        SELECT m.* FROM matching m
        WHERE m.printed_total AND CAST(:start AS date) IS NULL
          AND (m.document_id, split_part(m.record_key, ':row:', 1)) IN (
            SELECT k.document_id, split_part(k.record_key, ':row:', 1) FROM kept k)
    )
    SELECT m.printed, CASE WHEN m.printed = 1 THEN m.figure_id ELSE m.first_id END AS value_id,
           CASE WHEN m.printed = 1 THEN m.amount END AS amount,
           m.printed = 1 AND m.amount IS NOT NULL AND m.figure_verified AND NOT EXISTS (
               SELECT 1 FROM invoices f WHERE f.customer_id = :customer_id
               AND f.document_id = m.document_id AND f.check_status = 'failed') AS verified,
           CAST(:start AS date) IS NULL OR m.dated AS dated,
           CAST(:start AS date) IS NULL OR (m.dated AND m.date_verified) AS date_verified,
           m.grouped, m.party_verified, m.printed_total, m.looks_total
    FROM listed m
    ORDER BY m.day, m.display_name, m.document_id, length(m.record_key), m.record_key
    """
)

_EXTREME_VALUE = text(
    """
    WITH labelled AS (  -- C2, PROVISIONAL: the rows of the metric's sheets that read like a total
        SELECT p.customer_id, p.document_id,
               'sheet:' || (p.regions -> 0 ->> 'page') || ':row:' || (p.regions -> 0 ->> 'row')
                   AS record_key
        FROM pieces p
        WHERE p.customer_id = :customer_id AND p.regions -> 0 ->> 'kind' = 'cell'
          AND p.document_id IN (SELECT o.document_id FROM extracted_values o
              WHERE o.customer_id = :customer_id AND o.field = :metric AND o.record_key LIKE 'sheet:%')
          AND lower(p.text)  -- a cell ("A: Total Rs. | C: 3,500") that is or starts with a total word
              ~ '(^|[|] )[a-z]{1,3}: ((grand|net|sub)[[:space:]-]*)?totals?([^a-z]|$)'
                '|(^|[|] )[a-z]{1,3}: (एकूण|एकुण|कुल|योग)([[:space:]:.,;()/0-9-]|$)'
    ), sums AS (  -- each sheet's other rows, summed once
        SELECT t.customer_id, t.document_id, split_part(t.record_key, ':row:', 1) AS sheet,
               sum(t.value_number) AS total
        FROM extracted_values t
        WHERE t.customer_id = :customer_id AND t.field = :metric AND t.record_key LIKE 'sheet:%'
          AND t.document_id IN (SELECT l.document_id FROM labelled l)
          AND NOT EXISTS (SELECT 1 FROM labelled x WHERE x.customer_id = :customer_id
              AND x.document_id = t.document_id AND x.record_key = t.record_key)
        GROUP BY t.customer_id, t.document_id, split_part(t.record_key, ':row:', 1)
    ), totals AS (  -- printed: its one figure is the sum of its sheet's other rows; else doubtful
        SELECT l.customer_id, l.document_id, l.record_key,
               coalesce(count(t.id) = 1 AND min(t.value_number) = min(s.total), false) AS printed
        FROM labelled l
        LEFT JOIN extracted_values t ON t.customer_id = l.customer_id AND t.document_id = l.document_id
            AND t.record_key = l.record_key AND t.field = :metric
        LEFT JOIN sums s ON s.customer_id = l.customer_id AND s.document_id = l.document_id
            AND s.sheet = split_part(l.record_key, ':row:', 1)
        GROUP BY l.customer_id, l.document_id, l.record_key
    ), metric AS (
        SELECT v.document_id, v.record_key, (array_agg(v.id))[1] AS value_id,
               (array_agg(v.value_number))[1] AS amount,
               bool_and(v.label = 'verified') AND NOT EXISTS (SELECT 1 FROM invoices i
                   WHERE i.customer_id = :customer_id AND i.document_id = v.document_id
                   AND i.check_status = 'failed') AS verified,
               count(*) = 1 AND bool_and(v.value_number IS NOT NULL) AS readable,  -- C2: else doubtful
               EXISTS (SELECT 1 FROM totals x WHERE x.customer_id = :customer_id  -- reads like a total
                   AND x.document_id = v.document_id AND x.record_key = v.record_key) AS looks_total
        FROM extracted_values v
        JOIN documents d ON d.customer_id = v.customer_id AND d.id = v.document_id
        WHERE v.customer_id = :customer_id AND d.deleted_at IS NULL AND d.is_current AND d.status = 'ready'
          AND (CAST(:s_folders AS uuid[]) IS NULL OR d.folder_id = ANY(CAST(:s_folders AS uuid[])))
          AND (CAST(:s_tags AS uuid[]) IS NULL OR d.id IN (SELECT dt.document_id FROM document_tags dt
            WHERE dt.customer_id = :customer_id AND dt.tag_id = ANY(CAST(:s_tags AS uuid[]))
            GROUP BY dt.document_id HAVING count(DISTINCT dt.tag_id) = cardinality(CAST(:s_tags AS uuid[]))))
          AND (CAST(:s_doc_types AS text[]) IS NULL OR d.doc_type = ANY(CAST(:s_doc_types AS text[])))
          AND (CAST(:s_detected_type AS text) IS NULL OR d.detected_type = CAST(:s_detected_type AS text))
          AND (CAST(:s_documents AS uuid[]) IS NULL OR d.id = ANY(CAST(:s_documents AS uuid[])))
          AND (CAST(:s_from AS date) IS NULL AND CAST(:s_to AS date) IS NULL
            OR coalesce(d.document_date, (d.created_at AT TIME ZONE CAST(:s_tz AS text))::date)
            BETWEEN coalesce(CAST(:s_from AS date), '-infinity')
            AND coalesce(CAST(:s_to AS date), 'infinity'))
          AND v.field = :metric AND v.record_key NOT LIKE 'page:%'
          AND v.record_key <> 'card'
          AND v.record_key NOT LIKE 'line:%'
          AND NOT (v.record_key LIKE 'sheet:%' AND EXISTS (
              SELECT 1 FROM extracted_values o
              WHERE o.customer_id = :customer_id AND o.document_id = v.document_id
                AND o.field = v.field AND o.record_key = 'document'))
          AND NOT EXISTS (SELECT 1 FROM totals x WHERE x.customer_id = :customer_id
              AND x.document_id = v.document_id AND x.record_key = v.record_key AND x.printed)
        GROUP BY v.document_id, v.record_key
    ), dated AS (
        SELECT v.document_id, v.record_key, (array_agg(v.id))[1] AS value_id,
               bool_and(v.label = 'verified') AS verified, (array_agg(v.value_date))[1] AS day
        FROM extracted_values v
        JOIN documents d ON d.customer_id = v.customer_id AND d.id = v.document_id
        WHERE v.customer_id = :customer_id AND d.deleted_at IS NULL AND d.is_current AND d.status = 'ready'
          AND (CAST(:s_folders AS uuid[]) IS NULL OR d.folder_id = ANY(CAST(:s_folders AS uuid[])))
          AND (CAST(:s_tags AS uuid[]) IS NULL OR d.id IN (SELECT dt.document_id FROM document_tags dt
            WHERE dt.customer_id = :customer_id AND dt.tag_id = ANY(CAST(:s_tags AS uuid[]))
            GROUP BY dt.document_id HAVING count(DISTINCT dt.tag_id) = cardinality(CAST(:s_tags AS uuid[]))))
          AND (CAST(:s_doc_types AS text[]) IS NULL OR d.doc_type = ANY(CAST(:s_doc_types AS text[])))
          AND (CAST(:s_detected_type AS text) IS NULL OR d.detected_type = CAST(:s_detected_type AS text))
          AND (CAST(:s_documents AS uuid[]) IS NULL OR d.id = ANY(CAST(:s_documents AS uuid[])))
          AND (CAST(:s_from AS date) IS NULL AND CAST(:s_to AS date) IS NULL
            OR coalesce(d.document_date, (d.created_at AT TIME ZONE CAST(:s_tz AS text))::date)
            BETWEEN coalesce(CAST(:s_from AS date), '-infinity')
            AND coalesce(CAST(:s_to AS date), 'infinity'))
          AND v.field = ANY(CAST(:date_fields AS text[])) AND v.record_key NOT LIKE 'page:%'
          AND v.record_key <> 'card'
          AND v.record_key NOT LIKE 'line:%'
          AND NOT (v.record_key LIKE 'sheet:%' AND EXISTS (
              SELECT 1 FROM extracted_values o
              WHERE o.customer_id = :customer_id AND o.document_id = v.document_id
                AND o.field = :metric AND o.record_key = 'document'))
        GROUP BY v.document_id, v.record_key
        HAVING count(*) = 1 AND bool_and(v.value_date IS NOT NULL)
    ), compared AS (  -- C2: one with no date to read under a range is compared as doubtful, never dropped
        SELECT m.document_id, m.record_key, m.value_id, m.amount, m.verified, m.looks_total,
               (CASE WHEN CAST(:descending AS boolean) THEN -1 ELSE 1 END) * m.amount AS k,
               m.readable AND (cardinality(CAST(:date_fields AS text[])) = 0 OR dt.value_id IS NOT NULL)
                   AS readable,
               dt.value_id AS date_id, coalesce(dt.verified, true) AS date_verified
        FROM metric m
        LEFT JOIN dated dt ON dt.document_id = m.document_id AND dt.record_key = m.record_key
        WHERE CAST(:start AS date) IS NULL OR dt.value_id IS NULL
           OR (dt.day >= CAST(:start AS date) AND dt.day < CAST(:end AS date))
    ), unsure AS (  -- C2: every value compared not verified or not one number, and those like a total
        SELECT (array_agg(u.id ORDER BY u.k, u.id) FILTER (WHERE NOT u.total))[1:CAST(:cap AS int)] AS ids,
               (array_agg(u.id ORDER BY u.k, u.id) FILTER (WHERE u.total))[1:CAST(:cap AS int)] AS totals
        FROM (SELECT c.value_id::text AS id, c.k, c.looks_total AS total FROM compared c
              WHERE NOT (c.verified AND c.readable) OR c.looks_total
              UNION ALL SELECT c.date_id::text, c.k, false FROM compared c WHERE NOT c.date_verified) u
    )
    SELECT NULL::text AS name, c.amount AS number, 1 AS records,
           c.verified AND c.date_verified AS verified,
           ARRAY[c.value_id::text] AS metric_ids, ARRAY[]::text[] AS group_ids,
           array_remove(ARRAY[c.date_id::text], NULL) AS date_ids,
           (SELECT ids FROM unsure) AS unsure_ids, (SELECT totals FROM unsure) AS total_ids
    FROM compared c
    WHERE c.readable AND NOT c.looks_total  -- C2: one that reads like a total is never the one shown
    ORDER BY c.k, c.document_id, c.record_key
    LIMIT :limit
    """
)

_COUNT_DOCUMENTS = text(
    """
    WITH dated AS (
        SELECT v.document_id, v.record_key, (array_agg(v.id))[1] AS value_id,
               bool_and(v.label = 'verified') AND NOT EXISTS (SELECT 1 FROM invoices i
                   WHERE i.customer_id = :customer_id AND i.document_id = v.document_id
                   AND i.check_status = 'failed') AS verified
        FROM extracted_values v
        JOIN documents d ON d.customer_id = v.customer_id AND d.id = v.document_id
        WHERE v.customer_id = :customer_id AND d.deleted_at IS NULL AND d.is_current AND d.status = 'ready'
          AND (CAST(:s_folders AS uuid[]) IS NULL OR d.folder_id = ANY(CAST(:s_folders AS uuid[])))
          AND (CAST(:s_tags AS uuid[]) IS NULL OR d.id IN (SELECT dt.document_id FROM document_tags dt
            WHERE dt.customer_id = :customer_id AND dt.tag_id = ANY(CAST(:s_tags AS uuid[]))
            GROUP BY dt.document_id HAVING count(DISTINCT dt.tag_id) = cardinality(CAST(:s_tags AS uuid[]))))
          AND (CAST(:s_doc_types AS text[]) IS NULL OR d.doc_type = ANY(CAST(:s_doc_types AS text[])))
          AND (CAST(:s_detected_type AS text) IS NULL OR d.detected_type = CAST(:s_detected_type AS text))
          AND (CAST(:s_documents AS uuid[]) IS NULL OR d.id = ANY(CAST(:s_documents AS uuid[])))
          AND (CAST(:s_from AS date) IS NULL AND CAST(:s_to AS date) IS NULL
            OR coalesce(d.document_date, (d.created_at AT TIME ZONE CAST(:s_tz AS text))::date)
            BETWEEN coalesce(CAST(:s_from AS date), '-infinity')
            AND coalesce(CAST(:s_to AS date), 'infinity'))
          AND v.field = ANY(CAST(:date_fields AS text[])) AND v.record_key NOT LIKE 'page:%'
          AND v.record_key <> 'card'
          AND v.record_key NOT LIKE 'line:%'
          AND NOT (v.record_key LIKE 'sheet:%' AND EXISTS (
              SELECT 1 FROM extracted_values o
              WHERE o.customer_id = :customer_id AND o.document_id = v.document_id
                AND o.field = v.field AND o.record_key = 'document'))
        GROUP BY v.document_id, v.record_key
        HAVING count(*) = 1 AND bool_and(v.value_date IS NOT NULL AND (
            CAST(:start AS date) IS NULL
            OR (v.value_date >= CAST(:start AS date) AND v.value_date < CAST(:end AS date))))
    ), per_document AS (
        SELECT dt.document_id, (array_agg(dt.value_id ORDER BY dt.record_key))[1] AS value_id,
               bool_and(dt.verified) AS verified
        FROM dated dt
        GROUP BY dt.document_id
    )
    SELECT NULL::text AS name, count(*) AS number, count(*) AS records,
           bool_and(p.verified) AS verified, ARRAY[]::text[] AS metric_ids, ARRAY[]::text[] AS group_ids,
           array_agg(p.value_id::text ORDER BY p.document_id) AS date_ids
    FROM per_document p
    HAVING count(*) > 0
    """
)

_COUNT_INVOICES = text(
    """
    WITH in_scope AS (
        SELECT i.document_id, ev.value_id,
               i.label = 'verified' AND i.check_status = 'passed' AND coalesce(ev.verified, false) AS verified
        FROM invoices i
        JOIN documents d ON d.customer_id = i.customer_id AND d.id = i.document_id
        LEFT JOIN LATERAL (
            SELECT v.id AS value_id, v.label = 'verified' AS verified
            FROM extracted_values v
            WHERE v.customer_id = i.customer_id AND v.document_id = i.document_id
              AND v.record_key <> 'card' AND v.record_key NOT LIKE 'line:%'
            ORDER BY array_position(ARRAY['invoice_number', 'total_amount', 'invoice_date', 'seller_name',
                                          'buyer_name']::text[], v.field), v.page_no, v.id
            LIMIT 1
        ) ev ON true
        WHERE i.customer_id = :customer_id AND d.deleted_at IS NULL AND d.is_current AND d.status = 'ready'
          AND (CAST(:s_folders AS uuid[]) IS NULL OR d.folder_id = ANY(CAST(:s_folders AS uuid[])))
          AND (CAST(:s_tags AS uuid[]) IS NULL OR d.id IN (SELECT dt.document_id FROM document_tags dt
            WHERE dt.customer_id = :customer_id AND dt.tag_id = ANY(CAST(:s_tags AS uuid[]))
            GROUP BY dt.document_id HAVING count(DISTINCT dt.tag_id) = cardinality(CAST(:s_tags AS uuid[]))))
          AND (CAST(:s_doc_types AS text[]) IS NULL OR d.doc_type = ANY(CAST(:s_doc_types AS text[])))
          AND (CAST(:s_detected_type AS text) IS NULL OR d.detected_type = CAST(:s_detected_type AS text))
          AND (CAST(:s_documents AS uuid[]) IS NULL OR d.id = ANY(CAST(:s_documents AS uuid[])))
          AND (CAST(:s_from AS date) IS NULL AND CAST(:s_to AS date) IS NULL
            OR coalesce(d.document_date, (d.created_at AT TIME ZONE CAST(:s_tz AS text))::date)
            BETWEEN coalesce(CAST(:s_from AS date), '-infinity')
            AND coalesce(CAST(:s_to AS date), 'infinity'))
          AND (CAST(:start AS date) IS NULL OR (i.invoice_date >= CAST(:start AS date)
            AND i.invoice_date < CAST(:end AS date)))
    )
    SELECT NULL::text AS name, count(*) AS number, count(*) AS records, bool_and(verified) AS verified,
           ARRAY[]::text[] AS metric_ids, ARRAY[]::text[] AS group_ids,
           array_remove(array_agg(value_id::text ORDER BY document_id), NULL) AS date_ids
    FROM in_scope
    HAVING count(*) > 0
    """
)

_VALUE_REFS = text(
    """
    SELECT v.id::text AS value_id, v.document_id::text AS document_id, d.display_name,
           d.detected_type, v.page_no, v.region, v.field, v.value_text,
           CASE WHEN EXISTS (SELECT 1 FROM invoices i WHERE i.customer_id = :customer_id
               AND i.document_id = v.document_id AND i.check_status = 'failed')
               THEN 'needs_review' ELSE v.label END AS label,
           v.label_page_no, v.label_region
    FROM extracted_values v
    JOIN documents d ON d.customer_id = v.customer_id AND d.id = v.document_id
    WHERE v.customer_id = :customer_id AND d.deleted_at IS NULL AND d.is_current AND d.status = 'ready'
      AND (CAST(:s_folders AS uuid[]) IS NULL OR d.folder_id = ANY(CAST(:s_folders AS uuid[])))
      AND (CAST(:s_tags AS uuid[]) IS NULL OR d.id IN (SELECT dt.document_id FROM document_tags dt
        WHERE dt.customer_id = :customer_id AND dt.tag_id = ANY(CAST(:s_tags AS uuid[]))
        GROUP BY dt.document_id HAVING count(DISTINCT dt.tag_id) = cardinality(CAST(:s_tags AS uuid[]))))
      AND (CAST(:s_doc_types AS text[]) IS NULL OR d.doc_type = ANY(CAST(:s_doc_types AS text[])))
      AND (CAST(:s_detected_type AS text) IS NULL OR d.detected_type = CAST(:s_detected_type AS text))
      AND (CAST(:s_documents AS uuid[]) IS NULL OR d.id = ANY(CAST(:s_documents AS uuid[])))
      AND (CAST(:s_from AS date) IS NULL AND CAST(:s_to AS date) IS NULL
        OR coalesce(d.document_date, (d.created_at AT TIME ZONE CAST(:s_tz AS text))::date)
        BETWEEN coalesce(CAST(:s_from AS date), '-infinity')
        AND coalesce(CAST(:s_to AS date), 'infinity'))
      AND v.id = ANY(CAST(:ids AS uuid[]))
    """
)


class PgFixedQueries:
    """``FixedQueries`` over PostgreSQL, inside ``scope``. Every call runs inside ``db.customer``."""

    def __init__(self, db: Database, scope: Scope = EVERYTHING) -> None:
        self._db = db
        self._scope = scope

    def scoped(self, scope: Scope) -> "PgFixedQueries":
        return PgFixedQueries(self._db, scope)

    def available_fields(self, customer_id: str, fields: Sequence[str]) -> set[str]:
        """Which of ``fields`` the customer has: O(f) index probes for f fields."""
        with self._db.customer(customer_id) as conn:
            params = {"customer_id": customer_id, "candidates": list(fields), **self._scope.params()}
            rows = conn.execute(_AVAILABLE_FIELDS, params)
            return {str(row.field) for row in rows}

    def top_group(
        self,
        customer_id: str,
        metric: str,
        group_fields: Sequence[str],
        date_fields: Sequence[str],
        start: date | None,
        end: date | None,
        descending: bool,
    ) -> list[FixedRow]:
        params = _params(customer_id, date_fields, start, end)
        params.update(metric=metric, group_fields=list(group_fields), descending=descending, limit=_TOP_ROWS)
        return self._run(customer_id, _TOP_GROUP, params)

    def figures(
        self,
        customer_id: str,
        metric: str,
        date_fields: Sequence[str],
        start: date | None,
        end: date | None,
        *,
        invoices: bool,
        group_fields: Sequence[str] = (),
    ) -> list[Figure]:
        """Every matching record, by date then document, and the printed total rows of their
        sheets; ``invoices``: the question names them; ``group_fields``: a ranking's parties."""
        params = _params(customer_id, date_fields, start, end)
        params.update(metric=metric, invoices=invoices, group_fields=list(group_fields))
        with self._db.customer(customer_id) as conn:
            rows = conn.execute(_FIGURES, params | self._scope.params()).all()
            ids = [str(row.value_id) for row in rows if row.value_id is not None]
            refs = _value_refs(conn, customer_id, ids, self._scope)
        return [
            Figure(
                value=None if row.value_id is None else refs.get(str(row.value_id)),
                amount=None if row.amount is None else Decimal(row.amount),
                printed=int(row.printed),
                verified=bool(row.verified),
                dated=bool(row.dated),
                date_verified=bool(row.date_verified),
                grouped=bool(row.grouped),
                party_verified=bool(row.party_verified),
                printed_total=bool(row.printed_total),
                looks_total=bool(row.looks_total),
            )
            for row in rows
        ]

    def count_documents(
        self, customer_id: str, start: date | None, end: date | None, *, date_fields: Sequence[str]
    ) -> list[FixedRow]:
        return self._run(customer_id, _COUNT_DOCUMENTS, _params(customer_id, date_fields, start, end))

    def count_invoices(self, customer_id: str, start: date | None, end: date | None) -> list[FixedRow]:
        if (start is None) != (end is None):
            raise ValueError("start and end go together")
        return self._run(
            customer_id,
            _COUNT_INVOICES,
            {"customer_id": customer_id, "start": start, "end": end},
        )

    def extreme_value(
        self,
        customer_id: str,
        metric: str,
        date_fields: Sequence[str],
        start: date | None,
        end: date | None,
        descending: bool,
    ) -> list[FixedRow]:
        params = _params(customer_id, date_fields, start, end)
        params.update(metric=metric, descending=descending, limit=_TOP_ROWS, cap=MAX_CITED_VALUES)
        return self._run(customer_id, _EXTREME_VALUE, params)

    def _run(self, customer_id: str, query: TextClause, params: dict[str, Any]) -> list[FixedRow]:
        with self._db.customer(customer_id) as conn:
            aggregates = conn.execute(query, params | self._scope.params()).all()
            cited = [_cited_ids(row.metric_ids, row.group_ids, row.date_ids) for row in aggregates]
            # Highest and lowest only: the compared values not verified (the counts have none).
            unsure = [row._mapping.get("unsure_ids") or [] for row in aggregates]
            looks = [row._mapping.get("total_ids") or [] for row in aggregates]
            wanted = {value_id for ids in [*cited, *unsure, *looks] for value_id in ids}
            refs = _value_refs(conn, customer_id, list(wanted), self._scope)
        return [
            FixedRow(
                group=None if row.name is None else str(row.name),
                number=Decimal(row.number),
                records=int(row.records),
                verified=bool(row.verified),
                values=[refs[value_id] for value_id in ids if value_id in refs],
                unsure=tuple(refs[value_id] for value_id in doubts if value_id in refs),
                totals=tuple(refs[value_id] for value_id in like if value_id in refs),
            )
            for row, ids, doubts, like in zip(aggregates, cited, unsure, looks, strict=True)
        ]


def _params(
    customer_id: str, date_fields: Sequence[str], start: date | None, end: date | None
) -> dict[str, Any]:
    if (start is None) != (end is None):
        raise ValueError("start and end go together")
    if start is not None and not date_fields:
        raise ValueError("a date range needs date fields")
    return {"customer_id": customer_id, "date_fields": list(date_fields), "start": start, "end": end}


def _cited_ids(metric_ids: list[str], group_ids: list[str], date_ids: list[str]) -> list[str]:
    """Record by record (metric, party, date), up to MAX_CITED_VALUES. O(r) in the records."""
    ids: list[str] = []
    for index, metric_id in enumerate(metric_ids):
        ids.append(metric_id)
        if index < len(group_ids):
            ids.append(group_ids[index])
        if index < len(date_ids):
            ids.append(date_ids[index])
    if not metric_ids:
        ids.extend(date_ids)  # count_documents: one date value per document
    return ids[:MAX_CITED_VALUES]


def _value_refs(conn: Connection, customer_id: str, ids: list[str], scope: Scope) -> dict[str, ValueRef]:
    if not ids:
        return {}
    rows = conn.execute(_VALUE_REFS, {"customer_id": customer_id, "ids": ids, **scope.params()})
    return {
        str(row.value_id): ValueRef(
            value_id=str(row.value_id),
            document_id=str(row.document_id),
            document_name=str(row.display_name),
            detected_type=str(row.detected_type),
            page_no=int(row.page_no),
            region=dict(row.region),
            field=str(row.field),
            value_text=str(row.value_text),
            label=str(row.label),
            label_page_no=row.label_page_no,
            label_region=row.label_region,
        )
        for row in rows
    }
