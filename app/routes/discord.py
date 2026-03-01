import json
import os
import ssl
import urllib.error
import urllib.request
from fastapi import APIRouter, Response
from fastapi.responses import FileResponse
import certifi

from ..config import DISCORD_BOT_TOKEN
from ..database import get_db, get_db_for_user
from ..helpers import AVATAR_DIR

router = APIRouter()


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


@router.get("/avatar/{student_id}")
def serve_avatar(student_id: int):
    """Serve a cached avatar with browser cache headers."""
    path = os.path.join(AVATAR_DIR, f"{student_id}.png")
    if os.path.exists(path):
        return FileResponse(path, headers={"Cache-Control": "public, max-age=86400"})
    return Response(status_code=404)


@router.post("/api/avatar/fetch/{student_id}")
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
