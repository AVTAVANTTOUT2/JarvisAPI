/** Widget Notifications — glow subtil par priorite. */
(function(){
var el=document.getElementById('widget-notifications');
function render(d){
  if (!Array.isArray(d)||d.length===0){el.innerHTML='<div class="empty-state"><i data-lucide="bell"></i>Aucune alerte</div>';TV.renderIcons();return;}
  var h='';
  for (var i=0;i<d.length;i++){
    var n=d[i], p=n.priority||'low';
    h+='<div class="notif-item '+p+'">';
    h+='<span class="notif-dot '+p+'"></span>';
    h+='<span class="notif-content">'+TV.esc(n.content||n.title||'')+'</span></div>';
  }
  el.innerHTML=h;
}
function refresh(){TV.fetch('/api/notifications').then(render);TV.pulse('rd-notifications');}
var iv=(window.TV_INTERVALS&&window.TV_INTERVALS.notifications)?window.TV_INTERVALS.notifications*1000:30000;
setInterval(refresh,iv);refresh();
})();
