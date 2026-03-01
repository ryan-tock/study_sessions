"""Tests for auth routes: GET /, POST /login, POST /logout."""
from unittest.mock import patch, MagicMock
from tests.conftest import auth_cookies


class TestRootRoute:
    def test_unauthenticated_shows_login(self, client):
        resp = client.get("/")
        assert resp.status_code == 200
        assert "login" in resp.text.lower() or "Study Sessions" in resp.text

    def test_authenticated_admin_redirects_to_admin_portal(self, client):
        resp = client.get("/", cookies=auth_cookies(is_admin=True))
        assert resp.status_code in (301, 302, 307)
        assert "/admin/portal" in resp.headers["location"]

    def test_authenticated_user_redirects_to_user_portal(self, client):
        resp = client.get("/", cookies=auth_cookies(is_admin=False))
        assert resp.status_code in (301, 302, 307)
        assert "/user/portal" in resp.headers["location"]

    def test_error_query_param_rendered(self, client):
        resp = client.get("/?error=bad+password")
        assert resp.status_code == 200

    def test_prefill_query_params(self, client):
        resp = client.get("/?fn=John&ln=Doe")
        assert resp.status_code == 200


class TestLogin:
    @patch("app.routes.auth.authenticate_user")
    @patch("app.routes.auth.create_refresh_token", return_value="1.fakesecret")
    def test_successful_login_redirects(self, mock_refresh, mock_auth, client):
        mock_auth.return_value = {
            "student_id": 1, "is_admin": False, "is_root": False, "is_first_login": False
        }
        resp = client.post("/login", data={
            "first_name": "Test", "last_name": "User", "password": "pass"
        })
        assert resp.status_code == 302
        assert "/user/portal" in resp.headers["location"]
        assert "access_token" in resp.cookies

    @patch("app.routes.auth.authenticate_user")
    @patch("app.routes.auth.create_refresh_token", return_value="1.fakesecret")
    def test_admin_login_redirects_to_admin_portal(self, mock_refresh, mock_auth, client):
        mock_auth.return_value = {
            "student_id": 1, "is_admin": True, "is_root": False, "is_first_login": False
        }
        resp = client.post("/login", data={
            "first_name": "Admin", "last_name": "User", "password": "pass"
        })
        assert resp.status_code == 302
        assert "/admin/portal" in resp.headers["location"]

    @patch("app.routes.auth.authenticate_user")
    @patch("app.routes.auth.create_refresh_token", return_value="1.fakesecret")
    def test_first_login_redirects_to_set_password(self, mock_refresh, mock_auth, client):
        mock_auth.return_value = {
            "student_id": 1, "is_admin": False, "is_root": False, "is_first_login": True
        }
        resp = client.post("/login", data={
            "first_name": "New", "last_name": "User", "password": "pass"
        })
        assert resp.status_code == 302
        assert "/user/set_password" in resp.headers["location"]

    @patch("app.routes.auth.authenticate_user", return_value=None)
    def test_failed_login_redirects_with_error(self, mock_auth, client):
        resp = client.post("/login", data={
            "first_name": "Bad", "last_name": "User", "password": "wrong"
        })
        assert resp.status_code == 302
        assert "error=" in resp.headers["location"]

    @patch("app.routes.auth.authenticate_user", return_value=None)
    def test_rate_limiting_after_5_failures(self, mock_auth, client):
        for _ in range(5):
            client.post("/login", data={
                "first_name": "RateLimit", "last_name": "Test", "password": "wrong"
            })
        resp = client.post("/login", data={
            "first_name": "RateLimit", "last_name": "Test", "password": "wrong"
        })
        assert resp.status_code == 302
        assert "Too+many" in resp.headers["location"] or "Too%20many" in resp.headers["location"]


class TestLogout:
    @patch("app.routes.auth.revoke_refresh_token")
    def test_logout_clears_cookies(self, mock_revoke, client):
        resp = client.post("/logout", cookies={
            "access_token": "Bearer fake",
            "refresh_token": "1.secret"
        })
        assert resp.status_code == 302
        assert resp.headers["location"] == "/"
        mock_revoke.assert_called_once_with("1.secret")

    @patch("app.routes.auth.revoke_refresh_token")
    def test_logout_without_refresh_token(self, mock_revoke, client):
        resp = client.post("/logout")
        assert resp.status_code == 302
        mock_revoke.assert_not_called()
