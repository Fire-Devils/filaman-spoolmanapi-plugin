"""Spoolman-compatible REST API router.

Exposes vendor/filament/spool endpoints that mirror the Spoolman v1 API,
plus admin endpoints for managing the plugin's IP-filter settings.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status

from app.api.deps import DBSession, RequirePermission

from . import schemas
from .ip_filter import require_ip_access
from .service import SpoolmanService
from .settings import load_settings, save_settings

# Main Spoolman-compatible router — IP-filtered, no auth required
router = APIRouter(prefix="/api/v1", dependencies=[Depends(require_ip_access)])

# Admin router for plugin settings — auth-protected, no IP filter
admin_router = APIRouter(
    prefix="/admin/system/spoolman-api",
    tags=["admin-system"],
)


@router.get("/info", response_model=schemas.Info)
async def get_info():
    return schemas.Info()


@router.get("/health", response_model=schemas.HealthCheck)
async def health_check():
    return schemas.HealthCheck()


@router.get("/vendor", response_model=list[schemas.Vendor])
async def list_vendors(db: DBSession):
    svc = SpoolmanService(db)
    return await svc.list_vendors()


@router.get("/vendor/{vendor_id}", response_model=schemas.Vendor)
async def get_vendor(vendor_id: int, db: DBSession):
    svc = SpoolmanService(db)
    vendor = await svc.get_vendor(vendor_id)
    if vendor is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"message": "Vendor not found"},
        )
    return vendor


@router.post("/vendor", response_model=schemas.Vendor, status_code=status.HTTP_200_OK)
async def create_vendor(data: schemas.VendorParameters, db: DBSession):
    svc = SpoolmanService(db)
    return await svc.create_vendor(data)


@router.patch("/vendor/{vendor_id}", response_model=schemas.Vendor)
async def update_vendor(
    vendor_id: int,
    data: schemas.VendorUpdateParameters,
    db: DBSession,
):
    svc = SpoolmanService(db)
    vendor = await svc.update_vendor(vendor_id, data)
    if vendor is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"message": "Vendor not found"},
        )
    return vendor


@router.delete("/vendor/{vendor_id}", response_model=schemas.Message)
async def delete_vendor(vendor_id: int, db: DBSession):
    svc = SpoolmanService(db)
    if not await svc.delete_vendor(vendor_id):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"message": "Vendor not found"},
        )
    return schemas.Message(message="Success")


@router.get("/filament", response_model=list[schemas.Filament])
async def list_filaments(db: DBSession):
    svc = SpoolmanService(db)
    return await svc.list_filaments()


@router.get("/filament/{filament_id}", response_model=schemas.Filament)
async def get_filament(filament_id: int, db: DBSession):
    svc = SpoolmanService(db)
    filament = await svc.get_filament(filament_id)
    if filament is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"message": "Filament not found"},
        )
    return filament


@router.post(
    "/filament",
    response_model=schemas.Filament,
    status_code=status.HTTP_200_OK,
)
async def create_filament(data: schemas.FilamentParameters, db: DBSession):
    svc = SpoolmanService(db)
    return await svc.create_filament(data)


@router.patch("/filament/{filament_id}", response_model=schemas.Filament)
async def update_filament(
    filament_id: int,
    data: schemas.FilamentUpdateParameters,
    db: DBSession,
):
    svc = SpoolmanService(db)
    filament = await svc.update_filament(filament_id, data)
    if filament is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"message": "Filament not found"},
        )
    return filament


@router.delete("/filament/{filament_id}", response_model=schemas.Message)
async def delete_filament(filament_id: int, db: DBSession):
    svc = SpoolmanService(db)
    if not await svc.delete_filament(filament_id):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"message": "Filament not found"},
        )
    return schemas.Message(message="Success")


@router.get("/spool", response_model=list[schemas.Spool])
async def list_spools(db: DBSession):
    svc = SpoolmanService(db)
    return await svc.list_spools()


@router.get("/spool/{spool_id}", response_model=schemas.Spool)
async def get_spool(spool_id: int, db: DBSession):
    svc = SpoolmanService(db)
    spool = await svc.get_spool(spool_id)
    if spool is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"message": "Spool not found"},
        )
    return spool


@router.post("/spool", response_model=schemas.Spool, status_code=status.HTTP_200_OK)
async def create_spool(data: schemas.SpoolParameters, db: DBSession):
    svc = SpoolmanService(db)
    return await svc.create_spool(data)


@router.patch("/spool/{spool_id}", response_model=schemas.Spool)
async def update_spool(
    spool_id: int,
    data: schemas.SpoolUpdateParameters,
    db: DBSession,
):
    svc = SpoolmanService(db)
    spool = await svc.update_spool(spool_id, data)
    if spool is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"message": "Spool not found"},
        )
    return spool


@router.delete("/spool/{spool_id}", response_model=schemas.Message)
async def delete_spool(spool_id: int, db: DBSession):
    svc = SpoolmanService(db)
    if not await svc.delete_spool(spool_id):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"message": "Spool not found"},
        )
    return schemas.Message(message="Success")


@router.put("/spool/{spool_id}/use", response_model=schemas.Spool)
async def use_spool(
    spool_id: int,
    data: schemas.SpoolUseParameters,
    db: DBSession,
):
    if data.use_weight is None and data.use_length is None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={"message": "Either use_weight or use_length must be provided"},
        )
    svc = SpoolmanService(db)
    try:
        spool = await svc.use_spool(spool_id, data)
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={"message": "Internal server error"},
        ) from exc
    if spool is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"message": "Spool not found"},
        )
    return spool


@router.put("/spool/{spool_id}/measure", response_model=schemas.Spool)
async def measure_spool(
    spool_id: int,
    data: schemas.SpoolMeasureParameters,
    db: DBSession,
):
    svc = SpoolmanService(db)
    try:
        spool = await svc.measure_spool(spool_id, data)
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={"message": "Internal server error"},
        ) from exc
    if spool is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"message": "Spool not found"},
        )
    return spool


@admin_router.get("/settings", response_model=schemas.SpoolmanAPISettings)
async def get_settings(
    db: DBSession,
    principal=RequirePermission("admin:plugins_manage"),
):
    return load_settings()


@admin_router.put("/settings", response_model=schemas.SpoolmanAPISettings)
async def update_settings(
    data: schemas.SpoolmanAPISettings,
    db: DBSession,
    principal=RequirePermission("admin:plugins_manage"),
):
    save_settings(data)
    return data
