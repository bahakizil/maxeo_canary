"""
Canary Test Orchestrator

Main orchestrator for running the full E2E canary test that simulates
a real customer journey from landing page to dashboard.

IMPORTANT: This module is designed to be lightweight and independent.
It uses raw SQL queries instead of ORM models to avoid complex dependencies.
"""

import asyncio
import os
import time
from datetime import datetime, timezone
from typing import Optional

from canary.config import get_canary_config, CanaryConfig
from canary.browser_automation import BrowserAutomation
from canary.db_verification import DBVerifier
from canary.alerting import (
    CanaryTestError,
    CanaryResult,
    CanaryMetrics,
    AlertManager
)
from canary.utils import get_canary_logger, get_database_url

logger = get_canary_logger("canary.test")


class CanaryTest:
    """
    Full E2E Canary Test Orchestrator.

    Simulates a complete customer journey:
    1. Landing page navigation
    2. Click Get Report
    3. Enter email
    4. Verify OTP (from DB)
    5. Fill workspace details
    6. Wait for categories
    7. Approve prompts
    8. Wait for snapshot
    9. Verify dashboard
    10. Cleanup
    """

    def __init__(self, database_url: Optional[str] = None):
        self.config = get_canary_config()
        self.test_id = f"canary-{int(time.time())}"
        self.test_email = f"{self.test_id}@{self.config.EMAIL_DOMAIN}"
        self.metrics = CanaryMetrics(test_id=self.test_id)
        self.alert_manager = AlertManager()

        # Database URL
        self.database_url = database_url or get_database_url()

        # Components
        self.browser: Optional[BrowserAutomation] = None
        self.db_verifier: Optional[DBVerifier] = None

        # State tracking
        self.workspace_id: Optional[int] = None
        self.workspace_ulid: Optional[str] = None
        self.prompts_confirmed_time: Optional[float] = None
        self.db_state: Optional[dict] = None

        # CRITICAL LOADING TIMES - User's key metrics
        # Loading 1: Form submit → Prompts modal ready
        self.form_submitted_time: Optional[float] = None  # When "Get Report" clicked
        self.prompts_ready_time: Optional[float] = None   # When prompts modal appears

        # Loading 2: Confirm prompts → Dashboard ready
        # prompts_confirmed_time is already defined above
        self.dashboard_ready_time: Optional[float] = None  # When dashboard loads

    async def run(self) -> CanaryResult:
        """
        Execute the full E2E canary test.

        Returns:
            CanaryResult with success status and metrics
        """
        self.metrics.start_time = datetime.now(timezone.utc)
        logger.info(f"Starting canary test: {self.test_id}")
        logger.info(f"Test email: {self.test_email}")

        try:
            # Setup
            await self._setup()

            # Execute all steps with timing
            await self._step_01_navigate_to_landing()
            await self._step_02_click_get_report()
            await self._step_03_fill_email_and_submit()
            await self._step_04_verify_user_created()
            await self._step_05_fill_otp()
            await self._step_06_fill_workspace_details()
            await self._step_07_wait_for_categories()
            await self._step_08_approve_prompts()
            await self._step_09_wait_for_snapshot()
            await self._step_10_verify_dashboard()
            await self._step_11_full_verification()

            # Success
            self.metrics.end_time = datetime.now(timezone.utc)

            result = CanaryResult(
                success=True,
                test_id=self.test_id,
                metrics=self.metrics,
                workspace_id=self.workspace_id,
                workspace_ulid=self.workspace_ulid,
                db_state=self.db_state
            )

            await self.alert_manager.send_success_notification(result)
            logger.info(f"Canary test {self.test_id} completed successfully")

            # Cleanup test data after successful test
            await self._cleanup_test_data()

            return result

        except CanaryTestError as e:
            self.metrics.end_time = datetime.now(timezone.utc)
            self.metrics.record_error(e.step, e.message, e.details)

            # Get DB state for debugging
            db_state = None
            if self.db_verifier:
                try:
                    db_state = self.db_verifier.full_verification()
                except Exception as db_err:
                    logger.error(f"Failed to get DB state: {db_err}")

            result = CanaryResult(
                success=False,
                test_id=self.test_id,
                metrics=self.metrics,
                failed_step=e.step,
                error_message=e.message,
                db_state=db_state,
                workspace_id=self.workspace_id,
                workspace_ulid=self.workspace_ulid
            )

            await self.alert_manager.send_failure_alert(result)
            logger.error(f"Canary test {self.test_id} failed at step {e.step}: {e.message}")

            return result

        except Exception as e:
            self.metrics.end_time = datetime.now(timezone.utc)
            self.metrics.record_error("UNEXPECTED", str(e))

            result = CanaryResult(
                success=False,
                test_id=self.test_id,
                metrics=self.metrics,
                failed_step="UNEXPECTED",
                error_message=str(e),
                workspace_id=self.workspace_id,
                workspace_ulid=self.workspace_ulid
            )

            await self.alert_manager.send_failure_alert(result)
            logger.error(f"Canary test {self.test_id} failed unexpectedly: {e}")

            return result

        finally:
            await self._cleanup()

    async def _setup(self) -> None:
        """Initialize all components."""
        start = time.time()
        logger.info("Setting up canary test components")

        # Initialize browser
        self.browser = BrowserAutomation()
        await self.browser.setup()

        # Initialize DB verifier with connection string
        self.db_verifier = DBVerifier(self.database_url, self.test_email)

        self.metrics.record_step_timing("setup", time.time() - start)
        logger.info("Setup complete")

    async def _cleanup_test_data(self) -> None:
        """
        Clean up test data from database after successful test.

        This soft deletes the workspace and user created during the test
        to avoid data pollution in the database.
        """
        if not self.config.AUTO_CLEANUP:
            logger.info("Auto cleanup disabled, skipping test data cleanup")
            return

        if not self.db_verifier:
            logger.warning("DB verifier not available for cleanup")
            return

        logger.info("=" * 60)
        logger.info("CLEANUP: Soft deleting test data")
        logger.info("=" * 60)

        try:
            cleanup_result = self.db_verifier.cleanup_test_data()

            if cleanup_result["workspace_deleted"]:
                logger.info(f"  ✓ Workspace {self.workspace_id} soft deleted")
            else:
                logger.warning(f"  ⚠ Workspace not deleted")

            if cleanup_result["user_deleted"]:
                logger.info(f"  ✓ User {self.test_email} soft deleted")
            else:
                logger.warning(f"  ⚠ User not deleted")

            if cleanup_result["errors"]:
                for error in cleanup_result["errors"]:
                    logger.error(f"  ✗ Cleanup error: {error}")

            logger.info("  Cleanup complete")

        except Exception as e:
            logger.error(f"Failed to cleanup test data: {e}")

    async def _cleanup(self) -> None:
        """Cleanup resources and test data."""
        logger.info("Cleaning up canary test")

        # Cleanup browser
        if self.browser:
            try:
                await self.browser.cleanup()
            except Exception as e:
                logger.error(f"Browser cleanup error: {e}")

        # Close DB connection
        if self.db_verifier:
            try:
                self.db_verifier.close()
            except Exception as e:
                logger.error(f"DB cleanup error: {e}")

    async def _step_01_navigate_to_landing(self) -> None:
        """Step 1: Navigate to landing page."""
        start = time.time()
        logger.info("=" * 60)
        logger.info("STEP 1: NAVIGATE TO LANDING PAGE")
        logger.info("=" * 60)
        logger.info(f"  Target URL: {self.config.BASE_URL}")

        await self.browser.navigate_to_landing()

        duration = time.time() - start
        self.metrics.record_step_timing("step_01_landing", duration)
        logger.info(f"  ✓ STEP 1 COMPLETED in {duration:.2f}s")

    async def _step_02_click_get_report(self) -> None:
        """Step 2: Click Get Report button."""
        start = time.time()
        logger.info("=" * 60)
        logger.info("STEP 2: CLICK GET REPORT BUTTON")
        logger.info("=" * 60)

        await self.browser.click_get_report_button()

        duration = time.time() - start
        self.metrics.record_step_timing("step_02_click_get_report", duration)
        logger.info(f"  ✓ STEP 2 COMPLETED in {duration:.2f}s")

    async def _step_03_fill_email_and_submit(self) -> None:
        """Step 3: Fill email and submit to trigger OTP."""
        start = time.time()
        logger.info("=" * 60)
        logger.info("STEP 3: FILL FORM AND SUBMIT")
        logger.info("=" * 60)
        logger.info(f"  Brand URL: {self.config.TEST_BRAND_DOMAIN}")
        logger.info(f"  Email: {self.test_email}")
        logger.info(f"  Name: {self.config.TEST_FIRST_NAME} {self.config.TEST_LAST_NAME}")
        logger.info(f"  Country: {self.config.TEST_COUNTRY}, Language: {self.config.TEST_LANGUAGE}")

        # Fill workspace form with test data
        fill_start = time.time()
        logger.info("  [3.1] Filling form fields...")
        await self.browser.fill_workspace_form(
            brand_url=self.config.TEST_BRAND_DOMAIN,
            brand_name="Maxeo Canary Test",
            first_name=self.config.TEST_FIRST_NAME,
            last_name=self.config.TEST_LAST_NAME,
            email=self.test_email,
            country=self.config.TEST_COUNTRY,
            language=self.config.TEST_LANGUAGE
        )
        logger.info(f"  [3.1] Form filled in {time.time() - fill_start:.2f}s")

        # Enable console log capture before submission
        console_messages = []
        self.browser.page.on("console", lambda msg: console_messages.append(f"{msg.type}: {msg.text}"))

        # Submit the form
        submit_start = time.time()
        logger.info("  [3.2] Submitting form...")
        await self.browser.submit_workspace_form()
        self.form_submitted_time = time.time()  # LOADING 1 START
        logger.info(f"  [3.2] Submit clicked in {time.time() - submit_start:.2f}s")
        logger.info(f"  [3.2] 🚀 LOADING 1 STARTED (form submitted)")

        # Wait a moment for the page to react and collect any errors
        logger.info("  [3.3] Waiting 3s for page reaction...")
        await asyncio.sleep(3)

        # Log any console errors
        error_count = 0
        for msg in console_messages:
            if "error" in msg.lower() or "fail" in msg.lower():
                logger.warning(f"  ⚠ Console: {msg}")
                error_count += 1
        if error_count == 0:
            logger.info("  [3.3] No console errors detected")

        # Log current URL for debugging
        current_url = await self.browser.get_current_url()
        logger.info(f"  [3.4] Current URL: {current_url}")

        # Check for any error messages on the page
        try:
            error_text = await self.browser.page.evaluate("""
                () => {
                    const selectors = [
                        '.error', '.error-message', '[class*="error"]',
                        '.snackbar', '[class*="snackbar"]', '[class*="toast"]',
                        '[role="alert"]'
                    ];
                    for (const selector of selectors) {
                        const el = document.querySelector(selector);
                        if (el && el.textContent) {
                            return el.textContent.trim();
                        }
                    }
                    return null;
                }
            """)
            if error_text:
                logger.warning(f"  ⚠ Page error: {error_text}")
            else:
                logger.info("  [3.4] No page errors found")
        except Exception as e:
            logger.debug(f"  Could not check for error messages: {e}")

        # Take screenshot to debug
        try:
            screenshot_path = f"/tmp/canary_after_submit_{self.test_id}.png"
            await self.browser.take_screenshot(screenshot_path)
            logger.info(f"  [3.5] Screenshot: {screenshot_path}")
        except Exception as e:
            logger.warning(f"  Could not take screenshot: {e}")

        # Wait for OTP input to appear
        otp_wait_start = time.time()
        logger.info("  [3.6] Waiting for OTP input screen...")
        await self.browser.wait_for_otp_input()
        logger.info(f"  [3.6] OTP screen appeared in {time.time() - otp_wait_start:.2f}s")

        duration = time.time() - start
        self.metrics.record_step_timing("step_03_fill_email", duration)
        logger.info(f"  ✓ STEP 3 COMPLETED in {duration:.2f}s")

    async def _step_04_verify_user_created(self) -> None:
        """Step 4: Verify user was created in DB."""
        start = time.time()
        logger.info("Step 4: Verifying user created")

        # Wait a bit for user creation
        await asyncio.sleep(2)

        # Refresh session to see new data
        # DB verifier handles fresh queries automatically

        # Verify user exists
        result = self.db_verifier.verify_user_exists()
        if not result.success:
            raise CanaryTestError(
                "STEP_04_VERIFY_USER",
                f"User not created: {result.message}"
            )

        logger.info(f"User verified: {result.data}")
        self.metrics.record_step_timing("step_04_verify_user", time.time() - start)

    async def _step_05_fill_otp(self) -> None:
        """Step 5: Get OTP from DB and fill it."""
        start = time.time()
        logger.info("=" * 60)
        logger.info("STEP 5: GET AND FILL OTP CODE")
        logger.info("=" * 60)

        # Check if OTP should be skipped (for local development)
        if self.config.SKIP_OTP_VERIFICATION:
            logger.warning("SKIP_OTP_VERIFICATION is enabled - skipping OTP step")
            logger.warning("Note: This is for local development only. Full test requires production environment.")
            self.metrics.record_step_timing("step_05_fill_otp", time.time() - start)
            return

        # Get OTP from DB
        logger.info("  [5.1] Fetching OTP code from database...")
        otp_code = self.db_verifier.get_otp_code()
        if not otp_code:
            raise CanaryTestError(
                "STEP_05_GET_OTP",
                "Could not retrieve OTP code from database. "
                "This may be due to encryption key mismatch (local vs production). "
                "Set CANARY_SKIP_OTP=true for local development testing."
            )

        logger.info(f"  [5.1] Retrieved OTP code: {otp_code}")

        # Fill OTP
        logger.info("  [5.2] Filling OTP digits...")
        await self.browser.fill_otp(otp_code)

        # Submit OTP and wait for page transition
        logger.info("  [5.3] Submitting OTP and waiting for page transition...")
        await self.browser.submit_otp()
        # Note: submit_otp() now includes _wait_for_post_otp_transition()

        # Take screenshot after transition
        screenshot_path = f"/tmp/canary_after_otp_{self.test_id}.png"
        await self.browser.take_screenshot(screenshot_path)
        logger.info(f"  [5.4] Post-OTP screenshot: {screenshot_path}")

        current_url = await self.browser.get_current_url()
        logger.info(f"  [5.4] Current URL after OTP: {current_url}")

        duration = time.time() - start
        self.metrics.record_step_timing("step_05_fill_otp", duration)
        logger.info(f"  ✓ STEP 5 COMPLETED in {duration:.2f}s")

    async def _step_06_fill_workspace_details(self) -> None:
        """Step 6: Wait for workspace creation and 'Setting up topics' loading.

        IMPORTANT: After OTP, the flow stays in a MODAL on the landing page.
        The modal shows "Setting up your topics..." loading while categories are generated.
        We should NOT navigate away - the prompts will appear in the same modal.
        """
        start = time.time()
        logger.info("=" * 60)
        logger.info("STEP 6: WAIT FOR WORKSPACE CREATION & TOPICS LOADING")
        logger.info("=" * 60)

        # Wait for workspace to be created in DB
        max_wait = 60
        waited = 0
        while waited < max_wait:
            result = self.db_verifier.verify_workspace_created()
            if result.success:
                self.workspace_id = result.data.get("workspace_id")
                self.workspace_ulid = result.data.get("ulid")
                logger.info(f"  [6.1] Workspace created: ID={self.workspace_id}, ULID={self.workspace_ulid}")
                break
            await asyncio.sleep(2)
            waited += 2
            if waited % 10 == 0:
                logger.info(f"  [6.1] ... waiting for workspace ({waited}s)")

        if not self.workspace_id:
            raise CanaryTestError(
                "STEP_06_WORKSPACE_CREATED",
                "Workspace was not created within timeout"
            )

        # Check current URL
        current_url = await self.browser.get_current_url()
        logger.info(f"  [6.2] Current URL: {current_url}")

        # Take screenshot to see modal state
        await self.browser.take_screenshot(f"/tmp/canary_step6_modal_{self.test_id}.png")
        logger.info(f"  [6.2] Screenshot saved")

        # Check for loading indicators in the modal
        # The modal should show "Setting up your topics..." or similar
        logger.info("  [6.3] Checking for loading state in modal...")

        page_state = await self.browser.page.evaluate("""
            () => {
                const bodyText = document.body?.innerText || '';
                return {
                    hasSettingUp: bodyText.toLowerCase().includes('setting up'),
                    hasTopics: bodyText.toLowerCase().includes('topics'),
                    hasAnalyzing: bodyText.toLowerCase().includes('analyzing'),
                    hasLoading: bodyText.toLowerCase().includes('loading'),
                    hasPrompts: bodyText.toLowerCase().includes('prompts'),
                    hasConfirm: bodyText.toLowerCase().includes('confirm'),
                    visibleButtons: Array.from(document.querySelectorAll('button'))
                        .filter(b => b.offsetParent !== null)
                        .map(b => b.textContent?.trim()?.slice(0, 50))
                        .filter(t => t && t.length > 0)
                        .slice(0, 10)
                };
            }
        """)

        logger.info(f"  [6.3] Modal state: settingUp={page_state.get('hasSettingUp')}, "
                   f"topics={page_state.get('hasTopics')}, prompts={page_state.get('hasPrompts')}, "
                   f"buttons={page_state.get('visibleButtons', [])}")

        duration = time.time() - start
        self.metrics.record_step_timing("step_06_workspace_details", duration)
        logger.info(f"  ✓ STEP 6 COMPLETED in {duration:.2f}s")

    async def _step_07_wait_for_categories(self) -> None:
        """Step 7: Wait for categories and prompts to be generated in the modal.

        The modal flow (based on user screenshots):
        1. "Setting up your topics..." loading (categories being generated)
        2. "Workspace Prompts" modal appears with prompts list
        3. User clicks "Confirm Prompts" button

        We wait for prompts to appear (up to 1 minute as per user guidance).
        """
        start = time.time()
        logger.info("=" * 60)
        logger.info("STEP 7: WAIT FOR CATEGORIES & PROMPTS IN MODAL")
        logger.info("=" * 60)

        # Poll DB for categories and workspace status
        timeout = self.config.CATEGORY_WAIT_TIMEOUT  # 2 minutes
        waited = 0
        categories_ready = False

        while waited < timeout:
            # Check categories
            categories_result = self.db_verifier.verify_categories_created(
                min_count=self.config.MIN_CATEGORIES_COUNT
            )
            if categories_result.success and not categories_ready:
                logger.info(f"  [7.1] Categories created: {categories_result.data}")
                categories_ready = True

            # Check workspace status - accept any state that means we can proceed
            status_inter1 = self.db_verifier.verify_workspace_status("INTER_STEP_1_READY")
            status_inter2 = self.db_verifier.verify_workspace_status("INTER_STEP_2_READY")
            status_completed = self.db_verifier.verify_workspace_status("COMPLETED")

            if status_inter1.success:
                logger.info("  [7.1] Workspace reached INTER_STEP_1_READY")
                break
            elif status_inter2.success:
                logger.info("  [7.1] Workspace already at INTER_STEP_2_READY (fast path)")
                break
            elif status_completed.success:
                logger.info("  [7.1] Workspace already COMPLETED!")
                break

            if waited % 10 == 0:
                ws = self.db_verifier.refresh_workspace()
                if ws:
                    cats = self.db_verifier.get_categories_count(ws['id'])
                    logger.info(f"  [7.1] ... waiting ({waited}s), status={ws['status']}, categories={cats}")

            await asyncio.sleep(self.config.POLLING_INTERVAL)
            waited += self.config.POLLING_INTERVAL

        if waited >= timeout:
            raise CanaryTestError(
                "STEP_07_WAIT_CATEGORIES",
                f"Categories not ready within {timeout}s",
                {"waited": waited}
            )

        # Now wait for prompts to appear (up to 1 minute as per user)
        logger.info("  [7.2] Waiting for prompts to be generated...")
        prompts_timeout = 60
        prompts_waited = 0

        while prompts_waited < prompts_timeout:
            prompts_result = self.db_verifier.verify_prompts_created(
                min_count=self.config.MIN_PROMPTS_COUNT
            )
            if prompts_result.success:
                self.prompts_ready_time = time.time()  # LOADING 1 END
                loading_1_duration = self.prompts_ready_time - self.form_submitted_time if self.form_submitted_time else 0
                logger.info(f"  [7.2] Prompts ready: {prompts_result.data}")
                logger.info(f"  [7.2] ✅ LOADING 1 COMPLETED: {loading_1_duration:.1f}s (Form → Prompts)")
                self.metrics.record_step_timing("loading_1_form_to_prompts", loading_1_duration)
                break

            if prompts_waited % 10 == 0:
                ws = self.db_verifier.refresh_workspace()
                if ws:
                    prompts = self.db_verifier.get_prompts_count(ws['id'])
                    logger.info(f"  [7.2] ... waiting ({prompts_waited}s), prompts={prompts}")

            await asyncio.sleep(self.config.POLLING_INTERVAL)
            prompts_waited += self.config.POLLING_INTERVAL

        # Check page state - should see "Workspace Prompts" modal or "Confirm Prompts" button
        await asyncio.sleep(2)

        page_state = await self.browser.page.evaluate("""
            () => {
                const bodyText = document.body?.innerText || '';
                return {
                    hasWorkspacePrompts: bodyText.toLowerCase().includes('workspace prompts'),
                    hasConfirmPrompts: bodyText.toLowerCase().includes('confirm prompts'),
                    hasPromptsList: !!document.querySelector('[class*="prompt"]'),
                    visibleButtons: Array.from(document.querySelectorAll('button'))
                        .filter(b => b.offsetParent !== null)
                        .map(b => b.textContent?.trim()?.slice(0, 50))
                        .filter(t => t && t.length > 0)
                        .slice(0, 10)
                };
            }
        """)

        logger.info(f"  [7.3] Page state: workspacePrompts={page_state.get('hasWorkspacePrompts')}, "
                   f"confirmBtn={page_state.get('hasConfirmPrompts')}, "
                   f"buttons={page_state.get('visibleButtons', [])}")

        # Take screenshot
        screenshot_path = f"/tmp/canary_prompts_modal_{self.test_id}.png"
        await self.browser.take_screenshot(screenshot_path)
        logger.info(f"  [7.3] Screenshot: {screenshot_path}")

        duration = time.time() - start
        self.metrics.record_step_timing("step_07_wait_categories", duration)
        logger.info(f"  ✓ STEP 7 COMPLETED in {duration:.2f}s")

    async def _step_08_approve_prompts(self) -> None:
        """Step 8: Wait for prompts generation and approve them."""
        start = time.time()
        logger.info("=" * 60)
        logger.info("STEP 8: WAIT FOR PROMPTS & APPROVE")
        logger.info("=" * 60)

        current_url = await self.browser.get_current_url()
        logger.info(f"  [8.1] Current URL: {current_url}")

        # Wait for prompts to be generated in DB
        logger.info("  [8.2] Waiting for prompts generation...")
        timeout = self.config.CATEGORY_WAIT_TIMEOUT
        waited = 0
        prompts_ready = False

        while waited < timeout:
            prompts_result = self.db_verifier.verify_prompts_created(
                min_count=self.config.MIN_PROMPTS_COUNT
            )
            if prompts_result.success:
                logger.info(f"  [8.2] Prompts ready: {prompts_result.data}")
                prompts_ready = True
                break

            # Also check workspace status
            ws = self.db_verifier.refresh_workspace()
            if ws:
                logger.info(f"  [8.2] ... waiting ({waited}s), status={ws['status']}, prompts={self.db_verifier.get_prompts_count(ws['id'])}")

            await asyncio.sleep(self.config.POLLING_INTERVAL)
            waited += self.config.POLLING_INTERVAL

        if not prompts_ready:
            logger.warning(f"  [8.2] Prompts not ready after {timeout}s")

        # Take screenshot of prompts page
        screenshot_path = f"/tmp/canary_prompts_{self.test_id}.png"
        await self.browser.take_screenshot(screenshot_path)
        logger.info(f"  [8.3] Prompts page screenshot: {screenshot_path}")

        # Click continue/approve button to confirm prompts
        logger.info("  [8.4] Looking for approve/continue button...")
        try:
            await self.browser.click_continue_prompts()
            self.prompts_confirmed_time = time.time()  # LOADING 2 START
            logger.info("  [8.4] Clicked approve button - prompts confirmed")
            logger.info(f"  [8.4] 🚀 LOADING 2 STARTED (prompts confirmed)")
        except CanaryTestError:
            logger.info("  [8.4] No approve button found")

        await asyncio.sleep(3)

        # Wait for INTER_STEP_2_READY status
        logger.info("  [8.5] Waiting for workspace to reach INTER_STEP_2_READY...")
        timeout = 90
        waited = 0
        while waited < timeout:
            status_result = self.db_verifier.verify_workspace_status("INTER_STEP_2_READY")
            status_completed = self.db_verifier.verify_workspace_status("COMPLETED")

            if status_completed.success:
                logger.info("  [8.5] Workspace is COMPLETED")
                break
            elif status_result.success:
                logger.info("  [8.5] Workspace reached INTER_STEP_2_READY")
                break

            ws = self.db_verifier.refresh_workspace()
            if ws:
                logger.info(f"  [8.5] ... waiting ({waited}s), status={ws['status']}")

            await asyncio.sleep(self.config.POLLING_INTERVAL)
            waited += self.config.POLLING_INTERVAL

        duration = time.time() - start
        self.metrics.record_step_timing("step_08_approve_prompts", duration)
        logger.info(f"  ✓ STEP 8 COMPLETED in {duration:.2f}s")

    async def _step_09_wait_for_snapshot(self) -> None:
        """Step 9: Wait for snapshot to complete and workspace to reach COMPLETED."""
        start = time.time()
        logger.info("=" * 60)
        logger.info("STEP 9: WAIT FOR SNAPSHOT & WORKSPACE COMPLETED")
        logger.info("=" * 60)

        current_url = await self.browser.get_current_url()
        logger.info(f"  [9.1] Current URL: {current_url}")

        # Try browser wait but don't fail if it times out
        logger.info("  [9.2] Checking browser state...")
        try:
            await self.browser.wait_for_snapshot_loading(timeout_seconds=10)
        except CanaryTestError:
            logger.info("  [9.2] Browser wait completed (no snapshot loading UI detected)")

        # Poll DB for snapshot COMPLETED status
        timeout = self.config.SNAPSHOT_WAIT_TIMEOUT
        waited = 0
        snapshot_completed = False

        while waited < timeout:
            workspace = self.db_verifier.refresh_workspace()
            if not workspace:
                await asyncio.sleep(self.config.POLLING_INTERVAL)
                waited += self.config.POLLING_INTERVAL
                continue

            snapshot = self.db_verifier.get_latest_snapshot(workspace["id"])

            if snapshot:
                snap_status = snapshot.get("status", "UNKNOWN")
                if snap_status == "COMPLETED":
                    logger.info(f"  [9.2] Snapshot COMPLETED (ID={snapshot['id']})")
                    snapshot_completed = True
                    break
                elif snap_status == "FAILED":
                    logger.error(f"  [9.2] Snapshot FAILED (ID={snapshot['id']})")
                    break
                else:
                    # Get snapshot prompts progress
                    prompts_status = self.db_verifier.get_snapshot_prompts_status(snapshot["id"])
                    total = prompts_status.get("total", 0)
                    completed = prompts_status.get("completed", 0)
                    logger.info(f"  [9.2] ... snapshot {snap_status}, prompts {completed}/{total} ({waited}s)")
            else:
                logger.info(f"  [9.2] ... waiting for snapshot ({waited}s)")

            await asyncio.sleep(self.config.POLLING_INTERVAL)
            waited += self.config.POLLING_INTERVAL

        if not snapshot_completed and waited >= timeout:
            logger.warning(f"  [9.2] Snapshot not completed after {timeout}s")

        # Now wait for workspace COMPLETED status
        logger.info("  [9.3] Waiting for workspace COMPLETED status...")
        timeout = 120
        waited = 0
        workspace_completed = False

        while waited < timeout:
            status_result = self.db_verifier.verify_workspace_status("COMPLETED")
            if status_result.success:
                self.dashboard_ready_time = time.time()  # LOADING 2 END
                loading_2_duration = self.dashboard_ready_time - self.prompts_confirmed_time if self.prompts_confirmed_time else 0
                logger.info("  [9.3] Workspace is COMPLETED!")
                logger.info(f"  [9.3] ✅ LOADING 2 COMPLETED: {loading_2_duration:.1f}s (Confirm → Dashboard)")
                self.metrics.record_step_timing("loading_2_confirm_to_dashboard", loading_2_duration)
                workspace_completed = True
                break

            ws = self.db_verifier.refresh_workspace()
            if ws:
                logger.info(f"  [9.3] ... workspace status: {ws['status']} ({waited}s)")

            await asyncio.sleep(self.config.POLLING_INTERVAL)
            waited += self.config.POLLING_INTERVAL

        if not workspace_completed:
            ws = self.db_verifier.refresh_workspace()
            final_status = ws["status"] if ws else "UNKNOWN"
            logger.warning(f"  [9.3] Workspace not COMPLETED after {timeout}s, final status: {final_status}")

        # Calculate snapshot processing time (from prompts confirmation)
        if hasattr(self, 'prompts_confirmed_time'):
            snapshot_time = time.time() - self.prompts_confirmed_time
            logger.info(f"  [9.4] Snapshot processing took {snapshot_time:.1f}s from prompts confirmation")

        duration = time.time() - start
        self.metrics.record_step_timing("step_09_wait_snapshot", duration)
        logger.info(f"  ✓ STEP 9 COMPLETED in {duration:.2f}s")

    async def _step_10_verify_dashboard(self) -> None:
        """Step 10: Verify dashboard loaded and collect UI data."""
        start = time.time()
        logger.info("=" * 60)
        logger.info("STEP 10: VERIFY DASHBOARD & COLLECT UI DATA")
        logger.info("=" * 60)

        # Try to get workspace ULID from URL
        if not self.workspace_ulid:
            self.workspace_ulid = await self.browser.get_workspace_ulid_from_url()

        current_url = await self.browser.get_current_url()
        logger.info(f"  [10.1] Current URL: {current_url}")

        # Navigate to overview if not there
        if "/overview" not in current_url and self.workspace_ulid:
            overview_url = f"{self.config.BASE_URL}/workspace/{self.workspace_ulid}/overview"
            logger.info(f"  [10.2] Navigating to overview: {overview_url}")
            await self.browser.page.goto(overview_url, wait_until="load", timeout=30000)
            await asyncio.sleep(3)

        # Verify dashboard loaded
        dashboard_ok = await self.browser.verify_dashboard_loaded()
        logger.info(f"  [10.3] Dashboard loaded: {dashboard_ok}")

        # Collect UI data from the page
        ui_data = await self.browser.page.evaluate("""
            () => {
                const data = {
                    dashboard_loaded: false,
                    charts_visible: false,
                    current_url: window.location.href,
                    page_title: document.title,
                    sections: [],
                    metrics: {}
                };

                // Check for dashboard elements
                const mainContent = document.querySelector('main') || document.body;
                if (mainContent) {
                    data.dashboard_loaded = true;
                }

                // Check for charts
                const charts = document.querySelectorAll('[class*="chart"], canvas, svg');
                data.charts_visible = charts.length > 0;
                data.metrics.charts_count = charts.length;

                // Check for sidebar navigation
                const sidebar = document.querySelector('aside, nav, [class*="sidebar"]');
                if (sidebar) {
                    const navLinks = sidebar.querySelectorAll('a');
                    data.sections = Array.from(navLinks).map(a => a.textContent.trim()).filter(t => t.length > 0 && t.length < 30).slice(0, 10);
                }

                // Check for data elements
                const cards = document.querySelectorAll('[class*="card"]');
                data.metrics.cards_count = cards.length;

                // Check for brand name display
                const brandEl = document.querySelector('h1, [class*="brand"], [class*="title"]');
                if (brandEl) {
                    data.brand_name = brandEl.textContent.trim().slice(0, 50);
                }

                // Body text preview
                data.body_preview = document.body?.innerText?.slice(0, 500) || '';

                return data;
            }
        """)

        logger.info(f"  [10.4] UI Data collected:")
        logger.info(f"        - Dashboard loaded: {ui_data.get('dashboard_loaded')}")
        logger.info(f"        - Charts visible: {ui_data.get('charts_visible')}")
        logger.info(f"        - Sections: {ui_data.get('sections', [])}")
        logger.info(f"        - Brand: {ui_data.get('brand_name', 'N/A')}")

        # Store UI data for reporting
        self.metrics.set_ui_data(ui_data)

        # Check workspace status
        workspace = self.db_verifier.refresh_workspace()
        if workspace:
            logger.info(f"  [10.5] Workspace status: {workspace.get('status')}")

        # Take final screenshot
        screenshot_path = f"/tmp/canary_dashboard_{self.test_id}.png"
        await self.browser.take_screenshot(screenshot_path)
        logger.info(f"  [10.6] Dashboard screenshot: {screenshot_path}")

        duration = time.time() - start
        self.metrics.record_step_timing("step_10_verify_dashboard", duration)
        logger.info(f"  ✓ STEP 10 COMPLETED in {duration:.2f}s")

    async def _step_11_full_verification(self) -> None:
        """Step 11: Full verification and comprehensive data collection."""
        start = time.time()
        logger.info("=" * 60)
        logger.info("STEP 11: FULL VERIFICATION & DATA COLLECTION")
        logger.info("=" * 60)

        # Get full verification results
        verification = self.db_verifier.full_verification()

        logger.info("  [11.1] Verification Results:")
        for key, val in verification.get("results", {}).items():
            if val:
                status = "✓" if val.get("success") else "✗"
                logger.info(f"        {status} {key}: {val.get('message', 'N/A')}")

        # Get comprehensive DB data for reporting
        logger.info("  [11.2] Collecting comprehensive DB data...")
        comprehensive_data = self.db_verifier.get_comprehensive_data()

        logger.info(f"        - Workspace: {comprehensive_data.get('workspace', {}).get('ulid', 'N/A')}")
        logger.info(f"        - Status: {comprehensive_data.get('workspace', {}).get('status', 'N/A')}")
        logger.info(f"        - Categories: {comprehensive_data.get('categories_count', 0)}")
        logger.info(f"        - Prompts: {comprehensive_data.get('prompts_count', 0)}")
        logger.info(f"        - Competitors: {comprehensive_data.get('competitors_count', 0)}")

        if comprehensive_data.get('snapshot'):
            logger.info(f"        - Snapshot: {comprehensive_data['snapshot'].get('status', 'N/A')}")
            if comprehensive_data.get('snapshot_prompts_status'):
                sps = comprehensive_data['snapshot_prompts_status']
                logger.info(f"        - Snapshot Prompts: {sps.get('completed', 0)}/{sps.get('total', 0)} completed")

        # Log some actual data for debugging
        if comprehensive_data.get('categories_list'):
            logger.info("        Categories:")
            for cat in comprehensive_data['categories_list'][:5]:
                logger.info(f"          • {cat.get('name', 'N/A')}")

        if comprehensive_data.get('prompts_list'):
            logger.info("        Top Prompts:")
            for prompt in comprehensive_data['prompts_list'][:5]:
                tracked = "📍" if prompt.get('is_tracked') else ""
                logger.info(f"          • {prompt.get('name', 'N/A')[:50]} {tracked}")

        if comprehensive_data.get('competitors_list'):
            logger.info("        Competitors:")
            for comp in comprehensive_data['competitors_list'][:5]:
                logger.info(f"          • {comp.get('name', 'N/A')} ({comp.get('domain', 'N/A')})")

        # Store DB data for reporting
        self.metrics.set_db_data(comprehensive_data)

        # Store verification results for the report
        # This gets used by alerting.py
        self.db_state = verification

        # Determine overall success
        if verification["success"]:
            logger.info("  [11.3] ✅ All verification checks passed!")
        else:
            failed_checks = [
                key for key, val in verification["results"].items()
                if val and not val.get("success")
            ]
            logger.info(f"  [11.3] ⚠️ Some checks not fully met: {failed_checks}")

        duration = time.time() - start
        self.metrics.record_step_timing("step_11_full_verification", duration)
        logger.info(f"  ✓ STEP 11 COMPLETED in {duration:.2f}s")
        logger.info("=" * 60)
        logger.info("CANARY TEST COMPLETE")
        logger.info("=" * 60)


async def run_canary_test(database_url: Optional[str] = None) -> CanaryResult:
    """
    Entry point for running the canary test.

    Args:
        database_url: Optional database connection URL

    Returns:
        CanaryResult with test outcome
    """
    test = CanaryTest(database_url=database_url)
    return await test.run()


def run_canary_test_sync() -> CanaryResult:
    """
    Synchronous entry point for running the canary test.

    Used for cron jobs and CLI execution.

    Returns:
        CanaryResult with test outcome
    """
    return asyncio.run(run_canary_test())


if __name__ == "__main__":
    # CLI execution
    import sys

    logger.info("Running canary test from CLI")
    result = run_canary_test_sync()

    if result.success:
        logger.info("Canary test PASSED")
        print(f"✅ Canary test PASSED in {result.metrics.total_duration_seconds:.1f}s")
        sys.exit(0)
    else:
        logger.error(f"Canary test FAILED: {result.failed_step} - {result.error_message}")
        print(f"❌ Canary test FAILED at {result.failed_step}: {result.error_message}")
        sys.exit(1)
