"""Capture representative read-only UI states and audit responsive layout/contrast.

Usage: /tmp/pw/bin/python e2e/visual_refinement.py [base_url] [baseline|final]
"""
import os
import sys
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from playwright.sync_api import TimeoutError as PlaywrightTimeoutError, sync_playwright

ROOT = Path(__file__).resolve().parents[1]
BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8765"
PHASE = sys.argv[2] if len(sys.argv) > 2 else "baseline"
OUT = ROOT / "e2e" / "out" / "visual_refinement" / PHASE
STAFF = (os.getenv("VISUAL_REFINEMENT_STAFF", "admin@brightloop.test"), os.getenv("VISUAL_REFINEMENT_STAFF_PASSWORD", "Admin@12345"))
CUSTOMER = (os.getenv("VISUAL_REFINEMENT_CUSTOMER", "maya@acme.test"), os.getenv("VISUAL_REFINEMENT_CUSTOMER_PASSWORD", "Customer@123"))

SAMPLES = (
    ("dashboard", "/dashboard/"),
    ("subscriptions", "/subscriptions/"),
    ("contract-change", "/subscriptions/1/change/"),
    ("customer-form", "/customers/new/"),
    ("invoice-list", "/billing/invoices/"),
    ("payments", "/billing/payments/"),
    ("outbox", "/settings/outbox/"),
    ("payment-settings", "/settings/payments/"),
    ("revenue-report", "/reports/recurring-revenue/"),
)

AUDIT = r"""() => {
  const color = value => {
    let m = value.match(/rgba?\(([^)]+)\)/i);
    if (m) {
      const parts = m[1].replaceAll(',', ' ').replace('/', ' ').trim().split(/\s+/).map(Number);
      return [parts[0], parts[1], parts[2], parts[3] ?? 1];
    }
    m = value.match(/color\(srgb\s+([\d.]+)\s+([\d.]+)\s+([\d.]+)(?:\s*\/\s*([\d.]+))?\)/i);
    return m ? [Number(m[1])*255, Number(m[2])*255, Number(m[3])*255, Number(m[4] ?? 1)] : null;
  };
  const over = (front, back) => {
    const a = front[3];
    return [0,1,2].map(i => front[i]*a + back[i]*(1-a)).concat(a + back[3]*(1-a));
  };
  const lum = c => {
    const v = c.slice(0,3).map(x => x/255 <= .04045 ? x/255/12.92 : ((x/255+.055)/1.055)**2.4);
    return .2126*v[0] + .7152*v[1] + .0722*v[2];
  };
  const ratio = (a,b) => { const x=lum(a), y=lum(b); return (Math.max(x,y)+.05)/(Math.min(x,y)+.05); };
  const visible = el => {
    const s=getComputedStyle(el), r=el.getBoundingClientRect();
    if(r.width===0 || r.height===0 || s.display==='none' || s.visibility==='hidden' || el.closest('.sr-only,[inert],body.doc-open .wrap')) return false;
    if(el.closest('details:not([open])') && !el.closest('details:not([open]) > summary')) return false;
    for(let n=el;n;n=n.parentElement) if(Number(getComputedStyle(n).opacity)<.05) return false;
    return true;
  };
  const bgFor = el => {
    const chain=[]; for(let n=el;n;n=n.parentElement) chain.push(n);
    let bg=color(getComputedStyle(document.documentElement).backgroundColor) || [255,255,255,1];
    for(const n of chain.reverse()) {
      const style=getComputedStyle(n), c=color(style.backgroundColor);
      if(c && c[3]>0) bg=over(c,bg);
      const stops=style.backgroundImage.match(/rgba?\([^)]+\)/gi);
      if(stops?.length) { const stop=color(stops[stops.length-1]); if(stop) bg=over(stop,bg); }
    }
    return bg;
  };
  const low=[];
  for(const el of document.querySelectorAll('body *')) {
    if(!visible(el) || ![...el.childNodes].some(n=>n.nodeType===3 && n.textContent.trim())) continue;
    if(el.matches(':disabled')) continue;
    const text=[...el.childNodes].filter(n=>n.nodeType===3).map(n=>n.textContent.trim()).join(' ').trim();
    if(!text) continue;
    const fg=color(getComputedStyle(el).color), bg=bgFor(el); if(!fg || !bg) continue;
    let alpha=1; for(let n=el;n;n=n.parentElement) alpha*=Number(getComputedStyle(n).opacity || 1);
    const effective=over([fg[0],fg[1],fg[2],fg[3]*alpha],bg);
    const size=parseFloat(getComputedStyle(el).fontSize), weight=parseInt(getComputedStyle(el).fontWeight,10) || 400;
    const min=size>=24 || (size>=18.67 && weight>=700) ? 3 : 4.5;
    const value=ratio(effective,bg);
    if(value+0.01<min) low.push({text:text.slice(0,64),ratio:+value.toFixed(2),minimum:min,tag:el.tagName.toLowerCase(),className:typeof el.className==='string'?el.className:''});
  }
  const overflow=document.documentElement.scrollWidth-window.innerWidth;
  const small=[];
  if(window.innerWidth<=900) {
    for(const el of document.querySelectorAll('button,a.btn,a.chip-btn,input:not([type=hidden]):not([type=checkbox]):not([type=radio]),select,textarea')) {
      if(!visible(el)) continue;
      const r=el.getBoundingClientRect();
      if(r.width<44 || r.height<44) small.push({tag:el.tagName.toLowerCase(),className:typeof el.className==='string'?el.className:'',width:Math.round(r.width),height:Math.round(r.height)});
    }
  }
  return {overflow,low,small};
}"""


def login(page, credentials):
    page.goto(BASE + "/accounts/login/", wait_until="domcontentloaded")
    page.locator("#id_username").fill(credentials[0])
    page.locator("#id_password").fill(credentials[1])
    page.locator("button.primary").click()
    page.wait_for_load_state("networkidle")
    if page.url.endswith("/accounts/login/"):
        raise RuntimeError("demo login failed; pass local demo credentials in VISUAL_REFINEMENT_* env vars")


def audit(page, label, width):
    result = page.evaluate(AUDIT)
    if result["overflow"] > 1:
        print(f"FAIL overflow {label}: document exceeds viewport by {result['overflow']}px")
    if result["low"]:
        examples = "; ".join(f"{x['ratio']}:1 {x['text']!r}" for x in result["low"][:4])
        print(f"FAIL contrast {label}: {len(result['low'])} text nodes below threshold ({examples})")
    if width <= 900 and result["small"]:
        examples = "; ".join(f"{x['tag']}.{x['className']} {x['width']}×{x['height']}" for x in result["small"][:4])
        print(f"FAIL touch target {label}: {len(result['small'])} controls below 44px ({examples})")
    return result


def capture(page, label, path, width, theme):
    response = page.goto(BASE + path, wait_until="domcontentloaded")
    page.wait_for_load_state("networkidle")
    page.wait_for_timeout(150)
    page.evaluate("theme => document.documentElement.dataset.theme = theme", theme)
    page.wait_for_timeout(400)
    status = response.status if response else 0
    if status >= 400:
        print(f"FAIL HTTP {status} {label}: {path}")
    result = audit(page, f"{label}/{width}/{theme}", width)
    folder = OUT / f"{width}-{theme}"
    folder.mkdir(parents=True, exist_ok=True)
    page.screenshot(path=str(folder / f"{label}.png"))
    heading = page.locator("h1").first.inner_text()[:60] if page.locator("h1").count() else "(no heading)"
    print(f"{status} {width} {theme} {label}: {heading}")
    return result


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    failures = []
    errors = []
    with sync_playwright() as p:
        browser = p.chromium.launch(executable_path=os.getenv("CHROMIUM", "/usr/bin/chromium"), args=["--no-sandbox"])
        widths = tuple(int(value) for value in os.getenv("VISUAL_WIDTHS", "1440,390").split(","))
        assert widths and all(320 <= width <= 3840 for width in widths), "VISUAL_WIDTHS must contain widths from 320 to 3840"
        for width in widths:
            height = 844 if width <= 900 else 900
            for theme in ("light", "dark"):
                mobile = width <= 900
                viewport = {"width": width, "height": height}
                staff_ctx = browser.new_context(viewport=viewport, is_mobile=mobile, has_touch=mobile)
                staff = staff_ctx.new_page()
                staff.on("pageerror", lambda error: errors.append(f"pageerror: {error}"))
                staff.on("console", lambda msg: errors.append(f"console: {msg.text}") if msg.type == "error" and "favicon" not in msg.text.lower() else None)
                login(staff, STAFF)
                for label, path in SAMPLES:
                    before = len(errors)
                    result = capture(staff, label, path, width, theme)
                    if result["overflow"] > 1 or result["low"] or (mobile and result["small"]):
                        failures.append(f"{label}/{width}/{theme}")
                    if len(errors) > before:
                        failures.extend(f"browser error on {label}: {error}" for error in errors[before:])

                result = capture(staff, "invoice-detail", "/billing/invoices/1/", width, theme)
                if result["overflow"] > 1 or result["low"] or (mobile and result["small"]):
                    failures.append(f"invoice-detail/{width}/{theme}")
                hidden_doc = staff.locator("a[data-doc]").first
                if hidden_doc.count() and not hidden_doc.is_visible():
                    staff.locator("details").filter(has=hidden_doc).first.locator("summary").click()
                opener = staff.locator("a[data-doc]:visible").first
                if opener.count():
                    opener.click()
                    staff.wait_for_function("document.querySelector('#doc-frame')?.classList.contains('ready')", timeout=10000)
                    staff.wait_for_timeout(400)
                    result = audit(staff, f"document-drawer/{width}/{theme}", width)
                    if result["overflow"] > 1 or result["low"] or (mobile and result["small"]):
                        failures.append(f"document-drawer/{width}/{theme}")
                    folder = OUT / f"{width}-{theme}"
                    staff.screenshot(path=str(folder / "document-drawer.png"))
                    header = staff.locator("#docview > header")
                    dims = header.evaluate("e => ({client:e.clientWidth, scroll:e.scrollWidth})")
                    if dims["scroll"] > dims["client"] + 1:
                        print(f"FAIL document toolbar clips at {width}px: {dims}")
                        failures.append(f"document toolbar/{width}/{theme}")
                    staff.keyboard.press("Escape")
                    staff.wait_for_function("!document.body.classList.contains('doc-open')")
                else:
                    print("FAIL no invoice document opener found")
                    failures.append("invoice document opener")
                staff_ctx.close()

                portal_ctx = browser.new_context(viewport=viewport, is_mobile=mobile, has_touch=mobile)
                portal = portal_ctx.new_page()
                portal.on("pageerror", lambda error: errors.append(f"pageerror: {error}"))
                portal.on("console", lambda msg: errors.append(f"console: {msg.text}") if msg.type == "error" and "favicon" not in msg.text.lower() else None)
                login(portal, CUSTOMER)
                for label, path in (("portal-home", "/portal/"), ("portal-invoices", "/portal/invoices/")):
                    result = capture(portal, label, path, width, theme)
                    if result["overflow"] > 1 or result["low"] or (mobile and result["small"]):
                        failures.append(f"{label}/{width}/{theme}")
                pay = portal.locator("a[href*='/pay/']").first
                if pay.count():
                    pay_path = urlsplit(pay.get_attribute("href")).path
                    result = capture(portal, "portal-pay", pay_path, width, theme)
                    if result["overflow"] > 1 or result["low"] or (mobile and result["small"]):
                        failures.append(f"portal-pay/{width}/{theme}")
                portal_ctx.close()

                store_ctx = browser.new_context(viewport=viewport, is_mobile=mobile, has_touch=mobile)
                store = store_ctx.new_page()
                store.on("pageerror", lambda error: errors.append(f"pageerror: {error}"))
                store.on("console", lambda msg: errors.append(f"console: {msg.text}") if msg.type == "error" and "favicon" not in msg.text.lower() else None)
                login(store, CUSTOMER)
                result = capture(store, "store-home", "/store/", width, theme)
                if result["overflow"] > 1 or result["low"] or (mobile and result["small"]):
                    failures.append(f"store-home/{width}/{theme}")
                plan = store.locator("a[href^='/store/plans/']").first
                if plan.count():
                    plan_path = urlsplit(plan.get_attribute("href")).path
                    result = capture(store, "store-plan", plan_path, width, theme)
                    if result["overflow"] > 1 or result["low"] or (mobile and result["small"]):
                        failures.append(f"store-plan/{width}/{theme}")
                    token = store.locator("input[name='checkout_token']")
                    quantity = store.locator("#q")
                    if token.count():
                        token_value = token.input_value()
                        if quantity.get_attribute("max") != "1000000":
                            failures.append("store quantity max is not 1000000")
                        requests = []
                        store.on("request", lambda req: requests.append(req.url) if req.method == "GET" and urlsplit(req.url).path == plan_path else None)
                        signed = store.locator("input[name='signed_name']")
                        agree = store.locator("input[name='agree']")
                        signed.fill("Preview keeps this signature")
                        agree.check()
                        minimum = quantity.get_attribute("min")
                        quantity.evaluate("el => el.removeAttribute('min')")
                        quantity.fill("0")
                        store.wait_for_selector("#selection-error", timeout=10000)
                        store.wait_for_timeout(400)
                        if token.count() != 1 or token.input_value() != token_value:
                            failures.append("checkout token changed or disappeared during summary update")
                        if signed.input_value() != "Preview keeps this signature" or not agree.is_checked():
                            failures.append("summary update cleared storefront signature or consent")
                        if not store.locator("#subscribe-button").is_disabled():
                            failures.append("invalid selection did not disable Subscribe")
                        if not any("quantity=0" in urlsplit(url).query for url in requests):
                            failures.append("invalid quantity preview did not reach the server")
                        if minimum:
                            quantity.evaluate("(el, value) => el.setAttribute('min', value)", minimum)
                        quantity.fill("2")
                        try:
                            store.wait_for_function("!document.querySelector('#selection-error')", timeout=10000)
                        except PlaywrightTimeoutError:
                            failures.append("valid quantity preview did not clear the selection error")
                        store.wait_for_timeout(250)
                        if signed.input_value() != "Preview keeps this signature" or not agree.is_checked():
                            failures.append("valid summary update cleared storefront signature or consent")
                        if store.locator("#subscribe-button").is_disabled():
                            failures.append("valid selection did not re-enable Subscribe")
                        for url in requests:
                            query = parse_qs(urlsplit(url).query)
                            if any(key in query for key in ("csrfmiddlewaretoken", "checkout_token", "signed_name", "agree")):
                                failures.append("store summary GET exposed a CSRF, checkout token, signature, or consent value")
                                break
                        if not requests:
                            failures.append("store summary update did not issue a GET request")
                    elif PHASE == "final":
                        failures.append("checkout token missing from store plan")
                    else:
                        print("SKIP checkout token regression check: server token not present yet")
                store_ctx.close()
        browser.close()

    if errors:
        print(f"FAIL browser errors ({len(errors)}): " + " | ".join(errors[:8]))
        failures.extend(errors)
    print(f"screenshots: {OUT}")
    print(f"checks: {len(failures)} failed")
    if failures:
        print("failed checks: " + "; ".join(failures[:30]))
        raise SystemExit(1)


if __name__ == "__main__":
    main()
