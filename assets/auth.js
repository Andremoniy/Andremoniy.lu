(() => {
  'use strict';
  const tokenKey = 'portfolio-chat-auth';
  const flowKey = 'portfolio-chat-login';
  const resumeKey = 'portfolio-chat-resume';
  let configuration;
  let initializationError;

  function removeStoredValue(key) {
    try { sessionStorage.removeItem(key); } catch { /* Guest access can operate without browser storage. */ }
  }

  function readStoredValue(key) {
    try { return JSON.parse(sessionStorage.getItem(key) || 'null'); }
    catch { removeStoredValue(key); return null; }
  }

  function getAccessToken() {
    const session = readStoredValue(tokenKey);
    if (!session || typeof session.accessToken !== 'string' || session.expiresAt <= Date.now() + 30000) {
      removeStoredValue(tokenKey);
      return null;
    }
    return session.accessToken;
  }

  function base64Url(bytes) {
    return btoa(String.fromCharCode(...bytes)).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '');
  }

  function randomValue() {
    return base64Url(crypto.getRandomValues(new Uint8Array(32)));
  }

  function sameOriginPath(path) {
    if (typeof path !== 'string' || !path.startsWith('/') || path.startsWith('//')) return '/';
    const target = new URL(path, location.origin);
    return target.origin === location.origin && target.pathname !== '/auth-callback.html' ? target.pathname + target.search : '/';
  }

  async function signIn(question) {
    await ready;
    if (!configuration?.auth?.domain || !configuration.auth.clientId) {
      throw new Error('Sign-in is not configured yet. Please try again later.');
    }
    const verifier = randomValue();
    const state = randomValue();
    const redirectUri = location.origin + '/auth-callback.html';
    const challenge = base64Url(new Uint8Array(await crypto.subtle.digest('SHA-256', new TextEncoder().encode(verifier))));
    sessionStorage.setItem(flowKey, JSON.stringify({
      verifier, state, redirectUri, question: String(question || '').slice(0, 1000),
      returnPath: sameOriginPath(location.pathname + location.search), expiresAt: Date.now() + 600000,
    }));
    const destination = new URL('/oauth2/authorize', configuration.auth.domain);
    destination.search = new URLSearchParams({
      response_type: 'code', client_id: configuration.auth.clientId, redirect_uri: redirectUri,
      scope: 'openid email profile portfolio-chat/ask', state,
      code_challenge_method: 'S256', code_challenge: challenge,
    }).toString();
    location.assign(destination.href);
  }

  function clearAuthentication() {
    removeStoredValue(tokenKey);
  }

  async function signOut() {
    await ready;
    clearAuthentication();
    removeStoredValue(flowKey);
    removeStoredValue(resumeKey);
    if (!configuration?.auth) return;
    const destination = new URL('/logout', configuration.auth.domain);
    destination.search = new URLSearchParams({
      client_id: configuration.auth.clientId, logout_uri: location.origin + '/',
    }).toString();
    location.assign(destination.href);
  }

  function consumePendingQuestion() {
    const pending = readStoredValue(resumeKey);
    removeStoredValue(resumeKey);
    return pending?.expiresAt > Date.now() && typeof pending.question === 'string' ? pending.question : '';
  }

  async function finishSignIn() {
    const parameters = new URLSearchParams(location.search);
    const flow = readStoredValue(flowKey);
    removeStoredValue(flowKey);
    history.replaceState(null, '', '/auth-callback.html');
    if (!flow || flow.expiresAt <= Date.now() || parameters.get('state') !== flow.state) {
      throw new Error('This sign-in request has expired or could not be verified. Return to the portfolio and try again.');
    }
    if (parameters.has('error')) {
      throw new Error('Sign-in was cancelled or declined. Return to the portfolio to try again.');
    }
    const code = parameters.get('code');
    if (!code || !configuration?.auth || flow.redirectUri !== location.origin + '/auth-callback.html') {
      throw new Error('The sign-in response is incomplete. Return to the portfolio and try again.');
    }
    const response = await fetch(new URL('/oauth2/token', configuration.auth.domain), {
      method: 'POST', headers: {'Content-Type': 'application/x-www-form-urlencoded'},
      credentials: 'omit', signal: AbortSignal.timeout(15000),
      body: new URLSearchParams({grant_type: 'authorization_code', code,
        client_id: configuration.auth.clientId, redirect_uri: flow.redirectUri, code_verifier: flow.verifier}),
    });
    const tokens = await response.json();
    if (!response.ok || typeof tokens.access_token !== 'string' || !(Number(tokens.expires_in) > 0)) {
      throw new Error('Sign-in could not be completed. Return to the portfolio and try again.');
    }
    sessionStorage.setItem(tokenKey, JSON.stringify({accessToken: tokens.access_token,
      expiresAt: Date.now() + Number(tokens.expires_in) * 1000}));
    sessionStorage.setItem(resumeKey, JSON.stringify({question: flow.question, expiresAt: Date.now() + 600000}));
    location.replace(sameOriginPath(flow.returnPath));
  }

  async function initialize() {
    try {
      const response = await fetch('/assets/chat-config.json', {cache: 'no-store'});
      if (!response.ok) throw new Error('The assistant configuration is unavailable.');
      configuration = await response.json();
      if (configuration?.auth) {
        const authDomain = new URL(configuration.auth.domain);
        if (authDomain.protocol !== 'https:' || !configuration.auth.clientId) {
          throw new Error('The sign-in configuration is invalid.');
        }
      }
      if (location.pathname === '/auth-callback.html') await finishSignIn();
      return configuration;
    } catch (error) {
      initializationError = error.message;
      const status = document.getElementById('authStatus');
      if (status) status.textContent = initializationError;
      return null;
    }
  }

  const ready = initialize();
  window.portfolioAuth = {
    ready, getAccessToken, signIn, signOut, clearAuthentication, consumePendingQuestion,
    get error() { return initializationError; },
  };
})();
