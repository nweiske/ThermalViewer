"""Regressionstests fuer die Dialog-/Widget-Audit-Fixes (thermal_viewer/dialogs/)."""
from __future__ import annotations

import pytest
from qtpy import QtCore, QtGui, QtWidgets

from thermal_viewer.dialogs import AxisSettingsDialog, CsvColumnDialog, sanitize_filename_prefix
from thermal_viewer.dialogs import panels
from thermal_viewer.dialogs.panels import AxisOverridePanel, ExportPreviewPanel


@pytest.mark.parametrize("raw", ["CON", "nul", "Aux.backup", "COM1", "lpt9 "])
def test_sanitize_prefix_avoids_windows_reserved_names(raw):
    result = sanitize_filename_prefix(raw)
    assert result.startswith("_")
    assert result[1:].strip() == raw.strip()


def test_sanitize_prefix_keeps_normal_and_near_reserved_names():
    assert sanitize_filename_prefix("Frame_IDX_") == "Frame_IDX_"
    assert sanitize_filename_prefix("COM10") == "COM10"
    assert sanitize_filename_prefix("CONSOLE") == "CONSOLE"


def test_sanitize_prefix_replaces_control_characters():
    assert sanitize_filename_prefix("a\tb\x01c") == "a_b_c"


def test_preview_panel_survives_failing_provider(qapp):
    panel = ExportPreviewPanel()

    def broken_provider():
        raise RuntimeError("Renderfehler")

    panel._refresh_now(broken_provider)  # darf nicht werfen
    assert "nicht verfügbar" in panel.label.text()

    good = QtGui.QImage(8, 8, QtGui.QImage.Format.Format_RGB32)
    good.fill(QtGui.QColor("red"))
    panel._refresh_now(lambda: good)
    assert panel.label.text() == ""


def test_axis_panel_reopen_uses_override_auto_spacing(qapp, monkeypatch):
    captured: list[dict] = []

    class FakeAxisDialog:
        def __init__(self, parent, **kwargs):
            captured.append(kwargs)

        def exec(self):
            return QtWidgets.QDialog.DialogCode.Accepted

        def x_manual(self): return False
        def x_range(self): return (0.0, 10.0)
        def x_manual_spacing(self): return False
        def x_spacing(self): return 60.0
        def y_manual_range(self): return False
        def y_range(self): return (0.0, 50.0)
        def y_manual_spacing(self): return False
        def y_spacing(self): return 5.0

    monkeypatch.setattr(panels, "AxisSettingsDialog", FakeAxisDialog)
    state = {
        "x_min": 0.0, "x_max": 100.0, "y_min": 0.0, "y_max": 40.0, "x_auto": True, "y_auto": True,
        "x_runtime_mode": True, "x_spacing": 30.0, "y_spacing": 2.0,
    }
    parent = QtWidgets.QDialog()
    panel = AxisOverridePanel(parent, state)
    panel._open_sub_dialog()  # erstes Oeffnen: Hauptfenster-Zustand (Abstaende manuell)
    assert captured[0]["y_spacing"] == 2.0 and captured[0]["x_spacing"] == 30.0
    panel._open_sub_dialog()  # Override hat "automatisch" gewaehlt -> nicht wieder "manuell" anzeigen
    assert captured[1]["y_spacing"] is None
    assert captured[1]["x_spacing"] is None


def test_axis_dialog_keeps_precision_for_hours(qapp):
    dialog = AxisSettingsDialog(
        None, current_x_min=0.0, current_x_max=4500.0, current_y_min=0.0, current_y_max=50.0,
        x_manual=True, runtime_unit="h",
    )
    assert dialog.x_range() == pytest.approx((0.0, 4500.0))  # 1,25 h, nicht auf 1,3 h gerundet


def test_csv_all_checkbox_resyncs_px_master(qapp):
    entries = [
        {"name": f"ROI {i}", "width_px": 10.0, "height_px": 10.0, "width_mm": None, "height_mm": None}
        for i in range(3)
    ]
    dialog = CsvColumnDialog(None, entries, QtCore.QSettings("t", "t"))
    dialog._checks[0].setChecked(False)
    dialog.chk_px_all.setChecked(True)
    assert dialog.chk_px_all.isChecked()
    # Zeile 0 wird wieder relevant (px dort noch unangehakt) -> "ALLE px" darf nicht angehakt bleiben
    dialog.chk_all.setChecked(True)
    assert all(chk.isChecked() for chk in dialog._checks)
    assert not dialog._px_checks[0].isChecked()
    assert not dialog.chk_px_all.isChecked()
