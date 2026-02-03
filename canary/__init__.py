"""
Canary Test Module

E2E monitoring system that simulates a real customer journey:
Landing page → Get Report → OTP → Workspace setup → Prompts → Snapshot → Dashboard

Usage:
    # Async usage
    from canary import run_canary_test
    result = await run_canary_test()

    # Sync usage (for cron/CLI)
    from canary import run_canary_test_sync
    result = run_canary_test_sync()

    # CLI execution
    python -m app.modules.canary.canary_test
"""

from canary.canary_test import (
    CanaryTest,
    run_canary_test,
    run_canary_test_sync,
)
from canary.alerting import (
    CanaryResult,
    CanaryMetrics,
    CanaryTestError,
    AlertManager,
)
from canary.config import (
    CanaryConfig,
    get_canary_config,
)
from canary.db_verification import (
    DBVerifier,
    VerificationResult,
)
from canary.browser_automation import BrowserAutomation

# Note: CanaryCleanup is not imported by default as it depends on SQLModel ORM
# Import it directly if needed: from canary.cleanup import CanaryCleanup

__all__ = [
    # Main test functions
    "CanaryTest",
    "run_canary_test",
    "run_canary_test_sync",
    # Result types
    "CanaryResult",
    "CanaryMetrics",
    "CanaryTestError",
    # Components
    "AlertManager",
    "DBVerifier",
    "VerificationResult",
    "BrowserAutomation",
    # Config
    "CanaryConfig",
    "get_canary_config",
]
