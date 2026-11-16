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

The reading pause (0066, #478): the live counter, read, added and only its counts and alert time
written again, never deleted by the app (it goes with its organisation).

Audit keeping (0068, #482): a deleted organisation's audit rows, granted to no role (only the
owner reads them); the worker's sweep that removes them after 365 days; and the operator's
switch that requires two-step sign-in for an organisation's admins (#481).

Folder upload (0071, #484, NEEDS VRUSHIT'S APPROVAL): an upload, its folders and its items the
app reads, adds and updates, never deletes (they go with the customer); an organisation's own
limits it reads in four columns only, written by the operator's two functions (step-up).
"Add new files only" (#484, 0072): the upload counts the files it sends
(``items_counted``); the browser's SHA-256 of a file is never kept, on the upload or its items.

Storage limit per organisation (0061d, merged by 0075, #516): an organisation's own limit the app
reads in two columns only, written by the operator's two functions (step-up).

A folder in Trash (0081, #542, NEEDS VRUSHIT'S APPROVAL): read, added and deleted by the app,
never updated.

Who can see what (0084, PRD 7, #590, NEEDS VRUSHIT'S APPROVAL): department members, the people
named on a private folder, files given and lock grants, read, added and deleted by the app; only a
membership's Head tick is ever changed. Their triggers (0085, #593) run with the owner's rights,
which no role may run.

Page turns saved for everyone (0088, PRD 7 section 14, #600, NEEDS VRUSHIT'S APPROVAL): read, added
and deleted by the app, never updated; a save deletes the pages it changes and adds them again.
Bounded word search (0094, #617, NEEDS VRUSHIT'S SECURITY SIGN-OFF): the word counts, read, added
and deleted by the worker's rebuild, never updated; and ``kaagaz_word_candidates``, piece ids only.
Kaagaz on WhatsApp (0097, #603, NEEDS VRUSHIT'S APPROVAL): the Admin's switches, connections,
codes, messages and pickers (the last three one person's own, as my words), read and added by the
app, which writes only the columns listed and deletes only a picker; a connection ends, its number
gone, only through the owner-rights functions; the numbers told they are not connected or blocked
belong to no organisation and no role reads them.
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
# Email links (0061, #457 phase 0): read and added; only used_at may change (a column grant).
FROM_0061 = {"email_links": (True, True, False, False)}
# Connected accounts (0062, #457 phase 1): read, added and deleted; only the approval and
# last-used columns may change (a column grant).
FROM_0062 = {"user_identities": (True, True, False, True)}
# Changed on purpose (#507, 0074): Google's hd claim, refreshed at each sign-in, and its email,
# kept with hd when a Google sign-in is refused for the domains (#507 review).
UPDATES_0062 = {
    "user_identities": {
        "status",
        "approved_at",
        "approved_by",
        "last_used_at",
        "hosted_domain",
        "email_at_link",
    }
}
# The reading pause (0066, #478): a day's counter is added and counted up (a column grant).
FROM_0066 = {"reading_days": (True, True, False, False)}
UPDATES_0066 = {"reading_days": {"pages", "cost_usd", "alerted_at"}}
# Audit keeping (0068, #482): a deleted organisation's audit rows, no right for the app at all.
FROM_0068 = {"audit_log_kept": (False, False, False, False)}
# Folder upload (0071): never deleted by the app; the limits are a column grant, the operator writes.
FROM_0071 = {
    "folder_uploads": (True, True, True, False),
    "folder_upload_folders": (True, True, True, False),
    "folder_upload_items": (True, True, True, False),
    "org_upload_limits": (False, False, False, False),
}
# Storage limit (0061d): a column grant only, the operator writes.
FROM_0061D = {"org_storage_limits": (False, False, False, False)}
# A folder in Trash (0081, #542): read, added and deleted (restored, forever, purged), never updated.
FROM_0081 = {"trashed_folders": (True, True, False, True)}
# Who can see what (0084, #590): read, added and deleted; a membership's Head tick alone is changed
# (UPDATES_0084, a column grant).
FROM_0084 = {
    "department_members": (True, True, False, True),
    "private_folder_members": (True, True, False, True),
    "document_shares": (True, True, False, True),
    "lock_grants": (True, True, False, True),
}
UPDATES_0084 = {"department_members": {"is_head"}}
# Page turns saved for everyone (0088, #600): read, added and deleted, never updated.
FROM_0088 = {"page_rotations": (True, True, False, True)}
# Bounded word search (0094, #617): the word counts, deleted and added again by the rebuild.
FROM_0094 = {"term_df": (True, True, False, True), "customer_stats": (True, True, False, True)}
# Kaagaz on WhatsApp (0097, #603): read and added; a picker deleted; only the columns of UPDATES_0097
# updated (column grants), never a connection's person, number, sign-in version or end (only
# kaagaz_whatsapp_end_link and the other owner-rights functions end one).
FROM_0097 = {
    "whatsapp_org_settings": (True, True, False, False),
    "whatsapp_links": (True, True, False, False),
    "whatsapp_link_codes": (True, True, False, True),  # Changed on purpose (W3): a code used is deleted
    "whatsapp_messages": (True, True, False, False),
    "whatsapp_pickers": (True, True, False, True),
}
UPDATES_0097 = {
    # Changed on purpose (W3): the Admin's "our data rules allow WhatsApp", when and who.
    "whatsapp_org_settings": {
        "enabled",
        "roles",
        "management",
        "voice",
        "uploads",
        "rules_confirmed_at",
        "rules_confirmed_by",
    },
    "whatsapp_links": {"last_used_at", "recent_folder_ids", "busy_until"},
    "whatsapp_link_codes": {"tries", "used_at"},
    "whatsapp_messages": {"state", "tries", "body", "batch_id", "document_id", "reply_sent_at"},
    "whatsapp_pickers": {"generation", "state", "expires_at"},
}
# Logout ends this browser's session (0076, #502): the app adds and reads ended sessions only;
# the worker's sweep deletes them through kaagaz_sweep_ended_sessions.
FROM_0076 = {"ended_sessions": (True, True, False, False)}
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
    "price_request_usd", "price_image_usd",  # Changed on purpose (0070, #488 review): the prices a call
    "parameters",  # Changed on purpose (0095, #647): the supported parameters
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
# Changed on purpose (#606, 0091): the tables the per-person rule (person_only) holds.
PERSON_RULE_TABLES = {
    "documents", "folders", "trashed_folders", "corrections", "document_links", "document_sections",
    "document_stories", "document_tags", "document_topics", "ensemble_readings", "ensemble_runs",
    "extracted_values", "invoice_lines", "invoices", "learning_removals", "model_usage",
    "organization_word_suggestions", "page_blocks", "page_reads", "page_rotations", "pages", "pieces",
    "processing_runs", "reader_outputs", "source_pins", "table_cells", "user_word_spots", "value_keys",
    "version_suggestions", "word_changes",
}  # fmt: skip
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
# A restore's rows (0092): the topmost folder made live and the one asked for, or why nothing was done.
RESTORED = (
    "TABLE(id uuid, parent_id uuid, name text, created_at timestamp with time zone, kind text,"
    " sort_order integer, department_only boolean, private boolean, is_top boolean, is_asked boolean,"
    " trash_id uuid, refused text, renamed boolean, relocated boolean, sealed boolean, below uuid[],"
    " families uuid[])"
)
OWNER_RIGHTS = {
    # Changed on purpose (#479, 3 Nov 2026): 0067 gives 0001's two the empty path as well.
    "kaagaz_login_lookup": (
        "TABLE(user_id uuid, customer_id uuid, password_hash text, role text, disabled boolean)",
        EMPTY_PATH,
    ),  # 0001, sign-in
    "kaagaz_create_customer": ("uuid", EMPTY_PATH),  # 0001
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
    # 0076, the worker's sweep of ended sessions whose cookie expired: a count only.
    "kaagaz_sweep_ended_sessions": ("integer", EMPTY_PATH),
    # 0046, the shared scheduler's single admission function.
    "kaagaz_model_rate_slot": ("double precision", EMPTY_PATH),
    # 0061, email: a clicked link found by its hash (ids, purpose and times), and self sign-up
    # with email on (a pending customer).
    "kaagaz_email_link_lookup": (
        "TABLE(link_id uuid, customer_id uuid, user_id uuid, purpose text,"
        " expires_at timestamp with time zone, used_at timestamp with time zone)",
        EMPTY_PATH,
    ),
    "kaagaz_create_pending_customer": ("uuid", EMPTY_PATH),
    # 0062, connected accounts: a provider sign-in's connection found by its key (ids and
    # status), and whether an e-mail is another user's (yes or no).
    "kaagaz_identity_lookup": (
        "TABLE(identity_id uuid, user_id uuid, customer_id uuid, status text)",
        EMPTY_PATH,
    ),
    "kaagaz_email_belongs_to_other": ("boolean", EMPTY_PATH),
    # 0063: a pending, unconfirmed sign-up older than 24 hours no longer holds its e-mail.
    "kaagaz_release_stale_signup": ("boolean", EMPTY_PATH),
    # 0064 (#463): the worker claims and ends its own organisation's accuracy check (numbers only).
    # Changed on purpose (#463 review): its Dynamic, and the job's heartbeat.
    "kaagaz_accuracy_check_claim": (
        "TABLE(model text, scan_thinking text, scan_resolution text, careful_thinking text,"
        " careful_resolution text, page_max_output_tokens integer, dynamic boolean, max_pages integer)",
        EMPTY_PATH,
    ),
    "kaagaz_accuracy_check_beat": ("boolean", EMPTY_PATH),
    "kaagaz_accuracy_check_finish": ("boolean", EMPTY_PATH),
    # 0066 (#478): the release sweep's organisations with a held document, ids only.
    "kaagaz_customer_ids_held": ("SETOF uuid", EMPTY_PATH),
    # 0068 (#482): the worker removes deleted organisations' audit rows after 365 days (a count).
    "kaagaz_forget_kept_audit": ("bigint", EMPTY_PATH),
    # Changed on purpose (#595, 0086): the current person's access, for the access views: their
    # own row and the ids given to them only, from kaagaz.customer_id and kaagaz.user_id alone.
    "kaagaz_access": ("kaagaz_access_set", EMPTY_PATH),
    "kaagaz_shared_with_me": ("SETOF uuid", EMPTY_PATH),
    "kaagaz_lock_open_to_me": ("SETOF uuid", EMPTY_PATH),
    # Changed on purpose (#601, 0090): the access API's, each checking its caller inside (an Admin;
    # who sees a file or folder, also the Head of its department who can see it).
    "kaagaz_audience": (
        "TABLE(user_id uuid, management boolean, admin boolean, member boolean, head boolean, named boolean,"
        " given boolean, held boolean, everyone boolean, may_open_locked boolean, sees boolean)",
        EMPTY_PATH,
    ),
    "kaagaz_place_audience": ("TABLE(user_id uuid, sees boolean)", EMPTY_PATH),
    "kaagaz_kept_from_me": (
        "TABLE(id uuid, display_name text, detected_type text, uploader text,"
        " created_at timestamp with time zone, version_no integer, locked boolean)",
        EMPTY_PATH,
    ),
    "kaagaz_admin_document": (
        "TABLE(id uuid, family_id uuid, display_name text, in_trash boolean, locked boolean)",
        EMPTY_PATH,
    ),
    "kaagaz_give_file": ("TABLE(user_id uuid, versions integer, added integer)", EMPTY_PATH),
    "kaagaz_take_file": ("integer", EMPTY_PATH),
    # Changed on purpose (#601, 0090b): auto-filing gives the uploader the file; no person only.
    "kaagaz_give_uploader": ("integer", EMPTY_PATH),
    # Changed on purpose (#606, 0091): the database rule's switch, fast path and new-version check.
    "kaagaz_person_rule_on": ("boolean", EMPTY_PATH),
    "kaagaz_sees_everything": ("boolean", EMPTY_PATH),
    "kaagaz_family_visible": ("boolean", EMPTY_PATH),
    # Changed on purpose (#606, 0091): what the rule would narrow, read whole; ids and numbers only.
    "kaagaz_duplicate": ("TABLE(sha256 text, size_bytes bigint, answer text, document_id uuid)", EMPTY_PATH),
    "kaagaz_existing": ("SETOF uuid", EMPTY_PATH),
    "kaagaz_hidden_inside": (
        "TABLE(hidden_files bigint, hidden_folders bigint, any_files boolean)",
        EMPTY_PATH,
    ),
    "kaagaz_trash_top": ("TABLE(id uuid, kind text)", EMPTY_PATH),
    "kaagaz_document_place": (
        "TABLE(department_id uuid, private_folder_id uuid, held_for uuid, folder_id uuid, family_id uuid)",
        EMPTY_PATH,
    ),
    "kaagaz_folder_place": (
        "TABLE(department_id uuid, private_folder_id uuid, private boolean, department_only boolean)",
        EMPTY_PATH,
    ),
    "kaagaz_admin_files": ("bigint", EMPTY_PATH),
    "kaagaz_folder_depth": ("TABLE(depth integer, cycle boolean, height integer)", EMPTY_PATH),
    "kaagaz_storage_used": ("bigint", EMPTY_PATH),
    "kaagaz_spent": ("TABLE(spent numeric, unknown boolean)", EMPTY_PATH),
    "kaagaz_ensemble_runs_this_hour": ("bigint", EMPTY_PATH),
    "kaagaz_word_suggestions_count": ("bigint", EMPTY_PATH),
    "kaagaz_ready_stamp": ("text", EMPTY_PATH),
    # Changed on purpose (#606, 0091): an Admin's names and numbers, whole (decision 20).
    "kaagaz_admin_stats": (
        "TABLE(documents_by_status jsonb, questions_total bigint, answered bigint, not_found bigint,"
        " verified_answers bigint, needs_review_answers bigint, feedback_up bigint, feedback_down bigint,"
        " open_review_items bigint, older_versions bigint, in_trash bigint)",
        EMPTY_PATH,
    ),
    "kaagaz_admin_member_numbers": (
        "TABLE(id uuid, display_name text, email text, role text, disabled boolean,"
        " last_active_at timestamp with time zone, documents_uploaded bigint, pages_read numeric,"
        " questions_asked bigint, values_verified bigint)",
        EMPTY_PATH,
    ),
    "kaagaz_admin_failed": (
        "TABLE(id uuid, display_name text, detected_type text, status text, failure_code text,"
        " page_count integer, attempts integer, max_attempts integer,"
        " last_run_at timestamp with time zone, total bigint)",
        EMPTY_PATH,
    ),
    "kaagaz_admin_reader_pages": (
        "TABLE(pages_cells bigint, pages_text_layer bigint, pages_ocr bigint, pages_blank bigint,"
        " pages_gemini bigint, pages_local bigint, pages_cached bigint, pages_read_again bigint,"
        " input_tokens bigint, output_tokens bigint)",
        EMPTY_PATH,
    ),
    "kaagaz_admin_usage": (
        "TABLE(dimension text, user_id uuid, model text, kind text, day date, document_id uuid,"
        " of_document boolean, calls bigint, cached_calls bigint, input_tokens numeric,"
        " output_tokens numeric, reasoning_tokens numeric, cost_usd numeric, cost_estimated boolean,"
        " cost_part_unknown boolean,"
        " user_name text, model_name text, document_name text)",
        EMPTY_PATH,
    ),
    "kaagaz_admin_usage_rows": (
        "TABLE(day date, user_name text, model text, kind text, calls bigint, tokens numeric,"
        " cost_usd numeric, cost_estimated boolean, cost_part_unknown boolean)",
        EMPTY_PATH,
    ),
    "kaagaz_admin_folder_summary": ("TABLE(folders bigint, files bigint, size_bytes numeric)", EMPTY_PATH),
    "kaagaz_admin_document_names": ("TABLE(id uuid, display_name text, version_no integer)", EMPTY_PATH),
    "kaagaz_admin_given_to": (
        "TABLE(id uuid, display_name text, folder_id uuid, given_at timestamp with time zone, given_by uuid)",
        EMPTY_PATH,
    ),
    # Changed on purpose (#606, PR 9b, 0092): the writes over rows the person can't see; ids and counts.
    "kaagaz_admin_trash_in_folders": ("integer", EMPTY_PATH),
    "kaagaz_detach_trashed": ("integer", EMPTY_PATH),
    "kaagaz_admin_move_folder_documents": ("integer", EMPTY_PATH),
    "kaagaz_restore_folder": (RESTORED, EMPTY_PATH),
    "kaagaz_restore_with_folders": (RESTORED, EMPTY_PATH),
    "kaagaz_trash_uploaders": ("uuid[]", EMPTY_PATH),
    "kaagaz_trash_families": ("SETOF uuid", EMPTY_PATH),
    "kaagaz_purge_family": ("TABLE(document_id uuid, folder_id uuid)", EMPTY_PATH),
    "kaagaz_admin_move_documents": ("TABLE(document_id uuid, family_id uuid, from_folder uuid)", EMPTY_PATH),
    "kaagaz_admin_trash_documents": ("TABLE(document_id uuid, family_id uuid, trashed boolean)", EMPTY_PATH),
    # Changed on purpose (#605, 0093): locked files, each an Admin's only, checked inside.
    "kaagaz_lock": (
        "TABLE(document_id uuid, outcome text, version_id uuid, prepare boolean, rendition boolean)",
        EMPTY_PATH,
    ),
    "kaagaz_unlock": ("TABLE(document_id uuid, outcome text, version_id uuid, read boolean)", EMPTY_PATH),
    "kaagaz_lock_grant": ("text", EMPTY_PATH),
    "kaagaz_lock_revoke": ("integer", EMPTY_PATH),
    # 0094 (#617): the word search's candidates, the current customer's piece ids only, each call
    # planned for its own query.
    "kaagaz_word_candidates": ("SETOF uuid", [*EMPTY_PATH, "plan_cache_mode=force_custom_plan"]),
    # Changed on purpose (#605, 0096): the locked-file pop-ups' names, an Admin's only, checked inside.
    "kaagaz_admin_place_files": ("bigint", EMPTY_PATH),
    "kaagaz_admin_place_locked": (
        "TABLE(id uuid, display_name text, folder_id uuid, open boolean)",
        EMPTY_PATH,
    ),
    "kaagaz_admin_locked_for": (
        "TABLE(id uuid, display_name text, folder_id uuid, given_at timestamp with time zone, given_by uuid)",
        EMPTY_PATH,
    ),
    # Changed on purpose (#603, 0097): WhatsApp's number lookup and the channel's claim (ids only),
    # the two ways a connection ends (a count, or yes or no) and the sweep (a count).
    "kaagaz_whatsapp_lookup": ("TABLE(customer_id uuid, user_id uuid, link_id uuid)", EMPTY_PATH),
    "kaagaz_whatsapp_claim": (
        "TABLE(customer_id uuid, user_id uuid, link_id uuid, busy_until timestamp with time zone)",
        EMPTY_PATH,
    ),
    # A number with no connection: whether it may be told so now (yes or no), and its BLOCK.
    "kaagaz_whatsapp_tell_unknown": ("boolean", EMPTY_PATH),
    "kaagaz_whatsapp_block": ("void", EMPTY_PATH),
    "kaagaz_whatsapp_end_number": ("bigint", EMPTY_PATH),
    "kaagaz_whatsapp_end_link": ("boolean", EMPTY_PATH),
    "kaagaz_whatsapp_sweep": ("bigint", EMPTY_PATH),
    # Changed on purpose (W3): whether a connect code may go to a number (a word), and the second
    # way's "Link <code>" from the number (yes or no).
    "kaagaz_whatsapp_may_send": ("text", EMPTY_PATH),
    "kaagaz_whatsapp_phone_code": ("boolean", EMPTY_PATH),
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
        " last_activity_at timestamp with time zone, autofile_granted boolean, autofile_on boolean,"
        " admin_email_confirmed boolean)",  # Changed on purpose (0061): Approve needs it
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
            "kaagaz_op_approve_customer",  # 0061
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
        " answer_questions_costed bigint, answer_cost_usd numeric, answer_cost_unknown_calls bigint,"
        " price_request_usd numeric, price_image_usd numeric, parameters text[])",
        # 0026: the answer columns; 0033: the real cost, counts and sums only; changed on purpose
        # (0070, #488 review): the prices a call, for the operator's check of Ultra low cost's lead;
        # changed on purpose (0095, #647): the supported parameters
        EMPTY_PATH,
    ),
    "kaagaz_op_set_model_enabled": ("text", EMPTY_PATH),
    # 0026, features 25-27, in place of 0023's kaagaz_op_org_models and kaagaz_op_set_org_models:
    # one organisation's AI settings and budget, the platform defaults with counts of the
    # organisations each area reaches, and the two saves (step-up). Pinned: no user, no document.
    "kaagaz_op_org_ai": (
        "TABLE(scan_models text[], scan_default text, chat_models text[], chat_default text,"
        " scan_thinking text, chat_levels text[], chat_default_level text, dynamic boolean,"
        " scan_resolution text, careful_thinking text, careful_resolution text,"
        " page_max_output_tokens integer,"
        " scan_reader text, mistral_model text,"  # Changed on purpose (#473, 0065): who reads the pages
        " daily_page_limit integer,"  # Changed on purpose (#478, 0066): the reading pause
        " ulc_ceiling_paise smallint,"  # Changed on purpose (#488, 0070): Ultra low cost's ceiling
        " ensemble boolean,"
        " ai_budget_usd numeric)",  # 0064 (#463): the scan cost settings
        EMPTY_PATH,
    ),
    "kaagaz_op_set_org_ai": ("record", EMPTY_PATH),
    "kaagaz_op_ai_defaults": (
        "TABLE(scan_models text[], scan_default text, chat_models text[], chat_default text,"
        " scan_thinking text, chat_levels text[], chat_default_level text, dynamic boolean,"
        " scan_resolution text, careful_thinking text, careful_resolution text,"
        " page_max_output_tokens integer,"
        " scan_reader text, mistral_model text,"  # Changed on purpose (#473, 0065)
        " daily_page_limit integer,"  # Changed on purpose (#478, 0066)
        " ulc_ceiling_paise smallint,"  # Changed on purpose (#488, 0070)
        " orgs_inheriting_scan_models bigint, orgs_inheriting_chat_models bigint,"
        " orgs_inheriting_scan_thinking bigint, orgs_inheriting_chat_levels bigint,"
        " orgs_inheriting_dynamic bigint, orgs_inheriting_scan_resolution bigint,"
        " orgs_inheriting_careful_thinking bigint, orgs_inheriting_careful_resolution bigint,"
        " orgs_inheriting_page_max_output_tokens bigint, orgs_inheriting_scan_reader bigint,"
        " orgs_inheriting_mistral_model bigint, orgs_inheriting_daily_page_limit bigint,"
        " orgs_inheriting_ulc_ceiling_paise bigint)",  # Changed on purpose (#488, 0070)
        EMPTY_PATH,
    ),
    # 0066 (#478): one organisation's reading pause counter on one day (numbers only).
    "kaagaz_op_reading_today": ("TABLE(pages integer, cost_usd numeric)", EMPTY_PATH),
    "kaagaz_op_set_ai_defaults": ("text", EMPTY_PATH),
    # 0064 (#463): the estimate's measured cost, sums and counts only (no customer column).
    "kaagaz_op_scan_cost": (
        "TABLE(first_usd numeric, first_pages bigint, careful_usd numeric, careful_pages bigint,"
        " scan_calls bigint, careful_calls bigint, quality_pages bigint, clean_pages bigint,"
        " max_quality_pages bigint, max_clean_pages bigint, output_p99 integer, tokens_in numeric,"
        " tokens_out numeric, token_pages bigint, limit_hits bigint)",  # Changed on purpose (#463 review)
        EMPTY_PATH,
    ),
    # 0064 (#463): an organisation's accuracy check, started with a fresh code, and read back
    # (settings and numbers, never text).
    "kaagaz_op_start_accuracy_check": ("record", EMPTY_PATH),
    "kaagaz_op_accuracy_check": (
        "TABLE(id uuid, status text, model text, scan_thinking text, scan_resolution text,"
        " careful_thinking text, careful_resolution text, page_max_output_tokens integer,"
        " dynamic boolean, max_pages integer, pages_checked integer,"  # Changed on purpose (#463 review)
        " pages_failed integer, word_match double precision, numbers_match double precision,"
        " cost_usd numeric, started_at timestamp with time zone, finished_at timestamp with time zone)",
        EMPTY_PATH,
    ),
    # 0070 (#488): what Ultra low cost really cost for one model (sums and counts only, no
    # customer column).
    "kaagaz_op_ulc_cost": (
        "TABLE(pages bigint, cost_usd numeric, limit_hits bigint, top_usd_per_page numeric)",
        EMPTY_PATH,
    ),
    # 0065 (#473): whether Mistral stopped reading pages, every organisation's together (a code,
    # a time and a count; no customer column).
    "kaagaz_op_mistral_status": (
        "TABLE(code text, since timestamp with time zone, documents_waiting bigint)",
        EMPTY_PATH,
    ),
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
    # 0068 (#481): two-step sign-in required for an organisation's admins, and its write (step-up).
    "kaagaz_op_admin_two_step": ("boolean", EMPTY_PATH),
    "kaagaz_op_set_admin_two_step": ("text", EMPTY_PATH),
    "kaagaz_op_folder_upload_limits": (  # 0071
        "TABLE(org_max_files integer, org_max_gb integer, org_max_folders integer,"
        " platform_max_files integer, platform_max_gb integer, platform_max_folders integer)",
        EMPTY_PATH,
    ),
    "kaagaz_op_set_folder_upload_limits": ("text", EMPTY_PATH),
    # 0074 (#507): Google sign-in per organisation and for the platform, and its write (step-up).
    "kaagaz_op_google_signin": (
        "TABLE(org_on boolean, platform_on boolean, only_roles text[], domains text[])",
        EMPTY_PATH,
    ),
    "kaagaz_op_set_google_signin": ("text", EMPTY_PATH),
    # 0061d, #516: the storage limits (numbers and the bytes used only), and their write (step-up).
    "kaagaz_op_storage_limit": (
        "TABLE(org_storage_gb integer, platform_storage_gb integer, used_bytes bigint)",
        EMPTY_PATH,
    ),
    "kaagaz_op_set_storage_limit": ("text", EMPTY_PATH),
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
# Changed on purpose (#593, 0085): who-can-see-what's triggers, owner's rights so a person's rule
# never hides a row from them; no role may run them, only their triggers do.
TRIGGER_RIGHTS = dict.fromkeys(
    (
        "kaagaz_folder_access",
        "kaagaz_document_access",
        "kaagaz_version_access",
        "kaagaz_access_removed",
        "kaagaz_access_changed",
        "kaagaz_walls_set",  # 0089 (#594)
        "kaagaz_user_switched",  # Changed on purpose (#601, 0090)
        "kaagaz_lock_grants_follow",  # Changed on purpose (#605, 0093)
        "kaagaz_refuse_locked_content",  # Changed on purpose (#605, 0093)
    ),
    ("trigger", EMPTY_PATH),
) | {
    "kaagaz_access_of": ("kaagaz_access_set", EMPTY_PATH),  # 0090: internal, run by none (#601)
    "kaagaz_clear_derived": ("void", EMPTY_PATH),  # 0093: internal, run by kaagaz_lock only (#605)
}
# The only tables without customer_id: the customers themselves, alembic's own, the
# platform operator's (0015), the workers' reader status (0022) and the model catalogue
# (0023) and the platform AI defaults (0026), which belong to no customer.
# Changed on purpose (#463, 0064): the accuracy checks are the operator's too.
OPERATOR_TABLES = {"operators", "platform_audit", "scan_accuracy_checks"}
PLATFORM_DEFAULTS = {"platform_models", "platform_ai_settings", "feature_switches", "platform_model_override"}
# Changed on purpose (#606, 0091): the database rule's switch, the owner's only.
# Changed on purpose (#603, 0097): the WhatsApp numbers with no connection (no role reads them).
NO_CUSTOMER_ID = (
    {
        "customers",
        "alembic_version",
        "reader_status",
        "model_catalog",
        "model_rate_scheduler",
        "kaagaz_person_rule",
    }
    | {"whatsapp_unknown_numbers"}
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
        | set(FROM_0061)
        | set(FROM_0062)
        | set(FROM_0066)
        | set(FROM_0068)
        | set(FROM_0071)
        | set(FROM_0061D)
        | set(FROM_0076)
        | set(FROM_0081)  # Changed on purpose (#542, 0081)
        | set(FROM_0084)  # Changed on purpose (#590, 0084)
        | set(FROM_0088)  # Changed on purpose (#600, 0088)
        | set(FROM_0094)  # Changed on purpose (#617, 0094)
        | set(FROM_0097)  # Changed on purpose (#603, 0097)
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
        # Changed on purpose (#482, 0068): and a deleted organisation's kept audit rows.
        # Changed on purpose (#484, 0071): and an organisation's folder upload limits.
        # Changed on purpose (#516, 0061d): and an organisation's storage limit.
        if table not in (
            FROM_0023 | FROM_0026 | FROM_0039 | FROM_0040 | FROM_0043 | FROM_0068 | FROM_0071 | FROM_0061D
        ):
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
        | FROM_0061
        | FROM_0062
        | FROM_0066
        | FROM_0068
        | FROM_0071
        | FROM_0061D
        | FROM_0076
        | FROM_0081  # Changed on purpose (#542, 0081)
        | FROM_0084  # Changed on purpose (#590, 0084)
        | FROM_0088  # Changed on purpose (#600, 0088)
        | FROM_0094  # Changed on purpose (#617, 0094)
        | FROM_0097  # Changed on purpose (#603, 0097)
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
    own = (
        set(FROM_0008) | set(FROM_0009) | {"whatsapp_messages", "whatsapp_link_codes", "whatsapp_pickers"}
    )  # #603
    restrictive = ("ALL", "RESTRICTIVE", ["public"], OWN_USER, OWN_USER)
    # Changed on purpose (#606, 0091): the per-person rule, for the API role only, on documents,
    # folders, trashed folders and the 27 content tables (test_isolation_person_rule checks them).
    person = {key: value for key, value in policies.items() if key[1] == "person_only"}
    assert {table for table, _ in person} == PERSON_RULE_TABLES
    assert {(command, permissive, tuple(roles)) for command, permissive, roles, *_ in person.values()} == {
        ("ALL", "RESTRICTIVE", ("kaagaz_app",))
    }
    others = {key: value for key, value in policies.items() if key[1] != "person_only"}
    assert others == {(table, "own_user_only"): restrictive for table in own} | CATALOG_POLICIES


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


def test_the_app_may_update_only_when_an_email_link_was_used(db: Database) -> None:
    """0061: never a link's hash, purpose, user or expiry, so a link can't be moved or lengthened."""
    assert _updatable(db, "email_links") == {"used_at"}


def test_the_app_may_update_only_the_approval_and_last_use_of_a_connected_account(db: Database) -> None:
    """0062: never a connection's provider, issuer, subject, user or customer, so an approved
    connection can't be moved to another person or outside account."""
    assert {table: _updatable(db, table) for table in UPDATES_0062} == UPDATES_0062


def test_the_app_may_update_only_the_counts_of_a_reading_day(db: Database) -> None:
    """0066: never a counter's organisation or day, so pages can't be moved to another day."""
    assert {table: _updatable(db, table) for table in UPDATES_0066} == UPDATES_0066


def test_the_app_may_update_only_the_parent_of_a_trashed_folder(db: Database) -> None:
    """0082 (A13): never a trashed folder's name, kind, delete or owner; only where it would be
    restored to, when the folder it was in is deleted with its contents kept elsewhere."""
    assert _updatable(db, "trashed_folders") == {"parent_id"}


def test_the_app_may_update_only_the_head_tick_of_a_department_membership(db: Database) -> None:
    """0084 (#590): never a membership's department, person or who added it, so a row can't be
    moved to another department or person; nothing of the other access tables."""
    assert {table: _updatable(db, table) for table in FROM_0084} == {
        table: UPDATES_0084.get(table, set()) for table in FROM_0084
    }


def test_the_app_may_update_no_column_of_a_page_turn(db: Database) -> None:
    """0088 (#600): never a turn's page, version or who saved it; a save deletes and adds."""
    assert _updatable(db, "page_rotations") == set()


def test_the_app_may_update_only_the_listed_columns_of_whatsapps_tables(db: Database) -> None:
    """0097 (#603): never a connection's person, number, sign-in version or end, a code's number or
    hash, or a message's id, person, kind or times, so none can be moved or reopened."""
    assert {table: _updatable(db, table) for table in FROM_0097} == UPDATES_0097


def test_whatsapps_unknown_numbers_are_reached_by_no_role_only_by_their_functions(db: Database) -> None:
    """0097 (#603): no organisation, so row level security with no policy and no right for the app
    or the worker role; kaagaz_whatsapp_tell_unknown and kaagaz_whatsapp_block alone write it."""
    with db.system() as conn:
        row = conn.execute(
            text(
                "SELECT c.relrowsecurity, (SELECT count(*) FROM pg_policy p WHERE p.polrelid = c.oid),"
                " array_agg(r.rolname ORDER BY r.rolname) FILTER (WHERE"
                "  has_table_privilege(r.oid, c.oid, 'SELECT,INSERT,UPDATE,DELETE,TRUNCATE,REFERENCES')"
                "  OR has_any_column_privilege(r.oid, c.oid, 'SELECT,INSERT,UPDATE,REFERENCES'))"
                " FROM pg_class c CROSS JOIN pg_roles r"
                " WHERE c.oid = 'whatsapp_unknown_numbers'::regclass AND r.rolname IN (:a, 'kaagaz_worker')"
                " GROUP BY c.relrowsecurity, c.oid"
            ),
            {"a": APP_ROLE},
        ).one()
    assert tuple(row) == (True, 0, None)


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
    # Changed on purpose (#457 phase 1, 0062): the admin's auto-approval switch.
    # Changed on purpose (#507, 0074): the admin's Google rules; never google_signin (the operator's).
    assert {name for name, may_update in columns if may_update} == {
        "max_upload_mb",
        "autofile_on",
        "auto_approve_connections",
        "google_roles",
        "google_only_roles",
        "google_domains",
    }


def _columns(db: Database, table: str) -> set[str]:
    with db.system() as conn:
        rows = conn.execute(
            text(
                "SELECT attname::text FROM pg_attribute WHERE attrelid = CAST(:t AS regclass)"
                " AND attnum > 0 AND NOT attisdropped"
            ),
            {"t": table},
        ).scalars()
        return set(rows)


def test_a_folder_upload_keeps_counts_and_never_the_browsers_hash_of_a_file(db: Database) -> None:
    """0072 (#484, "Add new files only"): ``items_counted``, the files registered that weren't
    already in, is what the limit is checked against; no column keeps a file's SHA-256. Changed on
    purpose (#522 PR 3, 0080): ``zip_document_id``, the zip an upload the worker made is from."""
    assert _columns(db, "folder_uploads") == {
        "id", "customer_id", "user_id", "target", "parent_folder_id", "root_folder_id", "source_name",
        "root_name", "merge", "file_count", "total_bytes", "skipped_count", "folders_created",
        "items_registered", "items_counted", "bytes_registered", "created_at", "updated_at",
        "zip_document_id",
    }  # fmt: skip
    assert _columns(db, "folder_upload_items") == {
        "id", "customer_id", "folder_upload_id", "seq", "path_key", "name", "folder_id", "size_bytes",
        "batch_id", "state", "failure_code", "document_id", "updated_at",
    }  # fmt: skip


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
        | set(FROM_0061)
        | set(FROM_0062)
        | {"folder_upload_folders", "folder_upload_items"}  # 0071
        | set(FROM_0076)
        | set(FROM_0081)  # Changed on purpose (#542, 0081)
        | set(FROM_0084)  # Changed on purpose (#590, 0084)
        | set(FROM_0088)  # Changed on purpose (#600, 0088)
        | set(FROM_0097)
        - {"whatsapp_org_settings"}  # Changed on purpose (#603, 0097): it has no key but its own
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


# Changed on purpose (#601, 0090): the access and lock entries of an organisation, newest first.
ACCESS_INDEX = (
    "CREATE INDEX audit_log_access ON public.audit_log USING btree (customer_id, created_at DESC, id DESC)"
    " WHERE ((action ~~ 'access.%'::text) OR (action ~~ 'lock.%'::text))"
)


def test_the_access_entries_have_their_own_index(db: Database) -> None:
    with db.system() as conn:
        found = (
            conn.execute(
                text(
                    "SELECT indexdef FROM pg_indexes"
                    " WHERE schemaname = 'public' AND indexname = 'audit_log_access'"
                )
            )
            .scalars()
            .all()
        )
    assert found == [ACCESS_INDEX]


def test_only_the_listed_owner_rights_functions_exist_and_only_their_role_may_run_them(db: Database) -> None:
    """Found from the catalog, so a new one can't be left out: what it returns, its
    fixed search path, never runnable by PUBLIC, and by the app (OWNER_RIGHTS) or
    the operator (OPERATOR_RIGHTS), never both; a trigger's (TRIGGER_RIGHTS) by neither."""
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
    } == OWNER_RIGHTS | OPERATOR_RIGHTS | TRIGGER_RIGHTS
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
    assert sorted(tuple(row) for row in locked) == [
        ("operators", True, 0),
        ("platform_audit", True, 0),
        ("scan_accuracy_checks", True, 0),
    ]


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
