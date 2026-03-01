"""Tests for public API routes (no auth required)."""
from unittest.mock import patch, MagicMock
from contextlib import contextmanager
from tests.conftest import FakeConnection


class TestAllUsers:
    def test_returns_user_list(self, client, _patch_db):
        conns = _patch_db
        # Pre-load result for the next get_db call
        from app.main import app
        with patch("app.routes.public.get_db") as mock_db:
            conn = FakeConnection()
            conn._cursor._fetchall_results = [
                [(1, "Alice", "Smith", "123456"), (2, "Bob", "Jones", None)]
            ]
            @contextmanager
            def db():
                yield conn
            mock_db.side_effect = db
            resp = client.get("/api/users/all")

        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 2
        assert data[0]["first_name"] == "Alice"
        assert data[1]["discord_id"] is None


class TestSearchUsers:
    def test_short_query_returns_empty(self, client):
        resp = client.get("/api/users/search?q=A")
        assert resp.status_code == 200
        assert resp.json() == []

    def test_valid_query_returns_results(self, client):
        with patch("app.routes.public.get_db") as mock_db:
            conn = FakeConnection()
            conn._cursor._fetchall_results = [
                [(1, "Alice", "Smith", "123456")]
            ]
            @contextmanager
            def db():
                yield conn
            mock_db.side_effect = db
            resp = client.get("/api/users/search?q=Alice")

        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 1
        assert data[0]["first_name"] == "Alice"


class TestDiscordAvatar:
    @patch("app.routes.public.urllib.request.urlopen")
    def test_returns_avatar_url(self, mock_urlopen, client):
        import json
        mock_resp = MagicMock()
        mock_resp.read.return_value = json.dumps({"avatar": "abc123"}).encode()
        mock_resp.__enter__ = lambda s: s
        mock_resp.__exit__ = MagicMock(return_value=False)
        mock_urlopen.return_value = mock_resp

        resp = client.get("/api/discord_avatar/123456789")
        assert resp.status_code == 200
        data = resp.json()
        assert "avatar_url" in data
        assert "123456789" in data["avatar_url"]

    @patch("app.routes.public.DISCORD_BOT_TOKEN", "")
    def test_no_bot_token_returns_error(self, client):
        resp = client.get("/api/discord_avatar/123456789")
        assert resp.status_code == 200
        assert resp.json()["avatar_url"] is None


class TestGetAllCourses:
    def test_returns_current_term_courses(self, client):
        with patch("app.routes.public.get_db") as mock_db:
            conn = FakeConnection()
            conn._cursor._fetchall_results = [
                [(1, "CSCI", "101", "Intro to CS", 3), (2, "MATH", "201", "Calculus II", 4)]
            ]
            @contextmanager
            def db():
                yield conn
            mock_db.side_effect = db
            resp = client.get("/api/courses")

        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 2
        assert data[0]["combined"] == "CSCI101"
        assert data[1]["semester_hours"] == 4
