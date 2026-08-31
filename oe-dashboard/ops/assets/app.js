/* ShopMock IT Operations console.
 *
 * OIDC authorization code + PKCE against the it-operations client, implemented
 * directly against the realm's endpoints — no third-party script is loaded, so
 * the page runs under a CSP with no 'unsafe-inline' and no external origins.
 *
 * Nothing here is a security control: the browser only decides what to draw.
 * Every answer about container state comes from /api/containers, which verifies
 * the token and the it-ops realm role server-side.
 */
(function () {
  'use strict';

  var cfg = document.body.dataset;
  // Works whether the edge delivered /oe or /oe/ — both resolve to /oe/.
  var BASE = window.location.pathname.replace(/\/?$/, '/');
  var REDIRECT_URI = window.location.origin + BASE;
  var REALM = window.location.origin + '/auth/realms/' + cfg.realm;
  var AUTHORIZE = REALM + '/protocol/openid-connect/auth';
  var TOKEN = REALM + '/protocol/openid-connect/token';
  var LOGOUT = REALM + '/protocol/openid-connect/logout';

  var STORE = 'oe.session';
  var PKCE = 'oe.pkce';
  var REFRESH_MS = 15000;
  var MAX_BACKOFF_MS = 120000;

  var session = null;        // { access_token, refresh_token, expires_at, id_token }
  var inFlight = null;       // AbortController for the current status request
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
    ['loading', 'signedout', 'denied', 'error', 'status'].forEach(function (panel) {
      el('panel-' + panel).hidden = panel !== name;
    });
    var signedIn = name === 'status';
    el('refresh').hidden = !signedIn;
    el('auto-wrap').hidden = !signedIn;
    el('signout').hidden = !(signedIn || name === 'denied');
    el('identity').hidden = !(signedIn || name === 'denied');
  }

  function announce(message) {
    el('live').textContent = message;
  }

  function humanAge(seconds) {
    if (seconds === null || seconds === undefined) return '—';
    var units = [['d', 86400], ['h', 3600], ['m', 60]];
    for (var i = 0; i < units.length; i++) {
      if (seconds >= units[i][1]) return Math.floor(seconds / units[i][1]) + units[i][0];
    }
    return Math.max(0, seconds) + 's';
  }

  function stateTone(c) {
    if (c.state === 'running') return c.health === 'unhealthy' ? 'bad' : 'good';
    if (c.state === 'exited' || c.state === 'dead') return c.ok ? 'good' : 'bad';
    return 'warn';
  }

  function stateLabel(c) {
    if (c.state === 'exited' || c.state === 'dead') {
      return c.exit_code === null ? 'exited' : 'exited (' + c.exit_code + ')';
    }
    return c.state;
  }

  function healthTone(health) {
    if (health === 'healthy') return 'good';
    if (health === 'unhealthy') return 'bad';
    if (health === 'starting') return 'warn';
    return 'none';
  }

  function pill(text, tone) {
    var span = document.createElement('span');
    span.className = 'pill';
    if (tone !== 'none') span.setAttribute('data-tone', tone);
    span.textContent = text;
    return span;
  }

  function cell(row, label, value) {
    var td = document.createElement('td');
    td.setAttribute('data-label', label);
    if (typeof value === 'string') td.textContent = value;
    else td.appendChild(value);
    row.appendChild(td);
    return td;
  }

  var TILES = [
    { key: 'total', label: 'Containers', tone: function () { return 'none'; } },
    { key: 'running', label: 'Running', tone: function () { return 'good'; } },
    { key: 'healthy', label: 'Healthy', tone: function () { return 'good'; } },
    { key: 'starting', label: 'Starting', tone: function (v) { return v ? 'warn' : 'none'; } },
    { key: 'exited_ok', label: 'Seed jobs done', tone: function () { return 'none'; } },
    { key: 'failed', label: 'Failing', tone: function (v) { return v ? 'bad' : 'none'; } },
    { key: 'other', label: 'Not started', tone: function (v) { return v ? 'warn' : 'none'; } }
  ];

  function renderTiles(summary) {
    var list = el('tiles');
    list.textContent = '';
    TILES.forEach(function (tile) {
      var value = summary[tile.key] || 0;
      var li = document.createElement('li');
      li.className = 'tile';
      li.setAttribute('data-tone', tile.tone(value));
      var v = document.createElement('span');
      v.className = 'tile-value';
      v.textContent = String(value);
      var l = document.createElement('span');
      l.className = 'tile-label';
      l.textContent = tile.label;
      li.appendChild(v);
      li.appendChild(l);
      list.appendChild(li);
    });
  }

  function renderRows(containers) {
    var body = el('rows');
    body.textContent = '';
    containers.forEach(function (c) {
      var row = document.createElement('tr');
      cell(row, 'Service', c.service || '—').className = 'cell-service';
      cell(row, 'Container', c.name || '—').className = 'cell-name';
      cell(row, 'State', pill(stateLabel(c), stateTone(c)));
      var health = healthTone(c.health);
      cell(row, 'Health', health === 'none' ? '—' : pill(c.health, health));
      cell(row, 'Image', c.image || '—').className = 'cell-image';
      var age = cell(row, 'Age', humanAge(c.age_seconds));
      age.className = 'cell-age';
      if (c.created) age.title = 'Created ' + c.created;
      body.appendChild(row);
    });
  }

  function render(payload) {
    var summary = payload.summary || {};
    var verdict = el('verdict');
    verdict.textContent = summary.ok ? 'All good' : 'Attention';
    verdict.setAttribute('data-ok', summary.ok ? 'true' : 'false');
    renderTiles(summary);
    renderRows(payload.containers || []);
    el('updated').textContent = 'Updated ' + new Date().toLocaleTimeString();
    announce(
      summary.total + ' containers, ' + (summary.running || 0) + ' running, ' +
      (summary.failed || 0) + ' failing.'
    );
    show('status');
  }

  function fail(message) {
    el('error-detail').textContent = message;
    show('error');
    announce(message);
  }

  /* ------------------------------------------------------------- polling */

  function schedule(delay) {
    window.clearTimeout(timer);
    if (!el('auto').checked || !session) return;
    timer = window.setTimeout(load, delay);
  }

  function load() {
    if (!session) { show('signedout'); return Promise.resolve(); }
    if (inFlight) inFlight.abort();          // never stack requests
    var controller = new AbortController();
    inFlight = controller;

    return freshAccessToken()
      .then(function (token) {
        return fetch(BASE + 'api/containers', {
          headers: { Authorization: 'Bearer ' + token },
          signal: controller.signal,
          cache: 'no-store'
        });
      })
      .then(function (response) {
        if (response.status === 401) { clearSession(); show('signedout'); return null; }
        if (response.status === 403) { show('denied'); return null; }
        if (!response.ok) throw new Error('status service returned ' + response.status);
        return response.json();
      })
      .then(function (payload) {
        if (payload) { backoff = 0; render(payload); }
        schedule(REFRESH_MS);
      })
      .catch(function (err) {
        if (err.name === 'AbortError') return;
        if (!session) { show('signedout'); return; }
        // Back off rather than hammering an already-unhappy stack.
        backoff = Math.min(backoff ? backoff * 2 : REFRESH_MS * 2, MAX_BACKOFF_MS);
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
  el('auto').addEventListener('change', function () {
    if (el('auto').checked) load(); else window.clearTimeout(timer);
  });

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
