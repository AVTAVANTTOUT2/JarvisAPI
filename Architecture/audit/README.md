# Rapports d'Audit Détaillés

## Rapport consolidé (sorties Cursor)

- **[`RAPPORT_PIRE_AUDIT.md`](./RAPPORT_PIRE_AUDIT.md)** — consolidation des sorties des agents Cursor d’audit ligne par ligne (P01–P18), findings classés du plus grave au moindre, annexe avec rapports bruts concaténés pour traitement Claude Opus.
- **[`rapports_bruts/`](./rapports_bruts/)** — un fichier Markdown par périmètre, extrait tel quel des conversations cloud agents + `manifest.json` + `findings_index.json`.
- **[`PROMPTS_AUDIT_LIGNE_PAR_LIGNE.md`](./PROMPTS_AUDIT_LIGNE_PAR_LIGNE.md)** — prompts de distribution (P01–P18) + Prompt 0 de consolidation.

### Couverture actuelle

| Périmètres | État |
|---|---|
| P01–P18 | Sorties récupérées et archivées (complet) |

### Audits de haut niveau (docs Architecture)

- `../01_CARTOGRAPHIE.md` : cartographie complète
- `../02_ANALYSE_PROBLEMES.md` : 23 problèmes classés
- `../03_AUDIT_TECHNIQUE.md` : audit backend, frontend, DB, sécurité
