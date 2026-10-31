/* Brightloop Studio UI behaviour. Progressive enhancement: every page works without it, this adds speed and polish. */
(() => {
const $ = (s, r = document) => r.querySelector(s), $$ = (s, r = document) => [...r.querySelectorAll(s)];
const reduce = matchMedia('(prefers-reduced-motion: reduce)').matches;
const css = n => getComputedStyle(document.documentElement).getPropertyValue(n).trim();

/* navigate through htmx boost (keeps URL + title in sync) */
function go(url) { const a = document.createElement('a'); a.href = url; a.hidden = true; document.body.appendChild(a); if (window.htmx) htmx.process(a); a.click(); setTimeout(() => a.remove(), 0); }

/* ---------- toasts */
function toast(msg, tone = 'info', ms = 5200) {
  const box = $('#toasts'); if (!box) return;
  const el = document.createElement('div'); el.className = `toast ${tone}`; el.setAttribute('role', tone === 'error' ? 'alert' : 'status');
  el.innerHTML = '<span></span><button aria-label="Dismiss">×</button>'; el.firstChild.textContent = msg;
  const close = () => { el.classList.add('out'); setTimeout(() => el.remove(), 220); };
  el.lastChild.onclick = close; box.appendChild(el); if (ms) setTimeout(close, ms);
}
window.toast = toast;
function flash() { const f = $('#flash'); if (!f) return; try { JSON.parse(f.textContent).forEach(m => toast(m.m, (m.t || 'info').split(' ').pop())); } catch (e) {} f.remove(); }

/* ---------- confirm dialog (replaces window.confirm) */
document.addEventListener('submit', e => {
  const f = e.target, msg = f.dataset.confirm; if (!msg || f.dataset.confirmed) return;
  e.preventDefault(); const d = $('#confirm'); if (!d) { if (confirm(msg)) { f.dataset.confirmed = 1; f.requestSubmit(); } return; }
  $('#confirm-t').textContent = f.dataset.confirmTitle || 'Are you sure?'; $('#confirm-m').textContent = msg;
  const ok = $('#confirm-ok'); ok.className = 'btn ' + (f.dataset.confirmTone === 'danger' ? 'danger solid' : 'primary'); ok.textContent = f.dataset.confirmLabel || 'Confirm';
  d.returnValue = ''; d.showModal();
  d.addEventListener('close', () => { if (d.returnValue === 'yes') { f.dataset.confirmed = 1; f.requestSubmit(e.submitter); } }, { once: true });
}, true);
document.addEventListener('click', e => { const o = e.target.closest('[data-open]'); if (o) { const d = document.getElementById(o.dataset.open); d && d.showModal(); } });
/* disable submit buttons on submit: no double posts, visible progress */
document.addEventListener('submit', e => { if (e.defaultPrevented) return; const b = e.submitter || $('button:not([type=button])', e.target); if (b && !b.dataset.noload) { setTimeout(() => b.classList.add('loading'), 0); } });

/* ---------- theme, menus */
document.addEventListener('click', e => {
  const t = e.target.closest('.seg button[data-t]'); if (t) { document.documentElement.dataset.theme = t.dataset.t; localStorage.setItem('theme', t.dataset.t); chartsTheme(); }
  $$('details[open]').forEach(d => { if (!d.contains(e.target)) d.removeAttribute('open'); });
  if (!e.target.closest('.tabs') && !e.target.closest('.burger')) document.body.classList.remove('open');
  if (e.target.closest('.tabs a')) document.body.classList.remove('open');
});
document.addEventListener('toggle', e => { if (e.target.open) $$('details[open]').forEach(d => { if (d !== e.target && !d.contains(e.target)) d.removeAttribute('open'); }); }, true);

/* ---------- formsets, bulk select, rows */
document.addEventListener('click', e => {
  const btn = e.target.closest('[data-add-row]');
  if (btn) { const body = $(btn.dataset.addRow), total = document.getElementById(`id_${btn.dataset.prefix}-TOTAL_FORMS`);
    body.insertAdjacentHTML('beforeend', document.getElementById('empty-row').innerHTML.replaceAll('__prefix__', total.value)); total.value = +total.value + 1;
    body.lastElementChild.dispatchEvent(new CustomEvent('rowadded', { bubbles: true })); body.lastElementChild.querySelector('select,input')?.focus(); }
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
    if (reduce || !isFinite(end)) { el.textContent = fmt(end); return; }
    const t0 = performance.now(), dur = 700; const tick = t => { const p = Math.min((t - t0) / dur, 1), k = 1 - Math.pow(1 - p, 3); el.textContent = fmt(end * k); if (p < 1) requestAnimationFrame(tick); }; requestAnimationFrame(tick);
  });
}

/* ---------- charts (Chart.js) ----------
   Spec: 2px lines, bars <= 24px with 4px data-end radius, 2px surface gaps in stacks, hairline solid grid, text in ink
   tokens (never series colour), one tooltip listing every series with a crosshair, legend for >= 2 series, and a table twin. */
const charts = new Map();
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
    ...(line ? { tension: .35, fill: !!d.fill, pointRadius: 0, pointHoverRadius: 5, pointHoverBackgroundColor: c, pointHoverBorderColor: surface, pointHoverBorderWidth: 2, borderCapStyle: 'round', borderJoinStyle: 'round' }
      : { maxBarThickness: 24, borderRadius: cfg.stacked ? 0 : { topLeft: 4, topRight: 4 }, borderSkipped: 'bottom', categoryPercentage: .7, barPercentage: .9, ...(cfg.stacked ? { borderColor: surface } : {}) }) }; });
  if (bar && cfg.stacked) datasets.forEach(d => { d.stack = 's'; });
  const tickColor = tok('mute'), grid = tok('line');
  const c = new Chart(cv, { type: cfg.type, data: { labels: cfg.labels, datasets }, options: {
    responsive: true, maintainAspectRatio: false, animation: reduce ? false : { duration: 600, easing: 'easeOutCubic' }, interaction: { mode: 'index', intersect: false }, layout: { padding: { right: line ? 8 : 0, top: 4 } },
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
      b.onclick = () => { const on = chart.isDatasetVisible(i); chart.setDatasetVisibility(i, !on); b.setAttribute('aria-pressed', String(!on)); chart.update(); }; lg.appendChild(b); }); cap.appendChild(lg); }
  const t = document.createElement('div'); t.className = 'chart-table tw'; const tb = document.createElement('table'), hd = document.createElement('tr'); hd.appendChild(document.createElement('th'));
  cfg.datasets.forEach(d => { const th = document.createElement('th'); th.className = 'right'; th.textContent = d.label; hd.appendChild(th); }); const th = document.createElement('thead'); th.appendChild(hd); tb.appendChild(th);
  const body = document.createElement('tbody'); cfg.labels.forEach((lab, r) => { const tr = document.createElement('tr'), td0 = document.createElement('td'); td0.textContent = lab; tr.appendChild(td0); cfg.datasets.forEach(d => { const td = document.createElement('td'); td.className = 'right num'; td.textContent = cfg.plain ? d.data[r] : fmtMoney(d.data[r]); tr.appendChild(td); }); body.appendChild(tr); }); tb.appendChild(body); t.appendChild(tb); fig.appendChild(t);
  const btn = document.createElement('button'); btn.type = 'button'; btn.className = 'chip-btn'; btn.textContent = 'Table'; btn.setAttribute('aria-pressed', 'false'); btn.onclick = () => { const on = fig.classList.toggle('as-table'); btn.setAttribute('aria-pressed', String(on)); btn.textContent = on ? 'Chart' : 'Table'; };
  const right = document.createElement('div'); right.className = 'row-flex'; if (cap) { right.append(...cap.querySelectorAll('.legend')); right.appendChild(btn); cap.appendChild(right); }
}
function initCharts() {
  if (!window.Chart) return;
  Chart.defaults.font.family = getComputedStyle(document.body).fontFamily; Chart.defaults.font.size = 12; Chart.defaults.color = tok('mute');
  $$('canvas[data-chart]:not([data-ready])').forEach(cv => { cv.dataset.ready = 1; const { chart, cfg } = buildChart(cv); charts.set(cv, { chart, cfg }); figureExtras(cv, chart, cfg); });
  charts.forEach((v, cv) => { if (!document.body.contains(cv)) { v.chart.destroy(); charts.delete(cv); } });
}
function chartsTheme() { const cvs = [...charts.keys()]; charts.forEach(v => v.chart.destroy()); charts.clear(); cvs.forEach(cv => { delete cv.dataset.ready; cv.closest('figure.chart')?.querySelector('.ctip')?.remove(); }); initCharts(); }

/* ---------- command palette */
let palItems = [], palIdx = 0, palTimer;
function palOpen() { const pal = $('#pal'); if (!pal) return; pal.classList.add('open'); const q = $('#palq'); q.value = ''; palBase(); q.focus(); }
function palClose() { $('#pal')?.classList.remove('open'); }
function palBase() {
  const nav = [...$$('#tabs a'), ...$$('.subnav a')].map(a => ({ t: a.textContent.trim(), u: a.getAttribute('href'), k: 'Go to' }));
  const acts = [{ t: 'New subscription', u: '/subscriptions/new/', k: 'Create' }, { t: 'New customer', u: '/customers/new/', k: 'Create' }, { t: 'Run billing', u: '/billing/runs/', k: 'Action' }, { t: 'Overdue invoices', u: '/billing/invoices/?status=overdue', k: 'View' }, { t: 'Draft invoices', u: '/billing/invoices/?status=draft', k: 'View' }, { t: 'Payments awaiting confirmation', u: '/billing/payments/?status=pending', k: 'View' }];
  palItems = [...acts, ...nav].filter((v, i, a) => a.findIndex(x => x.u === v.u) === i); palIdx = 0; palRender();
}
function palRender() { const l = $('#pall'); if (!l) return; let last = '';
  l.innerHTML = palItems.map((i, n) => `${i.k !== last ? `<li class="grp">${last = i.k}</li>` : ''}<li class="${n === palIdx ? 'on' : ''}"><a href="${i.u}">${i.t.replace(/</g, '&lt;')}<small>${i.sub || ''}</small></a></li>`).join('');
  $('.on', l)?.scrollIntoView({ block: 'nearest' }); }
async function palSearch(v) {
  const q = v.trim().toLowerCase(); const base = palItems.filter(i => !i.live);
  if (q.length < 2) { palBase(); return; }
  const local = [...$$('#tabs a'), ...$$('.subnav a')].map(a => ({ t: a.textContent.trim(), u: a.getAttribute('href'), k: 'Go to' })).filter(i => i.t.toLowerCase().includes(q));
  palItems = local; palIdx = 0; palRender();
  try { const r = await fetch(`/search/?fmt=json&q=${encodeURIComponent(v)}`, { headers: { Accept: 'application/json' } }); const d = await r.json();
    if ($('#palq').value !== v) return; palItems = [...d.results.map(x => ({ ...x, live: 1 })), ...local]; palIdx = 0; palRender(); } catch (e) {}
}
document.addEventListener('input', e => { if (e.target.id === 'palq') { clearTimeout(palTimer); palTimer = setTimeout(() => palSearch(e.target.value), 120); } });
document.addEventListener('click', e => { if (e.target.closest('#cmdk')) palOpen(); if (e.target.id === 'pal') palClose(); if (e.target.closest('#pall a')) palClose(); });

/* ---------- keyboard */
let gPending = 0;
const GO = { d: '/dashboard/', s: '/subscriptions/', i: '/billing/invoices/', c: '/customers/', p: '/billing/payments/', r: '/reports/' };
document.addEventListener('keydown', e => {
  const typing = /input|textarea|select/i.test(document.activeElement.tagName) || document.activeElement.isContentEditable; const pal = $('#pal');
  if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 'k' && pal) { e.preventDefault(); pal.classList.contains('open') ? palClose() : palOpen(); return; }
  if (pal && pal.classList.contains('open')) {
    if (e.key === 'Escape') palClose();
    if (e.key === 'ArrowDown') { palIdx = Math.min(palIdx + 1, palItems.length - 1); palRender(); e.preventDefault(); }
    if (e.key === 'ArrowUp') { palIdx = Math.max(palIdx - 1, 0); palRender(); e.preventDefault(); }
    if (e.key === 'Enter' && palItems[palIdx]) { e.preventDefault(); const u = palItems[palIdx].u; palClose(); go(u); }
    return;
  }
  if (typing || e.ctrlKey || e.metaKey || e.altKey || !pal) return;
  if (e.key === '/') { e.preventDefault(); palOpen(); }
  else if (e.key === '?') $('#help')?.showModal();
  else if (e.key === 'n') go('/subscriptions/new/');
  else if (e.key === 'g') { gPending = Date.now(); }
  else if (gPending && Date.now() - gPending < 1200 && GO[e.key]) { gPending = 0; go(GO[e.key]); }
});

document.addEventListener('keydown', e => { if (e.key === 'Escape') $$('details[open]').forEach(d => { d.removeAttribute('open'); d.querySelector('summary')?.focus(); }); });

/* ---------- document viewer (invoices, quotations, statements) ---------- */
let docOpener = null;
function docOpen(a) {
  const dv = $('#docview'); if (!dv) return false; const url = a.getAttribute('href'), frame = $('#doc-frame'), load = $('#doc-load'), pdf = $('#doc-pdf');
  docOpener = a; $('#doc-title').textContent = a.dataset.docTitle || a.textContent.trim(); load.classList.remove('hide'); frame.classList.remove('ready');
  frame.onload = () => { frame.classList.add('ready'); load.classList.add('hide'); }; frame.src = url + (url.includes('?') ? '&' : '?') + 'embed=1';
  if (a.dataset.docPdf) { pdf.href = a.dataset.docPdf; pdf.hidden = false; } else pdf.hidden = true;
  dv.inert = false; document.body.classList.add('doc-open'); setTimeout(() => $('#doc-print')?.focus(), 60); return true;
}
function docClose() { const dv = $('#docview'); if (!dv || !document.body.classList.contains('doc-open')) return; document.body.classList.remove('doc-open'); dv.inert = true; setTimeout(() => { const f = $('#doc-frame'); if (f && !document.body.classList.contains('doc-open')) f.removeAttribute('src'); }, 360); docOpener?.focus?.(); }
document.addEventListener('click', e => {
  const a = e.target.closest('a[data-doc]'); if (a && !e.ctrlKey && !e.metaKey && !e.shiftKey && docOpen(a)) { e.preventDefault(); e.stopPropagation(); $$('details[open]').forEach(d => d.removeAttribute('open')); return; }
  if (e.target.closest('#doc-close') || e.target.id === 'doc-bg') docClose();
  if (e.target.closest('#doc-print')) { const f = $('#doc-frame'); try { f.contentWindow.focus(); f.contentWindow.print(); } catch (err) { window.open(f.src.replace(/[?&]embed=1/, ''), '_blank'); } }
}, true);
document.addEventListener('keydown', e => { if (e.key === 'Escape' && document.body.classList.contains('doc-open')) { e.preventDefault(); docClose(); } });

/* ---------- forms: dirty tracking, Ctrl+S, money adornment, first-field focus ---------- */
let submitting = false;
document.addEventListener('input', e => { const f = e.target.closest('form[data-dirty]'); if (f) f.classList.add('is-dirty'); });
document.addEventListener('submit', () => { submitting = true; setTimeout(() => submitting = false, 4000); });
window.addEventListener('beforeunload', e => { if (!submitting && document.querySelector('form[data-dirty].is-dirty')) { e.preventDefault(); e.returnValue = ''; } });
document.addEventListener('htmx:beforeRequest', e => { if (!submitting && document.querySelector('form[data-dirty].is-dirty') && e.detail.verb === 'get' && !confirm('You have unsaved changes. Leave this page?')) e.preventDefault(); });
document.addEventListener('keydown', e => { if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 's') { const f = document.querySelector('form[data-dirty]'); if (f) { e.preventDefault(); f.requestSubmit(); } } });
function enhanceForms() {
  $$('input[data-money]:not([data-wrapped])').forEach(i => { i.dataset.wrapped = 1; const w = document.createElement('span'); w.className = 'money'; i.parentNode.insertBefore(w, i); w.appendChild(i); });
  const f = $('form[data-dirty]'); if (f && !f.dataset.focused) { f.dataset.focused = 1; const first = $('input:not([type=hidden]):not([type=checkbox]):not([type=radio]):not([readonly]), select, textarea', f); if (first && !first.value && matchMedia('(min-width:900px)').matches) first.focus({ preventScroll: true }); }
}

/* ---------- init (runs on load and after every boosted navigation) */
function init() { enhanceForms(); if (!$('#docview') || $('#docview').inert) document.body.classList.remove('doc-open'); $$('.formset-row [id]:not([aria-label]):not([type=hidden])').forEach(i => { const l = i.closest('td')?.dataset.l; if (l) i.setAttribute('aria-label', l); }); $$('input[id$=-DELETE]:not([aria-label])').forEach(i => i.setAttribute('aria-label', 'Remove row')); flash(); counters(); initCharts(); document.body.classList.remove('open'); $$('input[autofocus]').forEach(i => i.focus()); }
document.addEventListener('DOMContentLoaded', init);
document.addEventListener('htmx:afterSettle', init);
document.addEventListener('htmx:responseError', () => toast('Something went wrong. Please try again.', 'error'));
document.addEventListener('htmx:sendError', () => toast('Network problem. Check your connection and retry.', 'error'));
window.addEventListener('pageshow', e => { if (e.persisted) init(); });
})();
