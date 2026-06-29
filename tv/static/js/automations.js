/** Widget Activite IA — timeline verticale. */
(function(){
var el=document.getElementById('widget-automations');
var COLORS={productivity:'var(--accent-cyan)',productivity_triage:'var(--accent-cyan)',productivity_draft:'var(--accent-cyan)',school:'var(--accent-amber)',coach:'var(--accent-purple)',coach_deep:'var(--accent-purple)',info:'var(--accent-blue)',journal:'var(--text-secondary)',orchestrator:'var(--text-muted)',memory:'var(--text-muted)',action_executor:'var(--accent-cyan)'};
var DOTCLS={productivity:'prod',productivity_triage:'prod',productivity_draft:'prod',school:'school',coach:'coach',coach_deep:'coach',info:'info',journal:'journal',orchestrator:'',memory:'',action_executor:'prod'};
function render(d){
  if (!Array.isArray(d)||d.length===0){el.innerHTML='<div class="empty-state"><i data-lucide="sparkles"></i>Aucune action recente</div>';TV.renderIcons();return;}
  if (d[0]&&d[0].error){TV.signalLost(el);return;}
  var h='<div class="timeline">';
  for (var i=0;i<d.length;i++){
    var a=d[i], color=COLORS[a.agent]||'var(--text-muted)', dc=DOTCLS[a.agent]||'';
    h+='<div class="timeline-item">';
    h+='<div class="timeline-dot '+dc+'" style="background:'+color+'"></div>';
    h+='<div class="timeline-content">';
    h+='<div class="timeline-time">'+TV.esc(a.time)+'</div>';
    h+='<div class="timeline-agent" style="color:'+color+'">'+TV.esc(a.agent)+'</div>';
    h+='<div class="timeline-desc">'+TV.esc(a.preview||a.action_type)+'</div></div></div>';
  }
  h+='</div>';
  el.innerHTML=h;
}
function refresh(){TV.fetch('/api/automations').then(render);TV.pulse('rd-automations');}
var iv=(window.TV_INTERVALS&&window.TV_INTERVALS.automations)?window.TV_INTERVALS.automations*1000:30000;
setInterval(refresh,iv);refresh();
})();
