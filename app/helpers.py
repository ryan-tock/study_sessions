import os
import re
import unicodedata
from typing import Optional
from fastapi.templating import Jinja2Templates
from .database import get_db

_ALLOWED_NAME_EXTRAS = set(" -'.\u2018\u2019")

_VALID_SHARING = {"closed", "common_class", "open"}


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

templates = Jinja2Templates(directory=os.path.join(current_dir, "templates"))
