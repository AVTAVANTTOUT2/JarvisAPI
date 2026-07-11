# 13 — Architecture de Plugins (ADR-013)

**Date** : 11 juillet 2026
**ADR** : ADR-013
**Statut** : Proposé

---

## Problème

Le cœur de JARVIS (`main.py`, `pipeline.py`, agents) dépend directement des connecteurs externes (Apple, météo, recherche web). Ajouter un nouveau connecteur (Telegram, Signal, Spotify) nécessite de modifier le code core.

## Solution

Définir une **interface Plugin** standard. Chaque connecteur externe l'implémente. Le cœur de JARVIS ne connaît que l'interface, pas les implémentations.

## Interface Plugin

```python
class Plugin(ABC):
    """Interface que tout connecteur externe doit implémenter."""

    # ── Métadonnées ──
    @property
    @abstractmethod
    def name(self) -> str: ...
    
    @property
    @abstractmethod
    def version(self) -> str: ...

    @property
    def permissions(self) -> list[str]:
        """Permissions macOS/OS requises."""
        return []

    @property
    def config_schema(self) -> dict:
        """Schéma JSON Schema pour la configuration du plugin."""
        return {}

    # ── Cycle de vie ──
    @abstractmethod
    async def start(self) -> None: ...

    @abstractmethod
    async def stop(self) -> None: ...

    @abstractmethod
    async def health(self) -> dict:
        """Retourne l'état de santé du plugin."""
        ...

    # ── Événements émis ──
    @property
    def events(self) -> list[str]:
        """Liste des types d'événements que ce plugin peut émettre."""
        return []

    # ── Routes API (optionnel) ──
    def register_routes(self, app: FastAPI) -> None:
        """Enregistre des routes API supplémentaires."""
        pass
```

## Plugins existants (à extraire)

| Plugin | Fichier actuel | Événements émis | Permissions |
|---|---|---|---|
| **Apple Data** | `integrations/apple_data.py` | `imessage.imported`, `imessage.sync_completed` | Full Disk Access |
| **AI Service** | `ai_service.py` (nouveau) | `llm.call_completed`, `embedding.generated` | Réseau (API DeepSeek) |
| **Weather** | `integrations/weather.py` | `weather.updated` | Réseau (OpenWeatherMap) |
| **Web Search** | `integrations/web_search.py` | `search.completed` | Réseau (Tavily) |
| **Computer Control** | `integrations/computer.py` | `terminal.executed`, `file.found` | Shell, Automation |
| **Notifications macOS** | `integrations/notifications_macos.py` | `desktop_notification.sent` | Automation |

## Plugins futurs

| Plugin | Événements émis | Permissions | Priorité |
|---|---|---|---|
| **Telegram** | `telegram.message.received`, `telegram.message.sent` | Réseau, API Token | P2 |
| **Signal** | `signal.message.received` | Réseau, signal-cli | P3 |
| **WhatsApp** | `whatsapp.message.received` | Réseau, API Business | P3 |
| **Spotify** | `spotify.track.changed`, `spotify.playlist.updated` | Réseau, OAuth | P2 |
| **Home Assistant** | `homeassistant.device.changed`, `homeassistant.automation.triggered` | Réseau, API Token | P2 |
| **GitHub** | `github.pr_opened`, `github.issue_created` | Réseau, OAuth | P2 |
| **Google Calendar** | `calendar.event_created` | Réseau, OAuth | P2 |
| **Notion** | `notion.page_updated` | Réseau, OAuth | P3 |
| **Obsidian** | `obsidian.note_updated` | Fichier local | P3 |

## Enregistrement d'un plugin

```python
# main.py (lifespan)
from plugins import plugin_registry

# Plugins core (toujours chargés)
plugin_registry.register(apple_data)
plugin_registry.register(ai_service)
plugin_registry.register(weather_service)

# Plugins optionnels (chargés si configurés)
if config.TELEGRAM_BOT_TOKEN:
    plugin_registry.register(telegram_plugin)

if config.SPOTIFY_CLIENT_ID:
    plugin_registry.register(spotify_plugin)

# Démarrage de tous les plugins enregistrés
await plugin_registry.start_all()
```

## Plugin Registry

```python
class PluginRegistry:
    def register(self, plugin: Plugin) -> None: ...
    async def start_all(self) -> None: ...
    async def stop_all(self) -> None: ...
    def get(self, name: str) -> Plugin: ...
    async def health_all(self) -> dict: ...
    def register_routes_all(self, app: FastAPI) -> None: ...
```

## Règles

1. **Isolation** : Un plugin ne peut pas importer un autre plugin. Communication via Event Bus uniquement.
2. **Configuration** : Chaque plugin lit sa configuration depuis `config.py` (`.env`). Pas de hardcoding.
3. **Health** : Chaque plugin expose sa santé via `plugin.health()`. Le endpoint `/health` agrège tous les plugins.
4. **Permissions** : Documentées dans `plugin.permissions`. Vérifiées au démarrage.
5. **Démarrage différé** : Un plugin qui échoue au démarrage ne bloque pas le reste du système.
