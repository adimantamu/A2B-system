/* Vendor console.
 *
 * The markup and the rendering below are the demo's, kept deliberately close so
 * the two can be compared side by side. What changed is underneath: every array
 * the demo held in memory is now a real record, and every mutation is a request
 * the server can refuse.
 */

/* ============ core helpers ============ */
function go(id){document.querySelectorAll('.view').forEach(v=>v.classList.remove('on'));document.getElementById(id).classList.add('on');window.scrollTo(0,0);}
let tt;function toast(m){const t=document.getElementById('toast');t.innerHTML=m;t.classList.add('on');clearTimeout(tt);tt=setTimeout(()=>t.classList.remove('on'),3600);}
const modal=document.getElementById('modal'),mbox=document.getElementById('mbox');
function openModal(h){mbox.innerHTML=h;modal.classList.add('on');}
function closeModal(){modal.classList.remove('on');}
modal.addEventListener('click',e=>{if(e.target===modal)closeModal();});
function scrollToId(id){document.getElementById(id).scrollIntoView({behavior:'smooth'});}
function scrollToTopAndFocus(){window.scrollTo({top:0,behavior:'smooth'});setTimeout(()=>document.getElementById('url-in').focus(),500);}
function copyDemo(m){try{navigator.clipboard.writeText(m);}catch(e){}toast(m);}
function copyText(text,msg){try{navigator.clipboard.writeText(text);}catch(e){}toast(msg);}
function esc(s){return (s||'').replace(/</g,'&lt;');}

/* ============ api ============ */
async function api(path,opts){
  const r=await fetch('/api'+path,Object.assign({headers:{'Content-Type':'application/json'}},opts||{}));
  const body=await r.json().catch(()=>({}));
  if(!r.ok){const e=new Error(body.detail||r.statusText);e.status=r.status;e.detail=body.detail;throw e;}
  return body;
}
const j=o=>({body:JSON.stringify(o)});

/* ============ state ============ */
let VENDOR=null;          // the tenant whose workspace is open
let PRODUCTS=[];          // skills, in the demo's shape
let curP=-1;
let RUN=null;             // the ingest run awaiting confirmation
let MATCHES=[];
let APPS=[];
let LIB={};               // library sections by industry name
let INDUSTRIES=[];
let MEMBERS=[];
let PRODUCT_REG=[];       // the template catalogue
let PROD_NAME={};         // product key -> display name, for match rows
let USER=null;            // the signed-in operator, per /auth/me
let MY_VENDORS=[];        // the workspaces they belong to
let MY_ROLE=null;         // their role on the open workspace
/* The sentences the scan lifted off the vendor's own site, by product. The
 * website preview reads better with the vendor's words than with a slug. */
let SITE_QUOTES={};

/* Sections carry view state the server has no opinion about. Keyed by section
 * id rather than index, so it survives a reorder. */
const uiState={};
const ui=id=>(uiState[id]=uiState[id]||{open:false,editing:false});

/* ============ mapping: api shape -> the shape the demo's renderers expect ==== */
function mapSkill(s){
  return {
    id:s.id, name:s.name, slug:s.slug, product_key:s.product_key,
    tmpl:s.seeded_from_template_id||'— custom —',
    tid:s.seeded_from_template_id||('custom-'+s.slug),
    status:s.status, custom:s.is_custom, version:s.version,
    pinned:s.seeded_from_template_version, template_status:s.template_status,
    dest:s.destination, entry:s.entry_prompt, entry_mode:s.entry_mode,
    secs:(s.sections||[]).map(x=>({
      id:x.id, name:x.name, m:x.summary, apps:x.apps||[], core:x.is_core,
      says:x.says, asks:x.asks||[], collects:x.collects||[],
      xind:x.origin_industry, ai:x.edited,
      get open(){return ui(x.id).open;}, set open(v){ui(x.id).open=v;},
      get editing(){return ui(x.id).editing;}, set editing(v){ui(x.id).editing=v;},
    })),
  };
}

async function loadSkills(){
  const d=await api('/vendors/'+VENDOR.id);
  PRODUCTS=(d.skills||[]).map(mapSkill);
}
async function reloadSkill(){
  if(curP<0)return;
  const d=await api('/skills/'+PRODUCTS[curP].id);
  PRODUCTS[curP]=mapSkill(d.skill);
  PRODUCTS[curP].links=d.links;
  PRODUCTS[curP].finalPrompt=d.final_prompt;
  PRODUCTS[curP].ptexts=d.prompt_texts||{};
  PRODUCTS[curP].pdefaults=d.prompt_defaults||{};
  PRODUCTS[curP].canPublish=d.can_publish;
  PRODUCTS[curP].blockers=d.publish_blockers;
  PRODUCTS[curP].warnings=d.warnings;
}

/* ============ scan flow ============ */
/* The animation is the demo's. The difference is that it now waits on a real
 * request — the last tick does not advance until the server has answered. */
async function startScan(){
  const u=document.getElementById('url-in').value.trim()||'bluebonnetbank.com';
  document.getElementById('scan-url').textContent=u;
  document.getElementById('conf-url').textContent=u;
  go('v-scan');
  const steps=[...document.querySelectorAll('#scan-steps .scanstep')];
  steps.forEach(s=>s.className='scanstep');

  const scanning=api('/ingest/scan',Object.assign({method:'POST'},j({url:u}))).catch(e=>({error:e}));

  let i=0;
  const tick=async()=>{
    if(i>0)steps[i-1].className='scanstep on done';
    if(i<steps.length){steps[i].className='scanstep on now';i++;setTimeout(tick,850);return;}
    const res=await scanning;
    if(res.error){
      toast('Could not read '+u+' — '+(res.error.detail||res.error.message));
      go('v-home');return;
    }
    RUN=res;MATCHES=res.matches.map(m=>({
      id:m.id, found:m.found_label, quote:m.found_quote?'"'+m.found_quote+'"':'',
      tmpl:m.template_id?(PROD_NAME[m.product_key]||m.template_id):null,
      tid:m.template_id, conf:m.confidence,
      on:!!m.template_id, name:PROD_NAME[m.product_key]||m.found_label,
    }));
    SITE_QUOTES={};res.matches.forEach(m=>{if(m.product_key&&m.found_quote)SITE_QUOTES[m.product_key]=m.found_quote;});
    renderIndustry();renderMatches();
    setTimeout(()=>go('v-industry'),500);
  };
  tick();
}

function renderIndustry(){
  if(!RUN)return;
  const top=(RUN.industry_suggestions||[])[0];
  const pct=top?Math.min(99,Math.round(top.score*100)):0;
  const icons={banking:'🏦',legal:'⚖️',insurance:'🛡️','real_estate':'🏠','cpa_tax':'📊'};
  const host=document.querySelector('#v-industry .indpick');
  host.innerHTML=`<div class="ic">${icons[top?top.key:'']||'🏢'}</div>
    <div><b>${top?top.name:'Unknown'}</b><span>${RUN.title||RUN.url} — detected from the words on your own pages</span></div>
    <div class="conf"><span class="pill live">${pct}% match</span><div style="font-size:11px;color:var(--muted);margin-top:3px;">${MATCHES.filter(m=>m.tid).length} skill templates matched</div></div>`;
  document.getElementById('conf-sub').innerHTML=`We read <b id="conf-url">${esc(RUN.url)}</b> — ${MATCHES.length} service areas found. First: did we get your industry right?`;
  const alts=document.querySelector('#v-industry .indalt');
  alts.innerHTML=INDUSTRIES.filter(x=>x.templates).slice(0,5).map(x=>
    `<button onclick="pickIndustry('${x.key}','${esc(x.name)}')">${x.name}</button>`).join('')+
    `<button onclick="toast('${INDUSTRIES.length} industries in the library — this list scrolls in production.')">More…</button>`;
  document.getElementById('ind-yes').textContent='Yes — '+(top?top.name:'that')+' is right →';
}
function pickIndustry(key,name){
  RUN.industry_suggestions=[{key,name,score:1}].concat((RUN.industry_suggestions||[]).filter(s=>s.key!==key));
  renderIndustry();
  toast(name+' selected — matching re-runs against that library when you confirm.');
}

/* ============ matching ============ */
function renderMatches(){
  const host=document.getElementById('match-list');
  host.innerHTML=MATCHES.map((m,i)=>{
    if(!m.tmpl)return `<div class="matchrow custom"><input type="checkbox" ${m.on?'checked':''} onchange="MATCHES[${i}].on=this.checked;renderMatches()"><div class="found"><b>${esc(m.found)}</b><span>${esc(m.quote)}</span></div><div class="arrow">→</div><div class="tmpl"><b>No template yet</b><span>build a custom skill — and you just taught our roadmap</span></div><div class="cf"><span class="pill draft">Custom</span></div></div>`;
    return `<div class="matchrow ${m.on?'':'off'}"><input type="checkbox" ${m.on?'checked':''} onchange="MATCHES[${i}].on=this.checked;renderMatches()"><div class="found"><b>${esc(m.found)}</b><span>${esc(m.quote)}</span></div><div class="arrow">→</div><div class="tmpl"><b>${esc(m.tmpl)}</b><span>${m.tid} · seeds pinned</span></div><div class="cf"><span class="pill ${m.conf==='High'?'live':'draft'}">${m.conf}</span></div></div>`;
  }).join('');
  document.getElementById('match-count').textContent=MATCHES.filter(m=>m.on).length+' of '+MATCHES.length+' selected — you can add more later from the Banking library';
}

/* ============ workspace ============ */
async function buildWorkspace(){
  /* Scanning is anonymous on purpose — it is the first thing a prospect does.
   * Owning the result is not, so this is where the account gets created. The
   * scan is held rather than discarded, and submitAuth resumes here. */
  if(!USER){
    /* stashPendingBuild inside the handoff keeps the run_id and the ticked
     * matches across the navigation, and boot() resumes here. */
    openSignup();
    return;
  }
  const top=(RUN.industry_suggestions||[])[0];
  const host=(RUN.url||'').replace(/^https?:\/\//,'').replace(/\/.*$/,'');
  /* A page title is usually "Name — tagline". The tagline is the description,
   * not part of the company's name, and it looks wrong in the sidebar. */
  const title=(RUN.title||host).trim();
  const name=title.split(/\s+[—|–]\s+/)[0].trim()||host;
  const tagline=title.slice(name.length).replace(/^\s*[—|–]\s*/,'').trim();
  try{
    const v=await api('/vendors',Object.assign({method:'POST'},j({
      name, industry_key:top?top.key:'banking', website:host, description:tagline,
    })));
    VENDOR=v.vendor;MY_ROLE=v.role||'admin';
    MY_VENDORS=MY_VENDORS.concat([Object.assign({role:MY_ROLE},VENDOR)]);
    await api('/ingest/'+RUN.run_id+'/confirm',Object.assign({method:'POST'},j({
      vendor_id:VENDOR.id, industry_key:top?top.key:null,
      match_ids:MATCHES.filter(m=>m.on).map(m=>m.id),
    })));
    await loadSkills();
    enterWorkspace();
    toast('Workspace built. Your services are now draft skills — open one to make it yours.');
  }catch(e){toast('Could not build the workspace: '+e.message);}
}
function enterWorkspace(){
  document.getElementById('ws-vendor').textContent=VENDOR.name;
  document.getElementById('ws-vendor-meta').textContent=VENDOR.website+' · '+(VENDOR.industry_key||'').replace('_',' ');
  document.getElementById('team-sub-vendor').textContent=VENDOR.name;
  document.getElementById('site-sub-host').textContent=VENDOR.website;
  go('v-ws');wsGo('dash');renderProducts();renderStore();renderAgent();loadTeam();
}
function wsGo(v){
  document.querySelectorAll('.wsview').forEach(x=>x.classList.toggle('on',x.dataset.wsv===v));
  document.querySelectorAll('.ws-nav .nitem[data-wsv]').forEach(n=>n.classList.toggle('active',n.dataset.wsv===v));
  document.querySelector('.ws-main').scrollTop=0;
}
function renderProducts(){
  const host=document.getElementById('prod-list');
  host.innerHTML=PRODUCTS.map((p,i)=>`<div class="prod" onclick="openProduct(${i})">
    <div class="pic">${p.custom?'✎':'UW'}</div>
    <div><b>${esc(p.name)}</b><div class="m">${p.custom?'custom skill':'template: '+esc(p.tmpl)+' · pinned '+p.pinned}${p.template_status&&p.template_status.update_available?' · <span style="color:var(--warn);">update available</span>':''} · ${p.secs.length} sections</div></div>
    <div class="r">${p.status==='published'?'<span class="pill live">Published</span>':'<span class="pill draft">Draft</span>'}<span style="color:var(--muted);font-weight:800;">›</span></div>
  </div>`).join('');
  document.getElementById('nav-prod-count').textContent=PRODUCTS.length;
  document.getElementById('st-total').textContent=PRODUCTS.length;
  document.getElementById('st-pub').textContent=PRODUCTS.filter(p=>p.status==='published').length;
  const inLib=PRODUCT_REG.filter(x=>x.industry===VENDOR.industry_key&&x.has_template).length;
  document.getElementById('add-row').textContent=
    `+ Add another product from the ${libName(VENDOR.industry_key)} library (${inLib} templates) — or build a custom skill from scratch`;
}

/* ============ skill editor ============ */
async function openProduct(i){
  curP=i;
  await reloadSkill();
  paintEditor();
  wsGo('editor');
}
function paintEditor(){
  const p=PRODUCTS[curP];
  document.getElementById('ed-title').textContent=p.name;
  document.getElementById('ed-meta').textContent=(p.custom?'custom skill · your build':p.tid+' · seeded from '+p.tmpl)+' · '+p.status;
  document.getElementById('ed-tmpl').textContent=p.custom?'— (from scratch)':p.tid;
  const ts=p.template_status||{};
  document.getElementById('ed-pinned').textContent=p.pinned||'—';
  document.getElementById('ed-current').innerHTML=ts.latest_version
    ?(ts.update_available
      ?`<span style="color:var(--warn);">${ts.latest_version} · update available</span>`
      :`<span style="color:var(--em-d);">${ts.latest_version} · up to date</span>`)
    :'—';
  document.getElementById('ed-ver').textContent=p.status+' · v'+p.version;
  renderSecs();renderEntry();renderDest();drawQR();
}

/* ============ destination (the exit) ============ */
function destLabel(p){
  if(!p||!p.dest)return 'delivered securely';
  const d=p.dest;
  if(d.mode==='convey')return d.connected?'delivered to your Convey — secure messaging and document upload':'delivered to Convey (connect it below)';
  if(d.mode==='link')return 'sent to your secure link'+(d.link?' ('+d.link.replace(/^https?:\/\//,'').slice(0,34)+'…)':'');
  return 'emailed to '+(d.email||'your inbox');
}
async function setDest(patch){
  const p=PRODUCTS[curP];
  await api('/skills/'+p.id+'/destination',Object.assign({method:'PUT'},j(patch)));
  // The entry prompt carries the closing message now, so a delivery change
  // rewrites it too. Without this the panel keeps showing the old link until
  // the vendor navigates away and back.
  await reloadSkill();renderDest();renderSecs();renderEntry();
}
function renderDest(){
  const p=PRODUCTS[curP];if(!p)return;const d=p.dest;
  const out=(k,label,desc)=>`<label style="display:flex;gap:9px;align-items:flex-start;border:1px solid var(--line);border-radius:10px;padding:9px 12px;cursor:pointer;background:${d.outputs[k]?'var(--mint-l)':'#fff'};">
    <input type="checkbox" ${d.outputs[k]?'checked':''} style="accent-color:var(--em);margin-top:3px;" onchange="setDest({outputs:{${k}:this.checked}})">
    <span style="font-size:12.5px;"><b>${label}</b><br><span style="color:var(--muted);font-size:11.5px;">${desc}</span></span></label>`;
  const mode=(m,label,desc)=>`<label style="display:flex;gap:9px;align-items:flex-start;border:1.5px solid ${d.mode===m?'var(--em)':'var(--line)'};border-radius:10px;padding:10px 12px;cursor:pointer;background:${d.mode===m?'var(--mint-l)':'#fff'};flex:1;min-width:150px;">
    <input type="radio" name="destmode" ${d.mode===m?'checked':''} style="accent-color:var(--em);margin-top:3px;" onchange="setDest({mode:'${m}'})">
    <span style="font-size:12.5px;"><b>${label}</b><br><span style="color:var(--muted);font-size:11.5px;">${desc}</span></span></label>`;
  let modeDetail='';
  if(d.mode==='convey'){
    modeDetail=d.connected
      ?`<div style="display:flex;align-items:center;gap:10px;font-size:12.5px;"><span class="pill live">Connected ✓</span><span style="font-family:monospace;font-size:11.5px;color:var(--muted);">${esc(VENDOR.convey_url||'')}</span><button class="btn ghost xs" onclick="openConvey()">Change</button></div>`
      :`<button class="btn navy sm" onclick="openConvey()">Connect Convey by Lenni</button><span style="font-size:11.5px;color:var(--muted);margin-left:10px;">Your own instance — set up separately, paste the link here.</span>`;
  }else if(d.mode==='link'){
    modeDetail=`<div style="display:flex;gap:8px;"><input id="dest-link" style="flex:1;border:1px solid var(--line);border-radius:8px;padding:9px 11px;font-size:12.5px;font-family:monospace;" placeholder="https://dropbox.com/request/… · SharePoint · your portal" value="${esc(d.link)}"><button class="btn ghost xs" onclick="setDest({link:document.getElementById('dest-link').value}).then(()=>toast('Destination saved — completed packages route to your link.'))">Save</button></div>`;
  }else{
    modeDetail=`<div style="display:flex;gap:8px;"><input id="dest-email" style="flex:1;border:1px solid var(--line);border-radius:8px;padding:9px 11px;font-size:12.5px;" placeholder="loans@yourbank.com" value="${esc(d.email)}"><button class="btn ghost xs" onclick="setDest({email:document.getElementById('dest-email').value}).then(()=>toast('Destination saved — the package will be emailed here.'))">Save</button></div>
    <div style="font-size:11.5px;color:var(--warn);margin-top:7px;">⚠ Email is the least secure option for sensitive documents — fine for summaries; consider Convey or a secure link for the full package.</div>`;
  }
  document.getElementById('dest-panel').innerHTML=`<div class="entrycard" style="border-color:var(--em);margin-top:14px;">
    <div class="ek">⤓ Destination — what happens when they finish</div>
    <div style="font-size:12px;color:var(--muted);margin-bottom:10px;">The customer completes the last section, everything they built gets packaged as a recap of the whole session — then it travels to you.</div>
    <div style="font-size:10.5px;letter-spacing:.12em;text-transform:uppercase;color:var(--em-d);font-weight:800;margin-bottom:7px;">What gets produced</div>
    <div style="display:grid;grid-template-columns:1fr 1fr 1fr;gap:8px;margin-bottom:14px;">
      ${out('pdf','PDF package','Formatted recap of everything completed — ready for the desk')}
      ${out('md','Markdown files','Structured MD of every section — loads into any system or AI')}
      ${out('doclist','Required-documents list','The checklist with status — attached vs. still owed')}
    </div>
    <div style="font-size:10.5px;letter-spacing:.12em;text-transform:uppercase;color:var(--em-d);font-weight:800;margin-bottom:7px;">Where it's delivered</div>
    <div style="display:flex;gap:8px;flex-wrap:wrap;margin-bottom:10px;">
      ${mode('convey','Convey by Lenni','Secure messaging + document upload — recommended')}
      ${mode('link','Secure link','Dropbox file request, SharePoint, your own portal')}
      ${mode('email','Email','Package summary to an inbox')}
    </div>
    ${modeDetail}
    <div class="dest-hint">The closing message your customer sees after their PDF —
      and the link above that carries it — is edited with the rest of the opening
      message, under <b>Entry prompt</b>.</div>
  </div>`;
}

/* ============ entry prompt ============ */
function renderEntry(){
  const p=PRODUCTS[curP];if(!p)return;
  /* Vendors read this panel as "the whole thing my customer gets" and then ask why
   * three of their four sections are missing. Say up front that it is section one
   * and that the rest is served as the conversation reaches it. */
  const n=(p.secs||[]).length;
  const mode=p.entry_mode||'staged';
  const notes={
    inline:'Nothing is fetched, so it behaves the same in every assistant. Your questions are readable by anyone who opens the link.',
    doc:'Their AI has to fetch your guide first, and some assistants read a cached copy instead of your live one.',
    staged:'Heavier than the others, and the only one that survives an AI with browsing switched off.'
  };
  const rest=mode==='connector'
    ? `\n<span class="c">// connector only — every section is served by your machine as the\n`
      +`// conversation reaches it, so none of your intake travels in the link</span>`
    : mode==='inline'
    ? `\n<span class="c">// all ${n} sections travel in the link, compiled at the moment your customer\n`
      +`// clicks — their AI fetches nothing, so nothing can go stale or fail to load</span>`
    : mode==='doc'
    ? `\n<span class="c">// their AI reads your published guide, then works through your ${n} sections\n`
      +`// one at a time from your server — no questions travel in the link</span>`
    : (n>1
      ? `\n<span class="c">// sections 2-${n} are served one at a time as your customer finishes each one,\n`
        +`// so their first message stays short and nobody can read your whole intake from a link</span>`
      : '');
  const sel=document.getElementById('ed-entry-mode');
  if(sel)sel.value=mode;
  const note=document.getElementById('ed-entry-mode-note');
  if(note)note.textContent=notes[mode]||'';
  const acts=document.getElementById('ed-prompt-actions');
  if(acts)acts.style.display=editingEP?'none':'flex';
  document.getElementById('ed-prompt-wrap').innerHTML=editingEP?epEditor(p):
    '<div class="promptblock" id="ed-prompt">'+
    `<span class="c">// compiled from this skill's config — regenerates on every save</span>${rest}\n`+
    esc(p.entry||'').replace(new RegExp(VENDOR.name.replace(/[.*+?^${}()|[\]\\]/g,'\\$&'),'g'),
      '<span class="g">'+esc(VENDOR.name)+'</span>')
      .replace(esc(p.name),'<span class="g">'+esc(p.name)+'</span>')+'</div>';
  document.getElementById('ed-unilink').textContent=(p.links&&p.links.universal||'').replace(/^https?:\/\//,'');
  renderStartLinks();
}

/* ---- editing the opening message ----------------------------------------
 * The vendor owns the wording; we own the shape. Each editable paragraph is a
 * box of its own rather than one big field, because the parts that are not
 * theirs sit between them: their questions, which are edited as sections, and
 * the guardrail. Those show as locked rows so the order of the finished message
 * is still readable here — a vendor who cannot see where their words land ends
 * up rewriting the whole thing to be sure.
 *
 * A box left exactly as we wrote it is not saved as an override. It looks like
 * the same text either way, and the difference only shows months later, when we
 * improve a default: the vendor who never touched that paragraph gets the
 * improvement, and the one who rewrote it keeps their words.
 */
let editingEP=false;
function editEntry(){editingEP=true;renderEntry();}
function cancelEntry(){editingEP=false;renderEntry();}

function epEditor(p){
  const d=p.pdefaults||{},t=p.ptexts||{},f=p.finalPrompt||{},n=(p.secs||[]).length;
  const val=k=>(t[k]||d[k]||'');
  const box=(k,label,rows)=>
    `<div class="ep-lbl">${label}</div>`+
    `<textarea class="edt ep-edt" id="ep-${k}" rows="${rows}">${esc(val(k))}</textarea>`;
  const lock=(head,why)=>
    `<div class="ep-lock">🔒 ${esc(head)}<span>${esc(why)}</span></div>`;
  return `<div class="promptblock ep-wrap">
    ${box('fresh','HOW IT OPENS',7)}
    ${box('pacing','HOW YOUR AI IS PACED',5)}
    ${lock(`Your ${n} section${n===1?'':'s'}, in your wording`,'edit these as sections above')}
    ${box('section_check','AFTER EACH SECTION',3)}
    ${box('summary','WHEN EVERY SECTION IS ANSWERED',3)}
    ${box('pdf','TURNING IT INTO A PDF',5)}
    ${lock('This is preparation, not a credit decision','required — cannot be edited')}
    <div class="ep-head">The closing message — after their PDF exists</div>
    <div class="ep-lbl">OPENING</div>
    <textarea class="edt ep-edt" id="fp-open" rows="2">${esc(f.opening)}</textarea>
    ${lock(f.produced,'compiled from your outputs')}
    <div class="ep-lbl">DELIVERY INSTRUCTIONS (${esc(p.dest.mode)})</div>
    <textarea class="edt ep-edt" id="fp-deliver" rows="4">${esc(f.delivery)}</textarea>
    ${lock(f.link_line,'comes from your delivery settings')}
    <div class="ep-lbl">SIGN-OFF</div>
    <textarea class="edt ep-edt" id="fp-close" rows="2">${esc(f.sign_off)}</textarea>
    <div style="display:flex;gap:8px;margin-top:12px;">
      <button class="btn em sm" onclick="saveEntry()">Save wording</button>
      <button class="btn ghost sm" onclick="cancelEntry()">Cancel</button>
      <button class="btn ghost sm" style="margin-left:auto;" onclick="resetEntry()">Reset to default</button>
    </div>
  </div>`;
}

async function saveEntry(){
  const p=PRODUCTS[curP],d=p.pdefaults||{};
  const texts={};
  ['fresh','pacing','section_check','summary','pdf'].forEach(k=>{
    const el=document.getElementById('ep-'+k);
    if(!el)return;
    const v=el.value.trim();
    // Null, not the text, when it still matches ours — that is what keeps this
    // paragraph following our copy rather than freezing at today's version.
    texts[k]=(v&&v!==(d[k]||'').trim())?v:null;
  });
  const dtexts={open:document.getElementById('fp-open').value,
    close:document.getElementById('fp-close').value,
    deliver:Object.assign({},(p.dest.texts||{}).deliver||{},
      {[p.dest.mode]:document.getElementById('fp-deliver').value})};
  editingEP=false;
  await api('/skills/'+p.id+'/prompt-texts',Object.assign({method:'PUT'},j(texts)));
  await setDest({texts:dtexts});
  toast('Saved — every start link from now on carries your wording.');
}

async function resetEntry(){
  const p=PRODUCTS[curP];
  editingEP=false;
  await api('/skills/'+p.id+'/prompt-texts',Object.assign({method:'PUT'},
    j({fresh:null,pacing:null,section_check:null,summary:null,pdf:null})));
  await setDest({texts:{open:null,close:null,deliver:{convey:null,link:null,email:null}}});
  toast('Back to the Lenni wording.');
}

/* The five rows under "Start links". Real anchors rather than click handlers, so
 * the row behaves like a link: it opens on click, middle-click and cmd-click, and
 * the browser shows the destination on hover. A window.open() in a handler gets
 * none of that and can be eaten by a popup blocker.
 *
 * Each row also carries a copy button, because the vendor needs two different
 * things from this list — to try the link, and to paste it into their website. */
const TERM_ICON={claude:{t:'C',bg:'#c96442'},chatgpt:{t:'G',bg:'#0f9d78'},
                 grok:{t:'𝕏',bg:'#1b1b1b'},copilot:{t:'Co',bg:'#4a6ba5'}};
function renderStartLinks(){
  const host=document.getElementById('start-links');
  if(!host)return;
  const p=PRODUCTS[curP];
  if(!p||!p.links){host.innerHTML='';return;}
  const rows=(p.links.terminals||[]).map(t=>{
    const ic=TERM_ICON[t.key]||{t:'AI',bg:'var(--navy)'};
    const badge=t.supported
      ? (t.autosubmits?'':'<small style="display:inline;color:var(--muted);"> · fills the box, you press send</small>')
      : '<small style="display:inline;color:var(--warn);"> · copy &amp; paste — this one takes no prompt from a link</small>';
    const open=t.supported&&t.url
      ? `<a class="startlink" href="${esc(t.url)}" target="_blank" rel="noopener"
           title="${esc(t.note||'')}" onclick="noteStart('${t.key}')">
           <span class="ti" style="background:${ic.bg};">${ic.t}</span>
           <span>Start in ${esc(t.name)}${badge}</span></a>`
      /* Nothing to open, so the row copies the prompt instead of pretending. */
      : `<div class="startlink" onclick="copyStart('${t.key}')" title="${esc(t.note||'')}">
           <span class="ti" style="background:${ic.bg};">${ic.t}</span>
           <span>Start in ${esc(t.name)}${badge}</span></div>`;
    return `<div class="startrow">${open}
      <button class="btn ghost xs linkcopy" title="Copy this link for your website"
        onclick="copyStart('${t.key}')">copy</button></div>`;
  }).join('');
  /* Opens the page that answers today; copies the one for the vendor's own site.
   * These are different addresses and conflating them produced a button that
   * went to a domain we do not serve. */
  const universal=`<div class="startrow">
    <a class="startlink" href="${esc(p.links.hosted||p.links.universal||'#')}" target="_blank" rel="noopener"
       title="A chooser page that remembers which AI the customer picked">
      <span class="ti" style="background:var(--em);">∞</span>
      <span>Any AI terminal <small style="display:inline;">· universal, routes by choice</small></span></a>
    <button class="btn ghost xs linkcopy" title="Copy the universal link — this is the one for your website"
      onclick="copyStart('any')">copy</button></div>`;
  /* A draft has no public start page, so every link here 404s. Say so on the
   * panel: one of these was handed to a customer and dead-ended in their AI. */
  const draft=p.links.live===false
    ? '<div style="font-size:11px;color:var(--warn);margin-top:8px;">This skill is still a draft, so these links lead nowhere yet — they are for previewing. Publish it and they go live.</div>'
    : '';
  const shortened=(p.links.terminals||[]).some(t=>t.shortened)
    ? '<div style="font-size:11px;color:var(--warn);margin-top:6px;">This skill\'s intake is too long to fit in a link, so the buttons above carry a short prompt that sends the AI to read the full workflow instead. The copy button still gives you everything.</div>'
    : '';
  host.innerHTML=rows+universal+draft+shortened;
}

/* Records that a vendor tried a terminal. Same event the customer-facing chooser
 * writes, so the funnel does not have to distinguish them. */
function noteStart(key){
  const p=PRODUCTS[curP];
  if(!p)return;
  const t=(p.links.terminals||[]).find(x=>x.key===key);
  if(t&&!t.autosubmits)toast(t.name+' opens with the prompt filled in — press enter there to send it.');
}

function copyStart(key){
  const p=PRODUCTS[curP];if(!p||!p.links)return;
  if(key==='any'){copyText(p.links.universal,'Universal start link copied: '+p.links.universal+' — this is the one for your website.');return;}
  const t=(p.links.terminals||[]).find(x=>x.key===key);
  if(!t)return;
  if(t.supported&&t.url){copyText(t.url,'Start-in-'+t.name+' link copied — it opens with your first section.');return;}
  /* No working deep link for this terminal. Copy the prompt instead of handing
   * the vendor a button that dies in front of their customer. */
  copyText(p.entry||'',t.name+': '+t.note);
}

/* ============ sections ============ */
function renderSecs(){
  const p=PRODUCTS[curP];
  document.getElementById('sec-list').innerHTML=p.secs.map((s,i)=>{
    const head=`<div class="sh" onclick="togSec(${i})">
      <div class="num">${i+1}</div>
      <div style="min-width:0;"><b>${esc(s.name)}${s.core?'':' <span class="pill draft" style="font-size:9.5px;">added</span>'}${s.xind?' <span class="pill navy" style="font-size:9.5px;">from '+esc(s.xind)+'</span>':''}${s.ai?' <span class="pill navy" style="font-size:9.5px;">your edits</span>':''}</b><div class="m">${esc(s.m)}</div>
        <div class="apps">${s.apps.map(a=>`<span class="appchip ${a==='convey-link'?'conv':''}">${a}</span>`).join('')}<span class="appchip" style="background:#eef2f7;color:var(--muted);cursor:pointer;" onclick="event.stopPropagation();openAddApp(${i})">+ app</span></div></div>
      <div class="ctr" onclick="event.stopPropagation()">
        <button class="icbtn" title="Move up" onclick="moveSec(${i},-1)">↑</button>
        <button class="icbtn" title="Move down" onclick="moveSec(${i},1)">↓</button>
        <button class="icbtn" title="Remove" onclick="delSec(${i})">✕</button>
        <button class="icbtn" title="${s.open?'Collapse':'See the script'}" onclick="togSec(${i})">${s.open?'▴':'▾'}</button>
      </div></div>`;
    if(!s.open)return `<div class="sec">${head}</div>`;
    let body;
    const nxt=p.secs[i+1];
    const flow=nxt?`When this section is done, the conversation moves on to <b>${i+2} — ${esc(nxt.name)}</b>.`:`This is the last section — everything is packaged and <b>${destLabel(p)}</b> (see Destination below).`;
    if(s.editing){
      body=`<div class="sec-body">
        <div class="sb-k">Opening — what the AI says</div>
        <textarea class="edt" id="ed-says-${i}" rows="3">${esc(s.says)}</textarea>
        <div class="sb-k">What it asks — each question is its own card</div>
        ${(s.asks||[]).map((a,ai2)=>`<div style="display:flex;gap:7px;margin-bottom:7px;align-items:flex-start;"><div class="an" style="width:18px;height:18px;border-radius:6px;background:var(--navy);color:#fff;font-size:10.5px;font-weight:800;display:flex;align-items:center;justify-content:center;flex:0 0 18px;margin-top:10px;">${ai2+1}</div><textarea class="edt ask-edit-${i}" rows="2" style="margin:0;">${esc(a)}</textarea><button class="icbtn" style="margin-top:6px;" title="Remove question" onclick="rmAsk(${i},${ai2})">✕</button></div>`).join('')}
        <button class="btn ghost xs" style="margin-bottom:4px;" onclick="addAsk(${i})">＋ Add a question</button>
        <div class="sb-acts"><button class="btn em sm" onclick="saveSec(${i})">Save section</button><button class="btn ghost sm" onclick="PRODUCTS[curP].secs[${i}].editing=false;renderSecs()">Cancel</button><span style="font-size:11.5px;color:var(--muted);">Edits land on <b>your version</b> — the template underneath never moves.</span></div>
      </div>`;
    }else{
      body=`<div class="sec-body">
        <div class="sb-k">Opening — what your customer's AI says</div>
        <div class="say">"${esc(s.says)}"</div>
        <div class="sb-k">What it asks, in order</div>
        ${(s.asks||[]).map((a,ai2)=>`<div class="askrow"><div class="an">${ai2+1}</div><div>${esc(a)}</div></div>`).join('')||'<div style="font-size:12px;color:var(--muted);">Nothing yet — hit ✎ Edit and draft it.</div>'}
        ${s.collects&&s.collects.length?`<div class="sb-k">What it collects</div><div class="collectchips">${s.collects.map(c=>'<span>'+esc(c)+'</span>').join('')}</div>`:''}
        <div style="font-size:12px;color:var(--muted);margin-top:10px;">${flow}</div>
        <div class="sb-acts"><button class="btn navy sm" onclick="PRODUCTS[curP].secs[${i}].editing=true;renderSecs()">✎ Edit this section</button></div>
      </div>`;
    }
    return `<div class="sec open">${head}${body}</div>`;
  }).join('');
}
function togSec(i){const s=PRODUCTS[curP].secs[i];s.open=!s.open;if(!s.open)s.editing=false;renderSecs();}
function grabDraft(i){
  const s=PRODUCTS[curP].secs[i];
  const says=document.getElementById('ed-says-'+i);if(says)s.says=says.value;
  const rows=[...document.querySelectorAll('.ask-edit-'+i)];if(rows.length||s.editing)s.asks=rows.map(r=>r.value).filter(x=>x.trim());
}
async function saveSec(i){
  grabDraft(i);const s=PRODUCTS[curP].secs[i];
  await api('/sections/'+s.id,Object.assign({method:'PUT'},j({says:s.says,asks:s.asks})));
  s.editing=false;
  await reloadSkill();paintEditor();
  toast('Section saved to your version.');
}
function addAsk(i){grabDraft(i);PRODUCTS[curP].secs[i].asks.push('');renderSecs();}
function rmAsk(i,ai2){grabDraft(i);PRODUCTS[curP].secs[i].asks.splice(ai2,1);renderSecs();}
async function moveSec(i,d){
  const p=PRODUCTS[curP];const j2=i+d;if(j2<0||j2>=p.secs.length)return;
  await api('/sections/'+p.secs[i].id+'/move?delta='+d,{method:'POST'});
  await reloadSkill();renderSecs();renderEntry();
  toast('Section order updated — the chain and the entry prompt re-link automatically ('+(Math.min(i,j2)+1)+' ↔ '+(Math.max(i,j2)+1)+').');
}
async function delSec(i){
  const p=PRODUCTS[curP];
  try{
    await api('/sections/'+p.secs[i].id,{method:'DELETE'});
    await reloadSkill();renderSecs();renderEntry();
  }catch(e){
    /* 409 is the model refusing, not a failure. */
    toast(e.status===409?'Core sections anchor every skill in this industry — edit them, but they can\'t be removed.':e.message);
  }
}
function togEntry(){const b=document.getElementById('entry-body');const ch=document.getElementById('entry-chev');const open=b.style.display==='none';b.style.display=open?'block':'none';ch.textContent=open?'▾':'▸';}

/* ============ the Library ============ */
let libTab='banking';
function libName(k){const x=INDUSTRIES.find(i=>i.key===k);return x?x.name:k;}
function openLibrary(){libTab=VENDOR.industry_key||'banking';renderLibrary();}
function renderLibrary(){
  const p=PRODUCTS[curP];
  const tabs=INDUSTRIES.filter(x=>(LIB[x.key]||[]).length);
  const items=LIB[libTab]||[];
  const mine=VENDOR.industry_key;
  openModal(`<h3>The Library</h3><div class="sub">Pre-drafted sections and apps, ready to drop into <b>${p?esc(p.name):'your skill'}</b> — fully written, then yours to edit. <b>Browse any industry:</b> your machine isn't fenced in. A bank with trust services can borrow from Legal; a firm with a building can borrow from Banking.</div>
  <div style="display:flex;gap:6px;flex-wrap:wrap;margin-bottom:12px;">
    ${tabs.map(t=>`<button class="btn ${t.key===libTab?'em':'ghost'} xs" onclick="libTab='${t.key}';renderLibrary()">${t.name}${t.key===mine?' · yours':''}</button>`).join('')}
    <button class="btn ghost xs" onclick="toast('${INDUSTRIES.length} industries in the full library — more section sets land with each template release.')">+${Math.max(0,INDUSTRIES.length-tabs.length)} more…</button>
  </div>
  <div style="font-size:10.5px;letter-spacing:.12em;text-transform:uppercase;color:var(--em-d);font-weight:800;margin:6px 0 8px;">Pre-drafted sections · ${libName(libTab)} library ${libTab!==mine?'<span class="pill navy" style="font-size:9px;margin-left:6px;">cross-industry add</span>':''}</div>
  ${items.map((l,i)=>`<div class="matchrow" style="cursor:default;"><div class="found" style="flex:1.6;"><b>${esc(l.name)}</b><span style="font-style:normal;">${esc(l.summary)}</span></div><div class="cf"><button class="btn em xs" onclick="addLibSec('${libTab}',${i})">+ Add section</button></div></div>`).join('')||'<div style="font-size:12.5px;color:var(--muted);">No pre-drafted sections in this library yet.</div>'}
  <div style="font-size:10.5px;letter-spacing:.12em;text-transform:uppercase;color:var(--em-d);font-weight:800;margin:14px 0 8px;">Apps · plug into an existing section (all industries)</div>
  ${APPS.map(a=>`<div class="matchrow" style="cursor:default;"><div class="found" style="flex:1.6;"><b>${a.icon} ${esc(a.name)}</b><span style="font-style:normal;">${esc(a.blurb)}</span></div><div class="cf"><button class="btn ghost xs" onclick="closeModal();storeAdd('${a.id}')">+ Add app</button></div></div>`).join('')}
  <div style="margin-top:12px;display:flex;justify-content:space-between;align-items:center;"><button class="btn navy sm" onclick="addBlank()">+ Blank section instead</button><button class="btn ghost sm" onclick="closeModal()">Close</button></div>`);
}
async function addLibSec(tab,i){
  const l=(LIB[tab]||[])[i];
  await api('/skills/'+PRODUCTS[curP].id+'/sections/library',Object.assign({method:'POST'},j({industry_key:tab,index:i})));
  closeModal();
  await reloadSkill();
  const added=PRODUCTS[curP].secs[PRODUCTS[curP].secs.length-1];if(added)added.open=true;
  renderSecs();renderEntry();
  toast('"'+l.name+'" added'+(tab!==VENDOR.industry_key?' from the '+libName(tab)+' library':'')+', fully drafted — it\'s open below so you can read exactly what it says.');
}
async function addBlank(){
  closeModal();
  await api('/skills/'+PRODUCTS[curP].id+'/sections/blank?name=New%20blank%20section',{method:'POST'});
  await reloadSkill();renderSecs();renderEntry();
  toast('Blank section added — open it and draft away.');
}

/* ============ apps ============ */
function renderStore(){
  document.getElementById('store-grid').innerHTML=APPS.map(a=>`<div class="appcard"><div class="ic">${a.icon}</div><h4>${esc(a.name)}</h4><p>${esc(a.blurb)}</p>
   <div style="display:flex;justify-content:space-between;align-items:center;"><span class="pill ${a.implemented?'live':'off'}">${a.tag} · wave ${a.wave}${a.implemented?'':' · planned'}</span><button class="btn ghost xs" onclick="storeAdd('${a.id}')">Add to a skill</button></div></div>`).join('');
}
function storeAdd(id){
  if(id==='convey-link'){openConvey();return;}
  if(PRODUCTS.length===0){toast('Build your workspace first.');return;}
  openModal('<h3>Add '+id+'</h3><div class="sub">Which product and section?</div>'+PRODUCTS.map((p,pi)=>'<div style="font-weight:700;margin:10px 0 4px;">'+esc(p.name)+'</div>'+p.secs.map(s=>`<button class="btn ghost sm" style="margin:0 6px 6px 0;" onclick="attachApp('${s.id}','${id}',${pi})">${esc(s.name)}</button>`).join('')).join(''));
}
async function attachApp(sectionId,appId,pi){
  await api('/sections/'+sectionId+'/apps?app_id='+encodeURIComponent(appId),{method:'POST'});
  closeModal();
  if(curP===pi){await reloadSkill();renderSecs();}else{await loadSkills();renderProducts();}
  toast(appId+' added.');
}
function openAddApp(si){
  const s=PRODUCTS[curP].secs[si];
  openModal('<h3>Add an app to this section</h3><div class="sub">Apps run inside the section when the customer\'s AI reaches it.</div>'+
    APPS.map(a=>`<button class="btn ghost sm" style="margin:0 6px 8px 0;" onclick="${a.id==='convey-link'?"closeModal();openConvey()":`attachApp('${s.id}','${a.id}',${curP})`}">${a.icon} ${esc(a.name)}</button>`).join(''));
}

/* ============ convey ============ */
function openConvey(){
  openModal(`<h3>Connect Convey by Lenni</h3><div class="sub">Convey runs as your own instance — set it up separately, then paste your instance link. It becomes the terminal step of every published skill: the package and every sensitive document arrive there. Not email.</div>
  <input id="conv-in" style="width:100%;border:1px solid var(--line);border-radius:9px;padding:12px;font-size:14px;font-family:monospace;" placeholder="https://yourbank.convey.lenni.io" value="${esc(VENDOR.convey_url||'')}">
  <div style="font-size:12px;color:var(--muted);margin:10px 0 14px;">Don't use Convey? Any secure portal link works here — the skill will route customers to it at completion.</div>
  <div style="display:flex;gap:8px;justify-content:flex-end;"><button class="btn ghost" onclick="closeModal()">Cancel</button><button class="btn em" onclick="connectConvey()">Connect</button></div>`);
}
async function connectConvey(){
  const url=document.getElementById('conv-in').value.trim();
  closeModal();
  if(curP<0){toast('Open a skill first.');return;}
  await api('/skills/'+PRODUCTS[curP].id+'/connect-convey?url='+encodeURIComponent(url),{method:'POST'});
  VENDOR.convey_url=url;
  await reloadSkill();renderDest();renderSecs();
  toast('Convey connected — the destination of this skill is now your secure channel.');
}

/* What each mode costs the vendor, said in the toast rather than buried in help.
 * The trade is real and picking wrong is invisible until a customer is stuck. */
const ENTRY_MODE_TOAST={
  inline:'Your whole intake travels in the link now — their AI fetches nothing, so nothing can go stale.',
  doc:'Your guide carries it now — their AI reads it from your server and needs nothing installed.',
  staged:'Section 1 now travels in the link, so it works even with pages blocked.'
};

async function setEntryMode(mode){
  if(curP<0){toast('Open a skill first.');return;}
  await api('/skills/'+PRODUCTS[curP].id+'/entry-mode?mode='+encodeURIComponent(mode),{method:'POST'});
  await reloadSkill();paintEditor();
  toast(ENTRY_MODE_TOAST[mode]||'Entry mode updated.');
}

/* ============ publish + QR ============ */
async function publishSkill(){
  const p=PRODUCTS[curP];
  try{
    await api('/skills/'+p.id+'/publish',{method:'POST'});
    await reloadSkill();paintEditor();renderProducts();renderAgent();
    toast('<b>'+esc(p.name)+' published — your version, v'+PRODUCTS[curP].version+'.</b> Link + QR are live; your agent page just regenerated.');
  }catch(e){
    /* Publishing without a destination strands a real customer at the last step. */
    toast(e.detail||e.message);
  }
}
async function saveDraft(){toast('Saved — your version, on your account.');}
function drawQR(){
  const c=document.getElementById('qr');if(!c)return;const x=c.getContext('2d');x.fillStyle='#fff';x.fillRect(0,0,92,92);x.fillStyle='#203048';
  let seed=(curP+3)*7919;const rnd=()=>{seed=(seed*9301+49297)%233280;return seed/233280;};
  for(let r=0;r<21;r++)for(let col=0;col<21;col++){if(rnd()>0.52)x.fillRect(4+col*4,4+r*4,4,4);}
  const fin=(px,py)=>{x.fillStyle='#203048';x.fillRect(px,py,26,26);x.fillStyle='#fff';x.fillRect(px+4,py+4,18,18);x.fillStyle='#209D76';x.fillRect(px+8,py+8,10,10);};
  fin(4,4);fin(62,4);fin(4,62);
}

/* ============ agent page ============ */
async function renderAgent(){
  const host=VENDOR.website||VENDOR.slug;
  document.getElementById('ap-url').textContent=host+'/agents';
  document.getElementById('ap-name').textContent=VENDOR.name;
  document.getElementById('ap-desc').textContent=VENDOR.description||'';
  document.getElementById('ap-intro-site').textContent=host;
  document.getElementById('agent-svcs').innerHTML=PRODUCTS.map(p=>
    `<div class="svcrow"><div><b>${esc(p.name)}</b><span>skill: ${p.slug} · start: ${host}/start/${p.slug}</span></div><div class="r">${p.status==='published'?'<span class="pill live">runnable</span>':'<span class="pill draft">draft — hidden from agents</span>'}</div></div>`
  ).join('')||'<div style="color:var(--muted);font-size:13px;">No products yet.</div>';
  /* The manifest is generated server-side — this is the real file, not a mock. */
  const md=await fetch('/v/'+VENDOR.slug+'/llms.md').then(r=>r.text()).catch(()=>'');
  document.getElementById('llms-block').innerHTML=esc(md)
    .replace(/^(#+ .*)$/gm,'<span class="h">$1</span>')
    .replace(/^(&lt;!--.*)$/gm,'<span class="c">$1</span>');
}
function copyAgentUrl(){
  const u='https://'+(VENDOR.website||VENDOR.slug)+'/agents';
  copyText(u,'Agent page URL copied: '+u+' — add it to your site header + /llms.txt.');
}
async function copyHeaderSnippet(){
  const p=await fetch('/v/'+VENDOR.slug+'/webmaster').then(r=>r.json());
  copyText(p.head||'','Header snippet copied — one line for your webmaster; we host the page.');
}

/* The two lines that go on one product page. Fetched rather than built here so
 * the console cannot drift from what the server tells the vendor's web team. */
async function copyEmbed(){
  if(curP<0){toast('Open a skill first.');return;}
  const skill=PRODUCTS[curP];
  const p=await fetch('/v/'+VENDOR.slug+'/webmaster').then(r=>r.json());
  const s=(p.services||[]).find(x=>x.slug===skill.slug);
  if(!s){toast('Publish '+skill.name+' first — a draft gets no button, the same way it gets no manifest entry.');return;}
  copyText(s.head+'\n\n'+s.button,
    'Embed copied — the head line and the button for your '+skill.name+' page. Works as pasted.');
}

/* ============ add product ============ */
function openAddProduct(){
  const have=new Set(PRODUCTS.map(p=>p.tid));
  const avail=PRODUCT_REG.filter(x=>x.industry===VENDOR.industry_key&&x.has_template&&!have.has(x.template));
  openModal('<h3>Add a product</h3><div class="sub">From the '+libName(VENDOR.industry_key)+' library ('+PRODUCT_REG.filter(x=>x.industry===VENDOR.industry_key&&x.has_template).length+' templates) — or start a custom skill from scratch.</div>'+
   (avail.map(l=>`<button class="btn ghost sm" style="margin:0 6px 8px 0;" onclick="seedProduct('${l.template}','${esc(l.name)}')">${esc(l.name)}</button>`).join('')||'<div style="font-size:12.5px;color:var(--muted);">Every template in this library is already in your workspace.</div>')+
   '<div style="margin-top:10px;"><button class="btn navy sm" onclick="customProduct()">+ Build custom skill from scratch</button></div>');
}
async function seedProduct(tid,name){
  closeModal();
  await api('/vendors/'+VENDOR.id+'/skills/seed?template_id='+encodeURIComponent(tid),{method:'POST'});
  await loadSkills();renderProducts();renderAgent();
  toast(name+' added — seeded from '+tid+', pinned at that version.');
}
async function customProduct(){
  closeModal();
  await api('/vendors/'+VENDOR.id+'/skills/custom?name=Custom%20skill',{method:'POST'});
  await loadSkills();renderProducts();
  toast('Custom skill created — hydrate it section by section.');
}

/* ============ website preview ============ */
function renderSite(){
  const host=VENDOR.website||VENDOR.slug;
  const cards=PRODUCTS.map(p=>{
    const pub=p.status==='published';
    return `<div class="bs-card ${pub?'annot':''}">${pub?'<span class="lenni-tag">Lenni link</span>':''}
      <h5>${esc(p.name)}</h5><p>${esc(SITE_QUOTES[p.product_key]||'A service you offer.')}</p>
      <button class="bs-start ${pub?'':'off'}" onclick="${pub?`toast('This is your universal start link — opens the customer\\'s AI with the ${esc(p.name)} skill. Same button, every terminal.')`:`toast('Publishes when you publish the skill — draft skills stay invisible to customers and agents.')`}">${pub?'▸ Start in your AI':'available soon'}</button>
    </div>`;
  }).join('');
  document.getElementById('site-prev').innerHTML=`
  <div style="font-size:13px;color:var(--muted);margin-bottom:12px;">This is <b>their existing website</b> — same design, same pages. Everything Lenni adds is tagged in <b style="color:var(--em-d);">emerald</b>: one header line, one button per service page. That's the whole integration.</div>
  <div class="bsite">
    <div class="bs-chrome"><span class="dots"><span style="background:#f26d6d;"></span><span style="background:#f2c94c;"></span><span style="background:#6dd28f;"></span></span><span class="urlbar">https://${esc(host)}</span></div>
    <div class="bs-top">
      <div class="bs-logo">${esc(VENDOR.name.split(' ')[0])}<small>${esc(VENDOR.name.split(' ').slice(1).join(' ')||'')}</small></div>
      <div class="bs-nav2">
        <span>Personal</span><span>Business</span><span style="color:#1c2b45;border-bottom:2px solid #cf9a34;padding-bottom:2px;">Loans</span><span>About</span>
        <span class="annot" style="padding:4px 10px;font-size:11px;color:var(--em-d);font-weight:700;cursor:pointer;" onclick="wsGo('agent')">🤖 For AI agents</span>
      </div>
    </div>
    <div class="annot-label">⤴ <b>Added by Lenni:</b> the agent header — one line in their site header (<span style="font-family:monospace;">/llms.txt · /agents</span>). AI assistants land there instead of crawling every page. Click it to see the agent page.</div>
    <div class="bs-hero2"><h3>${esc(VENDOR.description||VENDOR.name)}</h3><p>Now ready for however your customers would like to start, including their own AI.</p></div>
    <div class="bs-svc">${cards}</div>
    <div class="annot-label" style="margin-bottom:16px;">⤴ <b>Added by Lenni:</b> one start button per service page — the replaced link. Published skills go live (tagged emerald); drafts show nothing to customers. Everything else on this page is theirs, untouched.</div>
  </div>
  <div class="card" style="margin-top:14px;">
    <h4 style="font-size:13.5px;margin-bottom:8px;">The webmaster packet — the entire integration</h4>
    <div style="font-family:'SF Mono',Consolas,monospace;font-size:11.5px;background:var(--navy-d);color:#cfe6dc;border-radius:10px;padding:12px 14px;line-height:1.8;">
    <span style="color:#6d829e;">&lt;!-- 1. one line in the site header --&gt;</span><br>
    &lt;link rel="llms" href="https://${esc(host)}/llms.txt"&gt;<br>
    <span style="color:#6d829e;">&lt;!-- 2. one button per service page (universal link) --&gt;</span><br>
    &lt;a href="https://${esc(host)}/start/${esc((PRODUCTS[0]||{slug:'your-skill'}).slug)}"&gt;Start in your AI&lt;/a&gt;</div>
    <div style="font-size:12px;color:var(--muted);margin-top:8px;">Nothing to rebuild. Nothing to update. One link and one file — Lenni hosts everything behind them.</div>
  </div>`;
}

/* ============ team ============ */
async function loadTeam(){
  const d=await api('/vendors/'+VENDOR.id);
  MEMBERS=d.members||[];
  const initials=n=>n.split(/[@.\s]/).filter(Boolean).slice(0,2).map(x=>x[0].toUpperCase()).join('');
  const me=USER?USER.email:'';
  document.getElementById('team-list').innerHTML=MEMBERS.map(m=>{
    const removable=(d.role==='admin')&&m.email!==me;
    return `<div class="teamrow"><div class="av">${initials(m.email)}</div><div><b>${esc(m.email.split('@')[0])}${m.email===me?' <span style="color:var(--muted);font-weight:400;">(you)</span>':''}</b><span>${esc(m.email)} · ${m.role}</span></div><div class="r"><span class="pill ${m.status==='active'?'live':'draft'}">${m.status==='active'?'Active':'Invited'}</span>${removable?`<button class="btn ghost xs" style="margin-left:8px;" onclick="removeMember('${esc(m.email)}')">Remove</button>`:''}</div></div>`;
  }).join('')||'<div style="font-size:12.5px;color:var(--muted);">No members yet — invite the first one.</div>';
  document.getElementById('nav-team-count').textContent=MEMBERS.length;
  MY_ROLE=d.role||MY_ROLE;
  paintAccount();
}
function addMember(){
  openModal(`<h3>Invite a team member</h3><div class="sub">Pick the role — admin publishes, editor drafts, viewer reads.</div>
  <input id="tm-in" style="width:100%;border:1px solid var(--line);border-radius:9px;padding:12px;font-size:14px;" placeholder="name@${esc(VENDOR.website||'yourcompany.com')}">
  <div style="display:flex;gap:8px;margin:12px 0;">
    <button class="btn ghost xs" onclick="tmRole='admin';toast('Role: admin')">Admin</button>
    <button class="btn ghost xs" onclick="tmRole='editor';toast('Role: editor')">Editor</button>
    <button class="btn ghost xs" onclick="tmRole='viewer';toast('Role: viewer')">Viewer</button>
  </div>
  <div style="font-size:11.5px;color:var(--warn);margin-bottom:10px;">⚠ The invite is recorded, but no email is sent yet — outbound mail is not wired up.</div>
  <div style="display:flex;gap:8px;justify-content:flex-end;"><button class="btn ghost" onclick="closeModal()">Cancel</button><button class="btn em" onclick="sendInvite()">Send invite</button></div>`);
}
let tmRole='editor';
async function sendInvite(){
  const email=document.getElementById('tm-in').value.trim();
  closeModal();
  if(!email)return;
  try{
    await api('/vendors/'+VENDOR.id+'/members',Object.assign({method:'POST'},j({email,role:tmRole})));
    await loadTeam();
    toast('Invite recorded for '+email+'. They get access the moment they sign up.');
  }catch(e){toast('Could not invite '+email+': '+e.message);}
}
async function removeMember(email){
  try{
    await api('/vendors/'+VENDOR.id+'/members?email='+encodeURIComponent(email),{method:'DELETE'});
    await loadTeam();
    toast('Removed '+email+'.');
  }catch(e){toast(e.message);}
}

/* ============ customer preview — runs the real tools ============ */
async function openPreview(){
  const p=PRODUCTS[curP>=0?curP:0];
  if(!p){toast('Build your workspace first.');return;}
  if(p.status!=='published'){toast('Publish the skill first — a draft is not runnable, which is the point.');return;}
  openModal('<h3>Customer experience — section chaining</h3><div class="sub">Running the real tools against your published skill — the same calls a connected AI makes.</div><div class="cp-thread" id="cp-thread"><div class="cp-t">▸ starting…</div></div><div style="display:flex;justify-content:flex-end;margin-top:12px;"><button class="btn em" onclick="closeModal()">Close</button></div>');
  const thread=document.getElementById('cp-thread');
  const line=h=>{thread.insertAdjacentHTML('beforeend',h);thread.scrollTop=thread.scrollHeight;};
  const call=async(name,args,ctx)=>{
    const r=await fetch('/mcp/call',{method:'POST',headers:{'Content-Type':'application/json'},
      body:JSON.stringify(Object.assign({name,arguments:args},ctx||{}))});
    const b=await r.json();
    if(!r.ok||b.error)throw new Error((b.error&&(b.error.message+(b.error.hint?' — '+b.error.hint:'')))||b.detail||r.statusText);
    return b;
  };
  try{
    thread.innerHTML='';
    const email='preview@'+(VENDOR.website||'example.com');
    const start=await call('lenni_start_session',{vendor:VENDOR.slug,skill:p.slug,email});
    const sid=start.result.session_id;
    line(`<div class="cp-t">▸ start_session { vendor: ${VENDOR.slug}, skill: ${p.slug} } → ${p.secs.length} sections</div>`);
    let s=start.result.section;
    for(let n=0;s&&n<20;n++){
      line(`<div class="cp-m a">${esc(s.says)}</div>`);
      if(s.asks&&s.asks.length)line(`<div class="cp-m u">${esc(s.asks[0])} — (preview answers automatically)</div>`);
      if(s.apps&&s.apps.length)line(`<div class="cp-t">▸ apps in this section: ${s.apps.join(', ')}</div>`);
      const nx=await call('lenni_complete_section',{section_id:s.section_id,facts:{preview:true}},{session_id:sid});
      line(`<div class="cp-t">▸ ${esc(s.name)} complete ✓ → complete_section()</div>`);
      s=nx.result.done?null:nx.result.section;
    }
    const v=await call('lenni_validate_package',{},{session_id:sid});
    line(`<div class="cp-t">▸ validate_package() → ${(v.result.gaps||[]).length} gaps</div>`);
    const f=await call('lenni_finish',{},{session_id:sid});
    const r=f.result||{};
    line(`<div class="cp-t">▸ finish() → ${esc(r.destination&&r.destination.mode||p.dest.mode)}</div>`);
    line(`<div class="cp-m a">✅ <b>${esc(r.closing_message||r.message||'Package complete and delivered securely.')}</b></div>`);
  }catch(e){line(`<div class="cp-m a" style="color:var(--red);">The run stopped: ${esc(e.message)}</div>`);}
}

/* ============ authentication ============
 * The session lives in an HttpOnly cookie, so this file cannot read it and does
 * not try. USER is what /auth/me last said, and every guess about what the user
 * may do is confirmed by the server on the next call.
 */
/* Signing in is its own page — /login, /signup — rather than a modal in here.
 * A reset link arriving from an email has to land somewhere, a password manager
 * needs an address it can recognise, and support has to be able to say it out
 * loud. This file now only reflects the session and hands off. */

/* A prospect scans their website before they have an account, and that scan
 * lives in these globals. Sign-in is a page navigation now, so it would be gone
 * by the time they came back, and they would be staring at an empty homepage
 * having just watched us read their site.
 *
 * The server already holds the run — /ingest/scan wrote an ingest_run with a
 * null vendor_id precisely so this could happen before signup — so only the
 * handle and the choices made on screen need carrying. sessionStorage is the
 * right scope: this tab, until it closes, and not a second visitor on a shared
 * machine. */
const PENDING_KEY='a2b_pending_build';

function stashPendingBuild(){
  if(!RUN||!RUN.run_id)return;
  try{
    sessionStorage.setItem(PENDING_KEY,JSON.stringify({
      run_id:RUN.run_id, url:RUN.url, title:RUN.title,
      industry_suggestions:RUN.industry_suggestions||[],
      match_ids:MATCHES.filter(m=>m.on).map(m=>m.id),
    }));
  }catch(e){/* private browsing, or a full quota. The scan is recoverable by
             * rescanning, so this is not worth failing the sign-in over. */}
}

function takePendingBuild(){
  try{
    const raw=sessionStorage.getItem(PENDING_KEY);
    sessionStorage.removeItem(PENDING_KEY);
    return raw?JSON.parse(raw):null;
  }catch(e){return null;}
}

function authHandoff(page){
  stashPendingBuild();
  window.location.href=page+'?next='+encodeURIComponent('/console/');
}
function openLogin(){authHandoff('/login');}
function openSignup(){authHandoff('/signup');}

/* Signed in and a scan was waiting: finish what they were actually doing. */
async function resumePendingBuild(){
  const p=takePendingBuild();
  if(!p||!p.run_id||!USER)return false;
  RUN=Object.assign(RUN||{},{run_id:p.run_id,url:p.url,title:p.title,
                             industry_suggestions:p.industry_suggestions||[]});
  MATCHES=(p.match_ids||[]).map(id=>({id,on:true}));
  await buildWorkspace();
  return true;
}

/* Signing out is a navigation to /logout rather than a call from here. The
 * reload is the point: it guarantees nothing is left in these globals, which is
 * exactly what someone signing out of a shared machine is asking for. */

function chooseVendor(){
  openModal(`<h3>Which workspace?</h3><div class="sub">You have access to more than one.</div>
  ${MY_VENDORS.map(v=>`<button class="btn ghost sm" style="display:block;width:100%;text-align:left;margin-bottom:8px;"
    onclick="closeModal();openVendor('${v.id}')">${esc(v.name)} · <span style="color:var(--muted);">${esc(v.website||'')} · ${v.role}</span></button>`).join('')}
  <div style="display:flex;justify-content:flex-end;margin-top:6px;"><button class="btn ghost" onclick="closeModal()">Cancel</button></div>`);
}

async function openVendor(id){
  try{
    const d=await api('/vendors/'+id);
    VENDOR=d.vendor;MY_ROLE=d.role;PRODUCTS=(d.skills||[]).map(mapSkill);
    enterWorkspace();
    toast('Welcome back.');
  }catch(e){toast('Could not open that workspace: '+e.message);}
}

/* Reflects the session in the header and the workspace sidebar. */
function paintAccount(){
  const nav=document.getElementById('nav-auth');
  if(nav){
    nav.textContent=USER?'Log out':'Log in';
    /* Real hrefs, because these are navigations now: middle-click and "open in
     * new tab" behave, the target shows in the status bar, and signing out
     * still works if this script failed to load. The click handler exists only
     * to carry an unfinished scan across the sign-in page. */
    nav.setAttribute('href',USER?'/logout':'/login?next=%2Fconsole%2F');
    nav.setAttribute('onclick',USER?'':'stashPendingBuild()');
  }
  const acct=document.getElementById('nav-acct');
  if(acct)acct.style.display=USER?'':'none';
  const initials=n=>n.split(/[@.\s]/).filter(Boolean).slice(0,2).map(x=>x[0].toUpperCase()).join('');
  const av=document.getElementById('who-av'),nm=document.getElementById('who-name'),
        rl=document.getElementById('who-role');
  if(!av)return;
  av.textContent=USER?initials(USER.name||USER.email):'?';
  nm.textContent=USER?(USER.name||USER.email.split('@')[0]):'Not signed in';
  rl.textContent=USER?(MY_ROLE||'member'):'log in to edit';
}

/* ============ the account ============
 * The person, not the company. A workspace has a name, an industry and a team;
 * this page is the one place that is about whoever is signed in — and it is
 * where the change-password route finally has a caller.
 *
 * Everything renders from PROFILE, which is refetched after every write, so the
 * page never shows a value the server did not confirm. */
let PROFILE=null;

/* Stored timestamps are UTC and usually carry a +00:00 offset, but a plain
 * 'YYYY-MM-DD HH:MM:SS' would be read as local time — so mark that case, and
 * only that case. */
function acctDate(iso,fallback){
  if(!iso)return fallback||'—';
  const zoned=/([Zz]|[+-]\d{2}:?\d{2})$/.test(iso);
  const d=new Date(zoned?iso:iso.replace(' ','T')+'Z');
  if(isNaN(d))return fallback||'—';
  return d.toLocaleDateString(undefined,{year:'numeric',month:'short',day:'numeric'});
}

async function openAccount(){
  if(!USER){openLogin();return;}
  try{
    PROFILE=await api('/auth/profile');
    USER=PROFILE.user;MY_VENDORS=PROFILE.vendors||[];
  }catch(e){
    if(e.status===401){USER=null;paintAccount();openLogin();return;}
    toast('Could not load your account: '+e.message);return;
  }
  paintAccount();renderAccount();go('v-account');
}

/* Back goes where the user came from, which is a workspace if one is open and
 * the homepage otherwise — the account is reachable from both. */
function acctBack(){
  if(VENDOR){go('v-ws');}else{go('v-home');}
}

function renderAccount(){
  const host=document.getElementById('acct-body');
  if(!host||!PROFILE)return;
  const u=PROFILE.user||{};
  const initials=n=>(n||'').split(/[@.\s]/).filter(Boolean).slice(0,2).map(x=>x[0].toUpperCase()).join('');
  const vendors=PROFILE.vendors||[];
  const sessions=PROFILE.sessions||1;

  host.innerHTML=`
  <div class="acct-card">
    <div class="acct-id">
      <div class="acct-av">${esc(initials(u.name||u.email))}</div>
      <div>
        <b>${esc(u.name||u.email)}</b>
        <span>${esc(u.email)}${PROFILE.staff?' · <span class="pill navy">Lenni staff</span>':''}</span>
      </div>
    </div>
    <div class="acct-facts">
      <div><span>Member since</span><b>${esc(acctDate(u.created_at))}</b></div>
      <div><span>Last sign-in</span><b>${esc(acctDate(u.last_login_at,'this session'))}</b></div>
      <div><span>Workspaces</span><b>${vendors.length}</b></div>
      <div><span>Signed in on</span><b>${sessions} browser${sessions===1?'':'s'}</b></div>
    </div>
  </div>

  <div class="acct-card">
    <div class="acct-k">Your name</div>
    <p class="acct-note">What your team sees next to your invites and edits.</p>
    <div class="acct-row">
      <input id="acct-name" value="${esc(u.name||'')}" maxlength="120" placeholder="Your name">
      <button class="btn em sm" onclick="saveAcctName()">Save</button>
    </div>
    <div id="acct-name-err" class="acct-err"></div>
  </div>

  <div class="acct-card">
    <div class="acct-k">Email</div>
    <p class="acct-note">Fixed, and on purpose. Access to a workspace is granted to an
    address, not to an account — so changing this here would quietly drop you out of every
    workspace you have been invited to. To move addresses, have an admin invite the new one
    and remove the old.</p>
    <div class="acct-row">
      <input value="${esc(u.email)}" disabled>
      <button class="btn ghost sm" onclick="copyText('${esc(u.email)}','Copied ${esc(u.email)}')">Copy</button>
    </div>
  </div>

  <div class="acct-card">
    <div class="acct-k">Password</div>
    <p class="acct-note">Changing it signs you out everywhere else and keeps this tab signed in.</p>
    <button class="btn ghost sm" onclick="openPasswordChange()">Change password</button>
  </div>

  <div class="acct-card">
    <div class="acct-k">Signed-in browsers</div>
    <p class="acct-note">${sessions===1
      ?'This is the only browser signed in as you.'
      :`You are signed in on ${sessions} browsers. Ending the others leaves this one alone.`}</p>
    <button class="btn ghost sm" ${sessions<2?'disabled':''} onclick="revokeOtherSessions()">
      Sign out other browsers</button>
  </div>

  <div class="acct-card">
    <div class="acct-k">Your workspaces</div>
    ${vendors.length?vendors.map(v=>`<div class="acct-ws" onclick="openVendor('${esc(v.id)}')">
      <div><b>${esc(v.name)}</b><span>${esc(v.website||'')}</span></div>
      <div class="r"><span class="pill ${v.role==='admin'?'live':'draft'}">${esc(v.role)}</span> ›</div>
    </div>`).join('')
    :`<p class="acct-note">None yet. Paste a website on the homepage and the scan becomes your first one.</p>`}
  </div>`;
}

async function saveAcctName(){
  const el=document.getElementById('acct-name'),err=document.getElementById('acct-name-err');
  err.textContent='';
  try{
    PROFILE=await api('/auth/profile',Object.assign({method:'POST'},j({name:el.value})));
    USER=PROFILE.user;MY_VENDORS=PROFILE.vendors||[];
    renderAccount();paintAccount();
    toast('Saved.');
  }catch(e){err.textContent=e.message||'That did not save.';}
}

function openPasswordChange(){
  openModal(`<h3>Change your password</h3>
  <div class="sub">Every other browser signed in as you is signed out. This tab stays.</div>
  <input id="pw-cur" type="password" autocomplete="current-password" placeholder="Current password"
    style="width:100%;box-sizing:border-box;border:1px solid var(--line);border-radius:9px;padding:12px;font-size:14px;margin-bottom:8px;">
  <input id="pw-new" type="password" autocomplete="new-password" placeholder="New password — at least 10 characters"
    style="width:100%;box-sizing:border-box;border:1px solid var(--line);border-radius:9px;padding:12px;font-size:14px;">
  <div id="pw-err" style="font-size:12px;color:var(--red);margin-top:8px;min-height:16px;"></div>
  <div style="display:flex;gap:8px;justify-content:flex-end;margin-top:10px;">
    <button class="btn ghost" onclick="closeModal()">Cancel</button>
    <button class="btn em" onclick="submitPasswordChange()">Change password</button>
  </div>`);
  const cur=document.getElementById('pw-cur');
  if(cur)cur.focus();
  mbox.querySelectorAll('input').forEach(i=>i.addEventListener('keydown',e=>{
    if(e.key==='Enter')submitPasswordChange();
  }));
}

async function submitPasswordChange(){
  const err=document.getElementById('pw-err');
  err.textContent='';
  try{
    await api('/auth/password',Object.assign({method:'POST'},j({
      current_password:document.getElementById('pw-cur').value||'',
      new_password:document.getElementById('pw-new').value||'',
    })));
    closeModal();
    /* The server dropped the other sessions and reissued this one, so the count
     * on the page is stale until it is refetched. */
    await openAccount();
    toast('Password changed. Other browsers are signed out.');
  }catch(e){err.textContent=e.message||'That did not work.';}
}

async function revokeOtherSessions(){
  try{
    const d=await api('/auth/sessions/revoke-others',{method:'POST'});
    PROFILE=await api('/auth/profile');
    renderAccount();
    toast(d.ended?`Signed out ${d.ended} other browser${d.ended===1?'':'s'}.`:'Nothing else was signed in.');
  }catch(e){toast(e.message||'Could not do that.');}
}

/* ============ boot ============ */
/* ============ scan a website ============
 * The console's opening claim is "paste your URL and we read your website", and
 * until now the only site it had been run against was a bundled fixture. This
 * view points it at real businesses.
 *
 * Reads only. No run is recorded, no vendor is created, and nothing here
 * reaches the workspace on the left — which is why it can be aimed at a bank
 * that has never heard of us without leaving a half-built tenant behind. */
let SCAN_PICKS=null;

async function initScanner(){
  if(SCAN_PICKS)return;
  try{
    SCAN_PICKS=(await api('/prospect/shortlist')).banks||[];
  }catch(e){SCAN_PICKS=[];}
  const box=document.getElementById('scan-shortlist');
  if(!box)return;
  box.innerHTML=SCAN_PICKS.map((b,i)=>
    `<button class="scan-pick" onclick="scanThis(${i})">`+
    `<b>${esc(b.name)}</b><span>${esc(b.city)} · $${b.assets_musd}M assets</span></button>`
  ).join('');
}

/* By index rather than URL: the shortlist knows each bank's registered name,
 * which is better than anything we can pull out of a page title written for
 * search engines. A typed URL has no such luxury and falls back to guessing. */
function scanThis(i){
  const b=SCAN_PICKS[i];
  document.getElementById('scan-site-url').value=b.website;
  runScan(b.name);
}

async function runScan(knownName){
  const input=document.getElementById('scan-site-url');
  const out=document.getElementById('scan-out');
  const go=document.getElementById('scan-go');
  const url=(input.value||'').trim();
  if(!url){toast('Enter a website first.');return;}

  /* Six pages on someone else's server takes a few seconds, and a dead button
   * for that long reads as a hang. Say what is happening and roughly how long. */
  go.disabled=true;go.textContent='Reading…';
  out.innerHTML=`<div class="scan-result"><h3>Reading ${esc(url)}</h3>`+
    `<div class="scan-meta">Their homepage, then the pages their navigation points at. A few seconds.</div></div>`;
  try{
    /* `j` sets the body and nothing else, so the method has to come with it —
     * without it the browser refuses to send a GET carrying a body and the
     * request never leaves the page. */
    renderScan(await api('/prospect/preview',
      Object.assign({method:'POST'},j({url:url,name:knownName||''}))));
  }catch(e){
    /* Whose fault it was decides what the message should say, and getting that
     * wrong is expensive: this used to blame the bank's website for every
     * failure, so a missing `method:'POST'` on our side — which meant the
     * request never left the browser — was reported to the room as five banks
     * in a row blocking us. 502 is the only case where their site was actually
     * reached and refused. */
    const theirs=e.status===502;
    const body=theirs
      ? `Some sites block automated readers, and some are built entirely in JavaScript, `+
        `which leaves nothing in the HTML to read. Neither stops the bank using this — it `+
        `means their product page gets built from what they tell us rather than from what `+
        `we found.`
      : (e.status===401
          ? `Your session has expired. Sign in again and the scan will work.`
          : `This one is on us rather than on their website — the scan never reached them. `+
            `Worth reporting with the message above.`);
    out.innerHTML=`<div class="scan-result">`+
      `<h3>${theirs?'Could not read that site':'The scan did not run'}</h3>`+
      `<div class="scan-meta">${esc(e.detail||e.message||'')}</div>`+
      `<div class="scan-empty">${body}</div></div>`;
  }finally{
    go.disabled=false;go.textContent='Scan site';
  }
}

/* Their catalog first, our template library second.
 *
 * The order is the point. This panel used to lead with the products that
 * matched our registry, which meant a bank with forty offerings was shown eight
 * and the eight were the ones we already knew — it read as the limit of their
 * site rather than the limit of our library. The catalog is what we found on
 * their site; the templates are what we can build today. */
function renderScan(p){
  const offerings=p.offerings||[];

  /* Grouped by the bank's own nav sections, because that is the shape they
   * think in and it makes a missing branch of the catalog obvious. */
  const bySection=new Map();
  offerings.forEach(o=>{
    const k=o.section||'Top level';
    if(!bySection.has(k))bySection.set(k,[]);
    bySection.get(k).push(o);
  });

  const catalog=[...bySection.entries()].map(([section,rows])=>
    `<div class="scan-sect"><div class="scan-sect-h">${esc(section)}`+
    `<span>${rows.length}</span></div>`+
    rows.map(o=>{
      /* Low is not a match, it is a guess, and showing it as a template makes
       * the whole table untrustworthy. Say "template ready" only when we mean
       * it. */
      const solid=o.has_template&&(o.confidence==='High'||o.confidence==='Medium');
      const tag=solid
        ? `<span class="scan-tag ready" title="${esc(o.matched_phrase||'')}">`+
          `${esc(o.product_name)}</span>`
        : `<span class="scan-tag none">no template yet</span>`;
      const cat=o.is_category?`<span class="scan-tag cat">category</span>`:'';
      const quote=o.found_quote?`<div class="ev">“${esc(o.found_quote)}”</div>`:'';
      return `<div class="scan-item"><div class="scan-item-h">`+
        `<a href="${esc(o.url)}" target="_blank" rel="noopener">${esc(o.label)}</a>`+
        `${cat}${tag}</div>${quote}</div>`;
    }).join('')+`</div>`).join('');

  const ready=(p.products||[]).length;
  const warns=(p.notes||[]).map(n=>`<div class="scan-warn">${esc(n)}</div>`).join('');

  document.getElementById('scan-out').innerHTML=
    `<div class="scan-result">`+
      `<h3>${esc(p.name)}</h3>`+
      `<div class="scan-meta">${esc(p.industry_name)} · `+
        `<a href="${esc(p.url)}" target="_blank" rel="noopener">${esc(p.url)}</a></div>`+
      `<div class="scan-tally">`+
        `<div><b>${offerings.length}</b><span>offerings found on their site</span></div>`+
        `<div><b>${ready}</b><span>we hold a template for</span></div>`+
        `<div><b>${(p.pages||[]).length}</b><span>pages read</span></div>`+
      `</div>`+
      warns+
      (catalog||`<div class="scan-empty">We could not read a catalog from this site.</div>`)+
      `<div class="scan-pages"><b>Pages read:</b><br>${(p.pages||[]).map(esc).join('<br>')}</div>`+
    `</div>`;
}

/* ============ public library (no account, no scan) ============ */
const IND_ICONS={
  banking:'🏦',legal:'⚖️',insurance:'🛡️',real_estate:'🏠',cpa_tax:'📊',msp:'🖥️',
  title_escrow:'📜',property_mgmt:'🔑',wealth_ria:'📈',marketing:'📣',
  construction:'🏗️',mortgage_broker:'🏡',healthcare:'🏥',dental:'🦷',
  veterinary:'🐾',hr_peo:'👥',architecture_engineering:'📐',
};
let libPubTab='banking';
let TEMPLATES=[];
let CORE={};
let libPubOpen=null;

function shortIndName(n){
  return (n||'').split(' / ')[0];
}
function renderHomeIndustries(){
  const host=document.querySelector('#inds .indgrid');
  if(!host||!INDUSTRIES.length)return;
  const live=INDUSTRIES.filter(x=>x.templates);
  const soon=INDUSTRIES.filter(x=>!x.templates);
  const kicker=document.querySelector('#inds .shead .k');
  if(kicker)kicker.textContent=live.length+' industries in the library';
  host.innerHTML=live.map(x=>{
    const n=PRODUCT_REG.filter(p=>p.industry===x.key&&p.has_template).length;
    const core=(CORE[x.key]||[]).length;
    /* An industry with no catalogued services still has its intake written, and
     * that intake is what the library shows — so the card promises the intake
     * rather than a template count it cannot honour. */
    const label=n?`${n} template${n===1?'':'s'} · live`
                 :(core?`core intake · ${core} sections`:'ready to seed');
    return `<div class="indc${x.key==='banking'?' first':''}" onclick="openPublicLibrary('${x.key}')">`+
      `<div class="ic">${IND_ICONS[x.key]||'🏢'}</div>`+
      `<b>${esc(shortIndName(x.name))}</b>`+
      `<span>${label}</span></div>`;
  }).join('');
  const extra=document.getElementById('inds-more');
  if(extra){
    extra.textContent=soon.length
      ? '+ '+soon.map(x=>x.name).join(' · ')+' — cataloged, intake coming'
      : '';
  }
}
function renderSectionBlock(s){
  const asks=(s.asks||[]).map(a=>`<li>${esc(a)}</li>`).join('');
  const collects=(s.collects||[]).length
    ? `<div class="collects">Collects: ${esc(s.collects.join(', '))}</div>`:'';
  return `<div class="libsec"><b>${esc(s.name)}</b>`+
    (s.says?`<div class="says">${esc(s.says)}</div>`:'')+
    (asks?`<ul>${asks}</ul>`:'')+
    collects+`</div>`;
}
function renderPublicLibrary(){
  const tabs=document.getElementById('libpub-tabs');
  const body=document.getElementById('libpub-body');
  if(!tabs||!body)return;
  const live=INDUSTRIES.filter(x=>x.templates);
  const soon=INDUSTRIES.filter(x=>!x.templates);
  tabs.innerHTML=live.concat(soon).map(t=>
    `<button class="btn ${t.key===libPubTab?'em':'ghost'} xs" onclick="libPubTab='${t.key}';libPubOpen=null;renderPublicLibrary()">${esc(shortIndName(t.name))}${t.templates?'':' · soon'}</button>`
  ).join('');

  const ind=INDUSTRIES.find(x=>x.key===libPubTab)||{};
  const tpls=TEMPLATES.filter(t=>t.industry===libPubTab);
  const extras=LIB[libPubTab]||[];
  const core=CORE[libPubTab]||[];
  const tailored=tpls.filter(t=>t.tailored);
  const generic=tpls.filter(t=>!t.tailored);

  let html=`<p class="libpub-blurb">${esc(ind.blurb||'')}</p>`;
  if(!ind.templates){
    html+=`<div class="libpub-empty">Cataloged — we can classify a business into ${esc(ind.name||'this industry')}, but there is no intake to run yet.</div>`;
    body.innerHTML=html;return;
  }

  /* The core intake anchors every skill in the industry, so it is the honest
   * answer to "show me the templates" for an industry whose services are not
   * catalogued yet — and still worth reading for one whose services are. */
  if(core.length){
    const open=libPubOpen==='core';
    html+=`<div class="libpub-k">The ${esc(shortIndName(ind.name||''))} intake · every skill starts here</div>`;
    html+=`<div class="libcard${open?' open':''}">`+
      `<div class="libcard-h" onclick="libPubOpen=libPubOpen==='core'?null:'core';renderPublicLibrary()">`+
      `<div><b>Core intake</b><span class="meta">${core.length} section${core.length===1?'':'s'} · in every ${esc(shortIndName(ind.name||''))} skill</span></div>`+
      `<div class="chev">${open?'▾':'▸'}</div></div>`+
      (open?`<div>${core.map(renderSectionBlock).join('')}</div>`:'')+
      `</div>`;
  }

  html+=`<div class="libpub-k">Service templates · ${tpls.length}</div>`;
  if(!tpls.length){
    html+=`<div class="libpub-empty">Services for this industry are not catalogued yet, so there are no per-service templates to read. Every skill here runs the core intake above until tailored ones land.</div>`;
  }else{
    html+=tailored.concat(generic).map(t=>{
      const open=libPubOpen===t.id;
      const kind=t.tailored?'tailored':'generic intake';
      const n=t.sections.length;
      return `<div class="libcard${open?' open':''}">`+
        `<div class="libcard-h" onclick="libPubOpen=libPubOpen==='${t.id}'?null:'${t.id}';renderPublicLibrary()">`+
        `<div><b>${esc(t.name)}</b><span class="meta">${kind} · ${n} section${n===1?'':'s'}</span></div>`+
        `<div class="chev">${open?'▾':'▸'}</div></div>`+
        (open?`<div>${t.sections.map(renderSectionBlock).join('')}</div>`:'')+
        `</div>`;
    }).join('');
  }

  html+=`<div class="libpub-k">Add-on sections · drop into any skill</div>`;
  if(!extras.length){
    html+=`<div class="libpub-empty">No extra library sections authored for this industry yet. The templates above are the whole intake.</div>`;
  }else{
    html+=extras.map((s,i)=>{
      const id='lib-'+libPubTab+'-'+i;
      const open=libPubOpen===id;
      return `<div class="libcard${open?' open':''}">`+
        `<div class="libcard-h" onclick="libPubOpen=libPubOpen==='${id}'?null:'${id}';renderPublicLibrary()">`+
        `<div><b>${esc(s.name)}</b><span class="meta">${esc(s.summary||'')}</span></div>`+
        `<div class="chev">${open?'▾':'▸'}</div></div>`+
        (open?renderSectionBlock(s):'')+
        `</div>`;
    }).join('');
  }
  body.innerHTML=html;
}
async function openPublicLibrary(key){
  try{
    if(!INDUSTRIES.length){
      const [l,i,p]=await Promise.all([
        api('/registry/library'),api('/registry/industries'),api('/registry/products'),
      ]);
      LIB=l.library;INDUSTRIES=i.industries;PRODUCT_REG=p.products;
    }
    if(!TEMPLATES.length){
      const [t,c]=await Promise.all([api('/registry/templates'),api('/registry/core')]);
      TEMPLATES=t.templates||[];CORE=c.core||{};
    }
  }catch(e){
    toast('Could not load the library — is the server running?');
    return;
  }
  libPubTab=key||libPubTab||'banking';
  if(!INDUSTRIES.some(x=>x.key===libPubTab)){
    libPubTab=(INDUSTRIES.find(x=>x.templates)||INDUSTRIES[0]||{}).key||'banking';
  }
  libPubOpen=null;
  renderHomeIndustries();
  renderPublicLibrary();
  go('v-library');
}

(async function boot(){
  try{
    const [a,l,i,p,c,me]=await Promise.all([api('/registry/apps'),api('/registry/library'),
                                          api('/registry/industries'),api('/registry/products'),
                                          api('/registry/core'),api('/auth/me')]);
    APPS=a.apps;LIB=l.library;INDUSTRIES=i.industries;PRODUCT_REG=p.products;CORE=c.core||{};
    PROD_NAME=Object.fromEntries(PRODUCT_REG.map(x=>[x.key,x.name]));
    USER=me.user;MY_VENDORS=me.vendors||[];
    paintAccount();
    renderHomeIndustries();
    /* A scan interrupted by signing in takes priority over anything else we
     * might open: it is what they were in the middle of. */
    if(await resumePendingBuild())return;
    /* A returning user with exactly one workspace has no decision to make. */
    if(USER&&MY_VENDORS.length===1)openVendor(MY_VENDORS[0].id);
  }catch(e){toast('Could not reach the API — is the server running?');}
})();
