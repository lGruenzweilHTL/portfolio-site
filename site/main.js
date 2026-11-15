// Every page that uses styles.css: analytics beacon, the phone menu,
// scroll-spy, live status from /api/status, the copy button and the
// screenshot component (colours on hover, the original opened large).
// chalk.js draws; this file only behaves. A classic script on purpose: it
// runs without module support too.

// Strings in the page's language: window.I18N is rendered from
// content/i18n/<lang>.yaml (the "js" subtree). A missing key shows as itself.
function tr(key, values) {
  let s = (window.I18N && window.I18N[key]) || key;
  if (values) s = s.replace(/\{(\w+)\}/g, (m, k) => (k in values ? values[k] : m));
  return s;
}

const reducedMotion = matchMedia('(prefers-reduced-motion: reduce)');

// Analytics beacon: outbound link clicks and résumé downloads. /api/track is
// fire-and-forget and never blocks the click.
(function () {
  function track(name, data) {
    try {
      const body = JSON.stringify({ name, data: data || null });
      if (navigator.sendBeacon && navigator.sendBeacon('/api/track', new Blob([body], { type: 'application/json' }))) return;
      fetch('/api/track', { method: 'POST', headers: { 'content-type': 'application/json' }, body, keepalive: true }).catch(() => {});
    } catch (_) { /* tracking is best-effort */ }
  }
  document.addEventListener('click', (e) => {
    const a = e.target.closest && e.target.closest('a[href]');
    if (!a) return;
    const href = a.getAttribute('href') || '';
    if (href.startsWith('mailto:')) track('contact_clicked', { kind: href.slice(7).split('?')[0] });
    else if (/^\/resume(-ats)?\.pdf$/.test(href)) track('resume_downloaded', { variant: href === '/resume-ats.pdf' ? 'ats' : 'classic' });
    else if (a.target === '_blank' && /github\.com/.test(href)) track('github_clicked', { url: href });
  }, { passive: true });
})();

// Menu on narrow screens, and the section in view marked in the nav.
(function () {
  const toggle = document.querySelector('.nav-toggle');
  const nav = document.getElementById('site-nav');
  if (toggle && nav) {
    const setOpen = (open) => {
      toggle.setAttribute('aria-expanded', String(open));
      nav.classList.toggle('open', open);
      // the menu panel only has a size now: give it its chalk frame
      if (open && window.Chalk) { nav.setAttribute('data-chalk', 'box'); window.Chalk.draw(nav); }
    };
    document.addEventListener('click', (e) => {
      if (toggle.getAttribute('aria-expanded') === 'true' && !nav.contains(e.target) && !toggle.contains(e.target)) setOpen(false);
    });
    toggle.addEventListener('click', () => setOpen(toggle.getAttribute('aria-expanded') !== 'true'));
    nav.addEventListener('click', (e) => { if (e.target.closest('a[href*="#"]')) setOpen(false); });
    document.addEventListener('keydown', (e) => {
      if (e.key === 'Escape' && toggle.getAttribute('aria-expanded') === 'true') { setOpen(false); toggle.focus(); }
    });
  }

  const links = [...document.querySelectorAll('.nav-links a[href^="#"]')];
  const sections = links.map((a) => document.getElementById(a.getAttribute('href').slice(1))).filter(Boolean);
  if (!sections.length) return;
  let current = null;
  function spy() {
    const line = innerHeight * 0.35;
    let hit = null;
    for (const sec of sections) if (sec.getBoundingClientRect().top <= line) hit = sec;
    if (innerHeight + scrollY >= document.documentElement.scrollHeight - 2) hit = sections[sections.length - 1];
    if (hit === current) return;
    current = hit;
    for (const a of links) {
      if (hit && a.getAttribute('href') === '#' + hit.id) a.setAttribute('aria-current', 'true');
      else a.removeAttribute('aria-current');
    }
  }
  let raf = 0;
  addEventListener('scroll', () => { if (!raf) raf = requestAnimationFrame(() => { raf = 0; spy(); }); }, { passive: true });
  spy();
})();

// The bug-report form sits open beside Contact on wide screens; where it
// would stack under it, it starts folded.
(function () {
  const report = document.querySelector('details.report');
  if (report && innerWidth < 1100) report.open = false;
})();

// Copy the email address (the button only appears when the clipboard works).
(function () {
  for (const btn of document.querySelectorAll('[data-copy]')) {
    if (!navigator.clipboard) continue;
    btn.hidden = false;
    const label = btn.querySelector('.copy-label');
    const status = btn.parentElement.querySelector('.copy-status');
    const original = label ? label.textContent : '';
    let timer = 0;
    btn.addEventListener('click', async () => {
      let ok = true;
      try { await navigator.clipboard.writeText(btn.dataset.copy); } catch (_) { ok = false; }
      if (label) label.textContent = ok ? tr('copied') : original;
      if (status) status.textContent = ok ? tr('copied') : tr('copy_failed');
      btn.querySelector('use')?.setAttribute('href', ok ? '#ph-check' : '#ph-copy');
      clearTimeout(timer);
      timer = setTimeout(() => {
        if (label) label.textContent = original;
        if (status) status.textContent = '';
        btn.querySelector('use')?.setAttribute('href', '#ph-copy');
      }, 2200);
    });
  }
})();

// Live status from /api/status, into every [data-status] element (footer,
// the request path on the main page). A value that arrives un-hides its
// [data-show="name"] wrappers (and [data-show="readouts"] once any did); on
// failure they hide again, so the page never shows a row of dashes.
// Refreshes every 60 s while the tab is visible.
(function () {
  const fields = () => document.querySelectorAll('[data-status], [data-push]');
  if (!fields().length) return;

  function ago(iso) {
    const t = Date.parse(iso);
    if (!t) return '';
    const s = Math.max(0, (Date.now() - t) / 1000);
    if (s < 3600) return Math.max(1, Math.round(s / 60)) + tr('time.m');
    if (s < 86400) return Math.round(s / 3600) + tr('time.h');
    if (s < 86400 * 60) return Math.round(s / 86400) + tr('time.d');
    if (s < 86400 * 365) return Math.round(s / (86400 * 30)) + tr('time.mo');
    return Math.round(s / (86400 * 365)) + tr('time.y');
  }
  function uptime(sec) {
    if (sec == null) return '';
    const d = Math.floor(sec / 86400);
    const h = Math.floor((sec % 86400) / 3600);
    const pad = (n) => String(n).padStart(2, '0');
    return d ? `${d}${tr('time.d')} ${pad(h)}${tr('time.h')}` : `${h}${tr('time.h')} ${pad(Math.floor((sec % 3600) / 60))}${tr('time.m')}`;
  }
  function show(name, on) {
    for (const el of document.querySelectorAll(`[data-show="${name}"]`)) el.hidden = !on;
  }
  function set(name, text) {
    for (const el of document.querySelectorAll(`[data-status="${name}"]`)) {
      if (el.textContent !== text) el.textContent = text;
    }
    show(name, !!text);
    return !!text;
  }
  async function poll() {
    let s;
    try {
      const r = await fetch('/api/status', { cache: 'no-store' });
      if (!r.ok) throw new Error(String(r.status));
      s = await r.json();
    } catch (_) {
      for (const name of ['uptime', 'rev', 'edge', 'nodes']) set(name, '');
      show('readouts', false);
      for (const el of document.querySelectorAll('[data-push]')) el.textContent = '--';
      return;
    }
    let any = false;
    any = set('uptime', uptime(s.uptime_s)) || any;
    any = set('rev', s.rev ? s.rev + (s.rev_time && ago(s.rev_time) ? ', ' + tr('time.ago', { t: ago(s.rev_time) }) : '') : '') || any;
    any = set('edge', s.edge || '') || any;
    any = set('nodes', s.nodes != null ? String(s.nodes) : '') || any;
    show('readouts', any);
    // Project pages: when the repo was last pushed.
    for (const el of document.querySelectorAll('[data-push]')) {
      const at = s.projects && s.projects[el.dataset.push];
      el.textContent = at && ago(at) ? tr('time.ago', { t: ago(at) }) : '--';
    }
    window.__siteStatus = s;
    document.dispatchEvent(new CustomEvent('site:status', { detail: s }));
  }
  let timer = null;
  function sync() {
    if (document.hidden) { clearInterval(timer); timer = null; return; }
    if (!timer) { poll(); timer = setInterval(poll, 60000); }
  }
  document.addEventListener('visibilitychange', sync);
  sync();
})();

// Screenshots (<figure class="shot">): the chalk rendition is a link to the
// original. Hover or keyboard focus fades the real colours in, in place; a
// click opens the original large in a dialog. Without JS the link opens the
// image file. The original only loads when someone asks for it.
(function () {
  const links = document.querySelectorAll('.shot-link');
  if (!links.length) return;
  function real(link) {
    let img = link.querySelector('.shot-real');
    if (!img) {
      img = document.createElement('img');
      img.className = 'shot-real';
      img.alt = '';
      img.decoding = 'async';
      img.src = link.getAttribute('href');
      (link.querySelector('.shot-pic') || link).appendChild(img);
    }
    return img;
  }
  const on = (link) => { const img = real(link); const go = () => link.classList.add('is-real'); img.complete ? go() : img.addEventListener('load', go, { once: true }); };
  const off = (link) => link.classList.remove('is-real');

  let box = null;
  function lightbox() {
    if (box) return box;
    box = document.createElement('dialog');
    box.className = 'lightbox';
    box.setAttribute('aria-label', tr('shot.dialog'));
    const img = document.createElement('img');
    img.alt = '';
    const bar = document.createElement('div');
    bar.className = 'lightbox-bar';
    const cap = document.createElement('p');
    const close = document.createElement('button');
    close.type = 'button';
    close.className = 'btn btn--sm';
    close.textContent = tr('shot.close');
    close.addEventListener('click', () => box.close());
    bar.append(cap, close);
    box.append(img, bar);
    // a click on the backdrop closes it too
    box.addEventListener('click', (e) => { if (e.target === box) box.close(); });
    document.body.appendChild(box);
    return box;
  }
  for (const link of links) {
    link.addEventListener('pointerenter', (e) => { if (e.pointerType === 'mouse') on(link); });
    link.addEventListener('pointerleave', () => off(link));
    link.addEventListener('focus', () => { if (link.matches(':focus-visible')) on(link); });
    link.addEventListener('blur', () => off(link));
    link.addEventListener('click', (e) => {
      const d = lightbox();
      if (typeof d.showModal !== 'function') return;   // old browser: follow the link
      e.preventDefault();
      const shot = link.querySelector('.shot-chalk');
      const img = d.querySelector('img');
      img.src = link.getAttribute('href');
      img.alt = shot ? shot.alt : '';
      d.querySelector('p').textContent = shot ? shot.alt : '';
      d.showModal();
      if (window.Chalk) { d.setAttribute('data-chalk', 'box'); img.decode().catch(() => {}).then(() => window.Chalk.draw(d)); }
    });
  }
})();
