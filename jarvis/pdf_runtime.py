"""Frontière d'import du moteur PDF local."""

from __future__ import annotations

import warnings
from importlib import import_module

# Les wheels PyMuPDF 1.28 supportent officiellement Python 3.14 mais leurs
# bindings SWIG émettent encore ces avertissements à l'import. La portée reste
# volontairement limitée à cet import et aux trois types natifs identifiés.
with warnings.catch_warnings():
    warnings.filterwarnings(
        "ignore",
        message=(
            r"builtin type (SwigPyPacked|SwigPyObject|swigvarlink) "
            r"has no __module__ attribute"
        ),
        category=DeprecationWarning,
    )
    pymupdf = import_module("pymupdf")


__all__ = ["pymupdf"]
