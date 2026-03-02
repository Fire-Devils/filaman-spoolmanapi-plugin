"""Spoolman-compatible Pydantic schemas.

These mirror the Spoolman REST API v1 request/response shapes so that
external clients (Moonraker, OctoPrint-SpoolManager, etc.) can talk to
FilaMan as if it were a Spoolman instance.
"""

from __future__ import annotations

from datetime import datetime

from enum import Enum

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
    extra: dict[str, str] = Field(default_factory=dict)


class VendorParameters(BaseModel):
    """POST /vendor body."""
    name: str
    comment: str | None = None
    empty_spool_weight: float | None = None
    external_id: str | None = None
    extra: dict[str, str] | None = None


class VendorUpdateParameters(BaseModel):
    """PATCH /vendor/{id} body.  All fields optional."""
    name: str | None = None
    comment: str | None = None
    empty_spool_weight: float | None = None
    external_id: str | None = None
    extra: dict[str, str] | None = None


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
    density: float
    diameter: float
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
    extra: dict[str, str] = Field(default_factory=dict)


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
    extra: dict[str, str] | None = None


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
    extra: dict[str, str] | None = None


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
    used_weight: float = 0
    remaining_length: float | None = None
    used_length: float = 0
    location: str | None = None
    lot_nr: str | None = None
    comment: str | None = None
    archived: bool = False
    extra: dict[str, str] = Field(default_factory=dict)


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
    extra: dict[str, str] | None = None


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
    extra: dict[str, str] | None = None


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
    git_commit: str | None = None
    build_date: str | None = None


class HealthCheck(BaseModel):
    status: str = "healthy"


class Message(BaseModel):
    message: str


class BackupResponse(BaseModel):
    path: str


# ---------------------------------------------------------------------------
# Location
# ---------------------------------------------------------------------------

class RenameLocationBody(BaseModel):
    """PATCH /location/{location} body."""
    name: str


# ---------------------------------------------------------------------------
# Settings (Spoolman settings, not FilaMan admin settings)
# ---------------------------------------------------------------------------

class SettingType(str, Enum):
    boolean = "boolean"
    number = "number"
    string = "string"
    array = "array"
    object_type = "object"


class SettingResponse(BaseModel):
    """Response for GET /setting/{key}."""
    value: str = ""
    is_set: bool = False
    type: SettingType = SettingType.string


# ---------------------------------------------------------------------------
# Extra Fields
# ---------------------------------------------------------------------------

class EntityType(str, Enum):
    vendor = "vendor"
    filament = "filament"
    spool = "spool"


class ExtraFieldType(str, Enum):
    text = "text"
    integer = "integer"
    integer_range = "integer_range"
    float_type = "float"
    float_range = "float_range"
    datetime_type = "datetime"
    boolean = "boolean"
    choice = "choice"


class ExtraField(BaseModel):
    name: str
    order: int = 0
    unit: str | None = None
    field_type: ExtraFieldType = ExtraFieldType.text
    default_value: str | None = None
    key: str
    entity_type: EntityType
    choices: list[str] | None = None
    multi_choice: bool | None = None


class ExtraFieldParameters(BaseModel):
    """POST /field/{entity_type}/{key} body."""
    name: str
    order: int = 0
    unit: str | None = None
    field_type: ExtraFieldType = ExtraFieldType.text
    default_value: str | None = None
    choices: list[str] | None = None
    multi_choice: bool | None = None


# ---------------------------------------------------------------------------
# Export
# ---------------------------------------------------------------------------

class ExportFormat(str, Enum):
    csv = "csv"
    json = "json"


# ---------------------------------------------------------------------------
# External database
# ---------------------------------------------------------------------------

class ExternalFilament(BaseModel):
    id: str
    manufacturer: str
    name: str
    material: str
    density: float
    weight: float
    spool_weight: float | None = None
    spool_type: str | None = None
    diameter: float
    color_hex: str | None = None
    color_hexes: list[str] | None = None
    extruder_temp: int | None = None
    bed_temp: int | None = None
    finish: str | None = None
    multi_color_direction: str | None = None
    pattern: str | None = None
    translucent: bool = False
    glow: bool = False


class ExternalMaterial(BaseModel):
    material: str
    density: float
    extruder_temp: int | None = None
    bed_temp: int | None = None


# ---------------------------------------------------------------------------
# Admin settings (FilaMan-specific, not part of Spoolman API)
# ---------------------------------------------------------------------------

class SpoolmanAPISettings(BaseModel):
    ip_filter_enabled: bool = False
    allowed_ips: list[str] = Field(default_factory=list)
