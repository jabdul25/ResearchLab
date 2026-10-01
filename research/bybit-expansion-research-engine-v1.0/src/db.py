from pathlib import Path
from sqlalchemy import create_engine
from sqlalchemy.engine import Engine

def engine_for(database_url: str) -> Engine:
    return create_engine(database_url, pool_pre_ping=True, future=True)

def initialize(engine: Engine) -> None:
    with engine.begin() as connection:
        connection.connection.driver_connection.cursor().execute(Path("database/schema_v10.sql").read_text())
