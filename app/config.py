"""Application configuration loaded from environment variables.

Railway injects PORT directly; other vars are optional overrides.
"""

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    # Railway injects PORT at runtime
    port: int = 8000
    host: str = "0.0.0.0"
    log_level: str = "info"

    # Playwright browser configuration
    browser_headless: bool = True

    # API authentication — set via API_KEY env var in Railway
    api_key: str = ""

    # Rate limiting — requests per minute per IP
    rate_limit: str = "10/minute"

    # Scraping timeout in milliseconds
    scraping_timeout: int = 45000

    # Grace period, in milliseconds, to wait for result cards AFTER the search page
    # already finished loading. The navigation waits for networkidle, so by then the
    # results are rendered; anything longer just delays the "no matches" answer.
    empty_result_grace: int = 8000

    model_config = {"env_prefix": "", "case_sensitive": False}


settings = Settings()
