"""Regressionstests fuer die Export-/Render-/Theming-Audit-Fixes (siehe
thermal_viewer/main_window/export_*.py, theming.py, graph_cursor_ops.py und
error_handling.py)."""
from __future__ import annotations

import json
import sys

import numpy as np
import pyqtgraph as pg
import pytest
from qtpy import QtGui, QtWidgets

import thermal_viewer.main_window as mwmod
from thermal_viewer.error_handling import install_global_excepthook


def _record_message_boxes(monkeypatch) -> list[str]:
    texts: list[str] = []

    def fake_exec(self):
        texts.append(self.text())
        return 0

    monkeypatch.setattr(QtWidgets.QMessageBox, "exec", fake_exec)
    return texts


class _FakePreviewDialog:
    """Minimale Dialog-Attrappe fuer _render_export_preview_image."""

    def __init__(self, include_live=False, include_cursor=False):
        self._live = include_live
        self._cursor = include_cursor

    def selected_graph_keys(self):
        return ["zeitverlauf"]

    def graph_position(self):
        return "unten"

    def export_cursor_position(self):
        return self._cursor

    def export_layer_categories(self):
        return {"roi"}

    def included_roi_numbers(self):
        return set()

    def include_live(self):
        return self._live

    def included_crosssection_entry_numbers(self):
        return set()


# --------------------------------------------- Live-Kurve vs. Cursor-Ausblenden

def test_hidden_cursor_inside_graph_content_does_not_break_live_curve_restore(roi_and_live_window):
    """Ohne den Aufruf-Reihenfolge-Fix (graph_content ausserhalb von hidden_cursor)
    wuerde das Wiederherstellen der Live-Kurve mit leerem Hover-Pixel werfen."""
    mw = roi_and_live_window
    mw.chk_show_live_in_timeseries.setChecked(True)
    assert mw.timeseries_live_curve.isVisible()
    with mw._temporary_graph_content(set(), False), mw._maybe_hidden_live_cursor(False):
        assert not mw.timeseries_live_curve.isVisible()
    assert mw.timeseries_live_curve.isVisible()
    assert (mw._hover_row, mw._hover_col) == (2, 3)


def test_preview_with_live_curve_checked_in_ui_keeps_live_curve_and_renders(roi_and_live_window):
    mw = roi_and_live_window
    mw.chk_show_live_in_timeseries.setChecked(True)
    image = mw._render_export_preview_image(_FakePreviewDialog(include_live=False, include_cursor=False))
    assert image is not None
    assert mw.timeseries_live_curve.isVisible()
    assert (mw._hover_row, mw._hover_col) == (2, 3)


def test_preview_with_live_curve_selected_shows_it_while_rendering(roi_and_live_window, monkeypatch):
    mw = roi_and_live_window
    seen = []
    orig = mw._render_widget_image

    def spy(widget, *a, **k):
        if widget is mw.timeseries_plot:
            seen.append(mw.timeseries_live_curve.isVisible())
        return orig(widget, *a, **k)

    monkeypatch.setattr(mw, "_render_widget_image", spy)
    mw._render_export_preview_image(_FakePreviewDialog(include_live=True, include_cursor=False))
    assert seen == [True]
    assert not mw.timeseries_live_curve.isVisible()


def test_image_stack_export_with_live_curve_checked_in_ui_succeeds(roi_and_live_window, tmp_path, monkeypatch):
    mw = roi_and_live_window
    mw.chk_show_live_in_timeseries.setChecked(True)
    stack_dir = tmp_path / "stack"
    stack_dir.mkdir()
    texts = _record_message_boxes(monkeypatch)

    orig_dialog = mwmod.VideoExportDialog

    class Auto(orig_dialog):
        def exec(self):
            self.radio_output_images.setChecked(True)
            self.chk_graph_zeitverlauf.setChecked(True)
            self.edit_image_prefix.setText("F_IDX_")
            return QtWidgets.QDialog.DialogCode.Accepted

    monkeypatch.setattr(mwmod, "VideoExportDialog", Auto)
    monkeypatch.setattr(
        QtWidgets.QFileDialog, "getExistingDirectory", staticmethod(lambda *a, **k: str(stack_dir))
    )
    mw._export_video()

    assert texts == []
    assert len(list(stack_dir.glob("*.png"))) == mw.recording.n_frames
    assert mw.timeseries_live_curve.isVisible()
    assert (mw._hover_row, mw._hover_col) == (2, 3)


# ------------------------------------------------ Context-Manager-Wiederherstellung

def test_graph_content_restores_curves_when_live_curve_setup_fails(roi_and_live_window, monkeypatch):
    mw = roi_and_live_window
    entry = mw.roi_entries[-1]
    entry.curve.setVisible(True)

    def boom():
        raise RuntimeError("boom")

    monkeypatch.setattr(mw, "_show_live_curve_in_timeseries", boom)
    with pytest.raises(RuntimeError):
        with mw._temporary_graph_content(set(), True):
            pass
    assert entry.curve.isVisible()


def test_export_layers_restores_categories_when_apply_fails(roi_and_live_window, monkeypatch):
    mw = roi_and_live_window
    previous = mw._export_layer_categories
    calls = {"n": 0}
    orig = mw._apply_roi_and_shrinkage_visibility

    def flaky():
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("boom")
        orig()

    monkeypatch.setattr(mw, "_apply_roi_and_shrinkage_visibility", flaky)
    with pytest.raises(RuntimeError):
        with mw._temporary_export_layers({"roi"}):
            pass
    assert mw._export_layer_categories == previous


def test_best_legend_placement_resets_first_legend_when_later_one_fails(loaded_main_window, monkeypatch):
    mw = loaded_main_window
    anchors = []
    orig_anchor = mw.timeseries_legend.anchor

    def spy_anchor(item_pos, parent_pos, offset=(0, 0)):
        anchors.append((tuple(item_pos), tuple(parent_pos), tuple(offset)))
        return orig_anchor(item_pos, parent_pos, offset=offset)

    monkeypatch.setattr(mw.timeseries_legend, "anchor", spy_anchor)
    calls = {"n": 0}
    orig_corner = mw._best_legend_corner

    def flaky_corner(plot_item):
        calls["n"] += 1
        if calls["n"] == 2:
            raise RuntimeError("boom")
        return orig_corner(plot_item)

    monkeypatch.setattr(mw, "_best_legend_corner", flaky_corner)
    with pytest.raises(RuntimeError):
        with mw._temporary_best_legend_placement():
            pass
    assert anchors[-1] == ((0, 0), (0, 0), (10, 10))


# ---------------------------------------------------------------- Zeitachsen

def test_temporary_time_display_mode_also_switches_and_restores_shrinkage_axis(loaded_main_window):
    mw = loaded_main_window
    mw._apply_time_display_mode("clock")
    with mw._temporary_time_display_mode("runtime"):
        assert mw.axis_shrinkage_bottom.runtime_mode is True
    assert mw.axis_shrinkage_bottom.runtime_mode is False


def test_rebased_time_axis_shifts_and_restores_shrinkage_curve(loaded_main_window):
    mw = loaded_main_window
    unix = mw.recording.unix_seconds()
    values = np.linspace(0.0, 4.0, len(unix))
    mw.shrinkage_curve.setData(unix, values)
    with mw._rebased_time_axis(mw.shrinkage_plot):
        x, _y = mw.shrinkage_curve.getData()
        assert float(x[0]) == pytest.approx(0.0)
        assert mw.axis_shrinkage_bottom.export_offset == pytest.approx(float(unix[0]))
    x, _y = mw.shrinkage_curve.getData()
    np.testing.assert_allclose(x, unix)
    assert mw.axis_shrinkage_bottom.export_offset == 0.0


# --------------------------------------------------------------- Dateifehler

def test_video_export_error_with_directory_target_still_reports_error(roi_and_live_window, tmp_path, monkeypatch):
    mw = roi_and_live_window
    target = tmp_path / "ziel.mp4"
    target.mkdir()  # Verzeichnis als Ziel: unlink() darauf wirft selbst
    texts = _record_message_boxes(monkeypatch)

    class Auto(mwmod.VideoExportDialog):
        def exec(self):
            self.radio_output_video.setChecked(True)
            return QtWidgets.QDialog.DialogCode.Accepted

    def failing_writer(*a, **k):
        raise RuntimeError("boom-writer")

    monkeypatch.setattr(mwmod, "VideoExportDialog", Auto)
    monkeypatch.setattr("imageio.v2.get_writer", failing_writer)
    monkeypatch.setattr(
        QtWidgets.QFileDialog, "getSaveFileName", staticmethod(lambda *a, **k: (str(target), ""))
    )
    mw._export_video()

    assert any("boom-writer" in t for t in texts)
    assert target.is_dir()


def test_combined_image_export_removes_partially_written_file(roi_and_live_window, tmp_path, monkeypatch):
    mw = roi_and_live_window
    out_path = tmp_path / "Graph.png"
    errors = []

    class Auto(mwmod.GraphicExportDialog):
        def exec(self):
            return QtWidgets.QDialog.DialogCode.Accepted

    def partial_save(self, path, *a, **k):
        with open(path, "wb") as f:
            f.write(b"halb")
        raise OSError("Platte voll")

    monkeypatch.setattr(mwmod, "GraphicExportDialog", Auto)
    monkeypatch.setattr(QtGui.QImage, "save", partial_save)
    monkeypatch.setattr(mw, "_show_export_error", lambda what, exc: errors.append(str(exc)))
    monkeypatch.setattr(
        QtWidgets.QFileDialog, "getSaveFileName",
        staticmethod(lambda *a, **k: (str(out_path), "PNG-Bild (*.png)")),
    )
    mw._export_graphic()

    assert errors == ["Platte voll"]
    assert not out_path.exists()


def test_json_export_writes_null_instead_of_nan(roi_and_live_window, tmp_path, monkeypatch):
    mw = roi_and_live_window
    mw.recording.frames[1, :, :] = np.nan
    out_path = tmp_path / "Werte.json"

    class Auto(mwmod.CsvColumnDialog):
        def exec(self):
            self.combo_format.setCurrentIndex(self.combo_format.findData("json"))
            return QtWidgets.QDialog.DialogCode.Accepted

    monkeypatch.setattr(mwmod, "CsvColumnDialog", Auto)
    monkeypatch.setattr(
        QtWidgets.QFileDialog, "getSaveFileName", staticmethod(lambda *a, **k: (str(out_path), ""))
    )
    mw._export_csv()

    def reject_constant(name):
        raise ValueError(f"ungueltige JSON-Konstante {name}")

    records = json.loads(out_path.read_text(encoding="utf-8"), parse_constant=reject_constant)
    roi_key = next(k for k in records[0] if k.startswith(mw.roi_entries[-1].name))
    assert records[1][roi_key] is None


# ------------------------------------------------------------- Theming / Cursor

def test_graph_theme_recolors_crosssection_legend_labels(loaded_main_window):
    mw = loaded_main_window
    curve = mw.crosssection_plot.plot(pen=pg.mkPen("r"), name="Linie")
    mw._apply_curve_colors("#ffffff", "#123456")
    labels = [label for _sample, label in mw.crosssection_legend.items]
    assert labels
    assert all(QtGui.QColor(label.opts["color"]).name() == "#123456" for label in labels)
    mw.crosssection_plot.removeItem(curve)


@pytest.mark.parametrize("value", [1e18, -1e18, float("nan"), float("inf")])
def test_graph_cursor_x_format_survives_out_of_range_values(loaded_main_window, value):
    mw = loaded_main_window
    mw._apply_time_display_mode("clock")
    assert isinstance(mw._format_graph_cursor_x(value), str)


# ------------------------------------------------------------- Fehler-Hook

def test_excepthook_does_not_stack_dialogs_for_errors_raised_while_dialog_is_open(qapp, tmp_path, monkeypatch):
    monkeypatch.setattr("thermal_viewer.error_handling._LOG_PATH", tmp_path / "log.txt")
    original = sys.excepthook
    shown = []

    def fake_dialog(text):
        shown.append(text)
        try:
            raise ValueError("zweiter Fehler")
        except ValueError:
            sys.excepthook(*sys.exc_info())  # Fehler waehrend der offenen Event-Loop

    monkeypatch.setattr("thermal_viewer.error_handling._show_dialog", fake_dialog)
    try:
        install_global_excepthook()
        try:
            raise ValueError("erster Fehler")
        except ValueError:
            sys.excepthook(*sys.exc_info())
    finally:
        sys.excepthook = original
    assert len(shown) == 1
    assert "zweiter Fehler" in (tmp_path / "log.txt").read_text(encoding="utf-8")
