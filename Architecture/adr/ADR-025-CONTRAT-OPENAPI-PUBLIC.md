# ADR-025 — Contrat OpenAPI public, stable et sécurisé

- **Statut** : accepté et implémenté
- **Date** : 2026-08-11

## Contexte

FastAPI savait générer un schéma interne, mais celui-ci n'était ni publié, ni
versionné, ni vérifié. Il ne décrivait aucune sécurité globale, la majorité des
opérations n'avait pas de tag et les URLs automatiques Swagger/ReDoc avaient été
fermées car elles contournaient historiquement le préfixe protégé `/api/`.

## Décision

1. Générer un OpenAPI 3.1 enrichi depuis les routes réellement montées.
2. Dériver les `operationId` de la méthode et du chemin pour résister aux
   refactorings internes.
3. Traduire la politique du middleware en schémas cookie/CSRF/Bearer/device et
   en exigences par opération.
4. Versionner un JSON canonique et vérifier sa dérive en CI.
5. Servir le schéma et un catalogue HTML autonome uniquement sous `/api/`, donc
   derrière la session ; conserver les trois URLs FastAPI historiques fermées.

## Conséquences

- Les intégrateurs disposent d'un contrat stable et générable sans rendre
  publiques les données ou la topologie de l'instance au runtime.
- Une modification de route devient une décision de compatibilité visible dans
  le diff du JSON.
- Le HTML n'utilise aucun CDN et sa feuille de style inline est autorisée par un
  hash CSP calculé sur le contenu exact.
- Le JSON versionné est volumineux, mais évite qu'un SDK soit généré depuis une
  instance ou un environnement différent de celui validé par la CI.
