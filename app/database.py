"""Motor SQLite (modo WAL) y sesiones de SQLAlchemy 2.0."""
from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.config import get_settings


class Base(DeclarativeBase):
    pass


def _ensure_sqlite_dir(url: str) -> None:
    prefix = "sqlite:///"
    if url.startswith(prefix) and ":memory:" not in url:
        Path(url[len(prefix):]).parent.mkdir(parents=True, exist_ok=True)


def build_engine(url: str) -> Engine:
    _ensure_sqlite_dir(url)
    engine = create_engine(url, connect_args={"check_same_thread": False})

    @event.listens_for(engine, "connect")
    def _sqlite_pragmas(dbapi_conn, _record):  # pragma: no cover - trivial
        cur = dbapi_conn.cursor()
        cur.execute("PRAGMA journal_mode=WAL")
        cur.execute("PRAGMA synchronous=NORMAL")
        cur.execute("PRAGMA foreign_keys=ON")
        cur.execute("PRAGMA busy_timeout=5000")
        cur.close()

    return engine


engine = build_engine(get_settings().database_url)
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


def get_db() -> Iterator[Session]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def init_db() -> None:
    from app import models  # noqa: F401  (registra las tablas)

    Base.metadata.create_all(engine)
    _upgrade_schema()


def _upgrade_schema() -> None:
    """Migraciones ligeras para bases de datos creadas con versiones anteriores."""
    with engine.begin() as conn:
        cols = {row[1] for row in conn.exec_driver_sql("PRAGMA table_info(transactions)")}
        if "period_year" not in cols:
            # Mes contable inicial = mes natural de la fecha de operación.
            conn.exec_driver_sql("ALTER TABLE transactions ADD COLUMN period_year INTEGER")
            conn.exec_driver_sql("ALTER TABLE transactions ADD COLUMN period_month INTEGER")
            conn.exec_driver_sql(
                "UPDATE transactions SET period_year = CAST(strftime('%Y', date) AS INTEGER), "
                "period_month = CAST(strftime('%m', date) AS INTEGER)"
            )
            conn.exec_driver_sql("CREATE INDEX IF NOT EXISTS ix_transactions_period_year ON transactions (period_year)")
            conn.exec_driver_sql("CREATE INDEX IF NOT EXISTS ix_transactions_period_month ON transactions (period_month)")
        # Columnas nullable añadidas después: basta con crearlas.
        for table, column, ddl in (
            ("transactions", "savings_account_id", "INTEGER REFERENCES savings_accounts(id) ON DELETE SET NULL"),
            ("categories", "savings_account_id", "INTEGER REFERENCES savings_accounts(id) ON DELETE SET NULL"),
        ):
            existing = {row[1] for row in conn.exec_driver_sql(f"PRAGMA table_info({table})")}
            if existing and column not in existing:
                conn.exec_driver_sql(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}")
