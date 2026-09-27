// site/index.html — feedback form submit handler.
// Posts to /api/feedback with a fresh Turnstile token fetched on submit
// (see turnstile.js; '' when unavailable, which the dev bypass accepts).
(function () {
  const form = document.getElementById('feedback-form');
  if (!form) return;
  const status = document.getElementById('feedback-status');
  const submit = document.getElementById('feedback-submit');
  const turnstileBox = document.getElementById('feedback-turnstile');

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
      status.textContent = 'Message is required.';
      status.className = 'feedback-status err';
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
      status.textContent = 'Network error. Try again.';
      status.className = 'feedback-status err';
      submit.disabled = false;
      return;
    }
    if (resp.status === 204) {
      status.textContent = 'Thanks — got it.';
      status.className = 'feedback-status ok';
      form.reset();
    } else if (resp.status === 429) {
      status.textContent = 'You\'re sending too fast. Try again in a minute.';
      status.className = 'feedback-status err';
    } else if (resp.status === 403) {
      status.textContent = 'Captcha failed. Refresh and try again.';
      status.className = 'feedback-status err';
    } else {
      status.textContent = 'Server error. Try again later.';
      status.className = 'feedback-status err';
    }
    submit.disabled = false;
  });
})();
