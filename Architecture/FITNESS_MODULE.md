# Module Fitness — trace d'ajout

**Date** : 30 juillet 2026
**Mise à jour** : 31 juillet 2026
**Statut** : module complet, relié aux interfaces, à la voix, aux notifications et au scheduler

## Périmètre

Le module `app/fitness/` gère le programme poids du corps fourni par
l'utilisateur, les séances planifiées et leur progression exercice par
exercice, les repas/protéines/calories, l'eau, la pesée et le bien-être. Le
programme initial (poussée lundi, tirage avec barre mardi, jambes jeudi, full
body vendredi) est amorcé par migration puis intégralement modifiable.

## Propriété et dépendances

- `database/fitness.py` est l'unique propriétaire des lectures et écritures
  des neuf tables Fitness.
- Les tables et leurs index sont créés par la migration idempotente
  `_migrate_fitness()` enregistrée dans `database/migrations.py`, conformément
  au mécanisme appelé par `init_db()`.
- `app/fitness/routes.py` valide les entrées puis délègue à
  `app/fitness/services.py`; aucune logique SQL ne vit dans la couche API.
- `main.py` ne fait qu'enregistrer le nouveau routeur.

### Tables

- Journaux compatibles : `workouts`, `meals`, `water_intake`, `wellbeing_logs`.
- Programme : `fitness_programs`, `fitness_program_sessions`.
- Suivi : `fitness_session_progress`, `fitness_weight_logs`, `fitness_prompt_log`.

Le statut quotidien appartient à `fitness_session_progress` (`planned`,
`in_progress`, `done`, `skipped`). Les rappels interrogent cette table : ils
continuent selon la cadence du programme tant que la séance n'est ni faite ni
explicitement passée.

## Interfaces

- `/fitness` dans le frontend canonique et son fallback Vite : tableau de bord,
  exercices cochables, échauffement, étirements, nutrition, hydratation,
  pesée, conseil IA et éditeur de programme.
- `#/sante` dans `web_mobile/` : même tableau de bord et mêmes écritures via
  les contrats REST, optimisé pour le suivi pendant la séance.
- Les écritures passent toutes par le wrapper réseau authentifié avec CSRF.

## Voix

`app/fitness/voice.py` utilise des expressions déterministes, complètes et
ancrées. Un non-match retourne toujours `None` et laisse intact le routage
cognitif existant. Une quantité d'eau ambiguë déclenche une question de
clarification, jamais une estimation silencieuse. Le texte libre de bien-être
n'est accepté qu'après ouverture explicite du contexte journal.

Les formulations « j'ai fait mon sport », « marque ma séance comme non faite »
et « quel est mon programme du jour » agissent sur la séance planifiée. Les
réponses suivent le contrat vocal existant et sont donc lues par le moteur TTS.

## Proactivité et conseil

`scripts/fitness_reminders.py`, exécuté toutes les 30 minutes par APScheduler,
applique l'heure et la cadence stockées dans `fitness_programs`. Une
notification `high` déclenche Web Push et l'annonce TTS du daemon. Les heures
calmes et le mode DND restent prioritaires. Les questions déjeuner/dîner ne
sont émises qu'une fois par créneau manquant.

`POST /api/fitness/advice` envoie au modèle rapide uniquement un agrégat
fitness de la journée. Un conseil déterministe reste disponible hors ligne ;
aucun diagnostic médical n'est produit.

## Vérification et retour arrière

La suite `tests/test_fitness_*.py` couvre modèles, persistance, routes, programme
initial, modification, progression, cadence/arrêt des rappels, mapping STT
simulé et non-interception. Les données existantes des quatre journaux sont
conservées par la migration.
