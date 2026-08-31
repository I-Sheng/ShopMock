/* ShopMock People portal (HR).
 *
 * OIDC authorization code + PKCE against the hr-portal client, implemented
 * directly against the realm's endpoints — no third-party script is loaded, so
 * the page runs under a CSP with no 'unsafe-inline' and no external origins.
 *
 * Nothing here is a security control: the browser only decides what to draw.
 * Every answer comes from this service's own API, which verifies the token, the
 * client it was minted for and the `hr` realm role server-side. A 403 rendered
 * as a friendly panel is still a 403 that was decided on the server.
 */
(function () {
  'use strict';

  var cfg = document.body.dataset;
  // Works whether the edge delivered /hr or /hr/ — both resolve to /hr/.
  var BASE = window.location.pathname.replace(/\/?$/, '/');
  var REDIRECT_URI = window.location.origin + BASE;
  var REALM = window.location.origin + '/auth/realms/' + cfg.realm;
  var AUTHORIZE = REALM + '/protocol/openid-connect/auth';
  var TOKEN = REALM + '/protocol/openid-connect/token';
  var LOGOUT = REALM + '/protocol/openid-connect/logout';

  var STORE = 'people.session';
  var PKCE = 'people.pkce';
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
    ['loading', 'signedout', 'denied', 'error', 'people'].forEach(function (panel) {
      el('panel-' + panel).hidden = panel !== name;
    });
    var signedIn = name === 'people';
    el('refresh').hidden = !signedIn;
    el('signout').hidden = !(signedIn || name === 'denied');
    el('identity').hidden = !(signedIn || name === 'denied');
  }

  function announce(message) {
    el('live').textContent = message;
  }

  function titleCase(value) {
    return String(value || '').replace(/_/g, ' ').replace(
      /\b\w/g, function (c) { return c.toUpperCase(); });
  }

  function budget(cents) {
    var amount = (Number(cents) || 0) / 100;
    try {
      return new Intl.NumberFormat(undefined, {
        style: 'currency', currency: 'USD', maximumFractionDigits: 0
      }).format(amount);
    } catch (e) {
      return amount.toFixed(0);
    }
  }

  function statusTone(status) {
    if (status === 'active' || status === 'approved') return 'good';
    if (status === 'on_leave' || status === 'pending' || status === 'probation') return 'warn';
    if (status === 'left' || status === 'declined' || status === 'terminated') return 'bad';
    return 'none';
  }

  function chip(text, tone) {
    var span = document.createElement('span');
    span.className = 'chip';
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

  function renderTiles(counts) {
    var list = el('tiles');
    list.textContent = '';
    var keys = Object.keys(counts || {});
    var total = keys.reduce(function (sum, k) { return sum + (counts[k] || 0); }, 0);

    var tiles = [{ label: 'People on record', value: total, tone: 'none' }];
    keys.forEach(function (key) {
      tiles.push({ label: titleCase(key), value: counts[key], tone: statusTone(key) });
    });

    tiles.forEach(function (tile) {
      var li = document.createElement('li');
      li.className = 'tile';
      li.setAttribute('data-tone', tile.tone);
      var v = document.createElement('span');
      v.className = 'tile-value';
      v.textContent = String(tile.value);
      var l = document.createElement('span');
      l.className = 'tile-label';
      l.textContent = tile.label;
      li.appendChild(v);
      li.appendChild(l);
      list.appendChild(li);
    });
  }

  function renderDepartments(rows) {
    var body = el('department-rows');
    body.textContent = '';
    rows.forEach(function (d) {
      var row = document.createElement('tr');
      cell(row, 'Department', d.name || '—', 'cell-strong');
      cell(row, 'Cost centre', d.cost_center || '—', 'cell-code');
      cell(row, 'Headcount', d.headcount + ' / ' + d.headcount_budget, 'cell-num');
      cell(row, 'Payroll', budget(d.payroll_cents), 'cell-num');
      body.appendChild(row);
    });
  }

  function renderRoster(rows) {
    var body = el('roster-rows');
    body.textContent = '';
    rows.forEach(function (p) {
      var row = document.createElement('tr');
      cell(row, 'Reference', p.employee_no || '—', 'cell-code');
      cell(row, 'Name', ((p.first_name || '') + ' ' + (p.last_name || '')).trim() || '—',
           'cell-strong');
      cell(row, 'Role', p.job_title || '—');
      cell(row, 'Team', p.department || '—');
      cell(row, 'Contract', titleCase(p.employment_type));
      cell(row, 'Status', chip(titleCase(p.status), statusTone(p.status)));
      cell(row, 'Joined', p.hired_on || '—', 'cell-code');
      body.appendChild(row);
    });
  }

  function renderLeave(rows) {
    var body = el('leave-rows');
    body.textContent = '';
    rows.forEach(function (l) {
      var row = document.createElement('tr');
      cell(row, 'Person', ((l.first_name || '') + ' ' + (l.last_name || '')).trim() || '—',
           'cell-strong');
      cell(row, 'Type', titleCase(l.kind));
      cell(row, 'From', l.starts_on || '—', 'cell-code');
      cell(row, 'To', l.ends_on || '—', 'cell-code');
      cell(row, 'Days', String(l.days), 'cell-num');
      cell(row, 'Status', chip(titleCase(l.status), statusTone(l.status)));
      body.appendChild(row);
    });
  }

  function render(overview, roster, leave) {
    renderTiles(overview.headcount || {});
    renderDepartments(overview.departments || []);
    renderRoster(roster.employees || []);
    renderLeave(leave.leave_requests || []);
    el('updated').textContent = 'Updated ' + new Date().toLocaleTimeString();
    announce(
      (roster.employees || []).length + ' people, ' +
      (overview.departments || []).length + ' teams, ' +
      (leave.leave_requests || []).length + ' leave requests.'
    );
    show('people');
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
      if (!response.ok) throw new Error('the people service returned ' + response.status);
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
          get('api/employees', token, controller.signal),
          get('api/leave', token, controller.signal)
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
