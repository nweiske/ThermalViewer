"""Pfade zu mitgelieferten Assets (z.B. App-Icon).

Funktioniert sowohl beim Start aus dem Quellcode als auch aus einer per
PyInstaller gebauten exe (dort liegen Ressourcen im temporären
Entpack-Verzeichnis `sys._MEIPASS`, siehe --add-data in den Build-Befehlen).
"""
from __future__ import annotations

import sys
from pathlib import Path


def resource_path(relative: str) -> Path:
    if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
        base = Path(sys._MEIPASS)
    else:
        base = Path(__file__).resolve().parent
    return base / relative


ICON_PATH = resource_path("resources/icon.ico")
