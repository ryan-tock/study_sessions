import os
import re
import json
import shutil
from collections import defaultdict
from datetime import datetime
from .database import get_db, get_db_for_user
from .auth import get_password_hash
from .config import USER_PASSWORD
from .pdf_parser import parse_common_hour_pdf, parse_finals_pdf
from .course_scraper import load_courses_from_cache

_APP_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(_APP_DIR, '..', 'data')
BACKUP_DIR = os.path.join(DATA_DIR, 'backups')

SEASON_LETTER = {'spring': 'A', 'fall': 'B'}
_LETTER_SEASON = {'A': 'spring', 'B': 'fall'}
_SEASON_RANK = {'spring': 1, 'fall': 2}
_BACKUP_NAME_RE = re.compile(r'^\d{4}-\d{2}-\d{2}_\d{6}$')
_TERM_NAME_RE = re.compile(r'^\d{4}_[A-Z]$')


def _term_dir(academic_year: int, season: str) -> str:
    letter = SEASON_LETTER.get(season, '?')
    return os.path.join(DATA_DIR, f"{academic_year}_{letter}")


def _exam_season(month: int) -> str:
    """Infer exam season from month: Jan-Jun → spring, Jul-Dec → fall."""
    return 'spring' if month <= 6 else 'fall'


def backup() -> str:
    """
    Snapshot the database to disk. Each backup is self-contained and captures:

      data/backups/{timestamp}/
        users.json               — static: all non-root users with tutor caps
        {year}_{letter}/         — current term snapshot
          enrollments.json       — who was in which courses this term
          exams.json             — this term's exams
          courses.json           — this term's courses

    The current term data is also mirrored to data/{year}_{letter}/ so that
    old-term restore logic still works without needing the backup present.

    Returns the backup name (timestamp string).
    """
    name = datetime.now().strftime('%Y-%m-%d_%H%M%S')
    backup_dir = os.path.join(BACKUP_DIR, name)
    os.makedirs(backup_dir, exist_ok=True)

    with get_db() as conn:
        with conn.cursor() as cur:
            # Static user rows
            cur.execute("""
                SELECT s.student_id, s.first_name, s.last_name, s.discord_id, sa.is_admin,
                       sa.hashed_password, sa.last_login, s.graduated_date, s.sharing
                FROM students s
                JOIN student_auth sa ON s.student_id = sa.student_id
                WHERE sa.is_root = FALSE
                ORDER BY s.last_name, s.first_name
            """)
            user_rows = cur.fetchall()

            # Tutor capabilities (static, term-independent)
            cur.execute("""
                SELECT s.student_id, c.department, c.identifier, t.confidence
                FROM tutors t
                JOIN students s ON t.student_id = s.student_id
                JOIN courses c ON t.course_id = c.course_id
                JOIN student_auth sa ON s.student_id = sa.student_id
                WHERE sa.is_root = FALSE
            """)
            tutors_by_id: dict[int, list] = defaultdict(list)
            for r in cur.fetchall():
                tutors_by_id[r[0]].append({
                    'department': r[1],
                    'identifier': r[2],
                    'confidence': r[3],
                })

            # Determine current term
            cur.execute("SELECT academic_year, season FROM current_term")
            ct_row = cur.fetchone()
            if ct_row:
                ct_year, ct_season = int(ct_row[0]), str(ct_row[1])
            else:
                ct_year = ct_season = None

            # Current term enrollments
            if ct_year:
                cur.execute("""
                    SELECT s.discord_id, s.first_name, s.last_name,
                           c.department, c.identifier
                    FROM enrollments e
                    JOIN students s ON e.student_id = s.student_id
                    JOIN courses c ON e.course_id = c.course_id
                    JOIN student_auth sa ON s.student_id = sa.student_id
                    WHERE sa.is_root = FALSE
                      AND (e.term).academic_year = %s
                      AND (e.term).season = %s::term_season
                    ORDER BY s.last_name, s.first_name, c.department, c.identifier
                """, (ct_year, ct_season))
                ct_enrollment_rows = cur.fetchall()
            else:
                ct_enrollment_rows = []

            # All exams
            cur.execute("""
                SELECT c.department, c.identifier, e.test_date, e.exam_type
                FROM exams e
                JOIN courses c ON e.course_id = c.course_id
                ORDER BY e.test_date, c.department, c.identifier
            """)
            exam_rows = cur.fetchall()

            # All courses
            cur.execute("""
                SELECT department, identifier, title, semester_hours,
                       (last_offered).academic_year, (last_offered).season
                FROM courses
                ORDER BY (last_offered).academic_year, (last_offered).season, department, identifier
            """)
            course_rows = cur.fetchall()

    # ── Static users ──────────────────────────────────────────────────────────
    users = [
        {
            'first_name': r[1],
            'last_name': r[2],
            'discord_id': r[3],
            'is_admin': bool(r[4]),
            'hashed_password': r[5],
            'last_login': r[6].isoformat() if r[6] else None,
            'graduated_date': r[7].isoformat() if r[7] else None,
            'sharing': str(r[8]),
            'tutor_capabilities': tutors_by_id.get(r[0], []),
        }
        for r in user_rows
    ]
    with open(os.path.join(backup_dir, 'users.json'), 'w') as f:
        json.dump(users, f, indent=2)

    # ── Group exams and courses by term ───────────────────────────────────────
    exams_by_term: dict[tuple, list] = {}
    for r in exam_rows:
        test_date = r[2]
        key = (test_date.year, _exam_season(test_date.month))
        exams_by_term.setdefault(key, []).append({
            'department': r[0],
            'identifier': r[1],
            'date': test_date.isoformat(),
            'exam_type': r[3],
        })

    courses_by_term: dict[tuple, list] = {}
    for r in course_rows:
        dept, ident, title, sem_hours, c_year, c_season = r
        key = (int(c_year), str(c_season))
        courses_by_term.setdefault(key, []).append({
            'department': dept,
            'identifier': ident,
            'title': title,
            'semester_hours': sem_hours,
        })

    # ── Current term snapshot (in backup dir + mirrored to term dir) ──────────
    if ct_year:
        ct_letter = SEASON_LETTER[ct_season]
        ct_name = f"{ct_year}_{ct_letter}"
        ct_backup_subdir = os.path.join(backup_dir, ct_name)
        os.makedirs(ct_backup_subdir, exist_ok=True)

        # Enrollments
        by_student: dict[str, dict] = {}
        for discord_id, first_name, last_name, dept, ident in ct_enrollment_rows:
            key = discord_id or f"{first_name}_{last_name}"
            if key not in by_student:
                by_student[key] = {
                    'discord_id': discord_id,
                    'first_name': first_name,
                    'last_name': last_name,
                    'courses': [],
                }
            by_student[key]['courses'].append({'department': dept, 'identifier': ident})
        enr_data = list(by_student.values())

        # Exams and courses for current term only
        ct_exams = exams_by_term.get((ct_year, ct_season), [])
        ct_courses = courses_by_term.get((ct_year, ct_season), [])

        for fname, data in [('enrollments.json', enr_data),
                             ('exams.json', ct_exams),
                             ('courses.json', ct_courses)]:
            with open(os.path.join(ct_backup_subdir, fname), 'w') as f:
                json.dump(data, f, indent=2)

        # Mirror to data/{ct_name}/ so old-term restore logic stays consistent
        ct_data_dir = os.path.join(DATA_DIR, ct_name)
        os.makedirs(ct_data_dir, exist_ok=True)
        for fname in ['enrollments.json', 'exams.json', 'courses.json']:
            shutil.copy(os.path.join(ct_backup_subdir, fname),
                        os.path.join(ct_data_dir, fname))

    # ── Write all terms' exams and courses to their term dirs ─────────────────
    for (y, s), exams in exams_by_term.items():
        d = _term_dir(y, s)
        os.makedirs(d, exist_ok=True)
        with open(os.path.join(d, 'exams.json'), 'w') as f:
            json.dump(exams, f, indent=2)

    for (y, s), courses in courses_by_term.items():
        d = _term_dir(y, s)
        os.makedirs(d, exist_ok=True)
        with open(os.path.join(d, 'courses.json'), 'w') as f:
            json.dump(courses, f, indent=2)

    return name


def _backup_term_subdir(backup_name: str) -> tuple[str | None, str | None]:
    """
    Return (subdir_path, term_name) for the term subdir inside a backup, or
    (None, None) if the backup has no term subdir.
    """
    b_dir = os.path.join(BACKUP_DIR, backup_name)
    if not os.path.isdir(b_dir):
        return None, None
    for entry in os.listdir(b_dir):
        if _TERM_NAME_RE.match(entry) and os.path.isdir(os.path.join(b_dir, entry)):
            return os.path.join(b_dir, entry), entry
    return None, None


def list_backups() -> list[dict]:
    """Return metadata for all user backups, newest first."""
    if not os.path.isdir(BACKUP_DIR):
        return []
    result = []
    for name in sorted(os.listdir(BACKUP_DIR), reverse=True):
        if not _BACKUP_NAME_RE.match(name):
            continue
        users_file = os.path.join(BACKUP_DIR, name, 'users.json')
        if not os.path.exists(users_file):
            continue
        try:
            with open(users_file) as f:
                users = json.load(f)
            _, term_name = _backup_term_subdir(name)
            result.append({'name': name, 'user_count': len(users), 'term': term_name})
        except Exception:
            pass
    return result


def delete_backup(name: str) -> bool:
    """Delete a backup directory by name. Returns True if deleted."""
    if not _BACKUP_NAME_RE.match(name):
        return False
    backup_path = os.path.join(BACKUP_DIR, name)
    if os.path.isdir(backup_path):
        shutil.rmtree(backup_path)
        return True
    return False


def _all_term_dirs() -> list[str]:
    """Return all valid term dirs, sorted oldest-to-newest."""
    dirs = []
    if not os.path.isdir(DATA_DIR):
        return dirs
    for entry in sorted(os.listdir(DATA_DIR)):
        term_dir = os.path.join(DATA_DIR, entry)
        if not os.path.isdir(term_dir):
            continue
        parts = entry.split('_')
        if len(parts) != 2 or not parts[0].isdigit() or parts[1] not in _LETTER_SEASON:
            continue
        dirs.append(term_dir)
    return dirs


def _wipe_for_restore(user: dict) -> None:
    """
    Clear all non-root data from the database in preparation for a restore.
    Preserves the root student row and their hashed_password in student_auth.
    Clears their refresh tokens so they must log in again after restore.

    Must be called with the root user so that RLS admin context is set and
    the DELETE statements are not silently filtered out by RLS policies.
    """
    with get_db_for_user(user) as conn:
        with conn.cursor() as cur:
            # study_sessions → exams → courses chain; clear dependents first
            cur.execute("DELETE FROM study_sessions")
            cur.execute("DELETE FROM refresh_tokens")
            cur.execute("DELETE FROM enrollments")
            cur.execute("DELETE FROM tutors")
            cur.execute("DELETE FROM exams")
            # Delete non-root students (cascades to their student_auth rows)
            cur.execute("""
                DELETE FROM students
                WHERE student_id NOT IN (
                    SELECT student_id FROM student_auth WHERE is_root = TRUE
                )
            """)
            cur.execute("DELETE FROM courses")
        conn.commit()


# PDF filenames to check during restore: (filename, exam_type_for_db)
_RESTORE_PDF_FILES = [
    ('common_hour.pdf', 'common_hour'),
    ('finals.pdf', 'final'),
    ('finals_overview.pdf', 'final'),
    ('finals_detailed.pdf', 'final'),
]


def _restore_exam_entry(cur, dept: str, ident: str, date: str, exam_type: str, creator_id: int) -> bool:
    """Look up course by dept+ident and insert exam. Returns True if inserted."""
    cur.execute(
        """SELECT course_id FROM courses
           WHERE department ILIKE %s AND identifier = %s
           ORDER BY (last_offered).academic_year DESC,
                    CASE (last_offered).season
                        WHEN 'fall' THEN 2
                        WHEN 'spring' THEN 1
                    END DESC
           LIMIT 1""",
        (dept, ident)
    )
    row = cur.fetchone()
    if not row:
        return False
    cur.execute(
        """INSERT INTO exams (course_id, test_date, exam_type, creator_id)
           VALUES (%s, %s::date, %s::exam_type, %s)
           ON CONFLICT (course_id, test_date, exam_type) DO NOTHING""",
        (row[0], date, exam_type, creator_id)
    )
    return cur.rowcount > 0


def restore_from_disk(user: dict, backup_name: str | None = None) -> bool:
    """
    Restore users, enrollments, courses, and exams from disk.

    For a given backup, the restore strategy is:
      - Old terms  → data/{year}_{letter}/ (courses, enrollments, exams)
      - Current term → backup/{timestamp}/{year}_{letter}/ (authoritative snapshot)
      - Static data  → backup/{timestamp}/users.json (users + tutor caps)

    After the DB restore, the backup's current-term files are copied back to
    data/{year}_{letter}/ so the on-disk state matches what was restored.

    Legacy: if users.json contains an 'enrollments' field those are also restored.

    Returns True if any data was found and processed.
    """
    all_dirs = _all_term_dirs()
    if not all_dirs:
        return False

    # Wipe all non-root data before restoring so this is idempotent and
    # doesn't require running reset.sh first.
    _wipe_for_restore(user)

    found_data = False

    # Resolve the backup to use
    users_file = None
    bt_subdir = None   # full path to backup's term subdir
    bt_name = None     # e.g. "2026_A"

    if backup_name:
        if _BACKUP_NAME_RE.match(backup_name):
            candidate = os.path.join(BACKUP_DIR, backup_name, 'users.json')
            if os.path.exists(candidate):
                users_file = candidate
            bt_subdir, bt_name = _backup_term_subdir(backup_name)
    else:
        # Newest backup
        if os.path.isdir(BACKUP_DIR):
            for bname in sorted(os.listdir(BACKUP_DIR), reverse=True):
                if not _BACKUP_NAME_RE.match(bname):
                    continue
                candidate = os.path.join(BACKUP_DIR, bname, 'users.json')
                if os.path.exists(candidate):
                    users_file = candidate
                    bt_subdir, bt_name = _backup_term_subdir(bname)
                    break
        # Legacy fallback: old-style term dir users.json
        if not users_file:
            for d in reversed(all_dirs):
                candidate = os.path.join(d, 'users.json')
                if os.path.exists(candidate):
                    users_file = candidate
                    break

    # ── 1. Restore courses ────────────────────────────────────────────────────
    # For the backup's current term, prefer the backup's courses.json.
    # For all other terms, use the on-disk term dir.
    with get_db_for_user(user) as conn:
        with conn.cursor() as cur:
            for d in all_dirs:
                term_name = os.path.basename(d)
                if term_name == bt_name and bt_subdir:
                    courses_file = os.path.join(bt_subdir, 'courses.json')
                else:
                    courses_file = os.path.join(d, 'courses.json')
                if not os.path.exists(courses_file):
                    continue
                parts = term_name.split('_')
                c_year = int(parts[0])
                c_season = _LETTER_SEASON[parts[1]]
                with open(courses_file) as f:
                    courses = json.load(f)
                for c in courses:
                    cur.execute(
                        """INSERT INTO courses (department, identifier, title, semester_hours, last_offered)
                           SELECT %s, %s, %s, %s, ROW(%s, %s)::academic_term
                           WHERE NOT EXISTS (
                               SELECT 1 FROM courses
                               WHERE department = %s AND identifier = %s
                               AND (last_offered).academic_year = %s
                               AND (last_offered).season = %s::term_season
                           )""",
                        (c['department'], c['identifier'], c.get('title'), c.get('semester_hours'),
                         c_year, c_season,
                         c['department'], c['identifier'], c_year, c_season)
                    )
                    if cur.rowcount:
                        found_data = True
        conn.commit()

    # ── 2. Restore courses from scraper cache (fallback for missing courses) ──
    cache_courses: dict[tuple, dict] = {}
    for d in all_dirs:
        parts = os.path.basename(d).split('_')
        c_year = int(parts[0])
        c_season = _LETTER_SEASON[parts[1]]
        c_rank = (c_year, _SEASON_RANK[c_season])
        try:
            fetched = load_courses_from_cache(DATA_DIR, c_year, c_season)
        except Exception:
            fetched = []
        for c in fetched:
            key = (c['department'].upper(), c['identifier'])
            existing = cache_courses.get(key)
            if not existing or c_rank > (existing['year'], _SEASON_RANK[existing['season']]):
                cache_courses[key] = {**c, 'year': c_year, 'season': c_season}

    if cache_courses:
        with get_db_for_user(user) as conn:
            with conn.cursor() as cur:
                for c in cache_courses.values():
                    cur.execute(
                        """INSERT INTO courses (department, identifier, title, semester_hours, last_offered)
                           SELECT %s, %s, %s, %s, ROW(%s, %s::term_season)::academic_term
                           WHERE NOT EXISTS (
                               SELECT 1 FROM courses WHERE department = %s AND identifier = %s
                           )""",
                        (c['department'], c['identifier'], c.get('title'), c.get('semester_hours'),
                         c['year'], c['season'],
                         c['department'], c['identifier'])
                    )
                    if cur.rowcount:
                        found_data = True
            conn.commit()

    # ── 3. Restore users ──────────────────────────────────────────────────────
    discord_id_to_student: dict[str, int] = {}

    if users_file:
        with open(users_file) as f:
            users = json.load(f)
        if users:
            found_data = True
            fallback_pw = get_password_hash(USER_PASSWORD)
            with get_db_for_user(user) as conn:
                with conn.cursor() as cur:
                    for u in users:
                        sharing = u.get('sharing') or 'open'
                        cur.execute(
                            """INSERT INTO students (first_name, last_name, discord_id, graduated_date, sharing)
                               VALUES (%s, %s, %s, %s::date, %s::sharing_setting) RETURNING student_id""",
                            (u['first_name'], u.get('last_name'), u.get('discord_id'),
                             u.get('graduated_date'), sharing)
                        )
                        student_id = cur.fetchone()[0]
                        if u.get('discord_id'):
                            discord_id_to_student[u['discord_id']] = student_id
                        hashed_pw = u.get('hashed_password') or fallback_pw
                        cur.execute(
                            """INSERT INTO student_auth (student_id, hashed_password, is_admin, last_login)
                               VALUES (%s, %s, %s, %s::timestamptz)""",
                            (student_id, hashed_pw, bool(u.get('is_admin', False)),
                             u.get('last_login'))
                        )
                        # Legacy: embedded enrollments in users.json
                        for enr in u.get('enrollments', []):
                            cur.execute(
                                "SELECT course_id FROM courses WHERE department = %s AND identifier = %s LIMIT 1",
                                (enr['department'], enr['identifier'])
                            )
                            row = cur.fetchone()
                            if row:
                                cur.execute(
                                    """INSERT INTO enrollments (student_id, course_id, term)
                                       VALUES (%s, %s, ROW(%s, %s::term_season)::academic_term)
                                       ON CONFLICT DO NOTHING""",
                                    (student_id, row[0], enr['academic_year'], enr['season'])
                                )
                        for tut in u.get('tutor_capabilities', []):
                            cur.execute(
                                "SELECT course_id FROM courses WHERE department = %s AND identifier = %s LIMIT 1",
                                (tut['department'], tut['identifier'])
                            )
                            row = cur.fetchone()
                            if row:
                                cur.execute(
                                    """INSERT INTO tutors (student_id, course_id, confidence)
                                       VALUES (%s, %s, %s)
                                       ON CONFLICT (student_id, course_id) DO NOTHING""",
                                    (student_id, row[0], tut.get('confidence'))
                                )
                conn.commit()

    # ── 4. Restore enrollments ────────────────────────────────────────────────
    # Current term: use backup's enrollments.json (authoritative).
    # All other terms: use data/{term}/enrollments.json.
    with get_db_for_user(user) as conn:
        with conn.cursor() as cur:
            for d in all_dirs:
                term_name = os.path.basename(d)
                if term_name == bt_name and bt_subdir:
                    enr_file = os.path.join(bt_subdir, 'enrollments.json')
                else:
                    enr_file = os.path.join(d, 'enrollments.json')
                if not os.path.exists(enr_file):
                    continue
                parts = term_name.split('_')
                e_year = int(parts[0])
                e_season = _LETTER_SEASON[parts[1]]
                with open(enr_file) as f:
                    enr_list = json.load(f)
                for entry in enr_list:
                    discord_id = entry.get('discord_id')
                    student_id = discord_id_to_student.get(discord_id) if discord_id else None
                    if not student_id:
                        continue
                    for course in entry.get('courses', []):
                        cur.execute(
                            "SELECT course_id FROM courses WHERE department = %s AND identifier = %s LIMIT 1",
                            (course['department'], course['identifier'])
                        )
                        row = cur.fetchone()
                        if row:
                            cur.execute(
                                """INSERT INTO enrollments (student_id, course_id, term)
                                   VALUES (%s, %s, ROW(%s, %s::term_season)::academic_term)
                                   ON CONFLICT DO NOTHING""",
                                (student_id, row[0], e_year, e_season)
                            )
                            if cur.rowcount:
                                found_data = True
        conn.commit()

    # ── 5. Restore exams ──────────────────────────────────────────────────────
    # Current term: use backup's exams.json. All other terms: use term dirs + PDFs.
    with get_db_for_user(user) as conn:
        with conn.cursor() as cur:
            for d in all_dirs:
                term_name = os.path.basename(d)
                if term_name == bt_name and bt_subdir:
                    exams_file = os.path.join(bt_subdir, 'exams.json')
                else:
                    exams_file = os.path.join(d, 'exams.json')

                if os.path.exists(exams_file):
                    with open(exams_file) as f:
                        exams = json.load(f)
                    for e in exams:
                        if _restore_exam_entry(cur, e['department'], e['identifier'],
                                               e['date'], e['exam_type'], user['student_id']):
                            found_data = True
                else:
                    for pdf_name, exam_type in _RESTORE_PDF_FILES:
                        pdf_path = os.path.join(d, pdf_name)
                        if not os.path.isfile(pdf_path):
                            continue
                        try:
                            with open(pdf_path, 'rb') as f:
                                pdf_bytes = f.read()
                            if exam_type == 'common_hour':
                                entries = parse_common_hour_pdf(pdf_bytes)
                            else:
                                entries = parse_finals_pdf(pdf_bytes)
                            for e in entries:
                                if _restore_exam_entry(cur, e['department'], e['identifier'],
                                                       e['date'], exam_type, user['student_id']):
                                    found_data = True
                        except Exception:
                            pass
        conn.commit()

    # ── 6. Sync backup's term data back to data/{term}/ ───────────────────────
    # After restore, overwrite the on-disk term dir with the backup's snapshot
    # so the filesystem reflects exactly what was restored.
    if bt_subdir and bt_name:
        ct_data_dir = os.path.join(DATA_DIR, bt_name)
        os.makedirs(ct_data_dir, exist_ok=True)
        for fname in ['enrollments.json', 'exams.json', 'courses.json']:
            src = os.path.join(bt_subdir, fname)
            if os.path.exists(src):
                shutil.copy(src, os.path.join(ct_data_dir, fname))

    return found_data
