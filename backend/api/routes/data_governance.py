"""Public, read-only data governance catalog endpoints."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from api.core.db import get_db
from api.features.data_governance import (
    CatalogKind,
    CatalogRecord,
    DataCatalogResponse,
    build_data_catalog,
    collect_catalog_health,
    unavailable_data_catalog,
)


router = APIRouter(prefix="/api/data-governance", tags=["data-governance"])


def _catalog_response(
    db: Session,
    *,
    kind: CatalogKind | None = None,
) -> DataCatalogResponse:
    return build_data_catalog(health_checks=collect_catalog_health(db), kind=kind)


@router.get("/catalog", response_model=DataCatalogResponse)
def get_data_catalog(
    kind: CatalogKind | None = Query(default=None),
    db: Session = Depends(get_db),
):
    try:
        return _catalog_response(db, kind=kind)
    except Exception:
        payload = unavailable_data_catalog()
        return JSONResponse(status_code=503, content=jsonable_encoder(payload))


@router.get("/catalog/{record_id}", response_model=CatalogRecord)
def get_data_catalog_record(
    record_id: str,
    db: Session = Depends(get_db),
):
    try:
        catalog = _catalog_response(db)
    except Exception:
        payload = unavailable_data_catalog()
        return JSONResponse(status_code=503, content=jsonable_encoder(payload))
    record = next((item for item in catalog.records if item.record_id == record_id), None)
    if record is None:
        raise HTTPException(status_code=404, detail="catalog record not found")
    return record


__all__ = ("router",)
