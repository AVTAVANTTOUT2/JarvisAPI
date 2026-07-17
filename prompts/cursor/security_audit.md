---
id: security_audit
version: 2.0.0
date: 2026-07-16
domain: dev
variables: user_request,acceptance_criteria,required_tests,context_files,extra_context,repo_rules,result_format,date,template_version
---

# Audit de sécurité

# Objectif
{{user_request}}

# Contexte
Date: {{date}}
Template version: {{template_version}}
Dépôt : JARVIS — application mono-utilisateur exposée sur LAN/Tailscale. Auth fail-closed par PIN/passphrase (`auth.py`, sessions hashées SHA-256), verrou de session appliqué par `api/middleware.py` sur `/api/*` avec allowlist (`/api/auth/*`, ingestion device/location). Données très sensibles : iMessage, mails, GPS, journal intime.

Contexte JARVIS :
{{extra_context}}

# Symptômes / preuve disponible
- Dérouler la checklist OWASP Top 10 explicitement (A01 → A10) et noter pour chaque item : vérifié / vulnérable / non applicable, avec le fichier et la ligne en preuve.
- Secrets en clair : chercher clés API, tokens, mots de passe hardcodés hors `.env` (grep de motifs `sk-`, `api_key =`, `token =`, chaînes base64 longues) ; vérifier que `.env` et `credentials/` sont bien gitignorés.
- Injection SQL : le dépôt utilise sqlite3 avec requêtes paramétrées — VÉRIFIER que c'est vrai partout : grep des f-strings et concaténations dans les `execute(` de `database/` et des scripts ; toute interpolation de valeur utilisateur est une faille, l'interpolation de noms de colonnes contrôlés est à examiner au cas par cas.
- Endpoints sans verrou de session : croiser la liste des routes (`api/router_*.py`) avec l'allowlist de `api/middleware.py` ; chaque route hors allowlist non couverte par le verrou est une trouvaille.

# Périmètre
- REPORT-ONLY PAR DÉFAUT : l'audit produit un rapport classé par sévérité (critique / haute / moyenne / basse), avec preuve et exploitation possible pour chaque trouvaille.
- Correctifs appliqués uniquement s'ils sont triviaux et sans risque (paramétrisation d'une requête, ajout d'une route à la protection) ET couverts par un test.
- Les correctifs risqués (changement d'auth, de sessions, de middleware, de CORS) vont dans le rapport avec un patch proposé, PAS dans le code.

# Hors périmètre
- Rotation de secrets, modification de `.env`, révocation de sessions actives.
- Refactoring de l'architecture d'auth.
- Scan réseau actif ou test d'intrusion contre des services tiers.

# Fichiers probables
{{context_files}}
- `auth.py` (hash scrypt, anti-brute-force), `api/middleware.py` (verrou de session, en-têtes CSP/HSTS, vérif Origin), `api/router_*.py` (surfaces exposées), `database/*.py` (requêtes SQL), `integrations/computer.py` et `integrations/code_executor.py` (exécution de commandes — motifs dangereux), `push.py` (crypto Web Push).

# Règles d'architecture
{{repo_rules}}
- Ne jamais copier de valeur de secret dans le rapport (citer le fichier et le motif, jamais la valeur).

# Critères d'acceptation
{{acceptance_criteria}}
- Checklist OWASP complète avec verdict et preuve par item.
- Chaque trouvaille a : sévérité, localisation exacte, scénario d'exploitation, correctif proposé.

# Tests obligatoires
{{required_tests}}
- Si un correctif trivial est appliqué : test prouvant que la faille est fermée (ex. requête injectée neutralisée, endpoint refusant l'accès sans session).

# Validation réelle
- Pour chaque trouvaille critique/haute : démonstration concrète (requête curl sans cookie de session, payload d'injection sur la fonction incriminée en test isolé).
- Après correctif : rejouer la démonstration et constater l'échec de l'exploitation.
- Vérifier que la suite de tests auth/middleware existante reste verte.

# Stratégie Git
- Jamais de modification directe de main.
- Travailler uniquement dans le worktree / la branche fournie.
- Commits clairs, un par correctif trivial ; le rapport d'audit n'écrase aucun fichier existant.

# Format du rapport final (OBLIGATOIRE)
{{result_format}}

## Qualité constante
- Chercher la cause racine de chaque faiblesse (défaut de conception vs oubli ponctuel).
- Ne pas masquer le problème : une faille non corrigée doit rester visible dans le rapport, jamais minimisée.
- Ne rien inventer : pas de vulnérabilité théorique sans preuve dans CE code.
- Ne pas supprimer une fonction existante sans preuve.
- Respecter les conventions du dépôt ; tester avant chaque commit.
- Comparer avant/après pour tout correctif ; préserver les contrats existants.
- Rapport précis ; ne pas déclarer COMPLETED sans preuve (checklist + démonstrations).
