"""Google sign-in by organisation and role (#507, Tushar, 4 Nov 2026). NEEDS VRUSHIT'S
APPROVAL: schema and security configuration.

* ``platform_ai_settings.google_signin``: the platform's default, true. ``GOOGLE_SIGNIN_ENABLED``
  (the server's setting) stays the master switch above it.
* ``customers.google_signin``: one organisation's own value, set by the super admin only (NULL
  follows the platform). The app keeps SELECT only (its UPDATE on customers is per column, 0014),
  as ``admin_two_step_required`` (0068).
* The org admin's rules, on ``customers``, which the app may update (a column grant, as 0062's
  ``auto_approve_connections``): ``google_roles`` (the roles that may use Google, all three by
  default), ``google_only_roles`` (the roles whose password stops signing in once their Google
  account is connected, none by default; always inside ``google_roles``) and ``google_domains``
  (the company domains a Google account must be from, at most 10; none: any).
* ``user_identities.hosted_domain``: Google's ``hd`` claim (lower case) when the account was
  connected or last signed in, kept only when Google verified the email (so a stored link passes
  the domains exactly when Google's own sign-in would); the app may update it (a column grant).
  The app may also update ``email_at_link`` (#507 review): a Google sign-in refused for the
  domains keeps Google's verified ``hd`` and email, so the password then opens the limited
  session. The downgrade revokes that grant (the column stays).
* ``kaagaz_op_google_signin`` (read: the organisation's own value, the platform's, and the
  organisation's "Google only" roles and domains) and ``kaagaz_op_set_google_signin`` (a fresh
  code, step-up): the operator's own functions, as 0040's. When the value in force changes,
  the users with an approved connection are signed out (``session_version``, as 0069): in that
  organisation, or, for the platform, in every organisation that follows it. Audited
  ``org.google_signin_set`` in both logs, or ``google.platform_set`` in ``platform_audit``,
  with ``{old, new, effective_old, effective_new, by: "operator"}``.

The defaults change nothing on release day: Google stays on wherever the server allows it, for
every role, with no domain rule and every password working. Constant defaults: no table rewrite.
The downgrade drops the two functions and the six columns (their grants go with them). The
connections stay, and every password signs in again.

Revision ID: 0074
Revises: 0073 (Ultra low cost up to ₹0.55)
"""

import importlib.util
from pathlib import Path
from types import ModuleType

from alembic import op

PREVIOUS = "0073"
revision = "0074"
down_revision = PREVIOUS
branch_labels = None
depends_on = None


def _load_0038() -> ModuleType:
    path = Path(__file__).with_name("0038_ai_task_settings.py")
    spec = importlib.util.spec_from_file_location("migration_0038_for_0074", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_OLD = _load_0038()
APP_ROLE = _OLD.APP_ROLE
OPERATOR_ROLE = _OLD.OPERATOR_ROLE
_ENABLED = _OLD._ENABLED
_literal = _OLD._literal
ROLES = "ARRAY['admin', 'reviewer', 'member']::text[]"
MAX_DOMAINS = 10
RULES = ("google_roles", "google_only_roles", "google_domains")

# The users signed in with an approved connection, in the organisations ``{where}`` names.
_SIGN_OUT = """
            UPDATE public.users AS u SET session_version = u.session_version + 1
            WHERE {where} AND EXISTS (SELECT 1 FROM public.user_identities i
                WHERE i.customer_id = u.customer_id AND i.user_id = u.id AND i.status = 'approved');"""
_SIGN_OUT_FOLLOWERS = _SIGN_OUT.format(
    where="u.customer_id IN (SELECT c.id FROM public.customers c WHERE c.google_signin IS NULL)"
)
_SIGN_OUT_ORG = _SIGN_OUT.format(where="u.customer_id = p_customer")

FUNCTIONS = {
    # One organisation's own value with the platform's, its "Google only" roles and its domains
    # (no customer: the platform's alone). Nothing for an unknown organisation.
    "kaagaz_op_google_signin(uuid, uuid)": f"""
    CREATE FUNCTION public.kaagaz_op_google_signin(p_operator uuid, p_customer uuid)
    RETURNS TABLE (org_on boolean, platform_on boolean, only_roles text[], domains text[])
    LANGUAGE sql STABLE SECURITY DEFINER SET search_path = '' AS $$
        SELECT c.google_signin, p.google_signin, c.google_only_roles, c.google_domains
        FROM public.platform_ai_settings p
        LEFT JOIN public.customers c ON c.id = p_customer
        WHERE (p_customer IS NULL OR c.id IS NOT NULL)
          AND EXISTS (SELECT 1 FROM public.operators o WHERE {_ENABLED})
    $$
    """,
    # Set the platform's (no customer; true or false) or one organisation's (NULL: follow the
    # platform), with a fresh code (step-up).
    "kaagaz_op_set_google_signin(uuid, uuid, boolean, bigint)": f"""
    CREATE FUNCTION public.kaagaz_op_set_google_signin(
        p_operator uuid, p_customer uuid, p_on boolean, p_step bigint, OUT outcome text
    )
    LANGUAGE plpgsql SECURITY DEFINER SET search_path = '' AS $$
    DECLARE
        v_status text;
        v_old boolean;
        v_platform boolean;
        v_details jsonb;
    BEGIN
        IF p_customer IS NULL AND p_on IS NULL THEN
            RAISE EXCEPTION 'the platform is on or off' USING ERRCODE = 'check_violation';
        END IF;
        {_OLD._ACTOR}
        {_OLD._STEP_UP}
        IF p_customer IS NULL THEN
            SELECT p.google_signin INTO v_old FROM public.platform_ai_settings p FOR UPDATE;
            IF v_old = p_on THEN
                outcome := 'unchanged';
                RETURN;
            END IF;
            UPDATE public.platform_ai_settings AS p SET google_signin = p_on;
            {_SIGN_OUT_FOLLOWERS}
            INSERT INTO public.platform_audit (operator_id, action, target_type, target_id, details)
            VALUES (p_operator, 'google.platform_set', 'platform', NULL,
                    jsonb_build_object('old', v_old, 'new', p_on, 'effective_old', v_old,
                                       'effective_new', p_on, 'by', 'operator'));
            outcome := 'done';
            RETURN;
        END IF;
        SELECT c.status, c.google_signin INTO v_status, v_old
        FROM public.customers c WHERE c.id = p_customer FOR UPDATE;
        IF NOT FOUND THEN
            outcome := 'missing';
            RETURN;
        END IF;
        IF v_status = 'deleting' THEN
            outcome := 'deleting';
            RETURN;
        END IF;
        IF v_old IS NOT DISTINCT FROM p_on THEN
            outcome := 'unchanged';
            RETURN;
        END IF;
        SELECT p.google_signin INTO v_platform FROM public.platform_ai_settings p FOR SHARE;
        UPDATE public.customers AS c SET google_signin = p_on WHERE c.id = p_customer;
        IF COALESCE(v_old, v_platform) <> COALESCE(p_on, v_platform) THEN
            {_SIGN_OUT_ORG}
        END IF;
        v_details := jsonb_build_object('old', v_old, 'new', p_on, 'effective_old', COALESCE(v_old, v_platform),
                                        'effective_new', COALESCE(p_on, v_platform), 'by', 'operator');
        INSERT INTO public.audit_log (customer_id, actor_user_id, action, target_type, target_id, details)
        VALUES (p_customer, NULL, 'org.google_signin_set', 'customer', p_customer::text, v_details);
        INSERT INTO public.platform_audit (operator_id, action, subject_customer_id, target_type, target_id,
                                           details)
        VALUES (p_operator, 'org.google_signin_set', p_customer, 'customer', p_customer::text, v_details);
        outcome := 'done';
    END
    $$
    """,
}
FUNCTION_COMMENTS = {
    "kaagaz_op_google_signin": "Operator: one organisation's own Google sign-in value (NULL: follow the"
    " platform) with the platform's, and its Google only roles and domains (no customer: the"
    " platform's alone). Nothing for an unknown organisation.",
    "kaagaz_op_set_google_signin": "Operator: turn Google sign-in on or off for the platform (no"
    " customer) or one organisation (NULL: follow the platform), with a fresh code. A change of the"
    " value in force signs out the users with an approved connection there. Audited.",
}
COLUMN_COMMENTS = {
    "platform_ai_settings.google_signin": "Google sign-in for every organisation that follows the"
    " platform. Default true; the server's GOOGLE_SIGNIN_ENABLED is the master switch above it.",
    "customers.google_signin": "The super admin's Google sign-in for this organisation: true, false, or"
    " NULL to follow the platform. Off: everyone signs in with their password. The app can't write it.",
    "customers.google_roles": "The roles that may sign in with Google (admin, reviewer, member), set by"
    " the organisation's admin. Default all three.",
    "customers.google_only_roles": "The roles whose password stops signing in once their Google account"
    " is connected (until then it only lets them connect Google). Always inside google_roles.",
    "customers.google_domains": f"The company domains (lower case) a Google account must be from, at"
    f" most {MAX_DOMAINS}; empty: any.",
    "user_identities.hosted_domain": "Google's hd claim (lower case) when the account was connected or"
    " last signed in, kept only when Google verified the email; NULL: none (a personal account or an"
    " unverified email), which never passes an organisation's domains.",
}

UPGRADE = [
    "ALTER TABLE platform_ai_settings ADD COLUMN google_signin boolean NOT NULL DEFAULT true",
    "ALTER TABLE customers ADD COLUMN google_signin boolean NULL",
    f"""
    ALTER TABLE customers
        ADD COLUMN google_roles text[] NOT NULL DEFAULT '{{admin,reviewer,member}}'
            CONSTRAINT customers_google_roles_check CHECK (google_roles <@ {ROLES}),
        ADD COLUMN google_only_roles text[] NOT NULL DEFAULT '{{}}',
        ADD COLUMN google_domains text[] NOT NULL DEFAULT '{{}}'
            CONSTRAINT customers_google_domains_check CHECK (cardinality(google_domains) <= {MAX_DOMAINS}),
        ADD CONSTRAINT customers_google_only_roles_check CHECK (google_only_roles <@ google_roles)
    """,
    f"GRANT UPDATE ({', '.join(RULES)}) ON customers TO {APP_ROLE}",
    "ALTER TABLE user_identities ADD COLUMN hosted_domain text NULL"
    " CONSTRAINT user_identities_hosted_domain_check CHECK (length(hosted_domain) BETWEEN 1 AND 253)",
    f"GRANT UPDATE (hosted_domain, email_at_link) ON user_identities TO {APP_ROLE}",
    *[f"COMMENT ON COLUMN {column} IS {_literal(text)}" for column, text in COLUMN_COMMENTS.items()],
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
    f"REVOKE UPDATE (email_at_link) ON user_identities FROM {APP_ROLE}",
    "ALTER TABLE user_identities DROP COLUMN IF EXISTS hosted_domain",
    "ALTER TABLE customers DROP CONSTRAINT IF EXISTS customers_google_only_roles_check,"
    " DROP COLUMN IF EXISTS google_domains, DROP COLUMN IF EXISTS google_only_roles,"
    " DROP COLUMN IF EXISTS google_roles, DROP COLUMN IF EXISTS google_signin",
    "ALTER TABLE platform_ai_settings DROP COLUMN IF EXISTS google_signin",
]


def upgrade() -> None:
    for statement in UPGRADE:
        op.execute(statement)


def downgrade() -> None:
    for statement in DOWNGRADE:
        op.execute(statement)
