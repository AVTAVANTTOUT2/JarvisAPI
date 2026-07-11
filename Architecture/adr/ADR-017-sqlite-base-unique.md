# ADR-0002 : SQLite comme base de données unique

**Date** : 2026-07-11 (rétroactif — décision prise au démarrage du projet)
**Statut** : Accepté

## Contexte

Jarvis stocke des données structurées variées : conversations, contacts, tâches, faits utilisateur, emails, lieux, analytics, logs. Le système cible un utilisateur unique sur une machine locale.

## Décision

SQLite est la seule base de données du projet. Fichier unique : `data/jarvis.db`. 26+ tables. Schéma documenté dans `database/schema.sql`. Accès via `database/__init__.py`.

## Alternatives considérées

| Alternative | Avantages | Inconvénients | Raison du rejet |
|---|---|---|---|
| PostgreSQL | Concurrent writes, extensions, JSON avancé | Serveur séparé, overhead mémoire, complexité ops | Surdimensionné pour utilisateur unique |
| Supabase / Firebase | Cloud-native, temps réel, auth intégrée | Dépendance cloud, coût, données hors machine | Viole Privacy First et Local First |
| DuckDB | Analytics performantes, colonnar | Pas adapté OLTP, communauté plus petite | Jarvis est OLTP-first |
| Fichiers JSON | Simple, lisible | Pas de requêtes, pas d'index, pas ACID | Ne scale pas au-delà de quelques fichiers |

## Conséquences

### Positives
- Zéro serveur, zéro configuration réseau
- Backup = copier un fichier
- Performances excellentes en lecture pour un utilisateur unique
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
