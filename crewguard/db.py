from contextlib import contextmanager

from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

import crewguard.models  # noqa: F401  (register tables)


def make_engine(db_path: str):
    if db_path == ":memory:":
        # SQLite's default SingletonThreadPool hands each thread its own
        # connection, which for :memory: means a separate, empty database.
        # StaticPool + check_same_thread=False share one connection across threads.
        return create_engine(
            "sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False}
        )
    return create_engine(f"sqlite:///{db_path}")


def init_db(engine) -> None:
    SQLModel.metadata.create_all(engine)
    _migrate(engine)


def _migrate(engine) -> None:
    """Additive column migrations — create_all only creates missing TABLES."""
    from sqlalchemy import text

    with engine.connect() as conn:
        for col in ("feedback", "feedback_by"):
            try:
                conn.execute(text(f"ALTER TABLE moderationlog ADD COLUMN {col} VARCHAR"))
                conn.commit()
            except Exception:  # noqa: BLE001, S110 — duplicate column on a re-run
                pass


@contextmanager
def session_scope(engine):
    with Session(engine) as s:
        try:
            yield s
            s.commit()
        except Exception:
            s.rollback()
            raise
