# Prompt design — interface mobile JARVIS

> **ARCHIVE — prompt de conception déjà exécuté.** Ne pas l’utiliser comme
> description du runtime courant ; consulter
> [`32_FRONTEND_DATABASE_SOURCE_OF_TRUTH.md`](./32_FRONTEND_DATABASE_SOURCE_OF_TRUTH.md).

À donner à Claude pour la conception visuelle et UX de `web_mobile/`.
Brief autonome : tout le contexte nécessaire est ici.

---

Tu conçois l'interface mobile de **JARVIS**, un assistant personnel
multi-agents qui tourne en local sur le Mac d'une seule personne. Elle
l'utilisera depuis son iPhone, plusieurs fois par jour, souvent d'une main, en
marchant. C'est un outil quotidien privé, pas un produit à vendre.

Je veux **le design complet du front** : langage visuel, système de composants,
et les six écrans. Livrable en HTML/CSS réel, pas en images.

## Qui est JARVIS

Un majordome. Référence assumée : le JARVIS d'Iron Man. Britannique, concis,
légèrement formel, humour pince-sans-rire. Il tutoie l'utilisateur, l'appelle
parfois « Monsieur » avec une pointe d'ironie bienveillante. Il répond en trois
phrases quand trois phrases suffisent. La donnée d'abord, le contexte ensuite.

Cette personnalité doit se voir dans l'interface. Elle n'est pas qu'une affaire
de textes.

### Règles de ton — non négociables

- **Aucun emoji.** Nulle part. Ni dans les libellés, ni dans les états vides, ni
  pour la météo. Si tu as besoin d'un symbole, c'est une icône dessinée.
- **Aucun point d'exclamation**, sauf urgence réelle.
- **Aucun registre chatbot.** Pas de « Quoi de neuf ? », « N'hésite pas ! »,
  « Super ! », « Oups… ». Un écran vide dit « Aucune tâche. », pas
  « Rien à faire ici pour l'instant 🎉 ».
- **Français** partout.
- Pas de célébration, pas de confettis, pas de gamification. Aucun badge, aucun
  streak, aucun score de progression motivationnel.

## Contraintes techniques qui façonnent le design

Ce ne sont pas des détails d'implémentation : elles limitent réellement ce que
tu peux dessiner.

| Contrainte | Conséquence pour toi |
|---|---|
| HTML + CSS + JS vanilla, aucun build, aucune librairie | Pas de composant Tailwind/shadcn/Material. Tu écris le CSS. |
| CSP `default-src 'self'` — aucun CDN, aucune webfont Google | **Police système obligatoire** : `-apple-system, BlinkMacSystemFont, 'SF Pro Text', …`. Toute la typographie repose sur SF. Icônes en SVG inline, pas de librairie d'icônes. |
| iPhone, Safari, portrait, `viewport-fit=cover` | Safe areas haut et bas à respecter réellement (`env(safe-area-inset-*)`). Rien de critique sous la barre d'accueil. |
| Safari zoome au focus si `font-size < 16px` | Tous les champs de saisie à 16px minimum. |
| Doigt, en marchant | Cibles tactiles ≥ 44 px. Actions principales dans le tiers bas de l'écran. |
| Thème sombre uniquement | Pas de light mode à prévoir. |

## Palette existante

L'interface doit appartenir à la même famille que le reste de JARVIS. Ces
valeurs sont déjà en production, reprends-les comme base :

```
fond          #0a0a0f
carte         rgba(255,255,255,.035)
bordure       rgba(255,255,255,.07)
texte         #ffffff
texte second  #888888
texte tertiaire #555555
accent        #4a9eff   (bleu, sélection et actions)
violet        #9c59ff
succès        #30d158
alerte        #ffd60a
danger        #ff453a
```

Tu peux enrichir — élévations, états de survol, transparences, un accent
secondaire — mais l'identité sombre et sobre reste.

## Les six écrans

Navigation par barre d'onglets basse, six emplacements.

### 1. Chat — écran par défaut

Le cœur de l'usage. Conversation texte avec JARVIS, réponses **en streaming**
token par token.

À concevoir : bulles utilisateur / JARVIS, l'état « JARVIS écrit » pendant le
streaming, le composer (champ + envoi + accès micro), l'accès à l'historique
des conversations, et **la carte de confirmation d'action sensible** — JARVIS
peut proposer d'exécuter une commande sur le Mac et attend un accord explicite.
Cette carte doit être impossible à valider par accident.

### 2. Voix — push-to-talk

L'utilisateur maintient un bouton, parle, relâche, JARVIS répond à voix haute.

Quatre états à dessiner : au repos, écoute (avec retour visuel du niveau
sonore), traitement, JARVIS parle. Plus la transcription de ce qui a été
compris. Le bouton doit être atteignable au pouce sans regarder.

### 3. Dashboard

Ce que l'utilisateur regarde en se levant. Ordre de priorité imposé :

1. notifications urgentes
2. agenda du jour
3. tâches en retard
4. briefing

**Point important** : le briefing est **généré à la demande** par un LLM
(`/api/briefing`) — plusieurs secondes d'attente et un coût réel à chaque appel.
Il ne doit donc **pas** se déclencher au chargement de l'écran. Conçois-le comme
une action explicite, avec un état d'attente honnête et le briefing précédent
visible entre-temps.

### 4. Tâches

Liste, création, changement de statut. Champs réels : `title`, `description`,
`priority` (high/medium/low), `status` (todo/doing/done), `due_date`,
`category`. Le retard se lit d'un coup d'œil. La création doit prendre trois
secondes.

### 5. Mails

Résumés d'emails déjà analysés en amont par JARVIS. Deux catégories qu'il
distingue lui-même : **paiement** (facture, prélèvement, échéance, avec un
montant) et **demande** (une vraie personne qui attend une réponse). Champs
réels : `sender`, `subject`, `summary`, `action_needed`, `priority`.

Lecture seule. Pas de rédaction, pas d'envoi.

### 6. Sport / Santé — emplacement réservé

Sixième onglet **présent dans la navigation mais vide**. Le contenu sera
spécifié plus tard. Dessine l'emplacement et son état vide, rien d'autre. Ne
devine pas les écrans.

## États à ne pas oublier

C'est là que la plupart des maquettes mentent. Pour chaque écran, traite :

- **chargement** — sans faux squelettes qui promettent une mise en page qui ne
  viendra pas
- **vide** — formulé dans le ton JARVIS, sec, sans consolation
- **erreur réseau** — le serveur tourne à la maison, l'iPhone est souvent
  ailleurs : la coupure est un cas normal, pas exceptionnel
- **session expirée** — retour à l'écran de déverrouillage sans perdre ce qui
  était saisi

Ajoute l'**écran de déverrouillage** : l'app est protégée par un code, elle se
verrouille automatiquement après quelques minutes d'inactivité, et **rien** ne
s'affiche avant déverrouillage. Prévois le clavier numérique iOS, l'erreur de
code, et le blocage temporaire après plusieurs échecs (avec compte à rebours).

## Ce que je veux recevoir

**1. Un système de design d'abord** — tokens (couleurs, échelle typographique,
espacements, rayons, ombres, durées d'animation) et les composants récurrents :
carte, bouton, champ, liste, badge de priorité, barre d'onglets, en-tête,
notification. Chacun avec ses états.

**2. Les six écrans plus l'écran de déverrouillage**, en HTML + CSS complet,
cadrés en 390 × 844 (iPhone 15). Contenu **réaliste** : de vraies phrases
françaises dans le ton JARVIS, de vrais objets de mails, de vraies tâches. Pas
de lorem ipsum, pas de « Titre de la carte ».

**3. Les interactions clés** — ce qui bouge et pourquoi : arrivée d'un message
en streaming, appui sur le bouton micro, coche d'une tâche, ouverture d'un
mail, changement d'onglet. Le mouvement doit être discret et rapide ; JARVIS est
posé, pas nerveux.

Le HTML/CSS doit être directement récupérable par le développeur — c'est le
livrable, pas une illustration de ce qu'il faudrait construire.

## Deux garde-fous

**Ne conçois pas un tableau de bord d'entreprise.** Pas de grille de KPI, pas de
graphiques décoratifs, pas de densité d'information façon terminal. Une seule
personne lit cet écran, elle connaît déjà sa vie ; elle vient chercher une
chose précise et repartir.

**Ne conçois pas une app grand public.** Pas d'onboarding, pas de tour guidé,
pas de bandeau promotionnel, pas de paramètres à trois niveaux. L'utilisateur
est le seul, il a construit l'outil, il sait ce qu'il fait.

Si une contrainte ci-dessus rend un écran mauvais, dis-le et propose autre
chose — je préfère un désaccord argumenté à une maquette qui coche les cases.
