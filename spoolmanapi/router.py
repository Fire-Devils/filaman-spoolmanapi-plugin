"""Spoolman-compatible REST API router.

Exposes vendor/filament/spool endpoints that mirror the Spoolman v1 API,
plus admin endpoints for managing the plugin's IP-filter settings.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, Response, WebSocket, WebSocketDisconnect, status
from fastapi.responses import RedirectResponse

from app.api.deps import DBSession, RequirePermission

from . import schemas
from .service import SpoolmanService
from .ip_filter import check_ws_ip_access, require_ip_access
from .settings import load_settings, save_settings
from .ws import websocket_manager

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
async def list_vendors(
    response: Response,
    db: DBSession,
    name: str | None = Query(None),
    external_id: str | None = Query(None),
    sort: str | None = Query(None),
    limit: int | None = Query(None),
    offset: int = Query(0),
):
    svc = SpoolmanService(db)
    vendors, total = await svc.list_vendors(
        name=name,
        external_id=external_id,
        sort=sort,
        limit=limit,
        offset=offset,
    )
    response.headers["x-total-count"] = str(total)
    return vendors


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
async def list_filaments(
    response: Response,
    db: DBSession,
    vendor_name: str | None = Query(None, alias="vendor.name"),
    vendor_id: str | None = Query(None, alias="vendor.id"),
    name: str | None = Query(None),
    material: str | None = Query(None),
    article_number: str | None = Query(None),
    color_hex: str | None = Query(None),
    color_similarity_threshold: float = Query(20),
    external_id: str | None = Query(None),
    sort: str | None = Query(None),
    limit: int | None = Query(None),
    offset: int = Query(0),
):
    svc = SpoolmanService(db)
    filaments, total = await svc.list_filaments(
        vendor_name=vendor_name,
        vendor_id=vendor_id,
        name=name,
        material=material,
        article_number=article_number,
        color_hex=color_hex,
        external_id=external_id,
        sort=sort,
        limit=limit,
        offset=offset,
    )
    response.headers["x-total-count"] = str(total)
    return filaments


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
async def list_spools(
    response: Response,
    db: DBSession,
    filament_name: str | None = Query(None, alias="filament.name"),
    filament_id: str | None = Query(None, alias="filament.id"),
    filament_material: str | None = Query(None, alias="filament.material"),
    vendor_name: str | None = Query(None, alias="filament.vendor.name"),
    vendor_id: str | None = Query(None, alias="filament.vendor.id"),
    location: str | None = Query(None),
    lot_nr: str | None = Query(None),
    allow_archived: bool = Query(False),
    sort: str | None = Query(None),
    limit: int | None = Query(None),
    offset: int = Query(0),
):
    svc = SpoolmanService(db)
    spools, total = await svc.list_spools(
        filament_name=filament_name,
        filament_id=filament_id,
        filament_material=filament_material,
        vendor_name=vendor_name,
        vendor_id=vendor_id,
        location=location,
        lot_nr=lot_nr,
        allow_archived=allow_archived,
        sort=sort,
        limit=limit,
        offset=offset,
    )
    response.headers["x-total-count"] = str(total)
    return spools


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


@router.get("/material", response_model=list[str])
async def list_materials(db: DBSession):
    svc = SpoolmanService(db)
    return await svc.list_materials()


@router.get("/article-number", response_model=list[str])
async def list_article_numbers(db: DBSession):
    svc = SpoolmanService(db)
    return await svc.list_article_numbers()


@router.get("/lot-number", response_model=list[str])
async def list_lot_numbers(db: DBSession):
    svc = SpoolmanService(db)
    return await svc.list_lot_numbers()


@router.get("/location", response_model=list[str])
async def list_locations(db: DBSession):
    svc = SpoolmanService(db)
    return await svc.list_locations()


@router.patch("/location/{location}")
async def rename_location(location: str, data: schemas.RenameLocationBody, db: DBSession):
    svc = SpoolmanService(db)
    result = await svc.rename_location(location, data.name)
    if result is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"message": "Location not found"},
        )
    return result


@router.get("/setting/", response_model=dict[str, schemas.SettingResponse])
async def get_all_settings(db: DBSession):
    svc = SpoolmanService(db)
    return await svc.get_all_settings()


@router.get("/setting/{key}", response_model=schemas.SettingResponse)
async def get_setting(key: str, db: DBSession):
    svc = SpoolmanService(db)
    result = await svc.get_setting(key)
    if result is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"message": "Setting not found"},
        )
    return result


@router.post("/setting/{key}", response_model=schemas.SettingResponse)
async def set_setting(key: str, db: DBSession, body: str = ""):
    svc = SpoolmanService(db)
    result = await svc.set_setting(key, body)
    if result is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"message": "Setting not found"},
        )
    return result


@router.get("/field/{entity_type}", response_model=list[schemas.ExtraField])
async def get_extra_fields(entity_type: schemas.EntityType, db: DBSession):
    svc = SpoolmanService(db)
    return await svc.get_extra_fields(entity_type.value)


@router.post("/field/{entity_type}/{key}", response_model=list[schemas.ExtraField])
async def add_extra_field(
    entity_type: schemas.EntityType,
    key: str,
    data: schemas.ExtraFieldParameters,
    db: DBSession,
):
    svc = SpoolmanService(db)
    return await svc.add_extra_field(entity_type.value, key, data.model_dump())


@router.delete("/field/{entity_type}/{key}", response_model=list[schemas.ExtraField])
async def delete_extra_field(entity_type: schemas.EntityType, key: str, db: DBSession):
    svc = SpoolmanService(db)
    result = await svc.delete_extra_field(entity_type.value, key)
    if result is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"message": "Field not found"},
        )
    return result


@router.get("/export/spools")
async def export_spools(fmt: schemas.ExportFormat, db: DBSession):
    svc = SpoolmanService(db)
    data = await svc.export_spools()
    if fmt == schemas.ExportFormat.json:
        return data
    from fastapi.responses import StreamingResponse
    import csv
    import io
    output = io.StringIO()
    if data:
        writer = csv.DictWriter(output, fieldnames=data[0].keys())
        writer.writeheader()
        writer.writerows(data)
    return StreamingResponse(
        iter([output.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=spools.csv"},
    )


@router.get("/export/filaments")
async def export_filaments(fmt: schemas.ExportFormat, db: DBSession):
    svc = SpoolmanService(db)
    data = await svc.export_filaments()
    if fmt == schemas.ExportFormat.json:
        return data
    from fastapi.responses import StreamingResponse
    import csv
    import io
    output = io.StringIO()
    if data:
        writer = csv.DictWriter(output, fieldnames=data[0].keys())
        writer.writeheader()
        writer.writerows(data)
    return StreamingResponse(
        iter([output.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=filaments.csv"},
    )


@router.get("/export/vendors")
async def export_vendors(fmt: schemas.ExportFormat, db: DBSession):
    svc = SpoolmanService(db)
    data = await svc.export_vendors()
    if fmt == schemas.ExportFormat.json:
        return data
    from fastapi.responses import StreamingResponse
    import csv
    import io
    output = io.StringIO()
    if data:
        writer = csv.DictWriter(output, fieldnames=data[0].keys())
        writer.writeheader()
        writer.writerows(data)
    return StreamingResponse(
        iter([output.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=vendors.csv"},
    )


@router.get("/external/filament", response_model=list[schemas.ExternalFilament])
async def get_external_filaments(db: DBSession):
    svc = SpoolmanService(db)
    return await svc.get_external_filaments()


@router.get("/external/material", response_model=list[schemas.ExternalMaterial])
async def get_external_materials(db: DBSession):
    svc = SpoolmanService(db)
    return await svc.get_external_materials()


@router.post("/backup", response_model=schemas.BackupResponse)
async def backup(db: DBSession):
    svc = SpoolmanService(db)
    path = await svc.create_backup()
    return schemas.BackupResponse(path=path)


@admin_router.get("/settings", response_model=schemas.SpoolmanAPISettings)
async def get_settings(
    db: DBSession,
    principal=RequirePermission("admin:plugins_manage"),
):
    return await load_settings()


@admin_router.put("/settings", response_model=schemas.SpoolmanAPISettings)
async def update_settings(
    data: schemas.SpoolmanAPISettings,
    db: DBSession,
    principal=RequirePermission("admin:plugins_manage"),
):
    await save_settings(data)
    return data


# ---------------------------------------------------------------------------
# WebSocket endpoints (mirror Spoolman's push notification channels)
# ---------------------------------------------------------------------------


async def _handle_ws(websocket: WebSocket, pool: tuple[str, ...]) -> None:
    """Accept a WebSocket connection and keep it alive in the given pool.

    Responds to any incoming text with a health status (ping/pong).
    """
    if not await check_ws_ip_access(websocket):
        await websocket.close(code=1008, reason="IP not allowed")
        return
    await websocket.accept()
    websocket_manager.connect(pool, websocket)
    try:
        while True:
            await websocket.receive_text()
            await websocket.send_json({"status": "healthy"})
    except WebSocketDisconnect:
        websocket_manager.disconnect(pool, websocket)


@router.websocket("/")
async def ws_root(websocket: WebSocket) -> None:
    await _handle_ws(websocket, ())


@router.websocket("/spool")
async def ws_spool(websocket: WebSocket) -> None:
    await _handle_ws(websocket, ("spool",))


@router.websocket("/spool/{spool_id}")
async def ws_spool_id(websocket: WebSocket, spool_id: int) -> None:
    await _handle_ws(websocket, ("spool", str(spool_id)))


@router.websocket("/filament")
async def ws_filament(websocket: WebSocket) -> None:
    await _handle_ws(websocket, ("filament",))


@router.websocket("/filament/{filament_id}")
async def ws_filament_id(websocket: WebSocket, filament_id: int) -> None:
    await _handle_ws(websocket, ("filament", str(filament_id)))


@router.websocket("/vendor")
async def ws_vendor(websocket: WebSocket) -> None:
    await _handle_ws(websocket, ("vendor",))


@router.websocket("/vendor/{vendor_id}")
async def ws_vendor_id(websocket: WebSocket, vendor_id: int) -> None:
    await _handle_ws(websocket, ("vendor", str(vendor_id)))


# ---------------------------------------------------------------------------
# Wrapper router: provides a root redirect and re-exports all API routes.
# The plugin loader picks up the module-level `router` name, so we swap it
# here *after* all endpoints have been registered on the original APIRouter.
# ---------------------------------------------------------------------------

_api_router = router  # keep reference to the fully-populated API router
router = APIRouter()  # new wrapper without prefix (mounted at /spoolman)
router.include_router(_api_router)


@router.get("/", include_in_schema=False)
async def redirect_to_root():
    """Redirect bare /spoolman/ hits to the application root."""
    return RedirectResponse(url="/", status_code=302)


@router.get("", include_in_schema=False)
async def redirect_to_root_no_slash():
    """Redirect bare /spoolman (no trailing slash) to the application root."""
    return RedirectResponse(url="/", status_code=302)
