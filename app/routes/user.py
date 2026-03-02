from datetime import date as date_type, timedelta
from typing import Optional
from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from ..auth import (
    create_access_token, get_password_hash, revoke_refresh_token, verify_password,
)
from ..database import get_db, get_db_for_user
from ..dependencies import require_auth
from ..helpers import (
    _VALID_SHARING, get_user_profile, templates, validate_password,
)

page_router = APIRouter()
api_router = APIRouter()


# ── Portal & Password Pages ──

@page_router.get("/user/portal", response_class=HTMLResponse)
async def user_portal(request: Request, user: dict = Depends(require_auth), message: Optional[str] = None):
    """User portal - accessible by all authenticated users except root."""
    if user.get("is_root"):
        return RedirectResponse(url="/admin/portal", status_code=302)
    return templates.TemplateResponse(request, "user_portal.html", {
        "user": user,
        "user_profile": get_user_profile(user["student_id"]),
        "message": message
    })


@page_router.get("/user/set_password", response_class=HTMLResponse)
async def get_set_password(request: Request, user: dict = Depends(require_auth)):
    """First-login password setup page. Root users are not permitted here."""
    if user.get("is_root"):
        return RedirectResponse(url="/admin/portal", status_code=302)
    return templates.TemplateResponse(request, "set_password.html", {"user": user})


@page_router.post("/user/set_password", response_class=HTMLResponse)
async def post_set_password(
    user: dict = Depends(require_auth),
    new_password: str = Form(...),
    sharing: Optional[str] = Form(default=None),
):
    """Handle first-login password setup. Re-issues token with is_first_login=False. Root users are not permitted here."""
    if user.get("is_root"):
        return RedirectResponse(url="/admin/portal", status_code=302)
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
    response = RedirectResponse(url="/user/privacy", status_code=302)
    response.set_cookie(key="access_token", value=f"Bearer {access_token}", httponly=True, max_age=1800, samesite="lax")
    return response


@page_router.get("/user/change_password", response_class=HTMLResponse)
async def get_change_password(request: Request, user: dict = Depends(require_auth)):
    """Change password page. Root users cannot change their password here."""
    if user.get("is_root"):
        redirect_url = "/admin/portal" if user["is_admin"] else "/user/portal"
        return RedirectResponse(url=redirect_url, status_code=302)
    return templates.TemplateResponse(request, "change_password.html", {
        "user": user,
        "user_profile": get_user_profile(user["student_id"]),
    })


@page_router.post("/user/change_password", response_class=HTMLResponse)
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
        return RedirectResponse(url=redirect_url, status_code=302)

    def render_error(msg: str):
        return templates.TemplateResponse(request, "change_password.html", {
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
    resp = RedirectResponse(url="/", status_code=302)
    resp.delete_cookie("access_token")
    resp.delete_cookie("refresh_token", path="/")
    return resp


@page_router.get("/user/privacy", response_class=HTMLResponse)
async def get_privacy(request: Request, user: dict = Depends(require_auth)):
    """Privacy settings page — lets users change their sharing setting."""
    if user.get("is_root"):
        redirect_url = "/admin/portal" if user["is_admin"] else "/user/portal"
        return RedirectResponse(url=redirect_url, status_code=302)
    return templates.TemplateResponse(request, "privacy.html", {
        "user": user,
        "user_profile": get_user_profile(user["student_id"]),
    })


@page_router.post("/user/privacy", response_class=HTMLResponse)
async def post_privacy(
    user: dict = Depends(require_auth),
    sharing: str = Form(...),
):
    """Save the user's sharing setting."""
    if user.get("is_root"):
        redirect_url = "/admin/portal" if user["is_admin"] else "/user/portal"
        return RedirectResponse(url=redirect_url, status_code=302)
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
        status_code=302
    )


# ── Appearance ──

@api_router.post("/api/my/dark_mode")
async def toggle_dark_mode(user: dict = Depends(require_auth)):
    """Toggle the user's dark mode preference."""
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE students SET dark_mode = NOT dark_mode WHERE student_id = %s RETURNING dark_mode",
                (user["student_id"],)
            )
            row = cur.fetchone()
        conn.commit()
    return {"ok": True, "dark_mode": row[0] if row else False}


# ── Enrollment APIs ──

@api_router.get("/api/my/enrollments")
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


@api_router.post("/api/my/enrollments")
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


@api_router.delete("/api/my/enrollments/{course_id}")
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


# ── Tutor Capability APIs ──

@api_router.get("/api/my/tutor_capabilities")
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


@api_router.get("/api/my/tutor_recommendations")
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
                  AND NOT c.no_tutor_needed
                  AND NOT ((e.term).academic_year = ct.academic_year
                       AND (e.term).season = ct.season)
                  AND NOT EXISTS (
                    SELECT 1 FROM tutors t
                    WHERE t.student_id = %s
                      AND t.course_id = ANY(linked_course_ids_any(c.course_id))
                  )
                ORDER BY c.department, c.identifier
            """, (user["student_id"], user["student_id"]))
            rows = cur.fetchall()
    return [
        {"course_id": r[0], "department": r[1], "identifier": r[2], "title": r[3]}
        for r in rows
    ]


@api_router.post("/api/my/tutor_dismiss/{course_id}")
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


@api_router.post("/api/my/tutor_capabilities")
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


@api_router.delete("/api/my/tutor_capabilities/{course_id}")
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


@api_router.post("/api/my/report_no_tutor/{course_id}")
async def report_no_tutor_needed(course_id: int, user: dict = Depends(require_auth)):
    """Student reports that a course doesn't need a tutor (pending admin approval)."""
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE courses SET no_tutor_pending = TRUE WHERE course_id = %s AND NOT no_tutor_needed",
                (course_id,)
            )
        conn.commit()
    return {"ok": True}


# ── Assessment APIs ──

_REPORTABLE_EXAM_TYPES = {"in_class", "quiz"}


@api_router.get("/api/my/assessments")
async def get_my_assessments(user: dict = Depends(require_auth)):
    """Get upcoming assessments for the user's enrolled courses."""
    with get_db_for_user(user) as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT e.exam_id, c.course_id, c.department, c.identifier, c.title,
                       e.test_date, e.exam_type, e.confirmed,
                       (e.creator_id = %s) AS is_mine
                FROM exams e
                JOIN courses c ON e.course_id = c.course_id
                JOIN enrollments en ON en.course_id = c.course_id
                CROSS JOIN current_term ct
                WHERE en.student_id = %s
                  AND (en.term).academic_year = ct.academic_year
                  AND (en.term).season = ct.season
                  AND NOT e.disputed
                  AND NOT e.deleted
                  AND e.test_date >= CURRENT_DATE
                ORDER BY e.test_date, c.department, c.identifier
            """, (user["student_id"], user["student_id"]))
            rows = cur.fetchall()
    return [
        {
            "exam_id": r[0], "course_id": r[1], "department": r[2],
            "identifier": r[3], "title": r[4],
            "test_date": str(r[5]), "exam_type": r[6],
            "confirmed": r[7], "is_mine": r[8],
        }
        for r in rows
    ]


@api_router.post("/api/my/assessments")
async def report_assessment(
    user: dict = Depends(require_auth),
    course_id: int = Form(...),
    test_date: str = Form(...),
    exam_type: str = Form(...),
):
    """Report an in-class test or quiz. Created as unconfirmed."""
    if exam_type not in _REPORTABLE_EXAM_TYPES:
        raise HTTPException(400, "Invalid assessment type")
    try:
        date_type.fromisoformat(test_date)
    except ValueError:
        raise HTTPException(400, "Invalid date format")
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
                """SELECT 1 FROM enrollments
                   WHERE student_id = %s AND course_id = %s
                     AND (term).academic_year = %s AND (term).season = %s""",
                (user["student_id"], course_id, year, season)
            )
            if not cur.fetchone():
                raise HTTPException(403, "You must be enrolled in this course")
            cur.execute(
                """INSERT INTO exams (course_id, test_date, exam_type, creator_id, confirmed)
                   VALUES (%s, %s::date, %s::exam_type, %s, FALSE)
                   ON CONFLICT (course_id, test_date, exam_type) WHERE NOT deleted DO NOTHING""",
                (course_id, test_date, exam_type, user["student_id"])
            )
        conn.commit()
    return {"ok": True}


@api_router.delete("/api/my/assessments/{exam_id}")
async def delete_my_assessment(exam_id: int, user: dict = Depends(require_auth)):
    """Delete the user's own unconfirmed assessment report."""
    with get_db_for_user(user) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE exams SET deleted = TRUE WHERE exam_id = %s AND creator_id = %s AND NOT confirmed",
                (exam_id, user["student_id"])
            )
        conn.commit()
    return {"ok": True}


@api_router.post("/api/my/assessments/{exam_id}/dispute")
async def dispute_assessment(exam_id: int, user: dict = Depends(require_auth)):
    """Dispute a final exam — marks it as disputed (hidden from normal views)."""
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
                """SELECT e.course_id FROM exams e
                   WHERE e.exam_id = %s AND e.exam_type = 'final' AND NOT e.deleted""",
                (exam_id,)
            )
            row = cur.fetchone()
            if not row:
                raise HTTPException(404, "Final exam not found")
            cur.execute(
                """SELECT 1 FROM enrollments
                   WHERE student_id = %s AND course_id = %s
                     AND (term).academic_year = %s AND (term).season = %s""",
                (user["student_id"], row[0], year, season)
            )
            if not cur.fetchone():
                raise HTTPException(403, "You must be enrolled in this course")
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE exams SET disputed = TRUE WHERE exam_id = %s AND NOT disputed",
                (exam_id,)
            )
        conn.commit()
    return {"ok": True}


# ── Course Tutors & Study Sessions ──

@api_router.get("/api/my/course_tutors")
async def get_my_course_tutors(user: dict = Depends(require_auth)):
    """Get visible tutors for the user's enrolled courses (respects RLS sharing).

    Includes tutors from linked courses:
    - Strong links: tutor shown once (same test, courses are interchangeable)
    - Weak links: tutor shown with from_course annotation
    """
    with get_db_for_user(user) as conn:
        with conn.cursor() as cur:
            # Get enrolled course IDs
            cur.execute("""
                SELECT en.course_id
                FROM enrollments en
                CROSS JOIN current_term ct
                WHERE en.student_id = %s
                  AND (en.term).academic_year = ct.academic_year
                  AND (en.term).season = ct.season
            """, (user["student_id"],))
            enrolled_ids = [r[0] for r in cur.fetchall()]

            if not enrolled_ids:
                return []

            # For each enrolled course, get linked courses and their link types
            results = {}
            for eid in enrolled_ids:
                cur.execute("SELECT linked_course_ids_any(%s)", (eid,))
                all_linked = cur.fetchone()[0] or [eid]
                cur.execute("SELECT linked_course_ids(%s)", (eid,))
                strong_linked = cur.fetchone()[0] or [eid]
                weak_linked = [cid for cid in all_linked if cid not in strong_linked]

                # Fetch tutors from all linked courses
                cur.execute("""
                    SELECT t.student_id, s.first_name, s.last_name, t.confidence,
                           t.course_id, c.department, c.identifier
                    FROM tutors t
                    JOIN students s ON t.student_id = s.student_id
                    JOIN courses c ON t.course_id = c.course_id
                    WHERE t.course_id = ANY(%s)
                      AND t.confidence > 0
                      AND t.student_id != %s
                    ORDER BY t.confidence DESC
                """, (all_linked, user["student_id"]))
                tutor_rows = cur.fetchall()

                # Get enrolled course info
                cur.execute("""
                    SELECT c.course_id, c.department, c.identifier, c.title
                    FROM courses c WHERE c.course_id = %s
                """, (eid,))
                course_info = cur.fetchone()
                if not course_info:
                    continue

                # Deduplicate: same tutor across strong links = show once
                seen_tutors = set()
                tutors = []
                for tr in tutor_rows:
                    sid = tr[0]
                    if sid in seen_tutors:
                        continue
                    seen_tutors.add(sid)
                    tutor_entry = {
                        "first_name": tr[1], "last_name": tr[2], "confidence": tr[3],
                    }
                    # Annotate if tutor is from a weakly-linked course
                    if tr[4] in weak_linked:
                        tutor_entry["from_course"] = f"{tr[5]}{tr[6]}"
                    tutors.append(tutor_entry)

                results[eid] = {
                    "course_id": course_info[0], "department": course_info[1],
                    "identifier": course_info[2], "title": course_info[3],
                    "tutors": tutors,
                }

    return list(results.values())


@api_router.get("/api/my/study_sessions")
async def get_my_study_sessions(user: dict = Depends(require_auth)):
    """Get upcoming study sessions for enrolled courses or where user is tutor."""
    with get_db_for_user(user) as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT ss.session_id, ss.session_timestamp, ss.location,
                       e.exam_id, e.test_date, e.exam_type,
                       c.department, c.identifier, c.title,
                       ts.first_name, ts.last_name,
                       CASE WHEN ss.tutor_student_id = %s THEN true ELSE false END AS is_tutor
                FROM study_sessions ss
                JOIN exams e ON ss.exam_id = e.exam_id
                JOIN courses c ON e.course_id = c.course_id
                LEFT JOIN students ts ON ss.tutor_student_id = ts.student_id
                CROSS JOIN current_term ct
                WHERE NOT e.deleted
                  AND e.test_date >= CURRENT_DATE
                  AND (
                      ss.tutor_student_id = %s
                      OR EXISTS (
                          SELECT 1 FROM enrollments en
                          WHERE en.student_id = %s
                            AND en.course_id = ANY(linked_course_ids(e.course_id))
                            AND (en.term).academic_year = ct.academic_year
                            AND (en.term).season = ct.season
                      )
                  )
                ORDER BY ss.session_timestamp
            """, (user["student_id"], user["student_id"], user["student_id"]))
            rows = cur.fetchall()
    return [
        {
            "session_id": r[0],
            "session_timestamp": r[1].isoformat() if r[1] else None,
            "location": r[2],
            "exam_id": r[3], "test_date": str(r[4]), "exam_type": r[5],
            "department": r[6], "identifier": r[7], "title": r[8],
            "tutor_first": r[9], "tutor_last": r[10],
            "is_tutor": r[11],
        }
        for r in rows
    ]


# ── Classmates ──

@api_router.get("/api/my/classmates")
async def get_my_classmates(user: dict = Depends(require_auth)):
    """Get students sharing classes with the current user (respects RLS sharing)."""
    with get_db_for_user(user) as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT DISTINCT ON (s.student_id)
                       s.student_id, s.first_name, s.last_name, s.discord_id
                FROM enrollments en
                JOIN enrollments other_en ON en.course_id = other_en.course_id
                    AND (en.term).academic_year = (other_en.term).academic_year
                    AND (en.term).season = (other_en.term).season
                JOIN students s ON other_en.student_id = s.student_id
                CROSS JOIN current_term ct
                WHERE en.student_id = %s
                  AND other_en.student_id != %s
                  AND (en.term).academic_year = ct.academic_year
                  AND (en.term).season = ct.season
                ORDER BY s.student_id, s.last_name, s.first_name
            """, (user["student_id"], user["student_id"]))
            classmate_rows = cur.fetchall()

            # Get courses for each classmate (only ones visible via RLS)
            classmates = []
            for r in classmate_rows:
                cur.execute("""
                    SELECT c.course_id, c.department, c.identifier
                    FROM enrollments e
                    JOIN courses c ON e.course_id = c.course_id
                    CROSS JOIN current_term ct
                    WHERE e.student_id = %s
                      AND (e.term).academic_year = ct.academic_year
                      AND (e.term).season = ct.season
                    ORDER BY c.department, c.identifier
                """, (r[0],))
                courses = [{"course_id": cr[0], "department": cr[1], "identifier": cr[2]} for cr in cur.fetchall()]
                classmates.append({
                    "student_id": r[0], "first_name": r[1], "last_name": r[2],
                    "discord_id": r[3], "courses": courses,
                })
    return classmates


@api_router.get("/api/my/search_students")
async def search_students(q: str = "", user: dict = Depends(require_auth)):
    """Search for students by name (respects RLS sharing for enrollment visibility)."""
    if len(q) < 2:
        return []
    with get_db_for_user(user) as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT s.student_id, s.first_name, s.last_name, s.discord_id
                FROM students s
                JOIN student_auth sa ON s.student_id = sa.student_id
                WHERE CONCAT(s.first_name, ' ', s.last_name) ILIKE %s
                  AND s.student_id != %s
                  AND NOT sa.is_root
                ORDER BY s.last_name, s.first_name
                LIMIT 20
            """, (f"%{q}%", user["student_id"]))
            student_rows = cur.fetchall()

            results = []
            for r in student_rows:
                cur.execute("""
                    SELECT c.course_id, c.department, c.identifier
                    FROM enrollments e
                    JOIN courses c ON e.course_id = c.course_id
                    CROSS JOIN current_term ct
                    WHERE e.student_id = %s
                      AND (e.term).academic_year = ct.academic_year
                      AND (e.term).season = ct.season
                    ORDER BY c.department, c.identifier
                """, (r[0],))
                courses = [{"course_id": cr[0], "department": cr[1], "identifier": cr[2]} for cr in cur.fetchall()]
                results.append({
                    "student_id": r[0], "first_name": r[1], "last_name": r[2],
                    "discord_id": r[3], "courses": courses,
                })
    return results
