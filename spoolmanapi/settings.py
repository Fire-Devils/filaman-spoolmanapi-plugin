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
    insert,
    select,
    update,
)

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

_cached: SpoolmanAPISettings | None = None
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
        await conn.run_sync(_settings_table.create, checkfirst=True)

    _table_ensured = True


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


async def load_settings() -> SpoolmanAPISettings:
    """Return current settings (cached after first load)."""
    global _cached
    if _cached is not None:
        return _cached

    await _ensure_table()

    async with async_session_maker() as session:
        result = await session.execute(
            select(_settings_table).where(_settings_table.c.id == 1)
        )
        row = result.first()

    if row is not None:
        _cached = SpoolmanAPISettings(
            ip_filter_enabled=row.ip_filter_enabled,
            allowed_ips=row.allowed_ips or [],
        )
    else:
        _cached = SpoolmanAPISettings()

    return _cached


async def save_settings(settings: SpoolmanAPISettings) -> None:
    """Persist *settings* to the database and update the module cache."""
    global _cached

    await _ensure_table()

    async with async_session_maker() as session:
        result = await session.execute(
            select(_settings_table.c.id).where(_settings_table.c.id == 1)
        )
        exists = result.first() is not None

        if exists:
            await session.execute(
                update(_settings_table)
                .where(_settings_table.c.id == 1)
                .values(
                    ip_filter_enabled=settings.ip_filter_enabled,
                    allowed_ips=settings.allowed_ips,
                )
            )
        else:
            await session.execute(
                insert(_settings_table).values(
                    id=1,
                    ip_filter_enabled=settings.ip_filter_enabled,
                    allowed_ips=settings.allowed_ips,
                )
            )

        await session.commit()

    _cached = settings
    _logger.info("SpoolmanAPI-Settings in Datenbank gespeichert")
