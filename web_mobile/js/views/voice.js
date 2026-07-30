/* Voix — appui maintenu.
 *
 * Pas de détection automatique de fin de parole : Safari iOS exige un geste
 * utilisateur pour ouvrir le micro comme pour lancer une lecture audio, et
 * une écoute permanente y est de toute façon impossible. Le doigt reste posé
 * pendant qu'on parle et se relève pour envoyer — un contrat physique, donc
 * jamais ambigu.
 *
 * Quatre états : repos, écoute, traitement, réponse.
 */

import * as ws from '../ws.js';
import { h, icon } from '../ui.js';

const BARS = 15;

export default {
  async mount(ctx) {
    let state = 'idle';
    let recorder = null;
    let stream = null;
    let audioCtx = null;
    let analyser = null;
    let rafId = null;
    let chunks = [];
    let speech = [];          // morceaux MP3 renvoyés par le serveur
    let player = null;

    const stateLine = h('p', { class: 'v-state' });
    const transcript = h('div', { class: 'v-trans' });
    const meter = h('div', { class: 'v-meter' }, ...Array.from({ length: BARS }, () => h('i')));
    const hint = h('p', { class: 'v-hint' });
    const micBtn = h('button', {
      class: 'micbtn', type: 'button', 'aria-label': 'Maintenir pour parler',
    }, icon('mic'));
    const micWrap = h('div', { class: 'v-mic' },
      h('span', { class: 'ring' }), h('span', { class: 'ring r2' }), h('span', { class: 'ring r3' }), micBtn);
    const root = h('div', { class: 'voice' }, stateLine, transcript, meter, hint, micWrap);

    const LABELS = {
      idle:       ['Prêt',           'Maintenir pour parler',        ''],
      listening:  ['À l’écoute',     'Relâchez pour envoyer',        'listening'],
      processing: ['Traitement',     'JARVIS réfléchit',             'busy'],
      speaking:   ['Réponse',        'Appuyez pour interrompre',     'reply'],
    };

    function setState(next) {
      state = next;
      const [label, hintText, cls] = LABELS[next];
      stateLine.textContent = label;
      stateLine.className = 'v-state' + (cls === 'busy' ? ' busy' : cls === 'reply' ? ' reply' : '');
      hint.textContent = hintText;
      root.className = 'voice' + (cls === 'listening' ? ' listening' : cls === 'reply' ? ' reply' : '');
      micBtn.disabled = next === 'processing';
      if (next !== 'listening') resetMeter();
    }

    function say(text, dim = false) {
      if (!text) { transcript.replaceChildren(); return; }
      transcript.replaceChildren(dim ? h('p', { class: 'dim', text }) : h('p', { text }));
    }

    function resetMeter() {
      for (const bar of meter.children) bar.style.height = '4px';
    }

    // ── Capture ──
    function drawLevel() {
      if (!analyser) return;
      const data = new Uint8Array(analyser.frequencyBinCount);
      analyser.getByteFrequencyData(data);
      const step = Math.floor(data.length / BARS) || 1;
      for (let i = 0; i < BARS; i += 1) {
        let sum = 0;
        for (let j = 0; j < step; j += 1) sum += data[i * step + j] || 0;
        const level = (sum / step) / 255;
        meter.children[i].style.height = `${4 + Math.min(1, level * 1.6) * 22}px`;
      }
      rafId = requestAnimationFrame(drawLevel);
    }

    async function startRecording() {
      if (state === 'listening' || state === 'processing') return;

      // Interrompre JARVIS fait partie du métier de majordome.
      if (state === 'speaking') { stopPlayback(); }

      if (!navigator.mediaDevices || !window.MediaRecorder) {
        say("Micro indisponible sur ce navigateur.", true);
        return;
      }
      try {
        stream = await navigator.mediaDevices.getUserMedia({
          audio: { echoCancellation: true, noiseSuppression: true, autoGainControl: true },
        });
      } catch {
        // Refus de permission ou contexte non sécurisé (HTTP hors localhost).
        say("Micro refusé. Autorisez l’accès dans les réglages de Safari.", true);
        return;
      }

      chunks = [];
      recorder = new MediaRecorder(stream);
      recorder.addEventListener('dataavailable', (e) => { if (e.data && e.data.size) chunks.push(e.data); });
      recorder.addEventListener('stop', onRecorded);
      recorder.start();

      audioCtx = new (window.AudioContext || window.webkitAudioContext)();
      analyser = audioCtx.createAnalyser();
      analyser.fftSize = 256;
      audioCtx.createMediaStreamSource(stream).connect(analyser);
      drawLevel();

      say('…', true);
      setState('listening');
    }

    function stopRecording() {
      if (state !== 'listening' || !recorder) return;
      try { recorder.stop(); } catch { /* déjà arrêté */ }
      setState('processing');
    }

    function releaseCapture() {
      if (rafId) { cancelAnimationFrame(rafId); rafId = null; }
      if (audioCtx) { try { void audioCtx.close(); } catch { /* ignoré */ } audioCtx = null; }
      analyser = null;
      if (stream) { for (const t of stream.getTracks()) t.stop(); stream = null; }
      recorder = null;
    }

    function onRecorded() {
      const blob = new Blob(chunks, { type: chunks[0] ? chunks[0].type : 'audio/webm' });
      chunks = [];
      releaseCapture();
      if (!blob.size) { setState('idle'); say('Rien entendu.', true); return; }
      if (!ws.sendBinary(blob)) {
        setState('idle');
        say('Canal fermé. Rien n’a été envoyé.', true);
      }
    }

    // ── Lecture de la réponse ──
    function stopPlayback() {
      if (player) { try { player.pause(); } catch { /* ignoré */ } URL.revokeObjectURL(player.src); player = null; }
      speech = [];
      ws.donePlaying();
      setState('idle');
      say('');
    }

    function playSpeech() {
      if (!speech.length) { setState('idle'); return; }
      const blob = new Blob(speech, { type: 'audio/mpeg' });
      speech = [];
      player = new Audio(URL.createObjectURL(blob));
      player.addEventListener('ended', () => {
        if (player) URL.revokeObjectURL(player.src);
        player = null;
        ws.donePlaying();
        setState('idle');
      });
      // Le geste utilisateur de l'appui autorise la lecture ; si Safari refuse
      // malgré tout, on le dit plutôt que de rester muet sans explication.
      player.play().catch(() => {
        say('Réponse reçue. Lecture bloquée par le navigateur.', true);
        setState('idle');
      });
      setState('speaking');
    }

    // Appui maintenu — pointeur (couvre tactile, souris et stylet).
    micBtn.addEventListener('pointerdown', (e) => { e.preventDefault(); void startRecording(); });
    micBtn.addEventListener('pointerup', (e) => { e.preventDefault(); stopRecording(); });
    micBtn.addEventListener('pointercancel', () => stopRecording());
    micBtn.addEventListener('pointerleave', () => { if (state === 'listening') stopRecording(); });
    micBtn.addEventListener('contextmenu', (e) => e.preventDefault());

    const off = [];
    off.push(ws.on('transcript', (m) => { if (m.content || m.text) say(m.content || m.text); }));
    off.push(ws.on('processing', () => setState('processing')));
    off.push(ws.on('response', (m) => { if (m.content) say(m.content); }));
    off.push(ws.on('response_clean', (m) => { if (m.content) say(m.content); }));
    off.push(ws.on('binary', (buf) => { speech.push(buf); }));
    off.push(ws.on('speech_done', () => playSpeech()));
    off.push(ws.on('error', (m) => { setState('idle'); say(m.message || 'Erreur.', true); }));

    ctx.setHeader('Voix', null, [
      { icon: 'x', label: 'Retour au chat', onClick: () => ctx.navigate('chat') },
    ]);
    ctx.setBody(root);
    ctx.setDock(null);
    setState('idle');
    say('');

    if (!ws.isOpen()) ws.connect();

    return () => {
      for (const fn of off) fn();
      if (state === 'listening' && recorder) { try { recorder.stop(); } catch { /* ignoré */ } }
      releaseCapture();
      if (player) { try { player.pause(); } catch { /* ignoré */ } player = null; }
    };
  },
};
