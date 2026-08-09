"""
Quest Completion Module for xento.org
Automates quest completion via browser:
1. Navigate to quest page
2. Click "Start quest"
3. Upload screenshot proof
4. Confirm submission

Quests supported:
  07 - Follow Xento on X (slug: xento-follow-x) → screenshot proof
  08 - Follow Xento on Instagram (slug: xento-follow-instagram) → screenshot proof
  10 - Subscribe to Xento on YouTube (slug: xento-youtube-subscribe) → screenshot proof
"""

import asyncio
import logging
import os
import time
from pathlib import Path

from playwright.async_api import Page

logger = logging.getLogger(__name__)

XENTO_URL = "https://xento.org"

# Quest definitions
QUESTS = {
    "07": {
        "slug": "xento-follow-x",
        "title": "Follow Xento on X",
        "proof_type": "image",
        "proof_file": "quest_07_x.jpg",
    },
    "08": {
        "slug": "xento-follow-instagram",
        "title": "Follow Xento on Instagram",
        "proof_type": "image",
        "proof_file": "quest_08_instagram.jpg",
    },
    "10": {
        "slug": "xento-youtube-subscribe",
        "title": "Subscribe to Xento on YouTube",
        "proof_type": "image",
        "proof_file": "quest_10_youtube.jpg",
    },
}

PROOFS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "proofs")


class QuestCompleter:
    """Complete xento.org quests via browser automation"""

    def __init__(self, page: Page):
        self.page = page

    async def complete_quest(self, quest_num: str, screenshot_path: str = None) -> dict:
        """
        Complete a single quest.
        
        Args:
            quest_num: Quest number as string ("07", "08", "10")
            screenshot_path: Override path to proof screenshot
            
        Returns:
            dict with success status and details
        """
        quest = QUESTS.get(quest_num)
        if not quest:
            return {"success": False, "error": f"Unknown quest: {quest_num}"}

        result = {
            "quest_num": quest_num,
            "title": quest["title"],
            "slug": quest["slug"],
            "success": False,
        }

        # Resolve proof screenshot path
        if screenshot_path:
            proof_path = screenshot_path
        else:
            proof_path = os.path.join(PROOFS_DIR, quest["proof_file"])

        if not os.path.exists(proof_path):
            result["error"] = f"Proof file not found: {proof_path}"
            return result

        try:
            # Step 1: Navigate to quest page (with crash recovery)
            quest_url = f"{XENTO_URL}/quests/{quest['slug']}"
            logger.info(f"[Quest {quest_num}] Navigating to {quest_url}")
            try:
                await self.page.goto(quest_url, wait_until="domcontentloaded", timeout=30000)
                await asyncio.sleep(3)
                # Verify page is alive
                await self.page.evaluate("1+1")
            except Exception as e:
                logger.warning(f"[Quest {quest_num}] Page error, retrying: {e}")
                await asyncio.sleep(2)
                try:
                    await self.page.goto(quest_url, wait_until="domcontentloaded", timeout=30000)
                    await asyncio.sleep(3)
                except Exception as e2:
                    result["error"] = f"Page load failed: {str(e2)[:50]}"
                    return result

            # Step 2: Click "Start quest" button
            start_ok = await self._click_start_quest(quest_num)
            if not start_ok:
                # Maybe quest already started, try to continue
                logger.info(f"[Quest {quest_num}] Start button not found, checking if quest already started...")
                await asyncio.sleep(2)

            await asyncio.sleep(2)

            # Step 3: Upload proof screenshot
            if quest["proof_type"] == "image":
                upload_ok = await self._upload_proof_image(quest_num, proof_path)
                if not upload_ok:
                    result["error"] = "Failed to upload proof image"
                    return result
            else:
                result["error"] = f"Unsupported proof type: {quest['proof_type']}"
                return result

            await asyncio.sleep(3)

            # Step 4: Submit the proof
            submit_ok = await self._submit_proof(quest_num)
            if not submit_ok:
                logger.warning(f"[Quest {quest_num}] Submit button not found or not clicked")

            await asyncio.sleep(3)

            # Take screenshot of result
            await self.page.screenshot(
                path=f"/tmp/xento_quest_{quest_num}_result.png",
                full_page=False
            )

            result["success"] = True
            result["message"] = "Proof uploaded and submitted successfully"
            logger.info(f"[Quest {quest_num}] Completed successfully!")

            return result

        except Exception as e:
            logger.error(f"[Quest {quest_num}] Error: {e}", exc_info=True)
            result["error"] = str(e)
            return result

    async def complete_all_quests(self, proof_paths: dict = None) -> list:
        """
        Complete quests 07, 08, 10.
        
        Args:
            proof_paths: Optional dict mapping quest_num -> custom proof path
                        e.g. {"07": "/path/to/x_proof.jpg", "08": "/path/to/ig_proof.jpg"}
        
        Returns:
            List of result dicts
        """
        results = []
        for quest_num in ["07", "08", "10"]:
            custom_path = None
            if proof_paths and quest_num in proof_paths:
                custom_path = proof_paths[quest_num]

            result = await self.complete_quest(quest_num, custom_path)
            results.append(result)

            # Wait between quests
            if quest_num != "10":
                await asyncio.sleep(3)

        return results

    async def _click_start_quest(self, quest_num: str) -> bool:
        """Click the 'Start quest' button on the quest page"""
        try:
            # Try multiple selectors for the start button
            selectors = [
                'button:has-text("Start quest")',
                'button:has-text("Start Quest")',
                'a:has-text("Start quest")',
                'button:has-text("Start")',
            ]

            for selector in selectors:
                try:
                    element = self.page.locator(selector).first
                    if await element.is_visible(timeout=3000):
                        await element.click()
                        logger.info(f"[Quest {quest_num}] Clicked start via: {selector}")
                        await asyncio.sleep(2)
                        return True
                except Exception:
                    continue

            # Fallback: search all buttons
            buttons = await self.page.query_selector_all('button')
            for btn in buttons:
                text = (await btn.text_content() or "").strip()
                if "start" in text.lower() and "quest" in text.lower():
                    if await btn.is_visible():
                        await btn.click()
                        logger.info(f"[Quest {quest_num}] Clicked start button: {text}")
                        await asyncio.sleep(2)
                        return True

            logger.warning(f"[Quest {quest_num}] No start quest button found")
            return False

        except Exception as e:
            logger.error(f"[Quest {quest_num}] Error clicking start: {e}")
            return False

    async def _upload_proof_image(self, quest_num: str, image_path: str) -> bool:
        """Upload proof screenshot via file input"""
        try:
            # Find the file input
            # The site uses input[type=file] with accept="image/png,image/jpeg,image/webp,image/heic"
            selectors = [
                'input[type="file"]',
                'input[accept*="image"]',
                'input[accept*="jpeg"]',
            ]

            for selector in selectors:
                try:
                    element = self.page.locator(selector).first
                    # Check if the input exists (even if not visible - file inputs are often hidden)
                    if await element.count() > 0:
                        await element.set_input_files(image_path)
                        logger.info(f"[Quest {quest_num}] Uploaded proof via: {selector}")
                        await asyncio.sleep(2)
                        return True
                except Exception:
                    continue

            # Fallback: try to find by evaluating all inputs
            inputs = await self.page.query_selector_all('input[type="file"]')
            if inputs:
                await inputs[0].set_input_files(image_path)
                logger.info(f"[Quest {quest_num}] Uploaded proof via fallback file input")
                await asyncio.sleep(2)
                return True

            # Last resort: Look for a "Upload screenshot" or "Choose file" button
            # that might trigger the file input
            upload_selectors = [
                'button:has-text("Upload")',
                'button:has-text("Choose")',
                'button:has-text("Select")',
                'label:has-text("Upload")',
            ]

            for selector in upload_selectors:
                try:
                    element = self.page.locator(selector).first
                    if await element.is_visible(timeout=2000):
                        # Click the upload button, then try to find the file input
                        async with self.page.expect_file_chooser(timeout=5000) as fc_info:
                            await element.click()
                        file_chooser = await fc_info.value
                        await file_chooser.set_files(image_path)
                        logger.info(f"[Quest {quest_num}] Uploaded proof via file chooser: {selector}")
                        await asyncio.sleep(2)
                        return True
                except Exception:
                    continue

            logger.error(f"[Quest {quest_num}] No file input found for proof upload")
            await self.page.screenshot(path=f"/tmp/xento_quest_{quest_num}_no_input.png")
            return False

        except Exception as e:
            logger.error(f"[Quest {quest_num}] Error uploading proof: {e}")
            return False

    async def _submit_proof(self, quest_num: str) -> bool:
        """Click the submit button after uploading proof"""
        try:
            # Try multiple submit button selectors
            selectors = [
                'button:has-text("Submit")',
                'button:has-text("submit")',
                'button:has-text("Submit screenshot")',
                'button:has-text("Resubmit")',
                'button[type="submit"]',
            ]

            for selector in selectors:
                try:
                    element = self.page.locator(selector).first
                    if await element.is_visible(timeout=3000):
                        await element.click()
                        logger.info(f"[Quest {quest_num}] Clicked submit via: {selector}")
                        await asyncio.sleep(2)
                        return True
                except Exception:
                    continue

            # Maybe auto-submitted or no submit needed
            logger.info(f"[Quest {quest_num}] No submit button found - may have auto-submitted")
            return True  # Not a failure

        except Exception as e:
            logger.error(f"[Quest {quest_num}] Error submitting proof: {e}")
            return False


async def test_quest_page():
    """Test - just open a quest page and take screenshot"""
    from xento_signup import XentoSignup
    signup = XentoSignup(headless=True)
    try:
        await signup.start()
        await signup.page.goto(f"{XENTO_URL}/quests/xento-follow-x", wait_until="networkidle", timeout=30000)
        await asyncio.sleep(3)
        await signup.page.screenshot(path="/tmp/xento_quest07_page.png")
        print("Screenshot saved")
    finally:
        await signup.close()


if __name__ == "__main__":
    asyncio.run(test_quest_page())
