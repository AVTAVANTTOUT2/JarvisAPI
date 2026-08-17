# Jarvis pour macOS — prototype natif

Cette app SwiftUI est une nouvelle surface pour le cœur JARVIS existant. Elle ne duplique ni les agents, ni SQLite, ni le pipeline conversationnel : elle se connecte à FastAPI via REST et WebSocket.

## Utiliser le prototype

1. Démarrer le backend JARVIS normalement (`scripts/jarvis_full_restart.sh --daemon --no-clean`).
2. Ouvrir `dist/Jarvis.app`, ou monter `dist/Jarvis-Prototype.dmg` et glisser Jarvis dans Applications.
3. L’app détecte automatiquement les installations locales usuelles (`https://127.0.0.1:8081`, supervisor `9000`, puis HTTP `8080/8081/9000`). L’adresse retenue reste modifiable dans Réglages. Les certificats autosignés ne sont acceptés que pour les adresses loopback locales.
4. Déverrouiller avec le même PIN/passphrase que le dashboard web.

La fenêtre principale distingue explicitement **Missions Jarvis** (travaux planifiés, validés puis exécutés par l’agent) et **À faire** (liste personnelle simple à cocher), aux côtés d’Aujourd’hui, Conversation, Mémoire, Terminal et Système. La barre des menus offre un accès rapide et « Jarvis Glance » ouvre un panneau compact flottant. Une extension WidgetKit est incluse dans l’app pour préfigurer le widget de bureau.

## Terminal distant (⌘4)

La section Terminal ouvre une session `ssh` vers une machine du tailnet — le Mac
mini serveur depuis un poste client — et l’affiche dans un émulateur VT intégré.
Le pont est entièrement côté client : aucune route d’exécution n’a été ajoutée au
backend, et l’authentification reste celle de `ssh` (vos clés, votre
`known_hosts`). L’application ne stocke ni mot de passe ni passphrase ; les
invites de `ssh` s’affichent dans le terminal.

Prérequis sur la machine cible : « Connexion à distance » activée dans Réglages
Système, une clé publique dans `~/.ssh/authorized_keys`, et Tailscale actif des
deux côtés. Le menu **Machines** liste les pairs vus par le démon Tailscale
local ; une adresse peut aussi être saisie à la main.

Conception détaillée, garde-fous et limites : `APPLICATION.md`, section 17.

## Reconstruire

```bash
cd native_mac
./scripts/build-prototype.sh
```

Le script génère le projet Xcode avec XcodeGen, compile l’application, applique une signature locale ad hoc et produit le DMG. Une distribution publique nécessitera ensuite un certificat Developer ID et une notarisation Apple.

La CI régénère aussi `JarvisMac.xcodeproj` et refuse tout diff, puis compile en
configuration Release avec la signature désactivée. Elle vérifie explicitement
la présence de `Jarvis.app` et du plugin `JarvisWidget.appex` embarqué.
