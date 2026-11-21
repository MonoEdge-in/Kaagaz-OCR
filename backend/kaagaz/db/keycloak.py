"""Database calls for explicit Keycloak identity links and browser sessions.

Every database call except lookup and apply_signed_logout uses a connection from Database.user and
filters by the supplied customer and user where a read is needed. The two system calls have no
customer yet: lookup resolves one exact verified issuer and subject; apply_signed_logout has no
customer because its narrowly scoped database function finds sessions only by the claims in an
already verified Keycloak logout token.

stage_link must be called only after an authenticated legacy user receives a callback that proves
control of the exact issuer and subject, with state, nonce, CSRF and any required reauthentication
checked by the route. The route normally stages and finalizes in the same Database.user
transaction. finalize_link activates only that user's pending binding, switches authority and
increments session_version. Neither function accepts a user or customer id; PostgreSQL takes both
from the current transaction.

The database never maps by email or token organization claims. Identity values are opaque and
kept exactly as supplied by validated Keycloak claims.
"""

from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from sqlalchemy import Connection, text
from sqlalchemy.exc import IntegrityError

from kaagaz.db.sqlstate import UNIQUE_VIOLATION, sqlstate


class IdentityTaken(Exception):
    """This Keycloak identity is already mapped, or this user already has a Keycloak link."""


@dataclass(frozen=True, slots=True)
class IdentityRow:
    user_id: str
    customer_id: str
    session_version: int


@dataclass(frozen=True, slots=True)
class SessionRow:
    local_session_id: str
    keycloak_session_id: str
    expires_at: datetime


@dataclass(frozen=True, slots=True)
class FlowRow:
    nonce: str
    sealed_verifier: str
    purpose: str
    next_path: str
    customer_id: str | None
    user_id: str | None
    session_version: int | None
    local_session_id: str | None
    action: str | None
    started_at: datetime


def validate_binding(issuer: str, subject: str) -> None:
    """Validate exact OIDC identity keys without normalizing either claim."""
    _validate_opaque("issuer", issuer, 2048)
    _validate_opaque("subject", subject, 255)


def create_flow(
    conn: Connection,
    state_hash: bytes,
    *,
    nonce: str,
    sealed_verifier: str,
    purpose: str,
    next_path: str,
    started_at: datetime,
    expires_at: datetime,
    customer_id: str | None = None,
    user_id: str | None = None,
    session_version: int | None = None,
    local_session_id: str | None = None,
    action: str | None = None,
) -> None:
    """Persist one short flow; reauth calls require Database.user for the bound user.

    The caller generates random state/nonce/verifier, encrypts the PKCE verifier with the
    dedicated flow key and uses only a 32-byte state hash here. For ``reauth``, bind one action to
    the current browser session. Login flows have no customer/user context. The SQL function checks
    context and expected session version and prevents direct tenant ids from selecting another person.
    """
    if len(state_hash) != 32:
        raise ValueError("invalid Keycloak flow state hash")
    if purpose not in {"login", "reauth"}:
        raise ValueError("invalid Keycloak flow purpose")
    conn.execute(
        text(
            "SELECT public.kaagaz_keycloak_flow_create(:state_hash, :nonce, :sealed_verifier,"
            " :purpose, :next_path, CAST(:customer_id AS uuid), CAST(:user_id AS uuid),"
            " :session_version, CAST(:local_session_id AS uuid), :action, :started_at, :expires_at)"
        ),
        {
            "state_hash": state_hash,
            "nonce": nonce,
            "sealed_verifier": sealed_verifier,
            "purpose": purpose,
            "next_path": next_path,
            "customer_id": customer_id,
            "user_id": user_id,
            "session_version": session_version,
            "local_session_id": local_session_id,
            "action": action,
            "started_at": started_at,
            "expires_at": expires_at,
        },
    ).scalar_one()


def consume_flow(conn: Connection, state_hash: bytes, *, now: datetime) -> FlowRow | None:
    """Consume one unexpired flow state once; conn comes from Database.system()."""
    if len(state_hash) != 32:
        raise ValueError("invalid Keycloak flow state hash")
    row = (
        conn.execute(
            text("SELECT * FROM public.kaagaz_keycloak_flow_consume(:state_hash, :now)"),
            {"state_hash": state_hash, "now": now},
        )
        .mappings()
        .first()
    )
    if row is None:
        return None
    return FlowRow(
        nonce=str(row["nonce"]),
        sealed_verifier=str(row["sealed_verifier"]),
        purpose=str(row["purpose"]),
        next_path=str(row["next_path"]),
        customer_id=None if row["customer_id"] is None else str(row["customer_id"]),
        user_id=None if row["user_id"] is None else str(row["user_id"]),
        session_version=None if row["session_version"] is None else int(row["session_version"]),
        local_session_id=None if row["local_session_id"] is None else str(row["local_session_id"]),
        action=None if row["action"] is None else str(row["action"]),
        started_at=row["started_at"],
    )


def _validate_opaque(name: str, value: str, maximum: int) -> None:
    if (
        not isinstance(value, str)
        or not value
        or value != value.strip()
        or len(value) > maximum
        or any(ord(char) < 32 or ord(char) == 127 for char in value)
    ):
        raise ValueError(f"invalid Keycloak {name}")


def _validate_session_id(keycloak_session_id: str) -> None:
    _validate_opaque("session id", keycloak_session_id, 255)


def lookup(conn: Connection, issuer: str, subject: str) -> IdentityRow | None:
    """Resolve one active, enabled Keycloak identity; conn comes from Database.system()."""
    validate_binding(issuer, subject)
    row = (
        conn.execute(
            text(
                "SELECT user_id, customer_id, session_version"
                " FROM public.kaagaz_keycloak_lookup(:issuer, :subject)"
            ),
            {"issuer": issuer, "subject": subject},
        )
        .mappings()
        .first()
    )
    if row is None:
        return None
    return IdentityRow(
        user_id=str(row["user_id"]),
        customer_id=str(row["customer_id"]),
        session_version=int(row["session_version"]),
    )


def stage_link(
    conn: Connection,
    issuer: str,
    subject: str,
    *,
    now: datetime,
    expected_session_version: int,
) -> str:
    """Stage a pending link for the current authenticated legacy user.

    Call only after the route verifies the configured issuer, audience, callback signature, state,
    nonce and CSRF protection, with required reauthentication complete. The route normally follows
    this with finalize_link in the same transaction. Raises IdentityTaken on a uniqueness conflict;
    that error aborts the transaction, which the caller must end.
    """
    validate_binding(issuer, subject)
    try:
        identity_id: object = conn.execute(
            text("SELECT public.kaagaz_keycloak_stage_link(:issuer, :subject, :now, :version)"),
            {"issuer": issuer, "subject": subject, "now": now, "version": expected_session_version},
        ).scalar_one()
    except IntegrityError as error:
        if sqlstate(error) == UNIQUE_VIOLATION:
            raise IdentityTaken from None
        raise
    return str(identity_id)


def finalize_link(
    conn: Connection,
    issuer: str,
    subject: str,
    *,
    now: datetime,
    expected_session_version: int,
) -> int:
    """Activate the current user's pending link and return the raised session_version.

    The caller must already have authenticated the user and validated a Keycloak callback that
    proves control of these exact claims, including issuer, audience, state, nonce and CSRF
    protection. Use the returned version for the replacement local session. This does not accept a
    browser-submitted identity, user id, customer id, email or organization claim.
    """
    validate_binding(issuer, subject)
    version: object = conn.execute(
        text("SELECT public.kaagaz_keycloak_finalize_link(:issuer, :subject, :now, :version)"),
        {"issuer": issuer, "subject": subject, "now": now, "version": expected_session_version},
    ).scalar_one()
    if not isinstance(version, int):
        raise TypeError("session_version is not an integer")
    return version


def record_session(
    conn: Connection,
    issuer: str,
    subject: str,
    keycloak_session_id: str,
    local_session_id: str,
    *,
    expected_session_version: int,
    expires_at: datetime,
) -> bool:
    """Record a browser session only if the looked-up user version still matches.

    The caller supplies the issuer, subject and sid from that verified token, the session_version
    returned by the earlier explicit identity lookup, and a server-generated local session ID.
    The connection must be Database.user for the mapped user. False means the user changed,
    became disabled, or a signed logout tombstone already exists; the caller must not issue a
    cookie. SQL advisory locking serializes this insert with signed logout for the same tuple.
    """
    validate_binding(issuer, subject)
    _validate_session_id(keycloak_session_id)
    if expected_session_version < 1:
        raise ValueError("invalid expected session version")
    local_id = str(UUID(local_session_id))
    result = conn.execute(
        text(
            "SELECT public.kaagaz_keycloak_session_record"
            "(:issuer, :subject, :keycloak_session_id, CAST(:local_session_id AS uuid),"
            " :expected_session_version, :expires_at)"
        ),
        {
            "issuer": issuer,
            "subject": subject,
            "keycloak_session_id": keycloak_session_id,
            "local_session_id": local_id,
            "expected_session_version": expected_session_version,
            "expires_at": expires_at,
        },
    ).scalar_one()
    return bool(result)


def session_for_user(
    conn: Connection, customer_id: str, user_id: str, local_session_id: str
) -> SessionRow | None:
    """Read one active association for the same user and customer in Database.user."""
    local_id = str(UUID(local_session_id))
    row = (
        conn.execute(
            text(
                "SELECT local_session_id, keycloak_session_id, expires_at"
                " FROM public.keycloak_sessions"
                " WHERE customer_id = :customer AND user_id = :user"
                " AND user_id = CAST(NULLIF(current_setting('kaagaz.user_id', true), '') AS uuid)"
                " AND local_session_id = CAST(:local_session AS uuid) AND revoked_at IS NULL"
                " AND NOT EXISTS (SELECT 1 FROM public.ended_sessions e"
                " WHERE e.customer_id = keycloak_sessions.customer_id"
                " AND e.session_id = keycloak_sessions.local_session_id)"
            ),
            {"customer": customer_id, "user": user_id, "local_session": local_id},
        )
        .mappings()
        .first()
    )
    if row is None:
        return None
    return SessionRow(
        local_session_id=str(row["local_session_id"]),
        keycloak_session_id=str(row["keycloak_session_id"]),
        expires_at=row["expires_at"],
    )


def revoke_session(conn: Connection, local_session_id: str, *, now: datetime) -> bool:
    """Revoke one local browser session belonging to the current Database.user transaction."""
    local_id = str(UUID(local_session_id))
    return bool(
        conn.execute(
            text("SELECT public.kaagaz_keycloak_session_revoke(CAST(:local_session_id AS uuid), :now)"),
            {"local_session_id": local_id, "now": now},
        ).scalar_one()
    )


def apply_signed_logout(
    conn: Connection,
    issuer: str,
    subject: str,
    keycloak_session_id: str,
    jti: str,
    *,
    token_expires_at: datetime,
    now: datetime,
) -> int | None:
    """Apply a verified Keycloak logout token; conn comes from Database.system().

    Call only after the route verifies the signature, configured issuer, audience, logout event,
    issued-at/expiry, sid/sub pair, absence of nonce and replay identifier. PostgreSQL consumes the
    issuer/jti and stores an issuer/subject/sid tombstone even if no local session exists yet. The
    callback record function shares a transaction advisory lock, so it cannot survive an earlier
    logout.
    """
    validate_binding(issuer, subject)
    _validate_session_id(keycloak_session_id)
    _validate_opaque("logout token id", jti, 255)
    count: object = conn.execute(
        text(
            "SELECT public.kaagaz_keycloak_logout(:issuer, :subject, :keycloak_session_id,"
            " :jti, :token_expires_at, :now)"
        ),
        {
            "issuer": issuer,
            "subject": subject,
            "keycloak_session_id": keycloak_session_id,
            "jti": jti,
            "token_expires_at": token_expires_at,
            "now": now,
        },
    ).scalar_one()
    if count is None:
        return None
    if not isinstance(count, int):
        raise TypeError("logout result is not an integer")
    return count


def revoke_all_sessions(conn: Connection, *, now: datetime) -> int:
    """End every mapped browser session for the current user transaction.

    Call from an authenticated logout-everywhere request before raising session_version. The
    definer function applies tenant and user context and writes each local id to ended_sessions.
    """
    count: object = conn.execute(
        text("SELECT public.kaagaz_keycloak_sessions_revoke_all(:now)"), {"now": now}
    ).scalar_one()
    if not isinstance(count, int):
        raise TypeError("session revocation count is not an integer")
    return count


@dataclass(frozen=True, slots=True)
class Person:
    user_id: str
    email: str
    name: str
    disabled: bool
    linked: bool


def link_account(
    conn: Connection, issuer: str, subject: str, *, now: datetime, as_user: str | None = None
) -> int:
    """Link the user of this ``Database.user`` transaction to a Keycloak account an admin made.

    The Keycloak account was created by Kaagaz itself a moment ago (never found by email), so
    stage and finalize run together. Returns the raised session_version. IdentityTaken when the
    subject or user already has a link. ``as_user``: a user this same transaction just created, so
    the context moves to them first (the functions take the user from the context).
    """
    if as_user is not None:
        conn.execute(text("SELECT set_config('kaagaz.user_id', :u, true)"), {"u": str(UUID(as_user))})
    version = conn.execute(
        text(
            "SELECT session_version FROM public.users"
            " WHERE id = CAST(NULLIF(current_setting('kaagaz.user_id', true), '') AS uuid)"
        )
    ).scalar_one()
    stage_link(conn, issuer, subject, now=now, expected_session_version=int(version))
    raised = finalize_link(conn, issuer, subject, now=now, expected_session_version=int(version))
    # A person moved from Kaagaz's own old sign-in keeps an old last_login_at; clearing it makes
    # "signed in once" mean "since the link", which is what the People list's "Waiting for setup"
    # (and Resend setup email) rests on. The raised session_version signed them out anyway.
    conn.execute(
        text(
            "UPDATE public.users SET last_login_at = NULL"
            " WHERE id = CAST(NULLIF(current_setting('kaagaz.user_id', true), '') AS uuid)"
        )
    )
    return raised


def people(conn: Connection, customer_id: str, issuer: str) -> list[Person]:
    """Every user of the customer and whether a Keycloak account of this issuer is linked.

    ``conn`` from ``Database.customer``. Uses the read-only link function of 0104.
    """
    linked = subjects(conn, issuer)
    rows = conn.execute(
        text(
            "SELECT id, email, display_name, disabled FROM public.users"
            " WHERE customer_id = :c ORDER BY created_at, id"
        ),
        {"c": customer_id},
    ).mappings()
    return [
        Person(
            str(r["id"]), str(r["email"]), str(r["display_name"]), bool(r["disabled"]), str(r["id"]) in linked
        )
        for r in rows
    ]


def subjects(conn: Connection, issuer: str) -> dict[str, str]:
    """The customer's linked Keycloak subjects of this issuer by user id (0104).

    ``conn`` from ``Database.customer`` or ``Database.user``."""
    return {
        str(row["user_id"]): str(row["subject"])
        for row in conn.execute(
            text("SELECT user_id, issuer, subject FROM public.kaagaz_keycloak_subjects()")
        ).mappings()
        if row["issuer"] == issuer
    }


def subject_of(conn: Connection, user_id: str, issuer: str) -> str | None:
    """The Keycloak subject linked to this user, or None."""
    return subjects(conn, issuer).get(user_id)
