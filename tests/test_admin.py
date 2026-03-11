"""Tests for admin routes: portal, user management, discord validation, create user."""
import json
from contextlib import contextmanager
from unittest.mock import patch, MagicMock
from tests.conftest import auth_cookies, FakeConnection


ADMIN = auth_cookies(student_id=1, is_admin=True)
ROOT = auth_cookies(student_id=1, is_admin=True, is_root=True)


def _portal_db():
    """Return a mock get_db_for_user that provides plausible admin portal data."""
    conn = FakeConnection()
    # users query: student_id, first_name, last_name, role, is_root, discord_id
    conn._cursor._fetchall_results = [
        [(1, "Root", "Admin", None, True, None),
         (2, "Alice", "Smith", "scholarship_chair", False, "123456")]
    ]
    # current_term query
    conn._cursor._results = [(2026, "spring")]
    return conn


class TestAdminPortal:
    def test_unauthenticated_redirects(self, client):
        resp = client.get("/admin/portal")
        assert resp.status_code == 302

    def test_non_admin_forbidden(self, client):
        resp = client.get("/admin/portal", cookies=auth_cookies(is_admin=False))
        assert resp.status_code == 403

    def test_admin_sees_portal(self, client):
        with patch("app.routes.admin.get_db_for_user") as mock_dbu, \
             patch("app.routes.admin.get_db") as mock_db, \
             patch("app.routes.admin.get_user_profile", return_value={
                 "first_name": "Root", "last_name": "Admin", "discord_id": None, "sharing": "closed"
             }), \
             patch("app.routes.admin.course_cache_exists", return_value=False), \
             patch("app.routes.admin._list_wipeble_terms", return_value=[]):
            conn = FakeConnection()
            # student_id, first_name, last_name, role, is_root, discord_id
            conn._cursor._fetchall_results = [
                [(1, "Root", "Admin", None, True, None)]
            ]
            @contextmanager
            def dbu(user):
                yield conn
            mock_dbu.side_effect = dbu

            conn2 = FakeConnection()
            conn2._cursor._results = [(2026, "spring")]
            @contextmanager
            def db():
                yield conn2
            mock_db.side_effect = db

            resp = client.get("/admin/portal", cookies=ADMIN)
        assert resp.status_code == 200
        assert "Admin Portal" in resp.text


class TestSetRole:
    def _db(self, is_root, current_role):
        conn = FakeConnection()
        conn._cursor._results = [(is_root, current_role)]
        @contextmanager
        def db():
            yield conn
        return db

    def test_set_role_success(self, client):
        with patch("app.routes.admin.get_db") as mock_db:
            mock_db.side_effect = self._db(False, None)
            resp = client.post("/admin/set_role", data={
                "target_id": 2, "role": "study_session_coordinator"
            }, cookies=ROOT)
        assert resp.status_code == 302
        assert "/admin/portal" in resp.headers["location"]
        assert "message" not in resp.headers["location"]

    def test_downgrade_to_user(self, client):
        with patch("app.routes.admin.get_db") as mock_db:
            mock_db.side_effect = self._db(False, "study_session_coordinator")
            resp = client.post("/admin/set_role", data={
                "target_id": 2, "role": "user"
            }, cookies=ROOT)
        assert resp.status_code == 302
        assert "message" not in resp.headers["location"]

    def test_empty_role_rejected(self, client):
        resp = client.post("/admin/set_role", data={
            "target_id": 2, "role": ""
        }, cookies=ROOT)
        assert resp.status_code == 400

    def test_cannot_modify_root(self, client):
        with patch("app.routes.admin.get_db") as mock_db:
            mock_db.side_effect = self._db(True, None)
            resp = client.post("/admin/set_role", data={
                "target_id": 1, "role": "user"
            }, cookies=ROOT)
        assert resp.status_code == 302
        assert "Cannot" in resp.headers["location"]

    def test_cannot_assign_role_above_self(self, client):
        # scholarship_chair (level 2) cannot assign bca_scholarship (level 3)
        sc_cookies = auth_cookies(student_id=1, role="scholarship_chair")
        resp = client.post("/admin/set_role", data={
            "target_id": 2, "role": "bca_scholarship"
        }, cookies=sc_cookies)
        assert resp.status_code == 302
        assert "Cannot+assign" in resp.headers["location"]

    def test_cannot_modify_higher_role_user(self, client):
        # scholarship_chair cannot modify bca_scholarship user (target >= actor level)
        sc_cookies = auth_cookies(student_id=1, role="scholarship_chair")
        with patch("app.routes.admin.get_db") as mock_db:
            mock_db.side_effect = self._db(False, "bca_scholarship")
            resp = client.post("/admin/set_role", data={
                "target_id": 2, "role": "user"
            }, cookies=sc_cookies)
        assert resp.status_code == 302
        assert "Cannot+modify" in resp.headers["location"]

    def test_can_assign_own_role_level(self, client):
        # scholarship_chair can assign scholarship_chair (same level) to others
        sc_cookies = auth_cookies(student_id=1, role="scholarship_chair")
        with patch("app.routes.admin.get_db") as mock_db:
            mock_db.side_effect = self._db(False, None)
            resp = client.post("/admin/set_role", data={
                "target_id": 2, "role": "scholarship_chair"
            }, cookies=sc_cookies)
        assert resp.status_code == 302
        assert "message" not in resp.headers["location"]

    def test_coordinator_cannot_assign_coordinator(self, client):
        # study_session_coordinator cannot create more coordinators
        coord_cookies = auth_cookies(student_id=1, role="study_session_coordinator")
        resp = client.post("/admin/set_role", data={
            "target_id": 2, "role": "study_session_coordinator"
        }, cookies=coord_cookies)
        assert resp.status_code == 302
        assert "Coordinator" in resp.headers["location"]

    def test_scholarship_chair_can_assign_own_role(self, client):
        # scholarship_chair CAN assign scholarship_chair
        sc_cookies = auth_cookies(student_id=1, role="scholarship_chair")
        with patch("app.routes.admin.get_db") as mock_db:
            mock_db.side_effect = self._db(False, None)
            resp = client.post("/admin/set_role", data={
                "target_id": 2, "role": "scholarship_chair"
            }, cookies=sc_cookies)
        assert resp.status_code == 302
        assert "message" not in resp.headers["location"]

    def test_invalid_role_rejected(self, client):
        resp = client.post("/admin/set_role", data={
            "target_id": 2, "role": "superuser"
        }, cookies=ROOT)
        assert resp.status_code == 400


class TestEditUser:
    def test_edit_user_success(self, client):
        with patch("app.routes.admin.get_db") as mock_db, \
             patch("app.routes.admin.download_and_cache_avatar"):
            conn = FakeConnection()
            conn._cursor._results = [
                (False,),     # is_root check -> not root
                ("old_id",),  # old discord_id
            ]
            @contextmanager
            def db():
                yield conn
            mock_db.side_effect = db
            resp = client.post("/admin/api/edit_user", data={
                "target_id": 2, "first_name": "Alice", "last_name": "Jones",
                "discord_id": "999"
            }, cookies=ADMIN)
        assert resp.status_code == 200
        assert resp.json()["ok"] is True

    def test_edit_user_invalid_name(self, client):
        resp = client.post("/admin/api/edit_user", data={
            "target_id": 2, "first_name": "!!!!", "last_name": "User",
        }, cookies=ADMIN)
        assert resp.status_code == 400

    def test_edit_root_blocked(self, client):
        with patch("app.routes.admin.get_db") as mock_db:
            conn = FakeConnection()
            conn._cursor._results = [(True,)]  # is root
            @contextmanager
            def db():
                yield conn
            mock_db.side_effect = db
            resp = client.post("/admin/api/edit_user", data={
                "target_id": 1, "first_name": "Root", "last_name": "Admin",
            }, cookies=ADMIN)
        assert resp.status_code == 403


class TestAdminUserEnrollments:
    def test_get_user_enrollments(self, client):
        with patch("app.routes.admin.get_db") as mock_db:
            conn = FakeConnection()
            conn._cursor._fetchall_results = [
                [(10, "CSCI", "101", "Intro to CS")]
            ]
            @contextmanager
            def db():
                yield conn
            mock_db.side_effect = db
            resp = client.get("/admin/api/user/2/enrollments", cookies=ADMIN)
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 1
        assert data[0]["course_id"] == 10

    def test_add_user_enrollment(self, client):
        with patch("app.routes.admin.get_db") as mock_db:
            conn = FakeConnection()
            conn._cursor._results = [(2026, "spring")]  # current_term
            @contextmanager
            def db():
                yield conn
            mock_db.side_effect = db
            resp = client.post("/admin/api/user/2/enrollments",
                               data={"course_id": 10}, cookies=ADMIN)
        assert resp.status_code == 200
        assert resp.json()["ok"] is True

    def test_add_enrollment_no_term(self, client):
        with patch("app.routes.admin.get_db") as mock_db:
            conn = FakeConnection()
            conn._cursor._results = [None]  # no current term
            @contextmanager
            def db():
                yield conn
            mock_db.side_effect = db
            resp = client.post("/admin/api/user/2/enrollments",
                               data={"course_id": 10}, cookies=ADMIN)
        assert resp.status_code == 400

    def test_remove_user_enrollment(self, client):
        with patch("app.routes.admin.get_db") as mock_db:
            conn = FakeConnection()
            conn._cursor._results = [(2026, "spring")]  # current_term
            @contextmanager
            def db():
                yield conn
            mock_db.side_effect = db
            resp = client.delete("/admin/api/user/2/enrollments/10", cookies=ADMIN)
        assert resp.status_code == 200
        assert resp.json()["ok"] is True

    def test_unauthenticated_blocked(self, client):
        resp = client.get("/admin/api/user/2/enrollments")
        assert resp.status_code in (302, 403)


class TestAdminUserTutorCapabilities:
    def test_get_user_tutor_capabilities(self, client):
        with patch("app.routes.admin.get_db") as mock_db:
            conn = FakeConnection()
            conn._cursor._fetchall_results = [
                [(10, "CSCI", "101", "Intro to CS", 8)]
            ]
            @contextmanager
            def db():
                yield conn
            mock_db.side_effect = db
            resp = client.get("/admin/api/user/2/tutor_capabilities", cookies=ADMIN)
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 1
        assert data[0]["confidence"] == 8

    def test_add_user_tutor_capability(self, client):
        with patch("app.routes.admin.get_db") as mock_db:
            conn = FakeConnection()
            @contextmanager
            def db():
                yield conn
            mock_db.side_effect = db
            resp = client.post("/admin/api/user/2/tutor_capabilities",
                               data={"course_id": 10, "confidence": 7}, cookies=ADMIN)
        assert resp.status_code == 200
        assert resp.json()["ok"] is True

    def test_add_tutor_invalid_confidence(self, client):
        resp = client.post("/admin/api/user/2/tutor_capabilities",
                           data={"course_id": 10, "confidence": 15}, cookies=ADMIN)
        assert resp.status_code == 400

    def test_remove_user_tutor_capability(self, client):
        with patch("app.routes.admin.get_db") as mock_db:
            conn = FakeConnection()
            @contextmanager
            def db():
                yield conn
            mock_db.side_effect = db
            resp = client.delete("/admin/api/user/2/tutor_capabilities/10", cookies=ADMIN)
        assert resp.status_code == 200
        assert resp.json()["ok"] is True

    def test_unauthenticated_blocked(self, client):
        resp = client.get("/admin/api/user/2/tutor_capabilities")
        assert resp.status_code in (302, 403)


class TestDeleteUser:
    def test_delete_user_success(self, client):
        with patch("app.routes.admin.get_db_for_user") as mock_db, \
             patch("app.routes.admin.os.path.exists", return_value=False):
            conn = FakeConnection()
            conn._cursor._results = [(False,)]  # not root
            @contextmanager
            def db(user):
                yield conn
            mock_db.side_effect = db
            resp = client.post("/admin/delete_user",
                               data={"target_id": 2}, cookies=ADMIN)
        assert resp.status_code == 302
        assert "/admin/portal" in resp.headers["location"]

    def test_cannot_delete_self(self, client):
        resp = client.post("/admin/delete_user",
                           data={"target_id": 1}, cookies=ADMIN)
        assert resp.status_code == 302
        assert "Cannot" in resp.headers["location"]

    def test_cannot_delete_root(self, client):
        with patch("app.routes.admin.get_db_for_user") as mock_db:
            conn = FakeConnection()
            conn._cursor._results = [(True,)]  # is root
            @contextmanager
            def db(user):
                yield conn
            mock_db.side_effect = db
            resp = client.post("/admin/delete_user",
                               data={"target_id": 99}, cookies=ADMIN)
        assert resp.status_code == 302
        assert "root" in resp.headers["location"].lower()


class TestValidateDiscord:
    @patch("app.routes.admin.urllib.request.urlopen")
    def test_valid_discord_id(self, mock_urlopen, client):
        mock_resp = MagicMock()
        mock_resp.read.return_value = json.dumps({
            "avatar": "abc123", "global_name": "TestUser"
        }).encode()
        mock_resp.__enter__ = lambda s: s
        mock_resp.__exit__ = MagicMock(return_value=False)
        mock_urlopen.return_value = mock_resp

        resp = client.get("/admin/api/validate_discord/123456789", cookies=ADMIN)
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "valid"
        assert "avatar_url" in data

    def test_non_numeric_discord_id(self, client):
        resp = client.get("/admin/api/validate_discord/notanumber", cookies=ADMIN)
        assert resp.status_code == 200
        assert resp.json()["status"] == "invalid"

    @patch("app.routes.admin.DISCORD_BOT_TOKEN", "")
    def test_no_bot_token(self, client):
        resp = client.get("/admin/api/validate_discord/123456789", cookies=ADMIN)
        assert resp.status_code == 200
        assert resp.json()["status"] == "no_token"


class TestCreateUser:
    @patch("app.routes.admin.validate_discord_id", return_value=(True, ""))
    @patch("app.routes.admin.get_password_hash", return_value="hashed")
    @patch("app.routes.admin.download_and_cache_avatar")
    def test_create_user_success(self, mock_avatar, mock_hash, mock_validate, client):
        with patch("app.routes.admin.get_db_for_user") as mock_db:
            conn = FakeConnection()
            conn._cursor._results = [(99,)]  # returning student_id
            @contextmanager
            def db(user):
                yield conn
            mock_db.side_effect = db
            resp = client.post("/admin/create_user", data={
                "first_name": "New", "last_name": "Student", "discord_id": "123456"
            }, cookies=ADMIN)
        assert resp.status_code == 302
        assert "User+created" in resp.headers["location"]

    def test_invalid_first_name(self, client):
        resp = client.post("/admin/create_user", data={
            "first_name": "123", "last_name": "User", "discord_id": "123456"
        }, cookies=ADMIN)
        assert resp.status_code == 302
        assert "invalid" in resp.headers["location"].lower()

    def test_non_numeric_discord(self, client):
        resp = client.post("/admin/create_user", data={
            "first_name": "Test", "last_name": "User", "discord_id": "not_a_number"
        }, cookies=ADMIN)
        assert resp.status_code == 302
        assert "numeric" in resp.headers["location"].lower()

    @patch("app.routes.admin.validate_discord_id", return_value=(False, "Discord user not found"))
    def test_invalid_discord_user(self, mock_validate, client):
        resp = client.post("/admin/create_user", data={
            "first_name": "Test", "last_name": "User", "discord_id": "999999"
        }, cookies=ADMIN)
        assert resp.status_code == 302
        assert "not+found" in resp.headers["location"].lower() or "not%20found" in resp.headers["location"].lower()


class TestPendingAssessments:
    def test_get_pending(self, client):
        with patch("app.routes.admin.get_db") as mock_db:
            conn = FakeConnection()
            conn._cursor._fetchall_results = [
                [(1, "CSCI", "101", "Intro to CS", "2026-03-15", "in_class", False, False, "Alice", "Smith")],
                [],  # needs_session query
            ]
            @contextmanager
            def db():
                yield conn
            mock_db.side_effect = db
            resp = client.get("/admin/api/pending_assessments", cookies=ADMIN)
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 1
        assert data[0]["review_type"] == "pending"

    def test_get_pending_disputed(self, client):
        with patch("app.routes.admin.get_db") as mock_db:
            conn = FakeConnection()
            conn._cursor._fetchall_results = [
                [(2, "MATH", "201", "Calc II", "2026-05-10", "final", True, True, "Bob", "Jones")],
                [],  # needs_session query
            ]
            @contextmanager
            def db():
                yield conn
            mock_db.side_effect = db
            resp = client.get("/admin/api/pending_assessments", cookies=ADMIN)
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 1
        assert data[0]["review_type"] == "disputed"

    def test_get_pending_includes_needs_session(self, client):
        from datetime import date
        with patch("app.routes.admin.get_db") as mock_db:
            conn = FakeConnection()
            conn._cursor._results = [
                ([10],),  # linked_course_ids for dedup
            ]
            conn._cursor._fetchall_results = [
                [],  # no pending/disputed
                [(5, "CSCI", "200", "OOP", date(2026, 3, 1), "final", 10)],
            ]
            @contextmanager
            def db():
                yield conn
            mock_db.side_effect = db
            resp = client.get("/admin/api/pending_assessments", cookies=ADMIN)
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 1
        assert data[0]["review_type"] == "needs_session"
        assert data[0]["department"] == "CSCI"

    def test_needs_session_dedupes_strong_links(self, client):
        """Two strongly linked courses with exams on the same day = one todo."""
        from datetime import date
        with patch("app.routes.admin.get_db") as mock_db:
            conn = FakeConnection()
            conn._cursor._results = [
                ([10, 20],),  # linked_course_ids for course 10 (dedup)
                ([10, 20],),  # linked_course_ids for course 20 (dedup, skipped)
            ]
            conn._cursor._fetchall_results = [
                [],  # no pending/disputed
                [
                    (5, "MATH", "213", "Calc III", date(2026, 3, 5), "final", 10),
                    (6, "MATH", "223", "Calc III Honors", date(2026, 3, 5), "final", 20),
                ],
            ]
            @contextmanager
            def db():
                yield conn
            mock_db.side_effect = db
            resp = client.get("/admin/api/pending_assessments", cookies=ADMIN)
        assert resp.status_code == 200
        data = resp.json()
        needs = [d for d in data if d["review_type"] == "needs_session"]
        assert len(needs) == 1
        assert needs[0]["department"] == "MATH"
        assert needs[0]["identifier"] == "213"
        assert "MATH223" in needs[0]["also_covers"]

    def test_confirm_pending_report(self, client):
        with patch("app.routes.admin.get_db") as mock_db:
            conn = FakeConnection()
            conn._cursor._results = [(False, False)]  # unconfirmed, not disputed
            @contextmanager
            def db():
                yield conn
            mock_db.side_effect = db
            resp = client.post("/admin/api/confirm_assessment",
                               data={"exam_id": 1}, cookies=ADMIN)
        assert resp.status_code == 200
        assert resp.json()["ok"] is True

    def test_confirm_disputed_final(self, client):
        with patch("app.routes.admin.get_db") as mock_db:
            conn = FakeConnection()
            conn._cursor._results = [(True, True)]  # confirmed but disputed
            @contextmanager
            def db():
                yield conn
            mock_db.side_effect = db
            resp = client.post("/admin/api/confirm_assessment",
                               data={"exam_id": 2}, cookies=ADMIN)
        assert resp.status_code == 200
        assert resp.json()["ok"] is True

    def test_revert_disputed(self, client):
        with patch("app.routes.admin.get_db") as mock_db:
            conn = FakeConnection()
            conn._cursor._results = [(True, True)]  # disputed
            @contextmanager
            def db():
                yield conn
            mock_db.side_effect = db
            resp = client.post("/admin/api/revert_assessment",
                               data={"exam_id": 2}, cookies=ADMIN)
        assert resp.status_code == 200
        assert resp.json()["ok"] is True

    def test_revert_pending(self, client):
        with patch("app.routes.admin.get_db") as mock_db:
            conn = FakeConnection()
            conn._cursor._results = [(False, False)]  # unconfirmed
            @contextmanager
            def db():
                yield conn
            mock_db.side_effect = db
            resp = client.post("/admin/api/revert_assessment",
                               data={"exam_id": 1}, cookies=ADMIN)
        assert resp.status_code == 200
        assert resp.json()["ok"] is True

    def test_delete_pending(self, client):
        resp = client.delete("/admin/api/pending_assessment/1", cookies=ADMIN)
        assert resp.status_code == 200
        assert resp.json()["ok"] is True

    def test_delete_exam(self, client):
        with patch("app.routes.admin.get_db") as mock_db:
            conn = FakeConnection()
            @contextmanager
            def db():
                yield conn
            mock_db.side_effect = db
            resp = client.delete("/admin/api/exam/99", cookies=ADMIN)
        assert resp.status_code == 200
        assert resp.json()["ok"] is True
        sql = conn._cursor._executed[-1][0]
        assert "UPDATE exams SET deleted = TRUE" in sql

    def test_delete_exam_non_admin_blocked(self, client):
        resp = client.delete("/admin/api/exam/99",
                             cookies=auth_cookies(is_admin=False))
        assert resp.status_code == 403

    def test_non_admin_blocked(self, client):
        resp = client.get("/admin/api/pending_assessments",
                          cookies=auth_cookies(is_admin=False))
        assert resp.status_code == 403


class TestRestoreExams:
    """Tests for deleted exams listing and restoration."""

    def test_get_deleted_exams(self, client):
        from datetime import date
        with patch("app.routes.admin.get_db") as mock_db:
            conn = FakeConnection()
            # fetchone: current_term query
            conn._cursor._results = [(2026, "spring")]
            # fetchall: deleted exams SELECT (9 columns: id, dept, ident, title, date, type, deleted, skipped, disputed)
            conn._cursor._fetchall_results = [
                [(1, "CSCI", "101", "Intro to CS", date(2026, 3, 15), "final", True, False, False)]
            ]
            @contextmanager
            def db():
                yield conn
            mock_db.side_effect = db
            resp = client.get("/admin/api/deleted_exams", cookies=ADMIN)
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 1
        assert data[0]["department"] == "CSCI"
        assert data[0]["exam_type"] == "final"

    def test_restore_exam(self, client):
        with patch("app.routes.admin.get_db") as mock_db:
            conn = FakeConnection()
            @contextmanager
            def db():
                yield conn
            mock_db.side_effect = db
            resp = client.post("/admin/api/restore_exam",
                               data={"exam_id": "42"}, cookies=ADMIN)
        assert resp.status_code == 200
        assert resp.json()["ok"] is True
        sql = conn._cursor._executed[-1][0]
        assert "deleted = FALSE" in sql

    def test_restore_exam_non_admin_blocked(self, client):
        resp = client.post("/admin/api/restore_exam",
                           data={"exam_id": "42"},
                           cookies=auth_cookies(is_admin=False))
        assert resp.status_code == 403


class TestNoTutorReview:
    """Tests for no-tutor-needed report review."""

    def test_get_no_tutor_pending(self, client):
        with patch("app.routes.admin.get_db") as mock_db:
            conn = FakeConnection()
            conn._cursor._fetchall_results = [
                [(10, "PHGN", "100", "Physics 1")]
            ]
            @contextmanager
            def db():
                yield conn
            mock_db.side_effect = db
            resp = client.get("/admin/api/no_tutor_pending", cookies=ADMIN)
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 1
        assert data[0]["department"] == "PHGN"
        assert data[0]["course_id"] == 10

    def test_get_no_tutor_pending_empty(self, client):
        with patch("app.routes.admin.get_db") as mock_db:
            conn = FakeConnection()
            conn._cursor._fetchall_results = [[]]
            @contextmanager
            def db():
                yield conn
            mock_db.side_effect = db
            resp = client.get("/admin/api/no_tutor_pending", cookies=ADMIN)
        assert resp.status_code == 200
        assert resp.json() == []

    def test_approve_no_tutor(self, client):
        with patch("app.routes.admin.get_db") as mock_db:
            conn = FakeConnection()
            @contextmanager
            def db():
                yield conn
            mock_db.side_effect = db
            resp = client.post("/admin/api/approve_no_tutor",
                               data={"course_id": "10"}, cookies=ADMIN)
        assert resp.status_code == 200
        assert resp.json()["ok"] is True
        sql = conn._cursor._executed[-1][0]
        assert "no_tutor_needed = TRUE" in sql
        assert "no_tutor_pending = FALSE" in sql

    def test_reject_no_tutor(self, client):
        with patch("app.routes.admin.get_db") as mock_db:
            conn = FakeConnection()
            @contextmanager
            def db():
                yield conn
            mock_db.side_effect = db
            resp = client.post("/admin/api/reject_no_tutor",
                               data={"course_id": "10"}, cookies=ADMIN)
        assert resp.status_code == 200
        assert resp.json()["ok"] is True
        sql = conn._cursor._executed[-1][0]
        assert "no_tutor_pending = FALSE" in sql
        assert "no_tutor_needed" not in sql or "no_tutor_needed = TRUE" not in sql

    def test_toggle_no_tutor(self, client):
        with patch("app.routes.admin.get_db") as mock_db:
            conn = FakeConnection()
            @contextmanager
            def db():
                yield conn
            mock_db.side_effect = db
            resp = client.post("/admin/api/toggle_no_tutor",
                               data={"course_id": "10"}, cookies=ADMIN)
        assert resp.status_code == 200
        assert resp.json()["ok"] is True
        sql = conn._cursor._executed[-1][0]
        assert "NOT no_tutor_needed" in sql

    def test_no_tutor_non_admin_blocked(self, client):
        user = auth_cookies(is_admin=False)
        resp = client.get("/admin/api/no_tutor_pending", cookies=user)
        assert resp.status_code == 403
        resp = client.post("/admin/api/approve_no_tutor",
                           data={"course_id": "10"}, cookies=user)
        assert resp.status_code == 403


class TestStudySessions:
    """Tests for study session scheduling endpoints."""

    def test_get_scheduling_details(self, client):
        from datetime import date
        with patch("app.routes.admin.get_db") as mock_db:
            conn = FakeConnection()
            conn._cursor._results = [
                (1, 10, date(2026, 3, 5), "final", "CSCI", "200", "OOP"),  # exam info
                ([10],),  # linked_course_ids (strong)
                ([10],),  # linked_course_ids_any (all)
                None,  # no existing session
                (2026, "spring"),  # current_term
            ]
            conn._cursor._fetchall_results = [
                [(5, "John", "Smith", 8, "CSCI", "200", "discord_tutor_5")],  # tutors (with course info + discord)
                [(2, "Alice", "Jones", "123456"), (3, "Bob", "Brown", None)],  # students
            ]
            @contextmanager
            def db():
                yield conn
            mock_db.side_effect = db
            resp = client.get("/admin/api/exam/1/scheduling_details", cookies=ADMIN)
        assert resp.status_code == 200
        data = resp.json()
        assert data["exam"]["department"] == "CSCI"
        assert data["has_session"] is False
        assert len(data["tutors"]) == 1
        assert data["tutors"][0]["confidence"] == 8
        assert data["tutors"][0]["discord_id"] == "discord_tutor_5"
        assert len(data["students"]) == 2

    def test_get_scheduling_details_not_found(self, client):
        with patch("app.routes.admin.get_db") as mock_db:
            conn = FakeConnection()
            conn._cursor._results = [None]
            @contextmanager
            def db():
                yield conn
            mock_db.side_effect = db
            resp = client.get("/admin/api/exam/999/scheduling_details", cookies=ADMIN)
        assert resp.status_code == 404

    def test_get_scheduling_details_has_session(self, client):
        from datetime import date
        with patch("app.routes.admin.get_db") as mock_db:
            conn = FakeConnection()
            conn._cursor._results = [
                (1, 10, date(2026, 3, 5), "final", "CSCI", "200", "OOP"),
                ([10],),  # linked_course_ids (strong)
                ([10],),  # linked_course_ids_any (all)
                (42,),  # existing session
                (2026, "spring"),
            ]
            conn._cursor._fetchall_results = [
                [(5, "John", "Smith", 8, "CSCI", "200", None)],
                [],
            ]
            @contextmanager
            def db():
                yield conn
            mock_db.side_effect = db
            resp = client.get("/admin/api/exam/1/scheduling_details", cookies=ADMIN)
        assert resp.status_code == 200
        assert resp.json()["has_session"] is True

    def test_create_study_session(self, client):
        with patch("app.routes.admin.get_db") as mock_db:
            conn = FakeConnection()
            conn._cursor._results = [
                (1,),   # exam exists
                (True,),  # tutor valid
                (42,),  # returning session_id
            ]
            @contextmanager
            def db():
                yield conn
            mock_db.side_effect = db
            resp = client.post("/admin/api/study_sessions", data={
                "exam_id": "1", "tutor_student_id": "5",
                "session_timestamp": "2026-03-04T15:00",
                "location": "Study Room"
            }, cookies=ADMIN)
        assert resp.status_code == 200
        data = resp.json()
        assert data["ok"] is True
        assert data["session_id"] == 42

    def test_create_study_session_no_tutor(self, client):
        with patch("app.routes.admin.get_db") as mock_db:
            conn = FakeConnection()
            conn._cursor._results = [
                (1,),   # exam exists
                (43,),  # returning session_id
            ]
            @contextmanager
            def db():
                yield conn
            mock_db.side_effect = db
            resp = client.post("/admin/api/study_sessions", data={
                "exam_id": "1",
                "session_timestamp": "2026-03-04T15:00",
                "location": "Study Room"
            }, cookies=ADMIN)
        assert resp.status_code == 200
        assert resp.json()["session_id"] == 43

    def test_create_session_exam_not_found(self, client):
        with patch("app.routes.admin.get_db") as mock_db:
            conn = FakeConnection()
            conn._cursor._results = [None]
            @contextmanager
            def db():
                yield conn
            mock_db.side_effect = db
            resp = client.post("/admin/api/study_sessions", data={
                "exam_id": "999", "tutor_student_id": "5",
                "session_timestamp": "2026-03-04T15:00",
            }, cookies=ADMIN)
        assert resp.status_code == 404

    def test_create_session_invalid_tutor(self, client):
        with patch("app.routes.admin.get_db") as mock_db:
            conn = FakeConnection()
            conn._cursor._results = [
                (1,),   # exam exists
                None,   # tutor NOT valid
            ]
            @contextmanager
            def db():
                yield conn
            mock_db.side_effect = db
            resp = client.post("/admin/api/study_sessions", data={
                "exam_id": "1", "tutor_student_id": "99",
                "session_timestamp": "2026-03-04T15:00",
            }, cookies=ADMIN)
        assert resp.status_code == 400

    def test_list_study_sessions(self, client):
        from datetime import datetime as dt, date, timezone
        with patch("app.routes.admin.get_db") as mock_db:
            conn = FakeConnection()
            conn._cursor._results = [(2026, "spring")]
            conn._cursor._fetchall_results = [
                [(1, dt(2026, 3, 4, 15, 0, tzinfo=timezone.utc), "Study Room",
                  10, date(2026, 3, 5), "final", 20,
                  "CSCI", "200", "OOP",
                  "John", "Smith", 5, "tutor_discord_999")],
                [(2, "Alice", "Jones", "123456")],
            ]
            @contextmanager
            def db():
                yield conn
            mock_db.side_effect = db
            resp = client.get("/admin/api/study_sessions", cookies=ADMIN)
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 1
        assert data[0]["department"] == "CSCI"
        assert data[0]["tutor_first"] == "John"
        assert data[0]["location"] == "Study Room"
        assert len(data[0]["students"]) == 1

    def test_list_study_sessions_has_tutor_discord(self, client):
        from datetime import datetime as dt, date, timezone
        with patch("app.routes.admin.get_db") as mock_db:
            conn = FakeConnection()
            conn._cursor._results = [(2026, "spring")]
            conn._cursor._fetchall_results = [
                [(1, dt(2026, 3, 4, 15, 0, tzinfo=timezone.utc), "Study Room",
                  10, date(2026, 3, 5), "final", 20,
                  "CSCI", "200", "OOP",
                  "John", "Smith", 5, "tutor_discord_999")],
                [(2, "Alice", "Jones", "123456")],
            ]
            @contextmanager
            def db():
                yield conn
            mock_db.side_effect = db
            resp = client.get("/admin/api/study_sessions", cookies=ADMIN)
        data = resp.json()
        assert data[0]["tutor_discord_id"] == "tutor_discord_999"

    def test_list_study_sessions_empty(self, client):
        with patch("app.routes.admin.get_db") as mock_db:
            conn = FakeConnection()
            conn._cursor._results = [None]  # no current term
            @contextmanager
            def db():
                yield conn
            mock_db.side_effect = db
            resp = client.get("/admin/api/study_sessions", cookies=ADMIN)
        assert resp.status_code == 200
        assert resp.json() == []

    def test_delete_study_session(self, client):
        with patch("app.routes.admin.get_db") as mock_db:
            conn = FakeConnection()
            @contextmanager
            def db():
                yield conn
            mock_db.side_effect = db
            resp = client.delete("/admin/api/study_sessions/1", cookies=ADMIN)
        assert resp.status_code == 200
        assert resp.json()["ok"] is True
        sql = conn._cursor._executed[-1][0]
        assert "DELETE FROM study_sessions" in sql

    def test_update_study_session(self, client):
        with patch("app.routes.admin.get_db") as mock_db:
            conn = FakeConnection()
            conn._cursor._results = [
                (10,),   # session exists, exam_id=10
                (True,),  # tutor valid
            ]
            @contextmanager
            def db():
                yield conn
            mock_db.side_effect = db
            resp = client.put("/admin/api/study_sessions/1", data={
                "tutor_student_id": "5",
                "session_timestamp": "2026-03-04T16:00",
                "location": "Library"
            }, cookies=ADMIN)
        assert resp.status_code == 200
        assert resp.json()["ok"] is True
        sql = conn._cursor._executed[-1][0]
        assert "UPDATE study_sessions" in sql

    def test_update_study_session_not_found(self, client):
        with patch("app.routes.admin.get_db") as mock_db:
            conn = FakeConnection()
            conn._cursor._results = [None]  # session not found
            @contextmanager
            def db():
                yield conn
            mock_db.side_effect = db
            resp = client.put("/admin/api/study_sessions/999", data={
                "session_timestamp": "2026-03-04T16:00",
            }, cookies=ADMIN)
        assert resp.status_code == 404

    def test_update_study_session_invalid_tutor(self, client):
        with patch("app.routes.admin.get_db") as mock_db:
            conn = FakeConnection()
            conn._cursor._results = [
                (10,),   # session exists
                None,    # tutor NOT valid
            ]
            @contextmanager
            def db():
                yield conn
            mock_db.side_effect = db
            resp = client.put("/admin/api/study_sessions/1", data={
                "tutor_student_id": "99",
                "session_timestamp": "2026-03-04T16:00",
            }, cookies=ADMIN)
        assert resp.status_code == 400

    def test_study_session_non_admin_blocked(self, client):
        resp = client.get("/admin/api/study_sessions",
                          cookies=auth_cookies(is_admin=False))
        assert resp.status_code == 403


class TestNoTutorApproved:
    def test_get_no_tutor_approved(self, client):
        with patch("app.routes.admin.get_db") as mock_db:
            conn = FakeConnection()
            conn._cursor._fetchall_results = [
                [(1, "MATH", "111", "Calculus I")]
            ]
            @contextmanager
            def db():
                yield conn
            mock_db.side_effect = db
            resp = client.get("/admin/api/no_tutor_approved", cookies=ADMIN)
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 1
        assert data[0]["department"] == "MATH"
        assert data[0]["course_id"] == 1

    def test_get_no_tutor_approved_empty(self, client):
        with patch("app.routes.admin.get_db") as mock_db:
            conn = FakeConnection()
            conn._cursor._fetchall_results = [[]]
            @contextmanager
            def db():
                yield conn
            mock_db.side_effect = db
            resp = client.get("/admin/api/no_tutor_approved", cookies=ADMIN)
        assert resp.status_code == 200
        assert resp.json() == []

    def test_no_tutor_approved_non_admin_blocked(self, client):
        resp = client.get("/admin/api/no_tutor_approved",
                          cookies=auth_cookies(is_admin=False))
        assert resp.status_code == 403

    def test_deleted_exams_include_disputed(self, client):
        from datetime import date
        with patch("app.routes.admin.get_db") as mock_db:
            conn = FakeConnection()
            conn._cursor._results = [(2026, "spring")]
            conn._cursor._fetchall_results = [
                [(1, "MATH", "201", "Calc 2", date(2026, 4, 10), "final", True, False, True)]
            ]
            @contextmanager
            def db():
                yield conn
            mock_db.side_effect = db
            resp = client.get("/admin/api/deleted_exams", cookies=ADMIN)
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 1
        assert data[0]["is_disputed"] is True
        assert data[0]["is_deleted"] is True


class TestCourseLinks:
    def test_get_links_empty(self, client):
        with patch("app.routes.admin.get_db") as mock_db:
            conn = FakeConnection()
            conn._cursor._fetchall_results = [[]]
            @contextmanager
            def db():
                yield conn
            mock_db.side_effect = db
            resp = client.get("/admin/api/course_links", cookies=ADMIN)
        assert resp.status_code == 200
        assert resp.json() == []

    def test_get_links_pairs(self, client):
        with patch("app.routes.admin.get_db") as mock_db:
            conn = FakeConnection()
            conn._cursor._fetchall_results = [
                [(1, 2, "strong", "MATH", "213", "Calc III", "MATH", "223", "Calc III Honors")],
            ]
            @contextmanager
            def db():
                yield conn
            mock_db.side_effect = db
            resp = client.get("/admin/api/course_links", cookies=ADMIN)
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 1
        assert data[0]["course_id_a"] == 1
        assert data[0]["course_id_b"] == 2
        assert data[0]["link_type"] == "strong"

    def test_create_link(self, client):
        with patch("app.routes.admin.get_db") as mock_db:
            conn = FakeConnection()
            conn._cursor._fetchall_results = [[(1,), (2,)]]  # both courses found
            @contextmanager
            def db():
                yield conn
            mock_db.side_effect = db
            resp = client.post("/admin/api/course_links",
                               data={"course_id_a": "1", "course_id_b": "2"},
                               cookies=ADMIN)
        assert resp.status_code == 200
        assert resp.json()["ok"] is True
        sql, params = conn._cursor._executed[-1]
        assert "INSERT INTO course_links" in sql
        assert params == (1, 2, "strong")  # default link_type

    def test_create_link_weak(self, client):
        with patch("app.routes.admin.get_db") as mock_db:
            conn = FakeConnection()
            conn._cursor._fetchall_results = [[(1,), (2,)]]
            @contextmanager
            def db():
                yield conn
            mock_db.side_effect = db
            resp = client.post("/admin/api/course_links",
                               data={"course_id_a": "1", "course_id_b": "2", "link_type": "weak"},
                               cookies=ADMIN)
        assert resp.status_code == 200
        sql, params = conn._cursor._executed[-1]
        assert params == (1, 2, "weak")

    def test_create_link_invalid_type(self, client):
        resp = client.post("/admin/api/course_links",
                           data={"course_id_a": "1", "course_id_b": "2", "link_type": "invalid"},
                           cookies=ADMIN)
        assert resp.status_code == 400

    def test_create_link_reverse_order(self, client):
        """Posting (5, 3) should insert as (3, 5)."""
        with patch("app.routes.admin.get_db") as mock_db:
            conn = FakeConnection()
            conn._cursor._fetchall_results = [[(3,), (5,)]]
            @contextmanager
            def db():
                yield conn
            mock_db.side_effect = db
            resp = client.post("/admin/api/course_links",
                               data={"course_id_a": "5", "course_id_b": "3"},
                               cookies=ADMIN)
        assert resp.status_code == 200
        sql, params = conn._cursor._executed[-1]
        assert params == (3, 5, "strong")

    def test_create_link_same_course(self, client):
        resp = client.post("/admin/api/course_links",
                           data={"course_id_a": "1", "course_id_b": "1"},
                           cookies=ADMIN)
        assert resp.status_code == 400

    def test_create_link_not_found(self, client):
        with patch("app.routes.admin.get_db") as mock_db:
            conn = FakeConnection()
            conn._cursor._fetchall_results = [[(1,)]]  # only one found
            @contextmanager
            def db():
                yield conn
            mock_db.side_effect = db
            resp = client.post("/admin/api/course_links",
                               data={"course_id_a": "1", "course_id_b": "999"},
                               cookies=ADMIN)
        assert resp.status_code == 404

    def test_delete_link(self, client):
        with patch("app.routes.admin.get_db") as mock_db:
            conn = FakeConnection()
            @contextmanager
            def db():
                yield conn
            mock_db.side_effect = db
            resp = client.delete("/admin/api/course_links/1/2", cookies=ADMIN)
        assert resp.status_code == 200
        assert resp.json()["ok"] is True
        sql, params = conn._cursor._executed[-1]
        assert "DELETE FROM course_links" in sql
        assert params == (1, 2)

    def test_delete_link_reverse_order(self, client):
        """DELETE /5/3 should normalize to (3, 5)."""
        with patch("app.routes.admin.get_db") as mock_db:
            conn = FakeConnection()
            @contextmanager
            def db():
                yield conn
            mock_db.side_effect = db
            resp = client.delete("/admin/api/course_links/5/3", cookies=ADMIN)
        assert resp.status_code == 200
        sql, params = conn._cursor._executed[-1]
        assert params == (3, 5)

    def test_non_admin_blocked(self, client):
        resp = client.get("/admin/api/course_links",
                          cookies=auth_cookies(is_admin=False))
        assert resp.status_code == 403
        resp = client.post("/admin/api/course_links",
                           data={"course_id_a": "1", "course_id_b": "2"},
                           cookies=auth_cookies(is_admin=False))
        assert resp.status_code == 403

    def test_suggestions(self, client):
        with patch("app.routes.admin.get_db") as mock_db:
            conn = FakeConnection()
            conn._cursor._results = [(2026, "spring")]
            conn._cursor._fetchall_results = [
                [
                    (1, "MATH", "213", "Calculus for Scientists and Engineers III"),
                    (2, "MATH", "223", "Calculus III for Scientists and Engineers (Honors)"),
                    (3, "CSCI", "101", "Intro to CS"),
                ],
                [],  # no existing links
            ]
            @contextmanager
            def db():
                yield conn
            mock_db.side_effect = db
            resp = client.get("/admin/api/course_link_suggestions", cookies=ADMIN)
        assert resp.status_code == 200
        data = resp.json()
        # MATH213 and MATH223 should be suggested (similar titles)
        assert len(data) >= 1
        pair = data[0]
        depts = {pair["a"]["department"], pair["b"]["department"]}
        assert "MATH" in depts
        assert pair["similarity"] >= 0.7
        assert pair["link_type"] in ("strong", "weak")

    def test_scheduling_includes_linked_tutors(self, client):
        """When courses are linked, tutors from linked courses appear."""
        from datetime import date
        with patch("app.routes.admin.get_db") as mock_db:
            conn = FakeConnection()
            conn._cursor._results = [
                (1, 10, date(2026, 3, 5), "final", "MATH", "213", "Calc III"),  # exam
                ([10, 20],),  # linked_course_ids (strong)
                ([10, 20],),  # linked_course_ids_any (all)
                None,  # no existing session
                (2026, "spring"),  # current_term
            ]
            conn._cursor._fetchall_results = [
                [
                    (5, "John", "Smith", 8, "MATH", "213", "discord_5"),   # tutor from own course
                    (6, "Jane", "Doe", 7, "MATH", "223", "discord_6"),     # tutor from linked course
                ],
                [
                    (2, "Alice", "Jones", "111"),
                    (3, "Bob", "Brown", None),
                ],
                # linked_courses query
                [
                    (20, "MATH", "223", "Calc III Honors", "strong"),
                ],
            ]
            @contextmanager
            def db():
                yield conn
            mock_db.side_effect = db
            resp = client.get("/admin/api/exam/1/scheduling_details", cookies=ADMIN)
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["tutors"]) == 2
        assert data["tutors"][0]["from_course"] is None
        assert data["tutors"][1]["from_course"] == "MATH223"
        assert len(data["linked_courses"]) == 1
        assert data["linked_courses"][0]["link_type"] == "strong"

    def test_create_session_linked_tutor(self, client):
        """A tutor from a linked course should pass validation."""
        with patch("app.routes.admin.get_db") as mock_db:
            conn = FakeConnection()
            conn._cursor._results = [
                (1,),     # exam exists
                (True,),  # tutor valid (via linked_course_ids_any)
                (42,),    # returning session_id
            ]
            @contextmanager
            def db():
                yield conn
            mock_db.side_effect = db
            resp = client.post("/admin/api/study_sessions", data={
                "exam_id": "1", "tutor_student_id": "6",
                "session_timestamp": "2026-03-04T15:00",
                "location": "Study Room"
            }, cookies=ADMIN)
        assert resp.status_code == 200
        assert resp.json()["ok"] is True
