/* Calendar totals use integer minor units and already-filtered, single-currency rows. */
(function(){
'use strict';
function validMonth(month){
  return typeof month==='string'&&/^\d{4}-(0[1-9]|1[0-2])$/.test(month)&&month>='0001-01'&&month<='9999-12';
}
function monthDate(month){
  const [year,number]=month.split('-').map(Number),date=new Date(0);
  date.setUTCFullYear(year,number-1,1);return date;
}
function shiftMonth({month,step}){
  if(!validMonth(month))return null;
  const [year,number]=month.split('-').map(Number),index=year*12+number-1+step;
  if(index<12||index>9999*12+11)return null;
  return `${String(Math.floor(index/12)).padStart(4,'0')}-${String(index%12+1).padStart(2,'0')}`;
}
function monthGrid({month,transactions}){
  if(!validMonth(month))return null;
  const start=monthDate(month),end=new Date(start),totals=new Map();
  end.setUTCMonth(end.getUTCMonth()+1,0);
  for(const t of transactions){
    if(t.date.slice(0,7)!==month)continue;
    if(!totals.has(t.date))totals.set(t.date,{count:0,incoming:0,outgoing:0});
    const day=totals.get(t.date);day.count++;day[t.direction==='in'?'incoming':'outgoing']+=t.minor;
  }
  const days=Array.from({length:end.getUTCDate()},(_,i)=>{
    const date=`${month}-${String(i+1).padStart(2,'0')}`;
    const total=totals.get(date)||{count:0,incoming:0,outgoing:0};
    return {date,...total,net:total.incoming-total.outgoing};
  });
  return {offset:(start.getUTCDay()+6)%7,days};
}
const monthFormat=new Intl.DateTimeFormat('en',{month:'long',year:'numeric',timeZone:'UTC'});
let state,month='',sourceKey='',dateKey='',formatMoney,onSelect;
const element=id=>document.getElementById(id);
function preferredMonth(){
  for(const value of [state.from,state.to])if(validMonth(value.slice(0,7)))return value.slice(0,7);
  if(validMonth(state.year+'-01'))return state.year+'-01';
  return state.coverage.at(-1).date.slice(0,7);
}
function description(day){
  const amount=value=>`${formatMoney(value,state.currency)} ${state.currency}`;
  return `${day.date}: ${day.count} ${day.count===1?'transaction':'transactions'}; incoming ${amount(day.incoming)}, outgoing ${amount(day.outgoing)}, net ${amount(day.net)}`;
}
function render(){
  const grid=element('calendarDays');grid.replaceChildren();
  element('calendarMonth').value=month;
  element('calendarMonth').disabled=!month;
  element('calendarPrev').disabled=!shiftMonth({month,step:-1});
  element('calendarNext').disabled=!shiftMonth({month,step:1});
  element('calendarTitle').textContent='Calendar'+(month?' · '+monthFormat.format(monthDate(month)):'');
  element('calendarSelection').textContent='';
  element('calendarWeekdays').hidden=!month;
  if(!month){element('calendarSummary').textContent='No recorded activity for this source/currency selection.';return}
  const {offset,days}=monthGrid({month,transactions:state.transactions});
  const selected=state.from&&state.from===state.to&&(!state.year||state.from.startsWith(state.year))?state.from:'';
  const count=days.reduce((sum,day)=>sum+day.count,0);
  element('calendarSummary').textContent=`${count} matching ${count===1?'transaction':'transactions'} in ${state.currency}. Amounts: + incoming, − outgoing, net. Select a day for full totals.`;
  const slots=Math.ceil((offset+days.length)/7)*7;
  for(let i=0;i<slots;i++){
    const day=days[i-offset];
    if(!day){const blank=document.createElement('span');blank.className='calendar-blank';blank.setAttribute('aria-hidden','true');grid.append(blank);continue}
    const button=document.createElement('button');button.type='button';button.className='calendar-day';button.dataset.date=day.date;
    button.setAttribute('aria-pressed',String(day.date===selected));button.setAttribute('aria-label',description(day));button.title=description(day);
    const number=document.createElement('strong');number.textContent=String(i-offset+1);button.append(number);
    const count=document.createElement('span');count.className='calendar-count';count.textContent=`${day.count} tx`;button.append(count);
    if(day.count){
      const flows=document.createElement('span');flows.className='calendar-flows';
      for(const [value,prefix,className] of [[day.incoming,'+','pos'],[day.outgoing,'−','neg'],[day.net,'Net ','']]){
        const line=document.createElement('span');line.className=className;line.textContent=prefix+formatMoney(value,state.currency);flows.append(line);
      }
      button.append(flows);
    }
    grid.append(button);
  }
  const chosen=days.find(day=>day.date===selected);
  element('calendarSelection').textContent=chosen?'Selected day — '+description(chosen):selected?`Filtered day: ${selected} (outside this month).`:'Choose a day to filter transactions and inspect its totals.';
}
window.calendarView={
  monthGrid,shiftMonth,
  init(options){
    ({formatMoney,onSelect}=options);
    for(const [id,step] of [['calendarPrev',-1],['calendarNext',1]])element(id).onclick=()=>{
      const next=shiftMonth({month,step});if(next){month=next;render()}
    };
    element('calendarMonth').onchange=()=>{const value=element('calendarMonth').value;if(validMonth(value))month=value;render()};
    element('calendarDays').onclick=event=>{
      const button=event.target.closest('button[data-date]');if(!button)return;
      const date=button.dataset.date;onSelect(date);
      element('calendarDays').querySelector(`[data-date="${date}"]`)?.focus({preventScroll:true});
    };
  },
  update(next){
    state=next;
    const nextDateKey=JSON.stringify([state.from,state.to,state.year]);
    if(!state.coverage.length)month='';
    else if(!month||state.sourceKey!==sourceKey||nextDateKey!==dateKey)month=preferredMonth();
    sourceKey=state.sourceKey;dateKey=nextDateKey;render();
  }
};
})();
