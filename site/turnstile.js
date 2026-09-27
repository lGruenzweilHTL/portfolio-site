// Cloudflare Turnstile, on demand. Chat and feedback each get their own widget,
// rendered invisible and only executed when the user actually sends. Tokens are
// single-use and expire after 300s, so every request gets a fresh one, and an
// interactive challenge (if Cloudflare wants one) shows up next to whatever is
// being submitted instead of somewhere offscreen.
//
// Loaded synchronously right before the Turnstile API script (which is async),
// so window.__turnstileOnload exists by the time that script calls it.
(function () {
  const meta = document.querySelector('meta[name="turnstile-sitekey"]');
  const sitekey = meta ? meta.content : '';

  let markReady;
  const ready = new Promise((resolve) => { markReady = resolve; });
  window.__turnstileOnload = () => markReady(true);

  const READY_TIMEOUT_MS = 5000;    // API script blocked or slow: send without a token
  const TOKEN_TIMEOUT_MS = 120000;  // leaves time to solve an interactive challenge
  const widgets = new WeakMap();    // container -> { id, settle }

  function withTimeout(promise, ms, fallback) {
    return Promise.race([promise, new Promise((r) => setTimeout(() => r(fallback), ms))]);
  }

  function widgetFor(container, action) {
    let w = widgets.get(container);
    if (w) return w;
    w = { id: null, settle: null };
    const done = (token) => { if (w.settle) w.settle(token || ''); };
    w.id = window.turnstile.render(container, {
      sitekey: sitekey,
      action: action,
      execution: 'execute',
      appearance: 'interaction-only',
      size: 'flexible',
      theme: 'auto',
      callback: done,
      'error-callback': () => done(''),
      'expired-callback': () => done(''),
      'timeout-callback': () => done(''),
    });
    widgets.set(container, w);
    return w;
  }

  // Resolves to a fresh token, or '' when Turnstile is unavailable or the
  // challenge fails. The server's dev bypass accepts ''; production answers 403.
  window.getTurnstileToken = async function (container, action) {
    // Unset, or the placeholder wasn't rewritten (page not served by the backend).
    if (!sitekey || sitekey.startsWith('__')) return '';
    if (!(await withTimeout(ready, READY_TIMEOUT_MS, false))) return '';

    let w;
    try { w = widgetFor(container, action); } catch (_) { return ''; }
    const token = await withTimeout(new Promise((resolve) => {
      w.settle = resolve;
      try { window.turnstile.execute(w.id); } catch (_) { resolve(''); }
    }), TOKEN_TIMEOUT_MS, '');
    w.settle = null;
    // Back to the invisible, unexecuted state: hides a solved checkbox and
    // makes the next call mint a new token.
    try { window.turnstile.reset(w.id); } catch (_) {}
    return token;
  };
})();
