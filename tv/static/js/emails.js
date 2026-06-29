/**
 * Widget Emails — fetch /api/emails + rendu.
 * @module emails
 */
(function() {

var el = document.getElementById('widget-emails');

function render(data) {
  if (!Array.isArray(data) || data.length === 0) {
    el.innerHTML = '<span class="empty-state">AUCUN EMAIL</span>';
    return;
  }

  var html = '';
  for (var i = 0; i < data.length; i++) {
    var e = data[i];
    html += '<div class="email-row">';
    html += '<span class="email-icon">' + (e.action_needed ? '!' : '&gt;') + '</span>';
    html += '<span class="email-sender">' + TV.esc(e.sender) + '</span>';
    html += '<span class="email-subject">' + TV.esc(e.subject) + '</span>';
    html += '</div>';
  }
  el.innerHTML = html;
}

function refresh() {
  TV.fetch('/api/emails').then(function(d) { render(d); TV.pulse('rd-emails'); });
}

var interval = (window.TV_INTERVALS && window.TV_INTERVALS.emails) ? window.TV_INTERVALS.emails * 1000 : 300000;
setInterval(refresh, interval);
refresh();

})();
