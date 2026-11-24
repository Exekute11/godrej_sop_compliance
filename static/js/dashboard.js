(() => {
  const POLL_MS = 500;
  let cam = null,
    lastId = 0,
    lastAlertSeq = null,
    seen = new Set(),
    polling = false,
    switchingSource = false,
    soundOn = true;
  const cfgSound = { enabled: true };
  const tl = $('#timeline'), al = $('#alerts');
  const isSup = ['supervisor', 'admin'].includes(window.ROLE);

  const labels = window.EVENT_LABELS;
  const fmtT = ts => (ts || '').slice(11, 19);

  async function init() {
  const [{ cameras }, demoData] = await Promise.all([
    api('/api/cameras'),
    api('/api/demo-videos')
  ]);

  const sel = $('#camSel');

  const permanentCameras = cameras
    .filter(c => c && c.id && c.id !== 'demo')
    .map(c => `
      <option value="camera:${esc(c.id)}">
        ${esc(c.name)}
      </option>
    `)
    .join('');

  const demoVideos = (demoData.videos || [])
    .map(video => `
      <option value="demo:${esc(video.filename)}">
        Demo - ${esc(video.name)}
      </option>
    `)
    .join('');

  sel.innerHTML =
    permanentCameras +
    (
      demoVideos
        ? `<optgroup label="Demo Videos">${demoVideos}</optgroup>`
        : ''
    );

  sel.onchange = () => selectSource(sel.value);

  if (!sel.value) {
    toast('No camera or demo videos available');
    return;
  }

  await selectSource(sel.value);
    if (isSup) { $$('.sup-only').forEach(e => e.hidden = false); }
    setInterval(poll, POLL_MS);
  }
  
  async function selectSource(value) {
  if (!value || switchingSource) {
    return;
  }

  switchingSource = true;

  const sel = $('#camSel');

  if (sel) {
    sel.disabled = true;
  }

  try {
    // Demo video selected
    if (value.startsWith('demo:')) {
      const filename = value.substring(5);

      const result = await api(
        '/api/demo-videos/select',
        'POST',
        { filename: filename }
      );

      selectCam(result.camera_id || 'demo');

      toast(`Demo video started: ${filename}`);

      return;
    }

    // Laptop webcam or future CCTV selected
    if (value.startsWith('camera:')) {
      const cameraId = value.substring(7);

      const result = await api(
        `/api/cameras/${encodeURIComponent(cameraId)}/activate`,
        'POST'
      );

      selectCam(result.camera_id || cameraId);

      toast('Live camera started');

      return;
    }

    // Compatibility with older dropdown values
    selectCam(value);

  } catch (error) {
    console.error('Source switching error:', error);

    toast(
      error.message ||
      'Unable to open the selected camera or video'
    );

  } finally {
    switchingSource = false;

    if (sel) {
      sel.disabled = false;
    }
  }
}

  function selectCam(id) {
    cam = id; lastId = 0; lastAlertSeq = null; seen = new Set();
    tl.innerHTML = '<li class="empty">No events yet.</li>'; al.innerHTML = '<li class="empty">No alerts this shift.</li>';
    $('#feed').src = `/video_feed/${encodeURIComponent(id)}?t=${Date.now()}`;
    $('#btnCsv').href = `/api/export/events.csv?camera=${encodeURIComponent(id)}`;
    loadAlertHistory();
    if (isSup) loadSim();
  }

  async function loadAlertHistory() {
    const { alerts } = await api(`/api/cameras/${cam}/alerts`);
    al.innerHTML = '';
    if (!alerts.length) { al.innerHTML = '<li class="empty">No alerts this shift.</li>'; }
    alerts.slice().reverse().forEach(e => addAlert(e, false));
    $('#alCount').textContent = alerts.length;
  }

  function evLi(e, cls) {
    const shot = e.screenshot ? ` <a class="shot" data-src="/api/screenshots/${esc(e.screenshot)}">snapshot</a>` : '';
    return `<li class="${cls} new" data-id="${e.id}"><i></i><div class="grow-t"><b>${esc(e.label)}</b><small>${esc(e.message)} &middot; ${fmtT(e.ts)}${e.cycle_no ? ' &middot; cycle ' + e.cycle_no : ''}</small></div>${shot}</li>`;
  }
  function addEvent(e) {
    if (seen.has(e.id)) return; seen.add(e.id);
    tl.querySelector('.empty')?.remove();
    tl.insertAdjacentHTML('afterbegin', evLi(e, e.severity === 'alert' ? 'alert' : e.severity));
    while (tl.children.length > 60) tl.lastChild.remove();
    $('#evCount').textContent = tl.children.length;
    if (e.severity === 'alert') addAlert(e, true);
  }
  function addAlert(e, bump) {
    if (e.severity !== 'alert') return;
    if (al.querySelector(`[data-id="${e.id}"]`)) return;
    al.querySelector('.empty')?.remove();
    al.insertAdjacentHTML('afterbegin', evLi(e, 'alert'));
    while (al.children.length > 100) al.lastChild.remove();
    if (bump) $('#alCount').textContent = al.children.length;
  }

  async function poll() {
    if (!cam || polling) return; polling = true;
    try {
      const d = await api(`/api/cameras/${encodeURIComponent(cam)}/state?since=${lastId}`);
      render(d);
    } catch (e) { setLive(false); }
    polling = false;
  }

  function setLive(on) {
    const p = $('#livePill'); p.classList.toggle('off', !on); $('#liveText').textContent = on ? 'LIVE' : 'OFFLINE';
    $('#offlineOv').classList.toggle('show', !on);
  }

  function render(d) {
    const s = d.snapshot, c = s.counters, st = d.status;
    Beep.enabled = soundOn && d.sound.sound_enabled; Beep.volume = d.sound.sound_volume;
    setLive(st.online);
    if (st.aspect) $('#stage').style.aspectRatio = st.aspect;
    $('#fps').textContent = Math.round(st.fps);
    $('#backendChip').innerHTML = `<i></i>${esc(st.hand_backend)}${st.component_model ? ' + YOLO' : ''}`;
    $('#hudBackend').textContent = `AI \u00b7 ${st.hand_backend.toUpperCase()} \u00b7 ZONE HYBRID`;
    $('#shiftChip').innerHTML = `<i></i>Shift #${d.shift_info.id} since ${fmtT(d.shift_info.start_ts)}`;

    // banner
    const b = $('#banner'); b.className = 'banner ' + s.banner.level;
    $('#bTitle').textContent = s.banner.title; $('#bText').textContent = s.banner.text; $('#bTag').textContent = s.banner.tag;
    $('#bIcon').innerHTML = s.banner.level === 'alert' ? '<svg viewBox="0 0 24 24"><path d="M12 3 2 21h20zM12 10v5M12 18v.5"/></svg>'
      : s.banner.level === 'idle' ? '<svg viewBox="0 0 24 24"><circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/></svg>'
      : '<svg viewBox="0 0 24 24"><path d="m5 12 5 5L20 7"/></svg>';

    // KPIs
    const set = (k, v) => { const e = $(`[data-k="${k}"]`); if (e && e.textContent != v) { e.textContent = v; e.classList.remove('pop'); void e.offsetWidth; e.classList.add('pop'); } };
    set('violations', c.violations); set('cycles_total', c.cycles_total); set('pass_rate', c.pass_rate.toFixed(0));
    set('avg_cycle_time', c.avg_cycle_time.toFixed(1)); set('idle_time', Math.round(c.idle_time));
    $('#cycSplit').textContent = `${c.cycles_passed} pass / ${c.cycles_failed} fail`;
    $('#idleNow').textContent = c.idle_now > 2 ? `seconds this shift \u00b7 idle ${Math.round(c.idle_now)}s now` : 'seconds this shift';
    $('#violCard').classList.toggle('alert', s.banner.level === 'alert');
    $('#idleCard').classList.toggle('idle', s.banner.level === 'idle');

    // cycle + stage
    $('#cycNo').textContent = 'Cycle ' + s.cycle.no;
    $('#stageLbl').textContent = s.stage_label;
    $('#stageSub').textContent = s.cycle.active ? `Elapsed ${s.cycle.elapsed}s of ${s.cycle.timeout}s timeout` : `Timeout ${s.cycle.timeout}s`;
    const frac = s.cycle.active ? Math.min(1, s.cycle.elapsed / s.cycle.timeout) : (s.state === 'CYCLE_COMPLETED' ? 1 : 0);
    $('#ringBar').style.strokeDashoffset = 213.6 * (1 - frac);
    $('#ringT').textContent = Math.round(s.cycle.elapsed);
    $('#ring').className = 'ring' + (s.state === 'CYCLE_COMPLETED' ? ' res-PASS' : s.state === 'CYCLE_FAILED' ? ' res-FAIL' : '');
    $('#tracker').innerHTML = s.steps.map((x, i) => `<li class="${x.status}"><span class="n">${x.status === 'done' ? '\u2713' : i + 1}</span>${esc(x.name)}<span class="st">${x.status === 'current' ? 'in progress' : x.status === 'done' ? 'done' : x.status === 'failed' ? 'violation' : 'pending'}</span></li>`).join('');

    // shift stats (DB-backed, survive restarts)
    const sh = d.shift;
    $('#shiftNo').textContent = 'Shift #' + sh.shift_id;
    $('#shiftStats').innerHTML = [['Cycles', sh.cycles], ['Passed', sh.passed], ['Failed', sh.failed], ['Pass rate', sh.pass_rate + '%'], ['Avg time', sh.avg_cycle_time + 's'], ['Violations', sh.violations]]
      .map(([k, v]) => `<div><b>${v}</b>${k}</div>`).join('');
    const vt = Object.entries(sh.violations_by_type), mx = Math.max(1, ...vt.map(x => x[1]));
    $('#violBars').innerHTML = vt.length ? vt.map(([k, v]) => `<div class="bar-row"><div>${esc(labels[k] || k)}<div class="mbar"><i style="width:${100 * v / mx}%"></i></div></div><b>${v}</b></div>`).join('') : '<span class="hint">No violations this shift.</span>';

    // events (oldest first so newest ends on top)
    d.events.slice().reverse().forEach(addEvent);
    lastId = d.last_event_id;
    if (lastAlertSeq !== null && s.alert_seq > lastAlertSeq) Beep.play();
    lastAlertSeq = s.alert_seq;
  }

  // controls
  $('#btnShot').onclick = () => guard(async () => { const r = await api(`/api/cameras/${cam}/screenshot`, 'POST'); const a = document.createElement('a'); a.href = r.url; a.download = r.file; a.click(); }, 'Screenshot saved');
  $('#btnFull').onclick = () => { const st = $('#stage'); document.fullscreenElement ? document.exitFullscreen() : st.requestFullscreen?.(); };
  $('#btnSound').onclick = () => { soundOn = !soundOn; $('#soundLbl').textContent = soundOn ? 'Sound On' : 'Sound Off'; if (soundOn) Beep.play(); };
  $('#btnReset').onclick = () => { if (confirm('End the current shift and reset all counters?')) guard(async () => { await api('/api/shift/reset', 'POST'); selectCam(cam); }, 'Shift reset'); };
  document.addEventListener('click', e => { const a = e.target.closest('a.shot'); if (a) { $('#lbImg').src = a.dataset.src; $('#lightbox').classList.add('show'); } });
  $('#lightbox').onclick = () => $('#lightbox').classList.remove('show');
  $$('#viewSeg button').forEach(b => b.onclick = () => { $$('#viewSeg button').forEach(x => x.classList.toggle('on', x === b)); $('#stage').style.display = b.dataset.v === 'feed' ? '' : 'none'; });

  async function loadSim() {
    const st = (await api('/api/cameras')).cameras.find(c => c.id === cam);
    $('#simPanel').hidden = !(st && st.simulator);
    if (!st || !st.simulator) return;
    const { scenarios } = await api('/api/simulator/scenarios');
    $('#simBtns').innerHTML = scenarios.map(s => `<button class="chip btnchip" data-s="${s}"><i></i>${s.replace(/_/g, ' ')}</button>`).join('') + '<button class="chip btnchip stopchip" data-stop="1"><i></i>stop</button>';
    $('#simBtns').onclick = e => { const b = e.target.closest('button'); if (b) guard(() => api(`/api/cameras/${cam}/simulate`, 'POST', b.dataset.stop ? { stop: true } : { scenario: b.dataset.s })); };
  }
  init();
})();
