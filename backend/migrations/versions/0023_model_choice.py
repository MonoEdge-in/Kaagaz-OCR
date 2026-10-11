"""Model choice and AI spend (council 11 Oct 2026: brief section 7, the plan's FINAL
DECISIONS and the 0023 DDL spec; NEEDS VRUSHIT'S APPROVAL: three tables, two customer
columns and five more functions under the never-do 1 exception).

* ``model_catalog`` (MC1, MC3): the models a scan may be read with, from OpenRouter's live
  list. Platform data, no customer. Row level security with ``read_all`` (SELECT) and the
  refresh's own INSERT and UPDATE policies; the app's COLUMN grants are what limit its
  writes to the refresh columns. ``verified_*`` and ``tokens_per_page_*`` are written by
  ``verify-models --record`` (the owner), ``enabled_*`` only by
  ``kaagaz_op_set_model_enabled``. Rows are never deleted: a model OpenRouter stops
  listing gets ``eligible = false``.
* ``customer_models`` (MS1, MS2): the models the operator allowed an organisation, and its
  default scan model (one at most, a unique partial index). ``customer_only``; the app may
  only read it.
* ``customers.ai_budget_usd`` (SP4): the monthly budget, warn only. The app's UPDATE on
  ``customers`` is ``max_upload_mb`` only (0014), so only the operator function writes it.
* ``documents.read_model`` (MS4, MS5): the model an upload asked for, checked again by the
  worker (``kaagaz.db.models.resolve``). ``documents.read_requested_by`` (SP1): who the
  scan spend is billed to, backfilled from ``uploaded_by``; NULL is the system (the upgrade
  sweep). The backfill runs as the owner (0001 has no FORCE ROW LEVEL SECURITY), so it
  reaches every customer's rows, and moves each one's ``updated_at`` (the touch trigger;
  the app never reads it).
* ``model_usage`` (SP1): the ledger. One row per (piece of work, model, kind, cached), ids,
  codes and numbers only. ``customer_only``; the app may only read and insert, so the
  ledger is append-only for it. Its user, document and conversation keys are ``ON DELETE
  SET NULL (col)``: a deleted user or document keeps its spend, 0018's purge needs no
  change (its user delete nulls ``user_id``) and the customer's delete cascades the rows.
* The operator's functions (MC2, MS1, MS2, SP3, SP4): ``kaagaz_op_models``,
  ``kaagaz_op_set_model_enabled`` (step-up, as 0016's disable), ``kaagaz_op_org_models``,
  ``kaagaz_op_set_org_models`` and ``kaagaz_op_usage``. As 0016's: SECURITY DEFINER with an
  empty search path and schema-qualified names, revoked from PUBLIC, runnable by
  ``kaagaz_operator`` ONLY, and acting only for an enabled operator whose two-step secret
  is confirmed. None returns a user or a document column. Each write that changes
  something writes one ``platform_audit`` row; an organisation's models also write one row
  in its own ``audit_log`` (``{"by": "operator"}``), so its admins see it.

Outcomes. ``kaagaz_op_set_model_enabled``: step_up, missing, unchanged, not_eligible,
not_verified, in_use (still allowed to an organisation) or done. ``kaagaz_op_set_org_models``:
signed_out, missing, deleting, unavailable (a model not in the catalogue, not eligible or
not enabled), not_verified (not for this prompt version), default_not_allowed (the default
is not one of the models, or models without a default, or a default without models) or
done. A budget of 0 or less is refused by the column's CHECK, and a NULL ``p_enabled`` by
the function (both check_violation). Only done changes anything (a step-up still uses up
its step). The catalogue rows an organisation's list names are locked FOR SHARE, in id
order, before they are checked, and a disable locks its row FOR UPDATE before it looks for
organisations using it, so a model is never disabled while a list that names it commits
(not in the DDL spec: a race fix).

The app keeps its table-wide UPDATE on ``documents`` (0001), which covers ``read_model``
and ``read_requested_by``.

Lock order: ``customers``, then ``documents`` (ALTER, then the backfill), then the new
tables, so the migration queues behind the app instead of deadlocking with it
(migrations/env.py sets lock_timeout). The backfill reads ``documents`` once.

The downgrade drops the functions, the ledger, the organisations' models, the two
documents columns, the budget and the catalogue (all lost). 0018's
``kaagaz_finish_customer_delete`` is never changed, so there is nothing to restore.

Revision ID: 0023
Revises: 0022 (bulk queue)
"""

from alembic import op

revision = "0023"
down_revision = "0022"
branch_labels = None
depends_on = None

APP_ROLE = "kaagaz_app"
OPERATOR_ROLE = "kaagaz_operator"
MODEL_ID = "^[a-z0-9-]+/[A-Za-z0-9._-]+$"  # kaagaz.settings.MODEL_ID
SLUG = "[a-z0-9-]{1,64}"  # kaagaz.settings.PROVIDER_SLUG
EFFORT = "[a-z]{1,16}"
LOCK = "LOCK TABLE customers, documents IN ACCESS EXCLUSIVE MODE"
# The columns the catalogue refresh (the app's worker sweep and cli refresh-models) writes.
REFRESH = (
    "name",
    "price_in_usd_per_m",
    "price_out_usd_per_m",
    "providers",
    "efforts",
    "expires_on",
    "eligible",
    "seen_at",
)

# As 0016's (a migration can't import another): the operator check, the step-up, the rows.
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
_CUSTOMER_LOG = (
    "INSERT INTO public.audit_log (customer_id, actor_user_id, action, target_type, target_id, details)"
)


def _literal(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


FUNCTIONS = {
    # The catalogue for the operator's Models tab (MC1, MC3).
    "kaagaz_op_models(uuid)": f"""
    CREATE FUNCTION public.kaagaz_op_models(p_operator uuid)
    RETURNS TABLE (id text, name text, price_in_usd_per_m numeric, price_out_usd_per_m numeric,
                   providers text[], efforts text[], expires_on date, eligible boolean, seen_at timestamptz,
                   verified_prompt_version text, verified_at timestamptz,
                   tokens_per_page_in integer, tokens_per_page_out integer, enabled_at timestamptz)
    LANGUAGE sql STABLE SECURITY DEFINER SET search_path = '' AS $$
        SELECT m.id, m.name, m.price_in_usd_per_m, m.price_out_usd_per_m, m.providers, m.efforts, m.expires_on,
               m.eligible, m.seen_at, m.verified_prompt_version, m.verified_at,
               m.tokens_per_page_in, m.tokens_per_page_out, m.enabled_at
        FROM public.model_catalog m
        WHERE EXISTS (SELECT 1 FROM public.operators o WHERE {_ENABLED})
        ORDER BY m.name, m.id
    $$
    """,
    # Enable or disable one model on the platform (MC2), with a fresh code (step-up).
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
    # One organisation's models, default and budget. No row: no such customer, or the
    # operator is not enabled.
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
    # Replace one organisation's models, default and budget (MS1, MS2, SP4).
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
    # Spend per organisation and model in [p_from, p_to) (SP3): no user, no document.
    "kaagaz_op_usage(uuid, timestamptz, timestamptz)": f"""
    CREATE FUNCTION public.kaagaz_op_usage(p_operator uuid, p_from timestamptz, p_to timestamptz)
    RETURNS TABLE (customer_id uuid, customer_name text, model text, calls bigint, cached_calls bigint,
                   input_tokens numeric, output_tokens numeric, reasoning_tokens numeric,
                   cost_usd numeric, cost_estimated boolean)
    LANGUAGE sql STABLE SECURITY DEFINER SET search_path = '' AS $$
        SELECT u.customer_id, c.name, u.model,
               sum(u.calls), COALESCE(sum(u.calls) FILTER (WHERE u.cached), 0),
               sum(u.input_tokens), sum(u.output_tokens), sum(u.reasoning_tokens),
               sum(u.cost_usd), bool_or(u.cost_estimated)
        FROM public.model_usage u JOIN public.customers c ON c.id = u.customer_id
        WHERE u.created_at >= p_from AND u.created_at < p_to
          AND EXISTS (SELECT 1 FROM public.operators o WHERE {_ENABLED})
        GROUP BY u.customer_id, c.name, u.model
        ORDER BY c.name, u.model
    $$
    """,
}

FUNCTION_COMMENTS = {
    "kaagaz_op_models": "Operator: the model catalogue with prices, providers, verification and whether"
    " each model is enabled on the platform. Platform data only.",
    "kaagaz_op_set_model_enabled": "Operator, with a fresh code: enable a model on the platform (eligible and"
    " verified for the reader's prompt) or disable one no organisation still uses. Audited.",
    "kaagaz_op_org_models": "Operator: one organisation's allowed models, its default scan model and its"
    " monthly AI budget.",
    "kaagaz_op_set_org_models": "Operator: replace one organisation's allowed models (enabled and verified"
    " only), its default scan model and its budget. Audited in both logs.",
    "kaagaz_op_usage": "Operator: AI spend per organisation and model in a time range. Totals only, never a"
    " user or a document.",
}

CUSTOMER = "The customer the row belongs to (row level security filters on it)."
# {table: (table comment or None for a table that has one, {column: comment})}, as in 0021.
# Plain constants with no colon before a word, which SQLAlchemy would read as a bind parameter.
COMMENTS: dict[str, tuple[str | None, dict[str, str]]] = {
    "model_catalog": (
        "The models a scan may be read with, from OpenRouter's live list (refreshed daily)."
        " Platform data, no customer.",
        {
            "id": "The OpenRouter model ID, author/slug (never a variant after a colon).",
            "name": "OpenRouter's display name, clamped to 200 characters.",
            "price_in_usd_per_m": "Live price in US dollars per million input tokens.",
            "price_out_usd_per_m": "Live price in US dollars per million output tokens.",
            "providers": "Its zero-data-retention providers with structured output (base slugs), in"
            " OpenRouter's order. The ones in OPENROUTER_PROVIDERS may be used.",
            "efforts": "The reasoning efforts it accepts.",
            "expires_on": "OpenRouter's expiration date. NULL when none.",
            "eligible": "It passed every catalogue filter at the last refresh (image in, structured output,"
            " a ZDR endpoint of an approved processor).",
            "seen_at": "When a refresh last saw it.",
            "verified_prompt_version": "The reader prompt version verify-models --record passed with."
            " NULL, never verified.",
            "verified_at": "When verify-models --record passed. NULL, never verified.",
            "tokens_per_page_in": "Input tokens one page took in verify-models (for the per-page estimate)."
            " NULL until verified.",
            "tokens_per_page_out": "Output tokens one page took in verify-models (for the per-page estimate)."
            " NULL until verified.",
            "enabled_at": "When an operator enabled it on the platform. NULL, not enabled.",
            "enabled_by": "The operator (operators.id) who enabled it. NULL, not enabled.",
        },
    ),
    "customer_models": (
        "The models the platform operator allowed an organisation to read scans with. No rows, the"
        " platform's model (OCR_MODEL).",
        {
            "customer_id": CUSTOMER,
            "model": "An allowed model (model_catalog.id).",
            "is_default_scan": "The organisation's default scan model. Exactly one row per organisation"
            " that has any.",
            "created_at": "When the operator allowed it.",
        },
    ),
    "customers": (
        None,
        {
            "ai_budget_usd": "The organisation's monthly AI budget in US dollars, set by the operator. Warn"
            " only (at 80 and 100 percent). NULL, none.",
        },
    ),
    "documents": (
        None,
        {
            "read_model": "The model an upload asked to read its scans with (the read_model form field),"
            " checked against the organisation's allowed models on upload and again by the worker."
            " NULL, the organisation's default.",
            "read_requested_by": "Who asked for this document's latest reading (the uploader, or who asked"
            " to read it again), the user its scan spend is billed to. NULL, the system (the upgrade"
            " sweep).",
        },
    ),
    "model_usage": (
        "AI spend, one row per piece of work, model and cached (SP1). Ids, codes and numbers only,"
        " never content.",
        {
            "id": "Ledger row id, in order of writing.",
            "customer_id": CUSTOMER,
            "user_id": "Who it is billed to, the uploader or who asked to read again (scans), the asker"
            " (chat, memory, search, stories). NULL, no user asked (sweeps), or the user was removed.",
            "document_id": "What the work was about. NULL, none, or deleted (the spend stays).",
            "conversation_id": "The chat the work was for. NULL, none, or deleted (the spend stays).",
            "kind": "The kind of work, one of scan, upgrade, card, invoice, map, embed, chat, memory,"
            " search or story.",
            "model": "The OpenRouter model ID called.",
            "provider_name": "The provider OpenRouter named in the last reply. NULL, none named.",
            "cached": "Pages taken from the page cache, no call, cost 0.",
            "calls": "Calls (or cached pages) in this row.",
            "input_tokens": "Input tokens billed.",
            "output_tokens": "Output tokens billed, reasoning included.",
            "reasoning_tokens": "Reasoning tokens billed (also counted in output_tokens).",
            "cost_usd": "US dollars, the sum of OpenRouter's usage.cost, else the catalogue price times the"
            " tokens (cost_estimated). NULL, not known.",
            "cost_estimated": "cost_usd is the catalogue price times the tokens, not OpenRouter's billed cost.",
            "created_at": "When the work ended.",
        },
    ),
}
INDEX_COMMENTS = {
    "customer_models_default": "At most one default scan model per organisation.",
    "model_usage_period": "One organisation's spend over a period (the admin's Usage screen, the budget).",
    "model_usage_document": "The spend rows of one document, so its delete never scans the ledger.",
    "model_usage_conversation": "The spend rows of one chat, so its delete never scans the ledger.",
}
POLICY_COMMENTS = {
    ("read_all", "model_catalog"): "Every role that may read the catalogue sees every model (platform data).",
    (
        "refresh_insert",
        "model_catalog",
    ): "The refresh may add a model; the column grants limit what it writes.",
    (
        "refresh_update",
        "model_catalog",
    ): "The refresh may update a model; the column grants limit what it writes.",
    ("customer_only", "customer_models"): "A row is seen and written only by its own customer.",
    ("customer_only", "model_usage"): "A row is seen and written only by its own customer.",
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


_CUSTOMER_ONLY = """
    CREATE POLICY customer_only ON {table}
    USING (customer_id = kaagaz_current_customer())
    WITH CHECK (customer_id = kaagaz_current_customer())
"""

UPGRADE = [
    LOCK,
    # --- model_catalog: the platform catalogue (MC1, MC3) ---------------------------------
    f"""
    CREATE TABLE model_catalog (
        id text PRIMARY KEY CHECK (id ~ '{MODEL_ID}'),
        name text NOT NULL CHECK (length(name) BETWEEN 1 AND 200),
        price_in_usd_per_m numeric(12,6) NOT NULL CHECK (price_in_usd_per_m >= 0),
        price_out_usd_per_m numeric(12,6) NOT NULL CHECK (price_out_usd_per_m >= 0),
        providers text[] NOT NULL DEFAULT '{{}}'
            CHECK (cardinality(providers) <= 16 AND array_position(providers, NULL) IS NULL
                   AND array_to_string(providers, ',') ~ '^({SLUG}(,{SLUG})*)?$'),
        efforts text[] NOT NULL DEFAULT '{{}}'
            CHECK (cardinality(efforts) <= 16 AND array_position(efforts, NULL) IS NULL
                   AND array_to_string(efforts, ',') ~ '^({EFFORT}(,{EFFORT})*)?$'),
        expires_on date NULL,
        eligible boolean NOT NULL DEFAULT false,
        seen_at timestamptz NOT NULL DEFAULT now(),
        verified_prompt_version text NULL CHECK (verified_prompt_version ~ '^[a-z0-9]{{1,16}}$'),
        verified_at timestamptz NULL,
        tokens_per_page_in integer NULL CHECK (tokens_per_page_in >= 0),
        tokens_per_page_out integer NULL CHECK (tokens_per_page_out >= 0),
        enabled_at timestamptz NULL,
        enabled_by uuid NULL REFERENCES operators (id),
        CHECK ((verified_prompt_version IS NULL) = (verified_at IS NULL)),
        CHECK ((enabled_at IS NULL) = (enabled_by IS NULL))
    )
    """,
    f"GRANT SELECT ON model_catalog TO {APP_ROLE}",
    f"GRANT INSERT (id, {', '.join(REFRESH)}) ON model_catalog TO {APP_ROLE}",
    f"GRANT UPDATE ({', '.join(REFRESH)}) ON model_catalog TO {APP_ROLE}",
    "ALTER TABLE model_catalog ENABLE ROW LEVEL SECURITY",
    "CREATE POLICY read_all ON model_catalog FOR SELECT USING (true)",
    "CREATE POLICY refresh_insert ON model_catalog FOR INSERT WITH CHECK (true)",
    "CREATE POLICY refresh_update ON model_catalog FOR UPDATE USING (true) WITH CHECK (true)",
    # --- customers: the monthly budget (SP4) ------------------------------------------------
    "ALTER TABLE customers ADD COLUMN ai_budget_usd numeric(12,2) NULL CHECK (ai_budget_usd > 0)",
    # --- documents: the chosen model and who it is billed to (MS5, SP1) -----------------------
    """
    ALTER TABLE documents
        ADD COLUMN read_model text NULL REFERENCES model_catalog (id),
        ADD COLUMN read_requested_by uuid NULL,
        ADD CONSTRAINT documents_read_requested_by_fkey FOREIGN KEY (customer_id, read_requested_by)
            REFERENCES users (customer_id, id) ON DELETE SET NULL (read_requested_by)
    """,
    "UPDATE documents SET read_requested_by = uploaded_by WHERE uploaded_by IS NOT NULL",
    # --- customer_models: what the operator allowed each organisation (MS1, MS2) -------------
    """
    CREATE TABLE customer_models (
        customer_id uuid NOT NULL REFERENCES customers (id) ON DELETE CASCADE,
        model text NOT NULL REFERENCES model_catalog (id),
        is_default_scan boolean NOT NULL DEFAULT false,
        created_at timestamptz NOT NULL DEFAULT now(),
        PRIMARY KEY (customer_id, model)
    )
    """,
    "CREATE UNIQUE INDEX customer_models_default ON customer_models (customer_id) WHERE is_default_scan",
    f"GRANT SELECT ON customer_models TO {APP_ROLE}",
    "ALTER TABLE customer_models ENABLE ROW LEVEL SECURITY",
    _CUSTOMER_ONLY.format(table="customer_models"),
    # --- model_usage: the ledger (SP1) ------------------------------------------------------
    f"""
    CREATE TABLE model_usage (
        id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
        customer_id uuid NOT NULL REFERENCES customers (id) ON DELETE CASCADE,
        user_id uuid NULL,
        document_id uuid NULL,
        conversation_id uuid NULL,
        kind text NOT NULL CHECK (kind ~ '^[a-z_]{{1,32}}$'),
        model text NOT NULL CHECK (model ~ '{MODEL_ID}'),
        provider_name text NULL CHECK (provider_name ~ '^[A-Za-z0-9 ._-]{{1,64}}$'),
        cached boolean NOT NULL DEFAULT false,
        calls integer NOT NULL CHECK (calls >= 1),
        input_tokens bigint NOT NULL CHECK (input_tokens >= 0),
        output_tokens bigint NOT NULL CHECK (output_tokens >= 0),
        reasoning_tokens bigint NOT NULL CHECK (reasoning_tokens >= 0),
        cost_usd numeric(14,6) NULL CHECK (cost_usd >= 0),
        cost_estimated boolean NOT NULL DEFAULT false,
        created_at timestamptz NOT NULL DEFAULT now(),
        CHECK (NOT cost_estimated OR cost_usd IS NOT NULL),
        FOREIGN KEY (customer_id, user_id) REFERENCES users (customer_id, id) ON DELETE SET NULL (user_id),
        FOREIGN KEY (customer_id, document_id)
            REFERENCES documents (customer_id, id) ON DELETE SET NULL (document_id),
        FOREIGN KEY (customer_id, conversation_id)
            REFERENCES conversations (customer_id, id) ON DELETE SET NULL (conversation_id)
    )
    """,
    "CREATE INDEX model_usage_period ON model_usage (customer_id, created_at)",
    "CREATE INDEX model_usage_document ON model_usage (document_id) WHERE document_id IS NOT NULL",
    "CREATE INDEX model_usage_conversation ON model_usage (conversation_id) WHERE conversation_id IS NOT NULL",
    f"GRANT SELECT, INSERT ON model_usage TO {APP_ROLE}",
    "ALTER TABLE model_usage ENABLE ROW LEVEL SECURITY",
    _CUSTOMER_ONLY.format(table="model_usage"),
    # --- the operator's functions -----------------------------------------------------------
    *[
        statement
        for signature, create in FUNCTIONS.items()
        for statement in (
            create,
            f"COMMENT ON FUNCTION public.{signature} IS {_literal(FUNCTION_COMMENTS[signature.split('(')[0]])}",
            f"REVOKE ALL ON FUNCTION public.{signature} FROM PUBLIC",
            f"GRANT EXECUTE ON FUNCTION public.{signature} TO {OPERATOR_ROLE}",
        )
    ],
    *_comments(),
]

DOWNGRADE = [
    LOCK,
    *[f"DROP FUNCTION IF EXISTS public.{signature}" for signature in reversed(FUNCTIONS)],
    "DROP TABLE IF EXISTS model_usage",
    "DROP TABLE IF EXISTS customer_models",
    """
    ALTER TABLE documents
        DROP CONSTRAINT IF EXISTS documents_read_requested_by_fkey,
        DROP COLUMN IF EXISTS read_requested_by,
        DROP COLUMN IF EXISTS read_model
    """,
    "ALTER TABLE customers DROP COLUMN IF EXISTS ai_budget_usd",
    "DROP TABLE IF EXISTS model_catalog",
]


def upgrade() -> None:
    for statement in UPGRADE:
        op.execute(statement)


def downgrade() -> None:
    for statement in DOWNGRADE:
        op.execute(statement)
