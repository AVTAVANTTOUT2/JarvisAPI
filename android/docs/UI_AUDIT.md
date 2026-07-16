# Audit visuel — JARVIS Companion Android (avant refonte)

Date : 2026-07-16 · Base : `main` (e3e600c) · Périmètre : tous les écrans Compose.

## Synthèse

L'application est fonctionnellement solide (Room, WorkManager, WebSocket streaming,
offline-first) mais visuellement générique : thème Material 3 sombre par défaut à peine
personnalisé, cartes `Card` standard opaques, aucun gradient, aucune profondeur, aucune
animation d'apparition, typographie par défaut, densité de texte brute (surtout
Diagnostics et Localisation). Rien ne raccroche au langage du frontend web
(`#0a0a0f`, panneaux verre `rgba(255,255,255,.04)` + bordure `rgba(255,255,255,.07-.10)`,
motif grille pointillé, accents `#4a9eff`/`#00d4ff`/`#9c59ff`/`#30d158`/`#ff453a`,
animations slide-up/fade-in, chiffres tabulaires).

Composants existants réutilisables (à remplacer/promouvoir dans le design system) :
`JarvisCard`, `SectionHeader`, `ErrorCallout`, `NetworkStatusBadge` (dans
`core/ui/components/JarvisComponents.kt`, 99 lignes au total).

## Écran par écran

### Thème (`ui/theme/JarvisTheme.kt`, 29 lignes)
- **État** : `darkColorScheme` minimal — primary `#00D4FF`, background `#0A0F18`. Pas de
  typographie custom, pas de shapes, pas de tokens d'espacement, pas de gradients.
- **Défauts** : le fond `#0A0F18` (bleuté) diverge du web `#0a0a0f` (neutre) ; aucun
  système (spacing/alpha/border) ; les écrans piochent des `dp` à la main.
- **Proposition** : vrai design system — palette JarvisColors complète, Typography,
  Shapes, objets `JarvisSpacing`/`JarvisAlpha`, fond en dégradé + grille pointillée,
  famille de composants verre.

### Navigation (`JarvisNavHost.kt`)
- **État** : `NavigationBar`/`NavigationRail` M3 par défaut (Accueil, Chat, Voix, Agenda,
  Plus). Structure déjà bonne.
- **Défauts** : barre opaque standard, indicateur « pill » M3 générique, icônes filled
  par défaut, aucun traitement du bouton Voix (pourtant central dans l'identité JARVIS).
- **Proposition** : conserver la structure 5 onglets ; barre translucide bord supérieur
  lumineux, icônes outlined fines, bouton Voix central mis en scène (anneau orbe),
  transitions fade/slide entre destinations.

### Accueil (`HomeScreen.kt`)
- **État** : TopAppBar « Accueil » + pile verticale de 4 `JarvisCard` (Briefing, Tâches,
  Agenda, Notifications) + textes de statut.
- **Défauts** : exactement la « liste verticale de cartes Material » à éviter ; double
  titre (TopAppBar + SectionHeader JARVIS) ; aucune hiérarchie (le briefing pèse autant
  qu'une notif) ; statut de connexion en badge texte perdu à droite ; pas de salutation,
  pas d'actions rapides, états vides en une ligne sèche.
- **Proposition** : en-tête dynamique selon l'heure (« Bonsoir, Monsieur ») + présence
  Mac en anneau lumineux, carte briefing « héro » en verre accentué, timeline du jour
  (événements), tâches prioritaires compactes avec pastilles de priorité, notifications
  avec niveau de gravité, actions rapides (Parler, Écrire, Synchroniser), bandeau
  offline élégant réutilisable.

### Liste conversations (`ConversationListScreen.kt`)
- **État** : recherche `OutlinedTextField`, groupes datés, lignes texte + menu « … »,
  FAB `+`.
- **Défauts** : lignes sans avatar ni surface — sensation d'annuaire ; badge réseau
  au-dessus de la recherche (bruit) ; pas de mise en scène des épinglées ; pas d'état
  pending visible ; état vide en une ligne.
- **Proposition** : cartes verre par conversation (monogramme/orbe miniature, titre,
  aperçu, date relative discrète, épingle), champ de recherche verre arrondi, FAB
  gradient cyan, état vide illustré (orbe + suggestion), swipe/menu conservés.

### Chat (`ChatScreen.kt`)
- **État** : bulles `primaryContainer`/`surfaceVariant` arrondies 12 dp, composer
  Row (mic + OutlinedTextField + send), bannière offline `tertiaryContainer`,
  dialog de confirmation d'action sensible.
- **Défauts** : bulles génériques sans distinction JARVIS (pas d'identité visuelle
  assistant) ; indicateur streaming = spinner + « JARVIS répond… » ; composer plat ;
  bannière offline jaune-vert Material sans style ; pas d'affordance retry visible.
- **Proposition** : bulles utilisateur en dégradé bleu nuit→cyan discret alignées à
  droite, réponses JARVIS sur panneau verre pleine largeur avec filet lumineux à gauche
  et étiquette JARVIS, curseur de streaming animé (3 points « respirants »), composer
  capsule verre flottante (mic à gauche, envoi gradient), bannière offline standardisée
  `JarvisOfflineBanner`, retry en action inline sur le message échoué.

### Voix (`voice/VoiceActivity.kt`)
- **État** : colonne de textes, bouton rond 96 dp libellé « MIC »/« STOP », cartes
  Vous/JARVIS, spinners.
- **Défauts** : l'écran censé être emblématique est le plus pauvre — aucun orbe, aucune
  animation, un bouton texte « MIC » ; états (Idle/Recording/Sending/Processing/Playing/
  Error) traduits en textes uniquement.
- **Proposition** : orbe canvas plein écran (dégradés radiaux cyan/bleu, halo respirant
  en idle, pulsation par amplitude en enregistrement, rotation lente en traitement,
  ondes en lecture, rouge doux en erreur, gris en offline), transcript et réponse en
  panneaux verre bas d'écran, timer d'enregistrement, annulation visible, respect de
  « réduire les animations » (états statiques différenciés), placeholder « conversation
  continue — bientôt » derrière feature flag.

### Agenda (placeholder `PlaceholderScreen`)
- **État** : deux lignes de texte brut.
- **Défauts** : ne ressemble pas à un écran ; les événements existent pourtant dans Room
  (`CachedEventEntity`, affichés sur l'accueil).
- **Proposition** : vrai écran Agenda branché sur le cache : sélecteur de jours (chips
  horizontales 7 jours), timeline verticale groupée matin/après-midi/soir, cartes
  d'événement avec heure tabulaire et lieu, état vide élégant, création rapide en
  placeholder « Bientôt » (TODO JARVIS-FUTURE-CALENDAR-CREATE).

### Tâches (placeholder)
- **État** : texte brut.
- **Défauts** : les tâches sont déjà synchronisées dans Room et affichées sur l'accueil ;
  l'écran dédié n'exploite rien.
- **Proposition** : liste branchée sur `CachedTaskDao` : pastille de priorité colorée,
  échéance relative, filtres (Toutes/Haute/En retard), état vide et offline soignés,
  création/complétion rapides en placeholder flaggé (mutations non exposées par le
  contrat sync actuel — TODO JARVIS-FUTURE-TASKS-MUTATIONS).

### Localisation (`LocationScreen.kt`)
- **État** : cartes Collecte/État/Timeline + 4 boutons + textes `label : valeur`.
- **Défauts** : lisible par un développeur seulement ; « File offline-first, sans
  coordonnées affichées » comme sous-titre utilisateur ; compteurs bruts sans hiérarchie ;
  timeline en lignes de texte.
- **Proposition** : statut « héro » (anneau vert/orange/rouge : collecte active + sync
  OK), métriques en tuiles (`JarvisMetric` : en attente, dernière capture, dernière
  sync), timeline verticale stylée, actions destructives reléguées avec confirmation,
  carte live en placeholder « Bientôt » (TODO JARVIS-FUTURE-LIVE-MAP).

### Diagnostics (`DiagnosticsScreen.kt`)
- **État** : cartes de lignes `label : valeur` + rapport monospace complet affiché
  in-extenso + bouton copier.
- **Défauts** : mur de texte ; aucune notion de gravité ; le rapport brut doublonne les
  cartes.
- **Proposition** : santé globale en tête (badge OK/attention/problème calculé), sections
  par domaine (Application, Connexion, Synchronisation, GPS) en lignes `JarvisListItem`
  avec `JarvisStatusBadge`, rapport brut replié dans un panneau dépliable, bouton
  copier conservé.

### Réglages (`SettingsScreen.kt`)
- **État** : 4 cartes (Connexion, Localisation, Voix, Push) avec champs et switches.
- **Défauts** : organisation plate, boutons `TextButton` peu visibles, pas de sections
  Apparence/Données/À propos, feedback d'enregistrement inexistant.
- **Proposition** : sections normalisées (Connexion, Voix, Localisation, Notifications,
  Données, À propos) avec `JarvisSection` + lignes cohérentes, confirmations discrètes,
  entrées futures (apparence, multi-appareils) en placeholder flaggé.

### Onboarding (`OnboardingScreen.kt`)
- **État** : 5 étapes, LinearProgressIndicator, textes et champs bruts.
- **Défauts** : premier contact sans identité — pas de logo, pas d'orbe, pas de mise en
  scène ; saisie du code 6 chiffres en champ libre.
- **Proposition** : écran d'accueil avec orbe + « JARVIS », stepper à points lumineux,
  cartes verre par étape, code de pairage en 6 cases, écran final « Prêt » avec halo.

### Plus (`MoreScreen.kt`) et Réparation (`RepairScreen.kt`)
- **État** : `ListItem` M3 bruts / deux cartes.
- **Défauts** : aucune icône, pas de hiérarchie, Réparation anxiogène sans garde-fou
  visuel.
- **Proposition** : grille de tuiles verre avec icônes fines + sections (Organisation,
  Système), entrées futures (Mémoire, Contacts, Automatisations) en tuiles « Bientôt »
  flaggées ; Réparation avec zone « danger » claire et confirmations.

## Dettes transverses

1. Aucun composant d'état vide/erreur/offline réutilisable — chaque écran improvise.
2. Aucune animation (apparition, transitions de navigation, changements d'état).
3. Chiffres non tabulaires (compteurs qui « sautent »).
4. Icônes filled par défaut mélangées à du texte « … ».
5. `contentDescription` inégaux ; cibles tactiles OK mais focus/TalkBack non vérifiés.
6. Aucun feature flag — impossible de préparer des emplacements futurs proprement.
