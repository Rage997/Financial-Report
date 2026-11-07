/* Saved light/dark preference, with live OS defaults until the user chooses. */
(function(){
'use strict';
const STORAGE_KEY='finreport.theme';
const root=document.documentElement;
let saved=null;      // persisted explicit choice: 'light' | 'dark' | null
let current='light'; // effective theme
let initialized=false;

function osPrefersDark(){
  try{return window.matchMedia('(prefers-color-scheme: dark)').matches}catch(e){return false}
}
function readSaved(){
  try{
    const v=window.localStorage.getItem(STORAGE_KEY);
    return v==='dark'||v==='light'?v:null;
  }catch(e){return null} // storage denied: theme still works for this session
}
function persist(value){
  try{window.localStorage.setItem(STORAGE_KEY,value)}catch(e){/* session-only theme */}
}
function palette(theme){
  if(theme==='dark')return{
    theme:'dark',paper:'#16202f',ink:'#e8eef6',muted:'#9aa9bd',grid:'#2b3a50',axis:'#9aa9bd',
    categories:{income:'#3ecfa5',expense:'#ef7c8c',transfer:'#6ea0ff',investment:'#b18cf0',uncategorized:'#93a3b8'}
  };
  return{
    theme:'light',paper:'#ffffff',ink:'#172b42',muted:'#64748b',grid:'#edf1f5',axis:'#64748b',
    categories:{income:'#16816a',expense:'#cd5060',transfer:'#3975d5',investment:'#8960c2',uncategorized:'#94a3b8'}
  };
}
function setThemeAttribute(theme){
  if(theme==='dark')root.setAttribute('data-theme','dark');
  else root.removeAttribute('data-theme');
}
function updateToggle(){
  const btn=document.getElementById('themeToggle');
  if(!btn)return;
  btn.setAttribute('aria-pressed',String(current==='dark'));
  // Label text-only buttons with the mode the click will switch to;
  // icon buttons (child elements) are left to the parent.
  if(!btn.childElementCount)btn.textContent=current==='dark'?'Light mode':'Dark mode';
}
function apply(theme){
  current=theme;
  setThemeAttribute(theme);
  updateToggle();
  document.dispatchEvent(new CustomEvent('finreport-theme',{detail:{theme:current,palette:palette(current)}}));
}
function init(){
  if(initialized)return;
  initialized=true;
  saved=readSaved();
  apply(saved||(osPrefersDark()?'dark':'light'));
  let mq=null;
  try{mq=window.matchMedia('(prefers-color-scheme: dark)')}catch(e){}
  if(mq){
    const onOsChange=e=>{if(saved===null)apply(e.matches?'dark':'light')};
    if(typeof mq.addEventListener==='function')mq.addEventListener('change',onOsChange);
    else if(typeof mq.addListener==='function')mq.addListener(onOsChange);
  }
}
window.dashboardAppearance={
  init,
  toggle(){
    const next=current==='dark'?'light':'dark';
    saved=next;
    persist(next);
    apply(next);
  },
  palette(theme){
    if(theme===undefined||theme===null)theme=current;
    return palette(theme==='dark'?'dark':'light');
  },
};
})();
