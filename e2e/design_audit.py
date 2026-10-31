"""Layout strictness audit: walks every element on key pages and reports computed spacing, control heights and
type sizes that are off the design scale. Usage: python e2e/design_audit.py [base_url]"""
import json
import sys
from playwright.sync_api import sync_playwright

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://localhost:8765"
PAGES = ["/dashboard/", "/subscriptions/", "/subscriptions/quotations/", "/subscriptions/pipeline/", "/subscriptions/renewals/", "/subscriptions/1/", "/subscriptions/1/edit/",
         "/subscriptions/1/change/", "/billing/invoices/", "/billing/invoices/12/", "/billing/payments/", "/billing/runs/", "/customers/", "/customers/1/", "/catalog/products/1/",
         "/catalog/plans/2/", "/reports/", "/reports/recurring-revenue/", "/reports/retention/", "/reports/receivables/", "/settings/", "/settings/automation/", "/accounts/team/", "/store/"]
JS = r"""() => {
  const ON = new Set([0,1,2,4,8,12,16,20,24,28,32,36,40,44,48,56,64,72,80,96]);
  const TYPE = new Set([11,12,13,14,16,20,28,36,40,44,56]);
  const bad = {};
  const add = (el, prop, val) => { const sel = el.tagName.toLowerCase() + (el.className && typeof el.className === 'string' ? '.' + el.className.trim().split(/\s+/).slice(0,2).join('.') : ''); const k = `${sel} ${prop}=${val}`; bad[k] = (bad[k] || 0) + 1; };
  document.querySelectorAll('body *').forEach(el => {
    if (el.closest('svg, canvas, dialog:not([open]), .pal-bg, .toasts, script, style')) return;
    const r = el.getBoundingClientRect(); if (!r.width || !r.height) return;
    const cs = getComputedStyle(el);
    for (const p of ['paddingTop','paddingBottom','paddingLeft','paddingRight','rowGap','columnGap']) { const v = parseFloat(cs[p]); if (!isNaN(v) && !ON.has(Math.round(v*100)/100) ) add(el, p, v); }
    for (const p of ["marginTop","marginBottom"]) { const v = parseFloat(cs[p]); if (!isNaN(v) && v>0 && v<100 && !ON.has(v)) add(el, p, v); }
    if (el.matches('.btn,.input,select,.chip-btn,.icon-btn,.chip,.search,.avatar,.seg,.pill,.tag')) { const h = Math.round(r.height*100)/100; if (![24,32,40,48].includes(h) && !(el.matches('.pill,.tag') && h===24)) add(el, 'height', h); }
    const fs = parseFloat(cs.fontSize); if (!TYPE.has(Math.round(fs*100)/100) && el.childNodes.length && [...el.childNodes].some(n => n.nodeType===3 && n.textContent.trim())) add(el, 'fontSize', fs);
  });
  return bad;
}"""
agg = {}
with sync_playwright() as p:
    b = p.chromium.launch(executable_path="/usr/bin/chromium", args=["--no-sandbox"])
    pg = b.new_context(viewport={"width": 1440, "height": 900}).new_page()
    pg.goto(BASE + "/accounts/login/"); pg.fill("#id_username", "admin@brightloop.test"); pg.fill("#id_password", "Admin@12345"); pg.click("button.primary"); pg.wait_for_load_state("networkidle")
    for u in PAGES:
        pg.goto(BASE + u); pg.wait_for_load_state("networkidle"); pg.wait_for_timeout(400)
        for k, n in pg.evaluate(JS).items():
            agg.setdefault(k, [0, set()]); agg[k][0] += n; agg[k][1].add(u)
    b.close()
for k, (n, pages) in sorted(agg.items(), key=lambda kv: -kv[1][0])[:int(sys.argv[2]) if len(sys.argv) > 2 else 60]:
    print(f"{n:>5}  {k}   [{len(pages)} pages]")
print("off-scale findings:", len(agg))
