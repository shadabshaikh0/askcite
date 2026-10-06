// Askcite web page: Test buttons, show/hide fields, confirmations, auto-refresh while syncing.
const csrf = document.querySelector('meta[name="csrf-token"]').content;

function renderResult(box, r) {
  const lines = [...(r.details || []).map(d => `<div class="small">${escapeHtml(d)}</div>`),
                 ...(r.warnings || []).map(w => `<div class="small warn">⚠ ${escapeHtml(w)}</div>`)];
  box.innerHTML = `<div class="result ${r.ok ? 'ok' : 'bad'}"><strong>${r.ok ? '✓' : '✗'} ${escapeHtml(r.message)}</strong>${lines.join('')}</div>`;
}
function escapeHtml(text) {
  return String(text).replace(/[&<>"']/g, c => ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'}[c]));
}
async function post(url, body, box) {
  box.innerHTML = '<div class="small muted">Testing…</div>';
  try {
    const res = await fetch(url, {method: 'POST', body, headers: {'X-CSRF-Token': csrf}});
    renderResult(box, res.ok ? await res.json() : {ok: false, message: `Error ${res.status}`});
  } catch (e) { renderResult(box, {ok: false, message: String(e)}); }
}

// Test from the form (unsaved values)
document.querySelectorAll('.js-test-form').forEach(btn => btn.addEventListener('click', () => {
  const form = document.getElementById('connector-form');
  post('/connectors/test', new FormData(form), document.getElementById('test-result'));
}));
// Test a saved connector
document.querySelectorAll('.js-test-saved').forEach(btn => btn.addEventListener('click', () => {
  const body = new FormData(); body.append('csrf', csrf);
  post(`/connectors/${btn.dataset.id}/test`, body, document.getElementById(`result-${btn.dataset.id}`));
}));
// Fields that only matter for some choices (e.g. SSH key only for "SSH deploy key")
function updateVisibility() {
  document.querySelectorAll('[data-show-if]').forEach(el => {
    const [name, value] = el.dataset.showIf.split('=');
    const control = document.querySelector(`[name="${name}"]`);
    el.hidden = !control || control.value !== value;
  });
}
document.querySelectorAll('select').forEach(s => s.addEventListener('change', updateVisibility));
updateVisibility();
// Confirm before removing
document.querySelectorAll('.js-confirm').forEach(form => form.addEventListener('submit', e => {
  if (!confirm(form.dataset.confirm)) e.preventDefault();
}));
// "Thinking…" while a question runs
document.querySelectorAll('.js-ask').forEach(form => form.addEventListener('submit', () => {
  form.querySelector('button').disabled = true;
  document.querySelector('.js-thinking').hidden = false;
}));
// Refresh the connectors page while a sync is running
const head = document.querySelector('[data-running]');
if (head && head.dataset.running === '1') setTimeout(() => location.replace('/connectors'), 4000);
