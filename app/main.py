import os
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from .routes import auth, user, admin, admin_data, public, discord


@asynccontextmanager
async def lifespan(_: FastAPI):
    yield


app = FastAPI(lifespan=lifespan)

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
