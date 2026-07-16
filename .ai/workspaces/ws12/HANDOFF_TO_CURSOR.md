# HANDOFF → Cursor (backend) — validation frontend canonique 16/07/2026

Deux anomalies P3 côté backend. Les constats proxy 403 / SSE bufferisé ont été
corrigés dans `supervisor.py` (routage uniquement). Le complément Auth/chat
frontend est clos (voir `Architecture/33_…` + `artifacts/complement_report.json`).

---

## CUR-01 — Dashboard : compteurs « Dernières 24h » à zéro malgré activité récente

- **Sévérité** : P3
- **Route** : `/dashboard`
- **Endpoint** : `GET /api/stats/weekly?days=7`
- **Transport** : REST (cookie session)
- **Payload envoyé** : aucun (query `days=7`)
- **Réponse reçue** : série `days` dont l'entrée du jour donne `messages: 0`,
  `interactions: 0`, alors que des messages user/assistant ont été créés dans
  l'heure (conversation 51-52, 16/07 00:35). Le widget « Contacts Actifs : 329 »
  est correct.
- **Comportement attendu** : l'entrée du jour (ou la fenêtre glissante 24 h)
  reflète les messages du jour même.
- **Erreur console** : aucune.
- **Fichiers probablement concernés** : `api/router_stats.py` (ou équivalent
  stats weekly), requête SQL sur `messages.created_at` — vérifier le fuseau
  (`created_at` stocké en UTC vs journée locale Europe/Paris) et l'exclusion
  éventuelle du jour courant.
- **Reproduction minimale** :
  1. Envoyer un message chat (WS `/ws`) et vérifier `messages.created_at`.
  2. `GET /api/stats/weekly?days=7` → comparer `days[-1].messages` avec
     `SELECT COUNT(*) FROM messages WHERE date(created_at, 'localtime') = date('now', 'localtime')`.

---

## CUR-02 — `PATCH /api/conversations/{id}` sur id inexistant → 200

- **Sévérité** : P3
- **Route** : `PATCH /api/conversations/{id}`
- **Transport** : REST (cookie session, via supervisor `:9000` ou backend direct)
- **Payload envoyé** : `{"title":"x"}` avec `id=99999999`
- **Réponse reçue** : `200 {"ok": true}` (aucune ligne mise à jour)
- **Comportement attendu** : `404` si la conversation n'existe pas (contrat REST
  honnête pour le client — différencie « OK » et « cible absente »)
- **Erreur console** : aucune
- **Fichiers probablement concernés** : `api/router_conversations.py` +
  `database/conversations.py` (`update_conversation`) — vérifier le
  `rowcount` / existence avant de renvoyer `{ok:true}`
- **Reproduction minimale** :
  1. Session authentifiée.
  2. `PATCH /api/conversations/99999999` avec `{"title":"x"}`.
  3. Observer 200 au lieu de 404.

---

*Notes* : VAL-02 (403 `csrf_check_failed` via proxy) est résolu en conservant le
`Host` du navigateur dans le proxy supervisor — aucun changement requis dans
`api/middleware.py`. Si le backend souhaite durcir, ajouter un test contractuel
« proxy 9000 → écritures 200 » dans `tests/test_supervisor_frontend.py`.
