"""Graphe vivant des relations — nœuds `people`, arêtes dérivées de la DB.

Deux types d'arêtes, toutes deux ancrées dans des données réelles :
- **utilisateur ↔ personne** : une arête par contact, pondérée par le nombre
  d'échanges connus (`message_count`, iMessage + événements).
- **personne ↔ personne** : dérivée de `cross_insights.people_involved` —
  seul endroit où JARVIS enregistre qu'au moins deux personnes sont citées
  ensemble dans un même pattern. Pas de lien inventé : sans insight
  multi-personnes, aucune arête entre deux contacts.

« Vivant » signifie que le graphe change avec la base : nouveaux contacts,
nouveaux insights actifs, ou insights qui passent `resolved` en font
disparaître les arêtes — recalculé à la demande, jamais mis en cache.
"""

from __future__ import annotations

import json

import config


def build_relationship_graph() -> dict:
    """Construit `{nodes, edges}` à partir de `people`, `relationship_profiles`,
    `cross_insights` — recalcul complet à chaque appel (pas de LLM)."""
    from database import (
        get_active_insights,
        get_all_people,
        get_people_sorted_by_recent,
        get_relationship_profile,
    )

    people = get_all_people()
    message_counts = {p["id"]: p.get("message_count") or 0 for p in get_people_sorted_by_recent()}
    name_to_id = {p["name"]: p["id"] for p in people}

    nodes = [{"id": "user", "name": config.USER_NAME or "Monsieur", "type": "user"}]
    edges = []

    for p in people:
        profile = get_relationship_profile(p["id"]) or {}
        nodes.append({
            "id": f"person:{p['id']}",
            "name": p["name"],
            "type": "person",
            "relationship": p.get("relationship"),
            "sentiment": profile.get("sentiment"),
            "trust_level": profile.get("trust_level"),
            "interaction_frequency": profile.get("interaction_frequency"),
            "last_mentioned": p.get("last_mentioned"),
        })
        edges.append({
            "source": "user",
            "target": f"person:{p['id']}",
            "type": "interaction",
            "weight": message_counts.get(p["id"], 0),
        })

    cross_edges: dict[tuple[int, int], dict] = {}
    for insight in get_active_insights():
        try:
            involved = json.loads(insight.get("people_involved") or "[]")
        except (TypeError, ValueError):
            involved = []
        involved_ids = sorted({name_to_id[n] for n in involved if n in name_to_id})
        for i in range(len(involved_ids)):
            for j in range(i + 1, len(involved_ids)):
                key = (involved_ids[i], involved_ids[j])
                edge = cross_edges.setdefault(key, {
                    "source": f"person:{key[0]}",
                    "target": f"person:{key[1]}",
                    "type": "cross_insight",
                    "weight": 0,
                    "insights": [],
                })
                edge["weight"] += int(insight.get("occurrences") or 1)
                edge["insights"].append(insight.get("content"))

    edges.extend(cross_edges.values())
    return {"nodes": nodes, "edges": edges}
