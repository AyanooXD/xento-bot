"""
Browser Automation Module for xento.org Account Creation
Uses Playwright to automate the signup flow:
1. Navigate to xento.org with referral link
2. Click sign-in button
3. Enter email address
4. Enter OTP verification code
5. Handle referral prompt
"""

import asyncio
import logging
import re
from playwright.async_api import async_playwright, Page, BrowserContext

logger = logging.getLogger(__name__)

XENTO_URL = "https://xento.org"
PRIVY_APP_ID = "2e00e80a-60f2-41d2-bd52-2f10afe0d59f"


class XentoSignup:
    """Automate xento.org account creation via browser"""

    def __init__(self, headless=True):
        self.headless = headless
        self.browser = None
        self.context = None
        self.page = None
        self._playwright = None

    async def start(self):
        """Launch browser with resource-efficient settings"""
        self._playwright = await async_playwright().start()
        self.browser = await self._playwright.chromium.launch(
            headless=self.headless,
            args=[
                '--no-sandbox',
                '--disable-setuid-sandbox',
                '--disable-dev-shm-usage',
                '--disable-gpu',
                '--disable-software-rasterizer',
                '--disable-extensions',
                '--disable-background-networking',
                '--disable-sync',
                '--metrics-recording-only',
                '--no-first-run',
                '--safebrowsing-disable-auto-update',
                '--window-size=1280,720',
                '--single-process',
            ]
        )
        self.context = await self.browser.new_context(
            viewport={"width": 1280, "height": 720},
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            ignore_https_errors=True,
        )
        self.page = await self.context.new_page()
        # Set default timeout
        self.page.set_default_timeout(30000)
        logger.info("Browser launched")

    async def close(self):
        """Close browser"""
        try:
            if self.context:
                await self.context.close()
            if self.browser:
                await self.browser.close()
            if self._playwright:
                await self._playwright.stop()
        except Exception as e:
            logger.error(f"Error closing browser: {e}")

    async def create_account(self, email: str, otp_code: str, referral_code: str = None) -> dict:
        """
        Full account creation flow:
        1. Navigate to xento.org (with referral if provided)
        2. Click sign in
        3. Enter email
        4. Enter OTP code
        5. Handle referral prompt

        Returns dict with success status and details
        """
        result = {"success": False, "email": email, "referral_code": referral_code}

        try:
            # Step 1: Navigate with referral link
            url = XENTO_URL
            if referral_code:
                # Clean referral code - extract from URL if full URL given
                code = self._extract_referral_code(referral_code)
                url = f"{XENTO_URL}/?ref={code}"
                result["referral_code"] = code
                logger.info(f"Navigating with referral: {url}")
            else:
                logger.info(f"Navigating to: {url}")

            await self.page.goto(url, wait_until="networkidle", timeout=30000)
            await asyncio.sleep(2)

            # Step 2: Click sign in button
            signin_ok = await self._click_signin()
            if not signin_ok:
                logger.error("Could not find/click sign-in button")
                result["error"] = "Sign-in button not found"
                return result

            await asyncio.sleep(2)

            # Step 3: Enter email
            email_ok = await self._enter_email(email)
            if not email_ok:
                logger.error("Could not enter email")
                result["error"] = "Email entry failed"
                return result

            await asyncio.sleep(2)

            # Step 4: Enter OTP
            otp_ok = await self._enter_otp(otp_code)
            if not otp_ok:
                logger.error("Could not enter OTP")
                result["error"] = "OTP entry failed"
                return result

            await asyncio.sleep(3)

            # Step 5: Handle referral prompt
            await self._handle_referral_prompt(referral_code)

            await asyncio.sleep(2)

            # Check if we're logged in
            logged_in = await self._check_logged_in()
            result["success"] = logged_in

            if logged_in:
                # Try to get the user's own referral code
                own_code = await self._get_own_referral_code()
                result["own_referral_code"] = own_code
                logger.info(f"Account created! Own referral code: {own_code}")
            else:
                result["error"] = "Could not confirm login"

            return result

        except Exception as e:
            logger.error(f"Account creation failed: {e}")
            result["error"] = str(e)
            return result

    async def signup_step1_open_signin(self, referral_code: str = None) -> bool:
        """Step 1: Navigate and open sign-in modal. Returns True if modal opened."""
        try:
            url = XENTO_URL
            if referral_code:
                code = self._extract_referral_code(referral_code)
                url = f"{XENTO_URL}/?ref={code}"

            await self.page.goto(url, wait_until="networkidle", timeout=30000)
            await asyncio.sleep(2)

            return await self._click_signin()
        except Exception as e:
            logger.error(f"Step 1 failed: {e}")
            return False

    async def signup_step2_enter_email(self, email: str) -> bool:
        """Step 2: Enter email in the sign-in modal"""
        try:
            return await self._enter_email(email)
        except Exception as e:
            logger.error(f"Step 2 failed: {e}")
            return False

    async def signup_step3_enter_otp(self, otp_code: str) -> bool:
        """Step 3: Enter the OTP code"""
        try:
            return await self._enter_otp(otp_code)
        except Exception as e:
            logger.error(f"Step 3 failed: {e}")
            return False

    async def signup_step4_handle_referral(self, referral_code: str = None):
        """Step 4: Handle the referral prompt after auth"""
        try:
            await self._handle_referral_prompt(referral_code)
        except Exception as e:
            logger.error(f"Step 4 failed: {e}")

    def _extract_referral_code(self, input_str: str) -> str:
        """Extract referral code from URL or raw code"""
        if not input_str:
            return None
        # If it's a URL, extract the ref parameter
        match = re.search(r'[?&]ref=([2-9A-HJ-NP-Za-hj-np-z]{8})', input_str)
        if match:
            return match.group(1).upper()
        # If it's just a code (8 chars matching the pattern)
        if re.match(r'^[2-9A-HJ-NP-Za-hj-np-z]{8}$', input_str):
            return input_str.upper()
        # Return as-is and let the site validate
        return input_str.strip().upper()

    async def _safe_goto(self, url: str, max_retries: int = 2) -> bool:
        """Navigate to URL with retry on page crash"""
        for attempt in range(max_retries):
            try:
                await self.page.goto(url, wait_until="domcontentloaded", timeout=30000)
                # Wait for page to be interactive
                await asyncio.sleep(3)
                # Verify page is alive
                await self.page.evaluate("1+1")
                return True
            except Exception as e:
                err_str = str(e)
                if "crashed" in err_str.lower() or "target closed" in err_str.lower():
                    logger.warning(f"Page crashed on attempt {attempt+1}, retrying...")
                    if attempt < max_retries - 1:
                        # Recreate page
                        try:
                            await self.page.close()
                        except Exception:
                            pass
                        self.page = await self.context.new_page()
                        await asyncio.sleep(2)
                    continue
                else:
                    logger.error(f"Navigation error: {e}")
                    return False
        return False

    async def _click_signin(self) -> bool:
        """Click the sign-in button on the homepage (with retry)"""
        for attempt in range(3):
            try:
                # Try multiple selectors for the sign-in button
                selectors = [
                    'button:has-text("Sign in")',
                    'a:has-text("Sign in")',
                    'button:has-text("Get started")',
                    'button:has-text("Start winning")',
                    '[data-testid="signin"]',
                    'text=Sign in',
                ]

                for selector in selectors:
                    try:
                        element = self.page.locator(selector).first
                        if await element.is_visible(timeout=3000):
                            await element.click()
                            logger.info(f"Clicked sign-in via: {selector}")
                            await asyncio.sleep(2)
                            return True
                    except Exception:
                        continue

                # Fallback: try to find any button with sign-in text
                buttons = await self.page.query_selector_all('button, a')
                for btn in buttons:
                    text = await btn.text_content()
                    if text and ('sign in' in text.lower() or 'get started' in text.lower() or 'start winning' in text.lower()):
                        await btn.click()
                        logger.info(f"Clicked button: {text}")
                        await asyncio.sleep(2)
                        return True

                if attempt < 2:
                    logger.warning(f"Sign-in button not found (attempt {attempt+1}/3), waiting and retrying...")
                    await asyncio.sleep(3)
                    # Try reloading page
                    try:
                        await self.page.reload(wait_until="domcontentloaded", timeout=15000)
                        await asyncio.sleep(3)
                    except Exception:
                        pass
                    continue

            except Exception as e:
                logger.error(f"Error clicking sign-in: {e}")
                if attempt < 2:
                    await asyncio.sleep(3)
                    continue
                return False

        logger.warning("No sign-in button found after 3 attempts")
        return False

    async def _enter_email(self, email: str) -> bool:
        """Enter email in the Privy auth modal"""
        try:
            # Wait for the email input to appear
            selectors = [
                'input[type="email"]',
                'input[placeholder*="email"]',
                'input[placeholder*="@"]',
                'input[name="email"]',
            ]

            email_input = None
            for selector in selectors:
                try:
                    element = self.page.locator(selector).first
                    if await element.is_visible(timeout=5000):
                        email_input = element
                        logger.info(f"Found email input via: {selector}")
                        break
                except Exception:
                    continue

            if not email_input:
                # Fallback: look for any text input in the modal
                try:
                    inputs = await self.page.query_selector_all('input[type="text"], input:not([type])')
                    for inp in inputs:
                        placeholder = await inp.get_attribute("placeholder") or ""
                        if "@" in placeholder or "email" in placeholder.lower():
                            email_input = inp
                            break
                except Exception:
                    pass

            if not email_input:
                logger.error("Could not find email input")
                return False

            # Clear and type email
            await email_input.click()
            await email_input.fill("")
            await asyncio.sleep(0.3)
            await email_input.type(email, delay=50)
            await asyncio.sleep(1)

            # Click submit/continue button
            submit_selectors = [
                'button:has-text("Continue")',
                'button:has-text("Submit")',
                'button:has-text("Sign in")',
                'button:has-text("Log in")',
                'button:has-text("Next")',
                'button[type="submit"]',
            ]

            for selector in submit_selectors:
                try:
                    btn = self.page.locator(selector).first
                    if await btn.is_visible(timeout=2000):
                        await btn.click()
                        logger.info(f"Clicked submit via: {selector}")
                        return True
                except Exception:
                    continue

            # Try pressing Enter
            await email_input.press("Enter")
            logger.info("Pressed Enter to submit email")
            await asyncio.sleep(2)
            return True

        except Exception as e:
            logger.error(f"Error entering email: {e}")
            return False

    async def _enter_otp(self, otp_code: str) -> bool:
        """Enter the 6-digit OTP code in the verification modal"""
        try:
            # Wait for OTP input to appear
            await asyncio.sleep(3)

            # Strategy 1: 6 separate input boxes (common Privy pattern)
            otp_inputs = await self.page.query_selector_all(
                'input[type="text"], input[type="number"], input:not([type]), input[maxlength="1"]'
            )

            # Filter to likely OTP inputs (single char inputs)
            single_char_inputs = []
            for inp in otp_inputs:
                try:
                    maxlength = await inp.get_attribute("maxlength")
                    is_visible = await inp.is_visible()
                    if is_visible and (maxlength == "1" or maxlength is None):
                        single_char_inputs.append(inp)
                except Exception:
                    continue

            if len(single_char_inputs) >= 6:
                # Type each digit into separate inputs
                for i, digit in enumerate(otp_code[:6]):
                    await single_char_inputs[i].click()
                    await asyncio.sleep(0.1)
                    await single_char_inputs[i].fill(digit)
                    await asyncio.sleep(0.2)
                logger.info("Entered OTP via individual digit inputs")
                await asyncio.sleep(2)
                return True

            # Strategy 2: Single input for the full code
            code_selectors = [
                'input[placeholder*="code"]',
                'input[placeholder*="Code"]',
                'input[placeholder*="verification"]',
                'input[placeholder*="OTP"]',
                'input[name*="code"]',
                'input[name*="otp"]',
            ]

            for selector in code_selectors:
                try:
                    element = self.page.locator(selector).first
                    if await element.is_visible(timeout=2000):
                        await element.click()
                        await element.fill(otp_code)
                        logger.info(f"Entered OTP via: {selector}")

                        # Try to submit
                        try:
                            submit = self.page.locator('button[type="submit"]').first
                            if await submit.is_visible(timeout=2000):
                                await submit.click()
                        except Exception:
                            await element.press("Enter")

                        await asyncio.sleep(2)
                        return True
                except Exception:
                    continue

            # Strategy 3: Find any visible input in the verification modal and type
            try:
                # Look for modal dialog
                modal = await self.page.query_selector('[role="dialog"]')
                if modal:
                    inputs = await modal.query_selector_all('input')
                    visible_inputs = []
                    for inp in inputs:
                        if await inp.is_visible():
                            visible_inputs.append(inp)

                    if len(visible_inputs) >= 6:
                        for i, digit in enumerate(otp_code[:6]):
                            await visible_inputs[i].click()
                            await visible_inputs[i].fill(digit)
                            await asyncio.sleep(0.2)
                        logger.info("Entered OTP via modal inputs")
                        await asyncio.sleep(2)
                        return True
                    elif len(visible_inputs) == 1:
                        await visible_inputs[0].fill(otp_code)
                        await visible_inputs[0].press("Enter")
                        logger.info("Entered OTP via single modal input")
                        await asyncio.sleep(2)
                        return True
            except Exception as e:
                logger.error(f"Strategy 3 failed: {e}")

            logger.error("Could not find OTP input fields")
            return False

        except Exception as e:
            logger.error(f"Error entering OTP: {e}")
            return False

    async def _handle_referral_prompt(self, referral_code: str = None):
        """Handle the referral prompt that appears after first login"""
        try:
            await asyncio.sleep(2)

            # Check if referral prompt appeared
            # If there's a stored referral code from URL, it auto-applies
            # If not, and we have a code, enter it manually

            if referral_code:
                code = self._extract_referral_code(referral_code)

                # Look for referral code input
                ref_selectors = [
                    'input[placeholder*="ABCD"]',
                    'input[placeholder*="referral"]',
                    'input[placeholder*="code"]',
                    'input[placeholder*="Referral"]',
                ]

                for selector in ref_selectors:
                    try:
                        element = self.page.locator(selector).first
                        if await element.is_visible(timeout=3000):
                            await element.click()
                            await element.fill(code)
                            logger.info(f"Entered referral code via: {selector}")

                            # Click Apply button
                            try:
                                apply_btn = self.page.locator('button:has-text("Apply")').first
                                if await apply_btn.is_visible(timeout=2000):
                                    await apply_btn.click()
                                    logger.info("Clicked Apply referral")
                            except Exception:
                                pass
                            return
                    except Exception:
                        continue

            # If no referral code or couldn't enter it, try to skip
            try:
                skip_btn = self.page.locator('button:has-text("don\'t have")').first
                if await skip_btn.is_visible(timeout=3000):
                    await skip_btn.click()
                    logger.info("Skipped referral prompt")
                    return
            except Exception:
                pass

            try:
                skip_btn = self.page.locator('button:has-text("Skip")').first
                if await skip_btn.is_visible(timeout=2000):
                    await skip_btn.click()
                    logger.info("Skipped referral prompt")
                    return
            except Exception:
                pass

            # Try "Start questing" button (appears after auto-apply)
            try:
                start_btn = self.page.locator('button:has-text("Start questing")').first
                if await start_btn.is_visible(timeout=2000):
                    await start_btn.click()
                    logger.info("Clicked 'Start questing'")
            except Exception:
                pass

        except Exception as e:
            logger.error(f"Error handling referral prompt: {e}")

    async def _check_logged_in(self) -> bool:
        """Check if user is logged in"""
        try:
            # Wait a moment for any redirects
            await asyncio.sleep(2)

            # Check for signs of being logged in
            # Look for user avatar, profile button, or quest content
            logged_in_selectors = [
                'button:has-text("Quests")',
                'text=Quests',
                'text=Points',
                'text=Leaderboard',
                '[data-testid="user-menu"]',
                'text=Referrals',
            ]

            for selector in logged_in_selectors:
                try:
                    element = self.page.locator(selector).first
                    if await element.is_visible(timeout=3000):
                        logger.info(f"Logged in confirmed via: {selector}")
                        return True
                except Exception:
                    continue

            # Check URL - if redirected away from homepage, likely logged in
            current_url = self.page.url
            if current_url != XENTO_URL and "/quest" in current_url or "/dashboard" in current_url:
                return True

            logger.warning("Could not confirm login status")
            return False

        except Exception as e:
            logger.error(f"Error checking login: {e}")
            return False

    async def _get_own_referral_code(self) -> str:
        """Try to get the user's own referral code"""
        try:
            # Navigate to referrals page if possible
            # Look for referral code display on current page
            code_pattern = r'[2-9A-HJ-NP-Z]{8}'

            # Check page content
            content = await self.page.content()
            matches = re.findall(code_pattern, content)

            if matches:
                # Return first match that looks like a referral code
                return matches[0]

            return None
        except Exception as e:
            logger.error(f"Error getting referral code: {e}")
            return None

    async def take_screenshot(self, path: str = "/tmp/xento_screenshot.png"):
        """Take a screenshot for debugging"""
        try:
            await self.page.screenshot(path=path, full_page=False)
            logger.info(f"Screenshot saved: {path}")
        except Exception as e:
            logger.error(f"Screenshot failed: {e}")


async def test_flow():
    """Test the browser automation"""
    signup = XentoSignup(headless=True)
    try:
        await signup.start()
        await signup.page.goto(XENTO_URL, wait_until="networkidle", timeout=30000)
        await asyncio.sleep(3)
        await signup.take_screenshot("/tmp/xento_homepage.png")
        print("Screenshot saved to /tmp/xento_homepage.png")
    finally:
        await signup.close()


if __name__ == "__main__":
    asyncio.run(test_flow())
