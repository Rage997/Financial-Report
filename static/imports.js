// Upload-first onboarding, cancellable jobs, and persistent import history.
// Untrusted filenames and model/error text are rendered only through textContent.
(function(){
'use strict';
const ACTIVE=['queued','normalizing','categorizing','waiting_ai','cancelling'];
const DEMO_FILE='examples/demo-transactions.csv';
const el={
  onboarding:$('onboarding'),fmtList:$('fmtList'),disclosure:$('aiDisclosure'),
  msg:$('importMsg'),
  demoRow:$('demoRow'),demoBtn:$('demoBtn'),jobs:$('importJobs'),jobList:$('jobList'),
  fileSelect:$('file'),importBtn:$('importBtn'),headerImport:$('headerImport'),
  pickFile:$('pickFile'),pickFileInput:$('pickFileInput'),continueBtn:$('continueBtn'),
  savedList:$('savedList'),closeFile:$('closeFile')
};
const jobs=new Map(),intents=new Map(),busy=new Set(),removed=new Set();
let workspace=null,pollTimer=null,pollInFlight=false,pollError=false;
let selectionVersion=0,messageJob=null,archiveOpen=false,showDismissed=false,refreshVersion=0,files=[];
el.msg.setAttribute('role','status');
function setMsg(text,kind){el.msg.textContent=text||'';el.msg.hidden=!text;el.msg.className='import-hint'+(kind?' '+kind:'')}
function node(tag,className,text){const n=document.createElement(tag);if(className)n.className=className;if(text!==undefined)n.textContent=text;return n}
function duration(seconds){const s=Math.max(0,Math.floor(seconds||0));return s<60?s+'s':Math.floor(s/60)+'m '+s%60+'s'}
function datasetMeta(file){const d=workspace&&workspace.datasets.find(d=>d.file===file);return d?{name:d.name,warnings:d.warnings,model:d.model}:null}
function renderSupported(){
  el.fmtList.replaceChildren(...workspace.supported_extensions.map(ext=>node('span','fmt',ext)));
  el.headerImport.accept=el.pickFileInput.accept=workspace.supported_extensions.join(',');
  const ai=workspace.ai||{};
  el.disclosure.textContent='Files stay on the app host. Recognized tables are parsed directly; unfamiliar layouts and text use local llama.cpp ('+(ai.model||'not configured')+', '+(ai.tier||'')+'). Existing rules take priority. AI fills remaining categories provisionally—review important transactions. Originals and normalized data are saved privately.';
}
function renderSaved(){
  const names=new Map((workspace.datasets||[]).map(d=>[d.file,d.name]));
  const items=files.filter(f=>f!==DEMO_FILE);
  el.savedList.replaceChildren();
  el.savedList.hidden=items.length===0;
  for(const f of items){
    const b=node('button','saved-item',names.get(f)||f.split('/').pop());
    b.type='button';b.dataset.file=f;
    el.savedList.append(b);
  }
  const active=workspace.active_file;
  if(active&&files.includes(active)){
    el.continueBtn.hidden=false;
    el.continueBtn.textContent='Continue with '+(names.get(active)||active.split('/').pop());
  }else el.continueBtn.hidden=true;
}
function jobStatusText(j){
  const phases={waiting:'Waiting for the local model',loading:'Processing input',generating:'Generating response · '+j.ai_chars+' characters received'};
  const parts=[j.stage||'',duration(j.elapsed_seconds)+' elapsed'];
  if(j.ai_phase)parts.push(phases[j.ai_phase]||j.ai_phase);
  if(j.total>0&&!['waiting_ai','cancelling'].includes(j.status))parts.push(j.completed+' of '+j.total+' completed');
  return parts.filter(Boolean).join(' · ');
}
function jobMessage(j){
  if(j.status==='ready')setMsg('Import ready: '+j.name+'. '+j.count+' transactions saved.','ok');
  else if(j.status==='failed')setMsg('Import failed: '+j.name+' — '+j.error+' Open import history to retry or delete it.','err');
  else if(j.status==='cancelled')setMsg('Import cancelled: '+j.name+'. Saved processing can be retried from import history.');
  else setMsg(j.name+' — '+jobStatusText(j));
}
function action(label,name,disabled){const b=node('button','',label);b.type='button';b.dataset.action=name;b.disabled=!!disabled;return b}
function renderJob(j){
  const box=node('div','job');box.dataset.id=j.id;
  const head=node('div','job-head');head.append(node('span','job-name',j.name),node('span','job-status '+j.status,j.status.replace('_',' ')));box.append(head);
  const active=ACTIVE.includes(j.status);
  box.append(node('p','job-stage',active?jobStatusText(j):(j.status==='ready'?j.count+' transactions saved · ':'')+duration(j.elapsed_seconds)+' elapsed'));
  if(active&&j.status!=='cancelling'){
    const bar=node('div','job-progress');bar.setAttribute('role','progressbar');bar.setAttribute('aria-label',jobStatusText(j));
    const fill=node('span');
    if(j.ai_phase||!j.total){bar.classList.add('indeterminate')}
    else{bar.setAttribute('aria-valuemin','0');bar.setAttribute('aria-valuemax',String(j.total));bar.setAttribute('aria-valuenow',String(j.completed));fill.style.width=Math.min(100,100*j.completed/j.total)+'%'}
    bar.append(fill);box.append(bar);
  }
  if(j.error)box.append(node('p','job-error',j.error));
  const actions=node('div','job-actions'),disabled=busy.has(j.id);
  if(active)actions.append(action(j.status==='cancelling'?'Cancelling…':'Cancel import','cancel',disabled||j.status==='cancelling'));
  else{
    if(['failed','cancelled'].includes(j.status))actions.append(action('Retry import','retry',disabled));
    if(j.status==='ready'){
      actions.append(action('Open dataset','open',disabled));
      const normalized=node('a','','Download normalized CSV');normalized.href='/api/imports/'+j.id+'/normalized';actions.append(normalized);
    }
    const source=node('a','','Download original');source.href='/api/imports/'+j.id+'/source';actions.append(source);
    actions.append(action(j.dismissed?'Restore to history':'Dismiss','dismiss',disabled),action('Delete saved files','delete',disabled));
  }
  box.append(actions);return box;
}
function renderJobs(){
  el.jobs.hidden=jobs.size===0;
  const active=[...jobs.values()].filter(j=>ACTIVE.includes(j.status));
  const finished=[...jobs.values()].filter(j=>!ACTIVE.includes(j.status));
  const children=active.map(renderJob);
  if(finished.length){
    const archive=node('details','import-history');archive.open=archiveOpen;
    archive.addEventListener('toggle',()=>{archiveOpen=archive.open});
    const visible=finished.filter(j=>!j.dismissed),failed=visible.filter(j=>j.status==='failed').length;
    archive.append(node('summary','','Import history ('+visible.length+(failed?' · '+failed+' failed':'')+')'));
    const dismissed=finished.filter(j=>j.dismissed).length;
    if(dismissed){
      const label=node('label','show-dismissed'),check=document.createElement('input');check.type='checkbox';check.checked=showDismissed;
      check.onchange=()=>{showDismissed=check.checked;renderJobs()};label.append(check,document.createTextNode('Show dismissed ('+dismissed+')'));archive.append(label);
    }
    const list=node('div','history-list');for(const j of finished)if(!j.dismissed||showDismissed)list.append(renderJob(j));
    archive.append(list);children.push(archive);
  }
  el.jobList.replaceChildren(...children);
}
function activeJobs(){return [...jobs.values()].filter(j=>ACTIVE.includes(j.status))}
function startPolling(){if(!pollTimer&&!pollInFlight&&activeJobs().length)pollTimer=setTimeout(tick,1000)}
async function refreshWorkspace(){
  const token=++refreshVersion;
  const [next,listing]=await Promise.all([api('/api/workspace'),api('/api/files')]);
  if(token!==refreshVersion)return;
  const selected=el.fileSelect.value;
  workspace=next;files=listing.files;
  const names=new Map(next.datasets.map(d=>[d.file,d.name]));
  el.fileSelect.replaceChildren(new Option('Choose a dataset',''),...listing.files.map(f=>new Option(names.get(f)||f.split('/').pop(),f)));
  if(listing.files.includes(selected))el.fileSelect.value=selected;
  jobs.clear();for(const j of next.imports)if(!removed.has(j.id))jobs.set(j.id,j);
  renderSupported();renderSaved();renderJobs();
}
async function openImport(j,intent=selectionVersion){
  await refreshWorkspace();
  if(intent!==selectionVersion||removed.has(j.id)||!j.file)return;
  try{showDashboard();await window.dashboard.setDataset(j.file,true)}
  catch(e){if(intent===selectionVersion){window.dashboard.clear();showOnboarding();setMsg(e.message,'err')}}
}
async function openDataset(file){
  if(!file)return;
  const intent=++selectionVersion;messageJob=null;setMsg('');
  try{
    showDashboard();
    await window.dashboard.setDataset(file,true);
  }catch(e){if(intent===selectionVersion){window.dashboard.clear();showOnboarding();setMsg(e.message,'err')}}
}
async function tick(){
  pollTimer=null;pollInFlight=true;
  try{
    const snapshot=refreshVersion;
    const settled=await Promise.allSettled(activeJobs().map(j=>api('/api/imports/'+j.id)));
    const failures=settled.filter(r=>r.status==='rejected');
    if(failures.length){setMsg('Import status unavailable: '+failures[0].reason.message+' — reconnecting…','err');pollError=true}
    else if(pollError){pollError=false;setMsg('')}
    let completed=false,open=null;
    // Drop stale poll results if a job mutation or refresh ran while in flight.
    if(refreshVersion===snapshot){
      for(const result of settled){
        if(result.status!=='fulfilled'||removed.has(result.value.id))continue;
        const j=result.value,previous=jobs.get(j.id);jobs.set(j.id,j);
        if(j.id===messageJob&&!failures.length)jobMessage(j);
        if(previous&&ACTIVE.includes(previous.status)&&!ACTIVE.includes(j.status)){
          completed=true;
          if(j.status==='ready'&&intents.get(j.id)===selectionVersion)open={job:j,intent:intents.get(j.id)};
          intents.delete(j.id);
        }
      }
    }
    if(completed)await refreshWorkspace();
    if(open)await openImport(open.job,open.intent);
    renderJobs();
  }catch(e){setMsg('Could not refresh imports: '+e.message,'err')}
  finally{pollInFlight=false;startPolling()}
}
async function upload(file){
  if(!file||el.importBtn.disabled)return;
  const intent=++selectionVersion;messageJob=null;
  el.pickFile.disabled=el.importBtn.disabled=true;setMsg('Uploading '+file.name+'…');
  const fd=new FormData();fd.append('file',file);
  try{
    const j=await api('/api/imports',{method:'POST',body:fd});jobs.set(j.id,j);++refreshVersion;intents.set(j.id,intent);
    if(intent===selectionVersion){messageJob=j.id;jobMessage(j)}
    renderJobs();
    if(j.status==='ready')await openImport(j,intent);
    startPolling();
  }catch(e){setMsg('Upload failed: '+e.message,'err')}
  finally{el.pickFile.disabled=el.importBtn.disabled=false}
}
el.importBtn.onclick=()=>el.headerImport.click();
el.headerImport.onchange=e=>{const file=e.target.files[0];e.target.value='';if(file)upload(file)};
el.pickFile.onclick=()=>el.pickFileInput.click();
el.pickFileInput.onchange=e=>{const file=e.target.files[0];e.target.value='';if(file)upload(file)};
el.continueBtn.onclick=()=>{if(workspace.active_file)openDataset(workspace.active_file)};
el.savedList.addEventListener('click',event=>{const b=event.target.closest('button[data-file]');if(b)openDataset(b.dataset.file)});
el.closeFile.onclick=()=>{++selectionVersion;messageJob=null;setMsg('');window.dashboard.clear();showOnboarding()};
el.demoBtn.onclick=()=>openDataset(DEMO_FILE);
el.fileSelect.onchange=()=>{
  if(!el.fileSelect.value){++selectionVersion;messageJob=null;setMsg('');window.dashboard.clear();showOnboarding();return}
  openDataset(el.fileSelect.value);
};
el.jobList.addEventListener('click',async event=>{
  const button=event.target.closest('button[data-action]');if(!button)return;
  const id=button.closest('.job').dataset.id,j=jobs.get(id),operation=button.dataset.action;
  if(!j||busy.has(id))return;
  if(operation==='delete'&&!window.confirm('Delete '+j.name+'? This permanently removes its original file, normalized data and saved import. Reviewed rules are kept.'))return;
  busy.add(id);renderJobs();
  try{
    if(operation==='open'){
      const intent=++selectionVersion;messageJob=null;setMsg('');await openImport(j,intent);
    }else if(operation==='delete'){
      await api('/api/imports/'+id,{method:'DELETE',headers:{'Content-Type':'application/json'},body:'{}'});
      removed.add(id);jobs.delete(id);++refreshVersion;intents.delete(id);
      if(messageJob===id)messageJob=null;
      if(el.fileSelect.value===j.file){++selectionVersion;window.dashboard.clear();showOnboarding()}
      await refreshWorkspace();setMsg('Deleted saved files for '+j.name+'.');
    }else{
      const body=operation==='dismiss'?{dismissed:!j.dismissed}:{};
      const intent=operation==='retry'?++selectionVersion:selectionVersion;
      const result=await api('/api/imports/'+id+'/'+operation,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
      jobs.set(id,result);++refreshVersion;
      if(operation==='retry'){intents.set(id,intent);messageJob=id;jobMessage(result)}
      else if(operation==='cancel'){messageJob=id;jobMessage(result)}
      else{if(messageJob===id)messageJob=null;setMsg(result.dismissed?'Import dismissed; saved files are unchanged.':'Import restored to history.')}
      startPolling();
    }
  }catch(e){setMsg('Could not '+operation+' import: '+e.message,'err')}
  finally{busy.delete(id);renderJobs()}
});
function showDashboard(){el.onboarding.hidden=true;el.closeFile.hidden=false;window.dashboard.show()}
function showOnboarding(){el.onboarding.hidden=false;el.closeFile.hidden=true;window.dashboard.hide()}
async function boot(){
  try{
    await refreshWorkspace();
    el.demoRow.hidden=![...el.fileSelect.options].some(o=>o.value===DEMO_FILE);
    // Always start on the welcome screen: a saved selection or a previously running
    // import never opens by itself on page load.
    if(selectionVersion===0)showOnboarding();
  }catch(e){if(selectionVersion===0){showOnboarding();setMsg('Could not restore workspace: '+e.message,'err')}}
  finally{startPolling()}
}
window.imports={datasetMeta};
window.dashboard.onDatasetChanged(file=>{
  if(file&&workspace){workspace.active_file=file;renderSaved()}
});
boot();
})();
