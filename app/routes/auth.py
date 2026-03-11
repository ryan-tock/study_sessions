from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import Optional
from urllib.parse import urlencode
from fastapi import APIRouter, BackgroundTasks, Depends, Form, Request, Response, status
from fastapi.responses import HTMLResponse, RedirectResponse

from ..auth import (
    authenticate_user, create_access_token, create_refresh_token,
    revoke_refresh_token,
)
from ..database import get_db
from ..dependencies import get_current_user
from ..helpers import templates
from .discord import download_and_cache_avatar

router = APIRouter()

# Per-user failed login rate limiting: 5 attempts per 1-minute sliding window
_RATE_LIMIT_WINDOW = timedelta(minutes=1)
_RATE_LIMIT_MAX = 5
_failed_login_attempts: dict[str, list[datetime]] = defaultdict(list)


@router.get("/", response_class=HTMLResponse)
async def root(request: Request, error: Optional[str] = None, fn: Optional[str] = None, ln: Optional[str] = None):
    """Root route - redirects to appropriate portal if logged in, else shows login."""
    user = get_current_user(request)
    if user:
        if user["is_admin"]:
            return RedirectResponse(url="/admin/portal")
        else:
            return RedirectResponse(url="/user/portal")
    return templates.TemplateResponse(request, "login.html", {
        "error": error,
        "prefill_first_name": fn,
        "prefill_last_name": ln,
    })


@router.post("/login")
async def login(
    response: Response,
    background_tasks: BackgroundTasks,
    first_name: str = Form(...),
    last_name: str = Form(default=""),
    password: str = Form(...)
):
    """Handle login form submission."""
    key = f"{first_name.strip().lower()} {last_name.strip().lower()}"
    now = datetime.now(timezone.utc)
    _failed_login_attempts[key] = [t for t in _failed_login_attempts[key] if now - t < _RATE_LIMIT_WINDOW]
    if len(_failed_login_attempts[key]) >= _RATE_LIMIT_MAX:
        return RedirectResponse(
            url="/?" + urlencode({"error": "Too many failed attempts. Please wait a minute and try again.", "fn": first_name, "ln": last_name}),
            status_code=status.HTTP_302_FOUND
        )

    user = authenticate_user(first_name, last_name, password)
    if not user:
        _failed_login_attempts[key].append(now)
        return RedirectResponse(
            url="/?" + urlencode({"error": "Invalid name or password.", "fn": first_name, "ln": last_name}),
            status_code=status.HTTP_302_FOUND
        )
    _failed_login_attempts.pop(key, None)

    # Create tokens
    access_token_expires = timedelta(minutes=30)
    access_token = create_access_token(
        data={"sub": str(user["student_id"]), "role": user.get("role"), "is_admin": user["is_admin"], "is_root": user["is_root"], "is_first_login": user.get("is_first_login", False)},
        expires_delta=access_token_expires
    )
    refresh_token = create_refresh_token(user["student_id"])

    # Refresh avatar in background if stale (>1 day)
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT discord_id, avatar_checked_at FROM students WHERE student_id = %s",
                (user["student_id"],)
            )
            row = cur.fetchone()
    if row:
        discord_id, avatar_checked_at = row
        if discord_id and (
            avatar_checked_at is None or
            (datetime.now(timezone.utc) - avatar_checked_at.astimezone(timezone.utc)) > timedelta(days=1)
        ):
            background_tasks.add_task(download_and_cache_avatar, user["student_id"], discord_id)

    # Redirect based on role / first login
    if user.get("is_first_login"):
        redirect_url = "/user/set_password"
    elif user["is_admin"]:
        redirect_url = "/admin/portal"
    else:
        redirect_url = "/user/portal"
    response = RedirectResponse(url=redirect_url, status_code=status.HTTP_302_FOUND)

    # Set cookies
    response.set_cookie(
        key="access_token",
        value=f"Bearer {access_token}",
        httponly=True,
        max_age=1800,  # 30 minutes
        samesite="lax"
    )
    response.set_cookie(
        key="refresh_token",
        value=refresh_token,
        httponly=True,
        max_age=604800,  # 7 days
        samesite="lax",
        path="/"
    )
    return response


@router.post("/logout")
async def logout(request: Request, response: Response):
    """Handle logout - revoke refresh token and clear cookies."""
    refresh_token = request.cookies.get("refresh_token")
    if refresh_token:
        revoke_refresh_token(refresh_token)

    response = RedirectResponse(url="/", status_code=status.HTTP_302_FOUND)
    response.delete_cookie("access_token")
    response.delete_cookie("refresh_token", path="/")
    return response
