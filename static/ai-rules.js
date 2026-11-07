// static/ai-rules.js — Local AI rule suggestion panel.
// Depends on dashboard globals defined in index.html: $, version, load, window.dashboard.
// All counterparties/labels/reasons are rendered via textContent/createElement — no untrusted HTML.
(function(){
'use strict';
const MAX_BATCH=12, MAX_LIST=400;
const CATEGORIES=['income','expense','transfer','investment','uncategorized'];
let statusInfo=null, candidates=[], revision=null, busy=false, panelOpen=false;

const el={
  toggle:$('aiToggle'), panel:$('aiPanel'), close:$('aiClose'),
  dot:$('aiDot'), statusLine:$('aiStatusLine'), msg:$('aiMsg'),
  filter:$('aiCpFilter'), list:$('aiCpList'), count:$('aiCpCount'),
  suggest:$('aiSuggest'), reviewWrap:$('aiReviewWrap'), reviewBody:$('aiReviewBody'),
  save:$('aiSave'), discard:$('aiDiscard'), saveMsg:$('aiSaveMsg')
};

// Dataset identity: file + dashboard load version. Suggestions/saves captured against a
// token are discarded if the dataset changes while a request is in flight.
function dsToken(){return $('file').value+'#'+version}
function setMsg(node,text,kind){node.textContent=text||'';node.className=kind==='err'?'ai-msg err':kind==='ok'?'ai-msg ok':'ai-msg'}

async function aiApi(url,options){
  const r=await fetch(url,options);
  if(!r.ok){let d={};try{d=await r.json()}catch(_){}
    const err=new Error(d.detail||('Request failed ('+r.status+')'));err.status=r.status;throw err}
  return r.json();
}

async function refreshStatus(){
  el.statusLine.textContent='Checking local model…';
  try{
    const s=await aiApi('/api/ai/status');
    statusInfo=s;
    el.dot.className='ai-dot '+(s.enabled&&s.available?'ok':'bad');
    if(!s.enabled){el.statusLine.textContent='Local AI is disabled (FINREPORT_AI_URL not set). The dashboard works normally without it.';el.suggest.disabled=true;}
    else if(!s.available){el.statusLine.textContent='Local model unavailable — '+(s.message||'check the llama container in Docker Compose')+'. The dashboard works normally without it.';el.suggest.disabled=true;}
    else{el.statusLine.textContent='Local llama.cpp model ready: '+s.model+' · suggestions require your review.';}
  }catch(e){
    statusInfo=null;
    el.dot.className='ai-dot bad';
    el.statusLine.textContent='Could not check local model: '+e.message+' · the dashboard is unaffected.';
    el.suggest.disabled=true;
  }
  updateSuggestEnabled();
}

async function refreshCandidates(){
  const file=$('file').value;
  if(!file){candidates=[];revision=null;clearReview();renderList();return}
  const before=dsToken();
  try{
    const d=await aiApi('/api/ai/candidates?file='+encodeURIComponent(file));
    if(dsToken()!==before)return; // dataset changed while in flight
    candidates=d.candidates;revision=null;clearReview();
    renderList();
  }catch(e){
    if(dsToken()!==before)return;
    candidates=[];revision=null;clearReview();renderList();
    if(panelOpen)setMsg(el.msg,'Could not load eligible counterparties: '+e.message,'err');
  }
}

function renderList(){
  const q=el.filter.value.trim().toLowerCase();
  const shown=candidates.filter(c=>!q||c.counterparty.toLowerCase().includes(q));
  el.list.replaceChildren();
  const frag=document.createDocumentFragment();
  const cap=Math.min(shown.length,MAX_LIST);
  for(let i=0;i<cap;i++){
    const c=shown[i];
    const label=document.createElement('label');
    const cb=document.createElement('input');cb.type='checkbox';cb.dataset.cp=c.counterparty;
    const name=document.createElement('span');name.className='ai-cp-name';name.textContent=c.counterparty;
    const meta=document.createElement('span');meta.className='ai-cp-meta';
    meta.textContent=c.count+' × '+(c.current_category||'uncategorized');
    meta.title='directions: '+(c.directions||[]).join(', ')+' · payment types: '+(c.payment_types||[]).join(', ');
    label.append(cb,name,meta);
    frag.append(label);
  }
  el.list.append(frag);
  if(shown.length>MAX_LIST){
    const note=document.createElement('p');note.className='ai-msg';
    note.textContent='Showing '+MAX_LIST+' of '+shown.length+' — type above to narrow the list.';
    el.list.append(note);
  }else if(!shown.length){
    const note=document.createElement('p');note.className='ai-msg';
    note.textContent=!$('file').value?'Import or choose a dataset first.':candidates.length?'No counterparties match this filter.':'No eligible counterparties in this dataset (every counterparty already has an explicit rule).';
    el.list.append(note);
  }
  updateSelection();
}

function selectedCps(){return [...el.list.querySelectorAll('input[type=checkbox]:checked')].map(cb=>cb.dataset.cp)}
function updateSelection(){
  el.count.textContent=candidates.length?selectedCps().length+' of '+MAX_BATCH+' selected · '+candidates.length+' eligible':'';
  updateSuggestEnabled();
}
function updateSuggestEnabled(){
  el.suggest.disabled=busy||!statusInfo||!statusInfo.enabled||!statusInfo.available||selectedCps().length===0;
}

el.list.addEventListener('change',e=>{
  const cb=e.target;
  if(cb.type!=='checkbox')return;
  if(cb.checked&&selectedCps().length>MAX_BATCH){cb.checked=false;setMsg(el.msg,'Maximum '+MAX_BATCH+' counterparties per batch — save this batch, then repeat for more.','err')}
  updateSelection();
});
el.filter.oninput=renderList;

el.suggest.onclick=async()=>{
  const cps=selectedCps();
  if(!cps.length||busy)return;
  busy=true;updateSuggestEnabled();el.save.disabled=true;
  setMsg(el.msg,'Asking the local model… this can take a few minutes on CPU. Nothing is saved yet.','');
  const before=dsToken();
  try{
    const d=await aiApi('/api/ai/suggest',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({file:$('file').value,counterparties:cps})});
    if(dsToken()!==before){setMsg(el.msg,'Dataset changed while the model was working — suggestions discarded. Please re-request.','err');return}
    revision=d.revision;
    renderReview(d.suggestions);
    const unsure=d.suggestions.filter(s=>s.category==='uncategorized').length;
    setMsg(el.msg,d.suggestions.length+' suggestion'+(d.suggestions.length===1?'':'s')+' ready for review'+(unsure?' · '+unsure+' marked uncategorized — choose a category manually; none are auto-approved.':'')+'. Tick the rows you approve, then save.','ok');
  }catch(e){
    revision=null;
    setMsg(el.msg,'Suggestion failed: '+e.message,'err');
  }finally{busy=false;updateSuggestEnabled()}
};

function renderReview(suggestions){
  el.reviewBody.replaceChildren();
  for(const s of suggestions){
    const tr=document.createElement('tr');
    const tdApprove=document.createElement('td');
    const cb=document.createElement('input');cb.type='checkbox';cb.className='approve';cb.checked=false;
    cb.setAttribute('aria-label','Approve rule for '+s.counterparty);
    tdApprove.append(cb);
    const tdName=document.createElement('td');tdName.className='name';tdName.textContent=s.counterparty;
    const tdCount=document.createElement('td');
    const cnt=document.createElement('span');cnt.textContent=s.count+' × ';
    const badge=document.createElement('span');badge.className='badge';badge.textContent=s.current_category||'uncategorized';
    tdCount.append(cnt,badge);
    const tdPattern=document.createElement('td');tdPattern.className='pattern';
    const pat=document.createElement('input');pat.type='text';pat.value=s.pattern;pat.readOnly=true;
    pat.setAttribute('aria-label','Fixed matching pattern for '+s.counterparty);
    tdPattern.append(pat);
    const tdCat=document.createElement('td');
    const sel=document.createElement('select');
    for(const c of CATEGORIES){const o=document.createElement('option');o.value=c;o.textContent=c;if(c===s.category)o.selected=true;sel.append(o)}
    sel.setAttribute('aria-label','Proposed category for '+s.counterparty);
    tdCat.append(sel);
    const tdLabel=document.createElement('td');
    const inp=document.createElement('input');inp.type='text';inp.maxLength=80;inp.value=s.label||'';
    inp.setAttribute('aria-label','Proposed label for '+s.counterparty);
    tdLabel.append(inp);
    const tdReason=document.createElement('td');tdReason.className='reason';tdReason.textContent=s.reason||'';
    tr.append(tdApprove,tdName,tdCount,tdPattern,tdCat,tdLabel,tdReason);
    el.reviewBody.append(tr);
  }
  el.reviewWrap.hidden=false;
  el.discard.hidden=false;
  updateSaveEnabled();
}

function approvedRules(){
  const out=[];
  for(const tr of el.reviewBody.querySelectorAll('tr')){
    const cb=tr.querySelector('.approve');
    if(!cb.checked)continue;
    out.push({
      counterparty:tr.children[1].textContent,
      category:tr.querySelector('select').value,
      label:tr.querySelector('input[type=text]:not([readonly])').value.trim()
    });
  }
  return out;
}
function updateSaveEnabled(){
  el.save.disabled=busy||!revision||!approvedRules().length;
}
el.reviewBody.addEventListener('input',updateSaveEnabled);
el.reviewBody.addEventListener('change',updateSaveEnabled);

el.save.onclick=async()=>{
  const rules=approvedRules();
  if(!rules.length||!revision||busy)return;
  if(rules.some(r=>r.category==='uncategorized')){setMsg(el.saveMsg,'Rows marked uncategorized cannot be saved — choose a category first.','err');return}
  busy=true;updateSaveEnabled();updateSuggestEnabled();el.discard.disabled=true;
  setMsg(el.saveMsg,'Saving selected rules…','');
  try{
    const d=await aiApi('/api/ai/apply',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({file:$('file').value,revision,rules})});
    setMsg(el.saveMsg,'Saved '+d.saved+' rule'+(d.saved===1?'':'s')+' to '+d.path+'. Dashboard reloaded.','ok');
    clearReview();
    await load();
    await refreshCandidates();
  }catch(e){
    if(e.status===409){
      revision=null;clearReview();
      setMsg(el.saveMsg,e.message+' — suggestions invalidated; please re-request.','err');
      refreshCandidates();
    }else{
      setMsg(el.saveMsg,'Save could not be confirmed: '+e.message+' · refresh before retrying if the connection was interrupted.','err');
    }
  }finally{busy=false;el.discard.disabled=false;updateSaveEnabled();updateSuggestEnabled()}
};

function clearReview(){revision=null;el.reviewBody.replaceChildren();el.reviewWrap.hidden=true;el.discard.hidden=true;updateSaveEnabled()}
el.discard.onclick=()=>{
  clearReview();
  setMsg(el.saveMsg,'');
  setMsg(el.msg,'Suggestions discarded — nothing was saved. Select counterparties and suggest again.','');
};

// Invalidate any pending review when the dataset switches (chooser, import ready, or initial load).
function aiDatasetChanged(file){
  if(file&&file!==window.dashboard.currentFile())return;
  const hadReview=!!revision||el.reviewBody.children.length>0;
  clearReview();
  if(hadReview)setMsg(el.msg,'Dataset changed — previous suggestions were discarded. Select counterparties and suggest again.','');
  if(panelOpen)refreshCandidates();
}
window.dashboard.onDatasetChanged(aiDatasetChanged);

function setPanel(open){
  panelOpen=open;
  el.panel.hidden=!open;
  el.toggle.setAttribute('aria-expanded',String(open));
  el.toggle.textContent=open?'Hide Local AI rules':'Local AI rules';
  if(open){refreshStatus();refreshCandidates()}
}
el.toggle.onclick=()=>setPanel(el.panel.hidden);
el.close.onclick=()=>setPanel(false);
})();
