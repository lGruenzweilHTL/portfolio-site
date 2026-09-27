// site/index.html — feedback form submit handler.
// Posts to /api/feedback with a fresh Turnstile token fetched on submit
// (see turnstile.js; '' when unavailable, which the dev bypass accepts).
(function () {
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
      invalid(messageInput, 'Please write a message first.');
      submit.disabled = false;
      return;
    }
    if (body.email && !emailInput.checkValidity()) {
      invalid(emailInput, "That email address doesn't look right. Fix it, or leave it empty.");
      submit.disabled = false;
      return;
    }
    // Any interactive challenge appears beside the button while this waits,
    // and the helper hides it again once the token is issued.
    status.textContent = 'Verifying…';
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
      status.textContent = 'Network error. Try again, or email me directly.';
      status.className = 'feedback-status err';
      submit.disabled = false;
      return;
    }
    if (resp.status === 204) {
      status.textContent = 'Feedback sent. Thanks.';
      status.className = 'feedback-status ok';
      form.reset();
    } else if (resp.status === 429) {
      status.textContent = "You're sending messages too fast. Wait a moment, or email me.";
      status.className = 'feedback-status err';
    } else if (resp.status === 400 || resp.status === 422) {
      status.textContent = "The server rejected the form. Check the email address, or email me directly.";
      status.className = 'feedback-status err';
    } else if (resp.status === 403) {
      status.textContent = 'Captcha check failed. Refresh and try again.';
      status.className = 'feedback-status err';
    } else {
      status.textContent = 'Something went wrong. Try again, or email me.';
      status.className = 'feedback-status err';
    }
    submit.disabled = false;
  });
})();
