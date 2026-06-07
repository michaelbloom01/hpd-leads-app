"""Add truth claim ledger and confidence program tables.

Revision ID: 009_truth_confidence_program
Revises: 008_lead_lineage
Create Date: 2026-05-14
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "009_truth_confidence_program"
down_revision: Union[str, None] = "008_lead_lineage"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(sa.text("""
        CREATE TABLE IF NOT EXISTS truth_claims (
            claim_id VARCHAR(36) PRIMARY KEY,
            subject_type VARCHAR(30) NOT NULL,
            subject_id VARCHAR(80) NOT NULL,
            predicate VARCHAR(80) NOT NULL,
            object_type VARCHAR(30),
            object_id VARCHAR(80),
            extracted_value TEXT,
            normalized_value TEXT,
            claim_type VARCHAR(50) NOT NULL,
            belief_status VARCHAR(30) DEFAULT 'proposed',
            confidence_score DOUBLE PRECISION,
            freshness_days INTEGER,
            observed_at TIMESTAMPTZ,
            valid_from DATE,
            valid_to DATE,
            current_flag BOOLEAN DEFAULT true,
            actionability_level VARCHAR(40),
            rationale JSONB,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        );
    """))
    op.execute(sa.text("CREATE INDEX IF NOT EXISTS idx_truth_claims_subject ON truth_claims (subject_type, subject_id);"))
    op.execute(sa.text("CREATE INDEX IF NOT EXISTS idx_truth_claims_object ON truth_claims (object_type, object_id);"))
    op.execute(sa.text("CREATE INDEX IF NOT EXISTS idx_truth_claims_predicate ON truth_claims (predicate);"))
    op.execute(sa.text("CREATE INDEX IF NOT EXISTS idx_truth_claims_belief_status ON truth_claims (belief_status);"))
    op.execute(sa.text("CREATE INDEX IF NOT EXISTS idx_truth_claims_actionability ON truth_claims (actionability_level);"))

    op.execute(sa.text("""
        CREATE TABLE IF NOT EXISTS truth_evidence (
            evidence_id VARCHAR(36) PRIMARY KEY,
            claim_id VARCHAR(36) NOT NULL REFERENCES truth_claims(claim_id) ON DELETE CASCADE,
            source_name VARCHAR(60) NOT NULL,
            source_type VARCHAR(40) NOT NULL,
            source_record_id VARCHAR(120),
            source_url TEXT,
            observed_at TIMESTAMPTZ,
            extracted_value TEXT,
            normalized_value TEXT,
            support_status VARCHAR(20) NOT NULL,
            source_quality_score DOUBLE PRECISION,
            evidence_weight DOUBLE PRECISION,
            raw_payload JSONB,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        );
    """))
    op.execute(sa.text("CREATE INDEX IF NOT EXISTS idx_truth_evidence_claim ON truth_evidence (claim_id);"))
    op.execute(sa.text("CREATE INDEX IF NOT EXISTS idx_truth_evidence_source ON truth_evidence (source_name);"))
    op.execute(sa.text("CREATE INDEX IF NOT EXISTS idx_truth_evidence_support ON truth_evidence (support_status);"))

    op.execute(sa.text("""
        CREATE TABLE IF NOT EXISTS confidence_snapshots (
            snapshot_id VARCHAR(36) PRIMARY KEY,
            entity_type VARCHAR(30) NOT NULL,
            entity_id VARCHAR(80) NOT NULL,
            confidence_scope VARCHAR(50) NOT NULL,
            confidence_score DOUBLE PRECISION NOT NULL,
            actionability_level VARCHAR(40) NOT NULL,
            supporting_claim_count INTEGER DEFAULT 0,
            contradicting_claim_count INTEGER DEFAULT 0,
            stale_claim_count INTEGER DEFAULT 0,
            computed_at TIMESTAMPTZ NOT NULL,
            rationale JSONB,
            run_id VARCHAR(80),
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        );
    """))
    op.execute(sa.text("CREATE INDEX IF NOT EXISTS idx_confidence_snapshots_entity ON confidence_snapshots (entity_type, entity_id);"))
    op.execute(sa.text("CREATE INDEX IF NOT EXISTS idx_confidence_snapshots_scope ON confidence_snapshots (confidence_scope);"))
    op.execute(sa.text("CREATE INDEX IF NOT EXISTS idx_confidence_snapshots_run ON confidence_snapshots (run_id);"))

    op.execute(sa.text("""
        CREATE TABLE IF NOT EXISTS truth_review_items (
            review_id VARCHAR(36) PRIMARY KEY,
            queue_name VARCHAR(50) NOT NULL,
            subject_type VARCHAR(30) NOT NULL,
            subject_id VARCHAR(80) NOT NULL,
            status VARCHAR(30) DEFAULT 'open',
            priority INTEGER DEFAULT 50,
            confidence_score DOUBLE PRECISION,
            actionability_level VARCHAR(40),
            proposed_change JSONB,
            supporting_evidence JSONB,
            contradicting_evidence JSONB,
            rationale JSONB,
            run_id VARCHAR(80),
            reviewed_by VARCHAR(255),
            reviewed_at TIMESTAMPTZ,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        );
    """))
    op.execute(sa.text("CREATE INDEX IF NOT EXISTS idx_truth_review_items_queue ON truth_review_items (queue_name);"))
    op.execute(sa.text("CREATE INDEX IF NOT EXISTS idx_truth_review_items_status ON truth_review_items (status);"))
    op.execute(sa.text("CREATE INDEX IF NOT EXISTS idx_truth_review_items_subject ON truth_review_items (subject_type, subject_id);"))
    op.execute(sa.text("CREATE INDEX IF NOT EXISTS idx_truth_review_items_run ON truth_review_items (run_id);"))

    op.execute(sa.text("""
        CREATE TABLE IF NOT EXISTS golden_verification_cases (
            case_id VARCHAR(80) PRIMARY KEY,
            name TEXT NOT NULL,
            case_type VARCHAR(50) NOT NULL,
            subject_type VARCHAR(30) NOT NULL,
            subject_id VARCHAR(80),
            expected_outcome VARCHAR(80) NOT NULL,
            expected_claims JSONB,
            tricky_features JSONB,
            source_notes TEXT,
            active BOOLEAN DEFAULT true,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        );
    """))
    op.execute(sa.text("CREATE INDEX IF NOT EXISTS idx_golden_cases_type ON golden_verification_cases (case_type);"))
    op.execute(sa.text("CREATE INDEX IF NOT EXISTS idx_golden_cases_active ON golden_verification_cases (active);"))

    op.execute(sa.text("""
        CREATE TABLE IF NOT EXISTS truth_validation_runs (
            run_id VARCHAR(80) PRIMARY KEY,
            run_type VARCHAR(50) NOT NULL,
            dry_run BOOLEAN DEFAULT true,
            status VARCHAR(30) DEFAULT 'planned',
            started_at TIMESTAMPTZ,
            finished_at TIMESTAMPTZ,
            scope JSONB,
            metrics JSONB,
            sample_findings JSONB,
            rollback_strategy TEXT,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        );
    """))
    op.execute(sa.text("CREATE INDEX IF NOT EXISTS idx_truth_validation_runs_type ON truth_validation_runs (run_type);"))
    op.execute(sa.text("CREATE INDEX IF NOT EXISTS idx_truth_validation_runs_status ON truth_validation_runs (status);"))


def downgrade() -> None:
    op.execute(sa.text("DROP TABLE IF EXISTS truth_validation_runs"))
    op.execute(sa.text("DROP TABLE IF EXISTS golden_verification_cases"))
    op.execute(sa.text("DROP TABLE IF EXISTS truth_review_items"))
    op.execute(sa.text("DROP TABLE IF EXISTS confidence_snapshots"))
    op.execute(sa.text("DROP TABLE IF EXISTS truth_evidence"))
    op.execute(sa.text("DROP TABLE IF EXISTS truth_claims"))
