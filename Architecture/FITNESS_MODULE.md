# Module Fitness — trace d'ajout

**Date** : 30 juillet 2026
**Statut** : module additif, sans nouvelle décision d'architecture

## Périmètre

Le module `app/fitness/` ajoute le suivi des séances, repas, apports d'eau et
du bien-être (note de 1 à 10 et/ou journal libre). Il expose neuf opérations
sous `/api/fitness`, consommées par le pipeline
vocal existant.

## Propriété et dépendances

- `database/fitness.py` est l'unique propriétaire des lectures et écritures
  dans `workouts`, `meals`, `water_intake` et `wellbeing_logs`.
- Les quatre tables et leurs index sont créés par la migration idempotente
  `_migrate_fitness()` enregistrée dans `database/migrations.py`, conformément
  au mécanisme appelé par `init_db()`.
- `app/fitness/routes.py` valide les entrées puis délègue à
  `app/fitness/services.py`; aucune logique SQL ne vit dans la couche API.
- `main.py` ne fait qu'enregistrer le nouveau routeur.

## Voix

`app/fitness/voice.py` utilise des expressions déterministes, complètes et
ancrées. Un non-match retourne toujours `None` et laisse intact le routage
cognitif existant. Une quantité d'eau ambiguë déclenche une question de
clarification, jamais une estimation silencieuse. Le texte libre de bien-être
n'est accepté qu'après ouverture explicite du contexte journal.

Les réponses produites suivent le contrat vocal existant et sont donc lues par
le moteur TTS déjà configuré, sans nouvelle intégration audio.

## Vérification et retour arrière

La suite `tests/test_fitness_*.py` couvre modèles, persistance, routes, mapping
STT simulé et non-interception. Le retrait est additif : démonter le routeur et
le crochet vocal, puis supprimer les nouveaux modules. Les tables peuvent être
conservées pour préserver l'historique ou exportées avant suppression manuelle.
