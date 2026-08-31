"""SQLAlchemy 2.0 models for Double Edge Platform.

Import all models here so Alembic's `target_metadata = Base.metadata`
discovers every table.
"""
from .base import Base, TimestampMixin

from .auth import User
from .building import Building, BuildingManagement
from .building_identity import (
    BuildingIdentityQuarantine,
    BuildingParcelLink,
    HPDRefreshRollbackRow,
    HPDRegistrationSnapshot,
    PhysicalBuilding,
)
from .compliance import (
    ComplianceBalanceObservation,
    ComplianceObservation,
    ComplianceRecord,
    ComplianceSourceCheck,
)
from .contacts import BuildingContact, EnrichmentResult
from .compliance_reviews import ComplianceReview
from .entity import (
    CanonicalEntity,
    CanonicalEntityAlias,
    CanonicalEntityBuilding,
    CanonicalEntityLead,
    CanonicalEntityMatchProposal,
)
from .jobs import IngestionJob
from .lead import Lead
from .legacy import (
    AgentConversation,
    AgentMessage,
    AgentPendingAction,
    AISummaryCache,
    AppSetting,
    DOSCache,
    EnrichmentCache,
    EnrichmentJob,
    LeadUserData,
    OutreachAttempt,
    PlacesCache,
)
from .pipeline import ChangeAlert, OutreachEvent
from .quality import DataQualityLog
from .reference import PADAddress
from .scoring import BuildingScoreHistory, ScoringConfig
from .signals import (
    ACRISTransaction,
    AEPDesignation,
    DOBPermit,
    EmergencyRepair,
    EnergyGrade,
    EvictionFiling,
    FacadeInspection,
    HPDComplaint,
    HPDLitigation,
    HPDViolation,
)
from .targets import AcquisitionThesis, TargetList, TargetListItem, TargetMatch
from .truth import (
    ConfidenceSnapshot,
    GoldenVerificationCase,
    TruthClaim,
    TruthEvidence,
    TruthMaterializationManifest,
    TruthReviewItem,
    TruthValidationRun,
)

__all__ = [
    "Base",
    "TimestampMixin",
    # Core entities
    "Building",
    "BuildingManagement",
    "PhysicalBuilding",
    "BuildingParcelLink",
    "HPDRegistrationSnapshot",
    "BuildingIdentityQuarantine",
    "HPDRefreshRollbackRow",
    "ComplianceRecord",
    "ComplianceObservation",
    "ComplianceSourceCheck",
    "ComplianceBalanceObservation",
    "ComplianceReview",
    "Lead",
    "CanonicalEntity",
    "CanonicalEntityAlias",
    "CanonicalEntityLead",
    "CanonicalEntityBuilding",
    "CanonicalEntityMatchProposal",
    "AcquisitionThesis",
    "TargetList",
    "TargetListItem",
    "TargetMatch",
    "TruthClaim",
    "TruthEvidence",
    "ConfidenceSnapshot",
    "TruthMaterializationManifest",
    "TruthReviewItem",
    "GoldenVerificationCase",
    "TruthValidationRun",
    # Signals
    "HPDComplaint",
    "ACRISTransaction",
    "DOBPermit",
    "HPDViolation",
    "EnergyGrade",
    "HPDLitigation",
    "EmergencyRepair",
    "AEPDesignation",
    "EvictionFiling",
    "FacadeInspection",
    # Reference
    "PADAddress",
    # Contacts & enrichment
    "BuildingContact",
    "EnrichmentResult",
    # Pipeline
    "OutreachEvent",
    "ChangeAlert",
    # Jobs & quality
    "IngestionJob",
    "DataQualityLog",
    # Scoring
    "ScoringConfig",
    "BuildingScoreHistory",
    # Auth
    "User",
    # Legacy
    "LeadUserData",
    "EnrichmentCache",
    "OutreachAttempt",
    "EnrichmentJob",
    "DOSCache",
    "PlacesCache",
    "AISummaryCache",
    "AppSetting",
    "AgentConversation",
    "AgentMessage",
    "AgentPendingAction",
]
