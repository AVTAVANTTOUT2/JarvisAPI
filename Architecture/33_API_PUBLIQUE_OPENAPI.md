# API publique et contrat OpenAPI

> Source de vérité : `openapi/jarvis.openapi.json`, généré par
> `tools/export_openapi.py` depuis l'application FastAPI réellement montée.

## Contrat

Le contrat développeur est en OpenAPI **3.1.0** et possède sa propre version
sémantique, actuellement **1.0.0**. Il couvre les 269 opérations HTTP montées,
réparties sur 239 chemins. Chaque opération fournit :

- un `operationId` stable dérivé de la méthode et du chemin, indépendant du nom
  de la fonction Python ;
- un tag de domaine, un résumé et une description ;
- la frontière d'authentification réellement appliquée au runtime ;
- les réponses communes 401, 403, 428 et 429 lorsque pertinentes.

Les changements de méthode, chemin, paramètres requis, schéma de requête ou
réponse, authentification ou `operationId` sont considérés comme incompatibles
et imposent une évolution majeure de la version du contrat.

## Authentification documentée

| Schéma | Transport | Usage |
|---|---|---|
| `sessionCookie` | cookie `jarvis_session` par défaut | session navigateur locale |
| `csrfToken` | `X-CSRF-Token` | mutations par cookie, avec `Origin` autorisée |
| `mobileBearer` | `Authorization: Bearer …` | routes mobiles explicitement autorisées |
| `deviceToken` | `X-Device-Token` | heartbeat, écran et TTS d'un appareil pairé |
| `locationToken` | `X-Location-Token` | ingestion GPS externe |

Les opérations de pairage signalent `x-jarvis-authentication: pairing_code`.
La sonde `/api/health/live` et les seules routes d'ouverture/configuration de
session restent les exceptions publiques exactes du middleware.

## Accès runtime

- `GET /api/developer/openapi.json` : schéma exact de l'instance ;
- `GET /api/developer/docs` : catalogue HTML autonome, sans JavaScript ni CDN.

Ces deux routes vivent sous `/api/` et exigent une session JARVIS. Les anciens
`/docs`, `/redoc` et `/openapi.json` restent désactivés afin de ne pas exposer le
schéma en dehors du verrou de session.

## Export et contrôle de dérive

```bash
.venv/bin/python tools/export_openapi.py
.venv/bin/python tools/export_openapi.py --check
```

Le mode `--check` compare octet par octet le JSON trié et formaté. Il est lancé
en CI avec les contrats de routes et l'audit d'architecture. Toute nouvelle
route doit donc mettre à jour explicitement l'artefact versionné.

## Consommateurs

Le JSON peut être importé dans un générateur OpenAPI standard. Le SDK JARVIS
officiel s'appuie sur les mêmes `operationId` et ne redéfinit pas les chemins ou
les règles d'authentification.
