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
* The date filter is ``[start, end)`` on the record's own date value. No
  date candidates means no date filter.
* Party names are grouped ignoring case and repeated spaces; the name shown
  is the one written in the group's largest record.

Every row carries the values it used (``values``, at most
``MAX_CITED_VALUES``, each with its page and region) for the citations, and
``verified`` over *every* value used, cited or not.

Each call is one transaction with two statements: the aggregate, then one
lookup of the cited values' pages and regions (no N+1). Time is O(v) in the
customer's values of the fields asked, through the (customer_id, field)
index; space is O(r) in the result rows.
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


@dataclass(frozen=True, slots=True)
class FixedRow:
    group: str | None  # the party name (top_group only)
    number: Decimal  # the sum, the single value, or the count
    records: int  # how many records went into it
    verified: bool  # every value used is verified
    values: list[ValueRef]  # the values cited, at most MAX_CITED_VALUES


class FixedQueries(Protocol):
    def scoped(self, scope: Scope) -> "FixedQueries":
        """The same queries over the documents inside ``scope`` only (PRD 3 H2)."""
        ...

    def available_fields(self, customer_id: str) -> set[str]: ...

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

    def total(
        self, customer_id: str, metric: str, date_fields: Sequence[str], start: date | None, end: date | None
    ) -> list[FixedRow]: ...

    def count_documents(
        self, customer_id: str, start: date | None, end: date | None, *, date_fields: Sequence[str]
    ) -> list[FixedRow]: ...

    def extreme_value(
        self,
        customer_id: str,
        metric: str,
        date_fields: Sequence[str],
        start: date | None,
        end: date | None,
        descending: bool,
    ) -> list[FixedRow]: ...


_AVAILABLE_FIELDS = text(
    """
    SELECT DISTINCT v.field
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
      AND v.record_key <> 'card'
      AND v.record_key NOT LIKE 'line:%'
    """
)

_TOP_GROUP = text(
    """
    WITH metric AS (
        SELECT v.document_id, v.record_key, (array_agg(v.id))[1] AS value_id,
               (array_agg(v.value_number))[1] AS amount, bool_and(v.label = 'verified') AS verified
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
                AND o.field = v.field AND o.record_key = 'document'))
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
                AND o.field = v.field AND o.record_key = 'document'))
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

_TOTAL = text(
    """
    WITH metric AS (
        SELECT v.document_id, v.record_key, (array_agg(v.id))[1] AS value_id,
               (array_agg(v.value_number))[1] AS amount, bool_and(v.label = 'verified') AS verified
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
        GROUP BY v.document_id, v.record_key
        HAVING count(*) = 1 AND bool_and(v.value_number IS NOT NULL)
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
                AND o.field = v.field AND o.record_key = 'document'))
        GROUP BY v.document_id, v.record_key
        HAVING count(*) = 1 AND bool_and(v.value_date IS NOT NULL AND (
            CAST(:start AS date) IS NULL
            OR (v.value_date >= CAST(:start AS date) AND v.value_date < CAST(:end AS date))))
    ), records AS (
        SELECT m.document_id, m.record_key, m.value_id AS metric_id, m.amount, dt.value_id AS date_id,
               m.verified AND coalesce(dt.verified, true) AS verified
        FROM metric m
        LEFT JOIN dated dt ON dt.document_id = m.document_id AND dt.record_key = m.record_key
        WHERE cardinality(CAST(:date_fields AS text[])) = 0 OR dt.value_id IS NOT NULL
    )
    SELECT NULL::text AS name, sum(r.amount) AS number, count(*) AS records,
           bool_and(r.verified) AS verified,
           array_agg(r.metric_id::text ORDER BY r.document_id, r.record_key) AS metric_ids,
           ARRAY[]::text[] AS group_ids,
           array_remove(array_agg(r.date_id::text ORDER BY r.document_id, r.record_key), NULL) AS date_ids
    FROM records r
    HAVING count(*) > 0
    """
)

_EXTREME_VALUE = text(
    """
    WITH metric AS (
        SELECT v.document_id, v.record_key, (array_agg(v.id))[1] AS value_id,
               (array_agg(v.value_number))[1] AS amount, bool_and(v.label = 'verified') AS verified
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
        GROUP BY v.document_id, v.record_key
        HAVING count(*) = 1 AND bool_and(v.value_number IS NOT NULL)
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
                AND o.field = v.field AND o.record_key = 'document'))
        GROUP BY v.document_id, v.record_key
        HAVING count(*) = 1 AND bool_and(v.value_date IS NOT NULL AND (
            CAST(:start AS date) IS NULL
            OR (v.value_date >= CAST(:start AS date) AND v.value_date < CAST(:end AS date))))
    )
    SELECT NULL::text AS name, m.amount AS number, 1 AS records,
           m.verified AND coalesce(dt.verified, true) AS verified,
           ARRAY[m.value_id::text] AS metric_ids, ARRAY[]::text[] AS group_ids,
           array_remove(ARRAY[dt.value_id::text], NULL) AS date_ids
    FROM metric m
    LEFT JOIN dated dt ON dt.document_id = m.document_id AND dt.record_key = m.record_key
    WHERE cardinality(CAST(:date_fields AS text[])) = 0 OR dt.value_id IS NOT NULL
    ORDER BY (CASE WHEN CAST(:descending AS boolean) THEN -1 ELSE 1 END) * m.amount,
             m.document_id, m.record_key
    LIMIT :limit
    """
)

_COUNT_DOCUMENTS = text(
    """
    WITH dated AS (
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

_VALUE_REFS = text(
    """
    SELECT v.id::text AS value_id, v.document_id::text AS document_id, d.display_name,
           d.detected_type, v.page_no, v.region, v.field, v.value_text, v.label,
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

    def available_fields(self, customer_id: str) -> set[str]:
        with self._db.customer(customer_id) as conn:
            rows = conn.execute(_AVAILABLE_FIELDS, {"customer_id": customer_id, **self._scope.params()})
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

    def total(
        self, customer_id: str, metric: str, date_fields: Sequence[str], start: date | None, end: date | None
    ) -> list[FixedRow]:
        params = _params(customer_id, date_fields, start, end)
        params.update(metric=metric)
        return self._run(customer_id, _TOTAL, params)

    def count_documents(
        self, customer_id: str, start: date | None, end: date | None, *, date_fields: Sequence[str]
    ) -> list[FixedRow]:
        return self._run(customer_id, _COUNT_DOCUMENTS, _params(customer_id, date_fields, start, end))

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
        params.update(metric=metric, descending=descending, limit=_TOP_ROWS)
        return self._run(customer_id, _EXTREME_VALUE, params)

    def _run(self, customer_id: str, query: TextClause, params: dict[str, Any]) -> list[FixedRow]:
        with self._db.customer(customer_id) as conn:
            aggregates = conn.execute(query, params | self._scope.params()).all()
            cited = [_cited_ids(row.metric_ids, row.group_ids, row.date_ids) for row in aggregates]
            refs = _value_refs(
                conn, customer_id, [value_id for ids in cited for value_id in ids], self._scope
            )
        return [
            FixedRow(
                group=None if row.name is None else str(row.name),
                number=Decimal(row.number),
                records=int(row.records),
                verified=bool(row.verified),
                values=[refs[value_id] for value_id in ids if value_id in refs],
            )
            for row, ids in zip(aggregates, cited, strict=True)
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
