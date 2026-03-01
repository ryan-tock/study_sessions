from typing import Optional
from fastapi import HTTPException, Request, status
from jose import JWTError, jwt
from .config import SECRET_KEY, ALGORITHM


def get_current_user(request: Request) -> Optional[dict]:
    """Extract and verify JWT from cookie."""
    token = request.cookies.get("access_token")
    if not token or not token.startswith("Bearer "):
        return None

    token = token.replace("Bearer ", "")
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        student_id = int(payload.get("sub"))
        is_admin = payload.get("is_admin", False)
        is_root = payload.get("is_root", False)
        is_first_login = payload.get("is_first_login", False)
        return {"student_id": student_id, "is_admin": is_admin, "is_root": is_root, "is_first_login": is_first_login}
    except (JWTError, ValueError):
        return None


def require_auth(request: Request):
    """Dependency to require authentication."""
    user = get_current_user(request)
    if not user:
        raise HTTPException(
            status_code=status.HTTP_302_FOUND,
            headers={"Location": "/"},
            detail="Not authenticated"
        )
    return user


def require_admin(request: Request):
    """Dependency to require admin privileges."""
    user = require_auth(request)
    if not user["is_admin"]:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admin access required"
        )
    return user
