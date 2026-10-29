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
  panel.innerHTML = `<header><span class="bot-avatar">AI</span><strong>Ask about my experience</strong><button type="button" aria-label="Start a new conversation">↻</button><button type="button" aria-label="Collapse assistant">−</button></header><div class="cv-chat-log" role="log" aria-live="polite" aria-relevant="additions"></div><form><input aria-label="Question about Andrey's experience" placeholder="Ask about my experience…" maxlength="1000" required autocomplete="off"><button type="submit">Send</button></form><p class="cv-chat-note">AI answers based on my CV may contain mistakes. Questions are processed by AWS Bedrock. Avoid sharing personal or confidential information.</p>`;
  document.body.append(launcher, panel);
  const log = panel.querySelector('[role=log]');
  const form = panel.querySelector('form');
  const input = form.querySelector('input');
  const send = form.querySelector('button');
  let sessionId, activeController, busy = false, generation = 0;
  let endpoint;
  const config = fetch('assets/chat-config.json', {cache: 'no-store'})
    .then(r => r.ok ? r.json() : null)
    .then(c => { if (c?.endpoint && new URL(c.endpoint).protocol === 'https:') endpoint = c.endpoint; })
    .catch(() => {});
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
  message('Hello! Ask me about Andrey’s architecture, engineering, leadership or research experience.');
  launcher.addEventListener('click', () => toggle(panel.hidden));
  panel.querySelector('[aria-label="Collapse assistant"]').addEventListener('click', () => toggle(false));
  panel.addEventListener('keydown', e => { if (e.key === 'Escape') toggle(false); });
  panel.querySelector('[aria-label="Start a new conversation"]').addEventListener('click', () => {
    activeController?.abort();
    generation++; sessionId = undefined; log.replaceChildren();
    message('New conversation. What would you like to know about my experience?');
    busy = false; send.disabled = false; input.focus();
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
      const response = await fetch(endpoint, {method: 'POST', signal: controller.signal,
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({message: question, ...(conversation ? {sessionId: conversation} : {})})});
      const data = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(response.status === 429 ? 'The assistant is busy. Please wait a moment and try again.' : data.error || 'The assistant is temporarily unavailable. Please try again.');
      if (typeof data.answer !== 'string') throw new Error('The assistant returned an unexpected response. Please try again.');
      if (generation !== current) return;
      sessionId = data.sessionId; pending.textContent = data.answer;
      if (data.sources?.length) {
        const details = document.createElement('details');
        const summary = document.createElement('summary'); summary.textContent = 'CV passages used'; details.append(summary);
        data.sources.forEach(text => { const quote = document.createElement('blockquote'); quote.textContent = text; details.append(quote); });
        pending.append(details);
      }
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
})();
