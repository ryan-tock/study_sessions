# Architecture

## Stack

- **Backend**: FastAPI (Python), raw SQL via psycopg2, no ORM
- **Frontend**: Jinja2 server-rendered HTML, vanilla JS (no framework), Fuse.js for fuzzy search
- **Database**: PostgreSQL with Row-Level Security (RLS)
- **Auth**: JWT in httponly cookies + refresh tokens

## Project Structure

```
app/
  main.py              App creation, static mount, router includes (~30 lines)
  config.py            Env var loading via python-dotenv
  database.py          psycopg2 ThreadedConnectionPool, get_db(), get_db_for_user()
  auth.py              Password hashing (bcrypt), JWT create/verify, refresh tokens
  dependencies.py      FastAPI deps: get_current_user(), require_auth(), require_admin()
  helpers.py           validate_name(), validate_password(), get_user_profile(),
                       constants (AVATAR_DIR, DATA_DIR), Jinja2Templates instance
  persistence.py       Backup/restore logic (DB <-> JSON on disk)
  pdf_parser.py        Parse common-hour and finals exam PDFs (pypdf)
  course_scraper.py    Scrape catalog.mines.edu, cache HTML to disk, html.unescape titles
  routes/
    auth.py            GET / POST /login POST /logout, rate limiting
    user.py            User portal pages + /api/my/* enrollment/tutor/assessment/session APIs
    admin.py           Admin portal, user CRUD, discord validation, create user,
                       assessment review, study sessions, course links
    admin_data.py      Backup/restore, wipe, course/exam import, calendar
    public.py          Unauthenticated: /api/users/*, /api/courses, /api/discord_avatar/*
    discord.py         validate_discord_id(), download_and_cache_avatar(), avatar routes
  templates/
    login.html         Login page with name search
    user_portal.html   Student dashboard (classes, tutors, assessments, study sessions)
    admin_portal.html  Admin dashboard (users, imports, exams, sessions, links)
    set_password.html  First-login password setup
    change_password.html
    privacy.html       Sharing preference picker
  static/
    css/style.css      All CSS (includes mobile responsive rules for user portal)
    js/
      login.js         Fuse.js user search, avatar loading, prefill
      user_portal.js   Course search, enrollments, tutor capabilities, assessments,
                       study sessions, course tutors
      admin_portal.js  User management, import workflows, backup UI, exam calendar,
                       assessment review, study session scheduling, course link modal
    avatars/           Cached Discord PNGs (gitignored)
data/
  {year}_{letter}/     Term dirs (2026_A = Spring 2026, 2025_B = Fall 2025)
    courses.json       Course catalog snapshot
    exams.json         Exam schedule snapshot
    enrollments.json   Student enrollments snapshot
    coursesaz_website/ Cached department HTML from scraper
  backups/
    {timestamp}/       Backup dirs (e.g. 2026-01-15_143022)
      users.json       All non-root users + tutor capabilities
      {year}_{letter}/ Current-term snapshot at backup time
tests/
  conftest.py          Mock infrastructure (FakeCursor, FakeConnection, auth helpers)
  test_auth.py         Login/logout/redirect tests
  test_user.py         User portal + enrollment/tutor/assessment/session API tests
  test_admin.py        Admin portal + user management + study session + course link tests
  test_admin_data.py   Backup/restore/wipe + import tests
  test_public.py       Public API tests
  test_discord.py      Avatar serve/fetch + validation tests
init.sql               Full DB schema with RLS policies
reset.sh               Drop/recreate DB, create app_user, insert root account
```

## Database Connections

Two context managers in `database.py`:

- **`get_db()`** -- Returns a pooled connection with no RLS context. Used for admin operations that need to bypass row-level security.
- **`get_db_for_user(user)`** -- Sets `app.current_user_id` and `app.is_admin` as PostgreSQL session variables via `set_config()`. This activates RLS policies. Session vars are cleared before the connection returns to the pool.

The connection pool is created at module import time (`ThreadedConnectionPool`), which is why tests must mock `psycopg2.pool.ThreadedConnectionPool` before importing any app code.

## Authentication Flow

1. User submits first_name + last_name + password to `POST /login`
2. Rate limiter checks: max 5 failed attempts per name per 1-minute window
3. `authenticate_user()` looks up the student, verifies bcrypt hash, records `last_login`
4. On success: creates JWT access token (30min) and refresh token (7 days), sets both as httponly cookies
5. First-time users (`last_login` was NULL) get `is_first_login=True` in the JWT and are redirected to `/user/set_password`
6. On login, if the user has a Discord ID and their avatar hasn't been checked in 24h, a background task refreshes it

## Row-Level Security

RLS is enabled on all data tables. Key policies:

- **Enrollments/Tutors visibility** depends on the student's `sharing` setting:
  - `open` -- visible to all authenticated users
  - `common_class` -- only visible to students in the same course (same term for enrollments, any term for tutors)
  - `closed` -- only visible to the student themselves and admins
- **Non-admins** can INSERT/UPDATE/DELETE their own enrollments and tutor capabilities
- **Admins** bypass all sharing restrictions
- Two `SECURITY DEFINER` functions (`my_course_ids_for_term`, `my_all_course_ids`) prevent infinite recursion in sharing policies

## Course Links

Courses can be linked with two types:

- **Strong** -- Courses share the same test. Study sessions are combined: students from both courses attend together and tutors for either course are available. The `linked_course_ids()` function resolves strong-link groups via recursive CTE.
- **Weak** -- Courses cover the same subject but have separate tests. Tutors transfer across weakly linked courses but sessions and student lists stay separate. The `linked_course_ids_any()` function resolves all links (strong + weak).

Links are managed from the exam view in the admin portal (click the link icon on any exam row). Suggestions are auto-generated based on title similarity within the same department.

## Assessment Workflow

1. Students can report in-class tests and quizzes (created as unconfirmed)
2. Students can dispute imported finals (marks exam as disputed)
3. Admins see pending/disputed items in the assessment review panel
4. Admins confirm (approve report / delete disputed final) or revert (reject report / restore disputed final)
5. Exams within 3 days that lack a study session show as "needs session" todos (deduplicated across strongly linked courses)
6. Calendar and pending views filter to only show exams with enrolled students

## Study Sessions

- Created by admins from the scheduling modal (triggered from "needs session" todos or the session list)
- Tutor is optional (can schedule "open" sessions with no assigned tutor)
- Tutor validation uses `linked_course_ids_any()` (both strong and weak links)
- Student lists use `linked_course_ids()` (strong links only)
- Students see sessions for their enrolled courses + sessions where they are the assigned tutor

## Template/JS Pattern

Templates pass dynamic data to JS via `<script type="application/json">` tags (not inline Jinja2 in JS). The JS files read these tags with `JSON.parse()`. No `{{ }}` expressions appear inside JS code.

## Term Convention

Terms are `(academic_year, season)` in the DB and `{year}_{letter}` on disk:
- A = spring, B = fall
- Example: `2026_A` = Spring 2026

The `current_term` DB view auto-determines the active term by finding which term's exams are closest to today. Falls back to calendar (Jan-Jun = spring, Jul-Dec = fall) when no exams exist.
