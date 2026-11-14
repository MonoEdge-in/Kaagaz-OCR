"""PROVISIONAL NUMBER: renumbered to follow PRD 7's last migration before raising.

Kaagaz on WhatsApp: the tables, their row level security and the owner-rights functions (PRD 7
section 15, W1, #603, Tushar, 14 Nov 2026). NEEDS VRUSHIT'S APPROVAL: schema and security.

* ``whatsapp_org_settings``: the organisation Admin's switches, one row an organisation (no row:
  off). WhatsApp itself, the roles that may connect (members and reviewers by default; Admins
  need the role ticked), Management (an extra tick), voice notes (on) and uploads (off).
* ``whatsapp_links``: a person's connected number, sealed (``api/secret_box.py``) with a keyed
  hash to find it by, the person's sign-in version when it was connected (any change ends it),
  when it was last used, up to 3 recent folders, and the channel's lease (``busy_until``). One
  live connection per person and one per number across every organisation (two unique indexes on
  the live rows). An ended one keeps who, when and why, and loses its number at once.
* ``whatsapp_link_codes``: a connect code, hashed, bound to its person and number, with its expiry
  and tries (5 at most).
* ``whatsapp_messages``: each message WhatsApp delivered, once (its message id is unique in the
  organisation): its kind, text or media id until it is handled, Meta's time, its state and the
  reply's time; a batch and the document an upload made. ``own_user_only`` as well (0008): only
  the person it came from reads it.
* ``whatsapp_pickers``: a person's folder picker, kept 15 minutes (folder ids only).
* ``conversations.channel``: ``whatsapp`` for a conversation started there, NULL for the browser.

Every new table has ``customer_id`` first in its key, ``customer_only``, a comment on it and on
each column, and the same rights for ``kaagaz_app`` and ``kaagaz_worker`` (0083's equality test).
A user is never deleted, so their keys are NO ACTION and ``kaagaz_finish_customer_delete``
(0084's, with the WhatsApp tables added) deletes the rows by name.

The owner-rights functions (SECURITY DEFINER, the empty search path, every table named in full,
revoked from PUBLIC, for the two roles) read or change only what their names say:

* ``kaagaz_whatsapp_lookup(hash)``: a message arrives with no organisation; the live connection of
  that number, ids only.
* ``kaagaz_whatsapp_claim(limit, lease)``: the channel's queue. Takes up to ``limit`` connections
  with a waiting message whose lease is free (oldest waiting first, SKIP LOCKED), leases each for
  ``lease`` seconds and returns ids only, so one person's messages are handled one at a time and
  different people's side by side.
* ``kaagaz_whatsapp_end_number(hash, reason)``: WhatsApp says the number changed hands, or it is
  connected elsewhere: every live connection of it ends, in any organisation, each audited there.
* ``kaagaz_whatsapp_end_link(link, reason)``: one live connection of the current organisation ends
  (STOP, Not me, an Admin, or a check that failed), audited. The app has no right to change a
  connection's end or number itself, so an ended one always loses its number and is always audited.
* ``kaagaz_whatsapp_sweep()``: ends the live connections no longer allowed (the person disabled or
  their organisation not active, their sign-in version changed, WhatsApp or their role switched
  off) or silent for 60 days, each audited; deletes messages after 30 days, ended connections
  after 30 days, codes after a day and pickers once expired. Returns how many rows it changed.

Each argument is checked: a limit, lease or reason out of range raises ``check_violation``.

New tables only, but ``conversations``: a NULL column (catalog only) with its CHECK, which reads
the table once. The downgrade drops it all (every connection and message is lost) and puts back
0084's finish.

Revision ID: 0088
Revises: 0087
"""

import importlib.util
from pathlib import Path
from types import ModuleType

from alembic import op

revision = "0088"
down_revision = "0087"
branch_labels = None
depends_on = None


def _load(name: str) -> ModuleType:
    path = Path(__file__).with_name(name)
    spec = importlib.util.spec_from_file_location(f"migration_{path.stem}_for_whatsapp", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_0047 = _load("0047_purge_organization_words.py")
_0084 = _load("0084_access_tables.py")
_literal = _0084._literal
ROLES = ("kaagaz_app", "kaagaz_worker")
POLICY = "customer_id = kaagaz_current_customer()"
OWN_USER = "user_id = NULLIF(current_setting('kaagaz.user_id', true), '')::uuid"  # 0008's
TABLES = (
    "whatsapp_org_settings",
    "whatsapp_links",
    "whatsapp_link_codes",
    "whatsapp_messages",
    "whatsapp_pickers",
)
# Why a connection ended (whatsapp_links.end_reason).
END_REASONS = (
    "person",  # STOP, or Disconnect in the chat or in Kaagaz
    "not_me",  # Not me, on the welcome
    "admin",  # an Admin disconnected them
    "account",  # the person disabled, or their organisation not active
    "session",  # their sign-in version changed (password, reset, sign out everywhere)
    "not_allowed",  # WhatsApp, or their role, switched off for the organisation
    "number_changed",  # WhatsApp says the number's owner changed
    "replaced",  # the number was connected to another account
    "silent",  # 60 days with no message
)
_REASONS = ", ".join(f"'{reason}'" for reason in END_REASONS)
SILENT_DAYS = 60
KEEP_DAYS = 30  # messages (for de-duplication) and ended connections
ROLE_NAMES = "ARRAY['member', 'reviewer', 'admin']::text[]"  # users.role (0001)

CREATE_TABLES = (
    f"""
    CREATE TABLE whatsapp_org_settings (
        customer_id uuid PRIMARY KEY REFERENCES customers (id) ON DELETE CASCADE,
        enabled boolean NOT NULL DEFAULT false,
        roles text[] NOT NULL DEFAULT ARRAY['member', 'reviewer']::text[] CHECK (roles <@ {ROLE_NAMES}),
        management boolean NOT NULL DEFAULT false,
        voice boolean NOT NULL DEFAULT true,
        uploads boolean NOT NULL DEFAULT false
    )
    """,
    f"""
    CREATE TABLE whatsapp_links (
        customer_id uuid NOT NULL REFERENCES customers (id) ON DELETE CASCADE,
        id uuid NOT NULL DEFAULT gen_random_uuid(),
        user_id uuid NOT NULL,
        phone_hash bytea NULL CHECK (octet_length(phone_hash) = 32),
        phone_sealed text NULL CHECK (length(phone_sealed) BETWEEN 1 AND 200),
        session_version integer NOT NULL CHECK (session_version >= 1),
        linked_at timestamptz NOT NULL DEFAULT now(),
        last_used_at timestamptz NULL,
        recent_folder_ids uuid[] NOT NULL DEFAULT '{{}}' CHECK (cardinality(recent_folder_ids) <= 3),
        busy_until timestamptz NULL,
        ended_at timestamptz NULL,
        end_reason text NULL CHECK (end_reason IN ({_REASONS})),
        PRIMARY KEY (customer_id, id),
        FOREIGN KEY (customer_id, user_id) REFERENCES users (customer_id, id),
        CONSTRAINT whatsapp_links_ended CHECK ((ended_at IS NULL) = (end_reason IS NULL)),
        CONSTRAINT whatsapp_links_number CHECK (
            (ended_at IS NULL) = (phone_hash IS NOT NULL) AND (phone_hash IS NULL) = (phone_sealed IS NULL)
        )
    )
    """,
    """
    CREATE TABLE whatsapp_link_codes (
        customer_id uuid NOT NULL REFERENCES customers (id) ON DELETE CASCADE,
        id uuid NOT NULL DEFAULT gen_random_uuid(),
        user_id uuid NOT NULL,
        phone_hash bytea NOT NULL CHECK (octet_length(phone_hash) = 32),
        phone_sealed text NOT NULL CHECK (length(phone_sealed) BETWEEN 1 AND 200),
        code_hash bytea NOT NULL CHECK (octet_length(code_hash) = 32),
        created_at timestamptz NOT NULL DEFAULT now(),
        expires_at timestamptz NOT NULL,
        tries smallint NOT NULL DEFAULT 0 CHECK (tries BETWEEN 0 AND 5),
        used_at timestamptz NULL,
        PRIMARY KEY (customer_id, id),
        FOREIGN KEY (customer_id, user_id) REFERENCES users (customer_id, id),
        CHECK (expires_at > created_at)
    )
    """,
    """
    CREATE TABLE whatsapp_messages (
        customer_id uuid NOT NULL REFERENCES customers (id) ON DELETE CASCADE,
        id uuid NOT NULL DEFAULT gen_random_uuid(),
        link_id uuid NOT NULL,
        user_id uuid NOT NULL,
        wamid text NOT NULL CHECK (length(wamid) BETWEEN 1 AND 128),
        kind text NOT NULL
            CHECK (kind IN ('text', 'interactive', 'button', 'image', 'document', 'audio', 'video', 'other')),
        body text NULL CHECK (length(body) <= 4096),
        media_id text NULL CHECK (length(media_id) BETWEEN 1 AND 128),
        forwarded boolean NOT NULL DEFAULT false,
        sent_at timestamptz NOT NULL,
        received_at timestamptz NOT NULL DEFAULT now(),
        state text NOT NULL DEFAULT 'waiting' CHECK (state IN ('waiting', 'done', 'expired', 'failed')),
        tries smallint NOT NULL DEFAULT 0 CHECK (tries >= 0),
        batch_id uuid NULL,
        document_id uuid NULL,
        reply_sent_at timestamptz NULL,
        PRIMARY KEY (customer_id, id),
        UNIQUE (customer_id, wamid),
        FOREIGN KEY (customer_id, link_id) REFERENCES whatsapp_links (customer_id, id) ON DELETE CASCADE,
        FOREIGN KEY (customer_id, user_id) REFERENCES users (customer_id, id),
        FOREIGN KEY (customer_id, document_id) REFERENCES documents (customer_id, id)
            ON DELETE SET NULL (document_id)
    )
    """,
    """
    CREATE TABLE whatsapp_pickers (
        customer_id uuid NOT NULL REFERENCES customers (id) ON DELETE CASCADE,
        user_id uuid NOT NULL,
        generation integer NOT NULL DEFAULT 1 CHECK (generation >= 1),
        state jsonb NOT NULL DEFAULT '{}'::jsonb,
        expires_at timestamptz NOT NULL,
        PRIMARY KEY (customer_id, user_id),
        FOREIGN KEY (customer_id, user_id) REFERENCES users (customer_id, id)
    )
    """,
)
INDEXES = (
    # One live connection per number across every organisation, and per person.
    "CREATE UNIQUE INDEX whatsapp_links_live_number ON whatsapp_links (phone_hash) WHERE ended_at IS NULL",
    "CREATE UNIQUE INDEX whatsapp_links_live_person ON whatsapp_links (customer_id, user_id)"
    " WHERE ended_at IS NULL",
    # A person's codes in the last hour, and a number's in the last day (the limits).
    "CREATE INDEX whatsapp_link_codes_person ON whatsapp_link_codes (customer_id, user_id, created_at)",
    "CREATE INDEX whatsapp_link_codes_number ON whatsapp_link_codes (phone_hash, created_at)",
    # The channel's next message of a connection, oldest first; the sweep's old ones.
    "CREATE INDEX whatsapp_messages_waiting ON whatsapp_messages (customer_id, link_id, sent_at, received_at)"
    " WHERE state = 'waiting'",
    "CREATE INDEX whatsapp_messages_received ON whatsapp_messages (received_at)",
    "CREATE INDEX whatsapp_messages_user ON whatsapp_messages (customer_id, user_id)",
)
# What the app may write beyond SELECT and INSERT (column grants): never a connection's person,
# number or sign-in version, a code's number or hash, or a message's id, kind or times.
GRANTS = {
    "whatsapp_org_settings": ("SELECT, INSERT", "UPDATE (enabled, roles, management, voice, uploads)"),
    "whatsapp_links": ("SELECT, INSERT", "UPDATE (last_used_at, recent_folder_ids, busy_until)"),
    "whatsapp_link_codes": ("SELECT, INSERT", "UPDATE (tries, used_at)"),
    "whatsapp_messages": (
        "SELECT, INSERT",
        "UPDATE (state, tries, body, batch_id, document_id, reply_sent_at)",
    ),
    "whatsapp_pickers": ("SELECT, INSERT, DELETE", "UPDATE (generation, state, expires_at)"),
}

# Ends the live connections ``{which}`` picks (alias l, joined ``{using}``) for ``{why}``, its number
# gone at once, and writes one audit row each in its organisation (the person its subject,
# ``{actor}`` its actor); counts them. The CTEs of one statement, after its WITH.
_END = """
        ended AS (
            UPDATE public.whatsapp_links l
            SET ended_at = now(), end_reason = {why}, phone_hash = NULL, phone_sealed = NULL, busy_until = NULL
            {using}WHERE l.ended_at IS NULL AND {which}
            RETURNING l.customer_id, l.id, l.user_id, l.end_reason
        ), audited AS (
            INSERT INTO public.audit_log (customer_id, actor_user_id, action, target_type, target_id, details,
                                          subject_user_id)
            SELECT e.customer_id, {actor}, 'whatsapp.disconnected', 'user', e.user_id::text,
                   jsonb_build_object('reason', e.end_reason, 'link_id', e.id), e.user_id
            FROM ended e
            RETURNING 1
        )
        SELECT count(*) FROM audited"""
# The reasons the app may end one connection of its own organisation for (the rest are the
# functions' own), and who did it: the person in the transaction when they or an Admin did.
APP_REASONS = ("person", "not_me", "admin", "account", "session", "not_allowed")
_APP_REASONS = ", ".join(f"'{reason}'" for reason in APP_REASONS)
_ACTOR = (
    "CASE WHEN p_reason IN ('person', 'not_me', 'admin')"
    " THEN NULLIF(current_setting('kaagaz.user_id', true), '')::uuid END"
)
_END_LINK = _END.format(
    which="l.customer_id = public.kaagaz_current_customer() AND l.id = p_link_id",
    why="p_reason",
    using="",
    actor=_ACTOR,
)
_END_SWEPT = _END.format(
    which="l.customer_id = w.customer_id AND l.id = w.id AND w.reason IS NOT NULL",
    why="w.reason",
    using="FROM why w\n            ",
    actor="NULL::uuid",
)
# Why a live connection is no longer allowed, for the sweep (each one reason, in this order).
_NOT_ALLOWED = f"""
            CASE
                WHEN u.disabled OR c.status <> 'active' THEN 'account'
                WHEN u.session_version <> l.session_version THEN 'session'
                WHEN NOT coalesce(s.enabled, false) OR NOT u.role = ANY (s.roles)
                     OR (u.management AND NOT s.management) THEN 'not_allowed'
                WHEN coalesce(l.last_used_at, l.linked_at) < now() - interval '{SILENT_DAYS} days' THEN 'silent'
            END"""

FUNCTIONS = {
    "kaagaz_whatsapp_lookup(bytea)": """
    CREATE FUNCTION public.kaagaz_whatsapp_lookup(p_phone_hash bytea)
    RETURNS TABLE (customer_id uuid, user_id uuid, link_id uuid)
    LANGUAGE sql STABLE SECURITY DEFINER SET search_path = '' AS $$
        SELECT l.customer_id, l.user_id, l.id FROM public.whatsapp_links l
        WHERE l.phone_hash = p_phone_hash AND l.ended_at IS NULL
    $$
    """,
    "kaagaz_whatsapp_claim(integer, integer)": """
    CREATE FUNCTION public.kaagaz_whatsapp_claim(p_limit integer, p_lease_seconds integer)
    RETURNS TABLE (customer_id uuid, user_id uuid, link_id uuid)
    LANGUAGE plpgsql SECURITY DEFINER SET search_path = '' AS $$
    #variable_conflict use_column
    BEGIN
        IF p_limit IS NULL OR p_limit NOT BETWEEN 1 AND 100
           OR p_lease_seconds IS NULL OR p_lease_seconds NOT BETWEEN 1 AND 900 THEN
            RAISE EXCEPTION 'limit or lease out of range' USING ERRCODE = 'check_violation';
        END IF;
        RETURN QUERY
        WITH due AS (
            SELECT k.customer_id, k.id FROM public.whatsapp_links k
            WHERE (k.busy_until IS NULL OR k.busy_until <= now())
              AND EXISTS (SELECT 1 FROM public.whatsapp_messages m
                          WHERE m.customer_id = k.customer_id AND m.link_id = k.id AND m.state = 'waiting')
            ORDER BY (SELECT min(m.received_at) FROM public.whatsapp_messages m
                      WHERE m.customer_id = k.customer_id AND m.link_id = k.id AND m.state = 'waiting')
            LIMIT p_limit
            FOR UPDATE OF k SKIP LOCKED
        )
        UPDATE public.whatsapp_links l SET busy_until = now() + make_interval(secs => p_lease_seconds)
        FROM due WHERE l.customer_id = due.customer_id AND l.id = due.id
        RETURNING l.customer_id, l.user_id, l.id;
    END
    $$
    """,
    "kaagaz_whatsapp_end_number(bytea, text)": f"""
    CREATE FUNCTION public.kaagaz_whatsapp_end_number(p_phone_hash bytea, p_reason text) RETURNS bigint
    LANGUAGE plpgsql SECURITY DEFINER SET search_path = '' AS $$
    DECLARE
        v_ended bigint;
    BEGIN
        IF p_phone_hash IS NULL OR p_reason IS NULL OR p_reason NOT IN ('number_changed', 'replaced') THEN
            RAISE EXCEPTION 'unknown number or reason' USING ERRCODE = 'check_violation';
        END IF;
        WITH {_END.format(which="l.phone_hash = p_phone_hash", why="p_reason", using="", actor="NULL::uuid")}
        INTO v_ended;
        RETURN v_ended;
    END
    $$
    """,
    "kaagaz_whatsapp_end_link(uuid, text)": f"""
    CREATE FUNCTION public.kaagaz_whatsapp_end_link(p_link_id uuid, p_reason text) RETURNS boolean
    LANGUAGE plpgsql SECURITY DEFINER SET search_path = '' AS $$
    DECLARE
        v_ended bigint;
    BEGIN
        IF p_link_id IS NULL OR p_reason IS NULL OR p_reason NOT IN ({_APP_REASONS}) THEN
            RAISE EXCEPTION 'unknown connection or reason' USING ERRCODE = 'check_violation';
        END IF;
        WITH {_END_LINK}
        INTO v_ended;
        RETURN v_ended > 0;
    END
    $$
    """,
    "kaagaz_whatsapp_sweep()": f"""
    CREATE FUNCTION public.kaagaz_whatsapp_sweep() RETURNS bigint
    LANGUAGE plpgsql SECURITY DEFINER SET search_path = '' AS $$
    DECLARE
        v_changed bigint;
        v_deleted bigint;
    BEGIN
        WITH why AS (
            SELECT l.customer_id, l.id, {_NOT_ALLOWED} AS reason
            FROM public.whatsapp_links l
            JOIN public.users u ON u.customer_id = l.customer_id AND u.id = l.user_id
            JOIN public.customers c ON c.id = l.customer_id
            LEFT JOIN public.whatsapp_org_settings s ON s.customer_id = l.customer_id
            WHERE l.ended_at IS NULL
        ), {_END_SWEPT}
        INTO v_changed;
        WITH gone AS (
            DELETE FROM public.whatsapp_messages m WHERE m.received_at < now() - interval '{KEEP_DAYS} days'
            RETURNING 1
        ) SELECT v_changed + count(*) FROM gone INTO v_changed;
        WITH gone AS (
            DELETE FROM public.whatsapp_links l WHERE l.ended_at < now() - interval '{KEEP_DAYS} days'
            RETURNING 1
        ) SELECT v_changed + count(*) FROM gone INTO v_changed;
        WITH gone AS (
            DELETE FROM public.whatsapp_link_codes k WHERE k.created_at < now() - interval '1 day' RETURNING 1
        ) SELECT count(*) FROM gone INTO v_deleted;
        v_changed := v_changed + v_deleted;
        WITH gone AS (
            DELETE FROM public.whatsapp_pickers p WHERE p.expires_at < now() RETURNING 1
        ) SELECT count(*) FROM gone INTO v_deleted;
        RETURN v_changed + v_deleted;
    END
    $$
    """,
}
FUNCTION_COMMENTS = {
    "kaagaz_whatsapp_lookup(bytea)": "WhatsApp (PRD 7 section 15): the live connection of a number, by its"
    " keyed hash (a message arrives with no organisation). Ids only.",
    "kaagaz_whatsapp_claim(integer, integer)": "WhatsApp: the channel takes up to p_limit connections with a"
    " waiting message and a free lease, oldest first, and leases each for p_lease_seconds. Ids only.",
    "kaagaz_whatsapp_end_number(bytea, text)": "WhatsApp: ends every live connection of a number, in any"
    " organisation (number_changed or replaced), each audited there. Returns how many.",
    "kaagaz_whatsapp_end_link(uuid, text)": "WhatsApp: ends one live connection of the current organisation,"
    " its number gone at once, audited (the person in the transaction the actor when they or an Admin"
    " ended it). True when it ended one.",
    "kaagaz_whatsapp_sweep()": "WhatsApp: ends the live connections no longer allowed or silent for 60 days"
    " (audited), deletes messages and ended connections after 30 days, codes after a day and expired"
    " pickers. Returns how many rows it changed.",
}

TABLE_COMMENTS = {
    "whatsapp_org_settings": "Kaagaz on WhatsApp (PRD 7 section 15): the organisation Admin's switches."
    " No row: off.",
    "whatsapp_links": "A person's WhatsApp number connected to their account (PRD 7 section 15). One live per"
    " person and per number across every organisation; an ended one loses its number.",
    "whatsapp_link_codes": "A code sent to a number to connect it (PRD 7 section 15): hashed, 5 tries.",
    "whatsapp_messages": "A message WhatsApp delivered from a connected number, once each (PRD 7 section 15);"
    " its text is emptied once it is handled. Kept 30 days.",
    "whatsapp_pickers": "A person's WhatsApp folder picker (PRD 7 section 15): folder ids only, 15 minutes.",
}
COMMENTS = {
    "whatsapp_org_settings.customer_id": "The organisation.",
    "whatsapp_org_settings.enabled": "WhatsApp is on for the organisation (its Admin's switch). Default off.",
    "whatsapp_org_settings.roles": "The roles that may connect a number: member, reviewer, admin. Default"
    " member and reviewer.",
    "whatsapp_org_settings.management": "Management may connect a number too (an extra tick). Default false.",
    "whatsapp_org_settings.voice": "Questions by voice note are allowed. Default true.",
    "whatsapp_org_settings.uploads": "Saving files sent on WhatsApp is allowed. Default false.",
    "whatsapp_links.customer_id": "The organisation of the person connected.",
    "whatsapp_links.id": "The connection's id.",
    "whatsapp_links.user_id": "users.id (same customer) of the person whose number it is.",
    "whatsapp_links.phone_hash": "The number's keyed hash (HMAC-SHA256), to find the live connection by;"
    " NULL once ended.",
    "whatsapp_links.phone_sealed": "The number, sealed (AES-GCM, bound to the person); NULL once ended.",
    "whatsapp_links.session_version": "users.session_version when it was connected: any change ends it.",
    "whatsapp_links.linked_at": "When it was connected.",
    "whatsapp_links.last_used_at": "When its last message came. NULL: none yet.",
    "whatsapp_links.recent_folder_ids": "Up to 3 folders the person last saved to from WhatsApp, newest"
    " first. Folder ids with no key; each is checked again when shown.",
    "whatsapp_links.busy_until": "The WhatsApp channel's lease: until then one process handles its messages."
    " NULL: free.",
    "whatsapp_links.ended_at": "When it ended. NULL: live.",
    "whatsapp_links.end_reason": "Why it ended: person, not_me, admin, account, session, not_allowed,"
    " number_changed, replaced or silent. NULL: live.",
    "whatsapp_link_codes.customer_id": "The organisation of the person connecting.",
    "whatsapp_link_codes.id": "The code's id.",
    "whatsapp_link_codes.user_id": "users.id (same customer) of the person connecting.",
    "whatsapp_link_codes.phone_hash": "The keyed hash of the number the code was sent to.",
    "whatsapp_link_codes.phone_sealed": "That number, sealed, for the connection the code makes.",
    "whatsapp_link_codes.code_hash": "The code's hash (SHA-256); the code itself is never kept.",
    "whatsapp_link_codes.created_at": "When it was sent.",
    "whatsapp_link_codes.expires_at": "When it stops working.",
    "whatsapp_link_codes.tries": "Wrong codes typed for it, 5 at most.",
    "whatsapp_link_codes.used_at": "When it connected the number. NULL: not used.",
    "whatsapp_messages.customer_id": "The organisation of the connection it came on.",
    "whatsapp_messages.id": "The message's id in Kaagaz.",
    "whatsapp_messages.link_id": "whatsapp_links.id (same customer) of the connection it came on.",
    "whatsapp_messages.user_id": "users.id (same customer) of the person it came from.",
    "whatsapp_messages.wamid": "WhatsApp's own message id: a message delivered twice is kept once.",
    "whatsapp_messages.kind": "text, interactive (a tapped button or list row), button, image, document,"
    " audio, video or other.",
    "whatsapp_messages.body": "The text typed, or the id of the button or row tapped; NULL once handled.",
    "whatsapp_messages.media_id": "WhatsApp's id of the photo, file or voice note sent. NULL: none.",
    "whatsapp_messages.forwarded": "WhatsApp marked it forwarded.",
    "whatsapp_messages.sent_at": "When it was sent, by WhatsApp's clock: one person's messages are handled"
    " in this order.",
    "whatsapp_messages.received_at": "When Kaagaz stored it.",
    "whatsapp_messages.state": "waiting, done (handled), expired (older than WhatsApp's 24 hours when"
    " reached) or failed (its reply could not be sent).",
    "whatsapp_messages.tries": "Times its reply could not be sent.",
    "whatsapp_messages.batch_id": "The upload batch it joined. NULL: none.",
    "whatsapp_messages.document_id": "documents.id (same customer) of the file it saved; NULL for none or"
    " once deleted for good.",
    "whatsapp_messages.reply_sent_at": "When its reply was sent. NULL: none sent.",
    "whatsapp_pickers.customer_id": "The organisation of the person picking.",
    "whatsapp_pickers.user_id": "users.id (same customer) of the person picking.",
    "whatsapp_pickers.generation": "Goes up with every list sent: a tap on an older list is refused.",
    "whatsapp_pickers.state": "The batch, the folder stack, the page and the short row ids, each mapped to a"
    " folder id; never a name.",
    "whatsapp_pickers.expires_at": "When it stops working (15 minutes after the last list).",
    "conversations.channel": "whatsapp for a conversation started on WhatsApp; NULL for the browser.",
}

_WHATSAPP_ROWS = "".join(
    f"\n    DELETE FROM public.{table} x WHERE x.customer_id = p_customer;"
    for table in ("whatsapp_messages", "whatsapp_link_codes", "whatsapp_pickers", "whatsapp_links")
)
_FINISH_0084 = _0047._FINISH.format(words=_0047._WORDS + _0084._ACCESS_ROWS + _0084._KEEP)
FINISH = _0047._FINISH.format(words=_0047._WORDS + _0084._ACCESS_ROWS + _WHATSAPP_ROWS + _0084._KEEP)
_ROLES = ", ".join(ROLES)

UPGRADE = [
    *CREATE_TABLES,
    *INDEXES,
    "ALTER TABLE conversations ADD COLUMN channel text NULL CHECK (channel IN ('whatsapp'))",
    *[f"COMMENT ON TABLE {table} IS {_literal(comment)}" for table, comment in TABLE_COMMENTS.items()],
    *[f"COMMENT ON COLUMN {column} IS {_literal(comment)}" for column, comment in COMMENTS.items()],
    *[
        statement
        for table in TABLES
        for statement in (
            f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY",
            f"CREATE POLICY customer_only ON {table} USING ({POLICY}) WITH CHECK ({POLICY})",
            f"COMMENT ON POLICY customer_only ON {table} IS 'A row is seen and written only by its own customer.'",
            *[f"GRANT {grant} ON {table} TO {_ROLES}" for grant in GRANTS[table]],
        )
    ],
    f"CREATE POLICY own_user_only ON whatsapp_messages AS RESTRICTIVE USING ({OWN_USER}) WITH CHECK ({OWN_USER})",
    "COMMENT ON POLICY own_user_only ON whatsapp_messages IS"
    " 'A message is seen and written only in its own person''s transaction (kaagaz.user_id).'",
    *[
        statement
        for signature, create in FUNCTIONS.items()
        for statement in (
            create,
            f"COMMENT ON FUNCTION public.{signature} IS {_literal(FUNCTION_COMMENTS[signature])}",
            f"REVOKE ALL ON FUNCTION public.{signature} FROM PUBLIC",
            f"GRANT EXECUTE ON FUNCTION public.{signature} TO {_ROLES}",
        )
    ],
    FINISH,
]

DOWNGRADE = [
    _FINISH_0084,
    *[f"DROP FUNCTION IF EXISTS public.{signature}" for signature in FUNCTIONS],
    *[f"DROP TABLE IF EXISTS {table}" for table in reversed(TABLES)],
    "ALTER TABLE conversations DROP COLUMN IF EXISTS channel",
]


def upgrade() -> None:
    for statement in UPGRADE:
        op.execute(statement)


def downgrade() -> None:
    for statement in DOWNGRADE:
        op.execute(statement)
