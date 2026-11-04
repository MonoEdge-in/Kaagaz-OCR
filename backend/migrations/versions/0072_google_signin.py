"""Google sign-in by organisation and role (#507): the columns B1's sign-in reads.

shortcut: stand-in, B2's real 0072 replaces it at the merge. Only the columns, defaults,
CHECKs, grants and comments of the plan (section 1); no operator functions.

Revision ID: 0072
Revises: 0071 (folder upload)
"""

from alembic import op

revision = "0072"
down_revision = "0071"
branch_labels = None
depends_on = None

APP_ROLE = "kaagaz_app"

# 0007's rule: every column has a comment.
COMMENTS = {
    "platform_ai_settings.google_signin": "The platform's Google sign-in default for every"
    " organisation that follows it (#507). GOOGLE_SIGNIN_ENABLED stays the master switch.",
    "customers.google_signin": "The super admin's Google sign-in switch for this organisation"
    " (#507); NULL follows the platform. The app may not write it.",
    "customers.google_roles": "The roles that may sign in with Google (#507), set by the org admin.",
    "customers.google_only_roles": "The roles whose password stops working for sign-in once"
    " their Google account is connected (#507); a subset of google_roles.",
    "customers.google_domains": "The company domains a Google account must be from (#507), at"
    " most 10; empty allows any.",
    "user_identities.hosted_domain": "Google's hd claim (the Workspace domain) at the last"
    " connect or sign-in, lower case (#507).",
}

UPGRADE = [
    "ALTER TABLE platform_ai_settings ADD COLUMN google_signin boolean NOT NULL DEFAULT true",
    "ALTER TABLE customers ADD COLUMN google_signin boolean NULL",
    "ALTER TABLE customers ADD COLUMN google_roles text[] NOT NULL DEFAULT '{admin,reviewer,member}'"
    " CONSTRAINT customers_google_roles_check CHECK (google_roles <@ ARRAY['admin','reviewer','member'])",
    "ALTER TABLE customers ADD COLUMN google_only_roles text[] NOT NULL DEFAULT '{}'",
    "ALTER TABLE customers ADD COLUMN google_domains text[] NOT NULL DEFAULT '{}'"
    " CONSTRAINT customers_google_domains_check CHECK (cardinality(google_domains) <= 10)",
    "ALTER TABLE customers ADD CONSTRAINT customers_google_only_roles_check"
    " CHECK (google_only_roles <@ google_roles)",
    f"GRANT UPDATE (google_roles, google_only_roles, google_domains) ON customers TO {APP_ROLE}",
    "ALTER TABLE user_identities ADD COLUMN hosted_domain text NULL",
    f"GRANT UPDATE (hosted_domain) ON user_identities TO {APP_ROLE}",
    *[
        f"COMMENT ON COLUMN {column} IS '{comment.replace(chr(39), chr(39) * 2)}'"
        for column, comment in COMMENTS.items()
    ],
]

DOWNGRADE = [
    f"REVOKE UPDATE (hosted_domain) ON user_identities FROM {APP_ROLE}",
    "ALTER TABLE user_identities DROP COLUMN hosted_domain",
    f"REVOKE UPDATE (google_roles, google_only_roles, google_domains) ON customers FROM {APP_ROLE}",
    "ALTER TABLE customers DROP COLUMN google_domains",
    "ALTER TABLE customers DROP COLUMN google_only_roles",
    "ALTER TABLE customers DROP COLUMN google_roles",
    "ALTER TABLE customers DROP COLUMN google_signin",
    "ALTER TABLE platform_ai_settings DROP COLUMN google_signin",
]


def upgrade() -> None:
    for statement in UPGRADE:
        op.execute(statement)


def downgrade() -> None:
    for statement in DOWNGRADE:
        op.execute(statement)
