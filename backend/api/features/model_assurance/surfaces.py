"""Bounded, content-free inventory of public model-output surfaces.

This module inventories source boundaries; it is deliberately not a deployment
registry.  A route name, UI label, configured provider, or model path is not a
runtime attestation, so the current inventory keeps runtime identity fields
unknown/unavailable until a separately verified attestation source exists.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

MODEL_OUTPUT_SURFACE_SCHEMA_VERSION = "globemind.model-output-surfaces.v1"
MODEL_OUTPUT_SURFACE_INVENTORY_REVISION = "model-output-surfaces-2026-08-09.1"

_RUNTIME_REASON_CODES = (
    "RUNTIME_MODEL_ATTESTATION_NOT_AVAILABLE",
    "DEPLOYMENT_TIME_NOT_AVAILABLE",
    "CHANGE_NOTES_NOT_AVAILABLE",
)


class InventoryField(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    status: Literal["unknown", "not_available", "attested"]
    value: str | None = Field(default=None, max_length=2_000)

    @model_validator(mode="after")
    def validate_status_value(self) -> "InventoryField":
        if self.status in {"unknown", "not_available"} and self.value is not None:
            raise ValueError("unknown/unavailable inventory fields cannot carry values")
        if self.status == "attested" and not self.value:
            raise ValueError("attested inventory fields require a value")
        return self


class ModelSurfaceIdentity(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    model_id: InventoryField
    model_version: InventoryField
    deployed_at: InventoryField
    change_notes: InventoryField


class RuntimeModelAttestation(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    status: Literal["not_available", "attested"]
    attestation_id: str | None = Field(default=None, max_length=200)
    observed_at: str | None = Field(default=None, max_length=80)

    @model_validator(mode="after")
    def validate_attestation(self) -> "RuntimeModelAttestation":
        values = (self.attestation_id, self.observed_at)
        if self.status == "not_available" and any(value is not None for value in values):
            raise ValueError("unavailable runtime attestation cannot carry metadata")
        if self.status == "attested" and not all(values):
            raise ValueError("attested runtime state requires an id and observation time")
        return self


class SourceLocator(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    path: str = Field(pattern=r"^[A-Za-z0-9_.-]+(?:/[A-Za-z0-9_.-]+)+$")
    locator: str = Field(min_length=4, max_length=300)


class ModelOutputSurface(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    surface_id: str = Field(pattern=r"^[a-z][a-z0-9.-]{2,79}$")
    domain: Literal[
        "article",
        "assistant",
        "financial",
        "opinion",
        "story_graph",
    ]
    output_kind: Literal["classification", "derived", "generative"]
    route_patterns: tuple[str, ...] = Field(min_length=1, max_length=20)
    ui_surfaces: tuple[str, ...] = Field(min_length=1, max_length=10)
    identity_contract_fields: tuple[str, ...] = Field(max_length=20)
    identity: ModelSurfaceIdentity
    runtime_attestation: RuntimeModelAttestation
    source_locators: tuple[SourceLocator, ...] = Field(min_length=2, max_length=20)
    reason_codes: tuple[str, ...] = Field(min_length=3, max_length=20)

    @model_validator(mode="after")
    def validate_fail_closed_identity(self) -> "ModelOutputSurface":
        if self.runtime_attestation.status != "not_available":
            raise ValueError("inventory v1 has no runtime attestation source")
        if self.identity.model_id.status != "not_available":
            raise ValueError("model id is absent from current output contracts")
        expected_version_status = (
            "unknown"
            if "model_version" in self.identity_contract_fields
            else "not_available"
        )
        if self.identity.model_version.status != expected_version_status:
            raise ValueError("model version state conflicts with the output contract")
        if self.identity.deployed_at.status != "not_available":
            raise ValueError("deployment time is not available")
        if self.identity.change_notes.status != "not_available":
            raise ValueError("change notes are not available")
        if not set(_RUNTIME_REASON_CODES).issubset(self.reason_codes):
            raise ValueError("surface is missing fail-closed runtime reason codes")
        if len(set(self.route_patterns)) != len(self.route_patterns):
            raise ValueError("surface route patterns must be unique")
        if len(set(self.ui_surfaces)) != len(self.ui_surfaces):
            raise ValueError("surface UI paths must be unique")
        if len(set(self.identity_contract_fields)) != len(
            self.identity_contract_fields
        ):
            raise ValueError("identity contract fields must be unique")
        return self


class ModelOutputSurfaceInventory(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["globemind.model-output-surfaces.v1"] = (
        MODEL_OUTPUT_SURFACE_SCHEMA_VERSION
    )
    inventory_revision: Literal["model-output-surfaces-2026-08-09.1"] = (
        MODEL_OUTPUT_SURFACE_INVENTORY_REVISION
    )
    scope: Literal["bounded_public_model_output_surfaces"] = (
        "bounded_public_model_output_surfaces"
    )
    coverage_state: Literal["source_located"] = "source_located"
    complete_runtime_deployment_claim: Literal[False] = False
    runtime_attestation_state: Literal["not_available"] = "not_available"
    reason_codes: tuple[str, ...]
    surfaces: tuple[ModelOutputSurface, ...] = Field(min_length=1, max_length=50)

    @model_validator(mode="after")
    def validate_inventory(self) -> "ModelOutputSurfaceInventory":
        ids = [surface.surface_id for surface in self.surfaces]
        if ids != sorted(ids) or len(set(ids)) != len(ids):
            raise ValueError("surface ids must be sorted and unique")
        if "BOUNDED_INVENTORY_ONLY" not in self.reason_codes:
            raise ValueError("bounded inventory reason is required")
        if "RUNTIME_MODEL_ATTESTATION_NOT_AVAILABLE" not in self.reason_codes:
            raise ValueError("runtime attestation limitation is required")
        return self


@dataclass(frozen=True)
class SourceCoverageIssue:
    code: Literal[
        "SOURCE_PATH_INVALID",
        "SOURCE_PATH_UNAVAILABLE",
        "SOURCE_LOCATOR_MISSING",
        "SOURCE_LOCATOR_AMBIGUOUS",
    ]
    surface_id: str
    path: str


def _unattested_identity(*, model_version_available: bool) -> ModelSurfaceIdentity:
    return ModelSurfaceIdentity(
        model_id=InventoryField(status="not_available"),
        model_version=InventoryField(
            status="unknown" if model_version_available else "not_available"
        ),
        deployed_at=InventoryField(status="not_available"),
        change_notes=InventoryField(status="not_available"),
    )


def _unavailable_attestation() -> RuntimeModelAttestation:
    return RuntimeModelAttestation(status="not_available")


def _surface(
    *,
    surface_id: str,
    domain: Literal["article", "assistant", "financial", "opinion", "story_graph"],
    output_kind: Literal["classification", "derived", "generative"],
    route_patterns: tuple[str, ...],
    ui_surfaces: tuple[str, ...],
    identity_contract_fields: tuple[str, ...],
    source_locators: tuple[tuple[str, str], ...],
) -> ModelOutputSurface:
    return ModelOutputSurface(
        surface_id=surface_id,
        domain=domain,
        output_kind=output_kind,
        route_patterns=route_patterns,
        ui_surfaces=ui_surfaces,
        identity_contract_fields=identity_contract_fields,
        identity=_unattested_identity(
            model_version_available="model_version" in identity_contract_fields
        ),
        runtime_attestation=_unavailable_attestation(),
        source_locators=tuple(
            SourceLocator(path=path, locator=locator)
            for path, locator in source_locators
        ),
        reason_codes=_RUNTIME_REASON_CODES,
    )


def build_model_output_surface_inventory() -> ModelOutputSurfaceInventory:
    """Return the deterministic source inventory without reading runtime config."""

    surfaces = (
        _surface(
            surface_id="article.opinion-detail",
            domain="article",
            output_kind="classification",
            route_patterns=("POST /api/dashboard/search",),
            ui_surfaces=(
                "frontend/vue_project/src/views/DataService/news-detail.vue",
            ),
            identity_contract_fields=(),
            source_locators=(
                (
                    "backend/api/routes/search.py",
                    '@router.post("/api/dashboard/search", response_model=SearchResponse',
                ),
                (
                    "frontend/vue_project/src/views/DataService/news-detail.vue",
                    '<div class="news-detail" :class="{ split: showTranslationPanel }"',
                ),
            ),
        ),
        _surface(
            surface_id="assistant.interactive",
            domain="assistant",
            output_kind="generative",
            route_patterns=(
                "POST /api/ai/analyze",
                "POST /api/ai/analyze/stream",
                "POST /api/assistant/chat",
                "POST /api/assistant/cc/stream",
            ),
            ui_surfaces=(
                "frontend/vue_project/src/features/assistant/AssistantExperience.vue",
            ),
            identity_contract_fields=(),
            source_locators=(
                (
                    "backend/api/routes/assistant.py",
                    '@router.post("/api/assistant/chat", response_model=AssistantChatResponse',
                ),
                (
                    "frontend/vue_project/src/features/assistant/AssistantExperience.vue",
                    "const chatStreamController = createChatStreamController(assistantApi)",
                ),
            ),
        ),
        _surface(
            surface_id="assistant.scheduled-report",
            domain="assistant",
            output_kind="generative",
            route_patterns=("POST /api/assistant/schedules/{schedule_id}/run",),
            ui_surfaces=(
                "frontend/vue_project/src/features/assistant/AssistantExperience.vue",
            ),
            identity_contract_fields=(),
            source_locators=(
                (
                    "backend/api/routes/assistant_schedules.py",
                    '@router.post("/schedules/{schedule_id}/run")',
                ),
                (
                    "frontend/vue_project/src/features/assistant/AssistantExperience.vue",
                    "async function runBriefingSchedule(item = null)",
                ),
            ),
        ),
        _surface(
            surface_id="financial.derived-indicators",
            domain="financial",
            output_kind="derived",
            route_patterns=(
                "GET /api/financial/dashboard",
                "GET /api/financial/indices",
                "GET /api/financial/alert/**",
            ),
            ui_surfaces=(
                "frontend/financial-terminal/src/pages/TerminalDashboard.tsx",
            ),
            identity_contract_fields=("model_version", "method_version"),
            source_locators=(
                (
                    "backend/api/routes/financial.py",
                    '@router.get("/dashboard")',
                ),
                (
                    "frontend/financial-terminal/src/pages/TerminalDashboard.tsx",
                    "export default function TerminalDashboard()",
                ),
            ),
        ),
        _surface(
            surface_id="opinion.aggregate",
            domain="opinion",
            output_kind="classification",
            route_patterns=("GET /opinion/**",),
            ui_surfaces=("frontend/vue_project/src/views/sentimentAnalysis.vue",),
            identity_contract_fields=("model_version", "method_version"),
            source_locators=(
                (
                    "backend/api/routes/opinion_v2.py",
                    '@router.get("/opinion/overview", tags=["舆情"])',
                ),
                (
                    "frontend/vue_project/src/views/sentimentAnalysis.vue",
                    '<h1 class="sentiment-sr-only">智能舆情分析</h1>',
                ),
            ),
        ),
        _surface(
            surface_id="story-graph.derived-relations",
            domain="story_graph",
            output_kind="derived",
            route_patterns=("GET /api/story-graph/**",),
            ui_surfaces=("frontend/vue_project/src/views/StoryGraphView.vue",),
            identity_contract_fields=(),
            source_locators=(
                (
                    "backend/api/routes/story_graph.py",
                    '@router.get("/api/story-graph/list")',
                ),
                (
                    "frontend/vue_project/src/views/StoryGraphView.vue",
                    '<main class="intel-canvas-panel" data-tour="story-canvas">',
                ),
            ),
        ),
    )
    return ModelOutputSurfaceInventory(
        reason_codes=(
            "BOUNDED_INVENTORY_ONLY",
            "RUNTIME_MODEL_ATTESTATION_NOT_AVAILABLE",
            "STATIC_SOURCE_COVERAGE_IS_NOT_DEPLOYMENT_PROOF",
        ),
        surfaces=surfaces,
    )


def audit_model_output_surface_sources(
    repository_root: Path,
    inventory: ModelOutputSurfaceInventory | None = None,
) -> tuple[SourceCoverageIssue, ...]:
    """Verify declared source locators without returning source contents."""

    root = repository_root.resolve()
    declared = inventory or build_model_output_surface_inventory()
    issues: list[SourceCoverageIssue] = []
    for surface in declared.surfaces:
        for source in surface.source_locators:
            candidate = root / source.path
            try:
                resolved = candidate.resolve(strict=True)
                resolved.relative_to(root)
            except (FileNotFoundError, OSError, ValueError):
                issues.append(
                    SourceCoverageIssue(
                        code="SOURCE_PATH_INVALID",
                        surface_id=surface.surface_id,
                        path=source.path,
                    )
                )
                continue
            if candidate.is_symlink() or not resolved.is_file():
                issues.append(
                    SourceCoverageIssue(
                        code="SOURCE_PATH_UNAVAILABLE",
                        surface_id=surface.surface_id,
                        path=source.path,
                    )
                )
                continue
            try:
                if resolved.stat().st_size > 5 * 1024 * 1024:
                    raise OSError("source exceeds audit bound")
                content = resolved.read_text(encoding="utf-8")
            except (OSError, UnicodeError):
                issues.append(
                    SourceCoverageIssue(
                        code="SOURCE_PATH_UNAVAILABLE",
                        surface_id=surface.surface_id,
                        path=source.path,
                    )
                )
                continue
            occurrences = content.count(source.locator)
            if occurrences == 0:
                code = "SOURCE_LOCATOR_MISSING"
            elif occurrences > 1:
                code = "SOURCE_LOCATOR_AMBIGUOUS"
            else:
                continue
            issues.append(
                SourceCoverageIssue(
                    code=code,
                    surface_id=surface.surface_id,
                    path=source.path,
                )
            )
    return tuple(issues)


__all__ = (
    "MODEL_OUTPUT_SURFACE_INVENTORY_REVISION",
    "MODEL_OUTPUT_SURFACE_SCHEMA_VERSION",
    "InventoryField",
    "ModelOutputSurface",
    "ModelOutputSurfaceInventory",
    "ModelSurfaceIdentity",
    "RuntimeModelAttestation",
    "SourceCoverageIssue",
    "SourceLocator",
    "audit_model_output_surface_sources",
    "build_model_output_surface_inventory",
)
