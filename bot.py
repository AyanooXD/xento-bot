"""
Xento.org Telegram Bot
======================
Creates xento.org accounts using temporary email + OTP verification.
Supports referral link/code for account creation.
Mass creation with parallel processing and live progress UI.

Commands:
  /start     - Welcome message & instructions
  /create    - Create a single xento.org account (optionally with referral code)
  /mass      - Bulk account creation with parallel processing
  /quest     - Complete quests 07, 08, 10
  /status    - Check bot status
  /help      - Help message
  /cancel    - Cancel current operation

Setup:
  Set TELEGRAM_BOT_TOKEN env var or pass as argument.
"""

import os
import sys
import asyncio
import logging
import json
import time
import re
from datetime import datetime
from typing import Dict, Optional

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    filters,
    ContextTypes,
)

from temp_mail import TempMail
from xento_signup import XentoSignup
from xento_quests import QuestCompleter

# ─── Logging ─────────────────────────────────────────────────────────────────
logging.basicConfig(
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)
# Silence noisy loggers
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)
logging.getLogger("apscheduler").setLevel(logging.WARNING)

# ─── Config ──────────────────────────────────────────────────────────────────
BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
ACCOUNTS_FILE = os.path.join(DATA_DIR, "accounts.json")

# Max parallel browser instances for /mass (reduced from 7 for stability)
MAX_CONCURRENT_ACCOUNTS = int(os.environ.get("MAX_CONCURRENT_ACCOUNTS", "5"))
# Max accounts allowed in one /mass command
MAX_MASS_LIMIT = int(os.environ.get("MAX_MASS_LIMIT", "500"))

# Active creation tasks per chat
active_tasks = {}

# ─── Data Storage ────────────────────────────────────────────────────────────
os.makedirs(DATA_DIR, exist_ok=True)


def load_accounts():
    """Load created accounts from JSON file"""
    try:
        with open(ACCOUNTS_FILE, "r") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return []


def save_account(account_data: dict):
    """Save a created account to JSON file"""
    accounts = load_accounts()
    accounts.append(account_data)
    with open(ACCOUNTS_FILE, "w") as f:
        json.dump(accounts, f, indent=2, ensure_ascii=False)


def escape_md(text: str) -> str:
    """Escape text for MarkdownV2 parse mode"""
    special = r"_*[]()~`>#+-=|{}.!"
    return "".join(f"\\{c}" if c in special else c for c in text)


# ─── Bot Handlers ────────────────────────────────────────────────────────────

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /start command"""
    user = update.effective_user
    welcome = (
        f"🤖 *Xento Account Creator Bot*\n\n"
        f"Hey {user.first_name}! 👋\n\n"
        f"मैं xento.org पर account बनाने में आपकी मदद करता हूँ।\n\n"
        f"*Features:*\n"
        f"• 🔐 Temporary email se account create karta hoon\n"
        f"• ✉️ Email verification \\(OTP\\) auto\\-handle karta hoon\n"
        f"• 🔗 Referral link/code support hai\n"
        f"• 🚀 Mass account creation with parallel processing\n\n"
        f"*Commands:*\n"
        f"/create \\- Naya account banao \\(auto\\-quest\\)\n"
        f"/create CODE \\- Referral code ke saath\n"
        f"/mass \\- Bulk accounts banao \\(parallel\\)\n"
        f"/quest \\- Quests 07\\, 08\\, 10 complete karo\n"
        f"/status \\- Bot status check karo\n"
        f"/help \\- Help dekho\n\n"
        f"Account banane ke liye /create ya /mass type karo\\! 🚀"
    )

    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("🚀 Create Account", callback_data="create_no_ref"),
         InlineKeyboardButton("🔥 Mass Create", callback_data="mass_start")],
        [InlineKeyboardButton("📖 Help", callback_data="help")],
    ])

    await update.message.reply_text(
        welcome,
        parse_mode="MarkdownV2",
        reply_markup=keyboard,
    )


async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /help command"""
    help_text = (
        "🤖 *Xento Account Creator \\- Help*\n\n"
        "*Single Account:*\n"
        "1️⃣ /create \\- Simple account creation\n"
        "2️⃣ /create ABCD2345 \\- With referral code\n"
        "3️⃣ /create https://xento\\.org/?ref\\=ABCD2345 \\- With referral URL\n\n"
        "*Bulk Accounts \\(Mass\\):*\n"
        "4️⃣ /mass \\- Bulk account creation\n"
        "   → Bot quantity puchega \\(1\\-20\\)\n"
        "   → Phir referral code puchega\n"
        "   → Parallel processing se fast banata hai\n"
        "   → Live progress UI dikhaata hai\n\n"
        "*Quests:*\n"
        "5️⃣ /quest \\- Quests 07\\, 08\\, 10 complete karo\n"
        "6️⃣ /quest 07 \\- Sirf quest 07 karo\n\n"
        "*Process:*\n"
        "→ Bot temporary email generate karega\n"
        "→ xento\\.org par signup karega\n"
        "→ OTP verification auto\\-handle karega\n"
        "→ Referral code apply karega \\(if given\\)\n"
        "→ Quests 07\\, 08\\, 10 auto\\-complete karega\n\n"
        "⚠️ *Note:* /mass mein max 3 accounts parallel mein chalte hain\\."
    )
    await update.message.reply_text(help_text, parse_mode="MarkdownV2")


async def cmd_status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /status command"""
    accounts = load_accounts()
    mass_info = ""
    running_mass = sum(1 for t in active_tasks.values() if t.get("running") and t.get("type") == "mass")
    running_single = sum(1 for t in active_tasks.values() if t.get("running") and t.get("type") == "single")
    if running_mass:
        mass_info = f"\n🔥 Mass tasks running: {running_mass}"
    if running_single:
        mass_info += f"\n🚀 Single tasks running: {running_single}"

    status = (
        f"📊 *Bot Status*\n\n"
        f"🟢 Bot is running\n"
        f"📝 Total accounts created: {len(accounts)}\n"
        f"🕐 Time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n"
        f"⚡ Max parallel: {MAX_CONCURRENT_ACCOUNTS}{mass_info}"
    )
    await update.message.reply_text(status, parse_mode="Markdown")


async def cmd_create(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /create command - start account creation flow"""
    chat_id = update.effective_chat.id

    # Check if already creating
    if chat_id in active_tasks and active_tasks[chat_id].get("running"):
        await update.message.reply_text(
            "⏳ Pehle se ek task chal raha hai! Ruko ya /cancel karo."
        )
        return

    # Extract referral code from args
    args = context.args if context.args else []
    referral_code = None

    if args:
        referral_code = " ".join(args).strip()
        logger.info(f"[Chat {chat_id}] /create with referral: {referral_code}")

    if referral_code:
        msg = (
            f"🔗 *Referral Code Mila\\!*\n\n"
            f"Referral: `{referral_code}`\n\n"
            f"Account creation shuru kar raha hoon\\.\\.\\. 🚀\n"
            f"Please wait, ye 1\\-2 minute lag sakta hai\\."
        )
    else:
        msg = (
            "🚀 *Account Creation Shuru\\!*\n\n"
            "Koi referral code nahi diya\\. Seedha account bana raha hoon\\.\n\n"
            "Please wait, ye 1\\-2 minute lag sakta hai\\."
        )

    await update.message.reply_text(msg, parse_mode="MarkdownV2")

    # Run account creation in a separate task so we don't block the bot polling
    active_tasks[chat_id] = {"running": True, "type": "single"}

    async def _run_safe():
        try:
            await _create_account(chat_id, context, referral_code)
        except Exception as e:
            logger.error(f"[Chat {chat_id}] Unhandled error in _create_account: {e}", exc_info=True)
            try:
                await context.bot.send_message(
                    chat_id,
                    f"❌ Error: {str(e)[:200]}\nTry again: /create"
                )
            except Exception:
                pass
        finally:
            active_tasks[chat_id] = {"running": False, "type": "single"}

    asyncio.ensure_future(_run_safe())


# ═══════════════════════════════════════════════════════════════════════════════
# /MASS COMMAND - Bulk Account Creation with Parallel Processing
# NO ConversationHandler - uses simple user_data state tracking
# ═══════════════════════════════════════════════════════════════════════════════

async def cmd_mass(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /mass command - start bulk account creation flow"""
    chat_id = update.effective_chat.id

    # Check if already running
    if chat_id in active_tasks and active_tasks[chat_id].get("running"):
        await update.message.reply_text(
            "⏳ Pehle se ek task chal raha hai! Ruko ya /cancel karo."
        )
        return

    # Set state: waiting for quantity
    context.user_data["mass_state"] = "waiting_quantity"

    await update.message.reply_text(
        "🔥 *Mass Account Creation*\n\n"
        f"Kitne accounts banana hai? \\(1\\-{MAX_MASS_LIMIT}\\)\n\n"
        "Sirf number bhejo, e\\.g\\. `5` ya `10`\n\n"
        f"⚠️ Max {MAX_CONCURRENT_ACCOUNTS} accounts parallel mein chalenge\\.\n"
        "Cancel: /cancel",
        parse_mode="MarkdownV2",
    )


async def handle_mass_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Handle text messages during /mass conversation flow.
    This is a simple MessageHandler (NOT ConversationHandler) that checks
    user_data state to know what step we're on.
    """
    chat_id = update.effective_chat.id
    text = update.message.text.strip()
    mass_state = context.user_data.get("mass_state")

    if not mass_state:
        # Not in mass creation flow - ignore
        return

    if mass_state == "waiting_quantity":
        # Parse quantity
        try:
            quantity = int(text)
        except ValueError:
            await update.message.reply_text(
                "❌ Sirf number bhejo\\! e\\.g\\. `5` ya `10`\n\n"
                "Dobara try karo ya /cancel karo\\.",
                parse_mode="MarkdownV2",
            )
            return

        if quantity < 1 or quantity > MAX_MASS_LIMIT:
            await update.message.reply_text(
                f"❌ Quantity 1 se {MAX_MASS_LIMIT} ke beech mein honi chahiye\\!\n\n"
                f"Dobara try karo ya /cancel karo\\.",
                parse_mode="MarkdownV2",
            )
            return

        # Store quantity and ask for referral
        context.user_data["mass_quantity"] = quantity
        context.user_data["mass_state"] = "waiting_referral"

        await update.message.reply_text(
            f"✅ *Quantity: {quantity} accounts*\n\n"
            f"Ab referral code ya link bhejo\\.\n"
            f"Agar koi referral nahi hai toh `none` likho\\.\n\n"
            f"Examples:\n"
            f"• `ABCD2345` \\(code\\)\n"
            f"• `https://xento\\.org/?ref\\=ABCD2345` \\(link\\)\n"
            f"• `none` \\(no referral\\)\n\n"
            f"Cancel: /cancel",
            parse_mode="MarkdownV2",
        )
        return

    if mass_state == "waiting_referral":
        # Parse referral code
        if text.lower() in ["none", "no", "n", "skip", "0"]:
            referral_code = None
        else:
            referral_code = text

        quantity = context.user_data.get("mass_quantity", 1)

        # Clear state - we're done with conversation
        context.user_data["mass_state"] = None
        context.user_data["mass_quantity"] = None

        # Start mass creation
        active_tasks[chat_id] = {"running": True, "type": "mass"}

        # Send initial progress message
        progress_msg = await update.message.reply_text(
            _build_mass_progress(
                quantity=quantity,
                referral_code=referral_code,
                accounts_status={},
                started=False,
            ),
            parse_mode="MarkdownV2",
        )

        # Launch mass creation as non-blocking task
        async def _run_mass_safe():
            try:
                await _run_mass_creation(
                    chat_id=chat_id,
                    context=context,
                    quantity=quantity,
                    referral_code=referral_code,
                    progress_msg=progress_msg,
                )
            except Exception as e:
                logger.error(f"[Chat {chat_id}] Mass creation error: {e}", exc_info=True)
                try:
                    await context.bot.send_message(
                        chat_id,
                        f"❌ Mass creation error: {str(e)[:200]}\nTry again: /mass"
                    )
                except Exception:
                    pass
            finally:
                active_tasks[chat_id] = {"running": False, "type": "mass"}

        asyncio.ensure_future(_run_mass_safe())
        return


def _build_mass_progress(
    quantity: int,
    referral_code: Optional[str],
    accounts_status: Dict[int, dict],
    started: bool = True,
    finished: bool = False,
) -> str:
    """
    Build the live progress message for mass account creation.
    
    accounts_status: {1: {"status": "creating", "email": "xxx"}, 2: {"status": "done", "email": "yyy"}, ...}
    status values: "pending", "creating", "done", "failed"
    """
    # Progress bar
    done_count = sum(1 for v in accounts_status.values() if v.get("status") == "done")
    fail_count = sum(1 for v in accounts_status.values() if v.get("status") == "failed")
    creating_count = sum(1 for v in accounts_status.values() if v.get("status") == "creating")
    pending_count = quantity - len(accounts_status)

    # Emoji progress bar
    bar_len = min(quantity, 20)  # Cap bar at 20 chars
    bar_done = int(bar_len * done_count / quantity) if quantity else 0
    bar_fail = int(bar_len * fail_count / quantity) if quantity else 0
    bar_creating = int(bar_len * creating_count / quantity) if quantity else 0
    bar_pending = bar_len - bar_done - bar_fail - bar_creating

    progress_bar = (
        "🟩" * bar_done +
        "🟥" * bar_fail +
        "🟨" * bar_creating +
        "⬜" * bar_pending
    )

    # Status header
    if finished:
        header = "🏁 *Mass Creation Complete\\!*\n\n"
    elif started:
        header = "🔥 *Mass Creation In Progress\\.\\.\\.*\n\n"
    else:
        header = "⏳ *Mass Creation Starting\\.\\.\\.*\n\n"

    # Summary line
    summary = (
        f"📊 Done: {done_count}/{quantity} \\| "
        f"❌ Failed: {fail_count} \\| "
        f"⏳ Active: {creating_count} \\| "
        f"⬜ Pending: {pending_count}\n"
    )

    # Referral info
    if referral_code:
        ref_info = f"🔗 Referral: `{escape_md(referral_code)}`\n\n"
    else:
        ref_info = "🔗 Referral: None\n\n"

    # Individual account status
    lines = []
    for i in range(1, quantity + 1):
        info = accounts_status.get(i, {})
        status = info.get("status", "pending")
        email = info.get("email", "")
        quest_info = info.get("quest_summary", "")

        if status == "pending":
            lines.append(f"  ⬜ \\#{i} Pending")
        elif status == "creating":
            step = info.get("step", "")
            if email and step:
                lines.append(f"  🟨 \\#{i} `{escape_md(email[:25])}` \\- {escape_md(step)}")
            elif email:
                lines.append(f"  🟨 \\#{i} `{escape_md(email[:25])}` \\- Creating\\.\\.\\.")
            else:
                lines.append(f"  🟨 \\#{i} Creating\\.\\.\\.")
        elif status == "done":
            quest_tag = quest_info if quest_info else "✅ Quests done"
            lines.append(f"  🟩 \\#{i} `{escape_md(email[:25])}` \\- {quest_tag}")
        elif status == "failed":
            error = info.get("error", "Unknown error")
            lines.append(f"  🟥 \\#{i} ❌ {escape_md(error[:40])}")

    # Limit lines to 15 for Telegram message size
    if len(lines) > 15:
        more_count = len(lines) - 14
        display_lines = lines[:7] + [f"  \\.\\.\\. \\({more_count} more\\) \\.\\.\\."] + lines[-7:]
    else:
        display_lines = lines

    accounts_text = "\n".join(display_lines)

    # Estimated time
    if not finished and started:
        remaining = pending_count + creating_count
        batches = (remaining + MAX_CONCURRENT_ACCOUNTS - 1) // MAX_CONCURRENT_ACCOUNTS
        est_sec = batches * 90
        if est_sec > 60:
            est = f"~{est_sec // 60}m {est_sec % 60}s"
        else:
            est = f"~{est_sec}s"
        time_info = f"\n⏱ Est\\. remaining: {escape_md(est)}"
    else:
        time_info = ""

    # Final result
    if finished:
        success_pct = int(100 * done_count / quantity) if quantity else 0
        finish_info = f"\n🎯 Success rate: {success_pct}% \\({done_count}/{quantity}\\)"
    else:
        finish_info = ""

    msg = (
        header +
        progress_bar + "\n\n" +
        summary +
        ref_info +
        accounts_text +
        time_info +
        finish_info
    )

    return msg


async def _run_mass_creation(
    chat_id: int,
    context: ContextTypes.DEFAULT_TYPE,
    quantity: int,
    referral_code: Optional[str],
    progress_msg,
):
    """
    Run mass account creation with parallel processing.
    Uses asyncio.Semaphore for concurrency control.
    """
    bot = context.bot
    logger.info(f"[Chat {chat_id}] Mass creation: {quantity} accounts, referral={referral_code}")

    # Shared state for progress tracking
    accounts_status: Dict[int, dict] = {}
    progress_lock = asyncio.Lock()
    last_update_time = [0.0]  # mutable for closure

    async def update_progress(force=False):
        """Update the progress message (throttled to avoid rate limits)"""
        now = time.time()
        # Throttle: update every 3 seconds unless forced
        if not force and (now - last_update_time[0]) < 3:
            return
        last_update_time[0] = now
        try:
            text = _build_mass_progress(
                quantity=quantity,
                referral_code=referral_code,
                accounts_status=accounts_status,
                started=True,
            )
            await progress_msg.edit_text(text, parse_mode="MarkdownV2")
        except Exception as e:
            # Rate limit or message not modified - ignore
            if "Too many requests" not in str(e) and "Message is not modified" not in str(e):
                logger.debug(f"Progress update error (ignored): {e}")

    async def create_single_account(account_num: int, semaphore: asyncio.Semaphore):
        """Create a single account within the semaphore-controlled pool"""
        async with semaphore:
            # Stagger: random delay 0-3s to avoid all browsers launching at once
            stagger = random.uniform(0, 3)
            await asyncio.sleep(stagger)
            
            # Mark as creating
            async with progress_lock:
                accounts_status[account_num] = {"status": "creating", "step": "Generating email..."}
            await update_progress()

            signup = None
            temp_mail = None
            email = None

            try:
                # ── Step 1: Create temp email (with retry across providers) ──
                email = None
                for email_attempt in range(3):
                    temp_mail = TempMail()
                    email = temp_mail.create()
                    if email:
                        provider_name = getattr(temp_mail._provider, 'name', 'unknown') if temp_mail._provider else 'unknown'
                        logger.info(f"[Mass #{account_num}] Email created via {provider_name}: {email}")
                        break
                    logger.warning(f"[Mass #{account_num}] Email attempt {email_attempt+1}/3 failed, retrying...")
                    await asyncio.sleep(2)

                if not email:
                    async with progress_lock:
                        accounts_status[account_num] = {"status": "failed", "error": "All email providers failed"}
                    await update_progress(force=True)
                    return

                provider_name = getattr(temp_mail._provider, 'name', '?') if temp_mail._provider else '?'
                async with progress_lock:
                    accounts_status[account_num] = {"status": "creating", "email": email, "step": f"Email ({provider_name})"}
                await update_progress()

                # ── Step 2: Launch browser (with retry) ──
                async with progress_lock:
                    accounts_status[account_num]["step"] = "Opening browser..."
                await update_progress()

                signup = XentoSignup(headless=True)
                browser_launched = False
                for browser_attempt in range(2):
                    try:
                        await signup.start()
                        browser_launched = True
                        break
                    except Exception as be:
                        logger.warning(f"[Mass #{account_num}] Browser launch attempt {browser_attempt+1}/2 failed: {be}")
                        if browser_attempt < 1:
                            await asyncio.sleep(3)
                            signup = XentoSignup(headless=True)

                if not browser_launched:
                    async with progress_lock:
                        accounts_status[account_num] = {"status": "failed", "email": email, "error": "Browser launch failed"}
                    await update_progress(force=True)
                    return

                # Navigate with referral
                url = "https://xento.org"
                if referral_code:
                    match = re.search(r'[?&]ref=([2-9A-HJ-NP-Za-hj-np-z]{8})', referral_code)
                    if match:
                        code = match.group(1).upper()
                    elif re.match(r'^[2-9A-HJ-NP-Za-hj-np-z]{8}$', referral_code.strip()):
                        code = referral_code.strip().upper()
                    else:
                        code = referral_code.strip().upper()
                    url = f"https://xento.org/?ref={code}"

                await signup.page.goto(url, wait_until="networkidle", timeout=30000)
                await asyncio.sleep(3)

                # ── Step 3: Click sign-in and enter email (with retry) ──
                async with progress_lock:
                    accounts_status[account_num]["step"] = "Signing in..."
                await update_progress()

                signin_ok = await signup._click_signin()
                if not signin_ok:
                    async with progress_lock:
                        accounts_status[account_num] = {"status": "failed", "email": email, "error": "Sign-in button not found"}
                    await update_progress(force=True)
                    return

                await asyncio.sleep(2)

                # Enter email with retry (sometimes modal isn't ready)
                email_ok = False
                for email_entry_attempt in range(2):
                    email_ok = await signup._enter_email(email)
                    if email_ok:
                        break
                    logger.warning(f"[Mass #{account_num}] Email entry attempt {email_entry_attempt+1}/2 failed")
                    await asyncio.sleep(3)

                if not email_ok:
                    async with progress_lock:
                        accounts_status[account_num] = {"status": "failed", "email": email, "error": "Email entry failed"}
                    await update_progress(force=True)
                    return

                await asyncio.sleep(2)

                # ── Step 4: Wait for OTP (increased timeout 150s) ──
                async with progress_lock:
                    accounts_status[account_num]["step"] = "Waiting for OTP..."
                await update_progress()

                loop = asyncio.get_event_loop()
                otp_code = await loop.run_in_executor(
                    None, temp_mail.wait_for_otp, 150, 4
                )

                if not otp_code:
                    async with progress_lock:
                        accounts_status[account_num] = {"status": "failed", "email": email, "error": "OTP not received"}
                    await update_progress(force=True)
                    return

                # Enter OTP
                async with progress_lock:
                    accounts_status[account_num]["step"] = "Entering OTP..."
                await update_progress()

                otp_ok = await signup._enter_otp(otp_code)
                if not otp_ok:
                    async with progress_lock:
                        accounts_status[account_num] = {"status": "failed", "email": email, "error": "OTP entry failed"}
                    await update_progress(force=True)
                    return

                await asyncio.sleep(3)

                # ── Step 5: Handle referral prompt ──
                async with progress_lock:
                    accounts_status[account_num]["step"] = "Handling referral..."
                await update_progress()

                await signup._handle_referral_prompt(referral_code)
                await asyncio.sleep(2)

                # Check login
                logged_in = await signup._check_logged_in()

                # Save account
                account_data = {
                    "email": email,
                    "referral_used": referral_code,
                    "created_at": datetime.now().isoformat(),
                    "success": logged_in,
                }

                if not logged_in:
                    account_data["note"] = "Login unconfirmed"
                    save_account(account_data)
                    async with progress_lock:
                        accounts_status[account_num] = {"status": "failed", "email": email, "error": "Login unconfirmed"}
                    await update_progress(force=True)
                    return

                save_account(account_data)

                # ── Step 6: Auto-complete quests ──
                async with progress_lock:
                    accounts_status[account_num]["step"] = "Completing quests..."
                await update_progress()

                quest_summary_str = ""
                try:
                    quest_completer = QuestCompleter(signup.page)
                    quest_results = await quest_completer.complete_all_quests()

                    # Format mini quest summary
                    q_ok = sum(1 for qr in quest_results if qr.get("success"))
                    q_total = len(quest_results)
                    quest_summary_str = f"✅ {q_ok}/{q_total} quests"

                    account_data["quests"] = quest_results
                    # Re-save with quest data
                    accounts = load_accounts()
                    if accounts:
                        accounts[-1] = account_data
                        with open(ACCOUNTS_FILE, "w") as f:
                            json.dump(accounts, f, indent=2, ensure_ascii=False)

                except Exception as qe:
                    logger.error(f"[Mass #{account_num}] Quest error: {qe}")
                    quest_summary_str = "⚠️ Quests failed"

                # Mark as done
                async with progress_lock:
                    accounts_status[account_num] = {
                        "status": "done",
                        "email": email,
                        "quest_summary": quest_summary_str,
                    }
                await update_progress(force=True)

            except asyncio.CancelledError:
                async with progress_lock:
                    accounts_status[account_num] = {"status": "failed", "email": email or "", "error": "Cancelled"}
                await update_progress(force=True)
                raise
            except Exception as e:
                logger.error(f"[Mass #{account_num}] Error: {e}", exc_info=True)
                async with progress_lock:
                    accounts_status[account_num] = {"status": "failed", "email": email or "", "error": str(e)[:60]}
                await update_progress(force=True)
            finally:
                try:
                    if signup:
                        await signup.close()
                except Exception:
                    pass
                try:
                    if temp_mail:
                        temp_mail.cleanup()
                except Exception:
                    pass

    # ── Create semaphore and launch all tasks ──
    semaphore = asyncio.Semaphore(MAX_CONCURRENT_ACCOUNTS)
    tasks = []

    for i in range(1, quantity + 1):
        # Mark as pending
        accounts_status[i] = {"status": "pending"}
        task = asyncio.ensure_future(create_single_account(i, semaphore))
        tasks.append(task)

    # Initial progress update
    await update_progress(force=True)

    # Wait for all tasks to complete
    try:
        await asyncio.gather(*tasks, return_exceptions=True)
    except Exception as e:
        logger.error(f"[Chat {chat_id}] Mass gather error: {e}")

    # Final progress update
    final_text = _build_mass_progress(
        quantity=quantity,
        referral_code=referral_code,
        accounts_status=accounts_status,
        started=True,
        finished=True,
    )
    try:
        await progress_msg.edit_text(final_text, parse_mode="MarkdownV2")
    except Exception:
        pass

    # Send final summary
    done_count = sum(1 for v in accounts_status.values() if v.get("status") == "done")
    fail_count = sum(1 for v in accounts_status.values() if v.get("status") == "failed")

    summary_lines = [f"🎉 *Mass Creation Summary*\n"]
    summary_lines.append(f"✅ Success: {done_count}/{quantity}")
    summary_lines.append(f"❌ Failed: {fail_count}/{quantity}")
    if referral_code:
        summary_lines.append(f"🔗 Referral: `{escape_md(referral_code)}`")
    summary_lines.append(f"\n*Created Accounts:*")

    for i, (num, info) in enumerate(accounts_status.items()):
        if info.get("status") == "done" and info.get("email"):
            quest_tag = info.get("quest_summary", "")
            summary_lines.append(f"  {num}\\. `{escape_md(info['email'])}` {quest_tag}")

    summary_text = "\n".join(summary_lines)
    try:
        await bot.send_message(chat_id, summary_text, parse_mode="MarkdownV2")
    except Exception:
        try:
            await bot.send_message(chat_id, summary_text.replace("*", "").replace("`", ""))
        except Exception:
            pass

    logger.info(f"[Chat {chat_id}] Mass creation complete: {done_count}/{quantity} success")


# ═══════════════════════════════════════════════════════════════════════════════
# CORE ACCOUNT CREATION (used by /create)
# ═══════════════════════════════════════════════════════════════════════════════

async def _create_account(chat_id: int, context: ContextTypes.DEFAULT_TYPE, referral_code: str = None):
    """
    Core account creation logic (for /create command):
    1. Generate temp email
    2. Open browser, navigate to xento.org
    3. Click sign-in, enter email
    4. Wait for OTP email
    5. Enter OTP
    6. Handle referral prompt
    7. Auto-complete quests
    8. Report results
    """
    bot = context.bot
    logger.info(f"[Chat {chat_id}] Starting account creation, referral={referral_code}")

    try:
        status_msg = await bot.send_message(
            chat_id,
            "⏳ Step 1/5: Temporary email generate kar raha hoon..."
        )
    except Exception as e:
        logger.error(f"Failed to send initial message: {e}")
        active_tasks[chat_id] = {"running": False}
        return

    signup = XentoSignup(headless=True)
    temp_mail = None

    try:
        # ── Step 1: Create temp email (with retry across providers) ──
        logger.info(f"[Chat {chat_id}] Step 1: Creating temp email...")
        email = None
        for email_attempt in range(3):
            temp_mail = TempMail()
            email = temp_mail.create()
            if email:
                provider_name = getattr(temp_mail._provider, 'name', 'unknown') if temp_mail._provider else 'unknown'
                logger.info(f"[Chat {chat_id}] Step 1: email={email} via {provider_name}")
                break
            logger.warning(f"[Chat {chat_id}] Email attempt {email_attempt+1}/3 failed, retrying...")
            await asyncio.sleep(1)

        logger.info(f"[Chat {chat_id}] Step 1 result: email={email}")
        if not email:
            await status_msg.edit_text(
                "❌ *Failed\\!*\n\n"
                "Saari temp email services fail ho gayi\\. Kuch time baad try karo\\.\n"
                "Command: /create",
                parse_mode="MarkdownV2",
            )
            return

        await status_msg.edit_text(
            f"✅ Step 1/5: Email ready\\!\n"
            f"📧 `{email}`\n\n"
            f"⏳ Step 2/5: Browser open kar raha hoon\\.\\.\\.",
            parse_mode="MarkdownV2",
        )

        # ── Step 2: Launch browser and navigate ──
        logger.info(f"[Chat {chat_id}] Step 2: Launching browser...")
        await signup.start()
        logger.info(f"[Chat {chat_id}] Step 2: Browser launched")

        # Navigate with referral link
        url = "https://xento.org"
        if referral_code:
            match = re.search(r'[?&]ref=([2-9A-HJ-NP-Za-hj-np-z]{8})', referral_code)
            if match:
                code = match.group(1).upper()
            elif re.match(r'^[2-9A-HJ-NP-Za-hj-np-z]{8}$', referral_code.strip()):
                code = referral_code.strip().upper()
            else:
                code = referral_code.strip().upper()
            url = f"https://xento.org/?ref={code}"

        await signup.page.goto(url, wait_until="networkidle", timeout=30000)
        await asyncio.sleep(3)

        # ── Step 3: Click sign-in and enter email ──
        await status_msg.edit_text(
            f"✅ Step 2/5: Browser ready\\!\n\n"
            f"⏳ Step 3/5: xento\\.org par signup kar raha hoon\\.\\.\\.\n"
            f"📧 Email: `{email}`",
            parse_mode="MarkdownV2",
        )

        # Click sign-in button
        logger.info(f"[Chat {chat_id}] Step 3: Clicking sign-in...")
        signin_ok = await signup._click_signin()
        logger.info(f"[Chat {chat_id}] Step 3: Sign-in result={signin_ok}")
        if not signin_ok:
            await signup.take_screenshot(f"/tmp/xento_step3_fail_{int(time.time())}.png")
            await status_msg.edit_text(
                "❌ *Failed\\!*\n\n"
                "Sign\\-in button nahi mila\\. Website layout change hua ho sakta hai\\.\n"
                "Try again later: /create",
                parse_mode="MarkdownV2",
            )
            return

        await asyncio.sleep(2)

        # Enter email
        email_ok = await signup._enter_email(email)
        if not email_ok:
            await signup.take_screenshot(f"/tmp/xento_email_fail_{int(time.time())}.png")
            await status_msg.edit_text(
                "❌ *Failed\\!*\n\n"
                "Email enter nahi ho payi\\. Modal open nahi hua ya input field nahi mila\\.\n"
                "Try again: /create",
                parse_mode="MarkdownV2",
            )
            return

        await asyncio.sleep(2)

        # ── Step 4: Wait for OTP and enter it ──
        await status_msg.edit_text(
            f"✅ Step 3/5: Email submitted\\!\n\n"
            f"⏳ Step 4/5: OTP code ka wait kar raha hoon\\.\\.\\.\n"
            f"📧 Checking inbox at `{email}`\n"
            f"This may take 30\\-60 seconds\\.\\.\\.",
            parse_mode="MarkdownV2",
        )

        # Wait for OTP in a thread to not block
        logger.info(f"[Chat {chat_id}] Step 4: Waiting for OTP...")
        loop = asyncio.get_event_loop()
        otp_code = await loop.run_in_executor(
            None, temp_mail.wait_for_otp, 150, 4
        )
        logger.info(f"[Chat {chat_id}] Step 4: OTP result={otp_code}")

        if not otp_code:
            await status_msg.edit_text(
                f"⚠️ *OTP Auto\\-Detect Failed\\!*\n\n"
                f"Email: `{email}`\n\n"
                f"OTP email nahi mila ya extract nahi ho paya\\.\n"
                f"This can happen if mail service is slow\\.\n\n"
                f"Dobara try karo: /create",
                parse_mode="MarkdownV2",
            )
            return

        # Enter OTP automatically
        await status_msg.edit_text(
            f"✅ OTP mila: `{otp_code}`\n\n"
            f"⏳ Step 5/5: OTP enter kar raha hoon\\.\\.\\.",
            parse_mode="MarkdownV2",
        )

        otp_ok = await signup._enter_otp(otp_code)
        if not otp_ok:
            await status_msg.edit_text(
                "❌ *Failed\\!*\n\n"
                "OTP enter nahi ho paya\\. Input fields nahi mile\\.\n"
                "Try again: /create",
                parse_mode="MarkdownV2",
            )
            return

        await asyncio.sleep(3)

        # ── Step 5: Handle referral prompt ──
        logger.info(f"[Chat {chat_id}] Step 5: Handling referral prompt...")
        await signup._handle_referral_prompt(referral_code)
        await asyncio.sleep(2)

        # Check login status
        logged_in = await signup._check_logged_in()

        # Save account data
        account_data = {
            "email": email,
            "referral_used": referral_code,
            "created_at": datetime.now().isoformat(),
            "success": logged_in,
        }

        if logged_in:
            save_account(account_data)
            await status_msg.edit_text(
                f"🎉 *Account Created Successfully\\!*\n\n"
                f"📧 Email: `{email}`\n"
                f"🔗 Referral Used: `{referral_code or 'None'}`\n"
                f"🕐 Time: {datetime.now().strftime('%H:%M:%S')}\n\n"
                f"⏳ Ab quests complete kar raha hoon\\.\\.\\.\n"
                f"Quest 07 \\(X\\), 08 \\(Instagram\\), 10 \\(YouTube\\)",
                parse_mode="MarkdownV2",
            )

            # ── Auto-complete quests after account creation ──
            try:
                quest_completer = QuestCompleter(signup.page)
                quest_results = await quest_completer.complete_all_quests()

                # Format quest results
                quest_summary = ""
                for qr in quest_results:
                    status = "✅" if qr.get("success") else "❌"
                    quest_summary += f"{status} Quest {qr['quest_num']}: {qr['title']}\n"
                    if not qr.get("success") and qr.get("error"):
                        quest_summary += f"   Error: {qr['error'][:50]}\n"

                account_data["quests"] = quest_results
                # Re-save with quest data
                accounts = load_accounts()
                if accounts:
                    accounts[-1] = account_data
                    with open(ACCOUNTS_FILE, "w") as f:
                        json.dump(accounts, f, indent=2, ensure_ascii=False)

                await status_msg.edit_text(
                    f"🎉 *Account \\+ Quests Done\\!*\n\n"
                    f"📧 Email: `{email}`\n"
                    f"🔗 Referral: `{referral_code or 'None'}`\n\n"
                    f"*Quests:*\n{quest_summary}\n"
                    f"🎮 All done\\! Points will be credited after admin review\\.",
                    parse_mode="MarkdownV2",
                )
            except Exception as qe:
                logger.error(f"[Chat {chat_id}] Quest completion error: {qe}", exc_info=True)
                await status_msg.edit_text(
                    f"🎉 *Account Created\\!*\n\n"
                    f"📧 Email: `{email}`\n"
                    f"🔗 Referral: `{referral_code or 'None'}`\n\n"
                    f"⚠️ Quests nahi ho paye\\.\n"
                    f"Manually complete karo ya /quest bhejo\\.",
                    parse_mode="MarkdownV2",
                )
        else:
            account_data["note"] = "Login status unconfirmed - account may still be valid"
            save_account(account_data)
            await status_msg.edit_text(
                f"⚠️ *Account Creation \\- Uncertain*\n\n"
                f"📧 Email: `{email}`\n"
                f"🔗 Referral: `{referral_code or 'None'}`\n\n"
                f"OTP verify ho gaya lekin login confirm nahi hua\\.\n"
                f"Account ban gaya ho sakta hai \\- xento\\.org par check karo\\.\n"
                f"Login: `{email}` \\(Privy se OTP login\\)",
                parse_mode="MarkdownV2",
            )

    except asyncio.CancelledError:
        logger.info(f"[Chat {chat_id}] Account creation cancelled by user")
        try:
            await status_msg.edit_text("❌ Cancelled\\!", parse_mode="MarkdownV2")
        except Exception:
            pass
    except Exception as e:
        logger.error(f"[Chat {chat_id}] Account creation error: {e}", exc_info=True)
        try:
            await status_msg.edit_text(
                f"❌ *Error\\!*\n\n"
                f"Account creation mein error aaya:\n`{str(e)[:200]}`\n\n"
                f"Dobara try karo: /create",
                parse_mode="MarkdownV2",
            )
        except Exception:
            pass

    finally:
        active_tasks[chat_id] = {"running": False, "type": "single"}
        # Cleanup
        try:
            await signup.close()
        except Exception:
            pass
        try:
            if temp_mail:
                temp_mail.cleanup()
        except Exception:
            pass


# ═══════════════════════════════════════════════════════════════════════════════
# OTHER COMMANDS
# ═══════════════════════════════════════════════════════════════════════════════

async def cmd_quest(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    /quest command - Complete quests 07, 08, 10 on an existing logged-in account.
    Usage: /quest  (completes all 3 quests)
           /quest 07  (complete only quest 07)
    """
    chat_id = update.effective_chat.id
    args = context.args if context.args else []

    await update.message.reply_text(
        "🎯 *Quest Completion*\n\n"
        "Browser open karke quests complete kar raha hoon\\.\\.\\.\n"
        "Quest 07 \\(X\\), 08 \\(Instagram\\), 10 \\(YouTube\\)\n\n"
        "⏳ Please wait\\, 1\\-2 min lag sakta hai\\.",
        parse_mode="MarkdownV2",
    )

    signup = XentoSignup(headless=True)

    try:
        # Launch browser and navigate to xento.org
        await signup.start()
        await signup.page.goto("https://xento.org", wait_until="networkidle", timeout=30000)
        await asyncio.sleep(2)

        status_msg = await context.bot.send_message(
            chat_id, "⏳ Quests shuru kar raha hoon..."
        )

        quest_completer = QuestCompleter(signup.page)

        if args and args[0] in ["07", "08", "10"]:
            # Complete specific quest
            quest_num = args[0]
            result = await quest_completer.complete_quest(quest_num)
            results = [result]
        else:
            # Complete all quests
            results = await quest_completer.complete_all_quests()

        # Format results
        quest_summary = ""
        for qr in results:
            status = "✅" if qr.get("success") else "❌"
            quest_summary += f"{status} Quest {qr['quest_num']}: {qr['title']}\n"
            if not qr.get("success") and qr.get("error"):
                quest_summary += f"   ↳ {qr['error'][:60]}\n"

        await status_msg.edit_text(
            f"🎯 *Quest Results:*\n\n"
            f"{quest_summary}\n"
            f"🎮 Points admin review ke baad credit honge\\.",
            parse_mode="MarkdownV2",
        )

    except Exception as e:
        logger.error(f"[Chat {chat_id}] Quest command error: {e}", exc_info=True)
        await update.message.reply_text(
            f"❌ Quest error: {str(e)[:200]}\nDobara try karo: /quest"
        )
    finally:
        try:
            await signup.close()
        except Exception:
            pass


async def cmd_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Cancel the current operation"""
    chat_id = update.effective_chat.id

    # Clear mass state
    context.user_data["mass_state"] = None
    context.user_data["mass_quantity"] = None

    if chat_id in active_tasks:
        task_info = active_tasks[chat_id]
        if task_info.get("task") and not task_info["task"].done():
            task_info["task"].cancel()
        active_tasks[chat_id] = {"running": False}
        await update.message.reply_text("❌ Cancelled\\! /create ya /mass se dobbara shuru karo\\.", parse_mode="MarkdownV2")
    else:
        await update.message.reply_text("Koi active task nahi hai. /create ya /mass se shuru karo.")


async def callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle inline keyboard callbacks"""
    query = update.callback_query
    await query.answer()

    chat_id = query.message.chat.id

    if query.data == "create_no_ref":
        context.user_data["referral_code"] = None
        await query.edit_message_text(
            "🚀 *Account Creation Shuru\\!*\n\n"
            "Seedha account bana raha hoon\\.\\.\\.\n"
            "Please wait ⏳",
            parse_mode="MarkdownV2",
        )

        active_tasks[chat_id] = {"running": True, "type": "single"}

        async def _run_safe():
            try:
                await _create_account(chat_id, context, None)
            except Exception as e:
                logger.error(f"[Chat {chat_id}] Callback error: {e}", exc_info=True)
            finally:
                active_tasks[chat_id] = {"running": False, "type": "single"}

        asyncio.ensure_future(_run_safe())

    elif query.data == "mass_start":
        # Set state for mass creation
        context.user_data["mass_state"] = "waiting_quantity"
        await query.edit_message_text(
            "🔥 *Mass Account Creation*\n\n"
            f"Kitne accounts banana hai? \\(1\\-{MAX_MASS_LIMIT}\\)\n\n"
            "Sirf number bhejo, e\\.g\\. `5` ya `10`\n\n"
            f"⚠️ Max {MAX_CONCURRENT_ACCOUNTS} accounts parallel mein chalenge\\.\n"
            "Cancel: /cancel",
            parse_mode="MarkdownV2",
        )

    elif query.data == "help":
        help_text = (
            "🤖 *Help*\n\n"
            "/create \\- Account banao\n"
            "/create CODE \\- Referral code ke saath\n"
            "/mass \\- Bulk accounts \\(parallel\\)\n"
            "/status \\- Bot status\n"
            "/help \\- Ye message"
        )
        await query.edit_message_text(help_text, parse_mode="MarkdownV2")


async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE):
    """Handle errors"""
    logger.error(f"Bot error: {context.error}", exc_info=context.error)
    try:
        if update and hasattr(update, 'effective_chat') and update.effective_chat:
            await context.bot.send_message(
                update.effective_chat.id,
                "❌ Error aaya! Dobara try karo: /create"
            )
    except Exception:
        pass


# ─── Main ────────────────────────────────────────────────────────────────────

def main():
    """Start the bot"""
    token = BOT_TOKEN
    if not token:
        if len(sys.argv) > 1:
            token = sys.argv[1]
        else:
            print("=" * 50)
            print("  Xento.org Telegram Bot")
            print("=" * 50)
            print()
            print("Usage:")
            print("  python3 bot.py <TELEGRAM_BOT_TOKEN>")
            print("  TELEGRAM_BOT_TOKEN=xxx python3 bot.py")
            print()
            print("Steps to get a bot token:")
            print("1. Open Telegram, search @BotFather")
            print("2. Send /newbot")
            print("3. Choose a name and username")
            print("4. Copy the token and run this script")
            print()
            token = input("Enter your Telegram Bot Token: ").strip()

    if not token:
        print("Error: No bot token provided!")
        sys.exit(1)

    logger.info("Starting Xento Account Creator Bot...")

    # Build the application
    application = (
        Application.builder()
        .token(token)
        .concurrent_updates(True)
        .build()
    )

    # Register all handlers
    register_handlers(application)

    # Start polling
    logger.info("Bot is running! Press Ctrl+C to stop.")
    application.run_polling(allowed_updates=Update.ALL_TYPES)


def register_handlers(application):
    """Register all bot handlers - used by both polling and webhook modes"""
    # IMPORTANT: NO ConversationHandler! It blocks other commands.
    # Instead, /mass uses user_data state + MessageHandler for conversation flow.

    # Command handlers
    application.add_handler(CommandHandler("start", cmd_start))
    application.add_handler(CommandHandler("create", cmd_create))
    application.add_handler(CommandHandler("mass", cmd_mass))
    application.add_handler(CommandHandler("quest", cmd_quest))
    application.add_handler(CommandHandler("help", cmd_help))
    application.add_handler(CommandHandler("status", cmd_status))
    application.add_handler(CommandHandler("cancel", cmd_cancel))

    # Message handler for /mass conversation flow (quantity & referral input)
    # Only triggers when user_data["mass_state"] is set
    application.add_handler(MessageHandler(
        filters.TEXT & ~filters.COMMAND,
        handle_mass_message,
    ))

    # Callback query handler (inline buttons)
    application.add_handler(CallbackQueryHandler(callback_handler))

    # Error handler
    application.add_error_handler(error_handler)


if __name__ == "__main__":
    main()
