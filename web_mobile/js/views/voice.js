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
    let speech = [];          // morceaux audio renvoyés par le serveur
    let speechMime = 'audio/mpeg';
    let player = null;
    let playerUrl = null;
    let audioSource = null;
    let pointerHeld = false;
    let captureGeneration = 0;
    let disposed = false;

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
      arming:     ['Micro',           'Maintenez le bouton',          'busy'],
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
    function ensureAudioContext() {
      if (audioCtx && audioCtx.state !== 'closed') {
        if (audioCtx.state === 'suspended') {
          try { void audioCtx.resume(); } catch { /* le geste suivant réessaiera */ }
        }
        return audioCtx;
      }
      const AudioContextCtor = window.AudioContext || window.webkitAudioContext;
      if (!AudioContextCtor) return null;
      try {
        audioCtx = new AudioContextCtor();
        // Appelé directement dans pointerdown : Safari mémorise ainsi le geste
        // pour autoriser la réponse audio reçue plusieurs secondes plus tard.
        void audioCtx.resume();
        return audioCtx;
      } catch {
        audioCtx = null;
        return null;
      }
    }

    function recorderOptions() {
      if (!window.MediaRecorder || typeof MediaRecorder.isTypeSupported !== 'function') return {};
      const candidates = [
        'audio/mp4',
        'audio/webm;codecs=opus',
        'audio/webm',
        'audio/ogg;codecs=opus',
      ];
      const mimeType = candidates.find((candidate) => MediaRecorder.isTypeSupported(candidate));
      return mimeType ? { mimeType } : {};
    }

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
      if (state === 'listening' || state === 'processing' || state === 'arming') return;

      // Interrompre JARVIS fait partie du métier de majordome.
      if (state === 'speaking') { stopPlayback(); }

      if (!navigator.mediaDevices || !window.MediaRecorder) {
        say("Micro indisponible sur ce navigateur.", true);
        return;
      }
      const generation = ++captureGeneration;
      const context = ensureAudioContext();
      setState('arming');
      try {
        stream = await navigator.mediaDevices.getUserMedia({
          audio: { echoCancellation: true, noiseSuppression: true, autoGainControl: true },
        });
      } catch {
        // Refus de permission ou contexte non sécurisé (HTTP hors localhost).
        say("Micro refusé. Autorisez l’accès dans les réglages de Safari.", true);
        setState('idle');
        return;
      }

      // Au premier usage, la boîte de permission iOS survit au doigt. Si
      // l'utilisateur l'a déjà relâché, démarrer ici créerait un enregistrement
      // sans moyen physique de l'arrêter.
      if (disposed || !pointerHeld || generation !== captureGeneration) {
        for (const track of stream.getTracks()) track.stop();
        stream = null;
        setState('idle');
        say('Maintenez le bouton pendant que vous parlez.', true);
        return;
      }

      chunks = [];
      try {
        recorder = new MediaRecorder(stream, recorderOptions());
      } catch {
        releaseCapture(true);
        setState('idle');
        say("Format d’enregistrement non pris en charge.", true);
        return;
      }
      recorder.addEventListener('dataavailable', (e) => { if (e.data && e.data.size) chunks.push(e.data); });
      recorder.addEventListener('stop', onRecorded);
      recorder.start();

      if (context && context.state !== 'closed') {
        analyser = context.createAnalyser();
        analyser.fftSize = 256;
        context.createMediaStreamSource(stream).connect(analyser);
        drawLevel();
      }

      say('…', true);
      setState('listening');
    }

    function stopRecording() {
      if (state !== 'listening' || !recorder) return;
      try { recorder.stop(); } catch { /* déjà arrêté */ }
      setState('processing');
    }

    function releaseCapture(closeContext = false) {
      if (rafId) { cancelAnimationFrame(rafId); rafId = null; }
      analyser = null;
      if (stream) { for (const t of stream.getTracks()) t.stop(); stream = null; }
      recorder = null;
      if (closeContext && audioCtx) {
        try { void audioCtx.close(); } catch { /* ignoré */ }
        audioCtx = null;
      }
    }

    function onRecorded() {
      const blob = new Blob(chunks, { type: chunks[0] ? chunks[0].type : 'audio/webm' });
      chunks = [];
      releaseCapture(false);
      if (disposed) return;
      if (!blob.size) {
        releaseCapture(true);
        setState('idle');
        say('Rien entendu.', true);
        return;
      }
      if (!ws.sendBinary(blob)) {
        releaseCapture(true);
        setState('idle');
        say('Canal fermé. Rien n’a été envoyé.', true);
      }
    }

    // ── Lecture de la réponse ──
    function finishPlayback({ clearText = false } = {}) {
      if (audioSource) {
        audioSource.onended = null;
        audioSource = null;
      }
      if (player) {
        player.onended = null;
        player.onerror = null;
        player = null;
      }
      if (playerUrl) { URL.revokeObjectURL(playerUrl); playerUrl = null; }
      ws.donePlaying();
      setState('idle');
      if (clearText) say('');
      if (audioCtx) {
        try { void audioCtx.close(); } catch { /* ignoré */ }
        audioCtx = null;
      }
    }

    function stopPlayback() {
      if (audioSource) { try { audioSource.stop(); } catch { /* déjà terminé */ } }
      if (player) { try { player.pause(); } catch { /* ignoré */ } }
      speech = [];
      finishPlayback({ clearText: true });
    }

    async function decodeSpeech(context, parts, blob) {
      // Edge envoie les fragments d'un seul flux MP3 : ils doivent être
      // concaténés avant décodage. Kokoro envoie au contraire plusieurs WAV
      // complets ; concaténer leurs en-têtes ne joue que la première phrase.
      if (speechMime !== 'audio/wav' || parts.length <= 1) {
        return context.decodeAudioData(await blob.arrayBuffer());
      }

      const decodedParts = [];
      for (const part of parts) {
        const bytes = part instanceof ArrayBuffer ? part.slice(0) : await new Blob([part]).arrayBuffer();
        decodedParts.push(await context.decodeAudioData(bytes));
      }
      const sampleRate = decodedParts[0].sampleRate;
      const channels = Math.max(...decodedParts.map((buffer) => buffer.numberOfChannels));
      const length = decodedParts.reduce((sum, buffer) => sum + buffer.length, 0);
      const merged = context.createBuffer(channels, length, sampleRate);
      let offset = 0;
      for (const buffer of decodedParts) {
        for (let channel = 0; channel < channels; channel += 1) {
          const sourceChannel = Math.min(channel, buffer.numberOfChannels - 1);
          merged.copyToChannel(buffer.getChannelData(sourceChannel), channel, offset);
        }
        offset += buffer.length;
      }
      return merged;
    }

    async function playSpeech() {
      if (!speech.length) {
        // Le serveur émet aussi speech_done lorsque le moteur TTS est absent.
        // Sans cet acquittement, son état PTT reste `is_speaking=true` et tous
        // les enregistrements suivants sont silencieusement ignorés.
        finishPlayback();
        return;
      }
      const parts = speech;
      const blob = new Blob(parts, { type: speechMime });
      speech = [];
      const context = audioCtx && audioCtx.state !== 'closed' ? audioCtx : null;

      if (context) {
        try {
          const decoded = await decodeSpeech(context, parts, blob);
          if (disposed) return;
          audioSource = context.createBufferSource();
          audioSource.buffer = decoded;
          audioSource.connect(context.destination);
          audioSource.onended = () => finishPlayback();
          audioSource.start();
          setState('speaking');
          return;
        } catch {
          // Certains moteurs renvoient un conteneur que WebAudio ne décode pas
          // sur une version donnée d'iOS. L'élément audio reste le repli.
        }
      }

      playerUrl = URL.createObjectURL(blob);
      player = new Audio(playerUrl);
      player.playsInline = true;
      player.onended = () => finishPlayback();
      player.onerror = () => finishPlayback();
      player.play().catch(() => {
        say('Réponse reçue. Lecture bloquée par le navigateur.', true);
        finishPlayback();
      });
      setState('speaking');
    }

    // Appui maintenu — pointeur (couvre tactile, souris et stylet).
    micBtn.addEventListener('pointerdown', (e) => {
      e.preventDefault();
      pointerHeld = true;
      try { micBtn.setPointerCapture(e.pointerId); } catch { /* ancien Safari */ }
      // L'AudioContext doit être créé dans la pile du geste, avant toute await.
      ensureAudioContext();
      void startRecording();
    });
    micBtn.addEventListener('pointerup', (e) => {
      e.preventDefault();
      pointerHeld = false;
      if (state === 'arming') {
        captureGeneration += 1;
        setState('idle');
        say('Maintenez le bouton pendant que vous parlez.', true);
      } else {
        stopRecording();
      }
    });
    micBtn.addEventListener('pointercancel', () => {
      pointerHeld = false;
      captureGeneration += 1;
      if (state === 'arming') setState('idle');
      else stopRecording();
    });
    micBtn.addEventListener('contextmenu', (e) => e.preventDefault());

    const off = [];
    off.push(ws.on('transcript', (m) => { if (m.content || m.text) say(m.content || m.text); }));
    off.push(ws.on('processing', () => setState('processing')));
    off.push(ws.on('response', (m) => { if (m.content) say(m.content); }));
    off.push(ws.on('response_clean', (m) => { if (m.content) say(m.content); }));
    off.push(ws.on('speaking', (m) => {
      speech = [];
      speechMime = m.audio_mime || 'audio/mpeg';
    }));
    off.push(ws.on('binary', (buf) => { speech.push(buf); }));
    off.push(ws.on('speech_done', () => { void playSpeech(); }));
    off.push(ws.on('speech_cancelled', () => {
      speech = [];
      finishPlayback();
    }));
    off.push(ws.on('error', (m) => {
      speech = [];
      releaseCapture(true);
      setState('idle');
      say(m.message || 'Erreur.', true);
    }));

    ctx.setHeader('Voix', null, [
      { icon: 'x', label: 'Retour au chat', onClick: () => ctx.navigate('chat') },
    ]);
    ctx.setBody(root);
    ctx.setDock(null);
    setState('idle');
    say('');

    if (!ws.isOpen()) ws.connect();

    return () => {
      disposed = true;
      pointerHeld = false;
      captureGeneration += 1;
      for (const fn of off) fn();
      if (state === 'listening' && recorder) { try { recorder.stop(); } catch { /* ignoré */ } }
      if (state === 'processing' || state === 'speaking' || speech.length) ws.donePlaying();
      if (audioSource) { try { audioSource.stop(); } catch { /* ignoré */ } audioSource = null; }
      if (player) { try { player.pause(); } catch { /* ignoré */ } player = null; }
      if (playerUrl) { URL.revokeObjectURL(playerUrl); playerUrl = null; }
      releaseCapture(true);
    };
  },
};
