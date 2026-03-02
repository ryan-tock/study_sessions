import os
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request
from fastapi.staticfiles import StaticFiles
from starlette.middleware.base import BaseHTTPMiddleware

from .routes import auth, user, admin, admin_data, public, discord


class TokenRefreshMiddleware(BaseHTTPMiddleware):
    """Sets a new access_token cookie when the JWT was auto-refreshed from a refresh token."""
    async def dispatch(self, request: Request, call_next):
        request.state.new_access_token = None
        response = await call_next(request)
        new_token = request.state.new_access_token
        if new_token:
            response.set_cookie(
                key="access_token",
                value=f"Bearer {new_token}",
                httponly=True,
                max_age=1800,
                samesite="lax",
            )
        return response


@asynccontextmanager
async def lifespan(_: FastAPI):
    yield


app = FastAPI(lifespan=lifespan)
app.add_middleware(TokenRefreshMiddleware)

current_dir = os.path.dirname(os.path.abspath(__file__))
app.mount("/static", StaticFiles(directory=os.path.join(current_dir, "static")), name="static")

app.include_router(auth.router)
app.include_router(user.page_router)
app.include_router(user.api_router)
app.include_router(admin.router)
app.include_router(admin_data.router)
app.include_router(public.router)
app.include_router(discord.router)


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
