/* Chat — écran par défaut.
 *
 * Parti pris : JARVIS n'a pas de bulle. Les mots de l'utilisateur sont
 * enfermés dans une forme close alignée à droite ; ceux de JARVIS s'écrivent
 * à même le fond, tenus par un filet vertical. Il n'est pas au bout du fil,
 * il est dans la pièce.
 */

import * as ws from '../ws.js';
import { h, icon } from '../ui.js';

const ACTION_LABELS = {
  terminal: 'Exécution sur le Mac',
  mail: "Envoi d'un message",
  calendar_create: "Création d'un événement",
};

export default {
  async mount(ctx) {
    const thread = h('div', { class: 'thread' });
    const wrap = h('div', {}, thread);

    let streaming = null;   // { node, text } — réponse en cours d'écriture
    let atBottom = true;

    const scroll = () => {
      if (atBottom) wrap.parentElement.scrollTop = wrap.parentElement.scrollHeight;
    };

    // ── Bulles ──
    const mine = (text) => { thread.append(h('div', { class: 'mine', text })); scroll(); };

    const jarvisNode = (pending = false) => {
      const node = h('div', { class: pending ? 'jarvis pending' : 'jarvis' },
        h('span', { class: 'who', text: 'Jarvis' }));
      thread.append(node);
      return node;
    };

    const jarvis = (text, pending = false) => {
      const node = jarvisNode(pending);
      node.append(document.createTextNode(text));
      scroll();
      return node;
    };

    // ── Composer ──
    const field = h('input', {
      class: 'field', type: 'text', placeholder: 'Écrire à JARVIS',
      enterkeyhint: 'send', autocomplete: 'off', autocorrect: 'on', 'aria-label': 'Message',
    });
    const sendBtn = h('button', { class: 'round primary', type: 'button', 'aria-label': 'Envoyer' }, icon('send'));
    const micBtn = h('button', {
      class: 'round', type: 'button', 'aria-label': 'Dicter',
      onClick: () => ctx.navigate('voix'),
    }, icon('mic'));

    // Le bouton d'envoi ne paraît que lorsqu'il y a quelque chose à envoyer.
    const syncComposer = () => {
      const has = field.value.trim().length > 0;
      sendBtn.hidden = !has;
      micBtn.hidden = has;
    };

    const submit = () => {
      const text = field.value.trim();
      if (!text) return;
      if (!ws.isOpen()) {
        jarvis('Canal fermé. Le message n’a pas été envoyé.');
        return;
      }
      mine(text);
      field.value = '';
      syncComposer();
      ws.sendText(text);
    };

    field.addEventListener('input', syncComposer);
    field.addEventListener('keydown', (e) => { if (e.key === 'Enter') { e.preventDefault(); submit(); } });
    sendBtn.addEventListener('click', submit);
    syncComposer();

    ctx.setHeader('Chat', null, [
      { icon: 'plus', label: 'Nouvelle conversation', onClick: () => {
        ws.newConversation();
        thread.replaceChildren();
        streaming = null;
      } },
    ]);
    ctx.setBody(wrap);
    ctx.setDock(h('div', { style: 'display:flex;align-items:flex-end;gap:9px;width:100%' }, field, micBtn, sendBtn));

    // Suit l'intention de lecture : on ne recolle en bas que si l'utilisateur
    // y était déjà. Sinon une réponse longue arracherait sa lecture.
    const body = wrap.parentElement;
    body.addEventListener('scroll', () => {
      atBottom = body.scrollHeight - body.scrollTop - body.clientHeight < 60;
    }, { passive: true });

    // ── Flux serveur ──
    const off = [];

    off.push(ws.on('chunk', (msg) => {
      const delta = msg.content || '';
      if (!delta) return;
      if (!streaming) {
        const node = jarvisNode();
        streaming = { node, text: '', body: document.createTextNode(''), caret: h('span', { class: 'caret' }) };
        node.append(streaming.body, streaming.caret);
      }
      streaming.text += delta;
      streaming.body.textContent = streaming.text;
      scroll();
    }));

    const settle = (text) => {
      if (streaming) {
        streaming.caret.remove();
        streaming.body.textContent = text || streaming.text;
        streaming = null;
      } else if (text) {
        jarvis(text);
      }
      scroll();
    };

    off.push(ws.on('response', (msg) => settle(msg.content || '')));
    off.push(ws.on('response_clean', (msg) => settle(msg.content || '')));
    off.push(ws.on('response_followup', (msg) => { if (msg.content) jarvis(msg.content); }));

    // Confirmation d'action : la carte s'ouvre sous le message, commande
    // affichée verbatim. Les deux boutons sont de taille égale et séparés —
    // rien ne s'exécute par défilement ni par appui distrait.
    off.push(ws.on('action_pending', (msg) => {
      settle('');
      const action = msg.action || {};
      const label = ACTION_LABELS[msg.action_type] || 'Action à confirmer';
      const command = action.command || action.content || action.body || msg.message || '';

      const card = h('div', { class: 'confirm' },
        h('div', { class: 'confirm-h' }, icon('alert'), label),
        command ? h('div', { class: 'cmd', text: String(command) }) : null,
      );
      const close = (text) => { card.replaceWith(h('div', { class: 'jarvis' },
        h('span', { class: 'who', text: 'Jarvis' }), text)); };

      card.append(h('div', { class: 'confirm-a' },
        h('button', {
          class: 'btn ghost', type: 'button',
          onClick: () => { ws.send({ type: 'action_confirm', action, confirmed: false }); close('Annulé.'); },
        }, 'Refuser'),
        h('button', {
          class: 'btn primary', type: 'button',
          onClick: () => { ws.confirmAction(action); close('Exécution en cours.'); },
        }, 'Exécuter'),
      ));

      wrap.append(card);
      scroll();
    }));

    off.push(ws.on('action_result', (msg) => {
      if (msg.result && msg.result.message) jarvis(String(msg.result.message));
    }));

    off.push(ws.on('error', (msg) => {
      settle('');
      jarvis(msg.message || 'Erreur.');
    }));

    if (!ws.isOpen()) ws.connect();

    return () => { for (const fn of off) fn(); };
  },
};
