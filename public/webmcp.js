/* Exposes the vendor's intake tools to an agent running inside the page.
 *
 * Every handler here forwards to POST /mcp/call. That is the point: a tool
 * called by a browser agent runs the same server code, against the same
 * session, as the identical tool called through the connector. Nothing about
 * the intake is answered from page state, so what a visitor sees here is what
 * a customer's assistant would get — and the answers survive the tab closing.
 *
 * Registration is best-effort. WebMCP is a moving target and no shipping
 * assistant speaks it yet, so the page must be useful when it is absent: the
 * catalogue and the call panel work regardless, and say which case you are in.
 */
(function () {
  'use strict';

  var cfg = window.A2B_PAGE || {};
  var BASE = (cfg.base || '').replace(/\/$/, '');
  var VENDOR = cfg.vendor || '';
  var SKILL = cfg.skill || '';
  var SESSION_KEY = 'a2b_session:' + VENDOR + ':' + SKILL;

  var TOOLS = [];
  var byName = {};

  function readSession() {
    try { return sessionStorage.getItem(SESSION_KEY) || ''; } catch (e) { return ''; }
  }
  function writeSession(id) {
    try { sessionStorage.setItem(SESSION_KEY, id); } catch (e) { /* private mode */ }
  }

  function properties(tool) {
    return (tool && tool.inputSchema && tool.inputSchema.properties) || {};
  }
  function accepts(name, key) {
    return Object.prototype.hasOwnProperty.call(properties(byName[name]), key);
  }

  // The three arguments a visitor should never have to type. Which vendor and
  // skill this is, is what the page already knows; the session is what the
  // first call handed back.
  function withContext(name, args) {
    var out = {};
    for (var k in (args || {})) {
      if (args[k] !== '' && args[k] != null) out[k] = args[k];
    }
    if (accepts(name, 'vendor') && !out.vendor) out.vendor = VENDOR;
    if (accepts(name, 'skill') && !out.skill) out.skill = SKILL;
    if (accepts(name, 'session_id') && !out.session_id) out.session_id = readSession();
    return out;
  }

  function callTool(name, args) {
    return fetch(BASE + '/mcp/call', {
      method: 'POST',
      headers: { 'content-type': 'application/json' },
      body: JSON.stringify({
        name: name,
        arguments: withContext(name, args),
        vendor: VENDOR,
        session_id: readSession() || null
      })
    }).then(function (r) { return r.json(); }).then(function (res) {
      var result = res && res.result;
      var id = result && (result.session_id || (result.session && result.session.id));
      if (id) writeSession(id);
      return res;
    });
  }

  // ---------------------------------------------------------------- registration

  function asToolResult(res) {
    var payload = res && Object.prototype.hasOwnProperty.call(res, 'result')
      ? res.result : res;
    var out = { content: [{ type: 'text', text: JSON.stringify(payload, null, 2) }] };
    if (res && res.error) out.isError = true;
    return out;
  }

  /* The shape of this API has changed more than once and is not settled. Rather
   * than bet on one spelling, try each and report which one took. */
  function register() {
    var host = (typeof document !== 'undefined' && document.modelContext)
      || (typeof navigator !== 'undefined' && navigator.modelContext)
      || window.agent;
    if (!host) return null;

    var descriptors = TOOLS.map(function (t) {
      return {
        name: t.name,
        description: t.description,
        inputSchema: t.inputSchema,
        annotations: t.annotations,
        execute: function (args) { return callTool(t.name, args).then(asToolResult); }
      };
    });

    try {
      if (typeof host.provideContext === 'function') {
        host.provideContext({ tools: descriptors });
        return 'provideContext';
      }
      if (typeof host.registerTool === 'function') {
        descriptors.forEach(function (d) { host.registerTool(d); });
        return 'registerTool';
      }
    } catch (e) {
      return null;
    }
    return null;
  }

  // ------------------------------------------------------------------------ UI

  function el(tag, cls, text) {
    var n = document.createElement(tag);
    if (cls) n.className = cls;
    if (text != null) n.textContent = text;
    return n;
  }

  function renderStatus(root, how) {
    var box = el('p', 'wm-status ' + (how ? 'wm-live' : 'wm-idle'));
    box.textContent = how
      ? 'This browser exposes WebMCP, so the ' + TOOLS.length +
        ' tools below are registered with your in-page agent and it can call them directly.'
      : 'No in-page agent detected in this browser. The tools below are live all the '
        + 'same — run one here, or add the connector and any assistant gets the same '
        + 'ones from anywhere.';
    root.appendChild(box);
  }

  function renderForm(root) {
    var form = el('div', 'wm-form');

    var pick = el('select', 'wm-pick');
    TOOLS.forEach(function (t) {
      pick.appendChild(new Option(t.name, t.name));
    });
    form.appendChild(pick);

    var desc = el('p', 'wm-desc');
    var fields = el('div', 'wm-fields');
    var out = el('pre', 'wm-out');
    out.textContent = 'No call yet.';

    function paintFields() {
      var tool = byName[pick.value];
      desc.textContent = tool.description || '';
      fields.innerHTML = '';
      var props = properties(tool);
      var required = (tool.inputSchema && tool.inputSchema.required) || [];
      Object.keys(props).forEach(function (key) {
        var row = el('label', 'wm-row');
        var star = required.indexOf(key) >= 0 ? ' *' : '';
        row.appendChild(el('span', 'wm-key', key + star));
        var input = el('input', 'wm-input');
        input.name = key;
        input.placeholder = props[key].description || props[key].type || '';
        if (key === 'vendor') input.value = VENDOR;
        if (key === 'skill') input.value = SKILL;
        if (key === 'session_id') {
          input.value = readSession();
          input.placeholder = 'filled in once you start a session';
        }
        row.appendChild(input);
        fields.appendChild(row);
      });
    }

    pick.addEventListener('change', paintFields);

    var run = el('button', 'wm-run', 'Call this tool');
    run.addEventListener('click', function () {
      var args = {};
      fields.querySelectorAll('input').forEach(function (i) {
        if (i.value !== '') {
          // Objects come in as JSON so a fact bundle can be typed literally.
          try { args[i.name] = JSON.parse(i.value); }
          catch (e) { args[i.name] = i.value; }
        }
      });
      run.disabled = true;
      out.textContent = 'Calling ' + pick.value + '…';
      callTool(pick.value, args).then(function (res) {
        out.textContent = JSON.stringify(
          Object.prototype.hasOwnProperty.call(res, 'result') ? res.result : res, null, 2);
        paintFields();
      }).catch(function (err) {
        out.textContent = 'Call failed: ' + err;
      }).then(function () { run.disabled = false; });
    });

    form.appendChild(desc);
    form.appendChild(fields);
    form.appendChild(run);
    form.appendChild(out);
    root.appendChild(form);
    paintFields();
  }

  function renderCatalogue(root) {
    var list = el('ul', 'wm-list');
    TOOLS.forEach(function (t) {
      var li = el('li');
      li.appendChild(el('code', null, t.name));
      li.appendChild(el('span', 'wm-tdesc', ' — ' + (t.description || '')));
      list.appendChild(li);
    });
    root.appendChild(list);
  }

  function boot() {
    var root = document.getElementById('a2b-webmcp');
    if (!root) return;
    fetch(BASE + '/mcp/tools').then(function (r) { return r.json(); }).then(function (data) {
      TOOLS = (data && data.tools) || [];
      TOOLS.forEach(function (t) { byName[t.name] = t; });
      if (!TOOLS.length) return;
      var how = register();
      root.innerHTML = '';
      root.appendChild(el('h2', 'wm-h', 'Tools on this page'));
      renderStatus(root, how);
      renderCatalogue(root);
      renderForm(root);
    }).catch(function (err) {
      root.textContent = 'Could not load the tool list: ' + err;
    });
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', boot);
  } else {
    boot();
  }
})();
