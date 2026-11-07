// static/comparisons.js — Pure period-comparison calculations for the dashboard.
// No DOM, no dependencies; all monetary math uses integer minor units.
//
// API (window.periodComparisons):
//
// calculate({history, coverage, from, to, year})
//   history  – transactions matching the active non-date filters (currency/
//              source/category/direction/search/exact/anomalies). May include
//              dates outside the selected period; this function restricts.
//   coverage – all transactions in the active currency/source, before those
//              filters. Used only to derive period bounds and to detect
//              whether the previous period has any statement activity.
//   from/to  – inclusive 'YYYY-MM-DD' bounds ('' when unset), intersected
//              with the optional 'YYYY' year filter. A year-only filter means
//              the full calendar year (Jan 1 .. Dec 31); otherwise absent
//              bounds fall back to the first/last coverage dates.
//   Returns null when the selected scope is empty or inverted, otherwise:
//     {
//       kind: 'month' | 'year' | 'days',
//       start, end,                   // selected period, inclusive ISO dates
//       previousStart, previousEnd,   // comparison period ISO dates, or null
//                                     // pair when it falls outside the
//                                     // supported calendar (years 1-9999)
//       income:  {current, previous, delta, percent},
//       expense: {current, previous, delta, percent}
//     }
//   Previous period: exactly one full calendar month -> previous calendar
//   month; exactly one full calendar year -> previous calendar year;
//   otherwise the immediately preceding equal number of calendar days.
//   current/previous/delta are integer minor units: income is signed by
//   direction, expense uses the opposite sign (refund-aware). When coverage
//   has no records in the previous period, previous/delta/percent are null
//   (not 0); percent is also null when the previous baseline is zero.
//   percent is delta / abs(previous) × 100, for display only.
//
// monthlyOverlay({history, coverage, months})
//   months – sorted 'YYYY-MM' keys of the current monthly chart.
//   Returns {income: [...], expense: [...]} with one entry per key, aligned
//   to the same month one year earlier: integer minor total, or null when
//   that prior month has no coverage records (zero where the filtered
//   history simply has no records of that category). Never extends the
//   current time axis; history/coverage must already be scoped to one
//   currency/source.
(function(){
'use strict';
function parseISO(s){return{y:Number(s.slice(0,4)),m:Number(s.slice(5,7)),d:Number(s.slice(8,10))}}
function iso(y,m,d){return String(y).padStart(4,'0')+'-'+String(m).padStart(2,'0')+'-'+String(d).padStart(2,'0')}
// UTC Date with setFullYear — the constructor mis-maps two-digit years,
// setFullYear does not. Supported years are 1-9999 (financial dates).
function utcDate(y,m,d){const t=new Date(0);t.setUTCFullYear(y,m-1,d);return t}
function daysInMonth(y,m){return utcDate(y,m+1,0).getUTCDate()}
// Shift an ISO date by n days (UTC, calendar-safe); null when the result
// leaves the supported calendar (years 1-9999) instead of throwing.
function shiftDays(s,n){
  const p=parseISO(s),t=utcDate(p.y,p.m,p.d);
  t.setUTCDate(t.getUTCDate()+n);
  if(t.getUTCFullYear()<1||t.getUTCFullYear()>9999)return null;
  return iso(t.getUTCFullYear(),t.getUTCMonth()+1,t.getUTCDate());
}
function diffDays(a,b){const pa=parseISO(a),pb=parseISO(b);return Math.round((utcDate(pb.y,pb.m,pb.d)-utcDate(pa.y,pa.m,pa.d))/86400000)}

// Shape of an inclusive selected period.
function periodKind(start,end){
  const s=parseISO(start),e=parseISO(end);
  if(s.y===e.y&&s.m===e.m&&s.d===1&&e.d===daysInMonth(s.y,s.m))return'month';
  if(s.y===e.y&&s.m===1&&s.d===1&&e.m===12&&e.d===31)return'year';
  return'days';
}
// Bounds of the comparison period, or null when it leaves years 1-9999.
function previousBounds(start,end){
  const s=parseISO(start),e=parseISO(end);
  if(s.y===e.y&&s.m===e.m&&s.d===1&&e.d===daysInMonth(s.y,s.m)){
    const py=s.m===1?s.y-1:s.y,pm=s.m===1?12:s.m-1;
    if(py<1)return null;
    return{start:iso(py,pm,1),end:iso(py,pm,daysInMonth(py,pm))};
  }
  if(s.y===e.y&&s.m===1&&s.d===1&&e.m===12&&e.d===31){
    const py=s.y-1;
    if(py<1)return null;
    return{start:iso(py,1,1),end:iso(py,12,31)};
  }
  const len=diffDays(start,end)+1;
  const prevEnd=shiftDays(start,-1);
  if(!prevEnd)return null;
  const prevStart=shiftDays(prevEnd,-(len-1));
  if(!prevStart)return null;
  return{start:prevStart,end:prevEnd};
}

// Integer minor totals of one category set over an inclusive date range.
// Income is signed by direction; expense uses the opposite sign, so an
// incoming expense record (refund) reduces the expense total.
function totals(list,from,to){
  let income=0,expense=0;
  for(const t of list){
    if(t.date<from||t.date>to)continue;
    const s=t.direction==='in'?t.minor:-t.minor;
    if(t.category==='income')income+=s;
    else if(t.category==='expense')expense-=s;
  }
  return{income,expense};
}
function stat(current,previous){
  if(previous===null)return{current,previous:null,delta:null,percent:null};
  const delta=current-previous;
  return{current,previous,delta,percent:previous===0?null:(delta/Math.abs(previous))*100};
}

function calculate({history,coverage,from,to,year}){
  const cov=Array.isArray(coverage)?coverage:[];
  const hist=Array.isArray(history)?history:[];
  if(!cov.length)return null;
  let first=null,last=null;
  for(const t of cov){if(first===null||t.date<first)first=t.date;if(last===null||t.date>last)last=t.date;}
  const y=year?String(year):'';
  const yStart=y?y+'-01-01':null,yEnd=y?y+'-12-31':null;
  let start=from||(yStart||first),end=to||(yEnd||last);
  if(yStart&&start<yStart)start=yStart;
  if(yEnd&&end>yEnd)end=yEnd;
  if(start>end)return null;
  const prev=previousBounds(start,end);
  let prevTotals=null;
  if(prev){
    let hasCoverage=false;
    for(const t of cov){if(t.date>=prev.start&&t.date<=prev.end){hasCoverage=true;break}}
    if(hasCoverage)prevTotals=totals(hist,prev.start,prev.end);
  }
  const cur=totals(hist,start,end);
  return{
    kind:periodKind(start,end),
    start,end,
    previousStart:prev?prev.start:null,
    previousEnd:prev?prev.end:null,
    income:stat(cur.income,prevTotals?prevTotals.income:null),
    expense:stat(cur.expense,prevTotals?prevTotals.expense:null)
  };
}

function monthlyOverlay({history,coverage,months}){
  const cov=Array.isArray(coverage)?coverage:[];
  const hist=Array.isArray(history)?history:[];
  const keys=Array.isArray(months)?months:[];
  const covMonths=new Set();
  for(const t of cov)covMonths.add(t.date.slice(0,7));
  const histIncome=new Map(),histExpense=new Map();
  for(const t of hist){
    const k=t.date.slice(0,7);
    const s=t.direction==='in'?t.minor:-t.minor;
    if(t.category==='income')histIncome.set(k,(histIncome.get(k)||0)+s);
    else if(t.category==='expense')histExpense.set(k,(histExpense.get(k)||0)-s);
  }
  const income=new Array(keys.length),expense=new Array(keys.length);
  keys.forEach((k,i)=>{
    const py=Number(k.slice(0,4))-1,pm=k.slice(5,7);
    const pk=py<1?null:iso(py,Number(pm),1).slice(0,7);
    if(!pk||!covMonths.has(pk)){income[i]=null;expense[i]=null;return}
    income[i]=histIncome.get(pk)||0;
    expense[i]=histExpense.get(pk)||0;
  });
  return{income,expense};
}

window.periodComparisons={calculate,monthlyOverlay};
})();
