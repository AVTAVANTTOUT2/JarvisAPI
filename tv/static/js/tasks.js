/** Widget Taches. */
(function(){
var el=document.getElementById('widget-tasks');
function render(d){
  if (!Array.isArray(d)||d.length===0){el.innerHTML='<div class="empty-state"><i data-lucide="check-square"></i>Aucune tache</div>';TV.renderIcons();return;}
  var h='';
  for (var i=0;i<d.length;i++){
    var t=d[i];
    h+='<div class="task-item">';
    h+='<span class="task-dot '+t.priority+'"></span>';
    h+='<span class="task-title">'+TV.esc(t.title)+'</span>';
    if (t.due_date) h+='<span class="task-due">'+TV.esc(t.due_date)+'</span>';
    h+='</div>';
  }
  el.innerHTML=h;
}
function refresh(){TV.fetch('/api/tasks').then(render);TV.pulse('rd-tasks');}
var iv=(window.TV_INTERVALS&&window.TV_INTERVALS.tasks)?window.TV_INTERVALS.tasks*1000:120000;
setInterval(refresh,iv);refresh();
})();
