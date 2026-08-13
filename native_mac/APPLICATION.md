# Jarvis pour macOS — dossier complet de l’application

Document de référence du prototype natif SwiftUI (`native_mac/`).  
Version app : **0.1.0** · Bundle : `com.jarvis.desktop` · Plateforme : **macOS 15+** · Swift **6.0**  
Date de ce dossier : **5 août 2026**.

---

## 1. Positionnement produit

### Qu’est-ce que c’est

**Jarvis** (nom produit affiché) est une **surface native macOS** pour le cœur JARVIS déjà existant (FastAPI + agents + SQLite + audio). Ce n’est **pas** un second cerveau : l’app ne duplique ni les agents, ni la mémoire, ni le pipeline conversationnel. Elle se comporte comme un **client de bureau** — fenêtre principale, barre des menus, panneau flottant, widget WidgetKit — branché sur le backend local via REST et WebSocket.

### Ce que ce n’est pas

| Ce n’est pas | Pourquoi |
|---|---|
| Un clone du frontend Next.js / `web/` | Autre stack (SwiftUI), autre IA visuelle, surface plus courte |
| Un moteur LLM embarqué | Tout raisonnement reste côté Python / DeepSeek |
| Une app sandboxée App Store | Sandbox désactivée ; client réseau + micro ; signature ad hoc locale |
| Un produit « public » notarisé | Prototype installable en local / DMG de développement |

### Promesse utilisateur

> Un majordome de bureau calme, sombre, glassmorphique, toujours à portée (⌘1–3, ⇧⌘J, barre des menus, Glance), qui parle au **même** Jarvis que le web, avec le même PIN et la même mémoire.

Tagline UI sidebar : **« JARVIS — PERSONAL INTELLIGENCE »**.

---

## 2. Architecture technique

```
┌─────────────────────────────────────────────────────────────┐
│  Jarvis.app (SwiftUI)                                       │
│  ├── Fenêtre principale (NavigationSplitView)               │
│  ├── MenuBarExtra (fenêtre compacte)                        │
│  ├── Window « Jarvis Glance » (flottante)                   │
│  ├── Settings scene                                          │
│  └── JarvisWidget.appex (WidgetKit)                          │
│         │ REST (cookies + CSRF)          │ WebSocket /ws     │
└─────────┼────────────────────────────────┼──────────────────┘
          ▼                                ▼
┌─────────────────────────────────────────────────────────────┐
│  Cœur local JARVIS (Python / FastAPI)                       │
│  https://127.0.0.1:8081 (défaut) · supervisor optionnel :9000│
│  Auth PIN · agents · SQLite · STT/TTS · Mail/Calendar/…     │
└─────────────────────────────────────────────────────────────┘
```

### Principes structurants

1. **Thin client** — `AppModel` orchestre l’état UI ; `JarvisAPI` / `JarvisSocket` parlent au cœur.
2. **Fail-closed auth** — tant que le secret n’est pas validé, seule `LockView` est visible.
3. **Découverte automatique** des URL locales (HTTPS 8081/9000 puis HTTP 8080/8081/9000).
4. **Certificats autosignés acceptés uniquement en loopback** (`LocalTrustDelegate` / `WidgetTrustDelegate`).
5. **Temps réel conversationnel** via WebSocket : streaming, TTS binaire, bascule de conversation.
6. **Mode preview** (`JARVIS_UI_PREVIEW=1` / `--ui-preview`) pour captures sans backend.

### Stack

| Couche | Choix |
|---|---|
| UI | SwiftUI, dark forcé (`preferredColorScheme(.dark)`) |
| Build | XcodeGen (`project.yml`) → `JarvisMac.xcodeproj` |
| Langage | Swift 6.0, concurrency stricte |
| Cible | macOS 15.0+, Xcode ~26 |
| Extension | WidgetKit `com.jarvis.desktop.widget` |
| Signature locale | Ad hoc (`codesign --sign -`) via `scripts/build-prototype.sh` |
| Produits | `dist/Jarvis.app` + `dist/Jarvis-Prototype.dmg` |

### Entitlements

- App Sandbox : **désactivé**
- `network.client` : oui
- `device.audio-input` : oui (micro)

### Permissions Info.plist

- Microphone — conversation vocale
- Apple Events — interactions via le cœur (pas d’Automation directe lourde côté UI)
- Notifications utilisateur
- ATS : `NSAllowsLocalNetworking`
- URL scheme : `jarvis://`
- Catégorie : productivité
- Locale de développement : **fr**

---

## 3. Surfaces d’interface (carte UX)

L’app expose **cinq scènes / surfaces** distinctes :

| Surface | Rôle | Taille / style |
|---|---|---|
| **Fenêtre principale** | Shell complet (sidebar + 6 sections) | min 980×680, défaut 1240×820, title bar cachée |
| **Écran verrou** | Setup / unlock / offline / checking | Carte glass centrée ~590 px |
| **MenuBarExtra** | Accès rapide sans ouvrir la fenêtre | ~300 px, style `.window` |
| **Jarvis Glance** | Panneau flottant multi-Space | 370×430, floating, déplaçable par fond |
| **Réglages système** | URL du cœur + chemin projet | 540×390, Form groupé |
| **Widget WidgetKit** | Glance bureau / Notification Center | Small + Medium |

Deep links `jarvis://` :

| URL | Destination |
|---|---|
| `jarvis://today` (défaut) | Aujourd’hui |
| `jarvis://chat` | Conversation |
| `jarvis://tasks` | Tâches |
| `jarvis://actions` | Actions |
| `jarvis://system` | Système |

---

## 3 bis. Section « Tâches » — pilotage du moteur agentique

Ajoutée en août 2026. C'est la surface où l'utilisateur **décide** : elle lit
un plan, l'accepte ou le refuse, suit l'exécution, tranche les autorisations
d'effet, et lit le rapport.

### Ce que l'application ne fait pas

L'app reste un client, et cette section ne change pas ce contrat. Elle
n'appelle jamais un runtime d'exécution, ne connaît aucun identifiant de
fournisseur, ne détient aucune clé de modèle, ne crée aucun commit, ne gère
aucun worktree, et ne décide jamais qu'un run est terminé. Tout passe par
`/api/task-control/*` et `/api/task-candidates/*`.

### Disposition

`NavigationSplitView` à trois colonnes :

| Colonne | Contenu |
|---|---|
| Sections | À valider, Attention requise, Planifiées, En cours, Terminées, Bloquées / Échecs, Archives, Détectées — chacune avec son compteur |
| Liste | Titre, icône de source, statut, phase, priorité, progression, badge d'attention ; recherche `.searchable` |
| Détail | Six onglets : Résumé, Plan, Activité, Autorisations, Résultat, Contexte |

### Raccourcis

| Raccourci | Action |
|---|---|
| `⌘N` | Nouvelle tâche |
| `⌘↩` | Accepter le plan affiché et lancer |
| `⌘.` | Annuler la tâche |
| `⌥⌘A` | Aller aux tâches demandant attention |

### Ce que l'onglet Activité montre — et ne montre pas

Agent actif, étape, outil appelé, fichier lu ou modifié, test lancé, erreur,
blocage. Trois niveaux : Résumé, Détails, Technique.

Jamais un raisonnement brut. Le serveur n'en émet pas — l'activité est
reconstruite depuis une allowlist de champs — et l'écran n'a aucun champ où
l'afficher.

### Décision de plan

Le bouton d'acceptation renvoie le **digest du plan affiché**. Si une révision
est arrivée entre l'affichage et le clic, le serveur répond `409` et l'écran le
dit, au lieu de lancer un travail que personne n'a lu.

Un plan qui annonce un effet hors machine (`mail:send`, `message:send`,
`calendar:write`, `git:push`) l'affiche en clair, avec la mention que chaque
effet demandera sa propre autorisation le moment venu.

### Notifications

`UNUserNotificationCenter`, dédupliquées par état, groupées par tâche, retirées
quand la tâche n'attend plus rien, badge d'application aligné sur le nombre de
tâches en attente.

Deux règles : aucun contenu sensible dans le corps (pas d'extrait d'e-mail, pas
d'argument d'action) parce qu'il s'affiche sur l'écran verrouillé ; et **aucun
bouton d'action n'accorde d'autorisation** — l'unique action ouvre la tâche,
là où l'utilisateur voit l'effet exact avant de trancher.

### Fichiers

| Fichier | Rôle |
|---|---|
| `TaskControlModels.swift` | DTO et miroir de la machine à états |
| `TaskControlAPI.swift` | Extension du client HTTP (cookie, Origin, CSRF réutilisés) |
| `TaskControlStore.swift` | État observable, polling suspendu hors écran, reprise par rang |
| `TasksView.swift` | Trois colonnes, feuille de création, lignes de candidat |
| `TaskDetailView.swift` | Six onglets |
| `TaskNotifications.swift` | Notifications et badge |
| `JarvisMacTests/TaskControlDecodingTests.swift` | 14 tests (décodage, repli, machine à états) |

---

## 4. Direction artistique

### Intention

Esthétique **« majordome IA de luxe sombre »** — Iron Man / JARVIS, mais filtré par le langage Apple contemporain (Liquid Glass macOS 26, SF Symbols, typo rounded).  
Pas de dashboard SaaS générique, pas d’emoji, pas de pastels pastel AI purple-on-white.

### Palette (`JarvisPalette`)

| Token | RGB approx. | Usage |
|---|---|---|
| **Blue** | `0.26, 0.52, 1.0` | Primaires, bulles user, boutons send |
| **Cyan** | `0.25, 0.86, 1.0` | Accents, eyebrows, horaires, icônes actives |
| **Indigo** | `0.43, 0.37, 1.0` | Dégradés orbe / fond |
| **Warm** | `1.0, 0.66, 0.25` | Disponible (urgence douce / chaleur) — usage secondaire |

États système : vert (prêt / live), orange (verrouillé / sync), rouge (offline / enregistrement micro).

### Fond (`JarvisBackdrop`)

Empilement :

1. `windowBackgroundColor` système
2. Dégradé linéaire blue → transparent → indigo (coin haut-gauche → bas-droit)
3. Halo radial cyan en haut à droite

→ Atmosphère **profonde, froide, technologique**, sans texture bruitée ni photo.

### Matériaux « glass »

Modifier `.jarvisGlass(cornerRadius:)` :

- **macOS 26+** : `glassEffect(.regular)` + fond blanc 5,5 % + stroke blanc 10 %
- **Repli** : `.ultraThinMaterial` + stroke blanc 12 %

Coins typiques : 14–18 (composers / métriques), 22 (cartes), 24–30 (palette / lock), 26 (Glance).

### Orbe (`JarvisOrb`) — signature visuelle

Cercle dégradé cyan → blue → indigo, icône `sparkles`, halo flou, pulse lent (2,4 s) si actif.  
État inactif : gris. Accessibilité : « Jarvis opérationnel / indisponible ».  
Présent dans : sidebar, Today hero, Chat welcome, Lock, Menu bar, Glance, System, Widget.

C’est le **marque-signe** de l’app — l’équivalent d’un logo animé discret.

### Typographie

| Rôle | Traitement |
|---|---|
| Eyebrows de section | Caption2 semibold, tracking ~1.4, cyan, UPPERCASE |
| Titres de page | `largeTitle` semibold, tracking négatif |
| Hero Today / Chat welcome | System **rounded** 28–36, tracking négatif |
| Métriques chiffrées | Rounded semibold (34 / 25) |
| Horaires agenda | Monospaced subheadline cyan |
| Corps | SF Pro système ; secondaire / tertiaire pour hiérarchie |
| Branding sidebar | « JARVIS » headline bold tracking 1.1 + sous-ligne 8 pt « PERSONAL INTELLIGENCE » |

### Iconographie

100 % **SF Symbols** (pas d’illustration custom dans le code) :

- Sections : `sparkles`, `bubble.left.and.bubble.right.fill`, `checkmark.circle.fill`, `brain.head.profile.fill`, `waveform.path.ecg`
- Actions récurrentes : `command`, `arrow.clockwise`, `mic.fill`, `arrow.up`, `pin.fill`, `sun.max`, `scope`

### Motion

- Pulse orbe (loop easeInOut)
- Scroll auto messages (easeOut 0,2 s)
- Pas de particules, pas de glow agressif multi-couches, pas d’animation de page flashy

### Langage de marque dans l’UI

Vocabulaire volontairement **formel / majordome**, aligné persona JARVIS :

- « signaux » (pas « notifications »)
- « cœur » (pas « serveur »)
- « actions » (pas « todos »)
- « Jarvis Pulse »
- « Que puis-je faire pour vous ? »
- « Votre journée est sous contrôle. »
- Greeting contextuel : Bonjour / Bon après-midi / Bonsoir + prénom (`user` API)

Ton : calme, précis, zéro emoji, zéro exclamation marketing.

---

## 5. Design system — composants réutilisables

Fichier : `JarvisMac/DesignSystem.swift`.

| Composant | Rôle |
|---|---|
| `JarvisPalette` | Couleurs marque |
| `.jarvisGlass` | Conteneur glassmorphique |
| `.jarvisCardPadding` | Padding carte 18 |
| `JarvisSecondaryButtonStyle` | Bouton secondaire glass (pas filled) |
| `JarvisBackdrop` | Fond de fenêtre |
| `JarvisOrb` | Marque animée |
| `SectionHeader` | Eyebrow + titre + sous-titre |
| `StatusPill` | Pastille état (capsule + point coloré) |
| `EmptyState` | État vide (symbole léger + titre + sous-titre) |
| `WidgetWindowConfigurator` | NSWindow floating / transparent / all Spaces |

Patterns UX récurrents :

- **Cartes glass** en grille 2 colonnes (Today, mémoire)
- **Empty states** rédigés (jamais un écran blanc)
- **Pills de priorité** (Prioritaire / Normal / Secondaire)
- **Métriques horizontales** compactes (icône + valeur + label)

---

## 6. Parcours et états de connexion

`ConnectionPhase` :

| Phase | UI | Sens |
|---|---|---|
| `checking` | Orbe + ProgressView « Réveil de Jarvis » | Bootstrap API |
| `offline(reason)` | Boutons « Démarrer le cœur » + « Réessayer » | Lance `jarvis_full_restart.sh --daemon --no-clean` |
| `setupRequired` | SecureField création PIN/passphrase | Premier setup = même secret que le web |
| `locked` | SecureField déverrouillage | Session absente / logout / 401 |
| `ready` | Shell complet | Cookies + CSRF + WebSocket |

Après `ready` :

1. Demande autorisation notifications natives
2. Connexion WebSocket
3. `refresh()` (tâches, notifs, agenda, status, intégrations, conversations)
4. Relais notifs `urgent`/`high` vers Notification Center

---

## 7. Navigation principale

### Sidebar

- Orbe 32 + branding
- Liste `AppSection` (sélection persistante dans `AppModel`)
- CTA bas « Demander à Jarvis » + hint `⇧⌘J`

### Sections

| Section | Raccourci menu | Contenu |
|---|---|---|
| **Aujourd’hui** | ⌘1 | Dashboard du jour |
| **Conversation** | ⌘2 | Chat split + historique |
| **Actions** | ⌘3 | Tâches + agenda |
| **Mémoire** | — | Grille conversations / recherche |
| **Terminal** | ⌘4 | Session SSH sur une machine du tailnet |
| **Système** | — | Capacités + diagnostics |

### Toolbar détail

- Pill « Temps réel » / « Reconnexion »
- Bouton palette commandes
- Actualiser (désactivé pendant refresh)

### Menu macOS « Jarvis »

- Navigation sections + palette + Actualiser (⌘R)

---

## 8. Capacités par écran

### 8.1 Aujourd’hui (`TodayView`)

**Job-to-be-done** : en un regard, savoir quoi faire maintenant.

- Hero date + greeting + résumé dynamique + grand orbe
- 4 métriques : actions ouvertes, signaux, événements, LIVE/SYNC
- Grille 2×2 :
  - **Focus** — première tâche, priorité, terminer
  - **Agenda** — jusqu’à 4 événements (heure monospaced cyan)
  - **Signaux** — cliquer = marquer lu
  - **Jarvis Pulse** — état WS, agents, briefing matin, aller au chat
- Carte briefing (si généré) avec texte sélectionnable

### 8.2 Conversation (`ChatView`)

**Job** : parler à Jarvis comme sur le web, en natif.

Layout split :

- **Gauche (245 px)** — liste conversations, pin cyan, nouvelle (`square.and.pencil`)
- **Droite** — welcome orbe + 3 suggestions, ou fil de messages + composer

Composer :

- Pill statut (Écoute / Prêt / Jarvis réfléchit…)
- Toggle « Lire la réponse » (TTS via WS)
- TextField multi-lignes 1–5
- Micro (enregistrement AAC 16 kHz → binaire WS)
- Send circulaire blue

Bulles :

- User : fond blue opaque, alignées droite
- Assistant : glass, icône sparkles cyan, markdown rendu (`AttributedString`)
- Streaming : ProgressView puis point cyan « en cours »
- System : triangle orange

Événements WS gérés : `connected`, `chunk`, `response`, `response_followup`, `response_clean`, `transcript`, `done`, `speech_done`, `status`, `routing`, `processing`, `error`, `conversation_updated`, `action_pending`, …

### 8.3 Actions (`ActionsView`)

- Quick-add glass : titre + picker priorité + Ajouter
- Liste tâches (toggle done, pills, catégorie, échéance)
- Colonne « Prochainement » agenda + ouvrir Calendar.app

### 8.4 Mémoire (`MemoryView`)

- Recherche locale titre / dernier message
- Métriques : conversations, agents, cerveau principal
- Grille adaptative de cartes → ouvre la conversation dans Chat

> Note : ce n’est **pas** encore la mémoire profonde (people, facts, patterns) du web — uniquement l’historique conversationnel exposé par `/api/conversations`.

### 8.5 Système (`SystemView`)

- Hero : phase, URL API, messages du jour, coût USD du jour
- Grille capacités (point vert/gris) :
  - Conversation WebSocket
  - Microphone / STT
  - Voix / TTS
  - Mail, Calendar, iMessage, Météo
  - Contrôle Mac (shell)
- Contrôles : Reconnecter, Actualiser, Réglages, Ouvrir le projet, Verrouiller

### 8.6 Terminal (`TerminalView`) — ⌘4

Session `ssh` sur une machine du tailnet, rendue par un émulateur VT intégré.

- Barre : pastille d'état Tailscale, menu des machines découvertes, hôte / utilisateur / port, réglages, Connecter (⇧⌘↩)
- Surface : émulateur 256 couleurs + couleur vraie, historique 5 000 lignes, sélection à la souris
- Raccourcis : ⌘C copier, ⌘V coller, ⌘K effacer, ⇧⇞/⇧⇟ historique, ⌘+/⌘− taille
- Réglages : chemin de clé privée, touche Option en Meta, commande `ssh` exacte affichée

Conception, garde-fous et limites : **§17**.

### 8.7 Palette de commandes (`CommandPaletteView`) — ⇧⌘J

Sheet glass 590 px :

- Champ « Chercher une commande ou poser une question… »
- Commandes filtrables : Aujourd’hui, Nouvelle conversation, Glance, Créer action, Briefing, Capacités
- Entrée sur une requête libre → envoie en chat
- Pill statut live en bas

### 8.8 Barre des menus (`MenuBarView`)

- Orbe + phase
- Champ « Demander rapidement… » (si ready)
- Compteurs actions / signaux
- Ouvrir Jarvis / Nouvelle conversation / Glance / Quitter

Icône menu : `sparkles` si ready, sinon `circle.dashed`.

### 8.9 Jarvis Glance (`DeskWidgetView`)

Fenêtre flottante (level `.floating`, tous Spaces, title bar cachée) :

- Prochaine action
- 3 métriques
- Premier signal
- Parler / Actualiser / pill phase

### 8.10 Widget WidgetKit (`JarvisGlanceWidget`)

- Small / Medium
- Ping `https://127.0.0.1:8081/api/auth/status` toutes les 5 min
- Greeting horaire + état cœur
- Clic → `jarvis://today`

### 8.11 Réglages (`SettingsView`)

- Adresse du cœur (`jarvis.baseURL`)
- Dossier projet (`jarvis.projectRoot`, défaut `~/JARVIS`)
- Mentions intégration Menu Bar / Glance / ⇧⌘J / widget
- Enregistrer et reconnecter

---

## 9. Capacités fonctionnelles (contrat API consommé)

### REST

| Endpoint | Usage UI |
|---|---|
| `GET /api/auth/status` | Bootstrap + CSRF |
| `POST /api/auth/setup` | Premier secret |
| `POST /api/auth/unlock` | Déverrouillage |
| `POST /api/auth/logout` | Verrouiller |
| `GET/POST/PATCH /api/tasks…` | Actions |
| `GET /api/notifications` | Signaux |
| `POST /api/notifications/{id}/read` | Marquer lu |
| `GET /api/calendar` | Agenda |
| `GET /api/status` | Pulse / coûts / audio |
| `GET /api/integrations` | Grille capacités |
| `GET /api/conversations` | Historique |
| `GET /api/conversations/{id}` | Détail messages |
| `GET /api/briefing?kind=…` | Briefing matin |

### WebSocket `/ws`

- Texte streamé + option TTS
- Audio binaire (enregistrement micro)
- Switch / new conversation
- Réception chunks + audio TTS (`AVAudioPlayer`)
- Reconnexion auto ~2 s

### Services natifs locaux

| Service | Fichier | Rôle |
|---|---|---|
| `NativeAudioService` | `NativeServices.swift` | Micro AAC + playback TTS |
| `NativeNotifications` | idem | UNUserNotificationCenter (urgent/high) |
| `JarvisCoreLauncher` | idem | Lance `scripts/jarvis_full_restart.sh` ; reveal Finder |

### Ce que l’app native **ne couvre pas encore** (présent ailleurs dans JARVIS)

À titre de cartographie honnête du prototype 0.1 :

- Vues Food / Map / Fitness / Cognitive / TV / Contacts riches
- Journal, life profile éditable, people analytics
- Screen Time dashboard, doomscroll, time machine, etc.
- Confirmation visuelle complète des plans shell / Uber Eats (événements `action_pending` seulement signalés en statut)
- PWA / Service Worker (domaine web)
- Mode écoute continue longue / diarisation

L’utilisateur **y a accès via le chat** (même pipeline unifié côté serveur), mais **pas via des écrans dédiés** dans cette app.

---

## 10. UX — principes d’interaction

1. **Progressive disclosure** — Today d’abord ; profondeur via Conversation / Système.
2. **Toujours un chemin de secours** — offline → démarrer le cœur ; WS down → reconnecter / pill orange.
3. **Clavier-first macOS** — ⌘1–3, ⌘R, ⇧⌘J, Entrée palette.
4. **Multi-entrée** — fenêtre, menu bar, Glance, widget, deep link.
5. **Feedback d’état constant** — pills, orbe, labels « Jarvis réfléchit… », empty states rédigés.
6. **Confirmation implicite faible** — marquer notif lue au clic ; terminer tâche explicite.
7. **Dark only** — pas de light mode (choix produit, cohérence majordome nocturne).
8. **Accessibilité partielle** — labels orbe ; texte sélectionnable briefing / messages ; pas encore VoiceOver exhaustif documenté.

---

## 11. Structure du code source

```
native_mac/
├── README.md                 # Quick start
├── APPLICATION.md            # Ce dossier
├── project.yml               # XcodeGen
├── Config/
│   ├── JarvisMac-Info.plist
│   ├── JarvisWidget-Info.plist
│   └── JarvisMac.entitlements
├── scripts/build-prototype.sh
├── JarvisMac/                # App principale (~20 fichiers Swift)
│   ├── JarvisMacApp.swift    # Scenes
│   ├── AppModel.swift        # État + orchestration
│   ├── Models.swift          # DTOs + sections
│   ├── DesignSystem.swift    # DA / composants
│   ├── JarvisAPI.swift       # REST
│   ├── JarvisSocket.swift    # WS
│   ├── NativeServices.swift  # Audio / notifs / launcher
│   ├── RootView.swift / LockView.swift
│   ├── TodayView / ChatView / ActionsView / MemoryView / SystemView
│   ├── CommandPaletteView / MenuBarView / DeskWidgetView / SettingsView
│   ├── TerminalView.swift      # Section Terminal (SwiftUI)
│   ├── TerminalSurface.swift   # NSView : rendu Core Text, clavier, sélection
│   ├── TerminalEmulator.swift  # Moteur VT100/xterm (aucune dépendance UI)
│   ├── TerminalTheme.swift     # Palette ANSI 256 couleurs
│   ├── TerminalBridge.swift    # État de section : destination, session, tailnet
│   ├── SSHTerminalSession.swift # forkpty + ssh
│   ├── TailscaleService.swift  # Découverte des pairs (CLI locale)
│   ├── PreviewExporter.swift
│   └── Resources/Assets.xcassets
├── JarvisWidget/
│   └── JarvisWidget.swift
└── dist/
    ├── Jarvis.app
    └── Jarvis-Prototype.dmg
```

Ordre de grandeur : **~2 500–3 000 lignes Swift** (prototype dense, pas de framework UI tiers).

---

## 12. Build, distribution, preview

### Rebuild

```bash
cd native_mac
./scripts/build-prototype.sh
```

Produit `dist/Jarvis.app` (signé ad hoc) + DMG « Jarvis Prototype ».

### Lancer

1. Cœur : `scripts/jarvis_full_restart.sh --daemon --no-clean` (ou bouton LockView)
2. `open native_mac/dist/Jarvis.app`
3. Déverrouiller avec le PIN/passphrase web

### Preview UI sans backend

```bash
JARVIS_UI_PREVIEW=1 open …   # ou arguments --ui-preview / --export-ui-preview
```

Charge un snapshot fictif (tâches, agenda, conversations) pour captures / CI visuelle.

---

## 13. Sécurité côté client (périmètre app)

| Contrôle | Détail |
|---|---|
| Auth | Même PIN/passphrase + cookies session que le web |
| CSRF | Jeton synchronisé sur mutations POST/PATCH |
| TLS loopback | Trust délégué **uniquement** localhost / 127.0.0.1 / ::1 |
| Secrets | Jamais stockés en clair dans l’app ; setup via API |
| Sandbox | Off (prototype ; accès micro + scripts locaux) |
| Hardened Runtime | Activé dans le target |
| Notifications | Filtrées urgent/high uniquement |
| Terminal SSH | Authentification déléguée à `ssh` ; aucun mot de passe ni passphrase stocké ou saisi par l'app |
| Clé d'hôte | Vérification `ssh` par défaut conservée — aucune option ne l'affaiblit |
| Verrouillage | La session SSH est coupée au verrouillage de l'app (`AppModel.logout`) |

Limites assumées du prototype :

- Pas de notarisation Developer ID
- Widget ping fixe sur `https://127.0.0.1:8081` (ignore l’URL Settings)
- Sandbox off = surface plus large qu’une app Mac App Store

---

## 14. Relation avec le reste de JARVIS

| Couche | Qui porte quoi |
|---|---|
| Intelligence / mémoire / agents | Backend Python |
| UI web bureau | `frontend/` + vues `web/` |
| UI téléphone | `web_mobile/` |
| UI TV | `tv/` |
| **UI Mac native** | **`native_mac/` ← ce document** |
| Daemon audio / wake | `scripts/audio_daemon.py`, etc. |

L’app Mac est une **coque de présence** : elle rend Jarvis tangible sur le bureau macOS (orbe, glass, Glance, menu bar) sans fragmenter le cerveau.

---

## 15. Synthèse direction artistique + UX (en une page)

**Univers** : sombre, froid-cyan, glass Liquid Glass, orbe sparkles, typo rounded pour les moments « héros ».  
**Personnalité** : majordome britannique discret — vocabulaire « cœur / signaux / actions », zéro emoji.  
**Architecture UX** : shell split + 5 destinations + 4 surfaces satellites (menu, glance, settings, widget).  
**Interaction** : clavier, chat unifié, micro, TTS optionnel, refresh explicite, fail-closed.  
**Maturité** : prototype **0.1.0** fonctionnel pour le quotidien (today / chat / tasks / statut), pas un port complet de toutes les vues web.  
**Critère de réussite esthétique** : si on retire la sidebar, l’orbe + le cyan + le greeting suffisent encore à reconnaître Jarvis.

---

## 16. Fichiers clés à ouvrir en premier

1. `JarvisMac/DesignSystem.swift` — DA
2. `JarvisMac/RootView.swift` — shell
3. `JarvisMac/TodayView.swift` — dashboard
4. `JarvisMac/ChatView.swift` — conversation
5. `JarvisMac/AppModel.swift` — comportements
6. `JarvisMac/JarvisMacApp.swift` — scènes macOS
7. `README.md` — démarrage rapide

---

## 17. Terminal distant — pont SSH par Tailscale

La section **Terminal** (⌘4) ouvre une session `ssh` vers une machine du
tailnet — en pratique le Mac mini serveur — et l'affiche dans un émulateur VT
intégré à l'app.

### Le pont est côté client, pas côté cœur

Aucune route n'a été ajoutée au backend. Faire passer un shell distant par
FastAPI aurait créé une exécution de commandes arbitraires derrière le cookie
de session applicatif — exactement ce que le reste du projet refuse (plan shell
allowlisté, confirmation humaine, workspace isolé). Ici, l'app lance `ssh`
localement : **la frontière d'authentification est SSH**, avec ses clés, son
`~/.ssh/config` et son `known_hosts`. Jarvis ne voit rien de la session, et un
cœur compromis n'ouvre aucun shell.

Deux verrous se cumulent malgré tout : la section n'est atteignable qu'après
déverrouillage de l'app, et `AppModel.logout()` coupe la session SSH — laisser
un shell vivant derrière l'écran verrouillé annulerait le verrou.

### `forkpty`, et pas `Process`

`ssh` doit être **chef de session** et posséder son terminal de contrôle : sans
`/dev/tty`, il ne peut ni demander la confirmation d'une clé d'hôte inconnue,
ni lire une passphrase. `Process` n'expose aucun moyen d'appeler `setsid()`.
`SSHTerminalSession` fait donc `forkpty` + `execve`, avec `argv` et `envp`
construits **avant** le fork — entre `fork` et `exec`, seules les fonctions
async-signal-safe sont légitimes. Vérifié : le fils apparaît en `Ss+`, et
l'invite « Are you sure you want to continue connecting » s'affiche bien dans
la section.

L'environnement transmis est une liste explicite (`HOME`, `PATH`, `LANG`,
`SSH_AUTH_SOCK`…) plus `TERM=xterm-256color` : l'enfant n'a pas besoin de
l'état interne de l'application.

### Ce que la validation empêche

Hôte et utilisateur sont validés caractère par caractère et ne peuvent pas
commencer par `-`. Sans cela, un hôte nommé `-oProxyCommand=…` serait lu par
`ssh` comme une option, c'est-à-dire une exécution de commande locale. La
ligne de commande exacte est affichée dans le panneau de réglages, et
**aucune option n'affaiblit la vérification de clé d'hôte**.

Aucun mot de passe n'est stocké ni saisi par l'application : les invites de
`ssh` sont rendues dans le terminal, où l'utilisateur répond lui-même.

### Découverte Tailscale, en lecture seule

`TailscaleService` appelle `tailscale status --json` sur le démon local — aucun
appel réseau, aucune clé d'API. Il ne sert qu'à *proposer* des machines : le
nom MagicDNS est préféré au `HostName`, qui est un nom d'affichage parfois
générique (un iPhone s'y annonce « localhost »). Si Tailscale est absent ou
arrêté, la section reste utilisable en saisissant une adresse à la main.

### L'émulateur

`TerminalEmulator` est un moteur VT100/xterm sans dépendance UI ni bibliothèque
tierce : attributs SGR (16 / 256 / couleur vraie), régions de défilement,
écran alterné, insertion/suppression de lignes et de caractères, retour à la
ligne différé, historique de 5 000 lignes, UTF-8 avec diacritiques combinants
et glyphes double largeur, titre OSC, réponses DSR/DA. Le rendu passe par Core
Text dans un `NSView`, qui porte aussi le clavier (touches mortes comprises,
via `NSTextInputClient`), la sélection et la molette.

**Ce qu'il ne fait pas, volontairement** : pas de reflow au
redimensionnement (une ligne coupée reste coupée), pas de rapport de souris,
pas de Sixel, pas de jeux de caractères hérités. Les programmes plein écran
usuels (`vim`, `htop`, `tmux`) fonctionnent ; un programme exigeant le
signalement de la souris ne verra pas les clics.

### Mise en service

1. Sur la machine cible : Réglages Système → Général → Partage → **Connexion à distance**.
2. Une clé publique dans `~/.ssh/authorized_keys` de la cible — sinon `ssh`
   demandera un mot de passe à chaque session, dans le terminal.
3. Tailscale actif des deux côtés.
4. Section Terminal → menu **Machines** → **Connecter**.

### Limites assumées

- Une seule session à la fois ; changer de machine ferme la précédente.
- Pas de reconnexion automatique après une coupure réseau : `ServerAliveInterval`
  détecte la perte, la reconnexion reste un geste explicite.
- La première connexion à un hôte inconnu demande une confirmation dans le
  terminal — c'est voulu, et non contournable depuis l'interface.
- Le rendu est vérifié hors écran et par des bancs d'essai dédiés, pas par une
  suite de tests versionnée : le projet Xcode n'a pas de cible de tests.

---

*Fin du dossier. Source de vérité code : `native_mac/`. Toute évolution UI devrait d’abord passer par `DesignSystem.swift` pour préserver la cohérence glass / palette / orbe.*
