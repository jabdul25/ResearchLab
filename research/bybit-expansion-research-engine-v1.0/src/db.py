from __future__ import annotations

import hashlib
from pathlib import Path

from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine

from src.domain import SchemaProvenance


PROJECT_ROOT = Path(__file__).resolve().parents[1]
BASE_SCHEMA = PROJECT_ROOT / "database" / "schema_v10.sql"
MIGRATIONS = PROJECT_ROOT / "database" / "migrations"


class MigrationChecksumError(RuntimeError):
    pass


def engine_for(database_url: str) -> Engine:
    if database_url.startswith("postgresql://"):
        database_url = database_url.replace(
            "postgresql://", "postgresql+psycopg2://", 1
        )
    return create_engine(database_url, pool_pre_ping=True, future=True)


def _checksum(sql: str) -> str:
    return hashlib.sha256(sql.encode("utf-8")).hexdigest()


def apply_migrations(
    engine: Engine, migration_dir: Path = MIGRATIONS
) -> SchemaProvenance:
    with engine.begin() as connection:
        connection.execute(
            text(
                """
                CREATE TABLE IF NOT EXISTS schema_migrations (
                    version TEXT PRIMARY KEY,
                    checksum TEXT NOT NULL,
                    applied_at TIMESTAMPTZ NOT NULL DEFAULT now()
                )
                """
            )
        )

    for path in sorted(migration_dir.glob("*.sql")):
        version = path.stem
        sql = path.read_text()
        checksum = _checksum(sql)
        with engine.begin() as connection:
            recorded = connection.execute(
                text("SELECT checksum FROM schema_migrations WHERE version=:version"),
                {"version": version},
            ).scalar_one_or_none()
            if recorded is not None:
                if recorded != checksum:
                    raise MigrationChecksumError(
                        f"migration {version} checksum changed: {recorded} != {checksum}"
                    )
                continue
            connection.exec_driver_sql(sql)
            connection.execute(
                text(
                    "INSERT INTO schema_migrations(version,checksum) "
                    "VALUES (:version,:checksum)"
                ),
                {"version": version, "checksum": checksum},
            )
    return read_schema_provenance(engine)


def read_schema_provenance(engine: Engine) -> SchemaProvenance:
    with engine.connect() as connection:
        rows = connection.execute(
            text("SELECT version,checksum FROM schema_migrations ORDER BY version")
        ).all()
    checksums = {row.version: row.checksum for row in rows}
    return SchemaProvenance(
        schema_version=rows[-1].version if rows else "base",
        checksums=checksums,
    )


def initialize(engine: Engine) -> SchemaProvenance:
    with engine.begin() as connection:
        connection.exec_driver_sql(BASE_SCHEMA.read_text())
    return apply_migrations(engine)
