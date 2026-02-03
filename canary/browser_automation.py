"""
Browser Automation Module for Canary Tests

Provides Playwright-based browser automation for the E2E canary test flow.
"""

import asyncio
from typing import Optional
from playwright.async_api import async_playwright, Browser, Page, BrowserContext

from canary.config import get_canary_config
from canary.alerting import CanaryTestError
from canary.utils import get_canary_logger

logger = get_canary_logger("canary.browser_automation")


class BrowserAutomation:
    """
    Playwright-based browser automation for canary tests.

    Handles all browser interactions for the E2E test flow:
    - Landing page navigation
    - Get Report button click
    - Form filling
    - OTP verification
    - Navigation through workspace setup
    - Dashboard verification
    """

    def __init__(self):
        self.config = get_canary_config()
        self._playwright = None
        self._browser: Optional[Browser] = None
        self._context: Optional[BrowserContext] = None
        self._page: Optional[Page] = None

    async def setup(self) -> None:
        """Initialize browser and page."""
        logger.info("Setting up Playwright browser")
        self._playwright = await async_playwright().start()
        self._browser = await self._playwright.chromium.launch(
            headless=self.config.HEADLESS,
            slow_mo=self.config.SLOW_MO
        )
        self._context = await self._browser.new_context(
            viewport={
                "width": self.config.BROWSER_VIEWPORT_WIDTH,
                "height": self.config.BROWSER_VIEWPORT_HEIGHT
            }
        )
        self._page = await self._context.new_page()

        # Set default timeout
        self._page.set_default_timeout(self.config.PAGE_LOAD_TIMEOUT * 1000)

        logger.info("Browser setup complete")

    async def cleanup(self) -> None:
        """Close browser and cleanup resources."""
        try:
            if self._page:
                await self._page.close()
            if self._context:
                await self._context.close()
            if self._browser:
                await self._browser.close()
            if self._playwright:
                await self._playwright.stop()
            logger.info("Browser cleanup complete")
        except Exception as e:
            logger.error(f"Error during browser cleanup: {e}")

    @property
    def page(self) -> Page:
        """Get the current page object."""
        if not self._page:
            raise CanaryTestError("SETUP", "Browser not initialized")
        return self._page

    async def _select_custom_dropdown(self, dropdown_type: str, value: str) -> None:
        """
        Select a value from a custom dropdown component.

        The Maxeo frontend uses custom dropdown components with:
        - Button trigger with placeholder text like "Choose a country..." or "Choose a language..."
        - Portal-rendered dropdown list with data-dropdown="country" or data-dropdown="language"
        - Searchable input within dropdown

        Args:
            dropdown_type: "country" or "language"
            value: The code to select (e.g., "US" for country, "en" for language)
        """
        logger.info(f"Selecting {dropdown_type}: {value}")

        # The trigger button contains placeholder text and is identifiable by:
        # - Label text "Country" or "Language" nearby
        # - Button with "Choose a country..." or "Choose a language..." text
        # - Button with a dropdown arrow icon

        # Find the dropdown trigger button by its label context
        label_text = "Country" if dropdown_type == "country" else "Language"
        placeholder_text = f"Choose a {dropdown_type}..."

        # Try multiple strategies to find the button
        trigger_selectors = [
            # Button with specific placeholder text
            f"button:has-text('{placeholder_text}')",
            f"button:has-text('Select {label_text}')",
            # Button near a label with the dropdown type
            f"label:has-text('{label_text}') + * button",
            f"label:has-text('{label_text}') ~ * button",
            # Look for buttons with dropdown arrow in the form context
            f"div:has(label:text('{label_text}')) button",
        ]

        dropdown_trigger = None
        for selector in trigger_selectors:
            try:
                dropdown_trigger = await self.page.wait_for_selector(selector, timeout=5000)
                if dropdown_trigger:
                    logger.info(f"Found {dropdown_type} trigger with: {selector}")
                    break
            except Exception:
                continue

        if not dropdown_trigger:
            # Fallback: use JavaScript to find by structure
            dropdown_trigger = await self.page.evaluate_handle(f"""
                () => {{
                    // Find label with "Country" or "Language" text
                    const labels = document.querySelectorAll('label');
                    for (const label of labels) {{
                        if (label.textContent.trim() === '{label_text}') {{
                            // Find the button in the same container
                            const container = label.closest('div.flex-col') || label.parentElement;
                            if (container) {{
                                const button = container.querySelector('button');
                                if (button) return button;
                            }}
                        }}
                    }}
                    return null;
                }}
            """)
            if dropdown_trigger:
                logger.info(f"Found {dropdown_type} trigger via JS evaluation")

        if not dropdown_trigger:
            raise Exception(f"Dropdown trigger not found for {dropdown_type}")

        await dropdown_trigger.click()
        logger.info(f"Clicked {dropdown_type} dropdown trigger")

        # Wait a moment for the dropdown to render (it uses Portal)
        await asyncio.sleep(0.5)

        # The dropdown is portal-rendered with data-dropdown="country" or data-dropdown="language"
        # It contains a search input and button options
        # Wait for the portal dropdown to appear
        try:
            await self.page.wait_for_selector(
                f"[data-dropdown='{dropdown_type}']",
                timeout=5000
            )
            logger.info(f"Portal dropdown appeared for {dropdown_type}")
        except Exception:
            logger.warning(f"Portal dropdown not found for {dropdown_type}")

        # Full name mapping for searching
        value_names = {
            "country": {
                "US": "United States",
                "GB": "United Kingdom",
                "DE": "Germany",
                "FR": "France",
                "TR": "Turkey",
                "ES": "Spain",
                "IT": "Italy",
                "NL": "Netherlands",
            },
            "language": {
                "en": "English",
                "de": "German",
                "fr": "French",
                "es": "Spanish",
                "tr": "Turkish",
                "it": "Italian",
                "nl": "Dutch",
                "pt": "Portuguese",
            }
        }
        full_name = value_names.get(dropdown_type, {}).get(value, value)

        # Strategy 1: Search and click the matching option
        # Find search input within the portal dropdown
        try:
            search_input = await self.page.wait_for_selector(
                f"[data-dropdown='{dropdown_type}'] input",
                timeout=3000
            )
            if search_input:
                await search_input.fill(full_name)
                logger.info(f"Typed '{full_name}' in search input")
                await asyncio.sleep(0.5)  # Wait for filter

                # Click the first button option in the dropdown
                option_button = await self.page.wait_for_selector(
                    f"[data-dropdown='{dropdown_type}'] button.cursor-pointer",
                    timeout=3000
                )
                if option_button:
                    await option_button.click()
                    logger.info(f"Clicked option button for {dropdown_type}")
                    await asyncio.sleep(0.3)
                    return
        except Exception as e:
            logger.debug(f"Search strategy failed: {e}")

        # Strategy 2: Use JavaScript to find and click the option by text
        try:
            clicked = await self.page.evaluate(f"""
                () => {{
                    // Find the dropdown portal
                    const dropdown = document.querySelector('[data-dropdown="{dropdown_type}"]');
                    if (!dropdown) return false;

                    // Find buttons within the dropdown
                    const buttons = dropdown.querySelectorAll('button');
                    for (const btn of buttons) {{
                        const text = btn.textContent || '';
                        if (text.includes('{full_name}')) {{
                            btn.click();
                            return true;
                        }}
                    }}
                    return false;
                }}
            """)
            if clicked:
                logger.info(f"Selected {dropdown_type} via JS: {full_name}")
                await asyncio.sleep(0.3)
                return
        except Exception as e:
            logger.debug(f"JS click failed: {e}")

        # Strategy 3: Click using Playwright locator with text
        try:
            option = self.page.locator(f"[data-dropdown='{dropdown_type}'] button", has_text=full_name).first
            await option.click(timeout=5000)
            logger.info(f"Selected {dropdown_type} via Playwright locator: {full_name}")
            await asyncio.sleep(0.3)
            return
        except Exception as e:
            logger.debug(f"Playwright locator failed: {e}")

        raise Exception(f"Could not select {dropdown_type}: {value}")

    async def navigate_to_landing(self) -> None:
        """Navigate to the landing page."""
        url = self.config.BASE_URL
        logger.info(f"Navigating to landing page: {url}")

        try:
            await self.page.goto(url, wait_until="load", timeout=60000)
            # Wait a bit for JS to initialize
            await asyncio.sleep(2)
            logger.info("Landing page loaded successfully")
        except Exception as e:
            raise CanaryTestError(
                "STEP_01_LANDING_PAGE",
                f"Failed to load landing page: {e}",
                {"url": url}
            )

    async def click_get_report_button(self) -> None:
        """Click the 'Get Report' or 'Get Free Report' button on landing page."""
        logger.info("Looking for Get Report button")

        try:
            # Wait for the page to be fully loaded
            await asyncio.sleep(2)

            # Try multiple selectors for the button
            selectors = [
                "button:has-text('Get Free Report')",
                "button:has-text('Get Report')",
                "input[type='submit'][value='Get Report']",
                "[data-testid='get-report-button']",
                # Input field button in the hero section
                "input[placeholder*='Enter your website']"
            ]

            for selector in selectors:
                try:
                    element = await self.page.wait_for_selector(
                        selector,
                        timeout=10000
                    )
                    if element:
                        await element.click()
                        logger.info(f"Clicked button with selector: {selector}")
                        # Wait for dialog/form to appear
                        # The dialog uses fixed positioning with bg-[#00000080] overlay
                        await self.page.wait_for_selector(
                            "div.fixed input[name='brand_url'], div.fixed input[placeholder*='Website'], form input[name='brand_url']",
                            timeout=15000
                        )
                        logger.info("Dialog with form appeared")
                        return
                except Exception as e:
                    logger.debug(f"Selector {selector} failed: {e}")
                    continue

            # If no button found, try clicking anywhere that triggers the popup
            # The input-with-button component might work
            input_selector = "input[placeholder*='website'], input[placeholder*='URL']"
            element = await self.page.wait_for_selector(input_selector, timeout=10000)
            if element:
                await element.click()
                await self.page.wait_for_selector(
                    "div.fixed input[name='brand_url'], form input[name='brand_url']",
                    timeout=15000
                )
                return

            raise CanaryTestError(
                "STEP_02_CLICK_GET_REPORT",
                "Could not find Get Report button"
            )

        except CanaryTestError:
            raise
        except Exception as e:
            raise CanaryTestError(
                "STEP_02_CLICK_GET_REPORT",
                f"Failed to click Get Report button: {e}"
            )

    async def _fill_react_hook_form_input(self, selector: str, value: str) -> bool:
        """
        Fill a react-hook-form controlled input by triggering proper React events.

        react-hook-form tracks values through controlled inputs and expects specific
        events to properly update its internal state. This method uses JavaScript
        to set the value and trigger the necessary React events.

        Returns True if successful, False otherwise.
        """
        try:
            input_el = await self.page.wait_for_selector(selector, timeout=5000)
            if not input_el:
                return False

            # Use JavaScript to set value and trigger React's synthetic events
            # react-hook-form listens to InputEvent, not just Event
            success = await self.page.evaluate("""
                (args) => {
                    const [selector, value] = args;
                    const input = document.querySelector(selector.split(',')[0].trim());
                    if (!input) return false;

                    // Focus the input first
                    input.focus();

                    // Get the native value setter to bypass React's controlled input
                    const nativeInputValueSetter = Object.getOwnPropertyDescriptor(
                        window.HTMLInputElement.prototype, 'value'
                    ).set;

                    // Set the value using the native setter
                    nativeInputValueSetter.call(input, value);

                    // Create and dispatch InputEvent (what React actually listens to)
                    const inputEvent = new InputEvent('input', {
                        bubbles: true,
                        cancelable: true,
                        inputType: 'insertText',
                        data: value
                    });
                    input.dispatchEvent(inputEvent);

                    // Also dispatch a change event for good measure
                    const changeEvent = new Event('change', { bubbles: true });
                    input.dispatchEvent(changeEvent);

                    // Blur to finalize
                    input.blur();

                    console.log('Brand URL value after JS:', input.value);
                    return input.value === value;
                }
            """, [selector, value])

            await asyncio.sleep(0.2)  # Give React time to process

            # Verify the value was set
            final_value = await input_el.input_value()
            logger.info(f"Input {selector}: JS set '{value}', got '{final_value}', success={success}")

            return final_value == value

        except Exception as e:
            logger.warning(f"Failed to fill input {selector}: {e}")
            return False

    async def fill_workspace_form(
        self,
        brand_url: str,
        brand_name: str,
        first_name: str,
        last_name: str,
        email: str,
        country: str = "US",
        language: str = "en"
    ) -> None:
        """Fill in the workspace creation form."""
        logger.info(f"Filling workspace form for {email}")

        # Store full_url for use later (brand_url already includes https://)
        full_url = brand_url

        try:
            # Wait for form to be visible
            await self.page.wait_for_selector("form", timeout=10000)

            # FILL BRAND URL FIRST - before any other fields to avoid re-render issues
            # The domain to type (without protocol)
            domain_only = full_url.replace("https://", "").replace("http://", "")
            full_url_with_protocol = f"https://{domain_only}"

            logger.info(f"Filling brand URL FIRST: {full_url_with_protocol}")

            # Use our special method for react-hook-form inputs
            brand_url_filled = await self._fill_react_hook_form_input(
                "input[name='brand_url'], input[placeholder*='Website'], input[placeholder*='URL']",
                full_url_with_protocol
            )

            if not brand_url_filled:
                # Fallback to regular fill
                brand_url_input = await self.page.wait_for_selector(
                    "input[name='brand_url'], input[placeholder*='Website'], input[placeholder*='URL']",
                    timeout=5000
                )
                if brand_url_input:
                    await brand_url_input.fill(full_url_with_protocol)
                    await asyncio.sleep(0.2)
                    url_val = await brand_url_input.input_value()
                    logger.info(f"Brand URL after fallback fill: '{url_val}'")

            # Fill brand name (if visible)
            try:
                brand_name_input = await self.page.wait_for_selector(
                    "input[name='brand_name'], input[placeholder*='Brand']",
                    timeout=3000
                )
                if brand_name_input:
                    await brand_name_input.fill(brand_name)
                    logger.info(f"Filled brand name: {brand_name}")
            except Exception:
                logger.info("Brand name field not found, skipping")

            # Fill first name
            first_name_input = await self.page.wait_for_selector(
                "input[name='first_name'], input[placeholder*='First']",
                timeout=5000
            )
            if first_name_input:
                await first_name_input.fill(first_name)
                logger.info(f"Filled first name: {first_name}")

            # Fill last name
            last_name_input = await self.page.wait_for_selector(
                "input[name='last_name'], input[placeholder*='Last']",
                timeout=5000
            )
            if last_name_input:
                await last_name_input.fill(last_name)
                logger.info(f"Filled last name: {last_name}")

            # Fill email
            email_input = await self.page.wait_for_selector(
                "input[name='email'], input[type='email'], input[placeholder*='mail']",
                timeout=5000
            )
            if email_input:
                await email_input.fill(email)
                logger.info(f"Filled email: {email}")

            # Select country (custom dropdown with data-dropdown="country")
            try:
                await self._select_custom_dropdown("country", country)
                logger.info(f"Selected country: {country}")
            except Exception as e:
                logger.warning(f"Country selection failed: {e}")
                raise CanaryTestError(
                    "STEP_06_FILL_FORM",
                    f"Failed to select country: {e}",
                    {"country": country}
                )

            # Select language (custom dropdown with data-dropdown="language")
            try:
                await self._select_custom_dropdown("language", language)
                logger.info(f"Selected language: {language}")
            except Exception as e:
                logger.warning(f"Language selection failed: {e}")
                raise CanaryTestError(
                    "STEP_06_FILL_FORM",
                    f"Failed to select language: {e}",
                    {"language": language}
                )

            # Ensure any dropdowns are closed by pressing Escape
            await self.page.keyboard.press("Escape")
            await asyncio.sleep(0.5)

            # Re-verify brand URL at the end - check if it was overwritten by re-renders
            brand_url_input = await self.page.query_selector(
                "input[name='brand_url'], input[placeholder*='Website'], input[placeholder*='URL']"
            )
            if brand_url_input:
                final_url_val = await brand_url_input.input_value()
                logger.info(f"Brand URL final check: '{final_url_val}'")

                # If URL was cleared/reset, re-fill it using the special method
                if not final_url_val or final_url_val == "https://":
                    logger.warning("Brand URL was reset! Re-filling with react-hook-form method...")
                    refill_success = await self._fill_react_hook_form_input(
                        "input[name='brand_url'], input[placeholder*='Website'], input[placeholder*='URL']",
                        full_url_with_protocol
                    )
                    if not refill_success:
                        # Fallback to regular fill
                        await brand_url_input.fill(full_url_with_protocol)
                        await asyncio.sleep(0.2)
                        recheck_val = await brand_url_input.input_value()
                        logger.info(f"Brand URL after fallback re-fill: '{recheck_val}'")

        except CanaryTestError:
            raise
        except Exception as e:
            raise CanaryTestError(
                "STEP_06_FILL_FORM",
                f"Failed to fill workspace form: {e}",
                {"email": email}
            )

    async def submit_workspace_form(self) -> None:
        """Submit the workspace creation form."""
        logger.info("Submitting workspace form")

        try:
            # Check brand URL value right before submit
            brand_url_input = await self.page.query_selector(
                "input[name='brand_url'], input[placeholder*='Website'], input[placeholder*='URL']"
            )
            if brand_url_input:
                val = await brand_url_input.input_value()
                logger.info(f"Brand URL value just before submit: '{val}'")

            # Find and click submit button
            submit_selectors = [
                "input[type='submit'][value='Get Report']",
                "button[type='submit']:has-text('Get Report')",
                "button:has-text('Get Report')",
                "input[type='submit']",
                "button[type='submit']"
            ]

            for selector in submit_selectors:
                try:
                    button = await self.page.wait_for_selector(selector, timeout=3000)
                    if button:
                        await button.click()
                        logger.info(f"Clicked submit button: {selector}")
                        return
                except Exception:
                    continue

            raise CanaryTestError(
                "STEP_06_SUBMIT_FORM",
                "Could not find submit button"
            )

        except CanaryTestError:
            raise
        except Exception as e:
            raise CanaryTestError(
                "STEP_06_SUBMIT_FORM",
                f"Failed to submit form: {e}"
            )

    async def wait_for_otp_input(self) -> None:
        """Wait for OTP input to appear."""
        logger.info("    [OTP] ========== OTP WAIT SEQUENCE ==========")

        try:
            # Phase 1: Wait for loading to complete
            logger.info("    [OTP] Phase 1: Waiting for loading to complete (max 60s)...")
            max_wait = 60
            start_time = asyncio.get_event_loop().time()
            check_count = 0

            while (asyncio.get_event_loop().time() - start_time) < max_wait:
                check_count += 1
                elapsed = asyncio.get_event_loop().time() - start_time

                loading_button = await self.page.query_selector("button:has-text('Loading')")
                form_dialog = await self.page.query_selector("div.fixed input[name='brand_url']")

                # Log every 5 seconds
                if check_count % 5 == 0:
                    logger.info(f"    [OTP] ... {elapsed:.0f}s: loading={loading_button is not None}, form_visible={form_dialog is not None}")

                if not loading_button:
                    logger.info(f"    [OTP] ✓ Loading button gone after {elapsed:.1f}s")
                    break

                if not form_dialog:
                    logger.info(f"    [OTP] ✓ Form closed after {elapsed:.1f}s")
                    break

                await asyncio.sleep(1)

            total_wait = asyncio.get_event_loop().time() - start_time
            if total_wait >= max_wait:
                logger.warning(f"    [OTP] ⚠ TIMEOUT after {max_wait}s - form still loading!")

            # Phase 2: Wait for navigation
            logger.info("    [OTP] Phase 2: Waiting 2s for page transition...")
            await asyncio.sleep(2)

            current_url = self.page.url
            logger.info(f"    [OTP] Current URL: {current_url}")

            # Take screenshot
            await self.take_screenshot("/tmp/canary_otp_phase2.png")
            logger.info("    [OTP] Screenshot: /tmp/canary_otp_phase2.png")

            # Phase 3: Check for errors
            logger.info("    [OTP] Phase 3: Checking for error messages...")
            error_msg = await self.page.evaluate("""
                () => {
                    const errorSelectors = [
                        '.error', '.error-message', '[class*="error"]',
                        '.snackbar', '[class*="toast"]', '[role="alert"]',
                        'p[class*="text-red"]', 'span[class*="text-red"]'
                    ];
                    for (const sel of errorSelectors) {
                        const el = document.querySelector(sel);
                        if (el && el.textContent && el.textContent.trim()) {
                            return el.textContent.trim();
                        }
                    }
                    return null;
                }
            """)
            if error_msg:
                logger.warning(f"    [OTP] ⚠ PAGE ERROR: {error_msg}")
            else:
                logger.info("    [OTP] No error messages found")

            # Get page info
            page_info = await self.page.evaluate("""
                () => ({
                    title: document.title,
                    hasOtpInput: !!document.querySelector('input[maxlength="1"]'),
                    hasForm: !!document.querySelector('form'),
                    buttonTexts: Array.from(document.querySelectorAll('button')).map(b => b.textContent.trim()).slice(0, 5)
                })
            """)
            logger.info(f"    [OTP] Page: title='{page_info.get('title')}', hasOtpInput={page_info.get('hasOtpInput')}, buttons={page_info.get('buttonTexts')}")

            # Phase 4: Look for OTP elements
            logger.info("    [OTP] Phase 4: Searching for OTP input elements...")
            selectors = [
                ("text='Verify your email'", "Verify text"),
                ("text='verification code'", "verification code"),
                ("text='Enter the code'", "Enter code"),
                ("input[maxlength='1'][inputmode='numeric']", "numeric input"),
                ("input[aria-label*='Digit']", "digit input"),
                ("input[name='totp']", "totp input"),
            ]

            for selector, name in selectors:
                logger.info(f"    [OTP] Trying: {name}...")
                try:
                    await self.page.wait_for_selector(selector, timeout=10000)
                    logger.info(f"    [OTP] ✓ FOUND: {name}")
                    await asyncio.sleep(1)
                    return
                except Exception:
                    logger.info(f"    [OTP] ✗ Not found: {name}")
                    continue

            # Failed - take final screenshot
            await self.take_screenshot("/tmp/canary_otp_not_found.png")
            logger.error("    [OTP] ✗✗✗ OTP SCREEN NOT FOUND ✗✗✗")
            logger.error("    [OTP] Screenshot: /tmp/canary_otp_not_found.png")

            raise CanaryTestError(
                "STEP_03_OTP_WAIT",
                "OTP input screen did not appear"
            )
        except CanaryTestError:
            raise
        except Exception as e:
            logger.error(f"    [OTP] Exception: {e}")
            raise CanaryTestError(
                "STEP_03_OTP_WAIT",
                f"OTP input did not appear: {e}"
            )

    async def fill_otp(self, otp_code: str) -> None:
        """Fill in the OTP code."""
        logger.info(f"Filling OTP code: {otp_code}")

        try:
            # Try multiple OTP inputs (one per digit) - this is the most common pattern
            otp_inputs = await self.page.query_selector_all(
                "input[maxlength='1'][inputmode='numeric'], input[aria-label*='Digit'], input[maxlength='1'][type='text']"
            )

            if len(otp_inputs) >= 6:
                for i, digit in enumerate(otp_code[:6]):
                    await otp_inputs[i].fill(digit)
                    await asyncio.sleep(0.1)  # Small delay between digits
                logger.info("Filled multiple OTP inputs")
                return

            # Try single input as fallback
            try:
                single_input = await self.page.wait_for_selector(
                    "input[name='totp'], input[maxlength='6']",
                    timeout=3000
                )
                if single_input:
                    await single_input.fill(otp_code)
                    logger.info("Filled single OTP input")
                    return
            except Exception:
                pass

            # Try pasting into the first input
            first_input = await self.page.query_selector("input[maxlength='1']")
            if first_input:
                await first_input.focus()
                await self.page.keyboard.type(otp_code)
                logger.info("Typed OTP code via keyboard")
                return

            raise CanaryTestError(
                "STEP_05_FILL_OTP",
                "Could not find OTP inputs",
                {"found_inputs": len(otp_inputs)}
            )

        except CanaryTestError:
            raise
        except Exception as e:
            raise CanaryTestError(
                "STEP_05_FILL_OTP",
                f"Failed to fill OTP: {e}"
            )

    async def submit_otp(self) -> None:
        """Submit the OTP verification form and wait for page transition."""
        logger.info("Submitting OTP")

        try:
            # Some forms auto-submit, so check if we've already moved on
            try:
                await self.page.wait_for_selector(
                    "[class*='loader'], [class*='loading'], [class*='spinner']",
                    timeout=3000
                )
                logger.info("Form auto-submitted, loading state detected")
            except Exception:
                pass

            # Try to find and click submit button
            submit_selectors = [
                "button:has-text('Verify')",
                "button:has-text('Submit')",
                "button[type='submit']",
                "input[type='submit']"
            ]

            clicked = False
            for selector in submit_selectors:
                try:
                    button = await self.page.wait_for_selector(selector, timeout=3000)
                    if button:
                        await button.click()
                        logger.info(f"Clicked OTP submit: {selector}")
                        clicked = True
                        break
                except Exception:
                    continue

            if not clicked:
                logger.info("No submit button found, form may auto-submit")

            # IMPORTANT: Wait for page transition after OTP submit
            await self._wait_for_post_otp_transition()

        except Exception as e:
            logger.warning(f"OTP submit note: {e}")

    async def _wait_for_post_otp_transition(self, timeout: int = 60) -> None:
        """
        Wait for the page to transition after OTP verification.

        After successful OTP, the page should either:
        1. Navigate to /workspace/{ulid}/loading or similar
        2. Show a loading screen for categories
        3. Close the OTP modal and show the landing page (which will then redirect)
        4. Show a success state with a "Continue" button
        """
        logger.info("Waiting for post-OTP page transition...")
        start_time = asyncio.get_event_loop().time()
        last_log_time = 0
        tried_continue_click = False

        while (asyncio.get_event_loop().time() - start_time) < timeout:
            elapsed = asyncio.get_event_loop().time() - start_time

            # Check current URL for workspace navigation
            current_url = self.page.url
            url_has_workspace = "/workspace/" in current_url

            # Success condition 1: URL changed to workspace
            if url_has_workspace:
                logger.info(f"✓ Navigated to workspace URL: {current_url}")
                await asyncio.sleep(2)  # Give page time to load
                return

            # Check page state using JavaScript to avoid CSS selector issues
            page_state = await self.page.evaluate("""
                () => {
                    // Check for OTP inputs (6 single-digit inputs)
                    const otpInputs = document.querySelectorAll('input[maxlength="1"]');
                    const hasOtpInputs = otpInputs.length >= 6;

                    // Check for "Verify" text (OTP screen indicator)
                    const hasVerifyText = document.body.innerText.includes('Verify your email') ||
                                         document.body.innerText.includes('verification code');

                    // Check for loading indicators
                    const hasLoading = !!document.querySelector('[class*="loading"]') ||
                                      !!document.querySelector('[class*="spinner"]') ||
                                      !!document.querySelector('[class*="loader"]');

                    // Check for categories/workspace indicators
                    const hasCategories = document.body.innerText.includes('Setting up') ||
                                         document.body.innerText.includes('Analyzing') ||
                                         document.body.innerText.includes('categories');

                    // Check for success/continue buttons in modal
                    const buttons = Array.from(document.querySelectorAll('button'));
                    const continueBtn = buttons.find(b =>
                        b.textContent.toLowerCase().includes('continue') ||
                        b.textContent.toLowerCase().includes('start') ||
                        b.textContent.toLowerCase().includes('go to') ||
                        b.textContent.toLowerCase().includes('workspace')
                    );

                    return {
                        hasOtpInputs,
                        hasVerifyText,
                        hasLoading,
                        hasCategories,
                        hasContinueButton: !!continueBtn,
                        continueButtonText: continueBtn ? continueBtn.textContent.trim() : null,
                        url: window.location.href,
                        title: document.title
                    };
                }
            """)

            otp_modal_visible = page_state.get('hasOtpInputs') or page_state.get('hasVerifyText')
            has_loading_or_categories = page_state.get('hasLoading') or page_state.get('hasCategories')
            has_continue = page_state.get('hasContinueButton')

            # Log status every 5 seconds
            if elapsed - last_log_time >= 5:
                logger.info(f"  ... {elapsed:.0f}s: otp_modal={otp_modal_visible}, url={current_url}, loading={has_loading_or_categories}, continue_btn={has_continue}")
                last_log_time = elapsed

            # Try to click continue button if found and not tried yet
            if has_continue and not tried_continue_click:
                logger.info(f"  Found continue button: '{page_state.get('continueButtonText')}' - clicking...")
                try:
                    continue_selectors = [
                        "button:has-text('Continue')",
                        "button:has-text('Start')",
                        "button:has-text('Go to')",
                        "button:has-text('Workspace')"
                    ]
                    for sel in continue_selectors:
                        try:
                            btn = await self.page.wait_for_selector(sel, timeout=2000)
                            if btn:
                                await btn.click()
                                logger.info(f"  ✓ Clicked: {sel}")
                                tried_continue_click = True
                                await asyncio.sleep(3)
                                break
                        except Exception:
                            continue
                except Exception as e:
                    logger.warning(f"  Failed to click continue button: {e}")
                    tried_continue_click = True

            # Success condition 2: OTP modal is gone
            if not otp_modal_visible:
                logger.info(f"✓ OTP modal closed after {elapsed:.1f}s")
                # Wait a bit more for navigation to complete
                await asyncio.sleep(3)

                # Check if URL changed
                new_url = self.page.url
                if "/workspace/" in new_url:
                    logger.info(f"✓ Navigated to workspace: {new_url}")
                    return
                else:
                    logger.info(f"OTP closed but still on {new_url} - waiting for redirect...")
                    # Wait up to 10 more seconds for redirect
                    for _ in range(10):
                        await asyncio.sleep(1)
                        new_url = self.page.url
                        if "/workspace/" in new_url:
                            logger.info(f"✓ Navigated to workspace: {new_url}")
                            return
                    # If still no redirect, take screenshot and continue
                    logger.warning(f"No redirect after OTP - current URL: {new_url}")
                    await self.take_screenshot("/tmp/canary_no_redirect_after_otp.png")
                    return

            await asyncio.sleep(1)

        # Timeout - take screenshot for debugging
        await self.take_screenshot("/tmp/canary_otp_transition_timeout.png")
        logger.warning(f"Post-OTP transition timeout after {timeout}s")
        logger.warning(f"Current URL: {self.page.url}")
        logger.warning("Screenshot saved: /tmp/canary_otp_transition_timeout.png")

    async def wait_for_categories_loading(self) -> None:
        """Wait for categories loading screen or workspace page."""
        logger.info("Waiting for categories loading / workspace page")

        try:
            # Check current URL to see if we're already on a workspace page
            current_url = self.page.url
            logger.info(f"Current URL: {current_url}")

            if "/workspace/" in current_url:
                logger.info("Already on workspace page - checking for loading indicators")

            # Take screenshot to see current state
            await self.take_screenshot("/tmp/canary_categories_loading_check.png")

            # Try multiple selectors for loading state
            loading_selectors = [
                "[class*='loader']",
                "[class*='loading']",
                "[class*='spinner']",
                "text='Setting up'",
                "text='Analyzing'",
                "text='categories'",
                "text='topics'",
                # Also check for the categories result (may already be loaded)
                "[class*='category']",
                "text='Continue'"
            ]

            for selector in loading_selectors:
                try:
                    element = await self.page.wait_for_selector(selector, timeout=5000)
                    if element:
                        logger.info(f"Found element: {selector}")
                        return
                except Exception:
                    continue

            # If no loading indicators, check page content
            page_content = await self.page.evaluate("""
                () => ({
                    title: document.title,
                    url: window.location.href,
                    bodyText: document.body?.innerText?.slice(0, 500) || ''
                })
            """)
            logger.info(f"Page state: title='{page_content.get('title')}', url='{page_content.get('url')}'")
            logger.info(f"Body preview: {page_content.get('bodyText', '')[:200]}...")

        except Exception as e:
            logger.warning(f"Categories loading screen check: {e}")

    async def wait_for_prompts_page(self, timeout_seconds: int = 120) -> None:
        """Wait for prompts page to appear."""
        logger.info("Waiting for prompts page")

        try:
            # Poll for prompts page indicators
            start_time = asyncio.get_event_loop().time()
            while (asyncio.get_event_loop().time() - start_time) < timeout_seconds:
                # Check for prompts page elements
                try:
                    prompts_indicator = await self.page.query_selector(
                        "[class*='prompt'], text='prompts', text='queries', button:has-text('Continue')"
                    )
                    if prompts_indicator:
                        logger.info("Prompts page detected")
                        return
                except Exception:
                    pass

                await asyncio.sleep(self.config.POLLING_INTERVAL)

            raise CanaryTestError(
                "STEP_08_PROMPTS_PAGE",
                f"Prompts page did not appear within {timeout_seconds}s"
            )

        except CanaryTestError:
            raise
        except Exception as e:
            raise CanaryTestError(
                "STEP_08_PROMPTS_PAGE",
                f"Failed waiting for prompts page: {e}"
            )

    async def click_continue_prompts(self) -> None:
        """Click continue on prompts/categories page."""
        logger.info("Looking for continue/submit button")

        try:
            # First, log all visible buttons on the page
            all_buttons = await self.page.evaluate("""
                () => {
                    const buttons = document.querySelectorAll('button, input[type="submit"]');
                    return Array.from(buttons).map(b => ({
                        text: b.textContent?.trim() || b.value || '',
                        type: b.type || 'button',
                        visible: b.offsetParent !== null,
                        disabled: b.disabled
                    })).filter(b => b.visible && !b.disabled);
                }
            """)
            logger.info(f"Found {len(all_buttons)} visible buttons: {[b['text'][:30] for b in all_buttons]}")

            # Try multiple selectors
            continue_selectors = [
                "button:has-text('Continue')",
                "button:has-text('Submit')",
                "button:has-text('Confirm')",
                "button:has-text('Next')",
                "button:has-text('Save')",
                "button:has-text('Proceed')",
                "input[type='submit']",
                "button[type='submit']"
            ]

            for selector in continue_selectors:
                try:
                    button = await self.page.wait_for_selector(selector, timeout=5000)
                    if button:
                        # Check if button is visible and enabled
                        is_enabled = await button.is_enabled()
                        if is_enabled:
                            await button.click()
                            logger.info(f"Clicked button: {selector}")
                            return
                        else:
                            logger.info(f"Button found but disabled: {selector}")
                except Exception:
                    continue

            # If no standard button found, try using JavaScript to find and click
            clicked = await self.page.evaluate("""
                () => {
                    // Look for buttons with common continue/submit text
                    const patterns = ['continue', 'submit', 'confirm', 'next', 'save', 'proceed'];
                    const buttons = document.querySelectorAll('button, input[type="submit"]');

                    for (const btn of buttons) {
                        if (btn.offsetParent === null || btn.disabled) continue;
                        const text = (btn.textContent || btn.value || '').toLowerCase();
                        for (const pattern of patterns) {
                            if (text.includes(pattern)) {
                                btn.click();
                                return true;
                            }
                        }
                    }
                    return false;
                }
            """)

            if clicked:
                logger.info("Clicked continue button via JavaScript")
                return

            # Take screenshot to debug
            await self.take_screenshot("/tmp/canary_no_continue_button.png")
            logger.error("No continue button found - screenshot saved to /tmp/canary_no_continue_button.png")

            raise CanaryTestError(
                "STEP_08_PROMPTS_CONTINUE",
                f"Could not find continue button. Available buttons: {[b['text'][:30] for b in all_buttons]}"
            )

        except CanaryTestError:
            raise
        except Exception as e:
            raise CanaryTestError(
                "STEP_08_PROMPTS_CONTINUE",
                f"Failed to click continue: {e}"
            )

    async def wait_for_snapshot_loading(self, timeout_seconds: int = 300) -> None:
        """Wait for snapshot processing to complete."""
        logger.info(f"Waiting for snapshot processing (max {timeout_seconds}s)")

        try:
            start_time = asyncio.get_event_loop().time()
            while (asyncio.get_event_loop().time() - start_time) < timeout_seconds:
                # Check if we've reached dashboard or success page
                try:
                    dashboard = await self.page.query_selector(
                        "[class*='dashboard'], [class*='workspace'], [class*='success']"
                    )
                    if dashboard:
                        logger.info("Dashboard/success page detected")
                        return
                except Exception:
                    pass

                # Check for completion indicators
                try:
                    success = await self.page.query_selector(
                        "text='success', text='complete', text='ready'"
                    )
                    if success:
                        logger.info("Success indicator detected")
                        return
                except Exception:
                    pass

                await asyncio.sleep(self.config.POLLING_INTERVAL)

            raise CanaryTestError(
                "STEP_09_SNAPSHOT_WAIT",
                f"Snapshot did not complete within {timeout_seconds}s"
            )

        except CanaryTestError:
            raise
        except Exception as e:
            raise CanaryTestError(
                "STEP_09_SNAPSHOT_WAIT",
                f"Failed waiting for snapshot: {e}"
            )

    async def verify_dashboard_loaded(self) -> bool:
        """Verify that the dashboard has loaded with data."""
        logger.info("Verifying dashboard loaded")

        try:
            # Look for dashboard elements
            dashboard_selectors = [
                "[class*='chart']",
                "[class*='graph']",
                "[class*='metric']",
                "[class*='competitor']",
                "[class*='mention']",
                "[class*='score']"
            ]

            for selector in dashboard_selectors:
                try:
                    element = await self.page.query_selector(selector)
                    if element:
                        logger.info(f"Dashboard element found: {selector}")
                        return True
                except Exception:
                    continue

            logger.warning("Dashboard elements not found")
            return False

        except Exception as e:
            logger.error(f"Error verifying dashboard: {e}")
            return False

    async def get_current_url(self) -> str:
        """Get the current page URL."""
        return self.page.url

    async def take_screenshot(self, path: str) -> None:
        """Take a screenshot of the current page."""
        try:
            await self.page.screenshot(path=path)
            logger.info(f"Screenshot saved: {path}")
        except Exception as e:
            logger.error(f"Failed to take screenshot: {e}")

    async def get_workspace_ulid_from_url(self) -> Optional[str]:
        """Extract workspace ULID from current URL."""
        try:
            url = self.page.url
            # URL format: .../workspace/{ulid}/...
            if "/workspace/" in url:
                parts = url.split("/workspace/")
                if len(parts) > 1:
                    ulid = parts[1].split("/")[0].split("?")[0]
                    if len(ulid) == 26:  # ULID length
                        return ulid
            return None
        except Exception:
            return None
