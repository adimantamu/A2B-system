/**
 * Real authentication in front of the A2B demo. Staging only.
 *
 * The demo answers its own API: mock-api.js replaces window.fetch and serves
 * /api/* out of fixture.json in the browser, including its login screen. That
 * makes the sign-in form a prop -- the password is printed on the page.
 *
 * This file makes the credential half real without touching Doak's build. It
 * wraps window.fetch a second time and sends the authentication calls to the
 * FastAPI service instead of the fixture:
 *
 *   real   /api/auth/login, /signup, /logout, /me, /password
 *   demo   everything else
 *
 * So passwords are verified with PBKDF2 against the real database, sign-up
 * creates a real account, reset emails really leave through SES, sessions are
 * real server-side rows, and the rate limits are the real ones. What is behind
 * the sign-in stays demo data, because that is what staging is for.
 *
 * Nothing here edits a file in the document root. The script is served from
 * outside it and injected by nginx, because staging is re-mirrored from
 * sandbox every minute and anything written into the root would be deleted.
 *
 * The awkward part is that the demo keeps its own session. Every mocked
 * endpoint gates on an in-memory `session.user` holding a *fixture* user id,
 * and the org list comes from fixture memberships. Handing it the real user
 * would point it at vendor slugs the fixture has never heard of, and the
 * workspace would 404 immediately after a successful login. So once real
 * authentication succeeds, the demo is told to sign the matching fixture user
 * in as well, and the body the app receives is the demo's own -- consistent
 * with the data it is about to render.
 */
(function () {
  'use strict';

  var PASSTHROUGH = /^\/api\/auth\/(password|forgot|reset)$/;
  var ME = '/api/auth/me';
  var SIGNED_OUT = { user: null, vendors: [], staff: false };

  // mock-api.js has already replaced window.fetch by the time this runs.
  var mockFetch = window.fetch;
  var netFetch = findNativeFetch();

  /**
   * The browser's own fetch, not the demo's.
   *
   * mock-api.js overwrote window.fetch before this file loaded, so the
   * original is no longer reachable from this window. A same-origin iframe
   * gets a fresh realm with an untouched copy.
   */
  function findNativeFetch() {
    try {
      var frame = document.createElement('iframe');
      frame.setAttribute('aria-hidden', 'true');
      frame.style.display = 'none';
      document.documentElement.appendChild(frame);
      var fn = frame.contentWindow.fetch;
      if (typeof fn !== 'function') throw new Error('no fetch in frame');
      // The frame is deliberately left in the document. The function is bound
      // to its realm and stops working the moment the frame is collected.
      return function (input, init) { return fn.call(frame.contentWindow, input, init); };
    } catch (e) {
      // Falling through to the mock would leave a sign-in form that looks real
      // and is not, which is worse than an obvious failure.
      console.error('[a2b] real auth disabled: could not reach the native fetch.', e);
      return null;
    }
  }

  function jsonResponse(body, status) {
    return new Response(JSON.stringify(body), {
      status: status || 200,
      headers: { 'Content-Type': 'application/json' },
    });
  }

  function post(path, payload) {
    return netFetch(path, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      credentials: 'same-origin',
      body: JSON.stringify(payload || {}),
    });
  }

  /**
   * Wait for the demo to finish loading fixture.json.
   *
   * Until it does, every mocked endpoint answers 503. app.js happens to await
   * this before its first call, so today the ordering already holds -- but a
   * 503 here would surface as a signed-in user being shown as signed out,
   * which is too confusing a failure to leave resting on another file's
   * ordering.
   */
  function awaitMock() {
    try {
      return Promise.resolve(window.__A2B_MOCK_READY__);
    } catch (e) {
      return Promise.resolve();
    }
  }

  /* ---------- the demo's fixture, read once ---------- */

  var fixture = null;

  async function fixtureUsers() {
    if (fixture) return fixture;
    try {
      // Never an unchecked cached copy. The demo reads the file fresh, so a
      // stale one missing a newly added persona would hand that person a
      // stand-in the demo then refuses.
      var r = await netFetch('/fixture.json', { cache: 'no-cache' });
      var f = await r.json();
      fixture = f.users || [];
    } catch (e) {
      fixture = [];
    }
    return fixture;
  }

  async function fixtureUserFor(email) {
    var wanted = (email || '').trim().toLowerCase();
    var users = await fixtureUsers();
    return users.find(function (u) { return (u.email || '').toLowerCase() === wanted; }) || null;
  }

  function mockPost(path, payload) {
    return mockFetch(path, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    });
  }

  /**
   * The stand-in password an account uses with the demo.
   *
   * The real service owns the real password and this is never typed by anyone,
   * but the demo's endpoints require one. It is derived from the address so it
   * is the same on every sign-in: a random value would register the address
   * once and then be unable to sign in again, which is exactly the bug this
   * replaced. It guards fixture data in the visitor's own browser, so it is
   * not a secret and does not need to be unguessable.
   */
  function demoStandInPassword(email) {
    return 'a2b-bridge:' + email;
  }

  /**
   * Sign the demo in, so the app renders data it actually has.
   *
   * A demo persona signs in as itself with the password the fixture already
   * publishes. Anyone else -- a real account with no demo counterpart -- is
   * registered with the demo, which lands them on the account picker with no
   * organizations rather than on a broken workspace.
   *
   * Sign-in is tried before registration because the demo rebuilds its users
   * from the fixture on every page load but keeps them for the life of the
   * page. So within one page session the address is already known, and across
   * page loads it is not; attempting only one of the two fails half the time.
   *
   * Returns the demo's own session body. Throws with the demo's own wording,
   * so a failure here is reported rather than flattened into "something went
   * wrong".
   */
  async function signInToDemo(realUser) {
    await awaitMock();
    var email = ((realUser && realUser.email) || '').trim().toLowerCase();
    if (!email) throw new Error('Signed in, but the account has no email address.');

    var match = await fixtureUserFor(email);
    var password = match ? match.password : demoStandInPassword(email);

    var res = await mockPost('/api/auth/login', { email: email, password: password });
    if (res.ok) return res.json();

    if (match) {
      // A demo persona whose fixture password did not work. Registering would
      // collide with the fixture, so there is nothing sensible left to try.
      throw new Error(await detailOf(res, 'The demo rejected its own account.'));
    }

    res = await mockPost('/api/auth/signup', {
      name: (realUser && realUser.name) || email.split('@')[0],
      email: email,
      password: password,
    });
    if (res.ok) return res.json();
    throw new Error(await detailOf(res, 'The demo workspace could not be opened.'));
  }

  async function detailOf(res, fallback) {
    var body = await res.json().catch(function () { return null; });
    return (body && body.detail) || fallback;
  }

  function signOutOfDemo() {
    return mockFetch('/api/auth/logout', { method: 'POST' }).catch(function () {});
  }

  /* ---------- the routes that became real ---------- */

  async function handleLogin(init) {
    var sent = JSON.parse((init && init.body) || '{}');
    var real = await post('/api/auth/login', { email: sent.email, password: sent.password });
    // Wrong password, throttled, locked out: the real service's own answer,
    // passed through untouched so the user sees the real wording.
    if (!real.ok) return real;

    var me = await real.json().catch(function () { return {}; });
    try {
      return jsonResponse(await signInToDemo(me.user));
    } catch (e) {
      console.error('[a2b] demo handoff failed after a real sign-in', e);
      return jsonResponse({ detail: 'Signed in. ' + e.message }, 500);
    }
  }

  async function handleSignup(init) {
    var sent = JSON.parse((init && init.body) || '{}');
    var real = await post('/api/auth/signup', {
      name: sent.name, email: sent.email, password: sent.password,
    });
    if (!real.ok) return real;

    var me = await real.json().catch(function () { return {}; });
    try {
      return jsonResponse(await signInToDemo(me.user));
    } catch (e) {
      console.error('[a2b] demo handoff failed after a real sign-up', e);
      return jsonResponse({ detail: 'Account created. ' + e.message }, 500);
    }
  }

  async function handleLogout() {
    // The real session is the one that matters, so it goes first and its
    // failure is not allowed to leave the browser looking signed in.
    await post('/api/auth/logout', {}).catch(function () {});
    await signOutOfDemo();
    return jsonResponse({ ok: true });
  }

  /**
   * Whether the tab's demo session is this account's.
   *
   * It lives in sessionStorage and outlives changes the demo cannot see:
   * another account signing in elsewhere in the browser, or a persona added
   * to the fixture after this tab gave the account a stand-in.
   */
  async function belongsTo(demoUser, realUser) {
    var email = ((realUser && realUser.email) || '').trim().toLowerCase();
    if ((demoUser.email || '').trim().toLowerCase() !== email) return false;
    var match = await fixtureUserFor(email);
    return !match || demoUser.id === match.id;
  }

  /**
   * Who is signed in, according to the real service.
   *
   * Called on every page load. The real session lives in a cookie shared by
   * the whole browser; the demo's lives in sessionStorage, which is per tab.
   * Opening a second tab therefore arrives with a valid real session and no
   * demo one, and without the repair below the app would decide the user is
   * signed out while their cookie says otherwise.
   */
  async function handleMe() {
    var real;
    try {
      real = await netFetch(ME, { credentials: 'same-origin' });
    } catch (e) {
      return jsonResponse(SIGNED_OUT);
    }
    // A visitor without a session gets 200 and no user, not a 401.
    var me = real.ok ? await real.json().catch(function () { return {}; }) : {};

    await awaitMock();
    if (!me.user) {
      await signOutOfDemo();
      return jsonResponse(SIGNED_OUT);
    }

    var demo = await mockFetch(ME, { credentials: 'same-origin' });
    var body = await demo.json().catch(function () { return null; });
    if (body && body.user && await belongsTo(body.user, me.user)) return jsonResponse(body);
    if (body && body.user) await signOutOfDemo();

    try {
      return jsonResponse(await signInToDemo(me.user));
    } catch (e) {
      // The real session is valid but the demo will not open. Reporting signed
      // out is the honest answer: the app cannot render anything useful, and
      // pretending otherwise produces a half-loaded workspace.
      console.error('[a2b] could not restore the demo session', e);
      return jsonResponse(SIGNED_OUT);
    }
  }

  /* ---------- routing ---------- */

  if (netFetch) {
    window.fetch = function (input, init) {
      var url = typeof input === 'string' ? input : (input && input.url) || '';
      var path;
      try {
        path = new URL(url, location.origin).pathname;
      } catch (e) {
        return mockFetch(input, init);
      }
      var method = ((init && init.method) || 'GET').toUpperCase();

      if (path === ME && method === 'GET') return handleMe();
      if (path === '/api/auth/login' && method === 'POST') return handleLogin(init);
      if (path === '/api/auth/signup' && method === 'POST') return handleSignup(init);
      if (path === '/api/auth/logout' && method === 'POST') return handleLogout();

      // Changing a password, and the reset endpoints the real service's own
      // pages call. No demo state to keep in step, so they pass straight
      // through -- but they still need the session cookie attached.
      if (PASSTHROUGH.test(path)) {
        return netFetch(input, Object.assign({ credentials: 'same-origin' }, init));
      }

      return mockFetch(input, init);
    };
  }

  /* ---------- leaving the single-page app ---------- */

  /**
   * The demo routes every same-origin link through its own router, so an
   * ordinary <a href="/forgot-password"> never reaches the network -- the app
   * treats it as an unknown view and shows nothing.
   *
   * /forgot-password and /reset-password are served by the real service, not
   * by the demo, so they need a real navigation. A listener in the capture
   * phase on `document` runs before the router's, which is on `document` in
   * the bubble phase, and stopping the event there keeps the router out of it.
   */
  document.addEventListener('click', function (e) {
    if (e.defaultPrevented || e.button !== 0 || e.metaKey || e.ctrlKey || e.shiftKey || e.altKey) return;
    var a = e.target && e.target.closest && e.target.closest('a[data-a2b-real]');
    if (!a) return;
    e.stopImmediatePropagation();
    e.preventDefault();
    location.assign(a.getAttribute('href'));
  }, true);

  /* ---------- the sign-in screens now describe real accounts ---------- */

  function tellTheTruthOnTheAuthScreens() {
    // The demo prints "password demo" next to three addresses. Against the
    // real service those passwords do not work, so leaving the block up would
    // hand every visitor three failing logins.
    var creds = document.querySelector('#v-login .demo-creds');
    if (creds) {
      creds.innerHTML =
        '<b>Real accounts</b><br>' +
        'Sign in with your own email and password. ' +
        '<a href="/forgot-password" data-a2b-real>Forgot your password?</a><br>' +
        '<span class="mono">Staging — accounts and reset emails are real.</span>';
    }

    var foot = document.querySelector('#form-login .foot');
    if (foot && !foot.querySelector('[href="/forgot-password"]')) {
      foot.insertAdjacentHTML('beforeend',
        ' · <a href="/forgot-password" data-a2b-real>Forgot password?</a>');
    }

    // The real service requires ten characters. Leaving the browser's own
    // minimum at four means the first thing a new user meets is a rejection
    // from the server for a password the form told them was fine.
    var pw = document.querySelector('#su-pw');
    if (pw) {
      pw.setAttribute('minlength', '10');
      pw.setAttribute('placeholder', 'At least 10 characters');
    }
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', tellTheTruthOnTheAuthScreens);
  } else {
    tellTheTruthOnTheAuthScreens();
  }
})();
