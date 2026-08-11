# SDK développeurs

> Implémentation de référence : `sdk/python/`. Le registre d'opérations est
> généré depuis `openapi/jarvis.openapi.json` et ne doit jamais être édité à la
> main.

## Périmètre livré

Le premier SDK officiel cible Python 3.10+ et porte la même version que le
contrat OpenAPI, actuellement **1.0.0**. Le wheel est autonome et ne possède
aucune dépendance runtime.

```bash
python -m pip install ./sdk/python
```

Le client expose les 269 opérations par `operationId` via `call()` et
`call_json()`. `operations(tag=...)` permet de découvrir le registre embarqué.
Des raccourcis couvrent l'ouverture/fermeture de session et la sonde de vie.

## Sécurité du transport

- HTTPS est obligatoire hors loopback ; HTTP n'est accepté que pour
  `localhost`, `127.0.0.0/8` et `::1`.
- Les credentials, query strings et fragments sont refusés dans `base_url`.
- TLS reste vérifié ; une CA privée se configure avec `cafile`. Aucun mode
  `verify=False` n'existe.
- Les redirects HTTP sont refusés afin de ne jamais transférer cookie ou Bearer
  vers une autre origine.
- Les headers d'authentification sont réservés au client et les valeurs avec
  CR/LF/NUL sont rejetées.
- Les réponses sont bornées à 16 MiB par défaut.

## Authentification

Le registre généré embarque `x-jarvis-authentication`. Le client échoue donc
avant le réseau si l'opération exige un credential absent :

- cookie de session + CSRF + `Origin` pour les mutations ;
- Bearer mobile ;
- token device ou localisation ;
- alternatives session/Bearer et Bearer/localisation.

`unlock(secret)` envoie la passphrase une seule fois, exige le cookie et le
jeton CSRF dans la réponse, puis ne conserve pas le secret. Un `session_token`
et son `csrf_token` peuvent aussi être injectés explicitement.

## Fiabilité et erreurs

Les lectures GET/HEAD/OPTIONS retentent les erreurs réseau et les statuts
408/425/429/502/503/504 avec backoff borné et `Retry-After`. Les mutations ne
sont jamais rejouées automatiquement.

Le SDK distingue : configuration locale, credential manquant, transport/TLS,
réponse trop volumineuse et erreur API structurée (`status_code`, `code`,
`detail`, réponse bornée). Les messages d'exception n'incluent pas les tokens.

## Génération et validation

```bash
.venv/bin/python tools/generate_python_sdk.py --check
PYTHONPATH=sdk/python/src .venv/bin/python -m pytest sdk/python/tests -q
.venv/bin/python -m pip wheel --no-deps --no-build-isolation sdk/python
```

La CI exécute ces trois portes. Toute nouvelle valeur d'authentification non
supportée fait échouer la génération, ce qui évite un fallback silencieux.
