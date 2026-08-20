# Runtime OpenCode de JARVIS

Cette documentation ne décrit que le plugin fournisseur situé dans
`integrations/opencode`. Le domaine, l'API et les clients JARVIS restent
génériques. JARVIS est la source de vérité des runs, validations, événements,
artefacts et permissions ; OpenCode n'est qu'un moteur local remplaçable.

Version intégrée : **OpenCode 1.18.16**, tag `v1.18.16`, commit amont
`a3647eb025c7615159d417dcc49fc39fdaeba65b`, vérifiés le 2026-08-11.

- [Architecture](ARCHITECTURE.md)
- [Configuration](CONFIGURATION.md)
- [Installation](INSTALLATION.md) et [mise à niveau](UPGRADE.md)
- [Sécurité, menaces, avis et licence](SECURITY.md)
- [Modèle de menace](THREAT_MODEL.md)
- [Exploitation et dépannage](OPERATIONS.md)
- [Agents et permissions](AGENTS.md)
- [Pont MCP à capacités](MCP.md) et [contrat du bridge](MCP_BRIDGE.md)
- [Reprise après incident](RECOVERY.md)
- [Désinstallation](REMOVAL.md), [runbook de retrait](UNPLUG_RUNBOOK.md)
  et [preuve de suppression](REMOVAL_PROOF.md)
- [Rapport de validation](TEST_REPORT.md)
- [Audit du pipeline du 18 août 2026](PIPELINE_AUDIT_2026-08-18.md)

Les valeurs faisant foi sont `plugin.json`, `release-manifest.json`,
`config/defaults.json` et `config/opencode.json`. Toute divergence entre ces
fichiers et cette documentation doit être traitée comme une anomalie.
