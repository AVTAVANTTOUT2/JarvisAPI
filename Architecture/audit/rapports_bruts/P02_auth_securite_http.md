<!--
source_agent: bc-019fb865-e1ca-7440-bdf3-87cbfd45fc6d
agent_name: Audit auth et sécurité HTTP
agent_url: https://cursor.com/agents/bc-019fb865-e1ca-7440-bdf3-87cbfd45fc6d
agent_status: IDLE
created_at: 2026-07-31T13:38:32.764000+00:00
extracted_msg_index: 110
extracted_at: 2026-07-31T14:37:19.332428+00:00
-->

# Audit P02 — Auth et sécurité HTTP

**Mode** : lecture seule · **ID** : P02 · **Fichiers lus** : ~3270 lignes  
**Verdict** : pas de bypass auth navigateur évident hors allowlists documentées ; **faiblesses confirmées** sur rate-limit pairing mobile, PIN 4 vs doc 6, CSRF sans Origin, bypass supervisor loopback, et CSP `ws:`/`wss:` trop larges.

---

## Points d’attention connus

| Point | Statut | Preuve |
|--------|--------|--------|
| Asymétrie rate-limit pairing desktop vs mobile | **Confirmé** | Desktop : `consume_device_pairing_code(..., max_attempts=DEVICE_PAIRING_MAX_ATTEMPTS)` → 429. Mobile : `consume_mobile_pairing_code` booléen seulement, **aucun** compteur / lockout dans `api/router_auth.py` ni `database/mobile.py`. |
| PIN min 4 vs doc 6 | **Confirmé** | `auth._MIN_PIN_DIGITS = 4` ; `CLAUDE.md` dit « PIN 6 chiffres ». Tests mobile verrouillent même `MIN_PIN = 4`. |
| CSRF si Origin/Referer absents | **Confirmé (par design)** | `_csrf_origin_allowed` → `True` si source vide ; jeton `X-CSRF-Token` reste obligatoire. Test `test_post_without_origin_header_allowed`. |
| Bypass supervisor `X-Jarvis-Supervisor` | **Confirmé** | Loopback + header `"1"` → saute le session gate pour `/api/control/*` (start/stop services, logs). Header **non secret**. |
| Local unlock recovery | **Confirmé, borné** | Loopback IP + Host local + `X-Jarvis-Local-Recovery: 1` + secret ; ignore plafond global ; `clear_all_rate_limits()` au succès. |

---

## Findings

### F1 — HIGH — Pairing mobile sans rate-limit (A04)
**Où** : `api/router_auth.py:266-278`, `database/mobile.py:19-29`  
**Quoi** : `/api/mobile/pairing/complete` est hors session gate et accepte un code 6 chiffres (10⁶) sans limite d’essais par IP. Desktop a `DEVICE_PAIRING_MAX_ATTEMPTS=5` + lockout 15 min.  
**Impact** : bruteforce du code pendant sa fenêtre TTL (10 min) depuis le Tailnet / réseau exposé. Usage unique atténue après succès, pas les échecs.  
**Contraste** : `api/router_devices.py:102-114`.

### F2 — MEDIUM — PIN minimum 4 chiffres (A02 / doc drift)
**Où** : `auth.py:77-90` vs `CLAUDE.md` (~l.1516)  
**Quoi** : espace 10⁴ vs 10⁶ documenté. Mitigé par scrypt + lockout (5 essais / 15 min) + délai progressif, mais politique réelle ≠ doc.  
**Note** : passphrase ≥ 10 OK.

### F3 — MEDIUM — Bypass `/api/control/*` local (A01)
**Où** : `api/middleware.py:128-135`  
**Quoi** : tout processus local pouvant joindre le backend (compromission locale, autre service loopback) contrôle audio/daemon/scheduler via header statique. Pas de jeton partagé.  
**Acceptable** si modèle de menace = machine mono-user de confiance ; sinon secret partagé ou socket Unix requis.

### F4 — MEDIUM — CSP `connect-src` autorise tout WebSocket (A05)
**Où** : `security_headers.py:20` — `connect-src 'self' ws: wss: …`  
**Quoi** : en cas d’XSS (aidé par `script-src 'unsafe-inline'`), exfiltration via WS arbitraire. Restreindre à l’origine self.

### F5 — LOW/MEDIUM — CSRF Origin optionnel (A01)
**Où** : `api/middleware.py:99-105`  
**Quoi** : sans Origin/Referer, seul le jeton synchronisé compte. Correct pour clients natifs ; réduit la défense en profondeur navigateur. Cookie `SameSite=strict` reste un filet.

### F6 — LOW — Clé VAPID privée en clair dans `app_settings` (A02)
**Où** : `push.py:48-60` — PEM `NoEncryption` via `set_setting`.  
**Impact** : fuite DB → usurpation d’émetteur push (pas lecture des payloads chiffrés end-to-end).

### F7 — LOW — `send_web_push` sans allowlist d’endpoint (A10)
**Où** : `push.py:141-156` — `httpx.post(endpoint, …)`  
**Quoi** : SSRF si abonnement malveillant enregistré (route subscribe hors P02, session requise). Timeout 10 s présent. Pas de validation schéma/host FCM/Mozilla/Apple dans ce module.

### F8 — LOW — Routes mobile bypass sans auth middleware (A01 surface)
**Où** : `api/middleware.py:145-154` — chat, voice/turn, conversations, pairing/complete…  
**Quoi** : le gate ne vérifie pas le Bearer ; la sécurité dépend des routeurs hors P02. Oubli d’auth sur une de ces routes = ouverture totale.  
**Frontière** : P15/P16 + routeurs mobile.

### F9 — INFO — Local recovery (A07)
**Où** : `api/router_auth.py:161-189`, `_is_loopback`  
**Quoi** : bien borné (IP + Host + header + secret). Host spoofing seul insuffisant (IP doit être loopback). Header non secret — OK dans ce modèle. Efface tous les rate-limits après succès (intentionnel).

### F10 — INFO — Sessions / horloges
**Où** : `auth.py:501-562` — `datetime.now()` naïf pour sessions vs UTC pour rate-limits.  
**Frontière P06** : IP stockée en clair dans `sessions.ip`.

---

## Contrats vérifiés (OWASP)

| ID | Contrôle | Résultat | Détail |
|----|----------|----------|--------|
| **A01** | Contrôle d’accès / allowlists | **✗ partiel** | ✓ `_PUBLIC_AUTH_ROUTES` exactes (frozenset méthode+path) ; ✓ fail-closed 428 ; ✓ mobile Bearer whitelist GET/mutations ; ✗ bypass supervisor loopback ; ✗ surface bypass mobile déléguée ; ✗ CSRF Origin omis accepté |
| **A02** | Secrets / hashing | **✗ partiel** | ✓ scrypt N=2¹⁴ + salt 16 B + `hmac.compare_digest` ; ✓ tokens session/mobile/pairing hash SHA-256 ; ✓ CSRF dérivé non réversible ; ✗ PIN min 4 ; ✗ VAPID PEM non chiffrée |
| **A03** | Injection | **✓** | Requêtes auth rate-limit paramétrées ; pas de SQL concaténé dans le périmètre ; uploads : namespace/`..` rejetés, `resolve` + `relative_to` |
| **A04** | Rate-limit | **✗** | ✓ unlock/verify/change-secret + recovery (sans global) ; ✓ progressive + hard + global ; ✗ **pairing mobile sans limite** (asymétrie vs desktop) ; setup public sans rate-limit (course 1er install) |
| **A05** | Misconfig | **✗ partiel** | ✓ nosniff, frame DENY, referrer no-referrer, HSTS si HTTPS ; ✓ `network_security.validate_network_bind` ; ✗ CSP `unsafe-inline` + `ws:`/`wss:` larges ; messages d’erreur génériques OK |
| **A07** | Sessions | **✓** | ✓ opaque `token_urlsafe(32)`, hash seul en DB ; ✓ TTL inactivité + max age ; ✓ révocation unitaire / globale (change-secret) ; ✓ cookie HttpOnly + SameSite=strict + Secure si HTTPS ; ✓ status `Cache-Control: no-store` |
| **A08** | Intégrité jetons | **✓** | ✓ compare_digest CSRF ; ✓ pairing codes hashés `pair:{code}` / mobile token 48 ; ✓ consume atomique `used_at` |
| **A09** | Journalisation auth | **✓** | ✓ `record_failed_attempt` → `log_llm_action` fingerprint tronqué + channel ; ✓ notif high sur hard lock ; pas de secret/IP brute dans l’audit |
| **A10** | Redirects / SSRF | **✗ partiel** | ✓ pas de redirect user-controlled dans le périmètre ; ✓ path traversal frontend bloqué ; ✗ push POST vers `endpoint` abonné sans allowlist |

---

## Contrôles solides (pas de finding)

- **Fail-closed** tant que secret non configuré (`428 setup_required`).
- **scrypt** + sel aléatoire ; secret jamais en clair.
- **CSRF synchronizer** obligatoire sur mutations cookie (même same-origin sans token → 403).
- **Origin exact** schéma+hôte+port quand présent ; `CSRF_ALLOWED_ORIGINS` opt-in.
- **Fichiers sensibles** : `core/file_security` 0700/0600, `O_NOFOLLOW`, anti-symlink ; uploads UUID, MIME/signature, quota, path confinement.
- **PII / logs** : `log_privacy` + `security/redaction` + `DataBoundary` ; mapping PII détruit après usage.
- **Document privacy** : strict local par défaut ; cloud seulement avec consentement (anonymisation via `JARVISRouter`, hors P02 mais cohérent avec la politique déclarée).
- **Local unlock** : double contrainte réseau (peer + Host) + secret.

---

## Frontières (hors audit ligne à ligne)

| Cible | Pourquoi |
|-------|----------|
| **P06** `database/sessions.py`, `database/mobile.py` | CRUD sessions, rate_limits, pairing consume — tracé depuis P02 uniquement |
| **P06** `database/screen_daemon.consume_device_pairing_code` | Rate-limit desktop de référence pour F1 |
| **Hors P** `api/router_devices.py`, `api/router_daemon.py` (`/api/control`) | Pairing desktop + impact supervisor |
| **P14/P15/P16** | UI auth React / `web_mobile` / Android |
| Routeurs `/api/mobile/chat|voice|…` | Auth Bearer attendue **dans** la route, pas au middleware |
| `jarvis/router.py` | Anonymisation réelle avant DeepSeek (document cloud) |
| `api/router_misc` push subscribe | Validation endpoint avant persistance (SSRF F7) |

---

## Matrice fichiers (couverture)

| Fichier | Focus audit |
|---------|-------------|
| `auth.py` | scrypt, PIN4, rate-limit, sessions, CSRF crypto |
| `api/middleware.py` | allowlists, CSRF, supervisor, mobile bearer |
| `api/router_auth.py` | unlock, local-unlock, pairing mobile, cookies |
| `security_headers.py` | CSP / headers |
| `push.py` | VAPID, aes128gcm, POST endpoint |
| `core/file_security.py` | modes 0600/0700 |
| `core/network_security.py` | bind HTTPS |
| `core/frontend_*` | path traversal static |
| `jarvis/uploads.py` | confinement upload |
| `jarvis/log_privacy.py`, `security/redaction.py` | fuite logs |
| `jarvis/pii/*`, `document_privacy.py` | frontière cloud/PII |

---

## Synthèse actionnable (hors scope de ce tour — audit only)

1. Aligner pairing mobile sur le modèle desktop (`max_attempts` / lockout / `Retry-After`).  
2. Aligner `_MIN_PIN_DIGITS` à 6 **ou** corriger la doc/CLAUDE.  
3. Remplacer `X-Jarvis-Supervisor: 1` par un secret partagé fichier 0600 (ou restreindre autrement).  
4. Resserrer CSP `connect-src` (pas `ws:`/`wss:` globaux).  
5. Allowlist des hosts push dans `send_web_push`.

Aucun correctif appliqué (mission lecture seule).