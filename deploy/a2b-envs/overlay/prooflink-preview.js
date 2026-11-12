/**
 * ProofLink inside the demo workspace (#141). Staging only.
 *
 * The demo lists ProofLink as a product and opens it to a placeholder card,
 * and only for organisations that have it on. The working ProofLink is the
 * Plaid Sandbox preview at /plaid-demo/, a service of its own with its own
 * sign-in, until #141 moves it into the workspace. On staging, this file
 * replaces the placeholder with what ProofLink does and a way into that
 * preview. The preview's pages refuse to be framed, so it opens in a tab of
 * its own.
 *
 * Nothing here edits a file in the document root, for the same reason as the
 * other overlays: staging is re-mirrored from sandbox every minute. nginx
 * serves this from /opt/a2b-envs/overlay/staging and injects it on staging
 * only, the one environment that routes /plaid-demo/.
 */
(function () {
  'use strict';

  var PANEL = 'prooflink';
  var PREVIEW = '/plaid-demo/';

  var STEPS = [
    ['Officer joins', 'Each loan officer gets a permanent personal link.'],
    ['Link goes out', 'The officer sends it to the customer.'],
    ['Customer verifies', 'They agree to the consent, then confirm who they are on the verification provider\u2019s secure screens.'],
    ['Officer is notified', 'Passed, Needs review or Could not verify, on the dashboard and by email.'],
  ];

  function onPanel() {
    var p = location.pathname.split('/').filter(Boolean);
    return p.length >= 2 && p[p.length - 1] === PANEL;
  }

  function card() {
    var steps = STEPS.map(function (s, i) {
      return '<li style="display:flex;gap:10px;align-items:flex-start;">' +
        '<b style="flex:0 0 24px;height:24px;border-radius:50%;background:var(--mint-l);color:var(--em-d);' +
        'display:inline-flex;align-items:center;justify-content:center;font-size:12.5px;">' + (i + 1) + '</b>' +
        '<span><b style="display:block;font-size:14px;">' + s[0] + '</b>' +
        '<span class="muted" style="display:block;margin-top:2px;font-size:13px;line-height:1.45;">' + s[1] + '</span></span></li>';
    }).join('');
    return '<div class="card pl-preview" style="max-width:640px;">' +
      '<h3 style="font-size:16px;margin:0 0 6px;">Check a customer\u2019s identity with one link</h3>' +
      '<p class="muted" style="margin:0 0 16px;font-size:14px;line-height:1.5;">The customer confirms who they are, ' +
      'and the officer gets the result for their file. Their SSN and ID go to the verification provider, never to Lenni.</p>' +
      '<ol style="list-style:none;margin:0 0 18px;padding:0;display:grid;gap:12px;">' + steps + '</ol>' +
      '<a class="btn em" id="pl-open" href="' + PREVIEW + '" target="_blank" rel="noopener">Open ProofLink</a>' +
      '<p class="muted" style="margin:16px 0 0;font-size:12.5px;line-height:1.5;">Staging preview. ProofLink opens in ' +
      'its own tab, with its own sign-in, until it moves into this workspace (#141). Create an organization there, copy ' +
      'your link from the dashboard and open it in a private window to play the customer. It runs on Plaid\u2019s test ' +
      'system: use the test person Plaid\u2019s screens show, never a real SSN or ID.</p>' +
      '</div>';
  }

  // The workspace's column is as wide as the top bar's unwrapped product line,
  // which is wider than a phone; the page clips what doesn't fit. Holding the
  // column to the screen lets that line end in an ellipsis.
  function ensureStyles() {
    if (document.getElementById('pl-css')) return;
    var s = document.createElement('style');
    s.id = 'pl-css';
    s.textContent = 'body:has(.pl-preview) .shell-app{grid-template-columns:minmax(0,1fr);}';
    document.head.appendChild(s);
  }

  function check() {
    if (!onPanel()) return;
    var el = document.getElementById('ws-sku');
    if (!el || !el.classList.contains('on') || el.querySelector('.pl-preview')) return;
    var placeholder = el.querySelector('.card');
    if (!placeholder) return;
    ensureStyles();
    placeholder.outerHTML = card();
  }

  // A timer rather than requestAnimationFrame, which never fires in a tab
  // opened in the background.
  var queued = false;
  function schedule() {
    if (queued) return;
    queued = true;
    setTimeout(function () { queued = false; check(); }, 0);
  }

  function start() {
    new MutationObserver(schedule).observe(document.body, { childList: true, subtree: true, attributes: true, attributeFilter: ['class'] });
    window.addEventListener('popstate', schedule);
    schedule();
  }

  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', start);
  else start();
})();
