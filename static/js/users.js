(() => {
  let roles = [];
  async function load() {
    const d = await api('/api/users'); roles = d.roles;
    $('#nR').innerHTML = roles.map(r => `<option ${r === 'operator' ? 'selected' : ''}>${r}</option>`).join('');
    $('#uRows').innerHTML = d.users.map(u => `<tr data-id="${u.id}"><td>${esc(u.username)}</td><td><input value="${esc(u.full_name)}" data-f="full_name"></td>
      <td><select data-f="role">${roles.map(r => `<option ${r === u.role ? 'selected' : ''}>${r}</option>`).join('')}</select></td>
      <td><input type="checkbox" data-f="active" ${u.active ? 'checked' : ''}></td><td>${esc((u.last_login || '-').replace('T', ' '))}</td>
      <td><button class="btn" data-a="save">Save</button> <button class="btn" data-a="pw">Reset password</button> <button class="btn danger" data-a="del">Delete</button></td></tr>`).join('');
  }
  $('#uRows').onclick = e => {
    const b = e.target.closest('button'); if (!b) return; const tr = b.closest('tr'), id = tr.dataset.id, a = b.dataset.a;
    if (a === 'save') guard(async () => { await api('/api/users/' + id, 'PUT', { full_name: tr.querySelector('[data-f=full_name]').value, role: tr.querySelector('[data-f=role]').value, active: tr.querySelector('[data-f=active]').checked }); await load(); }, 'User updated');
    if (a === 'pw') { const p = prompt('New password (min 6 characters):'); if (p) guard(() => api('/api/users/' + id, 'PUT', { password: p }), 'Password reset'); }
    if (a === 'del' && confirm('Delete this user?')) guard(async () => { await api('/api/users/' + id, 'DELETE'); await load(); }, 'User deleted');
  };
  $('#addUser').onclick = () => guard(async () => { await api('/api/users', 'POST', { username: $('#nU').value, full_name: $('#nN').value, password: $('#nP').value, role: $('#nR').value }); $('#nU').value = $('#nN').value = $('#nP').value = ''; await load(); }, 'User created');
  load();
})();
