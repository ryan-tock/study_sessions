import asyncio
import html
import os
from contextlib import asynccontextmanager
from fastapi import FastAPI, Depends, HTTPException, status, Request, Response, Form, BackgroundTasks, UploadFile, File
from fastapi.templating import Jinja2Templates
from fastapi.staticfiles import StaticFiles
from fastapi.responses import RedirectResponse, HTMLResponse, FileResponse
from datetime import datetime, timedelta, timezone
from jose import JWTError, jwt

from .auth import (
    authenticate_user, create_access_token, create_refresh_token,
    verify_refresh_token, revoke_refresh_token, get_password_hash, verify_password
)
from .config import SECRET_KEY, ALGORITHM, DISCORD_BOT_TOKEN, USER_PASSWORD
from .database import get_db, get_db_for_user
from .persistence import backup, restore_from_disk, list_backups, delete_backup
from .pdf_parser import parse_common_hour_pdf, parse_finals_pdf
from .course_scraper import (
    fetch_courses, load_courses_from_cache,
    cache_exists as course_cache_exists,
    pending_cache_exists as course_pending_cache_exists,
    wipe_cache as wipe_course_cache_files,
    wipe_pending_cache as wipe_course_pending_cache,
    promote_pending_cache,
)
from typing import Optional
import urllib.request
import urllib.error
import ssl
import certifi
import json
import re
import unicodedata
from collections import defaultdict
from difflib import SequenceMatcher as _SM
from urllib.parse import urlencode

# Per-user failed login rate limiting: 5 attempts per 1-minute sliding window
_RATE_LIMIT_WINDOW = timedelta(minutes=1)
_RATE_LIMIT_MAX = 5
_failed_login_attempts: dict[str, list[datetime]] = defaultdict(list)


_ALLOWED_NAME_EXTRAS = set(" -'.\u2018\u2019")


def validate_name(name: str) -> bool:
    """Allow Unicode letters, combining marks, spaces, hyphens, apostrophes, and periods."""
    return bool(name.strip()) and all(
        unicodedata.category(c).startswith(('L', 'M')) or c in _ALLOWED_NAME_EXTRAS
        for c in name
    )


def validate_password(password: str) -> Optional[str]:
    """Returns an error message if password doesn't meet requirements, else None."""
    if len(password) < 12:
        return "Password must be at least 12 characters"
    if not re.search(r'\d', password):
        return "Password must contain at least one number"
    if not re.search(r'[!@#$%^&*()\-_=+\[\]{}|;:\'",.<>?/`~\\]', password):
        return "Password must contain at least one special character"
    return None


def get_user_profile(student_id: int) -> dict:
    """Fetch a user's display info (name, discord_id, sharing) from the DB."""
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT first_name, last_name, discord_id, sharing FROM students WHERE student_id = %s",
                (student_id,)
            )
            row = cur.fetchone()
    if row:
        return {"first_name": row[0], "last_name": row[1], "discord_id": row[2], "sharing": row[3]}
    return {"first_name": "", "last_name": "", "discord_id": None, "sharing": "common_class"}

current_dir = os.path.dirname(os.path.abspath(__file__))
AVATAR_DIR = os.path.join(current_dir, "static", "avatars")
DATA_DIR = os.path.join(current_dir, "..", "data")
os.makedirs(AVATAR_DIR, exist_ok=True)
os.makedirs(DATA_DIR, exist_ok=True)

# Tracks the pending-course-preview cleanup task so it can be cancelled on re-fetch.
_course_pending_cleanup_task: asyncio.Task | None = None
_PENDING_PREVIEW_TTL = 600  # seconds


async def _expire_course_pending(academic_year: int, season: str) -> None:
    """Delete the pending course cache after TTL seconds."""
    await asyncio.sleep(_PENDING_PREVIEW_TTL)
    try:
        wipe_course_pending_cache(DATA_DIR, academic_year, season)
    except Exception:
        pass


@asynccontextmanager
async def lifespan(_: FastAPI):
    yield


app = FastAPI(lifespan=lifespan)

# Setup templates and static files with absolute paths
templates = Jinja2Templates(directory=os.path.join(current_dir, "templates"))
app.mount("/static", StaticFiles(directory=os.path.join(current_dir, "static")), name="static")


def validate_discord_id(discord_id: str) -> tuple[bool, str]:
    """
    Validate a Discord ID via the bot token.
    Returns (is_valid, error_message). Skips validation if no bot token is configured.
    """
    token = (DISCORD_BOT_TOKEN or "").strip()
    if not token:
        return True, ""
    try:
        ctx = ssl.create_default_context(cafile=certifi.where())
        req = urllib.request.Request(
            f"https://discord.com/api/v10/users/{discord_id}",
            headers={
                "Authorization": f"Bot {token}",
                "User-Agent": "DiscordBot (https://github.com/discord/discord-api-docs, 10)",
            }
        )
        with urllib.request.urlopen(req, timeout=5, context=ctx) as resp:
            data = json.loads(resp.read())
        if not data.get("avatar"):
            return False, "That Discord account has no profile picture"
        return True, ""
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return False, "Discord user not found"
        return False, "Could not verify Discord ID"
    except Exception:
        return False, "Could not verify Discord ID"


def download_and_cache_avatar(student_id: int, discord_id: str) -> None:
    """Download a user's Discord avatar and cache it to disk. Safe to run as a background task."""
    token = (DISCORD_BOT_TOKEN or "").strip()
    if not token or not discord_id:
        return
    try:
        ctx = ssl.create_default_context(cafile=certifi.where())
        # Fetch user info from Discord
        req = urllib.request.Request(
            f"https://discord.com/api/v10/users/{discord_id}",
            headers={
                "Authorization": f"Bot {token}",
                "User-Agent": "DiscordBot (https://github.com/discord/discord-api-docs, 10)",
            }
        )
        with urllib.request.urlopen(req, timeout=5, context=ctx) as resp:
            data = json.loads(resp.read())

        avatar_hash = data.get("avatar")
        if avatar_hash:
            img_url = f"https://cdn.discordapp.com/avatars/{discord_id}/{avatar_hash}.png?size=128"
        else:
            img_url = f"https://cdn.discordapp.com/embed/avatars/{int(discord_id) % 5}.png"

        # Download the image
        img_req = urllib.request.Request(
            img_url,
            headers={"User-Agent": "DiscordBot (https://github.com/discord/discord-api-docs, 10)"}
        )
        with urllib.request.urlopen(img_req, timeout=5, context=ctx) as img_resp:
            img_data = img_resp.read()

        with open(os.path.join(AVATAR_DIR, f"{student_id}.png"), "wb") as f:
            f.write(img_data)

        with get_db_for_user({"student_id": student_id, "is_admin": False}) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE students SET avatar_checked_at = NOW() WHERE student_id = %s",
                    (student_id,)
                )
            conn.commit()
    except Exception:
        pass


@app.get("/avatar/{student_id}")
def serve_avatar(student_id: int):
    """Serve a cached avatar with browser cache headers."""
    path = os.path.join(AVATAR_DIR, f"{student_id}.png")
    if os.path.exists(path):
        return FileResponse(path, headers={"Cache-Control": "public, max-age=86400"})
    return Response(status_code=404)


@app.post("/api/avatar/fetch/{student_id}")
def fetch_avatar_for_login(student_id: int):
    """Fetch and cache a user's Discord avatar. Public — used on the login page before auth."""
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT discord_id FROM students WHERE student_id = %s", (student_id,))
            row = cur.fetchone()
    if not row or not row[0]:
        return {"ok": False}
    download_and_cache_avatar(student_id, row[0])
    return {"ok": os.path.exists(os.path.join(AVATAR_DIR, f"{student_id}.png"))}


def get_current_user(request: Request) -> Optional[dict]:
    """Extract and verify JWT from cookie."""
    token = request.cookies.get("access_token")
    if not token or not token.startswith("Bearer "):
        return None
    
    token = token.replace("Bearer ", "")
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        student_id = int(payload.get("sub"))
        is_admin = payload.get("is_admin", False)
        is_root = payload.get("is_root", False)
        is_first_login = payload.get("is_first_login", False)
        return {"student_id": student_id, "is_admin": is_admin, "is_root": is_root, "is_first_login": is_first_login}
    except (JWTError, ValueError):
        return None


def require_auth(request: Request):
    """Dependency to require authentication."""
    user = get_current_user(request)
    if not user:
        raise HTTPException(
            status_code=status.HTTP_302_FOUND,
            headers={"Location": "/"},
            detail="Not authenticated"
        )
    return user


def require_admin(request: Request):
    """Dependency to require admin privileges."""
    user = require_auth(request)
    if not user["is_admin"]:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admin access required"
        )
    return user


@app.get("/", response_class=HTMLResponse)
async def root(request: Request, error: Optional[str] = None, fn: Optional[str] = None, ln: Optional[str] = None):
    """Root route - redirects to appropriate portal if logged in, else shows login."""
    user = get_current_user(request)
    if user:
        if user["is_admin"]:
            return RedirectResponse(url="/admin/portal")
        else:
            return RedirectResponse(url="/user/portal")
    return templates.TemplateResponse("login.html", {
        "request": request,
        "error": error,
        "prefill_first_name": fn,
        "prefill_last_name": ln,
    })


@app.post("/login")
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
        data={"sub": str(user["student_id"]), "is_admin": user["is_admin"], "is_root": user["is_root"], "is_first_login": user.get("is_first_login", False)},
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



@app.post("/logout")
async def logout(request: Request, response: Response):
    """Handle logout - revoke refresh token and clear cookies."""
    refresh_token = request.cookies.get("refresh_token")
    if refresh_token:
        revoke_refresh_token(refresh_token)
    
    response = RedirectResponse(url="/", status_code=status.HTTP_302_FOUND)
    response.delete_cookie("access_token")
    response.delete_cookie("refresh_token", path="/")
    return response


@app.get("/user/portal", response_class=HTMLResponse)
async def user_portal(request: Request, user: dict = Depends(require_auth), message: Optional[str] = None):
    """User portal - accessible by all authenticated users except root."""
    if user.get("is_root"):
        return RedirectResponse(url="/admin/portal", status_code=status.HTTP_302_FOUND)
    return templates.TemplateResponse("user_portal.html", {
        "request": request,
        "user": user,
        "user_profile": get_user_profile(user["student_id"]),
        "message": message
    })


@app.get("/user/set_password", response_class=HTMLResponse)
async def get_set_password(request: Request, user: dict = Depends(require_auth)):
    """First-login password setup page. Root users are not permitted here."""
    if user.get("is_root"):
        return RedirectResponse(url="/admin/portal", status_code=status.HTTP_302_FOUND)
    return templates.TemplateResponse("set_password.html", {"request": request, "user": user})


_VALID_SHARING = {"closed", "common_class", "open"}


@app.post("/user/set_password", response_class=HTMLResponse)
async def post_set_password(
    user: dict = Depends(require_auth),
    new_password: str = Form(...),
    sharing: Optional[str] = Form(default=None),
):
    """Handle first-login password setup. Re-issues token with is_first_login=False. Root users are not permitted here."""
    if user.get("is_root"):
        return RedirectResponse(url="/admin/portal", status_code=status.HTTP_302_FOUND)
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE student_auth SET hashed_password = %s WHERE student_id = %s",
                (get_password_hash(new_password), user["student_id"])
            )
            if sharing in _VALID_SHARING:
                cur.execute(
                    "UPDATE students SET sharing = %s::sharing_setting WHERE student_id = %s",
                    (sharing, user["student_id"])
                )
        conn.commit()
    access_token = create_access_token(
        data={"sub": str(user["student_id"]), "is_admin": user["is_admin"], "is_root": user["is_root"], "is_first_login": False},
        expires_delta=timedelta(minutes=30)
    )
    response = RedirectResponse(url="/user/privacy", status_code=status.HTTP_302_FOUND)
    response.set_cookie(key="access_token", value=f"Bearer {access_token}", httponly=True, max_age=1800, samesite="lax")
    return response


@app.get("/user/change_password", response_class=HTMLResponse)
async def get_change_password(request: Request, user: dict = Depends(require_auth)):
    """Change password page. Root users cannot change their password here."""
    if user.get("is_root"):
        redirect_url = "/admin/portal" if user["is_admin"] else "/user/portal"
        return RedirectResponse(url=redirect_url, status_code=status.HTTP_302_FOUND)
    return templates.TemplateResponse("change_password.html", {
        "request": request,
        "user": user,
        "user_profile": get_user_profile(user["student_id"]),
    })


@app.post("/user/change_password", response_class=HTMLResponse)
async def post_change_password(
    request: Request,
    user: dict = Depends(require_auth),
    old_password: str = Form(...),
    new_password: str = Form(...),
    confirm_password: str = Form(...),
):
    """Update the current user's password. Requires current password + validation. Root users are blocked."""
    if user.get("is_root"):
        redirect_url = "/admin/portal" if user["is_admin"] else "/user/portal"
        return RedirectResponse(url=redirect_url, status_code=status.HTTP_302_FOUND)

    def render_error(msg: str):
        return templates.TemplateResponse("change_password.html", {
            "request": request,
            "user": user,
            "user_profile": get_user_profile(user["student_id"]),
            "error": msg,
        })

    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT hashed_password FROM student_auth WHERE student_id = %s", (user["student_id"],))
            row = cur.fetchone()
    if not row or not verify_password(old_password, row[0]):
        return render_error("Current password is incorrect")
    if new_password != confirm_password:
        return render_error("New passwords do not match")
    err = validate_password(new_password)
    if err:
        return render_error(err)

    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE student_auth SET hashed_password = %s WHERE student_id = %s",
                (get_password_hash(new_password), user["student_id"])
            )
        conn.commit()
    refresh_token = request.cookies.get("refresh_token")
    if refresh_token:
        revoke_refresh_token(refresh_token)
    resp = RedirectResponse(url="/", status_code=status.HTTP_302_FOUND)
    resp.delete_cookie("access_token")
    resp.delete_cookie("refresh_token", path="/")
    return resp


@app.get("/user/privacy", response_class=HTMLResponse)
async def get_privacy(request: Request, user: dict = Depends(require_auth)):
    """Privacy settings page — lets users change their sharing setting."""
    if user.get("is_root"):
        redirect_url = "/admin/portal" if user["is_admin"] else "/user/portal"
        return RedirectResponse(url=redirect_url, status_code=status.HTTP_302_FOUND)
    return templates.TemplateResponse("privacy.html", {
        "request": request,
        "user": user,
        "user_profile": get_user_profile(user["student_id"]),
    })


@app.post("/user/privacy", response_class=HTMLResponse)
async def post_privacy(
    user: dict = Depends(require_auth),
    sharing: str = Form(...),
):
    """Save the user's sharing setting."""
    if user.get("is_root"):
        redirect_url = "/admin/portal" if user["is_admin"] else "/user/portal"
        return RedirectResponse(url=redirect_url, status_code=status.HTTP_302_FOUND)
    if sharing not in _VALID_SHARING:
        raise HTTPException(status_code=400, detail="Invalid sharing setting")
    with get_db_for_user(user) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE students SET sharing = %s::sharing_setting WHERE student_id = %s",
                (sharing, user["student_id"])
            )
        conn.commit()
    redirect_url = "/admin/portal" if user["is_admin"] else "/user/portal"
    return RedirectResponse(
        url=f"{redirect_url}?message=Privacy+settings+saved",
        status_code=status.HTTP_302_FOUND
    )


@app.get("/api/my/enrollments")
async def get_my_enrollments(user: dict = Depends(require_auth)):
    """Get the current user's enrolled courses."""
    with get_db_for_user(user) as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT c.course_id, c.department, c.identifier, c.title
                FROM enrollments e
                JOIN courses c ON e.course_id = c.course_id
                CROSS JOIN current_term ct
                WHERE e.student_id = %s
                  AND (e.term).academic_year = ct.academic_year
                  AND (e.term).season = ct.season
                ORDER BY c.department, c.identifier
            """, (user["student_id"],))
            rows = cur.fetchall()
    return [
        {"course_id": r[0], "department": r[1], "identifier": r[2], "title": r[3]}
        for r in rows
    ]


@app.post("/api/my/enrollments")
async def add_my_enrollment(user: dict = Depends(require_auth), course_id: int = Form(...)):
    """Add a course enrollment for the current term."""
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT academic_year, season FROM current_term")
            term = cur.fetchone()
    if not term:
        raise HTTPException(400, "Could not determine current term")
    year, season = term
    with get_db_for_user(user) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """INSERT INTO enrollments (student_id, course_id, term)
                   VALUES (%s, %s, ROW(%s, %s::term_season)::academic_term)
                   ON CONFLICT DO NOTHING""",
                (user["student_id"], course_id, year, season)
            )
        conn.commit()
    return {"ok": True}


@app.delete("/api/my/enrollments/{course_id}")
async def remove_my_enrollment(course_id: int, user: dict = Depends(require_auth)):
    """Remove all enrollments for a course."""
    with get_db_for_user(user) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "DELETE FROM enrollments WHERE student_id = %s AND course_id = %s",
                (user["student_id"], course_id)
            )
        conn.commit()
    return {"ok": True}


@app.get("/api/my/tutor_capabilities")
async def get_my_tutor_capabilities(user: dict = Depends(require_auth)):
    """Get the current user's tutor capabilities (confidence > 0; 0 = dismissed)."""
    with get_db_for_user(user) as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT c.course_id, c.department, c.identifier, c.title, t.confidence
                FROM tutors t
                JOIN courses c ON t.course_id = c.course_id
                WHERE t.student_id = %s AND t.confidence > 0
                ORDER BY c.department, c.identifier
            """, (user["student_id"],))
            rows = cur.fetchall()
    return [
        {"course_id": r[0], "department": r[1], "identifier": r[2], "title": r[3], "confidence": r[4]}
        for r in rows
    ]


@app.get("/api/my/tutor_recommendations")
async def get_my_tutor_recommendations(user: dict = Depends(require_auth)):
    """Courses enrolled in past terms not already in tutors table (accepted or dismissed)."""
    with get_db_for_user(user) as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT DISTINCT c.course_id, c.department, c.identifier, c.title
                FROM enrollments e
                JOIN courses c ON e.course_id = c.course_id
                CROSS JOIN current_term ct
                WHERE e.student_id = %s
                  AND NOT ((e.term).academic_year = ct.academic_year
                       AND (e.term).season = ct.season)
                  AND NOT EXISTS (
                    SELECT 1 FROM tutors t
                    WHERE t.student_id = %s AND t.course_id = c.course_id
                  )
                ORDER BY c.department, c.identifier
            """, (user["student_id"], user["student_id"]))
            rows = cur.fetchall()
    return [
        {"course_id": r[0], "department": r[1], "identifier": r[2], "title": r[3]}
        for r in rows
    ]


@app.post("/api/my/tutor_dismiss/{course_id}")
async def dismiss_tutor_recommendation(course_id: int, user: dict = Depends(require_auth)):
    """Dismiss a recommendation so it never appears again (stored as confidence=0)."""
    with get_db_for_user(user) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """INSERT INTO tutors (student_id, course_id, confidence)
                   VALUES (%s, %s, 0)
                   ON CONFLICT (student_id, course_id) DO UPDATE SET confidence = 0""",
                (user["student_id"], course_id)
            )
        conn.commit()
    return {"ok": True}


@app.post("/api/my/tutor_capabilities")
async def set_my_tutor_capability(
    user: dict = Depends(require_auth),
    course_id: int = Form(...),
    confidence: int = Form(...)
):
    """Add or update a tutor capability."""
    if not 1 <= confidence <= 10:
        raise HTTPException(400, "Confidence must be between 1 and 10")
    with get_db_for_user(user) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """INSERT INTO tutors (student_id, course_id, confidence)
                   VALUES (%s, %s, %s)
                   ON CONFLICT (student_id, course_id) DO UPDATE SET confidence = EXCLUDED.confidence""",
                (user["student_id"], course_id, confidence)
            )
        conn.commit()
    return {"ok": True}


@app.delete("/api/my/tutor_capabilities/{course_id}")
async def remove_my_tutor_capability(course_id: int, user: dict = Depends(require_auth)):
    """Remove a tutor capability."""
    with get_db_for_user(user) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "DELETE FROM tutors WHERE student_id = %s AND course_id = %s",
                (user["student_id"], course_id)
            )
        conn.commit()
    return {"ok": True}


def _list_wipeble_terms() -> list[dict]:
    """Return all terms that have any wipeable data (backup files or course cache), newest first."""
    _LS = {'A': 'spring', 'B': 'fall'}
    _SR = {'spring': 1, 'fall': 2}
    if not os.path.isdir(DATA_DIR):
        return []
    terms = []
    for entry in os.listdir(DATA_DIR):
        term_dir = os.path.join(DATA_DIR, entry)
        if not os.path.isdir(term_dir):
            continue
        parts = entry.split('_')
        if len(parts) != 2 or not parts[0].isdigit() or parts[1] not in _LS:
            continue
        year, season = int(parts[0]), _LS[parts[1]]
        has_users = os.path.isfile(os.path.join(term_dir, 'users.json'))
        has_exams = os.path.isfile(os.path.join(term_dir, 'exams.json'))
        has_courses = os.path.isfile(os.path.join(term_dir, 'courses.json'))
        has_cache = course_cache_exists(DATA_DIR, year, season)
        if has_users or has_exams or has_courses or has_cache:
            terms.append({
                'term': entry, 'year': year, 'season': season,
                'label': f"{season.capitalize()} {year}",
                'has_users': has_users, 'has_exams': has_exams,
                'has_courses': has_courses, 'has_course_cache': has_cache,
            })
    terms.sort(key=lambda t: (t['year'], _SR[t['season']]), reverse=True)
    return terms


@app.get("/admin/portal", response_class=HTMLResponse)
async def admin_portal(request: Request, user: dict = Depends(require_admin), message: Optional[str] = None):
    """Admin portal - accessible only by admins."""
    with get_db_for_user(user) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """SELECT s.student_id, s.first_name, s.last_name, sa.is_admin, sa.is_root, s.graduated_date, s.discord_id
                   FROM students s
                   LEFT JOIN student_auth sa ON s.student_id = sa.student_id
                   ORDER BY sa.is_root DESC NULLS LAST, sa.is_admin DESC NULLS LAST,
                            (s.graduated_date IS NOT NULL), s.last_name, s.first_name"""
            )
            users = [
                {"student_id": r[0], "first_name": r[1], "last_name": r[2], "is_admin": r[3], "is_root": r[4], "graduated_date": r[5], "discord_id": r[6]}
                for r in cur.fetchall()
            ]
    current_term = None
    try:
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT academic_year, season FROM current_term")
                row = cur.fetchone()
                current_term = {"academic_year": row[0], "season": row[1]} if row else None
    except Exception:
        pass
    # Compute which data files exist for the current term
    _SL = {"spring": "A", "fall": "B"}
    ct_status = {"has_course_cache": False, "has_common_hour_pdf": False, "has_finals_pdf": False}
    if current_term:
        _yr = int(current_term["academic_year"])
        _s = str(current_term["season"])
        ct_status["has_course_cache"] = course_cache_exists(DATA_DIR, _yr, _s)
        _tdir = os.path.join(DATA_DIR, f"{_yr}_{_SL.get(_s, '?')}")
        ct_status["has_common_hour_pdf"] = os.path.isfile(os.path.join(_tdir, "common_hour.pdf"))
        ct_status["has_finals_pdf"] = os.path.isfile(os.path.join(_tdir, "finals.pdf"))
    return templates.TemplateResponse("admin_portal.html", {
        "request": request,
        "user": user,
        "user_profile": get_user_profile(user["student_id"]),
        "message": message,
        "users": users,
        "current_term": current_term,
        "current_term_status": ct_status,
        "wipe_terms": _list_wipeble_terms(),
    })


@app.post("/admin/set_admin", response_class=HTMLResponse)
async def set_admin(
    _: dict = Depends(require_admin),
    target_id: int = Form(...),
    make_admin: bool = Form(...)
):
    """Elevate or de-elevate a user's admin status. Root users cannot be modified."""
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT is_root FROM student_auth WHERE student_id = %s", (target_id,))
            result = cur.fetchone()
            if not result:
                raise HTTPException(status_code=404, detail="User not found")
            if result[0]:
                return RedirectResponse(
                    url="/admin/portal?message=Cannot+modify+root+user+privileges",
                    status_code=status.HTTP_302_FOUND
                )
            cur.execute(
                "UPDATE student_auth SET is_admin = %s WHERE student_id = %s",
                (make_admin, target_id)
            )
            cur.execute("DELETE FROM refresh_tokens WHERE student_id = %s", (target_id,))
        conn.commit()
    return RedirectResponse(url="/admin/portal", status_code=status.HTTP_302_FOUND)


@app.post("/admin/api/edit_user")
async def api_edit_user(
    background_tasks: BackgroundTasks,
    _: dict = Depends(require_admin),
    target_id: int = Form(...),
    first_name: str = Form(...),
    last_name: str = Form(...),
    discord_id: str = Form(default="")
):
    """Update a student's name and Discord ID."""
    if not validate_name(first_name):
        raise HTTPException(status_code=400, detail="First name contains invalid characters")
    if not validate_name(last_name):
        raise HTTPException(status_code=400, detail="Last name contains invalid characters")
    if discord_id and not discord_id.isdigit():
        raise HTTPException(status_code=400, detail="Discord ID must be numeric")
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT is_root FROM student_auth WHERE student_id = %s", (target_id,))
            result = cur.fetchone()
            if not result:
                raise HTTPException(status_code=404, detail="User not found")
            if result[0]:
                raise HTTPException(status_code=403, detail="Cannot edit root user")
            cur.execute(
                "SELECT discord_id FROM students WHERE student_id = %s", (target_id,)
            )
            old = cur.fetchone()
            old_discord = old[0] if old else None
            cur.execute(
                "UPDATE students SET first_name = %s, last_name = %s, discord_id = %s WHERE student_id = %s",
                (first_name.strip(), last_name.strip(), discord_id or None, target_id)
            )
        conn.commit()
    if discord_id and discord_id != old_discord:
        background_tasks.add_task(download_and_cache_avatar, target_id, discord_id)
    return {"ok": True}


@app.post("/admin/delete_user", response_class=HTMLResponse)
async def delete_user(
    user: dict = Depends(require_admin),
    target_id: int = Form(...)
):
    """Delete a student. Root users cannot be deleted."""
    if target_id == user["student_id"]:
        return RedirectResponse(url="/admin/portal?message=Cannot+delete+your+own+account", status_code=status.HTTP_302_FOUND)
    with get_db_for_user(user) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT is_root FROM student_auth WHERE student_id = %s", (target_id,))
            result = cur.fetchone()
            if result and result[0]:
                return RedirectResponse(url="/admin/portal?message=Cannot+delete+root+user", status_code=status.HTTP_302_FOUND)
            # Clear FK-constrained records first, then delete student (cascades to student_auth + refresh_tokens)
            cur.execute("DELETE FROM study_sessions WHERE tutor_student_id = %s", (target_id,))
            cur.execute("DELETE FROM tutors WHERE student_id = %s", (target_id,))
            cur.execute("DELETE FROM enrollments WHERE student_id = %s", (target_id,))
            cur.execute("DELETE FROM students WHERE student_id = %s", (target_id,))
        conn.commit()
    avatar_path = os.path.join(AVATAR_DIR, f"{target_id}.png")
    if os.path.exists(avatar_path):
        os.remove(avatar_path)
    return RedirectResponse(url="/admin/portal", status_code=status.HTTP_302_FOUND)


@app.post("/admin/set_graduated", response_class=HTMLResponse)
async def set_graduated(
    user: dict = Depends(require_admin),
    target_id: int = Form(...),
    graduated: bool = Form(...)
):
    """Set or clear a student's graduated_date. Root users cannot be modified."""
    with get_db_for_user(user) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT is_root FROM student_auth WHERE student_id = %s", (target_id,))
            result = cur.fetchone()
            if result and result[0]:
                return RedirectResponse(url="/admin/portal?message=Cannot+modify+root+user", status_code=status.HTTP_302_FOUND)
            if graduated:
                cur.execute("UPDATE students SET graduated_date = CURRENT_DATE WHERE student_id = %s", (target_id,))
            else:
                cur.execute("UPDATE students SET graduated_date = NULL WHERE student_id = %s", (target_id,))
        conn.commit()
    return RedirectResponse(url="/admin/portal", status_code=status.HTTP_302_FOUND)


@app.get("/api/users/all")
def all_users():
    """Return all users for client-side fuzzy search on the login page."""
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """SELECT s.student_id, s.first_name, s.last_name, s.discord_id
                   FROM students s
                   JOIN student_auth sa ON s.student_id = sa.student_id
                   ORDER BY s.last_name, s.first_name"""
            )
            return [
                {"student_id": r[0], "first_name": r[1], "last_name": r[2], "discord_id": r[3]}
                for r in cur.fetchall()
            ]


@app.get("/api/users/search")
def search_users(q: str = ""):
    """Return users matching a name query (for login autocomplete). Excludes root users."""
    if len(q) < 2:
        return []
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """SELECT s.student_id, s.first_name, s.last_name, s.discord_id
                   FROM students s
                   JOIN student_auth sa ON s.student_id = sa.student_id
                   WHERE CONCAT(s.first_name, ' ', s.last_name) ILIKE %s
                   ORDER BY s.last_name, s.first_name
                   LIMIT 8""",
                (f"%{q}%",)
            )
            return [
                {"student_id": r[0], "first_name": r[1], "last_name": r[2], "discord_id": r[3]}
                for r in cur.fetchall()
            ]


@app.get("/api/discord_avatar/{discord_id}")
def discord_avatar(discord_id: str):
    """Fetch a user's Discord avatar URL via the bot token."""
    token = (DISCORD_BOT_TOKEN or "").strip()
    if not token:
        return {"avatar_url": None, "error": "bot token not configured"}
    try:
        req = urllib.request.Request(
            f"https://discord.com/api/v10/users/{discord_id}",
            headers={
                "Authorization": f"Bot {token}",
                "User-Agent": "DiscordBot (https://github.com/discord/discord-api-docs, 10)",
            }
        )
        ctx = ssl.create_default_context(cafile=certifi.where())
        with urllib.request.urlopen(req, timeout=5, context=ctx) as resp:
            data = json.loads(resp.read())
        avatar_hash = data.get("avatar")
        if avatar_hash:
            ext = "gif" if avatar_hash.startswith("a_") else "png"
            return {"avatar_url": f"https://cdn.discordapp.com/avatars/{discord_id}/{avatar_hash}.{ext}?size=128"}
        default_index = int(discord_id) % 5
        return {"avatar_url": f"https://cdn.discordapp.com/embed/avatars/{default_index}.png"}
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")
        return {"avatar_url": None, "error": f"Discord API {e.code}: {body}"}
    except Exception as e:
        return {"avatar_url": None, "error": str(e)}



@app.post("/admin/backup")
async def backup_db(user: dict = Depends(require_admin)):
    """Create a new timestamped user backup. Root only."""
    if not user.get("is_root"):
        raise HTTPException(status_code=403)
    name = backup()
    return {"ok": True, "name": name}


@app.get("/admin/api/backups")
def get_backups(user: dict = Depends(require_admin)):
    """List all user backups. Root only."""
    if not user.get("is_root"):
        raise HTTPException(status_code=403)
    return list_backups()


@app.post("/admin/api/restore_backup")
async def restore_backup(user: dict = Depends(require_admin), backup_name: str = Form(...)):
    """Restore from a specific backup. Root only."""
    if not user.get("is_root"):
        raise HTTPException(status_code=403)
    restored = restore_from_disk(user, backup_name)
    return {"ok": restored}


@app.delete("/admin/api/backup/{backup_name}")
def delete_backup_endpoint(backup_name: str, user: dict = Depends(require_admin)):
    """Delete a backup by name. Root only."""
    if not user.get("is_root"):
        raise HTTPException(status_code=403)
    ok = delete_backup(backup_name)
    return {"ok": ok}


@app.post("/admin/wipe_selective", response_class=HTMLResponse)
async def wipe_selective(request: Request, user: dict = Depends(require_admin)):
    """Wipe selected data types from selected terms. Root only."""
    if not user.get("is_root"):
        return RedirectResponse(url="/admin/portal", status_code=status.HTTP_302_FOUND)
    form = await request.form()
    what_list = form.getlist("what")   # "exams", "courses", "course_cache"
    term_list = form.getlist("term")   # dir names like "2026_A"
    if not what_list or not term_list:
        return RedirectResponse(url="/admin/portal?message=Nothing+selected", status_code=status.HTTP_302_FOUND)
    _LS = {'A': 'spring', 'B': 'fall'}
    file_map = {"exams": "exams.json", "courses": "courses.json"}
    wiped = 0
    for term_name in term_list:
        parts = term_name.split('_')
        if len(parts) != 2 or not parts[0].isdigit() or parts[1] not in _LS:
            continue
        year, season = int(parts[0]), _LS[parts[1]]
        term_dir = os.path.join(DATA_DIR, term_name)
        for what in what_list:
            if what in file_map:
                fpath = os.path.join(term_dir, file_map[what])
                if os.path.isfile(fpath):
                    os.remove(fpath)
                    wiped += 1
            elif what == "course_cache":
                wiped += wipe_course_cache_files(DATA_DIR, year, season)
    msg = f"Wiped {wiped} item(s)" if wiped else "Nothing to wipe"
    return RedirectResponse(url="/admin/portal?" + urlencode({"message": msg}), status_code=status.HTTP_302_FOUND)


@app.post("/admin/refresh_course_cache", response_class=HTMLResponse)
async def refresh_course_cache(_: dict = Depends(require_admin)):
    """Wipe the current term's course cache so next preview re-fetches from the web."""
    try:
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT academic_year, season FROM current_term")
                row = cur.fetchone()
    except Exception:
        row = None
    if not row:
        return RedirectResponse(
            url="/admin/portal?message=Cannot+determine+current+term",
            status_code=status.HTTP_302_FOUND
        )
    yr, s = int(row[0]), str(row[1])
    count = wipe_course_cache_files(DATA_DIR, yr, s)
    msg = f"Cache cleared ({count} file(s) deleted). Use Fetch & Preview to re-download from web."
    return RedirectResponse(
        url="/admin/portal?" + urlencode({"message": msg}),
        status_code=status.HTTP_302_FOUND
    )


_EXAM_TYPE_LABELS = {
    "common_hour": "Common Hour Exam",
    "final": "Final Exam",
}


@app.post("/admin/api/preview_exam_pdf")
async def preview_exam_pdf(
    _: dict = Depends(require_admin),
    pdf_file: UploadFile = File(...),
    exam_type: str = Form(...),
):
    """Parse an uploaded exam PDF and return a preview of what would be imported."""
    if exam_type not in _EXAM_TYPE_LABELS:
        raise HTTPException(status_code=400, detail="Invalid exam type")
    if not pdf_file.filename.lower().endswith(".pdf"):
        raise HTTPException(status_code=400, detail="File must be a PDF")

    pdf_bytes = await pdf_file.read()
    try:
        if exam_type == "common_hour":
            entries = parse_common_hour_pdf(pdf_bytes)
        else:
            entries = parse_finals_pdf(pdf_bytes)
    except Exception as e:
        raise HTTPException(status_code=422, detail=f"Failed to parse PDF: {e}")

    # Enrich each entry with DB info: course title and whether it's a duplicate
    results = []
    with get_db() as conn:
        with conn.cursor() as cur:
            for entry in entries:
                cur.execute(
                    """SELECT course_id, title FROM courses
                       WHERE department ILIKE %s AND identifier = %s
                       ORDER BY (last_offered).academic_year DESC,
                                CASE (last_offered).season
                                    WHEN 'fall' THEN 2 WHEN 'spring' THEN 1
                                END DESC
                       LIMIT 1""",
                    (entry["department"], entry["identifier"])
                )
                course_row = cur.fetchone()
                course_id = course_row[0] if course_row else None
                title = course_row[1] if course_row else None

                duplicate = False
                if course_id:
                    cur.execute(
                        """SELECT 1 FROM exams
                           WHERE course_id = %s AND test_date = %s AND exam_type = %s""",
                        (course_id, entry["date"], exam_type)
                    )
                    duplicate = cur.fetchone() is not None

                results.append({
                    "department": entry["department"],
                    "identifier": entry["identifier"],
                    "title": title,
                    "date": entry["date"],
                    "found": course_id is not None,
                    "duplicate": duplicate,
                })

    return {"entries": results, "exam_type": exam_type}


@app.post("/admin/import_exams", response_class=HTMLResponse)
async def import_exams(
    user: dict = Depends(require_admin),
    entries_json: str = Form(...),
    exam_type: str = Form(...),
    pdf_b64: str = Form(default=""),
):
    """Insert confirmed exam entries into the database."""
    if exam_type not in _EXAM_TYPE_LABELS:
        return RedirectResponse(url="/admin/portal?message=Invalid+exam+type", status_code=status.HTTP_302_FOUND)
    try:
        entries = json.loads(entries_json)
    except Exception:
        return RedirectResponse(url="/admin/portal?message=Invalid+data", status_code=status.HTTP_302_FOUND)

    inserted = 0
    with get_db_for_user(user) as conn:
        with conn.cursor() as cur:
            for e in entries:
                dept = e.get("department", "")
                ident = e.get("identifier", "")
                date = e.get("date", "")
                if not (dept and ident and date):
                    continue
                # Re-look up course server-side (never trust client-supplied course_id)
                cur.execute(
                    """SELECT course_id FROM courses
                       WHERE department ILIKE %s AND identifier = %s
                       ORDER BY (last_offered).academic_year DESC,
                                CASE (last_offered).season
                                    WHEN 'fall' THEN 2 WHEN 'spring' THEN 1
                                END DESC
                       LIMIT 1""",
                    (dept, ident)
                )
                row = cur.fetchone()
                if not row:
                    continue
                cur.execute(
                    """INSERT INTO exams (course_id, test_date, exam_type, creator_id)
                       VALUES (%s, %s::date, %s::exam_type, %s)
                       ON CONFLICT (course_id, test_date, exam_type) DO NOTHING""",
                    (row[0], date, exam_type, user["student_id"])
                )
                if cur.rowcount:
                    inserted += 1
        conn.commit()

    # Save PDF to disk now that the user has confirmed the import
    if pdf_b64 and inserted > 0:
        _PDF_FILENAME = {"common_hour": "common_hour.pdf", "final": "finals.pdf"}
        pdf_filename = _PDF_FILENAME.get(exam_type, f"{exam_type}.pdf")
        try:
            import base64 as _b64
            pdf_bytes = _b64.b64decode(pdf_b64)
            first_date = min(e["date"] for e in entries if e.get("date"))
            dt = datetime.strptime(first_date, "%Y-%m-%d")
            s = "spring" if dt.month <= 6 else "fall"
            _SL = {"spring": "A", "fall": "B"}
            pdf_term_dir = os.path.join(DATA_DIR, f"{dt.year}_{_SL[s]}")
            os.makedirs(pdf_term_dir, exist_ok=True)
            with open(os.path.join(pdf_term_dir, pdf_filename), "wb") as _f:
                _f.write(pdf_bytes)
        except Exception:
            pass  # Never block redirect if PDF save fails

    return RedirectResponse(
        url="/admin/portal?" + urlencode({"message": f"Imported {inserted} exam(s) successfully"}),
        status_code=status.HTTP_302_FOUND
    )


_VALID_SEASONS = {"spring", "fall"}

# Title similarity threshold for treating two course rows as the same course.
# SequenceMatcher ratio is 0–1 (1 = identical). 0.6 allows minor wording changes
# while keeping genuinely different courses (e.g. renamed courses) separate.
_TITLE_SIM_THRESHOLD = 0.6


def _title_sim(a: str | None, b: str | None) -> float:
    """Return SequenceMatcher similarity ratio (0–1) between two titles.
    If either title is missing, returns 1.0 (assume same course)."""
    if not a or not b:
        return 1.0
    return _SM(None, a.lower().strip(), b.lower().strip()).ratio()


def _load_existing_courses(cur) -> dict[tuple, list[dict]]:
    """Return all courses keyed by (department.upper(), identifier).
    Each value is a list of dicts with course_id, title, year, season."""
    cur.execute("""
        SELECT course_id, department, identifier, title,
               (last_offered).academic_year, (last_offered).season
        FROM courses
    """)
    result: dict[tuple, list] = {}
    for course_id, dept, ident, title, year, season in cur.fetchall():
        key = (dept.upper(), ident)
        result.setdefault(key, []).append({
            "course_id": course_id,
            "title": title,
            "year": int(year),
            "season": str(season),
        })
    return result


def _classify_course(c: dict, academic_year: int, season: str,
                     existing: dict[tuple, list[dict]]) -> tuple[str, dict | None]:
    """Classify an incoming course as 'new', 'update', or 'already_current'.
    Returns (status, best_match_or_None)."""
    key = (c["department"].upper(), c["identifier"])
    matches = existing.get(key, [])
    best_ratio = 0.0
    best_match: dict | None = None
    for m in matches:
        r = _title_sim(c.get("title"), m["title"])
        if r > best_ratio:
            best_ratio = r
            best_match = m
    if not best_match or best_ratio < _TITLE_SIM_THRESHOLD:
        return "new", None
    if best_match["year"] == academic_year and best_match["season"] == season:
        return "already_current", best_match
    return "update", best_match


@app.post("/admin/api/preview_courses")
async def preview_courses(
    _: dict = Depends(require_admin),
    academic_year: int = Form(...),
    season: str = Form(...),
):
    """Fetch courses fresh from web into pending dir (does not touch confirmed data)."""
    global _course_pending_cleanup_task
    if season not in _VALID_SEASONS:
        raise HTTPException(status_code=400, detail="Invalid season")

    # Cancel any previous expiry task before starting a fresh fetch
    if _course_pending_cleanup_task and not _course_pending_cleanup_task.done():
        _course_pending_cleanup_task.cancel()

    courses, errors = await fetch_courses(DATA_DIR, academic_year, season)
    source = "web"

    # Schedule automatic deletion of the pending dir after TTL
    _course_pending_cleanup_task = asyncio.create_task(
        _expire_course_pending(academic_year, season)
    )

    with get_db() as conn:
        with conn.cursor() as cur:
            existing = _load_existing_courses(cur)

    dept_counts: dict[str, dict] = {}
    new_count = update_count = already_count = 0
    for c in courses:
        dept = c["department"]
        if dept not in dept_counts:
            dept_counts[dept] = {"total": 0, "new": 0, "update": 0}
        dept_counts[dept]["total"] += 1
        action, _ = _classify_course(c, academic_year, season, existing)
        if action == "new":
            dept_counts[dept]["new"] += 1
            new_count += 1
        elif action == "update":
            dept_counts[dept]["update"] += 1
            update_count += 1
        else:
            already_count += 1

    return {
        "source": source,
        "total": len(courses),
        "new": new_count,
        "update": update_count,
        "already_current": already_count,
        "departments": dept_counts,
        "errors": errors,
    }


@app.post("/admin/import_courses", response_class=HTMLResponse)
async def import_courses(
    user: dict = Depends(require_admin),
    academic_year: int = Form(...),
    season: str = Form(...),
):
    """Insert courses from the pending preview into the DB, then save data to disk."""
    if season not in _VALID_SEASONS:
        return RedirectResponse(
            url="/admin/portal?message=Invalid+season", status_code=status.HTTP_302_FOUND
        )
    if not course_pending_cache_exists(DATA_DIR, academic_year, season):
        return RedirectResponse(
            url="/admin/portal?message=No+preview+data+found.+Use+Fetch+%26+Preview+first.",
            status_code=status.HTTP_302_FOUND,
        )

    courses = load_courses_from_cache(DATA_DIR, academic_year, season, pending=True)
    inserted = updated = 0
    with get_db_for_user(user) as conn:
        with conn.cursor() as cur:
            existing = _load_existing_courses(cur)
            for c in courses:
                action, best_match = _classify_course(c, academic_year, season, existing)
                if action == "update":
                    # Same course in a different term — bump last_offered and adopt new title
                    new_title = c.get("title") or best_match["title"]
                    cur.execute(
                        """UPDATE courses
                           SET last_offered = ROW(%s, %s)::academic_term, title = %s
                           WHERE course_id = %s""",
                        (academic_year, season, new_title, best_match["course_id"]),
                    )
                    if cur.rowcount:
                        updated += 1
                elif action == "new":
                    cur.execute(
                        """INSERT INTO courses (department, identifier, title, semester_hours, last_offered)
                           VALUES (%s, %s, %s, %s, ROW(%s, %s)::academic_term)""",
                        (c["department"], c["identifier"], c.get("title"),
                         c.get("semester_hours"), academic_year, season),
                    )
                    if cur.rowcount:
                        inserted += 1
                # already_current → no action needed
        conn.commit()

    # Cancel the pending expiry task — we're promoting, not expiring
    if _course_pending_cleanup_task and not _course_pending_cleanup_task.done():
        _course_pending_cleanup_task.cancel()

    # Commit pending data to disk only after successful DB import
    try:
        promote_pending_cache(DATA_DIR, academic_year, season)
    except Exception:
        pass  # Never block redirect if promotion fails

    parts = []
    if inserted:
        parts.append(f"{inserted} new")
    if updated:
        parts.append(f"{updated} updated")
    summary = (", ".join(parts) + f" course(s) for {season.capitalize()} {academic_year}"
               if parts else f"Nothing to import for {season.capitalize()} {academic_year}")
    return RedirectResponse(
        url="/admin/portal?" + urlencode({"message": summary}),
        status_code=status.HTTP_302_FOUND,
    )


@app.get("/admin/api/calendar_exams")
async def calendar_exams(_: dict = Depends(require_admin)):
    """Return all exams with course info for the calendar view."""
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT e.test_date, e.exam_type, c.department, c.identifier, c.title
                FROM exams e
                JOIN courses c ON e.course_id = c.course_id
                ORDER BY e.test_date, c.department, c.identifier
            """)
            return [
                {
                    "date": str(r[0]),
                    "exam_type": r[1],
                    "department": r[2],
                    "identifier": r[3],
                    "title": r[4],
                }
                for r in cur.fetchall()
            ]


@app.get("/api/courses")
async def get_all_courses():
    """Return courses offered in the current term for client-side fuzzy search."""
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT c.course_id, c.department, c.identifier, c.title, c.semester_hours
                FROM courses c, current_term ct
                WHERE (c.last_offered).academic_year = ct.academic_year
                  AND (c.last_offered).season = ct.season
                ORDER BY c.department, c.identifier
            """)
            return [
                {
                    "course_id": r[0],
                    "department": r[1],
                    "identifier": r[2],
                    "combined": f"{r[1]}{r[2]}",
                    "title": html.unescape(r[3]) if r[3] else r[3],
                    "semester_hours": r[4],
                }
                for r in cur.fetchall()
            ]


@app.get("/admin/api/validate_discord/{discord_id}")
def validate_discord_endpoint(discord_id: str, _: dict = Depends(require_admin)):
    """Validate a Discord ID and return user info for the create-user form preview."""
    token = (DISCORD_BOT_TOKEN or "").strip()
    if not token:
        return {"status": "no_token"}
    if not discord_id.isdigit():
        return {"status": "invalid", "error": "Discord ID must be numeric"}
    try:
        ctx = ssl.create_default_context(cafile=certifi.where())
        req = urllib.request.Request(
            f"https://discord.com/api/v10/users/{discord_id}",
            headers={
                "Authorization": f"Bot {token}",
                "User-Agent": "DiscordBot (https://github.com/discord/discord-api-docs, 10)",
            }
        )
        with urllib.request.urlopen(req, timeout=5, context=ctx) as resp:
            data = json.loads(resp.read())
        avatar_hash = data.get("avatar")
        if not avatar_hash:
            return {"status": "invalid", "error": "That Discord account has no profile picture"}
        avatar_url = f"https://cdn.discordapp.com/avatars/{discord_id}/{avatar_hash}.png?size=128"
        return {
            "status": "valid",
            "avatar_url": avatar_url,
            "username": data.get("global_name") or data.get("username", ""),
        }
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return {"status": "invalid", "error": "Discord user not found"}
        return {"status": "invalid", "error": "Could not verify Discord ID"}
    except Exception:
        return {"status": "invalid", "error": "Could not verify Discord ID"}


@app.post("/admin/create_user", response_class=HTMLResponse)
async def create_user(
    background_tasks: BackgroundTasks,
    user: dict = Depends(require_admin),
    first_name: str = Form(...),
    last_name: str = Form(...),
    discord_id: str = Form(...)
):
    """Create a new student user."""
    if not validate_name(first_name):
        return RedirectResponse(url="/admin/portal?message=First+name+contains+invalid+characters", status_code=status.HTTP_302_FOUND)
    if not validate_name(last_name):
        return RedirectResponse(url="/admin/portal?message=Last+name+contains+invalid+characters", status_code=status.HTTP_302_FOUND)
    if not discord_id.isdigit():
        return RedirectResponse(url="/admin/portal?message=Discord+ID+must+be+numeric", status_code=status.HTTP_302_FOUND)
    is_valid, err = validate_discord_id(discord_id)
    if not is_valid:
        return RedirectResponse(url="/admin/portal?" + urlencode({"message": err}), status_code=status.HTTP_302_FOUND)
    with get_db_for_user(user) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO students (first_name, last_name, discord_id) VALUES (%s, %s, %s) RETURNING student_id",
                (first_name, last_name, discord_id)
            )
            student_id = cur.fetchone()[0]
            cur.execute(
                "INSERT INTO student_auth (student_id, hashed_password) VALUES (%s, %s)",
                (student_id, get_password_hash(USER_PASSWORD))
            )
        conn.commit()
    if discord_id:
        background_tasks.add_task(download_and_cache_avatar, student_id, discord_id)
    return RedirectResponse(url="/admin/portal?message=User+created+successfully", status_code=status.HTTP_302_FOUND)


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
