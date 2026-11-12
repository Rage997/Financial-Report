/* Print the current dashboard locally; never change the saved screen theme. */
(function(){
'use strict';
const root=document.documentElement;
let printing=false,buttonPrint=false,contextReader,svgState=[],closedDetails=[];
function setPrintLayout(){
  printing=true;root.setAttribute('data-printing','');root.removeAttribute('data-theme');
  closedDetails=[...document.querySelectorAll('#dashboard details:not([open])')];
  for(const details of closedDetails)details.open=true;
  document.getElementById('printContext').textContent=contextReader();
}
function scaleSVGs(){
  svgState=[...document.querySelectorAll('.js-plotly-plot svg.main-svg')].map(svg=>{
    const previous=svg.getAttribute('viewBox');
    svg.setAttribute('viewBox',`0 0 ${svg.getAttribute('width')} ${svg.getAttribute('height')}`);
    return {svg,previous};
  });
}
async function restore(){
  for(const {svg,previous} of svgState){
    if(previous===null)svg.removeAttribute('viewBox');else svg.setAttribute('viewBox',previous);
  }
  svgState=[];root.removeAttribute('data-printing');
  for(const details of closedDetails)details.open=false;
  closedDetails=[];
  const palette=window.dashboardAppearance.palette();
  if(palette.theme==='dark')root.setAttribute('data-theme','dark');else root.removeAttribute('data-theme');
  printing=false;
  await window.dashboardAppearance.renderCharts(palette);
}
window.dashboardPrint={
  init(readContext){
    contextReader=readContext;
    window.addEventListener('beforeprint',()=>{
      if(printing)return;
      // Native Ctrl+P cannot await rendering. Print CSS and viewBoxes keep the
      // existing SVGs light and unclipped; the button also awaits a full redraw.
      setPrintLayout();scaleSVGs();
    });
    window.addEventListener('afterprint',()=>{if(printing&&!buttonPrint)restore().catch(error=>console.error(error))});
  },
  async print(){
    if(printing)return;
    buttonPrint=true;
    try{
      setPrintLayout();
      await window.dashboardAppearance.renderCharts(window.dashboardAppearance.palette('light'));
      await Promise.all([...document.querySelectorAll('.js-plotly-plot')]
        .filter(chart=>chart.offsetParent!==null).map(chart=>Plotly.Plots.resize(chart)));
      scaleSVGs();
      await new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve)));
      window.print();
    }finally{
      buttonPrint=false;await restore();
    }
  }
};
})();
