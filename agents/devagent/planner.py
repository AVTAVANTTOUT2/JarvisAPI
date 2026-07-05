"""Prompt planning DeepSeek pour la boucle autonome."""

PLANNER_PROMPT = """Role: tech lead.
Spec projet: {spec_json}
Etat actuel: {state_json}
Historique derniere erreur: {last_log}

Genere le plan pour UNE seule tache suivante (pas plus).
Retourne JSON strict:
{{"task": "...", "files_to_create_or_edit": ["..."], "reasoning": "..."}}
"""

ACCEPTANCE_JUDGE_PROMPT = """Role: QA lead.
Spec projet: {spec_json}
Sortie tests: {test_output}
Arborescence src: {file_list}

Les criteres d'acceptation sont-ils satisfaits?
Retourne JSON: {{"done": true|false, "reason": "..."}}
"""
