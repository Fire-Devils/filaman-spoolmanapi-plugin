import json
from unittest.mock import patch

import pydantic_settings
import pytest
import pytest_asyncio
from sqlalchemy import event, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

_settings_init = pydantic_settings.BaseSettings.__init__


def _without_local_env(self, *args, **kwargs):
    kwargs["_env_file"] = None
    return _settings_init(self, *args, **kwargs)


with patch.object(pydantic_settings.BaseSettings, "__init__", _without_local_env):
    from app.models.base import Base
    from app.models.filament import Filament, Manufacturer
    from app.models.spool import Spool, SpoolStatus

    from spoolmanapi import schemas
    from spoolmanapi.service import SpoolmanService


@pytest_asyncio.fixture
async def sessions():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        async with factory() as db:
            db.add_all([
                Manufacturer(id=1, name="Test"),
                Filament(id=1, manufacturer_id=1, designation="PLA", material_type="PLA",
                         diameter_mm=1.75, density_g_cm3=1.24),
                SpoolStatus(id=1, key="new", label="New"),
            ])
            await db.commit()
        yield factory
    finally:
        await engine.dispose()


def spool(spool_id, tags):
    return Spool(id=spool_id, filament_id=1, status_id=1,
                 rfid_uid=tags[0] if tags else None, rfid_uid_2=tags[1] if len(tags) > 1 else None)


async def stored_tags(db, spool_id):
    row = (await db.execute(select(Spool.rfid_uid, Spool.rfid_uid_2).where(Spool.id == spool_id))).one()
    return [uid for uid in row if uid is not None]


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["link_tag", "update_spool"])
@pytest.mark.parametrize("target_id,owner_id", [(1, 2), (2, 1)])
@pytest.mark.parametrize("target_tags", [[], ["BB"]])
@pytest.mark.parametrize("owner_tags", [["AA"], ["AA", "CC"], ["CC", "AA"]])
async def test_transfer_releases_owner_before_assigning_target(
    sessions, operation, target_id, owner_id, target_tags, owner_tags,
):
    async with sessions() as db:
        db.add_all([spool(target_id, target_tags), spool(owner_id, owner_tags)])
        await db.commit()
        service = SpoolmanService(db)
        if operation == "link_tag":
            tag = await service.link_tag("spool", target_id, "AA")
            assert tag.uid == "AA"
        else:
            result = await service.update_spool(target_id, schemas.SpoolUpdateParameters(
                extra={"card_uids": json.dumps(", ".join([*target_tags, "AA"]))},
            ))
            assert [tag.uid for tag in result.tags] == [*target_tags, "AA"]
    async with sessions() as db:
        assert await stored_tags(db, target_id) == [*target_tags, "AA"]
        assert await stored_tags(db, owner_id) == (["CC"] if "CC" in owner_tags else [])


@pytest.mark.asyncio
async def test_create_spool_transfers_both_tags_and_keeps_other_owner_tags(sessions):
    async with sessions() as db:
        db.add_all([spool(1, ["AA", "CC"]), spool(2, ["DD", "BB"])])
        await db.commit()
        result = await SpoolmanService(db).create_spool(schemas.SpoolParameters(
            filament_id=1, extra={"card_uids": json.dumps("AA, BB")},
        ))
        target_id = result.id
        assert [tag.uid for tag in result.tags] == ["AA", "BB"]
    async with sessions() as db:
        assert await stored_tags(db, target_id) == ["AA", "BB"]
        assert await stored_tags(db, 1) == ["CC"]
        assert await stored_tags(db, 2) == ["DD"]


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["link_tag", "update_spool", "create_spool"])
async def test_failed_transfer_rolls_back_owner_release(sessions, operation):
    async with sessions() as db:
        db.add_all([spool(1, []), spool(2, ["AA", "CC"])])
        await db.commit()

        # Fail after old-owner updates have flushed, at the target assignment.
        def reject_assignment(connection, cursor, statement, parameters, context, executemany):
            if statement.startswith(("UPDATE spools SET rfid_uid=", "INSERT INTO spools")) and "AA" in parameters:
                raise IntegrityError(statement, parameters, ValueError("target assignment rejected"))

        event.listen(db.bind.sync_engine, "before_cursor_execute", reject_assignment)
        try:
            service = SpoolmanService(db)
            with pytest.raises(IntegrityError, match="target assignment rejected"):
                if operation == "link_tag":
                    await service.link_tag("spool", 1, "AA")
                elif operation == "update_spool":
                    await service.update_spool(1, schemas.SpoolUpdateParameters(extra={"card_uids": '"AA"'}))
                else:
                    await service.create_spool(schemas.SpoolParameters(filament_id=1, extra={"card_uids": '"AA"'}))
            await db.rollback()
        finally:
            event.remove(db.bind.sync_engine, "before_cursor_execute", reject_assignment)
    async with sessions() as db:
        assert await stored_tags(db, 1) == []
        assert await stored_tags(db, 2) == ["AA", "CC"]
        assert await db.scalar(select(Spool.id).where(Spool.id > 2)) is None
