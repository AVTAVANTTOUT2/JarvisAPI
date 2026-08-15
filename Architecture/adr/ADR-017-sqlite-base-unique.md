# ADR-0002 : SQLite comme moteur de données unique

**Date** : 2026-07-11 (rétroactif — décision prise au démarrage du projet)
**Statut** : Accepté

## Contexte

Jarvis stocke des données structurées variées : conversations, contacts, tâches, faits utilisateur, emails, lieux, analytics et logs. Le système reste local à une machine, avec plusieurs profils isolés depuis ADR-023.

## Décision

SQLite est le seul moteur de données du projet. Le profil historique utilise
`data/jarvis.db` et chaque profil additionnel un fichier sous
`data/profiles/<profile_id>/jarvis.db`. Tous rejouent exactement le même schéma
et les mêmes migrations ; aucune seconde technologie de persistance n'est
introduite.
Runtime SQLite canonique : **106 tables persistantes**, **111 tables physiques avec FTS5**, schéma généré : **113 déclarations de tables**.
Le miroir `database/schema.sql` n’est pas exécuté au runtime : il est régénéré depuis
`database/schema.py`, `database/migrations.py` et `database/devagent.py`, puis comparé
en CI. Les connexions vivent dans `database/core.py` et l'API compatible dans
`database/__init__.py`.

## Alternatives considérées

| Alternative | Avantages | Inconvénients | Raison du rejet |
|---|---|---|---|
| PostgreSQL | Concurrent writes, extensions, JSON avancé | Serveur séparé, overhead mémoire, complexité ops | Surdimensionné pour une installation locale |
| Supabase / Firebase | Cloud-native, temps réel, auth intégrée | Dépendance cloud, coût, données hors machine | Viole Privacy First et Local First |
| DuckDB | Analytics performantes, colonnar | Pas adapté OLTP, communauté plus petite | Jarvis est OLTP-first |
| Fichiers JSON | Simple, lisible | Pas de requêtes, pas d'index, pas ACID | Ne scale pas au-delà de quelques fichiers |

## Conséquences

### Positives
- Zéro serveur, zéro configuration réseau
- Backup = copier le fichier du profil concerné
- Performances excellentes en lecture pour chaque profil
- FTS5 intégré pour la recherche full-text
- Portable et inspectable (`sqlite3 jarvis.db`)
- ACID par défaut

### Négatives
- Pas de concurrent writes (un seul processus devrait écrire)
- Pas de types JSON natifs avancés (stockage TEXT)
- Schéma rigide (migrations manuelles)
- Limite taille blob pour les embeddings

### Risques
- Corruption fichier en cas de crash OS pendant écriture (mitigé par WAL mode)
- Performance dégradée si >1M rows par table (non atteint)
