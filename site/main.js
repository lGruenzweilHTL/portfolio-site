// site/index.html — analytics, résumé menu, entrance animations, nav state, arch diagram stepper.

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
    if (a.classList.contains('btn-solid') && href.startsWith('mailto:')) {
      track('contact_clicked', { kind: href.slice(7).split('?')[0] });
    } else if (/^\/resume(-ats)?\.pdf$/.test(href)) {
      track('resume_downloaded', { variant: href === '/resume-ats.pdf' ? 'ats' : 'classic' });
    } else if (a.target === '_blank' && /github\.com/.test(href)) {
      track('github_clicked', { url: href });
    }
  }, { passive: true });
})();

// Résumé format dropdown — <details> handles the open/close toggle itself, so
// this only adds the two things the native element doesn't do: dismiss on an
// outside click (or after picking a format) and dismiss on Escape.
(function () {
  const menu = document.querySelector('.resume-menu');
  if (!menu) return;
  document.addEventListener('click', function (e) {
    if (!menu.open) return;
    // A click on the summary is the native toggle — leave that alone.
    if (menu.contains(e.target) && !e.target.closest('.resume-menu-item')) return;
    menu.removeAttribute('open');
  });
  document.addEventListener('keydown', function (e) {
    if (e.key !== 'Escape' || !menu.open) return;
    menu.removeAttribute('open');
    menu.querySelector('summary').focus();
  });
})();

// Scroll-triggered entrances (styles.css, "ANIMATIONS"). Elements that come
// into view in the same frame are staggered in document order, so a row of
// cards cascades instead of popping in at once.
(function () {
  const items = document.querySelectorAll('.rv');
  document.querySelectorAll('.rv-stagger').forEach(row =>
    [...row.children].forEach((c, i) => c.style.setProperty('--i', i)));
  if (!('IntersectionObserver' in window)) {
    items.forEach(el => el.classList.add('in'));
    return;
  }
  const io = new IntersectionObserver(entries => {
    const arriving = entries.filter(e => e.isIntersecting).map(e => e.target);
    arriving.forEach((el, i) => {
      el.style.setProperty('--rv-delay', Math.min(i, 5) * 90 + 'ms');
      el.classList.add('in');
      io.unobserve(el);
    });
  }, { threshold: 0.12, rootMargin: '0px 0px -40px 0px' });
  items.forEach(el => io.observe(el));
})();

// Nav: mark the link of the section currently on screen (aria-current).
(function () {
  const links = [...document.querySelectorAll('.nav-links a[href^="#"]')];
  // The hero has no link; observing it clears the highlight at the top.
  const sections = ['#hero', ...links.map(a => a.getAttribute('href'))]
    .map(sel => document.querySelector(sel)).filter(Boolean);
  if (!sections.length || !('IntersectionObserver' in window)) return;
  const io = new IntersectionObserver(entries => {
    entries.forEach(e => {
      if (!e.isIntersecting) return;
      links.forEach(a => {
        if (a.getAttribute('href') === '#' + e.target.id) a.setAttribute('aria-current', 'true');
        else a.removeAttribute('aria-current');
      });
    });
  }, { rootMargin: '-45% 0px -50% 0px' });
  sections.forEach(s => io.observe(s));
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
    1: { on: ['n-relay', 'n-origin'], edges: [['e-relay', false]], note: '<b>Capture:</b> Relay catches the wake-word and streams audio to Origin.' },
    2: { on: ['n-origin', 'n-stt'], edges: [['e-stt', false]], badge: 'b-stt', note: '<b>STT:</b> Origin sends the audio to the STT worker, which returns text.' },
    3: { on: ['n-origin', 'n-router'], edges: [['e-router', false]], badge: 'b-router', note: '<b>Route:</b> the intent router picks which expert should answer.' },
    4: { on: ['n-origin', 'n-llm'], edges: [['e-llm', false]], badge: 'b-llm', note: '<b>Inference:</b> the chosen expert LLM generates the response.' },
    5: { on: ['n-origin', 'n-gate'], edges: [['e-gate', true]], note: '<b>Approve:</b> a risky tool call pauses for biometric approval in Gate.' },
    6: { on: ['n-origin', 'n-tts'], edges: [['e-tts', false]], badge: 'b-tts', note: '<b>TTS:</b> Origin sends the reply text to TTS, which returns audio.' },
    7: { on: ['n-relay', 'n-origin'], edges: [['e-relay', true]], note: '<b>Reply:</b> Origin streams the spoken reply back through the Relay.' }
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
    playBtn.textContent = wantPlay ? '❚❚ Pause' : '▶ Auto';
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
