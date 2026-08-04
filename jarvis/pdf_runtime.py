"""Frontière d'import du moteur PDF local."""

from __future__ import annotations

import atexit
import warnings
from importlib import import_module

# Les wheels PyMuPDF 1.28 supportent Python 3.14 mais leurs bindings SWIG
# émettent encore ces avertissements à l'import puis à la destruction de
# l'interpréteur. Le filtre est strictement borné aux trois types natifs connus.
_SWIG_DEPRECATION = (
    r"builtin type (SwigPyPacked|SwigPyObject|swigvarlink) "
    r"has no __module__ attribute"
)


def _silence_pymupdf_swig_deprecations() -> None:
    warnings.filterwarnings(
        "ignore",
        message=_SWIG_DEPRECATION,
        category=DeprecationWarning,
    )


_silence_pymupdf_swig_deprecations()
# pytest restaure ses filtres après la collecte, avant la finalisation des types
# SWIG. Réinstaller le filtre à atexit couvre cette seconde émission différée.
atexit.register(_silence_pymupdf_swig_deprecations)
pymupdf = import_module("pymupdf")


__all__ = ["pymupdf"]
