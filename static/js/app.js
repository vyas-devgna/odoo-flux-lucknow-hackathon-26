const $ = (s, r = document) => r.querySelector(s), $$ = (s, r = document) => [...r.querySelectorAll(s)];
// theme
$$('.seg button').forEach(b => b.addEventListener('click', () => { document.documentElement.dataset.theme = b.dataset.t; localStorage.setItem('theme', b.dataset.t); }));
// dropdowns: one open at a time, close on outside click / Esc
document.addEventListener('click', e => { $$('details[open]').forEach(d => { if (!d.contains(e.target)) d.removeAttribute('open'); }); if (!e.target.closest('.tabs') && !e.target.closest('.burger')) document.body.classList.remove('open'); });
document.addEventListener('toggle', e => { if (e.target.open) $$('details[open]').forEach(d => { if (d !== e.target && !d.contains(e.target)) d.removeAttribute('open'); }); }, true);
// inline formsets
$$('[data-add-row]').forEach(btn => btn.addEventListener('click', () => {
  const body = $(btn.dataset.addRow), total = document.getElementById(`id_${btn.dataset.prefix}-TOTAL_FORMS`);
  body.insertAdjacentHTML('beforeend', document.getElementById('empty-row').innerHTML.replaceAll('__prefix__', total.value));
  total.value = +total.value + 1; body.lastElementChild.dispatchEvent(new CustomEvent('rowadded', { bubbles: true }));
}));
document.addEventListener('submit', e => { const m = e.target.dataset.confirm; if (m && !confirm(m)) e.preventDefault(); });
// command palette
const pal = $('#pal');
if (pal) {
  const q = $('#palq'), list = $('#pall');
  const links = [...$$('#tabs a[href]:not([href="#"]), #tabs .pop a[href]:not([href="#"])')].map(a => ({ t: a.textContent.trim(), u: a.getAttribute('href'), k: 'Go to' }));
  const actions = [{ t: 'New subscription', u: '/subscriptions/new/', k: 'Create' }, { t: 'New customer', u: '/customers/new/', k: 'Create' }, { t: 'Run billing', u: '/billing/runs/', k: 'Action' }, { t: 'Overdue invoices', u: '/billing/invoices/?status=overdue', k: 'View' }, { t: 'Draft invoices', u: '/billing/invoices/?status=draft', k: 'View' }];
  let items = [], idx = 0;
  const render = () => { list.innerHTML = items.map((i, n) => `<li class="${n === idx ? 'on' : ''}"><a href="${i.u}">${i.t}<small>${i.k}</small></a></li>`).join(''); };
  const refresh = () => { const v = q.value.trim().toLowerCase(); items = [...actions, ...links].filter(i => !v || i.t.toLowerCase().includes(v)); if (v.length > 1) items.push({ t: `Search “${q.value.trim()}”`, u: `/search/?q=${encodeURIComponent(q.value.trim())}`, k: 'Search' }); idx = 0; render(); };
  const open = () => { pal.classList.add('open'); q.value = ''; refresh(); q.focus(); }, close = () => pal.classList.remove('open');
  $('#cmdk').addEventListener('click', open);
  document.addEventListener('keydown', e => {
    if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 'k') { e.preventDefault(); pal.classList.contains('open') ? close() : open(); }
    else if (e.key === '/' && !/input|textarea|select/i.test(document.activeElement.tagName)) { e.preventDefault(); open(); }
    else if (e.key === 'Escape') close();
    else if (pal.classList.contains('open')) {
      if (e.key === 'ArrowDown') { idx = Math.min(idx + 1, items.length - 1); render(); e.preventDefault(); }
      if (e.key === 'ArrowUp') { idx = Math.max(idx - 1, 0); render(); e.preventDefault(); }
      if (e.key === 'Enter' && items[idx]) location.href = items[idx].u;
    }
  });
  q.addEventListener('input', refresh); pal.addEventListener('click', e => { if (e.target === pal) close(); });
}
// bulk selection on list tables
const bulk = $('#bulkbar');
if (bulk) {
  const all = $('#cb-all'), boxes = () => $$('input.cb[name=ids]');
  const sync = () => { const n = boxes().filter(b => b.checked).length; bulk.classList.toggle('show', n > 0); $('#bulkn').textContent = n; };
  document.addEventListener('change', e => { if (e.target === all) boxes().forEach(b => b.checked = all.checked); if (e.target.matches('input.cb')) sync(); sync(); });
}
// clickable rows
document.addEventListener('click', e => { const r = e.target.closest('[data-href]'); if (r && !e.target.closest('a,button,input,select,label')) location.href = r.dataset.href; });
