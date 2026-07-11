# 00 — Vision Long Terme

**Date** : 11 juillet 2026
**Statut** : Document fondateur — toute décision d'architecture doit pouvoir être justifiée par cette vision

---

## Pourquoi JARVIS existe-t-il ?

JARVIS est un assistant personnel autonome conçu pour accompagner son utilisateur 24h/24 dans tous les aspects de sa vie : école, productivité, relations, émotions, information. Il ne remplace pas l'humain — il l'augmente, en automatisant ce qui peut l'être et en apportant de la clarté là où il y a du bruit.

Contrairement aux assistants cloud (Siri, Alexa, Google Assistant), JARVIS tourne **entièrement en local** sur le Mac de l'utilisateur. Les données ne quittent jamais la machine sans consentement explicite. La vie privée n'est pas une option — c'est le fondement.

## Problèmes que JARVIS cherche à résoudre

1. **Surcharge informationnelle** — Trop de canaux (iMessage, email, calendrier, notifications) → JARVIS consolide et priorise.
2. **Mémoire humaine limitée** — On oublie ce qu'on a dit, à qui, quand → JARVIS s'en souvient.
3. **Dispersion des outils** — Tâches dans une app, calendrier dans une autre, notes ailleurs → JARVIS unifie.
4. **Absence de recul** — On ne voit pas ses patterns de comportement → JARVIS les détecte et les signale avec bienveillance.
5. **Friction administrative** — Gérer ses mails, ses tâches, son agenda prend du temps → JARVIS automatise.
6. **Solitude décisionnelle** — On a parfois besoin d'un avis extérieur → JARVIS est un coach, pas un juge.

## Principes non négociables

### 1. Local First
> Les données de l'utilisateur résident sur sa machine. Aucune donnée personnelle n'est stockée dans le cloud sans consentement explicite et chiffrement.

- SQLite local pour la persistance
- Les LLM cloud (DeepSeek) ne reçoivent que des données anonymisées (DataBoundary)
- La vision par écran utilise Ollama local — aucune image ne quitte la machine

### 2. Privacy First
> La vie privée est le fondement du produit, pas une fonctionnalité.

- Chiffrement au repos (optionnel mais documenté)
- Données anonymisées avant tout appel API externe
- Aucune télémétrie, aucun tracking
- L'utilisateur peut auditer le code (open source)

### 3. Offline First
> L'application doit fonctionner normalement sans connexion Internet.

- IndexedDB pour les écritures hors ligne
- Service Worker pour le cache applicatif
- Queue de synchronisation au retour réseau
- Les LLM locaux (Ollama) fonctionnent sans Internet

### 4. Mobile First
> L'interface est conçue d'abord pour le téléphone, puis adaptée au desktop.

- PWA installable sur iOS et Android
- Navigation par BottomNav sur mobile
- Layout responsive unique (une seule codebase)
- L'utilisateur peut parler à JARVIS depuis n'importe où (iMessage, PWA)

### 5. IA comme copilote personnel
> L'IA assiste, elle ne remplace pas. L'utilisateur garde toujours le contrôle.

- Les suggestions sont explicables
- Les actions critiques nécessitent confirmation
- L'IA ne prend jamais de décision unilatérale impactant la vie réelle
- Transparence sur quel modèle a généré quelle réponse

### 6. Une seule source de vérité
> Chaque donnée a un propriétaire unique. Aucune duplication de logique métier.

- Data Ownership documenté (ADR-011)
- Les modules ne modifient que leurs propres données
- Communication inter-modules via Event Bus
- La base de données est la source de vérité, pas le cache

### 7. Automatisation intelligente
> JARVIS anticipe les besoins sans être intrusif.

- Briefing du matin automatique
- Détection de patterns de comportement
- Rappels contextuels (pas juste des alarmes)
- Actions proactives (notification d'un email important)

### 8. Simplicité d'utilisation
> L'interface est invisible. On parle à JARVIS comme à un majordome.

- Interface vocale mains libres
- Pas de configuration complexe
- Les defaults sont sains
- La complexité est cachée, pas supprimée

### 9. Fiabilité avant les fonctionnalités
> Mieux vaut 10 fonctionnalités qui marchent tout le temps que 100 qui marchent parfois.

- Tests avant merge
- Pas de régression
- Monitoring proactif
- Sauvegardes automatiques
- Rollback documenté pour chaque changement

### 10. Aucune dépendance cloud obligatoire
> JARVIS doit fonctionner même si Internet est coupé.

- SQLite local (pas PostgreSQL cloud)
- Ollama local pour la vision
- faster-whisper local pour le STT
- Edge TTS (pas de dépendance API TTS)
- Les API cloud (DeepSeek, météo, recherche web) sont optionnelles ou ont des fallbacks locaux

### 11. Fonctionnement autonome 24h/24
> JARVIS ne dort jamais. Il veille même quand l'utilisateur est absent.

- Supervisor 24/7 avec redémarrage automatique
- Scheduler pour les tâches planifiées
- Daemon pour la surveillance (écran, emails, iMessage)
- Détection de présence (arrivée/départ au bureau)

## Comment utiliser cette vision

Avant toute décision d'architecture, se poser la question :

> « Cette décision est-elle alignée avec les 11 principes de la vision JARVIS ? »

Si la réponse est non pour un principe, la décision doit être justifiée par un ADR expliquant pourquoi la dérogation est nécessaire.

Exemples :
- « Ajouter une dépendance cloud (Firebase) » → viole les principes 1, 2, 10 → nécessite un ADR solide
- « Dupliquer la logique de notification » → viole le principe 6 → refusé
- « Ajouter un mode multi-utilisateur » → ne viole aucun principe → acceptable si justifié
