import sys
from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout

EMAIL    = "Willbball31@icloud.com"
NAME     = "William Friel"
CHANNEL  = "https://www.youtube.com/@Tradingbot"

def run():
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False, slow_mo=500)
        page = browser.new_page()

        print("Opening Kraken affiliate program...")
        page.goto("https://www.kraken.com/affiliate")
        page.wait_for_load_state("networkidle")
        print(f"URL: {page.url}")

        if "404" in page.title() or "not found" in page.title().lower():
            print("Trying partner program...")
            page.goto("https://www.kraken.com/partner")
            page.wait_for_load_state("networkidle")
            print(f"URL: {page.url}")

        print("\nBrowser is open. Navigate to affiliate signup if needed.")
        print("Press ENTER when ready or if you need help.")
        input()

        print(f"Current URL: {page.url}")

        fields = {
            "email": EMAIL,
            "name":  NAME,
            "firstName": NAME.split()[0],
            "lastName":  NAME.split()[1],
            "website": CHANNEL,
            "company": "Tradingbot",
        }
        filled = []
        for field_name, value in fields.items():
            for selector in [
                f"input[name='{field_name}']",
                f"input[name='{field_name.lower()}']",
                f"input[placeholder*='{field_name}' i]",
                f"input[id*='{field_name}' i]",
            ]:
                try:
                    el = page.locator(selector).first
                    if el.is_visible():
                        el.fill(value)
                        filled.append(field_name)
                        break
                except Exception:
                    continue

        if filled:
            print(f"Filled: {', '.join(filled)}")
        else:
            print("No fields auto-filled — complete the form manually.")

        print("\nComplete the form and submit, then press ENTER.")
        input()

        for selector in [
            "text=referral", "text=affiliate", "text=partner link",
            "[class*='link']", "[class*='referral']", "[class*='affiliate']",
        ]:
            try:
                els = page.locator(selector).all()
                for el in els[:5]:
                    txt = el.inner_text().strip()
                    if txt and len(txt) < 200:
                        print(f"Found: {txt}")
            except Exception:
                continue

        print(f"\nFinal URL: {page.url}")
        print("Copy your affiliate link/ID above, then press ENTER to close.")
        input()
        browser.close()

if __name__ == "__main__":
    run()
