"""Spoolman-compatible Pydantic schemas.

These mirror the Spoolman REST API v1 request/response shapes so that
external clients (Moonraker, OctoPrint-SpoolManager, etc.) can talk to
FilaMan as if it were a Spoolman instance.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Vendor (maps to FilaMan Manufacturer)
# ---------------------------------------------------------------------------

class Vendor(BaseModel):
    id: int
    registered: datetime
    name: str
    comment: str | None = None
    empty_spool_weight: float | None = None
    external_id: str | None = None
    extra: dict[str, Any] = Field(default_factory=dict)


class VendorParameters(BaseModel):
    """POST /vendor body."""
    name: str
    comment: str | None = None
    empty_spool_weight: float | None = None
    external_id: str | None = None
    extra: dict[str, Any] | None = None


class VendorUpdateParameters(BaseModel):
    """PATCH /vendor/{id} body.  All fields optional."""
    name: str | None = None
    comment: str | None = None
    empty_spool_weight: float | None = None
    external_id: str | None = None
    extra: dict[str, Any] | None = None


# ---------------------------------------------------------------------------
# Filament
# ---------------------------------------------------------------------------

class Filament(BaseModel):
    id: int
    registered: datetime
    name: str | None = None
    vendor: Vendor | None = None
    material: str | None = None
    price: float | None = None
    density: float | None = None
    diameter: float | None = None
    weight: float | None = None
    spool_weight: float | None = None
    article_number: str | None = None
    comment: str | None = None
    settings_extruder_temp: int | None = None
    settings_bed_temp: int | None = None
    color_hex: str | None = None
    multi_color_hexes: str | None = None
    multi_color_direction: str | None = None
    external_id: str | None = None
    extra: dict[str, Any] = Field(default_factory=dict)


class FilamentParameters(BaseModel):
    """POST /filament body."""
    name: str | None = None
    vendor_id: int | None = None
    material: str | None = None
    price: float | None = None
    density: float | None = None
    diameter: float | None = None
    weight: float | None = None
    spool_weight: float | None = None
    article_number: str | None = None
    comment: str | None = None
    settings_extruder_temp: int | None = None
    settings_bed_temp: int | None = None
    color_hex: str | None = None
    multi_color_hexes: str | None = None
    multi_color_direction: str | None = None
    external_id: str | None = None
    extra: dict[str, Any] | None = None


class FilamentUpdateParameters(BaseModel):
    """PATCH /filament/{id} body.  All fields optional."""
    name: str | None = None
    vendor_id: int | None = None
    material: str | None = None
    price: float | None = None
    density: float | None = None
    diameter: float | None = None
    weight: float | None = None
    spool_weight: float | None = None
    article_number: str | None = None
    comment: str | None = None
    settings_extruder_temp: int | None = None
    settings_bed_temp: int | None = None
    color_hex: str | None = None
    multi_color_hexes: str | None = None
    multi_color_direction: str | None = None
    external_id: str | None = None
    extra: dict[str, Any] | None = None


# ---------------------------------------------------------------------------
# Spool
# ---------------------------------------------------------------------------

class Spool(BaseModel):
    id: int
    registered: datetime
    first_used: datetime | None = None
    last_used: datetime | None = None
    filament: Filament
    price: float | None = None
    initial_weight: float | None = None
    spool_weight: float | None = None
    remaining_weight: float | None = None
    used_weight: float | None = None
    remaining_length: float | None = None
    used_length: float | None = None
    location: str | None = None
    lot_nr: str | None = None
    comment: str | None = None
    archived: bool = False
    extra: dict[str, Any] = Field(default_factory=dict)


class SpoolParameters(BaseModel):
    """POST /spool body."""
    filament_id: int
    first_used: datetime | None = None
    last_used: datetime | None = None
    price: float | None = None
    initial_weight: float | None = None
    spool_weight: float | None = None
    remaining_weight: float | None = None
    used_weight: float | None = None
    location: str | None = None
    lot_nr: str | None = None
    comment: str | None = None
    archived: bool = False
    extra: dict[str, Any] | None = None


class SpoolUpdateParameters(BaseModel):
    """PATCH /spool/{id} body.  All fields optional."""
    filament_id: int | None = None
    first_used: datetime | None = None
    last_used: datetime | None = None
    price: float | None = None
    initial_weight: float | None = None
    spool_weight: float | None = None
    remaining_weight: float | None = None
    used_weight: float | None = None
    location: str | None = None
    lot_nr: str | None = None
    comment: str | None = None
    archived: bool | None = None
    extra: dict[str, Any] | None = None


class SpoolUseParameters(BaseModel):
    """PUT /spool/{id}/use body.  Provide *either* use_weight or use_length."""
    use_weight: float | None = None
    use_length: float | None = None


class SpoolMeasureParameters(BaseModel):
    """PUT /spool/{id}/measure body.  Gross weight (filament + spool)."""
    weight: float


# ---------------------------------------------------------------------------
# Meta / utility
# ---------------------------------------------------------------------------

class Info(BaseModel):
    version: str = "1.0.0"
    debug_mode: bool = False
    automatic_backups: bool = False
    data_dir: str = "/app/data"
    logs_dir: str = "/app/data/logs"
    backups_dir: str = "/app/data/backups"
    db_type: str = "sqlite"


class HealthCheck(BaseModel):
    status: str = "healthy"


class Message(BaseModel):
    message: str


# ---------------------------------------------------------------------------
# Admin settings (FilaMan-specific, not part of Spoolman API)
# ---------------------------------------------------------------------------

class SpoolmanAPISettings(BaseModel):
    ip_filter_enabled: bool = False
    allowed_ips: list[str] = Field(default_factory=list)
