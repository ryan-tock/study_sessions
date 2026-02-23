import os
from fastapi import FastAPI, Depends, HTTPException, status, Request, Response, Form
from fastapi.templating import Jinja2Templates
from fastapi.staticfiles import StaticFiles
from fastapi.responses import RedirectResponse, HTMLResponse
from datetime import timedelta
from jose import JWTError, jwt

from .auth import (
    authenticate_user, create_access_token, create_refresh_token, 
    verify_refresh_token, revoke_refresh_token
)
from .config import SECRET_KEY, ALGORITHM
from typing import Optional

app = FastAPI()

# Setup templates and static files with absolute paths
current_dir = os.path.dirname(os.path.abspath(__file__))
templates = Jinja2Templates(directory=os.path.join(current_dir, "templates"))
app.mount("/static", StaticFiles(directory=os.path.join(current_dir, "static")), name="static")


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
        return {"student_id": student_id, "is_admin": is_admin}
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


@app.get("/", response_class=HTMLResponse)
async def root(request: Request):
    """Root route - redirects to appropriate portal if logged in, else shows login."""
    user = get_current_user(request)
    if user:
        if user["is_admin"]:
            return RedirectResponse(url="/admin/portal")
        else:
            return RedirectResponse(url="/user/portal")
    return templates.TemplateResponse("login.html", {"request": request})


@app.post("/login")
async def login(
    response: Response,
    first_name: str = Form(...),
    last_name: str = Form(...),
    password: str = Form(...)
):
    """Handle login form submission."""
    user = authenticate_user(first_name, last_name, password)
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect name or password",
            headers={"WWW-Authenticate": "Bearer"},
        )
    
    # Create tokens
    access_token_expires = timedelta(minutes=30)
    access_token = create_access_token(
        data={"sub": str(user["student_id"]), "is_admin": user["is_admin"]},
        expires_delta=access_token_expires
    )
    refresh_token = create_refresh_token(user["student_id"])
    
    # Redirect based on role
    redirect_url = "/admin/portal" if user["is_admin"] else "/user/portal"
    response = RedirectResponse(url=redirect_url, status_code=status.HTTP_302_FOUND)
    
    # Set cookies
    response.set_cookie(
        key="access_token",
        value=f"Bearer {access_token}",
        httponly=True,
        max_age=1800,  # 30 minutes
        samesite="lax"
    )
    response.set_cookie(
        key="refresh_token",
        value=refresh_token,
        httponly=True,
        max_age=604800,  # 7 days
        samesite="lax",
        path="/"
    )
    return response


@app.post("/logout")
async def logout(request: Request, response: Response):
    """Handle logout - revoke refresh token and clear cookies."""
    refresh_token = request.cookies.get("refresh_token")
    if refresh_token:
        revoke_refresh_token(refresh_token)
    
    response = RedirectResponse(url="/", status_code=status.HTTP_302_FOUND)
    response.delete_cookie("access_token")
    response.delete_cookie("refresh_token", path="/")
    return response


@app.get("/user/portal", response_class=HTMLResponse)
async def user_portal(request: Request, user: dict = Depends(require_auth)):
    """User portal - accessible by all authenticated users."""
    return templates.TemplateResponse("user_portal.html", {
        "request": request,
        "user": user
    })


@app.get("/admin/portal", response_class=HTMLResponse)
async def admin_portal(request: Request, user: dict = Depends(require_admin)):
    """Admin portal - accessible only by admins."""
    return templates.TemplateResponse("admin_portal.html", {
        "request": request,
        "user": user
    })


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
