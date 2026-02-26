import os
import json
from .database import get_db, get_db_for_user
from .auth import get_password_hash
from .config import USER_PASSWORD

_APP_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(_APP_DIR, '..', 'data')

SEASON_LETTER = {'spring': 'A', 'summer': 'B', 'fall': 'C'}


def _term_dir(academic_year: int, season: str) -> str:
    letter = SEASON_LETTER.get(season, '?')
    return os.path.join(DATA_DIR, f"{academic_year}_{letter}")


def backup(academic_year: int, season: str) -> None:
    """Save current term and all non-root users to disk."""
    os.makedirs(DATA_DIR, exist_ok=True)

    # Save current term
    with open(os.path.join(DATA_DIR, 'current_term.json'), 'w') as f:
        json.dump({'academic_year': academic_year, 'season': season}, f, indent=2)

    # Save users
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT s.first_name, s.last_name, s.discord_id, sa.is_admin,
                       sa.hashed_password, sa.last_login, s.graduated_date
                FROM students s
                JOIN student_auth sa ON s.student_id = sa.student_id
                WHERE sa.is_root = FALSE
                ORDER BY s.last_name, s.first_name
            """)
            rows = cur.fetchall()

    users = [
        {
            'first_name': r[0],
            'last_name': r[1],
            'discord_id': r[2],
            'is_admin': bool(r[3]),
            'hashed_password': r[4],
            'last_login': r[5].isoformat() if r[5] else None,
            'graduated_date': r[6].isoformat() if r[6] else None,
        }
        for r in rows
    ]

    term_dir = _term_dir(academic_year, season)
    os.makedirs(term_dir, exist_ok=True)
    with open(os.path.join(term_dir, 'users.json'), 'w') as f:
        json.dump(users, f, indent=2)


def restore_from_disk(user: dict) -> bool:
    """
    Restore current term and users from the most recent backup.
    Requires an admin user dict for RLS (students/current_term tables enforce is_admin()).
    Returns True if data was restored, False if no backup found.
    """
    cterm_path = os.path.join(DATA_DIR, 'current_term.json')
    if not os.path.exists(cterm_path):
        return False

    with open(cterm_path) as f:
        term_data = json.load(f)

    academic_year = term_data['academic_year']
    season = term_data['season']

    with get_db_for_user(user) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """INSERT INTO current_term (id, term) VALUES (TRUE, ROW(%s, %s)::academic_term)
                   ON CONFLICT (id) DO UPDATE SET term = ROW(%s, %s)::academic_term""",
                (academic_year, season, academic_year, season)
            )
        conn.commit()

    users_file = os.path.join(_term_dir(academic_year, season), 'users.json')
    if not os.path.exists(users_file):
        return True

    with open(users_file) as f:
        users = json.load(f)

    if not users:
        return True

    fallback_pw = get_password_hash(USER_PASSWORD)
    with get_db_for_user(user) as conn:
        with conn.cursor() as cur:
            for u in users:
                cur.execute(
                    """INSERT INTO students (first_name, last_name, discord_id, graduated_date)
                       VALUES (%s, %s, %s, %s::date) RETURNING student_id""",
                    (u['first_name'], u.get('last_name'), u.get('discord_id'),
                     u.get('graduated_date'))
                )
                student_id = cur.fetchone()[0]
                hashed_pw = u.get('hashed_password') or fallback_pw
                cur.execute(
                    """INSERT INTO student_auth (student_id, hashed_password, is_admin, last_login)
                       VALUES (%s, %s, %s, %s::timestamptz)""",
                    (student_id, hashed_pw, bool(u.get('is_admin', False)),
                     u.get('last_login'))
                )
        conn.commit()

    return True
