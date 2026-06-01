from contextlib import asynccontextmanager

from fastapi import FastAPI

from web.database import engine, Base
from web.routes.projects import router as projects_router
from web.routes.jobs import router as jobs_router
from web.routes.options import router as options_router


@asynccontextmanager
async def lifespan(app: FastAPI):
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield
    await engine.dispose()


app = FastAPI(lifespan=lifespan)
app.include_router(projects_router)
app.include_router(jobs_router)
app.include_router(options_router)
