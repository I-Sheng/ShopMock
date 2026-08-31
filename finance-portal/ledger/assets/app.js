/* ShopMock Finance portal.
 *
 * OIDC authorization code + PKCE against the finance-portal client, implemented
 * directly against the realm's endpoints — no third-party script is loaded, so
 * the page runs under a CSP with no 'unsafe-inline' and no external origins.
 *
 * Nothing here is a security control: the browser only decides what to draw.
 * Every answer comes from this service's own API, which verifies the token,
 * the client it was minted for and the `finance` realm role server-side. A 403
 * rendered as a friendly panel is still a 403 that was decided on the server.
 */
(function () {
  'use strict';

  var cfg = document.body.dataset;
  // Works whether the edge delivered /finance or /finance/ — both resolve to /finance/.
  var BASE = window.location.pathname.replace(/\/?$/, '/');
  var REDIRECT_URI = window.location.origin + BASE;
  var REALM = window.location.origin + '/auth/realms/' + cfg.realm;
  var AUTHORIZE = REALM + '/protocol/openid-connect/auth';
  var TOKEN = REALM + '/protocol/openid-connect/token';
  var LOGOUT = REALM + '/protocol/openid-connect/logout';

  var STORE = 'finance.session';
  var PKCE = 'finance.pkce';
  var REFRESH_MS = 60000;
  var MAX_BACKOFF_MS = 300000;

  var session = null;        // { access_token, refresh_token, expires_at, id_token }
  var inFlight = null;       // AbortController for the current batch of requests
  var timer = null;
  var backoff = 0;

  var el = function (id) { return document.getElementById(id); };

  /* ------------------------------------------------------------- utilities */

  function b64url(bytes) {
    var s = '';
    for (var i = 0; i < bytes.length; i++) s += String.fromCharCode(bytes[i]);
    return btoa(s).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '');
  }

  function randomString(byteLength) {
    return b64url(crypto.getRandomValues(new Uint8Array(byteLength)));
  }

  function challengeFor(verifier) {
    return window.ShopMockPKCE.challengeFor(verifier, window.crypto);
  }

  function claimsOf(token) {
    try {
      var part = token.split('.')[1].replace(/-/g, '+').replace(/_/g, '/');
      return JSON.parse(atob(part + '==='.slice((part.length + 3) % 4)));
    } catch (e) {
      return {};
    }
  }

  function saveSession(tokens) {
    session = {
      access_token: tokens.access_token,
      refresh_token: tokens.refresh_token,
      id_token: tokens.id_token,
      expires_at: Date.now() + (tokens.expires_in || 60) * 1000
    };
    try { sessionStorage.setItem(STORE, JSON.stringify(session)); } catch (e) { /* private mode */ }
  }

  function loadSession() {
    try {
      var raw = sessionStorage.getItem(STORE);
      if (!raw) return null;
      var parsed = JSON.parse(raw);
      return parsed && parsed.access_token ? parsed : null;
    } catch (e) {
      return null;
    }
  }

  function clearSession() {
    session = null;
    try { sessionStorage.removeItem(STORE); } catch (e) { /* ignore */ }
  }

  function form(params) {
    var body = new URLSearchParams();
    Object.keys(params).forEach(function (k) {
      if (params[k] !== undefined && params[k] !== null) body.set(k, params[k]);
    });
    return body;
  }

  /* ------------------------------------------------------------ OIDC / PKCE */

  function signIn() {
    var verifier = randomString(48);
    var state = randomString(16);
    challengeFor(verifier).then(function (challenge) {
      try {
        sessionStorage.setItem(PKCE, JSON.stringify({ verifier: verifier, state: state }));
      } catch (e) { /* ignore */ }
      var q = new URLSearchParams({
        client_id: cfg.clientId,
        response_type: 'code',
        scope: 'openid profile email',
        redirect_uri: REDIRECT_URI,
        state: state,
        code_challenge: challenge,
        code_challenge_method: 'S256'
      });
      window.location.assign(AUTHORIZE + '?' + q.toString());
    }).catch(function (err) {
      fail('Unable to start sign-in: ' + err.message);
    });
  }

  function signOut() {
    var hint = session && session.id_token;
    clearSession();
    var q = new URLSearchParams({ post_logout_redirect_uri: REDIRECT_URI });
    if (hint) q.set('id_token_hint', hint);
    window.location.assign(LOGOUT + '?' + q.toString());
  }

  function postToken(params) {
    return fetch(TOKEN, {
      method: 'POST',
      headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
      body: form(params)
    }).then(function (response) {
      if (!response.ok) throw new Error('token endpoint returned ' + response.status);
      return response.json();
    });
  }

  function completeRedirect() {
    var params = new URLSearchParams(window.location.search);
    var code = params.get('code');
    var error = params.get('error');
    var stashed = null;
    try { stashed = JSON.parse(sessionStorage.getItem(PKCE) || 'null'); } catch (e) { /* ignore */ }
    try { sessionStorage.removeItem(PKCE); } catch (e) { /* ignore */ }

    if (!code && !error) return Promise.resolve(false);
    window.history.replaceState({}, document.title, BASE);

    if (error) return Promise.reject(new Error('sign-in was not completed (' + error + ')'));
    if (!stashed || stashed.state !== params.get('state')) {
      return Promise.reject(new Error('sign-in state did not match; please try again'));
    }

    return postToken({
      grant_type: 'authorization_code',
      client_id: cfg.clientId,
      code: code,
      redirect_uri: REDIRECT_URI,
      code_verifier: stashed.verifier
    }).then(function (tokens) {
      saveSession(tokens);
      return true;
    });
  }

  function freshAccessToken() {
    if (!session) return Promise.reject(new Error('not signed in'));
    if (Date.now() < session.expires_at - 30000) {
      return Promise.resolve(session.access_token);
    }
    if (!session.refresh_token) {
      clearSession();
      return Promise.reject(new Error('session expired'));
    }
    return postToken({
      grant_type: 'refresh_token',
      client_id: cfg.clientId,
      refresh_token: session.refresh_token
    }).then(function (tokens) {
      saveSession(tokens);
      return session.access_token;
    }).catch(function (err) {
      clearSession();
      throw err;
    });
  }

  /* ------------------------------------------------------------- rendering */

  function show(name) {
    ['loading', 'signedout', 'denied', 'error', 'ledger'].forEach(function (panel) {
      el('panel-' + panel).hidden = panel !== name;
    });
    var signedIn = name === 'ledger';
    el('refresh').hidden = !signedIn;
    el('signout').hidden = !(signedIn || name === 'denied');
    el('identity').hidden = !(signedIn || name === 'denied');
  }

  function announce(message) {
    el('live').textContent = message;
  }

  function money(cents, currency) {
    var amount = (Number(cents) || 0) / 100;
    try {
      return new Intl.NumberFormat(undefined, {
        style: 'currency', currency: currency || 'USD'
      }).format(amount);
    } catch (e) {
      return amount.toFixed(2) + ' ' + (currency || 'USD');
    }
  }

  function statusTone(status) {
    if (status === 'settled' || status === 'captured') return 'good';
    if (status === 'pending' || status === 'authorized') return 'warn';
    if (status === 'failed' || status === 'declined' || status === 'chargeback') return 'bad';
    return 'none';
  }

  function pill(text, tone) {
    var span = document.createElement('span');
    span.className = 'pill';
    if (tone !== 'none') span.setAttribute('data-tone', tone);
    span.textContent = text;
    return span;
  }

  function cell(row, label, value, className) {
    var td = document.createElement('td');
    td.setAttribute('data-label', label);
    if (typeof value === 'string') td.textContent = value;
    else td.appendChild(value);
    if (className) td.className = className;
    row.appendChild(td);
    return td;
  }

  function renderTiles(payload) {
    var list = el('tiles');
    list.textContent = '';

    var tiles = [];
    (payload.wallets || []).forEach(function (w) {
      tiles.push({
        label: 'Wallet float · ' + w.currency,
        value: money(w.balance_cents, w.currency),
        note: w.wallets + (w.wallets === 1 ? ' wallet' : ' wallets')
      });
    });
    Object.keys(payload.totals || {}).forEach(function (kind) {
      var t = payload.totals[kind];
      tiles.push({
        label: kind.charAt(0).toUpperCase() + kind.slice(1) + 's',
        value: money(t.amount_cents),
        note: t.count + (t.count === 1 ? ' entry' : ' entries')
      });
    });

    tiles.forEach(function (tile) {
      var li = document.createElement('li');
      li.className = 'tile';
      var v = document.createElement('span');
      v.className = 'tile-value';
      v.textContent = tile.value;
      var l = document.createElement('span');
      l.className = 'tile-label';
      l.textContent = tile.label;
      var n = document.createElement('span');
      n.className = 'tile-note';
      n.textContent = tile.note;
      li.appendChild(v);
      li.appendChild(l);
      li.appendChild(n);
      list.appendChild(li);
    });
  }

  function renderRevenue(days) {
    var body = el('revenue-rows');
    body.textContent = '';
    days.forEach(function (d) {
      var row = document.createElement('tr');
      cell(row, 'Day', d.day || '—', 'cell-day');
      cell(row, 'Gross', money(d.gross_cents), 'cell-num');
      cell(row, 'Refunds', money(d.refunds_cents), 'cell-num');
      cell(row, 'Net', money(d.net_cents), 'cell-num cell-strong');
      body.appendChild(row);
    });
  }

  function renderTransactions(rows) {
    var body = el('transaction-rows');
    body.textContent = '';
    rows.forEach(function (t) {
      var row = document.createElement('tr');
      cell(row, 'Reference', '#' + t.id, 'cell-ref');
      cell(row, 'Order', t.order_ref === null ? '—' : '#' + t.order_ref, 'cell-ref');
      cell(row, 'Amount', money(t.amount_cents), 'cell-num');
      cell(row, 'Kind', t.kind || '—');
      cell(row, 'Status', pill(t.status || 'unknown', statusTone(t.status)));
      var when = cell(row, 'Processed', (t.processed_at || '').replace('T', ' ').slice(0, 16));
      when.className = 'cell-when';
      body.appendChild(row);
    });
  }

  function renderCards(rows) {
    var body = el('card-rows');
    body.textContent = '';
    rows.forEach(function (c) {
      var row = document.createElement('tr');
      cell(row, 'Brand', c.brand || '—', 'cell-brand');
      cell(row, 'Card', c.masked || '—', 'cell-card');
      cell(row, 'Expires', c.expires || '—');
      body.appendChild(row);
    });
  }

  function render(overview, transactions, cards) {
    renderTiles(overview);
    renderRevenue(overview.revenue || []);
    renderTransactions(transactions.transactions || []);
    renderCards(cards.payment_methods || []);
    el('updated').textContent = 'Updated ' + new Date().toLocaleTimeString();
    announce(
      (overview.revenue || []).length + ' revenue days, ' +
      (transactions.transactions || []).length + ' transactions, ' +
      (cards.payment_methods || []).length + ' stored cards.'
    );
    show('ledger');
  }

  function fail(message) {
    el('error-detail').textContent = message;
    show('error');
    announce(message);
  }

  /* ------------------------------------------------------------- polling */

  function schedule(delay) {
    window.clearTimeout(timer);
    if (!session) return;
    timer = window.setTimeout(load, delay);
  }

  function get(path, token, signal) {
    return fetch(BASE + path, {
      headers: { Authorization: 'Bearer ' + token },
      signal: signal,
      cache: 'no-store'
    }).then(function (response) {
      if (response.status === 401) { var e = new Error('signedout'); e.code = 401; throw e; }
      if (response.status === 403) { var f = new Error('denied'); f.code = 403; throw f; }
      if (!response.ok) throw new Error('the finance service returned ' + response.status);
      return response.json();
    });
  }

  function load() {
    if (!session) { show('signedout'); return Promise.resolve(); }
    if (inFlight) inFlight.abort();          // never stack requests
    var controller = new AbortController();
    inFlight = controller;

    return freshAccessToken()
      .then(function (token) {
        return Promise.all([
          get('api/overview', token, controller.signal),
          get('api/transactions', token, controller.signal),
          get('api/payment-methods', token, controller.signal)
        ]);
      })
      .then(function (results) {
        backoff = 0;
        render(results[0], results[1], results[2]);
        schedule(REFRESH_MS);
      })
      .catch(function (err) {
        if (err.name === 'AbortError') return;
        if (err.code === 401) { clearSession(); show('signedout'); return; }
        if (err.code === 403) { show('denied'); return; }
        if (!session) { show('signedout'); return; }
        backoff = Math.min(backoff ? backoff * 2 : REFRESH_MS, MAX_BACKOFF_MS);
        fail(err.message + ' — retrying in ' + Math.round(backoff / 1000) + 's.');
        schedule(backoff);
      })
      .finally(function () {
        if (inFlight === controller) inFlight = null;
      });
  }

  /* ------------------------------------------------------------------ wire */

  el('signin').addEventListener('click', signIn);
  el('signout').addEventListener('click', signOut);
  el('signout-denied').addEventListener('click', signOut);
  el('refresh').addEventListener('click', function () { backoff = 0; load(); });
  el('retry').addEventListener('click', function () { backoff = 0; load(); });

  // Pause polling while the tab is hidden; catch up on return.
  document.addEventListener('visibilitychange', function () {
    if (document.hidden) window.clearTimeout(timer);
    else if (session) load();
  });

  completeRedirect()
    .then(function () {
      session = session || loadSession();
      if (!session) { show('signedout'); return; }
      var who = claimsOf(session.access_token);
      el('identity').textContent = '';
      var name = document.createElement('strong');
      name.textContent = who.preferred_username || who.name || 'signed in';
      el('identity').appendChild(name);
      return load();
    })
    .catch(function (err) {
      clearSession();
      fail(err.message);
    });
})();
