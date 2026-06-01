"""Database session factories — sync (CLI) and async (web)."""

from __future__ import annotations

import os

from sqlalchemy import create_engine
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import Session, sessionmaker

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

def get_sync_engine():
    return create_engine(_sync_url(), echo=False)


def get_sync_session_factory(engine=None):
    if engine is None:
        engine = get_sync_engine()
    return sessionmaker(bind=engine, expire_on_commit=False)


def init_db_sync(engine=None) -> None:
    """Create all tables (dev/CLI convenience). Use Alembic in production."""
    if engine is None:
        engine = get_sync_engine()
    Base.metadata.create_all(engine)


# --- Async (web) ---

def get_async_engine():
    return create_async_engine(_async_url(), echo=False)


def get_async_session_factory(engine=None):
    if engine is None:
        engine = get_async_engine()
    return async_sessionmaker(bind=engine, expire_on_commit=False, class_=AsyncSession)
