"""Ways to sign in (#457 phase 1): connected Google accounts, and the admin's auto-approval.

* ``user_identities``: one row per outside account (Google now; Microsoft later, only when a
  customer asks) connected to a Kaagaz user. The key is (provider, issuer, subject), unique
  across every customer, so one outside account belongs to one Kaagaz user; a user has at
  most one of each provider. The email is a label only, never a key. ``status`` is pending
  until an admin approves (or approved at once: an admin's own, auto-approval, or a Google
  email that is the user's own). ``customer_id`` on every row, the ``customer_only`` policy
  (0002's), and a (customer_id, user_id) key to ``users`` that goes with the user. The app may
  read, add and delete rows, and update only the approval and last-used columns.
* ``kaagaz_identity_lookup(provider, issuer, subject)``: a provider sign-in arrives with no
  customer, so the connection is found through this SECURITY DEFINER function (empty search
  path, revoked from PUBLIC, the app's only), as signing in finds an e-mail. It reads
  ``user_identities`` only, so an operator is never found.
* ``kaagaz_email_belongs_to_other(email, user)``: whether an e-mail is another user's sign-in
  e-mail, in any customer, for the "already belongs" rule. A yes or no only.
* ``customers.auto_approve_connections``: the admin's "Approve new sign-in connections
  automatically", off by default; the app may update it (a column grant, as 0025's
  ``autofile_on``).

The downgrade drops them all; every connection is lost (people still sign in with their
password).

Revision ID: 0062
Revises: 0061 (email)
"""

from alembic import op

revision = "0062"
down_revision = "0061"
branch_labels = None
depends_on = None

APP_ROLE = "kaagaz_app"
LOOKUP = "kaagaz_identity_lookup(text, text, text)"
BELONGS = "kaagaz_email_belongs_to_other(text, uuid)"


def _literal(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


TABLE_COMMENT = (
    "Outside accounts (Google; Microsoft later) connected to a Kaagaz user, for signing in with"
    " them. One per provider per user, and one Kaagaz user per outside account."
)
COLUMN_COMMENTS = {
    "id": "The connection's id.",
    "customer_id": "The customer of the user the account is connected to.",
    "user_id": "The Kaagaz user the outside account signs in as.",
    "provider": "google or microsoft.",
    "issuer": "The provider's issuer as its ID token names it, stored in one form.",
    "subject": "The provider's own id for the account (its sub claim), with the issuer the key."
    " Never shown or logged.",
    "email_at_link": "The account's email, in lower case, when it was connected. A label for the"
    " screens only, never used to find anyone.",
    "status": "pending (waiting for an admin, cannot sign in) or approved (signs in).",
    "linked_at": "When the account was connected.",
    "approved_at": "When it was approved. NULL while pending.",
    "approved_by": "The admin who approved it. NULL while pending, when it was approved at once"
    " (an admin's own, auto-approval or the user's own email), or once that admin is removed.",
    "last_used_at": "When it was last used to sign in. NULL: never.",
}
AUTO_APPROVE_COMMENT = (
    "The organisation's admin approves new sign-in connections automatically: members' and"
    " reviewers' Google accounts work at once, without review. Off by default."
)
FUNCTION_COMMENTS = {
    LOOKUP: "Find a connected outside account by provider, issuer and subject (a sign-in arrives"
    " with no customer): ids and status only.",
    BELONGS: "Whether an e-mail is the sign-in e-mail of a user other than this one, in any"
    " customer: yes or no only.",
}

LOOKUP_SQL = """
    CREATE FUNCTION public.kaagaz_identity_lookup(p_provider text, p_issuer text, p_subject text)
    RETURNS TABLE (identity_id uuid, user_id uuid, customer_id uuid, status text)
    LANGUAGE sql STABLE SECURITY DEFINER SET search_path = '' AS $$
        SELECT i.id, i.user_id, i.customer_id, i.status FROM public.user_identities i
        WHERE i.provider = p_provider AND i.issuer = p_issuer AND i.subject = p_subject
    $$
"""
BELONGS_SQL = """
    CREATE FUNCTION public.kaagaz_email_belongs_to_other(p_email text, p_user uuid)
    RETURNS boolean
    LANGUAGE sql STABLE SECURITY DEFINER SET search_path = '' AS $$
        SELECT EXISTS (SELECT 1 FROM public.users u WHERE u.email = lower(p_email) AND u.id <> p_user)
    $$
"""


def _function(signature: str, create: str) -> list[str]:
    return [
        create,
        f"COMMENT ON FUNCTION public.{signature} IS {_literal(FUNCTION_COMMENTS[signature])}",
        f"REVOKE ALL ON FUNCTION public.{signature} FROM PUBLIC",
        f"GRANT EXECUTE ON FUNCTION public.{signature} TO {APP_ROLE}",
    ]


UPGRADE = [
    """
    CREATE TABLE user_identities (
        id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        customer_id uuid NOT NULL,
        user_id uuid NOT NULL,
        provider text NOT NULL CHECK (provider IN ('google', 'microsoft')),
        issuer text NOT NULL CHECK (length(issuer) BETWEEN 1 AND 200),
        subject text NOT NULL CHECK (length(subject) BETWEEN 1 AND 255),
        email_at_link text NULL CHECK (length(email_at_link) <= 254),
        status text NOT NULL CHECK (status IN ('pending', 'approved')),
        linked_at timestamptz NOT NULL,
        approved_at timestamptz NULL,
        approved_by uuid NULL,
        last_used_at timestamptz NULL,
        CHECK ((status = 'approved') = (approved_at IS NOT NULL)),
        CHECK (approved_by IS NULL OR approved_at IS NOT NULL),
        UNIQUE (provider, issuer, subject),
        UNIQUE (customer_id, user_id, provider),
        FOREIGN KEY (customer_id, user_id) REFERENCES users (customer_id, id) ON DELETE CASCADE,
        FOREIGN KEY (customer_id, approved_by) REFERENCES users (customer_id, id)
            ON DELETE SET NULL (approved_by)
    )
    """,
    f"GRANT SELECT, INSERT, DELETE ON user_identities TO {APP_ROLE}",
    f"GRANT UPDATE (status, approved_at, approved_by, last_used_at) ON user_identities TO {APP_ROLE}",
    "ALTER TABLE user_identities ENABLE ROW LEVEL SECURITY",
    """
    CREATE POLICY customer_only ON user_identities
    USING (customer_id = kaagaz_current_customer())
    WITH CHECK (customer_id = kaagaz_current_customer())
    """,
    f"COMMENT ON TABLE user_identities IS {_literal(TABLE_COMMENT)}",
    *[
        f"COMMENT ON COLUMN user_identities.{column} IS {_literal(comment)}"
        for column, comment in COLUMN_COMMENTS.items()
    ],
    *_function(LOOKUP, LOOKUP_SQL),
    *_function(BELONGS, BELONGS_SQL),
    "ALTER TABLE customers ADD COLUMN auto_approve_connections boolean NOT NULL DEFAULT false",
    f"COMMENT ON COLUMN customers.auto_approve_connections IS {_literal(AUTO_APPROVE_COMMENT)}",
    f"GRANT UPDATE (auto_approve_connections) ON customers TO {APP_ROLE}",
]

DOWNGRADE = [
    f"REVOKE UPDATE (auto_approve_connections) ON customers FROM {APP_ROLE}",
    "ALTER TABLE customers DROP COLUMN auto_approve_connections",
    f"DROP FUNCTION public.{BELONGS}",
    f"DROP FUNCTION public.{LOOKUP}",
    "DROP TABLE user_identities",
]


def upgrade() -> None:
    for statement in UPGRADE:
        op.execute(statement)


def downgrade() -> None:
    for statement in DOWNGRADE:
        op.execute(statement)
