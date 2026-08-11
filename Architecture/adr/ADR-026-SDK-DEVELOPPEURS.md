# ADR-026 — SDK Python piloté par le contrat OpenAPI

- **Statut** : accepté et implémenté
- **Date** : 2026-08-11

## Contexte

Un SDK entièrement généré produirait des centaines de wrappers peu lisibles,
tandis qu'un client écrit à la main dupliquerait les chemins et dériverait du
contrat. Les intégrateurs ont surtout besoin d'un transport sûr, d'erreurs
stables et d'une résolution fiable des `operationId`.

## Décision

1. Livrer un package Python 3.10+ sans dépendance runtime.
2. Générer un registre compact des opérations depuis le JSON OpenAPI canonique.
3. Fournir un appel générique typé et quelques flux de cycle de vie, plutôt que
   269 méthodes recopiées.
4. Traduire localement chaque frontière d'authentification et échouer avant le
   réseau si un credential manque.
5. Vérifier TLS par défaut, borner les réponses et ne retenter que les méthodes
   idempotentes.
6. Construire un wheel et exécuter les tests dédiés en CI.

## Conséquences

- Une nouvelle route devient disponible après régénération sans écrire un
  wrapper fragile.
- Les IDE exposent un registre immuable d'objets `Operation`, tandis que les
  payloads métier restent compatibles avec l'évolution des schémas OpenAPI.
- L'ajout futur d'un SDK TypeScript peut réutiliser le même générateur et les
  mêmes extensions de sécurité sans changer le contrat public.
