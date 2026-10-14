"""NoteTutor AI REST API (FastAPI).

    uvicorn app.api.main:app --reload
"""

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api import worker
from app.api.config import settings
from app.api.db import init_db
from app.api.routers import admin, auth, lessons


@asynccontextmanager
async def lifespan(_: FastAPI):
    init_db()
    worker.recover_interrupted()
    yield


app = FastAPI(
    title="NoteTutor AI",
    description="Turns notes into narrated, animated explainer videos.",
    version="1.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=list(settings.cors_origins),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth.router)
app.include_router(lessons.router)
app.include_router(admin.router)


@app.get("/")
def read_root():
    return {"application": "NoteTutor AI", "docs": "/docs", "health": "/health"}


@app.get("/health")
def health_check():
    return {"status": "ok", "application": "NoteTutor AI"}
