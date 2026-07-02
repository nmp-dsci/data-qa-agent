"""Alembic environment.

Migrations run as a privileged connection (ADMIN_DATABASE_URL) so they can
create extensions, roles, and schemas, and so tables are owned by the admin
role — which is what makes RLS apply to the non-owner app/agent roles. There is
no ORM metadata: revisions apply raw SQL (the schema is hand-authored), so
autogenerate is intentionally not wired up.
"""

from __future__ import annotations

import os

from alembic import context
from sqlalchemy import engine_from_config, pool

config = context.config


def _database_url() -> str:
    url = os.environ.get("ADMIN_DATABASE_URL")
    if not url:
        raise RuntimeError("ADMIN_DATABASE_URL is required to run migrations")
    # Normalise to the sync psycopg driver Alembic uses.
    if url.startswith("postgresql://"):
        url = url.replace("postgresql://", "postgresql+psycopg://", 1)
    elif url.startswith("postgresql+asyncpg://"):
        url = url.replace("postgresql+asyncpg://", "postgresql+psycopg://", 1)
    return url


def run_migrations_offline() -> None:
    context.configure(
        url=_database_url(),
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    section = config.get_section(config.config_ini_section, {})
    section["sqlalchemy.url"] = _database_url()
    connectable = engine_from_config(section, prefix="sqlalchemy.", poolclass=pool.NullPool)
    with connectable.connect() as connection:
        context.configure(connection=connection)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
