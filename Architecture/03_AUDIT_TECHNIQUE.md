# 03 — Audit Technique

**Date initiale** : 11 juillet 2026
**Dernière mise à jour** : 14 juillet 2026

## 1. Backend — FastAPI

### 1.1 Couche API après Phase 4

| Aspect | Évaluation | Note |
|---|---|---|
| Taille | ✅ `main.py` 175 lignes | Tous les modules `api/*.py` restent à 500 lignes ou moins |
| Routes | ✅ 174 opérations HTTP + 1 WebSocket | Réparties dans exactement 12 `APIRouter`; 157 chemins OpenAPI |
| Responsabilités | ✅ Assemblage séparé | Routeurs, WS, pipeline, frontend, auth, middleware et lifespan isolés |
| Imports | ✅ Dépendances distribuées | Aucun module `api/` n'importe `main.py` |
| Middleware | ✅ Correct | CORS configuré, security_middleware fonctionnel |
| Lifespan | ✅ Extrait | `api/lifespan.py` est monté explicitement sur l'application |
| Tests | ⚠️ Couverture partielle | 554 fonctions backend (68 fichiers), 27 Vitest et 3 E2E ; couverture globale non mesurée |

L'état initial (7 197 lignes, 40+ responsabilités et 42 imports concentrés) est conservé comme constat historique de l'audit. ADR-008 a été appliqué le 14/07/2026 sans changement de signature HTTP/WebSocket ni de schéma OpenAPI.

### 1.2 Middlewares

| Middleware | État | Détail |
|---|---|---|
| CORS | ✅ OK | Origins configurées, méthodes * |
| Security | ✅ OK | CSP, X-Frame-Options, Referrer-Policy |
| Session | ✅ OK | Fail-closed, 428 si non configuré |
| Origin/Referer | ✅ OK | Vérification sur POST/PUT/PATCH/DELETE |

### 1.3 Routes — Audit par catégorie

| Catégorie | Routes | Testé ? | UI ? |
|---|---|---|---|
| Auth | 8 | ✅ | ✅ LockGate |
| Status/Stats | 5 | ✅ | ✅ Dashboard |
| Conversations | 12 | ✅ | ✅ ChatView |
| People | 18 | ✅ | ✅ ContactsView |
| Tasks | 5 | ✅ | ✅ TasksView |
| Life Profile | 7 | ✅ | ⚠️ Partiel |
| Calendar | 4 | ⚠️ | ✅ |
| Notifications | 4 | ✅ | ✅ Panel |
| Location/Places | 17 | ✅ | ✅ MapView |
| Devices | 7 | ✅ | ✅ Dashboard |
| Screen Activity | 3 | ⚠️ | ⚠️ |
| Daemon/Control | 14 | ❌ | ❌ admin |
| DevAgent | 8 | ✅ | ✅ |
| Quality/DevOps | 8 | ❌ | ❌ admin |
| Self-healing | 2 | ❌ | ❌ admin |
| Rituals/DND | 7 | ⚠️ | ⚠️ |
| Recordings | 6 | ✅ | ✅ |
| Semantic Search | 1 | ⚠️ | ⚠️ |
| Predictions | 6 | ❌ | ❌ |
| Day Scores | 3 | ❌ | ❌ |
| WebSocket | 1 | ⚠️ | ✅ |

### 1.4 WebSocket

| Aspect | État |
|---|---|
| Connexion | 1 endpoint /ws |
| Reconnexion | ✅ Exponentielle (côté client) |
| Types de messages | ✅ Texte, binaire (audio), action_confirm |
| Streaming | ✅ SSE → chunks progressifs |
| Broadcast | ✅ Registre verrouillé, snapshot défensif et I/O hors verrou (Phase 1) |
| Gestion déconnexion | ✅ Nettoyage du set |
| Reprise session | ✅ Grace period de 3 min après coupure |

### 1.5 Workers et Scheduler

| Worker | Type | Intervalle | Coordination |
|---|---|---|---|
| email_watcher | asyncio.create_task | 120s | Cache mémoire |
| jarvis_daemon | asyncio.create_task | 5-30s | Offset SQLite monotone `daemon.notifications` |
| audio_daemon | asyncio.create_task | Continu | VAD continu |
| scheduler | APScheduler | 29 jobs | Pas de max_instances |
| imessage_daemon | Subprocess | HTTP | Processus séparé |
| screen_watcher | Thread daemon | 30s | Spacing min 120s |

### 1.6 Démons et LaunchAgents

| Processus | Lancement | Port | Redémarrage |
|---|---|---|---|
| Supervisor | launch_supervisor.sh | 9000 | Auto (health-check) |
| Backend | Supervisor subprocess | 8081 | Auto |
| TV Dashboard | Supervisor subprocess | 5174 | Manuel |
| Ollama | Supervisor subprocess | 11434 | Manuel |
| Daemon iMessage | Subprocess main.py | 8193 | Manuel |

## 2. Frontend

### 2.0 frontend/ — application canonique responsive (Next.js 15)

| Aspect | État |
|---|---|
| Framework | ✅ Next.js 15.5, React 19, Tailwind v4 |
| Layout | ✅ Sélection téléphone/desktop déterministe ; 21 segments métier, 25 pages statiques exportées |
| Réutilisation | ✅ Les vues `web/src` et `pwa/src` sont importées, pas recopiées |
| API | ✅ Un seul wrapper `frontend/src/lib/api.ts`, cookie inclus sur chaque requête |
| Auth | ✅ SDK `jarvis_auth/`, LockGate fail-closed partagé |
| PWA | ✅ Manifest et Service Worker limité aux assets publics du shell |
| Tests | ✅ 9 Vitest, typecheck/build, 3 Playwright desktop/mobile |
| Déploiement | ✅ `frontend/out` prioritaire ; `web/dist` et `/m/` restent des fallbacks |

### 2.1 web/ — SPA desktop historique et source des vues

| Aspect | État |
|---|---|
| Framework | ✅ React 19 + Vite 6 |
| Style | ✅ Tailwind v4 |
| Router | ✅ react-router-dom v7, lazy-loading |
| État | ⚠️ useState/useEffect, pas de state manager |
| API | ✅ Wrapper partagé `frontend/src/lib/api.ts` |
| WebSocket | ✅ Singleton, reconnexion exponentielle |
| Offline | ✅ IndexedDB + file (création tâche uniquement) |
| Auth | ✅ `LockGate` importé depuis `jarvis_auth/` |
| Accessibilité | ⚠️ Non vérifiée |
| Responsive | ⚠️ Optimisé desktop, pas mobile-first |
| Tests | ⚠️ 18 tests Vitest centrés sur l'offline (2 fichiers) |

### 2.2 pwa/ — fallback mobile historique et source des vues

| Aspect | État |
|---|---|
| Framework | ⚠️ Next.js 14.2 (pas 15+) |
| Style | ⚠️ Tailwind v3.4 (version différente du desktop) |
| Data fetching | ✅ React Query v5 |
| API | ✅ `jarvisFetch` partagé depuis `frontend/src/lib/api.ts` |
| Offline | ❌ Aucun |
| Auth | ✅ `LockGate` partagé, fail-closed |
| Mobile-first | ✅ BottomNav, safe-area, Viewport |
| Carte | ✅ Leaflet |
| Tests | ✅ Couvert via les tests unitaires/E2E du frontend unifié |

### 2.3 Composants dupliqués

| Composant | web/ | pwa/ | Duplication |
|---|---|---|---|
| Liste de tâches | TasksView inline | TaskList + TaskItem | 70% |
| Création de tâche | TasksView inline | TaskCreator | 80% |
| Notifications | Dashboard (top 5) | MailList + MailItem | 60% |
| Dashboard | Cartes + Recharts | Stats 2x2 + BriefingCard | 30% |
| Carte | SVG custom (~840l) | Leaflet (~308l) | 0% |
| Types | Inline ou api.ts | Types locaux | 90% |
| Fetch wrapper | `frontend/src/lib/api.ts` | le même wrapper | 100% partagé |

### 2.4 PWA — Service Worker

| Aspect | web/ | pwa/ |
|---|---|---|
| Stratégie | Workbox injectManifest | next-pwa |
| Precache | App shell (JS/CSS/HTML) | Chunks Next.js |
| Cache routes API | ❌ (données personnelles) | ❌ |
| Push | ✅ (VAPID + aes128gcm) | ❌ |
| Background Sync | ✅ (event sync) | ❌ |
| Installation | ✅ (beforeinstallprompt + iOS) | ✅ (manifest) |

### 2.5 IndexedDB / Offline

| Aspect | web/ | pwa/ |
|---|---|---|
| Base | jarvis-offline (idb v8) | ❌ |
| File d'écriture | ✅ writeQueue | ❌ |
| Cache lecture | ✅ readCache (TTL) | ❌ |
| Sync auto | ✅ Au retour réseau | ❌ |
| Purge | ✅ Au logout | ❌ |
| Branché sur | Création tâche uniquement | — |

## 3. Base de données

### 3.1 SQLite — Configuration

| Paramètre | Valeur | Évaluation |
|---|---|---|
| Mode journal | WAL | ✅ Lectures concurrentes OK |
| busy_timeout | ✅ 5000 ms dans `database/core.py` | Validé Phase 1 |
| Foreign keys | ✅ ON | |
| FTS5 | ✅ | Recherche plein-texte |
| Sauvegardes | ✅ VACUUM INTO quotidien | Rotation configurable |

### 3.2 Schéma

| Métrique | Valeur |
|---|---|
| Tables | 73 applicatives après initialisation et migrations (`event_log` inclus, `sqlite_sequence` exclue) |
| Contraintes UNIQUE | ✅ Sur toutes les tables critiques |
| Clés étrangères | ✅ ON DELETE CASCADE où pertinent |
| Index | ✅ Colonnes de recherche fréquentes |
| Migrations | ✅ Versionnées, idempotentes, backup auto |

### 3.3 Intégrité

| Risque | Mitigation |
|---|---|
| Corruption WAL | ✅ Sauvegardes quotidiennes |
| Écritures concurrentes | ✅ WAL + `busy_timeout = 5000` |
| Dédoublonnage | ✅ Contraintes UNIQUE multiples |
| Cohérence référentielle | ✅ Foreign keys activées |

## 4. Synchronisation

### 4.1 iMessage

| Aspect | État |
|---|---|
| Import initial | ✅ Batch 5000, triple déduplication |
| Sync incrémentale | ✅ Curseur ROWID |
| Déduplication | ✅ ROWID + GUID + SHA256 |
| Curseurs parallèles | ✅ 3 offsets nommés, persistants et monotones dans un registre central |
| Apple timestamp | ✅ Conversion unique dans `integrations/apple_data.py` |

### 4.2 Contacts

| Aspect | État |
|---|---|
| Source | AddressBook SQLite + AppleScript |
| Résolution handle→nom | ✅ Cache + normalisation |
| Sync vers people | ✅ sync_contacts.py |
| Fusion doublons | ✅ Handle + nom existants |
| Cohérence | ⚠️ 2 résolveurs distincts |

### 4.3 Frontend Offline Queue

| Aspect | État |
|---|---|
| Existence | ✅ Uniquement dans web/ |
| UUID par opération | ❌ |
| Timestamp | ❌ |
| Checksum | ❌ |
| Retry | ✅ Retour réseau |
| Politique conflit | Dernière écriture gagne |
| Branché sur | Création tâche uniquement |

## 5. Sécurité

### 5.1 Authentification

| Contrôle | État |
|---|---|
| Hash du secret | ✅ scrypt (N=2^14) |
| Sessions | ✅ Jeton opaque (token_urlsafe 32) |
| Stockage session | ✅ Hash SHA-256 uniquement |
| Cookie | ✅ HttpOnly, SameSite=Strict |
| Secure (HTTPS) | ⚠️ Conditionnel (WEB_HTTPS) |
| Anti-brute-force | ✅ 5 tentatives / 15 min |
| Expiration | ✅ 30j absolue, 14j inactivité |
| Révocation | ✅ Logout + change-secret |

### 5.2 Autorisation

| Contrôle | État |
|---|---|
| Fail-closed | ✅ 428 tant que pas configuré |
| Middleware global | ✅ Sur /api/* |
| Bypass list | ✅ /api/auth/*, /api/location, /api/devices/* |
| Device token | ✅ X-Device-Token vérifié |
| Location token | ✅ LOCATION_API_TOKEN optionnel |

### 5.3 Injection

| Vecteur | Mitigation |
|---|---|
| SQL injection | ✅ Paramètres liés |
| XSS | ✅ CSP + React échappement |
| CSRF | ✅ SameSite=Strict + Origin/Referer |
| Path traversal | ✅ Validation uploads |
| Command injection | ✅ Whitelist computer.py |

### 5.4 Exposition réseau

| Service | Port | Exposition |
|---|---|---|
| Supervisor | 9000 | Tailscale/LAN |
| Backend | 8081 | Tailscale/LAN |
| PWA mobile | 8081/m/ | Tailscale/LAN |
| TV Dashboard | 5174 | LAN uniquement |
| Ollama | 11434 | Localhost |
| Daemon iMessage | 8193 | Localhost |

### 5.5 Permissions macOS

| Permission | Composants | Risque |
|---|---|---|
| Full Disk Access | 4 composants | Élevé |
| Automation Messages | 2 composants | Moyen |
| Automation Mail | 2 composants | Moyen |
| Automation Calendar | 1 | Faible |
| Automation Contacts | 1 | Faible |
| Enregistrement écran | 1 | Élevé |
| Microphone | 1 | Élevé |

**État après Phase 5** : l'implémentation de lecture est unique (`AppleDataService`), mais tout processus qui l'utilise nécessite encore Full Disk Access. La réduction des identités macOS autorisées reste une validation opérationnelle à mener séparément.

### 5.6 Chiffrement

| Donnée | État |
|---|---|
| jarvis.db au repos | ❌ Non chiffré |
| Sauvegardes | ✅ Fernet optionnel |
| Secrets (.env) | ✅ .gitignore |
| Mots de passe | ✅ Hash scrypt |
| Données en transit | ⚠️ HTTP par défaut |

## 6. Performances

### 6.1 Points chauds

| Opération | Coût estimé | Optimisable ? |
|---|---|---|
| Chargement de la couche API | Non re-mesuré après Phase 4 | Les dépendances sont distribuées par domaine ; benchmark à refaire |
| build_full_context() | ~50ms (20+ requêtes) | ✅ Cache ou jointure |
| Chat LLM | 1-3s (DeepSeek) | ⚠️ Externe |
| Vision Ollama | 500ms-2s (local) | ⚠️ GPU |
| Import iMessage | ~10s/5000 msg | ✅ Déjà optimisé |
| Analyse relationnelle | ~3s/contact (LLM) | ✅ Incrémentale |
| Broadcast WS | ~5ms/client | ✅ <5 clients |

### 6.2 Goulots potentiels

1. Temps de chargement après découpage non re-mesuré
2. `database/__init__.py` réduit à 236 lignes ; plus grand module DB : `schema.py` (666 lignes)
3. build_full_context() 20+ requêtes séquentielles
4. APScheduler 29 jobs sans max_instances

## 7. Maintenabilité

### 7.1 Dette technique

| Type | Points | Heures |
|---|---|---|
| God objects restants hors API/DB | 40 | 16h |
| Code dupliqué (8 zones) | 120 | 40h |
| Tests manquants | 60 | 24h |
| Documentation obsolète | 20 | 8h |
| Dead code | 15 | 4h |
| **Total indicatif après Phase 4** | **255** | **~92h** |

### 7.2 Complexité

| Fichier | Fns >50 lignes | Fns >100 lignes |
|---|---|---|
| main.py | 0 | 0 |
| api/*.py | Non re-mesuré | Non re-mesuré |
| database/__init__.py | 0 | 0 |
| database/people.py | 2 | 1 |
| agents/orchestrator.py | ~3 | ~2 |
| actions.py | ~5 | ~2 |

### 7.3 Event Bus — état après Phase 3

Le bus est actif avec 10 contrats de domaine immuables, un checksum SHA-256 et trois consommateurs réels. Les handlers sont exécutés concurremment et l'échec de l'un n'interrompt pas les autres. Le journal `event_log` rend chaque événement auditable et idempotent par `event_id`; le rejeu automatique reste une capacité future. La PWA invalide les requêtes notifications et tâches via SSE, sans polling périodique sur ces vues.
