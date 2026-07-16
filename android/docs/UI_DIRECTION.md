# Direction artistique — JARVIS Companion Android

Refonte `feat/android-ui-redesign` · 2026-07 · 100 % Jetpack Compose, zéro WebView.

## Vision

Un majordome numérique, pas un tableau de bord technique. L'application doit donner la
même sensation que le frontend web JARVIS : une pièce sombre et calme, des panneaux de
verre posés sur un fond profond, une seule couleur de vie (le cyan JARVIS) qui respire
là où l'assistant agit. Sobriété avant spectacle : la lumière sert la hiérarchie, jamais
la décoration.

Trois principes :

1. **Une entité, une lumière.** Le cyan `#00D4FF` est réservé à JARVIS (orbe, présence,
   streaming, actions primaires). Tout le reste est gris bleuté et verre.
2. **Le verre est un support, pas un filtre.** Surfaces translucides simulées
   (gradients + alpha + bordures hairline + highlight supérieur) — pas de `blur()`
   temps réel plein écran : coûteux et instable sur certaines ROM. Lisibilité d'abord.
3. **Le mouvement raconte l'état.** Une animation n'existe que si elle encode une
   information (apparition, streaming, enregistrement, sync). Aucune boucle GPU
   permanente en veille ; respect de « Supprimer les animations » système.

## Palette (`ui/theme/JarvisColors.kt`)

| Token | Valeur | Usage |
|---|---|---|
| `bg` | `#0A0A0F` | Fond racine (identique au web `--bg-primary`) |
| `bgTop` | `#0D1017` | Haut du dégradé de fond (nuance bleu nuit) |
| `surfaceGlass` | blanc 4–6 % | Panneaux verre (`JarvisGlassCard`) |
| `borderSubtle` | blanc 7–12 % | Hairlines, contour des panneaux |
| `cyan` | `#00D4FF` | JARVIS — orbe, primaire, streaming |
| `blue` | `#4A9EFF` | Interactif secondaire, bulles utilisateur |
| `purple` | `#9C59FF` | Touches rares (épinglé, insights) |
| `green` | `#30D158` | Succès, connecté, sync OK |
| `amber` | `#FFD60A` | Avertissement, attente |
| `red` | `#FF453A` | Erreur, danger, enregistrement |
| `textPrimary` | `#F2F5FA` | Titres, contenus |
| `textSecondary` | `#9AA6B5` | Sous-titres, méta |
| `textTertiary` | `#5E6B7C` | Détails techniques, timestamps |

Fond global : dégradé vertical `bgTop → bg` + halo radial cyan très faible (3 %) en
haut + motif grille pointillée (points blanc 5 %, pas 24 dp) dessiné une fois en
`drawBehind` — équivalent du `.bg-grid-pattern` web, coût GPU nul.

## Typographie (`ui/theme/JarvisType.kt`)

Sans-serif système (Roboto/Google Sans selon device — pas de police embarquée, 0 Ko).

- `displaySmall` 36 sp / -0.5 letterSpacing — salutation accueil.
- `headlineMedium/Small` semibold — titres d'écran.
- `titleMedium` semibold — titres de cartes.
- `bodyLarge/Medium` — contenus ; interligne aéré (1.45×).
- `labelMedium/Small` — méta, badges, majuscules espacées pour les sections.
- Chiffres importants : `JarvisMetric` force `FontFeature "tnum"` (tabulaires) —
  les compteurs ne « sautent » pas.

## Formes, espacements, profondeur

- Rayons : 24 dp (héros/orbe cartes), 20 dp (cartes), 14 dp (champs, badges), pill.
- Grille d'espacement 4/8/12/16/20/24 (`JarvisSpacing`).
- Profondeur sans ombre portée Material : superposition d'alpha + hairline. Un panneau
  « au-dessus » est légèrement plus clair et plus bordé, jamais plus ombré.

## Verre (`JarvisGlassCard`)

```
fond      : Brush.verticalGradient(blanc 6 % → blanc 2.5 %)
bordure   : 1 dp, Brush.verticalGradient(blanc 14 % → blanc 5 %)  (reflet en haut)
highlight : filet horizontal blanc 10 % sous le bord supérieur
variantes : default · accent (bordure cyan 35 % + halo intérieur cyan 6 %) · danger
```

## Iconographie

`Icons.Outlined.*` exclusivement (lignes fines, un seul style). Remplir uniquement
l'onglet actif de la navigation. Aucun emoji, conformément à la persona.

## Navigation

Structure conservée : **Accueil · Chat · Voix · Agenda · Plus** (bottom bar < 840 dp,
rail au-delà). Refonte visuelle :

- Barre translucide (fond `bg` 86 % + hairline supérieure blanc 8 %).
- Onglets : icône outlined + label ; actif = icône filled cyan + point lumineux 3 dp.
- **Voix** : bouton central circulaire 44 dp, dégradé cyan→bleu, anneau blanc 12 % —
  l'orbe miniature, signature de l'app. Ouvre `VoiceActivity` (contrat conservé).
- Transitions de destinations : fade 180 ms + translation 8 dp (désactivées si
  animations système réduites).

« Plus » regroupe : Tâches, Localisation, Notifications, Diagnostics, Réglages,
Réparation + tuiles futures (Mémoire, Contacts, Automatisations) flaggées « Bientôt ».

## Écrans

- **Accueil** : salutation horodatée (« Bonsoir, Monsieur ») + sous-ligne présence Mac,
  carte briefing héro (variante accent), actions rapides (Parler / Écrire / Sync),
  timeline du jour (agenda), tâches prioritaires (pastilles), notifications (gravité),
  bandeau offline standard. Pull-to-refresh conservé.
- **Chat** : liste = cartes verre avec monogramme, groupes datés, épinglées en tête ;
  conversation = bulles utilisateur bleu nuit dégradé à droite, réponses JARVIS en
  panneau verre bord-cyan pleine largeur, streaming = 3 points animés, retry inline ;
  composer capsule verre (mic | champ | envoi gradient), safe avec IME.
- **Voix** : orbe Canvas 220 dp au centre — halo respirant (idle), pulsation par
  amplitude micro (enregistrement), rotation d'arc (traitement), ondes concentriques
  (lecture), statique teinté (erreur/offline). Transcript + réponse en panneaux bas.
  Timer d'enregistrement, annulation, arrêt lecture. Push-to-talk inchangé ;
  emplacement « Conversation continue — bientôt » (flag).
- **Agenda** : nouvel écran réel (cache Room `cached_events`) : bandeau 7 jours,
  timeline groupée Matin/Après-midi/Soir, cartes événement (heure tabulaire, lieu),
  états vide/offline, création rapide en « Bientôt » (flag).
- **Tâches** : nouvel écran réel (cache Room `cached_tasks`) : filtres chips, pastille
  priorité, échéance relative, catégorie ; mutations locales en « Bientôt » (flag —
  le contrat sync actuel est lecture seule).
- **Localisation** : statut héro (anneau + verdict clair « La localisation fonctionne »),
  métriques tuiles, timeline stylée, actions destructives reléguées + confirmation,
  carte live « Bientôt » (flag). Jamais de coordonnées par défaut.
- **Diagnostics** : verdict global calculé (OK / attention / problème), sections par
  domaine en lignes badges, rapport brut replié (expandable) + copie.
- **Réglages** : sections Connexion / Voix / Localisation / Notifications / Données /
  À propos, lignes normalisées, confirmations discrètes, entrées futures flaggées.
- **Onboarding** : orbe + « JARVIS » en ouverture, stepper points lumineux, cartes
  verre, code d'appairage 6 cases, final avec halo. Logique de pairage intacte.

## Animations

| Contexte | Spec |
|---|---|
| Apparition de contenu | fade + translateY 10 dp, 220 ms, easing standard (équiv. `jarvis-slide-up`) |
| Navigation | fade 180 ms + 8 dp |
| Streaming chat | 3 points, alpha 0.3→1, décalés 150 ms |
| Orbe idle | halo scale 1→1.04, 3 s, aller-retour |
| Orbe enregistrement | rayon lié à l'amplitude micro (spring), anneau rouge doux |
| Orbe traitement | arc 270° rotation 1.4 s |
| Succès/erreur | teinte de bordure animée 300 ms, pas de shake |

Règle : `rememberReducedMotion()` (lit `ANIMATOR_DURATION_SCALE`) coupe toutes les
boucles infinies → états statiques différenciés par couleur/forme.

## Accessibilité

- Contraste : textPrimary sur bg ≈ 15:1 ; textSecondary ≈ 6.8:1 ; badges ≥ 4.5:1.
- Cibles ≥ 48 dp ; `contentDescription` sur toute icône actionnable ; états annoncés
  par le texte (jamais par la couleur seule — chaque badge a un libellé).
- `FontScale` : layouts en `wrap`/`weight`, aucun texte à hauteur fixe.
- TalkBack : ordre de lecture naturel (colonne), `semantics` sur l'orbe (état vocal).

## Performance

- Pas de `Modifier.blur` plein écran ; verre simulé par gradients (1 draw).
- Grille de fond : `drawBehind` sans invalidation.
- Boucles `rememberInfiniteTransition` uniquement sur l'écran Voix quand l'état le
  requiert, et sur l'indicateur streaming pendant le streaming.
- Listes : `LazyColumn` + `key` stables (déjà en place, conservés).
