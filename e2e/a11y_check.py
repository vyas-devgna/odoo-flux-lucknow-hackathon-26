"""Audit seeded staff/customer/auth screens with a caller-supplied axe-core script.

Usage: /tmp/pw/bin/python e2e/a11y_check.py --axe-script /tmp/axe.min.js
Install Playwright and supply a local axe-core distribution; no third-party script runs in the app.
"""
import argparse
import json
import os
from pathlib import Path

from playwright.sync_api import sync_playwright

STAFF = ["/dashboard/", "/subscriptions/", "/subscriptions/new/", "/billing/invoices/",
         "/customers/new/", "/catalog/products/new/", "/settings/outbox/", "/reports/recurring-revenue/"]
CUSTOMER = ["/portal/", "/portal/invoices/", "/portal/account/", "/store/"]
PUBLIC = ["/accounts/login/", "/accounts/signup/", "/accounts/reset/"]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", default="http://127.0.0.1:8765")
    parser.add_argument("--axe-script", required=True, type=Path)
    args = parser.parse_args()
    axe = args.axe_script.read_text()
    failures, scans = [], 0
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(executable_path=os.getenv("CHROMIUM", "/usr/bin/chromium"), headless=True)
        for width in (1440, 390):
            for theme in ("light", "dark"):
                for role, pages in (("staff", STAFF), ("customer", CUSTOMER), ("public", PUBLIC)):
                    context = browser.new_context(viewport={"width": width, "height": 900}, reduced_motion="reduce")
                    context.add_init_script(f"localStorage.setItem('theme', {json.dumps(theme)})")
                    page = context.new_page()
                    if role != "public":
                        prefix = "A11Y_STAFF" if role == "staff" else "A11Y_CUSTOMER"
                        defaults = ("admin@brightloop.test", "Admin@12345") if role == "staff" else ("maya@acme.test", "Customer@123")
                        page.goto(args.base + "/accounts/login/")
                        page.locator("#id_username").fill(os.getenv(prefix + "_EMAIL", defaults[0]))
                        page.locator("#id_password").fill(os.getenv(prefix + "_PASSWORD", defaults[1]))
                        page.locator("button.primary").click()
                        page.wait_for_load_state("networkidle")
                        if "/accounts/login/" in page.url:
                            raise RuntimeError(f"{role} demo login failed")
                    for path in pages:
                        response = page.goto(args.base + path)
                        page.wait_for_load_state("networkidle")
                        if response.status != 200 or page.url.rstrip("/") != (args.base + path).rstrip("/"):
                            failures.append({"page": path, "error": f"unexpected response or redirect ({response.status})"})
                            continue
                        page.evaluate(axe)
                        violations = page.evaluate("""async () => (await axe.run({runOnly: ['wcag2a','wcag2aa','wcag21aa','best-practice']})).violations.map(v => ({
                          id:v.id, impact:v.impact, help:v.help, targets:v.nodes.map(n=>n.target)
                        }))""")
                        scans += 1
                        if violations:
                            failures.append({"page": path, "width": width, "theme": theme, "violations": violations})
                    context.close()
        browser.close()
    print(json.dumps({"scans": scans, "failures": failures}, indent=2))
    raise SystemExit(bool(failures))


if __name__ == "__main__":
    main()
