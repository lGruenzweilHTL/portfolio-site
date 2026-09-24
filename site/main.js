// site/index.html — analytics, reveal observer, arch diagram stepper.

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

const io = new IntersectionObserver(entries => {
  entries.forEach(e => {
    if (e.isIntersecting) { e.target.classList.add('in'); io.unobserve(e.target); }
  });
}, { threshold: 0.07 });
document.querySelectorAll('.reveal').forEach(el => io.observe(el));

// Interactive architecture pipeline stepper
(function () {
  const svg = document.getElementById('archSvg');
  const noteEl = document.getElementById('archNote');
  const playBtn = document.getElementById('archPlay');
  if (!svg || !noteEl || !playBtn) return;
  const btns = [...document.querySelectorAll('.arch-step[data-step]')];

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
    svg.querySelectorAll('.on').forEach(el => el.classList.remove('on', 'rev'));
    btns.forEach(b => b.classList.remove('active'));
  }
  function show(n) {
    clear();
    const s = steps[n];
    if (!s) return;
    s.on.forEach(id => document.getElementById(id)?.classList.add('on'));
    s.edges.forEach(([id, rev]) => {
      const el = document.getElementById(id);
      if (el) { el.classList.add('on'); if (rev) el.classList.add('rev'); }
    });
    if (s.badge) document.getElementById(s.badge)?.classList.add('on');
    btns.find(b => +b.dataset.step === n)?.classList.add('active');
    noteEl.innerHTML = s.note;
  }

  let timer = null, cur = 1;
  function stop() {
    if (timer) { clearInterval(timer); timer = null; }
    playBtn.classList.remove('active');
    playBtn.textContent = '▶ Auto';
  }
  function play() {
    stop();
    playBtn.classList.add('active');
    playBtn.textContent = '❚❚ Pause';
    timer = setInterval(() => { cur = cur % 7 + 1; show(cur); }, 1700);
  }

  btns.forEach(b => b.addEventListener('click', () => { stop(); cur = +b.dataset.step; show(cur); }));
  playBtn.addEventListener('click', () => { timer ? stop() : play(); });

  show(1);
  if (!matchMedia('(prefers-reduced-motion: reduce)').matches) play();
})();
