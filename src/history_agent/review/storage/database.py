"""Open existing SQLite files through SQLAlchemy without implicit creation."""

from pathlib import Path

from sqlalchemy import URL, Engine, create_engine
from sqlalchemy.pool import NullPool


def checkpoint_engine(path: Path, *, read_only: bool = False) -> Engine:
    url: URL = URL.create(
        "sqlite+pysqlite",
        database=path.resolve().as_uri(),
        query={"mode": "ro" if read_only else "rw", "uri": "true"},
    )
    return create_engine(
        url,
        poolclass=NullPool,
        connect_args={"timeout": 0.1 if read_only else 5.0},
    )
