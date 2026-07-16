---
id: test_creation
version: 2.0.0
date: 2026-07-16
domain: dev
variables: user_request,acceptance_criteria,required_tests,context_files,extra_context,repo_rules,result_format,date,template_version
---

# Création de tests

# Objectif
{{user_request}}

# Contexte
Date: {{date}}
Template version: {{template_version}}
Dépôt : JARVIS — suite pytest dans `tests/`, fixtures partagées dans `tests/conftest.py` : la base SQLite est monkeypatchée vers un `tmp_path` (jamais la vraie `data/jarvis.db`), les threads d'arrière-plan (`database._dispatch_*`) et les daemons du lifespan sont neutralisés.

Contexte JARVIS :
{{extra_context}}

# Symptômes / preuve disponible
- Identifie d'abord ce qui n'est PAS couvert : lis le module cible et liste ses comportements publics, ses branches d'erreur et ses cas limites avant d'écrire le moindre test.
- Lis 2-3 fichiers de tests existants du même domaine pour copier leurs conventions (fixtures utilisées, style de nommage, structure).
- Vérifie qu'un test équivalent n'existe pas déjà (grep du nom de la fonction cible dans `tests/`).

# Périmètre
- pytest uniquement — aucun autre framework, pas d'unittest.TestCase.
- Structure Arrange / Act / Assert dans chaque test ; un concept d'assertion par test.
- Nommage descriptif : `test_<comportement>` (ex. `test_create_task_rejects_empty_title`), pas `test_1` ni `test_ok`.
- Réutiliser les fixtures de `tests/conftest.py` ; si une DB est nécessaire, passer par le pattern existant tmp_path + monkeypatch de `DB_PATH`, jamais la base réelle.
- Couvrir les cas limites : vide, None, unicode, valeurs hors bornes, branches d'exception.

# Hors périmètre
- Aucun I/O réel : pas d'appel LLM réseau, pas d'osascript, pas de lecture de `~/Library`, pas de vraie `data/jarvis.db` — tout est mocké ou monkeypatché.
- Ne pas modifier le code de production pour le rendre testable, sauf ajout minimal d'injection prouvé nécessaire (à signaler dans le rapport).
- Ne pas réécrire les tests existants qui passent.

# Fichiers probables
{{context_files}}
- Nouveaux tests dans `tests/test_<module>.py`, à côté des suites existantes du même domaine.
- Fixtures et neutralisations globales : `tests/conftest.py` (à lire, ne modifier qu'en dernier recours).

# Règles d'architecture
{{repo_rules}}
- Les tests doivent passer dans n'importe quel ordre, sans état mutable partagé entre eux.

# Critères d'acceptation
{{acceptance_criteria}}
- Chaque test échoue si on casse volontairement le comportement qu'il vérifie (vérification par mutation mentale ou réelle).
- La suite complète reste verte.

# Tests obligatoires
{{required_tests}}
- Exécuter les nouveaux tests isolément PUIS la suite complète (`pytest tests/ -q`).

# Validation réelle
- Lancer les nouveaux tests deux fois de suite et dans un ordre différent (`pytest -p no:randomly` absent → utiliser `--lf` puis run complet) pour détecter tout état partagé.
- Vérifier qu'aucun fichier n'est créé hors `tmp_path` pendant la suite (pas d'écriture dans `data/`).
- Confirmer qu'aucun test n'est plus lent que quelques secondes (pas de sleep réel, pas de timeout réseau).

# Stratégie Git
- Jamais de modification directe de main.
- Travailler uniquement dans le worktree / la branche fournie.
- Commits clairs, groupés par module testé.

# Format du rapport final (OBLIGATOIRE)
{{result_format}}

## Qualité constante
- Chercher la cause racine quand un test révèle un bug : le signaler, ne pas ajuster l'assertion pour qu'elle passe.
- Ne pas masquer le problème ; un test qui encode un comportement bogué est pire que pas de test.
- Ne rien inventer : les comportements testés sont ceux du code réel, lus dans le module.
- Ne pas supprimer un test existant sans preuve qu'il est invalide.
- Respecter les conventions du dépôt ; exécuter les tests avant chaque commit.
- Comparer la couverture avant/après ; préserver les contrats existants.
- Rapport précis ; ne pas déclarer COMPLETED sans preuve (sortie pytest complète verte).
