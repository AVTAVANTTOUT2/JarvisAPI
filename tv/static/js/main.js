/** Orchestrateur — health check, footer devices, fade-in. */
(function(){
var sd=document.getElementById('status-dot'),st=document.getElementById('status-text');
function checkHealth(){
  TV.fetch('/api/health').then(function(d){
    var alive=d&&d.tv==='ok'&&d.backend;
    if(alive){sd.classList.remove('offline');st.textContent='Operationnel';}
    else{sd.classList.add('offline');st.textContent='Alerte';}
  }).catch(function(){sd.classList.add('offline');st.textContent='Alerte';});
}
setInterval(checkHealth,15000);checkHealth();
// Footer devices
var fd=document.getElementById('footer-devices'),fc=document.getElementById('footer-cost');
function updateDevices(){
  TV.fetch('/api/devices').then(function(d){
    var devs=d.devices||[],h='';
    for (var i=0;i<devs.length;i++){
      var dev=devs[i],cls='online';
      if(dev.status==='idle')cls='idle';
      if(dev.status==='offline'||dev.status==='unknown')cls='offline';
      h+='<div class="footer-device-item"><span class="footer-device-dot '+cls+'"></span>'+TV.esc(dev.device_name)+' <span style="color:var(--text-muted)">'+TV.esc(dev.idle_text)+'</span></div>';
    }
    if(h==='')h='<span style="color:var(--text-muted)">Aucun device</span>';
    fd.innerHTML=h;
    var cost=d.api_cost_today;
    if(cost!==undefined&&cost!==null)fc.textContent='$'+Number(cost).toFixed(4);
    TV.pulse('rd-devices');
  });
}
var div=(window.TV_INTERVALS&&window.TV_INTERVALS.devices)?window.TV_INTERVALS.devices*1000:60000;
setInterval(updateDevices,div);updateDevices();
// Fade-in cards
setTimeout(function(){TV.fadeInCards();},200);
})();
