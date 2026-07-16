---
id: integration_validation
version: 2.0.0
date: 2026-07-16
domain: integration
variables: user_request,acceptance_criteria,required_tests,context_files,extra_context,repo_rules,result_format,date,template_version
---

# Validation d'intégration macOS

# Objectif
{{user_request}}

# Contexte
Date: {{date}}
Template version: {{template_version}}
Intégrations macOS de JARVIS : tout osascript passe par `integrations/_applescript.py` ; Mail (`integrations/mail.py`), Calendar (`integrations/calendar_api.py`), Contacts (`integrations/contacts.py`) pilotent les apps natives via AppleScript ; iMessage est en LECTURE SEULE via la façade unique `integrations/apple_data.py` (`chat.db` en mode ro, PRAGMA query_only) — aucun composant ne crée sa propre connexion à `chat.db`.

Contexte JARVIS :
{{extra_context}}

# Symptômes / preuve disponible
- Détermine d'abord si le problème est côté JARVIS (code) ou côté macOS (permissions, app absente) : une erreur AppleScript `-600` signifie app non lancée, un refus d'accès à `~/Library/Messages/chat.db` signifie Full Disk Access manquant, un blocage osascript signifie Automation non accordée.
- Teste avec les permissions RÉELLES de la machine : exécute la commande d'intégration concernée en conditions réelles (petit script d'appel direct de la façade) et cite la sortie exacte. Si une permission manque, le rapport doit le dire — ne pas contourner.
- Vérifie que le composant incriminé passe bien par les façades (`_applescript.py`, `apple_data.py`) : un appel direct `subprocess osascript` ou `sqlite3.connect(chat.db)` hors façade est déjà une anomalie à signaler.

# Périmètre
- Corriger/valider l'intégration via les façades existantes uniquement.
- TIMEOUTS OBLIGATOIRES sur tout appel osascript et subprocess (les AppleScript Mail/Contacts peuvent bloquer 90 s) ; tout appel sans timeout doit en recevoir un.
- Échappement strict des chaînes injectées en AppleScript (`\\`, `"`, retours ligne) — vérifier le helper d'échappement existant, ne pas le réinventer.

# Hors périmètre
- Écrire dans `chat.db` : INTERDIT dans tous les cas (lecture seule absolue).
- Envoyer des iMessages ou emails réels de test à des contacts réels sans demande explicite (utiliser `IMESSAGE_TARGET`/adresses de test si l'envoi doit être vérifié).
- Modifier les réglages de permissions macOS par script ; les permissions se documentent, elles ne se forcent pas.

# Fichiers probables
{{context_files}}
- Façades : `integrations/_applescript.py`, `integrations/apple_data.py` ; clients : `integrations/mail.py`, `integrations/calendar_api.py`, `integrations/contacts.py`, `integrations/imessage.py` (envoi), `integrations/imessage_reader.py` (compat lecture) ; consommateurs : `scripts/email_watcher.py`, `scripts/jarvis_daemon.py`.

# Règles d'architecture
{{repo_rules}}
- Les intégrations échouent proprement : app absente ou permission manquante → log + retour d'erreur structuré, jamais de crash du serveur.

# Critères d'acceptation
{{acceptance_criteria}}
- Chaque scénario d'intégration validé a une sortie réelle citée (succès) ou un diagnostic de permission précis (échec environnemental).
- Aucun nouvel appel sans timeout.

# Tests obligatoires
{{required_tests}}
- Tests unitaires avec osascript/chat.db MOCKÉS pour la logique (parsing, échappement, curseurs) — le vrai I/O reste dans la validation manuelle.
- La suite existante des intégrations touchées reste verte.

# Validation réelle
- Exécuter chaque intégration concernée en conditions réelles sur la machine (Full Disk Access, Automation accordées) et consigner : commande, sortie, durée.
- Cas dégradés : app fermée (relancer Calendar.app réduit les erreurs -600), permission absente — vérifier le message d'erreur structuré plutôt qu'un crash.
- Vérifier l'absence de fuite : aucune connexion résiduelle à `chat.db`, aucun process osascript zombie après timeout.

# Stratégie Git
- Jamais de modification directe de main.
- Travailler uniquement dans le worktree / la branche fournie.
- Commits clairs, séparant façade et consommateurs.

# Format du rapport final (OBLIGATOIRE)
{{result_format}}

## Qualité constante
- Chercher la cause racine (permission ? app ? code ?) avant tout correctif.
- Ne pas masquer le problème : un try/except qui avale une erreur de permission rend le diagnostic impossible.
- Ne rien inventer : chaque comportement macOS affirmé est observé sur la machine, pas supposé.
- Ne pas supprimer une fonction existante sans preuve.
- Respecter les conventions du dépôt ; tester avant chaque commit.
- Comparer avant/après ; préserver les contrats existants (façades, formats de retour).
- Rapport précis ; ne pas déclarer COMPLETED sans preuve (sorties réelles des intégrations).
