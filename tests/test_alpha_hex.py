from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import patch

import pydantic_settings
import pytest
import pytest_asyncio
from fastapi import FastAPI, Request
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

_settings_init = pydantic_settings.BaseSettings.__init__


def _without_local_env(self, *args, **kwargs):
    kwargs["_env_file"] = None
    return _settings_init(self, *args, **kwargs)


with patch.object(pydantic_settings.BaseSettings, "__init__", _without_local_env):
    from app.api.deps import get_db
    from app.api.v1.filaments import router_colors, router_filaments
    from app.core.security import Principal
    from app.models.base import Base
    from app.models.filament import Color, Filament, FilamentColor, Manufacturer
    from app.models.spool import Spool, SpoolStatus

    from spoolmanapi import settings as plugin_settings
    from spoolmanapi.router import router as plugin_router
    from spoolmanapi.service import SpoolmanService


@pytest.mark.parametrize(
    ("stored", "expected"),
    [
        ("#BE000022", "BE0000"),
        ("be000022", "be0000"),
        ("#FF0000", "FF0000"),
        ("#NOTHEXZZ", "NOTHEXZZ"),
    ],
)
def test_spoolman_rgb_output_preserves_legacy_non_hex(stored, expected):
    assert SpoolmanService._to_rgb_hex(stored) == expected


@pytest.mark.parametrize(
    ("color_mode", "expected_multi"),
    [("multi", "BE0000,00D4D4"), ("single", "00D4D4")],
)
def test_filament_response_uses_rgb_for_primary_and_multicolor(color_mode, expected_multi):
    def color(position, hex_code):
        return SimpleNamespace(position=position, color=SimpleNamespace(hex_code=hex_code))

    filament = SimpleNamespace(
        id=1, created_at=datetime.now(UTC), designation="Test", manufacturer=None,
        material_type="PLA", price=None, density_g_cm3=1.24, diameter_mm=1.75,
        raw_material_weight_g=None, default_spool_weight_g=None, multi_color_style=None,
        color_mode=color_mode, custom_fields={},
        filament_colors=[color(1, "#BE000022"), color(2, "#00D4D488")],
    )

    response = SpoolmanService(None)._filament_to_schema(filament)

    assert response.color_hex == "BE0000"
    assert response.multi_color_hexes == expected_multi


@pytest.mark.asyncio
async def test_rgb_filter_finds_filament_with_stored_alpha():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)

    async with async_sessionmaker(engine)() as db:
        filament = Filament(
            manufacturer=Manufacturer(name="Test"), designation="Red", material_type="PLA",
            diameter_mm=1.75,
            filament_colors=[FilamentColor(position=1, color=Color(name="Red", hex_code="#BE000022"))],
        )
        db.add(filament)
        await db.commit()

        filaments, total = await SpoolmanService(db).list_filaments(color_hex="BE0000")
        assert total == 1
        assert [item.color_hex for item in filaments] == ["BE0000"]

        _, exact_total = await SpoolmanService(db).list_filaments(color_hex="#BE000022")
        assert exact_total == 1

        _, different_alpha_total = await SpoolmanService(db).list_filaments(color_hex="#BE000088")
        assert different_alpha_total == 0

        stored = await db.scalar(select(Color.hex_code))
        assert stored == "#BE000022"

        db.add(Spool(filament=filament, status=SpoolStatus(key="new", label="New")))
        await db.commit()
        spools, total = await SpoolmanService(db).list_spools()
        assert total == 1
        assert spools[0].filament.color_hex == "BE0000"

        filament.filament_colors.append(
            FilamentColor(position=2, color=Color(name="Red translucent", hex_code="#BE000088"))
        )
        db.add(Filament(
            manufacturer=filament.manufacturer, designation="Other red", material_type="PLA", diameter_mm=1.75,
            filament_colors=[FilamentColor(position=1, color=Color(name="Red opaque", hex_code="#BE0000FF"))],
        ))
        await db.commit()
        for offset, expected_name in enumerate(["Red", "Other red"]):
            page, total = await SpoolmanService(db).list_filaments(color_hex="BE0000", limit=1, offset=offset)
            assert total == 2
            assert [item.name for item in page] == [expected_name]

    await engine.dispose()


@pytest_asyncio.fixture
async def color_api(monkeypatch):
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    monkeypatch.setattr(plugin_settings, "engine", engine)
    monkeypatch.setattr(plugin_settings, "async_session_maker", sessions)
    monkeypatch.setattr(plugin_settings, "_table_ensured", False)
    async with sessions() as db:
        db.add(Manufacturer(id=1, name="Roundtrip"))
        await db.commit()

    app = FastAPI()

    async def get_test_db():
        async with sessions() as db:
            yield db

    @app.middleware("http")
    async def authorize(request: Request, call_next):
        request.state.principal = Principal(auth_type="session", is_superadmin=True)
        return await call_next(request)

    app.dependency_overrides[get_db] = get_test_db
    app.include_router(router_colors, prefix="/api/v1")
    app.include_router(router_filaments, prefix="/api/v1")
    app.include_router(plugin_router, prefix="/spoolman")
    try:
        async with AsyncClient(transport=ASGITransport(app), base_url="http://test") as client:
            yield client
    finally:
        await engine.dispose()


async def create_native_filament(client, hexes):
    colors = []
    for position, hex_code in enumerate(hexes, start=1):
        response = await client.post("/api/v1/colors", json={"name": f"Color {position}", "hex_code": hex_code})
        assert response.status_code == 201, response.text
        colors.append({"color_id": response.json()["id"], "position": position, "display_name_override": f"Shade {position}"})
    response = await client.post("/api/v1/filaments", json={
        "manufacturer_id": 1, "designation": "Roundtrip", "material_type": "PLA", "diameter_mm": 1.75,
        "color_mode": "multi" if len(colors) > 1 else "single", "colors": colors,
    })
    assert response.status_code == 201, response.text
    return response.json()


@pytest.mark.asyncio
@pytest.mark.parametrize("stored", ["#BE000022", "#00000000", "#FFFFFF00", "#BE0000"])
async def test_rgb_echo_preserves_native_color_assignment(color_api, stored):
    native = await create_native_filament(color_api, [stored])
    fid = native["id"]
    wire = (await color_api.get(f"/spoolman/api/v1/filament/{fid}")).json()
    response = await color_api.patch(f"/spoolman/api/v1/filament/{fid}", json={
        "comment": "Unrelated edit", "color_hex": wire["color_hex"], "multi_color_hexes": "",
    })
    assert response.status_code == 200, response.text
    after = (await color_api.get(f"/api/v1/filaments/{fid}")).json()
    assert after["colors"] == native["colors"]
    assert after["colors"][0]["color"]["hex_code"] == stored


@pytest.mark.asyncio
@pytest.mark.parametrize("changed", [False, True])
async def test_multicolor_echo_preserves_untouched_alpha(color_api, changed):
    native = await create_native_filament(color_api, ["#BE000022", "#BE000088", "#00000000"])
    fid = native["id"]
    wire = (await color_api.get(f"/spoolman/api/v1/filament/{fid}")).json()
    response = await color_api.patch(f"/spoolman/api/v1/filament/{fid}", json={
        "color_hex": wire["color_hex"],
        "multi_color_hexes": "BE0000,112233,000000" if changed else wire["multi_color_hexes"],
    })
    assert response.status_code == 200, response.text
    after = (await color_api.get(f"/api/v1/filaments/{fid}")).json()
    assert [entry["color"]["hex_code"] for entry in after["colors"]] == [
        "#BE000022", "#112233" if changed else "#BE000088", "#00000000",
    ]
    assert after["colors"][0] == native["colors"][0]
    assert after["colors"][2] == native["colors"][2]


@pytest.mark.asyncio
@pytest.mark.parametrize(("changes", "expected", "mode"), [
    ({}, ["#BE000022", "#00D4D488"], "single"),
    ({"color_hex": "BE0000FF"}, ["#BE0000FF", "#00D4D488"], "single"),
    ({"color_hex": "be000022"}, ["#BE000022", "#00D4D488"], "single"),
    ({"multi_color_hexes": "112233"}, ["#BE000022", "#112233"], "single"),
    ({"color_hex": None, "multi_color_hexes": "11223344,00000000"}, ["#11223344", "#00000000"], "multi"),
    ({"color_hex": None, "multi_color_hexes": None}, [], "single"),
])
async def test_single_mode_secondary_color_echo_preserves_all_assignments(color_api, changes, expected, mode):
    native = await create_native_filament(color_api, ["#BE000022", "#00D4D488"])
    fid = native["id"]
    response = await color_api.put(f"/api/v1/filaments/{fid}/colors", json={
        "color_mode": "single",
        "colors": [{"color_id": entry["color_id"], "position": entry["position"]} for entry in native["colors"]],
    })
    assert response.status_code == 200, response.text
    native = (await color_api.get(f"/api/v1/filaments/{fid}")).json()
    wire = (await color_api.get(f"/spoolman/api/v1/filament/{fid}")).json()
    response = await color_api.patch(f"/spoolman/api/v1/filament/{fid}", json={
        "color_hex": wire["color_hex"], "multi_color_hexes": wire["multi_color_hexes"], **changes,
    })
    assert response.status_code == 200, response.text
    after = (await color_api.get(f"/api/v1/filaments/{fid}")).json()
    assert after["color_mode"] == mode
    assert [entry["color"]["hex_code"] for entry in after["colors"]] == expected
    for before, current in zip(native["colors"], after["colors"]):
        if before["color"]["hex_code"] == current["color"]["hex_code"]:
            assert current == before


@pytest.mark.asyncio
@pytest.mark.parametrize(("patch_body", "expected"), [
    ({"comment": "Only a comment"}, "#BE000022"),
    ({"color_hex": "be000022"}, "#BE000022"),
    ({"color_hex": "BE000044"}, "#BE000044"),
    ({"color_hex": "BE0000FF"}, "#BE0000FF"),
    ({"color_hex": "112233"}, "#112233"),
    ({"color_hex": None}, None),
    ({"color_hex": ""}, None),
])
async def test_color_updates_and_clears_remain_writable(color_api, patch_body, expected):
    native = await create_native_filament(color_api, ["#BE000022"])
    fid = native["id"]
    response = await color_api.patch(f"/spoolman/api/v1/filament/{fid}", json=patch_body)
    assert response.status_code == 200, response.text
    after = (await color_api.get(f"/api/v1/filaments/{fid}")).json()
    assert [entry["color"]["hex_code"] for entry in after["colors"]] == ([] if expected is None else [expected])


@pytest.mark.asyncio
@pytest.mark.parametrize(("color_fields", "expected"), [
    ({"color_hex": "112233"}, ["#112233"]),
    ({"color_hex": "11223344"}, ["#11223344"]),
    ({"multi_color_hexes": "11223344,00000000"}, ["#11223344", "#00000000"]),
])
async def test_spoolman_create_keeps_supplied_alpha(color_api, color_fields, expected):
    response = await color_api.post("/spoolman/api/v1/filament", json={
        "name": "Created through Spoolman", "vendor_id": 1, **color_fields,
    })
    assert response.status_code == 200, response.text
    fid = response.json()["id"]
    native = (await color_api.get(f"/api/v1/filaments/{fid}")).json()
    assert [entry["color"]["hex_code"] for entry in native["colors"]] == expected


async def set_native_positions(client, native, positions, mode):
    fid = native["id"]
    response = await client.put(f"/api/v1/filaments/{fid}/colors", json={
        "color_mode": mode,
        "colors": [
            {"color_id": entry["color_id"], "position": position, "display_name_override": f"Native {position}"}
            for entry, position in zip(native["colors"], positions)
        ],
    })
    assert response.status_code == 200, response.text
    return (await client.get(f"/api/v1/filaments/{fid}")).json()


@pytest.mark.asyncio
@pytest.mark.parametrize(("positions", "hexes", "mode", "full_echo"), [
    ([2], ["#BE000022"], "single", False),
    ([2], ["#BE000022"], "single", True),
    ([0], ["#00000000"], "single", True),
    ([-2], ["#FFFFFF00"], "single", True),
    ([2, 7], ["#BE000022", "#00D4D488"], "single", True),
    ([0, 1, 7], ["#12345644", "#BE000022", "#00D4D488"], "single", True),
    ([0, 1, 7], ["#12345644", "#BE000022", "#00D4D488"], "single", False),
    ([-2, 1, 7], ["#12345644", "#BE000022", "#00D4D488"], "multi", True),
    ([1, 7], ["", "#BE000022"], "single", True),
    ([1, 7], ["", "#BE000022"], "multi", True),
    ([1, 7], ["#BE000022", ""], "multi", True),
])
async def test_sparse_and_hidden_assignments_survive_rgb_echo(color_api, positions, hexes, mode, full_echo):
    native = await create_native_filament(color_api, hexes)
    native = await set_native_positions(color_api, native, positions, mode)
    fid = native["id"]
    wire = (await color_api.get(f"/spoolman/api/v1/filament/{fid}")).json()
    body = {"color_hex": wire.get("color_hex")}
    if full_echo:
        body["multi_color_hexes"] = wire.get("multi_color_hexes")
    response = await color_api.patch(f"/spoolman/api/v1/filament/{fid}", json=body)
    assert response.status_code == 200, response.text
    after = (await color_api.get(f"/api/v1/filaments/{fid}")).json()
    assert after["color_mode"] == native["color_mode"]
    assert after["colors"] == native["colors"]


@pytest.mark.asyncio
@pytest.mark.parametrize(("body", "expected"), [
    ({"color_hex": "BE0000FF", "multi_color_hexes": "BE0000"}, "#BE0000FF"),
    ({"color_hex": "BE0000", "multi_color_hexes": "BE000044"}, "#BE000044"),
    ({"color_hex": None, "multi_color_hexes": None}, None),
])
async def test_fallback_primary_shared_with_secondary_is_updated_once(color_api, body, expected):
    native = await create_native_filament(color_api, ["#BE000022"])
    native = await set_native_positions(color_api, native, [2], "single")
    fid = native["id"]
    response = await color_api.patch(f"/spoolman/api/v1/filament/{fid}", json=body)
    assert response.status_code == 200, response.text
    after = (await color_api.get(f"/api/v1/filaments/{fid}")).json()
    assert [entry["color"]["hex_code"] for entry in after["colors"]] == ([] if expected is None else [expected])
    if expected:
        assert after["color_mode"] == "single"
        assert after["colors"][0]["id"] == native["colors"][0]["id"]
        assert after["colors"][0]["position"] == 2


@pytest.mark.asyncio
@pytest.mark.parametrize(("tokens", "expected", "labels"), [
    ("00D4D4,BE0000", ["#00D4D488", "#BE000022"], ["Native 7", "Native 2"]),
    ("BE0000,112233", ["#BE000022", "#112233"], ["Native 2", None]),
    ("00D4D4", ["#00D4D488"], ["Native 7"]),
    ("BE0000,00D4D4,12345644", ["#BE000022", "#00D4D488", "#12345644"], ["Native 2", "Native 7", None]),
])
async def test_sparse_multicolor_edits_reorders_and_resizing(color_api, tokens, expected, labels):
    native = await create_native_filament(color_api, ["#BE000022", "#00D4D488"])
    native = await set_native_positions(color_api, native, [2, 7], "multi")
    fid = native["id"]
    response = await color_api.patch(f"/spoolman/api/v1/filament/{fid}", json={"multi_color_hexes": tokens})
    assert response.status_code == 200, response.text
    after = (await color_api.get(f"/api/v1/filaments/{fid}")).json()
    assert [entry["color"]["hex_code"] for entry in after["colors"]] == expected
    assert [entry["display_name_override"] for entry in after["colors"]] == labels
    assert len({entry["position"] for entry in after["colors"]}) == len(expected)


@pytest.mark.asyncio
async def test_explicit_alpha_edit_can_reuse_duplicate_palette_hex(color_api):
    native = await create_native_filament(color_api, ["#BE000022"])
    for name in ["Opaque red A", "Opaque red B"]:
        response = await color_api.post("/api/v1/colors", json={"name": name, "hex_code": "#BE0000FF"})
        assert response.status_code == 201, response.text
    fid = native["id"]
    response = await color_api.patch(f"/spoolman/api/v1/filament/{fid}", json={"color_hex": "BE0000FF"})
    assert response.status_code == 200, response.text
    after = (await color_api.get(f"/api/v1/filaments/{fid}")).json()
    assert after["colors"][0]["color"]["hex_code"] == "#BE0000FF"


@pytest.mark.asyncio
async def test_multicolor_request_keeps_precedence_without_native_secondaries(color_api):
    native = await create_native_filament(color_api, ["#BE000022"])
    fid = native["id"]
    response = await color_api.patch(f"/spoolman/api/v1/filament/{fid}", json={
        "color_hex": "BE0000", "multi_color_hexes": "11223344,00000000",
    })
    assert response.status_code == 200, response.text
    after = (await color_api.get(f"/api/v1/filaments/{fid}")).json()
    assert after["color_mode"] == "multi"
    assert [entry["color"]["hex_code"] for entry in after["colors"]] == ["#11223344", "#00000000"]


@pytest.mark.asyncio
@pytest.mark.parametrize(("tokens", "expected"), [
    ("123456,BE0000", ["#12345644", "#BE000088"]),
    ("123456,112233,BE0000", ["#12345644", "#112233", "#BE0000"]),
])
async def test_duplicate_rgb_variants_reserve_matching_slots_without_guessing(color_api, tokens, expected):
    native = await create_native_filament(color_api, ["#BE000022", "#BE000088", "#12345644"])
    native = await set_native_positions(color_api, native, [2, 7, 12], "multi")
    fid = native["id"]
    response = await color_api.patch(f"/spoolman/api/v1/filament/{fid}", json={"multi_color_hexes": tokens})
    assert response.status_code == 200, response.text
    after = (await color_api.get(f"/api/v1/filaments/{fid}")).json()
    assert [entry["color"]["hex_code"] for entry in after["colors"]] == expected


@pytest.mark.asyncio
@pytest.mark.parametrize("clear", [None, ""])
async def test_explicit_secondary_clear_preserves_primary_and_hidden_links(color_api, clear):
    native = await create_native_filament(color_api, ["#12345644", "#BE000022", "#00D4D488"])
    native = await set_native_positions(color_api, native, [0, 1, 7], "single")
    fid = native["id"]
    response = await color_api.patch(f"/spoolman/api/v1/filament/{fid}", json={
        "color_hex": "BE0000", "multi_color_hexes": clear,
    })
    assert response.status_code == 200, response.text
    after = (await color_api.get(f"/api/v1/filaments/{fid}")).json()
    assert after["colors"] == native["colors"][:2]
    assert after["color_mode"] == "single"


@pytest.mark.asyncio
@pytest.mark.parametrize("clear", [None, ""])
@pytest.mark.parametrize(("field", "mode", "remaining"), [
    ("multi_color_hexes", "single", [0, 1]),
    ("color_hex", "single", [0, 2]),
    ("color_hex", "multi", [0, 2]),
    ("multi_color_hexes", "multi", []),
])
async def test_partial_clear_only_removes_assignments_exposed_by_supplied_field(color_api, clear, field, mode, remaining):
    native = await create_native_filament(color_api, ["#12345644", "#BE000022", "#00D4D488"])
    native = await set_native_positions(color_api, native, [0, 1, 7], mode)
    fid = native["id"]
    response = await color_api.patch(f"/spoolman/api/v1/filament/{fid}", json={field: clear})
    assert response.status_code == 200, response.text
    after = (await color_api.get(f"/api/v1/filaments/{fid}")).json()
    assert after["colors"] == [native["colors"][index] for index in remaining]
    assert after["color_mode"] == mode


@pytest.mark.asyncio
@pytest.mark.parametrize("value", ["BE0000", "BE0000FF", "112233"])
async def test_primary_edit_omitting_multicolor_keeps_multi_mode_and_other_assignments(color_api, value):
    native = await create_native_filament(color_api, ["#BE000022", "#00D4D488"])
    fid = native["id"]
    response = await color_api.patch(f"/spoolman/api/v1/filament/{fid}", json={"color_hex": value})
    assert response.status_code == 200, response.text
    after = (await color_api.get(f"/api/v1/filaments/{fid}")).json()
    assert after["color_mode"] == "multi"
    assert after["colors"][1:] == native["colors"][1:]
    assert after["colors"][0]["color"]["hex_code"] == ("#BE000022" if value == "BE0000" else "#" + value)


@pytest.mark.asyncio
@pytest.mark.parametrize("empty_list", [",", " , , "])
async def test_empty_multicolor_tokens_do_not_clear_supplied_primary(color_api, empty_list):
    native = await create_native_filament(color_api, ["#BE000022", "#00D4D488"])
    fid = native["id"]
    response = await color_api.patch(f"/spoolman/api/v1/filament/{fid}", json={
        "color_hex": "BE0000", "multi_color_hexes": empty_list,
    })
    assert response.status_code == 200, response.text
    after = (await color_api.get(f"/api/v1/filaments/{fid}")).json()
    assert after["colors"] == native["colors"][:1]
    assert after["color_mode"] == "single"


@pytest.mark.asyncio
@pytest.mark.parametrize("shared_palette", [False, True])
async def test_reordered_identical_rgba_copies_preserve_common_alpha(color_api, shared_palette):
    native = await create_native_filament(color_api, ["#BE000022", "#00D4D488", "#BE000022"])
    fid = native["id"]
    if shared_palette:
        response = await color_api.put(f"/api/v1/filaments/{fid}/colors", json={
            "color_mode": "multi",
            "colors": [
                {"position": 1, "color_id": native["colors"][0]["color_id"], "display_name_override": "Same red"},
                {"position": 2, "color_id": native["colors"][1]["color_id"]},
                {"position": 3, "color_id": native["colors"][0]["color_id"], "display_name_override": "Same red"},
            ],
        })
        assert response.status_code == 200, response.text
    response = await color_api.patch(f"/spoolman/api/v1/filament/{fid}", json={
        "multi_color_hexes": "00D4D4,BE0000",
    })
    assert response.status_code == 200, response.text
    after = (await color_api.get(f"/api/v1/filaments/{fid}")).json()
    assert [entry["color"]["hex_code"] for entry in after["colors"]] == ["#00D4D488", "#BE000022"]
    assert after["colors"][1]["display_name_override"] == ("Same red" if shared_palette else None)


@pytest.mark.asyncio
async def test_ambiguous_moved_labels_are_not_assigned_to_identical_rgba_copies(color_api):
    native = await create_native_filament(color_api, ["#BE000022", "#BE000022", "#00D4D488", "#12345644"])
    fid = native["id"]
    response = await color_api.patch(f"/spoolman/api/v1/filament/{fid}", json={
        "multi_color_hexes": "00D4D4,123456,BE0000,BE0000",
    })
    assert response.status_code == 200, response.text
    after = (await color_api.get(f"/api/v1/filaments/{fid}")).json()
    assert [entry["color"]["hex_code"] for entry in after["colors"]] == ["#00D4D488", "#12345644", "#BE000022", "#BE000022"]
    assert [entry["display_name_override"] for entry in after["colors"]] == ["Shade 3", "Shade 4", None, None]


@pytest.mark.asyncio
async def test_rgb_filter_only_expands_to_valid_rgba_values(color_api):
    valid = []
    for hex_code in ["#BE0000", "#BE000022", "#BE0000ZZ", "#BE0000XYZ", "#BE#000022"]:
        native = await create_native_filament(color_api, [hex_code])
        if hex_code in ["#BE0000", "#BE000022"]:
            valid.append(native["id"])
    response = await color_api.get("/spoolman/api/v1/filament", params={"color_hex": "BE0000"})
    assert response.status_code == 200, response.text
    assert [entry["id"] for entry in response.json()] == valid
    assert response.headers["x-total-count"] == "2"
