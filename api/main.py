"""FastAPI application: the mock hospital backend and the source of truth for operational state."""

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, status
from fastapi.responses import JSONResponse
from sqlalchemy.exc import OperationalError

from api.database import Base, SessionLocal, engine
from api.routes import router
from api.seed import seed_if_empty

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
logger = logging.getLogger("bedmed.api")


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    Base.metadata.create_all(engine)
    with SessionLocal() as session:
        seed_if_empty(session)
    yield


app = FastAPI(title="BedMed API", version="0.1.0", lifespan=lifespan)
app.include_router(router)


@app.exception_handler(OperationalError)
def database_unavailable(request: Request, error: OperationalError) -> JSONResponse:
    logger.error("database_unavailable path=%s", request.url.path)
    return JSONResponse(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        content={"detail": {"code": "DATABASE_UNAVAILABLE", "message": "The database is unavailable. Try again."}},
    )
