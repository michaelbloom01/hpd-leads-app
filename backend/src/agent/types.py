"""
Agent types — the single source of truth for all API contracts, tool schemas,
and structured data models used by the HPD Leads AI Agent.
"""

from pydantic import BaseModel, Field
from typing import Optional, Literal
from enum import Enum


# ---------------------------------------------------------------------------
# Request types
# ---------------------------------------------------------------------------

class AgentConfirmationPayload(BaseModel):
    action_id: str
    confirmed: bool  # True = confirm, False = cancel


class AgentChatRequest(BaseModel):
    message: str = Field("", max_length=4000)  # Empty string allowed when confirming
    conversation_id: Optional[str] = None
    confirmation: Optional[AgentConfirmationPayload] = None
    # Exactly one of `message` or `confirmation` must be provided.
    # Validated in the endpoint handler, not the model (keeps the model simple).


# ---------------------------------------------------------------------------
# SSE Event Types
# ---------------------------------------------------------------------------

class SSEEventType(str, Enum):
    status = "status"                              # data: str (transient, replaced in-place on frontend)
    tool_call = "tool_call"                        # data: {"name": str, "input": dict}
    partial_text = "partial"                       # data: str (append to current message)
    leads = "leads"                                # data: AgentLeadRow[] (max 50)
    scripts = "scripts"                            # data: {"lead_id": str, "company_name": str, "script": str}[]
    briefing_preview = "briefing_preview"          # data: {"briefing_id": str, "html": str, "lead_count": int}
    rent_comparison = "rent_comparison"             # data: AgentRentComparison[]
    needs_confirmation = "needs_confirmation"       # data: {"action_id": str, "description": str, "count": int}
    actions = "actions"                            # data: str[] (list of action descriptions)
    error = "error"                                # data: str
    done = "done"                                  # data: {"conversation_id": str}


# ---------------------------------------------------------------------------
# Structured data models for SSE payloads
# ---------------------------------------------------------------------------

class AgentLeadRow(BaseModel):
    lead_id: str
    company_name: str               # agent_name fallback if company_name is null
    portfolio_size: int
    total_units: int
    score: float
    estimated_annual_revenue: float
    enrichment_status: str
    phone: Optional[str] = None
    email: Optional[str] = None
    website: Optional[str] = None
    boros: list[str]
    violations_per_unit: float
    pipeline_stage: str


class AgentScriptRow(BaseModel):
    lead_id: str
    company_name: str
    phone: Optional[str] = None
    owner_name: Optional[str] = None
    script: str


class AgentRentComparison(BaseModel):
    lead_id: str
    company_name: str
    current_monthly_rent: float         # From AVG_MONTHLY_RENT
    refined_monthly_rent: float         # From research
    delta_pct: float                    # (refined - current) / current * 100
    current_annual_revenue: float
    refined_annual_revenue: float
    confidence: Literal["high", "medium", "low"]
    sources: list[str]                  # ["NYC Open Data", "StreetEasy", ...]


class AgentBriefingPreview(BaseModel):
    briefing_id: str                    # UUID, used to fetch/send later
    html: str                           # Rendered HTML for preview
    lead_count: int


class AgentConfirmationRequest(BaseModel):
    action_id: str                      # UUID for idempotency
    description: str                    # "Move 20 leads to first_contact stage"
    count: int                          # Number of affected items
    tool_name: str                      # The tool that will execute on confirm
    tool_input: dict                    # The exact args that will be passed


# ---------------------------------------------------------------------------
# Conversation Memory types
# ---------------------------------------------------------------------------

class ConversationSummary(BaseModel):
    conversation_id: str
    title: str
    message_count: int
    created_at: str
    updated_at: str


class ConversationMessage(BaseModel):
    message_id: str
    role: Literal["user", "assistant"]
    content: str                            # Display text
    structured_data: Optional[dict] = None  # {leads: [...], scripts: [...], etc.} for re-rendering
    claude_messages: Optional[list] = None  # Full Claude API messages for this turn (for resumption)
    created_at: str


# ---------------------------------------------------------------------------
# Tool Schemas (Claude tool-use format)
# ---------------------------------------------------------------------------
# These are passed directly to the Anthropic API as the `tools` parameter.
# Each tool has a name, description, and input_schema (JSON Schema).
# The agent selects from these — it never writes raw SQL.
# ---------------------------------------------------------------------------

TOOL_SCHEMAS = [
    {
        "name": "query_leads",
        "description": (
            "Search and filter the 102k+ property management leads database. "
            "All parameters are optional — omit a parameter to not filter on that dimension. "
            "Returns up to 50 leads matching the criteria, plus total_count."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "min_units": {
                    "type": "integer",
                    "description": "Minimum total residential units managed"
                },
                "max_units": {
                    "type": "integer",
                    "description": "Maximum total residential units managed"
                },
                "min_score": {
                    "type": "number",
                    "description": "Minimum lead score (0-100)"
                },
                "max_score": {
                    "type": "number",
                    "description": "Maximum lead score (0-100)"
                },
                "boro": {
                    "type": "string",
                    "description": "Borough filter. One of: MANHATTAN, BROOKLYN, QUEENS, BRONX, STATEN ISLAND",
                    "enum": ["MANHATTAN", "BROOKLYN", "QUEENS", "BRONX", "STATEN ISLAND"]
                },
                "building_type_has": {
                    "type": "string",
                    "description": "Filter for leads with at least one building of this type",
                    "enum": ["condo", "coop", "rental_elevator", "rental_walkup", "small_residential"]
                },
                "entity_type": {
                    "type": "string",
                    "description": "Legal entity classification",
                    "enum": ["company", "individual_agent", "owner_operator"]
                },
                "enrichment_status": {
                    "type": "string",
                    "description": "Enrichment data status",
                    "enum": ["none", "partial", "complete", "failed"]
                },
                "pipeline_stage": {
                    "type": "string",
                    "description": "Current pipeline stage",
                    "enum": [
                        "research", "first_contact", "follow_up",
                        "meeting_scheduled", "meeting_done",
                        "loi", "due_diligence", "closed"
                    ]
                },
                "has_phone": {
                    "type": "boolean",
                    "description": "If true, only leads with a phone number"
                },
                "has_email": {
                    "type": "boolean",
                    "description": "If true, only leads with an email address"
                },
                "has_website": {
                    "type": "boolean",
                    "description": "If true, only leads with a website"
                },
                "min_violations_per_unit": {
                    "type": "number",
                    "description": "Minimum HPD violations per unit ratio"
                },
                "max_violations_per_unit": {
                    "type": "number",
                    "description": "Maximum HPD violations per unit ratio"
                },
                "min_revenue": {
                    "type": "number",
                    "description": "Minimum estimated annual management fee revenue ($)"
                },
                "max_revenue": {
                    "type": "number",
                    "description": "Maximum estimated annual management fee revenue ($)"
                },
                "search": {
                    "type": "string",
                    "description": "Free-text search across company name, agent name, owner name"
                },
                "sort_by": {
                    "type": "string",
                    "description": "Sort field",
                    "enum": ["score", "total_units", "portfolio_size", "estimated_annual_revenue", "violations_per_unit"],
                    "default": "score"
                },
                "sort_dir": {
                    "type": "string",
                    "description": "Sort direction",
                    "enum": ["asc", "desc"],
                    "default": "desc"
                },
                "limit": {
                    "type": "integer",
                    "description": "Max results to return (1-50)",
                    "default": 20
                }
            },
            "required": []
        }
    },
    {
        "name": "get_lead_details",
        "description": (
            "Get the full details for a single lead by ID. Returns all fields including "
            "buildings, contacts, score breakdown, violations, revenue breakdown, AI summary."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "lead_id": {
                    "type": "string",
                    "description": "The lead_id to look up"
                }
            },
            "required": ["lead_id"]
        }
    },
    {
        "name": "update_leads_batch",
        "description": (
            "Update pipeline stage, priority rank, or next follow-up date for one or more leads. "
            "Requires user confirmation before executing."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "lead_ids": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "List of lead_ids to update"
                },
                "pipeline_stage": {
                    "type": "string",
                    "description": "New pipeline stage to set",
                    "enum": [
                        "research", "first_contact", "follow_up",
                        "meeting_scheduled", "meeting_done",
                        "loi", "due_diligence", "closed"
                    ]
                },
                "priority_rank": {
                    "type": "integer",
                    "description": "Priority ranking 0-5 (0=unranked, 5=highest)",
                    "minimum": 0,
                    "maximum": 5
                },
                "next_follow_up": {
                    "type": "string",
                    "description": "Next follow-up date in YYYY-MM-DD format"
                }
            },
            "required": ["lead_ids"]
        }
    },
    {
        "name": "enrich_leads_batch",
        "description": (
            "Start background enrichment for up to 20 leads. Enrichment finds phone numbers, "
            "emails, websites, scrapes company info, and generates AI summaries. "
            "Requires user confirmation before executing. Results appear within minutes."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "lead_ids": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "List of lead_ids to enrich (max 20)",
                    "maxItems": 20
                }
            },
            "required": ["lead_ids"]
        }
    },
    {
        "name": "generate_cold_call_scripts",
        "description": (
            "Generate personalized cold call scripts for up to 10 leads. Scripts are "
            "personalized using the owner name, portfolio size, building types, boroughs, "
            "violations profile, and AI summary."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "lead_ids": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "List of lead_ids to generate scripts for (max 10)",
                    "maxItems": 10
                }
            },
            "required": ["lead_ids"]
        }
    },
    {
        "name": "compile_email_briefing",
        "description": (
            "Compile an HTML email briefing summarizing the specified leads. "
            "If SMTP is configured, the email can be sent directly. Otherwise, "
            "returns the HTML for download."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "lead_ids": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "List of lead_ids to include in the briefing"
                },
                "subject": {
                    "type": "string",
                    "description": "Email subject line (optional — auto-generated if omitted)"
                },
                "recipient_email": {
                    "type": "string",
                    "description": "Recipient email address (optional — uses default if omitted)"
                }
            },
            "required": ["lead_ids"]
        }
    },
    {
        "name": "refine_rent_estimates",
        "description": (
            "Research actual market rents for up to 15 leads using NYC Open Data and "
            "public sources. Compares current borough-level estimates against real data "
            "to show revenue upside or downside."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "lead_ids": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "List of lead_ids to refine rent estimates for (max 15)",
                    "maxItems": 15
                }
            },
            "required": ["lead_ids"]
        }
    },
    {
        "name": "get_stats",
        "description": (
            "Get aggregate statistics about the entire leads database: total leads, "
            "total buildings, total units, enrichment coverage, pipeline distribution, "
            "score distribution, borough breakdown."
        ),
        "input_schema": {
            "type": "object",
            "properties": {},
            "required": []
        }
    }
]
