(() => {
  'use strict';
  const launcher = document.createElement('button');
  launcher.className = 'cv-chat-launcher';
  launcher.textContent = 'Ask about my experience ↗';
  launcher.setAttribute('aria-controls', 'cv-chat');
  launcher.setAttribute('aria-expanded', 'false');
  const panel = document.createElement('section');
  panel.id = 'cv-chat'; panel.className = 'cv-chat'; panel.hidden = true;
  panel.setAttribute('aria-label', 'CV assistant');
  panel.innerHTML = `<header><span class="bot-avatar">AI</span><strong>Ask about my experience</strong><button type="button" aria-label="Sign out" hidden>Sign out</button><button type="button" aria-label="Start a new conversation">↻</button><button type="button" aria-label="Collapse assistant">−</button></header><div class="cv-chat-log" role="log" aria-live="polite" aria-relevant="additions"></div><form><input aria-label="Question about Andrey's experience" placeholder="Ask about my experience…" maxlength="1000" required autocomplete="off"><button type="submit">Send</button></form><p class="cv-chat-note">Sign in to chat. Your profile name personalizes replies. AI answers may contain mistakes. Questions and your name are processed by AWS Bedrock; avoid confidential information.</p>`;
  document.body.append(launcher, panel);
  const log = panel.querySelector('[role=log]');
  const form = panel.querySelector('form');
  const input = form.querySelector('input');
  const send = form.querySelector('button');
  const auth = window.portfolioAuth;
  const signOut = panel.querySelector('[aria-label="Sign out"]');
  let sessionId, activeController, busy = false, generation = 0, loginMessage;
  let endpoint;
  const config = (auth?.ready || Promise.resolve(null)).then(configuration => {
    if (configuration?.endpoint && new URL(configuration.endpoint).protocol === 'https:') endpoint = configuration.endpoint;
    signOut.hidden = !auth?.getAccessToken();
    return configuration;
  }).catch(() => null);
  function message(text, user = false) {
    const item = document.createElement('div');
    item.className = `cv-chat-message${user ? ' user' : ''}`;
    item.textContent = text; log.append(item); log.scrollTop = log.scrollHeight;
    return item;
  }
  function toggle(open) {
    panel.hidden = !open; launcher.setAttribute('aria-expanded', String(open));
    if (open) input.focus(); else launcher.focus();
  }
  function requireSignIn(question) {
    loginMessage?.remove();
    loginMessage = message('Sign in to continue. Your question will be kept ready.');
    const button = document.createElement('button');
    button.className = 'cv-chat-signin'; button.type = 'button'; button.textContent = 'Sign in to continue';
    button.addEventListener('click', async () => {
      button.disabled = true;
      try { await auth.signIn(question); }
      catch (error) { message(error.message); button.disabled = false; }
    });
    loginMessage.append(button);
    input.value = question;
  }
  message('Hello! Ask me about Andrey’s architecture, engineering, leadership or research experience.');
  launcher.addEventListener('click', () => toggle(panel.hidden));
  panel.querySelector('[aria-label="Collapse assistant"]').addEventListener('click', () => toggle(false));
  panel.addEventListener('keydown', e => { if (e.key === 'Escape') toggle(false); });
  panel.querySelector('[aria-label="Start a new conversation"]').addEventListener('click', () => {
    activeController?.abort();
    generation++; sessionId = undefined; loginMessage = undefined; log.replaceChildren();
    message('New conversation. What would you like to know about my experience?');
    busy = false; send.disabled = false; input.focus();
  });
  signOut.addEventListener('click', async () => {
    generation++; activeController?.abort(); sessionId = undefined; busy = false;
    send.disabled = false; signOut.hidden = true; log.replaceChildren();
    message('You have signed out. Sign in again to continue chatting.');
    try { await auth.signOut(); } catch { auth.clearAuthentication(); }
  });
  document.querySelectorAll('a[href$="#assistant"]').forEach(a => a.addEventListener('click', e => {
    e.preventDefault(); toggle(true);
  }));
  const questions = {leadership: 'Has Andrey led engineering teams?', cloud: 'What professional AWS experience does Andrey have?', research: 'What is Andrey’s research background?'};
  document.querySelectorAll('[data-chat]').forEach(button => button.addEventListener('click', () => {
    toggle(true); input.value = questions[button.dataset.chat] || button.textContent;
    if (!busy) form.requestSubmit();
  }));
  form.addEventListener('submit', async e => {
    e.preventDefault();
    const question = input.value.trim();
    if (!question || busy) return;
    const current = generation;
    const conversation = sessionId;
    busy = true; send.disabled = true; input.value = '';
    message(question, true);
    const pending = message('Looking through my CV…');
    const controller = new AbortController();
    activeController = controller;
    const timer = setTimeout(() => controller.abort(), 35000);
    try {
      await config;
      if (generation !== current) return;
      if (!endpoint) throw new Error('The assistant is not connected yet. Please try again after deployment.');
      const accessToken = auth?.getAccessToken();
      if (!accessToken) {
        pending.remove(); requireSignIn(question); return;
      }
      loginMessage?.remove(); loginMessage = undefined;
      signOut.hidden = false;
      const response = await fetch(endpoint, {method: 'POST', signal: controller.signal,
        headers: {'Content-Type': 'application/json', Authorization: `Bearer ${accessToken}`},
        body: JSON.stringify({message: question, ...(conversation ? {sessionId: conversation} : {})})});
      const data = await response.json().catch(() => ({}));
      if (response.status === 401) {
        if (generation !== current) return;
        auth.clearAuthentication(); signOut.hidden = true; sessionId = undefined;
        pending.remove(); requireSignIn(question); return;
      }
      if (response.status === 429 && data.retryAfterSeconds) {
        const minutes = Math.max(1, Math.ceil(data.retryAfterSeconds / 60));
        throw new Error(`Your chat limit has been reached. Try again in about ${minutes} minute${minutes === 1 ? '' : 's'}.`);
      }
      if (!response.ok) throw new Error(response.status === 429 ? 'The assistant is busy. Please wait a moment and try again.' : data.error || 'The assistant is temporarily unavailable. Please try again.');
      if (typeof data.answer !== 'string') throw new Error('The assistant returned an unexpected response. Please try again.');
      if (generation !== current) return;
      sessionId = data.sessionId; pending.textContent = data.answer;
    } catch (error) {
      if (generation === current) {
        pending.textContent = error.name === 'AbortError' ? 'The request timed out. Please try again.' : error.message;
        input.value = question;
      }
    } finally {
      clearTimeout(timer);
      if (generation === current) { activeController = undefined; busy = false; send.disabled = false; log.scrollTop = log.scrollHeight; }
    }
  });
  config.then(() => {
    const pendingQuestion = auth?.consumePendingQuestion();
    if (pendingQuestion) { toggle(true); input.value = pendingQuestion; form.requestSubmit(); }
  });
})();
