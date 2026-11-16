"""Set-up commands: ``python -m kaagaz.cli <command>``.

* ``create-customer --name --admin-email --admin-name``: a new customer and its
  first admin, and a ``platform_audit`` row (``customer.create``).
* ``create-user --customer-id --email --name --role``: one more user.
* ``seed-demo``: two made-up customers for the laptop, "Sharma Traders" and
  "Mehta Textiles", each with an admin, a reviewer and a member. Safe to run
  again: a customer whose admin already exists is skipped.
* ``disable-customer --customer-id`` and ``enable-customer --customer-id``: turn
  an organisation off or on again (super admin PR 1, 0014, PROVISIONAL). While
  it is off its users can't sign in, their sessions end at the next request
  (for good: after enabling, everyone signs in again), the worker fails its
  jobs (``customer_disabled``) and no model is called for it. A customer
  being deleted is refused, and nothing changes; so is enabling a sign-up waiting for
  approval (0061: the operator console's Approve, once its e-mail is proven). Each change
  writes one audit row in that customer's log (``customer.disable``,
  ``customer.enable``). These two run as the table owner (``_owner``).
* ``purge-customer --customer-id --now``: break-glass for a legal request (super admin
  PR 4b, PROVISIONAL): an organisation whose deletion is ALREADY scheduled (the operator
  console: disabled, its name typed, a fresh code) is removed for good NOW instead of at
  the end of its grace period, by the worker's own purge (``purge_customer``). Refused
  for any other organisation. One ``platform_audit`` row (``org.purge_now``, no
  operator) before it starts, and the purge's own ``org.purged`` at the end. There is no
  "delete now" in the console.
* ``create-operator --email --name``: a platform operator (super admin, 0015,
  PROVISIONAL). The password is typed twice at a hidden prompt, never a flag or
  the environment, with the users' rules (12 to 1024 characters). Two-step is
  mandatory and its secret is issued ONLY here: it is printed once (the key and
  the otpauth link) for an authenticator app; the first valid code at sign-in
  confirms it. ``reset-operator-2fa --email`` issues a new one the same way (the
  old one stops working, every session ends). Both refuse to run without a
  terminal, so neither the password nor the secret goes to a pipe or a log.
* ``unlock-operator --email``: ends a sign-in lock. Each operator command, and
  create-customer, disable-customer and enable-customer, writes one
  ``platform_audit`` row with no operator.
* ``reset-admin-2fa --email``: turn an admin's two-step code off (PRD 1 A6), for
  the last admin of a customer, who has no other admin to do it. Their sessions
  end; they set it up again at their next sign-in. It needs no password:
  whoever runs it already has the server.
* ``measure --file PATH [--truth DIR] [--engine gemini|rapidocr]``: read one local
  file the way the worker does and print, per page, its route and why, the route
  check's numbers, and the time it took (PRD 2 X4, the council's ML 12), to set the
  thresholds and timeouts from real numbers. It touches no database, file storage or
  queue, and it does NOT run the virus scan: use it only on files you trust.
  ``--truth`` names a folder of the right text for each page (``1.txt``, ``2.txt``,
  ..., UTF-8), and adds each page's character and word error rates (CER, WER), on NFC
  text, and its value error rate (#465): of the values in the truth (``values_of``: words
  with a digit, such as amounts, dates and GSTINs), the share the read got wrong or missed,
  in any order. A page's optional ``<n>.fields.json`` (gstin, invoice_number, date,
  amounts, names: a text or a list of texts each) adds each field's exact matches, a
  truth GSTIN with a wrong check character ``truth_bad``. A ``truth`` line then adds them
  up, with the reader's tokens per page (the price sum: docs/architecture.md, "Measure true
  accuracy with a truth set").
  ``--engine`` (PRD 5 PR 1, the measurement) reads scans with that reader instead of
  OCR_ENGINE's, with every OCR_ENGINE check of the settings, and each page read by
  OCR adds who read it, its tokens (in and out, 0 from the page cache), its unsure
  and illegible words and the share of its lines pinned to a found box (G5); the
  totals add the tokens. The engines come from ``kaagaz.reading.wiring``, as the
  worker's. It prints no text and no file name: only numbers and short codes.
* ``verify-models [--model ID ...] [--embed-model ID] [--record]``:
  check PRD 5's verify items (section 14) against the live models through OpenRouter
  and the installed RapidOCR, and print ``VERIFY <id> PASS|FAIL|INFO <evidence>`` for
  each (``kaagaz.verify_models``). It touches no file storage or queue, and the
  database only to read the stored parameters (#647) and for ``--record``; it sends
  only pictures it draws itself, prints the key only as set or missing, and exits 1
  when an item FAILs. ``--model`` adds an OpenRouter model ID to check, with the items
  a model must pass before the operator can enable it (model choice, council 11 Oct
  2026). ``--record``, with exactly one ``--model``: when that model passes them all,
  its pass for the reader's prompt version and its tokens per page are written to
  ``model_catalog`` as the owner (``_owner``); the model must be in the catalogue
  (``refresh-models`` first).
  ``--chat`` (features 25-27) checks each ``--model`` for drafting answers instead, and
  ``--chat --record`` writes its pass for the answer prompt's hash the same way.
* ``refresh-models``: read OpenRouter's model lists now and refresh ``model_catalog``
  (``kaagaz.jobs.catalog``, MC1), as the worker does once a day. Needs
  OPENROUTER_API_KEY. Prints counts only.
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
  Each customer's calls are billed to it as "embed", with no user (SP1). Prints ids
  and counts only.
* ``land-facts (--count | --sample N --customer-id ID | --all | --keys-only) [--customer-id ID]
  [--max-usd USD]``: owners, shares and places of the stored land records read again (PRD 6
  D3, ``kaagaz.jobs.land_facts``). ``--count`` and ``--keys-only`` call no model; ``--sample``
  and ``--all`` need ``--max-usd``, a hard stop, and are billed as ``land``; ``--all`` only
  once the sample's cost is approved. Ids, counts and dollars only.
* ``scan-times [--customer-id ID] [--last N]``: the last N ready runs (5), one line each,
  ids and seconds only: upload to Ready, queued, the run to Ready, and its steps (preparing,
  reading, Careful, card, map, checker, from the run's config), then how long after Ready
  its vectors were made (the ``embed`` ledger row; fast scan, council 16 Oct 2026). Ready
  first (16 Oct 2026): a run ready before its card ends after it, so Ready is its start plus
  ``seconds_to_ready``, and ``understood_after_ready`` is ``seconds_after_ready``. Each
  customer is read in its own ``db.customer()``.
* ``cost-saving [--customer-id ID] [--last N]``: Cost saving over the last N ready runs (50),
  from their run config (``cost_saving_report``): the pages read, those that kept the quick
  read, those read through the words path and those read whole again, the Careful tokens
  spent (all measured), and the tokens saved (estimated: a whole-page Careful read's tokens
  less what the page spent). Per-page tokens are recorded only since 16 Oct 2026, so the
  token lines count only the runs read since, and it says how many. Numbers only.
* ``measure-compact [--days N]`` (#488, Tushar, 4 Nov 2026): the offline proof of Ultra low cost's
  compact reply, for the server. It makes no AI call: over every organisation's cached g3 page
  reads (``page_reads``, READER_PROMPT_VERSION and its "+<level>" keys) of the last N days (30),
  each read as its own organisation, each reply is encoded to the compact ``r`` and decoded
  back (``reading.compact``) and compared (``same_reply``: ignoring the lines' order, blocks as
  groupings). It prints the pages, the share decoded without a loss, the average and 95th
  percentile characters of the compact and of the original replies (compact JSON), and an output
  token estimate from the cache's own output tokens a character (an upper bound: hidden thinking
  is billed as output too). Numbers only, never a word of a page.
* ``prune-folders [--customer-id ID] [--apply]``: the empty folders Kaagaz made (auto-filing's
  department, type, property, party and year folders with nothing in them, ``folders.prune_empty``'s
  rules), listed by each customer's count; ``--apply`` deletes them and then their parents left
  empty, each audited. Folders people made are never listed. Ids and counts only.
* ``check-access`` (PRD 7, #590): works out every live folder's and live document's department
  and private folder again from the folder tree, each customer in its own ``db.customer()``, and
  prints one line for each stored value that differs, then one for each member row whose folder
  is in neither ``folders`` nor ``trashed_folders``, then how many; exit 1 when any. A folder or
  document in Trash is left out: it keeps the values it had when it went there (PRD 7 section 5),
  which its tree need not give any more. Ids only.
* ``backup-files`` and ``restore-files`` (#476, 3 Nov 2026): every stored file, every
  customer's, as one tar on stdout, read through the S3 API (``S3Storage.write_tar``), for the
  nightly backup (infra/ec2/pgdump.sh); and that tar from stdin back into an EMPTY bucket, made
  first if missing (the restore drill, infra/ec2/restore-drill.sh). The file count goes to stderr;
  a backup of no files fails (code 2).
* ``backup-upload --bucket NAME --key KEY``: stdin, already encrypted, to the off-server backup
  bucket in ap-south-1, in parts, never over an object already there, with the machine's own AWS
  role; prints its size and sha256. It reads no settings and no database, so pgdump.sh runs it
  beside the app on the host network.
* ``disk-alert --used PERCENT``: emails OPS_ALERT_EMAIL from EMAIL_FROM through SES that the
  server's disk is that full (#480; infra/ec2/disk-alert.sh decides when, once a day at most), by
  the operations alerts' one rule (kaagaz.ops_alert); an email not sent fails the command.
* ``ops-alert --subject S --text T``: the same with this subject and text (A22,
  infra/ec2/ops-alert.sh: a failed deploy or dump, an unhealthy container).
* ``person-rule on|off|status [--customer ID | --all]`` (PRD 7, #606): the per-person database
  rule's switch (``kaagaz.db.person_rule``), on the owner's own login (the migrate container).
  ``on`` refuses until the workers run as kaagaz_worker; ``off`` (the way back) never refuses.
  Both are logged and sent as an ops alert (a failed alert is said, never a failed command).
  ``status`` lists where it is on. Ids only.
* Every command that reads the database runs as the worker role (PRD 7, #606): DB_WORKER_ROLE,
  else kaagaz_worker when the login may switch into it (the owner's, migrate's), else it refuses:
  as kaagaz_app with no person it would see nothing once the rule is on.
* ``queue-depth``: one line a lane (``main`` and ``express``) with its waiting, in-flight and
  delayed job counts, read from the queue (``SqsQueue.backlog``). It touches no database, file
  storage or model. The server's idle-stop reads it to stay awake while a job is in flight or a
  retry waits. Numbers only.

Why passwords are never flags: a flag lands in the shell history and the
process list. They come from ``KAAGAZ_ADMIN_PASSWORD`` (``KAAGAZ_DEMO_PASSWORD``
for seed-demo) or a hidden prompt, and must be at least 12 characters.

Output goes to ``sys.stdout.write`` (the one place the backend writes to the
terminal instead of logging): ids and e-mails only, never a password.
"""

import argparse
import functools
import getpass
import hashlib
import json
import logging
import math
import os
import sys
import time
import unicodedata
import uuid
from collections import Counter
from collections.abc import Callable, Iterable, Iterator, Mapping, Sequence
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import IO, Any, Protocol, TextIO

import boto3
from sqlalchemy import Connection, RowMapping, text
from sqlalchemy.exc import DBAPIError, SQLAlchemyError

from kaagaz import ops_alert, verify_models
from kaagaz.adapters.email import Mailer
from kaagaz.ai.client import ModelClient, ModelOutputError, ModelUnavailable, build_model_client
from kaagaz.ai.openrouter import OpenRouterClient
from kaagaz.ai.usage import usage_scope
from kaagaz.answering.draft import ANSWER_PROMPT_HASH
from kaagaz.api import totp
from kaagaz.api.auth import ROLES, hash_password
from kaagaz.api.twostep import box
from kaagaz.db import audit, customer_purge, person_rule
from kaagaz.db import customers as customers_sql
from kaagaz.db import folders as folders_sql
from kaagaz.db import operator_models as operator_models_sql
from kaagaz.db import operators as operators_sql
from kaagaz.db import users as users_sql
from kaagaz.db.document_stories import recipe_tag as story_tag
from kaagaz.db.engine import Database, make_engine
from kaagaz.db.sqlstate import INSUFFICIENT_PRIVILEGE, sqlstate
from kaagaz.extraction.checks import GSTIN_FIELDS, gstin_valid
from kaagaz.ingestion import sniff
from kaagaz.ingestion.errors import BackendUnavailable
from kaagaz.ingestion.ports import RenditionStorage
from kaagaz.ingestion.resolve import UNSUPPORTED, resolve_kind
from kaagaz.ingestion.spool import HEAD_SIZE
from kaagaz.jobs import catalog, land_facts
from kaagaz.jobs.purge import CustomerStorage, purge_customer
from kaagaz.preparing.page_checks import BlankMeasure, measure_blank
from kaagaz.preparing.pages import PreparedPages, prepare_pages
from kaagaz.reading.compact import decode_compact, encode_compact
from kaagaz.reading.ocr import (
    PATH_PAGE,
    PATH_WORDS,
    READER_PROMPT_VERSION,
    VALUE_PUNCTUATION,
    MeteredOcrEngine,
    PageRead,
    is_word_mark,
    values_of,
)
from kaagaz.reading.rapid_pool import shared_pool
from kaagaz.reading.route import RouteDecision
from kaagaz.reading.socket import NoReader
from kaagaz.reading.spans import ReadError, Span
from kaagaz.reading.wiring import PdfPageCheck, build_ocr_engine, build_reader_socket, pdf_text_layer_checks
from kaagaz.search import indexing, story_vectors
from kaagaz.settings import Settings, SettingsError

MIN_PASSWORD = 12
MAX_PASSWORD = 1024  # the API's (routers/admin.py)
# measure --engine (PRD 5 PR 1): the readers the measurement compares, as OCR_ENGINE names them.
MEASURE_ENGINES = ("gemini", "rapidocr")
# measure --truth: each page's texts are cut to these before the edit distance (council PR 3, low 10).
MAX_TRUTH_CHARS = 5000
MAX_TRUTH_WORDS = 1000
# measure --truth (#465): the fields a page's <n>.fields.json may name.
TRUTH_FIELDS = ("gstin", "invoice_number", "date", "amounts", "names")
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
# The operator commands that print a new two-step secret: only at a terminal.
SECRET_COMMANDS = ("create-operator", "reset-operator-2fa")
# cost-saving: the limits a run's config names (``reader_config``), after "cost_saving_".
LIMIT_NAMES = ("hard_lines_max", "hard_share", "crop_pad")
# backup-upload (#476): the off-server backup bucket's region, and its upload parts: 32 MB, one
# at a time, so S3's 10,000 parts hold up to 320 GB in about 32 MB of memory.
BACKUP_REGION = "ap-south-1"
BACKUP_PART_BYTES = 32 * 1024 * 1024
# The commands that read the settings but no database.
_log = logging.getLogger(__name__)
NO_DATABASE = ("backup-files", "restore-files", "disk-alert", "ops-alert")
# #647 review: verify-models waits at most this long for the database it reads the stored
# parameters from; one that does not answer is said ``unknown``, never a failure.
VERIFY_CONNECT_SECONDS = 3


class CliError(Exception):
    """Stops the command with a short message and exit code 2."""


class FileBackup(Protocol):
    """What backup-files and restore-files need of storage (``S3Storage``)."""

    def ensure_bucket(self) -> None: ...

    def write_tar(self, out: IO[bytes]) -> int: ...

    def read_tar(self, source: IO[bytes]) -> int: ...


class CliStorage(CustomerStorage, RenditionStorage, Protocol):
    """What the commands need of file storage (``S3Storage``): purge's calls, and land facts'
    renditions of Word and PowerPoint files (#522 PR 2)."""


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

    purge = commands.add_parser("purge-customer", help="remove an organisation being deleted, NOW")
    purge.add_argument("--customer-id", required=True)
    purge.add_argument("--now", action="store_true", required=True, help="no grace period: for good")

    operator = commands.add_parser("create-operator", help="a platform operator (asks for the password)")
    operator.add_argument("--email", required=True)
    operator.add_argument("--name", required=True)
    for command in ("reset-operator-2fa", "unlock-operator"):
        commands.add_parser(command, help="change a platform operator").add_argument("--email", required=True)

    reset = commands.add_parser("reset-admin-2fa", help="turn an admin's two-step code off")
    reset.add_argument("--email", required=True)

    measure = commands.add_parser(
        "measure", help="read one trusted local file and print each page's route and time (no virus scan)"
    )
    measure.add_argument("--file", required=True, type=Path)
    measure.add_argument(
        "--truth", type=Path, help="a folder of 1.txt, 2.txt, ...: the right text of each page"
    )
    measure.add_argument(
        "--engine", choices=MEASURE_ENGINES, help="read scans with this reader (default: OCR_ENGINE)"
    )

    verify = commands.add_parser(
        "verify-models",
        help="check the models through OpenRouter, and RapidOCR (no database)",
    )
    verify.add_argument("--model", action="append", default=[], help="one more OpenRouter model ID to check")
    verify.add_argument("--embed-model", help="the embedding model to check (default: MODEL_EMBED)")
    verify.add_argument(
        "--record", action="store_true", help="write the one --model's pass to the catalogue when it passes"
    )
    verify.add_argument("--chat", action="store_true", help="check each --model for answering instead")

    commands.add_parser("refresh-models", help="refresh the model catalogue from OpenRouter now")

    reembed = commands.add_parser("reembed", help="embed pieces again after a model or recipe change")
    reembed.add_argument("--customer-id", help="one customer only (default: every customer)")
    reembed.add_argument("--all", action="store_true", help="embed every piece again, not only stale ones")

    land = commands.add_parser("land-facts", help="read owners, shares and places of land records again")
    mode = land.add_mutually_exclusive_group(required=True)
    mode.add_argument("--count", action="store_true", help="eligible documents and characters (no model)")
    mode.add_argument("--sample", type=int, metavar="N", help="read N documents of --customer-id again")
    mode.add_argument("--all", action="store_true", help="read every eligible document again (approved)")
    mode.add_argument(
        "--keys-only", action="store_true", help="key the land values already stored (no model)"
    )
    land.add_argument("--customer-id", help="one customer only (default: every customer)")
    land.add_argument("--max-usd", type=float, help="never start a document that could pass this spend")

    times = commands.add_parser("scan-times", help="how long the last documents took, step by step")
    times.add_argument("--customer-id", help="one customer only (default: every customer)")
    times.add_argument("--last", type=int, default=5, help="the last N ready runs (default 5)")

    compact = commands.add_parser(
        "measure-compact", help="the compact reply's loss and size over the cached page reads (no AI call)"
    )
    compact.add_argument("--days", type=int, default=30, help="the last N days of cached reads (default 30)")

    prune = commands.add_parser("prune-folders", help="the empty folders Kaagaz made; --apply deletes them")
    prune.add_argument("--customer-id", help="one customer only (default: every customer)")
    prune.add_argument("--apply", action="store_true", help="delete them (default: only count them)")

    commands.add_parser("check-access", help="each stored department and private folder against the tree")

    rule = commands.add_parser("person-rule", help="the per-person database rule's switch (owner only)")
    rule.add_argument("action", choices=("on", "off", "status"))
    where = rule.add_mutually_exclusive_group()
    where.add_argument("--customer", help="one organisation's id")
    where.add_argument("--all", action="store_true", help="every organisation")

    saving = commands.add_parser("cost-saving", help="what Cost saving read and saved in the last runs")
    saving.add_argument("--customer-id", "--customer", help="one customer only (default: every customer)")
    saving.add_argument("--last", type=int, default=50, help="the last N ready runs (default 50)")

    commands.add_parser("backup-files", help="every stored file as one tar on stdout (the backup)")
    commands.add_parser("restore-files", help="a backup-files tar from stdin into an empty bucket")
    upload = commands.add_parser("backup-upload", help="stdin to the off-server backup bucket")
    upload.add_argument("--bucket", required=True)
    upload.add_argument("--key", required=True)
    disk = commands.add_parser("disk-alert", help="email OPS_ALERT_EMAIL that the disk is this full")
    disk.add_argument("--used", type=int, required=True, help="the percent of the disk in use")
    alert = commands.add_parser("ops-alert", help="email OPS_ALERT_EMAIL this subject and text")
    alert.add_argument("--subject", required=True)
    alert.add_argument("--text", required=True)

    commands.add_parser("queue-depth", help="each lane's waiting, in-flight and delayed job counts")
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
    """As the owner (``_owner``), so the ``platform_audit`` row goes in the same transaction."""
    try:
        with _owner(db) as conn:
            customer_id = users_sql.create_customer(
                conn,
                name=name,
                admin_email=email,
                admin_name=admin_name,
                password_hash=hash_password(password),
            )
            operators_sql.record(
                conn,
                "customer.create",
                target_type="customer",
                target_id=customer_id,
                subject_customer_id=customer_id,
            )
    except users_sql.EmailTaken:
        raise CliError("that e-mail is already in use") from None
    return customer_id


def _operator_password(ask: Callable[[str], str] | None) -> str:
    """Typed twice at a hidden prompt; never a flag or the environment."""
    if ask is None:
        raise CliError("type the password at the prompt")
    password = ask("Password: ")
    if password != ask("Repeat password: "):
        raise CliError("the passwords do not match")
    if not MIN_PASSWORD <= len(password) <= MAX_PASSWORD:
        raise CliError(f"the password must be {MIN_PASSWORD} to {MAX_PASSWORD} characters")
    return password


def _create_operator(db: Database, key: str, email: str, name: str, password: str) -> str:
    """The operator and its first pending two-step secret, in one owner transaction."""
    email = email.strip().lower()
    if not (3 <= len(email) <= 254 and "@" in email and 1 <= len(name) <= 100):
        raise CliError("the e-mail or the name is not valid")
    try:
        with _owner(db) as conn:
            operator_id = operators_sql.create(
                conn, email=email, display_name=name, password_hash=hash_password(password)
            )
            operators_sql.record(conn, "operator.create", target_type="operator", target_id=operator_id)
            shown = _issue_secret(conn, key, operator_id, email)
    except users_sql.EmailTaken:
        raise CliError("that e-mail is already in use") from None
    return f"operator {operator_id}: created\n{shown}"


def _change_operator(db: Database, key: str, command: str, email: str) -> str:
    """reset-operator-2fa (a new pending secret) or unlock-operator, with its audit row."""
    reset = command == "reset-operator-2fa"
    with _owner(db) as conn:
        operator_id = operators_sql.change(conn, operators_sql.FIND if reset else operators_sql.UNLOCK, email)
        if operator_id is None:
            raise CliError("no operator has that e-mail")
        action = "operator.reset_totp" if reset else "operator.unlock"
        operators_sql.record(conn, action, target_type="operator", target_id=operator_id)
        shown = _issue_secret(conn, key, operator_id, email.strip().lower()) if reset else ""
    done = "new two-step secret, every session ended" if reset else "unlocked"
    return f"operator {operator_id}: {done}\n{shown}"


def _issue_secret(conn: Connection, key: str, operator_id: str, email: str) -> str:
    """Store a new secret sealed for this operator (as admins' are) and return the
    lines that show it ONCE: the key to type and the otpauth link for a QR code."""
    secret = totp.new_secret()
    operators_sql.issue_secret(conn, operator_id, box(key).seal(secret, aad=operator_id))
    return (
        f"two-step key (shown once): {totp.grouped(totp.b32(secret))}\n"
        f"authenticator link: {totp.otpauth_uri(secret, email)}\n"
        "the first code from it at sign-in confirms it\n"
    )


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
        # The spec's auth.totp_reset (#457), the same action as the admin screen's 2FA reset.
        audit.record(
            conn,
            found.customer_id,
            actor_user_id=None,
            action="auth.totp_reset",
            target_type="user",
            target_id=found.user_id,
            details={"by": "cli"},
        )
    return found.user_id


@contextmanager
def _owner(db: Database) -> Iterator[Connection]:
    """One transaction as the login role itself, with NO ``SET LOCAL ROLE``: the
    table owner, which row level security does not filter. Only for what
    ``kaagaz_app`` may not do, so no code on the customer side can: a customer's
    status (0014), create-customer's and every command's ``platform_audit`` row and
    the operator commands (0015). Every statement names its one customer or operator.
    It needs DATABASE_URL to log in as the owner: on the server only migrate has that
    login, so these commands run there (``dc run --rm migrate python -m kaagaz.cli ...``);
    in api, which logs in as ``kaagaz_api`` (#479), PostgreSQL refuses them."""
    with db.engine.begin() as conn:
        yield conn


def _set_customer_status(db: Database, command: str, customer_id: str, now: datetime) -> str:
    """Disable or enable one customer and write its audit row, in one owner
    transaction. Returns the line to print. O(1)."""
    try:
        customer_id = str(uuid.UUID(customer_id))  # one spelling in the audit row and the output
    except ValueError:
        raise CliError("the customer id is not valid") from None
    status, action, done = CUSTOMER_STATUS[command]
    with _owner(db) as conn:
        before = customers_sql.set_status(conn, customer_id, status, now=now)
        if before is None:
            raise CliError("no such customer")
        if before == customers_sql.DELETING:
            raise CliError("this customer is being deleted; nothing was changed (cancel the deletion first)")
        if before == customers_sql.PENDING:  # neither enable nor disable: Approve only (0063)
            raise CliError(
                "this organisation is waiting for approval; nothing was changed"
                " (use Approve in the operator console once its admin has confirmed their email)"
            )
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
        operators_sql.record(
            conn, action, target_type="customer", target_id=customer_id, subject_customer_id=customer_id
        )
    return f"customer {customer_id}: {done}"


def _purge_customer(db: Database, storage: CustomerStorage, customer_id: str, now: datetime) -> str:
    """End the grace period of one customer being deleted, audited, then remove it at once
    by the worker's purge. Returns the line to print."""
    try:
        customer_id = str(uuid.UUID(customer_id))
    except ValueError:
        raise CliError("the customer id is not valid") from None
    with _owner(db) as conn:
        status = customer_purge.purge_now(conn, customer_id, now=now)
        if status is None:
            raise CliError("no such customer")
        if status != customers_sql.DELETING:
            raise CliError("its deletion is not scheduled (the operator console does that); nothing changed")
        operators_sql.record(
            conn,
            "org.purge_now",
            target_type="customer",
            target_id=customer_id,
            subject_customer_id=customer_id,
        )
    try:
        families, gone = purge_customer(db, storage, customer_id, now=now, keep_going=lambda: True)
    except BackendUnavailable as error:  # review: a line, never a traceback
        raise CliError(f"{error} unavailable; the worker or a re-run will finish it") from None
    if not gone:
        return f"customer {customer_id}: not finished, document families removed: {families}; run it again"
    return f"customer {customer_id}: purged, document families removed: {families}"


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
        # SP1: billed to the customer, no user asked for it (saved even when the model fails).
        with usage_scope(db, customer_id=customer, user_id=None, kind="embed"):
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


def _land_facts(
    db: Database,
    args: argparse.Namespace,
    settings: Settings,
    model: ModelClient | None,
    storage: CliStorage | None,
    out: TextIO,
) -> None:
    """land-facts: the mode's checks, then ``kaagaz.jobs.land_facts``."""
    if args.customer_id is not None:
        try:
            uuid.UUID(args.customer_id)
        except ValueError:
            raise CliError("the customer id is not valid") from None
    customers = land_facts.customers_of(db, args.customer_id, indexing.customer_ids)
    if args.count:
        land_facts.count(db, customers, out)
        return
    if args.keys_only:
        land_facts.keys_only(db, customers, out)
        return
    if args.sample is not None and (args.customer_id is None or args.sample < 1):
        raise CliError("--sample N needs N of at least 1 and --customer-id")
    if args.max_usd is None or args.max_usd <= 0:
        raise CliError("--sample and --all need --max-usd, more than 0")
    chat = model or build_model_client(
        settings, chat_timeout_seconds=settings.model_extract_timeout_seconds, db=db
    )
    if chat is None:
        raise CliError("land-facts needs the model: set MODEL_ENABLED=true")
    if storage is None:
        from kaagaz.adapters.storage import S3Storage

        storage = S3Storage(settings)
    checker = shared_pool(settings.cpu_workers) if settings.ocr_engine == "gemini" else None
    land_facts.reread(
        db, chat, settings, customers, max_usd=args.max_usd, out=out, storage=storage, checker=checker,
        sample=args.sample,
    )  # fmt: skip


# measure-compact: one organisation's cached g3 page reads of the last days (the reply, its tokens).
_COMPACT_PAGES = text(
    "SELECT result, output_tokens FROM page_reads WHERE customer_id = :c"
    " AND (prompt_version = :v OR prompt_version LIKE :level)"
    " AND created_at >= now() - make_interval(days => :days)"
)


def _measure_compact(db: Database, days: int, out: TextIO) -> None:
    """``measure-compact`` (module notes): every organisation's cached g3 reads, each in its own
    ``db.customer()``, then the numbers (``compact_lines``). O(pages x reply size)."""
    if days < 1:
        raise CliError("--days must be at least 1")
    with db.system() as conn:
        customers = indexing.customer_ids(conn)
    sizes: list[tuple[bool, int, int, int]] = []  # (lossless, compact chars, original chars, tokens)
    params = {"v": READER_PROMPT_VERSION, "level": f"{READER_PROMPT_VERSION}+%", "days": days}
    for customer in customers:
        with db.customer(customer) as conn:
            rows = conn.execute(_COMPACT_PAGES, {**params, "c": customer}).all()
        for result, tokens in rows:
            lossless, compact = compact_round_trip(
                dict(result), encode_compact, lambda r: decode_compact(r).result
            )
            original = len(json.dumps(result, ensure_ascii=False, separators=(",", ":")))
            sizes.append((lossless, compact, original, int(tokens or 0)))
    out.write("\n".join(compact_lines(days, sizes)) + "\n")


def compact_lines(days: int, sizes: Sequence[tuple[bool, int, int, int]]) -> list[str]:
    """The printed lines: numbers only. A page that could not be encoded counts 0 compact
    characters (and is not lossless). O(n log n)."""
    lines = [
        f"measure-compact: the cached g3 page reads of the last {days} days, numbers only",
        f"pages {len(sizes)}",
    ]
    if not sizes:
        return lines
    pages = len(sizes)
    kept = sum(1 for lossless, *_ in sizes if lossless)
    compact = [size[1] for size in sizes]
    original = [size[2] for size in sizes]
    ratio = sum(size[3] for size in sizes) / max(1, sum(original))
    return [
        *lines,
        f"lossless {kept / pages:.1%} ({kept} of {pages})",
        f"characters a page: original {sum(original) / pages:.0f} average, {_p95(original)} p95;"
        f" compact {sum(compact) / pages:.0f} average, {_p95(compact)} p95"
        f" ({sum(compact) / max(1, sum(original)):.0%} of the original)",
        f"output tokens a page, at the cache's own {ratio:.2f} a character: original"
        f" {sum(original) / pages * ratio:.0f}, compact {sum(compact) / pages * ratio:.0f}",
    ]


def _p95(values: Sequence[int]) -> int:
    """The 95th percentile, nearest rank."""
    ordered = sorted(values)
    return ordered[max(0, math.ceil(0.95 * len(ordered)) - 1)]


def compact_round_trip(
    result: dict[str, Any],
    encode: Callable[[dict[str, Any]], str],
    decode: Callable[[str], dict[str, Any]],
) -> tuple[bool, int]:
    """(decoded without a loss, the compact reply's characters) of one cached reply; a reply the
    pair can't encode is (False, 0), one it can't decode back (False, its characters)."""
    try:
        compact = encode(result)
    except Exception:  # noqa: BLE001 - one page the encoder can't take is counted, never stops the run
        return False, 0
    try:
        return same_reply(result, decode(compact)), len(compact)
    except Exception:  # noqa: BLE001 - as above, for the decoder
        return False, len(compact)


def same_reply(a: Mapping[str, Any], b: Mapping[str, Any]) -> bool:
    """Two replies say the same: the page's header (scripts and issues as sets), the lines as a
    multiset ignoring their order and block numbers, the blocks as groupings of lines, and
    whether the page continues on the next. O(lines log lines)."""

    def header(reply: Mapping[str, Any]) -> tuple[Any, ...]:
        page = reply.get("page") or {}
        return (
            page.get("quality"),
            page.get("handwritten"),
            frozenset(page.get("scripts") or ()),
            frozenset(page.get("issues") or ()),
        )

    def line(one: Mapping[str, Any]) -> str:
        table = one.get("table")
        return json.dumps(
            [one.get("kind"), one.get("text"), one.get("box_2d"), sorted(one.get("unsure") or []), table],
            sort_keys=True,
            ensure_ascii=False,
        )

    def shape(reply: Mapping[str, Any]) -> tuple[Any, ...]:
        lines = list(reply.get("lines") or [])
        blocks: dict[Any, list[str]] = {}
        for one in lines:
            blocks.setdefault(one.get("block"), []).append(line(one))
        return (
            header(reply),
            Counter(line(one) for one in lines),
            Counter(tuple(sorted(members)) for members in blocks.values()),
            any(one.get("continues_on_next_page") for one in lines),
        )

    return shape(a) == shape(b)


def _prune_folders(db: Database, customer_id: str | None, apply: bool, out: TextIO) -> None:
    """prune-folders: each customer's empty folders Kaagaz made, one line a customer with any,
    in its own ``db.customer()``; with ``apply``, deleted (``folders.prune_empty``). Then a total."""
    if customer_id is None:
        with db.system() as conn:
            customers = indexing.customer_ids(conn)
    else:
        try:
            uuid.UUID(customer_id)
        except ValueError:
            raise CliError("the customer id is not valid") from None
        customers = [customer_id]
    total = 0
    for customer in customers:
        with db.customer(customer) as conn:
            empty = folders_sql.empty_made_folders(conn, customer)
            count = len(folders_sql.prune_empty(conn, customer, empty)) if apply and empty else len(empty)
        if count:
            out.write(f"customer {customer}: {count} {'deleted' if apply else 'empty'}\n")
        total += count
    verb = "deleted" if apply else "empty, not deleted (add --apply)"
    out.write(f"{total} folder{'' if total == 1 else 's'} {verb}\n")


# check-access: each live folder's department (its top-level folder when that is a department) and
# private folder (the nearest private one, itself included), walked down from the top level; then
# each live folder and live document whose stored values differ: (kind, id, stored, worked out).
_ACCESS_DIFFERENCES = text(
    "WITH RECURSIVE tree (id, department_id, private_folder_id, depth) AS ("
    " SELECT f.id, CASE WHEN f.kind = 'department' THEN f.id END, CASE WHEN f.private THEN f.id END, 1"
    " FROM folders f WHERE f.customer_id = :c AND f.parent_id IS NULL"
    " UNION ALL"
    " SELECT f.id, t.department_id, CASE WHEN f.private THEN f.id ELSE t.private_folder_id END, t.depth + 1"
    " FROM tree t JOIN folders f ON f.parent_id = t.id WHERE f.customer_id = :c AND t.depth < 101)"
    " SELECT 'folder', f.id, f.department_id, f.private_folder_id, t.department_id, t.private_folder_id"
    " FROM folders f LEFT JOIN tree t ON t.id = f.id WHERE f.customer_id = :c"
    " AND (f.department_id, f.private_folder_id) IS DISTINCT FROM (t.department_id, t.private_folder_id)"
    " UNION ALL"
    " SELECT 'document', d.id, d.department_id, d.private_folder_id, t.department_id, t.private_folder_id"
    " FROM documents d LEFT JOIN tree t ON t.id = d.folder_id"
    " WHERE d.customer_id = :c AND d.deleted_at IS NULL"
    " AND (d.department_id, d.private_folder_id) IS DISTINCT FROM (t.department_id, t.private_folder_id)"
    " ORDER BY 1, 2"
)
# Member rows whose folder is in neither table (it has no key to folders, 0084).
_ACCESS_ORPHANS = text(
    "SELECT 'department_members', m.department_id, m.user_id FROM department_members m"
    " WHERE m.customer_id = :c"
    " AND NOT EXISTS (SELECT 1 FROM folders f WHERE f.customer_id = :c AND f.id = m.department_id)"
    " AND NOT EXISTS (SELECT 1 FROM trashed_folders t WHERE t.customer_id = :c AND t.id = m.department_id)"
    " UNION ALL"
    " SELECT 'private_folder_members', m.folder_id, m.user_id FROM private_folder_members m"
    " WHERE m.customer_id = :c"
    " AND NOT EXISTS (SELECT 1 FROM folders f WHERE f.customer_id = :c AND f.id = m.folder_id)"
    " AND NOT EXISTS (SELECT 1 FROM trashed_folders t WHERE t.customer_id = :c AND t.id = m.folder_id)"
    " ORDER BY 1, 2, 3"
)


def _check_access(db: Database, out: TextIO) -> int:
    """check-access: every customer's differences (``_ACCESS_DIFFERENCES``, ``_ACCESS_ORPHANS``),
    one line each, then how many; returns how many. O(F + D) a customer for F folders and D
    documents."""
    with db.system() as conn:
        customers = indexing.customer_ids(conn)
    found = 0
    for customer in customers:
        with db.customer(customer) as conn:
            differences = conn.execute(_ACCESS_DIFFERENCES, {"c": customer}).all()
            orphans = conn.execute(_ACCESS_ORPHANS, {"c": customer}).all()
        for kind, row_id, department, private, tree_department, tree_private in differences:
            out.write(
                f"customer {customer}: {kind} {row_id} stores department {department or 'none'}"
                f" private folder {private or 'none'}; the tree gives department {tree_department or 'none'}"
                f" private folder {tree_private or 'none'}\n"
            )
        for table, folder, user in orphans:
            out.write(f"customer {customer}: {table} of user {user}: folder {folder} is in neither table\n")
        found += len(differences) + len(orphans)
    out.write(f"{found} difference{'' if found == 1 else 's'}\n")
    return found


def _scan_times(db: Database, customer_id: str | None, last: int, out: TextIO) -> None:
    """The last ``last`` ready runs (``_ready_runs``), one line each (``scan_times_line``)."""
    for row in _ready_runs(db, customer_id, last):
        out.write(scan_times_line(row) + "\n")


def _ready_runs(db: Database, customer_id: str | None, last: int) -> list[RowMapping]:
    """The last ``last`` ready runs, newest first, each customer's read in its own
    ``db.customer()`` (one query a customer)."""
    if last < 1:
        raise CliError("--last must be at least 1")
    if customer_id is None:
        with db.system() as conn:
            customers = indexing.customer_ids(conn)
    else:
        try:
            uuid.UUID(customer_id)
        except ValueError:
            raise CliError("the customer id is not valid") from None
        customers = [customer_id]
    rows: list[RowMapping] = []
    for customer in customers:
        with db.customer(customer) as conn:
            rows += conn.execute(_SCAN_TIMES, {"c": customer, "n": last}).mappings().all()
    rows.sort(key=lambda row: row["finished_at"], reverse=True)
    return rows[:last]


# scan-times: a ready run's times; indexed_at is when its embeddings' ledger row was saved
# (the embed usage scope ends when the last vector is written, after Ready).
_SCAN_TIMES = text(
    "SELECT r.document_id, d.created_at AS uploaded_at, r.started_at, r.finished_at, r.pages_ocr,"
    " r.config, (SELECT max(u.created_at) FROM model_usage u WHERE u.customer_id = r.customer_id"
    " AND u.document_id = r.document_id AND u.kind = 'embed' AND u.created_at >= r.finished_at)"
    " AS indexed_at, r.error_code, (SELECT count(*) FROM pieces p WHERE r.model_embed IS NOT NULL"
    " AND p.customer_id = r.customer_id AND p.document_id = r.document_id AND p.embedding IS NULL)"
    " AS vectors_pending"
    " FROM processing_runs r JOIN documents d ON d.customer_id = r.customer_id AND d.id = r.document_id"
    " WHERE r.customer_id = :c AND r.status = 'ready' ORDER BY r.finished_at DESC LIMIT :n"
)


def scan_times_line(row: Mapping[Any, Any]) -> str:
    """One run's line: ids and seconds only (fast scan, council 16 Oct 2026). Upload to
    Ready counts from the upload (a read-again run from its first upload); careful is "inline"
    when Careful read inside reading (the pages it read), and careful_words how many of the
    pages read again had only their hard words read (Cost saving). O(escalated pages)."""
    config = row["config"] or {}

    def seconds(key: str) -> str:
        value = config.get(key)
        return "-" if not isinstance(value, int | float) else f"{value:.1f}"

    def between(start: datetime | None, end: datetime | None) -> str:
        return "-" if start is None or end is None else f"{(end - start).total_seconds():.1f}"

    to_ready = config.get("seconds_to_ready")
    ready = (
        row["started_at"] + timedelta(seconds=to_ready)
        if isinstance(to_ready, int | float) and row["started_at"] is not None
        else row["finished_at"]
    )

    escalated = (config.get("reading") or {}).get("escalated") or []
    careful = seconds("seconds_careful") if "seconds_careful" in config else f"inline({len(escalated)} pages)"
    # Cost saving (16 Oct 2026): of those pages, how many had only their hard words read again.
    words = sum(PATH_WORDS in (page.get("reasons") or ()) for page in escalated)
    careful = f"{careful} careful_words {words}"
    return (
        f"document {row['document_id']} ocr_pages {row['pages_ocr']}"
        f" upload_to_ready {between(row['uploaded_at'], ready)}"
        f" queued {between(row['uploaded_at'], row['started_at'])}"
        f" run_to_ready {between(row['started_at'], ready)}"
        f" preparing {seconds('seconds_preparing')} reading {seconds('seconds_reading')} careful {careful}"
        f" card {seconds('seconds_card')} map {seconds('seconds_map')} checker {seconds('seconds_checker')}"
        f" understood_after_ready {seconds('seconds_after_ready')}"
        f" index_after_ready {between(ready, row['indexed_at'])}"
        f" pending {_pending(row)}"
    )


def _pending(row: Mapping[Any, Any]) -> str:
    """The post-Ready steps still to come (never stuck, 17 Oct 2026): ``vectors`` while pieces
    have none (EmbedMissing fills them), ``card`` when the model failed it; ``-`` for none."""
    steps = ["vectors"] if (row.get("vectors_pending") or 0) > 0 else []
    steps += ["card"] if str(row.get("error_code") or "").startswith("model_") else []
    return ",".join(steps) or "-"


def cost_saving_report(rows: Sequence[Mapping[Any, Any]]) -> str:
    """Cost saving (16 Oct 2026) over ``rows`` (``_ready_runs``), from each run's config.
    Measured: the pages read in the runs read with Cost saving (Dynamic), split into those
    that kept the quick read, those read through the words path and those read whole again;
    the Careful tokens they spent (the per-page tokens ``pipeline._note_tokens`` records, so
    only runs with ``kept_quick``, read since; older ones are counted apart). Estimated: the
    tokens saved, for each words-path page, and each kept page that the old rule read whole
    (``kept_quick``), a whole-page Careful read's tokens less what the page spent; that read is
    the average of the document's own whole-page Careful reads, else the page's first read
    times the run set's Careful-to-first ratio, else not enough data. O(pages)."""
    dynamic = [
        (row, reading)
        for row in rows
        if (reading := (row["config"] or {}).get("reading") or {}).get("mode") == "dynamic"
    ]
    pages = sum(row["pages_ocr"] or 0 for row, _reading in dynamic)
    escalated = [entry for _row, reading in dynamic for entry in reading.get("escalated") or []]
    words = sum(_path(entry) == PATH_WORDS for entry in escalated)
    exact = [reading for _row, reading in dynamic if "kept_quick" in reading]
    spent = _sums(entry for reading in exact for entry in [*_escalated(reading), *reading["kept_quick"]])
    # Whole-page Careful reads with tokens: each document's own, and the run set's ratio of
    # their Careful tokens to their first read's.
    own = [[entry for entry in _escalated(reading) if _path(entry) == PATH_PAGE] for reading in exact]
    whole_pages = [entry for one in own for entry in one]
    careful, first = _sums(whole_pages), _sums(whole_pages, prefix="first")
    ratio = None if 0 in first else (careful[0] / first[0], careful[1] / first[1])
    saved = {"words path": [0.0, 0.0, 0], "kept quick": [0.0, 0.0, 0]}
    unknown = 0
    for reading, whole in zip(exact, own, strict=True):
        average = None if not whole else tuple(total / len(whole) for total in _sums(whole))
        pages_saved = [("words path", entry) for entry in _escalated(reading) if _path(entry) == PATH_WORDS]
        pages_saved += [("kept quick", entry) for entry in reading["kept_quick"]]
        for kind, entry in pages_saved:
            estimate = average
            first_in, first_out = entry.get("first_in", 0), entry.get("first_out", 0)
            if estimate is None and ratio is not None and first_in + first_out:
                estimate = (first_in * ratio[0], first_out * ratio[1])
            if estimate is None:
                unknown += 1
                continue
            total = saved[kind]
            total[0] += estimate[0] - entry.get("tokens_in", 0)
            total[1] += estimate[1] - entry.get("tokens_out", 0)
            total[2] += 1
    limits = sorted(
        {
            "/".join(str((row["config"] or {}).get(f"cost_saving_{name}", "-")) for name in LIMIT_NAMES)
            for row, _reading in dynamic
        }
    )
    lines = [
        f"cost saving: last {len(rows)} ready documents, {len(dynamic)} read with Cost saving"
        f" ({len(exact)} with per-page tokens, {len(dynamic) - len(exact)} read before they were recorded)",
        f"limits seen (hard lines max/hard share/crop pad): {', '.join(limits) or 'none'}",
        f"measured  pages read                     {pages}",
        f"measured  pages that kept the quick read {pages - len(escalated)}",
        f"measured  pages read via the words path  {words}",
        f"measured  pages read whole again         {len(escalated) - words}",
        f"measured  careful tokens spent           in {spent[0]} out {spent[1]}",
    ]
    for kind, (saved_in, saved_out, count) in saved.items():
        lines.append(
            f"estimated tokens saved, {kind:<16} in {round(saved_in)} out {round(saved_out)} ({count} pages)"
        )
    total_in, total_out = (round(sum(total[i] for total in saved.values())) for i in (0, 1))
    lines.append(f"estimated tokens saved, total            in {total_in} out {total_out}")
    if unknown:
        lines.append(f"estimated tokens saved: not enough data for {unknown} pages (no whole-page read)")
    shown = "none yet" if ratio is None else f"in {ratio[0]:.2f} out {ratio[1]:.2f}"
    lines += [
        "note: a page's estimate is a whole-page Careful read's tokens (its document's own whole-page"
        f" reads' average, else its first read x the Careful/first ratio, {shown}) less what it spent",
        "note: the token lines are exact only for documents read after per-page tokens were recorded"
        " (16 Oct 2026); the page counts cover every document",
    ]
    return "\n".join(lines)


def _escalated(reading: Mapping[str, Any]) -> list[dict[str, Any]]:
    return reading.get("escalated") or []


def _path(entry: Mapping[str, Any]) -> str:
    """How an escalated page was read again: the last of its reasons."""
    reasons = entry.get("reasons") or [""]
    return str(reasons[-1])


def _sums(entries: Iterable[Mapping[str, Any]], *, prefix: str = "tokens") -> tuple[int, int]:
    """The entries' ``<prefix>_in`` and ``<prefix>_out`` added up (0 when missing)."""
    tokens_in = tokens_out = 0
    for entry in entries:
        tokens_in += entry.get(f"{prefix}_in", 0)
        tokens_out += entry.get(f"{prefix}_out", 0)
    return tokens_in, tokens_out


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


# --- the model catalogue (model choice, council 11 Oct 2026) ------------------------


def _refresh_models(db: Database, settings: Settings) -> str:
    """Refresh model_catalog from OpenRouter now (MC1). Returns the line to print."""
    if settings.openrouter_api_key is None:
        raise CliError("refresh-models needs OPENROUTER_API_KEY")
    try:
        done = catalog.refresh(db, OpenRouterClient(settings), settings, today=datetime.now(UTC).date())
    except catalog.CatalogUnavailable as error:
        raise CliError(f"OpenRouter's lists could not be read ({error.code}); nothing was changed") from None
    except BackendUnavailable as error:
        raise CliError(f"{error} unavailable; nothing was changed") from None
    return (
        f"model catalogue: {done.saved} models saved, {done.eligible} eligible,"
        f" {done.skipped} skipped (endpoints not read, left as they were)"
    )


def _stored_parameters(db: Database) -> Callable[[str], str]:
    """verify-models (#647): each model's stored parameters (``model_catalog.parameters``), read
    once a run, as the endpoints item prints them: the names, ``none``, ``not read yet`` (NULL or
    not in the catalogue) or ``unknown`` with the error's type when the database does not answer."""
    try:
        held = db.model_parameters()
    except SQLAlchemyError as error:
        said = f"unknown ({type(error).__name__})"
        return lambda model: said

    def printed(model: str) -> str:
        names = held.get(model)
        return "not read yet" if names is None else ",".join(sorted(names)) or "none"

    return printed


def _record_verified(db: Database, model: str, tokens_in: int, tokens_out: int) -> None:
    """verify-models --record: the model's pass for the reader's prompt version, as the
    owner (no app or operator role may write it), with one ``platform_audit`` row
    (``model.verified``, no operator) in the same transaction, as the other owner commands."""
    with _owner(db) as conn:
        found = operator_models_sql.record_verified(
            conn, model, prompt_version=READER_PROMPT_VERSION, tokens_in=tokens_in, tokens_out=tokens_out
        )
        if found:
            operators_sql.record(conn, "model.verified", target_type="model", target_id=model[:64])
    if not found:
        raise CliError(f"{model} is not in the model catalogue: run refresh-models first")


def _record_chat_verified(db: Database, model: str) -> None:
    """verify-models --chat --record (features 25-27): the model's pass for the answer
    prompt's hash, as ``_record_verified`` (``model.chat_verified``)."""
    with _owner(db) as conn:
        found = operator_models_sql.record_chat_verified(conn, model, prompt_hash=ANSWER_PROMPT_HASH)
        if found:
            operators_sql.record(conn, "model.chat_verified", target_type="model", target_id=model[:64])
    if not found:
        raise CliError(f"{model} is not in the model catalogue: run refresh-models first")


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
    try:  # its kind, as the worker tells it after the scan (#522)
        detected = resolve_kind(data, sniff.detect_type(data[:HEAD_SIZE]), path.name)
    except ReadError as error:
        raise CliError(f"the file cannot be read ({error.code})") from None
    if detected == UNSUPPORTED:
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
    page_reads = meter.take_page_reads() if meter is not None else {}
    pages = [(info.page, info.unit) for info in prepared.pages] or sorted(
        {(span.location.page, "cells") for span in spans}
    )
    out.write(f"type {detected} size {len(data) / (1024 * 1024):.2f} MB pages {len(pages)}\n")
    by_page: dict[int, list[Span]] = {}
    scores: Counter[str] = Counter()  # --truth: the scored pages' counts, for the totals
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
        line += _reader_numbers(page_reads.get(page))
        if len(stamps) == len(pages) + 1:
            line += f" seconds {stamps[index + 1] - stamps[index]:.2f}"
        if truth is not None:  # the read's lines; its word marks repeat their words
            read_text = " ".join(span.text for span in page_spans if not is_word_mark(span))
            line += _truth_scores(truth, page, read_text, scores)
        out.write(line + "\n")
    if truth is not None:
        out.write(_truth_totals(len(pages), scores, list(page_reads.values())) + "\n")
    ocr_pages, ocr_seconds = meter.take() if meter is not None else (0, 0.0)
    per_page = ocr_seconds / ocr_pages if ocr_pages else 0.0
    if meter is not None:
        out.write(_reader_totals(meter.name, list(page_reads.values())) + "\n")
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


def _reader_numbers(read: PageRead | None) -> str:
    """A page read by OCR (PRD 5 PR 1's measurement): who read it, its tokens, its unsure
    and illegible words and the share of its lines pinned to a found box (G5: a line
    with box_source "model" is not; a reader that finds its own boxes pins every line),
    "none" with no line. Empty for a page no OCR read. Short codes and numbers only."""
    if read is None:
        return ""
    lines = len(read.lines)
    pinned = "none" if not lines else f"{(lines - read.unpinned_lines) / lines:.2f}"
    return (
        f" read_by {read.read_by} cached {'yes' if read.cached else 'no'}"
        f" input_tokens {read.input_tokens} output_tokens {read.output_tokens}"
        f" unsure {read.unsure_words} illegible {read.illegible_words} pinned {pinned}"
    )


def _reader_totals(engine: str, reads: list[PageRead]) -> str:
    """The reader's totals over the pages it read: tokens, unsure and illegible words,
    and the share of all their lines pinned (as ``_reader_numbers``). O(pages)."""
    lines = sum(len(read.lines) for read in reads)
    unpinned = sum(read.unpinned_lines for read in reads)
    pinned = "none" if not lines else f"{(lines - unpinned) / lines:.2f}"
    return (
        f"engine {engine} pages {len(reads)} cached {sum(read.cached for read in reads)}"
        f" input_tokens {sum(read.input_tokens for read in reads)}"
        f" output_tokens {sum(read.output_tokens for read in reads)}"
        f" unsure {sum(read.unsure_words for read in reads)}"
        f" illegible {sum(read.illegible_words for read in reads)} pinned {pinned}"
    )


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


def _truth_scores(folder: Path, page: int, read: str, scores: Counter[str]) -> str:
    """ " cer X wer Y values V value_errors E ver R" against the page's truth (``<page>.txt``
    in ``folder``), then its field scores (``_field_scores``), or " truth none" without a
    file; its counts are added to ``scores``. Both texts are NFC first, so a nukta or matra
    typed another way is no error. For the CER and WER both are cut at MAX_TRUTH_CHARS
    characters and MAX_TRUTH_WORDS words (" truth_cut yes"), so one page costs at most
    MAX_TRUTH_CHARS squared. The value errors are the truth's values (``values_of``) the read
    does not have, counted as a multiset, so lines read in another order are none."""
    try:
        truth = unicodedata.normalize("NFC", (folder / f"{page}.txt").read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError):
        return " truth none"
    read = unicodedata.normalize("NFC", read)
    truth_words, read_words = truth.split(), read.split()
    wanted, values_read = values_of(truth_words), values_of(read_words)
    value_errors, values = sum((wanted - values_read).values()), sum(wanted.values())
    fields = _field_scores(folder / f"{page}.fields.json", read_words, scores)
    truth_chars, read_chars = " ".join(truth_words), " ".join(read_words)
    cut = max(len(truth_chars), len(read_chars)) > MAX_TRUTH_CHARS
    cut = cut or max(len(truth_words), len(read_words)) > MAX_TRUTH_WORDS
    truth_chars, read_chars = truth_chars[:MAX_TRUTH_CHARS], read_chars[:MAX_TRUTH_CHARS]
    truth_words, read_words = truth_words[:MAX_TRUTH_WORDS], read_words[:MAX_TRUTH_WORDS]
    char_edits, word_edits = _edits(read_chars, truth_chars), _edits(read_words, truth_words)
    scores.update(
        scored=1,
        chars=len(truth_chars),
        char_edits=char_edits,
        words=len(truth_words),
        word_edits=word_edits,
        values=values,
        value_errors=value_errors,
    )
    line = f" cer {_share(char_edits, len(truth_chars))} wer {_share(word_edits, len(truth_words))}"
    line += " truth_cut yes" if cut else ""
    return line + f" values {values} value_errors {value_errors} ver {_share(value_errors, values)}" + fields


def _share(part: int, whole: int) -> str:
    """``part / whole`` to 3 places, as the error rates print (0 of nothing is 0.000)."""
    return f"{part / max(1, whole):.3f}"


def _field_scores(fields_file: Path, read_words: list[str], scores: Counter[str]) -> str:
    """ " field_<name> ok/all" for each field (TRUTH_FIELDS order) the page's fields file
    names, a text or a list of texts: a text is ok when the read has its words in a row, as
    typed, outer punctuation stripped (VALUE_PUNCTUATION). A GSTIN field follows the GSTIN
    rule (``checks.gstin_valid``: spaces and case do not count), and is "truth_bad", and not
    counted, when a truth GSTIN fails its check character: a typo in the truth. Its counts
    are added to ``scores``. "" without a file; " fields bad" when it is not such a JSON
    object. O(the fields' words x the read's words)."""
    try:
        fields = json.loads(fields_file.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return ""
    except (OSError, ValueError):
        return " fields bad"
    if not isinstance(fields, dict) or not set(fields) <= set(TRUTH_FIELDS):
        return " fields bad"
    wanted = {name: [value] if isinstance(value, str) else value for name, value in fields.items()}
    if not all(
        isinstance(texts, list) and texts and all(isinstance(text, str) and text.strip() for text in texts)
        for texts in wanted.values()
    ):
        return " fields bad"
    read = [word.strip(VALUE_PUNCTUATION) for word in read_words]
    line = ""
    for name in (name for name in TRUTH_FIELDS if name in wanted):
        texts = [unicodedata.normalize("NFC", text) for text in wanted[name]]
        if name in GSTIN_FIELDS:
            if not all(gstin_valid(text) for text in texts):
                line += f" field_{name} truth_bad"
                continue
            found = {word.upper() for word in read}
            ok = sum("".join(text.split()).upper() in found for text in texts)
        else:
            ok = sum(
                _in_a_row([word.strip(VALUE_PUNCTUATION) for word in text.split()], read) for text in texts
            )
        scores[f"field_{name}_ok"] += ok
        scores[f"field_{name}_all"] += len(texts)
        line += f" field_{name} {ok}/{len(texts)}"
    return line


def _in_a_row(words: list[str], read: list[str]) -> bool:
    """Whether ``read`` has ``words`` one after the other. O(len(words) x len(read))."""
    return any(read[start : start + len(words)] == words for start in range(len(read) - len(words) + 1))


def _truth_totals(pages: int, scores: Counter[str], reads: list[PageRead]) -> str:
    """The ``truth`` line: the scored pages' counts and rates added up (``_truth_scores``),
    each field's exact matches, and the reader's tokens over the file's ``pages`` and per
    page. Numbers only, each after its name, so the lines of several files add up."""
    tokens_in = sum(read.input_tokens for read in reads)
    tokens_out = sum(read.output_tokens for read in reads)
    line = (
        f"truth pages {pages} scored {scores['scored']}"
        f" chars {scores['chars']} char_edits {scores['char_edits']}"
        f" cer {_share(scores['char_edits'], scores['chars'])}"
        f" words {scores['words']} word_edits {scores['word_edits']}"
        f" wer {_share(scores['word_edits'], scores['words'])}"
        f" values {scores['values']} value_errors {scores['value_errors']}"
        f" ver {_share(scores['value_errors'], scores['values'])}"
    )
    for name in TRUTH_FIELDS:
        if f"field_{name}_all" in scores:
            line += f" field_{name} {scores[f'field_{name}_ok']}/{scores[f'field_{name}_all']}"
    return line + (
        f" input_tokens {tokens_in} output_tokens {tokens_out}"
        f" input_tokens_per_page {tokens_in / max(1, pages):.0f}"
        f" output_tokens_per_page {tokens_out / max(1, pages):.0f}"
    )


def _edits(a: Sequence[str], b: Sequence[str]) -> int:
    """The Levenshtein distance: O(len(a) x len(b)) time, O(len(b)) space."""
    previous = list(range(len(b) + 1))
    for i, x in enumerate(a, start=1):
        current = [i]
        for j, y in enumerate(b, start=1):
            current.append(min(previous[j] + 1, current[j - 1] + 1, previous[j - 1] + (x != y)))
        previous = current
    return previous[-1]


def _backup_upload(client: Any, bucket: str, key: str, source: IO[bytes]) -> str:
    """``source`` to ``bucket``/``key`` as it is read (it need not fit on the disk), never over
    an object already there: If-None-Match, which the bucket's policy requires (boto3's managed
    upload cannot send it). Returns its size and sha256, done's line for the part (pgdump.sh)."""
    target = {"Bucket": bucket, "Key": key}
    chunk = source.read(BACKUP_PART_BYTES)
    if len(chunk) < BACKUP_PART_BYTES:  # all of it in one request
        client.put_object(Body=chunk, IfNoneMatch="*", **target)
        return f"{len(chunk)} {hashlib.sha256(chunk).hexdigest()}"
    digest, size = hashlib.sha256(), 0
    parts: list[dict[str, Any]] = []
    # CRC32 on every part, as boto3's own upload does (Object Lock needs a checksum).
    upload_id = client.create_multipart_upload(ChecksumAlgorithm="CRC32", **target)["UploadId"]
    try:
        while chunk:
            digest.update(chunk)
            size += len(chunk)
            number = len(parts) + 1
            sent = client.upload_part(
                Body=chunk, PartNumber=number, UploadId=upload_id, ChecksumAlgorithm="CRC32", **target
            )
            parts.append({"PartNumber": number, "ETag": sent["ETag"], "ChecksumCRC32": sent["ChecksumCRC32"]})
            chunk = source.read(BACKUP_PART_BYTES)
        client.complete_multipart_upload(
            UploadId=upload_id, MultipartUpload={"Parts": parts}, IfNoneMatch="*", **target
        )
    except BaseException:
        client.abort_multipart_upload(UploadId=upload_id, **target)
        raise
    return f"{size} {digest.hexdigest()}"


def _disk_alert(settings: Settings, mailer: Mailer | None, used: int) -> None:
    _alert(
        settings,
        mailer,
        f"Kaagaz server: the disk is {used}% full",
        f"The Kaagaz server's disk is {used}% full. This alert is sent at most once a day.\n\n"
        "Free some space before it fills, or the database and the uploads stop. "
        "`docker system df` on the server shows what uses it.\n",
        purpose="disk_alert",
    )


def _cli_role(engine: Any, settings: Settings) -> str:
    """PRD 7 (#606): the worker role (module notes), or CliError."""
    if settings.db_worker_role != settings.db_app_role:
        return settings.db_worker_role
    with engine.connect() as conn:
        reaches = conn.execute(
            text("SELECT pg_has_role(session_user, :w, 'MEMBER')"), {"w": person_rule.WORKER_ROLE}
        ).scalar()
    engine.dispose()  # a command that never reads the database leaves no open connection behind
    if not reaches:
        raise CliError(
            f"the CLI runs as {person_rule.WORKER_ROLE}, which this login can't switch into: run it in the"
            " migrate container (as kaagaz_app with no person it would see nothing under the database rule)"
        )
    return person_rule.WORKER_ROLE


def _person_rule(db: Database, args: argparse.Namespace, settings: Settings, mailer: Mailer | None) -> str:
    """person-rule (module notes), on the login itself (the owner's: nothing else may touch the
    switch). Its line, and for on and off one log line and an ops alert, best effort."""
    if args.action != "status" and not (args.all or args.customer):
        raise CliError("say --customer ID or --all")
    customer = None if args.all else args.customer
    if customer is not None:
        try:
            uuid.UUID(customer)
        except ValueError:
            raise CliError("no such organisation") from None
    try:
        with db.engine.begin() as conn:
            if args.action == "status":
                rows = person_rule.status(conn)
                said = [f"on for {c or 'every organisation'} since {at} by {by}" for c, at, by in rows]
                return "\n".join(said) or "off"
            if customer is not None and not person_rule.is_customer(conn, customer):
                raise CliError("no such organisation")
            if args.action == "on":
                person_rule.turn_on(conn, customer, getpass.getuser())
            else:
                gone = person_rule.turn_off(conn, customer)
                # Off for one organisation is no way back while the rule is on for every one.
                if customer is not None and person_rule.on_everywhere(conn):
                    raise CliError("still on for every organisation: use off --all")
                if customer is not None and not gone:
                    raise CliError("it was not on for that organisation")
    except person_rule.Refused as refused:
        raise CliError(f"person-rule on refused: {refused}") from None
    except DBAPIError as error:
        if sqlstate(error) == INSUFFICIENT_PRIVILEGE:
            raise CliError("person-rule needs the owner's login: run it in the migrate container") from None
        raise
    line = f"person rule {args.action} for {customer or 'every organisation'}"
    _log.warning(line, extra={"code": "person_rule"})
    missing = ops_alert.missing(settings)
    if missing is not None:
        return f"{line} (no ops alert: {missing})"
    sent = ops_alert.send_ops_alert(
        settings, f"Kaagaz: {line}", line + "\n", purpose="person_rule", mailer=mailer
    )
    return line if sent else f"{line} (the ops alert was not sent: the log says why)"


def _alert(settings: Settings, mailer: Mailer | None, subject: str, text: str, *, purpose: str) -> None:
    problem = ops_alert.missing(settings)
    if problem is not None:
        raise CliError(problem)
    sent = ops_alert.send_ops_alert(settings, subject, text, purpose=purpose, mailer=mailer)
    if not sent:  # a failed command: the script notes no day and tries again next time
        raise CliError("the alert was not sent: the log says why")


def _file_backup(command: str, files: FileBackup, source: IO[bytes], out: IO[bytes]) -> str:
    """backup-files or restore-files; the line for stderr (stdout may be the tar)."""
    if command == "backup-files":
        count = files.write_tar(out)
        if count == 0:  # a storage that reads as empty is broken, not backed up (#476)
            raise CliError("backup-files: the storage bucket gave no files, so nothing was backed up")
        return f"backup-files: {count} files"
    files.ensure_bucket()
    try:
        return f"restore-files: {files.read_tar(source)} files"
    except ValueError as error:
        raise CliError(str(error)) from None


def main(
    argv: Sequence[str] | None = None,
    *,
    db: Database | None = None,
    environ: Mapping[str, str] | None = None,
    out: TextIO | None = None,
    ask: Callable[[str], str] | None = getpass.getpass,
    timer: Callable[[], float] = time.perf_counter,
    model: ModelClient | None = None,
    settings: Settings | None = None,
    storage: CliStorage | None = None,
    file_store: FileBackup | None = None,
    binary_in: IO[bytes] | None = None,
    binary_out: IO[bytes] | None = None,
    backup_bucket: Any = None,
    mailer: Mailer | None = None,
    queue: Any = None,
) -> int:
    """Run one command. Tests pass ``db``, ``environ``, ``out``, ``ask``, ``timer``,
    ``model``, ``settings``, ``storage``, ``file_store``, ``binary_in`` and ``binary_out``
    (stdin and stdout as bytes), ``backup_bucket`` (its S3 client), ``mailer`` and ``queue``."""
    args = _parser().parse_args(argv)
    env = os.environ if environ is None else environ
    stdout = sys.stdout if out is None else out
    try:
        if args.command in SECRET_COMMANDS and ask is getpass.getpass and not sys.stdin.isatty():
            raise CliError(
                "run it at a terminal (docker compose run --rm migrate, WITHOUT -T): it shows a secret"
            )
        if args.command == "measure":  # before any database is built: it never uses one
            chosen = env if args.engine is None else {**env, "OCR_ENGINE": args.engine}
            _measure(Settings.from_env(chosen), args.file, args.truth, stdout, timer=timer)
            return 0
        if args.command == "verify-models":  # no database either, but --record's one row
            checked = settings or Settings.from_env(env)
            record = record_chat = None
            if args.record and len(set(args.model)) != 1:
                raise CliError("--record checks exactly one --model")
            # and the stored parameters its endpoints item prints (#647), as the worker role (PRD 7, #606)
            owner = db or Database(
                make_engine(checked.database_url, connect_timeout_seconds=VERIFY_CONNECT_SECONDS),
                checked.db_worker_role,
            )
            if args.record:
                if args.chat:
                    record_chat = functools.partial(_record_chat_verified, owner)
                else:
                    record = functools.partial(_record_verified, owner)
            return verify_models.run(
                checked,
                stdout,
                models=args.model,
                embed_model=args.embed_model,
                record=record,
                chat=args.chat,
                record_chat=record_chat,
                stored=_stored_parameters(owner),
            )
        if args.command == "queue-depth":  # the queue only, no database
            from kaagaz.adapters.queue import SqsQueue

            counts = (queue or SqsQueue(settings or Settings.from_env(env))).backlog()
            for lane in ("main", "express"):
                waiting, in_flight, delayed = counts[lane]
                stdout.write(f"{lane} {waiting} {in_flight} {delayed}\n")
            return 0
        if args.command == "backup-upload":  # no settings either: it runs on the host network
            source = sys.stdin.buffer if binary_in is None else binary_in
            # No keys: boto3 finds the machine's own role.
            client = backup_bucket or boto3.client("s3", region_name=BACKUP_REGION)
            stdout.write(_backup_upload(client, args.bucket, args.key, source) + "\n")
            return 0
        if args.command in NO_DATABASE:
            checked = settings or Settings.from_env(env)
            if args.command == "disk-alert":
                _disk_alert(checked, mailer, args.used)
                stdout.write("disk alert sent\n")  # never the address
                return 0
            if args.command == "ops-alert":
                _alert(checked, mailer, args.subject, args.text, purpose="server_alert")
                stdout.write("alert sent\n")
                return 0
            if file_store is None:
                from kaagaz.adapters.storage import S3Storage

                file_store = S3Storage(checked)
            source = sys.stdin.buffer if binary_in is None else binary_in
            line = _file_backup(args.command, file_store, source, binary_out or sys.stdout.buffer)
            sys.stderr.write(line + "\n")
            return 0
        if db is None:
            settings = settings or Settings.from_env(env)
            engine = make_engine(settings.database_url)
            db = Database(engine, _cli_role(engine, settings))
        if args.command == "person-rule":
            stdout.write(_person_rule(db, args, settings or Settings.from_env(env), mailer) + "\n")
        elif args.command == "refresh-models":
            stdout.write(_refresh_models(db, settings or Settings.from_env(env)) + "\n")
        elif args.command == "create-customer":
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
        elif args.command == "land-facts":
            _land_facts(db, args, settings or Settings.from_env(env), model, storage, stdout)
        elif args.command == "scan-times":
            _scan_times(db, args.customer_id, args.last, stdout)
        elif args.command == "measure-compact":  # no model: it reads the cache and prints numbers
            _measure_compact(db, args.days, stdout)
        elif args.command == "prune-folders":
            _prune_folders(db, args.customer_id, args.apply, stdout)
        elif args.command == "check-access":
            if _check_access(db, stdout):
                return 1
        elif args.command == "cost-saving":
            stdout.write(cost_saving_report(_ready_runs(db, args.customer_id, args.last)) + "\n")
        elif args.command in CUSTOMER_STATUS:
            line = _set_customer_status(db, args.command, args.customer_id, datetime.now(UTC))
            stdout.write(line + "\n")
        elif args.command == "purge-customer":
            if storage is None:
                from kaagaz.adapters.storage import S3Storage

                storage = S3Storage(settings or Settings.from_env(env))
            line = _purge_customer(db, storage, args.customer_id, datetime.now(UTC))
            stdout.write(line + "\n")
        elif args.command == "create-operator":
            password = _operator_password(ask)
            key = (settings or Settings.from_env(env)).totp_encryption_key
            stdout.write(_create_operator(db, key, args.email, args.name, password))
        elif args.command in ("reset-operator-2fa", "unlock-operator"):
            reset = args.command == "reset-operator-2fa"
            key = (settings or Settings.from_env(env)).totp_encryption_key if reset else ""
            stdout.write(_change_operator(db, key, args.command, args.email))
        elif args.command == "reset-admin-2fa":
            user_id = _reset_admin_2fa(db, args.email)
            stdout.write(f"user {user_id}: 2FA is off; they set it up again at their next sign-in\n")
        else:
            _seed_demo(db, _password(env, DEMO_ENV_VAR, None), stdout)
    except (CliError, SettingsError) as error:
        sys.stderr.write(f"error: {error}\n")
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
