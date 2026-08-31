/* Chatbot UI for the portfolio site.
   - Floating button bottom-right
   - Slide-up panel
   - Per-session UUID in localStorage (cleared when the tab closes via sessionStorage would be cleaner,
     but localStorage is what was asked for; we clear on explicit "Reset" button)
   - Calls POST /api/chat, parses the SSE stream
   - Renders fallback messages with the email link

   Style: matches the site's dark/amber palette. Inlined here so the file is
   self-contained and can be dropped in without touching index.html's <style>. */
(function () {
  'use strict';

  const SESSION_KEY = 'lukas.chat.session_id';
  const ENDPOINT = '/api/chat';
  const EMAIL = 'lukas.gruenzweil@liwest.at';

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
    rate_limited:       { text: "You're sending messages too fast. Wait a moment, or email me.", email: true },
    captcha_failed:     { text: 'Captcha check failed. Refresh and try again.', email: false },
    bad_request:        { text: "Your message didn't go through. Try rephrasing, or email me if it keeps failing.", email: true },
    // Stream-level (SSE) — the backend always sends its own message text,
    // so the copy here is the rare fallback for an empty payload.
    server_misconfigured:{ text: "The chatbot is temporarily unavailable — there's a config issue on my end. Email me and I'll get it sorted.", email: true },
    server_error:       { text: "The chatbot hit an unexpected error. Try again, or email me if it keeps happening.", email: true },
    stream_interrupted: { text: "The connection dropped before the answer finished. Try again, or email me if it keeps happening.", email: true },
    // Pure client-side failures.
    network_error:      { text: 'Network error. Try again, or email me directly.', email: true },
    unknown:            { text: "Something went wrong. Try again, or email me.", email: true },
  };

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
      else if (k === 'html') e.innerHTML = attrs[k];
      else if (k.startsWith('on') && typeof attrs[k] === 'function') e.addEventListener(k.slice(2).toLowerCase(), attrs[k]);
      else e.setAttribute(k, attrs[k]);
    }
    if (children) for (const c of children) e.appendChild(c);
    return e;
  }

  // --- Styles (inlined to keep chat.js self-contained) ---
  const STYLE = `
  @keyframes chatFabEnter {
    0%   { transform: translateY(40px) scale(0.6); opacity: 0; }
    60%  { transform: translateY(-6px) scale(1.08); opacity: 1; }
    100% { transform: translateY(0) scale(1); opacity: 1; }
  }
  @keyframes chatFabPulse {
    0%, 100% { box-shadow: 0 8px 24px rgba(0,0,0,0.35), 0 0 0 0 rgba(235,165,58,0.55); }
    50%      { box-shadow: 0 8px 24px rgba(0,0,0,0.35), 0 0 0 14px rgba(235,165,58,0); }
  }
  @keyframes chatFabWiggle {
    0%, 100% { transform: rotate(0deg); }
    20%      { transform: rotate(-12deg); }
    40%      { transform: rotate(10deg); }
    60%      { transform: rotate(-6deg); }
    80%      { transform: rotate(4deg); }
  }
  @keyframes chatFabGlow {
    0%, 100% { filter: drop-shadow(0 0 0 rgba(235,165,58,0)); }
    50%      { filter: drop-shadow(0 0 8px rgba(235,165,58,0.45)); }
  }
  @keyframes chatPanelIn {
    0%   { opacity: 0; transform: translateY(18px) scale(0.96); }
    100% { opacity: 1; transform: translateY(0) scale(1); }
  }
  @keyframes chatPanelOut {
    0%   { opacity: 1; transform: translateY(0) scale(1); }
    100% { opacity: 0; transform: translateY(12px) scale(0.97); }
  }
  @keyframes chatMsgIn {
    0%   { opacity: 0; transform: translateY(6px); }
    100% { opacity: 1; transform: translateY(0); }
  }
  @keyframes chatTypingDot {
    0%, 80%, 100% { opacity: 0.25; transform: translateY(0); }
    40%           { opacity: 1;    transform: translateY(-3px); }
  }
  @keyframes chatCaret {
    0%, 49%   { opacity: 1; }
    50%, 100% { opacity: 0; }
  }
  .chat-fab {
    position: fixed; right: 22px; bottom: 22px; z-index: 60;
    width: 60px; height: 60px; border-radius: 50%;
    background: radial-gradient(circle at 30% 30%, var(--accent-hi, #f5be5f) 0%, var(--accent, #eba53a) 65%, #c9871f 100%);
    color: #17110a;
    border: 1px solid rgba(255,255,255,0.08);
    cursor: pointer; font-family: var(--font-display, 'Bricolage Grotesque', sans-serif);
    font-size: 22px; font-weight: 700; letter-spacing: 0.01em;
    box-shadow: 0 10px 28px rgba(0,0,0,0.45), inset 0 -2px 4px rgba(0,0,0,0.15);
    display: flex; align-items: center; justify-content: center;
    transform: translateY(0) scale(1);
    /* chatFabEnter is applied once via .chat-fab-enter (see init) and then
       stripped. Keeping it in the base style would re-trigger the entrance
       on every hover-out, because the 0% keyframe (scale 0.6, opacity 0)
       is held during the 0.8s delay and the button would snap invisible. */
    animation: chatFabPulse 2.4s ease-in-out 1.6s infinite;
    transition: transform 0.25s cubic-bezier(0.34, 1.36, 0.64, 1),
                background 0.25s ease,
                box-shadow 0.25s ease,
                filter 0.25s ease,
                opacity 0.25s ease;
  }
  .chat-fab.chat-fab-enter {
    animation: chatFabEnter 0.6s cubic-bezier(0.34, 1.56, 0.64, 1) 0.8s both,
               chatFabPulse 2.4s ease-in-out 1.6s infinite;
  }
  .chat-fab:hover {
    background: radial-gradient(circle at 30% 30%, #ffd07a 0%, var(--accent-hi, #f5be5f) 60%, var(--accent, #eba53a) 100%);
    transform: translateY(-2px) scale(1.05);
    animation: chatFabPulse 2.4s ease-in-out infinite, chatFabGlow 1.2s ease-in-out infinite;
  }
  .chat-fab:hover .chat-fab-icon { animation: chatFabWiggle 0.6s ease-in-out; }
  .chat-fab-icon {
    display: inline-block; line-height: 1; transform-origin: 50% 60%;
    transition: transform 0.25s cubic-bezier(0.34, 1.36, 0.64, 1);
  }
  .chat-fab[aria-expanded="true"] {
    opacity: 0; pointer-events: none;
    transform: translateY(14px) scale(0.85);
    transition: transform 0.28s cubic-bezier(0.34, 1.36, 0.64, 1),
                opacity 0.22s ease;
  }
  .chat-fab-tooltip {
    position: absolute; right: calc(100% + 12px); top: 50%;
    transform: translateY(-50%) translateX(6px);
    background: var(--panel, #191712); color: var(--hi, #eceae1);
    border: 1px solid var(--line2, #302c22);
    font-family: var(--font-mono, monospace); font-size: 11px;
    letter-spacing: 0.04em;
    padding: 6px 10px; border-radius: 4px;
    white-space: nowrap; pointer-events: none; opacity: 0;
    transition: opacity 0.22s ease, transform 0.22s cubic-bezier(0.34, 1.36, 0.64, 1);
  }
  .chat-fab-tooltip::after {
    content: ''; position: absolute; right: -4px; top: 50%;
    transform: translateY(-50%) rotate(45deg);
    width: 6px; height: 6px;
    background: var(--panel, #191712);
    border-right: 1px solid var(--line2, #302c22);
    border-top: 1px solid var(--line2, #302c22);
  }
  .chat-fab:hover .chat-fab-tooltip { opacity: 1; transform: translateY(-50%) translateX(0); }
  .chat-panel {
    position: fixed; right: 22px; bottom: 22px; z-index: 60;
    width: min(420px, calc(100vw - 28px));
    height: min(620px, calc(100vh - 100px));
    background: var(--panel, #191712);
    border: 1px solid var(--line2, #302c22);
    border-radius: 8px;
    display: flex; flex-direction: column;
    box-shadow: 0 12px 40px rgba(0,0,0,0.5);
    font-family: var(--font-body, 'IBM Plex Sans', sans-serif);
    overflow: hidden;
    opacity: 0; transform: translateY(18px) scale(0.96);
    transform-origin: bottom right;
    pointer-events: none;
    transition: opacity 0.22s ease, transform 0.22s cubic-bezier(0.34, 1.36, 0.64, 1);
  }
  .chat-panel.open {
    opacity: 1; transform: translateY(0) scale(1);
    pointer-events: auto;
    animation: chatPanelIn 0.26s cubic-bezier(0.34, 1.36, 0.64, 1) both;
  }
  .chat-panel.closing {
    animation: chatPanelOut 0.18s ease both;
  }
  .chat-head {
    display: flex; align-items: center; justify-content: space-between;
    padding: 12px 14px; border-bottom: 1px solid var(--line, #232019);
    background: var(--bg2, #15140f);
  }
  .chat-head-title { font-family: var(--font-display, 'Bricolage Grotesque', sans-serif);
    font-size: 14px; color: var(--hi, #eceae1); font-weight: 600; }
  .chat-head-sub { font-family: var(--font-mono, monospace); font-size: 10px;
    color: var(--muted, #918c7f); margin-top: 1px;
    letter-spacing: 0.04em; }
  .chat-head-actions { display: flex; align-items: center; gap: 4px; }
  .chat-close {
    background: none; border: none; color: var(--muted, #918c7f); cursor: pointer;
    font-family: var(--font-mono, monospace); font-size: 18px; line-height: 1;
    padding: 4px 8px; border-radius: 4px;
    transition: background 0.15s ease, color 0.15s ease, transform 0.15s ease;
  }
  .chat-close:hover { color: var(--hi, #eceae1); background: var(--line, #232019); }
  .chat-close:active { transform: scale(0.92); }
  .chat-messages {
    flex: 1; overflow-y: auto; padding: 14px;
    display: flex; flex-direction: column; gap: 10px;
    font-size: 13.5px; line-height: 1.55;
    scroll-behavior: smooth;
  }
  .chat-msg {
    max-width: 86%; padding: 8px 12px; border-radius: 6px; word-wrap: break-word;
    animation: chatMsgIn 0.22s cubic-bezier(0.34, 1.36, 0.64, 1) both;
  }
  .chat-msg.user {
    align-self: flex-end;
    background: var(--accent, #eba53a); color: #17110a;
  }
  .chat-msg.assistant {
    align-self: flex-start;
    background: var(--bg2, #15140f);
    color: var(--text, #d0ccc0);
    border: 1px solid var(--line, #232019);
  }
  .chat-msg.system {
    align-self: center; max-width: 100%;
    background: transparent; color: var(--muted, #918c7f);
    font-family: var(--font-mono, monospace); font-size: 11px;
    padding: 4px 0;
  }
  .chat-msg.fallback {
    align-self: flex-start; max-width: 100%;
    background: rgba(235,165,58,0.06);
    color: var(--dim, #aca699);
    border: 1px solid var(--accent-line, rgba(235,165,58,0.22));
  }
  .chat-msg.fallback a { color: var(--accent, #eba53a); }
  .chat-typing {
    align-self: flex-start;
    display: inline-flex; gap: 4px; align-items: center;
    background: var(--bg2, #15140f);
    border: 1px solid var(--line, #232019);
    padding: 8px 12px; border-radius: 6px;
    animation: chatMsgIn 0.22s cubic-bezier(0.34, 1.36, 0.64, 1) both;
  }
  .chat-typing-dot {
    width: 5px; height: 5px; border-radius: 50%;
    background: var(--muted, #918c7f);
    animation: chatTypingDot 1.2s ease-in-out infinite;
  }
  .chat-typing-dot:nth-child(2) { animation-delay: 0.15s; }
  .chat-typing-dot:nth-child(3) { animation-delay: 0.3s; }
  .chat-caret {
    display: inline-block; width: 6px; height: 1em; vertical-align: -2px;
    background: var(--text, #d0ccc0); margin-left: 1px;
    animation: chatCaret 0.9s steps(1) infinite;
  }
  .chat-form {
    border-top: 1px solid var(--line, #232019);
    padding: 10px 12px;
    display: flex; gap: 8px; align-items: flex-end;
    background: var(--bg2, #15140f);
  }
  .chat-input {
    flex: 1; resize: none; min-height: 38px; max-height: 120px;
    background: var(--bg, #0e0d0b); color: var(--text, #d0ccc0);
    border: 1px solid var(--line2, #302c22); border-radius: 4px;
    padding: 8px 10px; font-family: inherit; font-size: 13.5px;
    outline: none;
    transition: border-color 0.15s ease, box-shadow 0.15s ease;
  }
  .chat-input:focus {
    border-color: var(--accent-line, rgba(235,165,58,0.4));
    box-shadow: 0 0 0 3px rgba(235,165,58,0.10);
  }
  .chat-send {
    background: var(--accent, #eba53a); color: #17110a;
    border: none; border-radius: 4px; padding: 8px 16px;
    font-family: var(--font-body, 'IBM Plex Sans', sans-serif);
    font-size: 13px; font-weight: 600;
    cursor: pointer; align-self: stretch;
    transition: background 0.15s ease, transform 0.1s ease, box-shadow 0.15s ease;
    box-shadow: 0 1px 0 rgba(0,0,0,0.2);
  }
  .chat-send:hover { background: var(--accent-hi, #f5be5f); }
  .chat-send:active { transform: translateY(1px); box-shadow: none; }
  .chat-send:disabled { opacity: 0.5; cursor: not-allowed; transform: none; }
  .chat-reset {
    background: none; border: none; color: var(--muted, #918c7f);
    font-family: var(--font-body, 'IBM Plex Sans', sans-serif);
    font-size: 12px; font-weight: 500;
    cursor: pointer; padding: 4px 8px; border-radius: 4px;
    transition: background 0.15s ease, color 0.15s ease;
  }
  .chat-reset:hover { color: var(--accent, #eba53a); background: var(--line, #232019); }
  .chat-reset:active { transform: scale(0.96); }
  @media (max-width: 560px) {
    .chat-fab { right: 16px; bottom: 16px; }
    .chat-panel { right: 0; bottom: 0; width: 100vw; height: 100dvh; border-radius: 0; }
  }
  @media (prefers-reduced-motion: reduce) {
    .chat-fab, .chat-panel, .chat-msg, .chat-typing, .chat-fab-icon { animation: none !important; }
    .chat-panel, .chat-messages, .chat-input, .chat-send, .chat-reset, .chat-close,
    .chat-fab, .chat-fab-tooltip, .chat-fab-icon { transition: none !important; }
    .chat-typing-dot { animation: none !important; opacity: 0.7; }
  }
  `;

  function injectStyles() {
    if (document.getElementById('chat-styles')) return;
    const s = document.createElement('style');
    s.id = 'chat-styles';
    s.textContent = STYLE;
    document.head.appendChild(s);
  }

  function escape(s) {
    return s.replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  }

  // --- Public widget API ---
  const widget = {
    panel: null,
    messages: null,
    input: null,
    sendBtn: null,
    open: false,
    busy: false,

    init() {
      injectStyles();

      const fabIcon = el('span', { class: 'chat-fab-icon', 'aria-hidden': 'true' }, [
        document.createTextNode('✦')
      ]);
      const fab = el('button', { class: 'chat-fab chat-fab-enter', 'aria-label': 'Chat', 'aria-expanded': 'false', title: 'Chat' }, [
        fabIcon,
        el('span', { class: 'chat-fab-tooltip' }, [document.createTextNode('Chat')])
      ]);
      fab.addEventListener('click', () => this.toggle());
      // Once the one-shot entrance has finished, drop the class so
      // hover/hover-out transitions own transform/opacity. If the class
      // stayed, leaving hover would replay the entrance and the button
      // would snap invisible for ~0.8s. In reduced-motion the animation
      // is killed by the media query, so animationend never fires — fall
      // back to a timeout matching the 0.6s duration + 0.8s delay.
      const onEnterEnd = (e) => {
        if (e && e.animationName !== 'chatFabEnter') return;
        fab.classList.remove('chat-fab-enter');
        fab.removeEventListener('animationend', onEnterEnd);
      };
      fab.addEventListener('animationend', onEnterEnd);
      setTimeout(onEnterEnd, 1500);
      document.body.appendChild(fab);
      this.fab = fab;

      const head = el('div', { class: 'chat-head' }, [
        el('div', null, [
          el('div', { class: 'chat-head-title' }, [document.createTextNode('Ask About Lukas')]),
          el('div', { class: 'chat-head-sub' }, [document.createTextNode('Powered by a free model · may be slow')])
        ]),
        el('div', null, [
          el('button', { class: 'chat-reset', title: 'Start a new conversation' }, [document.createTextNode('Reset')]),
          el('button', { class: 'chat-close', 'aria-label': 'Close' }, [document.createTextNode('×')])
        ])
      ]);
      head.querySelector('.chat-close').addEventListener('click', () => this.close());
      head.querySelector('.chat-reset').addEventListener('click', () => this.reset());

      const messages = el('div', { class: 'chat-messages' });
      this.messages = messages;

      const input = el('textarea', { class: 'chat-input', rows: '1', placeholder: 'Ask about projects, skills, availability…' });
      this.input = input;
      input.addEventListener('keydown', (e) => {
        if (e.key === 'Enter' && !e.shiftKey) {
          e.preventDefault();
          this.send();
        }
      });
      // Auto-grow the textarea
      input.addEventListener('input', () => {
        input.style.height = 'auto';
        input.style.height = Math.min(input.scrollHeight, 120) + 'px';
      });

      const sendBtn = el('button', { class: 'chat-send' }, [document.createTextNode('Send')]);
      this.sendBtn = sendBtn;
      sendBtn.addEventListener('click', () => this.send());

      const form = el('form', { class: 'chat-form' }, [input, sendBtn]);
      form.addEventListener('submit', (e) => { e.preventDefault(); this.send(); });

      const panel = el('div', { class: 'chat-panel', role: 'dialog', 'aria-label': 'Chat with Lukas\'s assistant' }, [head, messages, form]);
      document.body.appendChild(panel);
      this.panel = panel;

      // Opening greeting
      this.appendMessage('assistant', 'Hi! I\'m a small assistant that can answer questions about Lukas\'s projects, skills, and background. Ask away — or use the contact section if you\'d rather email.');
      this.appendMessage('system', 'Powered by a free model; if rate-limited, try again or email ' + EMAIL);

      // Beacon: chatbot opened
      try { this.track('chatbot_opened'); } catch (_) {}
    },

    toggle() {
      if (this.open) this.close(); else this.openPanel();
    },
    openPanel() {
      this.panel.classList.remove('closing');
      this.panel.classList.add('open');
      this.fab.setAttribute('aria-expanded', 'true');
      this.open = true;
      this.input.focus();
    },
    close() {
      if (!this.open) return;
      this.fab.setAttribute('aria-expanded', 'false');
      this.open = false;
      // Play exit animation, then hide so it can't trap focus / block clicks.
      this.panel.classList.remove('open');
      this.panel.classList.add('closing');
      const onEnd = () => {
        this.panel.classList.remove('closing');
        this.panel.removeEventListener('animationend', onEnd);
      };
      this.panel.addEventListener('animationend', onEnd);
    },

    reset() {
      try { localStorage.removeItem(SESSION_KEY); } catch (_) {}
      this.messages.innerHTML = '';
      this.appendMessage('assistant', 'New conversation. Ask away.');
      this.appendMessage('system', 'Powered by a free model; if rate-limited, try again or email ' + EMAIL);
    },

    appendMessage(role, text) {
      const div = el('div', { class: 'chat-msg ' + role });
      div.textContent = text;
      this.messages.appendChild(div);
      this.messages.scrollTop = this.messages.scrollHeight;
      return div;
    },

    appendHTML(role, html) {
      const div = el('div', { class: 'chat-msg ' + role });
      div.innerHTML = html;
      this.messages.appendChild(div);
      this.messages.scrollTop = this.messages.scrollHeight;
      return div;
    },

    // --- Error rendering -----------------------------------------------------
    // Single funnel for every failure mode so the user always sees a
    // consistent, friendly message and we get one tracking call. Strips any
    // typing indicator, renders the message (preferring the backend's
    // text when it provided one), and optionally appends the email link.
    showError(reason, serverMessage) {
      // Drop the typing indicator if it's still in the DOM.
      const typing = this.messages.querySelector('.chat-typing');
      if (typing) typing.remove();
      // Drop any half-streamed assistant message — it never finished.
      const partial = this.messages.querySelector('.chat-msg.assistant');
      if (partial) partial.remove();

      const cfg = REASON_MESSAGES[reason] || REASON_MESSAGES.unknown;
      // Trust the server's copy when it sent one — it has more context.
      const text = (serverMessage && serverMessage.trim()) || cfg.text;
      const html = cfg.email
        ? escape(text) + ' <a href="mailto:' + EMAIL + '">' + EMAIL + '</a>'
        : escape(text);
      this.appendHTML('fallback', html);
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

    async send() {
      const text = this.input.value.trim();
      if (!text || this.busy) return;
      this.busy = true;
      this.sendBtn.disabled = true;
      this.input.value = '';
      this.input.style.height = 'auto';

      this.appendMessage('user', text);
      const typing = el('div', { class: 'chat-typing' }, [
        el('span', { class: 'chat-typing-dot' }),
        el('span', { class: 'chat-typing-dot' }),
        el('span', { class: 'chat-typing-dot' }),
      ]);
      this.messages.appendChild(typing);
      this.messages.scrollTop = this.messages.scrollHeight;

      const sessionId = getOrCreateSessionId();

      // Get a Turnstile token. If the widget isn't loaded (dev bypass or
      // missing sitekey), send an empty string — server-side dev bypass
      // accepts it.
      let turnstileToken = '';
      if (window.turnstile && typeof window.turnstile.getResponse === 'function') {
        try { turnstileToken = window.turnstile.getResponse() || ''; } catch (_) {}
      }

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
        this.busy = false;
        this.sendBtn.disabled = false;
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
           resp.status === 400 ? 'bad_request' : 'server_error');
        this.showError(reason, body && body.message);
        this.busy = false;
        this.sendBtn.disabled = false;
        return;
      }
      if (!resp.body) {
        this.showError('server_error');
        this.busy = false;
        this.sendBtn.disabled = false;
        return;
      }

      // Parse SSE stream
      const reader = resp.body.getReader();
      const decoder = new TextDecoder();
      let buf = '';
      let assistantDiv = null;
      let assistantText = '';
      // Track whether any renderable event (token/fallback/error) was
      // emitted. A clean EOF with no events means the server killed the
      // stream before sending anything — the original RuntimeError bug.
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
          if (!assistantDiv) {
            typing.remove();
            assistantDiv = el('div', { class: 'chat-msg assistant' });
            this.messages.appendChild(assistantDiv);
          }
          assistantText += evt.text;
          // Re-render with a blinking caret at the end of the streamed text
          assistantDiv.textContent = assistantText;
          const caret = el('span', { class: 'chat-caret', 'aria-hidden': 'true' });
          assistantDiv.appendChild(caret);
          this.messages.scrollTop = this.messages.scrollHeight;
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
          // end of stream — drop the streaming caret
          if (assistantDiv) {
            const caret = assistantDiv.querySelector('.chat-caret');
            if (caret) caret.remove();
            assistantDiv.textContent = assistantText;
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
        // first SSE event. This is the exact symptom of the original
        // "Content not loaded" bug — without this guard the user just sees
        // the typing indicator forever.
        if (!sawAnyEvent) this.showError('stream_interrupted');
      } catch (e) {
        this.showError('stream_interrupted');
      } finally {
        this.busy = false;
        this.sendBtn.disabled = false;
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
