"""Database session factories — sync (CLI) and async (web)."""

from __future__ import annotations

import os

from sqlalchemy import create_engine
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import sessionmaker

from src.db.models import Base


def _sync_url() -> str:
    url = os.getenv("DATABASE_URL", "")
    # Convert asyncpg URL to psycopg2 for sync usage
    return url.replace("postgresql+asyncpg://", "postgresql+psycopg2://").replace(
        "postgresql://", "postgresql+psycopg2://"
    )


def _async_url() -> str:
    url = os.getenv("DATABASE_URL", "")
    if not url.startswith("postgresql+asyncpg://"):
        url = url.replace("postgresql://", "postgresql+asyncpg://").replace(
            "postgresql+psycopg2://", "postgresql+asyncpg://"
        )
    return url


# --- Sync (CLI) ---

_sync_engine = None
_sync_factory = None


def get_sync_engine():
    global _sync_engine
    if _sync_engine is None:
        _sync_engine = create_engine(_sync_url(), echo=False)
    return _sync_engine


def get_sync_session_factory(engine=None):
    global _sync_factory
    if engine is not None:
        return sessionmaker(bind=engine, expire_on_commit=False)
    if _sync_factory is None:
        _sync_factory = sessionmaker(bind=get_sync_engine(), expire_on_commit=False)
    return _sync_factory


def init_db_sync(engine=None) -> None:
    """Create all tables (dev/CLI convenience). Use Alembic in production."""
    if engine is None:
        engine = get_sync_engine()
    Base.metadata.create_all(engine)


# --- Async (web) ---
# These functions have no callers yet — the web app currently uses sync sessions
# via web/deps.py. Caching is added here preemptively so connection pooling
# works correctly when async routes are wired up.

_async_engine = None
_async_factory = None


def get_async_engine():
    global _async_engine
    if _async_engine is None:
        _async_engine = create_async_engine(_async_url(), echo=False)
    return _async_engine


def get_async_session_factory(engine=None):
    global _async_factory
    if engine is not None:
        return async_sessionmaker(bind=engine, expire_on_commit=False, class_=AsyncSession)
    if _async_factory is None:
        _async_factory = async_sessionmaker(bind=get_async_engine(), expire_on_commit=False, class_=AsyncSession)
    return _async_factory
