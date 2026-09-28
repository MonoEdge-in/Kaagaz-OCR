"""POST /api/auth/login, POST /api/auth/logout, GET /api/auth/me.

Why the login looks the way it does: an unknown e-mail, a wrong password and a
disabled user all get the same 401 ``bad_credentials``, and an unknown e-mail
still costs one scrypt hash, so neither the reply nor its timing says which
accounts exist. The e-mail lookup is the one SECURITY DEFINER function made for
it, inside ``db.system()``; everything after it runs as the found customer.
"""

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from kaagaz.api.auth import (
    SessionClaims,
    User,
    clear_session_cookie,
    load_user,
    me_json,
    set_session_cookie,
    sign_session,
    spend_password_time,
    verify_password,
)
from kaagaz.api.context import AppContext, get_context
from kaagaz.api.errors import ApiError
from kaagaz.db import audit
from kaagaz.db import users as users_sql

router = APIRouter()

Context = Annotated[AppContext, Depends(get_context)]


class LoginBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    email: str = Field(min_length=1, max_length=254)
    password: str = Field(min_length=1, max_length=1024)


@router.post("/auth/login")
def login(body: LoginBody, context: Context) -> JSONResponse:
    with context.db.system() as conn:
        found = users_sql.login_lookup(conn, body.email)
    if found is None:
        spend_password_time(body.password)
        raise ApiError(401, "bad_credentials")
    password_ok = verify_password(body.password, found.password_hash)
    if not password_ok or found.disabled:
        raise ApiError(401, "bad_credentials")
    user = load_user(context.db, found.customer_id, found.user_id)
    if user is None:
        raise ApiError(401, "bad_credentials")

    with context.db.customer(user.customer_id) as conn:
        audit.record(
            conn,
            user.customer_id,
            actor_user_id=user.user_id,
            action="auth.login",
            target_type="user",
            target_id=user.user_id,
        )
    expires_at = int(context.clock().timestamp()) + context.settings.session_hours * 3600
    token = sign_session(
        SessionClaims(user.user_id, user.customer_id, user.role, expires_at), context.settings.session_secret
    )
    response = JSONResponse(me_json(user))
    set_session_cookie(response, token, context.settings)
    return response


@router.post("/auth/logout", status_code=204)
def logout(user: User, context: Context) -> Response:
    with context.db.customer(user.customer_id) as conn:
        audit.record(
            conn,
            user.customer_id,
            actor_user_id=user.user_id,
            action="auth.logout",
            target_type="user",
            target_id=user.user_id,
        )
    response = Response(status_code=204)
    clear_session_cookie(response, context.settings)
    return response


@router.get("/auth/me")
def me(user: User) -> dict[str, Any]:
    return me_json(user)
