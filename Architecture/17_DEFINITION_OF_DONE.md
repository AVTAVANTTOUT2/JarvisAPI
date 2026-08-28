# 17 — Definition of Done

**Revue :** 27 août 2026
**Référence :** `origin/main` @ `6becf26cb3ea4ab47acb1996a2a9125500446ab7`
**Statut :** règle de gouvernance active.

Les checklists et métriques des phases de refactoring de juillet 2026 sont des
preuves historiques. Elles vivent dans
[`06_PLAN_TESTS.md`](./06_PLAN_TESTS.md) et
[`19_VALIDATION_FINALE.md`](./19_VALIDATION_FINALE.md) ; elles ne définissent
plus l’état courant.

## Critères universels

Une livraison n’est terminée que si tous les critères applicables sont prouvés :

- le comportement demandé est implémenté sans élargissement implicite de
  permissions ni effet externe non autorisé ;
- les tests ciblés, la suite pertinente, le lint/typecheck et
  `git diff --check` passent dans l’environnement documenté ;
- tout artefact généré est régénéré depuis sa source puis validé en mode
  `--check` ; aucun compteur générable n’est maintenu à la main ;
- chaque Markdown des racines gouvernées est classé dans le registre de vérité,
  les snapshots portent un bandeau archive/superseded et tous les liens locaux
  sont valides ;
- aucun secret, PII, chemin utilisateur, IP personnelle, identifiant de
  matériel ou capture issue de données réelles n’est publié ;
- les migrations sont idempotentes, la compatibilité et le rollback des données
  sont testés, et aucune base réelle n’est utilisée dans les tests ;
- les files, caches et buffers ont une borne démontrée ; reprise, idempotence,
  dédoublonnage, annulation et nettoyage sont testés pour les tâches longues ;
- les contrats publics modifiés régénèrent OpenAPI et SDK et conservent une
  stratégie de compatibilité explicite ;
- l’observabilité permet de distinguer succès, attente, erreur, annulation et
  état dégradé sans journaliser de charge utile sensible ;
- le rollback est décrit par fichiers, données et procédure de vérification.

## Nature des preuves

| Type | Ce qu’il autorise à déclarer | Ce qu’il n’autorise pas |
|---|---|---|
| Test automatisé | invariant déterministe couvert par le test cité | qualité humaine, matériel, signature ou endurance |
| Build/lint statique | assemblage et contrat de compilation | parcours utilisateur réel |
| Validation matérielle | résultat exact sur appareil/Mac identifié dans un artefact privé expurgé | généralisation à tous les appareils |
| Campagne prolongée | stabilité pendant la durée réellement archivée | campagne 24 h si l’artefact couvre moins |
| Snapshot historique | état à sa date et son SHA | état actuel |

Une case ou un texte ancien ne vaut jamais preuve rétroactive. Si la preuve
n’existe pas, le registre conserve `PARTIAL` ou
`IMPLEMENTED_NEEDS_REAL_VALIDATION`.

## Portes par surface

### Backend et données

- auth, CSRF/origine et autorisations échouent fermés ;
- concurrence, transactions, timeouts et erreurs sont couverts ;
- le schéma frais et les migrations sont rejoués en mémoire ;
- logs et réponses publiques sont expurgés.

### Frontend, Android et macOS

- chargement, vide, succès, erreur, offline et révocation sont visibles ;
- aucune donnée privée n’est montée avant l’authentification ;
- accessibilité, reprise réseau et annulation sont couvertes ;
- les validations appareil, signature et notarisation restent ouvertes tant
  que leurs artefacts n’existent pas.

### Audio et tâches longues

- permission refusée, périphérique absent, crash et reconnexion sont gérés ;
- aucun repli cloud silencieux ;
- les scénarios 1/30/180 minutes, la RAM bornée et l’absence de doublons sont
  requis avant de déclarer les enregistrements longs terminés ;
- une campagne 24 h n’est déclarée qu’avec son artefact daté.

### Agentique et effets externes

- plan et permissions exactes sont affichés avant démarrage ;
- chaque effet sensible possède son approbation propre, non rejouable ;
- reprise après crash, annulation, rapport final et démontabilité du provider
  sont prouvés ;
- aucune fusion, publication, envoi ou paiement sans autorité explicite.

## Porte de release

La release exige en plus : statut P0 fermé, candidat identifié par SHA, builds
reproductibles, scans PII/secrets, validation physique applicable, signature,
notarisation le cas échéant, notes, hashes, rollback testé et approbation
humaine. Un build unsigned ou une checklist non signée reste un candidat
technique, jamais une release distribuable.
