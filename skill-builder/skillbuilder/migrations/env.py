"""Alembic environment — drives migrations against SKILLBUILDER_DB.

The URL comes from the same env var the app uses, normalized so a bare path is
treated as a SQLite file (dev) and a full DSN as Postgres (deployed).
"""

from __future__ import annotations

import os

from alembic import context
from sqlalchemy import engine_from_config, pool

from skillbuilder.db import Base, normalize_url

config = context.config

_url = normalize_url(os.environ.get("SKILLBUILDER_DB", "skillbuilder.db"))
config.set_main_option("sqlalchemy.url", _url)

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    context.configure(
        url=_url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    section = config.get_section(config.config_ini_section) or {}
    section["sqlalchemy.url"] = _url
    connectable = engine_from_config(
        section, prefix="sqlalchemy.", poolclass=pool.NullPool
    )
    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            compare_type=True,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
