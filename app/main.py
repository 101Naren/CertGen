"""Application entry point: `uvicorn app.main:app --reload`."""
import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse

from app import worker
from app.database import init_db
from app.routes import router

logging.basicConfig(level=logging.INFO)


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()                      # create tables on first run
    worker.requeue_unfinished()    # resume jobs interrupted by a restart
    yield


app = FastAPI(
    title="Bulk Certificate Generator",
    description="Submit many recipients in one request; certificates are generated in the background.",
    lifespan=lifespan,
)
app.include_router(router)


@app.get("/", include_in_schema=False)
def home():
    """The optional one-page form (the API itself is documented at /docs)."""
    return FileResponse(Path(__file__).parent / "static" / "index.html")
