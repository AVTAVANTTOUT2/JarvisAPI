/**
 * voice-overlay.js — Affichage temps reel de la conversation vocale sur la TV.
 *
 * S'abonne au SSE /api/events et affiche l'etat du daemon audio,
 * la transcription utilisateur, et la reponse de JARVIS.
 * L'overlay disparait 3s apres retour en veille.
 */

var JARVIS_VOICE = (function () {
  'use strict';

  /** Duree avant disparition de l'overlay apres retour en idle (ms). */
  var IDLE_FADE_DELAY_MS = 3000;

  /** Cache les elements DOM. */
  var overlay = null;
  var stateEl = null;
  var transcriptEl = null;
  var responseEl = null;
  var orbEl = null;
  var eventSource = null;
  var idleTimer = null;

  /** Mapping etat → configuration visuelle. */
  var STATE_CONFIG = {
    idle:            { color: '#52525b', label: 'VEILLE',       visible: false },
    wake_listening:  { color: '#00d4ff', label: 'ECOUTE',       visible: true  },
    listening:       { color: '#00d4ff', label: 'ECOUTE',       visible: true  },
    processing:      { color: '#a855f7', label: 'TRAITEMENT',   visible: true  },
    speaking:        { color: '#f59e0b', label: 'JARVIS PARLE', visible: true  },
    error:           { color: '#ef4444', label: 'ERREUR',       visible: true  },
  };

  // ── Initialisation ──────────────────────────────────────────

  function init() {
    overlay       = document.getElementById('voice-overlay');
    stateEl       = document.getElementById('voice-state');
    transcriptEl  = document.getElementById('voice-transcript');
    responseEl    = document.getElementById('voice-response');
    orbEl         = document.getElementById('voice-orb');

    if (!overlay || !stateEl || !transcriptEl || !responseEl || !orbEl) {
      console.warn('[voice-overlay] Elements DOM manquants — desactive');
      return;
    }

    connectSSE();
  }

  // ── Connexion SSE ───────────────────────────────────────────

  function connectSSE() {
    if (eventSource) {
      eventSource.close();
    }

    eventSource = new EventSource('/api/events');

    eventSource.onmessage = function (e) {
      var data;
      try {
        data = JSON.parse(e.data);
      } catch (_) {
        return;
      }
      handleEvent(data);
    };

    eventSource.onerror = function () {
      // Le navigateur reconnecte automatiquement.
      // On ne fait rien — l'etat courant est preserve.
    };
  }

  // ── Dispatch des evenements ─────────────────────────────────

  function handleEvent(data) {
    var type = data.type || '';

    switch (type) {
      case 'audio_daemon_state':
        handleDaemonState(data);
        break;
      case 'heartbeat':
        // Rien a faire — maintien de la connexion
        break;
    }
  }

  function handleDaemonState(data) {
    // 1. Mise a jour de l'etat visuel (orbe + label)
    updateState(data.state);

    // 2. Affichage transcription si presente
    if (data.transcript) {
      showTranscript(data.transcript);
    }

    // 3. Affichage reponse si presente
    if (data.response) {
      showResponse(data.response);
    }
  }

  // ── Etat visuel ─────────────────────────────────────────────

  function updateState(state) {
    var cfg = STATE_CONFIG[state] || STATE_CONFIG.idle;

    // Visibilite
    overlay.style.display = cfg.visible ? 'flex' : 'none';

    // Orbe
    orbEl.style.background = 'radial-gradient(circle, ' + cfg.color + '40, ' + cfg.color + '10)';
    orbEl.style.borderColor = cfg.color;

    // Label
    stateEl.textContent = cfg.label;
    stateEl.style.color = cfg.color;

    // Nettoyage au retour en veille
    if (idleTimer) {
      clearTimeout(idleTimer);
      idleTimer = null;
    }

    if (state === 'idle') {
      idleTimer = setTimeout(function () {
        transcriptEl.textContent = '';
        responseEl.textContent = '';
      }, IDLE_FADE_DELAY_MS);
    }
  }

  // ── Transcription ───────────────────────────────────────────

  function showTranscript(text) {
    transcriptEl.textContent = text;
    // Reset la reponse precedente quand une nouvelle phrase commence
    responseEl.textContent = '';
  }

  // ── Reponse ─────────────────────────────────────────────────

  function showResponse(text) {
    responseEl.textContent = text;
  }

  // ── Demarrage automatique ───────────────────────────────────

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }

  // API publique
  return {
    reconnect: connectSSE,
  };
})();
