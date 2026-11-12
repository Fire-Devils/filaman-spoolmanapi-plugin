"""Persistent settings for the SpoolmanAPI plugin.

Settings are stored in a dedicated database table (``spoolmanapi_settings``)
so they survive plugin ZIP updates.  The table is created lazily on first
access via ``CREATE TABLE IF NOT EXISTS``.
"""

from __future__ import annotations

import logging

from sqlalchemy import (
    Boolean,
    Column,
    Integer,
    JSON,
    MetaData,
    Table,
    inspect,
    select,
    text,
)
from sqlalchemy.dialects import mysql, postgresql, sqlite
from sqlalchemy.schema import CreateTable

from app.core.database import async_session_maker, engine

from .schemas import SpoolmanAPISettings

_logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Table definition (plugin-private MetaData to avoid Alembic interference)
# ---------------------------------------------------------------------------

_metadata = MetaData()

_settings_table = Table(
    "spoolmanapi_settings",
    _metadata,
    Column("id", Integer, primary_key=True),
    Column("ip_filter_enabled", Boolean, nullable=False),
    Column("allowed_ips", JSON, nullable=False),
)

# ---------------------------------------------------------------------------
# Internal state
# ---------------------------------------------------------------------------

_table_ensured: bool = False


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _ensure_table() -> None:
    """Create the settings table if it does not exist yet."""
    global _table_ensured
    if _table_ensured:
        return

    async with engine.begin() as conn:
        if not await conn.run_sync(lambda connection: inspect(connection).has_table(_settings_table.name)):
            if conn.dialect.name == "postgresql":
                # PostgreSQL can still race on catalog entries during concurrent DDL.
                await conn.execute(text("SELECT pg_advisory_xact_lock(hashtext('spoolmanapi_settings'))"))
            await conn.execute(CreateTable(_settings_table, if_not_exists=True))

    _table_ensured = True


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


async def load_settings() -> SpoolmanAPISettings:
    """Read current settings so every worker sees changes immediately."""

    await _ensure_table()

    async with async_session_maker() as session:
        result = await session.execute(
            select(_settings_table).where(_settings_table.c.id == 1)
        )
        row = result.first()

    if row is not None:
        return SpoolmanAPISettings(
            ip_filter_enabled=row.ip_filter_enabled,
            allowed_ips=row.allowed_ips or [],
        )
    return SpoolmanAPISettings()


async def save_settings(settings: SpoolmanAPISettings) -> None:
    """Persist *settings* to the database."""

    await _ensure_table()

    values = {"ip_filter_enabled": settings.ip_filter_enabled, "allowed_ips": settings.allowed_ips}
    dialect = engine.dialect.name
    if dialect in {"mysql", "mariadb"}:
        statement = mysql.insert(_settings_table).values(id=1, **values).on_duplicate_key_update(**values)
    else:
        insert = postgresql.insert if dialect == "postgresql" else sqlite.insert
        statement = insert(_settings_table).values(id=1, **values).on_conflict_do_update(
            index_elements=[_settings_table.c.id], set_=values,
        )

    async with async_session_maker() as session:
        await session.execute(statement)
        await session.commit()

    _logger.info("SpoolmanAPI-Settings in Datenbank gespeichert")
