"""Pydantic schemas for API requests and responses."""
from .requests import (
    LoginRequest,
    ChangePasswordRequest,
    EnrichmentRequest,
    UpdateLeadRequest,
    OutreachEventRequest,
    OutreachAttemptRequest,
)
from .responses import (
    OutreachAttemptResponse,
    ScoreBreakdown,
    BuildingTypeBreakdownResponse,
    ContactWithSource,
    LeadResponse,
    LeadsListResponse,
    PipelineStatus,
)

__all__ = [
    # Requests
    "LoginRequest",
    "ChangePasswordRequest",
    "EnrichmentRequest",
    "UpdateLeadRequest",
    "OutreachEventRequest",
    "OutreachAttemptRequest",
    # Responses
    "OutreachAttemptResponse",
    "ScoreBreakdown",
    "BuildingTypeBreakdownResponse",
    "ContactWithSource",
    "LeadResponse",
    "LeadsListResponse",
    "PipelineStatus",
]
