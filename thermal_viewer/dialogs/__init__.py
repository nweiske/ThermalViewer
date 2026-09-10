"""Zusätzliche Dialogfenster für Export- (Grafik, Video, CSV-Spalten) und
Import-Funktionen (Namensschema-Anpassung beim Laden).

Auf mehrere Dateien nach Themenblock aufgeteilt (siehe die einzelnen
Module in diesem Paket); dieses __init__.py re-exportiert alle öffentlichen
Namen unverändert, damit bestehender Code weiterhin einfach
`from thermal_viewer.dialogs import X` nutzen kann."""
from __future__ import annotations

from .csv_dialog import CsvColumnDialog, FilenameTemplateDialog
from .data_cleaning import DataCleaningDialog
from .export_dialogs import GraphicExportDialog, VideoExportDialog
from .filename_tokens import (
    INDEX_TOKEN,
    RUNTIME_TOKEN_HOURS,
    RUNTIME_TOKEN_MINUTES,
    RUNTIME_TOKEN_SECONDS,
    render_export_filename,
    render_index_token,
    render_runtime_token,
    sanitize_filename_prefix,
)
from .graph_selector import GraphContentSelector
from .import_dialogs import ImportSettingsDialog, TiffImportDialog
from .misc_dialogs import AxisSettingsDialog, RulerLengthDialog, StartTimestampDialog
from .panels import AxisOverridePanel, ColorScaleOverridePanel
from .scale_selector import ScaleContentSelector

__all__ = [
    "AxisOverridePanel",
    "AxisSettingsDialog",
    "ColorScaleOverridePanel",
    "CsvColumnDialog",
    "DataCleaningDialog",
    "FilenameTemplateDialog",
    "GraphContentSelector",
    "GraphicExportDialog",
    "INDEX_TOKEN",
    "ImportSettingsDialog",
    "RUNTIME_TOKEN_HOURS",
    "RUNTIME_TOKEN_MINUTES",
    "RUNTIME_TOKEN_SECONDS",
    "RulerLengthDialog",
    "ScaleContentSelector",
    "StartTimestampDialog",
    "TiffImportDialog",
    "VideoExportDialog",
    "render_export_filename",
    "render_index_token",
    "render_runtime_token",
    "sanitize_filename_prefix",
]
