"""Read-only browser checks for invoice panel density and checkbox selection.

Run against the local seeded server with:
  PYTHONPATH=/tmp/pw/lib/python3.13/site-packages .venv/bin/python e2e/layout_regressions.py
Screenshots are written to the ignored e2e/out/layout_regressions/final directory.
"""
import os
from pathlib import Path

from playwright.sync_api import sync_playwright


ROOT = Path(__file__).resolve().parents[1]
BASE = os.environ.get("LAYOUT_BASE_URL", "http://127.0.0.1:8765")
INVOICE_ID = os.environ.get("LAYOUT_INVOICE_ID", "221")
OUT = ROOT / "e2e" / "out" / "layout_regressions" / "final"
EMAIL = os.environ.get("LAYOUT_EMAIL", "admin@brightloop.test")
PASSWORD = os.environ.get("LAYOUT_PASSWORD", "Admin@12345")
errors = []


def require(ok, message):
    if not ok:
        raise AssertionError(message)


def theme(page, name):
    page.evaluate("([t]) => { document.documentElement.dataset.theme=t; localStorage.setItem('theme',t); }", [name])
    page.wait_for_timeout(120)


def capture(page, name, locator=None):
    OUT.mkdir(parents=True, exist_ok=True)
    target = locator or page
    if locator:
        target.screenshot(path=str(OUT / f"{name}.png"), animations="disabled")
    else:
        target.screenshot(path=str(OUT / f"{name}.png"), full_page=True, animations="disabled")


def viewport_metrics(page):
    return page.evaluate("""() => ({
      width: innerWidth, client: document.documentElement.clientWidth,
      scroll: document.documentElement.scrollWidth,
      height: document.documentElement.scrollHeight
    })""")


def assert_no_page_overflow(page, label):
    m = viewport_metrics(page)
    require(m["scroll"] <= m["client"] + 1, f"{label}: page overflow {m}")
    return m


def contrast_ratio(fg, bg):
    def channel(value):
        value = value / 255
        return value / 12.92 if value <= 0.04045 else ((value + 0.055) / 1.055) ** 2.4

    def luminance(color):
        values = [int(part.strip()) for part in color[color.index("(") + 1:color.index(")")].split(",")[:3]]
        return sum(weight * channel(value) for weight, value in zip((0.2126, 0.7152, 0.0722), values))

    a, b = sorted((luminance(fg), luminance(bg)), reverse=True)
    return (a + 0.05) / (b + 0.05)


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as p:
        browser = p.chromium.launch(executable_path=os.environ.get("CHROMIUM", "/usr/bin/chromium"), args=["--no-sandbox"])
        context = browser.new_context(viewport={"width": 1440, "height": 1000}, timezone_id="Asia/Kolkata", reduced_motion="reduce")
        page = context.new_page()
        page.set_default_timeout(10000)
        page.on("pageerror", lambda exc: errors.append(str(exc)))
        page.on("console", lambda msg: errors.append(msg.text) if msg.type == "error" and "favicon" not in msg.text else None)

        page.goto(f"{BASE}/accounts/login/", wait_until="networkidle")
        if page.locator("#id_username").count():
            page.fill("#id_username", EMAIL)
            page.fill("#id_password", PASSWORD)
            page.locator("button.primary").click()
            page.wait_for_load_state("networkidle")
        require("/accounts/login" not in page.url, f"staff login did not complete: {page.url}")

        detail_url = f"{BASE}/billing/invoices/{INVOICE_ID}/"
        for width, label in ((1440, "1440"), (390, "390")):
            page.set_viewport_size({"width": width, "height": 1000})
            for mode in ("light", "dark"):
                response = page.goto(detail_url, wait_until="networkidle")
                require(response and response.status == 200, f"invoice detail HTTP status at {label}/{mode}")
                theme(page, mode)
                require(page.locator(".invoice-detail-layout").count() == 1, "invoice detail layout hook missing")
                require(page.locator(".invoice-detail-layout>.stack>.card:first-child .stack-table tbody tr").count() == 1, "expected actual seeded one-line invoice")
                require("Daily Trial" in page.locator(".invoice-detail-layout").inner_text(), "seed invoice content changed")
                payment_rows = page.locator(".invoice-detail-layout .stack-table").nth(1).locator("tbody tr")
                refund = payment_rows.filter(has_text="Refund. Goodwill refund")
                receipt = payment_rows.filter(has_text="UPI-108177")
                require(refund.locator(".pill").inner_text() == "Refunded", "successful negative payment is not labeled Refunded")
                require(receipt.locator(".pill").inner_text() == "Received", "positive successful payment is not labeled Received")
                date_control = page.locator(".invoice-detail-layout input[type=date]")
                scheme = date_control.evaluate("el => getComputedStyle(el).colorScheme")
                require(scheme == mode, f"native date control scheme {scheme!r} does not match {mode}")
                capture(page, f"date-control-{label}-{mode}", date_control)
                metrics = assert_no_page_overflow(page, f"invoice detail {label}/{mode}")
                panel = page.locator(".invoice-detail-layout>.stack>.card").first
                panel_metrics = panel.evaluate("""el => {
                  const totals=el.querySelector('.totals');
                  const card=el.getBoundingClientRect(), end=totals?.getBoundingClientRect().bottom || card.bottom;
                  return {height:card.height, contentEnd:end-card.top, trailing:card.bottom-end,
                          alignContent:getComputedStyle(el.parentElement).alignContent,
                          tableWidth:el.querySelector('.tw').clientWidth,
                          tableScroll:el.querySelector('.tw').scrollWidth};
                }""")
                require(panel_metrics["trailing"] <= 48, f"single-line panel has unexplained blank tail: {panel_metrics}")
                capture(page, f"invoice-{INVOICE_ID}-{label}-{mode}")
                print(f"PASS actual invoice {label}/{mode}: {metrics}; Lines panel {panel_metrics}")

        # Real list page: verify desktop bulk selection and mobile hit target/layout.
        list_url = f"{BASE}/billing/invoices/"
        for width, label in ((1440, "1440"), (390, "390")):
            page.set_viewport_size({"width": width, "height": 1000})
            for mode in ("light", "dark"):
                response = page.goto(list_url, wait_until="networkidle")
                require(response and response.status == 200, f"invoice list HTTP status at {label}/{mode}")
                theme(page, mode)
                boxes = page.locator('input.cb[name="ids"]')
                require(boxes.count() >= 2, f"invoice list needs >=2 rows, saw {boxes.count()}")
                require(page.locator(".invoice-list").count() == 1, "invoice-list class missing")
                require(page.locator("input.cb").first.evaluate("el => getComputedStyle(el).appearance") == "none", "checkbox still uses native color-scheme rendering")
                require(page.locator("#cb-all").is_visible() == (width > 900), "bulk select visibility does not match viewport")
                metrics = assert_no_page_overflow(page, f"invoice list {label}/{mode}")
                target = page.locator(".invoice-list .row .cb-hit").first
                target_size = target.evaluate("el => ({width:el.getBoundingClientRect().width,height:el.getBoundingClientRect().height})")
                require(target_size["width"] >= 44 and target_size["height"] >= 44, f"selection hit target <44px: {target_size}")
                capture(page, f"list-{label}-{mode}-unselected")

                row = boxes.first
                row.focus()
                page.keyboard.press("Space")
                require(row.is_checked(), "Space did not select the focused invoice row")
                ring = row.evaluate("el => ({visible:el.matches(':focus-visible'),style:getComputedStyle(el).outlineStyle,width:getComputedStyle(el).outlineWidth})")
                require(ring["visible"] and ring["style"] != "none" and ring["width"] != "0px", f"keyboard focus ring missing: {ring}")
                capture(page, f"list-{label}-{mode}-row-focused")

                if width > 900:
                    header = page.locator("#cb-all")
                    require(header.evaluate("el => el.indeterminate"), "row selection did not make the bulk checkbox mixed")
                    require(not header.is_checked(), "mixed bulk checkbox must not be checked")
                    capture(page, f"list-{label}-{mode}-mixed")
                    header.click()
                    require(all(boxes.nth(i).is_checked() for i in range(boxes.count())), "select all did not check every visible invoice")
                    require(header.is_checked() and not header.evaluate("el => el.indeterminate"), "select all did not clear mixed state")
                    capture(page, f"list-{label}-{mode}-all-selected")
                    print(f"PASS bulk state {label}/{mode}: row -> mixed -> all selected; focus {ring}; hit {target_size}; page {metrics}")
                else:
                    target.click(position={"x": 40, "y": 22})
                    require(not row.is_checked(), "44px mobile label target failed to toggle the row")
                    assert_no_page_overflow(page, f"invoice list label click {label}/{mode}")
                    print(f"PASS mobile list {label}/{mode}: hit {target_size}; label-edge click toggled; page {metrics}")

        # DOM-only checkbox fixture checks ordinary consent controls stay square and keyboard-operable.
        page.set_viewport_size({"width": 390, "height": 1000})
        page.goto(detail_url, wait_until="networkidle")
        for mode in ("light", "dark"):
            theme(page, mode)
            page.evaluate("""() => {
              document.getElementById('layout-checkbox-fixture')?.remove();
              const card=document.createElement('section'); card.id='layout-checkbox-fixture'; card.className='card';
              card.innerHTML='<h2>Temporary layout regression fixture</h2><div class="field check"><input id="layout-consent" type="checkbox"><label for="layout-consent">Ordinary consent checkbox · temporary visual fixture</label></div><div style="display:flex;align-items:center;gap:12px;margin-top:8px"><input id="layout-switch" class="switch" type="checkbox"><label for="layout-switch">Switch control · temporary visual fixture</label></div>';
              document.querySelector('main').append(card);
            }""")
            field = page.locator("#layout-consent")
            field_box = field.evaluate("el => {const r=el.getBoundingClientRect(),s=getComputedStyle(el);return {width:r.width,height:r.height,appearance:s.appearance}}")
            label_height = page.locator("label[for=layout-consent]").evaluate("el => el.getBoundingClientRect().height")
            require(field_box == {"width": 18, "height": 18, "appearance": "none"}, f"ordinary field checkbox became switch/native: {field_box}")
            switch_box = page.locator("#layout-switch").evaluate("el => {const r=el.getBoundingClientRect(),s=getComputedStyle(el);return {width:r.width,height:r.height,appearance:s.appearance}}")
            require(switch_box == {"width": 40, "height": 24, "appearance": "none"}, f"switch presentation changed: {switch_box}")
            require(label_height >= 44, f"consent label hit target <44px: {label_height}")
            field.focus()
            page.keyboard.press("Space")
            require(field.is_checked(), "ordinary checkbox does not toggle by Space")
            colors = field.evaluate("el => {const s=getComputedStyle(el);return {fg:s.color,bg:s.backgroundColor,border:s.borderTopColor,focus:el.matches(':focus-visible'),outline:getComputedStyle(el).outlineStyle}}")
            ratio = contrast_ratio(colors["fg"], colors["bg"])
            require(ratio >= 3, f"checkbox mark contrast below 3:1: {ratio:.2f} ({colors})")
            require(colors["focus"] and colors["outline"] != "none", f"ordinary checkbox keyboard focus ring missing: {colors}")
            capture(page, f"ordinary-checkbox-390-{mode}-checked", page.locator("#layout-checkbox-fixture"))
            print(f"PASS checkbox/switch {mode}: checkbox 18x18; switch 40x24; label {label_height}px; Space toggled; mark contrast {ratio:.2f}:1; focus visible")

        # Simulate 200% browser zoom via a 720 CSS-pixel viewport and DOM-only long heading.
        page.set_viewport_size({"width": 720, "height": 1000})
        page.goto(detail_url, wait_until="networkidle")
        theme(page, "light")
        page.evaluate("""() => {
          const h=document.querySelector('.pagehead h1');
          h.textContent='Temporary long customer name wrapping fixture — Sahil Arora, Arora Cycles, invoice INV/2026/00221';
        }""")
        assert_no_page_overflow(page, "720 CSS px / 200% zoom long heading")
        heading = page.locator(".pagehead h1").evaluate("el => ({height:el.getBoundingClientRect().height,width:el.getBoundingClientRect().width,wrap:getComputedStyle(el.parentElement).overflowWrap})")
        require(heading["height"] > 44 and heading["wrap"] == "anywhere", f"long heading did not wrap: {heading}")
        capture(page, "invoice-720-200pct-long-heading-dom-fixture")
        print(f"PASS 200% zoom viewport: long heading wraps without page overflow: {heading}")

        require(not errors, f"browser console/page errors: {errors}")
        print("PASS browser console/page errors: 0")
        print(f"Screenshots: {OUT}")
        context.close()
        browser.close()


if __name__ == "__main__":
    main()
