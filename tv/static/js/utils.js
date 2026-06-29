/** Helpers JARVIS TV — fetch, pulse, icones Lucide. */
window.TV = window.TV || {};
TV.API_BASE = '';
TV.fetch = async function(p) {
  try { var r = await fetch(TV.API_BASE + p); if (!r.ok) throw new Error('HTTP '+r.status); return await r.json(); }
  catch(e) { return {ok:false,error:e.message}; }
};
TV.pulse = function(id) {
  var d = document.getElementById(id); if (!d) return;
  d.classList.add('pulse'); setTimeout(function(){d.classList.remove('pulse');},400);
};
TV.isError = function(d) { return !d || d.ok === false || d.error; };
TV.esc = function(s) { if (!s) return ''; return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;'); };
TV.barClass = function(p) { if (p >= 85) return 'danger'; if (p >= 60) return 'warn'; return ''; };
TV.signalLost = function(el) {
  if (!el) return;
  el.innerHTML = '<div class="signal-lost"><i data-lucide="wifi-off"></i>Signal perdu</div>';
  if (window.lucide) lucide.createIcons();
};
TV.renderIcons = function() { if (window.lucide) lucide.createIcons(); };
TV.fadeInCards = function() {
  document.querySelectorAll('.fade-in').forEach(function(c,i){
    setTimeout(function(){c.classList.add('visible');},i*50);
  });
};
