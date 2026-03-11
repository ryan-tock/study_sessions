"""Tests for user routes: portal pages + enrollment/tutor APIs."""
from contextlib import contextmanager
from unittest.mock import patch
from tests.conftest import auth_cookies, FakeConnection


# ---------------------------------------------------------------------------
# Portal & page routes
# ---------------------------------------------------------------------------

class TestUserPortal:
    def test_unauthenticated_redirects_to_login(self, client):
        resp = client.get("/user/portal")
        assert resp.status_code == 302
        assert resp.headers["location"] == "/"

    def test_root_user_redirected_to_admin(self, client):
        with patch("app.routes.user.get_user_profile", return_value={
            "first_name": "Root", "last_name": "", "discord_id": None, "sharing": "closed"
        }):
            resp = client.get("/user/portal", cookies=auth_cookies(is_root=True, is_admin=True))
        assert resp.status_code == 302
        assert "/admin/portal" in resp.headers["location"]

    def test_normal_user_sees_portal(self, client):
        with patch("app.routes.user.get_user_profile", return_value={
            "first_name": "Test", "last_name": "User", "discord_id": None, "sharing": "open"
        }):
            resp = client.get("/user/portal", cookies=auth_cookies(student_id=2))
        assert resp.status_code == 200
        assert "User Portal" in resp.text


class TestSetPassword:
    def test_get_set_password_page(self, client):
        resp = client.get("/user/set_password", cookies=auth_cookies(is_first_login=True))
        assert resp.status_code == 200

    @patch("app.routes.user.get_password_hash", return_value="hashed")
    @patch("app.routes.user.create_access_token", return_value="newtoken")
    def test_post_set_password(self, mock_token, mock_hash, client):
        resp = client.post("/user/set_password", data={
            "new_password": "NewPassword123!",
            "sharing": "open",
        }, cookies=auth_cookies(is_first_login=True))
        assert resp.status_code == 302
        assert "/user/privacy" in resp.headers["location"]

    def test_root_user_blocked(self, client):
        resp = client.get("/user/set_password", cookies=auth_cookies(is_root=True, is_admin=True))
        assert resp.status_code == 302
        assert "/admin/portal" in resp.headers["location"]


class TestChangePassword:
    def test_get_change_password_page(self, client):
        with patch("app.routes.user.get_user_profile", return_value={
            "first_name": "Test", "last_name": "User", "discord_id": None, "sharing": "open"
        }):
            resp = client.get("/user/change_password", cookies=auth_cookies())
        assert resp.status_code == 200

    @patch("app.routes.user.verify_password", return_value=False)
    def test_wrong_old_password(self, mock_verify, client):
        with patch("app.routes.user.get_user_profile", return_value={
            "first_name": "Test", "last_name": "User", "discord_id": None, "sharing": "open"
        }), patch("app.routes.user.get_db") as mock_db:
            conn = FakeConnection()
            conn._cursor._results = [("oldhash",)]
            @contextmanager
            def db():
                yield conn
            mock_db.side_effect = db
            resp = client.post("/user/change_password", data={
                "old_password": "wrong",
                "new_password": "NewPassword123!",
                "confirm_password": "NewPassword123!",
            }, cookies=auth_cookies())
        assert resp.status_code == 200
        assert "incorrect" in resp.text.lower()

    def test_root_blocked(self, client):
        resp = client.get("/user/change_password", cookies=auth_cookies(is_root=True, is_admin=True))
        assert resp.status_code == 302


class TestPrivacy:
    def test_get_privacy_page(self, client):
        with patch("app.routes.user.get_user_profile", return_value={
            "first_name": "Test", "last_name": "User", "discord_id": None, "sharing": "open"
        }):
            resp = client.get("/user/privacy", cookies=auth_cookies())
        assert resp.status_code == 200

    def test_post_valid_sharing(self, client):
        resp = client.post("/user/privacy", data={"sharing": "open"}, cookies=auth_cookies())
        assert resp.status_code == 302
        assert "message=" in resp.headers["location"]

    def test_post_invalid_sharing(self, client):
        resp = client.post("/user/privacy", data={"sharing": "invalid"}, cookies=auth_cookies())
        assert resp.status_code == 400

    def test_root_blocked(self, client):
        resp = client.post("/user/privacy", data={"sharing": "open"},
                           cookies=auth_cookies(is_root=True, is_admin=True))
        assert resp.status_code == 302


# ---------------------------------------------------------------------------
# Enrollment APIs
# ---------------------------------------------------------------------------

class TestEnrollments:
    def test_get_enrollments(self, client):
        with patch("app.routes.user.get_db_for_user") as mock_db:
            conn = FakeConnection()
            conn._cursor._fetchall_results = [
                [(1, "CSCI", "101", "Intro to CS")]
            ]
            @contextmanager
            def db(user):
                yield conn
            mock_db.side_effect = db
            resp = client.get("/api/my/enrollments", cookies=auth_cookies())

        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 1
        assert data[0]["department"] == "CSCI"

    def test_add_enrollment(self, client):
        with patch("app.routes.user.get_db") as mock_admin_db:
            conn = FakeConnection()
            conn._cursor._results = [(2026, "spring")]
            @contextmanager
            def db():
                yield conn
            mock_admin_db.side_effect = db
            resp = client.post("/api/my/enrollments", data={"course_id": 1},
                               cookies=auth_cookies())

        assert resp.status_code == 200
        assert resp.json()["ok"] is True

    def test_add_enrollment_no_term(self, client):
        with patch("app.routes.user.get_db") as mock_db:
            conn = FakeConnection()
            conn._cursor._results = [None]  # no term
            @contextmanager
            def db():
                yield conn
            mock_db.side_effect = db
            resp = client.post("/api/my/enrollments", data={"course_id": 1},
                               cookies=auth_cookies())
        assert resp.status_code == 400

    def test_delete_enrollment(self, client):
        resp = client.delete("/api/my/enrollments/1", cookies=auth_cookies())
        assert resp.status_code == 200
        assert resp.json()["ok"] is True

    def test_unauthenticated_blocked(self, client):
        resp = client.get("/api/my/enrollments")
        assert resp.status_code == 302


# ---------------------------------------------------------------------------
# Tutor Capability APIs
# ---------------------------------------------------------------------------

class TestTutorCapabilities:
    def test_get_capabilities(self, client):
        with patch("app.routes.user.get_db_for_user") as mock_db:
            conn = FakeConnection()
            conn._cursor._fetchall_results = [
                [(1, "MATH", "201", "Calc II", 8)]
            ]
            @contextmanager
            def db(user):
                yield conn
            mock_db.side_effect = db
            resp = client.get("/api/my/tutor_capabilities", cookies=auth_cookies())

        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 1
        assert data[0]["confidence"] == 8

    def test_add_capability(self, client):
        resp = client.post("/api/my/tutor_capabilities",
                           data={"course_id": 1, "confidence": 7},
                           cookies=auth_cookies())
        assert resp.status_code == 200
        assert resp.json()["ok"] is True

    def test_add_capability_invalid_confidence(self, client):
        resp = client.post("/api/my/tutor_capabilities",
                           data={"course_id": 1, "confidence": 11},
                           cookies=auth_cookies())
        assert resp.status_code == 400

    def test_add_capability_zero_confidence(self, client):
        resp = client.post("/api/my/tutor_capabilities",
                           data={"course_id": 1, "confidence": 0},
                           cookies=auth_cookies())
        assert resp.status_code == 400

    def test_delete_capability(self, client):
        resp = client.delete("/api/my/tutor_capabilities/1", cookies=auth_cookies())
        assert resp.status_code == 200
        assert resp.json()["ok"] is True

    def test_get_recommendations(self, client):
        with patch("app.routes.user.get_db_for_user") as mock_db:
            conn = FakeConnection()
            conn._cursor._fetchall_results = [
                [(5, "PHGN", "100", "Physics I")]
            ]
            @contextmanager
            def db(user):
                yield conn
            mock_db.side_effect = db
            resp = client.get("/api/my/tutor_recommendations", cookies=auth_cookies())

        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 1
        assert data[0]["course_id"] == 5

    def test_dismiss_recommendation(self, client):
        resp = client.post("/api/my/tutor_dismiss/5", cookies=auth_cookies())
        assert resp.status_code == 200
        assert resp.json()["ok"] is True


# ---------------------------------------------------------------------------
# Assessment APIs
# ---------------------------------------------------------------------------

class TestAssessments:
    def test_get_assessments(self, client):
        with patch("app.routes.user.get_db_for_user") as mock_db:
            conn = FakeConnection()
            conn._cursor._fetchall_results = [
                [(1, 10, "CSCI", "101", "Intro to CS", "2026-03-15", "in_class", True, False)]
            ]
            @contextmanager
            def db(user):
                yield conn
            mock_db.side_effect = db
            resp = client.get("/api/my/assessments", cookies=auth_cookies())

        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 1
        assert data[0]["exam_type"] == "in_class"
        assert data[0]["confirmed"] is True

    def test_report_assessment(self, client):
        with patch("app.routes.user.get_db") as mock_admin_db:
            conn = FakeConnection()
            conn._cursor._results = [(2026, "spring")]
            @contextmanager
            def db():
                yield conn
            mock_admin_db.side_effect = db
            with patch("app.routes.user.get_db_for_user") as mock_user_db:
                conn2 = FakeConnection()
                conn2._cursor._results = [(1,)]  # enrollment exists
                @contextmanager
                def dbu(user):
                    yield conn2
                mock_user_db.side_effect = dbu
                resp = client.post("/api/my/assessments", data={
                    "course_id": 10, "test_date": "2026-03-15", "exam_type": "in_class"
                }, cookies=auth_cookies())
        assert resp.status_code == 200
        assert resp.json()["ok"] is True

    def test_report_not_enrolled(self, client):
        with patch("app.routes.user.get_db") as mock_admin_db:
            conn = FakeConnection()
            conn._cursor._results = [(2026, "spring")]
            @contextmanager
            def db():
                yield conn
            mock_admin_db.side_effect = db
            with patch("app.routes.user.get_db_for_user") as mock_user_db:
                conn2 = FakeConnection()
                conn2._cursor._results = [None]  # not enrolled
                @contextmanager
                def dbu(user):
                    yield conn2
                mock_user_db.side_effect = dbu
                resp = client.post("/api/my/assessments", data={
                    "course_id": 10, "test_date": "2026-03-15", "exam_type": "in_class"
                }, cookies=auth_cookies())
        assert resp.status_code == 403

    def test_report_invalid_type(self, client):
        resp = client.post("/api/my/assessments", data={
            "course_id": 10, "test_date": "2026-03-15", "exam_type": "final"
        }, cookies=auth_cookies())
        assert resp.status_code == 400

    def test_report_invalid_date(self, client):
        resp = client.post("/api/my/assessments", data={
            "course_id": 10, "test_date": "not-a-date", "exam_type": "in_class"
        }, cookies=auth_cookies())
        assert resp.status_code == 400

    def test_delete_own_assessment(self, client):
        resp = client.delete("/api/my/assessments/1", cookies=auth_cookies())
        assert resp.status_code == 200
        assert resp.json()["ok"] is True

    def test_dispute_final(self, client):
        with patch("app.routes.user.get_db") as mock_admin_db, \
             patch("app.routes.user.get_db_for_user") as mock_user_db:
            conn = FakeConnection()
            conn._cursor._results = [(2026, "spring")]
            @contextmanager
            def db():
                yield conn
            mock_admin_db.side_effect = db

            conn2 = FakeConnection()
            conn2._cursor._results = [
                (10,),  # exam exists, course_id=10
                (1,),   # enrollment exists
            ]
            @contextmanager
            def dbu(user):
                yield conn2
            mock_user_db.side_effect = dbu

            resp = client.post("/api/my/assessments/1/dispute", cookies=auth_cookies())
        assert resp.status_code == 200
        assert resp.json()["ok"] is True

    def test_dispute_not_found(self, client):
        with patch("app.routes.user.get_db") as mock_admin_db, \
             patch("app.routes.user.get_db_for_user") as mock_user_db:
            conn = FakeConnection()
            conn._cursor._results = [(2026, "spring")]
            @contextmanager
            def db():
                yield conn
            mock_admin_db.side_effect = db

            conn2 = FakeConnection()
            conn2._cursor._results = [None]  # exam not found
            @contextmanager
            def dbu(user):
                yield conn2
            mock_user_db.side_effect = dbu

            resp = client.post("/api/my/assessments/99/dispute", cookies=auth_cookies())
        assert resp.status_code == 404

    def test_unauthenticated_blocked(self, client):
        resp = client.get("/api/my/assessments")
        assert resp.status_code == 302


# ---------------------------------------------------------------------------
# Course tutors & Study sessions
# ---------------------------------------------------------------------------

class TestCourseTutors:
    def test_get_my_course_tutors(self, client):
        with patch("app.routes.user.get_db_for_user") as mock_db:
            conn = FakeConnection()
            # Query sequence per enrolled course:
            # 1. fetchall: enrolled course IDs
            # 2. Per course: fetchone(linked_any), fetchone(linked_strong),
            #    fetchall(tutors), fetchone(course_info)
            conn._cursor._fetchall_results = [
                [(10,), (20,)],  # enrolled IDs
                # course 10 tutors
                [(100, "John", "Smith", 8, 10, "MATH", "213"),
                 (101, "Jane", "Doe", 7, 10, "MATH", "213")],
                # course 20 tutors
                [(102, "Bob", "Brown", 9, 20, "CSCI", "200")],
            ]
            conn._cursor._results = [
                ([10],),    # linked_course_ids_any(10)
                ([10],),    # linked_course_ids(10)
                (10, "MATH", "213", "Calc III"),  # course info for 10
                ([20],),    # linked_course_ids_any(20)
                ([20],),    # linked_course_ids(20)
                (20, "CSCI", "200", "OOP"),  # course info for 20
            ]
            @contextmanager
            def dbu(user):
                yield conn
            mock_db.side_effect = dbu
            resp = client.get("/api/my/course_tutors", cookies=auth_cookies())
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 2
        math_course = next(c for c in data if c["department"] == "MATH")
        assert len(math_course["tutors"]) == 2
        assert math_course["tutors"][0]["confidence"] == 8

    def test_get_my_course_tutors_empty(self, client):
        with patch("app.routes.user.get_db_for_user") as mock_db:
            conn = FakeConnection()
            conn._cursor._fetchall_results = [[]]
            @contextmanager
            def dbu(user):
                yield conn
            mock_db.side_effect = dbu
            resp = client.get("/api/my/course_tutors", cookies=auth_cookies())
        assert resp.status_code == 200
        assert resp.json() == []

    def test_unauthenticated_blocked(self, client):
        resp = client.get("/api/my/course_tutors")
        assert resp.status_code == 302


class TestStudySessions:
    def test_get_my_study_sessions(self, client):
        from datetime import datetime, timezone
        with patch("app.routes.user.get_db_for_user") as mock_db:
            conn = FakeConnection()
            dt = datetime(2026, 3, 4, 15, 0, tzinfo=timezone.utc)
            conn._cursor._fetchall_results = [
                [
                    (1, dt, "Study Room", 5, "2026-03-05", "final",
                     "MATH", "213", "Calc III", "John", "Smith", False),
                ],
            ]
            @contextmanager
            def dbu(user):
                yield conn
            mock_db.side_effect = dbu
            resp = client.get("/api/my/study_sessions", cookies=auth_cookies())
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 1
        assert data[0]["department"] == "MATH"
        assert data[0]["tutor_first"] == "John"
        assert data[0]["location"] == "Study Room"
        assert data[0]["is_tutor"] is False

    def test_get_my_study_sessions_as_tutor(self, client):
        """Sessions where user is the tutor should appear with is_tutor=True."""
        from datetime import datetime, timezone
        with patch("app.routes.user.get_db_for_user") as mock_db:
            conn = FakeConnection()
            dt = datetime(2026, 3, 4, 15, 0, tzinfo=timezone.utc)
            conn._cursor._fetchall_results = [
                [
                    (2, dt, "Library", 7, "2026-03-05", "common_hour",
                     "CSCI", "200", "OOP", "Test", "User", True),
                ],
            ]
            @contextmanager
            def dbu(user):
                yield conn
            mock_db.side_effect = dbu
            resp = client.get("/api/my/study_sessions", cookies=auth_cookies())
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 1
        assert data[0]["is_tutor"] is True
        assert data[0]["department"] == "CSCI"

    def test_get_my_study_sessions_no_tutor(self, client):
        """Sessions with no tutor assigned should still appear."""
        from datetime import datetime, timezone
        with patch("app.routes.user.get_db_for_user") as mock_db:
            conn = FakeConnection()
            dt = datetime(2026, 3, 4, 15, 0, tzinfo=timezone.utc)
            conn._cursor._fetchall_results = [
                [
                    (3, dt, "Study Room", 8, "2026-03-06", "final",
                     "PHGN", "100", "Physics I", None, None, False),
                ],
            ]
            @contextmanager
            def dbu(user):
                yield conn
            mock_db.side_effect = dbu
            resp = client.get("/api/my/study_sessions", cookies=auth_cookies())
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 1
        assert data[0]["tutor_first"] is None
        assert data[0]["is_tutor"] is False

    def test_get_my_study_sessions_empty(self, client):
        with patch("app.routes.user.get_db_for_user") as mock_db:
            conn = FakeConnection()
            conn._cursor._fetchall_results = [[]]
            @contextmanager
            def dbu(user):
                yield conn
            mock_db.side_effect = dbu
            resp = client.get("/api/my/study_sessions", cookies=auth_cookies())
        assert resp.status_code == 200
        assert resp.json() == []

    def test_unauthenticated_blocked(self, client):
        resp = client.get("/api/my/study_sessions")
        assert resp.status_code == 302


# ---------------------------------------------------------------------------
# Graduated user restrictions
# ---------------------------------------------------------------------------

class TestGraduatedRestrictions:
    def test_graduated_cannot_add_enrollment(self, client):
        resp = client.post("/api/my/enrollments", data={"course_id": 1},
                           cookies=auth_cookies(role="graduated"))
        assert resp.status_code == 403

    def test_graduated_cannot_remove_enrollment(self, client):
        resp = client.delete("/api/my/enrollments/1",
                             cookies=auth_cookies(role="graduated"))
        assert resp.status_code == 403

    def test_graduated_classmates_returns_empty(self, client):
        resp = client.get("/api/my/classmates",
                          cookies=auth_cookies(role="graduated"))
        assert resp.status_code == 200
        assert resp.json() == []

    def test_graduated_search_students_returns_empty(self, client):
        resp = client.get("/api/my/search_students?q=Alice",
                          cookies=auth_cookies(role="graduated"))
        assert resp.status_code == 200
        assert resp.json() == []

    def test_regular_user_can_add_enrollment(self, client):
        with patch("app.routes.user.get_db") as mock_db:
            conn = FakeConnection()
            conn._cursor._results = [(2026, "spring")]
            @contextmanager
            def db():
                yield conn
            mock_db.side_effect = db
            resp = client.post("/api/my/enrollments", data={"course_id": 1},
                               cookies=auth_cookies(role="user"))
        assert resp.status_code == 200
