from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import patch

import pydantic_settings
import pytest


_settings_init = pydantic_settings.BaseSettings.__init__


def _without_local_env(self, *args, **kwargs):
    kwargs["_env_file"] = None
    return _settings_init(self, *args, **kwargs)


with patch.object(pydantic_settings.BaseSettings, "__init__", _without_local_env):
    from spoolmanapi.service import SpoolmanService


@pytest.mark.parametrize(
    ("stored", "expected"),
    [
        ("#BE000022", "BE0000"),
        ("#00D4D488", "00D4D4"),
        ("#FFFFFF00", "FFFFFF"),
        ("#FF0000", "FF0000"),
        ("#NOTHEXZZ", "NOTHEXZZ"),
    ],
)
def test_spoolman_rgb_output_preserves_legacy_non_hex(stored, expected):
    assert SpoolmanService._to_rgb_hex(stored) == expected


def test_filament_response_uses_rgb_for_primary_and_multicolor():
    color = lambda position, hex_code: SimpleNamespace(position=position, color=SimpleNamespace(hex_code=hex_code))
    filament = SimpleNamespace(
        id=1, created_at=datetime.now(timezone.utc), designation="Test", manufacturer=None,
        material_type="PLA", price=None, density_g_cm3=1.24, diameter_mm=1.75,
        raw_material_weight_g=None, default_spool_weight_g=None, multi_color_style=None,
        color_mode="multi", custom_fields={},
        filament_colors=[color(1, "#BE000022"), color(2, "#00D4D488")],
    )

    response = SpoolmanService(None)._filament_to_schema(filament)

    assert response.color_hex == "BE0000"
    assert response.multi_color_hexes == "BE0000,00D4D4"
