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
  window.fetch = async (url, options) => {
    if (!options?.body) return {ok: true, json: async () => ({endpoint: 'https://api.example/chat'})};
    calls.push(JSON.parse(options.body));
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
  assert.match(calls[0].message, /AWS/);
  assert.equal(calls[0].sessionId, undefined);
  assert.equal(panel.querySelector('img'), null);
  assert.equal(panel.querySelector('script'), null);
  assert.match(panel.querySelector('details').textContent, /CV:/);
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
  assert.equal(calls[2].sessionId, undefined);
  assert.match(panel.querySelector('[role=log]').textContent, /busy/);
  assert.equal(input.value, 'Retry question');
  assert.equal(panel.querySelector('button[type=submit]').disabled, false);
  dom.window.close();
  console.log('Chat DOM checks passed: open/collapse, sample send, follow-up, reset, XSS-safe evidence, throttle/retry.');
}
run().catch(error => { console.error(error); process.exitCode = 1; });
