/* Shared helpers: API client (CSRF aware), toast, clock, alert beep. */
const CSRF = document.querySelector('meta[name=csrf-token]')?.content || '';
const $ = (s, r = document) => r.querySelector(s);
const $$ = (s, r = document) => [...r.querySelectorAll(s)];
const esc = s => String(s ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));

async function api(url, method = 'GET', body) {
  const opt = { method, headers: { 'X-CSRF-Token': CSRF }, credentials: 'same-origin' };
  if (body !== undefined) { opt.headers['Content-Type'] = 'application/json'; opt.body = JSON.stringify(body); }
  const r = await fetch(url, opt);
  if (r.status === 401) { location.href = '/login'; throw new Error('auth'); }
  let data = {};
  try { data = await r.json(); } catch (e) { /* non-JSON */ }
  if (!r.ok) throw new Error(data.error || ('HTTP ' + r.status));
  return data;
}

function toast(msg, ms = 2600) {
  const t = $('#toast'); if (!t) return;
  t.textContent = msg; t.classList.add('show');
  clearTimeout(toast._t); toast._t = setTimeout(() => t.classList.remove('show'), ms);
}
async function guard(fn, okMsg) {
  try { const r = await fn(); if (okMsg) toast(okMsg); return r; } catch (e) { toast(e.message || 'Error', 4000); }
}

setInterval(() => { const c = $('#clock'); if (c) c.textContent = new Date().toLocaleTimeString('en-GB'); }, 1000);

/* Alert sound - synthesised with WebAudio, so no audio asset is needed */
const Beep = {
  ctx: null, enabled: true, volume: 0.6,
  play() {
    if (!this.enabled) return;
    try {
      this.ctx = this.ctx || new (window.AudioContext || window.webkitAudioContext)();
      const t0 = this.ctx.currentTime;
      [0, 0.28, 0.56].forEach((d, i) => {
        const o = this.ctx.createOscillator(), g = this.ctx.createGain();
        o.type = 'square'; o.frequency.value = i % 2 ? 660 : 880;
        g.gain.setValueAtTime(0.0001, t0 + d);
        g.gain.exponentialRampToValueAtTime(Math.max(0.001, this.volume * 0.35), t0 + d + 0.02);
        g.gain.exponentialRampToValueAtTime(0.0001, t0 + d + 0.22);
        o.connect(g).connect(this.ctx.destination); o.start(t0 + d); o.stop(t0 + d + 0.25);
      });
    } catch (e) { /* autoplay blocked until first user gesture */ }
  }
};
document.addEventListener('click', () => { if (Beep.ctx && Beep.ctx.state === 'suspended') Beep.ctx.resume(); }, { once: true });
