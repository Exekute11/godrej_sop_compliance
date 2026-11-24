(() => {
  let S;
  async function load() {
    S = await api('/api/settings');
    for (const [k, v] of Object.entries(S.sop)) { const i = $(`#sopForm [name=${k}]`); if (i) i.value = v; }
    $('#aSound').checked = S.alerts.sound_enabled; $('#aShot').checked = S.alerts.auto_screenshot;
    $('#aVol').value = S.alerts.sound_volume; $('#volVal').textContent = Math.round(S.alerts.sound_volume * 100) + '%';
    $$('#violChecks input').forEach(c => c.checked = S.alerts.enabled_violations.includes(c.value));
    if (window.ROLE === 'admin') {
      for (const [k, v] of Object.entries(S.detector)) { const i = $(`#detForm [name=${k}]`); if (i) i.value = v; }
      $('#camRows').innerHTML = S.cameras.map(c => `<tr><td>${esc(c.id)}</td><td>${esc(c.name)}</td><td>${esc(c.source)}</td><td><span class="tag ${c.online ? 'pass' : 'fail'}">${c.online ? 'ONLINE' : 'OFFLINE'}</span></td><td><button class="btn" data-edit="${esc(c.id)}">Edit</button> <button class="btn danger" data-del="${esc(c.id)}">Delete</button></td></tr>`).join('');
    }
  }
  const val = n => Number($(`#sopForm [name=${n}]`).value);
  $('#saveSop').onclick = () => guard(() => api('/api/settings', 'PUT', { sop: { dwell_ms: val('dwell_ms'), assembly_timeout_s: val('assembly_timeout_s'), idle_threshold_s: val('idle_threshold_s'), fail_hold_s: val('fail_hold_s'), pass_hold_s: val('pass_hold_s') } }), 'Thresholds saved');
  $('#saveAlerts').onclick = () => guard(() => api('/api/settings', 'PUT', { alerts: { sound_enabled: $('#aSound').checked, sound_volume: Number($('#aVol').value), auto_screenshot: $('#aShot').checked, enabled_violations: $$('#violChecks input:checked').map(c => c.value) } }), 'Alert settings saved');
  $('#aVol').oninput = e => { $('#volVal').textContent = Math.round(e.target.value * 100) + '%'; };
  $('#testSound').onclick = () => { Beep.enabled = true; Beep.volume = Number($('#aVol').value); Beep.play(); };
  $('#btnPw').onclick = () => guard(async () => { await api('/api/account/password', 'POST', { current: $('#pwCur').value, new: $('#pwNew').value }); $('#pwCur').value = $('#pwNew').value = ''; }, 'Password changed');
  if (window.ROLE === 'admin') {
    $('#saveDet').onclick = () => guard(() => api('/api/settings', 'PUT', { detector: { backend: $('#detForm [name=backend]').value, target_fps: Number($('#detForm [name=target_fps]').value), component_conf: Number($('#detForm [name=component_conf]').value) } }), 'Detector settings saved');
    $('#addCam').onclick = () => guard(async () => { await api('/api/cameras', 'POST', { id: $('#cId').value, name: $('#cName').value, source: $('#cSrc').value }); await load(); }, 'Camera saved');
    $('#camRows').onclick = e => {
      const d = e.target.closest('[data-del]'), ed = e.target.closest('[data-edit]');
      if (d && confirm('Delete camera ' + d.dataset.del + '?')) guard(async () => { await api('/api/cameras/' + d.dataset.del, 'DELETE'); await load(); }, 'Camera deleted');
      if (ed) { const c = S.cameras.find(x => x.id === ed.dataset.edit); $('#cId').value = c.id; $('#cName').value = c.name; $('#cSrc').value = c.source; }
    };
  }
  load();
})();
