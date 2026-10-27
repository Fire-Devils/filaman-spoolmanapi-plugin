import importlib
import sys
from types import ModuleType
from unittest.mock import patch

import pytest
from pydantic import ValidationError
from sqlalchemy import update
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from spoolmanapi.schemas import SpoolmanAPISettings, SpoolmanAPISettingsUpdate


database = ModuleType("app.core.database")
database.engine = None
database.async_session_maker = None
with patch.dict(sys.modules, {"app": ModuleType("app"), "app.core": ModuleType("app.core"), "app.core.database": database}):
    plugin_settings = importlib.import_module("spoolmanapi.settings")


@pytest.mark.asyncio
async def test_settings_changes_from_another_worker_are_visible(tmp_path, monkeypatch):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'settings.db'}")
    monkeypatch.setattr(plugin_settings, "engine", engine)
    monkeypatch.setattr(plugin_settings, "async_session_maker", async_sessionmaker(engine))
    monkeypatch.setattr(plugin_settings, "_table_ensured", False)
    try:
        await plugin_settings.save_settings(
            SpoolmanAPISettings(ip_filter_enabled=True, allowed_ips=["192.0.2.1"])
        )
        assert (await plugin_settings.load_settings()).ip_filter_enabled is True

        async with engine.begin() as connection:
            await connection.execute(
                update(plugin_settings._settings_table)
                .where(plugin_settings._settings_table.c.id == 1)
                .values(ip_filter_enabled=False, allowed_ips=[])
            )

        assert (await plugin_settings.load_settings()).ip_filter_enabled is False
    finally:
        await engine.dispose()


@pytest.mark.parametrize("pattern", ["192.0.2.999", "not-an-ip", "192.0.2.1/99"])
def test_invalid_ip_patterns_are_rejected(pattern):
    with pytest.raises(ValidationError):
        SpoolmanAPISettingsUpdate(ip_filter_enabled=True, allowed_ips=[pattern])


@pytest.mark.parametrize("pattern", ["*", "192.0.2.1", "192.0.2.0/24", "2001:db8::/32"])
def test_valid_ip_patterns_are_accepted(pattern):
    assert SpoolmanAPISettingsUpdate(ip_filter_enabled=True, allowed_ips=[pattern]).allowed_ips == [pattern]


def test_existing_invalid_ip_pattern_can_still_be_read():
    assert SpoolmanAPISettings(allowed_ips=["192.0.2.999"]).allowed_ips == ["192.0.2.999"]


def test_filter_can_be_disabled_with_existing_invalid_ip_pattern():
    settings = SpoolmanAPISettings(ip_filter_enabled=True, allowed_ips=["192.0.2.999"])
    disabled = SpoolmanAPISettingsUpdate.model_validate(
        {**settings.model_dump(), "ip_filter_enabled": False}
    )
    assert disabled.ip_filter_enabled is False
    assert disabled.allowed_ips == settings.allowed_ips
