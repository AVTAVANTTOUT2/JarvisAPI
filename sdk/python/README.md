# JARVIS Developer SDK — Python

Client Python sans dépendance runtime pour le contrat OpenAPI public JARVIS
1.0.0. Il fonctionne avec Python 3.10+ et valide localement les opérations,
l'authentification et les URLs avant tout accès réseau.

## Installation

```bash
python -m pip install ./sdk/python
```

## Jeton mobile Bearer

```python
from jarvis_sdk import JarvisClient

with JarvisClient(
    "https://jarvis.example.test:8080",
    bearer_token="jeton-mobile",
    cafile="/chemin/vers/ca.pem",
) as client:
    tasks = client.call_json("get_api_tasks")
```

## Session navigateur et CSRF

```python
with JarvisClient("http://127.0.0.1:8080") as client:
    client.unlock("votre passphrase")
    created = client.call_json(
        "post_api_tasks",
        json_body={"title": "Vérifier la sauvegarde"},
    )
    client.logout()
```

HTTP en clair est refusé hors loopback. TLS est toujours vérifié ; utilisez
`cafile` pour une autorité privée. Le SDK ne propose pas de mode
`verify=False` et ne suit aucun redirect HTTP.

## Appel générique

Les 269 opérations sont disponibles par leur `operationId` versionné :

```python
result = client.call_json(
    "get_api_conversations_by_conv_id",
    path_params={"conv_id": 42},
)
```

`client.operations(tag="conversations")` permet de découvrir le registre
embarqué. Les lectures idempotentes retentent les erreurs réseau temporaires et
les statuts 408/425/429/502/503/504 ; les mutations ne sont jamais rejouées
automatiquement.
