import os
from fastapi import FastAPI, Depends, HTTPException, status, Request, Response, Form, BackgroundTasks
from fastapi.templating import Jinja2Templates
from fastapi.staticfiles import StaticFiles
from fastapi.responses import RedirectResponse, HTMLResponse, FileResponse
from datetime import datetime, timedelta, timezone
from jose import JWTError, jwt

from .auth import (
    authenticate_user, authenticate_root_user, create_access_token, create_refresh_token,
    verify_refresh_token, revoke_refresh_token, get_password_hash, verify_password
)
from .config import SECRET_KEY, ALGORITHM, DISCORD_BOT_TOKEN, USER_PASSWORD
from .database import get_db
from typing import Optional
import urllib.request
import urllib.error
import ssl
import certifi
import json
import re


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
    """Fetch a user's display info (name + discord_id) from the DB."""
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT first_name, last_name, discord_id FROM students WHERE student_id = %s",
                (student_id,)
            )
            row = cur.fetchone()
    if row:
        return {"first_name": row[0], "last_name": row[1], "discord_id": row[2]}
    return {"first_name": "", "last_name": "", "discord_id": None}

current_dir = os.path.dirname(os.path.abspath(__file__))
AVATAR_DIR = os.path.join(current_dir, "static", "avatars")
os.makedirs(AVATAR_DIR, exist_ok=True)


app = FastAPI()

# Setup templates and static files with absolute paths
templates = Jinja2Templates(directory=os.path.join(current_dir, "templates"))
app.mount("/static", StaticFiles(directory=os.path.join(current_dir, "static")), name="static")


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

        with get_db() as conn:
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
async def root(request: Request):
    """Root route - redirects to appropriate portal if logged in, else shows login."""
    user = get_current_user(request)
    if user:
        if user["is_admin"]:
            return RedirectResponse(url="/admin/portal")
        else:
            return RedirectResponse(url="/user/portal")
    return templates.TemplateResponse("login.html", {"request": request})


@app.post("/login")
async def login(
    response: Response,
    background_tasks: BackgroundTasks,
    first_name: str = Form(...),
    last_name: str = Form(...),
    password: str = Form(...)
):
    """Handle login form submission."""
    user = authenticate_user(first_name, last_name, password)
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect name or password",
            headers={"WWW-Authenticate": "Bearer"},
        )
    
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


@app.post("/root_login")
async def root_login(response: Response, password: str = Form(...)):
    """Handle root login — password only."""
    user = authenticate_root_user(password)
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid root password",
            headers={"WWW-Authenticate": "Bearer"},
        )

    access_token = create_access_token(
        data={"sub": str(user["student_id"]), "is_admin": True, "is_root": True},
        expires_delta=timedelta(minutes=30)
    )
    refresh_token = create_refresh_token(user["student_id"])

    response = RedirectResponse(url="/admin/portal", status_code=status.HTTP_302_FOUND)
    response.set_cookie(key="access_token", value=f"Bearer {access_token}", httponly=True, max_age=1800, samesite="lax")
    response.set_cookie(key="refresh_token", value=refresh_token, httponly=True, max_age=604800, samesite="lax", path="/")
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
    """User portal - accessible by all authenticated users."""
    return templates.TemplateResponse("user_portal.html", {
        "request": request,
        "user": user,
        "user_profile": get_user_profile(user["student_id"]),
        "message": message
    })


@app.get("/user/set_password", response_class=HTMLResponse)
async def get_set_password(request: Request, user: dict = Depends(require_auth)):
    """First-login password setup page."""
    return templates.TemplateResponse("set_password.html", {"request": request, "user": user})


@app.post("/user/set_password", response_class=HTMLResponse)
async def post_set_password(user: dict = Depends(require_auth), new_password: str = Form(...)):
    """Handle first-login password setup. Re-issues token with is_first_login=False."""
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE student_auth SET hashed_password = %s WHERE student_id = %s",
                (get_password_hash(new_password), user["student_id"])
            )
        conn.commit()
    access_token = create_access_token(
        data={"sub": str(user["student_id"]), "is_admin": user["is_admin"], "is_root": user["is_root"], "is_first_login": False},
        expires_delta=timedelta(minutes=30)
    )
    redirect_url = "/admin/portal" if user["is_admin"] else "/user/portal"
    response = RedirectResponse(url=f"{redirect_url}?message=Password+set+successfully", status_code=status.HTTP_302_FOUND)
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


@app.get("/admin/portal", response_class=HTMLResponse)
async def admin_portal(request: Request, user: dict = Depends(require_admin), message: Optional[str] = None):
    """Admin portal - accessible only by admins."""
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """SELECT s.student_id, s.first_name, s.last_name, sa.is_admin, sa.is_root
                   FROM students s
                   LEFT JOIN student_auth sa ON s.student_id = sa.student_id
                   ORDER BY sa.is_root DESC NULLS LAST, sa.is_admin DESC NULLS LAST, s.last_name, s.first_name"""
            )
            users = [
                {"student_id": r[0], "first_name": r[1], "last_name": r[2], "is_admin": r[3], "is_root": r[4]}
                for r in cur.fetchall()
            ]
    return templates.TemplateResponse("admin_portal.html", {
        "request": request,
        "user": user,
        "user_profile": get_user_profile(user["student_id"]),
        "message": message,
        "users": users
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
        conn.commit()
    action = "elevated+to+admin" if make_admin else "removed+from+admin"
    return RedirectResponse(url=f"/admin/portal?message=User+{action}", status_code=status.HTTP_302_FOUND)


@app.post("/admin/delete_user", response_class=HTMLResponse)
async def delete_user(
    user: dict = Depends(require_admin),
    target_id: int = Form(...)
):
    """Delete a student. Root users cannot be deleted."""
    if target_id == user["student_id"]:
        return RedirectResponse(url="/admin/portal?message=Cannot+delete+your+own+account", status_code=status.HTTP_302_FOUND)
    with get_db() as conn:
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
    return RedirectResponse(url="/admin/portal?message=User+deleted", status_code=status.HTTP_302_FOUND)


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
                   WHERE sa.is_root = FALSE
                   AND CONCAT(s.first_name, ' ', s.last_name) ILIKE %s
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


@app.post("/admin/create_user", response_class=HTMLResponse)
async def create_user(
    background_tasks: BackgroundTasks,
    _: dict = Depends(require_admin),
    first_name: str = Form(...),
    last_name: str = Form(...),
    discord_id: str = Form(...)
):
    """Create a new student user."""
    with get_db() as conn:
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
