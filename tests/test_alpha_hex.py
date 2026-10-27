from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import patch

import pydantic_settings
import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine


_settings_init = pydantic_settings.BaseSettings.__init__


def _without_local_env(self, *args, **kwargs):
    kwargs["_env_file"] = None
    return _settings_init(self, *args, **kwargs)


with patch.object(pydantic_settings.BaseSettings, "__init__", _without_local_env):
    from app.models.base import Base
    from app.models.filament import Color, Filament, FilamentColor, Manufacturer
    from spoolmanapi.service import SpoolmanService


@pytest.mark.parametrize(
    ("stored", "expected"),
    [
        ("#BE000022", "BE0000"),
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
    color = lambda position, hex_code: SimpleNamespace(position=position, color=SimpleNamespace(hex_code=hex_code))
    filament = SimpleNamespace(
        id=1, created_at=datetime.now(timezone.utc), designation="Test", manufacturer=None,
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

    await engine.dispose()
