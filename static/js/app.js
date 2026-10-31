/* Brightloop Studio UI behaviour. Progressive enhancement: every page works without it, this adds speed and polish. */
(() => {
const $ = (s, r = document) => r.querySelector(s), $$ = (s, r = document) => [...r.querySelectorAll(s)];
const reduce = matchMedia('(prefers-reduced-motion: reduce)').matches;
const css = n => getComputedStyle(document.documentElement).getPropertyValue(n).trim();

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
  const r = e.target.closest('[data-href]'); if (r && !e.target.closest('a,button,input,select,label,summary')) { if (window.htmx) htmx.ajax('GET', r.dataset.href, { target: 'body', swap: 'outerHTML', pushURL: true }).catch(() => location.href = r.dataset.href); else location.href = r.dataset.href; }
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

/* ---------- charts (Chart.js) */
const charts = new Map();
function palette() { return { ink: css('--ink'), mute: css('--mute'), line: css('--hair'), accent: css('--accent'), orange: css('--orange'), green: css('--green'), red: css('--red'), violet: css('--violet'), amber: css('--amber'), card: css('--card') }; }
function chartsTheme() { const p = palette(); charts.forEach(c => { if (c.options.scales) Object.values(c.options.scales).forEach(s => { if (s.grid) s.grid.color = p.line; if (s.ticks) s.ticks.color = p.mute; }); c.update('none'); }); }
function initCharts() {
  if (!window.Chart) return;
  Chart.defaults.font.family = getComputedStyle(document.body).fontFamily; Chart.defaults.font.size = 11; Chart.defaults.color = css('--mute');
  $$('canvas[data-chart]:not([data-ready])').forEach(cv => {
    cv.dataset.ready = 1; const cfg = JSON.parse(document.getElementById(cv.dataset.chart).textContent), p = palette();
    const col = n => p[n] || n; const money = v => '₹' + Number(v).toLocaleString('en-IN', { maximumFractionDigits: 0 });
    const compact = v => { v = Math.abs(v) >= 1e7 ? (v / 1e7).toFixed(1) + 'Cr' : Math.abs(v) >= 1e5 ? (v / 1e5).toFixed(1) + 'L' : Math.abs(v) >= 1e3 ? (v / 1e3).toFixed(0) + 'K' : v; return '₹' + v; };
    const datasets = cfg.datasets.map(d => ({ ...d, borderColor: col(d.color), backgroundColor: d.type === 'line' || cfg.type === 'line' ? col(d.color) + '22' : col(d.color), borderRadius: cfg.type === 'bar' ? 5 : 0, borderWidth: cfg.type === 'doughnut' ? 0 : 2, tension: .35, pointRadius: 0, pointHoverRadius: 5, fill: !!d.fill, barPercentage: .7 }));
    if (cfg.type === 'doughnut') datasets.forEach(d => { d.backgroundColor = d.colors.map(col); });
    const c = new Chart(cv, { type: cfg.type, data: { labels: cfg.labels, datasets }, options: {
      responsive: true, maintainAspectRatio: false, animation: reduce ? false : { duration: 700, easing: 'easeOutCubic' }, interaction: { mode: 'index', intersect: false },
      cutout: cfg.type === 'doughnut' ? '72%' : undefined,
      plugins: { legend: { display: !!cfg.legend, position: 'bottom', labels: { usePointStyle: true, boxWidth: 8 } }, tooltip: { backgroundColor: p.ink, titleColor: css('--bg'), bodyColor: css('--bg'), padding: 10, cornerRadius: 8, displayColors: true, callbacks: { label: i => ` ${i.dataset.label ? i.dataset.label + ': ' : ''}${cfg.plain ? i.formattedValue : money(i.parsed.y ?? i.parsed)}` } } },
      scales: cfg.type === 'doughnut' ? {} : { x: { stacked: !!cfg.stacked, grid: { display: false }, ticks: { color: p.mute } }, y: { stacked: !!cfg.stacked, beginAtZero: true, grid: { color: p.line }, border: { display: false }, ticks: { color: p.mute, callback: v => cfg.plain ? v : compact(v), maxTicksLimit: 5 } } } } });
    charts.set(cv, c);
  });
  charts.forEach((c, cv) => { if (!document.body.contains(cv)) { c.destroy(); charts.delete(cv); } });
}

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
    if (e.key === 'Enter' && palItems[palIdx]) { e.preventDefault(); const u = palItems[palIdx].u; palClose(); window.htmx ? htmx.ajax('GET', u, { target: 'body', swap: 'outerHTML', pushURL: true }) : location.href = u; }
    return;
  }
  if (typing || e.ctrlKey || e.metaKey || e.altKey || !pal) return;
  if (e.key === '/') { e.preventDefault(); palOpen(); }
  else if (e.key === '?') $('#help')?.showModal();
  else if (e.key === 'n') location.href = '/subscriptions/new/';
  else if (e.key === 'g') { gPending = Date.now(); }
  else if (gPending && Date.now() - gPending < 1200 && GO[e.key]) { gPending = 0; location.href = GO[e.key]; }
});

/* ---------- init (runs on load and after every boosted navigation) */
function init() { flash(); counters(); initCharts(); document.body.classList.remove('open'); $$('input[autofocus]').forEach(i => i.focus()); }
document.addEventListener('DOMContentLoaded', init);
document.addEventListener('htmx:afterSettle', init);
document.addEventListener('htmx:responseError', () => toast('Something went wrong. Please try again.', 'error'));
document.addEventListener('htmx:sendError', () => toast('Network problem. Check your connection and retry.', 'error'));
window.addEventListener('pageshow', e => { if (e.persisted) init(); });
})();
