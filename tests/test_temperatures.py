import ast
import csv
import io
import json

import pytest

from test_alpha_hex import color_api, plugin_settings
from app.models.filament import Filament
from app.models.spool import Spool, SpoolStatus


requires_native_temperatures = pytest.mark.skipif(
    not hasattr(Filament, "extruder_temp_range_c"), reason="Core has no native temperature fields",
)


@requires_native_temperatures
@pytest.mark.asyncio
async def test_native_temperatures_in_filament_spool_and_downloads(color_api):
    async with plugin_settings.async_session_maker() as db:
        filament = Filament(
            manufacturer_id=1, designation="PLA", material_type="PLA", diameter_mm=1.75,
            extruder_temp_range_c={"min": 210, "max": 230},
            bed_temp_range_c={"min": None, "max": 65},
            drying_temp_c=50, drying_time_hours=6,
            custom_fields={"article_number": "ARTICLE", "unrelated": False},
        )
        spool = Spool(
            filament=filament, status=SpoolStatus(key="new", label="New"),
            label_field_aliases={"spoolman_extra.dry": "drying_temp_c"},
        )
        db.add(spool)
        await db.commit()
        fid, sid = filament.id, spool.id

    api = "/spoolman/api/v1"
    filament = (await color_api.get(f"{api}/filament/{fid}")).json()
    spool = (await color_api.get(f"{api}/spool/{sid}")).json()
    assert filament["settings_extruder_temp"] == 210
    assert filament["settings_bed_temp"] == 65
    assert filament["article_number"] == "ARTICLE"
    assert filament["extra"] == {"dry_temp": "50.0", "dry_time_hours": "6.0", "unrelated": "false"}
    assert spool["filament"] == filament
    assert spool["extra"]["spoolman_extra"] == '{"dry": "50.0"}'

    for resource, expected in (
        ("filaments", {"settings_extruder_temp": 210, "dry_temp": 50.0}),
        ("spools", {"spoolman_extra": {"dry": "50.0"}}),
    ):
        response = await color_api.get(f"{api}/export/{resource}?fmt=json")
        assert response.status_code == 200, response.text
        assert expected.items() <= response.json()[0]["custom_fields"].items()
        response = await color_api.get(f"{api}/export/{resource}?fmt=csv")
        assert response.status_code == 200, response.text
        [row] = csv.DictReader(io.StringIO(response.text))
        assert expected.items() <= ast.literal_eval(row["custom_fields"]).items()

    response = await color_api.patch(f"{api}/spool/{sid}", json={
        "extra": {"spoolman_extra": '{"dry": "50.0", "rack": "A"}'},
    })
    assert response.status_code == 200, response.text
    async with plugin_settings.async_session_maker() as db:
        saved_spool = await db.get(Spool, sid)
        assert saved_spool.custom_fields == {"spoolman_extra": {"rack": "A"}}
        (await db.get(Filament, fid)).drying_temp_c = 60
        await db.commit()
    spool = (await color_api.get(f"{api}/spool/{sid}")).json()
    assert spool["extra"]["spoolman_extra"] == '{"rack": "A", "dry": "60.0"}'

    edited_alias = await color_api.patch(f"{api}/spool/{sid}", json={
        "extra": {"spoolman_extra": '{"dry": "70.0", "rack": "B"}'},
    })
    assert edited_alias.status_code == 400, edited_alias.text
    assert "spoolman_extra.dry" in edited_alias.json()["message"]
    async with plugin_settings.async_session_maker() as db:
        assert (await db.get(Spool, sid)).custom_fields == {"spoolman_extra": {"rack": "A"}}


@requires_native_temperatures
@pytest.mark.asyncio
async def test_spoolman_temperature_writes_preserve_native_ranges_and_clears(color_api):
    async with plugin_settings.async_session_maker() as db:
        filament = Filament(
            manufacturer_id=1, designation="PLA", material_type="PLA", diameter_mm=1.75,
            extruder_temp_range_c={"min": 210, "max": 230},
            drying_temp_c=50,
            custom_fields={"settings_bed_temp": 55, "note": "keep"},
        )
        db.add(filament)
        await db.commit()
        fid = filament.id

    api = "/spoolman/api/v1/filament"
    read = await color_api.get(f"{api}/{fid}")
    assert read.json()["settings_bed_temp"] == 55
    response = await color_api.patch(f"{api}/{fid}", json={
        "settings_extruder_temp": 210,
        "settings_bed_temp": 0,
        "extra": {"dry_temp": "50.0"},
    })
    assert response.status_code == 200, response.text
    assert response.json()["settings_bed_temp"] == 0
    async with plugin_settings.async_session_maker() as db:
        saved = await db.get(Filament, fid)
        assert saved.extruder_temp_range_c == {"min": 210, "max": 230}
        assert saved.bed_temp_range_c == {"min": 0, "max": 0}
        assert saved.custom_fields == {"note": "keep"}

    response = await color_api.patch(f"{api}/{fid}", json={"settings_extruder_temp": None})
    assert response.status_code == 200, response.text
    assert response.json().get("settings_extruder_temp") is None
    response = await color_api.patch(f"{api}/{fid}", json={"extra": {"article_number": "123"}})
    assert response.status_code == 422
    response = await color_api.patch(f"{api}/{fid}", json={"settings_bed_temp": 10**400})
    assert response.status_code == 422
    response = await color_api.post(api, json={
        "vendor_id": 1, "name": "Invalid", "settings_bed_temp": 10**400,
    })
    assert response.status_code == 422
    async with plugin_settings.async_session_maker() as db:
        assert (await db.get(Filament, fid)).custom_fields == {"note": "keep"}

    response = await color_api.post(api, json={
        "vendor_id": 1, "name": "New", "settings_extruder_temp": 235,
    })
    assert response.status_code == 200, response.text
    async with plugin_settings.async_session_maker() as db:
        cleared = await db.get(Filament, fid)
        created = await db.get(Filament, response.json()["id"])
        assert cleared.extruder_temp_range_c is None
        assert cleared.bed_temp_range_c == {"min": 0, "max": 0}
        assert created.extruder_temp_range_c == {"min": 235, "max": 235}
        assert not created.custom_fields


@requires_native_temperatures
@pytest.mark.asyncio
@pytest.mark.parametrize("retained", [False, True])
async def test_drying_extras_keep_native_and_custom_ownership(color_api, retained):
    from app.services.spoolman_projection import promotion_alias

    alias = promotion_alias("drying_temp_c", 50, 50)
    alias["wire"]["retained"] = True
    async with plugin_settings.async_session_maker() as db:
        filament = Filament(
            manufacturer_id=1, designation="PLA", material_type="PLA", diameter_mm=1.75,
            drying_temp_c=50,
            custom_fields={"dry_temp": 50} if retained else None,
            label_field_aliases={"dry_temp": alias} if retained else None,
        )
        db.add(filament)
        await db.commit()
        fid = filament.id

    url = f"/spoolman/api/v1/filament/{fid}"
    result = await color_api.patch(url, json={"extra": {"dry_temp": "60"}})
    assert result.status_code == 422, result.text
    assert "dry_temp" in result.json()["detail"]
    async with plugin_settings.async_session_maker() as db:
        saved = await db.get(Filament, fid)
        assert saved.custom_fields == ({"dry_temp": 50} if retained else None)
        saved.drying_temp_c = 70
        await db.commit()
    result = await color_api.get(url)
    assert json.loads(result.json()["extra"]["dry_temp"]) == 70

    async with plugin_settings.async_session_maker() as db:
        saved = await db.get(Filament, fid)
        saved.custom_fields = {"dry_temp": 42}
        await db.commit()
    result = await color_api.patch(url, json={"extra": {"dry_temp": "45"}})
    assert result.status_code == 200, result.text
    assert json.loads(result.json()["extra"]["dry_temp"]) == 45


@pytest.mark.skipif(hasattr(Filament, "extruder_temp_range_c"), reason="Requires legacy Core")
@pytest.mark.asyncio
async def test_legacy_temperature_writes_round_trip_through_custom_fields(color_api):
    api = "/spoolman/api/v1/filament"
    response = await color_api.post(api, json={
        "vendor_id": 1, "name": "PLA", "settings_extruder_temp": 215,
    })
    assert response.status_code == 200, response.text
    fid = response.json()["id"]
    assert response.json()["settings_extruder_temp"] == 215

    response = await color_api.patch(f"{api}/{fid}", json={"settings_bed_temp": 65})
    assert response.status_code == 200, response.text
    assert response.json()["settings_bed_temp"] == 65
    async with plugin_settings.async_session_maker() as db:
        assert (await db.get(Filament, fid)).custom_fields == {
            "settings_extruder_temp": 215, "settings_bed_temp": 65,
        }
