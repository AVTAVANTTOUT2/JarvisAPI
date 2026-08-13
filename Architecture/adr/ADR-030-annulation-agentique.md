# ADR-030 — Annulation agentique distincte de l'échec métier

- Statut : accepté
- Date : 2026-08-13

## Contexte

Un timeout d'ACK du runtime agentique classait parfois une annulation utilisateur en
`failed`, ce qui relançait des effets et faussait l'historique.

## Décision

États distincts :

- `cancelling` = `cancel_requested` persisté, aucun nouvel effet ;
- `cancelled` + `cancellation_kind=confirmed` si le provider ACK ;
- `cancelled` + `cancellation_kind=forced` si arrêt forcé du processus détenu
  ou ACK expiré (`AGENTIC_CANCEL_ACK_TIMEOUT_S`) ;
- `provider_unavailable` + `cancellation_kind=provider_lost` si le runtime
  a disparu pendant une annulation qui le nécessitait.

Un timeout d'ACK n'est pas un échec métier. Après `cancelling`/`cancelled`,
tout événement `completed` du provider est ignoré. Idempotent.

## Conséquences

- Pas de commit/push/PR après annulation.
- Réconciliation d'un `cancelling` orphelin → `cancelled` forcé, pas `failed`.
