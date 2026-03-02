from __future__ import annotations

import logging
import math
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.filament import Manufacturer, Filament, Color, FilamentColor
from app.models.spool import Spool, SpoolStatus
from app.models.location import Location
from app.services.spool_service import SpoolService

from . import schemas

logger = logging.getLogger(__name__)


def _weight_to_length_mm(weight_g: float, diameter_mm: float, density_g_cm3: float) -> float:
    """Convert net filament weight (g) to length (mm)."""
    radius_cm = (diameter_mm / 2) / 10
    cross_section_cm2 = math.pi * radius_cm**2
    volume_cm3 = weight_g / density_g_cm3
    length_cm = volume_cm3 / cross_section_cm2
    return length_cm * 10


def _length_to_weight_g(length_mm: float, diameter_mm: float, density_g_cm3: float) -> float:
    """Convert filament length (mm) to net weight (g)."""
    radius_cm = (diameter_mm / 2) / 10
    cross_section_cm2 = math.pi * radius_cm**2
    length_cm = length_mm / 10
    volume_cm3 = cross_section_cm2 * length_cm
    return volume_cm3 * density_g_cm3


class SpoolmanService:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def list_vendors(self) -> list[schemas.Vendor]:
        result = await self.db.execute(select(Manufacturer).order_by(Manufacturer.id))
        vendors = result.scalars().all()
        return [self._manufacturer_to_vendor(vendor) for vendor in vendors]

    async def get_vendor(self, vendor_id: int) -> schemas.Vendor | None:
        result = await self.db.execute(select(Manufacturer).where(Manufacturer.id == vendor_id))
        vendor = result.scalar_one_or_none()
        if not vendor:
            return None
        return self._manufacturer_to_vendor(vendor)

    async def create_vendor(self, data: schemas.VendorParameters) -> schemas.Vendor:
        custom_fields: dict[str, Any] = {}
        if data.extra:
            custom_fields.update(data.extra)
        if data.comment is not None:
            custom_fields["comment"] = data.comment
        if data.external_id is not None:
            custom_fields["external_id"] = data.external_id

        vendor = Manufacturer(
            name=data.name,
            empty_spool_weight_g=data.empty_spool_weight,
            custom_fields=custom_fields or None,
        )
        self.db.add(vendor)
        await self.db.commit()
        vendor = await self._get_manufacturer(vendor.id)
        return self._manufacturer_to_vendor(vendor)

    async def update_vendor(self, vendor_id: int, data: schemas.VendorUpdateParameters) -> schemas.Vendor | None:
        result = await self.db.execute(select(Manufacturer).where(Manufacturer.id == vendor_id))
        vendor = result.scalar_one_or_none()
        if not vendor:
            return None

        payload = data.model_dump(exclude_unset=True)
        if "name" in payload:
            vendor.name = payload["name"]
        if "empty_spool_weight" in payload:
            vendor.empty_spool_weight_g = payload["empty_spool_weight"]

        custom_fields = dict(vendor.custom_fields or {})
        if "comment" in payload:
            if payload["comment"] is None:
                custom_fields.pop("comment", None)
            else:
                custom_fields["comment"] = payload["comment"]
        if "external_id" in payload:
            if payload["external_id"] is None:
                custom_fields.pop("external_id", None)
            else:
                custom_fields["external_id"] = payload["external_id"]
        if "extra" in payload and payload["extra"]:
            custom_fields.update(payload["extra"])

        vendor.custom_fields = custom_fields or None
        await self.db.commit()
        vendor = await self._get_manufacturer(vendor.id)
        return self._manufacturer_to_vendor(vendor)

    async def delete_vendor(self, vendor_id: int) -> bool:
        result = await self.db.execute(select(Manufacturer).where(Manufacturer.id == vendor_id))
        vendor = result.scalar_one_or_none()
        if not vendor:
            return False
        await self.db.delete(vendor)
        await self.db.commit()
        return True

    async def list_filaments(self) -> list[schemas.Filament]:
        result = await self.db.execute(
            select(Filament)
            .options(
                selectinload(Filament.manufacturer),
                selectinload(Filament.filament_colors).selectinload(FilamentColor.color),
            )
            .order_by(Filament.id)
        )
        filaments = result.scalars().unique().all()
        return [self._filament_to_schema(filament) for filament in filaments]

    async def get_filament(self, filament_id: int) -> schemas.Filament | None:
        filament = await self._get_filament(filament_id)
        if not filament:
            return None
        return self._filament_to_schema(filament)

    async def create_filament(self, data: schemas.FilamentParameters) -> schemas.Filament:
        custom_fields: dict[str, Any] = {}
        if data.extra:
            custom_fields.update(data.extra)
        if data.article_number is not None:
            custom_fields["article_number"] = data.article_number
        if data.comment is not None:
            custom_fields["comment"] = data.comment
        if data.settings_extruder_temp is not None:
            custom_fields["settings_extruder_temp"] = data.settings_extruder_temp
        if data.settings_bed_temp is not None:
            custom_fields["settings_bed_temp"] = data.settings_bed_temp
        if data.external_id is not None:
            custom_fields["external_id"] = data.external_id

        color_mode = "multi" if data.multi_color_hexes else "single"
        filament = Filament(
            designation=data.name or "Unnamed",
            manufacturer_id=data.vendor_id,
            material_type=data.material or "PLA",
            density_g_cm3=data.density,
            diameter_mm=data.diameter or 1.75,
            raw_material_weight_g=data.weight,
            default_spool_weight_g=data.spool_weight,
            price=data.price,
            color_mode=color_mode,
            multi_color_style=data.multi_color_direction,
            custom_fields=custom_fields or None,
        )
        self.db.add(filament)
        await self.db.flush()
        await self._apply_filament_colors(filament, data.color_hex, data.multi_color_hexes)
        await self.db.commit()
        filament = await self._get_filament(filament.id)
        return self._filament_to_schema(filament)

    async def update_filament(self, filament_id: int, data: schemas.FilamentUpdateParameters) -> schemas.Filament | None:
        filament = await self._get_filament(filament_id)
        if not filament:
            return None

        payload = data.model_dump(exclude_unset=True)
        if "name" in payload:
            filament.designation = payload["name"] or "Unnamed"
        if "vendor_id" in payload:
            filament.manufacturer_id = payload["vendor_id"]
        if "material" in payload:
            filament.material_type = payload["material"] or "PLA"
        if "density" in payload:
            filament.density_g_cm3 = payload["density"]
        if "diameter" in payload:
            filament.diameter_mm = payload["diameter"] or 1.75
        if "weight" in payload:
            filament.raw_material_weight_g = payload["weight"]
        if "spool_weight" in payload:
            filament.default_spool_weight_g = payload["spool_weight"]
        if "price" in payload:
            filament.price = payload["price"]
        if "multi_color_direction" in payload:
            filament.multi_color_style = payload["multi_color_direction"]

        custom_fields = dict(filament.custom_fields or {})
        if "article_number" in payload:
            if payload["article_number"] is None:
                custom_fields.pop("article_number", None)
            else:
                custom_fields["article_number"] = payload["article_number"]
        if "comment" in payload:
            if payload["comment"] is None:
                custom_fields.pop("comment", None)
            else:
                custom_fields["comment"] = payload["comment"]
        if "settings_extruder_temp" in payload:
            if payload["settings_extruder_temp"] is None:
                custom_fields.pop("settings_extruder_temp", None)
            else:
                custom_fields["settings_extruder_temp"] = payload["settings_extruder_temp"]
        if "settings_bed_temp" in payload:
            if payload["settings_bed_temp"] is None:
                custom_fields.pop("settings_bed_temp", None)
            else:
                custom_fields["settings_bed_temp"] = payload["settings_bed_temp"]
        if "external_id" in payload:
            if payload["external_id"] is None:
                custom_fields.pop("external_id", None)
            else:
                custom_fields["external_id"] = payload["external_id"]
        if "extra" in payload and payload["extra"]:
            custom_fields.update(payload["extra"])

        filament.custom_fields = custom_fields or None

        if "multi_color_hexes" in payload or "color_hex" in payload:
            filament.color_mode = "multi" if payload.get("multi_color_hexes") else "single"
            await self._apply_filament_colors(
                filament,
                payload.get("color_hex"),
                payload.get("multi_color_hexes"),
                clear_existing=True,
            )
        elif "multi_color_direction" in payload:
            filament.color_mode = filament.color_mode or "single"

        await self.db.commit()
        filament = await self._get_filament(filament.id)
        return self._filament_to_schema(filament)

    async def delete_filament(self, filament_id: int) -> bool:
        filament = await self._get_filament(filament_id)
        if not filament:
            return False
        await self.db.delete(filament)
        await self.db.commit()
        return True

    async def list_spools(self) -> list[schemas.Spool]:
        result = await self.db.execute(
            select(Spool)
            .options(
                selectinload(Spool.filament).selectinload(Filament.manufacturer),
                selectinload(Spool.filament).selectinload(Filament.filament_colors).selectinload(FilamentColor.color),
                selectinload(Spool.status),
                selectinload(Spool.location),
            )
            .order_by(Spool.id)
        )
        spools = result.scalars().unique().all()
        return [self._spool_to_schema(spool) for spool in spools]

    async def get_spool(self, spool_id: int) -> schemas.Spool | None:
        spool = await self._get_spool(spool_id)
        if not spool:
            return None
        return self._spool_to_schema(spool)

    async def create_spool(self, data: schemas.SpoolParameters) -> schemas.Spool:
        custom_fields: dict[str, Any] = {}
        if data.extra:
            custom_fields.update(data.extra)
        if data.comment is not None:
            custom_fields["comment"] = data.comment

        location_id = await self._resolve_location(data.location)
        status_id = await self._resolve_status(data.archived)

        spool = Spool(
            filament_id=data.filament_id,
            status_id=status_id,
            stocked_in_at=data.first_used,
            last_used_at=data.last_used,
            purchase_price=data.price,
            lot_number=data.lot_nr,
            empty_spool_weight_g=data.spool_weight,
            location_id=location_id,
            custom_fields=custom_fields or None,
        )

        self._apply_spool_weights(spool, data.initial_weight, data.spool_weight, data.remaining_weight, data.used_weight)
        self.db.add(spool)
        await self.db.commit()
        spool = await self._get_spool(spool.id)
        return self._spool_to_schema(spool)

    async def update_spool(self, spool_id: int, data: schemas.SpoolUpdateParameters) -> schemas.Spool | None:
        spool = await self._get_spool(spool_id)
        if not spool:
            return None

        payload = data.model_dump(exclude_unset=True)
        if "filament_id" in payload:
            spool.filament_id = payload["filament_id"]
        if "first_used" in payload:
            spool.stocked_in_at = payload["first_used"]
        if "last_used" in payload:
            spool.last_used_at = payload["last_used"]
        if "price" in payload:
            spool.purchase_price = payload["price"]
        if "lot_nr" in payload:
            spool.lot_number = payload["lot_nr"]
        if "spool_weight" in payload:
            spool.empty_spool_weight_g = payload["spool_weight"]
        if "location" in payload:
            spool.location_id = await self._resolve_location(payload["location"])
        if "archived" in payload and payload["archived"] is not None:
            spool.status_id = await self._resolve_status(payload["archived"])

        custom_fields = dict(spool.custom_fields or {})
        if "comment" in payload:
            if payload["comment"] is None:
                custom_fields.pop("comment", None)
            else:
                custom_fields["comment"] = payload["comment"]
        if "extra" in payload and payload["extra"]:
            custom_fields.update(payload["extra"])
        spool.custom_fields = custom_fields or None

        if any(key in payload for key in ("initial_weight", "spool_weight", "remaining_weight", "used_weight")):
            self._apply_spool_weights(
                spool,
                payload.get("initial_weight"),
                payload.get("spool_weight"),
                payload.get("remaining_weight"),
                payload.get("used_weight"),
            )

        await self.db.commit()
        spool = await self._get_spool(spool.id)
        return self._spool_to_schema(spool)

    async def delete_spool(self, spool_id: int) -> bool:
        spool = await self._get_spool(spool_id)
        if not spool:
            return False
        spool.status_id = await self._resolve_status(True)
        await self.db.commit()
        return True

    async def use_spool(self, spool_id: int, data: schemas.SpoolUseParameters) -> schemas.Spool | None:
        spool = await self._get_spool(spool_id)
        if not spool:
            return None

        now = datetime.now(timezone.utc)
        if data.use_weight is not None:
            await SpoolService(self.db).record_consumption(
                spool,
                delta_weight_g=data.use_weight,
                event_at=now,
                source="spoolman_api",
            )
        elif data.use_length is not None:
            filament = spool.filament
            if filament and filament.diameter_mm and filament.density_g_cm3:
                weight_g = _length_to_weight_g(data.use_length, filament.diameter_mm, filament.density_g_cm3)
                await SpoolService(self.db).record_consumption(
                    spool,
                    delta_weight_g=weight_g,
                    event_at=now,
                    source="spoolman_api",
                )
            else:
                logger.warning("Cannot convert length to weight: missing filament data for spool %s", spool.id)

        spool = await self._get_spool(spool.id)
        return self._spool_to_schema(spool)

    async def measure_spool(self, spool_id: int, data: schemas.SpoolMeasureParameters) -> schemas.Spool | None:
        spool = await self._get_spool(spool_id)
        if not spool:
            return None

        now = datetime.now(timezone.utc)
        await SpoolService(self.db).record_measurement(
            spool,
            measured_weight_g=data.weight,
            event_at=now,
            source="spoolman_api",
        )
        spool = await self._get_spool(spool.id)
        return self._spool_to_schema(spool)

    def _manufacturer_to_vendor(self, manufacturer: Manufacturer) -> schemas.Vendor:
        extra = dict(manufacturer.custom_fields or {})
        comment = extra.pop("comment", None)
        external_id = extra.pop("external_id", None)
        return schemas.Vendor(
            id=manufacturer.id,
            registered=manufacturer.created_at,
            name=manufacturer.name,
            comment=comment,
            empty_spool_weight=manufacturer.empty_spool_weight_g,
            external_id=external_id,
            extra=extra,
        )

    def _filament_to_schema(self, filament: Filament) -> schemas.Filament:
        vendor = self._manufacturer_to_vendor(filament.manufacturer) if filament.manufacturer else None
        colors = sorted(filament.filament_colors or [], key=lambda item: item.position)
        primary_color = None
        if colors:
            primary = next((item for item in colors if item.position == 1), colors[0])
            if primary.color and primary.color.hex_code:
                primary_color = primary.color.hex_code.lstrip("#")

        if filament.color_mode == "multi":
            multi_colors = [item.color.hex_code.lstrip("#") for item in colors if item.color and item.color.hex_code]
        else:
            multi_colors = [
                item.color.hex_code.lstrip("#")
                for item in colors
                if item.position > 1 and item.color and item.color.hex_code
            ]
        multi_color_hexes = ",".join(multi_colors) if multi_colors else None

        custom_fields = dict(filament.custom_fields or {})
        article_number = custom_fields.pop("article_number", None)
        comment = custom_fields.pop("comment", None)
        settings_extruder_temp = custom_fields.pop("settings_extruder_temp", None)
        settings_bed_temp = custom_fields.pop("settings_bed_temp", None)
        external_id = custom_fields.pop("external_id", None)

        return schemas.Filament(
            id=filament.id,
            registered=filament.created_at,
            name=filament.designation,
            vendor=vendor,
            material=filament.material_type,
            price=filament.price,
            density=filament.density_g_cm3,
            diameter=filament.diameter_mm,
            weight=filament.raw_material_weight_g,
            spool_weight=filament.default_spool_weight_g,
            article_number=article_number,
            comment=comment,
            settings_extruder_temp=settings_extruder_temp,
            settings_bed_temp=settings_bed_temp,
            color_hex=primary_color,
            multi_color_hexes=multi_color_hexes,
            multi_color_direction=filament.multi_color_style,
            external_id=external_id,
            extra=custom_fields,
        )

    async def _find_or_create_color(self, hex_code: str) -> Color:
        normalized = hex_code.strip().upper()
        if not normalized.startswith("#"):
            normalized = f"#{normalized}"

        result = await self.db.execute(select(Color).where(Color.hex_code == normalized))
        color = result.scalar_one_or_none()
        if color:
            return color

        color = Color(name=normalized, hex_code=normalized)
        self.db.add(color)
        await self.db.flush()
        return color

    async def _apply_filament_colors(
        self,
        filament: Filament,
        color_hex: str | None,
        multi_color_hexes: str | None,
        clear_existing: bool = False,
    ) -> None:
        if clear_existing:
            filament.filament_colors.clear()

        if multi_color_hexes:
            hex_list = [item.strip() for item in multi_color_hexes.split(",") if item.strip()]
            for index, hex_code in enumerate(hex_list, start=1):
                color = await self._find_or_create_color(hex_code)
                filament.filament_colors.append(FilamentColor(color_id=color.id, position=index))
            filament.color_mode = "multi"
            return

        if color_hex:
            color = await self._find_or_create_color(color_hex)
            filament.filament_colors.append(FilamentColor(color_id=color.id, position=1))
        filament.color_mode = "single"

    async def _get_filament(self, filament_id: int) -> Filament | None:
        result = await self.db.execute(
            select(Filament)
            .where(Filament.id == filament_id)
            .options(
                selectinload(Filament.manufacturer),
                selectinload(Filament.filament_colors).selectinload(FilamentColor.color),
            )
        )
        return result.scalar_one_or_none()

    async def _get_manufacturer(self, manufacturer_id: int) -> Manufacturer:
        result = await self.db.execute(select(Manufacturer).where(Manufacturer.id == manufacturer_id))
        return result.scalar_one()

    async def _get_spool(self, spool_id: int) -> Spool | None:
        result = await self.db.execute(
            select(Spool)
            .where(Spool.id == spool_id)
            .options(
                selectinload(Spool.filament).selectinload(Filament.manufacturer),
                selectinload(Spool.filament).selectinload(Filament.filament_colors).selectinload(FilamentColor.color),
                selectinload(Spool.status),
                selectinload(Spool.location),
            )
        )
        return result.scalar_one_or_none()

    def _spool_to_schema(self, spool: Spool) -> schemas.Spool:
        filament = self._filament_to_schema(spool.filament)
        initial_weight = None
        if spool.initial_total_weight_g is not None and spool.empty_spool_weight_g is not None:
            initial_weight = spool.initial_total_weight_g - spool.empty_spool_weight_g

        used_weight = None
        if initial_weight is not None and spool.remaining_weight_g is not None:
            used_weight = initial_weight - spool.remaining_weight_g

        remaining_length = None
        used_length = None
        if filament.density and filament.diameter:
            if spool.remaining_weight_g is not None:
                remaining_length = _weight_to_length_mm(spool.remaining_weight_g, filament.diameter, filament.density)
            if used_weight is not None:
                used_length = _weight_to_length_mm(used_weight, filament.diameter, filament.density)

        custom_fields = dict(spool.custom_fields or {})
        comment = custom_fields.pop("comment", None)

        return schemas.Spool(
            id=spool.id,
            registered=spool.created_at,
            first_used=spool.stocked_in_at,
            last_used=spool.last_used_at,
            filament=filament,
            price=spool.purchase_price,
            initial_weight=initial_weight,
            spool_weight=spool.empty_spool_weight_g,
            remaining_weight=spool.remaining_weight_g,
            used_weight=used_weight,
            remaining_length=remaining_length,
            used_length=used_length,
            location=spool.location.name if spool.location else None,
            lot_nr=spool.lot_number,
            comment=comment,
            archived=spool.status.key == "archived" if spool.status else False,
            extra=custom_fields,
        )

    async def _resolve_location(self, name: str | None) -> int | None:
        if not name:
            return None
        result = await self.db.execute(select(Location).where(func.lower(Location.name) == name.lower()))
        location = result.scalar_one_or_none()
        if location:
            return location.id
        location = Location(name=name)
        self.db.add(location)
        await self.db.flush()
        return location.id

    async def _resolve_status(self, archived: bool) -> int:
        key = "archived" if archived else "new"
        result = await self.db.execute(select(SpoolStatus).where(SpoolStatus.key == key))
        status = result.scalar_one()
        return status.id

    def _apply_spool_weights(
        self,
        spool: Spool,
        initial_weight: float | None,
        spool_weight: float | None,
        remaining_weight: float | None,
        used_weight: float | None,
    ) -> None:
        if initial_weight is not None and spool_weight is not None:
            spool.initial_total_weight_g = initial_weight + spool_weight

        if remaining_weight is not None:
            spool.remaining_weight_g = remaining_weight
        elif initial_weight is not None and used_weight is not None:
            spool.remaining_weight_g = initial_weight - used_weight
        elif initial_weight is not None and remaining_weight is None:
            spool.remaining_weight_g = initial_weight
