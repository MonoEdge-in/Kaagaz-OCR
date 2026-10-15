"""Model permissions, Dynamic reading and Ensemble (features 25-27, council 15 Oct 2026, plan
section D1 with Tushar's overrides: the mode is "dynamic", no Vrushit gate, number 0026).

* ``platform_audit.details`` (S5d): what an operator's AI save changed, the budget too.
  Operator-only table (no app grant, no policy), so money never reaches an organisation.
* ``model_catalog``: ``chat_eligible`` (written by the catalogue refresh, so the app gets
  INSERT and UPDATE on it) and ``chat_verified_prompt``/``chat_verified_at`` (written by
  ``verify-models --record`` as the owner only): a model may answer only after it proved
  the answer schema for this ANSWER_PROMPT_HASH.
* ``customer_models``: ``scan``, ``chat`` and ``is_default_chat``, so one row allows a model
  for scanning, answering or both. Existing rows are scan rows (a 0023-era organisation
  reads with the same model).
* ``customer_ai_settings``: an organisation's own scan thinking level, answer levels and
  default level, Dynamic, and Ensemble. No row inherits every field; a NULL field inherits
  that field; Ensemble is never inherited (default off). ``customer_only``; SELECT only for
  the app. No ``updated_*``: ``platform_audit`` records who changed what (D3).
* ``platform_models`` and ``platform_ai_settings``: the platform defaults every
  organisation that has not been customised inherits, per area. No customer, ``read_all``
  for SELECT, no app write. The settings row is one row (``id`` is always true), seeded
  with today's behaviour (env thinking, answers at low, Dynamic off), so merging changes
  nothing for customers. No ``updated_*`` either (decided in the build, as D3: the app can
  read this table, so an operator id is kept out of it; ``platform_audit`` has it).
* ``documents.read_mode`` (single, dynamic or ensemble) and ``read_also`` (Ensemble's
  other readers, at most two, never the lead): checked by the API and again by the worker
  (``kaagaz.db.models.resolve_scan``). Constant defaults, so no table rewrite.
* ``extracted_values.votes`` (Ensemble's majority vote, Tushar 15 Oct 2026): what each
  chosen reader read at the value's spot and who agreed, saved with the value so the user
  sees why. NULL when one reader read it.
* The operator's functions. ``kaagaz_op_models`` gains the chat columns;
  ``kaagaz_op_set_model_enabled`` takes the answer prompt hash too and counts a model as in
  use while an organisation or the platform defaults name it; ``kaagaz_op_org_models`` and
  ``kaagaz_op_set_org_models`` are replaced by ``kaagaz_op_org_ai`` and
  ``kaagaz_op_set_org_ai``; ``kaagaz_op_ai_defaults`` and ``kaagaz_op_set_ai_defaults`` are
  new; ``kaagaz_op_daily`` counts the new ledger kinds (careful, ensemble_scan as document
  work; ensemble_chat as questions). As 0023's: SECURITY DEFINER, an empty search path and
  schema-qualified names, revoked from PUBLIC, runnable by ``kaagaz_operator`` only, acting
  only for an enabled operator whose two-step secret is confirmed. Both new writes need a
  fresh code (step-up, S3) and run one shared check (S4, ``_CHECK``) after locking the
  catalogue rows they name FOR SHARE in id order (the 0023 race fix); the defaults save
  locks the platform row FOR UPDATE first.

Outcomes of ``kaagaz_op_set_org_ai``: signed_out, step_up, missing, deleting, unavailable
(a model not in the catalogue, not enabled, or not eligible for that use), not_verified
(scan, for the reader's prompt version), chat_not_verified (for the answer prompt hash),
level_not_supported (a level that is not low, medium or high, or that an effective model
of the area does not accept), default_not_allowed (a default that is not one of its list,
or a list without a default, or levels without a default level), needs_two_models_scan
(Ensemble, or the organisation's own Dynamic switched on, with fewer than two scan models
in effect), needs_two_models_chat (Ensemble with an own answer list of one) or done.
``kaagaz_op_set_ai_defaults``: the same except missing, deleting and
needs_two_models_chat (Ensemble is never a platform default). Only done changes
anything (a step-up still uses up its step). The organisation's own ``audit_log`` row
holds no budget (D1); ``platform_audit.details`` does.

Lock order as 0023's: ``customers``, ``documents``, then the other tables the upgrade
alters, so it queues behind the app (migrations/env.py sets lock_timeout). The new CHECKs
on ``documents`` are added with their columns in that one locked transaction (decided in
the build, not D10's NOT VALID then VALIDATE: a VALIDATE after a commit leaves the schema
at 0026 with the revision at 0024 when it fails; one ALTER reads the table once).

Deploy window: the old API's operator console fails on the replaced functions between
``alembic upgrade`` and the API restart. Customer paths are unaffected.

The downgrade deletes the chat-only ``customer_models`` rows first, then drops what the
upgrade added and restores 0023's four functions and 0024's ``kaagaz_op_daily`` exactly.
Lost on downgrade: the platform defaults, chat rows, organisations' settings,
``read_mode``/``read_also``, ``votes``, ``chat_verified_*`` and audit details.

Revision ID: 0026
Revises: 0024 (0025, auto-filing, is on another branch; this is re-pointed when they merge)
"""

from alembic import op

revision = "0026"
down_revision = "0024"
branch_labels = None
depends_on = None

# Copied from 0023 and 0024 (a migration can't import another).
APP_ROLE = "kaagaz_app"
OPERATOR_ROLE = "kaagaz_operator"
LEDGER_DAYS = 30  # kaagaz.db.models.LEDGER_DAYS
MODEL_ID_BODY = "[a-z0-9][a-z0-9-]{0,127}/[A-Za-z0-9][A-Za-z0-9._-]{0,127}"  # MODEL_ID without ^ and $
# Every canonical (low, medium, high order) non-empty set of levels, as text[]::text.
LEVELS = "('{low}','{medium}','{high}','{low,medium}','{low,high}','{medium,high}','{low,medium,high}')"
LOCK = "LOCK TABLE customers, documents, extracted_values, model_catalog, customer_models IN ACCESS EXCLUSIVE MODE"
_ENABLED = "o.id = p_operator AND NOT o.disabled AND o.totp_enabled_at IS NOT NULL"
_WRITE = "OUT outcome text, OUT result_id uuid"
_HEAD = "LANGUAGE plpgsql SECURITY DEFINER SET search_path = '' AS $$"
_ACTOR = f"""
        PERFORM 1 FROM public.operators o WHERE {_ENABLED};
        IF NOT FOUND THEN
            outcome := 'signed_out';
            RETURN;
        END IF;
"""
_STEP_UP = f"""
        UPDATE public.operators AS o
        SET totp_last_step = p_step, step_up_failures = 0, step_up_locked_until = NULL
        WHERE {_ENABLED} AND p_step IS NOT NULL
          AND (o.totp_last_step IS NULL OR o.totp_last_step < p_step);
        IF NOT FOUND THEN
            outcome := 'step_up';
            RETURN;
        END IF;
"""
_PLATFORM = (
    "INSERT INTO public.platform_audit (operator_id, action, subject_customer_id, target_type, target_id)"
)
_PLATFORM_DETAILS = (
    "INSERT INTO public.platform_audit (operator_id, action, subject_customer_id, target_type, target_id,"
    " details)"
)
_CUSTOMER_LOG = (
    "INSERT INTO public.audit_log (customer_id, actor_user_id, action, target_type, target_id, details)"
)


def _literal(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


# --- the shared check of an AI save (S4) ----------------------------------------------------
# Needs v_scan, v_chat (sorted, distinct), v_levels (canonical, NULL: inherit) and the
# effective lists v_eff_scan, v_eff_chat, v_eff_levels, v_eff_thinking; p_scan_default,
# p_chat_default, p_chat_levels, p_chat_default_level, p_scan_thinking, p_scan_prompt and
# p_chat_prompt. A disable (FOR UPDATE) of one of the named models finishes first, or
# waits for this save.
_CHECK = """
        PERFORM 1 FROM public.model_catalog m WHERE m.id = ANY (v_scan || v_chat) ORDER BY m.id FOR SHARE;
        IF EXISTS (SELECT 1 FROM unnest(v_scan) AS w(model)
                   LEFT JOIN public.model_catalog m ON m.id = w.model
                   WHERE m.id IS NULL OR NOT m.eligible OR m.enabled_at IS NULL)
           OR EXISTS (SELECT 1 FROM unnest(v_chat) AS w(model)
                      LEFT JOIN public.model_catalog m ON m.id = w.model
                      WHERE m.id IS NULL OR NOT m.chat_eligible OR m.enabled_at IS NULL) THEN
            outcome := 'unavailable';
            RETURN;
        END IF;
        IF EXISTS (SELECT 1 FROM public.model_catalog m
                   WHERE m.id = ANY (v_scan) AND m.verified_prompt_version IS DISTINCT FROM p_scan_prompt) THEN
            outcome := 'not_verified';
            RETURN;
        END IF;
        IF EXISTS (SELECT 1 FROM public.model_catalog m
                   WHERE m.id = ANY (v_chat) AND m.chat_verified_prompt IS DISTINCT FROM p_chat_prompt) THEN
            outcome := 'chat_not_verified';
            RETURN;
        END IF;
        IF p_scan_thinking NOT IN ('low', 'medium', 'high')
           OR EXISTS (SELECT 1 FROM unnest(p_chat_levels) AS l(level)
                      WHERE l.level IS NULL OR l.level NOT IN ('low', 'medium', 'high'))
           OR cardinality(v_levels) = 0
           OR EXISTS (SELECT 1 FROM public.model_catalog m, unnest(v_eff_levels) AS l(level)
                      WHERE m.id = ANY (v_eff_chat) AND NOT l.level = ANY (m.efforts))
           OR EXISTS (SELECT 1 FROM public.model_catalog m
                      WHERE m.id = ANY (v_eff_scan) AND NOT v_eff_thinking = ANY (m.efforts)) THEN
            outcome := 'level_not_supported';
            RETURN;
        END IF;
        IF (cardinality(v_scan) = 0) <> (p_scan_default IS NULL)
           OR (p_scan_default IS NOT NULL AND NOT p_scan_default = ANY (v_scan))
           OR (cardinality(v_chat) = 0) <> (p_chat_default IS NULL)
           OR (p_chat_default IS NOT NULL AND NOT p_chat_default = ANY (v_chat))
           OR (v_levels IS NULL) <> (p_chat_default_level IS NULL)
           OR (p_chat_default_level IS NOT NULL AND NOT p_chat_default_level = ANY (v_levels)) THEN
            outcome := 'default_not_allowed';
            RETURN;
        END IF;
"""
# The lists as the function's DECLARE block makes them (the same in both writes).
_DECLARE_LISTS = """
        v_scan text[] := ARRAY(SELECT DISTINCT w.model FROM unnest(COALESCE(p_scan_models, '{}')) AS w(model)
                               ORDER BY 1);
        v_chat text[] := ARRAY(SELECT DISTINCT w.model FROM unnest(COALESCE(p_chat_models, '{}')) AS w(model)
                               ORDER BY 1);
        v_levels text[] := CASE WHEN p_chat_levels IS NOT NULL THEN
                               ARRAY(SELECT l.level FROM unnest(ARRAY['low', 'medium', 'high']) WITH ORDINALITY
                                     AS l(level, n) WHERE l.level = ANY (p_chat_levels) ORDER BY l.n) END;
"""
# Replace a list of models (the organisation's or the platform's) by v_scan and v_chat.
_ROWS = """
        SELECT {owner} w.model, w.model = ANY (v_scan), w.model = ANY (v_chat),
               COALESCE(w.model = p_scan_default, false), COALESCE(w.model = p_chat_default, false)
        FROM (SELECT DISTINCT unnest(v_scan || v_chat)) AS w(model);
"""
# The organisations that inherit each area now (not being deleted), for the defaults screen
# and the defaults save's audit (D8).
_INHERITING = """
        SELECT count(*) FILTER (WHERE NOT EXISTS (SELECT 1 FROM public.customer_models cm
                                                  WHERE cm.customer_id = c.id AND cm.scan)),
               count(*) FILTER (WHERE NOT EXISTS (SELECT 1 FROM public.customer_models cm
                                                  WHERE cm.customer_id = c.id AND cm.chat)),
               count(*) FILTER (WHERE s.scan_thinking IS NULL),
               count(*) FILTER (WHERE s.chat_levels IS NULL),
               count(*) FILTER (WHERE s.dynamic IS NULL)
        FROM public.customers c
        LEFT JOIN public.customer_ai_settings s ON s.customer_id = c.id
        WHERE c.status <> 'deleting'
"""
# The platform defaults as one JSON object (the defaults save's before and after).
_DEFAULTS_JSON = """
        SELECT jsonb_build_object(
                   'scan_models', COALESCE((SELECT jsonb_agg(x.model ORDER BY x.model)
                                            FROM public.platform_models x WHERE x.scan), '[]'),
                   'scan_default', (SELECT x.model FROM public.platform_models x WHERE x.is_default_scan),
                   'chat_models', COALESCE((SELECT jsonb_agg(x.model ORDER BY x.model)
                                            FROM public.platform_models x WHERE x.chat), '[]'),
                   'chat_default', (SELECT x.model FROM public.platform_models x WHERE x.is_default_chat),
                   'scan_thinking', p.scan_thinking, 'chat_levels', to_jsonb(p.chat_levels),
                   'chat_default_level', p.chat_default_level, 'dynamic', p.dynamic)
        FROM public.platform_ai_settings p
"""

FUNCTIONS = {
    # The catalogue for the operator's Models tab (MC1, MC3), with the chat columns (C11).
    "kaagaz_op_models(uuid)": f"""
    CREATE FUNCTION public.kaagaz_op_models(p_operator uuid)
    RETURNS TABLE (id text, name text, price_in_usd_per_m numeric, price_out_usd_per_m numeric,
                   providers text[], efforts text[], expires_on date, eligible boolean, seen_at timestamptz,
                   verified_prompt_version text, verified_at timestamptz,
                   tokens_per_page_in integer, tokens_per_page_out integer, enabled_at timestamptz,
                   ledger_tokens_in numeric, ledger_tokens_out numeric, chat_eligible boolean,
                   chat_verified_prompt text, chat_verified_at timestamptz)
    LANGUAGE sql STABLE SECURITY DEFINER SET search_path = '' AS $$
        WITH s AS (
            SELECT u.model, sum(u.input_tokens) / sum(u.calls) AS page_in,
                   sum(u.output_tokens) / sum(u.calls) AS page_out
            FROM public.model_usage u
            WHERE u.kind = 'scan' AND NOT u.cached AND u.created_at >= now() - make_interval(days => {LEDGER_DAYS})
            GROUP BY u.model
        )
        SELECT m.id, m.name, m.price_in_usd_per_m, m.price_out_usd_per_m, m.providers, m.efforts, m.expires_on,
               m.eligible, m.seen_at, m.verified_prompt_version, m.verified_at,
               m.tokens_per_page_in, m.tokens_per_page_out, m.enabled_at, s.page_in, s.page_out,
               m.chat_eligible, m.chat_verified_prompt, m.chat_verified_at
        FROM public.model_catalog m
        LEFT JOIN s ON s.model = m.id
        WHERE EXISTS (SELECT 1 FROM public.operators o WHERE {_ENABLED})
        ORDER BY m.name, m.id
    $$
    """,
    # Enable or disable one model on the platform, with a fresh code (step-up). Enabled:
    # eligible and verified for scanning, or chat-eligible and verified for answering.
    "kaagaz_op_set_model_enabled(uuid, text, boolean, text, text, bigint)": f"""
    CREATE FUNCTION public.kaagaz_op_set_model_enabled(
        p_operator uuid, p_model text, p_enabled boolean, p_scan_prompt text, p_chat_prompt text,
        p_step bigint, OUT outcome text
    )
    {_HEAD}
    DECLARE v_eligible boolean; v_scan_ok boolean; v_chat_ok boolean; v_enabled timestamptz;
    BEGIN
        IF p_enabled IS NULL THEN
            RAISE EXCEPTION 'enabled must be true or false' USING ERRCODE = 'check_violation';
        END IF;
        {_STEP_UP}
        SELECT m.eligible OR m.chat_eligible, m.eligible AND COALESCE(m.verified_prompt_version = p_scan_prompt, false),
               m.chat_eligible AND COALESCE(m.chat_verified_prompt = p_chat_prompt, false), m.enabled_at
        INTO v_eligible, v_scan_ok, v_chat_ok, v_enabled
        FROM public.model_catalog m WHERE m.id = p_model FOR UPDATE;
        IF NOT FOUND THEN
            outcome := 'missing';
            RETURN;
        END IF;
        IF p_enabled = (v_enabled IS NOT NULL) THEN
            outcome := 'unchanged';
            RETURN;
        END IF;
        IF p_enabled AND NOT v_eligible THEN
            outcome := 'not_eligible';
            RETURN;
        END IF;
        IF p_enabled AND NOT (v_scan_ok OR v_chat_ok) THEN
            outcome := 'not_verified';
            RETURN;
        END IF;
        -- Remove it from every organisation and from the platform defaults first.
        IF NOT p_enabled AND (EXISTS (SELECT 1 FROM public.customer_models cm WHERE cm.model = p_model)
                              OR EXISTS (SELECT 1 FROM public.platform_models pm WHERE pm.model = p_model)) THEN
            outcome := 'in_use';
            RETURN;
        END IF;
        UPDATE public.model_catalog AS m
        SET enabled_at = CASE WHEN p_enabled THEN now() END, enabled_by = CASE WHEN p_enabled THEN p_operator END
        WHERE m.id = p_model;
        INSERT INTO public.platform_audit (operator_id, action, target_type, target_id)
        VALUES (p_operator, CASE WHEN p_enabled THEN 'model.enabled' ELSE 'model.disabled' END, 'model',
                left(p_model, 64));
        outcome := 'done';
    END
    $$
    """,
    # One organisation's own AI settings and budget; an empty list or a NULL field inherits
    # the platform default. No row: no such customer, or the operator is not enabled.
    "kaagaz_op_org_ai(uuid, uuid)": f"""
    CREATE FUNCTION public.kaagaz_op_org_ai(p_operator uuid, p_customer uuid)
    RETURNS TABLE (scan_models text[], scan_default text, chat_models text[], chat_default text,
                   scan_thinking text, chat_levels text[], chat_default_level text, dynamic boolean,
                   ensemble boolean, ai_budget_usd numeric)
    LANGUAGE sql STABLE SECURITY DEFINER SET search_path = '' AS $$
        SELECT COALESCE(array_agg(cm.model ORDER BY cm.model) FILTER (WHERE cm.scan), '{{}}'),
               max(cm.model) FILTER (WHERE cm.is_default_scan),
               COALESCE(array_agg(cm.model ORDER BY cm.model) FILTER (WHERE cm.chat), '{{}}'),
               max(cm.model) FILTER (WHERE cm.is_default_chat),
               s.scan_thinking, s.chat_levels, s.chat_default_level, s.dynamic, COALESCE(s.ensemble, false),
               c.ai_budget_usd
        FROM public.customers c
        LEFT JOIN public.customer_models cm ON cm.customer_id = c.id
        LEFT JOIN public.customer_ai_settings s ON s.customer_id = c.id
        WHERE c.id = p_customer AND EXISTS (SELECT 1 FROM public.operators o WHERE {_ENABLED})
        GROUP BY c.id, c.ai_budget_usd, s.customer_id
    $$
    """,
    # Replace one organisation's AI settings and budget, with a fresh code (C10, S3, S4).
    "kaagaz_op_set_org_ai(uuid, uuid, text[], text, text[], text, text, text[], text, boolean, boolean,"
    " numeric, text, text, bigint)": f"""
    CREATE FUNCTION public.kaagaz_op_set_org_ai(
        p_operator uuid, p_customer uuid, p_scan_models text[], p_scan_default text, p_chat_models text[],
        p_chat_default text, p_scan_thinking text, p_chat_levels text[], p_chat_default_level text,
        p_dynamic boolean, p_ensemble boolean, p_budget numeric, p_scan_prompt text, p_chat_prompt text,
        p_step bigint, {_WRITE}
    )
    {_HEAD}
    DECLARE
        v_status text;
        {_DECLARE_LISTS}
        v_eff_scan text[];
        v_eff_chat text[];
        v_eff_levels text[];
        v_eff_thinking text;
        v_details jsonb;
    BEGIN
        IF p_ensemble IS NULL THEN
            RAISE EXCEPTION 'ensemble must be true or false' USING ERRCODE = 'check_violation';
        END IF;
        {_ACTOR}
        {_STEP_UP}
        SELECT c.status INTO v_status FROM public.customers c WHERE c.id = p_customer FOR UPDATE;
        IF NOT FOUND THEN
            outcome := 'missing';
            RETURN;
        END IF;
        result_id := p_customer;
        IF v_status = 'deleting' THEN
            outcome := 'deleting';
            RETURN;
        END IF;
        -- What will be in effect: the organisation's own, else the platform's.
        SELECT CASE WHEN cardinality(v_scan) > 0 THEN v_scan
                    ELSE ARRAY(SELECT x.model FROM public.platform_models x WHERE x.scan ORDER BY 1) END,
               CASE WHEN cardinality(v_chat) > 0 THEN v_chat
                    ELSE ARRAY(SELECT x.model FROM public.platform_models x WHERE x.chat ORDER BY 1) END,
               COALESCE(v_levels, (SELECT p.chat_levels FROM public.platform_ai_settings p)),
               COALESCE(p_scan_thinking, (SELECT p.scan_thinking FROM public.platform_ai_settings p))
        INTO v_eff_scan, v_eff_chat, v_eff_levels, v_eff_thinking;
        {_CHECK}
        IF (p_ensemble OR p_dynamic IS TRUE) AND cardinality(v_eff_scan) < 2 THEN
            outcome := 'needs_two_models_scan';
            RETURN;
        END IF;
        IF p_ensemble AND cardinality(v_chat) = 1 THEN
            outcome := 'needs_two_models_chat';
            RETURN;
        END IF;
        DELETE FROM public.customer_models cm WHERE cm.customer_id = p_customer;
        INSERT INTO public.customer_models (customer_id, model, scan, chat, is_default_scan, is_default_chat)
        {_ROWS.format(owner="p_customer,")}
        INSERT INTO public.customer_ai_settings AS s
            (customer_id, scan_thinking, chat_levels, chat_default_level, dynamic, ensemble)
        VALUES (p_customer, p_scan_thinking, v_levels, p_chat_default_level, p_dynamic, p_ensemble)
        ON CONFLICT (customer_id) DO UPDATE
        SET scan_thinking = EXCLUDED.scan_thinking, chat_levels = EXCLUDED.chat_levels,
            chat_default_level = EXCLUDED.chat_default_level, dynamic = EXCLUDED.dynamic,
            ensemble = EXCLUDED.ensemble;
        UPDATE public.customers AS c SET ai_budget_usd = p_budget WHERE c.id = p_customer;
        v_details := jsonb_build_object(
            'by', 'operator', 'scan_models', to_jsonb(v_scan), 'scan_default', p_scan_default,
            'chat_models', to_jsonb(v_chat), 'chat_default', p_chat_default, 'scan_thinking', p_scan_thinking,
            'chat_levels', to_jsonb(v_levels), 'chat_default_level', p_chat_default_level,
            'dynamic', p_dynamic, 'ensemble', p_ensemble);
        -- No budget in the organisation's own log (D1): money stays with the operator.
        {_CUSTOMER_LOG}
        VALUES (p_customer, NULL, 'org.ai_settings_set', 'customer', p_customer::text, v_details);
        {_PLATFORM_DETAILS}
        VALUES (p_operator, 'org.ai_settings_set', p_customer, 'customer', p_customer::text,
                v_details || jsonb_build_object('budget_usd', p_budget));
        outcome := 'done';
    END
    $$
    """,
    # The platform defaults and how many organisations inherit each area (D8).
    "kaagaz_op_ai_defaults(uuid)": f"""
    CREATE FUNCTION public.kaagaz_op_ai_defaults(p_operator uuid)
    RETURNS TABLE (scan_models text[], scan_default text, chat_models text[], chat_default text,
                   scan_thinking text, chat_levels text[], chat_default_level text, dynamic boolean,
                   orgs_inheriting_scan_models bigint, orgs_inheriting_chat_models bigint,
                   orgs_inheriting_scan_thinking bigint, orgs_inheriting_chat_levels bigint,
                   orgs_inheriting_dynamic bigint)
    LANGUAGE sql STABLE SECURITY DEFINER SET search_path = '' AS $$
        SELECT x.scan_list, x.scan_first, x.chat_list, x.chat_first, p.scan_thinking, p.chat_levels,
               p.chat_default_level, p.dynamic, n.*
        FROM public.platform_ai_settings p
        CROSS JOIN (
            SELECT COALESCE(array_agg(pm.model ORDER BY pm.model) FILTER (WHERE pm.scan), '{{}}') AS scan_list,
                   max(pm.model) FILTER (WHERE pm.is_default_scan) AS scan_first,
                   COALESCE(array_agg(pm.model ORDER BY pm.model) FILTER (WHERE pm.chat), '{{}}') AS chat_list,
                   max(pm.model) FILTER (WHERE pm.is_default_chat) AS chat_first
            FROM public.platform_models pm
        ) x
        CROSS JOIN ({_INHERITING}) n
        WHERE EXISTS (SELECT 1 FROM public.operators o WHERE {_ENABLED})
    $$
    """,
    # Replace the platform defaults, with a fresh code (C10, S3, S4, D8).
    "kaagaz_op_set_ai_defaults(uuid, text[], text, text[], text, text, text[], text, boolean, text, text,"
    " bigint)": f"""
    CREATE FUNCTION public.kaagaz_op_set_ai_defaults(
        p_operator uuid, p_scan_models text[], p_scan_default text, p_chat_models text[], p_chat_default text,
        p_scan_thinking text, p_chat_levels text[], p_chat_default_level text, p_dynamic boolean,
        p_scan_prompt text, p_chat_prompt text, p_step bigint, OUT outcome text
    )
    {_HEAD}
    DECLARE
        {_DECLARE_LISTS}
        v_eff_scan text[];
        v_eff_chat text[];
        v_eff_levels text[];
        v_eff_thinking text;
        v_before jsonb;
        v_after jsonb;
        v_scan_models bigint;
        v_chat_models bigint;
        v_scan_thinking bigint;
        v_chat_levels bigint;
        v_dynamic bigint;
    BEGIN
        IF p_dynamic IS NULL THEN
            RAISE EXCEPTION 'dynamic must be true or false' USING ERRCODE = 'check_violation';
        END IF;
        {_ACTOR}
        {_STEP_UP}
        PERFORM 1 FROM public.platform_ai_settings p FOR UPDATE;
        v_eff_scan := v_scan;
        v_eff_chat := v_chat;
        v_eff_levels := v_levels;
        v_eff_thinking := p_scan_thinking;
        IF p_chat_levels IS NULL THEN  -- the platform always has levels
            outcome := 'level_not_supported';
            RETURN;
        END IF;
        {_CHECK}
        IF p_dynamic AND cardinality(v_scan) < 2 THEN
            outcome := 'needs_two_models_scan';
            RETURN;
        END IF;
        {_DEFAULTS_JSON} INTO v_before;
        {_INHERITING} INTO v_scan_models, v_chat_models, v_scan_thinking, v_chat_levels, v_dynamic;
        DELETE FROM public.platform_models AS x WHERE true;
        INSERT INTO public.platform_models (model, scan, chat, is_default_scan, is_default_chat)
        {_ROWS.format(owner="")}
        UPDATE public.platform_ai_settings AS p
        SET scan_thinking = p_scan_thinking, chat_levels = v_levels, chat_default_level = p_chat_default_level,
            dynamic = p_dynamic;
        {_DEFAULTS_JSON} INTO v_after;
        {_PLATFORM_DETAILS}
        VALUES (p_operator, 'platform.ai_defaults_set', NULL, 'platform', NULL, jsonb_build_object(
            'before', v_before, 'after', v_after, 'orgs_changed', jsonb_build_object(
                'scan_models', CASE WHEN v_before -> 'scan_models' = v_after -> 'scan_models'
                                         AND v_before -> 'scan_default' = v_after -> 'scan_default'
                                    THEN 0 ELSE v_scan_models END,
                'chat_models', CASE WHEN v_before -> 'chat_models' = v_after -> 'chat_models'
                                         AND v_before -> 'chat_default' = v_after -> 'chat_default'
                                    THEN 0 ELSE v_chat_models END,
                'scan_thinking', CASE WHEN v_before -> 'scan_thinking' = v_after -> 'scan_thinking'
                                      THEN 0 ELSE v_scan_thinking END,
                'chat_levels', CASE WHEN v_before -> 'chat_levels' = v_after -> 'chat_levels'
                                         AND v_before -> 'chat_default_level' = v_after -> 'chat_default_level'
                                    THEN 0 ELSE v_chat_levels END,
                'dynamic', CASE WHEN v_before -> 'dynamic' = v_after -> 'dynamic' THEN 0 ELSE v_dynamic END)));
        outcome := 'done';
    END
    $$
    """,
}

FUNCTION_COMMENTS = {
    "kaagaz_op_models": "Operator: the model catalogue with prices, providers, scan and answer"
    " verification, whether each model is enabled on the platform, and the average tokens of a scanned"
    " page over the last 30 days of the ledger. Platform data and numbers only.",
    "kaagaz_op_set_model_enabled": "Operator, with a fresh code: enable a model on the platform (verified"
    " for the reader's prompt or for the answer prompt) or disable one no organisation and no platform"
    " default still uses. Audited.",
    "kaagaz_op_org_ai": "Operator: one organisation's own AI settings (scan and answer models and"
    " defaults, levels, Dynamic, Ensemble) and its monthly AI budget. Empty or NULL inherits the platform.",
    "kaagaz_op_set_org_ai": "Operator, with a fresh code: replace one organisation's AI settings and budget"
    " (enabled and verified models only). Audited in both logs; the organisation's log holds no budget.",
    "kaagaz_op_ai_defaults": "Operator: the platform AI defaults and how many organisations inherit each"
    " area.",
    "kaagaz_op_set_ai_defaults": "Operator, with a fresh code: replace the platform AI defaults (enabled and"
    " verified models only). Audited with the values before and after and the organisations changed.",
}


# --- kaagaz_op_daily (0024, F1), with the ledger kinds of work -----------------------------
# Copied from 0024 (its body exactly, the kinds apart): restored by the downgrade.
_READ_HEAD = "LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path = '' AS $$"
_COUNTED_RUN = "r.status NOT IN ('running', 'superseded')"  # a run that ended on its own
_BEGIN = f"""
    #variable_conflict use_column
    DECLARE v_lo timestamptz; v_hi timestamptz;
    BEGIN
        IF NOT EXISTS (SELECT 1 FROM public.operators o WHERE {_ENABLED}) THEN
            RETURN;
        END IF;
"""
_ZONE = """
        IF NOT EXISTS (SELECT 1 FROM pg_catalog.pg_timezone_names z WHERE z.name = p_zone) THEN
            RAISE EXCEPTION 'unknown time zone' USING ERRCODE = 'check_violation';
        END IF;
"""
_RANGE = f"""
        IF p_from IS NULL OR p_to IS NULL OR p_from > p_to OR p_to - p_from >= 366 THEN
            RAISE EXCEPTION 'a range runs forwards over at most 366 days' USING ERRCODE = 'check_violation';
        END IF;
        {_ZONE}
        v_lo := p_from::timestamp AT TIME ZONE p_zone;
        v_hi := (p_to + 1)::timestamp AT TIME ZONE p_zone;
"""


def _sums(alias: str, columns: str) -> str:
    return ",\n               ".join(
        f"coalesce(sum({alias}.{column}), 0)::bigint AS {column}" for column in columns.split()
    )


def _daily(document_kinds: str, question_kinds: str) -> str:
    """0024's ``kaagaz_op_daily``, CREATE OR REPLACE (same signature, grants and comment)."""
    return f"""
    CREATE OR REPLACE FUNCTION public.kaagaz_op_daily(p_operator uuid, p_from date, p_to date, p_zone text)
    RETURNS TABLE (customer_id uuid, day date, uploads bigint, sign_ins bigint, questions bigint,
                   corrections bigint, runs bigint, runs_failed bigint, pages bigint, scan_pages bigint,
                   cached_pages bigint, cost_usd numeric, cost_scan_usd numeric, cost_documents_usd numeric,
                   cost_questions_usd numeric, cost_estimated boolean, cost_part_unknown boolean)
    {_READ_HEAD}
    {_BEGIN}
        {_RANGE}
        RETURN QUERY
        SELECT c.id, x.day,
               {_sums("x", "uploads sign_ins questions corrections runs runs_failed pages scan_pages cached_pages")},
               sum(x.cost), sum(x.cost_scan), sum(x.cost_documents), sum(x.cost_questions),
               coalesce(bool_or(x.estimated), false), coalesce(bool_or(x.part_unknown), false)
        FROM public.customers c
        CROSS JOIN LATERAL (
            SELECT (a.created_at AT TIME ZONE p_zone)::date AS day,
                   count(*) FILTER (WHERE a.action IN ('document.upload', 'document.version')) AS uploads,
                   count(*) FILTER (WHERE a.action IN ('auth.login', 'auth.admin_login')) AS sign_ins,
                   count(*) FILTER (WHERE a.action = 'chat.ask') AS questions,
                   count(*) FILTER (WHERE a.action = 'review.resolve') AS corrections,
                   NULL::bigint AS runs, NULL::bigint AS runs_failed, NULL::bigint AS pages,
                   NULL::bigint AS scan_pages, NULL::bigint AS cached_pages,
                   NULL::numeric AS cost, NULL::numeric AS cost_scan, NULL::numeric AS cost_documents,
                   NULL::numeric AS cost_questions, NULL::boolean AS estimated, NULL::boolean AS part_unknown
            FROM public.audit_log a
            WHERE a.customer_id = c.id AND a.created_at >= v_lo AND a.created_at < v_hi
              AND a.action IN ('document.upload', 'document.version', 'auth.login', 'auth.admin_login',
                               'chat.ask', 'review.resolve')
            GROUP BY 1
            UNION ALL
            SELECT (r.started_at AT TIME ZONE p_zone)::date, NULL, NULL, NULL, NULL,
                   count(*), count(*) FILTER (WHERE r.status = 'failed'),
                   sum(r.pages_cells + r.pages_text_layer + r.pages_ocr + r.pages_blank),
                   NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL
            FROM public.processing_runs r
            WHERE r.customer_id = c.id AND r.started_at >= v_lo AND r.started_at < v_hi AND {_COUNTED_RUN}
            GROUP BY 1
            UNION ALL
            SELECT (u.created_at AT TIME ZONE p_zone)::date, NULL, NULL, NULL, NULL, NULL, NULL, NULL,
                   sum(u.calls) FILTER (WHERE u.kind = 'scan' AND NOT u.cached),
                   sum(u.calls) FILTER (WHERE u.kind = 'scan' AND u.cached),
                   sum(u.cost_usd), sum(u.cost_usd) FILTER (WHERE u.kind = 'scan'),
                   sum(u.cost_usd) FILTER (WHERE u.kind IN ({document_kinds})),
                   sum(u.cost_usd) FILTER (WHERE u.kind IN ({question_kinds})),
                   bool_or(u.cost_estimated), bool_or(u.cost_usd IS NULL)
            FROM public.model_usage u
            WHERE u.customer_id = c.id AND u.created_at >= v_lo AND u.created_at < v_hi
            GROUP BY 1
        ) x
        GROUP BY c.id, x.day
        ORDER BY c.id, x.day;
    END
    $$
    """


DOCUMENT_KINDS = "'scan', 'upgrade', 'card', 'invoice', 'map', 'embed', 'careful', 'ensemble_scan'"
QUESTION_KINDS = "'chat', 'memory', 'search', 'ensemble_chat'"
DOCUMENT_KINDS_0024 = "'scan', 'upgrade', 'card', 'invoice', 'map', 'embed'"
QUESTION_KINDS_0024 = "'chat', 'memory', 'search'"


# --- 0023's four functions, restored by the downgrade (copied exactly) ---------------------
OLD_FUNCTIONS = {
    "kaagaz_op_models(uuid)": f"""
    CREATE FUNCTION public.kaagaz_op_models(p_operator uuid)
    RETURNS TABLE (id text, name text, price_in_usd_per_m numeric, price_out_usd_per_m numeric,
                   providers text[], efforts text[], expires_on date, eligible boolean, seen_at timestamptz,
                   verified_prompt_version text, verified_at timestamptz,
                   tokens_per_page_in integer, tokens_per_page_out integer, enabled_at timestamptz,
                   ledger_tokens_in numeric, ledger_tokens_out numeric)
    LANGUAGE sql STABLE SECURITY DEFINER SET search_path = '' AS $$
        WITH s AS (
            SELECT u.model, sum(u.input_tokens) / sum(u.calls) AS page_in,
                   sum(u.output_tokens) / sum(u.calls) AS page_out
            FROM public.model_usage u
            WHERE u.kind = 'scan' AND NOT u.cached AND u.created_at >= now() - make_interval(days => {LEDGER_DAYS})
            GROUP BY u.model
        )
        SELECT m.id, m.name, m.price_in_usd_per_m, m.price_out_usd_per_m, m.providers, m.efforts, m.expires_on,
               m.eligible, m.seen_at, m.verified_prompt_version, m.verified_at,
               m.tokens_per_page_in, m.tokens_per_page_out, m.enabled_at, s.page_in, s.page_out
        FROM public.model_catalog m
        LEFT JOIN s ON s.model = m.id
        WHERE EXISTS (SELECT 1 FROM public.operators o WHERE {_ENABLED})
        ORDER BY m.name, m.id
    $$
    """,
    "kaagaz_op_set_model_enabled(uuid, text, boolean, text, bigint)": f"""
    CREATE FUNCTION public.kaagaz_op_set_model_enabled(
        p_operator uuid, p_model text, p_enabled boolean, p_prompt_version text, p_step bigint,
        OUT outcome text
    )
    {_HEAD}
    DECLARE v_eligible boolean; v_version text; v_enabled timestamptz;
    BEGIN
        IF p_enabled IS NULL THEN
            RAISE EXCEPTION 'enabled must be true or false' USING ERRCODE = 'check_violation';
        END IF;
        {_STEP_UP}
        SELECT m.eligible, m.verified_prompt_version, m.enabled_at INTO v_eligible, v_version, v_enabled
        FROM public.model_catalog m WHERE m.id = p_model FOR UPDATE;
        IF NOT FOUND THEN
            outcome := 'missing';
            RETURN;
        END IF;
        IF p_enabled = (v_enabled IS NOT NULL) THEN
            outcome := 'unchanged';
            RETURN;
        END IF;
        IF p_enabled AND NOT v_eligible THEN
            outcome := 'not_eligible';
            RETURN;
        END IF;
        IF p_enabled AND v_version IS DISTINCT FROM p_prompt_version THEN
            outcome := 'not_verified';
            RETURN;
        END IF;
        -- Remove it from every organisation first.
        IF NOT p_enabled AND EXISTS (SELECT 1 FROM public.customer_models cm WHERE cm.model = p_model) THEN
            outcome := 'in_use';
            RETURN;
        END IF;
        UPDATE public.model_catalog AS m
        SET enabled_at = CASE WHEN p_enabled THEN now() END, enabled_by = CASE WHEN p_enabled THEN p_operator END
        WHERE m.id = p_model;
        INSERT INTO public.platform_audit (operator_id, action, target_type, target_id)
        VALUES (p_operator, CASE WHEN p_enabled THEN 'model.enabled' ELSE 'model.disabled' END, 'model',
                left(p_model, 64));
        outcome := 'done';
    END
    $$
    """,
    "kaagaz_op_org_models(uuid, uuid)": f"""
    CREATE FUNCTION public.kaagaz_op_org_models(p_operator uuid, p_customer uuid)
    RETURNS TABLE (models text[], default_scan text, ai_budget_usd numeric)
    LANGUAGE sql STABLE SECURITY DEFINER SET search_path = '' AS $$
        SELECT COALESCE(array_agg(cm.model ORDER BY cm.model) FILTER (WHERE cm.model IS NOT NULL), '{{}}'),
               max(cm.model) FILTER (WHERE cm.is_default_scan),
               c.ai_budget_usd
        FROM public.customers c
        LEFT JOIN public.customer_models cm ON cm.customer_id = c.id
        WHERE c.id = p_customer AND EXISTS (SELECT 1 FROM public.operators o WHERE {_ENABLED})
        GROUP BY c.id, c.ai_budget_usd
    $$
    """,
    "kaagaz_op_set_org_models(uuid, uuid, text[], text, numeric, text)": f"""
    CREATE FUNCTION public.kaagaz_op_set_org_models(
        p_operator uuid, p_customer uuid, p_models text[], p_default text, p_budget numeric,
        p_prompt_version text, {_WRITE}
    )
    {_HEAD}
    DECLARE
        v_status text;
        v_models text[] := ARRAY(SELECT DISTINCT w.model FROM unnest(COALESCE(p_models, '{{}}')) AS w(model)
                                 ORDER BY 1);
    BEGIN
        {_ACTOR}
        SELECT c.status INTO v_status FROM public.customers c WHERE c.id = p_customer FOR UPDATE;
        IF NOT FOUND THEN
            outcome := 'missing';
            RETURN;
        END IF;
        result_id := p_customer;
        IF v_status = 'deleting' THEN
            outcome := 'deleting';
            RETURN;
        END IF;
        -- A disable (FOR UPDATE) of one of them finishes first, or waits for this list.
        PERFORM 1 FROM public.model_catalog m WHERE m.id = ANY (v_models) ORDER BY m.id FOR SHARE;
        IF EXISTS (SELECT 1 FROM unnest(v_models) AS w(model)
                   LEFT JOIN public.model_catalog m ON m.id = w.model
                   WHERE m.id IS NULL OR NOT m.eligible OR m.enabled_at IS NULL) THEN
            outcome := 'unavailable';
            RETURN;
        END IF;
        IF EXISTS (SELECT 1 FROM public.model_catalog m
                   WHERE m.id = ANY (v_models) AND m.verified_prompt_version IS DISTINCT FROM p_prompt_version) THEN
            outcome := 'not_verified';
            RETURN;
        END IF;
        IF (cardinality(v_models) = 0) <> (p_default IS NULL)
           OR (p_default IS NOT NULL AND NOT p_default = ANY (v_models)) THEN
            outcome := 'default_not_allowed';
            RETURN;
        END IF;
        DELETE FROM public.customer_models cm WHERE cm.customer_id = p_customer;
        INSERT INTO public.customer_models (customer_id, model, is_default_scan)
        SELECT p_customer, w.model, w.model = p_default FROM unnest(v_models) AS w(model);
        UPDATE public.customers AS c SET ai_budget_usd = p_budget WHERE c.id = p_customer;
        {_CUSTOMER_LOG}
        VALUES (p_customer, NULL, 'org.models_set', 'customer', p_customer::text,
                jsonb_build_object('by', 'operator', 'models', to_jsonb(v_models), 'default', p_default,
                                   'budget_usd', p_budget));
        {_PLATFORM} VALUES (p_operator, 'org.models_set', p_customer, 'customer', p_customer::text);
        outcome := 'done';
    END
    $$
    """,
}
OLD_FUNCTION_COMMENTS = {
    "kaagaz_op_models": "Operator: the model catalogue with prices, providers, verification, whether"
    " each model is enabled on the platform, and the average tokens of a scanned page over the last"
    " 30 days of the ledger. Platform data and numbers only.",
    "kaagaz_op_set_model_enabled": "Operator, with a fresh code: enable a model on the platform (eligible and"
    " verified for the reader's prompt) or disable one no organisation still uses. Audited.",
    "kaagaz_op_org_models": "Operator: one organisation's allowed models, its default scan model and its"
    " monthly AI budget.",
    "kaagaz_op_set_org_models": "Operator: replace one organisation's allowed models (enabled and verified"
    " only), its default scan model and its budget. Audited in both logs.",
}


# --- comments (0007's rule: every table and column; 0023's: every function, index, policy) --
CUSTOMER = "The customer the row belongs to (row level security filters on it)."
LEVEL = "A thinking level, low, medium or high."
# {table: (table comment or None for one that keeps its own, {column: comment})}.
# Plain constants with no colon before a word, which SQLAlchemy would read as a bind parameter.
COMMENTS: dict[str, tuple[str | None, dict[str, str]]] = {
    "platform_audit": (
        None,
        {
            "details": "What an AI settings save changed (features 25-27), the budget included. NULL for"
            " every other action. Operator only.",
        },
    ),
    "model_catalog": (
        None,
        {
            "chat_eligible": "It passed every answer filter at the last refresh (text in and out,"
            " structured output, a ZDR endpoint of an approved provider, an approved vendor, low thinking).",
            "chat_verified_prompt": "The answer prompt hash (ANSWER_PROMPT_HASH) verify-models --record"
            " passed with. NULL, never verified for answering.",
            "chat_verified_at": "When verify-models --record passed for answering. NULL, never.",
        },
    ),
    "customer_models": (
        "The models the platform operator allowed an organisation to read scans and draft answers with."
        " No scan rows, the platform defaults' scan models; no chat rows, the platform defaults' answer"
        " models.",
        {
            "scan": "Allowed for reading scans.",
            "chat": "Allowed for drafting answers.",
            "is_default_chat": "The organisation's default answer model. At most one row per organisation.",
        },
    ),
    "customer_ai_settings": (
        "An organisation's own AI settings, set by the platform operator. No row, or a NULL field,"
        " inherits the platform defaults (platform_ai_settings).",
        {
            "customer_id": CUSTOMER,
            "scan_thinking": "The thinking level scans are read at. NULL, the platform default.",
            "chat_levels": "The thinking levels its people may answer at, in low, medium, high order. NULL,"
            " the platform default.",
            "chat_default_level": "The level an answer is drafted at when none is chosen; one of chat_levels."
            " NULL with chat_levels.",
            "dynamic": "Dynamic reading allowed (a careful re-read of unclear pages). NULL, the platform"
            " default.",
            "ensemble": "More than one model may read a document or draft an answer. Never inherited.",
        },
    ),
    "platform_models": (
        "The platform defaults' models, for every organisation without its own models of that use."
        " Platform data, no customer.",
        {
            "model": "A model (model_catalog.id).",
            "scan": "A default model for reading scans.",
            "chat": "A default model for drafting answers.",
            "is_default_scan": "The default scan model. At most one row.",
            "is_default_chat": "The default answer model. At most one row.",
        },
    ),
    "platform_ai_settings": (
        "The platform AI defaults every organisation inherits field by field. One row. Platform data,"
        " no customer.",
        {
            "id": "Always true, so the table holds one row.",
            "scan_thinking": "The thinking level scans are read at. NULL, OCR_THINKING_LEVEL.",
            "chat_levels": "The thinking levels people may answer at, in low, medium, high order.",
            "chat_default_level": "The level an answer is drafted at when none is chosen; one of chat_levels.",
            "dynamic": "Dynamic reading allowed for organisations that inherit it.",
        },
    ),
    "documents": (
        None,
        {
            "read_mode": "How the document is read, single, dynamic (unclear pages read again with more"
            " care) or ensemble (more than one model reads it and votes). Checked again by the worker.",
            "read_also": "Ensemble's other models (model_catalog ids, at most two, never read_model)."
            " Empty for single and dynamic.",
        },
    ),
    "extracted_values": (
        None,
        {
            "votes": "Ensemble's vote on this value, what each chosen reader read at its spot and how"
            " many agreed, shown to the user. NULL when one reader read it.",
        },
    ),
    "model_usage": (
        None,
        {
            "kind": "The kind of work, one of scan, upgrade, card, invoice, map, embed, chat, memory,"
            " search, story, careful (Dynamic's re-reads), ensemble_scan (Ensemble's other readers) or"
            " ensemble_chat (Ensemble's other answer drafts).",
        },
    ),
}
# The comments the upgrade replaces, as 0023 wrote them: restored by the downgrade.
OLD_COMMENTS = [
    "COMMENT ON TABLE customer_models IS "
    + _literal(
        "The models the platform operator allowed an organisation to read scans with. No rows, the"
        " platform's model (OCR_MODEL)."
    ),
    "COMMENT ON COLUMN model_usage.kind IS "
    + _literal(
        "The kind of work, one of scan, upgrade, card, invoice, map, embed, chat, memory, search or story."
    ),
]
INDEX_COMMENTS = {
    "ai_customer_models_one_default_chat": "At most one default answer model per organisation.",
    "ai_platform_models_one_default_scan": "At most one default scan model on the platform.",
    "ai_platform_models_one_default_chat": "At most one default answer model on the platform.",
}
POLICY_COMMENTS = {
    ("customer_only", "customer_ai_settings"): "A row is seen and written only by its own customer.",
    ("read_all", "platform_models"): "Every role that may read the platform defaults sees them.",
    ("read_all", "platform_ai_settings"): "Every role that may read the platform defaults sees them.",
}


def _comments() -> list[str]:
    statements = []
    for table, (table_comment, columns) in COMMENTS.items():
        if table_comment is not None:
            statements.append(f"COMMENT ON TABLE {table} IS {_literal(table_comment)}")
        statements.extend(
            f"COMMENT ON COLUMN {table}.{column} IS {_literal(comment)}"
            for column, comment in columns.items()
        )
    statements.extend(
        f"COMMENT ON INDEX {index} IS {_literal(comment)}" for index, comment in INDEX_COMMENTS.items()
    )
    statements.extend(
        f"COMMENT ON POLICY {policy} ON {table} IS {_literal(comment)}"
        for (policy, table), comment in POLICY_COMMENTS.items()
    )
    return statements


def _create(functions: dict[str, str], comments: dict[str, str]) -> list[str]:
    return [
        statement
        for signature, create in functions.items()
        for statement in (
            create,
            f"COMMENT ON FUNCTION public.{signature} IS {_literal(comments[signature.split('(')[0]])}",
            f"REVOKE ALL ON FUNCTION public.{signature} FROM PUBLIC",
            f"GRANT EXECUTE ON FUNCTION public.{signature} TO {OPERATOR_ROLE}",
        )
    ]


UPGRADE = [
    LOCK,
    *[f"DROP FUNCTION public.{signature}" for signature in OLD_FUNCTIONS],
    # --- platform_audit: what an AI save changed (S5d) ---------------------------------------
    """
    ALTER TABLE platform_audit ADD COLUMN details jsonb NULL
        CONSTRAINT ai_platform_audit_details_object CHECK (jsonb_typeof(details) = 'object')
    """,
    # --- model_catalog: answering (C11) --------------------------------------------------------
    """
    ALTER TABLE model_catalog
        ADD COLUMN chat_eligible boolean NOT NULL DEFAULT false,
        ADD COLUMN chat_verified_prompt text NULL
            CONSTRAINT ai_model_catalog_chat_prompt_hash CHECK (chat_verified_prompt ~ '^[0-9a-f]{64}$'),
        ADD COLUMN chat_verified_at timestamptz NULL,
        ADD CONSTRAINT ai_model_catalog_chat_verified_pair
            CHECK ((chat_verified_prompt IS NULL) = (chat_verified_at IS NULL))
    """,
    f"GRANT INSERT (chat_eligible), UPDATE (chat_eligible) ON model_catalog TO {APP_ROLE}",
    # --- customer_models: scan and/or chat (existing rows are scan rows) ------------------------
    """
    ALTER TABLE customer_models
        ADD COLUMN scan boolean NOT NULL DEFAULT true,
        ADD COLUMN chat boolean NOT NULL DEFAULT false,
        ADD COLUMN is_default_chat boolean NOT NULL DEFAULT false,
        ADD CONSTRAINT ai_customer_models_some_use CHECK (scan OR chat),
        ADD CONSTRAINT ai_customer_models_default_scan_use CHECK (NOT is_default_scan OR scan),
        ADD CONSTRAINT ai_customer_models_default_chat_use CHECK (NOT is_default_chat OR chat)
    """,
    "CREATE UNIQUE INDEX ai_customer_models_one_default_chat ON customer_models (customer_id)"
    " WHERE is_default_chat",
    # --- customer_ai_settings: an organisation's own (no row: inherit everything) ---------------
    f"""
    CREATE TABLE customer_ai_settings (
        customer_id uuid PRIMARY KEY REFERENCES customers (id) ON DELETE CASCADE,
        scan_thinking text NULL CHECK (scan_thinking IN ('low', 'medium', 'high')),
        chat_levels text[] NULL CHECK (chat_levels::text IN {LEVELS}),
        chat_default_level text NULL CHECK (chat_default_level IN ('low', 'medium', 'high')),
        dynamic boolean NULL,
        ensemble boolean NOT NULL DEFAULT false,
        CONSTRAINT ai_customer_settings_levels_pair CHECK ((chat_levels IS NULL) = (chat_default_level IS NULL)),
        CONSTRAINT ai_customer_settings_default_in_levels
            CHECK (chat_default_level IS NULL OR chat_default_level = ANY (chat_levels))
    )
    """,
    f"GRANT SELECT ON customer_ai_settings TO {APP_ROLE}",
    "ALTER TABLE customer_ai_settings ENABLE ROW LEVEL SECURITY",
    """
    CREATE POLICY customer_only ON customer_ai_settings
    USING (customer_id = kaagaz_current_customer())
    WITH CHECK (customer_id = kaagaz_current_customer())
    """,
    # --- the platform defaults (no customer; read by every role that may, written by none) -----
    """
    CREATE TABLE platform_models (
        model text PRIMARY KEY REFERENCES model_catalog (id),
        scan boolean NOT NULL DEFAULT true,
        chat boolean NOT NULL DEFAULT false,
        is_default_scan boolean NOT NULL DEFAULT false,
        is_default_chat boolean NOT NULL DEFAULT false,
        CONSTRAINT ai_platform_models_some_use CHECK (scan OR chat),
        CONSTRAINT ai_platform_models_default_scan_use CHECK (NOT is_default_scan OR scan),
        CONSTRAINT ai_platform_models_default_chat_use CHECK (NOT is_default_chat OR chat)
    )
    """,
    "CREATE UNIQUE INDEX ai_platform_models_one_default_scan ON platform_models ((true)) WHERE is_default_scan",
    "CREATE UNIQUE INDEX ai_platform_models_one_default_chat ON platform_models ((true)) WHERE is_default_chat",
    f"""
    CREATE TABLE platform_ai_settings (
        id boolean PRIMARY KEY DEFAULT true CONSTRAINT ai_platform_settings_one_row CHECK (id),
        scan_thinking text NULL CHECK (scan_thinking IN ('low', 'medium', 'high')),
        chat_levels text[] NOT NULL DEFAULT '{{low}}' CHECK (chat_levels::text IN {LEVELS}),
        chat_default_level text NOT NULL DEFAULT 'low' CHECK (chat_default_level IN ('low', 'medium', 'high')),
        dynamic boolean NOT NULL DEFAULT false,
        CONSTRAINT ai_platform_settings_default_in_levels CHECK (chat_default_level = ANY (chat_levels))
    )
    """,
    "INSERT INTO platform_ai_settings DEFAULT VALUES",
    f"GRANT SELECT ON platform_models, platform_ai_settings TO {APP_ROLE}",
    "ALTER TABLE platform_models ENABLE ROW LEVEL SECURITY",
    "ALTER TABLE platform_ai_settings ENABLE ROW LEVEL SECURITY",
    "CREATE POLICY read_all ON platform_models FOR SELECT USING (true)",
    "CREATE POLICY read_all ON platform_ai_settings FOR SELECT USING (true)",
    # --- documents: the reading mode and Ensemble's other readers ------------------------------
    f"""
    ALTER TABLE documents
        ADD COLUMN read_mode text NOT NULL DEFAULT 'single',
        ADD COLUMN read_also text[] NOT NULL DEFAULT '{{}}',
        ADD CONSTRAINT ai_documents_read_mode CHECK (read_mode IN ('single', 'dynamic', 'ensemble')),
        ADD CONSTRAINT ai_documents_read_also_shape CHECK (
            cardinality(read_also) <= 2 AND array_position(read_also, NULL) IS NULL
            AND array_to_string(read_also, ',') ~ '^({MODEL_ID_BODY}(,{MODEL_ID_BODY})*)?$'
            AND (cardinality(read_also) < 2 OR read_also[1] <> read_also[2])),
        ADD CONSTRAINT ai_documents_read_mode_models CHECK (
            (read_mode IN ('single', 'dynamic') AND cardinality(read_also) = 0)
            OR (read_mode = 'ensemble' AND cardinality(read_also) BETWEEN 1 AND 2
                AND read_model IS NOT NULL AND NOT read_model = ANY (read_also)))
    """,
    # --- extracted_values: Ensemble's vote, saved with the value -------------------------------
    """
    ALTER TABLE extracted_values ADD COLUMN votes jsonb NULL
        CONSTRAINT ai_extracted_values_votes_object CHECK (jsonb_typeof(votes) = 'object')
    """,
    # --- the operator's functions ---------------------------------------------------------------
    *_create(FUNCTIONS, FUNCTION_COMMENTS),
    _daily(DOCUMENT_KINDS, QUESTION_KINDS),
    *_comments(),
]

DOWNGRADE = [
    LOCK,
    *[f"DROP FUNCTION IF EXISTS public.{signature}" for signature in reversed(FUNCTIONS)],
    _daily(DOCUMENT_KINDS_0024, QUESTION_KINDS_0024),
    "DELETE FROM customer_models WHERE NOT scan",  # chat-only rows have no 0023 meaning
    "DROP TABLE IF EXISTS platform_ai_settings, platform_models, customer_ai_settings",
    "DROP INDEX IF EXISTS ai_customer_models_one_default_chat",
    """
    ALTER TABLE customer_models
        DROP CONSTRAINT IF EXISTS ai_customer_models_default_chat_use,
        DROP CONSTRAINT IF EXISTS ai_customer_models_default_scan_use,
        DROP CONSTRAINT IF EXISTS ai_customer_models_some_use,
        DROP COLUMN IF EXISTS is_default_chat,
        DROP COLUMN IF EXISTS chat,
        DROP COLUMN IF EXISTS scan
    """,
    "ALTER TABLE extracted_values DROP COLUMN IF EXISTS votes",
    """
    ALTER TABLE documents
        DROP CONSTRAINT IF EXISTS ai_documents_read_mode_models,
        DROP CONSTRAINT IF EXISTS ai_documents_read_also_shape,
        DROP CONSTRAINT IF EXISTS ai_documents_read_mode,
        DROP COLUMN IF EXISTS read_also,
        DROP COLUMN IF EXISTS read_mode
    """,
    """
    ALTER TABLE model_catalog
        DROP CONSTRAINT IF EXISTS ai_model_catalog_chat_verified_pair,
        DROP COLUMN IF EXISTS chat_verified_at,
        DROP COLUMN IF EXISTS chat_verified_prompt,
        DROP COLUMN IF EXISTS chat_eligible
    """,
    "ALTER TABLE platform_audit DROP COLUMN IF EXISTS details",
    *_create(OLD_FUNCTIONS, OLD_FUNCTION_COMMENTS),
    *OLD_COMMENTS,
]


def upgrade() -> None:
    for statement in UPGRADE:
        op.execute(statement)


def downgrade() -> None:
    for statement in DOWNGRADE:
        op.execute(statement)
