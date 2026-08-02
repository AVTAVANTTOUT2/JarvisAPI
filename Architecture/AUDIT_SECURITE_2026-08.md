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
| 4 | Faible | `open_app` sans allowlist d'applications | **Corrigé** — allowlist opt-in |
| 5 | Faible | Aucun scan de dépendances / SAST en CI | **Corrigé** — job `pip-audit` bloquant |
| 6 | Info | `verify=False` dans le health check TV | **Corrigé** |
| 7 | **Moyenne** | `open-interpreter` (legacy) ancre 18 des 20 CVE de l'arbre | Mesuré ; retrait à arbitrer |

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
par le LLM lance directement `open -a <nom>`, sans allowlist d'applications.

> **Correction d'une surévaluation initiale.** Le premier passage suggérait un
> risque de lancement d'un bundle arbitraire depuis le disque. C'est faux :
> `ComputerControl._validate_argv` refuse déjà `/` et `\`, les caractères de
> contrôle, les noms au-delà de 128 caractères, et n'accepte que la forme
> exacte à trois éléments `(open, -a, nom)` — aucun fichier ni URL ne peut
> être passé en argument. Vérifié par test.

Le résidu réel est donc plus étroit : lancer une application **déjà
enregistrée**, sans lui transmettre quoi que ce soit. Nuisance plutôt que
compromission, et déjà atténué par `COMPUTER_ACCESS=false` par défaut.

### Correction appliquée

Allowlist facultative `COMPUTER_ALLOWED_APPS` : vide (défaut) = comportement
historique inchangé ; renseignée = allowlist stricte insensible à la casse.
L'ajout ne casse aucune installation existante et donne un cran de
durcissement à qui active `COMPUTER_ACCESS`.

`tests/test_computer_control_argv.py` (18 cas) verrouille à la fois les
garanties préexistantes — refus des chemins, des caractères de contrôle et de
toute forme argv autre que `open -a <nom>` — et le nouveau comportement. Le
test d'allowlist stricte échoue sans le correctif.

---

## 5. Aucun scan de dépendances ni SAST en CI — Faible

`.github/workflows/ci.yml` ne contenait ni `pip-audit`, ni `bandit`, ni
`safety`, ni CodeQL, ni `npm/pnpm audit`. L'arbre de dépendances est lourd
(torch, spacy, open-interpreter, faster-whisper, pymupdf, cryptography) et
épinglé en plages (`==0.115.*`), pas par hash.

`scripts/security_audit.py` existe et couvre des motifs internes, mais aucun
contrôle ne portait sur les CVE des dépendances tierces.

### Correction appliquée

Étape `pip-audit` **bloquante** ajoutée au job `production_dependencies`, qui
installe déjà l'arbre complet.

Deux choix de conception méritent d'être explicités :

- **l'audit porte sur l'environnement réellement installé**, pas sur
  `pip-audit -r requirements.txt`. Ce dernier résout les versions *minimales*
  de chaque plage et signale donc des versions que personne n'installe : sur
  ce dépôt il remonte starlette 0.37.2 par la plage `fastapi==0.115.*`, alors
  que la cause réelle est tout autre (finding 7). Auditer l'environnement
  installé évite ce faux diagnostic — et ne coûte rien, le job paie déjà
  l'installation ;
- **liste d'exceptions explicite plutôt que `continue-on-error`.** Un
  contrôle de sécurité non bloquant n'échoue jamais, donc ne contrôle rien.
  Les 19 identifiants de l'arriéré connu au 2026-08-01 sont listés dans le
  job, groupés par cause racine et commentés. Toute CVE **nouvelle**, ou sur
  un autre paquet, fait échouer la CI.

Vérification de la barrière, sur l'arbre de production exact (174 paquets
résolus) : `No known vulnerabilities found, 23 ignored`, sortie 0. En
remettant `python-dotenv` en 1.1.1 — une CVE absente de la liste — la
commande sort en 1 et affiche l'advisory. Le job n'est donc pas décoratif.

`python-dotenv` a été monté de `1.1.*` à `1.2.*` (PYSEC-2026-2270, corrigé en
1.2.2). Seul `load_dotenv(path, override=True)` est utilisé dans le dépôt
(`env_loader.py`) et l'API est inchangée ; la suite complète a été exécutée
avec 1.2.2 installé.

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

## 7. `open-interpreter` (legacy) ancre 18 des 20 CVE de l'arbre — Moyenne

Découvert en instrumentant le finding 5. Le premier `pip-audit` ne remonte pas
un angle mort théorique mais un arriéré réel : **20 advisories sur 5 paquets**
dans l'arbre de production résolu.

> **Correction d'un diagnostic intermédiaire.** Une première lecture attribuait
> le blocage de starlette au pin `fastapi==0.115.*` (qui contraint
> `starlette<0.47.0`). C'est faux, et ça désignait le mauvais chantier. La
> résolution complète montre la vraie contrainte :
> `open-interpreter -> starlette (>=0.37.2,<0.38.0)`. C'est ce plafond dur qui
> fixe starlette en 0.37.2 — et qui, par ricochet, rétrograde FastAPI en
> 0.115.2 au lieu de 0.115.14.

### Mesure

Arbre de production résolu, `pip install --dry-run --report` puis audit des
versions exactes :

| Paquet | Version | Advisories | Contrainte |
|---|---|---|---|
| litellm | 1.83.0 | 10 | `open-interpreter -> litellm (>=1.41.26,<2.0.0)` |
| starlette | 0.37.2 | 7 | `open-interpreter -> starlette (>=0.37.2,<0.38.0)` |
| setuptools | 81.0.0 | 1 | cap `<82` de `requirements.txt`, exigé car open-interpreter importe encore `pkg_resources` |
| protobuf | 4.25.9 | 1 | rétrogradé par la même résolution |
| python-dotenv | 1.1.1 | 1 | indépendant — **corrigé** |

Résolution refaite sans `open-interpreter`, toutes choses égales par ailleurs :

```
avec open-interpreter    : 174 paquets, 20 advisories sur 5 paquets
sans open-interpreter    : 146 paquets,  8 advisories sur 2 paquets
                           litellm absent, setuptools 83.0.0, protobuf 7.35.1,
                           fastapi 0.115.14, starlette 0.46.2
```

Soit **18 des 20 advisories** portées par une seule dépendance — dont les 7 de
starlette, qui n'est pas périphérique : c'est la couche HTTP sous FastAPI,
dans le chemin de chaque requête, y compris le `security_middleware` qui porte
tout le verrou de session.

### Pourquoi c'est une bonne cible

`open-interpreter` est déjà du code mort côté sécurité. `CLAUDE.md`, section
« Exécution de code avancée (Open Interpreter, legacy) » :

> Le wrapper Open Interpreter reste présent pour compatibilité et diagnostic,
> mais l'action publique `terminal` ne lui délègue plus d'instruction : il ne
> peut pas fournir à l'avance la liste exhaustive des commandes à confirmer.

`CODE_EXECUTOR_ENABLED=false` par défaut. La dépendance coûte donc 28 paquets
et 18 advisories pour une capacité volontairement débranchée.

### Ce que le retrait implique

Ce n'est pas une correction d'audit mais une décision produit — retirer une
fonctionnalité documentée, même legacy :

- `integrations/code_executor.py` et ses appelants ;
- l'import de fumée `interpreter` dans le job CI `production_dependencies` ;
- le cap `setuptools<82` de `requirements.txt` et son commentaire, qui
  n'existent que pour lui ;
- la section correspondante de `CLAUDE.md`.

Après retrait, l'arriéré tombe à 7 advisories starlette, atteignables par une
montée FastAPI (0.117 pour `starlette<0.49.0`, 0.14x pour la série 1.x). À
noter pour ce chantier-là : `tests/test_phase4_route_contract.py` verrouille
par empreinte les 235 opérations HTTP et l'empreinte OpenAPI, et exigera très
probablement une re-baseline.

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
| 5 | Job `pip-audit` bloquant + liste d'exceptions datée ; `python-dotenv` monté en 1.2.* | Simulé sur l'arbre de production exact : sortie 0 ; sortie 1 sur une CVE hors liste |
| 4 | Allowlist opt-in `COMPUTER_ALLOWED_APPS` | `tests/test_computer_control_argv.py` 18/18 ; le cas d'allowlist stricte échoue sans le correctif |

Régression globale : `tests/ jarvis/tests agents/devagent` → **1268 passés,
9 ignorés, 1 échec**. L'échec unique
(`test_edge_henri_produces_mpeg_not_wav`) est un artefact d'environnement :
`edge-tts` ne peut pas joindre `speech.platform.bing.com` derrière le proxy
TLS auto-signé du bac à sable. Sans rapport avec les fichiers modifiés.

## Reste à arbitrer

1. **Finding 7** — le plus important, et désormais chiffré : retirer
   `open-interpreter` supprime 18 des 19 exceptions du job CI, pour une
   capacité déjà débranchée (`CODE_EXECUTOR_ENABLED=false`, action `terminal`
   qui ne lui délègue plus rien). Décision produit : c'est un retrait de
   fonctionnalité, même legacy. Ensuite seulement, la montée FastAPI pour les
   7 advisories starlette restantes.
2. **Finding 2** — canal d'événements dédié en lecture seule authentifié par
   le jeton supervisor, ou retrait de la fonctionnalité.

Rien d'autre n'est en attente : les findings 1, 3, 4, 5 et 6 sont corrigés et
couverts par des tests.

## Observation hors périmètre — la suite de tests n'est pas idempotente

Constaté en validant les correctifs, sans rapport avec eux (reproduit avec
toutes les modifications remisées).

`tests/test_message_intelligence.py::test_message_insights_table_exists` lit la
**vraie** base `config.DB_PATH` et se contente de `pytest.skip()` si le fichier
est absent. Or la suite complète crée `data/jarvis.db` — 4 Ko, **zéro table** :
un chemin de code ouvre une connexion sur le `DB_PATH` réel sans passer par
`init_db()`, échappant au monkeypatch que `tests/conftest.py` met en place.

Conséquence : premier passage sur une copie fraîche → le test est *sauté* ;
la suite laisse le fichier ; **second passage consécutif → il échoue**. La CI
ne le voit jamais puisqu'elle repart d'un checkout neuf, ce qui explique que ce
soit passé inaperçu.

Ce n'est pas une faille, mais deux choses méritent d'être notées : sur le Mac
de l'utilisateur, lancer les tests touche le chemin de sa base réelle — c'est
exactement le risque que l'en-tête de `conftest.py` documente ; et un test qui
dépend d'un état ambiant hors dépôt donne un signal peu fiable. Le corriger
demande de faire pointer ce test sur la base de test plutôt que sur
`config.DB_PATH`, et de retrouver le chemin qui fuit.
