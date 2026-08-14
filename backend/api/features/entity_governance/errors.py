"""Stable entity-governance failure categories."""


class EntityGovernanceError(RuntimeError):
    pass


class EntityGovernanceAccessDenied(EntityGovernanceError):
    pass


class EntityGovernanceNotFound(EntityGovernanceError):
    pass


class EntityGovernanceConflict(EntityGovernanceError):
    pass


class EntityGovernanceUnavailable(EntityGovernanceError):
    pass


class EntityEvidenceReferenceRejected(EntityGovernanceConflict):
    pass


class EntityEvidenceVerificationBlocked(EntityGovernanceUnavailable):
    pass


__all__ = (
    "EntityEvidenceReferenceRejected",
    "EntityEvidenceVerificationBlocked",
    "EntityGovernanceAccessDenied",
    "EntityGovernanceConflict",
    "EntityGovernanceError",
    "EntityGovernanceNotFound",
    "EntityGovernanceUnavailable",
)
