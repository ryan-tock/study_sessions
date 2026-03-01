"""Tests for admin data routes: backup/restore, wipe, exam/course import."""
import json
import os
from contextlib import contextmanager
from unittest.mock import patch, MagicMock, AsyncMock
from tests.conftest import auth_cookies, FakeConnection


ADMIN = auth_cookies(student_id=1, is_admin=True)
ROOT = auth_cookies(student_id=1, is_admin=True, is_root=True)

# Use impossible term for any disk writes
FAKE_YEAR = 1970
FAKE_SEASON = "spring"
FAKE_TERM_DIR = "1970_A"


# ---------------------------------------------------------------------------
# Backup / Restore
# ---------------------------------------------------------------------------

class TestBackup:
    @patch("app.routes.admin_data.backup", return_value="2026-01-01_120000")
    def test_backup_root_only(self, mock_backup, client):
        resp = client.post("/admin/backup", cookies=ROOT)
        assert resp.status_code == 200
        assert resp.json()["ok"] is True
        assert resp.json()["name"] == "2026-01-01_120000"
        mock_backup.assert_called_once()

    def test_backup_non_root_forbidden(self, client):
        resp = client.post("/admin/backup", cookies=ADMIN)
        assert resp.status_code == 403


class TestGetBackups:
    @patch("app.routes.admin_data.list_backups", return_value=[
        {"name": "2026-01-01_120000", "size": 1234}
    ])
    def test_list_backups_root(self, mock_list, client):
        resp = client.get("/admin/api/backups", cookies=ROOT)
        assert resp.status_code == 200
        assert len(resp.json()) == 1

    def test_list_backups_non_root_forbidden(self, client):
        resp = client.get("/admin/api/backups", cookies=ADMIN)
        assert resp.status_code == 403


class TestRestoreBackup:
    @patch("app.routes.admin_data.restore_from_disk", return_value=True)
    def test_restore_root(self, mock_restore, client):
        resp = client.post("/admin/api/restore_backup",
                           data={"backup_name": "2026-01-01_120000"}, cookies=ROOT)
        assert resp.status_code == 200
        assert resp.json()["ok"] is True

    def test_restore_non_root_forbidden(self, client):
        resp = client.post("/admin/api/restore_backup",
                           data={"backup_name": "fake"}, cookies=ADMIN)
        assert resp.status_code == 403


class TestDeleteBackup:
    @patch("app.routes.admin_data.delete_backup", return_value=True)
    def test_delete_root(self, mock_delete, client):
        resp = client.delete("/admin/api/backup/2026-01-01_120000", cookies=ROOT)
        assert resp.status_code == 200
        assert resp.json()["ok"] is True

    def test_delete_non_root_forbidden(self, client):
        resp = client.delete("/admin/api/backup/fake", cookies=ADMIN)
        assert resp.status_code == 403


# ---------------------------------------------------------------------------
# Data wipe
# ---------------------------------------------------------------------------

class TestWipeSelective:
    def test_wipe_with_fake_term(self, client, tmp_data_dir):
        """Use 1970_A term dir so we never touch real data."""
        term_dir = os.path.join(tmp_data_dir["data"], FAKE_TERM_DIR)
        os.makedirs(term_dir, exist_ok=True)
        # Create fake files to wipe
        with open(os.path.join(term_dir, "exams.json"), "w") as f:
            f.write("{}")
        with open(os.path.join(term_dir, "courses.json"), "w") as f:
            f.write("{}")

        with patch("app.routes.admin_data.DATA_DIR", tmp_data_dir["data"]), \
             patch("app.routes.admin_data.wipe_course_cache_files", return_value=0):
            resp = client.post("/admin/wipe_selective", data={
                "what": ["exams", "courses"],
                "term": [FAKE_TERM_DIR],
            }, cookies=ROOT)

        assert resp.status_code == 302
        assert "Wiped" in resp.headers["location"]
        assert not os.path.exists(os.path.join(term_dir, "exams.json"))
        assert not os.path.exists(os.path.join(term_dir, "courses.json"))

    def test_wipe_non_root_redirects(self, client):
        resp = client.post("/admin/wipe_selective", data={
            "what": ["exams"], "term": [FAKE_TERM_DIR],
        }, cookies=ADMIN)
        assert resp.status_code == 302
        assert "/admin/portal" in resp.headers["location"]
        assert "message" not in resp.headers["location"]

    def test_wipe_nothing_selected(self, client):
        resp = client.post("/admin/wipe_selective", data={}, cookies=ROOT)
        assert resp.status_code == 302
        assert "Nothing+selected" in resp.headers["location"]


class TestRefreshCourseCache:
    def test_refresh_success(self, client):
        with patch("app.routes.admin_data.get_db") as mock_db, \
             patch("app.routes.admin_data.wipe_course_cache_files", return_value=3):
            conn = FakeConnection()
            conn._cursor._results = [(2026, "spring")]
            @contextmanager
            def db():
                yield conn
            mock_db.side_effect = db
            resp = client.post("/admin/refresh_course_cache", cookies=ADMIN)
        assert resp.status_code == 302
        assert "Cache+cleared" in resp.headers["location"] or "Cache%20cleared" in resp.headers["location"]

    def test_refresh_no_term(self, client):
        with patch("app.routes.admin_data.get_db") as mock_db:
            conn = FakeConnection()
            conn._cursor._results = [None]
            @contextmanager
            def db():
                yield conn
            mock_db.side_effect = db
            resp = client.post("/admin/refresh_course_cache", cookies=ADMIN)
        assert resp.status_code == 302
        assert "Cannot" in resp.headers["location"]


# ---------------------------------------------------------------------------
# Exam import
# ---------------------------------------------------------------------------

class TestPreviewExamPdf:
    @patch("app.routes.admin_data.parse_common_hour_pdf")
    def test_preview_common_hour(self, mock_parse, client):
        mock_parse.return_value = [
            {"department": "CSCI", "identifier": "101", "date": "1970-03-15"},
        ]
        with patch("app.routes.admin_data.get_db") as mock_db:
            conn = FakeConnection()
            conn._cursor._results = [
                (1, "Intro to CS"),  # course lookup
                None,               # duplicate check (no dup)
            ]
            @contextmanager
            def db():
                yield conn
            mock_db.side_effect = db

            resp = client.post("/admin/api/preview_exam_pdf",
                               data={"exam_type": "common_hour"},
                               files={"pdf_file": ("test.pdf", b"%PDF-fake", "application/pdf")},
                               cookies=ADMIN)

        assert resp.status_code == 200
        data = resp.json()
        assert data["exam_type"] == "common_hour"
        assert len(data["entries"]) == 1
        assert data["entries"][0]["found"] is True

    def test_invalid_exam_type(self, client):
        resp = client.post("/admin/api/preview_exam_pdf",
                           data={"exam_type": "invalid"},
                           files={"pdf_file": ("test.pdf", b"%PDF-fake", "application/pdf")},
                           cookies=ADMIN)
        assert resp.status_code == 400

    def test_non_pdf_file(self, client):
        resp = client.post("/admin/api/preview_exam_pdf",
                           data={"exam_type": "common_hour"},
                           files={"pdf_file": ("test.txt", b"not a pdf", "text/plain")},
                           cookies=ADMIN)
        assert resp.status_code == 400


class TestImportExams:
    def test_import_exams_success(self, client, tmp_data_dir):
        entries = [{"department": "CSCI", "identifier": "101", "date": "1970-03-15"}]
        with patch("app.routes.admin_data.get_db_for_user") as mock_db, \
             patch("app.routes.admin_data.DATA_DIR", tmp_data_dir["data"]):
            conn = FakeConnection()
            conn._cursor._results = [(1,)]  # course lookup
            conn._cursor.rowcount = 1
            @contextmanager
            def db(user):
                yield conn
            mock_db.side_effect = db
            resp = client.post("/admin/import_exams", data={
                "entries_json": json.dumps(entries),
                "exam_type": "common_hour",
                "pdf_b64": "",
            }, cookies=ADMIN)

        assert resp.status_code == 302
        assert "Imported" in resp.headers["location"]

    def test_import_invalid_json(self, client):
        resp = client.post("/admin/import_exams", data={
            "entries_json": "not json",
            "exam_type": "common_hour",
        }, cookies=ADMIN)
        assert resp.status_code == 302
        assert "Invalid" in resp.headers["location"]

    def test_import_invalid_exam_type(self, client):
        resp = client.post("/admin/import_exams", data={
            "entries_json": "[]",
            "exam_type": "bad_type",
        }, cookies=ADMIN)
        assert resp.status_code == 302
        assert "Invalid" in resp.headers["location"]


# ---------------------------------------------------------------------------
# Course import
# ---------------------------------------------------------------------------

class TestPreviewCourses:
    @patch("app.routes.admin_data.fetch_courses")
    def test_preview_success(self, mock_fetch, client):
        mock_fetch.return_value = (
            [{"department": "CSCI", "identifier": "101", "title": "Intro to CS",
              "semester_hours": "3"}],
            [],  # no errors
        )
        with patch("app.routes.admin_data.get_db") as mock_db:
            conn = FakeConnection()
            conn._cursor._fetchall_results = [[]]  # no existing courses
            @contextmanager
            def db():
                yield conn
            mock_db.side_effect = db
            resp = client.post("/admin/api/preview_courses", data={
                "academic_year": FAKE_YEAR, "season": FAKE_SEASON,
            }, cookies=ADMIN)

        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 1
        assert data["new"] == 1

    def test_invalid_season(self, client):
        resp = client.post("/admin/api/preview_courses", data={
            "academic_year": FAKE_YEAR, "season": "summer",
        }, cookies=ADMIN)
        assert resp.status_code == 400


class TestImportCourses:
    @patch("app.routes.admin_data.course_pending_cache_exists", return_value=True)
    @patch("app.routes.admin_data.load_courses_from_cache")
    @patch("app.routes.admin_data.promote_pending_cache")
    def test_import_new_courses(self, mock_promote, mock_load, mock_exists, client):
        mock_load.return_value = [
            {"department": "CSCI", "identifier": "101", "title": "Intro to CS",
             "semester_hours": "3"}
        ]
        with patch("app.routes.admin_data.get_db_for_user") as mock_db:
            conn = FakeConnection()
            conn._cursor._fetchall_results = [[]]  # no existing courses
            conn._cursor.rowcount = 1
            @contextmanager
            def db(user):
                yield conn
            mock_db.side_effect = db
            resp = client.post("/admin/import_courses", data={
                "academic_year": FAKE_YEAR, "season": FAKE_SEASON,
            }, cookies=ADMIN)

        assert resp.status_code == 302
        assert "1+new" in resp.headers["location"] or "1%20new" in resp.headers["location"]
        mock_promote.assert_called_once()

    @patch("app.routes.admin_data.course_pending_cache_exists", return_value=False)
    def test_import_no_preview_data(self, mock_exists, client):
        resp = client.post("/admin/import_courses", data={
            "academic_year": FAKE_YEAR, "season": FAKE_SEASON,
        }, cookies=ADMIN)
        assert resp.status_code == 302
        assert "preview" in resp.headers["location"].lower()

    def test_import_invalid_season(self, client):
        resp = client.post("/admin/import_courses", data={
            "academic_year": FAKE_YEAR, "season": "summer",
        }, cookies=ADMIN)
        assert resp.status_code == 302
        assert "Invalid" in resp.headers["location"]


# ---------------------------------------------------------------------------
# Import from existing disk files
# ---------------------------------------------------------------------------

class TestImportCoursesFromCache:
    @patch("app.routes.admin_data.course_cache_exists", return_value=True)
    @patch("app.routes.admin_data.load_courses_from_cache")
    def test_import_success(self, mock_load, mock_exists, client):
        mock_load.return_value = [
            {"department": "CSCI", "identifier": "101", "title": "Intro to CS",
             "semester_hours": "3"}
        ]
        with patch("app.routes.admin_data.get_db") as mock_get_db, \
             patch("app.routes.admin_data.get_db_for_user") as mock_db_user:
            # get_db for current_term query
            term_conn = FakeConnection()
            term_conn._cursor._results = [(FAKE_YEAR, FAKE_SEASON)]
            @contextmanager
            def db():
                yield term_conn
            mock_get_db.side_effect = db

            # get_db_for_user for the insert
            ins_conn = FakeConnection()
            ins_conn._cursor._fetchall_results = [[]]  # no existing courses
            ins_conn._cursor.rowcount = 1
            @contextmanager
            def db_user(user):
                yield ins_conn
            mock_db_user.side_effect = db_user

            resp = client.post("/admin/api/import_courses_from_cache", cookies=ADMIN)

        assert resp.status_code == 200
        data = resp.json()
        assert data["ok"] is True
        assert data["inserted"] == 1

    def test_import_no_term(self, client):
        with patch("app.routes.admin_data.get_db") as mock_db:
            conn = FakeConnection()
            conn._cursor._results = [None]
            @contextmanager
            def db():
                yield conn
            mock_db.side_effect = db
            resp = client.post("/admin/api/import_courses_from_cache", cookies=ADMIN)
        assert resp.status_code == 400

    @patch("app.routes.admin_data.course_cache_exists", return_value=False)
    def test_import_no_cache(self, mock_exists, client):
        with patch("app.routes.admin_data.get_db") as mock_db:
            conn = FakeConnection()
            conn._cursor._results = [(FAKE_YEAR, FAKE_SEASON)]
            @contextmanager
            def db():
                yield conn
            mock_db.side_effect = db
            resp = client.post("/admin/api/import_courses_from_cache", cookies=ADMIN)
        assert resp.status_code == 404

    def test_non_admin_forbidden(self, client):
        resp = client.post("/admin/api/import_courses_from_cache",
                           cookies=auth_cookies(is_admin=False))
        assert resp.status_code == 403


class TestImportExamsFromDisk:
    @patch("app.routes.admin_data.parse_common_hour_pdf")
    def test_import_success(self, mock_parse, client, tmp_data_dir):
        mock_parse.return_value = [
            {"department": "CSCI", "identifier": "101", "date": "1970-03-15"},
        ]
        # Create a fake PDF on disk
        term_dir = os.path.join(tmp_data_dir["data"], FAKE_TERM_DIR)
        os.makedirs(term_dir, exist_ok=True)
        with open(os.path.join(term_dir, "common_hour.pdf"), "wb") as f:
            f.write(b"%PDF-fake")

        with patch("app.routes.admin_data.get_db") as mock_get_db, \
             patch("app.routes.admin_data.get_db_for_user") as mock_db_user, \
             patch("app.routes.admin_data.DATA_DIR", tmp_data_dir["data"]):
            term_conn = FakeConnection()
            term_conn._cursor._results = [(FAKE_YEAR, FAKE_SEASON)]
            @contextmanager
            def db():
                yield term_conn
            mock_get_db.side_effect = db

            ins_conn = FakeConnection()
            ins_conn._cursor._results = [(1,)]  # course lookup
            ins_conn._cursor.rowcount = 1
            @contextmanager
            def db_user(user):
                yield ins_conn
            mock_db_user.side_effect = db_user

            resp = client.post("/admin/api/import_exams_from_disk",
                               data={"exam_type": "common_hour"}, cookies=ADMIN)

        assert resp.status_code == 200
        data = resp.json()
        assert data["ok"] is True
        assert data["inserted"] == 1

    def test_invalid_exam_type(self, client):
        resp = client.post("/admin/api/import_exams_from_disk",
                           data={"exam_type": "midterm"}, cookies=ADMIN)
        assert resp.status_code == 400

    def test_no_term(self, client):
        with patch("app.routes.admin_data.get_db") as mock_db:
            conn = FakeConnection()
            conn._cursor._results = [None]
            @contextmanager
            def db():
                yield conn
            mock_db.side_effect = db
            resp = client.post("/admin/api/import_exams_from_disk",
                               data={"exam_type": "common_hour"}, cookies=ADMIN)
        assert resp.status_code == 400

    def test_no_pdf_on_disk(self, client, tmp_data_dir):
        with patch("app.routes.admin_data.get_db") as mock_db, \
             patch("app.routes.admin_data.DATA_DIR", tmp_data_dir["data"]):
            conn = FakeConnection()
            conn._cursor._results = [(FAKE_YEAR, FAKE_SEASON)]
            @contextmanager
            def db():
                yield conn
            mock_db.side_effect = db
            resp = client.post("/admin/api/import_exams_from_disk",
                               data={"exam_type": "final"}, cookies=ADMIN)
        assert resp.status_code == 404

    def test_non_admin_forbidden(self, client):
        resp = client.post("/admin/api/import_exams_from_disk",
                           data={"exam_type": "common_hour"},
                           cookies=auth_cookies(is_admin=False))
        assert resp.status_code == 403


# ---------------------------------------------------------------------------
# Calendar exams
# ---------------------------------------------------------------------------

class TestCalendarExams:
    def test_get_calendar(self, client):
        with patch("app.routes.admin_data.get_db") as mock_db:
            conn = FakeConnection()
            conn._cursor._fetchall_results = [
                [(42, "1970-03-15", "common_hour", 10, "CSCI", "101", "Intro to CS", True, False)]
            ]
            @contextmanager
            def db():
                yield conn
            mock_db.side_effect = db
            resp = client.get("/admin/api/calendar_exams", cookies=ADMIN)

        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 1
        assert data[0]["exam_type"] == "common_hour"

    def test_calendar_non_admin_forbidden(self, client):
        resp = client.get("/admin/api/calendar_exams", cookies=auth_cookies(is_admin=False))
        assert resp.status_code == 403
