"""Engine, session factory, and the migration runner.

Requirement S4 asks for the schema to be applied as **migrations**, not
``Base.metadata.create_all``. Numbered .sql files in migrations/ are applied in
order and recorded in ``schema_migrations``, so re-running is a no-op and the
schema history is reviewable in git.
"""

from __future__ import annotations

import logging
from collections.abc import Iterator
from pathlib import Path

from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import get_settings

log = logging.getLogger(__name__)

MIGRATIONS_DIR = Path(__file__).resolve().parent.parent / "migrations"


def _make_engine():
    settings = get_settings()
    url = settings.database_url
    kwargs: dict = {"future": True, "pool_pre_ping": True}

    if url.startswith("sqlite"):
        # check_same_thread=False lets the background job thread share the engine.
        kwargs["connect_args"] = {"check_same_thread": False}
        db_path = url.split("///", 1)[-1]
        if db_path and db_path != ":memory:":
            Path(db_path).parent.mkdir(parents=True, exist_ok=True)

    engine = create_engine(url, **kwargs)

    if url.startswith("sqlite"):
        # SQLite ignores foreign keys unless told otherwise, and tenant isolation
        # depends on them being real.
        from sqlalchemy import event

        @event.listens_for(engine, "connect")
        def _sqlite_pragmas(dbapi_conn, _record):  # pragma: no cover - trivial
            cur = dbapi_conn.cursor()
            # SQLite ignores foreign keys unless asked, and tenant isolation
            # depends on them being real.
            cur.execute("PRAGMA foreign_keys=ON")
            # The metering lock serialises writers per tenant. Without a busy
            # timeout the loser of that race fails with "database is locked"
            # instead of waiting its turn, which would surface as a 500 on a
            # request that is merely contended.
            cur.execute("PRAGMA busy_timeout=10000")
            cur.close()

    return engine


engine = _make_engine()
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


def get_session() -> Iterator[Session]:
    """FastAPI dependency: one session per request, always closed."""
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()


def _split_statements(sql: str) -> list[str]:
    """Split a migration file into statements.

    Line comments are stripped first: a ``--`` comment may legitimately contain a
    semicolon, and splitting on ';' before removing them would cut a statement in
    half. Only whole-line and trailing comments are handled, which is all these
    migrations use.
    """
    cleaned_lines = []
    for line in sql.splitlines():
        stripped = line.split("--", 1)[0].rstrip()
        if stripped:
            cleaned_lines.append(stripped)
    cleaned = "\n".join(cleaned_lines)
    return [s.strip() for s in cleaned.split(";") if s.strip()]


def run_migrations() -> list[str]:
    """Apply every pending migration in filename order. Returns those applied."""
    applied: list[str] = []
    with engine.begin() as conn:
        conn.execute(
            text(
                "CREATE TABLE IF NOT EXISTS schema_migrations ("
                "  version TEXT PRIMARY KEY,"
                "  applied_at TEXT NOT NULL"
                ")"
            )
        )
        done = {
            row[0] for row in conn.execute(text("SELECT version FROM schema_migrations"))
        }

        for path in sorted(MIGRATIONS_DIR.glob("*.sql")):
            version = path.stem
            if version in done:
                continue
            log.info("applying migration %s", version)
            for statement in _split_statements(path.read_text(encoding="utf-8")):
                conn.execute(text(statement))
            conn.execute(
                text(
                    "INSERT INTO schema_migrations (version, applied_at) "
                    "VALUES (:v, CURRENT_TIMESTAMP)"
                ),
                {"v": version},
            )
            applied.append(version)

    return applied
