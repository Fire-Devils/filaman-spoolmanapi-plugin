import pytest
from sqlalchemy import func, select

from test_alpha_hex import color_api, plugin_settings
from app.models.filament import Filament, Manufacturer
from app.models.location import Location
from app.models.spool import Spool, SpoolEvent, SpoolStatus
from spoolmanapi.service import SpoolmanService


API = "/spoolman/api/v1"


async def add_spool(*, diameter=1.75, density=1.24):
    async with plugin_settings.async_session_maker() as db:
        first = Filament(
            manufacturer_id=1, designation="First", material_type="PLA",
            diameter_mm=diameter, density_g_cm3=density,
        )
        second = Filament(
            manufacturer=Manufacturer(name="Second vendor"), designation="Second",
            material_type="PETG", diameter_mm=1.75,
        )
        spool = Spool(
            filament=first, status=SpoolStatus(key="new", label="New"),
            location=Location(name="Old rack"), remaining_weight_g=1000,
        )
        db.add_all([spool, second, SpoolStatus(key="archived", label="Archived")])
        await db.commit()
        return spool.id, second.id, second.manufacturer_id


async def assert_inventory_untouched(spool_id):
    async with plugin_settings.async_session_maker() as db:
        assert (await db.get(Spool, spool_id)).remaining_weight_g == 1000
        assert await db.scalar(select(func.count()).select_from(SpoolEvent)) == 0


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "method,route,payload",
    [
        ("PUT", "/spool/{id}/use", {"use_weight": "NaN"}),
        ("PUT", "/spool/{id}/use", {"use_length": "Infinity"}),
        ("PUT", "/spool/{id}/measure", {"weight": "-Infinity"}),
        ("PATCH", "/spool/{id}", {"remaining_weight": "NaN"}),
        ("POST", "/spool", {"filament_id": 1, "initial_weight": "Infinity"}),
    ],
)
async def test_nonfinite_inventory_inputs_leave_spool_untouched(color_api, method, route, payload):
    spool_id, _, _ = await add_spool()
    response = await color_api.request(method, API + route.format(id=spool_id), json=payload)
    assert response.status_code == 422, response.text
    await assert_inventory_untouched(spool_id)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "resource,key,value,status_code",
    [
        ("vendor", "custom", '{"amount": 1e9999}', 422),
        ("vendor", "external_id", "123", 422),
        ("filament", "custom", "[NaN]", 422),
        ("spool", "custom", "-Infinity", 400),
        ("spool", "comment", "{}", 400),
    ],
)
async def test_invalid_json_extras_cannot_be_saved(color_api, resource, key, value, status_code):
    spool_id, _, _ = await add_spool()
    model, record_id = {
        "vendor": (Manufacturer, 1), "filament": (Filament, 1), "spool": (Spool, spool_id),
    }[resource]
    response = await color_api.patch(f"{API}/{resource}/{record_id}", json={"extra": {key: value}})
    assert response.status_code == status_code, response.text
    async with plugin_settings.async_session_maker() as db:
        assert (await db.get(model, record_id)).custom_fields is None


@pytest.mark.asyncio
@pytest.mark.parametrize("diameter,weight", [(1.75, 1e308), (1e-200, 900.0), (1e200, 900.0)])
async def test_unrepresentable_length_does_not_break_spool_writes_or_reads(color_api, diameter, weight):
    spool_id, _, _ = await add_spool(diameter=diameter)
    url = f"{API}/spool/{spool_id}"
    response = await color_api.patch(url, json={"remaining_weight": weight})
    assert response.status_code == 200, response.text
    result = await color_api.get(url)
    assert result.status_code == 200, result.text
    assert result.json()["remaining_weight"] == weight
    assert "remaining_length" not in result.json()


@pytest.mark.asyncio
async def test_unrepresentable_used_weight_does_not_break_spool_response(color_api):
    spool_id, _, _ = await add_spool()
    response = await color_api.patch(f"{API}/spool/{spool_id}", json={
        "initial_weight": 1e308, "spool_weight": 0, "remaining_weight": -1e308,
    })
    assert response.status_code == 200, response.text
    assert response.json()["remaining_weight"] == -1e308
    assert "used_weight" not in response.json()
    assert "used_length" not in response.json()


@pytest.mark.asyncio
async def test_consumption_overflow_cannot_replace_a_finite_ledger_entry(color_api):
    spool_id, _, _ = await add_spool()
    url = f"{API}/spool/{spool_id}/use"
    first = await color_api.put(url, json={"use_weight": 1e308})
    assert first.status_code == 200, first.text
    second = await color_api.put(url, json={"use_weight": 1e308})
    assert second.status_code == 422, second.text
    async with plugin_settings.async_session_maker() as db:
        deltas = await db.scalars(select(SpoolEvent.delta_weight_g).where(
            SpoolEvent.spool_id == spool_id, SpoolEvent.event_type == "print_consumption",
        ))
        assert list(deltas) == [-1e308]


@pytest.mark.asyncio
async def test_measurement_overflow_leaves_inventory_untouched(color_api):
    spool_id, _, _ = await add_spool()
    async with plugin_settings.async_session_maker() as db:
        (await db.get(Spool, spool_id)).empty_spool_weight_g = -1e308
        await db.commit()
    response = await color_api.put(f"{API}/spool/{spool_id}/measure", json={"weight": 1e308})
    assert response.status_code == 422, response.text
    await assert_inventory_untouched(spool_id)


@pytest.mark.asyncio
async def test_spool_patch_can_repair_a_preexisting_nonfinite_weight(color_api):
    spool_id, _, _ = await add_spool()
    async with plugin_settings.async_session_maker() as db:
        (await db.get(Spool, spool_id)).remaining_weight_g = float("inf")
        await db.commit()
    response = await color_api.patch(f"{API}/spool/{spool_id}", json={"remaining_weight": 950})
    assert response.status_code == 200, response.text
    assert response.json()["remaining_weight"] == 950
    async with plugin_settings.async_session_maker() as db:
        event = await db.scalar(select(SpoolEvent).where(SpoolEvent.spool_id == spool_id))
        assert event.delta_weight_g is None
        assert event.meta["before"]["remaining_weight_g"] is None


@pytest.mark.asyncio
@pytest.mark.parametrize("value", ["NaN", "1e999"])
async def test_nonfinite_json_request_number_returns_serializable_validation_error(color_api, value):
    spool_id, _, _ = await add_spool()
    response = await color_api.put(
        f"{API}/spool/{spool_id}/use",
        content=f'{{"use_weight":{value}}}', headers={"Content-Type": "application/json"},
    )
    assert response.status_code == 422, response.text
    assert response.json()["detail"][0]["loc"] == ["body", "use_weight"]
    await assert_inventory_untouched(spool_id)


@pytest.mark.asyncio
@pytest.mark.parametrize("operation,payload", [("use", {"use_weight": 1000}), ("measure", {"weight": 200})])
async def test_spool_operation_response_uses_saved_status(color_api, operation, payload):
    spool_id, _, _ = await add_spool()
    async with plugin_settings.async_session_maker() as db:
        spool = await db.get(Spool, spool_id)
        spool.status_id = await db.scalar(select(SpoolStatus.id).where(SpoolStatus.key == "archived"))
        spool.empty_spool_weight_g = 200
        db.add(SpoolStatus(key="empty", label="Empty"))
        await db.commit()
    url = f"{API}/spool/{spool_id}"
    response = await color_api.put(f"{url}/{operation}", json=payload)
    assert response.status_code == 200, response.text
    assert response.json()["archived"] is False
    assert response.json()["archived"] == (await color_api.get(url)).json()["archived"]


@pytest.mark.asyncio
@pytest.mark.parametrize("diameter,density", [(1.75, None), (-1, 1.24)])
async def test_length_requires_usable_filament_dimensions(color_api, diameter, density):
    spool_id, _, _ = await add_spool(diameter=diameter, density=density)
    response = await color_api.put(f"{API}/spool/{spool_id}/use", json={"use_length": 500})
    assert response.status_code == 422, response.text
    assert "use_weight" in response.text
    await assert_inventory_untouched(spool_id)


@pytest.mark.asyncio
async def test_spool_weight_calculation_cannot_overflow(color_api):
    spool_id, _, _ = await add_spool()
    response = await color_api.patch(
        f"{API}/spool/{spool_id}", json={"initial_weight": 1e308, "spool_weight": 1e308},
    )
    assert response.status_code == 400, response.text
    async with plugin_settings.async_session_maker() as db:
        spool = await db.get(Spool, spool_id)
        assert spool.initial_total_weight_g is None
        assert spool.empty_spool_weight_g is None
    await assert_inventory_untouched(spool_id)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "method,route,payload",
    [
        ("POST", "/backup", None),
        ("POST", "/setting/currency?body=USD", None),
        ("POST", "/field/filament/audit_key", {"name": "Audit field"}),
        ("DELETE", "/field/filament/audit_key", None),
    ],
)
async def test_unsupported_writes_report_failure(color_api, method, route, payload):
    response = await color_api.request(method, API + route, json=payload)
    assert response.status_code == 501, response.text


@pytest.mark.asyncio
async def test_patch_responses_and_events_use_saved_relationships(color_api, monkeypatch):
    spool_id, filament_id, vendor_id = await add_spool()
    events = []

    async def capture_event(self, resource, resource_id, event_type, payload):
        events.append((resource, resource_id, payload))

    monkeypatch.setattr(SpoolmanService, "_emit_event", capture_event)
    spool_response = await color_api.patch(
        f"{API}/spool/{spool_id}",
        json={"filament_id": filament_id, "archived": True, "location": "New rack"},
    )
    assert spool_response.status_code == 200, spool_response.text
    assert spool_response.json()["filament"]["id"] == filament_id
    assert spool_response.json()["archived"] is True
    assert spool_response.json()["location"] == "New rack"
    resource, resource_id, payload = events.pop()
    assert (resource, resource_id) == ("spool", spool_id)
    assert (payload.filament.id, payload.archived, payload.location) == (filament_id, True, "New rack")

    filament_response = await color_api.patch(f"{API}/filament/1", json={"vendor_id": vendor_id})
    assert filament_response.status_code == 200, filament_response.text
    assert filament_response.json()["vendor"]["id"] == vendor_id
    resource, resource_id, payload = events.pop()
    assert (resource, resource_id) == ("filament", 1)
    assert payload.vendor.id == vendor_id
