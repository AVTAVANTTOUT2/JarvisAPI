# Contrat du bridge MCP

Le détail des scopes et outils est dans [MCP.md](MCP.md). Le broker vit dans le
processus JARVIS ; OpenCode ne reçoit aucune enveloppe de capacité sérialisée.

1. JARVIS crée un socket de bootstrap one-shot dans un dossier `0700`.
2. Après démarrage, le broker lie le PID du serveur OpenCode attendu.
3. Le proxy se présente ; UID, PID et ascendance sont vérifiés.
4. Le socket est réclamé atomiquement puis supprimé avant remise du bearer.
5. Le bearer est lié au même peer pour la connexion broker.
6. Un outil mutateur reste invisible sans grant parent exact et expirant.
7. Le grant lie run, outil et hash canonique des arguments ; il est consommé
   une seule fois et le journal durable précède l'effet.

Toute incohérence ferme le bridge. Les données d'outil sont toujours marquées
non fiables et ne peuvent étendre une permission.
