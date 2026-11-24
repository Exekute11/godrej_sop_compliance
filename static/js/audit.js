(async () => {
  const { audit } = await api('/api/audit');
  $('#aRows').innerHTML = audit.map(a => `<tr><td>${esc(a.ts.replace('T', ' '))}</td><td>${esc(a.username)}</td><td><span class="tag">${esc(a.action)}</span></td><td>${esc(a.details)}</td><td>${esc(a.ip)}</td></tr>`).join('') || '<tr><td colspan="5">No entries.</td></tr>';
})();
