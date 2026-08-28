# AGENTS.md

Voir [`CLAUDE.md`](./CLAUDE.md) pour la référence détaillée du code, des routes
et des conventions, et [`README.md`](./README.md) pour l'installation et les
commandes standard (tests, lint, build). Ce fichier ne duplique pas ces
sources : il ne contient que ce qui est spécifique à l'exécution en agent cloud.

## Cursor Cloud specific instructions

Le projet cible macOS, mais le backend, les tests et les frontends tournent sur
le VM Linux de Cursor Cloud, exactement comme le job CI `backend` (voir
`.github/workflows/ci.yml`). Les intégrations Apple (Mail, Calendar, iMessage,
Contacts, osascript), l'audio natif et Ollama ne sont pas disponibles ici — les
modules se dégradent proprement (avertissements au démarrage, pas d'erreur).

### Environnement Python (venv déjà installé par l'update script)

- Le venv est dans `venv/` et utilise le lock léger Linux
  `requirements/locks/ci-linux-x86_64-py312.txt` (sous-ensemble CI ; les piles
  lourdes torch/faster-whisper/pyaudio sont volontairement absentes et
  optionnelles au runtime).
- **Activer le venv avant de lancer les tests** : `source venv/bin/activate`.
  Ce n'est pas cosmétique — certains tests de `agents/devagent` (staging,
  boucle d'intégration) lancent un sous-processus `python3 -m pytest`. Sans le
  venv sur le `PATH`, ce `python3` est le Python système sans pytest et ces
  tests échouent avec `result["ok"] is False`. Activé, ils passent.
- Lint et tests : commandes dans `README.md` (« Tests »). En résumé :
  `ruff check .` puis `python -m pytest tests/ jarvis/tests agents/devagent -q`.
- Les tests de récupération cross-source dans
  `tests/test_universal_memory_e2e.py` font partie de la suite normale. Ne pas
  maintenir ici de liste de tests supposés cassés : une régression se constate
  par l’exécution courante et la CI.

### Lancer le backend

- Le démarrage est fail-closed : `config.validate_required_runtime_config()`
  exige un `DEEPSEEK_API_KEY` non vide et différent de `sk-...`. Sans lui, le
  backend refuse de démarrer.
- La vraie clé est fournie comme **secret Cursor injecté** (variable
  d'environnement `DEEPSEEK_API_KEY`). Les modèles `deepseek-v4-flash` /
  `deepseek-v4-pro` sont réels sur cette API.
- **Ne pas mettre `DEEPSEEK_API_KEY` dans `.env`** : `env_loader.py` charge
  `.env` avec `override=True`, donc une valeur locale (même factice) masque le
  secret injecté et fait échouer les appels LLM. Laisser `.env` pour les
  bascules non-secrètes uniquement. `.env` est gitignored ; en créer un minimal
  pour le dev (voir bascules ci-dessous).
- Lancer le backend depuis un shell qui a bien la variable `DEEPSEEK_API_KEY`.
  Piège tmux : le serveur tmux partagé peut avoir démarré avant l'injection du
  secret ; propager la variable avec
  `tmux set-environment -g DEEPSEEK_API_KEY "$DEEPSEEK_API_KEY"` puis recréer la
  session, sinon le backend démarre en `ConfigurationError`.
- Sans vraie clé, une clé factice (`sk-dev-...`) dans `.env` suffit pour tout ce
  qui ne fait pas d'appel LLM (auth, tâches, navigation UI, santé).
- Sur le VM Linux, désactiver les services macOS/daemons dans `.env` pour un run
  propre : `DAEMON_ENABLED`, `SCREEN_WATCHER_ENABLED`, `RESOURCE_GUARD_ENABLED`,
  `OLLAMA_AUTOSTART`, `IMESSAGE_SOURCING_ENABLED`, `IMESSAGE_DAEMON_ENABLED`,
  `AUDIO_DAEMON_ENABLED`, `CLAW3D_MANAGED_BY_SUPERVISOR`, `BACKUP_ENABLED` = `false`.
  Le scheduler APScheduler démarre quand même (jobs planifiés inoffensifs).
- Démarrer : `source venv/bin/activate && python main.py` → `http://127.0.0.1:8080`.
- Vérification rapide : `GET /api/health/live` → `{"status":"ok"}`. Les routes
  `/api/*` répondent `428` tant que l'auth n'est pas configurée (fail-closed).
- Auth + mutations : la première visite définit un PIN (4 chiffres) ou une
  passphrase (10+ caractères) via `POST /api/auth/setup`. Les mutations par
  cookie exigent l'en-tête `X-CSRF-Token` **et** un `Origin` exact
  (`http://127.0.0.1:8080`) — sinon `csrf_check_failed`.

### Frontends

- `frontend/` (Next.js 15) est le bureau canonique. Servi à `/` **uniquement
  si `frontend/out` existe** (sinon le bureau répond 503 et seul `/mobile/`,
  statique, est servi). Il faut donc `pnpm build` dans `frontend/` puis
  (re)démarrer le backend, qui résout `frontend/out` au démarrage.
- pnpm est épinglé à `11.11.0` (activer via `corepack prepare pnpm@11.11.0
  --activate` ; le pnpm système est plus ancien). Installer avec
  `--frozen-lockfile` dans `web/` puis `frontend/` (`frontend` importe `web`).
- Commandes frontend (test/typecheck/build/e2e) : voir `README.md` (« Tests »).

### Dépendance système installée une fois (hors update script)

`python3.12-venv` (ensurepip) est requis pour créer le venv et a été installé
via `apt-get install python3.12-venv`. Il est capturé dans le snapshot du VM ;
l'update script ne le réinstalle pas.
