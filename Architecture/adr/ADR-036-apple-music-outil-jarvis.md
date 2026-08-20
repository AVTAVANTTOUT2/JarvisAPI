# ADR-036 : Apple Music est un outil JARVIS, pas une mission agentique

**Date** : 2026-08-19
**Statut** : Accepté
**Amende** : [ADR-016](./ADR-016-applescript-integration-apple.md) (pas d'osascript maison pour Music.app)

## Contexte

Le playback (« joue Werenoi », « met du werenoi ») était classé effet externe
(`musique` / `apple music` / `playlist`) et ouvrait une tâche + plan. Le MCP
`apple-music-mcp` n'était monté que dans OpenCode après `media:publish`.

## Décision

1. **Outil local**, même famille que Mail / météo : `integrations/apple_music.py`
   parle au binaire déjà installé (`PATH` puis `~/.local/bin/apple-music-mcp`).
2. **Pas de daemon**, pas de route `/api/music/*`. Control Center sonde `doctor`
   (`can_control: false`). Santé optionnelle, jamais critique.
3. Playback immédiat (play / pause / next / volume). Recherche **bibliothèque
   locale** seulement — `catalog_search` n'existe pas en v0.1.0.
4. OpenCode **garde** le même binaire pour les vrais workflows `media:publish`
   (transcode, ffmpeg). Le chat Cursor MCP n'est pas le chat JARVIS.
5. Les mots `musique` / `playlist` ne sont plus des effets externes agentiques.

## Conséquences

« met du werenoi » joue au tour suivant, ou refuse clairement si l'artiste
n'est pas dans Music.app. Aucun plan, aucune confirmation, aucun osascript
parallèle.
