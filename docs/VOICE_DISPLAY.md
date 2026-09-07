# JARVIS Voice HUD

Le Voice HUD est le visage plein écran du pipeline vocal existant. Il montre ce
que JARVIS entend, les opérations réellement exécutées, les sources réellement
retournées, la réponse canonique et la partie en cours de lecture. Il n’exécute
aucun outil et ne remplace ni le STT, ni le moteur de conversation, ni le TTS.

## Expérience et états

- **Veille :** heure, connexion, micro et « Prêt à vous écouter ».
- **Écoute :** transcription partielle si le STT en produit une, puis finale.
- **Compréhension :** critères structurés issus du pipeline.
- **Recherche :** uniquement les opérations et sources effectivement reçues.
- **Résultat :** résumé, résultats typés, affirmations et provenance.
- **Parole :** mise en évidence du segment sémantique associé.
- **Erreur/déconnexion :** message lisible et ancienneté des données.
- **Mode privé :** tout contenu sensible disparaît, le vocal continue.

La vue lecteur affiche l’extrait utilisé, le fournisseur, le locator expurgé et
les affirmations liées. Elle n’exécute aucun HTML ou JavaScript distant.

## Commandes vocales

Le parser déterministe traite notamment :

- « ouvre la source 2 », « source suivante », « source précédente » ;
- « ouvre le deuxième », « reviens aux résultats » ;
- « masque l’écran », « désactive le mode privé », « efface l’écran » ;
- « repasse en veille » ;
- « stop », « pause » et « continue » via le contrôle TTS prioritaire existant.

Le backend conserve le focus et la pile de navigation. Le frontend ne contient
ni champ texte ni bouton de navigation.

## Activation et lancement

Dans `.env` :

```dotenv
VOICE_DISPLAY_ENABLED=true
VOICE_DISPLAY_EVENT_RETENTION=512
VOICE_DISPLAY_PRIVACY_TIMEOUT_SECONDS=300
```

Construire et démarrer JARVIS :

```bash
cd frontend && pnpm build && cd ..
source venv/bin/activate
python main.py
```

Ouvrir `http://127.0.0.1:8080/voice-display`, ou le mode dédié :

```bash
./scripts/launch_voice_display.sh
```

La route est dans la liste blanche SPA de `api/frontend.py` (`voice-display`) : un
rechargement dur sert `frontend/out/voice-display/index.html` au lieu de retomber
sur 404.

Le lanceur utilise Chrome en mode kiosk et ne contient aucun secret. Pour le
lancer à l’ouverture de session macOS, ajouter ce script dans Réglages Système
→ Général → Ouverture. `VOICE_DISPLAY_URL` permet de changer l’URL sans modifier
le script.

Pour désactiver proprement le HUD :

```dotenv
VOICE_DISPLAY_ENABLED=false
```

Le pipeline vocal, le TTS et `/ws` continuent alors sans événement visuel.

## Contrat technique

### Snapshot et reprise

`GET /api/voice-display/snapshot` retourne la session courante, la dernière
séquence et le délai de confidentialité. `WS /ws/voice-display?since=N` envoie
d’abord un snapshot, puis les événements retenus après `N`. Le client recharge
le snapshot à chaque reconnexion, ignore les événements dupliqués ou en retard,
et applique un backoff borné à huit secondes.

Le WebSocket accepte seulement `pong`. Toute autre trame est fermée en `4405`.
Il réutilise la session navigateur ou le Bearer mobile existant ; aucune portée
d’administration n’est créée.

### Modèles et événements

Les modèles Pydantic stricts et versionnés vivent dans
`jarvis/voice_display.py` : `VoiceDisplaySession`, `VisualAnswer`,
`VisualSection`, `SourceEvidence`, `ClaimEvidence`, `VoiceAction`,
`SpeechSegment` et `VoiceDisplayEvent`.

Les producteurs actuels publient les états d’écoute, transcriptions disponibles,
début/fin du tour canonique, sources reçues, résultat final, parole,
interruption, focus, retour, effacement et confidentialité. Aucun événement de
progression n’est synthétisé lorsqu’un outil ne remonte son résultat qu’à la fin.

### Provenance

`answer_from_result` ne lit que `knowledge.references` et les données de
`action_result`. Une affirmation `confirmed` sans `source_ids` est rejetée par
validation. En l’absence de source, l’interface affiche explicitement
« Aucune source externe utilisée pour cette réponse ».

### Ajouter un renderer

Ajouter le type backend à l’union `VisualSection`, son type TypeScript miroir,
puis une branche dans `renderers.tsx`. Le renderer reçoit seulement des données
structurées et échappées par React ; il ne doit jamais exécuter du contenu
distant.

## Captures de référence

Les captures 1920 × 1080 sont régénérées par l’E2E, à partir de données
explicitement marquées comme fixtures de test :

- [veille](assets/voice-display/01-idle.png) ;
- [écoute et transcription](assets/voice-display/02-listening.png) ;
- [recherche](assets/voice-display/03-researching.png) ;
- [résultat](assets/voice-display/04-result.png) ;
- [lecture d’une source](assets/voice-display/05-source-reader.png) ;
- [confidentialité](assets/voice-display/06-private.png) ;
- [déconnexion](assets/voice-display/07-disconnected.png).

## Exploitation 24/7

- événements backend retenus et files d’abonnés bornés ;
- historique de séquences frontend limité à 512 ;
- activités, sources et navigation limitées ;
- nettoyage des WebSockets, timers et écouteurs au démontage ;
- reconnexion après veille via `visibilitychange` ;
- respect de `prefers-reduced-motion` ;
- léger mouvement respiratoire, désactivable par la préférence système ;
- masquage automatique après inactivité ;
- 1080p, 4K, 16:9 et 16:10 couverts par les styles adaptatifs.

## Diagnostic

En développement uniquement, `/voice-display?debug=1` affiche la séquence et
les identifiants de session. Si l’écran reste vide, vérifier successivement
`VOICE_DISPLAY_ENABLED`, l’authentification, `GET /api/voice-display/snapshot`,
puis le handshake `/ws/voice-display`.
