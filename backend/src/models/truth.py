"""Claim ledger, confidence, review, and benchmark models."""

from __future__ import annotations

from datetime import date, datetime
from typing import Optional

from sqlalchemy import BigInteger, Boolean, Date, DateTime, Float, ForeignKey, Index, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base, TimestampMixin


class TruthClaim(TimestampMixin, Base):
    __tablename__ = "truth_claims"

    claim_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    subject_type: Mapped[str] = mapped_column(String(30), nullable=False)
    subject_id: Mapped[str] = mapped_column(String(80), nullable=False)
    predicate: Mapped[str] = mapped_column(String(80), nullable=False)
    object_type: Mapped[Optional[str]] = mapped_column(String(30))
    object_id: Mapped[Optional[str]] = mapped_column(String(80))
    extracted_value: Mapped[Optional[str]] = mapped_column(Text)
    normalized_value: Mapped[Optional[str]] = mapped_column(Text)
    claim_type: Mapped[str] = mapped_column(String(50), nullable=False)
    belief_status: Mapped[str] = mapped_column(String(30), server_default="proposed")
    confidence_score: Mapped[Optional[float]] = mapped_column(Float)
    freshness_days: Mapped[Optional[int]] = mapped_column(Integer)
    observed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    valid_from: Mapped[Optional[date]] = mapped_column(Date)
    valid_to: Mapped[Optional[date]] = mapped_column(Date)
    current_flag: Mapped[bool] = mapped_column(Boolean, server_default="true")
    actionability_level: Mapped[Optional[str]] = mapped_column(String(40))
    rationale: Mapped[Optional[dict]] = mapped_column(JSONB)

    __table_args__ = (
        Index("idx_truth_claims_subject", "subject_type", "subject_id"),
        Index("idx_truth_claims_object", "object_type", "object_id"),
        Index("idx_truth_claims_predicate", "predicate"),
        Index("idx_truth_claims_belief_status", "belief_status"),
        Index("idx_truth_claims_actionability", "actionability_level"),
    )


class TruthEvidence(TimestampMixin, Base):
    __tablename__ = "truth_evidence"

    evidence_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    claim_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("truth_claims.claim_id", ondelete="CASCADE"), nullable=False
    )
    source_name: Mapped[str] = mapped_column(String(60), nullable=False)
    source_type: Mapped[str] = mapped_column(String(40), nullable=False)
    source_record_id: Mapped[Optional[str]] = mapped_column(String(120))
    source_url: Mapped[Optional[str]] = mapped_column(Text)
    observed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    extracted_value: Mapped[Optional[str]] = mapped_column(Text)
    normalized_value: Mapped[Optional[str]] = mapped_column(Text)
    support_status: Mapped[str] = mapped_column(String(20), nullable=False)
    source_quality_score: Mapped[Optional[float]] = mapped_column(Float)
    evidence_weight: Mapped[Optional[float]] = mapped_column(Float)
    raw_payload: Mapped[Optional[dict]] = mapped_column(JSONB)

    __table_args__ = (
        Index("idx_truth_evidence_claim", "claim_id"),
        Index("idx_truth_evidence_source", "source_name"),
        Index("idx_truth_evidence_support", "support_status"),
    )


class ConfidenceSnapshot(TimestampMixin, Base):
    __tablename__ = "confidence_snapshots"

    snapshot_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    entity_type: Mapped[str] = mapped_column(String(30), nullable=False)
    entity_id: Mapped[str] = mapped_column(String(80), nullable=False)
    confidence_scope: Mapped[str] = mapped_column(String(50), nullable=False)
    confidence_score: Mapped[float] = mapped_column(Float, nullable=False)
    actionability_level: Mapped[str] = mapped_column(String(40), nullable=False)
    supporting_claim_count: Mapped[int] = mapped_column(Integer, server_default="0")
    contradicting_claim_count: Mapped[int] = mapped_column(Integer, server_default="0")
    stale_claim_count: Mapped[int] = mapped_column(Integer, server_default="0")
    computed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    rationale: Mapped[Optional[dict]] = mapped_column(JSONB)
    run_id: Mapped[Optional[str]] = mapped_column(String(80))

    __table_args__ = (
        Index("idx_confidence_snapshots_entity", "entity_type", "entity_id"),
        Index("idx_confidence_snapshots_scope", "confidence_scope"),
        Index("idx_confidence_snapshots_run", "run_id"),
    )


class TruthReviewItem(TimestampMixin, Base):
    __tablename__ = "truth_review_items"

    review_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    queue_name: Mapped[str] = mapped_column(String(50), nullable=False)
    subject_type: Mapped[str] = mapped_column(String(30), nullable=False)
    subject_id: Mapped[str] = mapped_column(String(80), nullable=False)
    status: Mapped[str] = mapped_column(String(30), server_default="open")
    priority: Mapped[int] = mapped_column(Integer, server_default="50")
    confidence_score: Mapped[Optional[float]] = mapped_column(Float)
    actionability_level: Mapped[Optional[str]] = mapped_column(String(40))
    proposed_change: Mapped[Optional[dict]] = mapped_column(JSONB)
    supporting_evidence: Mapped[Optional[dict]] = mapped_column(JSONB)
    contradicting_evidence: Mapped[Optional[dict]] = mapped_column(JSONB)
    rationale: Mapped[Optional[dict]] = mapped_column(JSONB)
    run_id: Mapped[Optional[str]] = mapped_column(String(80))
    reviewed_by: Mapped[Optional[str]] = mapped_column(String(255))
    reviewed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        Index("idx_truth_review_items_queue", "queue_name"),
        Index("idx_truth_review_items_status", "status"),
        Index("idx_truth_review_items_subject", "subject_type", "subject_id"),
        Index("idx_truth_review_items_run", "run_id"),
    )


class GoldenVerificationCase(TimestampMixin, Base):
    __tablename__ = "golden_verification_cases"

    case_id: Mapped[str] = mapped_column(String(80), primary_key=True)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    case_type: Mapped[str] = mapped_column(String(50), nullable=False)
    subject_type: Mapped[str] = mapped_column(String(30), nullable=False)
    subject_id: Mapped[Optional[str]] = mapped_column(String(80))
    expected_outcome: Mapped[str] = mapped_column(String(80), nullable=False)
    expected_claims: Mapped[Optional[dict]] = mapped_column(JSONB)
    tricky_features: Mapped[Optional[list]] = mapped_column(JSONB)
    source_notes: Mapped[Optional[str]] = mapped_column(Text)
    active: Mapped[bool] = mapped_column(Boolean, server_default="true")

    __table_args__ = (
        Index("idx_golden_cases_type", "case_type"),
        Index("idx_golden_cases_active", "active"),
    )


class TruthValidationRun(TimestampMixin, Base):
    __tablename__ = "truth_validation_runs"

    run_id: Mapped[str] = mapped_column(String(80), primary_key=True)
    run_type: Mapped[str] = mapped_column(String(50), nullable=False)
    dry_run: Mapped[bool] = mapped_column(Boolean, server_default="true")
    status: Mapped[str] = mapped_column(String(30), server_default="planned")
    started_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    scope: Mapped[Optional[dict]] = mapped_column(JSONB)
    metrics: Mapped[Optional[dict]] = mapped_column(JSONB)
    sample_findings: Mapped[Optional[dict]] = mapped_column(JSONB)
    rollback_strategy: Mapped[Optional[str]] = mapped_column(Text)

    __table_args__ = (
        Index("idx_truth_validation_runs_type", "run_type"),
        Index("idx_truth_validation_runs_status", "status"),
    )


class TruthMaterializationManifest(TimestampMixin, Base):
    __tablename__ = "truth_materialization_manifest"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    run_id: Mapped[str] = mapped_column(String(80), nullable=False)
    item_type: Mapped[str] = mapped_column(String(30), nullable=False)
    item_id: Mapped[str] = mapped_column(String(120), nullable=False)
    was_existing: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="false")
    before_snapshot: Mapped[Optional[dict]] = mapped_column(JSONB)

    __table_args__ = (
        Index("idx_truth_materialization_manifest_run", "run_id"),
        Index("idx_truth_materialization_manifest_type", "item_type"),
        Index("idx_truth_materialization_manifest_existing", "was_existing"),
    )
