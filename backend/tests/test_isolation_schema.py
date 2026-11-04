"""Every customer table, found from the catalog, has both locks and the app's grants.

A table is a customer table when it has a ``customer_id`` column. Finding them
from the catalog means a new table can't be left out of this check: it needs
row level security, the ``customer_only`` policy with USING and WITH CHECK, and
grants to the app role, which must not own it (an owner skips the policy).

Super admin PR 2 (0015, NEEDS VRUSHIT'S APPROVAL): the operator's tables, role and functions.

PRD 5 (0021): the page cache, sections and topics, with the least grants each needs.

Model choice and AI spend (0023, NEEDS VRUSHIT'S APPROVAL): the organisations' models the
app may only read, the ledger it may only read and add to, the platform catalogue whose
refresh columns alone it may write, and the operator's five functions.

Auto-filing (0025): the app may switch its own ``autofile_on``, never ``autofile_granted``,
which only the operator's ``kaagaz_op_set_org_autofile`` writes.

Two-step by grant (0029) is gone (0035): no ``two_step_granted``, no ``kaagaz_op_set_org_two_step``.

Features 25-27 (0026): an organisation's AI settings the app may only read, the platform
defaults (no customer) every role that may reads, the catalogue's chat_eligible the refresh
writes, and the operator's AI functions in place of 0023's organisation models ones.

Word review (0030, NEEDS VRUSHIT'S APPROVAL): word changes the app adds and only marks undone
or writes again (applied), Ensemble runs whose progress columns alone change, readings whose
decision alone is set; none is ever deleted by the app (they go with their document).

Ranked sources (0036): a source a person put on top of an answer, added only.

AI settings by task (0038, for Vrushit's later review): the organisation's and members' task
levels the app reads and writes; the feature switches and the platform test model (no
customer) every role that may reads, two columns each, written only by the operator's
functions (step-up).

Per-organisation feature switches (0039): the app reads three columns of its own rows, never
who flipped one; only the operator's functions write (step-up).

The sign-in idle timeout (0040): the same for one organisation's hours (two columns).

File management (0041): version suggestions the app reads, adds and decides, never deletes
(they go with their document).

Shared OpenRouter scheduling and limits (0046): request tickets, fair-turn state and RPM
overrides are customer scoped, RLS protected, and only reachable through the narrow app or
operator functions.

Land facts (0049, #245): the company vocabulary ``value_keys``, read and added, only its
spellings written again, never deleted by the app (a key goes with its value).

Page layout (0056, #383, #382): a page's blocks and its tables' cells, deleted and inserted by
the replacing save, never updated.

Folder upload (0061c, #484, NEEDS VRUSHIT'S APPROVAL): an upload, its folders and its items the
app reads, adds and updates, never deletes (they go with the customer); an organisation's own
limits it reads in four columns only, written by the operator's two functions (step-up).
"""

import re

import pytest
from sqlalchemy import text

from kaagaz.db.engine import Database
from tests.db_support import APP_ROLE

pytestmark = pytest.mark.db

POLICY = "(customer_id = kaagaz_current_customer())"
FROM_0001 = {
    "users",
    "documents",
    "pages",
    "reader_outputs",
    "pieces",
    "extracted_values",
    "corrections",
    "learned_corrections",
    "conversations",
    "messages",
    "feedback",
    "audit_log",
}
FROM_0002 = {"folders", "tags", "document_tags"}
# (SELECT, INSERT, UPDATE, DELETE) for the app role, the least each needs: a save
# deletes and inserts invoices and links, never updates them; lines are only
# inserted (the invoice's delete cascades to them); runs are never deleted, and
# only their outcome columns may be updated (RUN_OUTCOME below).
FROM_0004 = {
    "invoices": (True, True, False, True),
    "invoice_lines": (True, True, False, False),
    "document_links": (True, True, False, True),
    "processing_runs": (True, True, False, False),
}
# My words (0008): a word's text and meaning may change; a spot is only added or deleted.
FROM_0008 = {"user_words": (True, True, True, True), "user_word_spots": (True, True, False, True)}
# Story of this PDF (0009): a story is created, replaced (its vector too) and deleted.
FROM_0009 = {"document_stories": (True, True, True, True)}
# Gemini reader (0021): a cached page read is inserted or has its result replaced (an upsert,
# UPDATE on PAGE_READ_UPDATES only); sections and topics are deleted and inserted by the
# replacing save, and only a section's vector is written after it (SECTION_UPDATES).
FROM_0021 = {
    "page_reads": (True, True, False, True),
    "document_sections": (True, True, False, True),
    "document_topics": (True, True, False, True),
}
# Model choice (0023): the operator alone writes an organisation's models (its functions),
# and the ledger is append-only for the app.
FROM_0023 = {
    "customer_models": (True, False, False, False),
    "model_usage": (True, True, False, False),
}
# Features 25-27 (0026): the operator alone writes an organisation's AI settings.
FROM_0026 = {"customer_ai_settings": (True, False, False, False)}
# Word review (0030): added, never deleted; only the columns below may be updated.
FROM_0030 = {
    "word_changes": (True, True, False, False),
    "ensemble_runs": (True, True, False, False),
    "ensemble_readings": (True, True, False, False),
}
# Ranked sources (0036): a pick is added, never changed or deleted by the app.
FROM_0036 = {"source_pins": (True, True, False, False)}
# AI settings by task (0038): a person's or the organisation's level is saved, changed and removed.
FROM_0038 = {"ai_task_settings": (True, True, True, True)}
# Per-organisation feature switches (0039): a column grant only (no table right), the operator writes.
FROM_0039 = {"org_feature_switches": (False, False, False, False)}
# The sign-in idle timeout (0040): the same, two columns.
FROM_0040 = {"org_session_settings": (False, False, False, False)}
# Changed on purpose (PRD 1, 0041): version suggestions are read, added and decided, never deleted.
FROM_0041 = {"version_suggestions": (True, True, True, False)}
# Shared OpenRouter RPM (0046): these rows are only accessed by the scheduler and operator
# functions, never with direct table rights from the app.
FROM_0043 = {
    "model_rate_waiters": (False, False, False, False),
    "model_rate_customer_turns": (False, False, False, False),
    "model_rate_user_turns": (False, False, False, False),
    "model_rate_overrides": (False, False, False, False),
    "user_model_rate_overrides": (False, False, False, False),
    "model_rate_state": (False, False, False, False),
}
# Land facts (0049): a key is read and added; only its spellings are written again (UPDATES_0049).
FROM_0049 = {"value_keys": (True, True, False, False)}
UPDATES_0049 = {"value_keys": {"latin", "sound"}}
# PRD 6 F3 (0051): an admin's Remove on What Kaagaz learned is added, never changed or deleted.
FROM_0051 = {"learning_removals": (True, True, False, False)}
# Page layout (0056, #383, #382): blocks and table cells are deleted and inserted by the save.
FROM_0056 = {"page_blocks": (True, True, False, True), "table_cells": (True, True, False, True)}
# Folder upload (0061c): never deleted by the app; the limits are a column grant, the operator writes.
FROM_0061C = {
    "folder_uploads": (True, True, True, False),
    "folder_upload_folders": (True, True, True, False),
    "folder_upload_items": (True, True, True, False),
    "org_upload_limits": (False, False, False, False),
}
UPDATES_0030 = {
    "word_changes": {"applied", "undone_at", "undone_by"},
    "ensemble_runs": {
        "pages_done", "pages_failed", "status", "stop_requested_at", "error_code", "attempts", "started_at",
        "finished_at",
    },
    "ensemble_readings": {"decision", "decided_by", "decided_at"},
}  # fmt: skip
# The catalogue (no customer): the app's refresh writes these columns only, never
# verified_*, tokens_per_page_* (verify-models, the owner) or enabled_* (the operator).
CATALOG_REFRESH = {
    "name", "price_in_usd_per_m", "price_out_usd_per_m", "providers", "efforts", "expires_on", "eligible",
    "seen_at", "chat_eligible",  # 0026, the answer filter
}  # fmt: skip
CATALOG_POLICIES = {
    ("model_catalog", "read_all"): ("SELECT", "PERMISSIVE", ["public"], "true", None),
    ("model_catalog", "refresh_insert"): ("INSERT", "PERMISSIVE", ["public"], None, "true"),
    ("model_catalog", "refresh_update"): ("UPDATE", "PERMISSIVE", ["public"], "true", "true"),
    # 0026: the platform defaults, read by every role that may (the app's grant is SELECT only).
    ("platform_models", "read_all"): ("SELECT", "PERMISSIVE", ["public"], "true", None),
    ("platform_ai_settings", "read_all"): ("SELECT", "PERMISSIVE", ["public"], "true", None),
    # 0038: the feature switches and the platform test model (the app's grant: two columns each).
    ("feature_switches", "read_all"): ("SELECT", "PERMISSIVE", ["public"], "true", None),
    ("platform_model_override", "read_all"): ("SELECT", "PERMISSIVE", ["public"], "true", None),
}
PAGE_READ_UPDATES = {"result", "input_tokens", "output_tokens", "created_at"}
SECTION_UPDATES = {"embedding", "embed_recipe"}
# The second policy of the tables that belong to one user (0008, 0009): it must pass too.
OWN_USER = "(user_id = (NULLIF(current_setting('kaagaz.user_id'::text, true), ''::text))::uuid)"
RUN_OUTCOME = {
    "status", "error_code", "ocr_engine", "model_chat", "model_vision", "model_embed", "config",
    "pages_cells", "pages_text_layer", "pages_ocr", "pages_blank", "vision_crops", "model_calls",
    "dropped_quotes", "quotes_returned", "values_verified", "values_needs_review", "seconds_total",
    "seconds_ocr", "finished_at",
    # 0021: who read the pages and the tokens billed (PRD 5).
    "pages_gemini", "pages_local", "pages_cached", "pages_read_again", "pages_unreadable", "input_tokens",
    "output_tokens",
}  # fmt: skip
# The owner-rights (SECURITY DEFINER) functions, the only ways past the policies,
# each narrow: {name: (what it returns, its search path)}. Those that list across
# customers or users give ids only.
EMPTY_PATH = ['search_path=""']
OWNER_RIGHTS = {
    "kaagaz_login_lookup": (
        "TABLE(user_id uuid, customer_id uuid, password_hash text, role text, disabled boolean)",
        ["search_path=public, pg_temp"],
    ),  # 0001, sign-in
    "kaagaz_create_customer": ("uuid", ["search_path=public, pg_temp"]),  # 0001
    "kaagaz_customer_ids_with_trash": ("SETOF uuid", EMPTY_PATH),  # 0002, the purge
    "kaagaz_customer_ids": ("SETOF uuid", EMPTY_PATH),  # 0006, reembed
    "kaagaz_stories_to_embed": ("TABLE(customer_id uuid, user_id uuid, family_id uuid)", EMPTY_PATH),  # 0010
    # 0018, the worker's purge of deleted organisations: ids only, and the one due, empty
    # customers row (never a kaagaz_op_* function: the app runs none of those).
    "kaagaz_customer_ids_to_purge": ("SETOF uuid", EMPTY_PATH),
    "kaagaz_finish_customer_delete": ("boolean", EMPTY_PATH),
    # 0019, the second sweep a day after a purge: ids only, and its org.swept row.
    "kaagaz_purged_ids_to_sweep": ("SETOF uuid", EMPTY_PATH),
    "kaagaz_customer_swept": ("boolean", EMPTY_PATH),
    # 0045, the expired upload sweep: ids and storage keys of expired sessions only.
    "kaagaz_expired_upload_sessions": (
        "TABLE(customer_id uuid, id uuid, storage_key text, storage_upload_id text)",
        EMPTY_PATH,
    ),
    # 0046, the shared scheduler's single admission function.
    "kaagaz_model_rate_slot": ("double precision", EMPTY_PATH),
}
# The platform operator's (0015): only kaagaz_operator may run these, never the app.
OPERATOR = "kaagaz_operator"
OPERATOR_RIGHTS = {
    "kaagaz_op_begin_login": (
        "TABLE(operator_id uuid, email text, display_name text, password_hash text, disabled boolean,"
        " totp_secret_enc text, totp_last_step bigint, failed_logins integer)",
        EMPTY_PATH,
    ),
    "kaagaz_op_login_failed": ("void", EMPTY_PATH),
    "kaagaz_op_login_succeeded": ("integer", EMPTY_PATH),
    "kaagaz_op_session": ("TABLE(email text, display_name text, session_version integer)", EMPTY_PATH),
    "kaagaz_op_logout": ("void", EMPTY_PATH),
    # 0016: organisations with counts only, and the writes, each answering (outcome, result_id).
    # 0025 added each one's auto-filing grant and switch (0029's two-step grant went in 0035).
    "kaagaz_op_customers": (
        "TABLE(customer_id uuid, name text, status text, disabled_at timestamp with time zone,"
        " delete_after timestamp with time zone, created_at timestamp with time zone, admins bigint,"
        " reviewers bigint, members bigint, documents bigint, pages bigint, size_bytes bigint,"
        " last_activity_at timestamp with time zone, autofile_granted boolean, autofile_on boolean)",
        EMPTY_PATH,
    ),
    # 0017: one organisation's admins, and the changes to one of them.
    "kaagaz_op_admins": (
        "TABLE(user_id uuid, display_name text, email text, totp_enabled boolean,"
        " locked_until timestamp with time zone, disabled boolean, must_change_password boolean,"
        " last_login_at timestamp with time zone, created_at timestamp with time zone)",
        EMPTY_PATH,
    ),
    **dict.fromkeys(
        (
            "kaagaz_op_create_customer",
            "kaagaz_op_set_customer_status",
            "kaagaz_op_create_admin",
            "kaagaz_op_change_admin",
            "kaagaz_op_schedule_delete",  # 0018
            "kaagaz_op_cancel_delete",
        ),
        ("record", EMPTY_PATH),
    ),
    "kaagaz_op_begin_step_up": (
        "TABLE(totp_secret_enc text, totp_last_step bigint, step_up_failures integer)",
        EMPTY_PATH,
    ),
    "kaagaz_op_step_up_failed": ("void", EMPTY_PATH),
    # 0031, moving the operator's own two-step to a new device (never off).
    "kaagaz_op_move_check": ("boolean", EMPTY_PATH),
    "kaagaz_op_move_totp": ("integer", EMPTY_PATH),
    # 0020, read-only: the feed (no details, target id, person or document) and counts.
    "kaagaz_op_activity": (
        "TABLE(source text, entry_id bigint, created_at timestamp with time zone, action text,"
        " customer_id uuid, customer_name text, target_type text, actor text)",
        EMPTY_PATH,
    ),
    "kaagaz_op_health": (
        "TABLE(customer_id uuid, name text, status text, delete_after timestamp with time zone,"
        " documents_by_status jsonb, failed_by_code jsonb, in_progress bigint,"
        " oldest_in_progress_at timestamp with time zone)",
        EMPTY_PATH,
    ),
    "kaagaz_op_purges": (
        "TABLE(purged bigint, last_purged_at timestamp with time zone, swept bigint,"
        " last_swept_at timestamp with time zone)",
        EMPTY_PATH,
    ),
    # 0022, read-only: each worker's reader state, status only (PRD 5 U9).
    "kaagaz_op_reader": (
        "TABLE(worker text, state text, since timestamp with time zone, seen_at timestamp with time zone)",
        EMPTY_PATH,
    ),
    # 0023, model choice and AI spend: the catalogue, the platform switch (step-up), one
    # organisation's models and budget, and spend per organisation and model. Pinned:
    # never a user or a document column.
    "kaagaz_op_models": (
        "TABLE(id text, name text, price_in_usd_per_m numeric, price_out_usd_per_m numeric, providers text[],"
        " efforts text[], expires_on date, eligible boolean, seen_at timestamp with time zone,"
        " verified_prompt_version text, verified_at timestamp with time zone, tokens_per_page_in integer,"
        " tokens_per_page_out integer, enabled_at timestamp with time zone, ledger_tokens_in numeric,"
        " ledger_tokens_out numeric, chat_eligible boolean, chat_verified_prompt text,"
        " chat_verified_at timestamp with time zone, scan_pages bigint, scan_pages_costed bigint,"
        " scan_cost_usd numeric, scan_cost_unknown_calls bigint, answer_questions bigint,"
        " answer_questions_costed bigint, answer_cost_usd numeric, answer_cost_unknown_calls bigint)",
        # 0026: the answer columns; 0033: the real cost, counts and sums only
        EMPTY_PATH,
    ),
    "kaagaz_op_set_model_enabled": ("text", EMPTY_PATH),
    # 0026, features 25-27, in place of 0023's kaagaz_op_org_models and kaagaz_op_set_org_models:
    # one organisation's AI settings and budget, the platform defaults with counts of the
    # organisations each area reaches, and the two saves (step-up). Pinned: no user, no document.
    "kaagaz_op_org_ai": (
        "TABLE(scan_models text[], scan_default text, chat_models text[], chat_default text,"
        " scan_thinking text, chat_levels text[], chat_default_level text, dynamic boolean, ensemble boolean,"
        " ai_budget_usd numeric)",
        EMPTY_PATH,
    ),
    "kaagaz_op_set_org_ai": ("record", EMPTY_PATH),
    "kaagaz_op_ai_defaults": (
        "TABLE(scan_models text[], scan_default text, chat_models text[], chat_default text,"
        " scan_thinking text, chat_levels text[], chat_default_level text, dynamic boolean,"
        " orgs_inheriting_scan_models bigint, orgs_inheriting_chat_models bigint,"
        " orgs_inheriting_scan_thinking bigint, orgs_inheriting_chat_levels bigint,"
        " orgs_inheriting_dynamic bigint)",
        EMPTY_PATH,
    ),
    "kaagaz_op_set_ai_defaults": ("text", EMPTY_PATH),
    "kaagaz_op_set_org_autofile": ("record", EMPTY_PATH),  # 0025: allow or stop auto-filing
    "kaagaz_op_usage": (
        "TABLE(customer_id uuid, customer_name text, model text, calls bigint, cached_calls bigint,"
        " input_tokens numeric, output_tokens numeric, reasoning_tokens numeric, cost_usd numeric,"
        " cost_estimated boolean, cost_part_unknown boolean)",
        EMPTY_PATH,
    ),
    # 0024, operator insights, read-only (F1-F4): counts, sums, rates and durations per
    # organisation or model. Pinned: never a user, document or name column.
    "kaagaz_op_daily": (
        "TABLE(customer_id uuid, day date, uploads bigint, sign_ins bigint, questions bigint,"
        " corrections bigint, runs bigint, runs_failed bigint, pages bigint, scan_pages bigint,"
        " cached_pages bigint, cost_usd numeric, cost_scan_usd numeric, cost_documents_usd numeric,"
        " cost_questions_usd numeric, cost_estimated boolean, cost_part_unknown boolean, pages_read bigint,"
        " pages_reread bigint, pages_read_costed bigint, cost_real_usd numeric, cost_scan_real_usd numeric,"
        " cost_answer_real_usd numeric, cost_unknown_calls bigint, scan_unknown_calls bigint,"
        " answer_unknown_calls bigint)",  # 0034: the real figures, counts and sums only
        EMPTY_PATH,
    ),
    "kaagaz_op_org_health": (
        "TABLE(customer_id uuid, active_users_7d bigint, active_users_30d bigint,"
        " active_users_prev_30d bigint, answers_30d bigint, not_found_30d bigint, open_review bigint,"
        " oldest_open_review_at timestamp with time zone, ai_budget_usd numeric, month_cost_usd numeric,"
        " month_cost_estimated boolean, month_cost_part_unknown boolean, last_active_day date)",
        EMPTY_PATH,
    ),
    "kaagaz_op_quality": (
        "TABLE(customer_id uuid, runs bigint, runs_failed bigint, runs_failed_platform bigint,"
        " runs_degraded bigint, documents_run bigint, pages_cells bigint, pages_text_layer bigint,"
        " pages_ocr bigint, pages_blank bigint, pages_ai bigint, pages_local bigint, pages_cached bigint,"
        " pages_unreadable bigint, ready_documents bigint, ready_p50_seconds double precision,"
        " ready_p95_seconds double precision, answers bigint, answered bigint, not_found bigint,"
        " unchecked bigint, answers_verified bigint, answers_fixed_query bigint, citations bigint,"
        " feedback bigint, feedback_up bigint, values_total bigint, values_needs_review bigint,"
        " corrections bigint, corrections_second bigint, corrections_typed bigint)",
        EMPTY_PATH,
    ),
    "kaagaz_op_model_board": (
        "TABLE(model text, documents bigint, runs bigint, runs_failed bigint, runs_degraded bigint,"
        " pages_ai bigint, pages_local bigint, pages_cached bigint, seconds_per_page_p50 double precision,"
        " seconds_per_page_p95 double precision, calls bigint, scan_calls bigint, cached_scan_calls bigint,"
        " input_tokens numeric, output_tokens numeric, cost_usd numeric, scan_cost_usd numeric,"
        " cost_estimated boolean, cost_part_unknown boolean, ocr_pages bigint, pages_disagree bigint,"
        " pages_refused bigint, pages_fell_back bigint, pages_unpinned bigint)",
        EMPTY_PATH,
    ),
    # 0038: the feature switches (counts only) and the platform test model (numbers only),
    # and their writes (step-up), each answering its outcome.
    "kaagaz_op_feature_switches": (
        "TABLE(feature text, enabled boolean, organisations_using bigint,"
        " changed_at timestamp with time zone)",
        EMPTY_PATH,
    ),
    "kaagaz_op_test_model": (
        "TABLE(scan_model text, chat_model text, note text, started_at timestamp with time zone,"
        " pages bigint, questions bigint, cost_usd numeric, seconds_per_page double precision)",
        EMPTY_PATH,
    ),
    **dict.fromkeys(
        ("kaagaz_op_set_feature_switch", "kaagaz_op_set_test_model", "kaagaz_op_clear_test_model"),
        ("text", EMPTY_PATH),
    ),
    # 0039: one organisation's feature switches, and their write (step-up).
    "kaagaz_op_org_feature_switches": (
        "TABLE(feature text, enabled boolean, platform_enabled boolean)",
        EMPTY_PATH,
    ),
    "kaagaz_op_set_org_feature_switch": ("text", EMPTY_PATH),
    # Changed on purpose (0040): the sign-in idle timeout, and its write (step-up).
    "kaagaz_op_session_timeout": ("TABLE(org_hours integer, platform_hours integer)", EMPTY_PATH),
    "kaagaz_op_set_session_timeout": ("text", EMPTY_PATH),
    "kaagaz_op_folder_upload_limits": (  # 0061c
        "TABLE(org_max_files integer, org_max_gb integer, org_max_folders integer,"
        " platform_max_files integer, platform_max_gb integer, platform_max_folders integer)",
        EMPTY_PATH,
    ),
    "kaagaz_op_set_folder_upload_limits": ("text", EMPTY_PATH),
    # 0046: operator-only read/write for customer and user RPM overrides.
    "kaagaz_op_model_rates": (
        "TABLE(org_rpm integer, user_id uuid, email text, user_rpm integer)",
        EMPTY_PATH,
    ),
    "kaagaz_op_set_model_rate": ("text", EMPTY_PATH),
    # 0053, package S: the platform's speed controls (numbers only), and their write (step-up).
    "kaagaz_op_speed": (
        "TABLE(model_rpm integer, documents_at_once integer, pages_at_once integer)",
        EMPTY_PATH,
    ),
    "kaagaz_op_set_speed": ("text", EMPTY_PATH),
    # 0051, PRD 6 F3, read-only: answers and ratings per organisation and ended ISO week, counts only.
    "kaagaz_op_answer_weeks": (
        "TABLE(customer_id uuid, week date, replies bigint, answered bigint, not_found bigint,"
        " clarify bigint, rated bigint, useful bigint)",
        EMPTY_PATH,
    ),
    # Changed on purpose (#386/#387, 27 Oct 2026): 0055, read-only, each organisation's numbers,
    # counts and codes only.
    "kaagaz_op_org_numbers": (
        "TABLE(customer_id uuid, documents bigint, documents_in_trash bigint, pages_read bigint,"
        " pages_by_route jsonb, failed_documents jsonb, active_users bigint, questions bigint,"
        " ai_calls bigint, ai_input_tokens bigint, ai_output_tokens bigint, ai_cost_usd numeric,"
        " ai_cost_unknown_calls bigint)",
        EMPTY_PATH,
    ),
}
# The only tables without customer_id: the customers themselves, alembic's own, the
# platform operator's (0015), the workers' reader status (0022) and the model catalogue
# (0023) and the platform AI defaults (0026), which belong to no customer.
OPERATOR_TABLES = {"operators", "platform_audit"}
PLATFORM_DEFAULTS = {"platform_models", "platform_ai_settings", "feature_switches", "platform_model_override"}
NO_CUSTOMER_ID = (
    {"customers", "alembic_version", "reader_status", "model_catalog", "model_rate_scheduler"}
    | PLATFORM_DEFAULTS
    | OPERATOR_TABLES
)

_CUSTOMER_TABLES = text(
    "SELECT c.relname, c.relrowsecurity, pg_get_userbyid(c.relowner)"
    " FROM pg_class c JOIN pg_attribute a ON a.attrelid = c.oid"
    " WHERE c.relnamespace = 'public'::regnamespace AND c.relkind IN ('r', 'p')"
    " AND a.attname = 'customer_id' AND NOT a.attisdropped"
    " ORDER BY c.relname"
)


def _customer_tables(db: Database) -> dict[str, tuple[bool, str]]:
    with db.system() as conn:
        return {name: (rls, owner) for name, rls, owner in conn.execute(_CUSTOMER_TABLES)}


def test_every_customer_table_has_row_level_security_and_the_customer_only_policy(db: Database) -> None:
    tables = _customer_tables(db)
    assert set(tables) >= (
        FROM_0001
        | FROM_0002
        | set(FROM_0004)
        | set(FROM_0008)
        | set(FROM_0009)
        | set(FROM_0021)
        | set(FROM_0023)
        | set(FROM_0026)
        | set(FROM_0030)
        | set(FROM_0036)
        | set(FROM_0038)
        | set(FROM_0039)
        | set(FROM_0040)
        | set(FROM_0041)  # Changed on purpose (PRD 1, 0041)
        | set(FROM_0043)
        | set(FROM_0049)
        | set(FROM_0051)
        | set(FROM_0056)
        | set(FROM_0061C)
    )

    with db.system() as conn:
        policies = {
            table: (command, permissive, roles, using, check)
            for table, command, permissive, roles, using, check in conn.execute(
                text(
                    "SELECT tablename, cmd, permissive, roles, qual, with_check FROM pg_policies"
                    " WHERE schemaname = 'public' AND policyname = 'customer_only'"
                )
            )
        }
        app_role = conn.execute(
            text("SELECT rolsuper, rolbypassrls FROM pg_roles WHERE rolname = :r"), {"r": APP_ROLE}
        ).one()

    assert tuple(app_role) == (False, False)
    for table, (rls, owner) in tables.items():
        assert rls, f"{table} has no row level security"
        assert owner != APP_ROLE, f"{table} is owned by the app role, which skips its policy"
        # For every command and every role, a row must be the current customer's
        # both to be seen (USING) and to be written (WITH CHECK).
        assert policies.get(table) == ("ALL", "PERMISSIVE", ["public"], POLICY, POLICY), table


def test_every_customer_table_is_granted_to_the_app_role(db: Database) -> None:
    tables = _customer_tables(db)
    with db.system() as conn:
        granted = {
            table: (select, insert, update, delete)
            for table, select, insert, update, delete in conn.execute(
                text(
                    "SELECT c.relname, has_table_privilege(:r, c.oid, 'SELECT'),"
                    " has_table_privilege(:r, c.oid, 'INSERT'), has_table_privilege(:r, c.oid, 'UPDATE'),"
                    " has_table_privilege(:r, c.oid, 'DELETE')"
                    " FROM pg_class c WHERE c.relnamespace = 'public'::regnamespace AND c.relname = ANY(:t)"
                ),
                {"r": APP_ROLE, "t": sorted(tables)},
            )
        }

    assert set(granted) == set(tables)
    for table, (select, insert, _update, _delete) in granted.items():
        # An organisation's models and settings are the operator's. Changed on purpose (0039):
        # its feature switches too.
        # Changed on purpose (0040): and its sign-in idle timeout.
        if table not in FROM_0023 | FROM_0026 | FROM_0039 | FROM_0040 | FROM_0043 | FROM_0061C:
            assert (select, insert) == (True, True), table
    for table in FROM_0002:
        assert granted[table] == (True, True, True, True), table
    for table, expected in (
        FROM_0004
        | FROM_0008
        | FROM_0009
        | FROM_0021
        | FROM_0023
        | FROM_0026
        | FROM_0030
        | FROM_0036
        | FROM_0038
        | FROM_0039
        | FROM_0040
        | FROM_0041  # Changed on purpose (PRD 1, 0041)
        | FROM_0043
        | FROM_0049
        | FROM_0051
        | FROM_0056
        | FROM_0061C
    ).items():
        assert granted[table] == expected, table


def test_the_tables_of_one_user_also_have_the_restrictive_own_user_only_policy(db: Database) -> None:
    """It must pass as well as customer_only: with no user set, no row is seen or written.
    The only other policies are the model catalogue's (0023) and the platform defaults' (0026):
    platform data, no customer."""
    with db.system() as conn:
        policies = {
            (table, name): (command, permissive, roles, using, check)
            for table, name, command, permissive, roles, using, check in conn.execute(
                text(
                    "SELECT tablename, policyname, cmd, permissive, roles, qual, with_check FROM pg_policies"
                    " WHERE schemaname = 'public' AND policyname <> 'customer_only'"
                )
            )
        }
    own = FROM_0008 | FROM_0009
    restrictive = ("ALL", "RESTRICTIVE", ["public"], OWN_USER, OWN_USER)
    assert policies == {(table, "own_user_only"): restrictive for table in own} | CATALOG_POLICIES


def _updatable(db: Database, table: str) -> set[str]:
    """The columns of ``table`` the app role may update."""
    with db.system() as conn:
        columns = conn.execute(
            text(
                "SELECT attname::text, has_column_privilege(:r, attrelid, attname, 'UPDATE')"
                " FROM pg_attribute WHERE attrelid = CAST(:t AS regclass)"
                " AND attnum > 0 AND NOT attisdropped"
            ),
            {"r": APP_ROLE, "t": table},
        ).all()
    return {name for name, may_update in columns if may_update}


def test_the_app_may_update_only_the_outcome_columns_of_a_run(db: Database) -> None:
    """Never its customer, document, start time or id."""
    assert _updatable(db, "processing_runs") == RUN_OUTCOME


def test_the_app_may_update_only_a_cached_reads_result_and_a_sections_vector(db: Database) -> None:
    """0021: never a page read's key (customer, document, page, image, model, prompt
    version), so an upsert can't move a read to another page or document; never a
    section's or a topic's content outside the replacing save."""
    assert _updatable(db, "page_reads") == PAGE_READ_UPDATES
    assert _updatable(db, "document_sections") == SECTION_UPDATES
    assert _updatable(db, "document_topics") == set()


def test_the_app_may_update_only_the_undo_and_progress_columns_of_word_review(db: Database) -> None:
    """0030: never a change's word, texts, who or when, so the history can't be rewritten;
    never a run's scope, pages or models, nor a reading's outcome or readings."""
    assert {table: _updatable(db, table) for table in UPDATES_0030} == UPDATES_0030


def test_the_app_may_update_only_the_spellings_of_a_value_key(db: Database) -> None:
    """0049: never a key's value, document, kind or place level, so a key can't be moved to
    another value or document."""
    assert {table: _updatable(db, table) for table in UPDATES_0049} == UPDATES_0049


def test_the_app_writes_only_the_refresh_columns_of_the_model_catalogue(db: Database) -> None:
    """0023: never who enabled a model or what verify-models measured, so a worker can't
    switch a model on or mark it verified. Row level security is on (its policies above),
    the app may not delete a row, and the app does not own the table."""
    with db.system() as conn:
        insertable = conn.execute(
            text(
                "SELECT attname::text FROM pg_attribute WHERE attrelid = 'model_catalog'::regclass"
                " AND attnum > 0 AND NOT attisdropped"
                " AND has_column_privilege(:r, attrelid, attname, 'INSERT')"
            ),
            {"r": APP_ROLE},
        ).all()
        locks = conn.execute(
            text(
                "SELECT relrowsecurity, pg_get_userbyid(relowner) <> :r,"
                " has_table_privilege(:r, oid, 'DELETE') FROM pg_class WHERE oid = 'model_catalog'::regclass"
            ),
            {"r": APP_ROLE},
        ).one()
    assert _updatable(db, "model_catalog") == CATALOG_REFRESH
    assert {str(row[0]) for row in insertable} == CATALOG_REFRESH | {"id"}
    assert tuple(locks) == (True, True, False)


def test_the_app_may_set_a_documents_model_and_who_it_is_billed_to(db: Database) -> None:
    """0023: an upload and a read again write them (the table's UPDATE grant, kept); 0026: the
    reading mode and Ensemble's other readers too, and the pipeline a value's vote."""
    assert {"read_model", "read_requested_by", "read_mode", "read_also"} <= _updatable(db, "documents")
    assert "votes" in _updatable(db, "extracted_values")


def test_the_app_may_update_only_the_upload_limit_and_the_auto_filing_switch_of_its_customer(
    db: Database,
) -> None:
    """Never its status, so a disabled organisation cannot switch itself back on (0014),
    nor its AI budget, which the operator sets (0023), nor the auto-filing grant (0025)."""
    with db.system() as conn:
        columns = conn.execute(
            text(
                "SELECT attname::text, has_column_privilege(:r, attrelid, attname, 'UPDATE')"
                " FROM pg_attribute WHERE attrelid = 'customers'::regclass"
                " AND attnum > 0 AND NOT attisdropped"
            ),
            {"r": APP_ROLE},
        ).all()
    # Changed on purpose (auto-filing council, 15 Oct 2026): the admin's switch, 0025.
    assert {name for name, may_update in columns if may_update} == {"max_upload_mb", "autofile_on"}


_FOREIGN_KEYS = text(
    "SELECT con.conname, con.conrelid::regclass::text, con.confrelid::regclass::text,"
    " ARRAY(SELECT a.attname::text FROM unnest(con.conkey) WITH ORDINALITY AS k (num, pos)"
    "  JOIN pg_attribute a ON a.attrelid = con.conrelid AND a.attnum = k.num ORDER BY k.pos),"
    " ARRAY(SELECT a.attname::text FROM unnest(con.confkey) WITH ORDINALITY AS k (num, pos)"
    "  JOIN pg_attribute a ON a.attrelid = con.confrelid AND a.attnum = k.num ORDER BY k.pos)"
    " FROM pg_constraint con WHERE con.contype = 'f' AND con.connamespace = 'public'::regnamespace"
)


def test_every_foreign_key_between_customer_tables_matches_the_customer_on_both_sides(db: Database) -> None:
    """A key on the id alone would let a row hang under another customer's parent: the
    key checks run without row level security."""
    tables = _customer_tables(db)
    with db.system() as conn:
        keys = [tuple(row) for row in conn.execute(_FOREIGN_KEYS)]
    between = [key for key in keys if key[1] in tables and key[2] in tables]

    assert {key[1] for key in between} >= (
        set(FROM_0004)
        | set(FROM_0021)
        | set(FROM_0030)
        | set(FROM_0036)
        | set(FROM_0038)
        | set(FROM_0041)  # Changed on purpose (PRD 1, 0041)
        | set(FROM_0049)
        | set(FROM_0056)
        | {"folder_upload_folders", "folder_upload_items"}  # 0061c
        | {"pages", "model_usage", "documents"}  # documents.owner_id (0041)
    )
    for name, _table, _parent, columns, parent_columns in between:
        assert ("customer_id", "customer_id") in zip(columns, parent_columns, strict=True), name


def test_only_the_listed_tables_have_no_customer_id(db: Database) -> None:
    with db.system() as conn:
        every = conn.execute(
            text(
                "SELECT relname FROM pg_class"
                " WHERE relnamespace = 'public'::regnamespace AND relkind IN ('r', 'p')"
            )
        )
        names = {str(row[0]) for row in every}
    assert names - set(_customer_tables(db)) == NO_CUSTOMER_ID


def test_only_the_listed_owner_rights_functions_exist_and_only_their_role_may_run_them(db: Database) -> None:
    """Found from the catalog, so a new one can't be left out: what it returns, its
    fixed search path, never runnable by PUBLIC, and by the app (OWNER_RIGHTS) or
    the operator (OPERATOR_RIGHTS), never both."""
    with db.system() as conn:
        functions = conn.execute(
            text(
                "SELECT p.proname, pg_get_function_result(p.oid), p.proconfig,"
                " has_function_privilege('public', p.oid, 'EXECUTE'),"
                " has_function_privilege(:r, p.oid, 'EXECUTE'), has_function_privilege(:o, p.oid, 'EXECUTE')"
                " FROM pg_proc p WHERE p.pronamespace = 'public'::regnamespace AND p.prosecdef"
            ),
            {"r": APP_ROLE, "o": OPERATOR},
        ).all()
    assert {
        name: (result, config) for name, result, config, *_ in functions
    } == OWNER_RIGHTS | OPERATOR_RIGHTS
    for name, *_, public_may_run, app_may_run, operator_may_run in functions:
        expected = (False, name in OWNER_RIGHTS, name in OPERATOR_RIGHTS)
        assert (public_may_run, app_may_run, operator_may_run) == expected, name


def test_the_operator_role_has_no_table_rights_and_its_tables_no_grants_and_no_policy(db: Database) -> None:
    """kaagaz_operator reaches data only through its functions; the app has no right on
    the operator's tables; and with no policy, row level security shows them to no one."""
    with db.system() as conn:
        role = conn.execute(
            text("SELECT rolsuper, rolbypassrls, rolcanlogin FROM pg_roles WHERE rolname = :o"),
            {"o": OPERATOR},
        ).one()
        granted = conn.execute(
            text(
                "SELECT c.relname, r.rolname FROM pg_class c CROSS JOIN pg_roles r"
                " WHERE c.relnamespace = 'public'::regnamespace AND c.relkind IN ('r', 'p', 'v', 'm')"
                " AND (r.rolname = :o OR (r.rolname = :a AND c.relname = ANY(:t)))"
                " AND (has_table_privilege(r.oid, c.oid, 'SELECT,INSERT,UPDATE,DELETE,TRUNCATE,REFERENCES')"
                " OR has_any_column_privilege(r.oid, c.oid, 'SELECT,INSERT,UPDATE,REFERENCES'))"
            ),
            {"o": OPERATOR, "a": APP_ROLE, "t": sorted(OPERATOR_TABLES)},
        ).all()
        locked = conn.execute(
            text(
                "SELECT c.relname, c.relrowsecurity, (SELECT count(*) FROM pg_policy WHERE polrelid = c.oid)"
                " FROM pg_class c WHERE c.relnamespace = 'public'::regnamespace AND c.relname = ANY(:t)"
            ),
            {"t": sorted(OPERATOR_TABLES)},
        ).all()
    assert (tuple(role), granted) == ((False, False, False), [])
    assert sorted(tuple(row) for row in locked) == [("operators", True, 0), ("platform_audit", True, 0)]


# 0024's readers (F1-F4): read-only, no dynamic SQL, the operator check, and no column
# that could name or point at a person or content. Counts named in the plural
# (``documents``, ``active_users_7d``) are numbers and allowed; an id other than the
# organisation's, a name, an e-mail or a bare person or content column is not.
INSIGHT_READERS = (
    "kaagaz_op_daily",
    "kaagaz_op_org_health",
    "kaagaz_op_quality",
    "kaagaz_op_model_board",
    "kaagaz_op_answer_weeks",  # 0051, PRD 6 F3
    # Changed on purpose (#386/#387, 27 Oct 2026): 0055, each organisation's numbers.
    "kaagaz_op_org_numbers",
)
ENABLED = "o.id = p_operator AND NOT o.disabled AND o.totp_enabled_at IS NOT NULL"
_PERSON_OR_CONTENT = re.compile(
    r"(^|_)id$|email|name|^(user|document|conversation|message|batch|folder|tag|text|answer|question)$"
)


def test_the_insight_readers_are_stable_static_check_the_operator_and_name_nobody(db: Database) -> None:
    with db.system() as conn:
        readers = conn.execute(
            text(
                "SELECT p.proname, p.provolatile, p.prosrc,"
                " ARRAY(SELECT a.n FROM unnest(p.proargnames, p.proargmodes) AS a (n, m) WHERE a.m = 't')"
                " FROM pg_proc p WHERE p.pronamespace = 'public'::regnamespace AND p.proname = ANY(:f)"
            ),
            {"f": list(INSIGHT_READERS)},
        ).all()
    assert sorted(row[0] for row in readers) == sorted(INSIGHT_READERS)
    for name, volatility, source, columns in readers:
        assert volatility == "s", name  # STABLE: reads only
        assert "EXECUTE" not in source.upper(), name  # no dynamic SQL
        assert ENABLED in source, name
        assert columns, name
        assert [c for c in columns if c != "customer_id" and _PERSON_OR_CONTENT.search(c)] == [], name
    caught = ("user_id", "document_id", "display_name", "email", "customer_name", "message", "id")
    assert all(_PERSON_OR_CONTENT.search(sample) for sample in caught)  # the pattern works
