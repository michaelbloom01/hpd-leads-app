"""Legacy tables preserved from the SQLite schema.

These support existing features (enrichment cache, DOS cache, agent chat)
and will be migrated to new patterns in later phases. Keeping them ensures
zero data loss during the PostgreSQL migration.
"""
from datetime import datetime
from typing import Optional

from sqlalchemy import DateTime, ForeignKey, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base, TimestampMixin


class LeadUserData(TimestampMixin, Base):
    """Legacy per-lead user overrides (notes, outreach status)."""

    __tablename__ = "lead_user_data"

    lead_id: Mapped[str] = mapped_column(String(12), primary_key=True)
    outreach_status: Mapped[Optional[str]] = mapped_column(
        String(20), server_default="new"
    )
    notes: Mapped[Optional[str]] = mapped_column(Text)


class EnrichmentCache(TimestampMixin, Base):
    """Legacy enrichment cache (backup to leads table enrichment fields)."""

    __tablename__ = "enrichment_cache"

    lead_id: Mapped[str] = mapped_column(String(12), primary_key=True)
    phone: Mapped[Optional[str]] = mapped_column(String(20))
    email: Mapped[Optional[str]] = mapped_column(Text)
    website: Mapped[Optional[str]] = mapped_column(Text)
    business_summary: Mapped[Optional[str]] = mapped_column(Text)
    owner_principal: Mapped[Optional[str]] = mapped_column(Text)
    enrichment_status: Mapped[Optional[str]] = mapped_column(String(20))
    enrichment_sources: Mapped[Optional[str]] = mapped_column(Text)
    last_enriched: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))


class OutreachAttempt(TimestampMixin, Base):
    """Legacy outreach attempt log."""

    __tablename__ = "outreach_attempts"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    lead_id: Mapped[str] = mapped_column(String(12), nullable=False)
    method: Mapped[str] = mapped_column(String(20), nullable=False)
    outcome: Mapped[str] = mapped_column(String(30), nullable=False)
    notes: Mapped[Optional[str]] = mapped_column(Text)
    timestamp: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))

    __table_args__ = (Index("idx_outreach_lead", "lead_id"),)


class EnrichmentJob(TimestampMixin, Base):
    """Legacy enrichment job tracking."""

    __tablename__ = "enrichment_jobs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    job_type: Mapped[str] = mapped_column(String(20), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    total_leads: Mapped[Optional[int]] = mapped_column(Integer, server_default="0")
    processed: Mapped[Optional[int]] = mapped_column(Integer, server_default="0")
    completed: Mapped[Optional[int]] = mapped_column(Integer, server_default="0")
    failed: Mapped[Optional[int]] = mapped_column(Integer, server_default="0")
    dos_found: Mapped[Optional[int]] = mapped_column(Integer, server_default="0")
    web_found: Mapped[Optional[int]] = mapped_column(Integer, server_default="0")
    current_lead: Mapped[Optional[str]] = mapped_column(Text)
    lead_ids_remaining: Mapped[Optional[str]] = mapped_column(Text)
    config: Mapped[Optional[str]] = mapped_column(Text)
    error: Mapped[Optional[str]] = mapped_column(Text)
    started_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))

    __table_args__ = (Index("idx_enrichment_jobs_status", "status"),)


class DOSCache(Base):
    """NY DOS lookup cache with TTL."""

    __tablename__ = "dos_cache"

    cache_key: Mapped[str] = mapped_column(String(255), primary_key=True)
    result: Mapped[Optional[str]] = mapped_column(Text)
    cached_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))


class PlacesCache(Base):
    """Google Places lookup cache with TTL."""

    __tablename__ = "places_cache"

    cache_key: Mapped[str] = mapped_column(String(255), primary_key=True)
    result: Mapped[Optional[str]] = mapped_column(Text)
    cached_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))


class AISummaryCache(Base):
    """Anthropic AI summary cache per lead."""

    __tablename__ = "ai_summary_cache"

    lead_id: Mapped[str] = mapped_column(String(12), primary_key=True)
    summary: Mapped[Optional[str]] = mapped_column(Text)
    model: Mapped[Optional[str]] = mapped_column(String(50))
    cached_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))


class AppSetting(Base):
    """Key-value store for persistent application state."""

    __tablename__ = "app_settings"

    key: Mapped[str] = mapped_column(String(100), primary_key=True)
    value: Mapped[Optional[str]] = mapped_column(Text)
    updated_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))


class AgentConversation(TimestampMixin, Base):
    """AI agent conversation sessions."""

    __tablename__ = "agent_conversations"

    conversation_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    title: Mapped[Optional[str]] = mapped_column(Text)


class AgentMessage(TimestampMixin, Base):
    """Individual messages in agent conversations."""

    __tablename__ = "agent_messages"

    message_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    conversation_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("agent_conversations.conversation_id", ondelete="CASCADE"),
        nullable=False,
    )
    role: Mapped[str] = mapped_column(String(10), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    structured_data: Mapped[Optional[str]] = mapped_column(Text)
    claude_messages: Mapped[Optional[str]] = mapped_column(Text)

    __table_args__ = (
        Index("idx_agent_messages_conv", "conversation_id", "created_at"),
    )


class AgentPendingAction(TimestampMixin, Base):
    """Pending agent tool actions awaiting user confirmation."""

    __tablename__ = "agent_pending_actions"

    action_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    conversation_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("agent_conversations.conversation_id", ondelete="CASCADE"),
        nullable=False,
    )
    tool_name: Mapped[str] = mapped_column(String(50), nullable=False)
    tool_input: Mapped[str] = mapped_column(Text, nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
