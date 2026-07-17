---
id: refactor_safe
version: 2.0.0
date: 2026-07-16
domain: dev
variables: user_request,acceptance_criteria,required_tests,context_files,extra_context,repo_rules,result_format,date,template_version
---

# Refactoring sûr à comportement constant

# Objectif
{{user_request}}

# Contexte
Date: {{date}}
Template version: {{template_version}}
Dépôt : JARVIS — la Phase 4 impose des frontières structurelles : 12 routeurs `api/router_*.py`, tous les modules `api/*.py` sous 500 lignes, aucun import de `main.py` depuis `api/`. Ces frontières sont verrouillées par `tests/test_phase4_architecture.py` et `tests/test_phase4_route_contract.py`.

Contexte JARVIS :
{{extra_context}}

# Symptômes / preuve disponible
- Le comportement doit être inchangé et PROUVÉ inchangé : exécute la suite de tests pertinente AVANT de toucher au code et sauvegarde la sortie (nombre de tests, durée) comme baseline.
- Si la zone à refactorer n'est pas couverte par des tests, écris d'abord des tests de caractérisation qui figent le comportement actuel — puis seulement refactore.
- Cartographie les consommateurs : pour chaque symbole public que tu comptes déplacer ou renommer, grep tous ses usages (imports, réexports `database/__init__.py`, appels dynamiques).

# Périmètre
- Étapes atomiques, chacune committée séparément et laissant la suite verte : extraire, puis déplacer, puis renommer — jamais les trois dans un commit.
- Tout renommage ou déplacement de symbole public inclut la mise à jour de TOUS ses consommateurs dans le même commit (ou un alias de réexport temporaire, signalé dans le rapport).
- Garder les modules `api/*.py` sous 500 lignes ; si un fichier approche la limite, extraire vers un module dédié du même domaine.

# Hors périmètre
- Tout changement de comportement observable : signatures publiques modifiées sans mise à jour des consommateurs, réponses HTTP différentes, ordre d'événements modifié.
- Corrections de bugs découverts en route : les signaler dans le rapport, ne pas les mélanger au refactoring.
- « Améliorations » de style hors de la zone demandée.

# Fichiers probables
{{context_files}}
- Attention aux façades de réexport : `database/__init__.py` réexporte les helpers des sous-modules ; un déplacement doit préserver les imports existants.
- Les tests d'architecture (`tests/test_phase4_*.py`) définissent les frontières à ne pas franchir.

# Règles d'architecture
{{repo_rules}}
- Aucune dépendance circulaire introduite ; `api/` n'importe jamais `main.py`.

# Critères d'acceptation
{{acceptance_criteria}}
- La suite AVANT et la suite APRÈS passent avec le même nombre de tests collectés (aucun test perdu en route).
- Aucun contrat public modifié sans mise à jour synchrone des consommateurs.

# Tests obligatoires
{{required_tests}}
- Exécuter la même commande pytest avant le premier commit et après le dernier ; joindre les deux sorties au rapport.
- `tests/test_phase4_architecture.py` et `tests/test_phase4_route_contract.py` si `api/` est touché.

# Validation réelle
- Diff de comportement : pour au moins un chemin critique traversant le code refactoré, comparer la sortie exacte avant/après (réponse d'endpoint via TestClient, retour de fonction sur entrées fixes).
- Vérifier `wc -l` des modules `api/*.py` touchés (< 500 lignes).
- Grep final des anciens noms de symboles : zéro occurrence résiduelle (hors changelog/rapport).

# Stratégie Git
- Jamais de modification directe de main.
- Travailler uniquement dans le worktree / la branche fournie.
- Un commit par étape atomique de refactoring, message décrivant la transformation (« extrait X vers Y », « renomme A en B + consommateurs »).

# Format du rapport final (OBLIGATOIRE)
{{result_format}}

## Qualité constante
- Chercher la cause racine de la dette visée par le refactoring, pas un déplacement cosmétique.
- Ne pas masquer le problème : un test qui casse pendant le refactoring révèle un couplage — le comprendre avant de l'adapter.
- Ne rien inventer ; ne pas supprimer une fonction existante sans preuve (grep des usages) qu'elle est morte.
- Respecter les conventions du dépôt ; tester avant CHAQUE commit, pas seulement à la fin.
- Comparer avant/après systématiquement ; préserver les contrats existants.
- Rapport précis ; ne pas déclarer COMPLETED sans preuve (suites identiques vertes avant/après).
