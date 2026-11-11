"""Regressionstests der zweiten Audit-Runde fuer Export/Rendering (siehe
thermal_viewer/main_window/export_*.py, render_pipeline.py, plot_items.py,
graph_cursor_ops.py)."""
from __future__ import annotations

import json

import pytest
from qtpy import QtGui, QtWidgets

import thermal_viewer.main_window as mwmod
from thermal_viewer.plot_items import TimeAxisItem


def _record_message_boxes(monkeypatch) -> list[str]:
    texts: list[str] = []

    def fake_exec(self):
        texts.append(self.text())
        return 0

    monkeypatch.setattr(QtWidgets.QMessageBox, "exec", fake_exec)
    return texts


def _answer_overwrite_prompt(monkeypatch, overwrite: bool) -> list[str]:
    """Beantwortet die Ueberschreiben-Rueckfrage, ohne zu blockieren."""
    texts: list[str] = []

    def fake_exec(self):
        texts.append(self.text())
        return 0

    def fake_clicked(self):
        wanted = "Überschreiben" if overwrite else "Abbrechen"
        return next(b for b in self.buttons() if b.text() == wanted)

    monkeypatch.setattr(QtWidgets.QMessageBox, "exec", fake_exec)
    monkeypatch.setattr(QtWidgets.QMessageBox, "clickedButton", fake_clicked)
    return texts


def _pick_save_path(monkeypatch, path, selected_filter="PNG-Bild (*.png)") -> None:
    monkeypatch.setattr(
        QtWidgets.QFileDialog, "getSaveFileName",
        staticmethod(lambda *a, **k: (str(path), selected_filter)),
    )


def _auto_graphic_dialog(monkeypatch, **overrides):
    class Auto(mwmod.GraphicExportDialog):
        def exec(self):
            for name, value in overrides.items():
                getattr(self, name)(value)
            return QtWidgets.QDialog.DialogCode.Accepted

    monkeypatch.setattr(mwmod, "GraphicExportDialog", Auto)


# --------------------------------------------------------------- Zeitachse (SVG)

def test_runtime_ticks_stay_aligned_to_runtime_zero_while_axis_is_rebased(qapp):
    t0 = 1_767_268_837.3
    axis = TimeAxisItem(orientation="bottom")
    axis.set_runtime_mode(True, t0)
    normal = axis.tickValues(t0, t0 + 600, 800)
    axis.export_offset = t0
    shifted = axis.tickValues(0.0, 600.0, 800)
    assert len(normal) == len(shifted)
    for (sp_n, vals_n), (sp_s, vals_s) in zip(normal, shifted):
        assert sp_n == sp_s
        assert [v - t0 for v in vals_n] == pytest.approx([v + 0.0 for v in vals_s])
    # erster Tick liegt auf Laufzeit 0, nicht bei 37,3 s
    assert shifted[0][1][0] == pytest.approx(0.0)


def test_clock_ticks_of_rebased_axis_match_unrebased_ticks(loaded_main_window):
    mw = loaded_main_window
    axis = mw.axis_timeseries_bottom
    mw._apply_time_display_mode("clock")
    if not hasattr(axis, "fontMetrics"):  # pyqtgraph setzt das erst beim ersten Zeichnen
        axis.fontMetrics = QtGui.QFontMetrics(QtGui.QFont())
    t0 = float(mw.recording.unix_seconds()[0]) + 0.37
    normal = axis.tickValues(t0, t0 + 3600, 800)
    axis.export_offset = t0
    try:
        shifted = axis.tickValues(0.0, 3600.0, 800)
    finally:
        axis.export_offset = 0.0
    assert [lvl[0] for lvl in normal] == [lvl[0] for lvl in shifted]
    for (_sp, vals_n), (_sp2, vals_s) in zip(normal, shifted):
        assert [float(v) - t0 for v in vals_n] == pytest.approx([float(v) for v in vals_s])


# ------------------------------------------------ Live-Kurve ohne Hover-Pixel

def test_graph_content_survives_checked_live_box_without_hover_pixel(loaded_main_window):
    mw = loaded_main_window
    mw.chk_show_live_in_timeseries.setChecked(True)
    mw._hover_row = mw._hover_col = None
    with mw._temporary_graph_content(set(), False):
        assert not mw.timeseries_live_curve.isVisible()
    assert mw.timeseries_live_curve.isVisible()
    assert mw.chk_show_live_in_timeseries.isChecked()


# --------------------------------------------------------- Hilfsfunktionen

def test_best_legend_corner_ignores_hidden_curves(loaded_main_window):
    mw = loaded_main_window
    plot = mw.crosssection_plot
    item = plot.getPlotItem()
    # Ausgeblendete Kurve belegt oben links, die sichtbare die anderen drei Ecken.
    hidden = plot.plot([0.1] * 50, [0.9] * 50)
    shown = plot.plot([0.9, 0.1, 0.9], [0.9, 0.1, 0.1])
    item.vb.setRange(xRange=(0, 1), yRange=(0, 1), padding=0)
    hidden.setVisible(False)
    try:
        corner = mw._best_legend_corner(item)
    finally:
        plot.removeItem(hidden)
        plot.removeItem(shown)
    assert corner[0] == (0, 0)


def test_paused_timers_restart_exactly_the_running_ones_even_after_error(loaded_main_window):
    mw = loaded_main_window
    mw.play_timer.stop()
    mw._live_watch_timer.start(60_000)
    with pytest.raises(RuntimeError):
        with mw._paused_background_timers():
            assert not mw._live_watch_timer.isActive()
            raise RuntimeError("boom")
    assert mw._live_watch_timer.isActive()
    assert not mw.play_timer.isActive()
    mw._live_watch_timer.stop()


def test_graph_cursor_x_format_handles_inf_in_runtime_mode(loaded_main_window):
    mw = loaded_main_window
    mw._apply_time_display_mode("runtime")
    assert isinstance(mw._format_graph_cursor_x(float("inf")), str)


# ------------------------------------------------------ Ueberschreiben-Rueckfrage

def test_confirm_overwrite_only_asks_when_a_file_exists(loaded_main_window, tmp_path, monkeypatch):
    mw = loaded_main_window
    texts = _answer_overwrite_prompt(monkeypatch, overwrite=False)
    assert mw._confirm_overwrite([tmp_path / "gibt_es_nicht.png"]) is True
    assert texts == []
    existing = tmp_path / "da.png"
    existing.write_bytes(b"alt")
    assert mw._confirm_overwrite([existing, existing]) is False
    assert len(texts) == 1 and "1 Datei" in texts[0]
    _answer_overwrite_prompt(monkeypatch, overwrite=True)
    assert mw._confirm_overwrite([existing]) is True


def test_confirm_overwrite_tolerates_unusable_paths(loaded_main_window):
    mw = loaded_main_window
    assert mw._confirm_overwrite(["x" * 5000 + ".png", "ung\0ueltig.png"]) is True


def test_combined_export_cancelled_overwrite_returns_to_dialog_and_keeps_state(
    roi_and_live_window, tmp_path, monkeypatch
):
    mw = roi_and_live_window
    out_path = tmp_path / "Graph.png"
    out_path.write_bytes(b"alt")
    meta = tmp_path / "Graph.json"
    meta.write_text("alt", encoding="utf-8")
    _answer_overwrite_prompt(monkeypatch, overwrite=False)
    _auto_graphic_dialog(monkeypatch)
    _pick_save_path(monkeypatch, tmp_path / "Graph")  # ohne Endung -> Graph.png wird ergaenzt

    class Dlg:
        def dpi(self):
            return 96

        def separate(self):
            return False

        def combined(self):
            return True

        def graph_position(self):
            return "unten"

        def export_cursor_position(self):
            return False

        def include_scale_ruler(self):
            return False

        def included_scale_measurement_numbers(self):
            return set()

        def export_layer_categories(self):
            return {"roi"}

        def use_custom_colors(self):
            return False

        def time_axis_mode(self):
            return "clock"

    retry = mw._export_combined_image(
        Dlg(), [(mw.timeseries_plot, "Titel", "Zeitverlauf")], "x.png", mw._timeseries_metadata
    )
    assert retry is True
    assert mw._export_busy is False
    assert out_path.read_bytes() == b"alt"
    assert meta.read_text(encoding="utf-8") == "alt"


def test_combined_export_overwrite_confirmed_writes_files(roi_and_live_window, tmp_path, monkeypatch):
    mw = roi_and_live_window
    out_path = tmp_path / "Graph.png"
    out_path.write_bytes(b"alt")
    _answer_overwrite_prompt(monkeypatch, overwrite=True)
    _auto_graphic_dialog(monkeypatch)
    _pick_save_path(monkeypatch, out_path)
    mw._export_graphic()
    assert out_path.stat().st_size > 10
    assert json.loads(out_path.with_suffix(".json").read_text(encoding="utf-8"))["dpi"]


def test_single_graph_non_io_failure_keeps_existing_file(roi_and_live_window, tmp_path, monkeypatch):
    mw = roi_and_live_window
    out_path = tmp_path / "Bild.png"
    out_path.write_bytes(b"alt")
    errors = []
    _answer_overwrite_prompt(monkeypatch, overwrite=True)
    _auto_graphic_dialog(monkeypatch)
    _pick_save_path(monkeypatch, tmp_path / "Bild")  # Endung wird ergaenzt -> Rueckfrage
    monkeypatch.setattr(mw, "_show_export_error", lambda what, exc: errors.append(str(exc)))

    def boom(*a, **k):
        raise RuntimeError("render kaputt")

    monkeypatch.setattr(mw, "_save_single_part", boom)
    mw._export_single_graph(mw.timeseries_plot, "x.png")
    assert errors == ["render kaputt"]
    assert out_path.read_bytes() == b"alt"
    assert mw._export_busy is False


def test_svg_failure_leaves_no_part_file_and_keeps_existing_svg(roi_and_live_window, tmp_path, monkeypatch):
    mw = roi_and_live_window
    out_path = tmp_path / "Graph.svg"
    out_path.write_text("<svg/>", encoding="utf-8")
    errors = []
    _answer_overwrite_prompt(monkeypatch, overwrite=True)
    _auto_graphic_dialog(monkeypatch)
    _pick_save_path(monkeypatch, out_path, "SVG-Vektorgrafik (*.svg)")
    monkeypatch.setattr(mw, "_show_export_error", lambda what, exc: errors.append(str(exc)))

    def boom(*a, **k):
        raise RuntimeError("svg kaputt")

    monkeypatch.setattr(mw, "_render_svg_panel", boom)
    mw._export_graphic()
    assert errors == ["svg kaputt"]
    assert out_path.read_text(encoding="utf-8") == "<svg/>"
    assert not list(tmp_path.glob("*.part"))
    assert mw._export_busy is False


def test_single_graph_svg_export_succeeds_without_part_leftover(roi_and_live_window, tmp_path, monkeypatch):
    mw = roi_and_live_window
    out_path = tmp_path / "Einzel.svg"
    texts = _record_message_boxes(monkeypatch)
    _auto_graphic_dialog(monkeypatch)
    _pick_save_path(monkeypatch, out_path, "SVG-Vektorgrafik (*.svg)")
    mw._export_single_graph(mw.timeseries_plot, "x.svg")
    assert texts == []
    assert "<svg" in out_path.read_text(encoding="utf-8")
    assert not list(tmp_path.glob("*.part"))


# ------------------------------------------------------------------- Video/Stapel

class _AutoVideoMixin:
    mode = "images"

    def exec(self):
        if self.mode == "video":
            self.radio_output_video.setChecked(True)
        else:
            self.radio_output_images.setChecked(True)
            self.edit_image_prefix.setText("F_IDX_")
        self.chk_graph_zeitverlauf.setChecked(True)
        return QtWidgets.QDialog.DialogCode.Accepted


def test_image_stack_cancelled_overwrite_writes_nothing(roi_and_live_window, tmp_path, monkeypatch):
    mw = roi_and_live_window
    stack_dir = tmp_path / "stack"
    stack_dir.mkdir()
    existing = stack_dir / "F_1_.png"
    existing.write_bytes(b"alt")
    _answer_overwrite_prompt(monkeypatch, overwrite=False)

    class Auto(_AutoVideoMixin, mwmod.VideoExportDialog):
        mode = "images"

    monkeypatch.setattr(mwmod, "VideoExportDialog", Auto)
    monkeypatch.setattr(
        QtWidgets.QFileDialog, "getExistingDirectory", staticmethod(lambda *a, **k: str(stack_dir))
    )
    mw._export_video()
    assert existing.read_bytes() == b"alt"
    assert len(list(stack_dir.iterdir())) == 1
    assert mw._export_busy is False


def test_image_stack_confirmed_overwrite_replaces_existing_frame(roi_and_live_window, tmp_path, monkeypatch):
    mw = roi_and_live_window
    stack_dir = tmp_path / "stack"
    stack_dir.mkdir()
    existing = stack_dir / "F_1_.png"
    existing.write_bytes(b"alt")
    _answer_overwrite_prompt(monkeypatch, overwrite=True)

    class Auto(_AutoVideoMixin, mwmod.VideoExportDialog):
        mode = "images"

    monkeypatch.setattr(mwmod, "VideoExportDialog", Auto)
    monkeypatch.setattr(
        QtWidgets.QFileDialog, "getExistingDirectory", staticmethod(lambda *a, **k: str(stack_dir))
    )
    mw._export_video()
    assert existing.stat().st_size > 10
    assert len(list(stack_dir.glob("*.png"))) == mw.recording.n_frames


def test_video_failure_before_writer_start_keeps_existing_file(roi_and_live_window, tmp_path, monkeypatch):
    mw = roi_and_live_window
    target = tmp_path / "ziel.mp4"
    target.write_bytes(b"altes video")
    texts = _record_message_boxes(monkeypatch)

    class Auto(_AutoVideoMixin, mwmod.VideoExportDialog):
        mode = "video"

    def boom(*a, **k):
        raise RuntimeError("segmente kaputt")

    monkeypatch.setattr(mwmod, "VideoExportDialog", Auto)
    monkeypatch.setattr(mw, "_tight_glw_segments", boom)
    _pick_save_path(monkeypatch, target, "MP4-Video (*.mp4)")
    mw._export_video()
    assert any("segmente kaputt" in t for t in texts)
    assert target.read_bytes() == b"altes video"
    assert mw._export_busy is False


def test_video_without_suffix_asks_before_overwriting_existing_file(roi_and_live_window, tmp_path, monkeypatch):
    mw = roi_and_live_window
    target = tmp_path / "ziel.mp4"
    target.write_bytes(b"altes video")
    texts = _answer_overwrite_prompt(monkeypatch, overwrite=False)
    opened = []

    class Auto(_AutoVideoMixin, mwmod.VideoExportDialog):
        mode = "video"

        def exec(self):
            if opened:  # zweiter Durchlauf (Rueckkehr zum Dialog) -> beenden
                return QtWidgets.QDialog.DialogCode.Rejected
            opened.append(1)
            return super().exec()

    monkeypatch.setattr(mwmod, "VideoExportDialog", Auto)
    _pick_save_path(monkeypatch, tmp_path / "ziel", "MP4-Video (*.mp4)")
    mw._export_video()
    assert len(texts) == 1
    assert target.read_bytes() == b"altes video"


# ---------------------------------------------------------------------- CSV

def test_csv_export_pauses_background_timers_during_dialogs(roi_and_live_window, tmp_path, monkeypatch):
    mw = roi_and_live_window
    out_path = tmp_path / "Werte.csv"
    mw._live_watch_timer.start(60_000)
    seen = []

    class Auto(mwmod.CsvColumnDialog):
        def exec(self):
            seen.append(mw._live_watch_timer.isActive())
            return QtWidgets.QDialog.DialogCode.Accepted

    monkeypatch.setattr(mwmod, "CsvColumnDialog", Auto)
    _pick_save_path(monkeypatch, out_path, "CSV-Datei (*.csv)")
    mw._export_csv()
    assert seen == [False]
    assert mw._live_watch_timer.isActive()
    assert out_path.exists()
    mw._live_watch_timer.stop()


def test_csv_without_suffix_asks_before_overwriting(roi_and_live_window, tmp_path, monkeypatch):
    mw = roi_and_live_window
    existing = tmp_path / "Werte.csv"
    existing.write_text("alt", encoding="utf-8")
    texts = _answer_overwrite_prompt(monkeypatch, overwrite=False)

    class Auto(mwmod.CsvColumnDialog):
        def exec(self):
            return QtWidgets.QDialog.DialogCode.Accepted

    monkeypatch.setattr(mwmod, "CsvColumnDialog", Auto)
    _pick_save_path(monkeypatch, tmp_path / "Werte", "CSV-Datei (*.csv)")
    mw._export_csv()
    assert len(texts) == 1
    assert existing.read_text(encoding="utf-8") == "alt"


def test_csv_percent_baseline_skips_excluded_first_frame(roi_and_live_window, tmp_path, monkeypatch):
    """Spalten-/Zeilenausrichtung mit ausgeblendetem ersten Bild: die Zeile des
    ausgeblendeten Bilds fehlt, alle uebrigen behalten ihren Zeitstempel."""
    mw = roi_and_live_window
    mw._excluded_frame_indices = {0}
    out_path = tmp_path / "Werte.json"

    class Auto(mwmod.CsvColumnDialog):
        def exec(self):
            self.combo_format.setCurrentIndex(self.combo_format.findData("json"))
            return QtWidgets.QDialog.DialogCode.Accepted

    monkeypatch.setattr(mwmod, "CsvColumnDialog", Auto)
    _pick_save_path(monkeypatch, out_path, "JSON-Datei (*.json)")
    mw._export_csv()
    records = json.loads(out_path.read_text(encoding="utf-8"))
    assert len(records) == mw.recording.n_frames - 1
    assert records[0]["Zeitstempel"] == mw.recording.timestamps[1].strftime("%Y-%m-%d %H:%M:%S")
