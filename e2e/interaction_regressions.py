"""Read-only browser checks for shared-shell interactions.

Run against the existing dev server with:
  CHROMIUM=/usr/bin/chromium /tmp/pw/bin/python e2e/interaction_regressions.py

The script performs GETs only. It uses temporary DOM fixtures for authenticated
shell-only controls and never submits checkout, billing, or outbox forms.
"""
import os
import sys
import tempfile
from pathlib import Path

from playwright.sync_api import sync_playwright

BASE = os.environ.get("BASE_URL", "http://127.0.0.1:8765").rstrip("/")
CHROMIUM = os.environ.get("CHROMIUM", "/usr/bin/chromium")


def check(name, passed):
    if not passed:
        raise AssertionError(name)
    print(f"PASS  {name}")


def main():
    screenshots = Path(tempfile.mkdtemp(prefix="brightloop-interaction-"))
    page_errors = []
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True, executable_path=CHROMIUM, args=["--no-sandbox"])
        context = browser.new_context(viewport={"width": 1440, "height": 900}, reduced_motion="reduce")
        page = context.new_page()
        page.on("pageerror", lambda error: page_errors.append(str(error)))

        response = page.goto(f"{BASE}/store/", wait_until="domcontentloaded")
        page.wait_for_load_state("networkidle")
        check("storefront loads without JavaScript errors", response is not None and response.status == 200)
        check("store brand link has the Brightloop Studio accessible name", page.locator("header a.brand").get_attribute("aria-label") == "Brightloop Studio")
        page.screenshot(path=str(screenshots / "store-desktop-light.png"), full_page=True)
        page.set_viewport_size({"width": 1920, "height": 1080})
        page.screenshot(path=str(screenshots / "store-wide-light.png"), full_page=True)
        page.set_viewport_size({"width": 1440, "height": 900})

        auth_page = context.new_page()
        auth_page.on("pageerror", lambda error: page_errors.append(str(error)))
        auth_response = auth_page.goto(f"{BASE}/accounts/login/", wait_until="domcontentloaded")
        check("auth page provides one main landmark around its content", auth_response is not None and auth_response.status == 200 and auth_page.locator("main").count() == 1 and auth_page.locator("main").locator(".art, .panel").count() == 2)
        auth_page.close()

        plan_href = page.locator('a[href^="/store/"]').evaluate_all(
            "els => els.map(a => a.getAttribute('href')).find(h => h && h !== '/store/')"
        )
        check("a published plan is available", bool(plan_href))
        response = page.goto(f"{BASE}{plan_href}", wait_until="domcontentloaded")
        page.wait_for_load_state("networkidle")
        check("store plan page loads", response is not None and response.status == 200)
        page.screenshot(path=str(screenshots / "plan-desktop-light.png"), full_page=True)
        page.set_viewport_size({"width": 1920, "height": 1080})
        page.screenshot(path=str(screenshots / "plan-wide-light.png"), full_page=True)
        page.set_viewport_size({"width": 1440, "height": 900})

        consent = page.evaluate("""() => {
          const form = document.querySelector('#summary')?.closest('form');
          const summary = document.querySelector('#summary');
          const region = form?.querySelector('#consent');
          if (!form || !summary || !region) return null;
          let field = region.querySelector('#agree');
          if (field && summary.contains(field)) return { inside: true };
          if (!field) {
            field = document.createElement('input'); field.type = 'checkbox'; field.id = '__e2e-consent'; field.checked = true;
            region.appendChild(field);
          }
          field.checked = true;
          return { inside: false, actual: field.id === 'agree', id: field.id, region: region.id };
        }""")
        check("consent region and control are outside the live summary", consent is not None and not consent.get("inside", True) and consent.get("region") == "consent")
        page.evaluate("""() => { const button=document.createElement('button'); button.id='subscribe-button'; button.type='button'; button.textContent='Subscribe'; document.querySelector('#consent').append(button); }""")
        quantity = page.locator('input[name="quantity"]')
        minimum = int(quantity.get_attribute("min") or 1)
        quantity.evaluate("e => e.removeAttribute('min')")
        quantity.fill("0")
        page.wait_for_selector("#selection-error")
        page.wait_for_function("document.querySelector('#subscribe-button').disabled")
        quantity.fill(str(minimum))
        page.wait_for_function("!document.querySelector('#selection-error') && !document.querySelector('#subscribe-button').disabled")
        check("corrected live validation restores the checkout action", True)
        quantity.evaluate("(e, minimum) => e.min = minimum", str(minimum))
        page.evaluate("document.querySelector('#subscribe-button').remove()")
        before_summary = page.locator("#summary").inner_text()
        page.evaluate("""() => {
          window.__summarySwaps = 0;
          document.body.addEventListener('htmx:afterSwap', e => { if (e.detail.target?.id === 'summary') window.__summarySwaps++; });
        }""")
        quantity = page.locator("#q")
        next_quantity = max(int(quantity.get_attribute("min") or "1"), int(quantity.input_value() or "1") + 1)
        quantity.fill(str(next_quantity))
        page.wait_for_function("window.__summarySwaps > 0", timeout=10000)
        page.wait_for_function("() => !!document.querySelector('#agree, #__e2e-consent')?.checked", timeout=3000)
        after_summary = page.locator("#summary").inner_text()
        check("live preview swaps summary without clearing checked consent", after_summary != before_summary and page.locator("#consent").is_visible() and page.locator("#agree, #__e2e-consent").first.is_checked())
        if not consent["actual"]:
            print("INFO  guest page used an external consent fixture; customer consent is checked when present")
            page.locator("#__e2e-consent").evaluate("e => e.remove()")
        page.evaluate("""() => {
          const source = document.querySelector('#summary').closest('form'), fixture = document.createElement('form');
          for (const key of ['hx-get', 'hx-trigger', 'hx-params', 'hx-push-url']) if (source.hasAttribute(key)) fixture.setAttribute(key, source.getAttribute(key));
          fixture.setAttribute('hx-target', '#e2e-consent-summary'); fixture.innerHTML = '<div id="e2e-consent-summary"></div><label>Consent <input id="e2e-consent" type="checkbox" name="agree"></label><label>Signature <input id="e2e-signature" name="signed_name"></label>';
          document.body.append(fixture); window.__consentPreviewRequests = 0;
          document.body.addEventListener('htmx:beforeRequest', e => { if (e.detail.elt === fixture) window.__consentPreviewRequests++; });
          htmx.process(fixture); window.__consentFixture = fixture;
        }""")
        page.locator("#e2e-signature").fill("Ada Example")
        page.locator("#e2e-consent").check()
        page.wait_for_timeout(350)
        check("signature and consent edits do not trigger a preview request", page.evaluate("window.__consentPreviewRequests === 0"))
        page.evaluate("window.__consentFixture.remove()")
        page.goto(f"{BASE}{plan_href}", wait_until="domcontentloaded")
        page.wait_for_load_state("networkidle")

        page.evaluate("""() => {
          const trigger = document.createElement('button'); trigger.id = 'cmdk'; trigger.type = 'button'; trigger.textContent = 'Open palette'; document.body.append(trigger);
          const overlay = document.createElement('div'); overlay.id = 'pal'; overlay.className = 'pal-bg'; overlay.setAttribute('role', 'dialog'); overlay.setAttribute('aria-modal', 'true');
          overlay.innerHTML = '<div class="pal"><input id="palq" aria-label="Search"><ul id="pall"></ul></div>'; document.body.append(overlay);
          window.__searchPending = []; window.__baseFetch = window.fetch;
          window.fetch = url => new Promise(resolve => {
            const q = new URL(url, location.href).searchParams.get('q');
            window.__searchPending.push({ q, resolve: results => resolve({ ok: true, json: async () => ({ results }) }) });
          });
        }""")
        page.locator("#cmdk").click()
        check("palette makes background content inert", page.locator(".wrap").evaluate("e => e.inert"))
        page.locator("#palq").fill("xss")
        page.wait_for_function("window.__searchPending.some(r => r.q === 'xss')")
        page.evaluate("""() => window.__searchPending.find(r => r.q === 'xss').resolve([
          { t: '<img src=x onerror=window.__paletteXss=1>', sub: '<svg onload=window.__paletteXss=1>', u: '/store/' },
          { t: 'unsafe url', u: 'javascript:window.__paletteXss=2' }
        ])""")
        page.wait_for_function("document.querySelector('#pall a')?.textContent.includes('<img')")
        safe_palette = page.evaluate("""() => ({
          literal: document.querySelector('#pall').textContent.includes('<img src=x'),
          injected: !!document.querySelector('#pall img, #pall svg'),
          unsafe: !!document.querySelector('#pall a[href^="javascript:"]'),
          ran: !!window.__paletteXss
        })""")
        check("palette renders untrusted labels as text and drops unsafe URLs", safe_palette["literal"] and not safe_palette["injected"] and not safe_palette["unsafe"] and not safe_palette["ran"])

        page.evaluate("""() => {
          const tabs=document.createElement('nav'); tabs.id='tabs'; tabs.innerHTML='<a href="/store/?first">Keep first</a><a href="/store/?second">Keep second</a>'; document.body.append(tabs);
        }""")
        page.locator("#palq").fill("keep")
        page.wait_for_function("window.__searchPending.some(r => r.q === 'keep')")
        page.keyboard.press("ArrowDown")
        page.evaluate("() => window.__searchPending.find(r => r.q === 'keep').resolve([{t:'Keep remote',u:'/store/?remote'}])")
        page.wait_for_function("document.querySelector('#pall').textContent.includes('Keep remote')")
        check("keyboard selection survives newly inserted remote search results", page.locator("#pall .on a").get_attribute("href").endswith("?second"))
        page.evaluate("document.querySelector('#tabs').remove()")

        page.locator("#palq").fill("old")
        page.wait_for_function("window.__searchPending.some(r => r.q === 'old')")
        page.locator("#palq").fill("new")
        page.wait_for_function("window.__searchPending.some(r => r.q === 'new')")
        page.evaluate("""() => {
          window.__searchPending.find(r => r.q === 'new').resolve([{ t: 'new result', u: '/store/' }]);
          window.__searchPending.find(r => r.q === 'old').resolve([{ t: 'stale result', u: '/accounts/signup/' }]);
        }""")
        page.wait_for_function("document.querySelector('#pall').textContent.includes('new result')")
        page.wait_for_timeout(100)
        check("older palette response cannot overwrite the latest query", "stale result" not in page.locator("#pall").inner_text())
        page.keyboard.press("Escape")
        check("palette Escape closes and restores focus", not page.locator("#pal").evaluate("e => e.classList.contains('open')") and page.evaluate("document.activeElement.id") == "cmdk")

        page.evaluate("""() => {
          const root = document.createElement('div'); root.innerHTML = `
            <a id="doc-opener" data-doc href="${location.pathname}" data-doc-title="Preview test">Open document</a>
            <a id="doc-error-opener" data-doc href="/__ux_test_fail__/document/">Open failing document</a>
            <div class="doc-bg" id="doc-bg"></div>
            <aside class="docview" id="docview" role="dialog" aria-modal="true" aria-labelledby="doc-title" inert>
              <header><b id="doc-title">Document</b><button id="doc-copy" type="button" hidden>Copy link</button><a id="doc-pdf" href="#" hidden>PDF</a><button id="doc-print" type="button">Print</button><button id="doc-close" type="button">Close</button></header>
              <div class="docbody"><iframe id="doc-frame" title="Document preview"></iframe><div class="docload" id="doc-load" role="status">Loading…</div></div>
            </aside>`;
          root.id = 'doc-fixtures'; document.body.append(root);
          Object.defineProperty(navigator, 'clipboard', { configurable: true, value: { writeText: async value => { window.__copiedLink = value; } } });
        }""")
        page.route("**/__ux_test_fail__/**", lambda route: route.abort())
        page.locator("#doc-opener").click()
        page.wait_for_function("document.querySelector('#doc-frame').classList.contains('ready')", timeout=10000)
        check("document preview opens with focus inside the viewer", page.evaluate("document.activeElement.id") == "doc-print")
        check("document viewer makes background content inert", page.locator(".wrap").evaluate("e => e.inert"))
        page.locator("#doc-copy").click()
        page.wait_for_function("!!window.__copiedLink")
        copied = page.evaluate("window.__copiedLink")
        check("copy link uses the same-origin document URL without the embed flag", copied == f"{BASE}{plan_href}" and "embed=1" not in copied)
        page.locator("#doc-close").focus()
        page.keyboard.press("Tab")
        check("viewer Tab cycle includes the iframe", page.evaluate("document.activeElement.id") == "doc-frame")
        page.keyboard.press("Escape")
        page.wait_for_function("!document.body.classList.contains('doc-open')")
        check("Escape from the document iframe closes and restores focus", page.evaluate("document.activeElement.id") == "doc-opener")
        page.locator("#doc-error-opener").click()
        page.wait_for_function("document.querySelector('#doc-load').textContent.includes('Preview unavailable')", timeout=10000)
        check("failed iframe shows a recoverable error state", True)
        page.locator("#doc-close").click()
        page.evaluate("""() => { document.querySelector('#doc-fixtures').remove(); document.querySelector('#cmdk').remove(); document.querySelector('#pal').remove(); window.fetch = window.__baseFetch; }""")

        page.add_script_tag(url=f"{BASE}/static/vendor/chart.min.js")
        page.evaluate("""() => {
          const f = document.createElement('figure'); f.className = 'chart'; f.innerHTML = '<figcaption><h2>Interaction test</h2></figcaption><div class="chart-box"><canvas id="e2e-chart" data-chart="e2e-chart-data"></canvas></div>';
          const data = document.createElement('script'); data.type = 'application/json'; data.id = 'e2e-chart-data'; data.textContent = JSON.stringify({ type: 'line', labels: ['Jan', 'Feb'], datasets: [{ label: 'One', data: [2, 4], color: 'c1' }, { label: 'Two', data: [1, 3], color: 'c2' }] });
          document.body.append(data, f); document.dispatchEvent(new Event('htmx:afterSettle'));
        }""")
        page.wait_for_function("Chart.getChart(document.querySelector('#e2e-chart')) !== undefined")
        initial_theme = page.evaluate("document.documentElement.dataset.theme")
        aligned_palette = """() => {
          const chart = Chart.getChart(document.querySelector('#e2e-chart'));
          const style = getComputedStyle(document.documentElement);
          const line = style.getPropertyValue('--line').trim(), mute = style.getPropertyValue('--mute').trim();
          return !!chart && chart.options.scales.x.border.color === line && chart.options.scales.y.grid.color === line && chart.options.scales.x.ticks.color === mute && chart.options.scales.y.ticks.color === mute;
        }"""
        check("new chart palette matches the theme already applied at page load", page.evaluate(aligned_palette))
        page.evaluate("theme => document.documentElement.dataset.theme = theme", "dark" if initial_theme != "dark" else "light")
        page.wait_for_function(aligned_palette)
        check("chart grid and axis colors refresh after a direct theme attribute change", True)
        page.evaluate("theme => document.documentElement.dataset.theme = theme", initial_theme)
        page.wait_for_function(aligned_palette)
        check("chart palette follows restored theme tokens", True)
        chart_opts = page.evaluate("""() => {
          const c = Chart.getChart(document.querySelector('#e2e-chart'));
          return { reduced: c.options.animation === false, pointer: c.options.events.includes('pointerdown'), hit: c.data.datasets[0].pointHitRadius, touchAction: c.canvas.style.touchAction };
        }""")
        check("reduced motion disables chart animation", chart_opts["reduced"])
        check("chart accepts touch pointer input with a larger line hit area", chart_opts["pointer"] and chart_opts["hit"] == 24 and chart_opts["touchAction"] == "pan-y")
        page.emulate_media(reduced_motion="no-preference")
        page.wait_for_function("Chart.getChart(document.querySelector('#e2e-chart')).options.animation.duration === 200")
        check("chart animation duration is capped at 200ms", True)
        page.evaluate("""() => {
          const counter = document.createElement('span'); counter.id = 'e2e-counter'; counter.dataset.count = '100'; counter.dataset.dec = '0'; counter.textContent = '0';
          window.__counterStart = performance.now(); document.body.append(counter); document.dispatchEvent(new Event('htmx:afterSettle'));
        }""")
        page.wait_for_function("document.querySelector('#e2e-counter').textContent === '100'", timeout=450)
        check("counter animation settles within a 350ms browser budget", page.evaluate("performance.now() - window.__counterStart < 350"))
        page.locator("#e2e-counter").evaluate("e => e.remove()")
        page.emulate_media(reduced_motion="reduce")
        page.wait_for_function("Chart.getChart(document.querySelector('#e2e-chart')).options.animation === false")
        check("reduced motion also disables chart animation after a preference change", True)
        page.locator('.legend button').first.click()
        page.locator('.seg button[data-t="dark"]').click()
        page.locator('.legend button').first.click()
        check("chart legend remains wired after theme rebuild", page.evaluate("!Chart.getChart(document.querySelector('#e2e-chart')).isDatasetVisible(0)"))
        page.evaluate("""() => {
          const figure = document.querySelector('#e2e-chart').closest('figure');
          window.__chartCanvas = document.querySelector('#e2e-chart');
          document.dispatchEvent(new CustomEvent('htmx:afterSettle')); document.dispatchEvent(new CustomEvent('htmx:afterSettle'));
          window.__chartStable = Chart.getChart(document.querySelector('#e2e-chart')) !== undefined;
          document.dispatchEvent(new CustomEvent('htmx:beforeCleanupElement', { detail: { elt: figure } })); figure.remove();
        }""")
        check("HTMX cleanup destroys chart instances and repeated init stays idempotent", page.evaluate("window.__chartStable && !Chart.getChart(window.__chartCanvas)"))

        page.set_viewport_size({"width": 390, "height": 844})
        heading = page.locator(".pagehead h1").first
        original_heading = heading.inner_text()
        heading.evaluate("e => e.textContent = 'X'.repeat(160)")
        check("long product headings wrap without page overflow", page.evaluate("document.documentElement.scrollWidth <= innerWidth"))
        page.set_viewport_size({"width": 1280, "height": 900})
        page.evaluate("document.body.style.zoom = '2'")
        check("long headings remain usable at 200 percent zoom", page.evaluate("document.documentElement.scrollWidth <= innerWidth"))
        page.evaluate("document.body.style.zoom = ''")
        page.set_viewport_size({"width": 390, "height": 844})
        heading.evaluate("(e, text) => e.textContent = text", original_heading)
        page.evaluate("document.querySelectorAll('.toast').forEach(e => e.remove())")
        page.screenshot(path=str(screenshots / "plan-mobile-dark.png"), full_page=True)

        page.evaluate("""() => {
          const fixture = document.createElement('div'); fixture.id = 'interaction-fixtures'; fixture.innerHTML = `
            <form id="dirty-form" data-dirty><input name="name" value="original"><input id="id_lines-TOTAL_FORMS" type="hidden" name="lines-TOTAL_FORMS" value="1"><button type="button" data-add-row="#fixture-rows" data-prefix="lines">Add row</button><table><tbody id="fixture-rows"></tbody></table></form>
            <script id="empty-row" type="text/template"><tr class="formset-row"><td><input name="lines-__prefix__-name" value=""></td></tr></script>
            <form id="submit-form"><button id="submit-button">Save</button></form>
            <button id="e2e-menu" class="burger" type="button" aria-expanded="false">Menu</button><details id="e2e-details" open><summary>More</summary><button id="e2e-menu-item" type="button">Item</button></details>
            <div class="card flush"><header><h2>Recurring revenue</h2></header><div id="e2e-table-overflow" class="tw" style="width:300px;max-width:100%;overflow-x:auto"><table style="width:600px"><tbody><tr><td>Wide table</td></tr></tbody></table></div></div>
            <div id="e2e-table-fit" class="tw" style="width:300px;max-width:100%;overflow-x:auto"><table style="width:200px"><tbody><tr><td>Fitting table</td></tr></tbody></table></div>`;
          document.body.append(fixture);
          document.querySelector('#submit-form').addEventListener('submit', e => { e.preventDefault(); window.__submitEvents = (window.__submitEvents || 0) + 1; });
        }""")
        table_a11y = page.evaluate("""() => {
          document.dispatchEvent(new CustomEvent('htmx:afterSettle'));
          const wide = document.querySelector('#e2e-table-overflow'), fit = document.querySelector('#e2e-table-fit');
          return { overflow: wide.scrollWidth > wide.clientWidth, tabindex: wide.getAttribute('tabindex'), role: wide.getAttribute('role'), label: wide.getAttribute('aria-label'), fitOverflow: fit.scrollWidth > fit.clientWidth, fitAttrs: ['tabindex', 'role', 'aria-label'].some(a => fit.hasAttribute(a)) };
        }""")
        check("only overflowing table wrappers receive a named keyboard region", table_a11y["overflow"] and table_a11y["tabindex"] == "0" and table_a11y["role"] == "region" and table_a11y["label"] == "Recurring revenue" and not table_a11y["fitOverflow"] and not table_a11y["fitAttrs"])
        page.locator("#e2e-table-overflow").focus()
        check("overflowing table wrapper accepts keyboard focus", page.evaluate("document.activeElement.id === 'e2e-table-overflow'"))
        dirty = page.locator("#dirty-form input[name=name]")
        dirty.fill("changed")
        check("dirty form warns only while values differ from their initial state", page.locator("#dirty-form").evaluate("f => f.classList.contains('is-dirty')"))
        dirty.fill("original")
        check("restoring original values clears dirty state", not page.locator("#dirty-form").evaluate("f => f.classList.contains('is-dirty')"))
        page.locator('#dirty-form [data-add-row]').click()
        check("adding an empty formset row marks the form dirty", page.locator("#dirty-form").evaluate("f => f.classList.contains('is-dirty')"))
        page.evaluate("""() => { const f = document.querySelector('#submit-form'), b = document.querySelector('#submit-button'); f.requestSubmit(b); f.requestSubmit(b); }""")
        page.wait_for_function("document.querySelector('#submit-button').disabled")
        check("rapid repeated submits are blocked and show progress", page.evaluate("window.__submitEvents === 1 && document.querySelector('#submit-button').classList.contains('loading')"))
        page.evaluate("""() => document.dispatchEvent(new CustomEvent('htmx:afterRequest', { detail: { elt: document.querySelector('#submit-form') } }))""")
        check("HTMX completion unlocks a form for retry", page.evaluate("!document.querySelector('#submit-form').dataset.submitting && !document.querySelector('#submit-button').disabled"))

        page.locator("#e2e-menu").click()
        check("mobile menu exposes expanded state", page.locator("#e2e-menu").get_attribute("aria-expanded") == "true")
        page.keyboard.press("Escape")
        check("Escape closes mobile menu and restores its trigger", page.locator("#e2e-menu").get_attribute("aria-expanded") == "false" and page.evaluate("document.activeElement.id") == "e2e-menu")
        page.locator("#e2e-details summary").click()
        page.locator("#e2e-menu-item").focus()
        page.keyboard.press("Escape")
        check("Escape closes the active details menu and restores its summary", not page.locator("#e2e-details").evaluate("e => e.open") and page.evaluate("document.activeElement.textContent") == "More")
        check("no uncaught browser JavaScript errors", not page_errors)
        print(f"Evidence screenshots: {screenshots}")
        context.close()
        browser.close()


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"FAIL  {exc}", file=sys.stderr)
        raise
