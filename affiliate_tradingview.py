import sys
from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout

EMAIL    = "Willbball31@icloud.com"
CHANNEL  = "https://www.youtube.com/channel/UC4AKaszmlCmHrHJH2jouYTA"
NAME     = "William Friel"

# Bank details used only in memory, never written to disk
BANK_ACCOUNT = "325202593171"
ROUTING      = "121000358"

def run():
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False, slow_mo=500)
        page = browser.new_page()

        print("Opening TradingView affiliate program...")
        page.goto("https://www.tradingview.com/partner-program/")
        page.wait_for_load_state("networkidle")

        # Look for Apply / Join / Sign up button
        for selector in ["text=Apply now", "text=Join now", "text=Become an affiliate", "text=Apply"]:
            try:
                btn = page.locator(selector).first
                if btn.is_visible():
                    print(f"Clicking: {selector}")
                    btn.click()
                    break
            except Exception:
                continue

        page.wait_for_load_state("networkidle")
        print(f"Current URL: {page.url}")
        print("\nBrowser is open. Handle any login/captcha/2FA manually.")
        print("Press ENTER here when you're done or stuck and need help.")
        input()

        print(f"Current URL after manual step: {page.url}")

        # Try to fill in common affiliate form fields
        fields = {
            "email":   EMAIL,
            "name":    NAME,
            "website": CHANNEL,
        }
        for field_name, value in fields.items():
            for selector in [
                f"input[name='{field_name}']",
                f"input[placeholder*='{field_name}' i]",
                f"input[id*='{field_name}' i]",
            ]:
                try:
                    el = page.locator(selector).first
                    if el.is_visible():
                        el.fill(value)
                        print(f"Filled {field_name}")
                        break
                except Exception:
                    continue

        print("\nFilled what I could. Check the browser and complete anything remaining.")
        print("Press ENTER when the form is submitted.")
        input()

        print("Done. Closing browser.")
        browser.close()

if __name__ == "__main__":
    run()
