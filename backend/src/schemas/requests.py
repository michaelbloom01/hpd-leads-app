"""Pydantic request models for the Double Edge API."""
from typing import List, Optional
from pydantic import BaseModel


class LoginRequest(BaseModel):
    email: str
    password: str


class ChangePasswordRequest(BaseModel):
    current_password: str
    new_password: str


class EnrichmentRequest(BaseModel):
    """Request to enrich specific leads."""
    lead_ids: List[str]
    dry_run: bool = True
    confirm_execute: bool = False


class UpdateLeadRequest(BaseModel):
    """Request to update lead status/notes/pipeline."""
    outreach_status: Optional[str] = None
    notes: Optional[str] = None
    pipeline_stage: Optional[str] = None
    next_follow_up: Optional[str] = None
    priority_rank: Optional[int] = None


class BatchPipelineStageUpdateRequest(BaseModel):
    """Request to update pipeline stage for multiple leads."""
    lead_ids: List[str]
    pipeline_stage: str


class OutreachEventRequest(BaseModel):
    """Request to log an outreach event (Phase 5.3)."""
    stage: str
    method: Optional[str] = None
    outcome: Optional[str] = None
    notes: Optional[str] = None
    next_follow_up: Optional[str] = None


class OutreachAttemptRequest(BaseModel):
    """Request to log an outreach attempt."""
    method: str
    outcome: str
    notes: Optional[str] = None
