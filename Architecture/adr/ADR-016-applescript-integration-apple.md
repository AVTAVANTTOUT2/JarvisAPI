# ADR-0001 : AppleScript comme unique intégration Apple

**Date** : 2026-07-11 (rétroactif — décision prise au démarrage du projet)
**Statut** : Accepté

## Contexte

Jarvis doit accéder aux données Apple Mail, Calendar, iMessage et Contacts sur macOS. Apple propose plusieurs approches : OAuth/API cloud (iCloud), EventKit/Contacts framework (Swift), et AppleScript (automation macOS native).

Le projet cible un utilisateur unique sur un Mac dédié 24/7. La simplicité d'implémentation et l'absence de dépendance cloud sont prioritaires.

## Décision

Toutes les intégrations Apple passent par AppleScript via `osascript`, centralisé dans `integrations/_applescript.py`.

Modules concernés :
- `integrations/mail.py` — Apple Mail
- `integrations/calendar_api.py` — Calendar
- `integrations/imessage.py` — iMessage (envoi)
- `integrations/imessage_reader.py` — iMessage (lecture chat.db)
- `integrations/contacts.py` — Contacts
- `integrations/notifications_macos.py` — Notifications
- `integrations/computer.py` — Contrôle système

## Alternatives considérées

| Alternative | Avantages | Inconvénients | Raison du rejet |
|---|---|---|---|
| OAuth iCloud API | Standard, portable | Tokens expirants, dépendance cloud, setup complexe, Apple ne fournit pas d'API Mail complète | Viole Privacy First et Local First |
| Swift EventKit/Contacts | Natif, performant | Nécessite compilation Swift, bridge Python-Swift complexe, maintenance lourde | Complexité disproportionnée pour un utilisateur unique |
| Shortcuts + HTTP | Sans code, visuel | Limité, pas scriptable, pas fiable pour du 24/7 | Pas adapté à un système autonome |

## Conséquences

### Positives
- Zéro OAuth, zéro token, zéro renouvellement
- Accès local direct, aucune dépendance réseau
- Implémentation rapide et lisible
- Helper unique `_applescript.py` pour tous les modules

### Négatives
- Dépendance forte à macOS (non portable Linux/Windows)
- Fragilité si Apple modifie le dictionnaire AppleScript d'une app
- Permissions TCC nécessaires (Accessibility, Full Disk Access)
- Performance inférieure à un framework natif Swift

### Risques
- Apple pourrait déprécier AppleScript (risque faible à moyen terme, maintenu depuis 30+ ans)
- Mise à jour macOS cassant un script existant (mitigé par tests)
