import html
import json
import ssl
import urllib.error
import urllib.request
from fastapi import APIRouter
import certifi

from ..config import DISCORD_BOT_TOKEN
from ..database import get_db

router = APIRouter()


@router.get("/api/users/all")
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


@router.get("/api/users/search")
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


@router.get("/api/discord_avatar/{discord_id}")
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


@router.get("/api/courses")
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
