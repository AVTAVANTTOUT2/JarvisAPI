# Sécurité, menaces, avis et licence

## Invariants appliqués

- serveur uniquement sur `127.0.0.1`, port éphémère et Basic Auth aléatoire ;
- Web UI, partage, mDNS, CORS additionnel et autoupdate désactivés ;
- aucun héritage global d'environnement : liste système minimale, chemins
  HOME/XDG/TMP confinés, identifiants serveur gérés par le lifecycle ;
- binaire téléchargé uniquement depuis l'hôte GitHub autorisé, taille bornée,
  SHA-256 obligatoire, extraction sans traversée, lien ou fichier spécial ;
- version du binaire, contrat HTTP et agents JARVIS obligatoires vérifiés avant
  une session ;
- chemins runtime/workspace validés sans lien et sans sortie de frontière ;
- capacités MCP courtes, liées au run/profil/workspace, expirables et conservées
  dans le processus parent ; bearer remis par bootstrap socket privé one-shot,
  après preuve UID/PID/ascendance, puis lié au même peer ;
- mutations invisibles sans reçu parent exact run/outil/arguments/expiration,
  idempotentes, scopes contrôlés, origine et garde anti-récursion obligatoires ;
- secrets, arguments et contenu non fiable redactés avant persistance ou
  diffusion ; aucune chaîne de pensée n'est exposée ;
- arrêt uniquement si PID, binaire, instance et health authentifié prouvent la
  propriété du processus.

## Modèle de menace

| Menace | Contrôle | Risque résiduel |
|---|---|---|
| Accès réseau non autorisé | loopback + Basic Auth éphémère + aucune UI | un processus du même compte OS peut observer mémoire/fichiers privés selon les garanties de l'OS |
| Archive ou release compromise | hôte/plateforme allowlistés, tailles, SHA-256, version et extraction stricte | une release amont signée par l'empreinte attendue peut elle-même être malveillante ; revue de release requise |
| Injection via Web, email ou document | contenu marqué non fiable, agents sans web, instructions système JARVIS prioritaires, outils bornés | un modèle peut encore être influencé ; toute capacité à effet reste minimisée et soumise à validation |
| Exfiltration de secrets | environnement nettoyé, redaction, sorties bornées, pas de logs d'auth | la clé modèle explicitement transmise est nécessairement visible du processus fournisseur |
| Vol du bearer MCP | aucun secret en argv/env/fichier, socket `0700/0600`, bootstrap one-shot lié au PID et endpoint immédiatement supprimé | une course locale du même compte peut provoquer un DoS ; elle ne crée aucun reçu effectful |
| Écriture hors workspace | validation des chemins, `external_directory=deny`, scopes et outils natifs désactivés sans permission | ce n'est pas une sandbox noyau ; le processus partage le compte OS de JARVIS |
| Commande destructive ou publication Git | bash soumis à permission, commit/push/merge/rebase refusés, DevAgent propriétaire du Git | une nouvelle forme de commande doit rester couverte lors des upgrades |
| Appel MCP récursif ou rejoué | métadonnées run/tool-call/origin, bypass explicite, expiration, journal d'idempotence | un journal plein bloque les nouvelles mutations jusqu'au nettoyage du run |
| DoS local | délais, reconnexions bornées, limites d'archive/sortie/budget, un run actif | tâches CPU longues dans le workspace et saturation disque restent possibles |
| PID réutilisé / mauvais processus | preuve de propriété et health authentifié avant signal | si l'état privé est supprimé avant l'arrêt, le manager ne peut plus prouver ni arrêter ce processus |

Par conséquent, ne jamais supprimer manuellement `.runtime` ou le dossier du
plugin pendant que le serveur tourne. Utiliser `manager uninstall`, qui arrête
d'abord l'instance dont il prouve la propriété.

## Advisories revus

Revue effectuée le 2026-08-11. La borne minimale imposée est `1.1.10`.

- `GHSA-c83v-7274-4vgp` / `CVE-2026-22813`, critique : XSS de la Web UI menant
  à l'exécution de code, corrigée en `1.1.10` ;
- `GHSA-vxw4-wv6m-9hhh` / `CVE-2026-22812`, haute : exposition serveur sans
  authentification, corrigée en `1.0.216`.

La version épinglée `1.18.16` est supérieure aux deux correctifs. Les contrôles
JARVIS restent nécessaires et la Web UI demeure désactivée. Avant chaque
upgrade, revoir <https://github.com/anomalyco/opencode/security/advisories> et
mettre à jour `release-manifest.json`.

## Licence et provenance

OpenCode est distribué sous licence MIT. Le texte vendored est `LICENSE`, sa
source est `https://raw.githubusercontent.com/anomalyco/opencode/v1.18.16/LICENSE`
et le blob Git attendu est `6439474beed8e0271df9862eff97ffd70ec2464c`.
`THIRD_PARTY_NOTICES.md` conserve version, commit, contrat et avis revus.

Ne pas supprimer `LICENSE` ou la notice d'une distribution qui contient le
binaire. Une mise à niveau doit comparer la licence amont et actualiser son
empreinte ; le SHA-256 des archives ne remplace pas cette vérification de
provenance.

## Limites explicites

Le runtime n'est ni une VM ni une sandbox matérielle. Il peut lire ce que son
compte OS et ses outils autorisés rendent accessible. Les requêtes envoyées au
modèle connecté peuvent contenir du code et du contexte du workspace : la
politique de confidentialité de ce fournisseur s'applique. JARVIS ne garantit
pas le fonctionnement hors ligne d'une tâche modèle ; seul le lifecycle et le
smoke test du serveur sont sans clé externe.

Sur les plateformes sans permissions POSIX, les modes `0600/0700` ne suffisent
pas à décrire les ACL effectives : vérifier les permissions du compte et du
volume. Toute ouverture réseau, ajout de variable secrète, nouveau tool MCP ou
capacité non réversible exige une revue de menace.

Le broker MCP privé de cette version exige les credentials peer et l'ascendance
de processus testés sur macOS ou Linux. Sur Windows il échoue fermé avec
`unsupported_secure_peer_transport` et n'expose aucun outil MCP ; il n'existe
aucun fallback bearer via argv, environnement ou fichier. Le lifecycle OpenCode
reste installable, mais les tâches qui requièrent le bridge MCP sont bloquées
jusqu'à l'ajout d'un transport Windows avec preuve PID testée.
