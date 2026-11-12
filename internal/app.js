/* Lenni internal portal.
 *
 * Vanilla, like the console, and for the same reason: it is served as static
 * files by the API and there is no build step to go wrong at deploy time.
 *
 * This file assumes nothing about who is allowed to see what. It renders what
 * the server returns and shows the gate when the server says no — every route
 * behind /api/admin re-checks the caller against the staff list, so a browser
 * that lies to itself about being staff still gets 404s.
 */

const esc = s => (s || '').toString().replace(/[<>&]/g, c => ({'<':'&lt;','>':'&gt;','&':'&amp;'}[c]));
const pct = (n, d) => d ? Math.round(n * 100 / d) : 0;

async function api(path, opts) {
  const r = await fetch('/api' + path, Object.assign(
    {headers: {'Content-Type': 'application/json'}}, opts || {}));
  const body = await r.json().catch(() => ({}));
  if (!r.ok) { const e = new Error(body.detail || r.statusText); e.status = r.status; throw e; }
  return body;
}

let ME = null;
let OVERVIEW = null;
let CUR = null;          // the company currently open, for the run screen's back button

function go(v) {
  document.querySelectorAll('.view').forEach(s => s.classList.toggle('on', s.dataset.v === v));
  document.querySelectorAll('.tab').forEach(t => t.classList.toggle('on', t.dataset.v === v));
  window.scrollTo(0, 0);
}

/* ---- dates -------------------------------------------------------------
 * Everything from the API is ISO-8601 UTC. Rendered in the reader's own zone,
 * because a support question is always "when did this happen for me".
 */
function when(iso) {
  if (!iso) return '—';
  const d = new Date(iso.endsWith('Z') || iso.includes('+') ? iso : iso + 'Z');
  if (isNaN(d)) return iso;
  return d.toLocaleString([], {month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit'});
}
function ago(iso) {
  if (!iso) return 'never';
  const d = new Date(iso.endsWith('Z') || iso.includes('+') ? iso : iso + 'Z');
  const mins = Math.round((Date.now() - d) / 60000);
  if (mins < 1) return 'just now';
  if (mins < 60) return mins + 'm ago';
  if (mins < 1440) return Math.round(mins / 60) + 'h ago';
  return Math.round(mins / 1440) + 'd ago';
}

/* ---- sign in ---------------------------------------------------------- */
async function signIn() {
  const err = document.getElementById('g-err');
  err.textContent = '';
  try {
    await api('/auth/login', {method: 'POST', body: JSON.stringify({
      email: document.getElementById('g-email').value.trim(),
      password: document.getElementById('g-pass').value})});
    await boot();
  } catch (e) {
    err.textContent = e.status === 401 ? 'That email and password do not match.' : e.message;
  }
}

async function boot() {
  try { ME = await api('/auth/me'); } catch (e) { ME = null; }
  const staff = ME && ME.staff;
  const staffless = !staff && ME && ME.user ? ME.user.email : '';
  document.getElementById('nav').classList.toggle('hide', !staff);
  document.getElementById('who').textContent = staff ? ME.user.email : '';
  if (!staff) {
    // Everything drawn from a previous session goes, and goes before the gate
    // is shown. Signing out, or having access removed while the tab is open,
    // otherwise leaves one bank's customer list sitting in the DOM behind a
    // login box — which is where somebody would find it after handing over a
    // laptop, and it is the sort of thing that only ever surfaces then.
    ME = OVERVIEW = CUR = null;
    ['companies', 'totals', 'flags', 'series', 'company', 'run', 'access', 'demand']
      .forEach(id => { const el = document.getElementById(id); if (el) el.innerHTML = ''; });
    // Same screen whether they are signed out or signed in without access.
    const msg = document.getElementById('gate-msg');
    msg.textContent = staffless
      ? 'Signed in as ' + staffless + '. Nothing here for this account.'
      : 'Sign in with a Lenni account.';
    return go('gate');
  }
  go('overview');
  loadOverview();
}

/* ---- overview --------------------------------------------------------- */
async function loadOverview() {
  const days = document.getElementById('win').value;
  const host = document.getElementById('companies');
  host.innerHTML = '<div class="empty">Reading…</div>';
  try { OVERVIEW = await api('/admin/overview?days=' + days); }
  catch (e) { host.innerHTML = `<div class="empty">Could not load: ${esc(e.message)}</div>`; return; }

  const t = OVERVIEW.totals;
  const stat = (n, l, sub) => `<div class="stat"><b>${n}</b><span>${l}</span>${
    sub ? `<i>${sub}</i>` : ''}</div>`;
  document.getElementById('totals').innerHTML =
    stat(t.companies, 'companies', `${t.live_companies} have had a real customer`) +
    stat(t.products, 'products', `${t.published} published`) +
    stat(t.runs, 'customer runs', `${t.completed} finished`) +
    stat(t.deliveries, 'packages delivered',
         t.runs ? pct(t.deliveries, t.runs) + '% of runs' : '');

  renderFlags();
  renderSeries();

  const rows = OVERVIEW.companies.map((c, i) => {
    const runs = c.runs || {};
    return `<tr onclick="openCompany(${i})">
      <!-- The slug rather than the website. Repeated demo runs leave a dozen
           workspaces with the same name on the same domain, and the slug is the
           only thing on the row that tells them apart. -->
      <td><b>${esc(c.name)}</b><small>${esc(c.slug)}</small></td>
      <td>${esc(c.industry_key)}</td>
      <td class="n">${c.published}<small>of ${c.products}</small></td>
      <td class="n">${c.members || 0}</td>
      <td class="n">${runs.total || 0}<small>${runs.complete || 0} done</small></td>
      <td class="n">${(c.events || {}).delivery || 0}</td>
      <td>${c.live ? '<span class="pill live">live</span>'
                   : '<span class="pill idle">no runs yet</span>'}</td>
      <td class="t">${ago(c.last_active)}</td></tr>`;
  }).join('');

  host.innerHTML = `<table class="grid">
    <thead><tr><th>Company</th><th>Industry</th><th class="n">Published</th>
      <th class="n">Team</th><th class="n">Runs</th><th class="n">Delivered</th>
      <th>State</th><th class="t">Last activity</th></tr></thead>
    <tbody>${rows || '<tr><td colspan="8" class="empty">No companies yet.</td></tr>'}</tbody>
  </table>`;
}

/* Things worth interrupting someone about. Kept to conditions that are wrong
 * rather than merely low, so the row stays worth reading when it appears. */
function renderFlags() {
  const staged = (OVERVIEW.entry_modes || {});
  const fetched = (staged.staged || 0) + (staged.doc || 0);
  const out = [];
  if (fetched) out.push(`<b>${fetched} product${fetched === 1 ? '' : 's'}</b> still open in a mode
    that asks the customer's assistant to fetch something. Those are the ones that go stale
    or silently load an old version. Everything else carries its questions in the link.`);
  const dark = OVERVIEW.companies.filter(c => c.published && !c.live).length;
  if (dark) out.push(`<b>${dark} compan${dark === 1 ? 'y has' : 'ies have'}</b> published a product
    that no customer has ever started. Either nobody has put the button on their site, or the
    button is not being found.`);
  document.getElementById('flags').innerHTML =
    out.map(m => `<div class="flag">${m}</div>`).join('');
}

function renderSeries() {
  const s = OVERVIEW.series || [];
  const host = document.getElementById('series');
  if (!s.length) { host.innerHTML = '<div class="empty">No activity in this window.</div>'; return; }
  const max = Math.max(...s.map(d => d.events));
  host.innerHTML = `<div class="serieshead">Activity — every recorded customer action,
    last ${OVERVIEW.window_days} days</div><div class="bars">` +
    s.map(d => `<div class="bcol" title="${esc(d.day)}: ${d.events}">
      <div class="b" style="height:${Math.max(2, Math.round(d.events * 100 / max))}%"></div>
      <span>${esc(d.day.slice(5))}</span></div>`).join('') + '</div>';
}

/* ---- one company ------------------------------------------------------ */
async function openCompany(i) {
  const c = OVERVIEW.companies[i];
  go('company');
  const host = document.getElementById('company');
  host.innerHTML = '<div class="empty">Reading…</div>';
  let d;
  try { d = await api('/admin/companies/' + c.id); }
  catch (e) { host.innerHTML = `<div class="empty">Could not load: ${esc(e.message)}</div>`; return; }
  CUR = d;

  const v = d.vendor;
  const products = d.products.map(p => {
    const ev = p.events || {};
    const drop = p.dropoff.map(s => {
      const share = pct(s.completed, s.of);
      return `<div class="drop"><span>${esc(s.name)}</span>
        <div class="track"><div style="width:${share}%"></div></div>
        <i>${s.completed}/${s.of}</i></div>`;
    }).join('');
    const mode = p.entry_mode === 'inline' ? ''
      : `<span class="pill warn">${esc(p.entry_mode)} — their AI has to fetch</span>`;
    return `<div class="card">
      <div class="cardhead"><b>${esc(p.name)}</b>
        <span class="pill ${p.status === 'published' ? 'live' : 'idle'}">${esc(p.status)}</span>
        ${mode}<i>${p.sections} sections · ${esc(p.destination.mode)}</i></div>
      <div class="evrow">${['start_link_viewed','guide_read','session_started','skill_completed','delivery']
        .map(k => `<span>${ev[k] || 0}<i>${k.replace(/_/g, ' ')}</i></span>`).join('')}</div>
      ${drop ? `<div class="dropwrap"><div class="lbl">How far customers got</div>${drop}</div>` : ''}
    </div>`;
  }).join('');

  const runs = d.runs.map(r => `<tr onclick="openRun('${r.id}')">
    <td>${esc(r.skill_name || '—')}</td>
    <td>${esc(r.email || 'no email given')}</td>
    <td><span class="pill ${r.status === 'complete' ? 'live' : 'idle'}">${esc(r.status)}</span></td>
    <td>${esc(r.terminal || '—')}</td>
    <td class="t">${when(r.created_at)}</td></tr>`).join('');

  const members = d.members.map(m => `<tr><td>${esc(m.email)}</td><td>${esc(m.role)}</td>
    <td>${esc(m.status)}</td><td class="t">${when(m.created_at)}</td></tr>`).join('');

  host.innerHTML = `
    <div class="head"><h2>${esc(v.name)}</h2></div>
    <p class="sub">${esc(v.industry_key)} ·
      ${v.website ? `<a href="${esc(v.website.startsWith('http') ? v.website : 'https://' + v.website)}"
        target="_blank" rel="noopener">${esc(v.website)}</a>` : 'no website'} ·
      joined ${when(v.created_at)}${v.convey_url ? ' · Convey connected' : ''}</p>

    <h3>Products</h3>
    ${products || '<div class="empty">No products yet.</div>'}

    <h3>Customer runs</h3>
    <p class="sub">Opening one shows what that customer actually entered, and records that
      you opened it.</p>
    <table class="grid"><thead><tr><th>Product</th><th>Customer</th><th>State</th>
      <th>Terminal</th><th class="t">Started</th></tr></thead>
      <tbody>${runs || '<tr><td colspan="5" class="empty">Nobody has started this company\'s intake yet.</td></tr>'}</tbody></table>

    <h3>Team</h3>
    <table class="grid"><thead><tr><th>Email</th><th>Role</th><th>State</th>
      <th class="t">Added</th></tr></thead><tbody>${members}</tbody></table>`;
}

/* ---- one customer run ------------------------------------------------- */
async function openRun(id) {
  go('run');
  const host = document.getElementById('run');
  host.innerHTML = '<div class="empty">Reading…</div>';
  document.getElementById('run-back').onclick = () => go('company');
  let d;
  try { d = await api('/admin/runs/' + id); }
  catch (e) { host.innerHTML = `<div class="empty">Could not load: ${esc(e.message)}</div>`; return; }

  const facts = d.run.facts || {};
  const status = d.run.section_status || {};
  const sections = (d.skill ? d.skill.sections : []).map(s => {
    const state = status[s.id] || 'pending';
    const asked = (s.asks || []).map(q => `<li>${esc(q)}</li>`).join('');
    return `<div class="card"><div class="cardhead"><b>${esc(s.name)}</b>
      <span class="pill ${state === 'complete' ? 'live' : 'idle'}">${esc(state)}</span></div>
      <ul class="asks">${asked}</ul></div>`;
  }).join('');

  const captured = Object.keys(facts).length
    ? `<table class="grid"><tbody>${Object.entries(facts).map(([k, v]) =>
        `<tr><td class="k">${esc(k)}</td><td>${esc(
          typeof v === 'object' ? JSON.stringify(v) : v)}</td></tr>`).join('')}</tbody></table>`
    : '<div class="empty">Nothing captured yet.</div>';

  host.innerHTML = `
    <div class="head"><h2>${esc(d.skill ? d.skill.name : 'Run')}</h2></div>
    <p class="sub">${esc(d.vendor ? d.vendor.name : '')} ·
      ${esc((d.profile && d.profile.email) || 'no email given')} ·
      started ${when(d.run.created_at)} · ${esc(d.run.terminal || 'terminal not recorded')}</p>
    <div class="notice">You are looking at a bank customer's own answers. This view is
      recorded against your account.</div>
    <h3>What they entered</h3>
    ${captured}
    <h3>Where they got to</h3>
    ${sections || '<div class="empty">The product behind this run no longer exists.</div>'}`;
}

/* ---- demand and access log -------------------------------------------- */
async function loadDemand() {
  const host = document.getElementById('demand');
  host.innerHTML = '<div class="empty">Reading…</div>';
  try {
    const d = await api('/ingest/demand');
    host.innerHTML = d.unmatched.length
      ? `<table class="grid"><thead><tr><th>Label found on a site</th>
         <th class="n">Times seen</th></tr></thead><tbody>${
        d.unmatched.map(u => `<tr><td>${esc(u.label)}</td><td class="n">${u.count}</td></tr>`)
          .join('')}</tbody></table>`
      : '<div class="empty">Every offering we have scanned matched a template.</div>';
  } catch (e) { host.innerHTML = `<div class="empty">Could not load: ${esc(e.message)}</div>`; }
}

async function loadAccess() {
  const host = document.getElementById('access');
  host.innerHTML = '<div class="empty">Reading…</div>';
  try {
    const d = await api('/admin/access-log');
    host.innerHTML = d.access.length
      ? `<table class="grid"><thead><tr><th>Who</th><th>Looked at</th><th>Company</th>
         <th class="t">When</th></tr></thead><tbody>${d.access.map(a =>
        `<tr><td>${esc(a.email)}</td>
          <td>${a.what === 'run' ? 'a customer\'s answers' : 'a company'}</td>
          <td>${esc(a.vendor_name || '—')}</td>
          <td class="t">${when(a.created_at)}</td></tr>`).join('')}</tbody></table>`
      : '<div class="empty">Nothing opened yet.</div>';
  } catch (e) { host.innerHTML = `<div class="empty">Could not load: ${esc(e.message)}</div>`; }
}

boot();
