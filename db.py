"""Thin Postgres layer: engine + query/execute/replace + SQL-file runner."""

from pathlib import Path

import pandas as pd
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine

from .config import PG_DATABASE_URL, SCHEMA

_engine: Engine | None = None


def engine() -> Engine:
    global _engine
    if _engine is None:
        _engine = create_engine(PG_DATABASE_URL, pool_pre_ping=True)
    return _engine


def qualified(name: str) -> str:
    return f'"{SCHEMA}"."{name}"'


def query(sql: str, **params) -> pd.DataFrame:
    """Run a SELECT and return a DataFrame."""
    return pd.read_sql(text(sql), engine(), params=params or None)


def execute(sql: str, **params) -> None:
    with engine().begin() as conn:
        conn.execute(text(sql), params or {})


def _ensure_schema(conn) -> None:
    conn.exec_driver_sql(f'CREATE SCHEMA IF NOT EXISTS "{SCHEMA}"')


def run_sql_file(path: str | Path) -> None:
    """Execute a .sql file (may contain multiple statements)."""
    sql = Path(path).read_text(encoding="utf-8")
    with engine().begin() as conn:
        _ensure_schema(conn)
        conn.exec_driver_sql(sql)


def replace(df: pd.DataFrame, table: str) -> int:
    """Drop+reload a table with df (snapshot ingest)."""
    with engine().begin() as conn:
        _ensure_schema(conn)
        df.to_sql(table, conn, schema=SCHEMA, if_exists="replace", index=False)
    return len(df)


def append_aligned(df: pd.DataFrame, table: str) -> int:
    """Append only the df columns that already exist in the target table."""
    cols = set(
        query(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_schema = :s AND table_name = :t",
            s=SCHEMA,
            t=table,
        )["column_name"]
    )
    use = [c for c in df.columns if c in cols]
    with engine().begin() as conn:
        df[use].to_sql(table, conn, schema=SCHEMA, if_exists="append", index=False)
    return len(df)
