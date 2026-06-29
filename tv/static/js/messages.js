/** Widget Messages — avatars, badges source. */
(function(){
var el=document.getElementById('widget-messages');
function initials(n){if(!n)return'?';var p=n.split(/\s+/);return p.length>1?(p[0][0]+p[p.length-1][0]).toUpperCase():n.substring(0,2).toUpperCase();}
function render(d){
  if (!Array.isArray(d)||d.length===0){el.innerHTML='<div class="empty-state"><i data-lucide="message-circle"></i>Aucun message</div>';TV.renderIcons();return;}
  var h='';
  for (var i=0;i<d.length;i++){
    var m=d[i], src=m.source, isIm=(src==='imessage');
    h+='<div class="msg-item">';
    h+='<div class="msg-avatar">'+TV.esc(initials(m.display_name))+'</div>';
    h+='<div class="msg-body">';
    h+='<div class="msg-meta">';
    h+='<span class="msg-sender">'+TV.esc(m.display_name||'?')+'</span>';
    h+='<span class="msg-source-badge'+(isIm?' imessage':' jarvis')+'">'+(isIm?'iMessage':'JARVIS')+'</span>';
    h+='<span class="msg-time">'+TV.esc(String(m.timestamp||'').substring(11,16))+'</span>';
    h+='</div>';
    h+='<div class="msg-text">'+TV.esc(m.text||'')+'</div></div></div>';
  }
  el.innerHTML=h;
}
function refresh(){TV.fetch('/api/messages').then(render);TV.pulse('rd-messages');}
var iv=(window.TV_INTERVALS&&window.TV_INTERVALS.messages)?window.TV_INTERVALS.messages*1000:30000;
setInterval(refresh,iv);refresh();
})();
