"""Authenticated HTTP boundary for the V1.5 research workflow."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Callable, TypeVar

from fastapi import (
    APIRouter,
    Depends,
    HTTPException,
    Path as ApiPath,
    Query,
    Request,
    Response,
    status,
)

from api.core.environment import string_setting
from api.features.evidence import EvidenceLedgerUnavailable, EvidenceSnapshotLedger
from api.features.research_workflow import (
    AlternativeHypothesisCreateRequest,
    EvidenceCreateRequest,
    EvidenceSnapshotReferenceRejected,
    EvidenceSnapshotVerificationUnavailable,
    ExportManifestCreateRequest,
    HumanDecisionCreateRequest,
    InformationGapCreateRequest,
    JudgmentCreateRequest,
    MemberChangeRequest,
    ProjectCreateRequest,
    ProjectListResponse,
    QuestionCreateRequest,
    ResearchAccessDenied,
    ResearchContractConflict,
    ResearchProject,
    ResearchProjectNotFound,
    ResearchRepositoryCapacityExceeded,
    ResearchRepositoryUnavailable,
    ResearchVersionComparison,
    ResearchVersionConflict,
    ResearchWorkflowNotReady,
    ResearchWorkflowService,
    ReviewCreateRequest,
    SavedSearchCreateRequest,
    SavedSearchMonitoringUnavailable,
    SearchSnapshotReferenceRejected,
    SearchSnapshotVerificationUnavailable,
    configured_research_repository,
    build_saved_search_monitoring_status,
)
from api.features.search import SearchSnapshotLedger, SearchSnapshotUnavailable
from api.services.auth import get_current_user_required


_Result = TypeVar("_Result")
_MAX_RESEARCH_JSON_BYTES = 2 * 1024 * 1024
_ARTIFACT_RESPONSE_BOUNDARY_HEADERS = {
    "Cache-Control": "private, no-store",
    "X-Content-Type-Options": "nosniff",
    "Vary": "Authorization",
}


def _reject_duplicate_json_keys(
    pairs: list[tuple[str, Any]],
) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key")
        result[key] = value
    return result


def _reject_non_finite_json_number(_value: str) -> None:
    raise ValueError("non-finite JSON number")


async def _require_unambiguous_mutation_json(request: Request) -> None:
    if request.method not in {"POST", "PUT", "PATCH", "DELETE"}:
        return
    body = await request.body()
    if not body or len(body) > _MAX_RESEARCH_JSON_BYTES:
        raise HTTPException(
            status_code=422,
            detail={
                "code": "RESEARCH_JSON_AMBIGUOUS",
                "message": "研究工作流 JSON 正文为空或超出边界",
            },
        )
    try:
        json.loads(
            body,
            object_pairs_hook=_reject_duplicate_json_keys,
            parse_constant=_reject_non_finite_json_number,
        )
    except (TypeError, UnicodeError, ValueError) as exc:
        raise HTTPException(
            status_code=422,
            detail={
                "code": "RESEARCH_JSON_AMBIGUOUS",
                "message": "研究工作流正文必须是无重复键的有限 JSON",
            },
        ) from exc


router = APIRouter(prefix="/api/research", tags=["research-workflow"],
    dependencies=[Depends(_require_unambiguous_mutation_json)],
)


def get_research_workflow_service() -> ResearchWorkflowService:
    try:
        snapshot_reader = EvidenceSnapshotLedger(
            Path(
                string_setting(
                    "EVIDENCE_SNAPSHOT_ROOT",
                    "/root/data/web/evidence-snapshots",
                )
            )
        )
    except EvidenceLedgerUnavailable:
        snapshot_reader = None
    try:
        search_snapshot_reader = SearchSnapshotLedger(
            Path(
                string_setting(
                    "SEARCH_SNAPSHOT_ROOT",
                    "/root/data/web/search-snapshots",
                )
            )
        )
    except SearchSnapshotUnavailable:
        search_snapshot_reader = None
    return ResearchWorkflowService(
        configured_research_repository(),
        evidence_snapshot_reader=snapshot_reader,
        search_snapshot_reader=search_snapshot_reader,
    )


def _execute(operation: Callable[[], _Result]) -> _Result:
    try:
        return operation()
    except ResearchProjectNotFound as exc:
        raise HTTPException(status_code=404, detail="研究项目不存在") from exc
    except ResearchAccessDenied as exc:
        raise HTTPException(status_code=403, detail="无此研究项目权限") from exc
    except ResearchVersionConflict as exc:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "PROJECT_VERSION_CONFLICT",
                "expected_version": exc.expected,
                "current_version": exc.actual,
            },
        ) from exc
    except ResearchWorkflowNotReady as exc:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "RESEARCH_WORKFLOW_NOT_READY",
                "reason_codes": list(exc.reason_codes),
            },
        ) from exc
    except SavedSearchMonitoringUnavailable as exc:
        raise HTTPException(
            status_code=503,
            detail={
                "code": "SAVED_SEARCH_MONITORING_UNAVAILABLE",
                "reason_code": str(exc),
                "fallback": "none",
            },
        ) from exc
    except EvidenceSnapshotReferenceRejected as exc:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "EVIDENCE_SNAPSHOT_REFERENCE_REJECTED",
                "reason_code": str(exc),
            },
        ) from exc
    except EvidenceSnapshotVerificationUnavailable as exc:
        raise HTTPException(
            status_code=503,
            detail={
                "code": "EVIDENCE_SNAPSHOT_VERIFICATION_UNAVAILABLE",
                "reason_code": str(exc),
                "fallback": "none",
            },
        ) from exc
    except SearchSnapshotReferenceRejected as exc:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "SEARCH_SNAPSHOT_REFERENCE_REJECTED",
                "reason_code": str(exc),
            },
        ) from exc
    except SearchSnapshotVerificationUnavailable as exc:
        raise HTTPException(
            status_code=503,
            detail={
                "code": "SEARCH_SNAPSHOT_VERIFICATION_UNAVAILABLE",
                "reason_code": str(exc),
                "fallback": "none",
            },
        ) from exc
    except ResearchContractConflict as exc:
        raise HTTPException(
            status_code=409,
            detail={"code": "RESEARCH_CONTRACT_CONFLICT", "message": str(exc)},
        ) from exc
    except ResearchRepositoryCapacityExceeded as exc:
        raise HTTPException(
            status_code=413,
            detail={
                "code": "RESEARCH_PROJECT_CAPACITY_EXCEEDED",
                "message": str(exc),
            },
        ) from exc
    except ResearchRepositoryUnavailable as exc:
        raise HTTPException(
            status_code=503,
            detail={
                "code": "RESEARCH_STORAGE_UNAVAILABLE",
                "durability": "unavailable",
                "fallback": "none",
                "reason_code": str(exc),
            },
        ) from exc


@router.get("/storage-status")
def get_storage_status(
    _user: dict[str, Any] = Depends(get_current_user_required),
    service: ResearchWorkflowService = Depends(get_research_workflow_service),
):
    available, reason = service.repository.availability()
    if not available:
        raise HTTPException(
            status_code=503,
            detail={
                "schema_version": "research-storage-status-v1",
                "status": "unavailable",
                "durability": "unavailable",
                "fallback": "none",
                "reason_code": reason or "RESEARCH_STORE_UNAVAILABLE",
            },
        )
    return {
        "schema_version": "research-storage-status-v1",
        "status": "available",
        "backend": "filesystem:workspace-root-isolated-service-store",
        "durability": "atomic-json-fsync",
        "fallback": "none",
        "integrity_check": "sha256-sealed-state-and-history-chain",
        "audit_immutability": "unavailable",
    }


@router.post(
    "/projects",
    response_model=ResearchProject,
    status_code=status.HTTP_201_CREATED,
)
def create_project(
    body: ProjectCreateRequest,
    user: dict[str, Any] = Depends(get_current_user_required),
    service: ResearchWorkflowService = Depends(get_research_workflow_service),
):
    return _execute(lambda: service.create_project(body, user))


@router.get("/projects", response_model=ProjectListResponse)
def list_projects(
    user: dict[str, Any] = Depends(get_current_user_required),
    service: ResearchWorkflowService = Depends(get_research_workflow_service),
):
    return _execute(lambda: service.list_projects(user))


@router.get("/projects/{project_id}", response_model=ResearchProject)
def get_project(
    project_id: str,
    user: dict[str, Any] = Depends(get_current_user_required),
    service: ResearchWorkflowService = Depends(get_research_workflow_service),
):
    return _execute(lambda: service.get_project(project_id, user))


@router.get("/projects/{project_id}/saved-search-monitoring")
def get_saved_search_monitoring(
    project_id: str,
    response: Response,
    user: dict[str, Any] = Depends(get_current_user_required),
    service: ResearchWorkflowService = Depends(get_research_workflow_service),
):
    """Report monitoring gaps without scheduling or replaying a search."""
    try:
        result = _execute(
            lambda: build_saved_search_monitoring_status(
                service.get_project(project_id, user)
            )
        )
    except HTTPException as exc:
        exc.headers = {
            **(exc.headers or {}),
            **_ARTIFACT_RESPONSE_BOUNDARY_HEADERS,
        }
        raise
    response.headers.update(_ARTIFACT_RESPONSE_BOUNDARY_HEADERS)
    return result


@router.get("/projects/{project_id}/audit")
def get_project_audit(
    project_id: str,
    user: dict[str, Any] = Depends(get_current_user_required),
    service: ResearchWorkflowService = Depends(get_research_workflow_service),
):
    return _execute(lambda: service.get_audit_events(project_id, user))


@router.put("/projects/{project_id}/members/{username}", response_model=ResearchProject)
def set_project_member(
    project_id: str,
    username: str,
    body: MemberChangeRequest,
    user: dict[str, Any] = Depends(get_current_user_required),
    service: ResearchWorkflowService = Depends(get_research_workflow_service),
):
    return _execute(lambda: service.set_member(project_id, username, body, user))


@router.post("/projects/{project_id}/questions", response_model=ResearchProject)
def add_question(
    project_id: str,
    body: QuestionCreateRequest,
    user: dict[str, Any] = Depends(get_current_user_required),
    service: ResearchWorkflowService = Depends(get_research_workflow_service),
):
    return _execute(lambda: service.add_question(project_id, body, user))


@router.post("/projects/{project_id}/saved-searches", response_model=ResearchProject)
def add_saved_search(
    project_id: str,
    body: SavedSearchCreateRequest,
    user: dict[str, Any] = Depends(get_current_user_required),
    service: ResearchWorkflowService = Depends(get_research_workflow_service),
):
    return _execute(lambda: service.add_saved_search(project_id, body, user))


@router.post("/projects/{project_id}/evidence", response_model=ResearchProject)
def add_evidence(
    project_id: str,
    body: EvidenceCreateRequest,
    user: dict[str, Any] = Depends(get_current_user_required),
    service: ResearchWorkflowService = Depends(get_research_workflow_service),
):
    return _execute(lambda: service.add_evidence(project_id, body, user))


@router.post("/projects/{project_id}/information-gaps", response_model=ResearchProject)
def add_information_gap(
    project_id: str,
    body: InformationGapCreateRequest,
    user: dict[str, Any] = Depends(get_current_user_required),
    service: ResearchWorkflowService = Depends(get_research_workflow_service),
):
    return _execute(lambda: service.add_information_gap(project_id, body, user))


@router.post(
    "/projects/{project_id}/alternative-hypotheses",
    response_model=ResearchProject,
)
def add_alternative_hypothesis(
    project_id: str,
    body: AlternativeHypothesisCreateRequest,
    user: dict[str, Any] = Depends(get_current_user_required),
    service: ResearchWorkflowService = Depends(get_research_workflow_service),
):
    return _execute(
        lambda: service.add_alternative_hypothesis(project_id, body, user)
    )


@router.post("/projects/{project_id}/judgments", response_model=ResearchProject)
def add_judgment(
    project_id: str,
    body: JudgmentCreateRequest,
    user: dict[str, Any] = Depends(get_current_user_required),
    service: ResearchWorkflowService = Depends(get_research_workflow_service),
):
    return _execute(lambda: service.add_judgment(project_id, body, user))


@router.post("/projects/{project_id}/decisions", response_model=ResearchProject)
def add_human_decision(
    project_id: str,
    body: HumanDecisionCreateRequest,
    user: dict[str, Any] = Depends(get_current_user_required),
    service: ResearchWorkflowService = Depends(get_research_workflow_service),
):
    return _execute(lambda: service.add_human_decision(project_id, body, user))


@router.post("/projects/{project_id}/reviews", response_model=ResearchProject)
def add_review(
    project_id: str,
    body: ReviewCreateRequest,
    user: dict[str, Any] = Depends(get_current_user_required),
    service: ResearchWorkflowService = Depends(get_research_workflow_service),
):
    return _execute(lambda: service.add_review(project_id, body, user))


@router.post("/projects/{project_id}/exports", response_model=ResearchProject)
def create_export_manifest(
    project_id: str,
    body: ExportManifestCreateRequest,
    user: dict[str, Any] = Depends(get_current_user_required),
    service: ResearchWorkflowService = Depends(get_research_workflow_service),
):
    return _execute(lambda: service.create_export_manifest(project_id, body, user))


@router.get("/projects/{project_id}/exports/{export_version}")
def get_export_manifest(
    project_id: str,
    export_version: int = ApiPath(ge=1),
    user: dict[str, Any] = Depends(get_current_user_required),
    service: ResearchWorkflowService = Depends(get_research_workflow_service),
):
    return _execute(
        lambda: service.get_export_manifest(project_id, export_version, user)
    )


@router.get("/projects/{project_id}/exports/{export_version}/artifact")
def download_export_artifact(
    project_id: str,
    export_version: int = ApiPath(ge=1),
    format: str | None = Query(default=None),
    fields: list[str] | None = Query(default=None),
    user: dict[str, Any] = Depends(get_current_user_required),
    service: ResearchWorkflowService = Depends(get_research_workflow_service),
):
    try:
        artifact = _execute(
            lambda: service.get_export_artifact(
                project_id,
                export_version,
                format,  # type: ignore[arg-type]
                user,
                export_fields=fields,
            )
        )
    except HTTPException as exc:
        exc.headers = {
            **(exc.headers or {}),
            **_ARTIFACT_RESPONSE_BOUNDARY_HEADERS,
        }
        raise
    headers = {
        "Content-Disposition": f'attachment; filename="{artifact.filename}"',
        **_ARTIFACT_RESPONSE_BOUNDARY_HEADERS,
        "ETag": f'"sha256-{artifact.response_sha256}"',
        "X-Research-Artifact-Schema": artifact.schema_version,
        "X-Research-Artifact-Format": artifact.artifact_format,
        "X-Research-Artifact-SHA256": artifact.response_sha256,
        "X-Research-Report-Content-SHA256": artifact.report_content_sha256,
        "X-Research-Manifest-SHA256": artifact.manifest_integrity_sha256,
        "X-Research-Publication-Status": artifact.publication_status,
        "X-Researcher-Acceptance": artifact.researcher_acceptance,
        "X-Research-Distribution-Status": artifact.distribution_status,
        "X-Research-Field-Selection-Schema": (
            artifact.field_selection_schema_version
        ),
        "X-Research-Export-Fields": ",".join(artifact.selected_fields),
        "X-Research-Source-License-Status": artifact.source_license_status,
    }
    if artifact.artifact_format == "html":
        headers["Content-Security-Policy"] = (
            "default-src 'none'; base-uri 'none'; form-action 'none'; "
            "frame-ancestors 'none'; sandbox"
        )
    return Response(
        content=artifact.body,
        media_type=artifact.media_type,
        headers=headers,
    )


@router.get(
    "/projects/{project_id}/export-comparisons",
    response_model=ResearchVersionComparison,
)
def compare_export_manifests(
    project_id: str,
    from_export_version: int = Query(ge=1),
    to_export_version: int = Query(ge=1),
    user: dict[str, Any] = Depends(get_current_user_required),
    service: ResearchWorkflowService = Depends(get_research_workflow_service),
):
    return _execute(
        lambda: service.compare_export_manifests(
            project_id,
            from_export_version=from_export_version,
            to_export_version=to_export_version,
            user=user,
        )
    )


__all__ = ("get_research_workflow_service", "router")
