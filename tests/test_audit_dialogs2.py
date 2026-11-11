"""Regressionstests fuer die zweite Dialog-/Widget-Audit-Runde (thermal_viewer/dialogs/).
Dialoge werden ohne exec() aufgebaut."""
from __future__ import annotations

from datetime import datetime, timedelta

import pytest
from qtpy import QtCore, QtGui, QtWidgets

from thermal_viewer.dialogs import (
    AxisSettingsDialog,
    CsvColumnDialog,
    DataCleaningDialog,
    GraphicExportDialog,
    TiffImportDialog,
    VideoExportDialog,
)
from thermal_viewer.dialogs.panels import ExportPreviewPanel


def _settings(tmp_path):
    return QtCore.QSettings(str(tmp_path / "s.ini"), QtCore.QSettings.Format.IniFormat)


def _video_dialog(timestamps, n_frames=None, **kwargs):
    n = n_frames if n_frames is not None else len(timestamps)
    return VideoExportDialog(
        None, n, [("Grau", "gray")], 0, False, "global", 0.0, 50.0, 10.0,
        timestamps=timestamps, sample_timestamp=timestamps[0] if timestamps else None, **kwargs,
    )


# ------------------------------------------------------------ Achsen-Dialog

@pytest.mark.parametrize("unit,spacing_s", [("h", 300.0), ("min", 7.0), ("s", 60.0)])
def test_axis_dialog_x_spacing_survives_reopen_in_all_units(qapp, unit, spacing_s):
    dlg = AxisSettingsDialog(
        None, 0.0, 3600.0, 0.0, 50.0, x_manual=True, x_runtime_mode=True,
        x_spacing=spacing_s, runtime_unit=unit,
    )
    assert dlg.x_manual_spacing()
    assert dlg.x_spacing() == pytest.approx(spacing_s, rel=0.01)


# --------------------------------------------------------- Vorschau-Panel

def test_preview_panel_timer_dies_with_group_box(qapp):
    panel = ExportPreviewPanel()
    assert panel._timer.parent() is panel.group_box
    panel.schedule_refresh(lambda: None)
    panel.stop()
    assert not panel._timer.isActive()


def test_preview_panel_stop_and_refresh_survive_destroyed_widgets(qapp):
    panel = ExportPreviewPanel()
    panel.schedule_refresh(lambda: QtGui.QImage())
    group_box, label = panel.group_box, panel.label
    group_box.deleteLater()
    QtWidgets.QApplication.sendPostedEvents(None, QtCore.QEvent.Type.DeferredDelete)
    del group_box, label
    panel.stop()  # darf nicht werfen
    panel._refresh_now(lambda: None)  # darf nicht werfen


def _graphic_dialog(tmp_path, **kwargs):
    return GraphicExportDialog(
        None, _settings(tmp_path), colormaps=[("Grau", "gray")],
        show_graph_source_choice=True, live_available=True, roi_entries=[(1, "A"), (2, "B")], **kwargs,
    )


def test_enable_preview_twice_connects_controls_only_once(qapp, tmp_path):
    dlg = _graphic_dialog(tmp_path)
    calls: list[str] = []

    def make(tag):
        def provider():
            calls.append(tag)
            return None
        return provider

    dlg.enable_preview(make("first"))
    dlg.enable_preview(make("second"))
    scheduled: list = []
    dlg._preview_panel.schedule_refresh = lambda provider: scheduled.append(provider)
    dlg.chk_graph_schwindung.setChecked(True)
    assert len(scheduled) == 1  # nicht doppelt verbunden
    scheduled[0]()
    assert calls == ["second"]  # der zuletzt uebergebene Renderer gilt


def test_video_enable_preview_twice_connects_controls_only_once(qapp):
    dlg = _video_dialog([datetime(2026, 1, 1)])
    dlg.enable_preview(lambda: None)
    dlg.enable_preview(lambda: None)
    scheduled: list = []
    dlg._preview_panel.schedule_refresh = lambda provider: scheduled.append(provider)
    dlg.chk_cursor_position.setChecked(True)
    assert len(scheduled) == 1


# ---------------------------------------------------- Video-Export-Dialog

def test_video_dialog_frame_range_is_zero_based_and_validated(qapp, monkeypatch):
    dlg = _video_dialog([datetime(2026, 1, 1) + timedelta(seconds=i) for i in range(5)])
    dlg.spin_start.setValue(2)
    dlg.spin_end.setValue(4)
    assert dlg.frame_range() == (1, 3)
    shown: list[str] = []
    monkeypatch.setattr(QtWidgets.QMessageBox, "warning", staticmethod(lambda *a, **k: shown.append(a[1])))
    dlg.spin_start.setValue(5)
    dlg.spin_end.setValue(1)
    dlg._on_accept()
    assert shown == ["Ungültiger Bereich"]
    assert dlg.result() != QtWidgets.QDialog.DialogCode.Accepted


def test_video_dialog_filename_warning_for_large_range_is_debounced(qapp):
    n = 2000
    t0 = datetime(2026, 1, 1)
    # Alle Frames in derselben Sekunde -> ein reiner Zeitstempel-Name kollidiert.
    dlg = _video_dialog([t0] * n)
    dlg.edit_image_prefix.setText("F_hh-mm-ss")
    assert dlg._filename_warning_timer.isActive()
    dlg._filename_warning_timer.stop()
    dlg._update_filename_warning()
    assert not dlg.lbl_filename_warning.isHidden()
    dlg.edit_image_prefix.setText("F_IDX")
    dlg._update_filename_warning()
    assert dlg.lbl_filename_warning.isHidden()


def test_video_dialog_filename_warning_small_range_is_immediate(qapp):
    t0 = datetime(2026, 1, 1)
    dlg = _video_dialog([t0, t0, t0])
    dlg.edit_image_prefix.setText("F_hh-mm-ss")
    assert not dlg._filename_warning_timer.isActive()
    assert not dlg.lbl_filename_warning.isHidden()


def test_video_dialog_rejects_overlong_image_filename(qapp, monkeypatch):
    dlg = _video_dialog([datetime(2026, 1, 1)])
    dlg.radio_output_images.setChecked(True)
    dlg.edit_image_prefix.setText("x" * 300)
    shown: list[str] = []
    monkeypatch.setattr(QtWidgets.QMessageBox, "warning", staticmethod(lambda *a, **k: shown.append(a[1])))
    dlg._on_accept()
    assert shown == ["Dateiname zu lang"]
    assert dlg.result() != QtWidgets.QDialog.DialogCode.Accepted
    # Im Video-Modus ist der Praefix irrelevant.
    dlg.radio_output_video.setChecked(True)
    dlg._on_accept()
    assert dlg.result() == QtWidgets.QDialog.DialogCode.Accepted


def test_video_dialog_single_frame_and_zero_frames(qapp):
    dlg = _video_dialog([datetime(2026, 1, 1)])
    assert dlg.frame_range() == (0, 0)
    empty = _video_dialog([], n_frames=0)
    assert empty.frame_range() == (0, 0)


# -------------------------------------------------------------- CSV-Dialog

def _entry(name, **extra):
    return {"name": name, "width_px": 4.0, "height_px": 3.0, "width_mm": None, "height_mm": None, **extra}


def test_csv_dialog_zero_entries_cannot_be_accepted(qapp, tmp_path, monkeypatch):
    dlg = CsvColumnDialog(None, [], _settings(tmp_path))
    shown: list[str] = []
    monkeypatch.setattr(QtWidgets.QMessageBox, "information", staticmethod(lambda *a, **k: shown.append(a[1])))
    dlg._on_accept()
    assert shown == ["Keine Auswahl"]
    assert dlg.included() == [] and dlg.column_names() == []


def test_csv_dialog_name_collision_with_fixed_column_is_blocked(qapp, tmp_path, monkeypatch):
    dlg = CsvColumnDialog(None, [_entry("A")], _settings(tmp_path), reserved_names=["Zeitstempel"])
    dlg._edits[0].setText("Zeitstempel")
    shown: list[str] = []
    monkeypatch.setattr(QtWidgets.QMessageBox, "information", staticmethod(lambda *a, **k: shown.append(a[1])))
    dlg._on_accept()
    assert shown == ["Doppelte Spaltennamen"]


def test_csv_dialog_entry_name_is_shown_as_plain_text(qapp, tmp_path):
    dlg = CsvColumnDialog(None, [_entry("Probe <b>1</b>")], _settings(tmp_path))
    labels = [w for w in dlg.findChildren(QtWidgets.QLabel) if "Probe" in w.text() and "(" not in w.text()]
    assert labels and labels[0].textFormat() == QtCore.Qt.TextFormat.PlainText


# ------------------------------------------------------ Tiff-Import-Dialog

@pytest.mark.parametrize("shape", [(1, 1), (2, 3)])
def test_tiff_dialog_with_tiny_images(qapp, shape, monkeypatch):
    import numpy as np

    dlg = TiffImportDialog(None, np.arange(shape[0] * shape[1], dtype=np.uint8).reshape(shape), 1)
    x0, y0, x1, y1 = dlg.crop_rect()
    assert (x0, y0, x1, y1) == (0, 0, shape[1], shape[0])
    # Leerer Ausschnitt wird bei Akzeptieren abgelehnt.
    dlg.roi.setSize((0, 0))
    shown: list[str] = []
    monkeypatch.setattr(QtWidgets.QMessageBox, "warning", staticmethod(lambda *a, **k: shown.append(a[1])))
    dlg._on_accept()
    assert shown == ["Ungültiger Ausschnitt"]
    assert "leer" in dlg.lbl_crop.text()


# --------------------------------------------------- Datenbereinigungs-Dialog

class _Rec:
    def __init__(self, n):
        import numpy as np

        self.n_frames = n
        self.shape = (4, 4)
        self.frames = np.zeros((n, 4, 4), dtype=np.float32)
        self.timestamps = [datetime(2026, 1, 1) + timedelta(seconds=i) for i in range(n)]


def _cleaning_dialog(main_window, n):
    mw = main_window
    mw.recording = _Rec(n)
    mw._cleaning_points = []
    mw._excluded_frame_indices = set()
    mw._push_undo_snapshot = lambda: None
    return DataCleaningDialog(mw)


def test_cleaning_dialog_caps_candidate_widgets_but_keeps_all_excluded(main_window):
    mw = main_window
    dlg = _cleaning_dialog(mw, 1000)
    mw._compute_cleaning_candidates = lambda: set(range(1, 1000))
    mw._apply_cleaning_exclusions = lambda exclude, **kw: setattr(mw, "_excluded_frame_indices", set(exclude))
    mw._cleaning_points = [(0, 0, "or", True)]
    dlg.refresh_candidates()
    assert len(dlg._candidate_checks) == DataCleaningDialog._MAX_LISTED_CANDIDATES
    assert len(mw._excluded_frame_indices) == 999
    assert dlg._candidate_overflow_label is not None

    # Einzelnes Wieder-Einblenden darf die nicht aufgelisteten Bilder nicht verlieren.
    first = min(dlg._candidate_checks)
    dlg._candidate_checks[first].setChecked(False)
    assert first not in mw._excluded_frame_indices
    assert len(mw._excluded_frame_indices) == 998

    # Neuaufbau raeumt den Hinweis auf (kein Anwachsen).
    dlg.refresh_candidates()
    overflow_labels = [w for w in dlg.findChildren(QtWidgets.QLabel) if w.text().startswith("…")]
    assert len(overflow_labels) <= 1


def test_cleaning_dialog_rejected_application_shows_real_state(main_window):
    mw = main_window
    dlg = _cleaning_dialog(mw, 3)
    mw._cleaning_points = [(0, 0, "or", True)]
    mw._excluded_frame_indices = {0}
    mw._compute_cleaning_candidates = lambda: {1, 2}  # zusammen mit 0 = ALLE Bilder
    applied: list[set] = []
    mw._apply_cleaning_exclusions = lambda exclude, **kw: applied.append(set(exclude))  # lehnt ab: Zustand bleibt
    dlg.refresh_candidates()
    assert {i: c.isChecked() for i, c in dlg._candidate_checks.items()} == {0: True, 1: False, 2: False}


def test_cleaning_dialog_single_frame_recording(main_window):
    mw = main_window
    dlg = _cleaning_dialog(mw, 1)
    mw._apply_cleaning_exclusions = lambda exclude, **kw: setattr(mw, "_excluded_frame_indices", set(exclude))
    dlg.refresh_points()
    assert dlg.spin_manual_frame.maximum() == 1
    assert dlg._candidate_checks == {}
