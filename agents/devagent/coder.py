"""Prompt generation code DeepSeek pour la boucle autonome."""

CODER_PROMPT = """Role: developpeur senior.
Tache: {task}
Fichiers concernes: {files}
Contenu fichiers existants: {existing_content}
Contraintes projet: {constraints}

Genere du code complet, production-ready, fichiers entiers (pas de diff).
Retourne JSON:
{{"files": {{"chemin/fichier.py": "contenu complet"}}, "test_command": "commande test a executer"}}
"""

FIXER_PROMPT = """Role: developpeur senior.
Tache en cours: {task}
Erreur test:
{error}

Fichiers concernes: {files}
Contenu actuel: {existing_content}

Corrige le code. Retourne JSON:
{{"files": {{"chemin/fichier.py": "contenu corrige"}}, "test_command": "commande test"}}
"""
