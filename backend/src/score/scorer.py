"""
Lead scoring logic - V3.

Scoring dimensions (6 total):
1. Condo/Co-op concentration (35%) — % of portfolio buildings that are condo or co-op
2. Density (20%) — unit-to-building ratio, ideal > 15
3. Units (15%) — sweet spot 50-300 total units
4. Location (10%) — Manhattan bonus
5. Professional (10%) — entity type, corporate indicators
6. Contact (10%) — phone, email, website completeness

Hard filter: leads with < 10 total units get score = 0.
"""
import logging
from pathlib import Path
from typing import List

import yaml

from src.transform.aggregate import Lead

logger = logging.getLogger(__name__)


class Scorer:
    """Score and tier leads based on configurable rules (V3)."""

    def __init__(self, config_path: str = "config/scoring_weights.yaml"):
        self.config = self._load_config(config_path)
        self.weights = self.config.get("weights", {})
        self.professional_keywords = self.config.get("professional_keywords", [])
        self.tier_labels = self.config.get("tier_labels", {})
        self.min_units = self.config.get("min_units_threshold", 10)

    def _load_config(self, path: str) -> dict:
        config_path = Path(path)
        if not config_path.exists():
            logger.warning(f"Config not found at {path}, using defaults")
            return {}
        with open(config_path) as f:
            return yaml.safe_load(f)

    # ------------------------------------------------------------------
    # Main entry point
    # ------------------------------------------------------------------

    def score_lead(self, lead: Lead) -> Lead:
        """
        Score a single lead using V3 dimensions.

        Weights:
        - condo_coop:    0.35
        - density:       0.20
        - units:         0.15
        - location:      0.10
        - professional:  0.10
        - contact:       0.10
        """
        # Hard filter: leads below minimum unit threshold score 0
        if lead.total_units < self.min_units:
            lead.score = 0.0
            lead.score_breakdown = {
                "condo_coop": 0, "density": 0, "units": 0,
                "location": 0, "professional": 0, "contact": 0,
            }
            lead.tags = self._generate_tags(lead)
            return lead

        # Calculate each dimension (0-100)
        condo_coop_score = self._score_condo_coop(lead)
        density_score = self._score_density(lead)
        units_score = self._score_units(lead.total_units)
        location_score = self._score_location(lead)
        professional_score = self._score_professional(lead)
        contact_score = self._score_contact(lead)

        # Weighted sum
        total_score = (
            self.weights.get("condo_coop", 0.35) * condo_coop_score
            + self.weights.get("density", 0.20) * density_score
            + self.weights.get("units", 0.15) * units_score
            + self.weights.get("location", 0.10) * location_score
            + self.weights.get("professional", 0.10) * professional_score
            + self.weights.get("contact", 0.10) * contact_score
        )
        total_score = min(total_score, 100)

        lead.score = round(total_score, 1)
        lead.score_breakdown = {
            "condo_coop": round(condo_coop_score, 1),
            "density": round(density_score, 1),
            "units": round(units_score, 1),
            "location": round(location_score, 1),
            "professional": round(professional_score, 1),
            "contact": round(contact_score, 1),
        }
        lead.tags = self._generate_tags(lead)
        return lead

    # ------------------------------------------------------------------
    # Scoring components
    # ------------------------------------------------------------------

    def _score_condo_coop(self, lead: Lead) -> float:
        """
        Score based on what percentage of the portfolio's buildings are
        condos or co-ops.  Uses building_types.condo + building_types.coop
        divided by portfolio_size.
        """
        bt = getattr(lead, "building_types", None)
        if not bt or lead.portfolio_size <= 0:
            return 0

        condo_coop_count = (getattr(bt, "condo", 0) or 0) + (getattr(bt, "coop", 0) or 0)
        ratio = condo_coop_count / lead.portfolio_size

        thresholds = self.config.get("condo_coop_thresholds", {})
        if ratio >= thresholds.get("tier_100", 0.80):
            return 100
        elif ratio >= thresholds.get("tier_85", 0.60):
            return 85
        elif ratio >= thresholds.get("tier_65", 0.40):
            return 65
        elif ratio >= thresholds.get("tier_40", 0.20):
            return 40
        elif ratio > 0:
            return 20
        return 0

    def _score_density(self, lead: Lead) -> float:
        """
        Score based on unit-to-building ratio (total_units / portfolio_size).
        Ideal profile: ratio > 15.
        """
        if lead.portfolio_size <= 0:
            return 0

        ratio = lead.total_units / lead.portfolio_size

        thresholds = self.config.get("density_thresholds", {})
        if ratio >= thresholds.get("tier_100", 20):
            return 100
        elif ratio >= thresholds.get("tier_90", 15):
            return 90
        elif ratio >= thresholds.get("tier_60", 10):
            return 60
        elif ratio >= thresholds.get("tier_30", 5):
            return 30
        return 10

    def _score_units(self, units: int) -> float:
        """
        Score based on total unit count.  Sweet spot is 50-300.
        """
        spot = self.config.get("unit_sweet_spot", {})
        sweet_min = spot.get("min", 50)
        sweet_max = spot.get("max", 300)
        close_low = spot.get("close_low", 30)
        close_high = spot.get("close_high", 500)
        viable_high = spot.get("viable_high", 1000)

        if sweet_min <= units <= sweet_max:
            return 100  # Sweet spot
        elif close_low <= units < sweet_min or sweet_max < units <= close_high:
            return 60  # Close to ideal
        elif units <= viable_high:
            return 30  # Viable but not ideal
        return 15  # Very large (> 1000)

    def _score_location(self, lead: Lead) -> float:
        """
        Manhattan gets full points; all other boroughs get a baseline score.
        """
        loc = self.config.get("location_scores", {})
        manhattan_score = loc.get("manhattan", 100)
        default_score = loc.get("default", 50)

        boro = (lead.boro or "").upper().strip()
        if boro == "MANHATTAN":
            return manhattan_score
        return default_score

    def _score_professional(self, lead: Lead) -> float:
        """Score based on entity type and corporate indicators (unchanged from V2)."""
        score = 0

        if lead.owner_type and lead.owner_type.upper() in ("AGENT", "MANAGEMENT"):
            score += 40

        name = (lead.agent_name or "").upper()
        if any(suffix in name for suffix in ["LLC", "INC", "CORP", "LP"]):
            score += 30

        for keyword in self.professional_keywords:
            if keyword.upper() in name:
                score += 20
                break

        entity_type = getattr(lead, "entity_type", "unknown")
        if entity_type == "company":
            score += 15
        elif entity_type == "individual_agent" and lead.portfolio_size >= 30:
            score += 5
        elif entity_type == "owner_operator":
            score -= 10

        return min(max(score, 0), 100)

    def _score_contact(self, lead: Lead) -> float:
        """Score based on contactability (unchanged from V2)."""
        score = 0
        if lead.phone:
            score += 30
        if lead.email:
            score += 30
        if lead.website:
            score += 20
        if lead.business_summary:
            score += 10
        if lead.owner_principal:
            score += 10
        return min(score, 100)

    # ------------------------------------------------------------------
    # Tags & tiers
    # ------------------------------------------------------------------

    def _generate_tags(self, lead: Lead) -> List[str]:
        tags = []

        # Condo/co-op concentration
        bt = getattr(lead, "building_types", None)
        if bt and lead.portfolio_size > 0:
            condo_coop_count = (getattr(bt, "condo", 0) or 0) + (getattr(bt, "coop", 0) or 0)
            ratio = condo_coop_count / lead.portfolio_size
            if ratio >= 0.80:
                tags.append("condo_coop_heavy")
            elif ratio >= 0.40:
                tags.append("condo_coop_mixed")
            elif ratio > 0:
                tags.append("condo_coop_some")

        # Density (unit-to-building ratio)
        if lead.portfolio_size > 0:
            density = lead.total_units / lead.portfolio_size
            if density >= 15:
                tags.append("high_density")
            elif density >= 10:
                tags.append("medium_density")

        # Unit sweet spot
        if 50 <= lead.total_units <= 300:
            tags.append("sweet_spot_units")

        # Location
        boro = (lead.boro or "").upper().strip()
        if boro:
            tags.append(boro.lower().replace(" ", "_"))
        if boro == "MANHATTAN":
            tags.append("manhattan_target")

        # Professional
        if lead.owner_type and lead.owner_type.upper() in ("AGENT", "MANAGEMENT"):
            tags.append("professional_mgmt")
        else:
            tags.append("owner_operator")

        # Entity type
        entity_type = getattr(lead, "entity_type", "unknown")
        if entity_type and entity_type != "unknown":
            tags.append(f"entity_{entity_type}")

        # Contact
        if lead.website:
            tags.append("has_website")
        if lead.email:
            tags.append("has_email")
        if lead.phone:
            tags.append("has_phone")

        # Geographic focus
        if lead.boros and len(lead.boros) == 1:
            tags.append("single_borough_focus")
        elif lead.boros and len(lead.boros) <= 2:
            tags.append("concentrated")

        # Tier
        tier = self._get_tier(lead.score)
        tags.append(f"tier_{tier.lower()}")

        return tags

    def _get_tier(self, score: float) -> str:
        if score >= 80:
            return "A"
        elif score >= 60:
            return "B"
        elif score >= 40:
            return "C"
        elif score >= 20:
            return "D"
        return "F"


def score_leads(leads: List[Lead]) -> List[Lead]:
    """Score a list of leads using V3 scorer."""
    scorer = Scorer()
    return [scorer.score_lead(lead) for lead in leads]
