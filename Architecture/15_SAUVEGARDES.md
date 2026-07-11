# 15 — Stratégie de Sauvegardes (ADR-015)

**Date** : 11 juillet 2026
**ADR** : ADR-015
**Statut** : Proposé

---

## Stratégie actuelle

- Sauvegarde quotidienne : `VACUUM INTO` → `data/backups/jarvis_YYYYMMDD_HHMMSS.db`
- Rotation : conserve les `BACKUP_KEEP` dernières (défaut 7)
- Chiffrement optionnel : Fernet (AES) si `BACKUP_ENCRYPTION_ENABLED=true`
- Restauration : `POST /api/backups/{name}/restore` avec snapshot de sécurité

## Stratégie complète

### Fréquence et rétention

| Type | Fréquence | Rétention | Déclencheur |
|---|---|---|---|
| **Full** | Quotidienne (04:15) | 7 jours | APScheduler |
| **Différentielle** | Toutes les heures | 24 heures | APScheduler |
| **Avant migration** | Manuelle | 30 jours | `/api/backups/run` |
| **Snapshot sécurité** | Avant toute restauration | 30 jours | Automatique |

### Procédure de sauvegarde automatique

```python
async def backup_full():
    """Sauvegarde complète avec vérification d'intégrité."""
    backup_path = BACKUP_DIR / f"jarvis_{timestamp}.db"
    
    # 1. Vérifier l'intégrité de la base source
    integrity = db.execute("PRAGMA integrity_check").fetchone()
    if integrity[0] != "ok":
        event_bus.emit(SystemError(
            module="backup",
            error_type="integrity_check_failed",
            detail=integrity[0]
        ))
        return False
    
    # 2. VACUUM INTO (copie cohérente sans bloquer les lectures)
    db.execute(f"VACUUM INTO '{backup_path}'")
    
    # 3. Vérifier l'intégrité de la sauvegarde
    backup_db = sqlite3.connect(backup_path)
    integrity = backup_db.execute("PRAGMA integrity_check").fetchone()
    backup_db.close()
    
    if integrity[0] != "ok":
        backup_path.unlink()  # Supprimer la sauvegarde corrompue
        return False
    
    # 4. Chiffrer si configuré
    if config.BACKUP_ENCRYPTION_ENABLED:
        encrypted_path = encrypt_backup(backup_path)
        backup_path.unlink()
        backup_path = encrypted_path
    
    # 5. Rotation : supprimer les anciennes
    rotate_backups(keep=config.BACKUP_KEEP)
    
    # 6. Événement
    event_bus.emit(BackupCompleted(
        path=str(backup_path),
        size_mb=backup_path.stat().st_size / 1024 / 1024,
        encrypted=config.BACKUP_ENCRYPTION_ENABLED
    ))
    
    return True
```

### Restauration

```python
async def restore_backup(backup_name: str) -> bool:
    """Restaure une sauvegarde avec snapshot de sécurité."""
    backup_path = BACKUP_DIR / backup_name
    
    # 1. Vérifier que le fichier existe
    if not backup_path.is_file():
        raise FileNotFoundError(f"Backup {backup_name} introuvable")
    
    # 2. Déchiffrer si nécessaire
    if backup_path.suffix == ".enc":
        backup_path = decrypt_backup(backup_path)
    
    # 3. Vérifier l'intégrité de la sauvegarde
    backup_db = sqlite3.connect(f"file:{backup_path}?mode=ro", uri=True)
    integrity = backup_db.execute("PRAGMA integrity_check").fetchone()
    backup_db.close()
    
    if integrity[0] != "ok":
        raise ValueError("Backup corrompu — restauration impossible")
    
    # 4. Snapshot de sécurité de la base courante
    safety_path = BACKUP_DIR / f"safety_snapshot_{timestamp}.db"
    db.execute(f"VACUUM INTO '{safety_path}'")
    
    # 5. Remplacer la base courante
    db.close()
    backup_path.replace(DB_PATH)
    
    # 6. Rouvrir la base
    init_db()
    
    # 7. Événement
    event_bus.emit(BackupRestored(
        backup=backup_name,
        safety_snapshot=str(safety_path)
    ))
    
    return True
```

### Validation des sauvegardes

Chaque semaine (dimanche 05:00), un job vérifie la dernière sauvegarde :

```python
async def validate_latest_backup():
    """Vérifie que la dernière sauvegarde est restaurable."""
    backups = sorted(BACKUP_DIR.glob("jarvis_*.db*"), reverse=True)
    if not backups:
        return
    
    latest = backups[0]
    
    # 1. Déchiffrer si nécessaire
    # 2. Ouvrir en READONLY
    # 3. PRAGMA integrity_check
    # 4. Vérifier que toutes les tables attendues sont présentes
    # 5. Vérifier le nombre de rows (pas une base vide)
    # 6. Logguer le résultat
```

### Tests de restauration

Un test automatisé (optionnel, `BACKUP_TEST_RESTORE=true`) restaure la dernière sauvegarde dans une base temporaire et vérifie :

1. Toutes les tables sont présentes
2. Les contraintes UNIQUE sont respectées
3. Les clés étrangères sont valides
4. Le nombre de rows est cohérent (±10% de la base courante)

### Reprise après corruption

```
┌──────────────┐
│ SQLITE_CORRUPT│
└──────┬───────┘
       │
       ▼
┌──────────────┐     ┌──────────────────┐
│ Tenter       │ NON │ Restaurer la     │
│ PRAGMA       │────▶│ dernière backup  │
│ integrity_   │     │ valide           │
│ check ?      │     └──────────────────┘
└──────┬───────┘
       │ OUI
       ▼
┌──────────────┐
│ Réparer via  │
│ .recover ?   │
└──────────────┘
```

### Configuration

```bash
BACKUP_ENABLED=true
BACKUP_DIR=./data/backups
BACKUP_KEEP=7
BACKUP_ENCRYPTION_ENABLED=false
BACKUP_ENCRYPTION_PASSPHRASE=
BACKUP_HOURLY_ENABLED=true
BACKUP_HOURLY_KEEP=24
BACKUP_VALIDATION_DAY=sunday
BACKUP_VALIDATION_TIME=05:00
BACKUP_TEST_RESTORE=false
```
