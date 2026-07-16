import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from fixtures.pr45_e2e_math import add

def test_add_basic_positives():
    """Vérifie l’addition de deux entiers positifs."""
    assert add(2, 3) == 5

def test_add_negative_and_positive():
    """Vérifie l’addition d’un négatif et d’un positif."""
    assert add(-2, 5) == 3

def test_add_floats():
    """Vérifie l’addition de flottants."""
    assert add(2.5, 3.1) == 5.6

def test_add_zero():
    """Vérifie l’élément neutre."""
    assert add(0, 7) == 7
    assert add(-7, 0) == -7

def test_add_large_numbers():
    """Vérifie l’addition de grands nombres."""
    assert add(10**9, 10**9) == 2 * 10**9
