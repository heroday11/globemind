"""Read-only routes for bounded authoritative-data connectors."""

from __future__ import annotations

from datetime import date
from typing import Any, TypeVar

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ValidationError

from api.features.authoritative_data import (
    AuthoritativeCatalogResponse,
    AuthoritativeDataService,
    AuthoritativeQueryResponse,
    CountryInstitutionCatalogResponse,
    CountryPrimaryDocumentCatalogResponse,
    CountryProfileCatalogResponse,
    CrossrefQuery,
    ImfQuery,
    UnSdgQuery,
    WorldBankQuery,
)
from api.services.auth import get_current_user_required


router = APIRouter(
    prefix="/api/authoritative-data",
    tags=["authoritative-data"],
)
_service = AuthoritativeDataService()
ModelT = TypeVar("ModelT", bound=BaseModel)


def get_authoritative_data_service() -> AuthoritativeDataService:
    return _service


def _validated(model_type: type[ModelT], **values: Any) -> ModelT:
    try:
        return model_type(**values)
    except ValidationError as exc:
        detail = [
            {
                "loc": error.get("loc", ()),
                "msg": error.get("msg", "invalid query"),
                "type": error.get("type", "value_error"),
            }
            for error in exc.errors()
        ]
        raise HTTPException(status_code=422, detail=detail) from exc


def _render(response: AuthoritativeQueryResponse):
    if response.available:
        return response
    return JSONResponse(status_code=503, content=jsonable_encoder(response))


@router.get("/catalog", response_model=AuthoritativeCatalogResponse)
def authoritative_catalog(
    service: AuthoritativeDataService = Depends(get_authoritative_data_service),
):
    """Return registrations only; this endpoint never performs a live probe."""

    return service.catalog()


@router.get(
    "/country-profiles/catalog",
    response_model=CountryProfileCatalogResponse,
)
def country_profile_catalog(
    service: AuthoritativeDataService = Depends(get_authoritative_data_service),
):
    """Expose the static profile schema; no country facts are read or fetched."""

    return service.country_profile_catalog()


@router.get(
    "/country-profiles/institutions/catalog",
    response_model=CountryInstitutionCatalogResponse,
)
def country_institution_catalog(
    response: Response,
    service: AuthoritativeDataService = Depends(get_authoritative_data_service),
):
    """Expose institution schema requirements; no country facts are read."""

    response.headers["Cache-Control"] = "no-store"
    response.headers["X-Content-Type-Options"] = "nosniff"
    return service.country_institution_catalog()


@router.get(
    "/country-profiles/primary-documents/catalog",
    response_model=CountryPrimaryDocumentCatalogResponse,
)
def country_primary_document_catalog(
    response: Response,
    service: AuthoritativeDataService = Depends(get_authoritative_data_service),
):
    """Expose primary-document requirements; no document or network is read."""

    response.headers["Cache-Control"] = "no-store"
    response.headers["X-Content-Type-Options"] = "nosniff"
    return service.country_primary_document_catalog()


@router.get("/world-bank", response_model=AuthoritativeQueryResponse)
async def world_bank_query(
    country: str = Query(pattern=r"^[A-Z0-9]{2,3}$"),
    indicator: str = Query(pattern=r"^[A-Z0-9][A-Z0-9_.-]{1,63}$"),
    start_year: int | None = Query(default=None, ge=1900, le=2100),
    end_year: int | None = Query(default=None, ge=1900, le=2100),
    limit: int = Query(default=24, ge=1, le=100),
    refresh: bool = Query(default=False),
    _user: dict[str, Any] = Depends(get_current_user_required),
    service: AuthoritativeDataService = Depends(get_authoritative_data_service),
):
    query = _validated(
        WorldBankQuery,
        country=country,
        indicator=indicator,
        start_year=start_year,
        end_year=end_year,
        limit=limit,
    )
    return _render(await service.world_bank(query, refresh=refresh))


@router.get("/imf", response_model=AuthoritativeQueryResponse)
async def imf_query(
    indicator: str = Query(pattern=r"^[A-Z0-9][A-Z0-9_.-]{1,63}$"),
    entity: list[str] = Query(min_length=1, max_length=5),
    period: list[int] = Query(default_factory=list, max_length=20),
    limit: int = Query(default=50, ge=1, le=100),
    refresh: bool = Query(default=False),
    _user: dict[str, Any] = Depends(get_current_user_required),
    service: AuthoritativeDataService = Depends(get_authoritative_data_service),
):
    query = _validated(
        ImfQuery,
        indicator=indicator,
        entities=entity,
        periods=period,
        limit=limit,
    )
    return _render(await service.imf(query, refresh=refresh))


@router.get("/un-sdg", response_model=AuthoritativeQueryResponse)
async def un_sdg_query(
    series_code: str = Query(pattern=r"^[A-Z0-9][A-Z0-9_.~-]{1,79}$"),
    area_code: int = Query(ge=0, le=999),
    start_year: int | None = Query(default=None, ge=1900, le=2100),
    end_year: int | None = Query(default=None, ge=1900, le=2100),
    limit: int = Query(default=25, ge=1, le=50),
    refresh: bool = Query(default=False),
    _user: dict[str, Any] = Depends(get_current_user_required),
    service: AuthoritativeDataService = Depends(get_authoritative_data_service),
):
    query = _validated(
        UnSdgQuery,
        series_code=series_code,
        area_code=area_code,
        start_year=start_year,
        end_year=end_year,
        limit=limit,
    )
    return _render(await service.un_sdg(query, refresh=refresh))


@router.get("/crossref", response_model=AuthoritativeQueryResponse)
async def crossref_query(
    query: str = Query(min_length=2, max_length=200),
    from_index_date: date | None = Query(default=None),
    until_index_date: date | None = Query(default=None),
    limit: int = Query(default=10, ge=1, le=20),
    refresh: bool = Query(default=False),
    _user: dict[str, Any] = Depends(get_current_user_required),
    service: AuthoritativeDataService = Depends(get_authoritative_data_service),
):
    request = _validated(
        CrossrefQuery,
        query=query,
        from_index_date=from_index_date,
        until_index_date=until_index_date,
        limit=limit,
    )
    return _render(await service.crossref(request, refresh=refresh))


__all__ = (
    "get_authoritative_data_service",
    "router",
)
