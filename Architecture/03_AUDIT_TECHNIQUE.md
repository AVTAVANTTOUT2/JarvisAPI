# 03 — Audit Technique

**Date** : 11 juillet 2026

## 1. Backend — FastAPI

### 1.1 main.py (7 194 lignes)

| Aspect | Évaluation | Note |
|---|---|---|
| Taille | ❌ 7 194 lignes | Seuil acceptable : ~500 lignes |
| Routes | ❌ 183 endpoints | Devraient être répartis en 12 routeurs |
| Responsabilités | ❌ 40+ | Routeur, WS, pipeline, frontend, auth, middleware |
| Imports | ❌ 42 top-level | Dont 8 singletons d'agents individuels |
| Middleware | ✅ Correct | CORS configuré, security_middleware fonctionnel |
| Lifespan | ⚠️ 5 services lancés | Devrait être délégué à un ServiceManager |
| Tests | ⚠️ Couverture partielle | 534 fonctions de test (59 fichiers), couverture par route non mesurée |

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

### 2.1 web/ — SPA desktop (React 19 + Vite)

| Aspect | État |
|---|---|
| Framework | ✅ React 19 + Vite 6 |
| Style | ✅ Tailwind v4 |
| Router | ✅ react-router-dom v7, lazy-loading |
| État | ⚠️ useState/useEffect, pas de state manager |
| API | ✅ api.ts (626 lignes, 60 méthodes typées) |
| WebSocket | ✅ Singleton, reconnexion exponentielle |
| Offline | ✅ IndexedDB + file (création tâche uniquement) |
| Auth | ✅ LockGate (PIN, auto-lock, anti-brute-force) |
| Accessibilité | ⚠️ Non vérifiée |
| Responsive | ⚠️ Optimisé desktop, pas mobile-first |
| Tests | ⚠️ 18 tests Vitest centrés sur l'offline (2 fichiers) |

### 2.2 pwa/ — PWA mobile (Next.js 14)

| Aspect | État |
|---|---|
| Framework | ⚠️ Next.js 14.2 (pas 15+) |
| Style | ⚠️ Tailwind v3.4 (version différente du desktop) |
| Data fetching | ✅ React Query v5 |
| API | ⚠️ jarvisFetch (52 lignes, pas de types) |
| Offline | ❌ Aucun |
| Auth | ❌ Aucun LockGate (faille documentée) |
| Mobile-first | ✅ BottomNav, safe-area, Viewport |
| Carte | ✅ Leaflet |
| Tests | ❌ Aucun |

### 2.3 Composants dupliqués

| Composant | web/ | pwa/ | Duplication |
|---|---|---|---|
| Liste de tâches | TasksView inline | TaskList + TaskItem | 70% |
| Création de tâche | TasksView inline | TaskCreator | 80% |
| Notifications | Dashboard (top 5) | MailList + MailItem | 60% |
| Dashboard | Cartes + Recharts | Stats 2x2 + BriefingCard | 30% |
| Carte | SVG custom (~840l) | Leaflet (~308l) | 0% |
| Types | Inline ou api.ts | Types locaux | 90% |
| Fetch wrapper | api.ts (626 lignes) | jarvisFetch (52 lignes) | 0% |

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
| Tables | 72 après initialisation et migrations |
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
| Apple timestamp | ❌ 4 implémentations différentes |

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

**Recommandation** : Principe de moindre privilège. Actuellement 4 composants ont Full Disk Access pour un besoin unique (lire chat.db).

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
| Chargement main.py | ~200ms (42 imports) | ✅ Routeurs paresseux |
| build_full_context() | ~50ms (20+ requêtes) | ✅ Cache ou jointure |
| Chat LLM | 1-3s (DeepSeek) | ⚠️ Externe |
| Vision Ollama | 500ms-2s (local) | ⚠️ GPU |
| Import iMessage | ~10s/5000 msg | ✅ Déjà optimisé |
| Analyse relationnelle | ~3s/contact (LLM) | ✅ Incrémentale |
| Broadcast WS | ~5ms/client | ✅ <5 clients |

### 6.2 Goulots potentiels

1. 42 imports top-level → temps de chargement au démarrage
2. database/__init__.py 4 169 lignes → compilation lente
3. build_full_context() 20+ requêtes séquentielles
4. APScheduler 29 jobs sans max_instances

## 7. Maintenabilité

### 7.1 Dette technique

| Type | Points | Heures |
|---|---|---|
| God objects (2) | 80 | 32h |
| Code dupliqué (8 zones) | 120 | 40h |
| Tests manquants | 60 | 24h |
| Documentation obsolète | 20 | 8h |
| Dead code | 15 | 4h |
| **Total** | **295** | **~108h** |

### 7.2 Complexité

| Fichier | Fns >50 lignes | Fns >100 lignes |
|---|---|---|
| main.py | ~15 | ~8 |
| database/__init__.py | ~10 | ~5 |
| agents/orchestrator.py | ~3 | ~2 |
| actions.py | ~5 | ~2 |
