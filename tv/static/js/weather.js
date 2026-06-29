/** Widget Meteo — Lucide icons, mood integre. */
(function(){
var el=document.getElementById('widget-weather');
function wmoIcon(code) {
  if (code<=1) return 'sun';
  if (code===2) return 'cloud-sun';
  if (code===3) return 'cloud';
  if (code>=45&&code<=48) return 'cloud-fog';
  if (code>=51&&code<=55) return 'cloud-drizzle';
  if (code>=61&&code<=65) return 'cloud-rain';
  if (code>=71&&code<=77) return 'cloud-snow';
  if (code>=80&&code<=82) return 'cloud-rain';
  if (code>=85&&code<=86) return 'cloud-snow';
  if (code>=95) return 'cloud-lightning';
  return 'cloud';
}
function render(d){
  if (TV.isError(d)) { TV.signalLost(el); return; }
  var c=d.current, fc=d.forecast||[];
  var h='<div class="weather-current">';
  h+='<i data-lucide="'+wmoIcon(c.weather_code||3)+'" style="width:28px;height:28px;color:var(--accent-cyan)"></i>';
  h+='<span class="weather-temp">'+TV.esc(String(c.temperature))+'°C</span>';
  h+='<span class="weather-desc">'+TV.esc(c.description||'')+'</span></div>';
  h+='<div class="weather-wind">Vent '+TV.esc(String(c.wind_speed))+' km/h</div>';
  for (var i=0;i<fc.length;i++) {
    var f=fc[i], day=f.date.substring(5);
    h+='<div class="weather-forecast-row">';
    h+='<span class="weather-fc-day">'+TV.esc(day)+'</span>';
    h+='<span class="weather-fc-temps">'+TV.esc(f.max)+'° / '+TV.esc(f.min)+'°</span></div>';
  }
  h+='<div class="mood-section glass-inner" id="mood-inline"></div>';
  el.innerHTML=h;
  TV.renderIcons();
  fetchMood();
}
function fetchMood() {
  TV.fetch('/api/mood').then(function(d){
    var m=document.getElementById('mood-inline');
    if (!m) return;
    if (d.mood_score===null||d.mood_score===undefined||d.mood_score===0) {
      m.innerHTML='<div class="mood-row"><i data-lucide="heart" style="color:var(--text-muted)"></i><span class="mood-label">Aucune donnee</span></div>';
      TV.renderIcons(); return;
    }
    var h='<div class="mood-row"><i data-lucide="heart" style="color:var(--accent-purple);width:16px;height:16px"></i><span class="mood-label">Humeur</span><span class="mood-value">'+d.mood_score+'/10</span></div>';
    h+='<div class="mood-bar"><div class="mood-bar-fill" style="width:'+(d.mood_score*10)+'%"></div></div>';
    h+='<div class="mood-row"><i data-lucide="zap" style="color:var(--accent-amber);width:16px;height:16px"></i><span class="mood-label">Energie</span><span class="mood-value">'+(d.energy_level||'?')+'/10</span></div>';
    m.innerHTML=h;
    TV.renderIcons();
  });
}
function refresh(){ TV.fetch('/api/weather').then(render); TV.pulse('rd-weather'); }
var iv=(window.TV_INTERVALS&&window.TV_INTERVALS.weather)?window.TV_INTERVALS.weather*1000:900000;
setInterval(refresh,iv); refresh();
})();
