/* Brightloop Studio UI behaviour. Progressive enhancement: every page works without it, this adds speed and polish. */
(() => {
const $ = (s, r = document) => r.querySelector(s), $$ = (s, r = document) => [...r.querySelectorAll(s)];
const motion = matchMedia('(prefers-reduced-motion: reduce)');
const reduce = () => motion.matches;
const css = n => getComputedStyle(document.documentElement).getPropertyValue(n).trim();

/* navigate through htmx boost (keeps URL + title in sync) */
function sameOriginUrl(raw) { if (typeof raw !== 'string' || !raw) return null; try { const u = new URL(raw, location.href); return u.origin === location.origin && !u.username && !u.password ? u.href : null; } catch (e) { return null; } }
function go(url) { const href = sameOriginUrl(url); if (!href) return; const a = document.createElement('a'); a.href = href; a.hidden = true; document.body.appendChild(a); if (window.htmx) htmx.process(a); a.click(); setTimeout(() => a.remove(), 0); }

/* ---------- toasts */
function toast(msg, tone = 'info', ms = 5200) {
  const box = $('#toasts'); if (!box) return;
  const el = document.createElement('div'); el.className = `toast ${tone}`; el.setAttribute('role', tone === 'error' ? 'alert' : 'status');
  el.innerHTML = '<span></span><button aria-label="Dismiss">×</button>'; el.firstChild.textContent = msg;
  const close = () => { el.classList.add('out'); setTimeout(() => el.remove(), reduce() ? 0 : 220); };
  el.lastChild.onclick = close; box.appendChild(el); if (ms) setTimeout(close, ms);
}
window.toast = toast;
function flash() { const f = $('#flash'); if (!f) return; try { JSON.parse(f.textContent).forEach(m => toast(m.m, (m.t || 'info').split(' ').pop())); } catch (e) {} f.remove(); }

/* ---------- confirm dialog (replaces window.confirm) */
document.addEventListener('submit', e => {
  const f = e.target, msg = f.dataset.confirm; if (!msg || f.dataset.confirmed) return;
  e.preventDefault(); const d = $('#confirm'); if (!d) { if (confirm(msg)) { f.dataset.confirmed = 1; f.requestSubmit(e.submitter); delete f.dataset.confirmed; } return; }
  $('#confirm-t').textContent = f.dataset.confirmTitle || 'Are you sure?'; $('#confirm-m').textContent = msg;
  const ok = $('#confirm-ok'); ok.className = 'btn ' + (f.dataset.confirmTone === 'danger' ? 'danger solid' : 'primary'); ok.textContent = f.dataset.confirmLabel || 'Confirm';
  d.returnValue = ''; d.showModal();
  d.addEventListener('close', () => { if (d.returnValue === 'yes') { f.dataset.confirmed = 1; f.requestSubmit(e.submitter); delete f.dataset.confirmed; } }, { once: true });
}, true);
document.addEventListener('click', e => { const o = e.target.closest('[data-open]'); if (o) { const d = document.getElementById(o.dataset.open); d && d.showModal(); } });
/* Lock the form before a second submit can reach HTMX; defer disabling so submitter values serialize. */
function unlockSubmit(elt) {
  const f = elt?.matches?.('form') ? elt : elt?.closest?.('form'); if (!f) return;
  delete f.dataset.submitting; delete f.dataset.confirmed;
  $$('[data-submit-locked]', f).forEach(b => { b.disabled = false; b.classList.remove('loading'); delete b.dataset.submitLocked; });
}
document.addEventListener('submit', e => {
  const f = e.target; if (e.defaultPrevented || f.dataset.confirm && !f.dataset.confirmed) return;
  if (f.dataset.submitting) { e.preventDefault(); e.stopImmediatePropagation(); return; }
  f.dataset.submitting = '1'; const b = e.submitter || $('button:not([type=button]),input[type=submit]', f);
  if (b && !b.dataset.noload) { b.dataset.submitLocked = '1'; setTimeout(() => { if (b.isConnected) { b.classList.add('loading'); b.disabled = true; } }, 0); }
}, true);
document.addEventListener('htmx:afterRequest', e => unlockSubmit(e.detail.elt));

/* ---------- theme, menus */
function setMobileMenu(open, restore = false) {
  const burger = $('.burger'); document.body.classList.toggle('open', open);
  if (burger) { burger.setAttribute('aria-expanded', String(open)); burger.setAttribute('aria-label', open ? 'Close menu' : 'Open menu'); if (!open && restore) burger.focus(); }
}
document.addEventListener('click', e => {
  const t = e.target.closest('.seg button[data-t]'); if (t) { document.documentElement.dataset.theme = t.dataset.t; localStorage.setItem('theme', t.dataset.t); }
  $$('details[open]').forEach(d => { if (!d.contains(e.target)) d.removeAttribute('open'); });
  const burger = e.target.closest('.burger');
  if (burger) setMobileMenu(!document.body.classList.contains('open'));
  else if (!e.target.closest('.tabs') || e.target.closest('.tabs a')) setMobileMenu(false);
});
document.addEventListener('toggle', e => { if (e.target.open) $$('details[open]').forEach(d => { if (d !== e.target && !d.contains(e.target)) d.removeAttribute('open'); }); }, true);

/* ---------- formsets, bulk select, rows */
document.addEventListener('click', e => {
  const btn = e.target.closest('[data-add-row]');
  if (btn) { const body = $(btn.dataset.addRow), total = document.getElementById(`id_${btn.dataset.prefix}-TOTAL_FORMS`);
    syncDirty(body.closest('form[data-dirty]'));
    body.insertAdjacentHTML('beforeend', document.getElementById('empty-row').innerHTML.replaceAll('__prefix__', total.value)); total.value = +total.value + 1;
    syncDirty(body.closest('form[data-dirty]')); body.lastElementChild.dispatchEvent(new CustomEvent('rowadded', { bubbles: true })); body.lastElementChild.querySelector('select,input')?.focus(); }
});
document.addEventListener('change', e => {
  const bulk = $('#bulkbar'); if (!bulk) return; const all = $('#cb-all'), boxes = $$('input.cb[name=ids]');
  if (e.target === all) boxes.forEach(b => b.checked = all.checked);
  if (e.target === all || e.target.matches('input.cb')) { const n = boxes.filter(b => b.checked).length; bulk.classList.toggle('show', n > 0); $('#bulkn').textContent = n; }
});

/* ---------- count-up numbers */
function counters() {
  $$('[data-count]:not([data-done])').forEach(el => {
    el.dataset.done = 1; const end = parseFloat(el.dataset.count), dec = +(el.dataset.dec || 0), pre = el.dataset.prefix || '', suf = el.dataset.suffix || '';
    const fmt = v => pre + v.toLocaleString('en-IN', { minimumFractionDigits: dec, maximumFractionDigits: dec }) + suf;
    if (reduce() || !isFinite(end)) { el.textContent = fmt(end); return; }
    const t0 = performance.now(), dur = 200; const tick = t => { if (reduce()) { el.textContent = fmt(end); return; } const p = Math.min((t - t0) / dur, 1), k = 1 - Math.pow(1 - p, 3); el.textContent = fmt(end * k); if (p < 1) requestAnimationFrame(tick); }; requestAnimationFrame(tick);
  });
}

/* ---------- charts (Chart.js) ----------
   Spec: 2px lines, bars <= 24px with 4px data-end radius, 2px surface gaps in stacks, hairline solid grid, text in ink
   tokens (never series colour), one tooltip listing every series with a crosshair, legend for >= 2 series, and a table twin. */
const charts = new Map();
motion.addEventListener('change', e => charts.forEach(v => { if (e.matches) v.chart.stop(); v.chart.options.animation = e.matches ? false : { duration: 200, easing: 'easeOutCubic' }; }));
const tok = n => css('--' + n);
const fmtMoney = v => '₹' + Number(v).toLocaleString('en-IN', { maximumFractionDigits: 0 });
const fmtCompact = v => { const a = Math.abs(v), s = v < 0 ? '−' : ''; return '₹' + s + (a >= 1e7 ? (a / 1e7).toFixed(1) + 'Cr' : a >= 1e5 ? (a / 1e5).toFixed(1) + 'L' : a >= 1e3 ? (a / 1e3).toFixed(0) + 'K' : a); };
const crosshair = { id: 'crosshair', afterDatasetsDraw(c) { const t = c.tooltip; if (c.config.type !== 'line' || !t || !t.getActiveElements().length) return; const x = t.getActiveElements()[0].element.x, { top, bottom } = c.chartArea, g = c.ctx; g.save(); g.strokeStyle = tok('hair'); g.lineWidth = 1; g.beginPath(); g.moveTo(x, top); g.lineTo(x, bottom); g.stroke(); g.restore(); } };
const endDot = { id: 'endDot', afterDatasetsDraw(c) { if (c.config.type !== 'line') return; c.data.datasets.forEach((d, i) => { if (!c.isDatasetVisible(i)) return; const pts = c.getDatasetMeta(i).data, p = pts[pts.length - 1]; if (!p) return; const g = c.ctx; g.save(); g.fillStyle = d.borderColor; g.strokeStyle = tok('card'); g.lineWidth = 2; g.beginPath(); g.arc(p.x, p.y, 5, 0, 7); g.fill(); g.stroke(); g.restore(); }); } };
function externalTooltip(cfg) {
  return ctx => {
    const { chart, tooltip } = ctx; let el = chart.canvas.parentNode.querySelector('.ctip');
    if (!el) { el = document.createElement('div'); el.className = 'ctip'; el.setAttribute('role', 'status'); chart.canvas.parentNode.appendChild(el); }
    if (tooltip.opacity === 0) { el.style.opacity = 0; return; }
    el.replaceChildren(); const t = document.createElement('div'); t.className = 't'; t.textContent = tooltip.title[0]; el.appendChild(t);
    tooltip.dataPoints.forEach(dp => { const r = document.createElement('div'), l = document.createElement('span'), k = document.createElement('i'), v = document.createElement('b'); r.className = 'r'; k.style.setProperty('--k', dp.dataset.borderColor); l.append(k, document.createTextNode(dp.dataset.label)); v.textContent = cfg.plain ? dp.formattedValue : fmtMoney(dp.raw); r.append(l, v); el.appendChild(r); });
    const w = chart.canvas.parentNode.clientWidth, x = tooltip.caretX, ew = el.offsetWidth; el.style.opacity = 1; el.style.left = Math.max(0, Math.min(w - ew, x + 16 > w - ew ? x - ew - 16 : x + 16)) + 'px'; el.style.top = Math.max(0, tooltip.caretY - el.offsetHeight / 2) + 'px';
  };
}
function buildChart(cv) {
  const cfg = JSON.parse(document.getElementById(cv.dataset.chart).textContent), surface = tok('card');
  const col = n => tok(n) || n, line = cfg.type === 'line', bar = cfg.type === 'bar';
  const datasets = cfg.datasets.map(d => { const c = col(d.color); return { label: d.label, data: d.data, borderColor: c, backgroundColor: line ? `color-mix(in srgb, ${c} 10%, transparent)` : c, borderWidth: line ? 2 : (cfg.stacked ? 2 : 0), borderColor_: c,
      ...(line ? { tension: .35, fill: !!d.fill, pointRadius: 0, pointHitRadius: 24, pointHoverRadius: 5, pointHoverBackgroundColor: c, pointHoverBorderColor: surface, pointHoverBorderWidth: 2, borderCapStyle: 'round', borderJoinStyle: 'round' }
      : { maxBarThickness: 24, borderRadius: cfg.stacked ? 0 : { topLeft: 4, topRight: 4 }, borderSkipped: 'bottom', categoryPercentage: .7, barPercentage: .9, ...(cfg.stacked ? { borderColor: surface } : {}) }) }; });
  if (bar && cfg.stacked) datasets.forEach(d => { d.stack = 's'; });
  const tickColor = tok('mute'), grid = tok('line');
  const c = new Chart(cv, { type: cfg.type, data: { labels: cfg.labels, datasets }, options: {
    responsive: true, maintainAspectRatio: false, animation: reduce() ? false : { duration: 200, easing: 'easeOutCubic' }, events: ['mousemove', 'mouseout', 'click', 'touchstart', 'touchmove', 'pointerdown'], interaction: { mode: 'index', intersect: false }, layout: { padding: { right: line ? 8 : 0, top: 4 } },
    plugins: { legend: { display: false }, tooltip: { enabled: false, external: externalTooltip(cfg), mode: 'index', intersect: false, position: 'nearest' } },
    scales: { x: { stacked: !!cfg.stacked, grid: { display: false }, border: { color: grid }, ticks: { color: tickColor, padding: 8, maxRotation: 0, autoSkip: true } },
              y: { stacked: !!cfg.stacked, beginAtZero: true, grid: { color: grid, lineWidth: 1 }, border: { display: false }, ticks: { color: tickColor, padding: 8, maxTicksLimit: 5, callback: v => cfg.plain ? v : fmtCompact(v) } } } }, plugins: [crosshair, endDot] });
  return { chart: c, cfg };
}
function figureExtras(cv, chart, cfg) {
  const fig = cv.closest('figure.chart'); if (!fig || fig.dataset.built) return; fig.dataset.built = 1;
  const cap = fig.querySelector('figcaption'); const many = cfg.datasets.length > 1;
  if (cap && many) { const lg = document.createElement('div'); lg.className = 'legend' + (cfg.type === 'bar' ? ' bars' : ''); lg.setAttribute('role', 'group'); lg.setAttribute('aria-label', 'Series');
    cfg.datasets.forEach((d, i) => { const b = document.createElement('button'); b.type = 'button'; b.setAttribute('aria-pressed', 'true'); const k = document.createElement('i'); k.style.setProperty('--k', tok(d.color)); b.append(k, document.createTextNode(d.label));
      b.onclick = () => { const active = charts.get(cv)?.chart; if (!active) return; const on = active.isDatasetVisible(i); active.setDatasetVisibility(i, !on); b.setAttribute('aria-pressed', String(!on)); active.update(); }; lg.appendChild(b); }); cap.appendChild(lg); }
  const t = document.createElement('div'); t.className = 'chart-table tw'; const tb = document.createElement('table'), hd = document.createElement('tr'); hd.appendChild(document.createElement('th'));
  cfg.datasets.forEach(d => { const th = document.createElement('th'); th.className = 'right'; th.textContent = d.label; hd.appendChild(th); }); const th = document.createElement('thead'); th.appendChild(hd); tb.appendChild(th);
  const body = document.createElement('tbody'); cfg.labels.forEach((lab, r) => { const tr = document.createElement('tr'), td0 = document.createElement('td'); td0.textContent = lab; tr.appendChild(td0); cfg.datasets.forEach(d => { const td = document.createElement('td'); td.className = 'right num'; td.textContent = cfg.plain ? d.data[r] : fmtMoney(d.data[r]); tr.appendChild(td); }); body.appendChild(tr); }); tb.appendChild(body); t.appendChild(tb); fig.appendChild(t);
  const btn = document.createElement('button'); btn.type = 'button'; btn.className = 'chip-btn'; btn.textContent = 'Table'; btn.setAttribute('aria-pressed', 'false'); btn.onclick = () => { const on = fig.classList.toggle('as-table'); btn.setAttribute('aria-pressed', String(on)); btn.textContent = on ? 'Chart' : 'Table'; };
  const right = document.createElement('div'); right.className = 'row-flex'; if (cap) { right.append(...cap.querySelectorAll('.legend')); right.appendChild(btn); cap.appendChild(right); }
}
function initCharts() {
  if (!window.Chart) return;
  Chart.defaults.font.family = getComputedStyle(document.body).fontFamily; Chart.defaults.font.size = 12; Chart.defaults.color = tok('mute');
  $$('canvas[data-chart]:not([data-ready])').forEach(cv => { cv.dataset.ready = 1; cv.style.touchAction = 'pan-y'; const { chart, cfg } = buildChart(cv); charts.set(cv, { chart, cfg }); figureExtras(cv, chart, cfg); });
  charts.forEach((v, cv) => { if (!document.body.contains(cv)) destroyChart(cv); });
}
function destroyChart(cv) { const v = charts.get(cv); if (v) { v.chart.destroy(); charts.delete(cv); } }
document.addEventListener('htmx:beforeCleanupElement', e => { const root = e.detail?.elt; if (!root) return; charts.forEach((v, cv) => { if (root === cv || root.contains?.(cv)) destroyChart(cv); }); });
function chartsTheme() { const cvs = [...charts.keys()]; charts.forEach(v => v.chart.destroy()); charts.clear(); cvs.forEach(cv => { delete cv.dataset.ready; cv.closest('figure.chart')?.querySelector('.ctip')?.remove(); }); initCharts(); }
let observedTheme = document.documentElement.dataset.theme;
new MutationObserver(() => {
  const nextTheme = document.documentElement.dataset.theme;
  if (nextTheme === observedTheme) return;
  observedTheme = nextTheme;
  chartsTheme();
}).observe(document.documentElement, { attributes: true, attributeFilter: ['data-theme'] });

/* ---------- command palette */
let palItems = [], palIdx = 0, palTimer, palRequest = 0, palController, palOpener;
function safePaletteItems(items) { return items.filter(i => sameOriginUrl(i.u)); }
function palOpen() { const pal = $('#pal'); if (!pal) return; palOpener = document.activeElement; pal.classList.add('open'); if ($('.wrap')) $('.wrap').inert = true; const q = $('#palq'); q.value = ''; palBase(); q.focus(); }
function palClose(restore = true) {
  $('#pal')?.classList.remove('open'); clearTimeout(palTimer); palRequest++; palController?.abort(); palController = null;
  if ($('.wrap') && !document.body.classList.contains('doc-open')) $('.wrap').inert = false;
  const opener = palOpener; palOpener = null; if (restore && opener?.isConnected) opener.focus({ preventScroll: true });
}
function palBase() {
  const nav = [...$$('#tabs a'), ...$$('.subnav a')].map(a => ({ t: a.textContent.trim(), u: a.getAttribute('href'), k: 'Go to' }));
  const acts = JSON.parse($('#commands')?.textContent || '[]');
  palItems = safePaletteItems([...acts, ...nav].filter((v, i, a) => a.findIndex(x => x.u === v.u) === i)); palIdx = 0; palRender();
}
function palRender() {
  const l = $('#pall'); if (!l) return; const frag = document.createDocumentFragment(); let last = '';
  palItems.forEach((item, n) => {
    const href = sameOriginUrl(item.u); if (!href) return;
    if (item.k !== last) { const group = document.createElement('li'); group.className = 'grp'; group.textContent = item.k || ''; frag.appendChild(group); last = item.k; }
    const row = document.createElement('li'), a = document.createElement('a'); if (n === palIdx) row.className = 'on'; a.href = href; a.appendChild(document.createTextNode(String(item.t ?? '')));
    if (item.sub) { const small = document.createElement('small'); small.textContent = item.sub; a.appendChild(small); }
    row.appendChild(a); frag.appendChild(row);
  });
  l.replaceChildren(frag); $('.on', l)?.scrollIntoView({ block: 'nearest' });
}
async function palSearch(v, request) {
  const q = v.trim().toLowerCase(); if (request !== palRequest) return;
  if (q.length < 2) { palBase(); return; }
  const local = [...$$('#tabs a'), ...$$('.subnav a')].map(a => ({ t: a.textContent.trim(), u: a.getAttribute('href'), k: 'Go to' })).filter(i => i.t.toLowerCase().includes(q));
  palItems = safePaletteItems(local); palIdx = 0; palRender();
  const controller = new AbortController(); palController = controller;
  try {
    const r = await fetch(`/search/?fmt=json&q=${encodeURIComponent(v)}`, { headers: { Accept: 'application/json' }, signal: controller.signal }); if (!r.ok) throw new Error('Search failed');
    const d = await r.json(); if (request !== palRequest || !$('#pal')?.classList.contains('open')) return;
    const results = Array.isArray(d.results) ? safePaletteItems(d.results.filter(x => x && typeof x === 'object').map(x => ({ ...x, live: 1 }))) : [];
    const selected = palItems[palIdx]?.u;
    palItems = [...results, ...palItems]; palIdx = Math.max(0, palItems.findIndex(item => item.u === selected)); palRender();
  } catch (e) { if (e.name !== 'AbortError' && request === palRequest) return; }
}
document.addEventListener('input', e => {
  if (e.target.id !== 'palq') return; const request = ++palRequest, value = e.target.value;
  clearTimeout(palTimer); palController?.abort(); palController = null; palTimer = setTimeout(() => palSearch(value, request), 120);
});
document.addEventListener('click', e => { if (e.target.closest('#cmdk')) palOpen(); if (e.target.id === 'pal') palClose(); if (e.target.closest('#pall a')) palClose(false); });

/* ---------- keyboard */
let gPending = 0;
const GO = { d: '/dashboard/', s: '/subscriptions/', i: '/billing/invoices/', c: '/customers/', p: '/billing/payments/', r: '/reports/' };
const FOCUSABLE = 'a[href],button:not([disabled]),input:not([disabled]),select:not([disabled]),textarea:not([disabled]),iframe,[tabindex]:not([tabindex="-1"])';
function focusables(root) { return $$(FOCUSABLE, root).filter(el => !el.hidden && !el.closest('[inert]') && el.getAttribute('aria-hidden') !== 'true' && el.getClientRects().length); }
function trapTab(e, root) {
  const items = focusables(root), first = items[0], last = items.at(-1); if (!first) { e.preventDefault(); return; }
  const active = document.activeElement;
  if (e.shiftKey && (active === first || !root.contains(active))) { e.preventDefault(); last.focus(); }
  else if (!e.shiftKey && (active === last || !root.contains(active))) { e.preventDefault(); first.focus(); }
}
document.addEventListener('keydown', e => {
  const pal = $('#pal'), docview = $('#docview');
  if (document.body.classList.contains('doc-open') && docview) {
    if (e.key === 'Escape') { e.preventDefault(); docClose(); }
    else if (e.key === 'Tab') trapTab(e, docview);
    return;
  }
  if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 'k' && pal) { e.preventDefault(); pal.classList.contains('open') ? palClose() : palOpen(); return; }
  if (pal && pal.classList.contains('open')) {
    if (e.key === 'Escape') { e.preventDefault(); palClose(); return; }
    if (e.key === 'Tab') { trapTab(e, pal); return; }
    if (e.key === 'ArrowDown') { palIdx = Math.min(palIdx + 1, palItems.length - 1); palRender(); e.preventDefault(); }
    if (e.key === 'ArrowUp') { palIdx = Math.max(palIdx - 1, 0); palRender(); e.preventDefault(); }
    if (e.key === 'Enter' && palItems[palIdx]) { e.preventDefault(); const u = palItems[palIdx].u; palClose(false); go(u); }
    return;
  }
  if (e.key === 'Escape') {
    if ($('dialog[open]')) return;
    const menu = $$('details[open]').at(-1); if (menu) { e.preventDefault(); menu.open = false; menu.querySelector('summary')?.focus(); return; }
    if (document.body.classList.contains('open')) { e.preventDefault(); setMobileMenu(false, true); return; }
  }
  if ($('dialog[open]')) return;
  const active = document.activeElement, typing = /input|textarea|select/i.test(active?.tagName || '') || active?.isContentEditable;
  if (document.body.classList.contains('open') || typing || e.ctrlKey || e.metaKey || e.altKey || !pal) return;
  if (e.key === '/') { e.preventDefault(); palOpen(); }
  else if (e.key === '?') $('#help')?.showModal();
  else if (e.key === 'n') go('/subscriptions/new/');
  else if (e.key === 'g') { gPending = Date.now(); }
  else if (gPending && Date.now() - gPending < 1200 && GO[e.key]) { gPending = 0; go(GO[e.key]); }
});

/* ---------- document viewer (invoices, quotations, statements) ---------- */
let docOpener = null, docUrl = '', docLoadTimer;
function docFailure() {
  const load = $('#doc-load'), frame = $('#doc-frame'); if (!load || !frame || !document.body.classList.contains('doc-open')) return;
  clearTimeout(docLoadTimer); frame.classList.remove('ready'); load.textContent = 'Preview unavailable. Close this panel and try again.'; load.setAttribute('role', 'alert'); load.classList.remove('hide');
}
function docOpen(a) {
  const dv = $('#docview'); if (!dv) return false; const url = sameOriginUrl(a.href); if (!url) return false;
  const frame = $('#doc-frame'), load = $('#doc-load'), pdf = $('#doc-pdf'), copy = $('#doc-copy'), preview = new URL(url);
  docOpener = a; docUrl = url; $('#doc-title').textContent = a.dataset.docTitle || a.textContent.trim(); load.textContent = 'Loading preview…'; load.setAttribute('role', 'status'); load.classList.remove('hide'); frame.classList.remove('ready'); clearTimeout(docLoadTimer);
  preview.searchParams.set('embed', '1'); frame.onload = () => {
    clearTimeout(docLoadTimer);
    try {
      const inner = frame.contentDocument; if (!inner || inner.location.origin !== location.origin || /^Page not found|^Server Error/i.test(inner.title)) { docFailure(); return; }
      inner.addEventListener('keydown', e => {
        if (!document.body.classList.contains('doc-open')) return;
        if (e.key === 'Escape') { e.preventDefault(); docClose(); }
        else if (e.key === 'Tab') { e.preventDefault(); const outer = focusables(dv).filter(el => el !== frame); (e.shiftKey ? outer.at(-1) : outer[0])?.focus(); }
      }, true);
    } catch (e) { docFailure(); return; }
    frame.classList.add('ready'); load.classList.add('hide');
  };
  frame.onerror = docFailure; frame.src = preview.href; docLoadTimer = setTimeout(docFailure, 15000);
  const pdfUrl = sameOriginUrl(a.dataset.docPdf); if (pdfUrl) { pdf.href = pdfUrl; pdf.hidden = false; } else pdf.hidden = true;
  copy.hidden = false; dv.inert = false; document.body.classList.add('doc-open'); if ($('.wrap')) $('.wrap').inert = true; $('#doc-print')?.focus(); return true;
}
function docClose() {
  const dv = $('#docview'); if (!dv || !document.body.classList.contains('doc-open')) return;
  document.body.classList.remove('doc-open'); dv.inert = true; clearTimeout(docLoadTimer); setTimeout(() => { const f = $('#doc-frame'); if (f && !document.body.classList.contains('doc-open')) f.removeAttribute('src'); }, reduce() ? 0 : 360);
  if ($('.wrap') && !$('#pal')?.classList.contains('open')) $('.wrap').inert = false;
  const opener = docOpener; docOpener = null; if (opener?.isConnected && opener.getClientRects().length) opener.focus({ preventScroll: true }); else $('#main')?.focus({ preventScroll: true });
}
async function copyDocLink() {
  const url = sameOriginUrl(docUrl); if (!url) { toast('This document link cannot be copied.', 'error'); return; }
  const link = new URL(url); link.searchParams.delete('embed');
  try { if (!navigator.clipboard?.writeText) throw new Error('Clipboard unavailable'); await navigator.clipboard.writeText(link.href); toast('Document link copied.', 'success'); }
  catch (e) { toast('Could not copy the document link. Try again or use the PDF link.', 'error'); }
}
document.addEventListener('click', e => {
  const a = e.target.closest('a[data-doc]'); if (a && !e.ctrlKey && !e.metaKey && !e.shiftKey && docOpen(a)) { e.preventDefault(); e.stopPropagation(); $$('details[open]').forEach(d => d.removeAttribute('open')); return; }
  if (e.target.closest('#doc-close') || e.target.id === 'doc-bg') docClose();
  if (e.target.closest('#doc-copy')) copyDocLink();
  if (e.target.closest('#doc-print')) { const f = $('#doc-frame'); try { f.contentWindow.focus(); f.contentWindow.print(); } catch (err) { window.open(f.src.replace(/[?&]embed=1/, ''), '_blank'); } }
}, true);

/* ---------- forms: dirty tracking, Ctrl+S, money adornment, first-field focus ---------- */
const formsetBaselines = new WeakMap();
function formsetTotals(f) { return [...f.querySelectorAll('input[name$="-TOTAL_FORMS"]')].map(el => [el.name, el.value]); }
function syncDirty(f) {
  if (!f?.matches('form[data-dirty]')) return;
  if (!formsetBaselines.has(f)) formsetBaselines.set(f, formsetTotals(f));
  const totalsChanged = JSON.stringify(formsetTotals(f)) !== JSON.stringify(formsetBaselines.get(f));
  const changed = totalsChanged || [...f.elements].some(el => {
    if (!/^(INPUT|SELECT|TEXTAREA)$/.test(el.tagName) || !el.name || el.name === 'csrfmiddlewaretoken' || el.disabled || el.readOnly) return false;
    if (el.type === 'checkbox' || el.type === 'radio') return el.checked !== el.defaultChecked;
    if (el.tagName === 'SELECT') return [...el.options].some(o => o.selected !== o.defaultSelected);
    return el.value !== el.defaultValue;
  });
  f.classList.toggle('is-dirty', changed);
}
document.addEventListener('input', e => syncDirty(e.target.closest('form[data-dirty]')));
document.addEventListener('change', e => syncDirty(e.target.closest('form[data-dirty]')));
document.addEventListener('reset', e => setTimeout(() => syncDirty(e.target), 0));
window.addEventListener('beforeunload', e => { if (document.querySelector('form[data-dirty].is-dirty:not([data-submitting])')) { e.preventDefault(); e.returnValue = ''; } });
document.addEventListener('htmx:beforeRequest', e => { if (e.detail.verb === 'get' && document.querySelector('form[data-dirty].is-dirty:not([data-submitting])') && !confirm('You have unsaved changes. Leave this page?')) e.preventDefault(); });
document.addEventListener('keydown', e => { if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 's') { const f = document.querySelector('form[data-dirty].is-dirty'); if (f) { e.preventDefault(); f.requestSubmit(); } } });
function enhanceForms() {
  const subscribe = $('#subscribe-button');
  if (subscribe && !subscribe.dataset.submitLocked) { const invalid = !!$('#selection-error'); subscribe.disabled = invalid; if (invalid) subscribe.setAttribute('aria-describedby', 'selection-error'); else subscribe.removeAttribute('aria-describedby'); }
  $$('input[data-money]:not([data-wrapped])').forEach(i => { i.dataset.wrapped = 1; const w = document.createElement('span'); w.className = 'money'; i.parentNode.insertBefore(w, i); w.appendChild(i); });
  const f = $('form[data-dirty]'); if (f && !f.dataset.focused) { f.dataset.focused = 1; const first = $('input:not([type=hidden]):not([type=checkbox]):not([type=radio]):not([readonly]), select, textarea', f); if (first && !first.value && matchMedia('(min-width:900px)').matches) first.focus({ preventScroll: true }); }
}
function enhanceScrollableTables() {
  $$('.tw').forEach(region => {
    const table = $('table', region), overflow = table && region.scrollWidth > region.clientWidth;
    if (!overflow) {
      if (region.dataset.a11yScrollRegion) {
        if (region.dataset.a11yTabindex) region.removeAttribute('tabindex');
        if (region.dataset.a11yRole) region.removeAttribute('role');
        if (region.dataset.a11yLabel) region.removeAttribute('aria-label');
        delete region.dataset.a11yScrollRegion; delete region.dataset.a11yTabindex; delete region.dataset.a11yRole; delete region.dataset.a11yLabel;
      }
      return;
    }
    let managed = false;
    if (!region.hasAttribute('tabindex')) { region.tabIndex = 0; region.dataset.a11yTabindex = '1'; managed = true; }
    if (!region.hasAttribute('role')) { region.setAttribute('role', 'region'); region.dataset.a11yRole = '1'; managed = true; }
    if (!region.hasAttribute('aria-label') && !region.hasAttribute('aria-labelledby')) {
      const label = table.caption?.textContent.trim() || table.getAttribute('aria-label') || region.closest('.card')?.querySelector('header h1,header h2,header h3,header h4')?.textContent.trim() || region.closest('figure')?.querySelector('figcaption')?.textContent.trim() || document.querySelector('main h1')?.textContent.trim() || 'Scrollable table';
      region.setAttribute('aria-label', label); region.dataset.a11yLabel = '1'; managed = true;
    }
    if (managed) region.dataset.a11yScrollRegion = '1';
  });
}
let tableResizeFrame;
window.addEventListener('resize', () => { cancelAnimationFrame(tableResizeFrame); tableResizeFrame = requestAnimationFrame(enhanceScrollableTables); });

/* ---------- init (runs on load and after every boosted navigation) */
function init() { enhanceForms(); enhanceScrollableTables(); if (!$('#docview') || $('#docview').inert) document.body.classList.remove('doc-open'); $$('.formset-row [id]:not([aria-label]):not([type=hidden])').forEach(i => { const l = i.closest('td')?.dataset.l; if (l) i.setAttribute('aria-label', l); }); $$('input[id$=-DELETE]:not([aria-label])').forEach(i => i.setAttribute('aria-label', 'Remove row')); flash(); counters(); initCharts(); setMobileMenu(false); $$('input[autofocus]').forEach(i => i.focus()); }
document.addEventListener('DOMContentLoaded', init);
document.addEventListener('htmx:afterSettle', init);
document.addEventListener('htmx:responseError', e => { unlockSubmit(e.detail.elt); toast('Something went wrong. Please try again.', 'error'); });
document.addEventListener('htmx:sendError', e => { unlockSubmit(e.detail.elt); toast('Network problem. Check your connection and retry.', 'error'); });
window.addEventListener('pageshow', e => { if (e.persisted) init(); });
})();
