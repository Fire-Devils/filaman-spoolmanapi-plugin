import json
from unittest.mock import patch

import pydantic_settings
import pytest
from fastapi import WebSocket
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from starlette.websockets import WebSocketState

_settings_init = pydantic_settings.BaseSettings.__init__


def _without_local_env(self, *args, **kwargs):
    kwargs["_env_file"] = None
    return _settings_init(self, *args, **kwargs)


with patch.object(pydantic_settings.BaseSettings, "__init__", _without_local_env):
    from app.models.base import Base
    from app.models.filament import Filament, Manufacturer
    from app.models.spool import Spool, SpoolEvent, SpoolStatus

    from spoolmanapi import schemas, settings
    from spoolmanapi.service import SpoolmanService
    from spoolmanapi.ws import websocket_manager


@pytest.mark.asyncio
@pytest.mark.parametrize("operation,resource,event_count", [
    ("create_vendor", "vendor", 1), ("update_vendor", "vendor", 1), ("delete_vendor", "vendor", 1),
    ("create_filament", "filament", 1), ("update_filament", "filament", 1), ("delete_filament", "filament", 1),
    ("create_spool", "spool", 2), ("update_spool", "spool", 2), ("delete_spool", "spool", 1),
    ("use_spool", "spool", 1), ("use_length", "spool", 1), ("use_noop", "spool", 1),
    ("measure_spool", "spool", 1), ("measure_without_tara", "spool", 1),
    ("link_tag", "spool", 2), ("unlink_tag", "spool", 1), ("scan_tag", "tag_scan", 1),
])
async def test_mutations_deliver_with_a_single_pooled_connection(
    tmp_path, monkeypatch, operation, resource, event_count,
):
    # A refetch must release the mutation's connection before the fresh access check.
    engine = create_async_engine(
        f"sqlite+aiosqlite:///{tmp_path / 'service.db'}",
        pool_size=1, max_overflow=0, pool_timeout=0.1,
    )
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr(settings, "engine", engine)
    monkeypatch.setattr(settings, "async_session_maker", sessions)
    monkeypatch.setattr(settings, "_table_ensured", False)
    messages = []

    async def receive():
        return {"type": "websocket.connect"}

    async def send(message):
        messages.append(message)

    ws = WebSocket({"type": "websocket", "client": ("192.0.2.2", 50000)}, receive, send)
    try:
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        await settings.save_settings(schemas.SpoolmanAPISettings(
            ip_filter_enabled=True, allowed_ips=["192.0.2.2"],
        ))
        async with sessions() as db:
            db.add_all([
                Manufacturer(id=1, name="Main"), Manufacturer(id=2, name="Unused"),
                Filament(id=1, manufacturer_id=1, designation="PLA", material_type="PLA",
                         diameter_mm=1.75, density_g_cm3=1.24),
                Filament(id=2, manufacturer_id=1, designation="Unused", material_type="PLA", diameter_mm=1.75),
                SpoolStatus(id=1, key="new", label="New"),
                SpoolStatus(id=2, key="opened", label="Opened"),
                SpoolStatus(id=3, key="archived", label="Archived"),
                Spool(id=2, filament_id=1, status_id=1, remaining_weight_g=1000,
                      empty_spool_weight_g=None if operation == "measure_without_tara" else 200,
                      rfid_uid="BB"),
                Spool(id=1, filament_id=1, status_id=1, rfid_uid="AA"),
            ])
            await db.commit()
            await ws.accept()
            websocket_manager.connect((), ws)
            service = SpoolmanService(db)
            calls = {
                "create_vendor": lambda: service.create_vendor(schemas.VendorParameters(name="Added")),
                "update_vendor": lambda: service.update_vendor(1, schemas.VendorUpdateParameters(name="Updated")),
                "delete_vendor": lambda: service.delete_vendor(2),
                "create_filament": lambda: service.create_filament(schemas.FilamentParameters(vendor_id=1, name="Added")),
                "update_filament": lambda: service.update_filament(1, schemas.FilamentUpdateParameters(name="Updated")),
                "delete_filament": lambda: service.delete_filament(2),
                "create_spool": lambda: service.create_spool(schemas.SpoolParameters(
                    filament_id=1, extra={"card_uids": '"AA"'},
                )),
                "update_spool": lambda: service.update_spool(2, schemas.SpoolUpdateParameters(extra={"card_uids": '"AA"'})),
                "delete_spool": lambda: service.delete_spool(2),
                "use_spool": lambda: service.use_spool(2, schemas.SpoolUseParameters(use_weight=10)),
                "use_length": lambda: service.use_spool(2, schemas.SpoolUseParameters(use_length=1000)),
                "use_noop": lambda: service.use_spool(2, schemas.SpoolUseParameters()),
                "measure_spool": lambda: service.measure_spool(2, schemas.SpoolMeasureParameters(weight=1100)),
                "measure_without_tara": lambda: service.measure_spool(2, schemas.SpoolMeasureParameters(weight=1100)),
                "link_tag": lambda: service.link_tag("spool", 2, "AA"),
                "unlink_tag": lambda: service.unlink_tag("spool", 2, "BB"),
                "scan_tag": lambda: service.scan_tag(schemas.TagScanParameters(uid="BB", reader_id="test"), None),
            }
            held_vendor = await db.get(Manufacturer, 1)
            result = await calls[operation]()
            assert result is not None
            assert ws.application_state == WebSocketState.CONNECTED, messages
            events = [json.loads(message["text"]) for message in messages if message["type"] == "websocket.send"]
            assert len(events) == event_count
            assert {event["resource"] for event in events} == {resource}
            # Releasing the connection must not expire loaded objects or discard flushed writes.
            assert held_vendor.name == ("Updated" if operation == "update_vendor" else "Main")

        async with sessions() as db:
            if operation in {"create_spool", "update_spool", "link_tag"}:
                assert await db.scalar(select(Spool.rfid_uid).where(Spool.id == 1)) is None
            if operation in {"measure_spool", "measure_without_tara"}:
                assert await db.scalar(select(SpoolEvent.measured_weight_g).where(SpoolEvent.event_type == "measurement")) == 1100
            if operation == "use_spool":
                assert await db.scalar(select(Spool.remaining_weight_g).where(Spool.id == 2)) == 990
    finally:
        websocket_manager.disconnect((), ws)
        await engine.dispose()


@pytest.mark.asyncio
async def test_broadcast_does_not_hide_a_failed_transaction():
    from sqlalchemy.exc import IntegrityError

    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    try:
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        async with async_sessionmaker(engine, expire_on_commit=False)() as db:
            db.add(Manufacturer(name="Duplicate"))
            await db.commit()
            db.add(Manufacturer(name="Duplicate"))
            with pytest.raises(IntegrityError):
                await SpoolmanService(db)._emit_event("vendor", 1, schemas.EventType.added, {})
    finally:
        await engine.dispose()
