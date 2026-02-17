"""FastAPI app entry for the itinerary API."""

from __future__ import annotations

import os

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .routes import router


def create_app() -> FastAPI:
    app = FastAPI(
        title="Itinerary Multi-Agent API",
        description="Trip info upload, research agent, itinerary agent.",
        version="0.1.0",
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.include_router(router)
    return app


app = create_app()


@app.get("/")
def root() -> dict:
    return {"service": "itinerary-api", "docs": "/docs"}
