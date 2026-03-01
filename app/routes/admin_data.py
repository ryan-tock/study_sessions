import asyncio
import json
import os
from datetime import datetime
from urllib.parse import urlencode
from difflib import SequenceMatcher as _SM
from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse

from ..database import get_db, get_db_for_user
from ..dependencies import require_admin
from ..helpers import DATA_DIR
from ..persistence import backup, restore_from_disk, list_backups, delete_backup
from ..pdf_parser import parse_common_hour_pdf, parse_finals_pdf
from ..course_scraper import (
    fetch_courses, load_courses_from_cache,
    cache_exists as course_cache_exists,
    pending_cache_exists as course_pending_cache_exists,
    wipe_cache as wipe_course_cache_files,
    wipe_pending_cache as wipe_course_pending_cache,
    promote_pending_cache,
)

router = APIRouter()


# ── Backup / Restore ──

@router.post("/admin/backup")
async def backup_db(user: dict = Depends(require_admin)):
    """Create a new timestamped user backup. Root only."""
    if not user.get("is_root"):
        raise HTTPException(status_code=403)
    name = backup()
    return {"ok": True, "name": name}


@router.get("/admin/api/backups")
def get_backups(user: dict = Depends(require_admin)):
    """List all user backups. Root only."""
    if not user.get("is_root"):
        raise HTTPException(status_code=403)
    return list_backups()


@router.post("/admin/api/restore_backup")
async def restore_backup(user: dict = Depends(require_admin), backup_name: str = Form(...)):
    """Restore from a specific backup. Root only."""
    if not user.get("is_root"):
        raise HTTPException(status_code=403)
    restored = restore_from_disk(user, backup_name)
    return {"ok": restored}


@router.delete("/admin/api/backup/{backup_name}")
def delete_backup_endpoint(backup_name: str, user: dict = Depends(require_admin)):
    """Delete a backup by name. Root only."""
    if not user.get("is_root"):
        raise HTTPException(status_code=403)
    ok = delete_backup(backup_name)
    return {"ok": ok}


# ── Data Wipe ──

@router.post("/admin/wipe_selective", response_class=HTMLResponse)
async def wipe_selective(request: Request, user: dict = Depends(require_admin)):
    """Wipe selected data types from selected terms. Root only."""
    if not user.get("is_root"):
        return RedirectResponse(url="/admin/portal", status_code=302)
    form = await request.form()
    what_list = form.getlist("what")   # "exams", "courses", "course_cache"
    term_list = form.getlist("term")   # dir names like "2026_A"
    if not what_list or not term_list:
        return RedirectResponse(url="/admin/portal?message=Nothing+selected", status_code=302)
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
    return RedirectResponse(url="/admin/portal?" + urlencode({"message": msg}), status_code=302)


@router.post("/admin/refresh_course_cache", response_class=HTMLResponse)
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
            status_code=302
        )
    yr, s = int(row[0]), str(row[1])
    count = wipe_course_cache_files(DATA_DIR, yr, s)
    msg = f"Cache cleared ({count} file(s) deleted). Use Fetch & Preview to re-download from web."
    return RedirectResponse(
        url="/admin/portal?" + urlencode({"message": msg}),
        status_code=302
    )


# ── Exam Import ──

_EXAM_TYPE_LABELS = {
    "common_hour": "Common Hour Exam",
    "final": "Final Exam",
}


@router.post("/admin/api/preview_exam_pdf")
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
                           WHERE course_id = %s AND test_date = %s AND exam_type = %s AND NOT deleted""",
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


@router.post("/admin/import_exams", response_class=HTMLResponse)
async def import_exams(
    user: dict = Depends(require_admin),
    entries_json: str = Form(...),
    exam_type: str = Form(...),
    pdf_b64: str = Form(default=""),
):
    """Insert confirmed exam entries into the database."""
    if exam_type not in _EXAM_TYPE_LABELS:
        return RedirectResponse(url="/admin/portal?message=Invalid+exam+type", status_code=302)
    try:
        entries = json.loads(entries_json)
    except Exception:
        return RedirectResponse(url="/admin/portal?message=Invalid+data", status_code=302)

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
                       ON CONFLICT (course_id, test_date, exam_type) WHERE NOT deleted DO NOTHING""",
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
        status_code=302
    )


# ── Course Import ──

_VALID_SEASONS = {"spring", "fall"}

# Title similarity threshold for treating two course rows as the same course.
_TITLE_SIM_THRESHOLD = 0.6

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


@router.post("/admin/api/preview_courses")
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


@router.post("/admin/import_courses", response_class=HTMLResponse)
async def import_courses(
    user: dict = Depends(require_admin),
    academic_year: int = Form(...),
    season: str = Form(...),
):
    """Insert courses from the pending preview into the DB, then save data to disk."""
    if season not in _VALID_SEASONS:
        return RedirectResponse(
            url="/admin/portal?message=Invalid+season", status_code=302
        )
    if not course_pending_cache_exists(DATA_DIR, academic_year, season):
        return RedirectResponse(
            url="/admin/portal?message=No+preview+data+found.+Use+Fetch+%26+Preview+first.",
            status_code=302,
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
        status_code=302,
    )


# ── Import from existing disk files ──

@router.post("/admin/api/import_courses_from_cache")
async def import_courses_from_cache(user: dict = Depends(require_admin)):
    """Import courses directly from the final course cache (no preview)."""
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT academic_year, season FROM current_term")
            term = cur.fetchone()
    if not term:
        raise HTTPException(400, "No current term detected")
    year, season = int(term[0]), str(term[1])
    if not course_cache_exists(DATA_DIR, year, season):
        raise HTTPException(404, "No course cache on disk for current term")

    courses = load_courses_from_cache(DATA_DIR, year, season, pending=False)
    inserted = updated = 0
    with get_db_for_user(user) as conn:
        with conn.cursor() as cur:
            existing = _load_existing_courses(cur)
            for c in courses:
                action, best_match = _classify_course(c, year, season, existing)
                if action == "update":
                    new_title = c.get("title") or best_match["title"]
                    cur.execute(
                        """UPDATE courses
                           SET last_offered = ROW(%s, %s)::academic_term, title = %s
                           WHERE course_id = %s""",
                        (year, season, new_title, best_match["course_id"]),
                    )
                    if cur.rowcount:
                        updated += 1
                elif action == "new":
                    cur.execute(
                        """INSERT INTO courses (department, identifier, title, semester_hours, last_offered)
                           VALUES (%s, %s, %s, %s, ROW(%s, %s)::academic_term)""",
                        (c["department"], c["identifier"], c.get("title"),
                         c.get("semester_hours"), year, season),
                    )
                    if cur.rowcount:
                        inserted += 1
            conn.commit()
    parts = []
    if inserted:
        parts.append(f"{inserted} new")
    if updated:
        parts.append(f"{updated} updated")
    summary = ", ".join(parts) if parts else "Nothing to import"
    return {"ok": True, "summary": summary, "inserted": inserted, "updated": updated}


_PDF_FILENAME = {"common_hour": "common_hour.pdf", "final": "finals.pdf"}
_SEASON_LETTER = {"spring": "A", "fall": "B"}


@router.post("/admin/api/import_exams_from_disk")
async def import_exams_from_disk(
    user: dict = Depends(require_admin),
    exam_type: str = Form(...),
):
    """Import exams from an existing PDF on disk (no upload needed)."""
    if exam_type not in _PDF_FILENAME:
        raise HTTPException(400, "Invalid exam type")
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT academic_year, season FROM current_term")
            term = cur.fetchone()
    if not term:
        raise HTTPException(400, "No current term detected")
    year, season = int(term[0]), str(term[1])
    term_dir = os.path.join(DATA_DIR, f"{year}_{_SEASON_LETTER.get(season, '?')}")
    pdf_path = os.path.join(term_dir, _PDF_FILENAME[exam_type])
    if not os.path.isfile(pdf_path):
        raise HTTPException(404, "No PDF on disk for this exam type")

    with open(pdf_path, "rb") as f:
        pdf_bytes = f.read()

    try:
        if exam_type == "common_hour":
            entries = parse_common_hour_pdf(pdf_bytes)
        else:
            entries = parse_finals_pdf(pdf_bytes)
    except Exception as e:
        raise HTTPException(422, f"Failed to parse PDF: {e}")

    inserted = 0
    with get_db_for_user(user) as conn:
        with conn.cursor() as cur:
            for e in entries:
                dept = e.get("department", "")
                ident = e.get("identifier", "")
                date_str = e.get("date", "")
                if not (dept and ident and date_str):
                    continue
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
                       ON CONFLICT (course_id, test_date, exam_type) WHERE NOT deleted DO NOTHING""",
                    (row[0], date_str, exam_type, user["student_id"])
                )
                if cur.rowcount:
                    inserted += 1
            conn.commit()
    return {"ok": True, "inserted": inserted}


# ── Exam Calendar ──

@router.get("/admin/api/calendar_exams")
async def calendar_exams(_: dict = Depends(require_admin)):
    """Return all exams with course info for the calendar view."""
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT e.exam_id, e.test_date, e.exam_type, c.course_id, c.department,
                       c.identifier, c.title, e.confirmed, e.disputed
                FROM exams e
                JOIN courses c ON e.course_id = c.course_id
                WHERE NOT e.deleted
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
            return [
                {
                    "exam_id": r[0],
                    "date": str(r[1]),
                    "exam_type": r[2],
                    "course_id": r[3],
                    "department": r[4],
                    "identifier": r[5],
                    "title": r[6],
                    "confirmed": r[7],
                    "disputed": r[8],
                }
                for r in cur.fetchall()
            ]
