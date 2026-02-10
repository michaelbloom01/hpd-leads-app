"""
Lead scoring logic - V2 (Phase 5.6).

Scoring dimensions:
1. Portfolio (size of managed portfolio)
2. Units (total residential units)
3. Professional (entity type, corporate indicators)
4. Contact (phone, email, website completeness)
5. Concentration (geographic focus)
6. Revenue (estimated management fee revenue) - NEW in V2
7. Distress (HPD violations as signal) - NEW in V2
8. Deal Fit (composite PE criteria match) - NEW in V2

See docs/03-scoring-rules.md for details.
"""
import logging
from pathlib import Path
from typing import List

import yaml

from src.transform.aggregate import Lead

logger = logging.getLogger(__name__)


class Scorer:
    """Score and tier leads based on configurable rules (V2)."""
    
    def __init__(self, config_path: str = "config/scoring_weights.yaml"):
        self.config = self._load_config(config_path)
        self.weights = self.config.get("weights", {})
        self.portfolio_thresholds = self.config.get("portfolio_thresholds", {})
        self.unit_thresholds = self.config.get("unit_thresholds", {})
        self.professional_keywords = self.config.get("professional_keywords", [])
        self.tier_labels = self.config.get("tier_labels", {})
    
    def _load_config(self, path: str) -> dict:
        config_path = Path(path)
        if not config_path.exists():
            logger.warning(f"Config not found at {path}, using defaults")
            return {}
        with open(config_path) as f:
            return yaml.safe_load(f)
    
    def score_lead(self, lead: Lead) -> Lead:
        """
        Calculate V2 score with all dimensions.
        
        V2 weights (when all data available):
        - portfolio:      0.25
        - units:          0.10
        - professional:   0.15
        - contact:        0.10
        - concentration:  0.05 (bonus)
        - revenue:        0.15
        - distress:       0.05
        - deal_fit:       0.15
        
        Falls back to V1 weights when revenue/violations data isn't available.
        """
        # Core V1 scores
        portfolio_score = self._score_portfolio(lead.portfolio_size)
        units_score = self._score_units(lead.total_units)
        professional_score = self._score_professional(lead)
        contact_score = self._score_contact(lead)
        concentration_score = self._score_concentration(lead)
        
        # V2 scores
        revenue_score = self._score_revenue(lead)
        distress_score = self._score_distress(lead)
        deal_fit_score = self._score_deal_fit(lead)
        
        # Determine if V2 data is available
        has_revenue = getattr(lead, 'estimated_annual_revenue', 0) > 0
        has_violations = getattr(lead, 'violation_count', 0) > 0 or getattr(lead, 'violation_count', None) is not None
        
        if has_revenue:
            # V2 weights (balanced across all dimensions)
            total_score = (
                self.weights.get("portfolio", 0.25) * portfolio_score +
                self.weights.get("units", 0.10) * units_score +
                self.weights.get("professional", 0.15) * professional_score +
                self.weights.get("contact", 0.10) * contact_score +
                self.weights.get("revenue", 0.15) * revenue_score +
                self.weights.get("distress", 0.05) * distress_score +
                self.weights.get("deal_fit", 0.15) * deal_fit_score
            )
            # Concentration bonus
            concentration_bonus = self.weights.get("concentration_bonus", 0.05) * concentration_score
            total_score = min(total_score + concentration_bonus, 100)
        else:
            # V1 fallback weights (for leads without revenue estimation)
            total_score = (
                0.5 * portfolio_score +
                0.2 * units_score +
                0.15 * professional_score +
                0.15 * contact_score
            )
            concentration_bonus = 0.1 * concentration_score
            total_score = min(total_score + concentration_bonus, 100)
        
        # Store breakdown (all dimensions)
        lead.score = round(total_score, 1)
        lead.score_breakdown = {
            "portfolio": round(portfolio_score, 1),
            "units": round(units_score, 1),
            "professional": round(professional_score, 1),
            "contact": round(contact_score, 1),
            "concentration": round(concentration_score, 1),
            "revenue": round(revenue_score, 1),
            "distress": round(distress_score, 1),
            "deal_fit": round(deal_fit_score, 1),
        }
        
        lead.tags = self._generate_tags(lead)
        return lead
    
    # === V1 Scoring Components ===
    
    def _score_portfolio(self, size: int) -> float:
        thresholds = self.portfolio_thresholds
        if size >= thresholds.get("tier_100", 100):
            return 100
        elif size >= thresholds.get("tier_80", 50):
            return 80
        elif size >= thresholds.get("tier_60", 25):
            return 60
        elif size >= thresholds.get("tier_40", 10):
            return 40
        elif size >= thresholds.get("tier_20", 5):
            return 20
        else:
            return 10
    
    def _score_units(self, units: int) -> float:
        thresholds = self.unit_thresholds
        if units >= thresholds.get("tier_100", 1000):
            return 100
        elif units >= thresholds.get("tier_80", 500):
            return 80
        elif units >= thresholds.get("tier_60", 250):
            return 60
        elif units >= thresholds.get("tier_40", 100):
            return 40
        elif units >= thresholds.get("tier_20", 50):
            return 20
        else:
            return 10
    
    def _score_professional(self, lead: Lead) -> float:
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
        
        # Entity classification bonus/penalty
        entity_type = getattr(lead, 'entity_type', 'unknown')
        if entity_type == "company":
            score += 15
        elif entity_type == "individual_agent" and lead.portfolio_size >= 30:
            score += 5
        elif entity_type == "owner_operator":
            score -= 10
        
        return min(max(score, 0), 100)
    
    def _score_contact(self, lead: Lead) -> float:
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
    
    def _score_concentration(self, lead: Lead) -> float:
        if not lead.boros or len(lead.boros) == 0:
            return 50
        num_boroughs = len(lead.boros)
        if num_boroughs == 1:
            return 100
        elif num_boroughs == 2:
            return 80
        elif num_boroughs == 3:
            return 60
        elif num_boroughs == 4:
            return 40
        else:
            return 20
    
    # === V2 Scoring Components ===
    
    def _score_revenue(self, lead: Lead) -> float:
        """
        Score based on estimated annual management fee revenue.
        
        PE target range: $500K-$1.5M EBITDA (revenue is rough proxy).
        Sweet spot is $300K-$2M annual management revenue.
        """
        revenue = getattr(lead, 'estimated_annual_revenue', 0) or 0
        
        if revenue <= 0:
            return 0  # No data
        
        # Sweet spot scoring
        if 300_000 <= revenue <= 2_000_000:
            return 100  # In target range
        elif 100_000 <= revenue < 300_000:
            return 70  # Close to range
        elif 2_000_000 < revenue <= 5_000_000:
            return 80  # Larger than target but still attractive
        elif revenue > 5_000_000:
            return 60  # Too large for typical PE acquisition
        elif 50_000 <= revenue < 100_000:
            return 40  # Small but viable
        else:
            return 20  # Very small
    
    def _score_distress(self, lead: Lead) -> float:
        """
        Score based on HPD violations (distress signal).
        
        Moderate violations = motivated seller (positive for acquisition).
        Extreme violations = operational risk (negative).
        Uses violations_per_unit for normalized comparison.
        """
        vpu = getattr(lead, 'violations_per_unit', 0) or 0
        total_v = getattr(lead, 'violation_count', 0) or 0
        class_c = getattr(lead, 'violation_class_c', 0) or 0
        
        if total_v == 0:
            return 50  # Neutral (no data or clean record)
        
        # Moderate violations = opportunity signal
        if 0.5 <= vpu <= 3.0 and class_c < total_v * 0.3:
            return 80  # Moderate distress, likely motivated to sell
        elif vpu < 0.5:
            return 60  # Low violations, well-managed (less motivated to sell)
        elif vpu <= 5.0:
            return 50  # Higher violations, some risk
        elif vpu <= 10.0:
            return 30  # High violations, significant risk
        else:
            return 10  # Extreme violations, avoid
    
    def _score_deal_fit(self, lead: Lead) -> float:
        """
        Composite score for PE deal fit criteria:
        - Recurring revenue business (property management = yes)
        - NYC focused (yes by definition)
        - Professional management (entity_type = company)
        - Has contactable decision-maker
        - In revenue target range
        - Manageable operational complexity
        """
        score = 0
        max_possible = 0
        
        # Entity quality (20 pts)
        max_possible += 20
        entity_type = getattr(lead, 'entity_type', 'unknown')
        if entity_type == "company":
            score += 20
        elif entity_type == "individual_agent":
            score += 12
        elif entity_type == "owner_operator":
            score += 5
        
        # Contactability (20 pts)
        max_possible += 20
        if lead.phone and lead.email:
            score += 20
        elif lead.phone or lead.email:
            score += 12
        elif lead.website:
            score += 5
        
        # Revenue in target range (25 pts)
        max_possible += 25
        revenue = getattr(lead, 'estimated_annual_revenue', 0) or 0
        if 300_000 <= revenue <= 2_000_000:
            score += 25
        elif 100_000 <= revenue < 300_000 or 2_000_000 < revenue <= 5_000_000:
            score += 15
        elif revenue > 0:
            score += 5
        
        # Portfolio size (15 pts) - medium portfolios are easier to integrate
        max_possible += 15
        ps = lead.portfolio_size
        if 10 <= ps <= 100:
            score += 15  # Sweet spot
        elif 5 <= ps < 10:
            score += 8
        elif 100 < ps <= 300:
            score += 12
        elif ps > 300:
            score += 8  # Very large, complex integration
        
        # Geographic concentration (10 pts)
        max_possible += 10
        if lead.boros and len(lead.boros) <= 2:
            score += 10
        elif lead.boros and len(lead.boros) <= 3:
            score += 6
        else:
            score += 3
        
        # Manageable violations (10 pts)
        max_possible += 10
        vpu = getattr(lead, 'violations_per_unit', 0) or 0
        if vpu <= 2.0:
            score += 10
        elif vpu <= 5.0:
            score += 5
        # else: 0 (high risk)
        
        # Normalize to 0-100
        return round((score / max_possible) * 100, 1) if max_possible > 0 else 50
    
    def _generate_tags(self, lead: Lead) -> List[str]:
        tags = []
        
        # Portfolio size
        if lead.portfolio_size >= 50:
            tags.append("large_portfolio")
        elif lead.portfolio_size >= 10:
            tags.append("medium_portfolio")
        else:
            tags.append("small_portfolio")
        
        # Professional
        if lead.owner_type and lead.owner_type.upper() in ("AGENT", "MANAGEMENT"):
            tags.append("professional_mgmt")
        else:
            tags.append("owner_operator")
        
        # Entity type
        entity_type = getattr(lead, 'entity_type', 'unknown')
        if entity_type and entity_type != 'unknown':
            tags.append(f"entity_{entity_type}")
        
        # Contact
        if lead.website:
            tags.append("has_website")
        if lead.email:
            tags.append("has_email")
        if lead.phone:
            tags.append("has_phone")
        
        # Borough
        if lead.boro:
            tags.append(lead.boro.lower().replace(" ", "_"))
        
        # Concentration
        if lead.boros and len(lead.boros) == 1:
            tags.append("single_borough_focus")
        elif lead.boros and len(lead.boros) <= 2:
            tags.append("concentrated")
        
        # Revenue tags (V2)
        revenue = getattr(lead, 'estimated_annual_revenue', 0) or 0
        if revenue >= 1_000_000:
            tags.append("revenue_1m_plus")
        elif revenue >= 500_000:
            tags.append("revenue_500k_plus")
        elif revenue >= 100_000:
            tags.append("revenue_100k_plus")
        
        # Distress tags (V2)
        vpu = getattr(lead, 'violations_per_unit', 0) or 0
        if vpu > 5:
            tags.append("high_violations")
        elif vpu > 2:
            tags.append("moderate_violations")
        
        # Deal fit tag (V2)
        deal_fit = self._score_deal_fit(lead)
        if deal_fit >= 80:
            tags.append("strong_deal_fit")
        elif deal_fit >= 60:
            tags.append("good_deal_fit")
        
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
        else:
            return "F"


def score_leads(leads: List[Lead]) -> List[Lead]:
    """Score a list of leads using V2 scorer."""
    scorer = Scorer()
    return [scorer.score_lead(lead) for lead in leads]
