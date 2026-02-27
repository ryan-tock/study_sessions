from datetime import datetime, timedelta, timezone
from typing import Optional
import bcrypt
from jose import JWTError, jwt
import uuid
import secrets
from .database import get_db
from .config import SECRET_KEY, ALGORITHM, ACCESS_TOKEN_EXPIRE_MINUTES


def verify_password(plain_password: str, hashed_password: str) -> bool:
    hp = hashed_password.encode() if isinstance(hashed_password, str) else hashed_password
    return bcrypt.checkpw(plain_password.encode(), hp)


def get_password_hash(password: str) -> str:
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()


def create_access_token(data: dict, expires_delta: Optional[timedelta] = None) -> str:
    to_encode = data.copy()
    if expires_delta:
        expire = datetime.now(timezone.utc) + expires_delta
    else:
        expire = datetime.now(timezone.utc) + timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    to_encode.update({"exp": expire})
    encoded_jwt = jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)
    return encoded_jwt


def create_refresh_token(student_id: int) -> str:
    """Creates a refresh token. Returns the token string (UUID.secret) to be sent to client."""
    token_secret = secrets.token_urlsafe(32)
    token_hash = get_password_hash(token_secret)
    expires = datetime.now(timezone.utc) + timedelta(days=7)

    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO refresh_tokens (student_id, token_hash, expires_at) VALUES (%s, %s, %s) RETURNING token_id",
                (student_id, token_hash, expires)
            )
            token_id = cur.fetchone()[0]
            conn.commit()

    return f"{token_id}.{token_secret}"


def verify_refresh_token(token: str) -> Optional[int]:
    """Verifies a refresh token and returns student_id if valid."""
    if not token or "." not in token:
        return None

    try:
        token_id_str, token_secret = token.split(".", 1)
    except ValueError:
        return None

    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT student_id, token_hash FROM refresh_tokens WHERE token_id = %s AND expires_at > NOW()",
                (token_id_str,)
            )
            result = cur.fetchone()
            if not result:
                return None
            student_id, token_hash = result
            if verify_password(token_secret, token_hash):
                return student_id
    return None


def revoke_refresh_token(token: str) -> None:
    """Revoke a refresh token by deleting it from the database."""
    if not token or "." not in token:
        return

    try:
        token_id_str = token.split(".", 1)[0]
    except ValueError:
        return

    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM refresh_tokens WHERE token_id = %s", (token_id_str,))
            conn.commit()


def authenticate_root_user(password: str) -> Optional[dict]:
    """Authenticate a root user by password alone."""
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT student_id, hashed_password FROM student_auth WHERE is_root = TRUE")
            for student_id, hashed_password in cur.fetchall():
                if verify_password(password, hashed_password):
                    return {"student_id": student_id, "is_admin": True, "is_root": True}
    return None


def authenticate_user(first_name: str, last_name: str, password: str) -> Optional[dict]:
    """Authenticate user by first name, last name, and password."""
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """SELECT s.student_id, sa.hashed_password, sa.is_admin, sa.is_root, sa.last_login
                   FROM students s
                   JOIN student_auth sa ON s.student_id = sa.student_id
                   WHERE s.first_name = %s AND COALESCE(s.last_name, '') = %s""",
                (first_name, last_name)
            )
            result = cur.fetchone()
            if not result:
                return None
            student_id, hashed_password, is_admin, is_root, last_login = result
            if not verify_password(password, hashed_password):
                return None
            is_first_login = last_login is None
            cur.execute("UPDATE student_auth SET last_login = NOW() WHERE student_id = %s", (student_id,))
            conn.commit()
            return {"student_id": student_id, "is_admin": is_admin, "is_root": is_root, "is_first_login": is_first_login}
