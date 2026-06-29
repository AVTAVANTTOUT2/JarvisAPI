/** Widget Systeme — barres progressions + services. */
(function(){
var el=document.getElementById('widget-stats');
function bar(pct) {
  var cls=TV.barClass(pct);
  return '<div class="stat-bar"><div class="stat-bar-fill '+cls+'" style="width:'+Math.min(pct,100)+'%"></div></div>';
}
function render(d){
  if (TV.isError(d)) { TV.signalLost(el); return; }
  var cpu=d.cpu||{}, ram=d.ram||{}, disk=d.disk||{}, svc=d.services||{};
  var h='';
  h+='<div class="stat-row"><div class="stat-label"><span>CPU</span><span class="stat-pct">'+(cpu.percent||0).toFixed(1)+'%</span></div>'+bar(cpu.percent||0)+'</div>';
  h+='<div class="stat-row"><div class="stat-label"><span>RAM</span><span class="stat-pct">'+(ram.percent||0).toFixed(1)+'%</span></div>'+bar(ram.percent||0);
  h+='<div class="stat-value">'+(ram.used_gb||0)+' / '+(ram.total_gb||0)+' GB</div></div>';
  h+='<div class="stat-row"><div class="stat-label"><span>DISQUE</span><span class="stat-pct">'+(disk.percent||0).toFixed(1)+'%</span></div>'+bar(disk.percent||0);
  h+='<div class="stat-value">'+(disk.used_gb||0)+' / '+(disk.total_gb||0)+' GB</div></div>';
  h+='<div class="service-list"><div class="service-list-title">Services</div>';
  var keys=Object.keys(svc).sort();
  for (var i=0;i<keys.length;i++) {
    var k=keys[i], up=svc[k], label=k.replace('com.jarvis.','');
    h+='<div class="service-item"><span class="service-dot'+(up?' pulse':' offline')+'"></span>'+TV.esc(label)+'</div>';
  }
  var ollama=d.ollama;
  h+='<div class="service-item"><span class="service-dot'+(ollama?' pulse':' offline')+'"></span>Ollama</div>';
  if (d.database) h+='<div class="service-item"><span class="service-dot pulse"></span>SQLite</div>';
  if (d.hasOwnProperty('backend')) h+='<div class="service-item"><span class="service-dot'+(d.backend?' pulse':' offline')+'"></span>Backend</div>';
  if (d.backend_data&&d.backend_data.cost_today!==undefined)
    h+='<div class="stat-value" style="margin-top:8px">API: $'+Number(d.backend_data.cost_today).toFixed(4)+'</div>';
  h+='</div>';
  el.innerHTML=h;
}
function refresh(){ TV.fetch('/api/stats').then(render); TV.pulse('rd-stats'); }
var iv=(window.TV_INTERVALS&&window.TV_INTERVALS.stats)?window.TV_INTERVALS.stats*1000:10000;
setInterval(refresh,iv); refresh();
})();
