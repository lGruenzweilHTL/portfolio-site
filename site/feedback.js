// Main page (backend/templates/index.html) — feedback form submit handler.
// Posts to /api/feedback with a fresh Turnstile token fetched on submit
// (see turnstile.js; '' when unavailable, which the dev bypass accepts).
(function () {
  // Strings in the page's language (window.I18N, see backend/i18n.py).
  const tr = key => (window.I18N && window.I18N[key]) || key;
  const form = document.getElementById('feedback-form');
  if (!form) return;
  const status = document.getElementById('feedback-status');
  const submit = document.getElementById('feedback-submit');
  const turnstileBox = document.getElementById('feedback-turnstile');
  const emailInput = form.elements.email;
  const messageInput = form.elements.message;

  // The form is novalidate so errors show up in the status line (announced
  // by screen readers) instead of the browser's own bubbles.
  function invalid(input, text) {
    input.setAttribute('aria-invalid', 'true');
    input.focus();
    status.textContent = text;
    status.className = 'feedback-status err';
  }
  [emailInput, messageInput].forEach((input) =>
    input.addEventListener('input', () => input.removeAttribute('aria-invalid')));

  form.addEventListener('submit', async function (e) {
    e.preventDefault();
    status.textContent = '';
    status.className = 'feedback-status';
    submit.disabled = true;
    const data = new FormData(form);
    const body = {
      name: (data.get('name') || '').toString().trim() || null,
      email: (data.get('email') || '').toString().trim() || null,
      message: (data.get('message') || '').toString().trim(),
    };
    if (!body.message) {
      invalid(messageInput, tr('fb.empty'));
      submit.disabled = false;
      return;
    }
    if (body.email && !emailInput.checkValidity()) {
      invalid(emailInput, tr('fb.bad_email'));
      submit.disabled = false;
      return;
    }
    // Any interactive challenge appears beside the button while this waits,
    // and the helper hides it again once the token is issued.
    status.textContent = tr('fb.verifying');
    body.turnstile_token = window.getTurnstileToken
      ? await window.getTurnstileToken(turnstileBox, 'feedback')
      : '';
    status.textContent = '';
    let resp;
    try {
      resp = await fetch('/api/feedback', {
        method: 'POST',
        headers: { 'content-type': 'application/json' },
        body: JSON.stringify(body),
      });
    } catch (_) {
      status.textContent = tr('fb.network');
      status.className = 'feedback-status err';
      submit.disabled = false;
      return;
    }
    if (resp.status === 204) {
      status.textContent = tr('fb.sent');
      status.className = 'feedback-status ok';
      form.reset();
      // main.js turns the contact unit's fault LED green.
      form.dispatchEvent(new CustomEvent('feedback:sent', { bubbles: true }));
    } else if (resp.status === 429) {
      status.textContent = tr('fb.rate');
      status.className = 'feedback-status err';
    } else if (resp.status === 400 || resp.status === 422) {
      status.textContent = tr('fb.rejected');
      status.className = 'feedback-status err';
    } else if (resp.status === 403) {
      status.textContent = tr('fb.captcha');
      status.className = 'feedback-status err';
    } else {
      status.textContent = tr('fb.unknown');
      status.className = 'feedback-status err';
    }
    submit.disabled = false;
  });
})();
