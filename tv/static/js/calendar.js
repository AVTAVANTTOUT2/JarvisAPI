/** Widget Calendrier. */
(function(){
var el=document.getElementById('widget-calendar');
function render(d){
  if (!Array.isArray(d)||d.length===0){el.innerHTML='<div class="empty-state"><i data-lucide="calendar"></i>Aucun evenement</div>';TV.renderIcons();return;}
  if (d[0]&&d[0].error){el.innerHTML='<div class="signal-lost"><i data-lucide="wifi-off"></i>'+TV.esc(d[0].message||'Signal perdu')+'</div>';TV.renderIcons();return;}
  var h='';
  for (var i=0;i<d.length;i++){
    var e=d[i], cls=e.is_live?'cal-item live':'cal-item';
    h+='<div class="'+cls+'"><span class="cal-time">'+TV.esc(e.time)+'</span><span class="cal-title">'+TV.esc(e.title)+'</span></div>';
  }
  el.innerHTML=h;
}
function refresh(){TV.fetch('/api/calendar').then(render);TV.pulse('rd-calendar');}
var iv=(window.TV_INTERVALS&&window.TV_INTERVALS.calendar)?window.TV_INTERVALS.calendar*1000:300000;
setInterval(refresh,iv);refresh();
})();
