// Main page (backend/templates/index.html): the rack. Analytics, nav ports,
// U grid, chassis flip, feedback drawer, live status (PDU), power-on
// self-test, entrances, and the vektorgrid wiring-diagram stepper. Also loaded
// by the project pages, which share the nav and the PDU.

// Strings in the page's language: window.I18N is rendered from
// content/i18n/<lang>.yaml (the "js" subtree). A missing key shows as itself.
function tr(key, values) {
  let s = (window.I18N && window.I18N[key]) || key;
  if (values) s = s.replace(/\{(\w+)\}/g, (m, k) => (k in values ? values[k] : m));
  return s;
}

// Analytics beacon — auto-tracks outbound link clicks + resume download.
// /api/track is fire-and-forget; never blocks the click.
(function () {
  function track(name, data) {
    try {
      const body = JSON.stringify({ name: name, data: data || null });
      // sendBeacon when available (navigates don't cancel it); fall back to fetch keepalive.
      if (navigator.sendBeacon) {
        const blob = new Blob([body], { type: 'application/json' });
        if (navigator.sendBeacon('/api/track', blob)) return;
      }
      fetch('/api/track', { method: 'POST', headers: { 'content-type': 'application/json' }, body: body, keepalive: true });
    } catch (_) { /* tracking is best-effort */ }
  }
  document.addEventListener('click', function (e) {
    const a = e.target.closest && e.target.closest('a[href]');
    if (!a) return;
    const href = a.getAttribute('href') || '';
    if (href.startsWith('mailto:')) {
      track('contact_clicked', { kind: href.slice(7).split('?')[0] });
    } else if (/^\/resume(-ats)?\.pdf$/.test(href)) {
      track('resume_downloaded', { variant: href === '/resume-ats.pdf' ? 'ats' : 'classic' });
    } else if (a.target === '_blank' && /github\.com/.test(href)) {
      track('github_clicked', { url: href });
    }
  }, { passive: true });
})();

const reducedMotion = matchMedia('(prefers-reduced-motion: reduce)').matches;

// Rack grid: number the rails in U, and on wide screens round every unit up
// to a whole number of rack units (44px, minus the 2px gap between units) so
// faceplates line up with the rail holes. The unit's model label shows its
// size ("12U", or "Event log · 3U" when it has a label).
(function () {
  const U = 44;
  const units = [...document.querySelectorAll('.units > .unit, .units-main > .unit')];
  const rails = [...document.querySelectorAll('.rail')];
  const wide = matchMedia('(min-width: 901px)');
  units.forEach((u, i) => u.style.setProperty('--unit-i', i));

  function layout() {
    units.forEach(u => { u.style.minHeight = ''; });
    const heights = units.map(u => u.offsetHeight); // layout size, ignores the rack-in transform
    let total = 0;
    units.forEach((u, i) => {
      const n = Math.max(1, Math.ceil((heights[i] + 2) / U));
      total += n;
      if (wide.matches) u.style.minHeight = (n * U - 2) + 'px';
      const size = u.querySelector('.u-size');
      if (size) { size.textContent = n + 'U'; size.classList.toggle('sep', !!size.previousSibling); }
    });
    if (!wide.matches) return;
    rails.forEach(rail => {
      if (rail.childElementCount === total) return;
      rail.textContent = '';
      for (let i = 0; i < total; i++) {
        const s = document.createElement('span');
        s.className = 'rail-u';
        s.style.top = (i * U) + 'px';
        s.textContent = total - i;
        rail.appendChild(s);
      }
    });
  }
  let raf = 0;
  const schedule = () => { cancelAnimationFrame(raf); raf = requestAnimationFrame(layout); };
  window.addEventListener('resize', schedule);
  document.addEventListener('rack:relayout', schedule);
  if (document.fonts && document.fonts.ready) document.fonts.ready.then(schedule);
  window.addEventListener('load', schedule);
  schedule();
})();

// Power-on self-test: the inline script in <head> adds html.post on the first
// visit of a browser session; drop it once the sequence has played.
(function () {
  const h = document.documentElement;
  if (!h.classList.contains('post')) return;
  setTimeout(() => {
    h.classList.remove('post');
    try { sessionStorage.setItem('rack.post', '1'); } catch (_) {}
  }, 1400);
})();

// Units get racked as they scroll into view: pushed in, then screwed to the
// rails (the motion is CSS, keyed on .in). Units that arrive together are
// racked one after the other, top to bottom.
(function () {
  const units = document.querySelectorAll('.unit.rv');
  if (reducedMotion || !('IntersectionObserver' in window)) {
    units.forEach(el => el.classList.add('in'));
    return;
  }
  const io = new IntersectionObserver(entries => {
    entries.filter(e => e.isIntersecting).forEach((e, i) => {
      e.target.style.setProperty('--rv-delay', Math.min(i, 3) * 220 + 'ms');
      e.target.classList.add('in');
      io.unobserve(e.target);
    });
  }, { rootMargin: '0px 0px -12% 0px' });
  units.forEach(el => io.observe(el));
})();

// Nav ports: scroll-spy lights the port of the section in view
// (aria-current), and its act LED blinks once on change. On phones the
// ports fold behind a "Ports" key.
(function () {
  const ports = [...document.querySelectorAll('.ports a[href^="#"]')];
  // Sections without a port of their own light up the nearest one.
  const owner = { hero: null, projects: 'projects', 'more-projects': 'projects', experience: 'experience', stack: 'about', about: 'about', contact: 'contact', pdu: 'contact' };
  let current = undefined;
  function light(id) {
    if (id === current) return;
    current = id;
    ports.forEach(a => {
      const on = id && a.getAttribute('href') === '#' + id;
      if (on) {
        a.setAttribute('aria-current', 'true');
        a.classList.remove('blink'); void a.offsetWidth; a.classList.add('blink');
      } else {
        a.removeAttribute('aria-current');
      }
    });
  }
  // The lit port belongs to the section currently under the nav bar: the
  // last one whose top has scrolled past it. A short section (Experience
  // with its rows) still lights up when its port is clicked, because the
  // jump puts its top right there. At the very bottom, the last one wins.
  const sections = Object.keys(owner).map(id => document.getElementById(id)).filter(Boolean);
  const nav = document.getElementById('nav');
  function spy() {
    if (!sections.length) return;
    const line = (nav ? nav.getBoundingClientRect().bottom : 0) + 24;
    const atEnd = innerHeight + scrollY >= document.documentElement.scrollHeight - 2;
    let hit = sections[0];
    for (const sec of sections) {
      if (sec.getBoundingClientRect().top <= line) hit = sec;
    }
    if (atEnd) hit = sections[sections.length - 1];
    light(owner[hit.id]);
  }
  let spyRaf = 0;
  addEventListener('scroll', () => { cancelAnimationFrame(spyRaf); spyRaf = requestAnimationFrame(spy); }, { passive: true });
  addEventListener('resize', spy);
  spy();

  const toggle = document.querySelector('.nav-toggle');
  const list = document.getElementById('ports');
  if (!toggle || !list) return;
  function setOpen(open) {
    toggle.setAttribute('aria-expanded', String(open));
    list.classList.toggle('open', open);
  }
  toggle.addEventListener('click', () => setOpen(toggle.getAttribute('aria-expanded') !== 'true'));
  list.addEventListener('click', e => { if (e.target.closest('a')) setOpen(false); });
  document.addEventListener('keydown', e => {
    if (e.key === 'Escape' && toggle.getAttribute('aria-expanded') === 'true') { setOpen(false); toggle.focus(); }
  });
})();

// Résumé switch: the radios and :has() do the work; this only lets a click
// on the decorative track flip it too.
(function () {
  const track = document.querySelector('.switch-track');
  if (!track) return;
  track.addEventListener('click', () => {
    const ats = document.getElementById('rf-ats');
    const classic = document.getElementById('rf-classic');
    (ats.checked ? classic : ats).checked = true;
  });
})();

// vektorgrid chassis: FRONT (modules) / REAR (wiring diagram). Without JS
// both sides are shown one after the other.
(function () {
  const chassis = document.getElementById('chassis');
  if (!chassis) return;
  const front = chassis.querySelector('.chassis-front');
  const rear = chassis.querySelector('.chassis-rear');
  const keys = [...document.querySelectorAll('.seg [data-view]')];
  let view = 'front';
  rear.hidden = true;

  function swap(next) {
    front.hidden = next !== 'front';
    rear.hidden = next !== 'rear';
    keys.forEach(k => k.setAttribute('aria-pressed', String(k.dataset.view === next)));
    document.dispatchEvent(new Event('rack:relayout'));
  }
  keys.forEach(k => k.addEventListener('click', () => {
    const next = k.dataset.view;
    if (next === view) return;
    view = next;
    if (reducedMotion) { swap(next); return; }
    chassis.classList.remove('flip-in');
    chassis.classList.add('flip-out');
    setTimeout(() => {
      swap(next);
      chassis.classList.remove('flip-out');
      chassis.classList.add('flip-in');
    }, 220);
  }));
  chassis.addEventListener('animationend', e => {
    if (e.animationName === 'flip-in') chassis.classList.remove('flip-in');
  });
})();

// KVM drawer with the feedback form. Fault LED: red while the drawer is
// open, green after a report went through (feedback.js fires feedback:sent).
(function () {
  const toggle = document.querySelector('.drawer-toggle');
  const drawer = document.getElementById('drawer');
  const fault = document.getElementById('fault-status');
  if (!toggle || !drawer) return;
  const label = fault && fault.querySelector('.fault-label');
  const inner = drawer.querySelector('.drawer-inner');
  function setState(state, text) {
    if (!fault) return;
    fault.dataset.state = state;
    if (label) label.textContent = text;
  }
  function setOpen(open) {
    toggle.setAttribute('aria-expanded', String(open));
    drawer.classList.toggle('open', open);
    inner.inert = !open;
    if (fault && fault.dataset.state !== 'sent') setState(open ? 'open' : '', tr(open ? 'fault.reporting' : 'fault.idle'));
    if (open) setTimeout(() => { const m = drawer.querySelector('textarea'); if (m) m.focus(); }, reducedMotion ? 0 : 360);
    setTimeout(() => document.dispatchEvent(new Event('rack:relayout')), reducedMotion ? 0 : 380);
  }
  inner.inert = true;
  const flip = () => setOpen(toggle.getAttribute('aria-expanded') !== 'true');
  toggle.addEventListener('click', flip);
  const handle = drawer.querySelector('.drawer-handle');
  if (handle) handle.addEventListener('click', flip);
  document.addEventListener('feedback:sent', () => setState('sent', tr('fault.sent')));
})();

// Live status from /api/status: PDU readouts, "last push" chips on the
// blades, the hero LAT readout and the console's model name. Refreshes every
// 60s while the tab is visible; on failure the PDU goes amber and shows "--".
// LEDs with data-led show real state: "status" is green while this endpoint
// answers and amber when it doesn't; "push:<slug>" is green if that project
// was pushed to in the last two weeks, amber if not. The hero ACT LED blinks
// once per poll.
(function () {
  const $ = sel => document.querySelector(sel);
  const pduState = $('#pdu-state');
  const pduLabel = pduState && pduState.querySelector('.pdu-state-label');
  const lat = $('#lat');

  function ago(iso) {
    const t = Date.parse(iso);
    if (!t) return '--';
    const s = Math.max(0, (Date.now() - t) / 1000);
    if (s < 3600) return Math.max(1, Math.round(s / 60)) + tr('time.m');
    if (s < 86400) return Math.round(s / 3600) + tr('time.h');
    if (s < 86400 * 60) return Math.round(s / 86400) + tr('time.d');
    if (s < 86400 * 365) return Math.round(s / (86400 * 30)) + tr('time.mo');
    return Math.round(s / (86400 * 365)) + tr('time.y');
  }
  function uptime(sec) {
    if (sec == null) return '--';
    const d = Math.floor(sec / 86400), h = Math.floor(sec % 86400 / 3600);
    const pad = n => String(n).padStart(2, '0');
    return d ? d + tr('time.d') + ' ' + pad(h) + tr('time.h') : h + tr('time.h') + ' ' + pad(Math.floor(sec % 3600 / 60)) + tr('time.m');
  }
  function set(el, text) {
    if (!el || el.textContent === text) return;
    el.textContent = text;
    if (!reducedMotion) { el.classList.remove('tick'); void el.offsetWidth; el.classList.add('tick'); }
  }
  function setState(state, text) {
    if (!pduState) return;
    pduState.dataset.state = state;
    if (pduLabel) pduLabel.textContent = text;
  }
  function setLed(el, color) {
    el.classList.remove('led--green', 'led--amber');
    if (color) el.classList.add('led--' + color);
  }
  function leds(s) {
    document.querySelectorAll('[data-led]').forEach(el => {
      const src = el.dataset.led;
      if (src === 'status') setLed(el, s ? 'green' : 'amber');
      else if (src.startsWith('push:')) {
        const t = s && s.projects && Date.parse(s.projects[src.slice(5)]);
        setLed(el, t ? (Date.now() - t < 14 * 86400000 ? 'green' : 'amber') : '');
      }
    });
  }
  const act = $('.led--act');
  function blinkAct() {
    if (!act || reducedMotion) return;
    act.classList.add('led--amber');
    setTimeout(() => act.classList.remove('led--amber'), 140);
  }

  async function poll() {
    blinkAct();
    const t0 = performance.now();
    let s;
    try {
      const r = await fetch('/api/status', { cache: 'no-store' });
      if (!r.ok) throw new Error(r.status);
      s = await r.json();
    } catch (_) {
      document.querySelectorAll('[data-status]').forEach(el => set(el, '--'));
      if (lat) lat.textContent = 'LAT --';
      leds(null);
      setState('stale', tr('status.stale'));
      return;
    }
    if (lat) lat.textContent = 'LAT ' + Math.round(performance.now() - t0) + 'ms';
    set($('[data-status="uptime"]'), uptime(s.uptime_s));
    set($('[data-status="rev"]'), s.rev ? s.rev + (s.rev_time ? ' · ' + tr('time.ago', { t: ago(s.rev_time) }) : '') : '--');
    set($('[data-status="edge"]'), s.edge || '--');
    set($('[data-status="nodes"]'), s.nodes != null ? String(s.nodes) : '--');
    const projects = s.projects || {};
    document.querySelectorAll('[data-push]').forEach(el => {
      const when = projects[el.dataset.push];
      if (when) set(el, ago(when));
    });
    leds(s);
    setState('live', tr('status.online'));
    window.__rackStatus = s;
    document.dispatchEvent(new CustomEvent('rack:status', { detail: s }));
  }

  let timer = null;
  function sync() {
    if (document.hidden) { clearInterval(timer); timer = null; return; }
    if (!timer) { poll(); timer = setInterval(poll, 60000); }
  }
  document.addEventListener('visibilitychange', sync);
  sync();
})();

// Interactive architecture pipeline stepper. The diagram exists twice
// (landscape + portrait SVG); parts are addressed by data-part so both
// stay in sync whichever one is visible.
(function () {
  const arch = document.getElementById('arch');
  const noteEl = document.getElementById('archNote');
  const playBtn = document.getElementById('archPlay');
  if (!arch || !noteEl || !playBtn) return;
  const btns = [...arch.querySelectorAll('.arch-step[data-step]')];
  const parts = id => arch.querySelectorAll('[data-part="' + id + '"]');

  // step -> { nodes lit, edges lit ([id, reverse-flow]), badge lit, note }
  const steps = {
    1: { on: ['n-relay', 'n-origin'], edges: [['e-relay', false]], note: tr('arch.note1') },
    2: { on: ['n-origin', 'n-stt'], edges: [['e-stt', false]], badge: 'b-stt', note: tr('arch.note2') },
    3: { on: ['n-origin', 'n-router'], edges: [['e-router', false]], badge: 'b-router', note: tr('arch.note3') },
    4: { on: ['n-origin', 'n-llm'], edges: [['e-llm', false]], badge: 'b-llm', note: tr('arch.note4') },
    5: { on: ['n-origin', 'n-gate'], edges: [['e-gate', true]], note: tr('arch.note5') },
    6: { on: ['n-origin', 'n-tts'], edges: [['e-tts', false]], badge: 'b-tts', note: tr('arch.note6') },
    7: { on: ['n-relay', 'n-origin'], edges: [['e-relay', true]], note: tr('arch.note7') }
  };

  function clear() {
    arch.querySelectorAll('svg .on').forEach(el => el.classList.remove('on', 'rev'));
    btns.forEach(b => { b.classList.remove('active'); b.setAttribute('aria-pressed', 'false'); });
  }
  function show(n) {
    clear();
    const s = steps[n];
    if (!s) return;
    s.on.forEach(id => parts(id).forEach(el => el.classList.add('on')));
    s.edges.forEach(([id, rev]) => parts(id).forEach(el => {
      el.classList.add('on');
      if (rev) el.classList.add('rev');
    }));
    if (s.badge) parts(s.badge).forEach(el => el.classList.add('on'));
    const b = btns.find(b => +b.dataset.step === n);
    if (b) { b.classList.add('active'); b.setAttribute('aria-pressed', 'true'); }
    noteEl.innerHTML = s.note;
  }

  // Autoplay runs only while the diagram is on screen, and never after the
  // user has taken over (a step click or Pause). The note is announced to
  // screen readers only for manual steps, not every autoplay tick.
  let timer = null, cur = 1, wantPlay = !matchMedia('(prefers-reduced-motion: reduce)').matches, visible = false;
  function tick() { cur = cur % 7 + 1; show(cur); }
  function sync() {
    const run = wantPlay && visible && !document.hidden;
    if (run && !timer) timer = setInterval(tick, 1700);
    if (!run && timer) { clearInterval(timer); timer = null; }
    playBtn.classList.toggle('active', wantPlay);
    playBtn.setAttribute('aria-pressed', String(wantPlay));
    playBtn.textContent = tr(wantPlay ? 'arch.pause' : 'arch.auto');
    noteEl.setAttribute('aria-live', wantPlay ? 'off' : 'polite');
  }

  btns.forEach(b => b.addEventListener('click', () => { wantPlay = false; sync(); cur = +b.dataset.step; show(cur); }));
  playBtn.addEventListener('click', () => { wantPlay = !wantPlay; sync(); });
  document.addEventListener('visibilitychange', sync);
  if ('IntersectionObserver' in window) {
    new IntersectionObserver(entries => {
      visible = entries[entries.length - 1].isIntersecting; sync();
    }).observe(arch);
  } else { visible = true; }

  show(1);
  sync();
})();
