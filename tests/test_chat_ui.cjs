// Offline DOM tests. Requires jsdom in Node's module search path; no AWS calls.
const assert = require('node:assert/strict');
const {readFileSync} = require('node:fs');
const {JSDOM} = require('jsdom');
const source = readFileSync(require('node:path').join(__dirname, '../assets/chat.js'), 'utf8');
const tick = () => new Promise(resolve => setImmediate(resolve));

async function run() {
  const dom = new JSDOM('<a href="index.html#assistant">Ask</a><button data-chat="cloud">AWS</button>',
    {url: 'https://portfolio.example/career.html', runScripts: 'outside-only'});
  const {window} = dom;
  const calls = [];
  let fail = false;
  let signedIn = false;
  let signInQuestion;
  window.portfolioAuth = {
    ready: Promise.resolve({endpoint: 'https://api.example/chat'}),
    getAccessToken: () => signedIn ? 'signed-access-token' : null,
    signIn: async question => { signInQuestion = question; },
    signOut: async () => { signedIn = false; },
    clearAuthentication: () => { signedIn = false; },
    consumePendingQuestion: () => '',
  };
  window.fetch = async (url, options) => {
    if (!options?.body) return {ok: true, json: async () => ({endpoint: 'https://api.example/chat'})};
    calls.push({...JSON.parse(options.body), authorization: options.headers.Authorization});
    return {ok: !fail, status: fail ? 429 : 200, json: async () => fail ? {} : {
      answer: '<img src=x onerror=alert(1)> AWS experience', sessionId: 'session-123',
      sources: ['CV: <script>bad()</script>']}};
  };
  window.eval(source);
  await tick();
  const doc = window.document;
  const panel = doc.querySelector('.cv-chat');
  const launcher = doc.querySelector('.cv-chat-launcher');
  assert.equal(panel.hidden, true);
  doc.querySelector('a').click();
  assert.equal(panel.hidden, false);
  assert.equal(launcher.getAttribute('aria-expanded'), 'true');
  assert.equal(doc.activeElement, doc.querySelector('input'));
  doc.querySelector('[data-chat]').click();
  await tick();
  assert.equal(calls.length, 0);
  assert.match(panel.textContent, /Sign in to continue/);
  panel.querySelector('.cv-chat-signin').click();
  await tick();
  assert.match(signInQuestion, /AWS/);
  signedIn = true;
  panel.querySelector('[aria-label="Start a new conversation"]').click();
  doc.querySelector('[data-chat]').click();
  await tick();
  assert.match(calls[0].message, /AWS/);
  assert.equal(calls[0].authorization, 'Bearer signed-access-token');
  assert.equal(calls[0].sessionId, undefined);
  assert.equal(panel.querySelector('img'), null);
  assert.equal(panel.querySelector('script'), null);
  assert.equal(panel.querySelector('details'), null);
  assert.equal(panel.querySelector('[role=log]').textContent.includes('CV passages used'), false);
  assert.equal(panel.querySelector('[role=log]').textContent.includes('CV: <script>'), false);
  assert.match(panel.querySelector('[role=log]').textContent, /AWS experience/);
  const input = panel.querySelector('input');
  input.value = 'Tell me more';
  panel.querySelector('form').dispatchEvent(new window.Event('submit', {cancelable: true}));
  await tick();
  assert.equal(calls[1].sessionId, 'session-123');
  panel.querySelector('[aria-label="Collapse assistant"]').click();
  assert.equal(panel.hidden, true);
  launcher.click();
  assert.equal(panel.querySelectorAll('.user').length, 2);
  panel.querySelector('[aria-label="Start a new conversation"]').click();
  assert.equal(panel.querySelectorAll('.user').length, 0);
  fail = true;
  input.value = 'Retry question';
  panel.querySelector('form').dispatchEvent(new window.Event('submit', {cancelable: true}));
  await tick();
  assert.deepEqual(calls[2], {action: 'close', sessionId: 'session-123', authorization: 'Bearer signed-access-token'});
  assert.equal(calls[3].sessionId, undefined);
  assert.match(panel.querySelector('[role=log]').textContent, /busy/);
  assert.equal(input.value, 'Retry question');
  assert.equal(panel.querySelector('button[type=submit]').disabled, false);
  panel.querySelector('[aria-label="Sign out"]').click();
  await tick();
  assert.equal(signedIn, false);
  dom.window.close();

  const resumedDom = new JSDOM('', {url: 'https://portfolio.example/career.html', runScripts: 'outside-only'});
  let restoredQuestion = 'Does he manage people?';
  let resumedToken = 'returned-access-token';
  const resumedRequests = [];
  let responseStatus = 200;
  let globalLimit = false;
  resumedDom.window.portfolioAuth = {
    ready: Promise.resolve({endpoint: 'https://api.example/chat'}),
    getAccessToken: () => resumedToken,
    clearAuthentication: () => { resumedToken = null; },
    signIn: async () => {}, signOut: async () => {},
    consumePendingQuestion: () => { const question = restoredQuestion; restoredQuestion = ''; return question; },
  };
  resumedDom.window.fetch = async (url, options) => {
    resumedRequests.push(JSON.parse(options.body));
    return {ok: responseStatus === 200, status: responseStatus, json: async () => responseStatus === 200 ? {
      answer: 'Yes, he managed engineers.', sessionId: 'resumed-session',
    } : responseStatus === 429 ? {retryAfterSeconds: 61, limitScope: globalLimit ? 'global' : 'user'} : {}};
  };
  resumedDom.window.eval(source);
  await tick();
  assert.equal(resumedRequests.length, 1);
  assert.equal(resumedRequests[0].message, 'Does he manage people?');
  const resumedPanel = resumedDom.window.document.querySelector('.cv-chat');
  assert.equal(resumedPanel.hidden, false);
  responseStatus = 429;
  resumedPanel.querySelector('input').value = 'More details';
  resumedPanel.querySelector('form').dispatchEvent(new resumedDom.window.Event('submit', {cancelable: true}));
  await tick();
  assert.match(resumedPanel.textContent, /about 2 minutes/);
  globalLimit = true;
  resumedPanel.querySelector('input').value = 'One more question';
  resumedPanel.querySelector('form').dispatchEvent(new resumedDom.window.Event('submit', {cancelable: true}));
  await tick();
  assert.match(resumedPanel.textContent, /shared allowance/);
  responseStatus = 401;
  resumedPanel.querySelector('input').value = 'Try again';
  resumedPanel.querySelector('form').dispatchEvent(new resumedDom.window.Event('submit', {cancelable: true}));
  await tick();
  assert.equal(resumedToken, null);
  assert.ok(resumedPanel.querySelector('.cv-chat-signin'));
  resumedDom.window.close();
  const historyDom = new JSDOM('', {url: 'https://portfolio.example/', runScripts: 'outside-only'});
  const historyWindow = historyDom.window;
  const historyRequests = [];
  let currentTime = 1000000;
  historyWindow.Date.now = () => currentTime;
  historyWindow.portfolioAuth = {
    ready: Promise.resolve({endpoint: 'https://api.example/chat'}),
    getAccessToken: () => 'signed-access-token',
    consumePendingQuestion: () => '',
    signOut: async () => {},
  };
  historyWindow.fetch = async (url, options) => {
    const request = JSON.parse(options.body);
    historyRequests.push({...request, keepalive: options.keepalive});
    return {ok: true, status: 200, json: async () => ({answer: 'Answer', sessionId: 'history-session'})};
  };
  historyWindow.eval(source);
  await tick();
  async function askHistoryQuestion() {
    historyWindow.document.querySelector('input').value = 'Experience?';
    historyWindow.document.querySelector('form').dispatchEvent(new historyWindow.Event('submit', {cancelable: true}));
    await tick();
  }
  await askHistoryQuestion();
  currentTime += 300001;
  await askHistoryQuestion();
  assert.equal(historyRequests[1].sessionId, undefined);
  assert.match(historyWindow.document.querySelector('[role=log]').textContent, /five minutes of inactivity/);
  historyWindow.document.querySelector('[aria-label="Collapse assistant"]').click();
  assert.equal(historyRequests.length, 2);
  historyWindow.dispatchEvent(new historyWindow.Event('pagehide'));
  await tick();
  assert.equal(historyRequests[2].action, 'close');
  assert.equal(historyRequests[2].keepalive, true);
  await askHistoryQuestion();
  assert.equal(historyRequests[3].sessionId, undefined);
  historyWindow.document.querySelector('[aria-label="Sign out"]').click();
  await tick();
  assert.equal(historyRequests[4].action, 'close');
  historyDom.window.close();
  console.log('Chat DOM checks passed: anonymous sign-in gate, saved question, bearer token, follow-up, reset, safe text, hidden sources, throttle/retry, sign-out.');
}
run().catch(error => { console.error(error); process.exitCode = 1; });
