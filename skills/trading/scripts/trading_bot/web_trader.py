"""
Questrade web trading via Playwright browser automation.
Handles login, 2FA, session persistence, and order placement.

Session is persisted in BROWSER_STATE_DIR so 2FA is only needed once.
When 2FA is required, sends Telegram alert and waits for code file.
"""
import logging
import os
import random
import time
from datetime import datetime
from pathlib import Path

from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeout

import config
from notify import send_telegram

log = logging.getLogger("WebTrader")

TRADING_URL = "https://my.questrade.com/trading"
LOGIN_URL = "https://login.questrade.com"


class WebTrader:
    def __init__(self):
        self._playwright = None
        self._context = None
        self._page = None
        self._available = False
        self._state_dir = Path(config.BROWSER_STATE_DIR)
        self._state_dir.mkdir(parents=True, exist_ok=True)
        self._profile_dir = str(self._state_dir / "chromium_profile")

    def _ensure_browser(self):
        """Launch browser if not already running."""
        if self._context and self._page:
            try:
                self._page.url  # test if page is alive
                return True
            except Exception:
                self._cleanup()

        try:
            self._playwright = sync_playwright().start()
            self._context = self._playwright.chromium.launch_persistent_context(
                self._profile_dir,
                headless=True,
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
                viewport={"width": 1280, "height": 720},
            )
            self._page = self._context.pages[0] if self._context.pages else self._context.new_page()
            return True
        except Exception as e:
            log.error(f"Browser launch failed: {e}")
            return False

    def _cleanup(self):
        try:
            if self._context:
                self._context.close()
        except Exception:
            pass
        try:
            if self._playwright:
                self._playwright.stop()
        except Exception:
            pass
        self._context = None
        self._page = None
        self._playwright = None

    def _delay(self, min_ms=200, max_ms=500):
        """Human-like random delay."""
        time.sleep(random.randint(min_ms, max_ms) / 1000)

    def _screenshot(self, name):
        """Save screenshot for debugging."""
        try:
            path = str(self._state_dir / f"{name}_{int(time.time())}.png")
            self._page.screenshot(path=path)
            return path
        except Exception:
            return None

    # ── Login Flow ──

    def login(self):
        """Full login flow including 2FA if needed. Returns True if logged in."""
        if not self._ensure_browser():
            return False

        try:
            self._page.goto(LOGIN_URL, timeout=30000)
            self._delay(1000, 2000)

            # Accept cookies if present
            try:
                self._page.click('button:has-text("Accept all")', timeout=3000)
                self._delay()
            except PlaywrightTimeout:
                pass

            # Check if already logged in (persistent session)
            if self._is_logged_in():
                log.info("Already logged in (persistent session)")
                self._available = True
                return True

            # Fill login form
            user = config.QUESTRADE_WEB_USER
            password = config.QUESTRADE_WEB_PASSWORD
            if not user or not password:
                log.error("QUESTRADE_WEB_USER or QUESTRADE_WEB_PASSWORD not set")
                return False

            self._page.fill("#userId", user)
            self._delay()
            self._page.fill("#password", password)
            self._delay()
            self._page.click('button:has-text("LOG IN")')
            self._delay(3000, 5000)

            current_url = self._page.url

            # Check for 2FA
            if "loginmfa" in current_url:
                log.info("2FA selection page detected")
                # Select SMS (default) and continue
                self._page.click('button:has-text("CONTINUE")')
                self._delay(3000, 5000)
                current_url = self._page.url

            if "entermfacode" in current_url:
                log.info("2FA code entry page — requesting code from Tony")
                success = self._handle_2fa()
                if not success:
                    return False

            # Verify login succeeded
            self._delay(3000, 5000)
            if self._is_logged_in():
                log.info("Login successful!")
                self._available = True
                return True

            # Check for error
            self._screenshot("login_failed")
            log.error(f"Login failed. URL: {self._page.url}")
            return False

        except Exception as e:
            log.error(f"Login error: {e}")
            self._screenshot("login_error")
            return False

    def _handle_2fa(self):
        """Handle 2FA by sending Telegram alert and waiting for code file."""
        send_telegram(
            "<b>Questrade 2FA Code Needed</b>\n"
            "Yuri needs your verification code to log in.\n\n"
            "Check your phone for the SMS code, then either:\n"
            "1. Reply to this message with the code\n"
            "2. Or ask Yuri: 'write 2fa code 123456'"
        )

        code_file = Path(config.TWO_FA_CODE_FILE)
        log.info(f"Waiting for 2FA code at {code_file} (5 min timeout)...")

        # Poll for code file for 5 minutes
        for i in range(60):
            if code_file.exists():
                code = code_file.read_text().strip()
                code_file.unlink()  # delete after reading
                if code and len(code) >= 4:
                    log.info(f"Got 2FA code: {code}")
                    self._page.fill("#Code", code)
                    self._delay()
                    try:
                        self._page.check("#RememberDevice")
                    except Exception:
                        pass
                    self._delay()
                    self._page.click('button:has-text("VERIFY NOW")')
                    self._delay(5000, 8000)

                    # Check if it worked
                    if "entermfacode" in self._page.url:
                        # Still on 2FA page — code was wrong
                        self._screenshot("2fa_failed")
                        send_telegram("2FA code was invalid. Please send a new code.")
                        log.error("2FA code rejected")
                        return False

                    log.info("2FA verified successfully")
                    return True
            time.sleep(5)

        send_telegram("2FA timeout — no code received in 5 minutes. Trading paused.")
        log.error("2FA timeout")
        return False

    def _is_logged_in(self):
        """Check if we're on a logged-in page."""
        try:
            url = self._page.url
            # If we're on the trading page or dashboard, we're logged in
            if "my.questrade.com" in url or "questrade.com/trading" in url:
                return True
            # Check for account elements
            if self._page.locator('text=Account').first.is_visible(timeout=2000):
                return True
        except Exception:
            pass
        return False

    def check_session(self):
        """Verify session is still valid. Re-login if needed."""
        if not self._ensure_browser():
            return False

        try:
            self._page.goto(TRADING_URL, timeout=30000)
            self._delay(2000, 3000)

            if self._is_logged_in():
                return True

            # Session expired — re-login
            log.info("Session expired, re-logging in...")
            return self.login()
        except Exception as e:
            log.error(f"Session check failed: {e}")
            return False

    # ── Order Placement ──

    def place_order(self, symbol, action, quantity):
        """Place a buy or sell order via the web interface.

        Args:
            symbol: Stock symbol (e.g., 'AAPL', 'TD.TO')
            action: 'Buy' or 'Sell'
            quantity: Number of shares (can be fractional)

        Returns:
            dict with order details, or raises Exception on failure
        """
        if not self._available:
            if not self.check_session():
                raise Exception("Not logged in and login failed")

        try:
            # Navigate to trading page
            if "trading" not in self._page.url.lower():
                self._page.goto(TRADING_URL, timeout=30000)
                self._delay(3000, 5000)

            # Take pre-order screenshot
            self._screenshot(f"pre_order_{symbol}_{action}")

            # Search for symbol
            # Look for the order entry / symbol search
            search_selectors = [
                'input[placeholder*="Symbol"]',
                'input[placeholder*="symbol"]',
                'input[placeholder*="Search"]',
                'input[placeholder*="search"]',
                'input[aria-label*="Symbol"]',
                'input[aria-label*="symbol"]',
                '#symbolSearch',
                '#symbol',
                '[data-testid="symbol-search"]',
            ]

            search_input = None
            for sel in search_selectors:
                try:
                    elem = self._page.locator(sel).first
                    if elem.is_visible(timeout=2000):
                        search_input = elem
                        break
                except Exception:
                    continue

            if not search_input:
                # Try clicking a "Trade" or "New Order" button first
                try:
                    self._page.click('button:has-text("Trade"), button:has-text("New Order"), a:has-text("Trade")', timeout=5000)
                    self._delay(2000, 3000)
                    # Try search selectors again
                    for sel in search_selectors:
                        try:
                            elem = self._page.locator(sel).first
                            if elem.is_visible(timeout=2000):
                                search_input = elem
                                break
                        except Exception:
                            continue
                except Exception:
                    pass

            if not search_input:
                self._screenshot(f"no_search_input_{symbol}")
                raise Exception(f"Could not find symbol search input on trading page")

            # Enter symbol
            search_input.fill(symbol.replace(".TO", ""))
            self._delay(1000, 2000)

            # Select from dropdown if it appears
            try:
                self._page.click(f'text="{symbol}"', timeout=3000)
                self._delay()
            except PlaywrightTimeout:
                # Try pressing Enter instead
                search_input.press("Enter")
                self._delay(1000, 2000)

            # Select Buy or Sell
            try:
                self._page.click(f'button:has-text("{action}"), label:has-text("{action}"), [data-testid="{action.lower()}"]', timeout=5000)
                self._delay()
            except PlaywrightTimeout:
                log.warning(f"Could not click {action} button explicitly")

            # Enter quantity
            qty_selectors = [
                'input[placeholder*="Qty"]',
                'input[placeholder*="qty"]',
                'input[placeholder*="Quantity"]',
                'input[aria-label*="Quantity"]',
                'input[aria-label*="quantity"]',
                '#quantity',
                '#qty',
            ]

            qty_input = None
            for sel in qty_selectors:
                try:
                    elem = self._page.locator(sel).first
                    if elem.is_visible(timeout=2000):
                        qty_input = elem
                        break
                except Exception:
                    continue

            if qty_input:
                qty_input.fill(str(round(quantity, 6)))
                self._delay()
            else:
                self._screenshot(f"no_qty_input_{symbol}")
                raise Exception("Could not find quantity input")

            # Select Market order type
            try:
                self._page.click('text="Market"', timeout=3000)
                self._delay()
            except PlaywrightTimeout:
                pass  # May already be selected

            # Preview / Submit
            try:
                self._page.click('button:has-text("Preview"), button:has-text("Review")', timeout=5000)
                self._delay(2000, 3000)
            except PlaywrightTimeout:
                pass  # Some UIs go straight to submit

            # Confirm / Place Order
            try:
                self._page.click('button:has-text("Send"), button:has-text("Place"), button:has-text("Submit"), button:has-text("Confirm")', timeout=5000)
                self._delay(3000, 5000)
            except PlaywrightTimeout:
                self._screenshot(f"no_submit_{symbol}")
                raise Exception("Could not find submit/place order button")

            # Take post-order screenshot
            screenshot_path = self._screenshot(f"order_placed_{symbol}_{action}")

            log.info(f"Order placed via web: {action} {quantity:.6f} x {symbol}")
            return {
                "symbol": symbol,
                "action": action,
                "quantity": quantity,
                "method": "web_automation",
                "screenshot": screenshot_path,
            }

        except Exception as e:
            self._screenshot(f"order_error_{symbol}")
            log.error(f"Order placement failed for {symbol}: {e}")
            raise

    def close(self):
        """Cleanly close the browser."""
        self._cleanup()
        log.info("WebTrader browser closed")
