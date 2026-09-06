"""Hauptfenster: Thermobild links, ROI-/Legenden-Steuerung und
Zeitverlauf/Live-Cursor rechts als andockbare, frei in der Breite
verstellbare Panels.

Die Klasse selbst ist auf mehrere Dateien nach fachlichem Themenblock
aufgeteilt (siehe die einzelnen Module in diesem Paket, insbesondere
window.py für den Konstruktor); dieses __init__.py re-exportiert die von
außerhalb (Tests, run.py) genutzten Namen unverändert.

Hinweis fuer einige Dialog-Klassen (AxisSettingsDialog/GraphicExportDialog/
CsvColumnDialog/VideoExportDialog): Tests ersetzen diese teils per
monkeypatch.setattr(thermal_viewer.main_window, "XDialog", FakeDialog), um
ohne echten Dialog zu testen. Ein gewöhnlicher `from ..dialogs import
XDialog` auf Modulebene in einer der Mixin-Dateien würde das NICHT
respektieren (bindet einmalig beim Modul-Import an die Original-Klasse,
unabhängig von einem späteren Patch auf DIESEM Paket). Die betroffenen
Aufrufstellen importieren die jeweilige Klasse deshalb bewusst lokal
(`from . import XDialog`, direkt vor der Erzeugung) statt auf Modulebene --
das liest den Namen bei jedem Aufruf frisch aus diesem __init__.py."""
from __future__ import annotations

from ..dialogs import AxisSettingsDialog, CsvColumnDialog, FilenameTemplateDialog, GraphicExportDialog, VideoExportDialog
from ..plot_items import TimeAxisItem, _StaysOpenMenu
from ..roi_entry import DEFAULT_ROI_SIZE, MAX_ROI_COUNT, default_roi_name
from .constants import (
    COLORMAPS,
    INTERP_END_CAPTURE_LABEL,
    INTERP_END_LABEL,
    INTERP_START_CAPTURE_LABEL,
    INTERP_START_LABEL,
    THEMES,
)
from .window import MainWindow

__all__ = [
    "AxisSettingsDialog",
    "COLORMAPS",
    "CsvColumnDialog",
    "DEFAULT_ROI_SIZE",
    "FilenameTemplateDialog",
    "GraphicExportDialog",
    "INTERP_END_CAPTURE_LABEL",
    "INTERP_END_LABEL",
    "INTERP_START_CAPTURE_LABEL",
    "INTERP_START_LABEL",
    "MAX_ROI_COUNT",
    "MainWindow",
    "THEMES",
    "TimeAxisItem",
    "VideoExportDialog",
    "_StaysOpenMenu",
    "default_roi_name",
]
