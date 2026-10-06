"""Set-up commands: ``python -m kaagaz.cli <command>``.

* ``create-customer --name --admin-email --admin-name``: a new customer and its
  first admin.
* ``create-user --customer-id --email --name --role``: one more user.
* ``seed-demo``: two made-up customers for the laptop, "Sharma Traders" and
  "Mehta Textiles", each with an admin, a reviewer and a member. Safe to run
  again: a customer whose admin already exists is skipped.
* ``disable-customer --customer-id`` and ``enable-customer --customer-id``: turn
  an organisation off or on again (super admin PR 1, 0014, PROVISIONAL). While
  it is off its users can't sign in, their sessions end at the next request,
  the worker fails its jobs (``customer_disabled``) and no model is called for
  it. A customer being deleted is refused, and nothing changes. Each change
  writes one audit row in that customer's log (``customer.disable``,
  ``customer.enable``). These two run as the table owner (``_owner``).
* ``reset-admin-2fa --email``: turn an admin's two-step code off (PRD 1 A6), for
  the last admin of a customer, who has no other admin to do it. Their sessions
  end; they can turn it on again in Admin, Settings. It needs no password:
  whoever runs it already has the server.
* ``measure --file PATH [--truth DIR]``: read one local file the way the worker
  does and print, per page, its route and why, the route check's numbers, and
  the time it took (PRD 2 X4, the council's ML 12), to set the thresholds and
  timeouts from real numbers. It touches no database, file storage or queue, and
  it does NOT run the virus scan: use it only on files you trust. ``--truth``
  names a folder of the right text for each page (``1.txt``, ``2.txt``, ...,
  UTF-8), and adds each page's character and word error rates (CER, WER). It
  prints no text and no file name: only numbers and short codes.
* ``reembed [--customer-id ID] [--all]``: give pieces their meaning vectors
  again after the model or what is embedded changed (PRD 3 V4), for every
  customer or one. Per document: one short transaction clears the vectors whose
  tag is not the current one (``--all``: every vector), then the pieces with no
  vector are embedded, with the model called outside any transaction. If the
  model fails it stops; running it again (without ``--all``) goes on where it
  stopped, and a run with nothing left to do makes only its one probe call (a
  model already down stops it before anything is cleared). Run it
  once after upgrading to PRD 3 V3: every older vector carries the old tag.
  Then each customer's stories (Story of this PDF) with no vector, or one
  with another tag, are embedded, 32 at a time, each as its own user, each
  story tried once a run; ``--all`` does not embed a story again whose vector
  is current. A customer that is not active is skipped and named.
  Prints ids and counts only.

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
import time
import unicodedata
import uuid
from collections.abc import Callable, Iterator, Mapping, Sequence
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import TextIO

from sqlalchemy import Connection

from kaagaz.ai.client import ModelClient, ModelOutputError, ModelUnavailable, build_model_client
from kaagaz.api.auth import ROLES, hash_password
from kaagaz.db import audit
from kaagaz.db import customers as customers_sql
from kaagaz.db import users as users_sql
from kaagaz.db.document_stories import recipe_tag as story_tag
from kaagaz.db.engine import Database, make_engine
from kaagaz.ingestion import sniff
from kaagaz.ingestion.spool import HEAD_SIZE
from kaagaz.preparing.page_checks import BlankMeasure, measure_blank
from kaagaz.preparing.pages import PreparedPages, prepare_pages
from kaagaz.reading.ocr import MeteredOcrEngine, build_ocr_engine
from kaagaz.reading.route import RouteDecision
from kaagaz.reading.socket import NoReader
from kaagaz.reading.spans import ReadError, Span
from kaagaz.reading.wiring import PdfPageCheck, build_reader_socket, pdf_text_layer_checks
from kaagaz.search import indexing, story_vectors
from kaagaz.settings import Settings, SettingsError

MIN_PASSWORD = 12
# measure --truth: each page's texts are cut to these before the edit distance (council PR 3, low 10).
MAX_TRUTH_CHARS = 5000
MAX_TRUTH_WORDS = 1000
ADMIN_ENV_VAR = "KAAGAZ_ADMIN_PASSWORD"
DEMO_ENV_VAR = "KAAGAZ_DEMO_PASSWORD"
# reembed embeds this once before it clears anything (a check that the model answers).
REEMBED_PROBE = "probe"
# reembed: the stories one pass embeds (one model call).
REEMBED_STORIES = 32

# Made-up companies on the reserved ".example" domain.
DEMO_CUSTOMERS = (
    ("Sharma Traders", "sharma-traders.example"),
    ("Mehta Textiles", "mehta-textiles.example"),
)
DEMO_ROLES = ("admin", "reviewer", "member")
# disable-customer and enable-customer: the status each sets, its audit action and what it is called.
CUSTOMER_STATUS = {
    "disable-customer": (customers_sql.DISABLED, "customer.disable", "disabled"),
    "enable-customer": (customers_sql.ACTIVE, "customer.enable", "enabled"),
}


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

    disable = commands.add_parser("disable-customer", help="turn an organisation off: no sign-in, no jobs")
    disable.add_argument("--customer-id", required=True)
    enable = commands.add_parser("enable-customer", help="turn a disabled organisation on again")
    enable.add_argument("--customer-id", required=True)

    reset = commands.add_parser("reset-admin-2fa", help="turn an admin's two-step code off")
    reset.add_argument("--email", required=True)

    measure = commands.add_parser(
        "measure", help="read one trusted local file and print each page's route and time (no virus scan)"
    )
    measure.add_argument("--file", required=True, type=Path)
    measure.add_argument(
        "--truth", type=Path, help="a folder of 1.txt, 2.txt, ...: the right text of each page"
    )

    reembed = commands.add_parser("reembed", help="embed pieces again after a model or recipe change")
    reembed.add_argument("--customer-id", help="one customer only (default: every customer)")
    reembed.add_argument("--all", action="store_true", help="embed every piece again, not only stale ones")
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


@contextmanager
def _owner(db: Database) -> Iterator[Connection]:
    """One transaction as the login role itself, with NO ``SET LOCAL ROLE``: the
    table owner, which row level security does not filter. Only for changing a
    customer's status, which ``kaagaz_app`` may not do (0014), so no code on the
    customer side can. Every statement in it names the one customer by id.
    PROVISIONAL: it needs DATABASE_URL to log in as the owner, as it does today
    (change plan, open question 8)."""
    with db.engine.begin() as conn:
        yield conn


def _set_customer_status(db: Database, command: str, customer_id: str, now: datetime) -> str:
    """Disable or enable one customer and write its audit row, in one owner
    transaction. Returns the line to print. O(1)."""
    try:
        uuid.UUID(customer_id)
    except ValueError:
        raise CliError("the customer id is not valid") from None
    status, action, done = CUSTOMER_STATUS[command]
    with _owner(db) as conn:
        before = customers_sql.set_status(conn, customer_id, status, now=now)
        if before is None:
            raise CliError("no such customer")
        if before == customers_sql.DELETING:
            raise CliError("this customer is being deleted; nothing was changed (cancel the deletion first)")
        if before == status:
            return f"customer {customer_id}: already {done}, nothing changed"
        audit.record(
            conn,
            customer_id,
            actor_user_id=None,
            action=action,
            target_type="customer",
            target_id=customer_id,
            details={"by": "cli"},
        )
    return f"customer {customer_id}: {done}"


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


def _reembed(db: Database, model: ModelClient, customer_id: str | None, every: bool, out: TextIO) -> None:
    """Re-embed each customer's documents in turn, one line per customer.

    O(P) model inputs in batches of 32 for the P pieces cleared or without a
    vector, and two short transactions per document plus embed_pieces' own.
    """
    if customer_id is None:
        with db.system() as conn:
            customers = indexing.customer_ids(conn)
    else:
        try:
            uuid.UUID(customer_id)
        except ValueError:
            raise CliError("the customer id is not valid") from None
        customers = [customer_id]
    try:  # one tiny call first, so a model that is already down clears nothing
        model.embed([REEMBED_PROBE])
    except (ModelUnavailable, ModelOutputError) as error:
        raise CliError(f"the model is not answering ({error}); nothing was changed") from None
    for customer in customers:
        with db.customer(customer) as conn:
            active = customers_sql.is_active(conn, customer)
            documents = indexing.document_ids(conn, customer) if active else []
        if not active:  # disabled or being deleted (0014): no model calls for it
            out.write(f"customer {customer}: not active, skipped\n")
            continue
        cleared = written = 0
        for document in documents:
            with db.customer(customer) as conn:
                cleared += indexing.clear_stale(conn, customer, document, model.embed_model, every=every)
            try:
                written += indexing.embed_pieces(db, customer, document, model)
            except (ModelUnavailable, ModelOutputError) as error:
                raise CliError(f"the model failed ({error}); run reembed again to go on") from None
        stories = _reembed_stories(db, model, customer)
        out.write(
            f"customer {customer}: {len(documents)} documents, {cleared} cleared, {written} embedded,"
            f" {stories} stories\n"
        )


def _reembed_stories(db: Database, model: ModelClient, customer: str) -> int:
    """Embed the customer's stories with no current vector, a pass at a time, each
    pass after the last one's cursor, so each story is tried once a run (one edited
    meanwhile or refused is left for the next run). Returns how many vectors were
    written. O(S / 32) model calls for the customer's S stories."""
    written = 0
    after: story_vectors.StoryKey | None = None
    while True:
        try:
            done = story_vectors.embed_missing_stories(
                db,
                model,
                limit=REEMBED_STORIES,
                tag=story_tag(model.embed_model),
                customer_id=customer,
                after=after,
            )
        except (ModelUnavailable, ModelOutputError) as error:
            raise CliError(f"the model failed ({error}); run reembed again to go on") from None
        written += done.written
        if done.after is None:
            return written
        after = done.after


# --- measure (PRD 2 X4) -----------------------------------------------------------


def _measure(
    settings: Settings, path: Path, truth: Path | None, out: TextIO, *, timer: Callable[[], float]
) -> None:
    """Read ``path`` as the worker would, and write one line per page, then the totals.

    Each page's route and reason are the ones its reader reported through
    ``on_page``, as in the pipeline (a sheet page is ``cells``); a PDF page's
    numbers come from the route check itself, through the wiring. Time: the reading's, plus O(p x t) per page
    for ``--truth`` (p and t the page's and the truth's characters).
    """
    try:
        data = path.read_bytes()
    except OSError:
        raise CliError("the file cannot be opened") from None
    detected = sniff.detect_type(data[:HEAD_SIZE])
    if detected is None:
        raise CliError("this kind of file is not supported")
    engine = build_ocr_engine(settings)
    meter = None if engine is None else MeteredOcrEngine(engine, timer=timer)
    started = timer()
    blanks: dict[int, BlankMeasure] = {}  # page number -> what the blank rule saw (ML 12)
    try:
        prepared = prepare_pages(
            detected,
            data,
            lambda page, count, picture: blanks.__setitem__(page, measure_blank(picture)),
            dpi=settings.page_image_dpi,
            max_pages=settings.max_pages,
            max_image_pixels=settings.max_image_pixels,
            blank_stddev=settings.blank_page_stddev,
            low_res_dpi=settings.low_res_dpi,
        )
        prepared_at = timer()
        stamps = [prepared_at]
        reads: list[RouteDecision | None] = []  # how each page was read, as the reader said

        def on_page(read: RouteDecision | None) -> None:
            stamps.append(timer())
            reads.append(read)

        spans = build_reader_socket(settings, meter).read(detected, data, on_page=on_page)
        read_at = timer()
        checks = pdf_text_layer_checks(settings, data) if detected == sniff.PDF else []
    except NoReader:
        raise CliError("no reader for this kind of file: set OCR_ENGINE for scans and photos") from None
    except ReadError as error:
        raise CliError(f"the file cannot be read ({error.code})") from None
    pages = [(info.page, info.unit) for info in prepared.pages] or sorted(
        {(span.location.page, "cells") for span in spans}
    )
    out.write(f"type {detected} size {len(data) / (1024 * 1024):.2f} MB pages {len(pages)}\n")
    by_page: dict[int, list[Span]] = {}
    for span in spans:
        by_page.setdefault(span.location.page, []).append(span)
    for index, (page, unit) in enumerate(pages):
        page_spans = by_page.get(page, [])
        check = checks[index] if index < len(checks) else None
        read = (
            RouteDecision("cells", "ok")
            if unit == "cells"
            else (reads[index] if index < len(reads) else None)
        )
        line = _page_line(page, read, page_spans, prepared, blanks.get(page), check)
        if len(stamps) == len(pages) + 1:
            line += f" seconds {stamps[index + 1] - stamps[index]:.2f}"
        if truth is not None:
            line += _truth_scores(truth / f"{page}.txt", " ".join(span.text for span in page_spans))
        out.write(line + "\n")
    ocr_pages, ocr_seconds = meter.take() if meter is not None else (0, 0.0)
    per_page = ocr_seconds / ocr_pages if ocr_pages else 0.0
    out.write(
        f"prepare {prepared_at - started:.2f} s, read {read_at - prepared_at:.2f} s,"
        f" OCR {ocr_pages} pages in {ocr_seconds:.2f} s, {per_page:.2f} s per page\n"
    )


def _page_line(
    page: int,
    read: RouteDecision | None,
    spans: list[Span],
    prepared: PreparedPages,
    blank: BlankMeasure | None,
    check: PdfPageCheck | None,
) -> str:
    """One page's route and reason as its reader read it, and the numbers (ML 12),
    with no text from the page."""
    found = prepared.checks.get(page)
    decision = read
    route, reason = ("none", "none") if decision is None else (decision.route, decision.reason)
    text = " ".join(span.text for span in spans)
    devanagari, foreign = _script_shares(text)
    line = f"page {page} route {route} reason {reason} blocks {len(spans)} chars {len(text)}"
    line += f" devanagari {devanagari:.2f} foreign {foreign:.2f}"
    if check is not None:
        signals = check[1]
        line += f" coverage {signals.coverage:.2f} hidden {signals.hidden_share:.2f}"
        line += f" legacy_font {signals.legacy_font_share:.2f} map_errors {signals.map_error_share:.2f}"
    if found is not None:
        dpi = "none" if found.effective_dpi is None else str(found.effective_dpi)
        line += f" dpi {dpi} low_resolution {'yes' if found.low_resolution else 'no'}"
    if blank is not None:
        line += f" blank_stddev {blank.stddev:.2f} dark_pixels {blank.dark_pixels}"
    return line


def _script_shares(text: str) -> tuple[float, float]:
    """(Devanagari, other scripts) as shares of the letters and marks; digits and
    punctuation are left out. Latin letters and their accents are neither. O(c)."""
    letters = devanagari = foreign = 0
    for char in text:
        category = unicodedata.category(char)
        if category[0] not in "LM":
            continue
        letters += 1
        if "\u0900" <= char <= "\u097f":
            devanagari += 1
        elif not (
            char.isascii() or "\u0300" <= char <= "\u036f" or unicodedata.name(char, "").startswith("LATIN")
        ):
            foreign += 1
    return (devanagari / letters, foreign / letters) if letters else (0.0, 0.0)


def _truth_scores(truth_file: Path, read: str) -> str:
    """ " cer X wer Y" against the page's truth, or " truth none" without a file.
    Both texts are cut at MAX_TRUTH_CHARS characters and MAX_TRUTH_WORDS words
    first (" truth_cut yes"), so one page costs at most MAX_TRUTH_CHARS squared."""
    try:
        truth = truth_file.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return " truth none"
    truth_words, read_words = truth.split(), read.split()
    truth_chars, read_chars = " ".join(truth_words), " ".join(read_words)
    cut = max(len(truth_chars), len(read_chars)) > MAX_TRUTH_CHARS
    cut = cut or max(len(truth_words), len(read_words)) > MAX_TRUTH_WORDS
    truth_chars, read_chars = truth_chars[:MAX_TRUTH_CHARS], read_chars[:MAX_TRUTH_CHARS]
    truth_words, read_words = truth_words[:MAX_TRUTH_WORDS], read_words[:MAX_TRUTH_WORDS]
    cer = _edits(read_chars, truth_chars) / max(1, len(truth_chars))
    wer = _edits(read_words, truth_words) / max(1, len(truth_words))
    return f" cer {cer:.3f} wer {wer:.3f}" + (" truth_cut yes" if cut else "")


def _edits(a: Sequence[str], b: Sequence[str]) -> int:
    """The Levenshtein distance: O(len(a) x len(b)) time, O(len(b)) space."""
    previous = list(range(len(b) + 1))
    for i, x in enumerate(a, start=1):
        current = [i]
        for j, y in enumerate(b, start=1):
            current.append(min(previous[j] + 1, current[j - 1] + 1, previous[j - 1] + (x != y)))
        previous = current
    return previous[-1]


def main(
    argv: Sequence[str] | None = None,
    *,
    db: Database | None = None,
    environ: Mapping[str, str] | None = None,
    out: TextIO | None = None,
    ask: Callable[[str], str] | None = getpass.getpass,
    timer: Callable[[], float] = time.perf_counter,
    model: ModelClient | None = None,
) -> int:
    """Run one command. Tests pass ``db``, ``environ``, ``out``, ``ask``, ``timer`` and ``model``."""
    args = _parser().parse_args(argv)
    env = os.environ if environ is None else environ
    stdout = sys.stdout if out is None else out
    try:
        if args.command == "measure":  # before any database is built: it never uses one
            _measure(Settings.from_env(env), args.file, args.truth, stdout, timer=timer)
            return 0
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
        elif args.command == "reembed":
            if model is None:
                model = build_model_client(Settings.from_env(env))
            if model is None:
                raise CliError("reembed needs the model: set MODEL_ENABLED=true")
            _reembed(db, model, args.customer_id, args.all, stdout)
        elif args.command in CUSTOMER_STATUS:
            line = _set_customer_status(db, args.command, args.customer_id, datetime.now(UTC))
            stdout.write(line + "\n")
        elif args.command == "reset-admin-2fa":
            user_id = _reset_admin_2fa(db, args.email)
            stdout.write(f"user {user_id}: 2FA is off; they can turn it on again in Admin, Settings\n")
        else:
            _seed_demo(db, _password(env, DEMO_ENV_VAR, None), stdout)
    except (CliError, SettingsError) as error:
        sys.stderr.write(f"error: {error}\n")
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
