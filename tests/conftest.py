"""
Shared fixtures for all tests.

Strategy: mock psycopg2's connection pool at import time so database.py
never tries to connect. Then mock get_db/get_db_for_user per-test with
fake connections that have programmable cursors.
"""
import os
import sys
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import pytest
from jose import jwt

# ---------------------------------------------------------------------------
# 1) Set env vars BEFORE any app code can load
# ---------------------------------------------------------------------------
os.environ.setdefault("DATABASE_URL", "postgresql://fake:fake@localhost/fake")
os.environ.setdefault("SECRET_KEY", "test-secret-key-for-testing-only")
os.environ.setdefault("ALGORITHM", "HS256")
os.environ.setdefault("ACCESS_TOKEN_EXPIRE_MINUTES", "30")
os.environ.setdefault("USER_PASSWORD", "DefaultPass123!")
os.environ.setdefault("DISCORD_BOT_TOKEN", "fake-bot-token")

# ---------------------------------------------------------------------------
# 2) Patch psycopg2 pool so database.py module-level code doesn't connect
# ---------------------------------------------------------------------------
_mock_pool = MagicMock()
_mock_pool.getconn.return_value = MagicMock()
_mock_pool.putconn = MagicMock()

with patch("psycopg2.pool.ThreadedConnectionPool", return_value=_mock_pool):
    # Force re-import of database module with mocked pool
    if "app.database" in sys.modules:
        del sys.modules["app.database"]
    from app import database  # noqa: E402
    database.connection_pool = _mock_pool

# Now it's safe to import everything else
from fastapi.testclient import TestClient  # noqa: E402


# ---------------------------------------------------------------------------
# Fake DB helpers
# ---------------------------------------------------------------------------

class FakeCursor:
    """A mock cursor that can be pre-loaded with results."""

    def __init__(self):
        self._results = []       # for fetchone (consumed in order)
        self._fetchall_results = []  # for fetchall (consumed in order)
        self.rowcount = 1
        self.description = None
        self._executed = []

    def execute(self, query, params=None):
        self._executed.append((query, params))

    def fetchone(self):
        if self._results:
            return self._results.pop(0)
        return None

    def fetchall(self):
        if self._fetchall_results:
            return self._fetchall_results.pop(0)
        return []

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass


class FakeConnection:
    """A mock connection that yields FakeCursors."""

    def __init__(self):
        self._cursor = FakeCursor()
        self.committed = False
        self.rolled_back = False

    def cursor(self):
        return self._cursor

    def commit(self):
        self.committed = True

    def rollback(self):
        self.rolled_back = True


def make_fake_conn():
    return FakeConnection()


@contextmanager
def fake_get_db():
    yield make_fake_conn()


@contextmanager
def fake_get_db_for_user(user):
    yield make_fake_conn()


# ---------------------------------------------------------------------------
# Token helpers
# ---------------------------------------------------------------------------

SECRET = os.environ["SECRET_KEY"]
ALGO = os.environ["ALGORITHM"]


def make_access_token(student_id=1, is_admin=False, is_root=False, is_first_login=False, expires_minutes=30):
    payload = {
        "sub": str(student_id),
        "is_admin": is_admin,
        "is_root": is_root,
        "is_first_login": is_first_login,
        "exp": datetime.now(timezone.utc) + timedelta(minutes=expires_minutes),
    }
    return jwt.encode(payload, SECRET, algorithm=ALGO)


def auth_cookies(student_id=1, is_admin=False, is_root=False, is_first_login=False):
    """Return a dict of cookies that simulate a logged-in user."""
    token = make_access_token(student_id, is_admin, is_root, is_first_login)
    return {"access_token": f"Bearer {token}"}


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def tmp_data_dir(tmp_path):
    """Provide a temp directory for DATA_DIR / AVATAR_DIR."""
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    avatar_dir = tmp_path / "avatars"
    avatar_dir.mkdir()
    return {"data": str(data_dir), "avatars": str(avatar_dir)}


@pytest.fixture()
def _patch_db():
    """Patch get_db and get_db_for_user in every module that imports them."""
    conns = []

    @contextmanager
    def tracked_get_db():
        conn = make_fake_conn()
        conns.append(conn)
        yield conn

    @contextmanager
    def tracked_get_db_for_user(user):
        conn = make_fake_conn()
        conns.append(conn)
        yield conn

    targets = [
        "app.database",
        "app.helpers",
        "app.routes.auth",
        "app.routes.user",
        "app.routes.admin",
        "app.routes.admin_data",
        "app.routes.public",
        "app.routes.discord",
    ]
    patches = []
    for mod in targets:
        patches.append(patch(f"{mod}.get_db", tracked_get_db))
        patches.append(patch(f"{mod}.get_db_for_user", tracked_get_db_for_user))

    for p in patches:
        try:
            p.start()
        except AttributeError:
            pass  # module doesn't import that name

    yield conns

    for p in patches:
        try:
            p.stop()
        except (AttributeError, RuntimeError):
            pass


class _TestClient(TestClient):
    """TestClient that sets per-request cookies on the client instance
    instead of passing them to httpx (avoids Starlette deprecation warning)."""

    def request(self, *args, cookies=None, **kwargs):
        if cookies is not None:
            self.cookies.clear()
            self.cookies.update(cookies)
        return super().request(*args, **kwargs)


@pytest.fixture()
def client(_patch_db):
    """A TestClient with all DB calls mocked out."""
    from app.main import app
    return _TestClient(app, follow_redirects=False)
