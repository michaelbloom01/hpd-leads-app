"""
Application settings loaded from environment variables.
"""
from pathlib import Path
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    """Application settings with defaults and env var loading."""
    
    # NYC Open Data
    nyc_open_data_app_token: str = ""
    
    # Google Sheets
    google_sheets_credentials: str = "credentials.json"
    google_sheet_id: str = ""
    
    # Enrichment APIs
    apollo_api_key: str = ""
    serpapi_key: str = ""
    anthropic_api_key: str = ""
    
    # Pipeline settings
    min_portfolio_size: int = 1  # Changed from 10 — no longer discard small portfolios before DB storage
    enrichment_batch_size: int = 50
    
    # Paths
    data_dir: Path = Path("data")
    log_dir: Path = Path("data/logs")
    cache_dir: Path = Path("data/cache")
    raw_dir: Path = Path("data/raw")
    
    # Rate limits
    web_crawl_delay_seconds: float = 1.0
    api_retry_attempts: int = 3
    api_retry_delay_seconds: float = 1.0
    
    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"


def get_settings() -> Settings:
    """Get application settings (singleton pattern)."""
    return Settings()


# Convenience instance
settings = get_settings()
