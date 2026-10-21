"""AI settings by task, the platform feature switches and the platform test model (Tushar,
17 Oct 2026; spec Outside-Folder/prd/ai-task-settings-spec.md). NEEDS VRUSHIT'S LATER REVIEW
(15 Oct decision: no wait).

* ``ai_task_settings``: an organisation's default (``user_id`` NULL) or a member's own choice
  of how much effort the AI puts into a task (scan, ask: none/low/high/max; ensemble,
  autofile: off/on), and ``locked`` (organisation rows only: members may not change it).
  One row per organisation and task and per member and task (two partial unique indexes,
  NULL user = the organisation). ``customer_only`` policy; the app reads and writes it (the
  API checks the role, the lock and the ceiling). Seeded with nothing: no row is today's
  behaviour.
* ``feature_switches``: the super admin turns one product feature off for every
  organisation. No row (or ``enabled``) is on. The app may read ``feature`` and ``enabled``
  only (a column grant, so no operator id reaches it); only ``kaagaz_op_set_feature_switch``
  writes, with a fresh code (step-up), and one ``platform_audit`` row for each change.
* ``platform_model_override`` (one row at most, id always true): the platform test model.
  While a row is there every organisation's scan list (``scan_model``) and answer list
  (``chat_model``) is that one model (``kaagaz.db.models``' area query); deleting it puts
  every organisation back as it was (nothing else is rewritten). The app may read the two
  model columns only; ``kaagaz_op_set_test_model`` (start or replace, the model checked as
  0026's saves check one) and ``kaagaz_op_clear_test_model`` (undo) write it, each with a
  fresh code and a ``platform_audit`` row.

The operator's functions follow 0026's rules: SECURITY DEFINER, an empty search path,
schema-qualified names, revoked from PUBLIC, runnable by ``kaagaz_operator`` only, acting
only for an enabled operator whose two-step secret is confirmed.
``kaagaz_op_feature_switches`` lists every feature with how many organisations (not being
deleted) use it now; ``kaagaz_op_test_model`` the override with what ran on it since it
started (pages and questions from the spend ledger, the cost OpenRouter reported only, and
the reading seconds a page of the ready runs read with it).

New tables only: no lock on an existing table. The downgrade drops the functions and the
three tables (the settings, switches and override are lost; nothing else changes).

Revision ID: 0038
Revises: 0037 (any number of scanning models)
"""

from alembic import op

revision = "0038"
down_revision = "0037"
branch_labels = None
depends_on = None

# Copied from 0026 and 0033 (a migration can't import another).
APP_ROLE = "kaagaz_app"
OPERATOR_ROLE = "kaagaz_operator"
TASKS = "('scan', 'ask', 'ensemble', 'autofile')"
FEATURE_NAMES = ("scan_ai", "ask", "ensemble", "autofile", "cost_saving", "review", "my_words", "self_signup")
FEATURES = "(" + ", ".join(f"'{name}'" for name in FEATURE_NAMES) + ")"
SCAN_KINDS = "'scan', 'upgrade', 'careful', 'ensemble_scan'"
ANSWER_KINDS = "'chat', 'ensemble_chat'"
_ENABLED = "o.id = p_operator AND NOT o.disabled AND o.totp_enabled_at IS NOT NULL"
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
_AUDIT = "INSERT INTO public.platform_audit (operator_id, action, target_type, target_id, details)"


def _literal(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


FUNCTIONS = {
    # Every feature, its switch and how many organisations use it now.
    "kaagaz_op_feature_switches(uuid)": f"""
    CREATE FUNCTION public.kaagaz_op_feature_switches(p_operator uuid)
    RETURNS TABLE (feature text, enabled boolean, organisations_using bigint, changed_at timestamptz)
    LANGUAGE sql STABLE SECURITY DEFINER SET search_path = '' AS $$
        WITH f (feature, n) AS (
            SELECT x.feature, x.n FROM unnest(ARRAY[{FEATURES[1:-1]}]::text[]) WITH ORDINALITY AS x (feature, n)
        ), org AS (
            SELECT c.autofile_on, COALESCE(s.ensemble, false) AS ensemble,
                   COALESCE(s.dynamic, p.dynamic, true) AS dynamic
            FROM public.customers c
            LEFT JOIN public.customer_ai_settings s ON s.customer_id = c.id
            LEFT JOIN public.platform_ai_settings p ON true
            WHERE c.status <> 'deleting'
        )
        SELECT f.feature, COALESCE(w.enabled, true),
               (SELECT count(*) FROM org WHERE CASE f.feature WHEN 'ensemble' THEN org.ensemble
                                                              WHEN 'autofile' THEN org.autofile_on
                                                              WHEN 'cost_saving' THEN org.dynamic
                                                              ELSE true END),
               w.changed_at
        FROM f
        LEFT JOIN public.feature_switches w ON w.feature = f.feature
        WHERE EXISTS (SELECT 1 FROM public.operators o WHERE {_ENABLED})
        ORDER BY f.n
    $$
    """,
    # Turn one feature on or off for every organisation, with a fresh code (step-up).
    "kaagaz_op_set_feature_switch(uuid, text, boolean, bigint)": f"""
    CREATE FUNCTION public.kaagaz_op_set_feature_switch(
        p_operator uuid, p_feature text, p_enabled boolean, p_step bigint, OUT outcome text
    )
    LANGUAGE plpgsql SECURITY DEFINER SET search_path = '' AS $$
    DECLARE
        v_enabled boolean;
    BEGIN
        IF p_enabled IS NULL OR p_feature IS NULL OR p_feature NOT IN {FEATURES} THEN
            RAISE EXCEPTION 'unknown feature or switch' USING ERRCODE = 'check_violation';
        END IF;
        {_ACTOR}
        {_STEP_UP}
        SELECT w.enabled INTO v_enabled FROM public.feature_switches w WHERE w.feature = p_feature FOR UPDATE;
        IF COALESCE(v_enabled, true) = p_enabled THEN
            outcome := 'unchanged';
            RETURN;
        END IF;
        INSERT INTO public.feature_switches AS w (feature, enabled, changed_by, changed_at)
        VALUES (p_feature, p_enabled, p_operator, now())
        ON CONFLICT (feature) DO UPDATE
        SET enabled = EXCLUDED.enabled, changed_by = EXCLUDED.changed_by, changed_at = EXCLUDED.changed_at;
        {_AUDIT}
        VALUES (p_operator, CASE WHEN p_enabled THEN 'feature.on' ELSE 'feature.off' END, 'feature', p_feature,
                jsonb_build_object('enabled', p_enabled));
        outcome := 'done';
    END
    $$
    """,
    # The test model while one is set, with what ran on it since it started.
    "kaagaz_op_test_model(uuid)": f"""
    CREATE FUNCTION public.kaagaz_op_test_model(p_operator uuid)
    RETURNS TABLE (scan_model text, chat_model text, note text, started_at timestamptz, pages bigint,
                   questions bigint, cost_usd numeric, seconds_per_page double precision)
    LANGUAGE sql STABLE SECURITY DEFINER SET search_path = '' AS $$
        SELECT t.scan_model, t.chat_model, t.note, t.started_at, coalesce(s.pages, 0)::bigint,
               coalesce(s.questions, 0)::bigint, s.cost, r.per_page
        FROM public.platform_model_override t
        CROSS JOIN LATERAL (
            SELECT sum(x.calls) FILTER (WHERE x.kind IN ({SCAN_KINDS}) AND x.model = t.scan_model) AS pages,
                   count(*) FILTER (WHERE x.kind IN ({ANSWER_KINDS}) AND x.model = t.chat_model) AS questions,
                   sum(x.cost_usd) FILTER (
                       WHERE x.cost_usd IS NOT NULL AND NOT x.cost_estimated
                         AND ((x.kind IN ({SCAN_KINDS}) AND x.model = t.scan_model)
                              OR (x.kind IN ({ANSWER_KINDS}) AND x.model = t.chat_model))) AS cost
            FROM public.customers c
            CROSS JOIN LATERAL (
                SELECT u.kind, u.model, u.calls, u.cost_usd, u.cost_estimated FROM public.model_usage u
                WHERE u.customer_id = c.id AND u.created_at >= t.started_at AND NOT u.cached
            ) x
        ) s
        CROSS JOIN LATERAL (
            SELECT sum((p.config ->> 'seconds_reading')::double precision)
                   / nullif(sum(p.pages_gemini + p.pages_local + p.pages_cached), 0) AS per_page
            FROM public.customers c
            CROSS JOIN LATERAL (
                SELECT g.config, g.pages_gemini, g.pages_local, g.pages_cached FROM public.processing_runs g
                WHERE g.customer_id = c.id AND g.started_at >= t.started_at AND g.status = 'ready'
                  AND g.config ->> 'read_model' = t.scan_model
                  AND jsonb_typeof(g.config -> 'seconds_reading') = 'number'
            ) p
        ) r
        WHERE EXISTS (SELECT 1 FROM public.operators o WHERE {_ENABLED})
    $$
    """,
    # Start (or replace) the test model, with a fresh code: each named model must be in the
    # catalogue, enabled, eligible for its use and verified for the current prompt.
    "kaagaz_op_set_test_model(uuid, text, text, text, text, text, bigint)": f"""
    CREATE FUNCTION public.kaagaz_op_set_test_model(
        p_operator uuid, p_scan text, p_chat text, p_note text, p_scan_prompt text, p_chat_prompt text,
        p_step bigint, OUT outcome text
    )
    LANGUAGE plpgsql SECURITY DEFINER SET search_path = '' AS $$
    BEGIN
        IF p_scan IS NULL AND p_chat IS NULL THEN
            RAISE EXCEPTION 'name a scanning or an answering model' USING ERRCODE = 'check_violation';
        END IF;
        {_ACTOR}
        {_STEP_UP}
        PERFORM 1 FROM public.model_catalog m WHERE m.id IN (p_scan, p_chat) ORDER BY m.id FOR SHARE;
        IF (p_scan IS NOT NULL AND NOT EXISTS (SELECT 1 FROM public.model_catalog m
                                               WHERE m.id = p_scan AND m.eligible AND m.enabled_at IS NOT NULL))
           OR (p_chat IS NOT NULL AND NOT EXISTS (SELECT 1 FROM public.model_catalog m
                                                  WHERE m.id = p_chat AND m.chat_eligible
                                                    AND m.enabled_at IS NOT NULL)) THEN
            outcome := 'unavailable';
            RETURN;
        END IF;
        IF p_scan IS NOT NULL AND NOT EXISTS (SELECT 1 FROM public.model_catalog m
                                              WHERE m.id = p_scan AND m.verified_prompt_version = p_scan_prompt) THEN
            outcome := 'not_verified';
            RETURN;
        END IF;
        IF p_chat IS NOT NULL AND NOT EXISTS (SELECT 1 FROM public.model_catalog m
                                              WHERE m.id = p_chat AND m.chat_verified_prompt = p_chat_prompt) THEN
            outcome := 'chat_not_verified';
            RETURN;
        END IF;
        INSERT INTO public.platform_model_override AS t (id, scan_model, chat_model, note, started_by, started_at)
        VALUES (true, p_scan, p_chat, p_note, p_operator, now())
        ON CONFLICT (id) DO UPDATE
        SET scan_model = EXCLUDED.scan_model, chat_model = EXCLUDED.chat_model, note = EXCLUDED.note,
            started_by = EXCLUDED.started_by, started_at = EXCLUDED.started_at;
        {_AUDIT}
        VALUES (p_operator, 'platform.test_model_start', 'platform', NULL,
                jsonb_build_object('scan_model', p_scan, 'chat_model', p_chat));
        outcome := 'done';
    END
    $$
    """,
    # Undo the test model, with a fresh code: every organisation is back as it was.
    "kaagaz_op_clear_test_model(uuid, bigint)": f"""
    CREATE FUNCTION public.kaagaz_op_clear_test_model(p_operator uuid, p_step bigint, OUT outcome text)
    LANGUAGE plpgsql SECURITY DEFINER SET search_path = '' AS $$
    DECLARE
        v_scan text;
        v_chat text;
    BEGIN
        {_ACTOR}
        {_STEP_UP}
        DELETE FROM public.platform_model_override t RETURNING t.scan_model, t.chat_model INTO v_scan, v_chat;
        IF NOT FOUND THEN
            outcome := 'unchanged';
            RETURN;
        END IF;
        {_AUDIT}
        VALUES (p_operator, 'platform.test_model_undo', 'platform', NULL,
                jsonb_build_object('scan_model', v_scan, 'chat_model', v_chat));
        outcome := 'done';
    END
    $$
    """,
}
FUNCTION_COMMENTS = {
    "kaagaz_op_feature_switches": "Operator: every product feature, whether it is on, and how many"
    " organisations use it now. Counts only.",
    "kaagaz_op_set_feature_switch": "Operator: turn one product feature on or off for every organisation,"
    " with a fresh code. Audited.",
    "kaagaz_op_test_model": "Operator: the platform test model while one is set, with the pages, questions,"
    " reported cost and reading seconds a page since it started. Numbers only.",
    "kaagaz_op_set_test_model": "Operator: start or replace the platform test model, with a fresh code;"
    " each model must be enabled, eligible and verified for its use. Audited.",
    "kaagaz_op_clear_test_model": "Operator: undo the platform test model, with a fresh code. Audited.",
}

# Plain constants with no colon before a word, which SQLAlchemy would read as a bind parameter.
COMMENTS = {
    "ai_task_settings": (
        "How much effort the AI puts into a task: an organisation's default (no user) or one"
        " member's own choice. No row is today's behaviour.",
        {
            "id": "The row.",
            "customer_id": "→ customers.id, the organisation the row belongs to (row level security filters on it).",
            "user_id": "→ users.id (same customer), the member whose own choice it is; NULL, the organisation's"
            " default.",
            "task": "scan, ask, ensemble or autofile.",
            "level": "none, low, high or max (scan, ask); off or on (ensemble, autofile).",
            "locked": "Organisation rows only: members may not change this task.",
            "updated_by": "→ users.id (same customer), who saved it last; NULL once that user is gone.",
            "updated_at": "When it was saved last.",
        },
    ),
    "feature_switches": (
        "A product feature the super admin turned on or off for every organisation. No row is on.",
        {
            "feature": "scan_ai, ask, ensemble, autofile, cost_saving, review, my_words or self_signup.",
            "enabled": "On for every organisation (true) or off for all of them.",
            "changed_by": "→ operators.id, who flipped it last (the app can't read this column).",
            "changed_at": "When it was flipped last.",
        },
    ),
    "platform_model_override": (
        "The platform test model: while the row is there every organisation scans and answers with it."
        " Deleting the row puts every organisation back as it was.",
        {
            "id": "Always true: one row at most.",
            "scan_model": "→ model_catalog.id, the model every scan is read with; NULL, each organisation's own.",
            "chat_model": "→ model_catalog.id, the model every answer is drafted with; NULL, each organisation's own.",
            "note": "The super admin's note on why (the app can't read this column).",
            "started_by": "→ operators.id, who started it (the app can't read this column).",
            "started_at": "When it started: the spend since then is the test's.",
        },
    ),
}
INDEX_COMMENTS = {
    "ai_task_settings_organisation": "One default per organisation and task.",
    "ai_task_settings_member": "One choice per member and task.",
}
POLICY_COMMENTS = {
    ("customer_only", "ai_task_settings"): "A row is seen and written only by its own customer.",
    ("read_all", "feature_switches"): "Every role that may read the switches sees them.",
    ("read_all", "platform_model_override"): "Every role that may read the test model sees it.",
}


def _comments() -> list[str]:
    statements = []
    for table, (table_comment, columns) in COMMENTS.items():
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


UPGRADE = [
    f"""
    CREATE TABLE ai_task_settings (
        id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        customer_id uuid NOT NULL REFERENCES customers (id) ON DELETE CASCADE,
        user_id uuid NULL,
        task text NOT NULL CHECK (task IN {TASKS}),
        level text NOT NULL CHECK (level IN ('none', 'low', 'high', 'max', 'on', 'off')),
        locked boolean NOT NULL DEFAULT false,
        updated_by uuid NULL,
        updated_at timestamptz NOT NULL DEFAULT now(),
        CONSTRAINT ai_task_settings_level_fits_task CHECK (
            CASE WHEN task IN ('scan', 'ask') THEN level IN ('none', 'low', 'high', 'max')
                 ELSE level IN ('off', 'on') END),
        CONSTRAINT ai_task_settings_lock_on_organisation CHECK (user_id IS NULL OR NOT locked),
        FOREIGN KEY (customer_id, user_id) REFERENCES users (customer_id, id) ON DELETE CASCADE,
        FOREIGN KEY (customer_id, updated_by) REFERENCES users (customer_id, id) ON DELETE SET NULL (updated_by)
    )
    """,
    "CREATE UNIQUE INDEX ai_task_settings_organisation ON ai_task_settings (customer_id, task) WHERE user_id IS NULL",
    "CREATE UNIQUE INDEX ai_task_settings_member ON ai_task_settings (customer_id, user_id, task)"
    " WHERE user_id IS NOT NULL",
    f"GRANT SELECT, INSERT, UPDATE, DELETE ON ai_task_settings TO {APP_ROLE}",
    "ALTER TABLE ai_task_settings ENABLE ROW LEVEL SECURITY",
    """
    CREATE POLICY customer_only ON ai_task_settings
    USING (customer_id = kaagaz_current_customer())
    WITH CHECK (customer_id = kaagaz_current_customer())
    """,
    f"""
    CREATE TABLE feature_switches (
        feature text PRIMARY KEY CHECK (feature IN {FEATURES}),
        enabled boolean NOT NULL DEFAULT true,
        changed_by uuid NULL REFERENCES operators (id),
        changed_at timestamptz NOT NULL DEFAULT now()
    )
    """,
    """
    CREATE TABLE platform_model_override (
        id boolean PRIMARY KEY DEFAULT true CONSTRAINT platform_model_override_one_row CHECK (id),
        scan_model text NULL REFERENCES model_catalog (id),
        chat_model text NULL REFERENCES model_catalog (id),
        note text NULL CHECK (length(note) <= 500),
        started_by uuid NULL REFERENCES operators (id),
        started_at timestamptz NOT NULL DEFAULT now(),
        CONSTRAINT platform_model_override_some_model CHECK (scan_model IS NOT NULL OR chat_model IS NOT NULL)
    )
    """,
    f"GRANT SELECT (feature, enabled) ON feature_switches TO {APP_ROLE}",
    f"GRANT SELECT (scan_model, chat_model) ON platform_model_override TO {APP_ROLE}",
    "ALTER TABLE feature_switches ENABLE ROW LEVEL SECURITY",
    "ALTER TABLE platform_model_override ENABLE ROW LEVEL SECURITY",
    "CREATE POLICY read_all ON feature_switches FOR SELECT USING (true)",
    "CREATE POLICY read_all ON platform_model_override FOR SELECT USING (true)",
    *_comments(),
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
]

DOWNGRADE = [
    *[f"DROP FUNCTION IF EXISTS public.{signature}" for signature in FUNCTIONS],
    "DROP TABLE IF EXISTS platform_model_override",
    "DROP TABLE IF EXISTS feature_switches",
    "DROP TABLE IF EXISTS ai_task_settings",
]


def upgrade() -> None:
    for statement in UPGRADE:
        op.execute(statement)


def downgrade() -> None:
    for statement in DOWNGRADE:
        op.execute(statement)
