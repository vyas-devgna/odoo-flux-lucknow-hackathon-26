document.getElementById('theme')?.addEventListener('click', () => {
  const t = document.documentElement.dataset.theme === 'dark' ? 'light' : 'dark';
  document.documentElement.dataset.theme = t; localStorage.setItem('theme', t);
});
// Inline formsets: clone the hidden empty form
document.querySelectorAll('[data-add-row]').forEach(btn => btn.addEventListener('click', () => {
  const body = document.querySelector(btn.dataset.addRow), prefix = btn.dataset.prefix;
  const total = document.getElementById(`id_${prefix}-TOTAL_FORMS`);
  const html = document.getElementById('empty-row').innerHTML.replaceAll('__prefix__', total.value);
  body.insertAdjacentHTML('beforeend', html); total.value = +total.value + 1;
  body.lastElementChild.dispatchEvent(new CustomEvent('rowadded', {bubbles: true}));
}));
// Confirm destructive actions
document.addEventListener('submit', e => {
  const m = e.target.dataset.confirm; if (m && !confirm(m)) e.preventDefault();
});
