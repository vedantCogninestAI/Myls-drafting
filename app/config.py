from typing import List

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    PROJECT_NAME: str = "AI Backend"
    VERSION: str = "0.1.0"
    DEBUG: bool = False

    HOST: str = "0.0.0.0"
    PORT: int = 8000

    CORS_ORIGINS: List[str] = ["*"]

    # AWS / Bedrock
    AWS_REGION: str = "us-east-1"
    AWS_ACCESS_KEY_ID: str = ""
    AWS_SECRET_ACCESS_KEY: str = ""
    BEDROCK_MODEL_ID: str = "anthropic.claude-3-5-sonnet-20241022-v2:0"

    # Ingestion
    INGESTION_CONCURRENCY: int = 3
    CLASSIFY_PDF_LLM: bool

    # Templates
    TEMPLATE_CONCURRENCY: int

    # Draft generation
    MAX_DRAFT_REVISIONS: int = 5

    # USCIS fee scraping
    USCIS_FEE_CALCULATOR_URL: str = "https://www.uscis.gov/feecalculator"
    USCIS_BASE_URL: str = "https://www.uscis.gov"
    FEE_SCRAPE_CONCURRENCY: int = 5
    FEE_SCRAPE_TIMEOUT: int = 30
    FEE_SCRAPE_BATCH_DELAY: int = 5
    FIRECRAWL_API_KEY: str = ""

    # LLM
    LLM_MAX_RETRIES: int = 3

    # Database
    DATABASE_URL: str = ""

    # Auth
    SECRET_KEY: str = "change-me-in-production"

    # Logging
    LOG_LEVEL: str = "INFO"
    LOG_FORMAT: str = "json"


settings = Settings()
