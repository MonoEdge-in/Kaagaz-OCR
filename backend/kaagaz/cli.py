"""Set-up commands: ``python -m kaagaz.cli <command>``.

* ``create-customer --name --admin-email --admin-name``: a new customer and its
  first admin.
* ``create-user --customer-id --email --name --role``: one more user.
* ``seed-demo``: two made-up customers for the laptop, "Sharma Traders" and
  "Mehta Textiles", each with an admin, a reviewer and a member. Safe to run
  again: a customer whose admin already exists is skipped.
* ``reset-admin-2fa --email``: turn an admin's two-step code off (PRD 1 A6), for
  the last admin of a customer, who has no other admin to do it. Their sessions
  end and their next admin sign-in sets a new code up. It needs no password:
  whoever runs it already has the server.

Why passwords are never flags: a flag lands in the shell history and the
process list. They come from ``KAAGAZ_ADMIN_PASSWORD`` (``KAAGAZ_DEMO_PASSWORD``
for seed-demo) or a hidden prompt, and must be at least 12 characters.

Output goes to ``sys.stdout.write`` (the one place the backend writes to the
terminal instead of logging): ids and e-mails only, never a password.
"""

import argparse
import getpass
import os
import sys
import uuid
from collections.abc import Callable, Mapping, Sequence
from typing import TextIO

from kaagaz.api.auth import ROLES, hash_password
from kaagaz.db import audit
from kaagaz.db import users as users_sql
from kaagaz.db.engine import Database, make_engine
from kaagaz.settings import Settings, SettingsError

MIN_PASSWORD = 12
ADMIN_ENV_VAR = "KAAGAZ_ADMIN_PASSWORD"
DEMO_ENV_VAR = "KAAGAZ_DEMO_PASSWORD"

# Made-up companies on the reserved ".example" domain.
DEMO_CUSTOMERS = (
    ("Sharma Traders", "sharma-traders.example"),
    ("Mehta Textiles", "mehta-textiles.example"),
)
DEMO_ROLES = ("admin", "reviewer", "member")


class CliError(Exception):
    """Stops the command with a short message and exit code 2."""


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m kaagaz.cli", description="Kaagaz set-up commands.")
    commands = parser.add_subparsers(dest="command", required=True)

    customer = commands.add_parser("create-customer", help="a new customer and its first admin")
    customer.add_argument("--name", required=True)
    customer.add_argument("--admin-email", required=True)
    customer.add_argument("--admin-name", required=True)

    user = commands.add_parser("create-user", help="add a user to a customer")
    user.add_argument("--customer-id", required=True)
    user.add_argument("--email", required=True)
    user.add_argument("--name", required=True)
    user.add_argument("--role", required=True, choices=ROLES)

    commands.add_parser("seed-demo", help="two made-up customers for the laptop")

    reset = commands.add_parser("reset-admin-2fa", help="turn an admin's two-step code off")
    reset.add_argument("--email", required=True)
    return parser


def _password(environ: Mapping[str, str], variable: str, ask: Callable[[str], str] | None) -> str:
    password = environ.get(variable, "")
    if not password and ask is not None:
        password = ask("Password: ")
        if password != ask("Repeat password: "):
            raise CliError("the passwords do not match")
    if len(password) < MIN_PASSWORD:
        raise CliError(f"the password must be at least {MIN_PASSWORD} characters (set {variable})")
    return password


def _create_customer(db: Database, name: str, email: str, admin_name: str, password: str) -> str:
    try:
        with db.system() as conn:
            return users_sql.create_customer(
                conn,
                name=name,
                admin_email=email,
                admin_name=admin_name,
                password_hash=hash_password(password),
            )
    except users_sql.EmailTaken:
        raise CliError("that e-mail is already in use") from None


def _create_user(db: Database, customer_id: str, email: str, name: str, role: str, password: str) -> str:
    try:
        uuid.UUID(customer_id)
    except ValueError:
        raise CliError("the customer id is not valid") from None
    try:
        with db.customer(customer_id) as conn:
            # Every customer has at least its first admin, so no users means no customer.
            if not users_sql.list_users(conn, customer_id):
                raise CliError("no such customer")
            row = users_sql.create_user(
                conn,
                customer_id,
                email=email,
                display_name=name,
                role=role,
                password_hash=hash_password(password),
                must_change_password=False,
            )
            audit.record(
                conn,
                customer_id,
                actor_user_id=None,
                action="user.create",
                target_type="user",
                target_id=str(row["id"]),
                details={"role": role},
            )
    except users_sql.EmailTaken:
        raise CliError("that e-mail is already in use") from None
    return str(row["id"])


def _reset_admin_2fa(db: Database, email: str) -> str:
    """Clear the admin's two-step code and raise their session version. Returns their id."""
    with db.system() as conn:
        found = users_sql.login_lookup(conn, email)
    if found is None or found.role != "admin":
        raise CliError("no admin has that e-mail")
    with db.customer(found.customer_id) as conn:
        if users_sql.reset_totp(conn, found.customer_id, found.user_id) is None:
            raise CliError("no admin has that e-mail")
        # PROVISIONAL: "user.reset_totp", the same action as the admin screen's 2FA reset.
        audit.record(
            conn,
            found.customer_id,
            actor_user_id=None,
            action="user.reset_totp",
            target_type="user",
            target_id=found.user_id,
            details={"by": "cli"},
        )
    return found.user_id


def _seed_demo(db: Database, password: str, out: TextIO) -> None:
    for name, domain in DEMO_CUSTOMERS:
        emails = {role: f"{role}@{domain}" for role in DEMO_ROLES}
        with db.system() as conn:
            existing = users_sql.login_lookup(conn, emails["admin"])
        if existing is not None:
            out.write(f"{name}: already there, skipped\n")
            continue
        customer_id = _create_customer(db, name, emails["admin"], f"{name} admin", password)
        for role in ("reviewer", "member"):
            _create_user(db, customer_id, emails[role], f"{name} {role}", role, password)
        out.write(f"{name} ({customer_id}):\n")
        for role in DEMO_ROLES:
            out.write(f"  {role}: {emails[role]}\n")


def main(
    argv: Sequence[str] | None = None,
    *,
    db: Database | None = None,
    environ: Mapping[str, str] | None = None,
    out: TextIO | None = None,
    ask: Callable[[str], str] | None = getpass.getpass,
) -> int:
    """Run one command. Tests pass ``db``, ``environ``, ``out`` and ``ask``."""
    args = _parser().parse_args(argv)
    env = os.environ if environ is None else environ
    stdout = sys.stdout if out is None else out
    try:
        if db is None:
            settings = Settings.from_env(env)
            db = Database(make_engine(settings.database_url), settings.db_app_role)
        if args.command == "create-customer":
            password = _password(env, ADMIN_ENV_VAR, ask)
            customer_id = _create_customer(db, args.name, args.admin_email, args.admin_name, password)
            stdout.write(f"customer {customer_id}\n")
        elif args.command == "create-user":
            password = _password(env, ADMIN_ENV_VAR, ask)
            user_id = _create_user(db, args.customer_id, args.email, args.name, args.role, password)
            stdout.write(f"user {user_id}\n")
        elif args.command == "reset-admin-2fa":
            user_id = _reset_admin_2fa(db, args.email)
            stdout.write(f"user {user_id}: 2FA reset, they set it up again at the next admin sign-in\n")
        else:
            _seed_demo(db, _password(env, DEMO_ENV_VAR, None), stdout)
    except (CliError, SettingsError) as error:
        sys.stderr.write(f"error: {error}\n")
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
