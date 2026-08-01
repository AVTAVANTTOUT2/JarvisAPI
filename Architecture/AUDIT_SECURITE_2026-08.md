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
| 1 | **Moyenne** | `git` échappe au workspace « isolé » du shell LLM | **Corrigé** + test de non-régression |
| 2 | Faible | Le listener WebSocket du serveur TV n'est pas authentifié (intégration morte) | Symptôme corrigé, fond à arbitrer |
| 3 | Faible | Interpolation HTML non échappée dans les widgets TV | **Corrigé** |
| 4 | Faible | `open_app` sans allowlist d'applications | À arbitrer |
| 5 | Faible | Aucun scan de dépendances / SAST en CI | Ouvert — voir finding 7 |
| 6 | Info | `verify=False` dans le health check TV | **Corrigé** |
| 7 | **Moyenne** | `fastapi==0.115.*` bloque les correctifs de sécurité de starlette | Découvert en corrigeant le 5 |

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

### Correction appliquée

`GIT_CEILING_DIRECTORIES` ajouté à `_safe_environment()`.

Les tests de `tests/test_shell_safety.py` ne couvraient que
`analyze_command()`, ce qui explique que le problème soit passé :
`git status --short` et `git diff -- .` y figurent comme cas *attendus
valides*. L'analyse statique ne peut pas voir l'échappement, puisque la
commande est allowlistée par ailleurs — seule une exécution le révèle.

Deux tests d'**exécution** ont donc été ajoutés :

- `test_git_cannot_reach_the_repository_hosting_the_workspace` — crée un vrai
  dépôt git contenant un marqueur canari, place le workspace à l'intérieur,
  exécute réellement le plan et vérifie l'échec ainsi que l'absence du canari
  et du message de commit dans la sortie. Paramétré sur `git status`,
  `git log -p`, `git show HEAD:<path>` et `git diff` ;
- `test_git_still_works_on_a_repository_created_inside_the_workspace` — garde
  contre un correctif trop agressif.

Vérification du couple correctif/test : les quatre cas du premier test
échouent sans `GIT_CEILING_DIRECTORIES` et passent avec. Suite complète :
51/51.

Durcissement complémentaire, non appliqué : déplacer `LLM_SHELL_WORKSPACE`
hors de l'arborescence du dépôt par défaut. Le plafond rend la mesure
redondante, mais la défense en profondeur reste souhaitable.

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

### Correction partielle appliquée

Le fond n'est pas corrigé, et **ne doit pas l'être à la légère** : `/ws` est
l'endpoint de *chat* — il accepte des messages, déclenche le LLM et exécute
des actions. Y donner accès au serveur TV pour lui faire lire des événements
`audio_daemon_*` diffusés serait une permission très supérieure au besoin.
Élargir la surface d'authentification de l'endpoint le plus sensible du
système pour réparer du bruit de log serait un mauvais échange.

Ce qui a été fait, sans toucher à la frontière d'authentification :

- l'échec devient explicite — un code de fermeture 4401/4428 est journalisé en
  `ERROR` en disant que le canal n'est pas authentifié et que l'overlay vocal
  restera vide, au lieu d'un `WARNING` noyé dans une reconnexion permanente ;
- backoff exponentiel plafonné à 300 s, réinitialisé à la connexion : un refus
  permanent ne martèle plus le backend toutes les 5 s.

Deux directions possibles pour le fond, à arbitrer :

1. **Canal d'événements dédié en lecture seule** — un endpoint distinct,
   loopback uniquement, authentifié par le jeton de contrôle supervisor
   (`core/supervisor_auth.py`, déjà en place, fichier privé 0600). Le serveur
   TV tourne sur la même machine et lit déjà la base directement : ce n'est
   pas une élévation. Coût : un endpoint de plus.
2. **Retrait** de `_ws_listener` et de `/api/events` si l'overlay vocal TV
   n'est plus voulu — il ne fonctionne pas aujourd'hui de toute façon.

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
reposait donc entièrement sur une contrainte située à deux couches de distance.

**Corrigé** : les trois interpolations passent désormais par `TV.esc()`. La
dépendance à la contrainte de schéma disparaît.

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
(Tailscale).

**Corrigé** : `verify=False` retiré.

---

## 7. `fastapi==0.115.*` bloque les correctifs de sécurité de starlette — Moyenne

Découvert en instrumentant le finding 5 : le premier `pip-audit` exécuté sur
l'arbre de dépendances ne remonte pas un angle mort théorique, mais un arriéré
réel.

**Fichier :** `requirements.txt` l. 2

Sur l'environnement réellement installé (fastapi 0.115.14, la plus récente de
la plage), `pip-audit` remonte **8 advisories starlette** en 0.46.2. Or
`fastapi==0.115.*` contraint `starlette<0.47.0`, tandis que les correctifs
sont en 0.47.2, 0.49.1 et 1.x. **Le pin rend ces correctifs inatteignables**,
quelle que soit la résolution.

Starlette n'est pas une dépendance périphérique : c'est la couche HTTP sous
FastAPI, dans le chemin de traitement de chaque requête — y compris le
`security_middleware` qui porte tout le verrou de session.

Autres paquets signalés sur le même arbre : `litellm` (10 advisories, tiré par
`open-interpreter==0.4.*` — dont la section « Exécution de code avancée » de
`CLAUDE.md` note qu'il n'est déjà plus joignable par l'action `terminal`),
`protobuf`, `setuptools`, `urllib3`, `wheel`, `python-dotenv`.

### Pourquoi ce n'est pas corrigé ici

Atteindre les correctifs starlette impose une montée FastAPI 0.115 → 0.117 au
minimum (`starlette<0.49.0`), et 0.14x pour la série 1.x. Or
`tests/test_phase4_route_contract.py` verrouille par empreinte les 235
opérations HTTP et l'empreinte OpenAPI ; une montée de 26 versions mineures a
toutes les chances d'exiger une re-baseline de ce contrat. Ce n'est pas une
correction d'audit, c'est un chantier de mise à niveau avec un risque de
régression propre.

### Conséquence sur le finding 5

La forme du job CI dépend de cet arbitrage, et il n'a donc pas été livré :

- livrer `pip-audit` bloquant **maintenant** rend la CI rouge dès le premier
  passage, sur un arriéré connu — la voie la plus sûre pour que l'équipe
  apprenne à ignorer le job ;
- livrer le job en `continue-on-error` produit un contrôle de sécurité qui
  n'échoue jamais, c'est-à-dire aucun contrôle.

Les deux options défendables sont donc : traiter l'arriéré puis livrer le job
bloquant ; ou livrer le job bloquant avec une liste d'exceptions
`--ignore-vuln` explicite, datée et commentée, qui laisse passer l'arriéré
connu mais fait échouer toute CVE **nouvelle**. La seconde donne une CI verte
immédiatement et un vrai garde-fou ; elle demande d'énumérer l'arriéré sur un
environnement de production complet (torch, spaCy…), impossible à installer
dans l'environnement d'audit.

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

## État des corrections

Corrigé et vérifié dans ce lot :

| Finding | Correction | Vérification |
|---|---|---|
| 1 | `GIT_CEILING_DIRECTORIES` dans `_safe_environment()` | 2 tests d'exécution ajoutés ; échouent sans le correctif, passent avec ; suite 51/51 |
| 3 | `TV.esc()` sur les trois interpolations d'attribut | `tests/test_tv_security.py` 16/16 |
| 6 | `verify=False` retiré | idem |
| 2 (partiel) | Journalisation explicite du refus + backoff plafonné | idem |

Régression globale : `tests/ jarvis/tests agents/devagent` → **1268 passés,
9 ignorés, 1 échec**. L'échec unique
(`test_edge_henri_produces_mpeg_not_wav`) est un artefact d'environnement :
`edge-tts` ne peut pas joindre `speech.platform.bing.com` derrière le proxy
TLS auto-signé du bac à sable. Sans rapport avec les fichiers modifiés.

## Reste à arbitrer

1. **Finding 7 + 5** — le plus important. Traiter l'arriéré de dépendances
   (montée FastAPI, avec re-baseline probable du contrat de routes), puis
   livrer `pip-audit` bloquant ; ou livrer le job avec une liste d'exceptions
   datée qui gate uniquement les CVE nouvelles.
2. **Finding 2** — canal d'événements dédié en lecture seule authentifié par
   le jeton supervisor, ou retrait de la fonctionnalité.
3. **Finding 4** — allowlist d'applications pour `open_app`, à trancher si
   `COMPUTER_ACCESS` doit passer à `true`. Sans cet opt-in, l'action est
   inerte.
