"""Measure local browser navigation/paint against a seeded server, without changing business data."""
import argparse
import json
import os
import statistics

from playwright.sync_api import sync_playwright


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", default="http://127.0.0.1:8765")
    args = parser.parse_args()
    result, errors = {}, []
    with sync_playwright() as p:
        browser = p.chromium.launch(executable_path=os.getenv("CHROMIUM", "/usr/bin/chromium"), headless=True)
        context = browser.new_context(viewport={"width": 1440, "height": 900})
        context.add_init_script("""window.__paint = {lcp:0, cls:0};
          new PerformanceObserver(list => {for(const e of list.getEntries()) window.__paint.lcp=e.startTime;}).observe({type:'largest-contentful-paint',buffered:true});
          new PerformanceObserver(list => {for(const e of list.getEntries()) if(!e.hadRecentInput) window.__paint.cls+=e.value;}).observe({type:'layout-shift',buffered:true});
        """)
        page = context.new_page()
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.goto(args.base + "/accounts/login/")
        page.locator("#id_username").fill(os.getenv("PERF_EMAIL", "admin@brightloop.test"))
        page.locator("#id_password").fill(os.getenv("PERF_PASSWORD", "Admin@12345"))
        page.locator("button.primary").click()
        page.wait_for_load_state("networkidle")
        assert "/accounts/login/" not in page.url, "Demo login failed"
        for path in ("/dashboard/", "/subscriptions/", "/billing/invoices/", "/reports/recurring-revenue/"):
            samples = []
            for _ in range(3):
                response = page.goto(args.base + path)
                page.wait_for_load_state("networkidle")
                samples.append(page.evaluate("""() => {
                  const n=performance.getEntriesByType('navigation')[0];
                  return {ttfb_ms:n.responseStart,dom_ready_ms:n.domContentLoadedEventEnd,lcp_ms:window.__paint.lcp,cls:window.__paint.cls};
                }"""))
                assert response.status == 200, (path, response.status)
                assert page.url.rstrip("/") == (args.base + path).rstrip("/"), "Unexpected authentication redirect"
            result[path] = {key: round(statistics.median(row[key] for row in samples), 3) for key in samples[0]}
        browser.close()
    assert not errors, errors
    print(json.dumps({"viewport": "1440x900", "runs": 3, "throttling": "none; local development server", "median": result}, indent=2))


if __name__ == "__main__":
    main()
