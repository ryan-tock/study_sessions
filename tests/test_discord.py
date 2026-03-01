"""Tests for discord routes: avatar serve, avatar fetch, validation helpers."""
import os
from contextlib import contextmanager
from unittest.mock import patch, MagicMock
from tests.conftest import FakeConnection


class TestServeAvatar:
    def test_serve_existing_avatar(self, client, tmp_data_dir):
        # Create a fake avatar file
        avatar_path = os.path.join(tmp_data_dir["avatars"], "42.png")
        with open(avatar_path, "wb") as f:
            f.write(b"\x89PNG\r\n\x1a\nfake")

        with patch("app.routes.discord.AVATAR_DIR", tmp_data_dir["avatars"]):
            resp = client.get("/avatar/42")

        assert resp.status_code == 200
        assert resp.headers.get("cache-control") == "public, max-age=86400"

    def test_serve_missing_avatar_404(self, client, tmp_data_dir):
        with patch("app.routes.discord.AVATAR_DIR", tmp_data_dir["avatars"]):
            resp = client.get("/avatar/999")
        assert resp.status_code == 404


class TestFetchAvatarForLogin:
    @patch("app.routes.discord.download_and_cache_avatar")
    def test_fetch_with_discord_id(self, mock_download, client, tmp_data_dir):
        with patch("app.routes.discord.get_db") as mock_db, \
             patch("app.routes.discord.AVATAR_DIR", tmp_data_dir["avatars"]):
            conn = FakeConnection()
            conn._cursor._results = [("123456789",)]
            @contextmanager
            def db():
                yield conn
            mock_db.side_effect = db

            # Simulate download creating the file
            avatar_path = os.path.join(tmp_data_dir["avatars"], "42.png")
            def create_avatar(sid, did):
                with open(avatar_path, "wb") as f:
                    f.write(b"fake")
            mock_download.side_effect = create_avatar

            resp = client.post("/api/avatar/fetch/42")

        assert resp.status_code == 200
        assert resp.json()["ok"] is True
        mock_download.assert_called_once_with(42, "123456789")

    def test_fetch_no_discord_id(self, client):
        with patch("app.routes.discord.get_db") as mock_db:
            conn = FakeConnection()
            conn._cursor._results = [(None,)]
            @contextmanager
            def db():
                yield conn
            mock_db.side_effect = db
            resp = client.post("/api/avatar/fetch/42")

        assert resp.status_code == 200
        assert resp.json()["ok"] is False

    def test_fetch_user_not_found(self, client):
        with patch("app.routes.discord.get_db") as mock_db:
            conn = FakeConnection()
            conn._cursor._results = [None]
            @contextmanager
            def db():
                yield conn
            mock_db.side_effect = db
            resp = client.post("/api/avatar/fetch/999")

        assert resp.status_code == 200
        assert resp.json()["ok"] is False


class TestValidateDiscordId:
    @patch("app.routes.discord.urllib.request.urlopen")
    def test_valid_id_with_avatar(self, mock_urlopen, client):
        import json
        mock_resp = MagicMock()
        mock_resp.read.return_value = json.dumps({"avatar": "abc123"}).encode()
        mock_resp.__enter__ = lambda s: s
        mock_resp.__exit__ = MagicMock(return_value=False)
        mock_urlopen.return_value = mock_resp

        from app.routes.discord import validate_discord_id
        ok, err = validate_discord_id("123456789")
        assert ok is True
        assert err == ""

    @patch("app.routes.discord.urllib.request.urlopen")
    def test_valid_id_no_avatar(self, mock_urlopen, client):
        import json
        mock_resp = MagicMock()
        mock_resp.read.return_value = json.dumps({"avatar": None}).encode()
        mock_resp.__enter__ = lambda s: s
        mock_resp.__exit__ = MagicMock(return_value=False)
        mock_urlopen.return_value = mock_resp

        from app.routes.discord import validate_discord_id
        ok, err = validate_discord_id("123456789")
        assert ok is False
        assert "no profile picture" in err.lower()

    @patch("app.routes.discord.DISCORD_BOT_TOKEN", "")
    def test_no_bot_token_skips_validation(self):
        from app.routes.discord import validate_discord_id
        ok, err = validate_discord_id("anything")
        assert ok is True
