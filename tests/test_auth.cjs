const assert = require('node:assert/strict');
const {readFileSync} = require('node:fs');
const {webcrypto, createHash} = require('node:crypto');
const {runInNewContext} = require('node:vm');
const {JSDOM} = require('jsdom');
const source = readFileSync(require('node:path').join(__dirname, '../assets/auth.js'), 'utf8');
const configuration = {
  endpoint: 'https://api.example/chat',
  auth: {domain: 'https://login.example.com', clientId: 'public-client'},
};

function createSession(path = '/career.html', storedValues = {}) {
  const dom = new JSDOM('<p id="authStatus"></p>', {url: 'https://portfolio.example'});
  Object.entries(storedValues).forEach(([key, value]) => dom.window.sessionStorage.setItem(key, JSON.stringify(value)));
  const url = new URL(path, 'https://portfolio.example');
  const navigation = [];
  const tokenCalls = [];
  const historyCalls = [];
  const location = {
    origin: url.origin, pathname: url.pathname, search: url.search,
    assign: destination => navigation.push(destination),
    replace: destination => navigation.push(destination),
  };
  const fetch = async (destination, options) => {
    if (!options?.body) return {ok: true, json: async () => configuration};
    tokenCalls.push({destination: String(destination), options});
    return {ok: true, json: async () => ({access_token: 'access-only', refresh_token: 'discard-me', expires_in: 900})};
  };
  runInNewContext(source, {
    window: dom.window, document: dom.window.document, sessionStorage: dom.window.sessionStorage,
    location, history: {replaceState: (...parameters) => historyCalls.push(parameters)},
    fetch, URL, URLSearchParams, TextEncoder, Uint8Array, AbortSignal,
    crypto: webcrypto, btoa: value => Buffer.from(value, 'binary').toString('base64'),
  });
  return {dom, auth: dom.window.portfolioAuth, storage: dom.window.sessionStorage,
    navigation, tokenCalls, historyCalls};
}

async function run() {
  const initial = createSession();
  await initial.auth.ready;
  assert.equal(initial.auth.getAccessToken(), null);
  await initial.auth.signIn('Does he manage people?');
  const flow = JSON.parse(initial.storage.getItem('portfolio-chat-login'));
  const destination = new URL(initial.navigation[0]);
  assert.equal(destination.origin, configuration.auth.domain);
  assert.equal(destination.pathname, '/oauth2/authorize');
  assert.equal(destination.searchParams.get('response_type'), 'code');
  assert.equal(destination.searchParams.get('code_challenge_method'), 'S256');
  assert.equal(destination.searchParams.get('code_challenge'), createHash('sha256').update(flow.verifier).digest('base64url'));
  assert.equal(destination.searchParams.get('state'), flow.state);
  assert.match(destination.searchParams.get('scope'), /portfolio-chat\/ask/);
  assert.equal(flow.returnPath, '/career.html');
  assert.equal(flow.question, 'Does he manage people?');

  const callback = createSession('/auth-callback.html?code=oauth-code&state=' + flow.state,
    {'portfolio-chat-login': flow});
  await callback.auth.ready;
  assert.equal(callback.tokenCalls.length, 1);
  assert.equal(callback.tokenCalls[0].options.body.get('code_verifier'), flow.verifier);
  assert.equal(callback.tokenCalls[0].options.body.get('redirect_uri'), 'https://portfolio.example/auth-callback.html');
  assert.equal(callback.navigation[0], '/career.html');
  assert.equal(callback.storage.getItem('portfolio-chat-login'), null);
  assert.equal(callback.auth.getAccessToken(), 'access-only');
  assert.equal(callback.storage.getItem('portfolio-chat-auth').includes('discard-me'), false);
  assert.equal(callback.auth.consumePendingQuestion(), 'Does he manage people?');
  assert.equal(callback.auth.consumePendingQuestion(), '');
  assert.equal(callback.historyCalls[0][2], '/auth-callback.html');

  const mismatch = createSession('/auth-callback.html?code=oauth-code&state=wrong',
    {'portfolio-chat-login': flow});
  await mismatch.auth.ready;
  assert.equal(mismatch.tokenCalls.length, 0);
  assert.match(mismatch.auth.error, /could not be verified/);

  const expired = createSession('/auth-callback.html?code=oauth-code&state=' + flow.state,
    {'portfolio-chat-login': {...flow, expiresAt: 1}});
  await expired.auth.ready;
  assert.equal(expired.tokenCalls.length, 0);

  const unsafeReturn = createSession('/auth-callback.html?code=oauth-code&state=' + flow.state,
    {'portfolio-chat-login': {...flow, returnPath: '//attacker.example'}});
  await unsafeReturn.auth.ready;
  assert.equal(unsafeReturn.navigation[0], '/');
  callback.storage.setItem('portfolio-chat-auth', JSON.stringify({accessToken: 'expired', expiresAt: 1}));
  assert.equal(callback.auth.getAccessToken(), null);
  assert.equal(callback.storage.getItem('portfolio-chat-auth'), null);
  await initial.auth.signOut();
  assert.equal(new URL(initial.navigation.at(-1)).pathname, '/logout');
  const blocked = createSession();
  await blocked.auth.ready;
  for (const method of ['getItem', 'removeItem']) {
    Object.defineProperty(Object.getPrototypeOf(blocked.storage), method, {value() { throw Error('Storage blocked'); }});
  }
  assert.equal(blocked.auth.getAccessToken(), null, 'Blocked storage still permits guest mode');
  assert.equal(blocked.auth.consumePendingQuestion(), '');
  assert.doesNotThrow(() => blocked.auth.clearAuthentication());
  [initial, callback, mismatch, expired, unsafeReturn, blocked].forEach(session => session.dom.window.close());
  console.log('OAuth checks passed: PKCE, state, expiry, token exchange, saved question, safe return path, sign-out.');
}
run().catch(error => { console.error(error); process.exitCode = 1; });
