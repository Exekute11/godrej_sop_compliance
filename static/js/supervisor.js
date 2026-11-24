(() => {
  const L = window.EVENT_LABELS;
  async function load() {
    const d = await api('/api/supervisor/overview'), s = d.shift;
    $('#supKpis').innerHTML = [['Cycles', s.cycles, 'this shift'], ['Pass Rate', s.pass_rate + '%', `${s.passed} pass / ${s.failed} fail`], ['Avg Cycle Time', s.avg_cycle_time + 's', 'passed cycles'], ['Violations', s.violations, 'this shift'], ['Cameras Online', d.cameras.filter(c => c.online).length + '/' + d.cameras.length, 'stations']]
      .map(([k, v, e], i) => `<article class="kpi ${k === 'Violations' && v > 0 ? 'alert' : ''}"><span>${k}</span><h2>${v}</h2><em>${e}</em></article>`).join('');
    $('#camCards').innerHTML = d.cameras.map(c => {
      const sn = c.snapshot, fail = sn && sn.state === 'CYCLE_FAILED';
      return `<div class="cam-card ${fail ? 'fail' : ''}"><h5>${esc(c.name)}</h5><span class="tag ${c.online ? 'pass' : 'fail'}">${c.online ? 'ONLINE' : 'OFFLINE'}</span>
      ${c.enabled && c.online ? `<img src="/video_feed/${encodeURIComponent(c.id)}" alt="">` : ''}
      <small><b>${sn ? esc(sn.stage_label) : '-'}</b></small><small>Cycles ${c.shift.cycles} &middot; Pass ${c.shift.pass_rate}% &middot; Violations ${c.shift.violations}</small>
      <small>Idle ${sn ? Math.round(sn.counters.idle_time) : 0}s &middot; ${esc(c.hand_backend || '')}</small></div>`;
    }).join('');
    $('#recent').innerHTML = d.recent_alerts.length ? d.recent_alerts.map(e => `<li class="alert"><i></i><div class="grow-t"><b>${esc(e.label)}</b><small>${esc(e.camera_id)} &middot; ${esc(e.ts.replace('T', ' '))}</small></div>${e.screenshot ? `<a class="shot" href="/api/screenshots/${esc(e.screenshot)}" target="_blank">snapshot</a>` : ''}</li>`).join('') : '<li class="empty">No violations.</li>';
    $('#rcCount').textContent = d.recent_alerts.length;
    $('#shiftRows').innerHTML = d.shifts.map(x => `<tr><td>${x.id}</td><td>${esc(x.start_ts.replace('T', ' '))}</td><td>${esc((x.end_ts || '').replace('T', ' ')) || '-'}</td><td>${esc(x.started_by)}</td><td><span class="tag ${x.end_ts ? '' : 'pass'}">${x.end_ts ? 'CLOSED' : 'ACTIVE'}</span></td></tr>`).join('');
  }
  $('#btnReset').onclick = () => { if (confirm('End the current shift and reset all counters?')) guard(async () => { await api('/api/shift/reset', 'POST'); await load(); }, 'Shift reset'); };
  load(); setInterval(load, 3000);
})();
