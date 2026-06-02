"""FastAPI dependency: sync SQLAlchemy session."""

from collections.abc import Generator

from sqlalchemy.orm import Session

from src.db.database import get_sync_session_factory


def get_db() -> Generator[Session, None, None]:
    """Yield a sync session; close on exit. Used as a FastAPI dependency."""
    factory = get_sync_session_factory()
    with factory() as session:
        yield session
