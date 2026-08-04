# Jarvis pour macOS — prototype natif

Cette app SwiftUI est une nouvelle surface pour le cœur JARVIS existant. Elle ne duplique ni les agents, ni SQLite, ni le pipeline conversationnel : elle se connecte à FastAPI via REST et WebSocket.

## Utiliser le prototype

1. Démarrer le backend JARVIS normalement (`scripts/jarvis_full_restart.sh --daemon --no-clean`).
2. Ouvrir `dist/Jarvis.app`, ou monter `dist/Jarvis-Prototype.dmg` et glisser Jarvis dans Applications.
3. L’app détecte automatiquement les installations locales usuelles (`https://127.0.0.1:8081`, supervisor `9000`, puis HTTP `8080/8081/9000`). L’adresse retenue reste modifiable dans Réglages. Les certificats autosignés ne sont acceptés que pour les adresses loopback locales.
4. Déverrouiller avec le même PIN/passphrase que le dashboard web.

La fenêtre principale propose Aujourd’hui, Conversation, Actions, Mémoire et Système. La barre des menus offre un accès rapide et « Jarvis Glance » ouvre un panneau compact flottant. Une extension WidgetKit est incluse dans l’app pour préfigurer le widget de bureau.

## Reconstruire

```bash
cd native_mac
./scripts/build-prototype.sh
```

Le script génère le projet Xcode avec XcodeGen, compile l’application, applique une signature locale ad hoc et produit le DMG. Une distribution publique nécessitera ensuite un certificat Developer ID et une notarisation Apple.

La CI régénère aussi `JarvisMac.xcodeproj` et refuse tout diff, puis compile en
configuration Release avec la signature désactivée. Elle vérifie explicitement
la présence de `Jarvis.app` et du plugin `JarvisWidget.appex` embarqué.
