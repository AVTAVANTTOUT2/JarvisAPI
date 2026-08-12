# Reprise après incident

Au démarrage, JARVIS réconcilie les runs non terminaux et scanne uniquement les
états privés sous `.runtime/runs/`. Un PID n'est arrêté qu'après validation du
propriétaire, du type/mode du fichier d'état, de la commande et du runtime.

- run en file sans session : reprovisionnement autorisé ;
- session fournisseur persistée mais non reconstructible sûrement : état
  `blocked` ou `provider_unavailable`, jamais de répétition automatique ;
- annulation en attente : reprise de l'annulation, pas du travail ;
- effet MCP `pending` après crash : ambiguïté bloquée jusqu'à réconciliation ;
- événement runtime persistant : inbox avec lease/fencing, effet idempotent,
  acquittement après succès ;
- approbation expirée/outbox stale : sweeper et reclaim bornés.

Commandes :

```bash
python -m integrations.opencode.scripts.manager status
python -m integrations.opencode.scripts.manager health
python -m integrations.opencode.scripts.manager clean
```
