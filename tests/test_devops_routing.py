"""Tests de routing pour l'agent DEVOPS.

Verifie que classify_category() route correctement chaque type de message
vers la bonne categorie. Priorite : COACH > JOURNAL > SCHOOL > PRODUCTIVITY
> DEVOPS > INFO.
"""

import asyncio
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

import pytest


CASES: list[tuple[str, str]] = [
    ("debug mon API Flask", "DEVOPS"),
    ("comment configurer Tailscale", "DEVOPS"),
    ("erreur Docker build", "DEVOPS"),
    ("git merge conflict sur ma branche", "DEVOPS"),
    ("optimise cette requete SQL", "DEVOPS"),
    ("script bash pour backup", "DEVOPS"),
    ("certificat SSL expire sur le serveur", "DEVOPS"),
    ("architecture microservices pour JARVIS", "DEVOPS"),
    ("cree un webhook GitHub", "DEVOPS"),
    ("pourquoi mon docker-compose crash", "DEVOPS"),
    ("quel temps fait-il ?", "INFO"),
    ("raconte-moi une blague", "INFO"),
    ("c'est quoi la capitale du Japon", "INFO"),
    ("fais un exercice de code", "SCHOOL"),
    ("j'ai un controle de maths demain", "SCHOOL"),
    ("je suis stresse par mon code", "COACH"),
    ("j'en peux plus de ce bug", "COACH"),
    ("dispute avec mon coloc hier soir", "COACH"),
    ("ajoute une tache : deployer le serveur demain", "PRODUCTIVITY"),
    ("rappelle-moi le rendez-vous de 15h", "PRODUCTIVITY"),
    ("planning de la semaine", "PRODUCTIVITY"),
    ("aujourd'hui j'ai debugge pendant 6h, epuisant", "JOURNAL"),
]


@pytest.mark.asyncio
async def test_devops_routing() -> None:
    """Verifie 22/22 classifications correctes."""
    from agents.orchestrator import classify_category

    correct = 0
    failures: list[str] = []

    for message, expected in CASES:
        result = await classify_category(message)
        if result == expected:
            correct += 1
        else:
            failures.append(
                f"FAIL | {message!r} -> {result} (attendu {expected})"
            )

    if failures:
        print("\n".join(failures))
    print(f"\n{correct}/{len(CASES)} corrects")

    assert correct == len(CASES), (
        f"Taux de succes : {correct}/{len(CASES)}"
    )


if __name__ == "__main__":
    async def _main() -> None:
        from agents.orchestrator import classify_category
        correct = 0
        for msg, exp in CASES:
            r = await classify_category(msg)
            s = "OK" if r == exp else "FAIL"
            if r == exp:
                correct += 1
            print(f"  {s} | [{r:12s}] {msg}")
        print(f"\n  {correct}/{len(CASES)} corrects")

    asyncio.run(_main())
