"""
Canary Test Configuration

Configuration settings for the E2E canary test system that monitors
the full customer journey from landing page to dashboard.
"""

import os
from dataclasses import dataclass
from typing import Optional


@dataclass
class CanaryConfig:
    """Configuration for canary tests."""

    # Test identity
    EMAIL_DOMAIN: str = "canary.maxeo.ai"
    TEST_BRAND_DOMAIN: str = "www.letsbecool.com"
    TEST_FIRST_NAME: str = "Canary"
    TEST_LAST_NAME: str = "Test"
    TEST_COUNTRY: str = "TR"
    TEST_LANGUAGE: str = "tr"

    # Timeouts (in seconds)
    PAGE_LOAD_TIMEOUT: int = 30
    NAVIGATION_TIMEOUT: int = 60
    CATEGORY_WAIT_TIMEOUT: int = 120  # 2 minutes
    SNAPSHOT_WAIT_TIMEOUT: int = 300  # 5 minutes
    ELEMENT_WAIT_TIMEOUT: int = 10
    POLLING_INTERVAL: int = 5  # How often to poll for status changes

    # Browser settings
    HEADLESS: bool = True
    BROWSER_VIEWPORT_WIDTH: int = 1920
    BROWSER_VIEWPORT_HEIGHT: int = 1080
    SLOW_MO: int = 0  # Milliseconds to slow down operations (useful for debugging)

    # AI Model (cheap for canary - minimal processing)
    CANARY_AI_MODEL: str = "google/gemini-2.5-flash-lite"
    CANARY_MAX_TOKENS: int = 500

    # Alerting
    ALERT_ON_FAILURE: bool = True
    SLACK_WEBHOOK_URL: Optional[str] = None

    # Cleanup
    AUTO_CLEANUP: bool = True
    CLEANUP_AFTER_HOURS: int = 24

    # URLs
    BASE_URL: str = "https://maxeo.ai"

    # Development/Debug options
    SKIP_OTP_VERIFICATION: bool = False  # Set to True for local dev testing
    DEBUG_MODE: bool = False  # Enable extra logging

    # Test data verification thresholds
    MIN_CATEGORIES_COUNT: int = 3
    MIN_PROMPTS_COUNT: int = 15

    def __post_init__(self):
        """Load values from environment variables or main settings if available."""
        # Try to load from main settings first, then fall back to env vars
        try:
            from app.shared.config import get_settings
            settings = get_settings()

            self.SLACK_WEBHOOK_URL = settings.CANARY_SLACK_WEBHOOK or self.SLACK_WEBHOOK_URL
            self.BASE_URL = settings.CANARY_BASE_URL or self.BASE_URL
            self.HEADLESS = settings.CANARY_HEADLESS
            self.CATEGORY_WAIT_TIMEOUT = settings.CANARY_CATEGORY_WAIT_TIMEOUT
            self.SNAPSHOT_WAIT_TIMEOUT = settings.CANARY_SNAPSHOT_WAIT_TIMEOUT
            self.AUTO_CLEANUP = settings.CANARY_AUTO_CLEANUP
            self.CLEANUP_AFTER_HOURS = settings.CANARY_CLEANUP_AFTER_HOURS
        except Exception:
            # Fall back to environment variables
            self.SLACK_WEBHOOK_URL = os.getenv("CANARY_SLACK_WEBHOOK", self.SLACK_WEBHOOK_URL)
            self.BASE_URL = os.getenv("CANARY_BASE_URL", self.BASE_URL)
            self.HEADLESS = os.getenv("CANARY_HEADLESS", "true").lower() == "true"

            if os.getenv("CANARY_CATEGORY_WAIT_TIMEOUT"):
                self.CATEGORY_WAIT_TIMEOUT = int(os.getenv("CANARY_CATEGORY_WAIT_TIMEOUT"))
            if os.getenv("CANARY_SNAPSHOT_WAIT_TIMEOUT"):
                self.SNAPSHOT_WAIT_TIMEOUT = int(os.getenv("CANARY_SNAPSHOT_WAIT_TIMEOUT"))

        # Development options from environment
        self.SKIP_OTP_VERIFICATION = os.getenv("CANARY_SKIP_OTP", "false").lower() == "true"
        self.DEBUG_MODE = os.getenv("CANARY_DEBUG", "false").lower() == "true"


# Singleton instance
_config: Optional[CanaryConfig] = None


def get_canary_config() -> CanaryConfig:
    """Get the canary configuration singleton."""
    global _config
    if _config is None:
        _config = CanaryConfig()
    return _config
