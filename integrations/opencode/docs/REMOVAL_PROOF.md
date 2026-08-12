# Preuve de suppression

La commande suivante copie le dépôt dans un dossier temporaire, retire
uniquement le plugin, refuse les symlinks, interdit réseau/spawn, compile les
sources, initialise une base fraîche, importe API/voix/iMessage, génère
l'OpenAPI, vérifie qu'aucun runtime n'est découvert et obtient
`provider_unavailable` sans résidu :

```bash
python -m integrations.opencode.tools.removal_proof
```

La preuve complète exécute en plus, **dans cette même copie sans plugin**, les
tests core/voix, Ruff, les deux frontends, Android debug/release, macOS Release
et les audits générés. Elle échoue si un outil ou un cache hors ligne requis
manque :

```bash
python -m integrations.opencode.tools.removal_proof --full
```

La preuve de livraison complète associe ce test hermétique aux gates CI déjà
exécutées sur le même SHA :

| Étape | Commande / preuve |
|---|---|
| 1–4 | copie sûre, retrait exact, scan fournisseur, compilation mémoire |
| 5–8 | DB fraîche, imports core/API/voix/iMessage, OpenAPI, provider absent |
| 9 | Pytest core, agentique, voix et iMessage ciblé |
| 10 | `python -m ruff check .` |
| 11 | `web`: installation hors ligne, tests + typecheck |
| 12 | `frontend`: installation hors ligne, tests + typecheck + build |
| 13 | Android debug/release hors ligne: tests + lint + assemble |
| 14 | macOS: tests Apple, xcodegen + build Release non signé |
| 15 | audits architecture/OpenAPI/SDK/dette avec `--check` |
| 16 | absence de processus, port, auth, log, DB ou fichier runtime résiduel |

Le résultat JSON contient exactement 16 étapes ordonnées. Sans `--full`, les
étapes 9–15 portent explicitement l'état `delegated_to_delivery_gates`; elles ne
sont donc jamais présentées comme exécutées. Avec `--full`, chacune doit être
`passed`. Aucun succès complet n'est revendiqué si l'une des deux parties
manque.
