"""
Database connection setup.

Points at a local SQLite file by default (see README for why: no server
to install, no cost, the file itself is what gets committed back to the
repo between GitHub Actions runs so state persists). Swapping to Postgres
later is a one-line change to DB_URL - models.py has no SQLite-only types.
"""

from __future__ import annotations

import os
from pathlib import Path
from contextlib import contextmanager

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from crawler.models import Base

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DB_PATH = REPO_ROOT / "data" / "trivia.db"

DB_URL = os.environ.get("TRIVIA_DB_URL", f"sqlite:///{DEFAULT_DB_PATH}")

# check_same_thread=False is only needed/safe for SQLite; harmless no-op
# to omit for other backends, so gate it explicitly.
connect_args = {"check_same_thread": False} if DB_URL.startswith("sqlite") else {}
engine = create_engine(DB_URL, connect_args=connect_args)
SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)


def init_db() -> None:
    """Create all tables that don't already exist. Safe to call every run."""
    DEFAULT_DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    Base.metadata.create_all(engine)


@contextmanager
def get_session() -> Session:
    session = SessionLocal()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
