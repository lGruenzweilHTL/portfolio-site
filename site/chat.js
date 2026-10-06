/* Chatbot UI: a plain terminal ("ask-lukas") opened from a console tab in the
   bottom-right corner.
   - Per-session UUID in localStorage; "clear" starts a new conversation
   - Calls POST /api/chat and parses the SSE stream
   - Renders failures as red "error:" lines with the email link
   - While a reply streams, html.console-busy makes the hero ACT LED flicker

   Styles live in styles.css ("Console" section); this file only builds the
   markup. Copy comes from window.I18N (content/i18n/<lang>.yaml, "js.con"
   and "js.reason"), so the console speaks the page language. */
(function () {
  'use strict';

  const tr = (key, values) => {
    let s = (window.I18N && window.I18N[key]) || key;
    if (values) s = s.replace(/\{(\w+)\}/g, (m, k) => (k in values ? values[k] : m));
    return s;
  };
  const LANG = document.documentElement.lang || 'en';

  const SESSION_KEY = 'lukas.chat.session_id';
  const ENDPOINT = '/api/chat';
  const EMAIL = 'lukas.gruenzweil@liwest.at';
  const HOST = 'lg-rack-01';

  // User-facing copy for each failure reason. The backend sends its own copy
  // in some cases (model-level fallbacks); we use that when present. This
  // map is the fallback for cases where the backend can't reach the client
  // (network error, route-level 4xx with no message, clean EOF mid-handshake).
  // `email: true` means the user is offered the email link as a next step;
  // `email: false` means they should fix the form (captcha, bad input) and
  // try again.
  const REASON_MESSAGES = {
    // Route-level (pre-stream) — the backend returns a JSON body with
    // {reason, message} and we prefer its `message` if present.
    rate_limited:        { text: tr('reason.rate_limited'), email: true },
    captcha_failed:      { text: tr('reason.captcha_failed'), email: false },
    message_too_long:    { text: tr('reason.message_too_long'), email: false },
    bad_request:         { text: tr('reason.bad_request'), email: true },
    // Stream-level (SSE) — the backend always sends its own message text,
    // so the copy here is the rare fallback for an empty payload.
    server_misconfigured:{ text: tr('reason.server_misconfigured'), email: true },
    server_error:        { text: tr('reason.server_error'), email: true },
    stream_interrupted:  { text: tr('reason.stream_interrupted'), email: true },
    // Pure client-side failures.
    network_error:       { text: tr('reason.network_error'), email: true },
    unknown:             { text: tr('reason.unknown'), email: true },
  };

  const EXAMPLES = [tr('con.ex1'), tr('con.ex2'), tr('con.ex3')];

  // --- Session management ---
  function getOrCreateSessionId() {
    try {
      let id = localStorage.getItem(SESSION_KEY);
      if (!id) {
        id = (crypto.randomUUID && crypto.randomUUID()) || (Date.now().toString(36) + Math.random().toString(36).slice(2));
        localStorage.setItem(SESSION_KEY, id);
      }
      return id;
    } catch (_) {
      // localStorage blocked — fall back to ephemeral id
      return (Date.now().toString(36) + Math.random().toString(36).slice(2));
    }
  }

  // --- DOM construction ---
  function el(tag, attrs, children) {
    const e = document.createElement(tag);
    if (attrs) for (const k in attrs) {
      if (k === 'class') e.className = attrs[k];
      else if (k === 'text') e.textContent = attrs[k];
      else e.setAttribute(k, attrs[k]);
    }
    if (children) for (const c of children) e.appendChild(typeof c === 'string' ? document.createTextNode(c) : c);
    return e;
  }
  function prompt() {
    return [
      el('span', { class: 'p-user', text: 'guest@' + HOST }),
      el('span', { class: 'p-path', text: ':~$' }),
    ];
  }
  const cursor = () => el('span', { class: 'con-cur', 'aria-hidden': 'true' });

  // --- Public widget API ---
  const widget = {
    panel: null,
    out: null,
    input: null,
    sendBtn: null,
    open: false,
    busy: false,

    init() {
      const tab = el('button', { type: 'button', class: 'con-tab', 'aria-label': tr('con.tab_label'), 'aria-expanded': 'false', 'aria-controls': 'console', 'aria-haspopup': 'dialog' }, [
        el('span', { class: 'con-tab-p', 'aria-hidden': 'true', text: '>_' }),
        el('span', { text: 'ask-lukas' }),
        cursor(),
      ]);
      tab.addEventListener('click', () => this.toggle());
      document.body.appendChild(tab);
      this.tab = tab;

      const close = el('button', { type: 'button', class: 'con-dot con-dot--red', 'aria-label': tr('con.close') });
      close.addEventListener('click', () => this.close());
      const clear = el('button', { type: 'button', class: 'con-clear', title: tr('con.clear_title'), text: 'clear' });
      clear.addEventListener('click', () => this.reset());
      const bar = el('div', { class: 'con-bar' }, [
        el('div', { class: 'con-dots' }, [
          close,
          el('span', { class: 'con-dot con-dot--amber', 'aria-hidden': 'true' }),
          el('span', { class: 'con-dot con-dot--green', 'aria-hidden': 'true' }),
        ]),
        el('h2', { class: 'con-title', id: 'console-title', text: 'ask-lukas — guest@' + HOST + ': ~' }),
        clear,
      ]);

      // Screen readers don't follow the log while tokens stream in; finished
      // replies and errors are announced once through this.live instead.
      const out = el('div', { class: 'con-out', role: 'log', 'aria-live': 'off', 'aria-label': tr('con.output_label'), tabindex: '0' });
      this.out = out;
      const live = el('div', { class: 'sr-only', 'aria-live': 'polite', 'aria-atomic': 'true' });
      this.live = live;

      // The chatbot's own Turnstile widget. Empty unless Cloudflare asks for
      // an interactive challenge on send, which then appears right here.
      const turnstileBox = el('div', { class: 'con-turnstile' });
      this.turnstileBox = turnstileBox;

      const input = el('textarea', { class: 'con-input', rows: '1', 'aria-label': tr('con.input_label'), placeholder: tr('con.placeholder'), spellcheck: 'false' });
      this.input = input;
      input.addEventListener('keydown', (e) => {
        if (e.key === 'Enter' && !e.shiftKey) {
          e.preventDefault();
          this.send();
        }
      });
      input.addEventListener('input', () => {
        input.style.height = 'auto';
        input.style.height = input.scrollHeight + 'px';
      });
      const sendBtn = el('button', { type: 'submit', class: 'key key--sm con-send', text: tr('con.send') });
      this.sendBtn = sendBtn;
      const form = el('form', { class: 'con-in' }, [
        el('span', { class: 'con-prompt', 'aria-hidden': 'true' }, prompt()),
        input,
        sendBtn,
      ]);
      form.addEventListener('submit', (e) => { e.preventDefault(); this.send(); });

      const modelLabel = el('span', { text: tr('con.free_model') + ' · OpenRouter' });
      this.modelLabel = modelLabel;
      const status = el('div', { class: 'con-status', 'aria-hidden': 'true' }, [modelLabel, el('span', { text: tr('con.hint') })]);

      const panel = el('div', { class: 'con', id: 'console', role: 'dialog', 'aria-labelledby': 'console-title' }, [bar, out, live, turnstileBox, form, status]);
      panel.inert = true;
      panel.addEventListener('keydown', (e) => {
        if (e.key === 'Escape') { e.stopPropagation(); this.close(); }
      });
      document.body.appendChild(panel);
      this.panel = panel;

      const setModel = (s) => { if (s && s.model) modelLabel.textContent = s.model + ' · OpenRouter'; };
      setModel(window.__rackStatus);
      document.addEventListener('rack:status', (e) => setModel(e.detail));

      this.greet(true);
    },

    greet(first) {
      if (first) {
        const d = new Date();
        let day;
        try {
          day = d.toLocaleDateString(LANG, { weekday: 'short', month: 'short', day: 'numeric', year: 'numeric' });
        } catch (_) { day = d.toDateString(); }
        this.line('sys', tr('con.last_login') + ' ' + day + ' ' + d.toTimeString().slice(0, 8) + ' on ttys001');
      }
      this.command('ask --help');
      this.line('out', tr('con.help', { email: EMAIL }));
      this.line('sys', tr('con.examples'));
      for (const q of EXAMPLES) {
        const b = el('button', { type: 'button', text: q });
        b.addEventListener('click', () => { this.input.value = q; this.send(); });
        this.out.appendChild(el('div', { class: 'ln ln--ex' }, [b]));
      }
    },

    toggle() {
      if (this.open) this.close(); else this.openPanel();
    },
    openPanel() {
      this.panel.inert = false;
      this.panel.classList.add('open');
      this.tab.setAttribute('aria-expanded', 'true');
      this.open = true;
      this.input.focus();
      this.track('chatbot_opened');
    },
    close() {
      if (!this.open) return;
      this.tab.setAttribute('aria-expanded', 'false');
      this.open = false;
      this.panel.inert = true;
      this.panel.classList.remove('open');
      this.tab.focus();
    },

    announce(text) {
      this.live.textContent = '';
      // Set on the next frame, so a repeated message is still announced.
      requestAnimationFrame(() => { this.live.textContent = text; });
    },

    reset() {
      try { localStorage.removeItem(SESSION_KEY); } catch (_) {}
      this.out.textContent = '';
      this.line('sys', tr('con.new_session'));
      this.greet(false);
      this.input.focus();
    },

    scroll() { this.out.scrollTop = this.out.scrollHeight; },

    line(kind, text) {
      const div = el('div', { class: 'ln ln--' + kind, text: text });
      this.out.appendChild(div);
      this.scroll();
      return div;
    },

    command(cmd) {
      const div = el('div', { class: 'ln ln--cmd' }, [...prompt(), ' ', el('span', { class: 'p-cmd', text: cmd })]);
      this.out.appendChild(div);
      this.scroll();
      return div;
    },

    // --- Error rendering -----------------------------------------------------
    // Single funnel for every failure mode so the user always sees a
    // consistent message and we get one tracking call. Strips the wait line
    // and any half-streamed reply, renders the message (preferring the
    // backend's text when it provided one), and optionally the email link.
    showError(reason, serverMessage) {
      const wait = this.out.querySelector('.ln--wait');
      if (wait) wait.remove();
      // Drop the half-streamed reply, if any — it never finished. Only the
      // one still streaming: earlier replies stay.
      const partial = this.out.querySelector('.ln--out.streaming');
      if (partial) partial.remove();

      const cfg = REASON_MESSAGES[reason] || REASON_MESSAGES.unknown;
      // Trust the server's copy when it sent one — it has more context.
      const text = (serverMessage && serverMessage.trim()) || cfg.text;
      const div = el('div', { class: 'ln ln--err' }, [tr('con.error') + ' ' + text]);
      if (cfg.email) div.append(' ', el('a', { href: 'mailto:' + EMAIL, text: EMAIL }));
      this.out.appendChild(div);
      this.scroll();
      this.announce(tr('con.error_sr') + ' ' + text);
      this.track('chatbot_error', { reason: reason });
    },

    track(name, data) {
      try {
        const body = JSON.stringify({ name: name, data: data || null });
        if (navigator.sendBeacon) {
          const blob = new Blob([body], { type: 'application/json' });
          if (navigator.sendBeacon('/api/track', blob)) return;
        }
        fetch('/api/track', { method: 'POST', headers: { 'content-type': 'application/json' }, body: body, keepalive: true });
      } catch (_) {}
    },

    setBusy(busy) {
      this.busy = busy;
      this.sendBtn.disabled = busy;
      document.documentElement.classList.toggle('console-busy', busy);
    },

    async send() {
      // Plain questions go straight in; "ask ..." typed out of habit (the
      // greeting shows `ask --help`) is accepted too.
      const text = this.input.value.trim().replace(/^ask\s+/i, '').replace(/^"(.*)"$/s, '$1').trim();
      if (!text || this.busy) return;
      this.setBusy(true);
      this.input.value = '';
      this.input.style.height = 'auto';

      this.command(text.replace(/\s+/g, ' '));
      const wait = this.line('wait', tr('con.connecting'));
      wait.classList.add('ln--sys');

      const sessionId = getOrCreateSessionId();

      // Fresh single-use Turnstile token for this message. '' if Turnstile
      // isn't available; the server's dev bypass accepts that.
      const turnstileToken = window.getTurnstileToken
        ? await window.getTurnstileToken(this.turnstileBox, 'chat')
        : '';

      let resp;
      try {
        resp = await fetch(ENDPOINT, {
          method: 'POST',
          headers: { 'content-type': 'application/json' },
          body: JSON.stringify({
            session_id: sessionId,
            message: text,
            turnstile_token: turnstileToken,
          }),
        });
      } catch (e) {
        this.showError('network_error');
        this.setBusy(false);
        return;
      }

      if (!resp.ok) {
        // Route-level error (rate-limit, captcha, validation). The backend
        // sends {reason, message}; fall back to status-code inference if
        // the body is missing or unparseable.
        let body = null;
        try { body = await resp.json(); } catch (_) { body = null; }
        const reason = (body && body.reason) ||
          (resp.status === 429 ? 'rate_limited' :
           resp.status === 403 ? 'captcha_failed' :
           resp.status === 413 ? 'message_too_long' :
           resp.status === 400 ? 'bad_request' : 'server_error');
        if (reason === 'message_too_long' && body && body.limit) {
          // Local copy instead of the backend's English one, with the
          // numbers filled in. Then hand the text back so the user can
          // shorten it, and drop the echoed command: it was never sent.
          this.showError(reason, tr('reason.message_too_long', { length: body.length, limit: body.limit }));
          const cmds = this.out.querySelectorAll('.ln--cmd');
          if (cmds.length) cmds[cmds.length - 1].remove();
          if (!this.input.value) {
            this.input.value = text;
            this.input.dispatchEvent(new Event('input'));
          }
        } else {
          this.showError(reason, body && body.message);
        }
        this.setBusy(false);
        return;
      }
      if (!resp.body) {
        this.showError('server_error');
        this.setBusy(false);
        return;
      }

      // Parse SSE stream
      const reader = resp.body.getReader();
      const decoder = new TextDecoder();
      let buf = '';
      let outDiv = null;
      let outText = '';
      const caret = cursor();
      // Track whether any renderable event (token/fallback/error) was
      // emitted. A clean EOF with no events means the server killed the
      // stream before sending anything.
      let sawAnyEvent = false;

      const handleEvent = (raw) => {
        if (!raw) return;
        // Each SSE event is one JSON object on a line beginning with "data: "
        let json = raw;
        if (raw.startsWith('data:')) json = raw.slice(5).trim();
        if (!json) return;
        let evt;
        try { evt = JSON.parse(json); } catch (_) { return; }

        if (evt.type === 'token') {
          sawAnyEvent = true;
          if (!outDiv) {
            wait.remove();
            outDiv = el('div', { class: 'ln ln--out streaming' });
            this.out.appendChild(outDiv);
          }
          outText += evt.text;
          outDiv.textContent = outText;
          outDiv.appendChild(caret);
          this.scroll();
        } else if (evt.type === 'fallback') {
          sawAnyEvent = true;
          // The backend already sends the user-facing copy. We pass the
          // upstream reason (rate_limit, timeout, upstream_error, no_key)
          // for analytics; it won't be in REASON_MESSAGES so the unknown
          // fallback (email: true) is used for the link.
          this.showError(evt.reason || 'model_fallback', evt.message);
          this.track('chatbot_fallback', { reason: evt.reason });
        } else if (evt.type === 'error') {
          sawAnyEvent = true;
          this.showError(evt.reason || 'server_error', evt.message);
        } else if (evt.type === 'done') {
          if (outDiv) {
            outDiv.classList.remove('streaming');
            outDiv.textContent = outText;
            this.announce(outText);
          }
        }
      };

      try {
        while (true) {
          const { value, done } = await reader.read();
          if (done) break;
          buf += decoder.decode(value, { stream: true });
          // SSE events separated by blank lines
          let idx;
          while ((idx = buf.indexOf('\n\n')) >= 0) {
            const event = buf.slice(0, idx);
            buf = buf.slice(idx + 2);
            handleEvent(event.replace(/\n/g, ' '));
          }
        }
        // Flush any trailing event
        if (buf.trim()) handleEvent(buf.replace(/\n/g, ' '));
        // Clean EOF with nothing delivered = the server died before the
        // first SSE event. Without this guard the wait line stays forever.
        if (!sawAnyEvent) this.showError('stream_interrupted');
      } catch (e) {
        this.showError('stream_interrupted');
      } finally {
        this.setBusy(false);
      }
    },
  };

  // Boot
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', () => widget.init());
  } else {
    widget.init();
  }

  // Expose for debugging
  window.__chatWidget = widget;
})();
