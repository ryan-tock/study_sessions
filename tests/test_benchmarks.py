"""
Benchmark tests: time endpoint processing at various mock database sizes.

These tests use large fake result sets to measure how long endpoint logic takes
(excluding real DB latency). Run with: pytest tests/test_benchmarks.py -v -s

Results are written to BENCHMARK_RESULTS.md after each run.
"""
import time
from contextlib import contextmanager
from unittest.mock import patch
from tests.conftest import auth_cookies, FakeConnection


ADMIN = auth_cookies(student_id=1, is_admin=True)
USER = auth_cookies(student_id=2, is_admin=False)
SIZES = [50, 200, 500]


def _make_users(n):
    """Generate n user rows for admin portal query."""
    return [
        (i, f"First{i}", f"Last{i}", i == 1, i == 1, None, str(100000 + i))
        for i in range(1, n + 1)
    ]


def _make_courses(n):
    """Generate n course rows for /api/courses."""
    return [
        (i, f"DEPT{i % 10}", f"{100 + i}", f"Course Title {i}", 3, f"DEPT{i % 10}{100 + i}", False, False)
        for i in range(1, n + 1)
    ]


def _make_enrollments(n):
    """Generate n enrollment rows."""
    return [
        (i, f"DEPT{i % 10}", f"{100 + i}", f"Course Title {i}")
        for i in range(1, n + 1)
    ]


def _make_tutor_caps(n):
    """Generate n tutor capability rows."""
    return [
        (i, f"DEPT{i % 10}", f"{100 + i}", f"Course Title {i}", min(i % 10 + 1, 10))
        for i in range(1, n + 1)
    ]


def _make_classmates(n):
    """Generate n classmate rows (student_id, first, last, course_id, dept, ident)."""
    return [
        (100 + i, f"Peer{i}", f"Student{i}", i % 20 + 1, f"DEPT{i % 5}", f"{200 + i % 20}")
        for i in range(1, n + 1)
    ]


class TestAdminPortalBenchmark:
    """Time the admin portal page load with varying user counts."""

    def _bench_portal(self, client, num_users):
        with patch("app.routes.admin.get_db_for_user") as mock_dbu, \
             patch("app.routes.admin.get_db") as mock_db, \
             patch("app.routes.admin.get_user_profile", return_value={
                 "first_name": "Root", "last_name": "Admin", "discord_id": None,
                 "sharing": "closed", "dark_mode": False,
             }), \
             patch("app.routes.admin.course_cache_exists", return_value=False), \
             patch("app.routes.admin._list_wipeble_terms", return_value=[]):
            conn = FakeConnection()
            conn._cursor._fetchall_results = [_make_users(num_users)]
            @contextmanager
            def dbu(user):
                yield conn
            mock_dbu.side_effect = dbu

            conn2 = FakeConnection()
            conn2._cursor._results = [
                (2026, "spring"),  # current_term
                (2026, "spring"),  # last_seen_term check
                None,              # confidence_decay_log check
            ]
            @contextmanager
            def db():
                yield conn2
            mock_db.side_effect = db

            start = time.perf_counter()
            resp = client.get("/admin/portal", cookies=ADMIN)
            elapsed = time.perf_counter() - start

        assert resp.status_code == 200
        return elapsed

    def test_portal_50_users(self, client, bench_record):
        t = self._bench_portal(client, 50)
        bench_record("Admin portal (50)", t)
        print(f"\n  Admin portal (50 users): {t*1000:.1f}ms")
        assert t < 2.0

    def test_portal_200_users(self, client, bench_record):
        t = self._bench_portal(client, 200)
        bench_record("Admin portal (200)", t)
        print(f"\n  Admin portal (200 users): {t*1000:.1f}ms")
        assert t < 5.0

    def test_portal_500_users(self, client, bench_record):
        t = self._bench_portal(client, 500)
        bench_record("Admin portal (500)", t)
        print(f"\n  Admin portal (500 users): {t*1000:.1f}ms")
        assert t < 10.0


class TestCourseListBenchmark:
    """Time the /api/courses endpoint with varying course counts."""

    def _bench_courses(self, client, num_courses):
        with patch("app.routes.public.get_db") as mock_db:
            conn = FakeConnection()
            conn._cursor._results = [(2026, "spring")]
            conn._cursor._fetchall_results = [_make_courses(num_courses)]
            @contextmanager
            def db():
                yield conn
            mock_db.side_effect = db

            start = time.perf_counter()
            resp = client.get("/api/courses")
            elapsed = time.perf_counter() - start

        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == num_courses
        return elapsed

    def test_courses_50(self, client, bench_record):
        t = self._bench_courses(client, 50)
        bench_record("/api/courses (50)", t)
        print(f"\n  /api/courses (50): {t*1000:.1f}ms")
        assert t < 1.0

    def test_courses_200(self, client, bench_record):
        t = self._bench_courses(client, 200)
        bench_record("/api/courses (200)", t)
        print(f"\n  /api/courses (200): {t*1000:.1f}ms")
        assert t < 2.0

    def test_courses_500(self, client, bench_record):
        t = self._bench_courses(client, 500)
        bench_record("/api/courses (500)", t)
        print(f"\n  /api/courses (500): {t*1000:.1f}ms")
        assert t < 5.0


class TestEnrollmentsBenchmark:
    """Time enrollment list with varying counts."""

    def _bench_enrollments(self, client, n):
        with patch("app.routes.user.get_db_for_user") as mock_db:
            conn = FakeConnection()
            conn._cursor._fetchall_results = [_make_enrollments(n)]
            @contextmanager
            def db(user):
                yield conn
            mock_db.side_effect = db

            start = time.perf_counter()
            resp = client.get("/api/my/enrollments", cookies=USER)
            elapsed = time.perf_counter() - start

        assert resp.status_code == 200
        assert len(resp.json()) == n
        return elapsed

    def test_enrollments_10(self, client, bench_record):
        t = self._bench_enrollments(client, 10)
        bench_record("/api/my/enrollments (10)", t)
        print(f"\n  /api/my/enrollments (10): {t*1000:.1f}ms")
        assert t < 1.0

    def test_enrollments_50(self, client, bench_record):
        t = self._bench_enrollments(client, 50)
        bench_record("/api/my/enrollments (50)", t)
        print(f"\n  /api/my/enrollments (50): {t*1000:.1f}ms")
        assert t < 1.0

    def test_enrollments_200(self, client, bench_record):
        t = self._bench_enrollments(client, 200)
        bench_record("/api/my/enrollments (200)", t)
        print(f"\n  /api/my/enrollments (200): {t*1000:.1f}ms")
        assert t < 2.0


class TestTutorCapsBenchmark:
    """Time tutor capabilities list with varying counts."""

    def _bench_tutor_caps(self, client, n):
        with patch("app.routes.user.get_db_for_user") as mock_db:
            conn = FakeConnection()
            conn._cursor._fetchall_results = [_make_tutor_caps(n)]
            @contextmanager
            def db(user):
                yield conn
            mock_db.side_effect = db

            start = time.perf_counter()
            resp = client.get("/api/my/tutor_capabilities", cookies=USER)
            elapsed = time.perf_counter() - start

        assert resp.status_code == 200
        assert len(resp.json()) == n
        return elapsed

    def test_tutor_caps_10(self, client, bench_record):
        t = self._bench_tutor_caps(client, 10)
        bench_record("/api/my/tutor_capabilities (10)", t)
        print(f"\n  /api/my/tutor_capabilities (10): {t*1000:.1f}ms")
        assert t < 1.0

    def test_tutor_caps_50(self, client, bench_record):
        t = self._bench_tutor_caps(client, 50)
        bench_record("/api/my/tutor_capabilities (50)", t)
        print(f"\n  /api/my/tutor_capabilities (50): {t*1000:.1f}ms")
        assert t < 1.0

    def test_tutor_caps_200(self, client, bench_record):
        t = self._bench_tutor_caps(client, 200)
        bench_record("/api/my/tutor_capabilities (200)", t)
        print(f"\n  /api/my/tutor_capabilities (200): {t*1000:.1f}ms")
        assert t < 2.0


class TestClassmatesBenchmark:
    """Time classmates list with varying counts."""

    def _bench_classmates(self, client, n):
        with patch("app.routes.user.get_db_for_user") as mock_db:
            conn = FakeConnection()
            conn._cursor._fetchall_results = [_make_classmates(n)]
            @contextmanager
            def db(user):
                yield conn
            mock_db.side_effect = db

            start = time.perf_counter()
            resp = client.get("/api/my/classmates", cookies=USER)
            elapsed = time.perf_counter() - start

        assert resp.status_code == 200
        return elapsed

    def test_classmates_50(self, client, bench_record):
        t = self._bench_classmates(client, 50)
        bench_record("/api/my/classmates (50)", t)
        print(f"\n  /api/my/classmates (50): {t*1000:.1f}ms")
        assert t < 1.0

    def test_classmates_200(self, client, bench_record):
        t = self._bench_classmates(client, 200)
        bench_record("/api/my/classmates (200)", t)
        print(f"\n  /api/my/classmates (200): {t*1000:.1f}ms")
        assert t < 2.0

    def test_classmates_500(self, client, bench_record):
        t = self._bench_classmates(client, 500)
        bench_record("/api/my/classmates (500)", t)
        print(f"\n  /api/my/classmates (500): {t*1000:.1f}ms")
        assert t < 5.0
