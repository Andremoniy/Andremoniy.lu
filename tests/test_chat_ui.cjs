// Offline browser contract checks; network services are mocked, no AWS calls.
const assert = require('node:assert/strict');
const {readFileSync} = require('node:fs');
const {JSDOM} = require('jsdom');
const source = readFileSync(require('node:path').join(__dirname, '../assets/chat.js'), 'utf8');
const tick = () => new Promise(resolve => setImmediate(resolve));
const greeting = 'Hello! Ask me about Andrey’s architecture, engineering, leadership or research experience.';

async function harness({snapshot, signedIn = false, pending = '', blockedStorage = false} = {}) {
  const dom = new JSDOM('<a href="index.html#assistant">Ask</a><button data-chat="cloud">AWS</button>',
    {url: 'https://portfolio.example/', runScripts: 'outside-only'});
  const {window} = dom, calls = [], actions = [];
  window.AbortSignal = global.AbortSignal;
  Object.defineProperty(window.document, 'cookie', {get() { throw Error('Third-party cookies blocked'); }});
  if (blockedStorage) for (const kind of ['localStorage', 'sessionStorage']) {
    Object.defineProperty(window, kind, {get() { throw Error('Site storage blocked'); }});
  }
  if (snapshot) for (const [kind, entries] of Object.entries(snapshot)) {
    for (const [key, value] of Object.entries(entries)) window[kind].setItem(key, value);
  }
  let verification, status = 200, scope = '', createFails = false;
  window.turnstile = {
    render(container, options) { verification = options; return 0; },
    reset() { actions.push('turnstile-reset'); }, remove() { actions.push('turnstile-remove'); },
  };
  window.portfolioAuth = {
    ready: Promise.resolve({endpoint: 'https://api.example/chat', turnstileSiteKey: 'public-test-key'}),
    getAccessToken: () => signedIn ? 'cognito-access-token' : null,
    clearAuthentication() { signedIn = false; }, consumePendingQuestion() { const value = pending; pending = ''; return value; },
    async signIn(question) { actions.push({signIn: question}); }, async signOut() { signedIn = false; },
  };
  window.fetch = async (url, options) => {
    calls.push({url, ...options, body: JSON.parse(options.body)});
    if (url.endsWith('/session/upgrade')) return {ok: true, status: 200, json: async () => ({
      sessionToken: 'authenticated-chat-token', expiresAt: Math.floor(Date.now() / 1000) + 900,
      conversationId: 'conversation-123', authStatus: 'authenticated'})};
    if (url.endsWith('/session')) return {ok: !createFails, status: createFails ? 400 : 201, json: async () => createFails ? {
      error: 'The security check failed or expired. Please try it again.',
    } : {sessionToken: 'guest-chat-token', visitorToken: 'backend-issued-visitor',
      visitorExpiresAt: Math.floor(Date.now() / 1000) + 180 * 86400,
      conversationId: 'conversation-123', authStatus: 'guest', expiresAt: Math.floor(Date.now() / 1000) + 3600}};
    if (JSON.parse(options.body).action === 'close') return {ok: true, status: 200, json: async () => ({closed: true})};
    return {ok: status === 200, status, json: async () => status === 200 ? {
      answer: '<img src=x onerror=alert(1)> AWS experience', sessionId: 'conversation-123',
    } : status === 403 ? {message: 'Forbidden'} : status === 429 ? {retryAfterSeconds: 60, quotaResetsAt: '2026-10-31T00:00:00Z',
      limitScope: scope, offerSignIn: scope.startsWith('guest')} : {error: 'Unavailable'}};
  };
  window.eval(source); await tick();
  const doc = window.document, panel = doc.querySelector('.cv-chat'), compose = doc.querySelector('.cv-chat-compose');
  return {dom, window, doc, panel, calls, actions,
    open: async () => { doc.querySelector('a').click(); await tick(); },
    verify: () => verification.callback('verified-token'), expire: () => verification['expired-callback'](),
    limit: value => { status = 429; scope = value; }, failCreation: () => { createFails = true; },
    revoke: () => { status = 403; },
    ask: async (question = 'More details?') => {
      compose.querySelector('input').value = question;
      compose.dispatchEvent(new window.Event('submit', {cancelable: true})); await tick();
    },
    start: async (name = ' Alice ', company = ' Example Ltd ') => {
      const form = doc.querySelector('.cv-chat-introduction');
      form.elements.name.value = name; form.elements.company.value = company;
      form.dispatchEvent(new window.Event('input', {bubbles: true}));
      form.dispatchEvent(new window.Event('submit', {cancelable: true})); await tick();
    },
    snapshot: () => Object.fromEntries(['localStorage', 'sessionStorage'].map(kind => [kind,
      Object.fromEntries(Array.from({length: window[kind].length}, (_, i) => {
        const key = window[kind].key(i); return [key, window[kind].getItem(key)];
      }))])),
  };
}

async function run() {
  const h = await harness();
  assert.equal(h.panel.hidden, true);
  assert.equal(h.doc.querySelector('.cv-chat-message').textContent, greeting);
  assert.match(h.doc.querySelector('.cv-chat-introduction').textContent, /Before we start, could you please tell me your name and the company you represent\?/);
  await h.open(); h.verify();
  await h.start('', 'Example'); await h.start('Alice', '   '); await h.start('!!!', 'Example');
  assert.equal(h.calls.length, 0, 'Invalid introductions never create a session');
  h.doc.querySelector('[data-chat]').click(); await tick();
  assert.equal(h.calls.length, 0, 'Selected questions wait for establishment');
  await h.start();
  assert.equal(h.calls[0].url, 'https://api.example/chat/session');
  assert.equal(h.calls[0].body.name, 'Alice'); assert.equal(h.calls[0].body.company, 'Example Ltd');
  assert.equal(h.calls[0].body.turnstileToken, 'verified-token');
  assert.match(h.calls[1].body.message, /AWS/);
  assert.equal(h.calls[1].headers.Authorization, 'Bearer guest-chat-token');
  assert.equal(h.calls[1].headers['X-Visitor-Token'], 'backend-issued-visitor');
  assert.equal(h.calls[1].credentials, 'omit');
  assert.equal(h.doc.querySelector('.cv-chat-introduction'), null);
  assert.equal(h.panel.querySelector('img'), null, 'Responses render as text');
  await h.ask(); assert.equal(h.calls.at(-1).body.sessionId, 'conversation-123');
  h.limit('guest'); await h.ask('Pending question');
  assert.match(h.panel.textContent, /You've reached the guest chat limit/);
  const buttons = h.panel.querySelectorAll('.cv-chat-signin');
  assert.equal(buttons[0].textContent, 'Sign in / Register'); assert.equal(buttons[1].textContent, 'Maybe later');
  assert.match(buttons[0].previousSibling.textContent, /higher daily allowance/);
  buttons[0].click(); await tick();
  assert.equal(h.actions.at(-1).signIn, 'Pending question');
  const snapshot = h.snapshot();
  h.window.dispatchEvent(new h.window.Event('pagehide'));
  assert.notEqual(h.calls.at(-1).body.action, 'close', 'OAuth navigation preserves the conversation');
  const resumed = await harness({snapshot, signedIn: true, pending: 'Pending question'});
  await tick();
  assert.equal(resumed.calls[0].url, 'https://api.example/chat/session/upgrade');
  assert.equal(resumed.calls[0].body.sessionToken, 'guest-chat-token');
  assert.equal(resumed.calls[0].headers.Authorization, 'Bearer cognito-access-token');
  assert.equal(resumed.calls[1].body.message, 'Pending question');
  assert.equal(resumed.calls[1].headers.Authorization, 'Bearer authenticated-chat-token');
  assert.match(resumed.panel.textContent, /AWS experience/);
  resumed.limit('global'); await resumed.ask();
  assert.match(resumed.panel.textContent, /shared allowance/);
  assert.equal(resumed.panel.querySelector('.cv-chat-signin'), null, 'Global exhaustion never suggests sign-in');
  resumed.doc.querySelector('[aria-label="Sign out"]').click(); await tick();
  assert.ok(resumed.doc.querySelector('.cv-chat-introduction'), 'Sign-out returns to guest introduction');
  assert.equal(resumed.doc.querySelector('.cv-chat-compose button').disabled, true);
  h.dom.window.close(); resumed.dom.window.close();

  const failed = await harness(); await failed.open(); failed.verify(); failed.expire();
  assert.equal(failed.doc.querySelector('.cv-chat-introduction button').disabled, true);
  failed.verify(); failed.failCreation(); await failed.start();
  assert.equal(failed.calls.length, 1); assert.match(failed.panel.textContent, /failed or expired/);
  assert.ok(failed.actions.includes('turnstile-reset'));
  assert.equal(failed.doc.querySelector('.cv-chat-compose button').disabled, true);
  failed.dom.window.close();

  const authenticated = await harness({signedIn: true});
  assert.ok(authenticated.doc.querySelector('.cv-chat-introduction'), 'Authenticated visitors confirm introduction too');
  await authenticated.open(); authenticated.verify(); await authenticated.start();
  assert.equal(authenticated.calls[0].url, 'https://api.example/chat/session');
  assert.equal(authenticated.calls[1].url, 'https://api.example/chat/session/upgrade');
  authenticated.doc.querySelector('[aria-label="Start a new conversation"]').click(); await tick();
  assert.equal(authenticated.doc.querySelector('.cv-chat-introduction input[name=name]').value, 'Alice');
  assert.equal(authenticated.calls.at(-1).body.action, 'close');
  authenticated.dom.window.close();

  const reopened = await harness({snapshot: {localStorage: snapshot.localStorage}});
  await reopened.open(); reopened.verify(); await reopened.start();
  assert.equal(reopened.calls[0].body.visitorToken, 'backend-issued-visitor', 'Visitor identity persists across browser sessions');
  reopened.revoke(); await reopened.ask('Keep this question');
  assert.ok(reopened.doc.querySelector('.cv-chat-introduction'), 'Authorizer denial returns to session establishment');
  assert.equal(reopened.doc.querySelector('.cv-chat-compose input').value, 'Keep this question');
  reopened.dom.window.close();
  const memoryOnly = await harness({blockedStorage: true});
  await memoryOnly.open(); memoryOnly.verify(); await memoryOnly.start(); await memoryOnly.ask();
  assert.equal(memoryOnly.calls.at(-1).headers.Authorization, 'Bearer guest-chat-token');
  memoryOnly.dom.window.close();
  console.log('Chat DOM checks passed: introduction, guest session, Turnstile, cookies blocked, quotas, OAuth continuation, upgrade, reset and sign-out.');
}
run().catch(error => { console.error(error); process.exitCode = 1; });
