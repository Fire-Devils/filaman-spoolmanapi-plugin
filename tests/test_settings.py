import importlib
import sys
from pathlib import Path
from types import ModuleType
from unittest.mock import patch

import pytest
from fastapi import HTTPException, WebSocket
from pydantic import ValidationError
from sqlalchemy import update
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from starlette.requests import HTTPConnection

from spoolmanapi.schemas import SpoolmanAPISettings, SpoolmanAPISettingsUpdate

database = ModuleType("app.core.database")
database.engine = None
database.async_session_maker = None
package = ModuleType("_spoolmanapi_settings_test")
package.__path__ = [str(Path(__file__).resolve().parents[1] / "spoolmanapi")]
with patch.dict(sys.modules, {
    "app": ModuleType("app"), "app.core": ModuleType("app.core"), "app.core.database": database,
    package.__name__: package,
}):
    plugin_settings = importlib.import_module(f"{package.__name__}.settings")
    ip_filter = importlib.import_module(f"{package.__name__}.ip_filter")


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
        conn = HTTPConnection({"type": "http", "client": ("192.0.2.2", 12345)})
        ws = WebSocket({"type": "websocket", "client": ("192.0.2.2", 12345)}, receive=None, send=None)
        with pytest.raises(HTTPException) as denied:
            await ip_filter.require_ip_access(conn)
        assert denied.value.status_code == 403
        assert await ip_filter.check_ws_ip_access(ws) is False

        async with engine.begin() as connection:
            await connection.execute(
                update(plugin_settings._settings_table)
                .where(plugin_settings._settings_table.c.id == 1)
                .values(ip_filter_enabled=True, allowed_ips=["192.0.2.2"])
            )

        assert (await plugin_settings.load_settings()).allowed_ips == ["192.0.2.2"]
        await ip_filter.require_ip_access(conn)
        assert await ip_filter.check_ws_ip_access(ws) is True

        await plugin_settings.save_settings(SpoolmanAPISettings(ip_filter_enabled=False))
        assert (await plugin_settings.load_settings()).ip_filter_enabled is False
    finally:
        await engine.dispose()


@pytest.mark.parametrize("pattern", ["192.0.2.999", "not-an-ip", "192.0.2.1/99"])
def test_invalid_ip_patterns_are_rejected(pattern):
    with pytest.raises(ValidationError):
        SpoolmanAPISettingsUpdate(ip_filter_enabled=True, allowed_ips=[pattern])


@pytest.mark.parametrize("pattern", ["*", "192.0.2.1", "192.0.2.0/24", "2001:db8::/32", " 192.0.2.1 ", " * "])
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
