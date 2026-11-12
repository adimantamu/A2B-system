/**
 * Keeps a bank's fixed start links in step with the demo workspace. Staging and
 * sandbox only.
 *
 * The demo's start buttons carry the prompt inside the link, and the demo keeps
 * its data in the browser, so a link pasted onto a bank's site froze whatever
 * the prompt was at the moment of copying. The service now holds one row per
 * product and /go/{vendor}/{product}/{assistant} reads it at the click. This
 * file is what keeps those rows current:
 *
 *   - After any change the workspace makes, it asks the demo for each product's
 *     public start payload -- the same text the workspace's own buttons and
 *     start page use -- and sends the products whose payload changed.
 *   - When embed code is copied for a product that has a row, the buttons in
 *     it point at the fixed links, and its copy button fetches the current
 *     prompt instead of carrying a snapshot.
 *
 * Only organisations the service already holds rows for take part. Rows are
 * created deliberately, so editing a scanned prospect never puts a prompt in a
 * real bank's name behind a lenni.io address. The service enforces the same
 * rule; this just avoids asking.
 *
 * Nothing is sent when a page opens. The demo's fixture organisations reset on
 * every load, and pushing that state would undo an edit made in another tab.
 * Only a change made in this page is sent, and only for the products it
 * altered.
 *
 * It changes nothing on screen. Every failure is a console warning: a sync that
 * stops must not take the workspace down with it.
 */
(function () {
  'use strict';

  var LOG = '[a2b live links]';
  var DEBOUNCE_MS = 400;
  var RETRY_MS = 5000;
  var MAX_RETRIES = 5;
  // The service refuses a longer notice, and a refused push would also hold
  // back the prompt that came with it.
  var MAX_NOTICE = 2000;
  // Browsers cap the bodies of keepalive requests in flight at 64 KiB.
  var KEEPALIVE_MAX = 60000;
  // Doak's start page says this when a product has no notice of its own.
  var DEFAULT_NOTICE = 'This offering is not currently available.';

  // mock-api.js has already replaced window.fetch by the time this runs.
  var demoFetch = window.fetch;
  var netFetch = findNativeFetch();
  if (!netFetch || typeof demoFetch !== 'function') return;

  /**
   * The browser's own fetch, from a fresh realm. See a2b-real-auth.js: the
   * mock overwrote this window's copy before either overlay loaded.
   */
  function findNativeFetch() {
    try {
      var frame = document.createElement('iframe');
      frame.setAttribute('aria-hidden', 'true');
      frame.style.display = 'none';
      document.documentElement.appendChild(frame);
      var fn = frame.contentWindow.fetch;
      if (typeof fn !== 'function') throw new Error('no fetch in frame');
      // Left in the document: the function stops working once the frame goes.
      return function (input, init) { return fn.call(frame.contentWindow, input, init); };
    } catch (e) {
      console.error(LOG, 'disabled: could not reach the native fetch.', e);
      return null;
    }
  }

  function warn() {
    try { console.warn.apply(console, [LOG].concat([].slice.call(arguments))); } catch (e) {}
  }

  function enc(s) { return encodeURIComponent(String(s)); }

  /* ---------- state ---------- */

  // vendor id -> { id, slug, state, rows, sent, ready }
  //   state  'checking' | 'on' | 'off' | 'blocked' | 'failed'
  //   rows   product slug -> the service's public view, links included
  //   sent   product slug -> the payload last sent or found, as JSON text
  var orgs = {};
  var currentOrgId = null;
  var paused = false;
  var timer = null;
  var running = null;
  var again = false;
  var flushing = false;
  var retries = 0;

  /* ---------- reading the demo ---------- */

  function demoJson(path) {
    return demoFetch(path, { headers: { 'Content-Type': 'application/json' } }).then(function (r) {
      if (!r.ok) {
        var e = new Error(path + ' answered ' + r.status);
        e.status = r.status;
        throw e;
      }
      return r.json();
    });
  }

  /**
   * What the service should hold for one product, from the demo's own public
   * start payload. Unpublished and draft products carry the notice Doak's
   * start page would show in place of a prompt.
   */
  function payloadOf(d) {
    var vendor = d.vendor || {};
    var product = d.product || {};
    var published = product.status === 'published';
    var notice = (d.unpublished_notice || product.unpublished_notice || product.description
      || DEFAULT_NOTICE).slice(0, MAX_NOTICE);
    return {
      status: published ? 'published' : 'unpublished',
      vendor_name: vendor.name || '',
      product_name: product.name || '',
      prompt: published ? (d.prompt || '') : '',
      notice: notice,
      page: {
        vendor: {
          name: vendor.name || '', slug: vendor.slug || '', website: vendor.website || '',
          convey_url: vendor.convey_url || '',
        },
        product: {
          name: product.name || '', slug: product.slug || '',
          sections: Array.isArray(product.sections) ? product.sections : [],
        },
        legal: d.legal || {},
      },
    };
  }

  /**
   * Every product of one organisation as the demo would publish it now.
   *
   * agents-profile and public/start are the two reads in the demo with no side
   * effects; opening the organisation itself writes to its activity log.
   * Resolves to { vendorSlug, products: { slug: payloadJson | null } }, where
   * null means the product exists but could not be read this time.
   */
  function snapshot(org) {
    return demoJson('/api/vendors/' + enc(org.id) + '/agents-profile').then(function (prof) {
      var vendorSlug = (prof.vendor && prof.vendor.slug) || org.slug;
      var list = (prof.products || []).filter(function (p) { return p && (p.slug || p.id); });
      return Promise.all(list.map(function (p) {
        var key = p.slug || p.id;
        return demoJson('/api/public/start/' + enc(vendorSlug) + '/' + enc(key)).then(
          function (d) { return [key, JSON.stringify(payloadOf(d))]; },
          function () { return [key, null]; }
        );
      })).then(function (pairs) {
        var products = {};
        pairs.forEach(function (kv) { products[kv[0]] = kv[1]; });
        return { vendorSlug: vendorSlug, products: products };
      });
    });
  }

  /* ---------- organisations ---------- */

  function listRows(slug, tries) {
    return netFetch('/api/live/' + enc(slug), { credentials: 'same-origin', cache: 'no-store' })
      .then(function (r) {
        if (!r.ok) throw new Error('/api/live/' + slug + ' answered ' + r.status);
        return r.json();
      })
      .then(function (d) { return (d && d.products) || []; }, function (e) {
        if (tries <= 1) throw e;
        return new Promise(function (wait) { setTimeout(wait, 1000); })
          .then(function () { return listRows(slug, tries - 1); });
      });
  }

  function register(id, slug) {
    if (!id || !slug) return;
    var known = orgs[id];
    if (known && known.state !== 'failed') return;
    var org = orgs[id] = {
      id: id, slug: slug, state: 'checking', rows: {}, sent: known ? known.sent : {},
    };
    // Read now, before anything in this page can change it: this is what the
    // page starts from, and it is sent only once a change here alters it. Kept
    // even if the service cannot be reached, so a retry after an edit does not
    // mistake the edited text for the starting point.
    var start = snapshot(org).then(function (snap) {
      Object.keys(snap.products).forEach(function (key) {
        if (snap.products[key] != null && !(key in org.sent)) org.sent[key] = snap.products[key];
      });
      return snap;
    }, function () { return null; });
    org.ready = listRows(slug, 3)
      .then(function (rows) {
        rows.forEach(function (row) { if (row && row.slug) org.rows[row.slug] = row; });
        if (!rows.length) { org.state = 'off'; return; }
        return start.then(function (snap) {
          if (!snap) throw new Error('the demo would not describe ' + slug);
          org.state = 'on';
        });
      })
      .catch(function (e) {
        // Tried again on the next change rather than left silently off.
        org.state = 'failed';
        warn('could not check live links for', slug, e && e.message ? e.message : e);
      });
  }

  function harvestVendors(list) {
    (Array.isArray(list) ? list : []).forEach(function (v) {
      if (v && typeof v === 'object') register(v.id, v.slug);
    });
  }

  /* ---------- sending ---------- */

  function push(org, vendorSlug, productSlug, body) {
    var init = {
      method: 'PUT',
      credentials: 'same-origin',
      headers: { 'Content-Type': 'application/json' },
      body: body,
    };
    if (flushing && body.length < KEEPALIVE_MAX) init.keepalive = true;
    return netFetch('/api/live/' + enc(vendorSlug) + '/' + enc(productSlug), init).then(function (r) {
      if (r.ok) {
        org.sent[productSlug] = body;
        return r.json().then(function (d) {
          if (d && d.live) org.rows[productSlug] = d.live;
        }, function () {});
      }
      return r.json().catch(function () { return {}; }).then(function (d) {
        var detail = (d && d.detail) || r.statusText;
        if (r.status === 401) {
          paused = true;
          warn('not signed in to the service; live links paused.', detail);
        } else if (r.status === 403 || r.status === 404) {
          org.state = 'blocked';
          warn('live links refused for', vendorSlug + ':', detail);
        } else if (r.status === 400 || r.status === 422) {
          // The same text would be refused again; wait for it to change.
          org.sent[productSlug] = body;
          warn('live link for', vendorSlug + '/' + productSlug, 'was refused:', detail);
        } else {
          throw new Error(vendorSlug + '/' + productSlug + ' answered ' + r.status);
        }
      });
    });
  }

  function gone(lastBody) {
    var p = JSON.parse(lastBody);
    p.status = 'unpublished';
    p.prompt = '';
    return JSON.stringify(p);
  }

  function syncOrg(org) {
    if (org.state === 'failed') {
      register(org.id, org.slug);
      org = orgs[org.id];
    }
    return Promise.resolve(org.ready).then(function () {
      if (org.state !== 'on' || paused) return;
      return snapshot(org).then(function (snap) {
        var sends = [];
        Object.keys(snap.products).forEach(function (key) {
          var body = snap.products[key];
          if (body != null && org.sent[key] !== body) {
            sends.push(push(org, snap.vendorSlug, key, body));
          }
        });
        // Deleted in the workspace: its button on the bank's site should say
        // so, not keep starting the last prompt it had.
        Object.keys(org.sent).forEach(function (key) {
          if (key in snap.products) return;
          var body = gone(org.sent[key]);
          if (org.sent[key] !== body) sends.push(push(org, snap.vendorSlug, key, body));
        });
        return Promise.all(sends);
      }, function (e) {
        // 401 or 403 from the demo itself: signed out of it, or not this org.
        if (!(e && (e.status === 401 || e.status === 403))) throw e;
      });
    });
  }

  function reconcile() {
    var all = Object.keys(orgs).map(function (id) { return orgs[id]; });
    return Promise.all(all.map(function (org) { return syncOrg(org); }));
  }

  function run() {
    timer = null;
    if (running) { again = true; return running; }
    running = reconcile().then(function () {
      retries = 0;
    }, function (e) {
      warn('sync failed;', retries < MAX_RETRIES ? 'retrying.' : 'giving up until the next change.',
        e && e.message ? e.message : e);
      if (retries < MAX_RETRIES) { retries += 1; schedule(RETRY_MS); }
    }).then(function () {
      running = null;
      flushing = false;
      if (again) { again = false; return run(); }
    });
    return running;
  }

  function schedule(delay) {
    if (timer) clearTimeout(timer);
    timer = setTimeout(run, delay == null ? DEBOUNCE_MS : delay);
  }

  function flush() {
    if (!timer) return;
    clearTimeout(timer);
    flushing = true;
    run();
  }

  document.addEventListener('visibilitychange', function () {
    if (document.visibilityState === 'hidden') flush();
  });
  window.addEventListener('pagehide', flush);

  /* ---------- watching the workspace ---------- */

  var VENDOR = /^\/api\/vendors\/([^/]+)$/;
  var AUTH_WITH_VENDORS = /^\/api\/auth\/(me|login|signup)$/;

  function observe(path, method, res) {
    if (!res || !res.ok) return;
    if (method !== 'GET' && path.indexOf('/api/auth/') !== 0) {
      schedule();
      return;
    }
    if (path === '/api/auth/logout') {
      orgs = {};
      currentOrgId = null;
      return;
    }
    var vendorMatch = method === 'GET' && VENDOR.exec(path);
    var lists = (method === 'GET' && path === '/api/vendors') || AUTH_WITH_VENDORS.test(path);
    if (!vendorMatch && !lists) return;
    if (path === '/api/auth/login' || path === '/api/auth/signup') paused = false;

    res.clone().json().then(function (b) {
      if (!b || typeof b !== 'object') return;
      if (vendorMatch) {
        var v = b.vendor || {};
        currentOrgId = v.id || decodeURIComponent(vendorMatch[1]);
        register(currentOrgId, v.slug);
        return;
      }
      harvestVendors(b.vendors);
      // Staff can change a universal prompt every organisation uses, including
      // ones they are not a member of and never open in this page.
      if (b.staff === true && path.indexOf('/api/auth/') === 0) {
        return demoJson('/api/vendors?all=1').then(function (all) { harvestVendors(all.vendors); });
      }
    }).catch(function () {});
  }

  window.fetch = function (input, init) {
    var promise = demoFetch.apply(this, arguments);
    var path, method;
    try {
      var url = typeof input === 'string' ? input : (input && input.url) || String(input || '');
      path = new URL(url, location.origin).pathname;
      method = ((init && init.method) || (input && input.method) || 'GET').toUpperCase();
    } catch (e) {
      return promise;
    }
    if (path.indexOf('/api/') !== 0) return promise;
    return promise.then(function (res) {
      try { observe(path, method, res); } catch (e) { warn(e); }
      return res;
    });
  };

  /* ---------- embed code that stays current ---------- */

  // The copy button on the bank's page asks for today's prompt, and falls back
  // to the copy it was pasted with. Written without double quotes, ampersands
  // or angle brackets so it can sit in an HTML attribute untouched.
  var LIVE_COPY = "var b=this,s=b.dataset.prompt||'',c=navigator.clipboard;if(!c)return;" +
    "var t=fetch(b.dataset.live,{cache:'no-store'})" +
    ".then(function(r){return r.ok?r.json():null})" +
    ".then(function(d){return (d?(d.status==='published'?d.prompt:d.notice):'')||s},function(){return s});" +
    "if(window.ClipboardItem?c.write:0){c.write([new ClipboardItem({'text/plain':t.then(function(x){" +
    "return new Blob([x],{type:'text/plain'})})})]).catch(function(){t.then(function(x){c.writeText(x)})})}" +
    "else{t.then(function(x){c.writeText(x)})}";

  var SNAPSHOT_COPY = 'onclick="navigator.clipboard.writeText(this.dataset.prompt)"';
  var BUTTONS = {
    claude: /href="https:\/\/claude\.ai\/new\?q=[^"]*"/,
    chatgpt: /href="https:\/\/chatgpt\.com\/\?q=[^"]*"/,
    grok: /href="https:\/\/grok\.com\/\?q=[^"]*"/,
  };

  function attr(s) {
    return String(s).replace(/&/g, '&amp;').replace(/"/g, '&quot;').replace(/</g, '&lt;');
  }

  function rowFor(productSlug) {
    var here = orgs[currentOrgId];
    if (here && here.rows[productSlug]) return { org: here, row: here.rows[productSlug] };
    var hits = Object.keys(orgs).map(function (id) { return orgs[id]; })
      .filter(function (o) { return o.rows[productSlug]; });
    return hits.length === 1 ? { org: hits[0], row: hits[0].rows[productSlug] } : null;
  }

  function liveEmbed(text) {
    if (typeof text !== 'string') return text;
    var m = /<div class="lenni-get-started" data-product="([^"]+)">/.exec(text);
    if (!m) return text;
    var hit = rowFor(m[1]);
    var links = hit && hit.row.links;
    if (!links || !links.terminals || !links.page) return text;

    var out = text;
    Object.keys(BUTTONS).forEach(function (key) {
      var to = links.terminals[key];
      if (to) out = out.replace(BUTTONS[key], 'href="' + attr(to) + '"');
    });
    var api = links.page.replace(/\/go\/([^/]+)\/([^/]+)$/, '/api/live/$1/$2');
    if (api !== links.page) {
      out = out.replace(SNAPSHOT_COPY, 'onclick="' + LIVE_COPY + '" data-live="' + attr(api) + '"');
    }
    return out;
  }

  var clip = navigator.clipboard;
  if (clip && typeof clip.writeText === 'function') {
    var writeText = clip.writeText.bind(clip);
    clip.writeText = function (text) {
      var out = text;
      try { out = liveEmbed(text); } catch (e) { warn('embed code left as it was:', e); }
      return writeText(out);
    };
  }

  /* ---------- for support, and for the tests ---------- */

  window.__A2B_LIVE__ = {
    state: function () {
      var out = { paused: paused, current: currentOrgId, orgs: {} };
      Object.keys(orgs).forEach(function (id) {
        out.orgs[id] = { slug: orgs[id].slug, state: orgs[id].state, rows: Object.keys(orgs[id].rows) };
      });
      return out;
    },
    // What this page would send for one product. Used to seed the rows that
    // turn an organisation's live links on.
    payloadFor: function (vendorSlug, productSlug) {
      return demoJson('/api/public/start/' + enc(vendorSlug) + '/' + enc(productSlug)).then(payloadOf);
    },
    // Resolves once nothing is waiting to be sent.
    idle: function () {
      return new Promise(function (resolve) {
        (function poll() {
          var checking = Object.keys(orgs).some(function (id) { return orgs[id].state === 'checking'; });
          if (!timer && !running && !checking) resolve(true);
          else setTimeout(poll, 50);
        })();
      });
    },
  };
})();
