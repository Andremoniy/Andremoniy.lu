(() => {
  'use strict';
  const greeting = 'Hello! Ask me about Andrey’s architecture, engineering, leadership or research experience.';
  const launcher = document.createElement('button');
  launcher.className = 'cv-chat-launcher'; launcher.textContent = 'Ask about my experience ↗';
  launcher.setAttribute('aria-controls', 'cv-chat'); launcher.setAttribute('aria-expanded', 'false');
  const panel = document.createElement('section');
  panel.id = 'cv-chat'; panel.className = 'cv-chat'; panel.hidden = true;
  panel.setAttribute('aria-label', 'CV assistant');
  panel.innerHTML = `<header><span class="bot-avatar">AI</span><strong>Ask about my experience</strong><button type="button" aria-label="Sign out" hidden>Sign out</button><button type="button" aria-label="Start a new conversation">↻</button><button type="button" aria-label="Collapse assistant">−</button></header><div class="cv-chat-log" role="log" aria-live="polite" aria-relevant="additions"></div><form class="cv-chat-compose"><input aria-label="Question about Andrey's experience" placeholder="Ask about my experience…" maxlength="1000" required autocomplete="off"><button type="submit" disabled>Send</button></form><p class="cv-chat-note">Name and company are required to start. AI answers may contain mistakes. Conversations and your introduction are saved; verified email is added after sign-in. AWS processes chat data and Cloudflare checks for abuse. Avoid confidential information. <a href="privacy.html">Privacy &amp; Cookies Notice</a>.</p>`;
  document.body.append(launcher, panel);
  const log = panel.querySelector('[role=log]'), form = panel.querySelector('.cv-chat-compose');
  const input = form.querySelector('input'), send = form.querySelector('button');
  const auth = window.portfolioAuth, signOut = panel.querySelector('[aria-label="Sign out"]');
  const stateKey = 'portfolio-chat-session', visitorKey = 'portfolio-chat-visitor', introductionKey = 'portfolio-chat-introduction';
  let session, visitor, introForm, loginMessage, activeController, endpoint, configuration, widget, turnstileLoading;
  let turnstileToken = '', busy = false, generation = 0, transcript = [], signingIn = false;
  const fallbackStorage = {getItem() {}, setItem() {}, removeItem() {}};
  let local, tab;
  try { local = window.localStorage; } catch { local = fallbackStorage; }
  try { tab = window.sessionStorage; } catch { tab = fallbackStorage; }
  function read(storage, key) {
    try { return JSON.parse(storage.getItem(key) || 'null'); } catch { return null; }
  }
  function write(storage, key, value) {
    try { if (value === null) storage.removeItem(key); else storage.setItem(key, JSON.stringify(value)); } catch { /* Memory-only mode. */ }
  }
  visitor = read(local, visitorKey);
  if (!visitor || visitor.expiresAt * 1000 <= Date.now()) { visitor = null; write(local, visitorKey, null); }
  let introduction = read(tab, introductionKey) || {};
  const config = (auth?.ready || Promise.resolve(null)).then(value => {
    configuration = value;
    if (value?.endpoint && new URL(value.endpoint).protocol === 'https:') endpoint = value.endpoint.replace(/\/$/, '');
    signOut.hidden = !auth?.getAccessToken(); return value;
  }).catch(() => null);
  function persist() { write(tab, stateKey, session ? {session, visitor, transcript, pending: input.value} : null); }
  function message(text, user = false, remember = true) {
    const item = document.createElement('div'); item.className = `cv-chat-message${user ? ' user' : ''}`;
    item.textContent = text; log.append(item); log.scrollTop = log.scrollHeight;
    if (remember) { transcript.push({text, user}); persist(); } return item;
  }
  function validField(value, maximum) {
    return value.trim().length > 0 && value.trim().length <= maximum && !/\p{C}/u.test(value) && /[\p{L}\p{N}]/u.test(value);
  }
  function updateStart() {
    if (introForm) introForm.querySelector('button').disabled = busy || !turnstileToken ||
      !validField(introForm.elements.name.value, 80) || !validField(introForm.elements.company.value, 120);
  }
  function removeWidget() {
    if (widget !== undefined && window.turnstile) window.turnstile.remove(widget);
    widget = undefined; turnstileToken = '';
  }
  async function loadTurnstile() {
    if (!introForm || panel.hidden) return;
    await config;
    if (!introForm) return;
    if (!configuration?.turnstileSiteKey) {
      introForm.querySelector('[role=status]').textContent = 'The assistant security check is not configured yet. Please try again later.'; return;
    }
    if (!window.turnstile) {
      if (!turnstileLoading) turnstileLoading = new Promise((resolve, reject) => {
        const script = document.createElement('script');
        script.src = 'https://challenges.cloudflare.com/turnstile/v0/api.js?render=explicit'; script.async = true;
        script.onload = resolve; script.onerror = () => reject(new Error('The security check could not load. Please reopen the chat to retry.'));
        document.head.append(script);
      }).catch(error => { turnstileLoading = undefined; throw error; });
      try { await turnstileLoading; } catch (error) { message(error.message, false, false); return; }
    }
    if (!introForm || widget !== undefined) return;
    widget = window.turnstile.render(introForm.querySelector('.cv-chat-verification'), {
      sitekey: configuration.turnstileSiteKey, action: 'chat_session', appearance: 'interaction-only',
      callback: token => { turnstileToken = token; updateStart(); },
      'expired-callback': () => { turnstileToken = ''; updateStart(); },
      'error-callback': () => {
        turnstileToken = ''; updateStart();
        if (introForm) introForm.querySelector('[role=status]').textContent = 'The security check failed. Please retry or reopen the chat.';
      },
    });
  }
  function showIntroduction() {
    removeWidget(); introForm?.remove(); introForm = document.createElement('form');
    introForm.className = 'cv-chat-introduction cv-chat-message';
    introForm.innerHTML = `<p>Before we start, could you please tell me your name and the company you represent?</p><label>Name<input name="name" maxlength="80" required autocomplete="name"></label><label>Company<input name="company" maxlength="120" required autocomplete="organization"></label><div class="cv-chat-verification"></div><p role="status"></p><button type="submit" disabled>Start chatting</button>`;
    introForm.elements.name.value = introduction.name || ''; introForm.elements.company.value = introduction.company || '';
    introForm.addEventListener('input', updateStart); introForm.addEventListener('submit', startSession);
    log.append(introForm); send.disabled = true; form.hidden = true;
    if (!panel.hidden) loadTurnstile();
  }
  function toggle(open) {
    panel.hidden = !open; launcher.setAttribute('aria-expanded', String(open));
    if (open) {
      if (introForm) { introForm.elements.name.focus(); loadTurnstile(); } else input.focus();
    } else launcher.focus();
  }
  function sessionHeaders(token = session?.sessionToken) {
    return {'Content-Type': 'application/json', Authorization: `Bearer ${token}`, 'X-Visitor-Token': visitor?.token || ''};
  }
  async function api(path, payload, headers, signal) {
    const response = await fetch(endpoint + path, {method: 'POST', credentials: 'omit', headers,
      body: JSON.stringify(payload), signal: signal || AbortSignal.timeout(15000)});
    return {response, data: await response.json().catch(() => ({}))};
  }
  async function upgradeSession() {
    const token = auth?.getAccessToken();
    if (!token || !session || session.authStatus === 'authenticated') return;
    const original = session, current = generation;
    const {response, data} = await api('/session/upgrade', {sessionToken: original.sessionToken}, sessionHeaders(token));
    if (generation !== current || session !== original) return;
    if (!response.ok) {
      if (response.status === 401) auth.clearAuthentication();
      throw new Error(data.error || 'Sign-in could not be linked to this conversation. Please try again.');
    }
    session = {...session, ...data}; persist(); signOut.hidden = false;
  }
  async function startSession(event) {
    event.preventDefault();
    if (busy || !introForm || !turnstileToken) return;
    const name = introForm.elements.name.value.trim(), company = introForm.elements.company.value.trim();
    if (!validField(name, 80) || !validField(company, 120)) {
      introForm.querySelector('[role=status]').textContent = 'Please enter both your name and company.'; return;
    }
    const current = generation; busy = true; updateStart();
    try {
      await config;
      if (!endpoint) throw new Error('The assistant is not connected yet. Please try again after deployment.');
      const {response, data} = await api('/session', {name, company, turnstileToken,
        ...(visitor ? {visitorToken: visitor.token} : {})}, {'Content-Type': 'application/json'});
      if (current !== generation) return;
      if (!response.ok) {
        if (response.status === 401) { visitor = null; write(local, visitorKey, null); }
        throw new Error(data.error || 'The chat could not start. Please try again.');
      }
      visitor = {token: data.visitorToken, expiresAt: data.visitorExpiresAt}; write(local, visitorKey, visitor);
      introduction = {name: name.replace(/\s+/g, ' '), company: company.replace(/\s+/g, ' ')}; write(tab, introductionKey, introduction);
      session = {...data, lastActivityAt: Date.now()}; persist();
      removeWidget(); introForm.remove(); introForm = undefined; form.hidden = false; await upgradeSession();
    } catch (error) {
      if (current === generation) {
        if (introForm) {
          introForm.querySelector('[role=status]').textContent = error.message;
          turnstileToken = ''; if (widget !== undefined) window.turnstile.reset(widget);
        } else { message(error.message); offerSignIn(input.value, false); }
      }
    } finally {
      if (current === generation) {
        busy = false; send.disabled = !session; updateStart();
        if (session && !introForm && !loginMessage && input.value.trim()) form.requestSubmit();
      }
    }
  }
  function closeConversation() {
    if (!endpoint || !session) return Promise.resolve();
    return fetch(endpoint, {method: 'POST', credentials: 'omit', keepalive: true,
      signal: AbortSignal.timeout(3000), headers: sessionHeaders(),
      body: JSON.stringify({action: 'close', sessionId: session.conversationId})}).catch(() => {});
  }
  function resetConversation(pending = '', close = true) {
    if (close) closeConversation(); activeController?.abort(); generation++; session = undefined; loginMessage = undefined;
    busy = false; transcript = []; log.replaceChildren(); message(greeting); input.value = pending; persist(); showIntroduction();
  }
  function offerSignIn(question, guestLimit = true) {
    loginMessage?.remove();
    loginMessage = message(guestLimit ? "You've reached the guest chat limit.\n\nTo protect this AI assistant from automated abuse and keep it freely available, we limit the number of questions from guest visitors.\n\nYou can sign in or create a free account to continue with a higher daily allowance." : 'Please sign in again to link your verified account and continue this conversation. Your question will be kept ready.', false, false);
    const button = document.createElement('button'); button.className = 'cv-chat-signin'; button.type = 'button'; button.textContent = 'Sign in / Register';
    button.addEventListener('click', async () => {
      button.disabled = true; input.value = question; persist(); signingIn = true;
      try { await auth.signIn(question); } catch (error) { signingIn = false; message(error.message); button.disabled = false; }
    });
    const later = document.createElement('button'); later.className = 'cv-chat-signin'; later.type = 'button'; later.textContent = 'Maybe later';
    later.addEventListener('click', () => { loginMessage?.remove(); loginMessage = undefined; input.focus(); });
    loginMessage.append(button, later); input.value = question; persist(); log.scrollTop = log.scrollHeight;
  }
  launcher.addEventListener('click', () => toggle(panel.hidden));
  panel.querySelector('[aria-label="Collapse assistant"]').addEventListener('click', () => toggle(false));
  panel.addEventListener('keydown', event => { if (event.key === 'Escape') toggle(false); });
  panel.querySelector('[aria-label="Start a new conversation"]').addEventListener('click', () => resetConversation());
  signOut.addEventListener('click', async () => {
    const closing = closeConversation(); resetConversation('', false); signOut.hidden = true; auth.clearAuthentication(); await closing;
    try { await auth.signOut(); } catch { message('You have signed out. You can start a new chat as a guest.'); }
  });
  document.querySelectorAll('a[href$="#assistant"]').forEach(link => link.addEventListener('click', event => { event.preventDefault(); toggle(true); }));
  const questions = {leadership: 'Has Andrey led engineering teams?', cloud: 'What professional AWS experience does Andrey have?', research: 'What is Andrey’s research background?'};
  document.querySelectorAll('[data-chat]').forEach(button => button.addEventListener('click', () => {
    toggle(true); input.value = questions[button.dataset.chat] || button.textContent; persist();
    if (session && !busy) form.requestSubmit();
  }));
  form.addEventListener('submit', async event => {
    event.preventDefault(); const question = input.value.trim();
    if (!question || busy) return;
    if (!session) { toggle(true); return; }
    if (Date.now() - session.lastActivityAt >= 300000 || session.expiresAt * 1000 <= Date.now()) {
      resetConversation(question); message('The previous conversation ended. Please confirm your introduction to start again.', false, false); return;
    }
    const current = generation; busy = true; send.disabled = true; input.value = ''; loginMessage?.remove(); loginMessage = undefined;
    if (transcript.at(-1)?.text !== question || !transcript.at(-1)?.user) message(question, true);
    const pending = message('Looking through my CV…', false, false);
    const controller = new AbortController(); activeController = controller;
    const timer = setTimeout(() => controller.abort(), 35000);
    try {
      await config; await upgradeSession();
      if (generation !== current) return;
      const {response, data} = await api('', {message: question, sessionId: session.conversationId}, sessionHeaders(), controller.signal);
      if (generation !== current) return;
      pending.remove();
      if (response.status === 401 || (response.status === 403 && !data.error) || data.sessionEnded) {
        resetConversation(question); message('Your chat session ended. Please confirm your introduction to continue.', false, false); return;
      }
      if (response.status === 429 && data.retryAfterSeconds) {
        const reset = data.quotaResetsAt ? new Date(data.quotaResetsAt).toLocaleString() : 'shortly';
        message(`${data.limitScope === 'global' ? 'The assistant has reached its shared allowance.' : 'Your chat allowance has been reached.'} Try again after ${reset}.`);
        if (data.offerSignIn === true && data.limitScope !== 'global') offerSignIn(question); else input.value = question;
        persist(); return;
      }
      if (!response.ok) throw new Error(data.error || 'The assistant is temporarily unavailable. Please try again.');
      if (typeof data.answer !== 'string') throw new Error('The assistant returned an unexpected response. Please try again.');
      session.lastActivityAt = Date.now(); message(data.answer); persist();
    } catch (error) {
      if (generation === current) {
        pending.remove(); message(error.name === 'AbortError' ? 'The request timed out. Please try again.' : error.message);
        input.value = question; persist();
      }
    } finally {
      clearTimeout(timer);
      if (generation === current) { activeController = undefined; busy = false; send.disabled = !session; log.scrollTop = log.scrollHeight; }
    }
  });
  input.addEventListener('input', persist);
  window.addEventListener('pagehide', () => {
    if (!signingIn) { closeConversation(); session = undefined; persist(); }
  });
  const saved = read(tab, stateKey);
  if (saved?.session?.expiresAt * 1000 > Date.now() && Date.now() - saved.session.lastActivityAt < 300000) {
    session = saved.session; visitor = saved.visitor || visitor;
    for (const entry of saved.transcript || []) message(entry.text, entry.user, false);
    transcript = saved.transcript || []; input.value = saved.pending || ''; send.disabled = false;
  } else { message(greeting); showIntroduction(); }
  config.then(() => {
    const question = auth?.consumePendingQuestion();
    if (question) { toggle(true); input.value = question; if (session) form.requestSubmit(); }
  });
})();
