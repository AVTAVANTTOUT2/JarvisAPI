/**
 * Widget Mood — fetch /api/mood + rendu.
 * @module mood
 */
(function() {

var el = document.getElementById('widget-mood');

function render(data) {
  if (TV.isError(data)) { el.innerHTML = '<span class="mood-na">AUCUNE DONNEE</span>'; return; }
  if (data.mood_score === null || data.mood_score === undefined) {
    el.innerHTML = '<span class="mood-na">AUCUNE DONNEE</span>';
    return;
  }
  var html = '<div class="mood-score">MOOD: ' + data.mood_score + '/10</div>';
  html += '<div class="mood-energy">ENERGIE: ' + (data.energy_level || '?') + '/10</div>';
  if (data.context) {
    html += '<div class="mood-context">' + TV.esc(data.context) + '</div>';
  }
  el.innerHTML = html;
}

function refresh() {
  TV.fetch('/api/mood').then(function(d) { render(d); TV.pulse('rd-mood'); });
}

var interval = (window.TV_INTERVALS && window.TV_INTERVALS.mood) ? window.TV_INTERVALS.mood * 1000 : 300000;
setInterval(refresh, interval);
refresh();

})();
