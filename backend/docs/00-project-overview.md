# Project Overview

> Note (Feb 24, 2026): This document contains historical project framing from early pipeline phases (including Google Sheets-first output). Keep for context, but follow the current architecture plan and root docs for execution.

## Current Execution Objective

Deliver the simplest reliable architecture that preserves all JTBD outcomes:

- PE sourcing (lead identification, scoring, enrichment, pipeline actions)
- PM operator workflow (building targeting and outreach)
- Operational reliability (durable jobs, observability, migration safety)

Current system output is the web app/API experience; Google Sheets references below are historical unless explicitly re-enabled.

## Goal

Generate high-quality acquisition leads for Property Management and HOA/COA management firms in NYC that Michael could potentially purchase.

## Target Profile

**Primary targets:**
- Property management firms (third-party managers)
- HOA/COA management firms

**Secondary (still ingest):**
- Owner-operators with in-house management

## Lead Criteria

A good lead has:
- 10+ buildings under management (professional scale)
- Clear contact information (phone, email, or website)
- Professional indicators (LLC/Inc structure, dedicated management entity)
- NYC-based operations

## Output

Google Sheet with:
- Contact info for direct outreach (email, phone, address)
- Portfolio size and unit count
- Business summary from website
- Owner/principal names
- Score for prioritization
- Tags for filtering (borough, size tier, enrichment status)

## Success Metrics

MVP success:
- 500+ unique leads with contact info
- 80%+ have phone or email
- 50%+ have website and business summary
- Leads sorted by portfolio size
- Daily refresh working

Stretch:
- 2000+ leads
- 90%+ enrichment rate
- AI-generated opportunity notes

## Scope

**In scope:**
- All NYC boroughs
- HPD-registered buildings (3+ units)
- Public data sources only (plus optional paid APIs)
- Google Sheet output

**Out of scope (for now):**
- Non-NYC geographies
- Single/two-family homes
- CRM integration
- Automated outreach

## Timeline

This is a "fat pitch" opportunity research tool, not a time-critical deliverable. Build it right, iterate as needed.
