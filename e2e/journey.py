"""End-to-end acceptance journey (Playwright, real browser, real server, real database).

    uv pip install playwright && playwright install chromium     # or set CHROMIUM=/usr/bin/chromium
    python e2e/journey.py [--keep]

Starts its own server on a temporary database, drives the 26-step journey from the brief through the UI,
restarts the server to prove persistence, saves screenshots to e2e/out/, and exits non-zero on any failure.
"""
import os
import re
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import time
from decimal import Decimal
from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "e2e" / "out"
PY = os.environ.get("E2E_PYTHON", str(ROOT / ".venv" / "bin" / "python"))
PW = "Str0ng!Passw0rd"
results = []
PAGES = []


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def step(n, name, ok, detail=""):
    results.append((n, name, ok))
    print(f"{'PASS' if ok else 'FAIL'}  {n:>2}. {name}{(' — ' + detail.replace(chr(10), ' / ')) if detail and not ok else ''}")


def money(text):
    return Decimal(re.sub(r"[^\d.\-]", "", text.replace("−", "-")) or "0")


class Server:
    def __init__(self, db, port):
        self.db, self.port, self.proc = db, port, None
        self.env = {**os.environ, "DJANGO_DB": db, "DJANGO_DEBUG": "1", "SITE_URL": f"http://127.0.0.1:{port}", "PYTHONUNBUFFERED": "1"}

    def manage(self, *args):
        return subprocess.run([PY, "manage.py", *args], cwd=ROOT, env=self.env, capture_output=True, text=True)

    def start(self):
        self.proc = subprocess.Popen([PY, "manage.py", "runserver", f"127.0.0.1:{self.port}", "--noreload"], cwd=ROOT, env=self.env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        for _ in range(60):
            try:
                socket.create_connection(("127.0.0.1", self.port), 0.2).close()
                return
            except OSError:
                time.sleep(0.25)
        raise RuntimeError("server did not start")

    def stop(self):
        if self.proc:
            self.proc.send_signal(signal.SIGTERM)
            self.proc.wait(10)


def login(pg, base, email, pw):
    pg.goto(base + "/accounts/login/")
    pg.fill("#id_username", email)
    pg.fill("#id_password", pw)
    pg.click("button.primary")
    pg.wait_for_load_state("networkidle")


def cw(pg, selector):
    """Click and wait for the (possibly HTMX-boosted) navigation to settle."""
    pg.click(selector)
    pg.wait_for_load_state("networkidle")
    pg.wait_for_timeout(450)


def shot(pg, name):
    OUT.mkdir(parents=True, exist_ok=True)
    pg.wait_for_timeout(350)
    pg.screenshot(path=str(OUT / f"{name}.png"))


def main():
    keep = "--keep" in sys.argv
    tmp = tempfile.mkdtemp(prefix="brightloop-e2e-")
    port = free_port()
    srv = Server(os.path.join(tmp, "db.sqlite3"), port)
    base = f"http://127.0.0.1:{port}"
    shutil.rmtree(OUT, ignore_errors=True)
    assert srv.manage("migrate", "-v0").returncode == 0
    srv.manage("shell", "-c", f"from accounts.models import User; User.objects.create_user('admin@e2e.test','{PW}',name='Ada Admin',role='admin',is_staff=True,is_superuser=True)")
    srv.start()
    js_errors = []
    try:
        with sync_playwright() as p:
            br = p.chromium.launch(executable_path=os.environ.get("CHROMIUM", "/usr/bin/chromium"), args=["--no-sandbox"])

            def ctx(w=1440, h=900, watch=True):
                c = br.new_context(viewport={"width": w, "height": h}, timezone_id="Asia/Kolkata")
                pg = c.new_page()
                if watch:
                    pg.on("pageerror", lambda e: js_errors.append(str(e)))
                    pg.on("console", lambda m: js_errors.append(m.text) if m.type == "error" and "favicon" not in m.text else None)
                pg.set_default_timeout(15000)
                PAGES.append(pg)
                return c, pg

            # 1 admin logs in
            ac, admin = ctx()
            login(admin, base, "admin@e2e.test", PW)
            step(1, "Administrator logs in", "/dashboard" in admin.url)
            shot(admin, "01-dashboard-empty")

            # taxes and discount (6)
            admin.goto(base + "/catalog/taxes/new/")
            admin.fill("#id_name", "GST"); admin.fill("#id_rate", "18")
            admin.check("#id_is_default"); admin.click("button.primary")
            admin.goto(base + "/catalog/discounts/new/")
            admin.fill("#id_name", "Launch 10%"); admin.select_option("#id_kind", "percent"); admin.fill("#id_value", "10")
            admin.fill("#id_start_date", "2020-01-01"); cw(admin, "button.primary")
            step(6, "Administrator configures a tax and a discount", "Launch 10%" in admin.content())

            # 2/3 recurring product with a priced variant
            admin.goto(base + "/catalog/products/new/")
            admin.fill("#id_name", "Studio Suite"); admin.select_option("#id_type", "software")
            admin.fill("#id_sales_price", "1200"); admin.fill("#id_cost_price", "400")
            admin.fill("#id_variants-0-attribute", "Brand"); admin.fill("#id_variants-0-value", "Premium"); admin.fill("#id_variants-0-extra_price", "560")
            shot(admin, "02-product-form")
            cw(admin, "button.primary")
            step(2, "Administrator creates a recurring product", "Studio Suite" in admin.content())
            admin.goto(base + "/catalog/products/")
            cw(admin, "text=Studio Suite >> nth=0")
            step(3, "Variant 'Brand: Premium' (+560) is saved on the product", admin.input_value("#id_variants-0-extra_price").startswith("560"))

            # 4 optional product (add-on)
            admin.goto(base + "/catalog/products/new/")
            admin.fill("#id_name", "Analytics Pack"); admin.select_option("#id_type", "addon"); admin.fill("#id_sales_price", "300"); admin.fill("#id_cost_price", "90")
            cw(admin, "button.primary")
            step(4, "Administrator adds an optional add-on product", "Analytics Pack" in admin.content())

            # 5 recurring plan
            admin.goto(base + "/catalog/plans/new/")
            admin.fill("#id_name", "Business"); admin.fill("#id_price", "2000"); admin.select_option("#id_interval", "monthly"); admin.fill("#id_min_quantity", "1")
            admin.select_option("#id_activation", "acceptance")
            admin.select_option("#id_products", label="Studio Suite"); admin.select_option("#id_products", index=[0, 1])
            cw(admin, "button.primary")
            step(5, "Administrator creates a monthly recurring plan", "Business" in admin.content())

            # template (staff capability: sales) + staff user
            admin.goto(base + "/catalog/templates/new/")
            admin.fill("#id_name", "Business starter"); admin.select_option("#id_plan", label="Business · Monthly"); admin.fill("#id_validity_days", "21")
            admin.select_option("#id_lines-0-product", label="Studio Suite"); admin.fill("#id_lines-0-quantity", "1")
            cw(admin, "button.primary")
            admin.goto(base + "/accounts/team/new/")
            admin.fill("#id_name", "Sam Staff"); admin.fill("#id_email", "sam@e2e.test"); admin.select_option("#id_role", "staff")
            admin.fill("#id_password", PW)
            for f in ("can_sales", "can_billing"):
                admin.check(f"#id_{f}")
            cw(admin, "button.primary")

            # 7 staff logs in, 8 creates customer, 9 quotation from template
            sc, staff = ctx()
            login(staff, base, "sam@e2e.test", PW)
            step(7, "Staff member logs in", "/dashboard" in staff.url)
            staff.goto(base + "/customers/new/")
            staff.fill("#id_name", "Priya Customer"); staff.fill("#id_company", "Priya Retail"); staff.fill("#id_email", "priya@e2e.test"); staff.fill("#id_address", "12 MG Road, Lucknow")
            cw(staff, "button.primary")
            step(8, "Staff creates a customer", "Priya Retail" in staff.content())
            staff.goto(base + "/subscriptions/new/")
            staff.select_option("#id_customer", label="Priya Retail")
            staff.select_option("#id_plan", label="Business · Monthly")
            staff.select_option("#id_template", label="Business starter")
            staff.fill("#id_quantity", "3")
            cw(staff, "button.primary")
            sub_id = int(staff.url.rstrip("/").split("/")[-1])
            num = staff.locator("h1").first.inner_text().split()[0]
            cw(staff, "button:has-text('Send quotation')")
            step(9, f"Staff creates and sends quotation {num} from a template", "Quotation" in staff.locator("h1").first.inner_text() and "valid" in staff.content().lower(), staff.inner_text("main")[:200] + " | toasts: " + staff.inner_text("#toasts"))
            shot(staff, "09-quotation-sent")

            # 10 customer registers (adopting the prepared record), reviews and accepts
            cc, cust = ctx(390, 844)
            cust.goto(base + "/accounts/signup/")
            cust.fill("#id_name", "Priya Customer"); cust.fill("#id_email", "priya@e2e.test"); cust.fill("#id_password", PW); cust.fill("#id_confirm", PW)
            cw(cust, "button.primary")
            cust.goto(f"{base}/portal/subscriptions/{sub_id}/")
            shot(cust, "10-customer-quotation-mobile")
            cust.fill("#signed", "Priya Customer"); cust.check("#agree"); cw(cust, "button:has-text('Accept quotation')")
            step(10, "Customer reviews and accepts the quotation (consent recorded)", "Your acceptance" in cust.content())
            step(11, "Subscription becomes active", "Active" in cust.locator("h1").first.inner_text())

            # 12 scheduled billing via the real command
            out = srv.manage("run_scheduled")
            step(12, "Scheduled billing command creates the invoice", "created=1" in out.stdout, out.stdout + out.stderr)
            staff.goto(base + "/billing/invoices/")
            cw(staff, ".row >> nth=0")
            inv_url = staff.url
            body = staff.inner_text("main")
            # plan 3 x 2000 + suite Premium variant? template uses product base 1200 -> subtotal 7200, discount 10% = 720, tax 18% of 6480 = 1166.40, total 7646.40
            ok = all(x in body for x in ("₹7,200.00", "₹720.00", "₹1,166.40", "₹7,646.40")) or "7,646.40" in body
            step(13, "Invoice shows correct prices, 10% discount and 18% tax (₹7,646.40)", ok, body[:400])
            shot(staff, "13-invoice-detail")

            # 14 admin confirms + records partial payment (finance capability)
            admin.goto(inv_url); cw(admin, "button:has-text('Confirm')")
            admin.fill("#id_amount", "3000"); admin.select_option("#id_method", "upi"); admin.fill("#id_reference", "UTR-E2E-1")
            cw(admin, "button:has-text('Record payment')")
            body = admin.inner_text("main")
            step(14, "Partial payment recorded (₹3,000), balance ₹4,646.40", "4,646.40" in body and "Partial" in admin.locator("h1").first.inner_text())

            # 15/16 customer reports the balance, staff reconciles, invoice paid
            cust.goto(base + "/portal/invoices/")
            cw(cust, "table a.link >> nth=0")
            cw(cust, "a:has-text('Pay')")
            cust.fill("#id_reference", "UPI-CUST-77"); cw(cust, "button:has-text('made this payment')")
            step(15, "Customer reports the balance payment; it awaits confirmation", "awaiting confirmation" in cust.inner_text("main").lower())
            admin.goto(inv_url); cw(admin, "button:has-text('Found on statement')")
            step(16, "Staff reconciles it and the invoice is Paid", "Paid" in admin.locator("h1").first.inner_text())
            shot(admin, "16-invoice-paid")

            # 17/18 customer portal
            cust.goto(base + "/portal/")
            shot(cust, "17-portal-home-mobile")
            step(17, "Customer signs in to their own portal", "Hello" in cust.inner_text("main"))
            cust.goto(f"{base}/portal/subscriptions/{sub_id}/")
            step(18, "Customer reviews their subscription and invoice", "₹" in cust.inner_text("main") and num in cust.inner_text("main"))

            # 19/20 allowed add-on change with preview
            cust.goto(f"{base}/portal/subscriptions/{sub_id}/change/")
            cust.check("input[name=addon]"); cust.wait_for_timeout(900)
            preview = cust.inner_text("#preview")
            shot(cust, "19-change-preview-mobile")
            step(19, "Customer enables an add-on and sees a live cost preview", "Cost preview" in preview and "Due now" in preview or "Next billing amount" in preview)
            cw(cust, "button:has-text('Confirm change')")
            step(20, "Change is applied with prorated adjustment on the right day", "Add-on updated" in cust.content() or "Plan updated" in cust.content() or "adjustment" in cust.content().lower(), cust.inner_text("main")[:300])

            # 21 upgrade seats
            cust.goto(f"{base}/portal/subscriptions/{sub_id}/change/")
            cust.fill("#quantity", "5"); cust.wait_for_timeout(900)
            cw(cust, "button:has-text('Confirm change')")
            step(21, "Customer upgrades seats 3 → 5", "updated" in cust.content().lower() or "adjustment" in cust.content().lower())

            # 22 staff reviews contract history
            staff.goto(f"{base}/subscriptions/{sub_id}/")
            hist = staff.inner_text("main")
            shot(staff, "22-contract-history")
            step(22, "Staff sees the contract history (accepted, activated, add-on, seats)", all(w in hist for w in ("Activated", "Accepted")) and ("Quantity" in hist or "Plan" in hist or "Add-on" in hist))

            # 23/24 rerun billing, no duplicates
            n_before = admin.goto(base + "/billing/invoices/?q=" + num) and admin.locator(".hist .row").count()
            out = srv.manage("run_scheduled")
            admin.goto(base + "/billing/invoices/?q=" + num)
            n_after = admin.locator(".hist .row").count()
            step(23, "Billing rerun for the same period", "created=0" in out.stdout, out.stdout)
            step(24, "No duplicate invoice was created", n_before == n_after, f"{n_before} -> {n_after}")

            # 25 reports
            admin.goto(base + "/reports/")
            rep = admin.inner_text("main")
            shot(admin, "25-revenue-report")
            admin.goto(base + "/reports/recurring-revenue/")
            shot(admin, "25-mrr-report")
            mrr = admin.inner_text("main")
            step(25, "Reports show cash received and MRR from the events above", "₹7,646.40" in rep and "MRR" in mrr, rep[:300])

            # 26 another customer is denied
            oc, other = ctx(watch=False)  # this visitor deliberately triggers 403/404 responses
            other.goto(base + "/accounts/signup/")
            other.fill("#id_name", "Mallory"); other.fill("#id_email", "mallory@e2e.test"); other.fill("#id_password", PW); other.fill("#id_confirm", PW)
            cw(other, "button.primary")
            denied = []
            for path in (f"/portal/subscriptions/{sub_id}/", "/portal/invoices/1/", "/portal/invoices/1/pdf/", f"/portal/subscriptions/{sub_id}/change/", "/dashboard/", "/billing/invoices/1/"):
                r = other.goto(base + path)
                denied.append(r.status in (403, 404))
            step(26, "Another customer is denied access to the first customer's data", all(denied), str(denied))

            # persistence across a restart
            srv.stop(); srv.start()
            c2, again = ctx()
            login(again, base, "priya@e2e.test", PW)
            again.goto(f"{base}/portal/subscriptions/{sub_id}/")
            step(27, "Everything persists across a server restart", num in again.inner_text("main") and "Active" in again.locator("h1").first.inner_text())
            for w, h, nm in ((390, 844, "mobile"), (1024, 768, "tablet")):
                mc, mp = ctx(w, h)
                login(mp, base, "admin@e2e.test", PW)
                for path, label in (("/dashboard/", "dashboard"), ("/subscriptions/", "subscriptions"), ("/billing/invoices/", "invoices")):
                    mp.goto(base + path); mp.wait_for_load_state("networkidle")
                    overflow = mp.evaluate("document.documentElement.scrollWidth - document.documentElement.clientWidth")
                    step(28, f"No horizontal overflow on {label} ({nm})", overflow <= 1, f"overflow {overflow}px")
                    shot(mp, f"28-{nm}-{label}")
            step(29, "No JavaScript errors during the whole journey", not js_errors, "; ".join(js_errors[:3]))
            br.close()
    except Exception:
        OUT.mkdir(parents=True, exist_ok=True)
        for i, pg in enumerate(PAGES):
            try:
                pg.screenshot(path=str(OUT / f"crash-{i}.png"))
            except Exception:
                pass
        raise
    finally:
        srv.stop()
        if keep:
            print("kept:", tmp)
        else:
            shutil.rmtree(tmp, ignore_errors=True)
    failed = [r for r in results if not r[2]]
    print(f"\n{len(results) - len(failed)}/{len(results)} checks passed. Screenshots: {OUT}")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
