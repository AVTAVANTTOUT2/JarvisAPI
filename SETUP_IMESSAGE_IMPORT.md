# Setup Import iMessage — A faire au retour sur le Mac

> **Pourquoi cette procedure ?** Cursor (remote SSH) ne peut pas lire `chat.db` meme avec Full Disk Access accorde a Cursor.app — c'est `cursor-server` (node) qui ouvre le fichier, et macOS TCC refuse. Un daemon permanent a ete cree pour contourner ce probleme. Il ne reste qu'une seule action manuelle.

---

## Etape 1 — Accorder Full Disk Access a Python (1 minute)

1. Ouvrir **Reglages Systeme** -> **Confidentialite et securite** -> **Acces complet au disque**

2. Cliquer le bouton **+**

3. Appuyer sur **Cmd+Shift+G** (Aller au dossier) et coller ce chemin exact :

```
/opt/homebrew/Cellar/python@3.12/3.12.13_4/Frameworks/Python.framework/Versions/3.12/Resources/
```

4. Selectionner **Python.app** et cliquer **Ouvrir**

5. **Activer l'interrupteur** a cote de Python.app dans la liste

> Si le chemin ci-dessus n'existe pas (version de Python differente), executer dans Terminal :
> ```bash
> readlink -f /Users/zeldris/JarvisAPI/venv/bin/python
> ```
> Puis remplacer `bin/python3.12` par `Resources/Python.app` dans le chemin retourne.

---

## Etape 2 — Verifier que le daemon est vivant

```bash
curl http://127.0.0.1:8193/health
```

Doit retourner `"ok": true`. Si ce n'est pas le cas, attendre 60 secondes (le watchdog verifie automatiquement) et reessayer.

> Si `connection refused` : le daemon n'a pas demarre. Verifier :
> ```bash
> launchctl list | grep imessage
> ```
> Si absent, relancer :
> ```bash
> launchctl load ~/Library/LaunchAgents/com.jarvis.imessage-daemon.plist
> ```

---

## Etape 3 — Lancer l'import

```bash
curl -X POST http://127.0.0.1:8193/import/start
```

Reponse attendue : `{"status": "started", "mode": "initial"}`

---

## Etape 4 — Suivre la progression

```bash
# Progression en direct
curl http://127.0.0.1:8193/import/progress

# Statistiques apres import
curl http://127.0.0.1:8193/stats
```

L'import prend quelques minutes (62 MB de `chat.db`, traite par batches de 5000 messages).

---

## Etape 5 — Verifier que tout est OK

```bash
# Audit de coherence
curl -X POST http://127.0.0.1:8193/reconcile
```

Doit retourner `"ok": true`.

---

## Resume des commandes (copier-coller)

```bash
# 1. Verifier le daemon
curl http://127.0.0.1:8193/health

# 2. Lancer l'import
curl -X POST http://127.0.0.1:8193/import/start

# 3. Surveiller
curl http://127.0.0.1:8193/import/progress

# 4. Stats finales
curl http://127.0.0.1:8193/stats
```

---

## Ce qui est deja en place (aucune action requise)

- **LaunchAgent** : `~/Library/LaunchAgents/com.jarvis.imessage-daemon.plist`
- Demarre automatiquement au boot (`RunAtLoad=true`)
- Redemarre automatiquement en cas de crash (`KeepAlive=true`)
- Watchdog toutes les 60 secondes (verifie l'acces a `chat.db`)
- Le daemon est le **seul** processus qui ouvre `chat.db` — tout le reste passe par son API

---

## En cas de probleme

```bash
# Voir les logs du daemon
tail -100 /Users/zeldris/JarvisAPI/data/logs/imessage-daemon.log

# Redemarrer le daemon
launchctl unload ~/Library/LaunchAgents/com.jarvis.imessage-daemon.plist
launchctl load ~/Library/LaunchAgents/com.jarvis.imessage-daemon.plist

# Diagnostic complet
curl -X POST http://127.0.0.1:8193/doctor
```
