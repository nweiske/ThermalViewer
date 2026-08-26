"""Integrationstests fuer thermal_viewer/main_window.py -- deckt die
regressionstraechtigsten Bereiche ab: Design/Dunkelmodus, Live-Cursor-
Mittelung, Achsen-Reset/-Einstellungen, die vereinheitlichten Export-
Dialoge (Grafik/Werte/Video-Bildstapel) und Projekt speichern/laden."""
from __future__ import annotations

import json

import pytest
from qtpy import QtGui, QtWidgets

import thermal_viewer.main_window as mwmod
from thermal_viewer.main_window import MainWindow, _StaysOpenMenu


# ---------------------------------------------------------------- Design

def test_dark_palette_sets_all_fusion_shading_roles():
    # Bugfix-Regression: eine fruehere Version setzte nur Window/Base/Text/
    # Button, aber NICHT Light/Midlight/Dark/Mid/Shadow/Link -- genau diese
    # Rollen nutzt der Fusion-Stil fuer Rahmen/Rillen/deaktivierte Elemente,
    # wodurch der Dunkelmodus vorher fleckig/unvollstaendig wirkte.
    palette = MainWindow._dark_palette()
    for role_name in ("Window", "Base", "Text", "Button", "Light", "Midlight", "Dark", "Mid", "Shadow", "Link"):
        role = getattr(QtGui.QPalette, role_name)
        assert palette.color(role).isValid()
    assert palette.color(QtGui.QPalette.Light).lightness() > palette.color(QtGui.QPalette.Dark).lightness()
    assert palette.color(QtGui.QPalette.Window).lightness() < 128


def test_dark_mode_toggle_switches_theme_and_graph_colors(main_window):
    mw = main_window
    mw._apply_theme("light")
    assert mw._current_theme == "light"
    assert mw._graph_bg == "#ffffff"

    mw.act_dark_mode.trigger()
    assert mw._current_theme == "dark"
    assert mw.act_dark_mode.isChecked()
    assert mw._graph_bg == "#1e1e1e"

    mw.act_dark_mode.trigger()
    assert mw._current_theme == "light"
    assert not mw.act_dark_mode.isChecked()


def test_only_a_single_dark_mode_toggle_exists(main_window):
    # Regression: getrennte "Design"/"Grafik-Darstellung"-Untermenues
    # wurden zu einem einzigen Umschalter zusammengelegt.
    assert not hasattr(main_window, "_theme_actions")
    assert not hasattr(main_window, "_graph_theme_actions")
    assert not hasattr(main_window, "_graph_theme_mode")


def test_timestamp_label_uses_font_not_stylesheet(main_window):
    # Bugfix-Regression: ein setStyleSheet() auf diesem Label uebernahm eine
    # spaeter geaenderte QApplication-Palette nicht zuverlaessig (Zeitstempel
    # blieb nach Dunkel->Hell-Wechsel in kaum lesbarer Schrift).
    assert main_window.timestamp_label.styleSheet() == ""
    assert main_window.timestamp_label.font().bold()


def test_ansicht_menu_stays_open_after_checkable_clicks(main_window):
    view_menu = None
    for action in main_window.menuBar().actions():
        if action.text().replace("&", "") == "Ansicht":
            view_menu = action.menu()
            break
    assert view_menu is not None
    assert isinstance(view_menu, _StaysOpenMenu)


# -------------------------------------------------------- Live-Cursor

@pytest.mark.parametrize("size", [1, 3, 5, 7])
def test_live_cursor_bounds_odd_sizes_match_legacy_symmetric_window(loaded_main_window, size):
    mw = loaded_main_window
    mw._live_cursor_kernel_size = size
    row0, row1, col0, col1 = mw._live_cursor_bounds(10, 10)
    half = size // 2
    assert (row1 - row0) == size
    assert (col1 - col0) == size
    assert row0 == 10 - half
    assert row1 == 10 + half + 1


def test_live_cursor_bounds_10x10_is_exactly_ten_pixels_wide(loaded_main_window):
    # Bugfix-Regression: fuer eine GERADE Kantenlaenge ergab die alte
    # "half = size // 2"-Formel nur size-1 Pixel (asymmetrisch) statt
    # tatsaechlich size Pixel -- "10x10" haette 9x9 gemittelt.
    mw = loaded_main_window
    mw._live_cursor_kernel_size = 10
    row0, row1, col0, col1 = mw._live_cursor_bounds(10, 10)
    assert (row1 - row0) == 10
    assert (col1 - col0) == 10


def test_kernel_size_menu_offers_10x10_option(main_window):
    assert 10 in main_window._live_cursor_kernel_actions
    assert main_window._live_cursor_kernel_actions[10].text() == "10×10 Pixel (Mittelwert)"


# ------------------------------------------------------------- Achsen

def test_reset_plot_view_does_not_leave_autorange_permanently_enabled(loaded_main_window):
    # Bugfix-Regression: enableAutoRange() vor autoRange() liess den
    # Auto-Fit-Modus dauerhaft an, wodurch die Ansicht bei jeder weiteren
    # Datenaenderung nachjustierte und ueber mehrere Klicks "schrumpfte".
    mw = loaded_main_window
    mw._reset_plot_view(mw.timeseries_plot)
    vb = mw.timeseries_plot.getPlotItem().getViewBox()
    assert vb.autoRangeEnabled() == [False, False]


def test_reset_plot_view_is_stable_across_repeated_calls_and_data_updates(roi_and_live_window):
    # Braucht eine ECHTE Kurve (roi_and_live_window statt loaded_main_window):
    # ein komplett leerer Graph hat naturgemaess keinen sinnvollen
    # "stabilen" Zielbereich, an dem autoRange() konvergieren koennte.
    mw = roi_and_live_window
    mw._reset_plot_view(mw.timeseries_plot)
    vb = mw.timeseries_plot.getPlotItem().getViewBox()
    first_x, first_y = vb.viewRange()

    for i in range(mw.recording.n_frames):
        mw._reset_plot_view(mw.timeseries_plot)
        mw._show_frame(i)
        mw._recompute_curves()

    final_x, final_y = vb.viewRange()
    assert final_x == pytest.approx(first_x)
    assert final_y == pytest.approx(first_y)


def test_open_axis_settings_applies_manual_y_range_and_spacing(loaded_main_window, monkeypatch):
    mw = loaded_main_window
    plot_item = mw.timeseries_plot.getPlotItem()

    orig_dialog = mwmod.AxisSettingsDialog

    class AutoAcceptDialog(orig_dialog):
        def exec(self):
            self.chk_y_manual_range.setChecked(True)
            self.spin_y_min.setValue(10.0)
            self.spin_y_max.setValue(30.0)
            self.chk_y_manual_spacing.setChecked(True)
            self.spin_y_spacing.setValue(2.0)
            return QtWidgets.QDialog.DialogCode.Accepted

    monkeypatch.setattr(mwmod, "AxisSettingsDialog", AutoAcceptDialog)
    mw._open_axis_settings(mw.timeseries_plot)

    y0, y1 = plot_item.getViewBox().viewRange()[1]
    assert y0 == pytest.approx(10.0, abs=0.01)
    assert y1 == pytest.approx(30.0, abs=0.01)


def test_reopened_axis_settings_reflects_previously_applied_manual_state(loaded_main_window, monkeypatch):
    # Bugfix-Regression: der Dialog zeigte beim erneuten Oeffnen immer
    # "Automatisch" (Haekchen leer), selbst wenn zuvor bereits ein
    # manueller Bereich/eine manuelle Schrittweite angewendet wurde --
    # dadurch wirkte es so, als sei die vorherige Einstellung nie
    # angekommen.
    mw = loaded_main_window
    plot_item = mw.timeseries_plot.getPlotItem()

    orig_dialog = mwmod.AxisSettingsDialog

    class ApplyManualDialog(orig_dialog):
        def exec(self):
            self.chk_y_manual_range.setChecked(True)
            self.spin_y_min.setValue(5.0)
            self.spin_y_max.setValue(15.0)
            self.chk_y_manual_spacing.setChecked(True)
            self.spin_y_spacing.setValue(2.5)
            return QtWidgets.QDialog.DialogCode.Accepted

    monkeypatch.setattr(mwmod, "AxisSettingsDialog", ApplyManualDialog)
    mw._open_axis_settings(mw.timeseries_plot)

    captured = {}

    class CaptureStateDialog(orig_dialog):
        def __init__(self, *args, **kwargs):
            captured.update(kwargs)
            super().__init__(*args, **kwargs)

        def exec(self):
            return QtWidgets.QDialog.DialogCode.Rejected

    monkeypatch.setattr(mwmod, "AxisSettingsDialog", CaptureStateDialog)
    mw._open_axis_settings(mw.timeseries_plot)

    assert captured["y_manual_range"] is True
    assert captured["y_spacing"] == pytest.approx(2.5)


def test_plot_context_menu_hides_unneeded_pyqtgraph_entries(loaded_main_window):
    plot_item = loaded_main_window.timeseries_plot.getPlotItem()
    hidden = {"Transforms", "Downsample", "Average", "Alpha", "Points"}
    found = {action.text() for action in plot_item.ctrlMenu.actions()}
    assert hidden <= found
    for action in plot_item.ctrlMenu.actions():
        if action.text() in hidden:
            assert not action.isVisible()


# ------------------------------------------------------------- ROI

def test_roi_label_shows_temperature_on_same_line_as_name(loaded_main_window):
    mw = loaded_main_window
    mw._add_roi_entry()
    entry = mw.roi_entries[-1]
    entry.place(center_x=3, center_y=3, width=2, height=2)
    mw._recompute_curves()
    mw._show_frame(0)

    text = entry.label.textItem.toPlainText()
    assert "\n" not in text
    assert entry.name in text
    assert "°C" in text


# --------------------------------------------------------- Datei-Menue

def test_open_files_action_was_removed(main_window):
    # Punkt: der Ordner-Import deckt den Anwendungsfall bereits ab, ein
    # Einzelbild ergibt fuer diese App ohnehin keinen Sinn.
    assert not hasattr(main_window, "_open_files")


# ------------------------------------------------------------ Export

def test_export_graphic_requires_at_least_one_curve_selected(loaded_main_window, monkeypatch):
    from thermal_viewer.dialogs import GraphicExportDialog

    # Verhindert, dass die erwartete Warnmeldung als echter, blockierender
    # modaler Dialog aufgeht (kein Nutzer da, der ihn wegklicken koennte).
    monkeypatch.setattr(QtWidgets.QMessageBox, "information", staticmethod(lambda *a, **k: None))

    dlg = GraphicExportDialog(
        loaded_main_window, loaded_main_window._settings, default_dpi=150,
        colormaps=[("Ironbow", "CET-L17")], current_colormap_index=0, current_invert=False,
        current_level_mode="global", current_min=0.0, current_max=50.0,
        current_time_axis_mode="clock", show_graph_source_choice=True, live_available=False,
    )
    dlg.chk_include_timeseries.setChecked(False)
    dlg._on_accept()
    assert dlg.result() != QtWidgets.QDialog.DialogCode.Accepted
    dlg.close()


def test_export_graphic_writes_a_png(roi_and_live_window, tmp_path, monkeypatch):
    mw = roi_and_live_window
    out_path = tmp_path / "Graph.png"

    orig_dialog = mwmod.GraphicExportDialog

    class AutoAcceptDialog(orig_dialog):
        def exec(self):
            return QtWidgets.QDialog.DialogCode.Accepted

    monkeypatch.setattr(mwmod, "GraphicExportDialog", AutoAcceptDialog)
    monkeypatch.setattr(
        QtWidgets.QFileDialog, "getSaveFileName",
        staticmethod(lambda *a, **k: (str(out_path), "PNG-Bild (*.png)")),
    )

    mw._export_graphic()

    assert out_path.exists()
    assert out_path.stat().st_size > 0


@pytest.mark.parametrize("fmt,check", [
    ("csv", ";"),
    ("text", "\t"),
])
def test_export_values_csv_and_text_use_expected_delimiter(roi_and_live_window, tmp_path, monkeypatch, fmt, check):
    mw = roi_and_live_window
    out_path = tmp_path / f"Werte.{fmt}"

    orig_dialog = mwmod.CsvColumnDialog

    class AutoAcceptDialog(orig_dialog):
        def exec(self):
            self.combo_format.setCurrentIndex(self.combo_format.findData(fmt))
            return QtWidgets.QDialog.DialogCode.Accepted

    monkeypatch.setattr(mwmod, "CsvColumnDialog", AutoAcceptDialog)
    monkeypatch.setattr(
        QtWidgets.QFileDialog, "getSaveFileName",
        staticmethod(lambda *a, **k: (str(out_path), "")),
    )

    mw._export_csv()

    content = out_path.read_text(encoding="utf-8-sig")
    header = content.splitlines()[0]
    assert check in header
    assert "Live X-Achse" in header and "Live Y-Achse" in header


def test_export_values_json_contains_real_rounded_numbers(roi_and_live_window, tmp_path, monkeypatch):
    mw = roi_and_live_window
    out_path = tmp_path / "Werte.json"

    orig_dialog = mwmod.CsvColumnDialog

    class AutoAcceptDialog(orig_dialog):
        def exec(self):
            self.combo_format.setCurrentIndex(self.combo_format.findData("json"))
            return QtWidgets.QDialog.DialogCode.Accepted

    monkeypatch.setattr(mwmod, "CsvColumnDialog", AutoAcceptDialog)
    monkeypatch.setattr(
        QtWidgets.QFileDialog, "getSaveFileName",
        staticmethod(lambda *a, **k: (str(out_path), "")),
    )

    mw._export_csv()

    records = json.loads(out_path.read_text(encoding="utf-8"))
    assert isinstance(records, list)
    assert len(records) == mw.recording.n_frames
    live_col_key = next(k for k in records[0] if k.startswith("Live (Cursor)"))
    value = records[0][live_col_key]
    assert isinstance(value, float)
    # Bugfix-Regression: JSON darf nicht das ungerundete float32-Rauschen
    # der Rohdaten ausgeben (z.B. 20.200000762939453 statt 20.2).
    assert value == round(value, 3)


def test_export_video_image_stack_uses_rendered_timestamp_prefix(roi_and_live_window, tmp_path, monkeypatch):
    mw = roi_and_live_window
    stack_dir = tmp_path / "stack"
    stack_dir.mkdir()

    orig_dialog = mwmod.VideoExportDialog

    class AutoAcceptImagesDialog(orig_dialog):
        def exec(self):
            self.radio_output_images.setChecked(True)
            self.combo_image_format.setCurrentIndex(0)
            self.edit_image_prefix.setText("Frame_YYYY-MM-DD_hh-mm-ss_")
            return QtWidgets.QDialog.DialogCode.Accepted

    monkeypatch.setattr(mwmod, "VideoExportDialog", AutoAcceptImagesDialog)
    monkeypatch.setattr(
        QtWidgets.QFileDialog, "getExistingDirectory",
        staticmethod(lambda *a, **k: str(stack_dir)),
    )

    mw._export_video()

    written = sorted(p.name for p in stack_dir.glob("*.png"))
    assert written == [
        "Frame_2026-01-01_12-00-00_1.png",
        "Frame_2026-01-01_12-00-01_2.png",
        "Frame_2026-01-01_12-00-02_3.png",
        "Frame_2026-01-01_12-00-03_4.png",
        "Frame_2026-01-01_12-00-04_5.png",
    ]


# ------------------------------------------------------------ Projekt

def test_save_project_records_source_folder(loaded_main_window, tmp_path, monkeypatch):
    mw = loaded_main_window
    proj_path = tmp_path / "test.tvproj"
    monkeypatch.setattr(
        QtWidgets.QFileDialog, "getSaveFileName",
        staticmethod(lambda *a, **k: (str(proj_path), "")),
    )

    mw._save_project()

    saved = json.loads(proj_path.read_text(encoding="utf-8"))
    assert saved.get("quellordner") == str(mw.recording.paths[0].parent)


def test_load_project_auto_loads_source_folder_without_prior_manual_load(
    loaded_main_window, qapp, tmp_path, monkeypatch
):
    # Bugfix-Regression: "Projekt laden…" ohne bereits geladene Messreihe
    # zeigte vorher nur "Keine Daten" -- der beim Speichern hinterlegte
    # Quellordner wird jetzt automatisch mitgeladen.
    proj_path = tmp_path / "test.tvproj"
    monkeypatch.setattr(
        QtWidgets.QFileDialog, "getSaveFileName",
        staticmethod(lambda *a, **k: (str(proj_path), "")),
    )
    loaded_main_window._save_project()

    # Bewusst NICHT die main_window-Fixture verwenden: die haengt bereits
    # (als Abhaengigkeit von loaded_main_window) an DERSELBEN Instanz --
    # fuer diesen Test wird eine wirklich UNABHAENGIGE, frische Instanz
    # gebraucht, die den "Keine Daten"-Ausgangszustand simuliert.
    fresh = MainWindow()
    try:
        assert fresh.recording is None
        monkeypatch.setattr(
            QtWidgets.QFileDialog, "getOpenFileName",
            staticmethod(lambda *a, **k: (str(proj_path), "")),
        )

        fresh._load_project()

        assert fresh.recording is not None
        assert fresh.recording.n_frames == loaded_main_window.recording.n_frames
    finally:
        fresh.close()
