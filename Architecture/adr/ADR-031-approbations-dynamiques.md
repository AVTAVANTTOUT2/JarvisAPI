# ADR-031 — Approbations dynamiques MCP

- Statut : accepté
- Date : 2026-08-13

## Contexte

Une action non préautorisée était refusée à la frontière MCP
(`capability_scope_denied`) sans créer de demande utilisable par JARVIS.

## Décision

Les outils éligibles (`DYNAMIC_APPROVAL_TOOLS`, actuellement
`jarvis_tasks_create`) restent listés. Un appel sans grant :

1. refuse l'effet ;
2. émet un callback vers l'adaptateur ;
3. persiste une `ApprovalRequest` (run, outil, action, workspace, profil,
   digest canonique, TTL, nonce, one-shot) ;
4. le run passe en `awaiting_approval` ;
5. la décision accorde une capability temporaire exacte, jamais une wildcard.

Rejeu, mauvais digest, mauvais run ou expiration : aucun effet.
`pending-before-effect` + receipt après effet. Pas d'élargissement d'enveloppe.

Canaux : API/Web, macOS, Android, voix (confirmation non ambiguë). Claw3D
affiche seulement « attention requise » en lecture seule.

## Conséquences

Git interdit (merge, deploy, force-push) reste hors de cette voie.
