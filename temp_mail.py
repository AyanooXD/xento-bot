"""
Multi-Provider Temporary Mail Module (VERIFIED PROVIDERS ONLY)
==============================================================
Only providers that have been tested and confirmed working:
  1. mail.tm      - Primary, most reliable, full API
  2. tempmail.lol  - Secondary, modern API, good reliability
  3. Guerrilla Mail - Tertiary, different domain, good for diversity

Dead/Removed providers:
  - 1secmail      - 403 Forbidden (API blocked)
  - tempmail.plus - Uses 1secmail API (also 403)
  - dispostable   - Connection refused
  - mailnesia     - Can't reliably receive messages

Round-robin cycles providers so parallel requests don't all hit the same service.
On failure, automatically retries with next provider.
"""

import requests
import time
import re
import random
import string
import logging
import threading

logger = logging.getLogger(__name__)

# ─── Round-Robin Counter (thread-safe) ────────────────────────────────────────
_rr_counter = 0
_rr_lock = threading.Lock()


def _next_rr(n: int) -> int:
    """Get next round-robin index (thread-safe)"""
    global _rr_counter
    with _rr_lock:
        idx = _rr_counter % n
        _rr_counter += 1
        return idx


# ═══════════════════════════════════════════════════════════════════════════════
# PROVIDER IMPLEMENTATIONS (VERIFIED WORKING)
# ═══════════════════════════════════════════════════════════════════════════════


class MailTmProvider:
    """mail.tm - Most reliable temp email API (VERIFIED ✅)"""

    name = "mail.tm"
    BASE = "https://api.mail.tm"

    def __init__(self):
        self.token = None
        self.email = None
        self.password = None
        self.account_id = None

    def create_email(self):
        try:
            # Get domains
            resp = requests.get(f"{self.BASE}/domains", timeout=10)
            resp.raise_for_status()
            data = resp.json()
            if isinstance(data, dict) and "hydra:member" in data:
                domains = [d["domain"] for d in data["hydra:member"]]
            elif isinstance(data, list):
                domains = [d["domain"] for d in data]
            else:
                domains = [d["domain"] for d in data.get("hydra:member", [])]

            if not domains:
                return None

            domain = domains[0]
            username = ''.join(random.choices(string.ascii_lowercase + string.digits, k=12))
            self.email = f"{username}@{domain}"
            self.password = ''.join(random.choices(string.ascii_letters + string.digits, k=16))

            # Create account
            resp = requests.post(
                f"{self.BASE}/accounts",
                json={"address": self.email, "password": self.password},
                timeout=15
            )
            resp.raise_for_status()
            self.account_id = resp.json().get("id")

            # Get auth token
            token_resp = requests.post(
                f"{self.BASE}/token",
                json={"address": self.email, "password": self.password},
                timeout=15
            )
            token_resp.raise_for_status()
            self.token = token_resp.json().get("token")

            logger.info(f"[{self.name}] Created: {self.email}")
            return self.email
        except Exception as e:
            logger.warning(f"[{self.name}] Failed: {e}")
            return None

    def get_messages(self):
        if not self.token:
            return []
        try:
            resp = requests.get(
                f"{self.BASE}/messages",
                headers={"Authorization": f"Bearer {self.token}"},
                timeout=10
            )
            resp.raise_for_status()
            data = resp.json()
            if isinstance(data, dict) and "hydra:member" in data:
                return data["hydra:member"]
            return data if isinstance(data, list) else []
        except Exception as e:
            logger.error(f"[{self.name}] Get messages failed: {e}")
            return []

    def get_message_content(self, msg_id):
        if not self.token:
            return None
        try:
            resp = requests.get(
                f"{self.BASE}/messages/{msg_id}",
                headers={"Authorization": f"Bearer {self.token}"},
                timeout=10
            )
            resp.raise_for_status()
            return resp.json()
        except Exception as e:
            logger.error(f"[{self.name}] Get content failed: {e}")
            return None

    def cleanup(self):
        if self.token and self.account_id:
            try:
                requests.delete(
                    f"{self.BASE}/accounts/{self.account_id}",
                    headers={"Authorization": f"Bearer {self.token}"},
                    timeout=10
                )
            except Exception:
                pass


class TempMailLolProvider:
    """tempmail.lol - Modern API (VERIFIED ✅)"""

    name = "tempmail.lol"

    def __init__(self):
        self.email = None
        self.token = None

    def create_email(self):
        try:
            resp = requests.get("https://api.tempmail.lol/generate", timeout=10)
            resp.raise_for_status()
            data = resp.json()
            self.email = data.get("address")
            self.token = data.get("token")
            if self.email:
                logger.info(f"[{self.name}] Created: {self.email}")
                return self.email
            return None
        except Exception as e:
            logger.warning(f"[{self.name}] Failed: {e}")
            return None

    def get_messages(self):
        if not self.token:
            return []
        try:
            resp = requests.get(
                f"https://api.tempmail.lol/auth/{self.token}",
                timeout=10
            )
            resp.raise_for_status()
            data = resp.json()
            return data.get("email", [])
        except Exception as e:
            logger.error(f"[{self.name}] Get messages failed: {e}")
            return []

    def get_message_content(self, msg_id):
        # tempmail.lol returns full content in message list
        if isinstance(msg_id, dict):
            return msg_id
        return None

    def cleanup(self):
        pass


class GuerrillaMailProvider:
    """Guerrilla Mail - Different domain, good for diversity (VERIFIED ✅)"""

    name = "guerrilla"

    def __init__(self):
        self.email = None
        self.sid_token = None

    def create_email(self):
        try:
            base = "https://api.guerrillamail.com/ajax.php"
            resp = requests.get(f"{base}?f=get_email_address", timeout=10)
            resp.raise_for_status()
            data = resp.json()
            self.email = data.get("email_addr")
            self.sid_token = data.get("sid_token")
            if self.email:
                logger.info(f"[{self.name}] Created: {self.email}")
                return self.email
            return None
        except Exception as e:
            logger.warning(f"[{self.name}] Failed: {e}")
            return None

    def get_messages(self):
        if not self.sid_token:
            return []
        try:
            base = "https://api.guerrillamail.com/ajax.php"
            resp = requests.get(
                f"{base}?f=get_email_list&sid_token={self.sid_token}",
                timeout=10
            )
            resp.raise_for_status()
            data = resp.json()
            return data.get("list", [])
        except Exception as e:
            logger.error(f"[{self.name}] Get messages failed: {e}")
            return []

    def get_message_content(self, msg_id):
        if not self.sid_token:
            return None
        try:
            base = "https://api.guerrillamail.com/ajax.php"
            resp = requests.get(
                f"{base}?f=fetch_email&sid_token={self.sid_token}&email_id={msg_id}",
                timeout=10
            )
            resp.raise_for_status()
            return resp.json()
        except Exception as e:
            logger.error(f"[{self.name}] Get content failed: {e}")
            return None

    def cleanup(self):
        pass


# ═══════════════════════════════════════════════════════════════════════════════
# MAIN TempMail CLASS - Round-Robin Multi-Provider (ONLY VERIFIED)
# ═══════════════════════════════════════════════════════════════════════════════

# Only providers that are VERIFIED to work (create + receive messages)
PROVIDERS = [
    MailTmProvider,
    TempMailLolProvider,
    GuerrillaMailProvider,
]


class TempMail:
    """
    Multi-provider temporary email with round-robin load balancing.
    
    Only uses VERIFIED providers that can both create emails AND receive messages.
    Round-robin distributes requests across all providers.
    On failure, retries with next provider (up to 3 attempts).
    Thread-safe counter ensures even distribution in parallel.
    """

    def __init__(self, force_provider=None):
        self._provider = None
        self._force_provider = force_provider
        self.email = None
        self.token = None
        self.account_id = None

    def create(self):
        """Create a temporary email account using round-robin provider selection"""
        if self._force_provider:
            self._provider = self._force_provider()
            result = self._provider.create_email()
            if result:
                self.email = result
                self.token = getattr(self._provider, 'token', None)
                self.account_id = getattr(self._provider, 'account_id', None)
                return result
            return None

        # Round-robin: start from next provider in sequence
        n = len(PROVIDERS)
        start_idx = _next_rr(n)
        
        # Try all providers starting from round-robin position
        for offset in range(n):
            idx = (start_idx + offset) % n
            provider_cls = PROVIDERS[idx]
            
            try:
                self._provider = provider_cls()
                result = self._provider.create_email()
                if result:
                    self.email = result
                    self.token = getattr(self._provider, 'token', None)
                    self.account_id = getattr(self._provider, 'account_id', None)
                    return result
            except Exception as e:
                logger.warning(f"Provider {provider_cls.name} failed: {e}")
                continue
        
        logger.error("All temp mail providers failed!")
        return None

    def get_messages(self):
        """Get all messages in the inbox"""
        if self._provider:
            return self._provider.get_messages()
        return []

    def get_message_content(self, msg_id):
        """Get full content of a specific message"""
        if self._provider:
            return self._provider.get_message_content(msg_id)
        return None

    def wait_for_otp(self, max_wait=150, poll_interval=4):
        """
        Wait for OTP verification email and extract the 6-digit code.
        Returns the OTP code string or None.
        
        Increased max_wait to 150s and poll_interval to 4s for reliability.
        """
        provider_name = getattr(self._provider, 'name', 'unknown') if self._provider else 'unknown'
        logger.info(f"Waiting for OTP email at {self.email} (max {max_wait}s) via {provider_name}")
        start = time.time()
        seen_ids = set()

        while time.time() - start < max_wait:
            try:
                messages = self.get_messages()
            except Exception as e:
                logger.error(f"Error fetching messages: {e}")
                messages = []

            for msg in messages:
                msg_id = msg.get("id")
                
                # Skip already-seen messages
                if msg_id and msg_id in seen_ids:
                    continue
                if msg_id:
                    seen_ids.add(msg_id)

                # Check if this is a verification/OTP email
                subject = msg.get("subject", "") or ""
                from_addr = msg.get("from", {}) or {}
                if isinstance(from_addr, dict):
                    from_str = from_addr.get("address", "") or ""
                else:
                    from_str = str(from_addr)

                # Also check "from" as string
                if not from_str:
                    from_str = str(msg.get("from", ""))

                is_privy = "privy" in from_str.lower() or "privy" in subject.lower()
                is_verification = (
                    "verif" in subject.lower() or
                    "code" in subject.lower() or
                    "confirm" in subject.lower() or
                    "otp" in subject.lower() or
                    "log in" in subject.lower() or
                    "sign in" in subject.lower()
                )

                if is_privy or is_verification:
                    # Get full message
                    if msg_id:
                        content = self.get_message_content(msg_id)
                    else:
                        content = msg

                    if content:
                        otp = self._extract_otp(content)
                        if otp:
                            logger.info(f"Found OTP: {otp}")
                            return otp

            time.sleep(poll_interval)

        logger.warning(f"Timed out waiting for OTP at {self.email} (waited {max_wait}s)")
        return None

    def _extract_otp(self, message):
        """Extract 6-digit OTP code from email content"""
        # Combine all text fields
        text_parts = []
        for field in ["text", "html", "body", "mail_body", "mail_html", "content", "bodyHtml", "bodyText"]:
            val = message.get(field, "")
            if val:
                if isinstance(val, (list, dict)):
                    text_parts.append(str(val))
                else:
                    text_parts.append(str(val))

        # Also check intro/excerpt/subject
        for field in ["intro", "excerpt", "subject"]:
            val = message.get(field, "")
            if val:
                if isinstance(val, (list, dict)):
                    text_parts.append(str(val))
                else:
                    text_parts.append(str(val))

        full_text = "\n".join(text_parts)

        if not full_text.strip():
            return None

        # Pattern 1: 6 consecutive digits preceded by keywords
        patterns = [
            r'(?:code|otp|verification|pin|verify|your)\s*(?:is|:)?\s*(\d{6})',
            r'(?:code|otp|verification|pin|verify)[:\s]*(\d{6})',
            r'\b(\d{6})\b',  # Any 6-digit number
        ]

        for pattern in patterns:
            matches = re.findall(pattern, full_text, re.IGNORECASE)
            if matches:
                return matches[0]

        return None

    def cleanup(self):
        """Delete the temporary email account"""
        if self._provider:
            try:
                self._provider.cleanup()
                logger.info(f"Cleaned up temp mail: {self.email}")
            except Exception as e:
                logger.error(f"Failed to cleanup: {e}")


def get_provider_stats():
    """Get current round-robin counter for debugging"""
    global _rr_counter
    return {
        "counter": _rr_counter,
        "providers": [p.name for p in PROVIDERS],
        "total_providers": len(PROVIDERS),
    }


if __name__ == "__main__":
    # Test verified providers
    print("Testing Verified Temp Mail Providers")
    print("=" * 50)
    
    stats = get_provider_stats()
    print(f"Active providers: {stats['providers']}")
    print()
    
    for i in range(6):
        mail = TempMail()
        email = mail.create()
        provider_name = getattr(mail._provider, 'name', 'unknown') if mail._provider else 'none'
        print(f"  #{i+1} [{provider_name}] {email}")
        mail.cleanup()
