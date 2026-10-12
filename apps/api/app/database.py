"""Database session management."""

from __future__ import annotations

from collections.abc import Iterator

from sqlalchemy import create_engine, event, text
from sqlalchemy.orm import Session, sessionmaker

from app.config import DATA_DIR, get_settings
from app.errors import AppError
from app.events import bus
from app.models import Base

engine = None
SessionLocal: sessionmaker[Session] | None = None


def init_db(url: str | None = None) -> None:
    global engine, SessionLocal
    settings = get_settings()
    database_url = url or settings.database_url
    if database_url.startswith("sqlite"):
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        connect_args = {"check_same_thread": False}
    else:
        connect_args = {}
    if engine is not None:
        engine.dispose()
    engine = create_engine(database_url, connect_args=connect_args, pool_pre_ping=True)

    @event.listens_for(engine, "connect")
    def _sqlite_fk(dbapi_connection, _record):  # noqa: ANN001
        if engine is not None and engine.dialect.name == "sqlite":
            cursor = dbapi_connection.cursor()
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.close()

    SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False)
    if engine.dialect.name == "postgresql":
        try:
            with engine.begin() as conn:
                conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
        except Exception:
            # JSON embeddings remain available when the extension cannot be created.
            pass
    Base.metadata.create_all(engine)


def get_db() -> Iterator[Session]:
    if SessionLocal is None:
        init_db()
    assert SessionLocal is not None
    db = SessionLocal()
    try:
        yield db
        queued = list(db.info.get("ams_events") or [])
        db.info["ams_events"] = []
        db.commit()
        for item in queued:
            bus.publish(item)
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def commit_and_publish(db: Session) -> None:
    queued = list(db.info.get("ams_events") or [])
    db.info["ams_events"] = []
    db.commit()
    for item in queued:
        bus.publish(item)


def get_in_account(db: Session, model, object_id: str, account_id: str):
    obj = db.get(model, object_id)
    if obj is None or getattr(obj, "account_id", None) != account_id:
        raise AppError("NOT_FOUND", f"{model.__name__} was not found.", 404)
    return obj
