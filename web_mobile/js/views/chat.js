/* Chat — écran par défaut.
 *
 * Parti pris : JARVIS n'a pas de bulle. Les mots de l'utilisateur sont
 * enfermés dans une forme close alignée à droite ; ceux de JARVIS s'écrivent
 * à même le fond, tenus par un filet vertical. Il n'est pas au bout du fil,
 * il est dans la pièce.
 */

import * as ws from '../ws.js';
import { api, ApiError } from '../api.js';
import { h, icon, skeleton, banner } from '../ui.js';

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
    let alive = true;
    let body = null;
    let showingHistory = false;
    let activeConversationId = null;
    let conversations = [];
    let historyError = null;

    const scroll = () => {
      if (atBottom && wrap.parentElement) {
        wrap.parentElement.scrollTop = wrap.parentElement.scrollHeight;
      }
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

    function renderMessages(messages) {
      thread.replaceChildren();
      streaming = null;
      for (const message of messages || []) {
        const text = String(message.content || '').trim();
        if (!text) continue;
        if (message.role === 'user') mine(text);
        else jarvis(text, message.role === 'system');
      }
      requestAnimationFrame(scroll);
    }

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

    const composer = h(
      'div',
      { style: 'display:flex;align-items:flex-end;gap:9px;width:100%' },
      field,
      micBtn,
      sendBtn,
    );

    function showThread() {
      showingHistory = false;
      ctx.setHeader('Chat', null, [
        { icon: 'list', label: 'Conversations', onClick: () => { void showHistory(); } },
        { icon: 'plus', label: 'Nouvelle conversation', onClick: beginNewConversation },
      ]);
      ctx.setBody(wrap);
      ctx.setDock(composer);
      body = wrap.parentElement;
      requestAnimationFrame(scroll);
    }

    function beginNewConversation() {
      if (!ws.newConversation()) {
        if (showingHistory) {
          historyError = 'Canal fermé. La conversation n’a pas été créée.';
          renderHistoryList();
        } else {
          jarvis('Canal fermé. La conversation n’a pas été créée.');
        }
      }
      // Le fil n'est vidé qu'après l'accusé `conversation_switched`. Une
      // coupure réseau ne doit jamais faire disparaître le fil courant.
    }

    function conversationRow(conversation) {
      const title = conversation.title || `Conversation ${conversation.id}`;
      const preview = String(conversation.last_message || '').trim();
      const count = conversation.message_count ?? conversation.msg_count ?? 0;
      return h('button', {
        class: 'conv-row',
        type: 'button',
        onClick: () => {
          historyError = null;
          if (!ws.switchConversation(conversation.id)) {
            historyError = 'Canal fermé. Impossible de changer de conversation.';
            renderHistoryList();
          }
        },
      },
        h('span', { class: 'conv-main' },
          h('span', { class: 'ct', text: title }),
          preview ? h('span', { class: 'cs conv-preview', text: preview }) : null),
        h('span', { class: 'cm num', text: String(count) }));
    }

    function renderHistoryList() {
      if (!showingHistory || !alive) return;
      const nodes = [];
      if (historyError) nodes.push(banner(historyError, 'err'));
      if (conversations.length) {
        nodes.push(h('div', { class: 'card flush conv-list' }, ...conversations.map(conversationRow)));
      } else {
        nodes.push(h('div', { class: 'empty' },
          h('p', { text: 'Aucune conversation.' }),
          h('span', { text: 'Commencez un nouveau fil depuis le bouton +.' })));
      }
      ctx.setBody(h('div', { class: 'pad' }, ...nodes));
    }

    async function loadConversations() {
      try {
        const data = await api.conversations();
        if (!alive) return;
        conversations = data.conversations || [];
        historyError = null;
      } catch (err) {
        if (!alive) return;
        historyError = err instanceof ApiError && err.status === 0
          ? 'Serveur injoignable.' : 'Conversations indisponibles.';
      }
      renderHistoryList();
    }

    async function showHistory() {
      showingHistory = true;
      ctx.setHeader('Conversations', null, [
        { icon: 'back', label: 'Retour au chat', onClick: showThread },
        { icon: 'plus', label: 'Nouvelle conversation', onClick: beginNewConversation },
      ]);
      ctx.setDock(null);
      ctx.setBody(h('div', { class: 'pad' }, skeleton(4)));
      await loadConversations();
    }

    async function loadConversation(id) {
      if (!Number.isInteger(id)) return;
      activeConversationId = id;
      try {
        const data = await api.conversation(id);
        if (!alive || activeConversationId !== id) return;
        renderMessages(data.messages || []);
        showThread();
      } catch (err) {
        if (!alive || activeConversationId !== id) return;
        showThread();
        jarvis(err instanceof ApiError && err.status === 0
          ? 'Serveur injoignable. Historique non chargé.'
          : 'Historique indisponible.');
      }
    }

    showThread();

    // Suit l'intention de lecture : on ne recolle en bas que si l'utilisateur
    // y était déjà. Sinon une réponse longue arracherait sa lecture.
    const scrollBody = body;
    const onScroll = () => {
      atBottom = scrollBody.scrollHeight - scrollBody.scrollTop - scrollBody.clientHeight < 60;
    };
    scrollBody.addEventListener('scroll', onScroll, { passive: true });

    // ── Flux serveur ──
    const off = [];

    off.push(ws.on('connected', (msg) => {
      activeConversationId = msg.conversation_id;
      if (msg.resumed) void loadConversation(msg.conversation_id);
      else {
        renderMessages([]);
        showThread();
      }
      void loadConversations();
    }));

    off.push(ws.on('conversation_switched', (msg) => {
      void loadConversation(msg.conversation_id);
      void loadConversations();
    }));

    off.push(ws.on('conversation_updated', () => { void loadConversations(); }));
    off.push(ws.on('welcome', (msg) => { if (msg.content) jarvis(msg.content); }));

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
          onClick: () => { ws.cancelAction(action); close('Annulé.'); },
        }, 'Refuser'),
        h('button', {
          class: 'btn primary', type: 'button',
          onClick: () => { ws.confirmAction(action); close('Exécution en cours.'); },
        }, 'Exécuter'),
      ));

      thread.append(card);
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
    const currentId = ws.currentConversationId();
    if (Number.isInteger(currentId)) {
      void loadConversation(currentId);
      void loadConversations();
    }

    return () => {
      alive = false;
      scrollBody.removeEventListener('scroll', onScroll);
      for (const fn of off) fn();
    };
  },
};
