(() => {
  const DEFAULTS = {
    1: { name: 'Bolt Bin', x: .05, y: .10, w: .22, h: .35 }, 2: { name: 'Spring Washer Bin', x: .30, y: .10, w: .22, h: .35 },
    3: { name: 'Plain Washer Bin', x: .55, y: .10, w: .22, h: .35 }, 4: { name: 'Assembly Area', x: .30, y: .58, w: .40, h: .32 }
  };
  const img = $('#zFeed'), cv = $('#zCanvas'), ctx = cv.getContext('2d');
  let aspect = 16 / 9, cam, rois = {}, sel = '1', drag = null, dirty = false;

  async function init() {
    const { cameras } = await api('/api/cameras');
    $('#camSel').innerHTML = cameras.filter(c => c.enabled).map(c => `<option value="${esc(c.id)}">${esc(c.name)}</option>`).join('');
    $('#camSel').onchange = () => load($('#camSel').value);
    load($('#camSel').value);
    requestAnimationFrame(loop);
  }
  async function load(id) {
    cam = id;
    const cs = (await api('/api/cameras')).cameras.find(c => c.id === id);
    if (cs && cs.aspect) { aspect = cs.aspect; $('#zStage').style.aspectRatio = aspect; }
    img.src = `/video_feed/${encodeURIComponent(id)}?t=${Date.now()}`;
    rois = (await api(`/api/cameras/${id}/rois`)).rois; dirty = false; list();
  }
  function list() {
    $('#zoneList').innerHTML = Object.keys(DEFAULTS).map(k => {
      const r = rois[k]; return `<div class="zone-item ${k === sel ? 'sel' : ''}" data-k="${k}"><span class="zn">${k}</span><div><input value="${esc(r.name)}" data-name="${k}" maxlength="40"><small>x ${r.x.toFixed(3)} y ${r.y.toFixed(3)} w ${r.w.toFixed(3)} h ${r.h.toFixed(3)}</small></div></div>`;
    }).join('');
    $$('.zone-item').forEach(el => el.onclick = e => { sel = el.dataset.k; if (e.target.tagName !== 'INPUT') list(); });
    $$('[data-name]').forEach(i => i.oninput = () => { rois[i.dataset.name].name = i.value; dirty = true; });
  }
  function fit() {
    const r = cv.getBoundingClientRect(); if (cv.width !== r.width | 0 || cv.height !== r.height | 0) { cv.width = r.width; cv.height = r.height; }
    const ia = img.naturalWidth ? img.naturalWidth / img.naturalHeight : aspect, ca = cv.width / cv.height;
    let w = cv.width, h = cv.height; if (ia > ca) h = w / ia; else w = h * ia;
    return { x: (cv.width - w) / 2, y: (cv.height - h) / 2, w, h };
  }
  function loop() {
    const f = fit(); ctx.clearRect(0, 0, cv.width, cv.height);
    for (const k in rois) {
      const r = rois[k], x = f.x + r.x * f.w, y = f.y + r.y * f.h, w = r.w * f.w, h = r.h * f.h, on = k === sel;
      ctx.fillStyle = on ? 'rgba(185,16,94,.38)' : 'rgba(185,16,94,.18)'; ctx.fillRect(x, y, w, h);
      ctx.strokeStyle = '#fff'; ctx.lineWidth = on ? 3 : 1.5; ctx.setLineDash(on ? [] : [6, 4]); ctx.strokeRect(x, y, w, h);
      ctx.setLineDash([]); const ly = y >= 22 ? y - 22 : y; ctx.fillStyle = '#B9105E'; ctx.fillRect(x, ly, Math.max(80, 18 + 8 * r.name.length), 22);
      ctx.fillStyle = '#fff'; ctx.font = '600 12px Poppins,sans-serif'; ctx.fillText(`Z${k} ${r.name}`, x + 6, ly + 15);
    }
    requestAnimationFrame(loop);
  }
  const pt = e => { const f = fit(), b = cv.getBoundingClientRect(); return { x: Math.min(1, Math.max(0, (e.clientX - b.left - f.x) / f.w)), y: Math.min(1, Math.max(0, (e.clientY - b.top - f.y) / f.h)) }; };
  cv.onmousedown = e => {
    const p = pt(e), r = rois[sel];
    if (p.x >= r.x && p.x <= r.x + r.w && p.y >= r.y && p.y <= r.y + r.h) drag = { mode: 'move', dx: p.x - r.x, dy: p.y - r.y };
    else drag = { mode: 'draw', sx: p.x, sy: p.y };
  };
  window.onmousemove = e => {
    if (!drag) return; const p = pt(e), r = rois[sel];
    if (drag.mode === 'move') { r.x = Math.min(1 - r.w, Math.max(0, p.x - drag.dx)); r.y = Math.min(1 - r.h, Math.max(0, p.y - drag.dy)); }
    else { r.x = Math.min(p.x, drag.sx); r.y = Math.min(p.y, drag.sy); r.w = Math.max(.03, Math.abs(p.x - drag.sx)); r.h = Math.max(.03, Math.abs(p.y - drag.sy)); }
    dirty = true;
  };
  window.onmouseup = () => { if (drag) { drag = null; list(); } };
  $('#btnSave').onclick = () => guard(async () => { await api(`/api/cameras/${cam}/rois`, 'PUT', { rois }); dirty = false; }, 'ROIs saved - live immediately');
  $('#btnReload').onclick = () => load(cam);
  $('#btnDefault').onclick = () => { rois = JSON.parse(JSON.stringify(DEFAULTS)); dirty = true; list(); toast('Default layout loaded - press Save to apply'); };
  window.onbeforeunload = () => dirty ? 'Unsaved zone changes' : undefined;
  init();
})();
