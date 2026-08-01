# Audit de cybersécurité — JARVIS API (août 2026)

Revue de code manuelle de l'ensemble du dépôt (411 fichiers Python, backend
FastAPI, trois arbres frontend, serveur TV, agent distant, companion Android).
Portée : authentification, autorisation, exécution de commandes, injection,
traversée de chemin, cryptographie, frontière LLM, exposition réseau.

**Ce n'est pas un pentest.** Aucune instance en fonctionnement n'a été
attaquée ; les dépendances n'étaient pas installées dans l'environnement
d'audit, donc la suite de tests n'a pas pu être exécutée. Les conclusions
portent sur le code tel qu'il est écrit.

## Résumé

Une vulnérabilité confirmée (sévérité moyenne), plus cinq points de
durcissement de sévérité faible. La posture générale est nettement au-dessus
de ce qu'on rencontre habituellement sur un projet personnel de cette taille :
les classes de vulnérabilités classiques (injection SQL, injection de
commande, désérialisation, traversée de chemin, XSS) sont systématiquement
traitées, pas ponctuellement.

| # | Sévérité | Finding | Statut |
|---|---|---|---|
| 1 | **Moyenne** | `git` échappe au workspace « isolé » du shell LLM | Confirmé de bout en bout |
| 2 | Faible | Le listener WebSocket du serveur TV n'est pas authentifié (intégration morte) | Confirmé par lecture |
| 3 | Faible | Interpolation HTML non échappée dans les widgets TV | Non exploitable aujourd'hui |
| 4 | Faible | `open_app` sans allowlist d'applications | Derrière un opt-in |
| 5 | Faible | Aucun scan de dépendances / SAST en CI | Absence de contrôle |
| 6 | Info | `verify=False` dans le health check TV | Latent |

---

## 1. `git` échappe au workspace « isolé » du shell LLM — Moyenne

**Fichier :** `integrations/shell_safety.py` — `_safe_environment()` (l. 414-428),
allowlist `_COMMAND_CAPABILITIES` (l. 70), `_ALLOWED_GIT_SUBCOMMANDS` (l. 87)

### Le problème

`git` est allowlisté avec les sous-commandes `diff`, `log`, `rev-parse`,
`show`, `status`. Le plan s'exécute avec `cwd=<workspace>`, où le workspace
est `data/llm_shell_workspace/<plan_id>/` — c'est-à-dire **à l'intérieur du
dépôt JARVIS** (`config.BASE_DIR / "data" / "llm_shell_workspace"`, l. 214).

`_safe_environment()` neutralise bien `GIT_CONFIG_GLOBAL`, `GIT_CONFIG_NOSYSTEM`
et `GIT_PAGER`, mais ne pose **ni `GIT_CEILING_DIRECTORIES` ni `GIT_DIR`**. Git
remonte donc l'arborescence, découvre `<repo>/.git`, et opère sur le dépôt
JARVIS réel au lieu du workspace vide.

### Vérification

Analyse statique — ces commandes passent toutes `analyze_command()` :

```
ALLOWED  'git show HEAD:config.py'                risk=low
ALLOWED  'git show HEAD:api/middleware.py'        risk=low
ALLOWED  'git log -p'                             risk=low
ALLOWED  'git status'                             risk=low
ALLOWED  'git show HEAD:auth.py'                  risk=low
BLOCKED  'git show HEAD:.env'                     -> accès à un chemin sensible interdit
```

Exécution réelle depuis un workspace, avec exactement l'environnement de
`_safe_environment()` :

```
$ cd data/llm_shell_workspace/<plan_id>
$ env -i PATH=... HOME=$PWD GIT_CONFIG_GLOBAL=/dev/null GIT_CONFIG_NOSYSTEM=1 \
      git show HEAD:auth.py
"""Verrouillage de l'application — PIN/passphrase, sessions, anti-brute-force.
...
```

Le contenu intégral de `auth.py` est retourné.

### Impact

Depuis un workspace annoncé comme isolé, une commande confirmée peut lire
**tout le code source du projet et tout l'historique des commits**
(`git log -p`). Le filtrage `.env` de `_SENSITIVE_PATH_PARTS` ne constitue pas
une défense réelle : `.env` est gitignoré, donc jamais dans git de toute façon ;
en revanche, tout secret ayant été commité puis retiré reste accessible via
`git log -p`, sans jamais toucher au token `.env`.

Le point le plus gênant n'est pas la lecture en elle-même — c'est que le plan
présenté à l'utilisateur pour confirmation affiche :

```json
"impact_analysis": {
  "max_risk": "low",
  "secret_access": false,
  "isolation": "dedicated_workspace"
}
```

Ces trois assertions sont fausses pour toute commande `git`. La confirmation
humaine est la garantie centrale de cette architecture ; elle ne vaut que si
l'analyse d'impact affichée est exacte. Ici, l'utilisateur confirme une
« inspection git à faible risque dans un workspace dédié » et obtient une
lecture du dépôt.

Une exécution reste nécessaire côté attaquant : soit l'utilisateur confirme
un plan proposé par un LLM sous injection de prompt (le contenu non fiable —
mails, iMessages — atteint bien le pipeline), soit il confirme sans lire. Le
gain est en confidentialité, pas en exécution de code.

### Correctif recommandé

Ajouter le plafond de découverte dans `_safe_environment()`. Vérifié :

```python
def _safe_environment(workspace: Path) -> dict[str, str]:
    ...
    return {
        ...
        "GIT_CONFIG_GLOBAL": "/dev/null",
        "GIT_CONFIG_NOSYSTEM": "1",
        # Empêche git de remonter au-dessus du workspace et de découvrir le
        # dépôt JARVIS lui-même — l'isolation annoncée dans impact_analysis.
        "GIT_CEILING_DIRECTORIES": str(workspace.parent),
    }
```

Le plafond doit être le **parent** du workspace, pas le workspace lui-même :
git ne considère pas les répertoires plafonds eux-mêmes, donc
`GIT_CEILING_DIRECTORIES=<workspace>` ne bloque rien (testé — la découverte
réussit toujours). Avec le parent, un dépôt git légitimement créé *dans* le
workspace continue de fonctionner :

```
ceiling=<workspace>        → git log réussit (échappement toujours possible)
ceiling=<workspace.parent> → fatal: not a git repository   ✓
```

Deux durcissements complémentaires, indépendants :

- déplacer `LLM_SHELL_WORKSPACE` hors de l'arborescence du dépôt par défaut ;
- ajouter à `tests/test_shell_safety.py` un cas qui exécute `git status` dans
  un workspace imbriqué dans un dépôt git de test et vérifie l'échec. Les
  tests actuels ne couvrent que `analyze_command()`, ce qui explique que le
  problème soit passé : `git status --short` et `git diff -- .` figurent dans
  `test_allowlisted_capabilities_are_analyzed` comme cas *attendus valides*.

---

## 2. Le listener WebSocket du serveur TV n'est pas authentifié — Faible

**Fichier :** `tv/server.py` — `_ws_listener()` (l. 88-137)

`_ws_listener` se connecte à `ws://{BACKEND_HOST}:{BACKEND_PORT}/ws` sans
cookie de session ni Bearer. Or `api/ws_handler.py` (l. 41-44) ferme toute
connexion non authentifiée avec le code 4401.

Conséquence : la boucle ne reçoit jamais d'événement, se reconnecte toutes les
5 secondes indéfiniment, et l'overlay vocal SSE de la TV (`/api/events`) ne
diffuse que des heartbeats. Ce n'est pas une faille — c'est une intégration
morte qui *ressemble* à une intégration vivante, plus du bruit de log continu.

À corriger en présentant un jeton (le canal supervisor ou un jeton device
dédié), ou à retirer si la fonctionnalité est abandonnée.

---

## 3. Interpolation HTML non échappée dans les widgets TV — Faible

**Fichiers :** `tv/static/js/notifications.js` (l. 9-10),
`tv/static/js/tasks.js` (l. 10)

```js
h+='<div class="notif-item '+p+'">';        // p = n.priority, non échappé
h+='<span class="task-dot '+t.priority+'"></span>';
```

Le reste de ces widgets passe correctement par `TV.esc()`. Ces trois
interpolations d'attribut ne le font pas.

**Non exploitable en l'état** : `priority` est contraint côté base
(`CHECK(priority IN ('urgent','high','medium','low'))`, `schema.sql` l. 110 et
155) et normalisé par `notification_service._normalize_priority()`. La sécurité
repose donc entièrement sur une contrainte située à deux couches de distance.
Passer ces valeurs par `TV.esc()` coûte une ligne et supprime la dépendance.

`automations.js` interpole également `color` et `dc` sans échappement, mais les
deux proviennent de tables de correspondance codées en dur avec valeur de repli
— aucune donnée externe n'y entre. Rien à corriger.

---

## 4. `open_app` sans allowlist d'applications — Faible

**Fichiers :** `actions.py` — `_action_open_app()` (l. 467-477),
`integrations/computer.py` — `open_app()` (l. 175-178)

`open_app` n'est pas dans le flux de confirmation : un bloc ```action``` émis
par le LLM lance directement `open -a <nom>`, sans allowlist. L'exécution passe
par argv sans shell, et `open -a` ne prend pas d'argument fichier — la portée
se limite donc au lancement d'une application installée, sans passage de
paramètres.

Atténué par `COMPUTER_ACCESS=false` par défaut. Une allowlist d'applications
alignerait cette action sur le traitement — nettement plus strict — réservé à
`terminal`.

---

## 5. Aucun scan de dépendances ni SAST en CI — Faible

`.github/workflows/ci.yml` ne contient ni `pip-audit`, ni `bandit`, ni
`safety`, ni CodeQL, ni `npm/pnpm audit`. L'arbre de dépendances est lourd
(torch, spacy, open-interpreter, faster-whisper, pymupdf, cryptography) et
épinglé en plages (`==0.115.*`), pas par hash.

`scripts/security_audit.py` existe et couvre des motifs internes, mais aucun
contrôle ne porte sur les CVE des dépendances tierces. Ajouter un job
`pip-audit` est peu coûteux et couvre l'angle mort le plus probable de ce
projet.

---

## 6. `verify=False` dans le health check TV — Info

**Fichier :** `tv/server.py` — `_check_backend_health()` (l. 273)

```python
async with httpx.AsyncClient(verify=False, timeout=3.0) as client:
```

Sans effet aujourd'hui : `BACKEND_BASE_URL` est construit en `http://`
(`tv/config.py` l. 39). Le drapeau devient une vraie faiblesse dès que le
backend passe en TLS ou que `BACKEND_HOST` désigne une machine distante
(Tailscale). À retirer tant qu'il ne sert à rien.

---

## Ce qui a été vérifié et tient

Ces points ont été examinés activement, pas supposés corrects.

**Authentification et sessions** — `scrypt` (N=2^14) avec sel par entrée ;
jetons de session opaques dont seul le SHA-256 est persisté ; verrou
anti-brute-force par client haché avec délai progressif exponentiel, plus un
plafond global, le tout incrémenté sous `BEGIN IMMEDIATE` (pas de perte
d'incrément en concurrence) ; fail-closed en 428 tant qu'aucun secret n'est
configuré. La récupération locale exige boucle locale **et** en-tête explicite.

**Middleware** — verrou de session sur tout `/api/*`, allowlist de contournement
courte et explicite ; chaque route contournante s'authentifie autrement
(jeton device, jeton localisation, code de pairage, Bearer mobile) — les huit
routes `/api/mobile/*` concernées ont été vérifiées une par une, toutes
appellent `_require_mobile_device()` ou consomment un code à usage unique. Les
en-têtes de sécurité sont appliqués **hors** du verrou, donc aussi aux réponses
401/403/428 — détail souvent manqué.

**CSRF** — jeton synchronisé lié à la session **et** origine exacte
(schéma + hôte + port). Pas de comparaison par suffixe. Le contrôle ne
s'applique qu'aux mutations portées par cookie, correctement exclu pour les
clients Bearer natifs.

**WebSocket** — `resolve_websocket_auth()` exige l'origine exacte du handshake
pour l'authentification par cookie (protection CSWSH), jamais de jeton en
query string.

**Injection SQL** — toutes les requêtes dynamiques trouvées
(`location_helpers`, `conversations`, `people`, `migrations`) construisent
leurs noms de colonnes depuis des allowlists figées ou des tuples codés en
dur ; les valeurs sont toujours paramétrées. Aucune injection trouvée.

**Exécution de commandes** — aucun `shell=True`, aucun `os.system`, aucun
`eval`/`exec`, aucun `pickle.load` dans le code applicatif. Tout passe par
argv.

**AppleScript** — échappement centralisé dans `escape_applescript_string()`
(antislash avant guillemet, ordre correct), destinataires iMessage validés par
regex avant insertion. Un `\r` brut n'est pas échappé mais ne permet pas
d'injection : sans guillemet non échappé, la chaîne ne peut pas être fermée —
au pire une erreur de syntaxe.

**Traversée de chemin** — `resolve()` + `relative_to()` appliqués de façon
cohérente sur les sorties scolaires, la restauration de sauvegarde, les
statiques bureau et mobile, et les uploads.

**Uploads** — noms internes en UUID (nom d'origine jamais utilisé comme
chemin), validation par signature de fichier (magic bytes) et non par MIME
déclaré, plafond de taille appliqué pendant l'écriture par blocs, quota disque,
`O_NOFOLLOW`, mode 0600 dès la création. C'est la partie la plus soignée du
dépôt.

**Frontière LLM** — `wrap_untrusted_data()` est appliqué sur ~50 sites
couvrant mails, iMessage, transcriptions, historique de conversation,
timelines, événements daemon ; `UNTRUSTED_DATA_SYSTEM_RULE` est injecté dans
les prompts système correspondants. Les résultats d'action passent par une
allowlist de champs avec plafonds par champ, jamais de dump générique ; le
presse-papiers est explicitement exclu du cloud. C'est une atténuation au
niveau du prompt, pas une garantie — la défense réelle reste la confirmation
humaine sur les actions à fort impact, et elle est en place (`mail` ne renvoie
qu'un brouillon, `terminal` exige un plan serveur confirmé).

**Confirmation d'action** — l'action est stockée côté serveur (le client ne
peut pas la modifier entre proposition et exécution), liée au couple
session + conversation, à usage unique, avec TTL, et toute réponse qui n'est
pas une confirmation exacte révoque la proposition. Correctement fail-closed.

**Cryptographie** — Fernet avec PBKDF2-HMAC-SHA256 à 600 000 itérations
(conforme aux recommandations OWASP actuelles), sel aléatoire de 16 octets,
enveloppe versionnée par magic bytes, KDF historique conservée en lecture
seule pour restaurer les anciennes sauvegardes.

**Secrets** — aucun secret commité (vérifié sur les fichiers suivis) ;
`.gitignore` couvre `.env*`, `certs/*.pem|key|crt|p12`, `google-services.json`,
`signing.properties`, les keystores.

**Réseau** — `validate_network_bind()` refuse tout bind réseau implicite et
tout HTTP distant ; loopback par défaut ; TLS direct et mode reverse proxy
mutuellement exclusifs. Le serveur TV a sa propre frontière : allowlist IP +
jeton d'au moins 32 caractères, fail-closed, `X-Forwarded-For` ignoré sauf si
le pair TCP direct appartient à une liste de proxies déclarée (vide par
défaut).

**SSRF** — aucune requête sortante ne prend d'URL contrôlée par l'utilisateur :
Tavily, OpenWeatherMap, DuckDuckGo, Ollama et FCM utilisent tous des bases
d'URL constantes.

**XSS** — aucun `dangerouslySetInnerHTML` dans les trois arbres React ;
`web_mobile/` passe tout par `textContent` par construction. Seule la TV
utilise `innerHTML`, avec `TV.esc()` correct sur le contenu texte (voir
finding 3 pour les attributs).

## Priorités suggérées

1. Finding 1 — trois lignes dans `_safe_environment()` plus un test
   d'exécution, pas seulement d'analyse.
2. Finding 5 — un job `pip-audit` en CI.
3. Findings 2, 3, 6 — nettoyage à faible coût.
4. Finding 4 — allowlist d'applications si `COMPUTER_ACCESS` doit être activé.
