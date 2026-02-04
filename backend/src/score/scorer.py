"""
Lead scoring logic.

See docs/03-scoring-rules.md for details.
"""
import logging
from pathlib import Path
from typing import List

import yaml

from src.transform.aggregate import Lead

logger = logging.getLogger(__name__)


class Scorer:
    """Score and tier leads based on configurable rules."""
    
    def __init__(self, config_path: str = "config/scoring_weights.yaml"):
        """
        Initialize scorer with config.
        
        Args:
            config_path: Path to scoring weights YAML
        """
        self.config = self._load_config(config_path)
        self.weights = self.config.get("weights", {})
        self.portfolio_thresholds = self.config.get("portfolio_thresholds", {})
        self.unit_thresholds = self.config.get("unit_thresholds", {})
        self.professional_keywords = self.config.get("professional_keywords", [])
        self.tier_labels = self.config.get("tier_labels", {})
    
    def _load_config(self, path: str) -> dict:
        """Load scoring config from YAML."""
        config_path = Path(path)
        if not config_path.exists():
            logger.warning(f"Config not found at {path}, using defaults")
            return {}
        
        with open(config_path) as f:
            return yaml.safe_load(f)
    
    def score_lead(self, lead: Lead) -> Lead:
        """
        Calculate score and assign tier to a lead.
        
        Args:
            lead: Lead to score
            
        Returns:
            Lead with score, tier, and tags populated
        """
        # Calculate component scores
        portfolio_score = self._score_portfolio(lead.portfolio_size)
        units_score = self._score_units(lead.total_units)
        professional_score = self._score_professional(lead)
        contact_score = self._score_contact(lead)
        
        # Apply weights
        total_score = (
            self.weights.get("portfolio", 0.5) * portfolio_score +
            self.weights.get("units", 0.2) * units_score +
            self.weights.get("professional", 0.15) * professional_score +
            self.weights.get("contact", 0.15) * contact_score
        )
        
        # Store breakdown
        lead.score = round(total_score, 1)
        lead.score_breakdown = {
            "portfolio": portfolio_score,
            "units": units_score,
            "professional": professional_score,
            "contact": contact_score,
        }
        
        # Assign tier
        lead.tags = self._generate_tags(lead)
        
        return lead
    
    def _score_portfolio(self, size: int) -> float:
        """Score based on portfolio size."""
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
        """Score based on total units."""
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
        """Score based on professional indicators."""
        score = 0
        
        # Agent type
        if lead.owner_type and lead.owner_type.upper() in ("AGENT", "MANAGEMENT"):
            score += 40
        
        # Entity type (LLC/Corp)
        name = (lead.agent_name or "").upper()
        if any(suffix in name for suffix in ["LLC", "INC", "CORP", "LP"]):
            score += 30
        
        # Professional keywords
        for keyword in self.professional_keywords:
            if keyword.upper() in name:
                score += 20
                break
        
        return min(score, 100)
    
    def _score_contact(self, lead: Lead) -> float:
        """Score based on contact completeness."""
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
    
    def _generate_tags(self, lead: Lead) -> List[str]:
        """Generate tags for a lead."""
        tags = []
        
        # Portfolio size tags
        if lead.portfolio_size >= 50:
            tags.append("large_portfolio")
        elif lead.portfolio_size >= 10:
            tags.append("medium_portfolio")
        else:
            tags.append("small_portfolio")
        
        # Professional tags
        if lead.owner_type and lead.owner_type.upper() in ("AGENT", "MANAGEMENT"):
            tags.append("professional_mgmt")
        else:
            tags.append("owner_operator")
        
        # Contact tags
        if lead.website:
            tags.append("has_website")
        if lead.email:
            tags.append("has_email")
        if lead.phone:
            tags.append("has_phone")
        
        # Borough tag
        if lead.boro:
            tags.append(lead.boro.lower().replace(" ", "_"))
        
        # Tier tag
        tier = self._get_tier(lead.score)
        tags.append(f"tier_{tier.lower()}")
        
        return tags
    
    def _get_tier(self, score: float) -> str:
        """Get tier letter from score."""
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
