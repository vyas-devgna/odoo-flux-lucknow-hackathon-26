"""Print regression run against an isolated SQLite DB and real Chromium.

Prerequisites: project .venv/Django, Playwright available to this interpreter,
Chromium (CHROMIUM may select its path), and Poppler pdfinfo/pdftotext/pdftoppm
for PDF page-count, text, and raster review. All generated output stays in /tmp.
Run with: /tmp/pw/bin/python e2e/print_regressions.py
"""
import os
import re
import shutil
import signal
import socket
import subprocess
import tempfile
import time
from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]
PY = str(ROOT / ".venv" / "bin" / "python")
PASSWORD = "Print-Only-Test-Password-2026!"


def free_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def main():
    for command in ("pdfinfo", "pdftotext", "pdftoppm"):
        if not shutil.which(command):
            raise SystemExit(f"Missing Poppler command: {command}")
    out = Path(tempfile.mkdtemp(prefix="brightloop-print-review-"))
    port = free_port()
    env = {**os.environ, "DJANGO_DB": str(out / "db.sqlite3"), "DJANGO_DEBUG": "1",
           "SITE_URL": f"http://127.0.0.1:{port}", "PYTHONUNBUFFERED": "1"}

    def manage(*args, check=True):
        result = subprocess.run([PY, "manage.py", *args], cwd=ROOT, env=env, text=True, capture_output=True)
        if check and result.returncode:
            raise RuntimeError(result.stdout + result.stderr)
        return result

    manage("migrate", "-v0")
    seed = (
        "from accounts.models import User; from core.tests.helpers import make_world, make_active, TODAY; "
        "from billing import services; from billing.models import Invoice, InvoiceLine; "
        "from decimal import Decimal; "
        f"u=User.objects.create_user('print-admin@example.test','{PASSWORD}',role='admin',name='Print Admin',is_staff=True,is_superuser=True); "
        "p,c1=make_world(price=1000); s1=make_active(p,c1,TODAY(),qty=1,user=u); "
        "c2=make_world(price=1000)[1]; s2=make_active(p,c2,TODAY(),qty=1,user=u); "
        "services.run_billing(TODAY(),only=[s1.pk,s2.pk]); "
        "i1=Invoice.objects.get(subscription=s1); i2=Invoice.objects.get(subscription=s2); "
        "services.confirm_invoice(i1); services.record_payment(i1,amount=i1.total,method='bank',reference='PRINT-PAID'); "
        "InvoiceLine.objects.bulk_create([InvoiceLine(invoice=i2,description=f'Print long line {n:02d}',quantity=1,unit_price=Decimal('100.00'),tax_rate=Decimal('18.00'),tax_name='GST',tax_amount=Decimal('18.00'),amount=Decimal('100.00')) for n in range(70)]); "
        "i2.subtotal += Decimal('7000.00'); i2.tax_total += Decimal('1260.00'); i2.total=i2.subtotal-i2.discount_total+i2.tax_total; i2.save(update_fields=['subtotal','tax_total','total']); "
        "print('PRINT_IDS',i1.pk,i2.pk)"
    )
    stdout = manage("shell", "-c", seed).stdout
    match = re.search(r"PRINT_IDS\s+(\d+)\s+(\d+)", stdout)
    if not match:
        raise RuntimeError(f"Could not read seeded invoice IDs: {stdout}")
    short_id, long_id = match.groups()
    server = subprocess.Popen([PY, "manage.py", "runserver", f"127.0.0.1:{port}", "--noreload"], cwd=ROOT, env=env,
                              stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    base = f"http://127.0.0.1:{port}"
    try:
        for _ in range(80):
            try:
                with socket.create_connection(("127.0.0.1", port), timeout=0.2):
                    break
            except OSError:
                time.sleep(0.2)
        else:
            raise RuntimeError("Isolated Django server did not start")
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(executable_path=os.environ.get("CHROMIUM", "/usr/bin/chromium"), args=["--no-sandbox"])
            context = browser.new_context(viewport={"width": 390, "height": 844}, timezone_id="Asia/Kolkata", accept_downloads=True)
            page = context.new_page()
            page.set_default_timeout(15000)
            page.goto(base + "/accounts/login/", wait_until="networkidle")
            page.fill("#id_username", "print-admin@example.test")
            page.fill("#id_password", PASSWORD)
            page.click("button.primary")
            page.wait_for_load_state("networkidle")

            short_url = f"{base}/billing/invoices/{short_id}/print/"
            page.goto(short_url, wait_until="networkidle")
            page.screenshot(path=str(out / "invoice-mobile-view.png"), full_page=True)
            page.pdf(path=str(out / "invoice-short-native.pdf"), format="A4", print_background=True, prefer_css_page_size=True)
            pdf_response = page.request.get(base + f"/billing/invoices/{short_id}/pdf/")
            if pdf_response.status != 200 or "application/pdf" not in pdf_response.headers.get("content-type", ""):
                raise AssertionError(f"ReportLab invoice endpoint returned {pdf_response.status}")
            (out / "invoice-short-reportlab.pdf").write_bytes(pdf_response.body())

            page.goto(f"{base}/billing/invoices/{long_id}/print/", wait_until="networkidle")
            page.pdf(path=str(out / "invoice-long-native.pdf"), format="A4", print_background=True, prefer_css_page_size=True)

            page.goto(base + "/reports/recurring-revenue/", wait_until="networkidle")
            page.wait_for_function("[...document.querySelectorAll('canvas[data-chart]')].every(c => Chart.getChart(c) && c.width > 0 && c.height > 0)")
            page.screenshot(path=str(out / "report-mobile-screen.png"), full_page=True)
            chart_screen = page.locator("canvas[data-chart]").first.evaluate("c => ({width:c.width,height:c.height,box:c.parentElement.getBoundingClientRect().width})")
            page.emulate_media(media="print")
            page.wait_for_timeout(300)
            print_layout_width = page.locator(".dash").evaluate("e => ({width:e.getBoundingClientRect().width, chartWidth:e.querySelector('.chart-box').getBoundingClientRect().width})")
            page.evaluate("""() => {
              window.__chartPrintEvents = [];
              for (const name of ['beforeprint','afterprint']) window.addEventListener(name, () =>
                window.__chartPrintEvents.push({name, boxes:[...document.querySelectorAll('.chart-box')].map(b=>({box:b.clientWidth,canvas:Chart.getChart(b.querySelector('canvas'))?.width,bitmap:b.querySelector('canvas')?.width}))}));
            }""")
            print_state = page.evaluate("""() => ({filter:getComputedStyle(document.querySelector('form#rf')).display,
              header:getComputedStyle(document.querySelector('table thead')).display,
              cell:getComputedStyle(document.querySelector('table td')).display,
              drawer:getComputedStyle(document.querySelector('.docview')).display,
              drawerBackdrop:getComputedStyle(document.querySelector('.doc-bg')).display,
              wrapTransform:getComputedStyle(document.querySelector('.wrap')).transform,
              chartBox:document.querySelector('.chart-box').getBoundingClientRect().toJSON(),
              chart:[...document.querySelectorAll('canvas[data-chart]')].map(c=>({w:c.width,h:c.height,box:c.parentElement.getBoundingClientRect().toJSON()})),
              scrollWidth:document.documentElement.scrollWidth})""")
            page.pdf(path=str(out / "report-mrr-native.pdf"), format="A4", print_background=True, prefer_css_page_size=True)
            chart_after_print = page.evaluate("[...document.querySelectorAll('canvas[data-chart]')].map(c=>({w:c.width,h:c.height,box:c.parentElement.getBoundingClientRect().width}))")
            chart_print_events = page.evaluate("window.__chartPrintEvents")
            page.goto(base + "/reports/", wait_until="networkidle")
            page.emulate_media(media="print")
            page.wait_for_timeout(200)
            page.pdf(path=str(out / "report-revenue-native.pdf"), format="A4", print_background=True, prefer_css_page_size=True)
            browser.close()
    finally:
        server.send_signal(signal.SIGTERM)
        server.wait(timeout=10)

    pdf_names = ("invoice-short-native", "invoice-short-reportlab", "invoice-long-native", "report-mrr-native", "report-revenue-native")
    page_counts, pdf_texts = {}, {}
    for name in pdf_names:
        path = out / f"{name}.pdf"
        info = subprocess.run(["pdfinfo", str(path)], check=True, capture_output=True, text=True).stdout
        pages = re.search(r"^Pages:\s+(\d+)", info, re.MULTILINE).group(1)
        page_counts[name] = int(pages)
        pdf_texts[name] = subprocess.run(["pdftotext", "-layout", str(path), "-"], check=True, capture_output=True, text=True).stdout
        extracted_pages = [page for page in pdf_texts[name].split("\f") if page.strip()]
        if len(extracted_pages) != page_counts[name]:
            raise AssertionError(f"{name} contains an empty page or has no extractable page content")
        print(f"{name}: {pages} page(s), {path.stat().st_size} bytes")
        subprocess.run(["pdftoppm", "-f", "1", "-l", "1", "-scale-to", "1400", "-png", str(path), str(out / name)], check=True, stdout=subprocess.DEVNULL)
    if page_counts["invoice-short-native"] != 1 or page_counts["invoice-short-reportlab"] != 1:
        raise AssertionError("A short invoice should fit on one page in both print paths")
    if page_counts["invoice-long-native"] <= 1 or "Print long line 69" not in pdf_texts["invoice-long-native"]:
        raise AssertionError("Long native invoice did not paginate through its final item")
    long_pages = pdf_texts["invoice-long-native"].split("\f")
    if not any("Print long line 69" in text and re.search(r"Description", text, re.I) for text in long_pages):
        raise AssertionError("Long invoice final page does not repeat its item table heading")
    if not all(word in pdf_texts["report-mrr-native"] for word in ("Month", "Opening", "Expansion", "Closing")):
        raise AssertionError("Recurring revenue PDF is missing a rightmost or interior table column")
    if not all(word in pdf_texts["report-revenue-native"] for word in ("Invoice", "Customer", "Balance", "Status")):
        raise AssertionError("Revenue PDF is missing a rightmost or interior table column")
    for name in ("report-mrr-native", "report-revenue-native"):
        bbox = subprocess.run(["pdftotext", "-bbox", str(out / f"{name}.pdf"), "-"], check=True, capture_output=True, text=True).stdout
        width_match = re.search(r'<page width="([\d.]+)"', bbox)
        x_positions = [float(x) for x in re.findall(r'xMax="([\d.]+)"', bbox)]
        if not width_match or not x_positions or max(x_positions) > float(width_match.group(1)) - 12:
            raise AssertionError(f"{name} has text at or beyond the printable right edge")
    text = pdf_texts["invoice-short-reportlab"]
    if "Received with thanks" not in text or "Payment activity" not in text:
        raise AssertionError("ReportLab short invoice lost expected paid/activity semantics")
    print(f"chart screen metrics: {chart_screen}")
    print(f"report print width: {print_layout_width}")
    print(f"chart after actual print: {chart_after_print}")
    print(f"chart print events: {chart_print_events}")
    print(f"report print metrics: {print_state}")
    before_print = next((event for event in chart_print_events if event["name"] == "beforeprint"), None)
    if print_layout_width["width"] < 1000 or print_layout_width["chartWidth"] < 400:
        raise AssertionError("Report print layout is still using the narrow mobile viewport")
    if not before_print or not before_print["boxes"] or any(abs(box["box"] - box["canvas"]) > 4 or box["bitmap"] < box["canvas"] * 1.9 for box in before_print["boxes"]):
        raise AssertionError("Chart.js canvas was not resized to its physical print parent")
    if print_state["filter"] != "none" or print_state["header"] != "table-header-group" or print_state["cell"] != "table-cell":
        raise AssertionError("Report print CSS did not hide filters or restore table layout")
    if print_state["drawer"] != "none" or print_state["drawerBackdrop"] != "none" or print_state["wrapTransform"] != "none":
        raise AssertionError("Application drawer, backdrop, or transform leaked into report print")
    if not print_state["chart"] or any(c["w"] <= 0 or c["h"] <= 0 for c in print_state["chart"]):
        raise AssertionError("Chart.js canvas has empty print dimensions")
    print(f"PDFs, screenshots and first-page raster previews: {out}")


if __name__ == "__main__":
    main()
