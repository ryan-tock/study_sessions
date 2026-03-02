import json
import os
import ssl
import urllib.error
import urllib.request
from difflib import SequenceMatcher as _SM
from typing import Optional
from urllib.parse import urlencode
from fastapi import APIRouter, BackgroundTasks, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
import certifi

from ..auth import get_password_hash
from ..config import DISCORD_BOT_TOKEN, USER_PASSWORD
from ..database import get_db, get_db_for_user
from ..dependencies import require_admin
from ..helpers import (
    AVATAR_DIR, DATA_DIR, get_user_profile, templates, validate_name,
)
from ..course_scraper import cache_exists as course_cache_exists
from .discord import download_and_cache_avatar, validate_discord_id

router = APIRouter()


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


@router.get("/admin/portal", response_class=HTMLResponse)
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
    # Auto-apply confidence decay for graduated tutors (once per term)
    if current_term:
        try:
            _yr = int(current_term["academic_year"])
            _sn = str(current_term["season"])
            with get_db() as conn_decay:
                with conn_decay.cursor() as cur_decay:
                    cur_decay.execute(
                        "SELECT 1 FROM confidence_decay_log WHERE academic_year = %s AND season = %s::term_season",
                        (_yr, _sn)
                    )
                    if not cur_decay.fetchone():
                        # Decrease confidence by 1 for all graduated tutors
                        cur_decay.execute("""
                            UPDATE tutors SET confidence = confidence - 1
                            FROM students s
                            WHERE tutors.student_id = s.student_id
                              AND s.graduated_date IS NOT NULL
                              AND tutors.confidence > 0
                        """)
                        # Remove tutors where confidence hit 0
                        cur_decay.execute("""
                            DELETE FROM tutors
                            USING students s
                            WHERE tutors.student_id = s.student_id
                              AND s.graduated_date IS NOT NULL
                              AND tutors.confidence <= 0
                        """)
                        # Mark this term as processed
                        cur_decay.execute(
                            "INSERT INTO confidence_decay_log (academic_year, season) VALUES (%s, %s::term_season)",
                            (_yr, _sn)
                        )
                conn_decay.commit()
        except Exception:
            pass
    # Compute which data files exist for the current term + DB status
    _SL = {"spring": "A", "fall": "B"}
    ct_status = {
        "has_course_cache": False, "has_common_hour_pdf": False, "has_finals_pdf": False,
        "courses_in_db": 0, "common_hour_in_db": 0, "finals_in_db": 0,
    }
    if current_term:
        _yr = int(current_term["academic_year"])
        _s = str(current_term["season"])
        ct_status["has_course_cache"] = course_cache_exists(DATA_DIR, _yr, _s)
        _tdir = os.path.join(DATA_DIR, f"{_yr}_{_SL.get(_s, '?')}")
        ct_status["has_common_hour_pdf"] = os.path.isfile(os.path.join(_tdir, "common_hour.pdf"))
        ct_status["has_finals_pdf"] = os.path.isfile(os.path.join(_tdir, "finals.pdf"))
        try:
            with get_db() as conn2:
                with conn2.cursor() as cur2:
                    cur2.execute(
                        """SELECT COUNT(*) FROM courses
                           WHERE (last_offered).academic_year = %s
                             AND (last_offered).season = %s::term_season""",
                        (_yr, _s)
                    )
                    ct_status["courses_in_db"] = cur2.fetchone()[0]
                    if _s == "spring":
                        date_cond = "EXTRACT(YEAR FROM e.test_date)::int = %s AND EXTRACT(MONTH FROM e.test_date) <= 6"
                    else:
                        date_cond = "EXTRACT(YEAR FROM e.test_date)::int = %s AND EXTRACT(MONTH FROM e.test_date) > 6"
                    cur2.execute(f"""
                        SELECT COUNT(*) FROM exams e
                        WHERE NOT e.deleted AND e.exam_type = 'common_hour'
                          AND {date_cond}
                    """, (_yr,))
                    ct_status["common_hour_in_db"] = cur2.fetchone()[0]
                    cur2.execute(f"""
                        SELECT COUNT(*) FROM exams e
                        WHERE NOT e.deleted AND e.exam_type = 'final'
                          AND {date_cond}
                    """, (_yr,))
                    ct_status["finals_in_db"] = cur2.fetchone()[0]
        except Exception:
            pass
    # Determine which users have enrollments for the current term
    enrolled_student_ids: set[int] = set()
    if current_term:
        try:
            with get_db() as conn3:
                with conn3.cursor() as cur3:
                    cur3.execute("""
                        SELECT DISTINCT student_id FROM enrollments
                        WHERE (term).academic_year = %s
                          AND (term).season = %s::term_season
                    """, (int(current_term["academic_year"]), str(current_term["season"])))
                    enrolled_student_ids = {r[0] for r in cur3.fetchall()}
        except Exception:
            pass
    for u in users:
        u["has_schedule"] = u["student_id"] in enrolled_student_ids

    # Determine if the new-semester checklist should show
    show_checklist = False
    if current_term:
        try:
            with get_db() as conn_cl:
                with conn_cl.cursor() as cur_cl:
                    cur_cl.execute(
                        "SELECT (last_seen_term).academic_year, (last_seen_term).season FROM student_auth WHERE student_id = %s",
                        (user["student_id"],)
                    )
                    row_cl = cur_cl.fetchone()
                    if row_cl and (row_cl[0] is None or row_cl[1] is None):
                        show_checklist = True
                    elif row_cl and (int(row_cl[0]) != int(current_term["academic_year"]) or str(row_cl[1]) != str(current_term["season"])):
                        show_checklist = True
                    elif not row_cl:
                        show_checklist = True
        except Exception:
            pass

    return templates.TemplateResponse(request, "admin_portal.html", {
        "user": user,
        "user_profile": get_user_profile(user["student_id"]),
        "message": message,
        "users": users,
        "current_term": current_term,
        "current_term_status": ct_status,
        "wipe_terms": _list_wipeble_terms(),
        "show_checklist": show_checklist,
    })


@router.post("/admin/api/dismiss_checklist")
async def dismiss_checklist(user: dict = Depends(require_admin)):
    """Mark the current term's checklist as seen for this admin."""
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT academic_year, season FROM current_term")
            term = cur.fetchone()
            if term:
                cur.execute(
                    "UPDATE student_auth SET last_seen_term = ROW(%s, %s::term_season)::academic_term WHERE student_id = %s",
                    (term[0], term[1], user["student_id"])
                )
        conn.commit()
    return {"ok": True}


@router.post("/admin/set_admin", response_class=HTMLResponse)
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
                    status_code=302
                )
            cur.execute(
                "UPDATE student_auth SET is_admin = %s WHERE student_id = %s",
                (make_admin, target_id)
            )
            cur.execute("DELETE FROM refresh_tokens WHERE student_id = %s", (target_id,))
        conn.commit()
    return RedirectResponse(url="/admin/portal", status_code=302)


@router.post("/admin/api/edit_user")
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


# ── Admin: manage enrollments / tutor capabilities for any user ──

@router.get("/admin/api/user/{student_id}/enrollments")
async def admin_get_user_enrollments(student_id: int, _: dict = Depends(require_admin)):
    """Get a user's current-term enrollments."""
    with get_db() as conn:
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
            """, (student_id,))
            rows = cur.fetchall()
    return [
        {"course_id": r[0], "department": r[1], "identifier": r[2], "title": r[3]}
        for r in rows
    ]


@router.post("/admin/api/user/{student_id}/enrollments")
async def admin_add_user_enrollment(
    student_id: int,
    _: dict = Depends(require_admin),
    course_id: int = Form(...)
):
    """Add a course enrollment for a user in the current term."""
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT academic_year, season FROM current_term")
            term = cur.fetchone()
            if not term:
                raise HTTPException(400, "Could not determine current term")
            cur.execute(
                """INSERT INTO enrollments (student_id, course_id, term)
                   VALUES (%s, %s, ROW(%s, %s::term_season)::academic_term)
                   ON CONFLICT DO NOTHING""",
                (student_id, course_id, term[0], term[1])
            )
        conn.commit()
    return {"ok": True}


@router.delete("/admin/api/user/{student_id}/enrollments/{course_id}")
async def admin_remove_user_enrollment(
    student_id: int, course_id: int, _: dict = Depends(require_admin)
):
    """Remove a user's enrollment for a course (current term only)."""
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT academic_year, season FROM current_term")
            term = cur.fetchone()
            if not term:
                raise HTTPException(400, "Could not determine current term")
            cur.execute(
                """DELETE FROM enrollments
                   WHERE student_id = %s AND course_id = %s
                     AND (term).academic_year = %s AND (term).season = %s::term_season""",
                (student_id, course_id, term[0], term[1])
            )
        conn.commit()
    return {"ok": True}


@router.get("/admin/api/user/{student_id}/tutor_capabilities")
async def admin_get_user_tutor_capabilities(student_id: int, _: dict = Depends(require_admin)):
    """Get a user's tutor capabilities (confidence > 0)."""
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT c.course_id, c.department, c.identifier, c.title, t.confidence
                FROM tutors t
                JOIN courses c ON t.course_id = c.course_id
                WHERE t.student_id = %s AND t.confidence > 0
                ORDER BY c.department, c.identifier
            """, (student_id,))
            rows = cur.fetchall()
    return [
        {"course_id": r[0], "department": r[1], "identifier": r[2], "title": r[3], "confidence": r[4]}
        for r in rows
    ]


@router.post("/admin/api/user/{student_id}/tutor_capabilities")
async def admin_set_user_tutor_capability(
    student_id: int,
    _: dict = Depends(require_admin),
    course_id: int = Form(...),
    confidence: int = Form(...)
):
    """Add or update a tutor capability for a user."""
    if not 1 <= confidence <= 10:
        raise HTTPException(400, "Confidence must be between 1 and 10")
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """INSERT INTO tutors (student_id, course_id, confidence)
                   VALUES (%s, %s, %s)
                   ON CONFLICT (student_id, course_id) DO UPDATE SET confidence = EXCLUDED.confidence""",
                (student_id, course_id, confidence)
            )
        conn.commit()
    return {"ok": True}


@router.delete("/admin/api/user/{student_id}/tutor_capabilities/{course_id}")
async def admin_remove_user_tutor_capability(
    student_id: int, course_id: int, _: dict = Depends(require_admin)
):
    """Remove a tutor capability for a user."""
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "DELETE FROM tutors WHERE student_id = %s AND course_id = %s",
                (student_id, course_id)
            )
        conn.commit()
    return {"ok": True}


@router.post("/admin/delete_user", response_class=HTMLResponse)
async def delete_user(
    user: dict = Depends(require_admin),
    target_id: int = Form(...)
):
    """Delete a student. Root users cannot be deleted."""
    if target_id == user["student_id"]:
        return RedirectResponse(url="/admin/portal?message=Cannot+delete+your+own+account", status_code=302)
    with get_db_for_user(user) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT is_root FROM student_auth WHERE student_id = %s", (target_id,))
            result = cur.fetchone()
            if result and result[0]:
                return RedirectResponse(url="/admin/portal?message=Cannot+delete+root+user", status_code=302)
            # Clear FK-constrained records first, then delete student (cascades to student_auth + refresh_tokens)
            cur.execute("DELETE FROM study_sessions WHERE tutor_student_id = %s", (target_id,))
            cur.execute("DELETE FROM tutors WHERE student_id = %s", (target_id,))
            cur.execute("DELETE FROM enrollments WHERE student_id = %s", (target_id,))
            cur.execute("DELETE FROM students WHERE student_id = %s", (target_id,))
        conn.commit()
    avatar_path = os.path.join(AVATAR_DIR, f"{target_id}.png")
    if os.path.exists(avatar_path):
        os.remove(avatar_path)
    return RedirectResponse(url="/admin/portal", status_code=302)


@router.post("/admin/set_graduated", response_class=HTMLResponse)
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
                return RedirectResponse(url="/admin/portal?message=Cannot+modify+root+user", status_code=302)
            if graduated:
                cur.execute("UPDATE students SET graduated_date = CURRENT_DATE WHERE student_id = %s", (target_id,))
            else:
                cur.execute("UPDATE students SET graduated_date = NULL WHERE student_id = %s", (target_id,))
        conn.commit()
    return RedirectResponse(url="/admin/portal", status_code=302)


# ── Discord Validation & User Creation ──

@router.get("/admin/api/validate_discord/{discord_id}")
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


@router.post("/admin/create_user", response_class=HTMLResponse)
async def create_user(
    background_tasks: BackgroundTasks,
    user: dict = Depends(require_admin),
    first_name: str = Form(...),
    last_name: str = Form(...),
    discord_id: str = Form(...)
):
    """Create a new student user."""
    if not validate_name(first_name):
        return RedirectResponse(url="/admin/portal?message=First+name+contains+invalid+characters", status_code=302)
    if not validate_name(last_name):
        return RedirectResponse(url="/admin/portal?message=Last+name+contains+invalid+characters", status_code=302)
    if not discord_id.isdigit():
        return RedirectResponse(url="/admin/portal?message=Discord+ID+must+be+numeric", status_code=302)
    is_valid, err = validate_discord_id(discord_id)
    if not is_valid:
        return RedirectResponse(url="/admin/portal?" + urlencode({"message": err}), status_code=302)
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
    return RedirectResponse(url="/admin/portal?message=User+created+successfully", status_code=302)


# ── Assessment Review ──

@router.get("/admin/api/pending_assessments")
async def get_pending_assessments(_: dict = Depends(require_admin)):
    """Return all unconfirmed/disputed assessments + exams needing sessions."""
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT e.exam_id, c.department, c.identifier, c.title,
                       e.test_date, e.exam_type, e.confirmed, e.disputed,
                       s.first_name, s.last_name
                FROM exams e
                JOIN courses c ON e.course_id = c.course_id
                LEFT JOIN students s ON e.creator_id = s.student_id
                WHERE (NOT e.confirmed OR e.disputed) AND NOT e.deleted
                  AND EXISTS (
                      SELECT 1 FROM enrollments en
                      WHERE en.course_id = e.course_id
                        AND (en.term).academic_year = EXTRACT(YEAR FROM e.test_date)::smallint
                        AND (en.term).season = CASE
                            WHEN EXTRACT(MONTH FROM e.test_date) <= 6 THEN 'spring'::term_season
                            ELSE 'fall'::term_season END
                  )
                ORDER BY e.test_date, c.department, c.identifier
            """)
            rows = cur.fetchall()
            # Exams within 3 days that have no study session scheduled
            # (also checks linked courses — if a linked course's exam on the
            # same date already has a session, this exam is considered covered)
            # Exams needing sessions: deduplicate strongly linked courses
            # by picking the lowest course_id per link-group + test_date.
            cur.execute("""
                SELECT e.exam_id, c.department, c.identifier, c.title,
                       e.test_date, e.exam_type, e.course_id
                FROM exams e
                JOIN courses c ON e.course_id = c.course_id
                WHERE NOT e.deleted AND NOT e.skipped AND e.confirmed
                  AND e.test_date BETWEEN CURRENT_DATE AND CURRENT_DATE + INTERVAL '5 days'
                  AND EXISTS (
                      SELECT 1 FROM enrollments en
                      WHERE en.course_id = e.course_id
                        AND (en.term).academic_year = EXTRACT(YEAR FROM e.test_date)::smallint
                        AND (en.term).season = CASE
                            WHEN EXTRACT(MONTH FROM e.test_date) <= 6 THEN 'spring'::term_season
                            ELSE 'fall'::term_season END
                  )
                  AND NOT EXISTS (
                      SELECT 1 FROM study_sessions ss
                      JOIN exams e2 ON ss.exam_id = e2.exam_id
                      WHERE e2.course_id = ANY(linked_course_ids(e.course_id))
                        AND e2.test_date = e.test_date
                        AND NOT e2.deleted
                  )
                ORDER BY e.test_date, c.department, c.identifier
            """)
            needs_session_rows = cur.fetchall()

            # Deduplicate: for strongly linked courses with exams on the same
            # date, show only one todo.  Resolve each course's strong-link
            # group root (min id in the group) and keep the first exam per
            # (root, test_date).
            deduped_needs = []
            seen_groups: set[tuple] = set()
            for r in needs_session_rows:
                course_id = r[6]
                test_date = r[4]
                cur.execute("SELECT linked_course_ids(%s)", (course_id,))
                group_ids = cur.fetchone()[0]
                group_root = min(group_ids)
                key = (group_root, test_date)
                if key in seen_groups:
                    continue
                seen_groups.add(key)
                # Collect labels for all linked courses in this group that
                # also have an exam on this date.
                linked_labels = []
                for nr in needs_session_rows:
                    if nr[6] != course_id and nr[6] in group_ids and nr[4] == test_date:
                        linked_labels.append(f"{nr[1]}{nr[2]}")
                deduped_needs.append((*r, linked_labels))

    result = [
        {
            "exam_id": r[0], "department": r[1], "identifier": r[2],
            "title": r[3], "test_date": str(r[4]), "exam_type": r[5],
            "confirmed": r[6], "disputed": r[7],
            "reporter_first": r[8], "reporter_last": r[9],
            "review_type": "disputed" if r[7] else "pending",
        }
        for r in rows
    ]
    for r in deduped_needs:
        entry = {
            "exam_id": r[0], "department": r[1], "identifier": r[2],
            "title": r[3], "test_date": str(r[4]), "exam_type": r[5],
            "review_type": "needs_session",
        }
        if r[7]:
            entry["also_covers"] = r[7]
        result.append(entry)
    return result


@router.post("/admin/api/confirm_assessment")
async def confirm_assessment(
    _: dict = Depends(require_admin),
    exam_id: int = Form(...),
):
    """Confirm a pending report (set confirmed=TRUE) or a disputed final (delete it)."""
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT confirmed, disputed FROM exams WHERE exam_id = %s", (exam_id,))
            row = cur.fetchone()
            if not row:
                raise HTTPException(404, "Exam not found")
            if row[1]:  # disputed — admin agrees, soft-delete the final
                cur.execute("UPDATE exams SET deleted = TRUE WHERE exam_id = %s", (exam_id,))
            else:  # unconfirmed — approve the report
                cur.execute("UPDATE exams SET confirmed = TRUE WHERE exam_id = %s", (exam_id,))
        conn.commit()
    return {"ok": True}


@router.post("/admin/api/revert_assessment")
async def revert_assessment(
    _: dict = Depends(require_admin),
    exam_id: int = Form(...),
):
    """Revert: for disputed, set disputed=FALSE (restore). For unconfirmed, delete."""
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT confirmed, disputed FROM exams WHERE exam_id = %s", (exam_id,))
            row = cur.fetchone()
            if not row:
                raise HTTPException(404, "Exam not found")
            if row[1]:  # disputed — admin disagrees, restore the final
                cur.execute("UPDATE exams SET disputed = FALSE WHERE exam_id = %s", (exam_id,))
            else:  # unconfirmed — reject the report
                cur.execute("UPDATE exams SET deleted = TRUE WHERE exam_id = %s", (exam_id,))
        conn.commit()
    return {"ok": True}


@router.delete("/admin/api/pending_assessment/{exam_id}")
async def delete_pending_assessment(exam_id: int, _: dict = Depends(require_admin)):
    """Delete an unconfirmed assessment report."""
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE exams SET deleted = TRUE WHERE exam_id = %s AND NOT confirmed",
                (exam_id,)
            )
        conn.commit()
    return {"ok": True}


@router.delete("/admin/api/exam/{exam_id}")
async def delete_exam(exam_id: int, _: dict = Depends(require_admin)):
    """Soft-delete any exam (admin only)."""
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("UPDATE exams SET deleted = TRUE WHERE exam_id = %s", (exam_id,))
        conn.commit()
    return {"ok": True}


# ── Restore Deleted Exams ──

@router.get("/admin/api/deleted_exams")
async def get_deleted_exams(_: dict = Depends(require_admin)):
    """Return soft-deleted and skipped exams for the current term.
    Auto-purges old-term deletions. Excludes past-date exams from skipped items."""
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT academic_year, season FROM current_term")
            term = cur.fetchone()
            if not term:
                return []
            year, season = int(term[0]), str(term[1])
            # Auto-purge: hard-delete soft-deleted exams from previous terms
            # Also clear skipped flag on past-date exams
            if season == "spring":
                term_cond = "EXTRACT(YEAR FROM test_date)::int = %s AND EXTRACT(MONTH FROM test_date) <= 6"
            else:
                term_cond = "EXTRACT(YEAR FROM test_date)::int = %s AND EXTRACT(MONTH FROM test_date) > 6"
            cur.execute(f"DELETE FROM exams WHERE deleted AND NOT ({term_cond})", (year,))
            cur.execute(f"UPDATE exams SET skipped = FALSE WHERE skipped AND test_date < CURRENT_DATE")
            conn.commit()
            # Return current-term deleted + skipped exams (exclude past-date skipped)
            cur.execute(f"""
                SELECT e.exam_id, c.department, c.identifier, c.title,
                       e.test_date, e.exam_type, e.deleted, e.skipped, e.disputed
                FROM exams e
                JOIN courses c ON e.course_id = c.course_id
                WHERE (e.deleted OR e.skipped)
                  AND {term_cond}
                  AND e.test_date >= CURRENT_DATE
                ORDER BY e.test_date, c.department, c.identifier
            """, (year,))
            rows = cur.fetchall()
    return [
        {
            "exam_id": r[0], "department": r[1], "identifier": r[2],
            "title": r[3], "test_date": str(r[4]), "exam_type": r[5],
            "is_deleted": r[6], "is_skipped": r[7], "is_disputed": r[8],
        }
        for r in rows
    ]


@router.post("/admin/api/skip_exam")
async def skip_exam(
    _: dict = Depends(require_admin),
    exam_id: int = Form(...),
):
    """Skip an exam — removes it from the needs-session todo list."""
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE exams SET skipped = TRUE WHERE exam_id = %s AND NOT deleted",
                (exam_id,)
            )
        conn.commit()
    return {"ok": True}


@router.post("/admin/api/unskip_exam")
async def unskip_exam(
    _: dict = Depends(require_admin),
    exam_id: int = Form(...),
):
    """Unskip an exam — restores it to the needs-session todo list."""
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE exams SET skipped = FALSE WHERE exam_id = %s",
                (exam_id,)
            )
        conn.commit()
    return {"ok": True}


@router.post("/admin/api/restore_exam")
async def restore_exam(
    _: dict = Depends(require_admin),
    exam_id: int = Form(...),
):
    """Restore a soft-deleted exam."""
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE exams SET deleted = FALSE WHERE exam_id = %s AND deleted",
                (exam_id,)
            )
        conn.commit()
    return {"ok": True}


# ── Study Sessions ──

@router.get("/admin/api/exam/{exam_id}/scheduling_details")
async def get_exam_scheduling_details(exam_id: int, _: dict = Depends(require_admin)):
    """Return tutors and enrolled students for scheduling a study session."""
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT e.exam_id, e.course_id, e.test_date, e.exam_type,
                       c.department, c.identifier, c.title
                FROM exams e
                JOIN courses c ON e.course_id = c.course_id
                WHERE e.exam_id = %s AND NOT e.deleted
            """, (exam_id,))
            exam_row = cur.fetchone()
            if not exam_row:
                raise HTTPException(404, "Exam not found")

            course_id = exam_row[1]
            exam_dept, exam_ident = exam_row[4], exam_row[5]

            # Resolve linked course groups
            # Strong links: merge students + sessions
            cur.execute("SELECT linked_course_ids(%s)", (course_id,))
            strong_ids = cur.fetchone()[0]
            # All links: share tutors
            cur.execute("SELECT linked_course_ids_any(%s)", (course_id,))
            all_ids = cur.fetchone()[0]

            cur.execute(
                "SELECT session_id FROM study_sessions WHERE exam_id = %s",
                (exam_id,)
            )
            existing_session = cur.fetchone()

            # Tutors from all linked courses (strong + weak)
            cur.execute("""
                SELECT t.student_id, s.first_name, s.last_name, t.confidence,
                       c.department, c.identifier
                FROM tutors t
                JOIN students s ON t.student_id = s.student_id
                JOIN courses c ON t.course_id = c.course_id
                WHERE t.course_id = ANY(%s) AND t.confidence > 0
                ORDER BY t.confidence DESC, s.last_name, s.first_name
            """, (all_ids,))
            tutors = [
                {
                    "student_id": r[0], "first_name": r[1], "last_name": r[2],
                    "confidence": r[3],
                    "from_course": f"{r[4]}{r[5]}" if (r[4] != exam_dept or r[5] != exam_ident) else None,
                }
                for r in cur.fetchall()
            ]

            # Students from strong-linked courses only
            cur.execute("SELECT academic_year, season FROM current_term")
            term = cur.fetchone()
            students = []
            if term:
                cur.execute("""
                    SELECT DISTINCT ON (s.student_id)
                           s.student_id, s.first_name, s.last_name, s.discord_id
                    FROM enrollments en
                    JOIN students s ON en.student_id = s.student_id
                    WHERE en.course_id = ANY(%s)
                      AND (en.term).academic_year = %s
                      AND (en.term).season = %s::term_season
                    ORDER BY s.student_id, s.last_name, s.first_name
                """, (strong_ids, term[0], term[1]))
                students = [
                    {"student_id": r[0], "first_name": r[1], "last_name": r[2], "discord_id": r[3]}
                    for r in cur.fetchall()
                ]

            # Include linked course info
            linked_courses = []
            if len(all_ids) > 1:
                cur.execute("""
                    SELECT c.course_id, c.department, c.identifier, c.title, cl.link_type
                    FROM courses c
                    LEFT JOIN course_links cl ON
                        (cl.course_id_a = LEAST(c.course_id, %s) AND cl.course_id_b = GREATEST(c.course_id, %s))
                    WHERE c.course_id = ANY(%s) AND c.course_id != %s
                    ORDER BY c.department, c.identifier
                """, (course_id, course_id, all_ids, course_id))
                linked_courses = [
                    {"course_id": r[0], "department": r[1], "identifier": r[2], "title": r[3], "link_type": r[4]}
                    for r in cur.fetchall()
                ]

    return {
        "exam": {
            "exam_id": exam_row[0], "course_id": exam_row[1],
            "test_date": str(exam_row[2]), "exam_type": exam_row[3],
            "department": exam_row[4], "identifier": exam_row[5], "title": exam_row[6],
        },
        "has_session": existing_session is not None,
        "tutors": tutors,
        "students": students,
        "linked_courses": linked_courses,
    }


@router.post("/admin/api/study_sessions")
async def create_study_session(
    _: dict = Depends(require_admin),
    exam_id: int = Form(...),
    tutor_student_id: Optional[int] = Form(default=None),
    session_timestamp: str = Form(...),
    location: str = Form(default="Study Room"),
):
    """Create a study session for an exam."""
    location = location.strip() or "Study Room"
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT exam_id FROM exams WHERE exam_id = %s AND NOT deleted", (exam_id,))
            if not cur.fetchone():
                raise HTTPException(404, "Exam not found")
            if tutor_student_id:
                cur.execute("""
                    SELECT 1 FROM tutors t
                    JOIN exams e ON e.exam_id = %s
                    WHERE t.student_id = %s
                      AND t.course_id = ANY(linked_course_ids_any(e.course_id))
                      AND t.confidence > 0
                """, (exam_id, tutor_student_id))
                if not cur.fetchone():
                    raise HTTPException(400, "Selected tutor is not available for this course")
            try:
                cur.execute("""
                    INSERT INTO study_sessions (tutor_student_id, exam_id, session_timestamp, location)
                    VALUES (%s, %s, %s::timestamptz, %s)
                    RETURNING session_id
                """, (tutor_student_id, exam_id, session_timestamp, location))
                session_id = cur.fetchone()[0]
            except Exception:
                conn.rollback()
                raise HTTPException(409, "A session already exists for this exam")
            conn.commit()
    return {"ok": True, "session_id": session_id}


@router.get("/admin/api/study_sessions")
async def list_study_sessions(_: dict = Depends(require_admin)):
    """Return all study sessions for the current term."""
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT academic_year, season FROM current_term")
            term = cur.fetchone()
            if not term:
                return []
            year, season = int(term[0]), str(term[1])

            if season == "spring":
                date_filter = "EXTRACT(YEAR FROM e.test_date)::int = %s AND EXTRACT(MONTH FROM e.test_date) <= 6"
            else:
                date_filter = "EXTRACT(YEAR FROM e.test_date)::int = %s AND EXTRACT(MONTH FROM e.test_date) > 6"

            cur.execute(f"""
                SELECT ss.session_id, ss.session_timestamp, ss.location,
                       e.exam_id, e.test_date, e.exam_type, e.course_id,
                       c.department, c.identifier, c.title,
                       ts.first_name, ts.last_name, ts.student_id
                FROM study_sessions ss
                JOIN exams e ON ss.exam_id = e.exam_id
                JOIN courses c ON e.course_id = c.course_id
                LEFT JOIN students ts ON ss.tutor_student_id = ts.student_id
                WHERE NOT e.deleted AND {date_filter}
                  AND ss.session_timestamp > NOW()
                ORDER BY ss.session_timestamp, c.department, c.identifier
            """, (year,))
            session_rows = cur.fetchall()

            sessions = []
            for r in session_rows:
                course_id = r[6]
                cur.execute("""
                    SELECT DISTINCT ON (s.student_id)
                           s.student_id, s.first_name, s.last_name, s.discord_id
                    FROM enrollments en
                    JOIN students s ON en.student_id = s.student_id
                    WHERE en.course_id = ANY(linked_course_ids(%s))
                      AND (en.term).academic_year = %s
                      AND (en.term).season = %s::term_season
                    ORDER BY s.student_id, s.last_name, s.first_name
                """, (course_id, year, season))
                students = [
                    {"student_id": sr[0], "first_name": sr[1], "last_name": sr[2], "discord_id": sr[3]}
                    for sr in cur.fetchall()
                ]
                sessions.append({
                    "session_id": r[0],
                    "session_timestamp": r[1].isoformat() if r[1] else None,
                    "location": r[2],
                    "exam_id": r[3], "test_date": str(r[4]), "exam_type": r[5],
                    "course_id": r[6],
                    "department": r[7], "identifier": r[8], "title": r[9],
                    "tutor_first": r[10], "tutor_last": r[11], "tutor_id": r[12],
                    "students": students,
                })
    return sessions


@router.put("/admin/api/study_sessions/{session_id}")
async def update_study_session(
    session_id: int,
    _: dict = Depends(require_admin),
    tutor_student_id: Optional[int] = Form(default=None),
    session_timestamp: str = Form(...),
    location: str = Form(default="Study Room"),
):
    """Update a study session's tutor, timestamp, or location."""
    location = location.strip() or "Study Room"
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT exam_id FROM study_sessions WHERE session_id = %s",
                (session_id,)
            )
            row = cur.fetchone()
            if not row:
                raise HTTPException(404, "Session not found")
            exam_id = row[0]
            if tutor_student_id:
                cur.execute("""
                    SELECT 1 FROM tutors t
                    JOIN exams e ON e.exam_id = %s
                    WHERE t.student_id = %s
                      AND t.course_id = ANY(linked_course_ids_any(e.course_id))
                      AND t.confidence > 0
                """, (exam_id, tutor_student_id))
                if not cur.fetchone():
                    raise HTTPException(400, "Selected tutor is not available for this course")
            cur.execute("""
                UPDATE study_sessions
                SET tutor_student_id = %s, session_timestamp = %s::timestamptz, location = %s
                WHERE session_id = %s
            """, (tutor_student_id, session_timestamp, location, session_id))
        conn.commit()
    return {"ok": True}


@router.delete("/admin/api/study_sessions/{session_id}")
async def delete_study_session(session_id: int, _: dict = Depends(require_admin)):
    """Delete a study session."""
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM study_sessions WHERE session_id = %s", (session_id,))
        conn.commit()
    return {"ok": True}


# ── Course Links ──

@router.get("/admin/api/course_links")
async def get_course_links(_: dict = Depends(require_admin)):
    """Return all course link pairs with link type."""
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT cl.course_id_a, cl.course_id_b, cl.link_type,
                       ca.department, ca.identifier, ca.title,
                       cb.department, cb.identifier, cb.title
                FROM course_links cl
                JOIN courses ca ON cl.course_id_a = ca.course_id
                JOIN courses cb ON cl.course_id_b = cb.course_id
                ORDER BY ca.department, ca.identifier
            """)
            return [
                {
                    "course_id_a": r[0], "course_id_b": r[1], "link_type": r[2],
                    "a_department": r[3], "a_identifier": r[4], "a_title": r[5],
                    "b_department": r[6], "b_identifier": r[7], "b_title": r[8],
                }
                for r in cur.fetchall()
            ]


@router.post("/admin/api/course_links")
async def create_course_link(
    _: dict = Depends(require_admin),
    course_id_a: int = Form(...),
    course_id_b: int = Form(...),
    link_type: str = Form(default="strong"),
):
    """Link two courses. link_type: 'strong' (same tests) or 'weak' (same subject)."""
    if link_type not in ("strong", "weak"):
        raise HTTPException(400, "link_type must be 'strong' or 'weak'")
    if course_id_a == course_id_b:
        raise HTTPException(400, "Cannot link a course to itself")
    lo, hi = min(course_id_a, course_id_b), max(course_id_a, course_id_b)
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT course_id FROM courses WHERE course_id IN (%s, %s)", (lo, hi))
            if len(cur.fetchall()) != 2:
                raise HTTPException(404, "One or both courses not found")
            try:
                cur.execute(
                    "INSERT INTO course_links (course_id_a, course_id_b, link_type) VALUES (%s, %s, %s::link_type)",
                    (lo, hi, link_type)
                )
            except Exception:
                conn.rollback()
                raise HTTPException(409, "These courses are already linked")
        conn.commit()
    return {"ok": True}


@router.delete("/admin/api/course_links/{course_id_a}/{course_id_b}")
async def delete_course_link(course_id_a: int, course_id_b: int, _: dict = Depends(require_admin)):
    """Remove a link between two courses."""
    lo, hi = min(course_id_a, course_id_b), max(course_id_a, course_id_b)
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "DELETE FROM course_links WHERE course_id_a = %s AND course_id_b = %s",
                (lo, hi)
            )
        conn.commit()
    return {"ok": True}


@router.get("/admin/api/no_tutor_pending")
async def get_no_tutor_pending(_: dict = Depends(require_admin)):
    """Return courses with pending no-tutor-needed reports."""
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT c.course_id, c.department, c.identifier, c.title
                FROM courses c, current_term ct
                WHERE c.no_tutor_pending AND NOT c.no_tutor_needed
                  AND (c.last_offered).academic_year = ct.academic_year
                  AND (c.last_offered).season = ct.season
                ORDER BY c.department, c.identifier
            """)
            return [
                {"course_id": r[0], "department": r[1], "identifier": r[2], "title": r[3]}
                for r in cur.fetchall()
            ]


@router.post("/admin/api/approve_no_tutor")
async def approve_no_tutor(
    _: dict = Depends(require_admin),
    course_id: int = Form(...),
):
    """Approve a no-tutor-needed report."""
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE courses SET no_tutor_needed = TRUE, no_tutor_pending = FALSE WHERE course_id = %s",
                (course_id,)
            )
        conn.commit()
    return {"ok": True}


@router.post("/admin/api/reject_no_tutor")
async def reject_no_tutor(
    _: dict = Depends(require_admin),
    course_id: int = Form(...),
):
    """Reject a no-tutor-needed report."""
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE courses SET no_tutor_pending = FALSE WHERE course_id = %s",
                (course_id,)
            )
        conn.commit()
    return {"ok": True}


@router.post("/admin/api/toggle_no_tutor")
async def toggle_no_tutor(
    _: dict = Depends(require_admin),
    course_id: int = Form(...),
):
    """Toggle no_tutor_needed for a course."""
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE courses SET no_tutor_needed = NOT no_tutor_needed, no_tutor_pending = FALSE WHERE course_id = %s",
                (course_id,)
            )
        conn.commit()
    return {"ok": True}


@router.get("/admin/api/no_tutor_approved")
async def get_no_tutor_approved(_: dict = Depends(require_admin)):
    """Return current-term courses where no_tutor_needed has been approved."""
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT c.course_id, c.department, c.identifier, c.title
                FROM courses c, current_term ct
                WHERE c.no_tutor_needed
                  AND (c.last_offered).academic_year = ct.academic_year
                  AND (c.last_offered).season = ct.season
                ORDER BY c.department, c.identifier
            """)
            return [
                {"course_id": r[0], "department": r[1], "identifier": r[2], "title": r[3]}
                for r in cur.fetchall()
            ]


@router.get("/admin/api/course_link_suggestions")
async def get_course_link_suggestions(_: dict = Depends(require_admin)):
    """Suggest course pairs that might be equivalent based on title similarity."""
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT academic_year, season FROM current_term")
            term = cur.fetchone()
            if not term:
                return []
            cur.execute("""
                SELECT course_id, department, identifier, title
                FROM courses
                WHERE (last_offered).academic_year = %s
                  AND (last_offered).season = %s::term_season
                ORDER BY department, identifier
            """, (term[0], term[1]))
            courses = [
                {"course_id": r[0], "department": r[1], "identifier": r[2], "title": r[3]}
                for r in cur.fetchall()
            ]
            cur.execute("SELECT course_id_a, course_id_b FROM course_links")
            existing = {(r[0], r[1]) for r in cur.fetchall()}
    # Group by department
    by_dept: dict[str, list[dict]] = {}
    for c in courses:
        by_dept.setdefault(c["department"], []).append(c)
    suggestions = []
    for dept_courses in by_dept.values():
        n = len(dept_courses)
        for i in range(n):
            for j in range(i + 1, n):
                a, b = dept_courses[i], dept_courses[j]
                ta, tb = (a.get("title") or "").lower().strip(), (b.get("title") or "").lower().strip()
                if not ta or not tb:
                    continue
                sim = _SM(None, ta, tb).ratio()
                if sim < 0.7:
                    continue
                lo, hi = min(a["course_id"], b["course_id"]), max(a["course_id"], b["course_id"])
                if (lo, hi) in existing:
                    continue
                # Suggest strong if identifiers are numerically close (e.g. 213 vs 223)
                try:
                    num_a = int("".join(c for c in a["identifier"] if c.isdigit()))
                    num_b = int("".join(c for c in b["identifier"] if c.isdigit()))
                    suggested_type = "strong" if abs(num_a - num_b) <= 10 else "weak"
                except (ValueError, TypeError):
                    suggested_type = "strong"
                suggestions.append({"a": a, "b": b, "similarity": round(sim, 2), "link_type": suggested_type})
    suggestions.sort(key=lambda s: s["similarity"], reverse=True)
    return suggestions
