from __future__ import annotations

from sqlalchemy import create_engine
from sqlalchemy.engine import Engine


def create_db_engine(db_url: str) -> Engine:
    connect_args = {"check_same_thread": False} if db_url.startswith("sqlite:///") else {}
    return create_engine(db_url, future=True, connect_args=connect_args)
