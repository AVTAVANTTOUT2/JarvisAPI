# ADR-0003 : Architecture dual-LLM (local + cloud)

**Date** : 2026-07-11 (rétroactif — décision prise en juin 2026)
**Statut** : Accepté

## Contexte

Jarvis traite des données de sensibilité variable : messages iMessage (hautement sensibles), emails (sensibles), tâches et RAG (modérément sensibles), questions factuelles (non sensibles). Un seul provider cloud pour tout viole le principe Privacy First. Un seul modèle local pour tout dégrade la qualité.

## Décision

Architecture dual-LLM avec routage par sensibilité :

| Données | LLM | Raison |
|---|---|---|
| Messages iMessage | Local MLX-LM (Qwen3-30B-A3B-4bit) | Jamais envoyés au cloud |
| Emails, RAG, tâches | DeepSeek v4 Pro (cloud) | Après anonymisation PII |
| Routage, extraction silencieuse | Anthropic Haiku (cloud) | Pas de données sensibles |
| Coaching, journal | Anthropic Sonnet/Opus (cloud) | Données contrôlées |

Module : `jarvis/` — `router.py`, `backends/`, `pii/`, `models.py`

Pipeline de protection :
1. Classification sensibilité (`message_intelligence.py`)
2. Anonymisation PII (`pii/PIIAnonymizer` — spaCy NER + regex)
3. Vérification `DataBoundary` (bloque patterns interdits)
4. Routage vers backend approprié

## Alternatives considérées

| Alternative | Avantages | Inconvénients | Raison du rejet |
|---|---|---|---|
| Tout cloud (Anthropic) | Qualité maximale, simple | Toutes les données au cloud | Viole Privacy First |
| Tout local (MLX-LM) | Zéro fuite données | Qualité inférieure, latence variable, RAM limitée | Dégrade l'expérience utilisateur |
| Chiffrement end-to-end avant cloud | Données protégées en transit | Le LLM doit voir le clair pour raisonner | Incompatible avec le cas d'usage |

## Conséquences

### Positives
- Messages iMessage ne quittent jamais la machine
- Qualité optimale via le meilleur LLM par cas d'usage
- PII anonymisé avant tout appel cloud
- Double barrière (anonymisation + DataBoundary)
- 44 tests validant le package

### Négatives
- Complexité accrue du pipeline
- MLX-LM consomme RAM (~8GB pour Qwen3-30B-A3B)
- Deux API keys à gérer (Anthropic + DeepSeek)
- Package `jarvis/` pas encore câblé dans `main.py` (TD-0004)

### Risques
- Faux négatif du routeur envoyant des données sensibles au cloud (mitigé par DataBoundary en dernière ligne)
- Performance MLX-LM insuffisante sur requêtes complexes (fallback cloud impossible pour iMessage)
