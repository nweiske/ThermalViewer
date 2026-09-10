"""Integrationstests fuer thermal_viewer/main_window.py -- deckt die
regressionstraechtigsten Bereiche ab: Design/Dunkelmodus, Live-Cursor-
Mittelung, Achsen-Reset/-Einstellungen, die vereinheitlichten Export-
Dialoge (Grafik/Werte/Video-Bildstapel) und Projekt speichern/laden."""
from __future__ import annotations

import json

import pytest
from qtpy import QtCore, QtGui, QtWidgets

import thermal_viewer.main_window as mwmod
from thermal_viewer.main_window import MainWindow, _StaysOpenMenu
from thermal_viewer.widgets import UpwardSafeComboBox


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


def test_window_theme_switch_leaves_graph_and_image_colors_fixed(main_window):
    # Nutzerwunsch: Graphen bleiben IMMER hell (wissenschaftlicher Standard),
    # das Thermobild bleibt IMMER dunkel (Kontrast zu Hotspots) --
    # unabhaengig vom Fenster-Farbschema, das nur die uebrige App-Oberflaeche
    # betrifft (siehe _window_theme_actions).
    mw = main_window
    mw._apply_window_theme("light")
    assert mw._window_theme == "light"
    assert mw._graph_bg == "#ffffff"
    assert mw._image_bg == "#1e1e1e"

    mw._window_theme_actions["dark"].trigger()
    assert mw._window_theme == "dark"
    assert mw._window_theme_actions["dark"].isChecked()
    assert mw._graph_bg == "#ffffff"
    assert mw._image_bg == "#1e1e1e"

    mw._window_theme_actions["light"].trigger()
    assert mw._window_theme == "light"
    assert not mw._window_theme_actions["dark"].isChecked()


def test_apply_default_theme_sets_window_image_and_graph_together(main_window):
    # Folgeanfrage: die beiden "Alles: Hell"/"Alles: Dunkel"-Knoepfe setzen
    # Fenster-, Thermobild- UND Graph-Farbschema gemeinsam -- danach bleiben
    # alle drei trotzdem weiterhin unabhaengig voneinander veraenderbar
    # (siehe test_ansichts_manager_image_and_graph_theme_independent_and_
    # persist_across_restart in smoke_test.py).
    mw = main_window
    try:
        mw._apply_default_theme("dark")
        assert mw._window_theme == "dark"
        assert mw._image_theme == "dark"
        assert mw._graph_theme == "dark"

        mw._apply_default_theme("light")
        assert mw._window_theme == "light"
        assert mw._image_theme == "light"
        assert mw._graph_theme == "light"

        # Danach weiterhin unabhaengig einzeln veraenderbar.
        mw._apply_graph_theme("dark")
        assert mw._graph_theme == "dark"
        assert mw._window_theme == "light"
        assert mw._image_theme == "light"
    finally:
        mw._apply_image_theme("dark")
        mw._apply_graph_theme("light")


def test_no_leftover_single_dark_mode_toggle_attributes(main_window):
    # Regression: der frühere einzelne "Dunkelmodus"-Umschalter (act_dark_mode)
    # wurde durch die beiden "Alles: ..."-Knoepfe PLUS ein eigenstaendiges
    # "Fenster-Farbschema"-Untermenue ersetzt -- diese alten, seitdem toten
    # Attributnamen duerfen nicht wieder auftauchen. _image_theme_actions/
    # _graph_theme_actions/_window_theme_actions sind KEIN Ruecksprung
    # dahin: sie gehoeren zum bewusst UNABHAENGIGEN Ansichts-Manager
    # (Punkt 5) fuer Fenster/Thermobild/Graph.
    assert not hasattr(main_window, "act_dark_mode")
    assert not hasattr(main_window, "_on_dark_mode_toggled")
    assert not hasattr(main_window, "_theme_actions")
    assert not hasattr(main_window, "_graph_theme_mode")


def test_upward_safe_combo_box_flips_popup_above_when_it_would_overflow_bottom():
    # Bugreport: "im Vollbild-Modus geht das Dropdown [der Zeitachse-Combobox
    # unter den Kurven-Graphen] unten aus dem Bildschirm raus -- ich kann die
    # letzte Option nicht auswaehlen, weil ich sie gar nicht sehe". Qt richtet
    # die Popup-Liste einer QComboBox immer nach UNTEN aus, ohne das bei
    # Platzmangel wie hier automatisch zu korrigieren.
    class _FakeScreen:
        def availableGeometry(self):
            return QtCore.QRect(0, 0, 800, 600)

    combo = UpwardSafeComboBox()
    try:
        combo.addItem("a")
        combo.addItem("b")
        combo.addItem("c")
        combo.move(10, 590)  # nahe am unteren Rand des (gefakten) Bildschirms
        combo.resize(100, 20)
        combo.show()
        combo.screen = lambda: _FakeScreen()
        combo.showPopup()
        popup = combo.view().window()
        combo_top = combo.mapToGlobal(QtCore.QPoint(0, 0)).y()
        assert popup.geometry().y() < combo_top, "Popup muss bei Platzmangel oberhalb der Combobox erscheinen"
        combo.hidePopup()
    finally:
        combo.close()


def test_upward_safe_combo_box_leaves_popup_untouched_when_it_fits():
    # Gegenprobe: passt die Liste normal auf den (gefakten) Bildschirm, darf
    # UpwardSafeComboBox NICHTS an Qts eigener Platzierung aendern (kein
    # unnoetiges Umklappen) -- verglichen gegen eine normale QComboBox an
    # exakt derselben Position, da die genaue Popup-Position unter der
    # Offscreen-QPA-Plattform nicht mit der Combobox-Position selbst
    # zusammenhaengt (kein echter Fensterserver).
    class _FakeScreen:
        def availableGeometry(self):
            return QtCore.QRect(0, 0, 8000, 6000)  # riesig -> nie ein Ueberlauf

    def popup_geometry(cls):
        combo = cls()
        try:
            combo.addItem("a")
            combo.addItem("b")
            combo.move(10, 10)
            combo.resize(100, 20)
            combo.show()
            combo.screen = lambda: _FakeScreen()
            combo.showPopup()
            geom = combo.view().window().geometry()
            combo.hidePopup()
            return geom
        finally:
            combo.close()

    assert popup_geometry(UpwardSafeComboBox) == popup_geometry(QtWidgets.QComboBox)


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


# ------------------------------------------------- Aktivitaets-/Statusleiste

def test_idle_guidance_reflects_current_state(loaded_main_window):
    # Bugreport/Nutzerwunsch: "Anleitung/Anweisung, was User als naechstes
    # machen muss" -- die permanente Aktivitaetsanzeige unten links soll bei
    # jedem relevanten Zustandswechsel einen passenden Hinweis zeigen.
    mw = loaded_main_window
    assert not any(e.placed for e in mw.roi_entries)
    assert "Messbereich platzieren" in mw._activity_label.text()

    mw.roi_entries[0].place(2, 2, 3, 3)
    mw._refresh_idle_guidance()
    assert mw._activity_label.text() == "Bereit."

    mw._watched_folder = mw.recording.paths[0].parent
    mw._live_watch_timer.start()
    try:
        mw._refresh_idle_guidance()
        assert "Live-Überwachung aktiv" in mw._activity_label.text()
    finally:
        mw._live_watch_timer.stop()
        mw._watched_folder = None


def test_idle_guidance_prioritizes_roi_hint_over_live_watch(loaded_main_window):
    # Bugfix: Live-Ueberwachung startet nach "Ordner öffnen…" immer sofort
    # automatisch (siehe _load_folder), bereits BEVOR ein Messbereich
    # platziert wurde -- ohne diese Prioritaet waere der "Messbereich
    # platzieren"-Hinweis (der eigentliche Kern des Nutzerwunschs: "was
    # User als naechstes machen muss") in der Praxis nie sichtbar, weil er
    # von der (fast immer gleichzeitig zutreffenden) Live-Ueberwachung-
    # Meldung dauerhaft verdeckt wuerde.
    mw = loaded_main_window
    assert not any(e.placed for e in mw.roi_entries)
    mw._watched_folder = mw.recording.paths[0].parent
    mw._live_watch_timer.start()
    try:
        mw._refresh_idle_guidance()
        assert "Messbereich platzieren" in mw._activity_label.text()
    finally:
        mw._live_watch_timer.stop()
        mw._watched_folder = None


def test_idle_guidance_shows_roi_hint_via_real_open_folder_flow(
    main_window, synthetic_recording_folder, monkeypatch
):
    # End-zu-End-Regression fuer denselben Bug: die Fixture loaded_main_window
    # laedt ueber _load_paths() DIREKT (ohne _load_folder), wodurch die
    # Live-Ueberwachung in den uebrigen Tests nie automatisch mitstartet --
    # der echte Weg ueber "Ordner öffnen…" (_open_folder -> _load_folder)
    # startet sie dagegen IMMER sofort. Deckt genau die Zustandskombination
    # ab, die den Bug tatsaechlich ausgeloest hat.
    mw = main_window
    monkeypatch.setattr(
        QtWidgets.QFileDialog, "getExistingDirectory",
        staticmethod(lambda *a, **k: str(synthetic_recording_folder)),
    )
    mw._open_folder()
    assert mw._live_watch_timer.isActive()
    assert not any(e.placed for e in mw.roi_entries)
    assert "Messbereich platzieren" in mw._activity_label.text(), (
        "der Live-Ueberwachung-Hinweis darf den Platzieren-Hinweis nicht sofort verdecken"
    )


def test_idle_guidance_shows_load_hint_without_a_recording(main_window):
    assert main_window.recording is None
    assert "Ordner öffnen" in main_window._activity_label.text()


def test_activity_progress_shows_and_hides_the_progress_bar(main_window):
    # isHidden() statt isVisible(): die Test-Fixture zeigt das Fenster nie
    # per show() an, wodurch JEDES Kind-Widget isVisible()==False meldet,
    # unabhaengig vom eigenen setVisible()-Aufruf -- isHidden() spiegelt
    # dagegen den tatsaechlich per setVisible() gesetzten Zustand wider.
    mw = main_window
    assert mw._activity_progress.isHidden()

    mw._set_activity_progress("Lade Frames… (3/10)", 0.3)
    assert not mw._activity_progress.isHidden()
    assert mw._activity_progress.value() == 300
    assert mw._activity_label.text() == "Lade Frames… (3/10)"

    mw._refresh_idle_guidance()
    assert mw._activity_progress.isHidden()


# -------------------------------------------------------- Live-Cursor

@pytest.mark.parametrize("size", [1, 3, 5, 7, 9, 11, 13, 15])
def test_live_cursor_bounds_odd_sizes_match_legacy_symmetric_window(loaded_main_window, size):
    mw = loaded_main_window
    mw._live_cursor_kernel_size = size
    row0, row1, col0, col1 = mw._live_cursor_bounds(10, 10)
    half = size // 2
    assert (row1 - row0) == size
    assert (col1 - col0) == size
    assert row0 == 10 - half
    assert row1 == 10 + half + 1


def test_kernel_size_menu_offers_only_odd_sizes_with_5x5_default(main_window):
    # Bugfix-Regression: 10x10 (einzige GERADE Groesse) wurde entfernt --
    # ausschliesslich ungerade Kantenlaengen liegen symmetrisch um ein
    # Mittelpunkt-Pixel. 5x5 ist jetzt der Standard (vorher 1x1).
    assert set(main_window._live_cursor_kernel_actions.keys()) == {1, 3, 5, 7, 9, 11, 13, 15}
    assert 10 not in main_window._live_cursor_kernel_actions
    assert main_window._live_cursor_kernel_size == 5
    assert main_window._live_cursor_kernel_actions[5].isChecked()
    assert main_window._live_cursor_kernel_actions[9].text() == "9×9 Pixel (Mittelwert)"
    assert main_window._live_cursor_kernel_actions[15].text() == "15×15 Pixel (Mittelwert)"


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


# --------------------------------------------------- Maßstab/Messungen

def test_clamp_label_offset_keeps_long_ruler_labels_visibly_near_the_line():
    # Bugfix Folgeanfrage ("auch wenn ich die Box loslasse springt sie nicht
    # zurueck"): der urspruengliche Faktor (Linienlaenge * 1.5) ergab fuer
    # eine Linie, die einen guten Teil des Bilds ueberspannt (typischer
    # Maßstab), einen Radius GROESSER als das ganze sichtbare Bild -- das
    # Clamping griff dann in der Praxis nie. Mit dem neuen Faktor (0.5) darf
    # die Beschriftung sich hoechstens um die HALBE Linienlaenge vom
    # Mittelpunkt entfernen.
    from thermal_viewer.measurement import clamp_label_offset

    line_length = 300.0
    far_offset = QtCore.QPointF(280.0, 0.0)  # fast so weit wie die Linie selbst lang ist
    clamped = clamp_label_offset(far_offset, line_length)
    dist = (clamped.x() ** 2 + clamped.y() ** 2) ** 0.5
    assert dist == pytest.approx(150.0), "muss auf die HALBE Linienlaenge begrenzt werden"

    # Innerhalb des erlaubten Radius bleibt der Versatz unveraendert.
    near_offset = QtCore.QPointF(100.0, 0.0)
    assert clamp_label_offset(near_offset, line_length) == near_offset

    # Sehr kurze/punktfoermige Linien: feste Untergrenze statt eines
    # verschwindend kleinen Radius.
    tiny_far_offset = QtCore.QPointF(50.0, 0.0)
    clamped_tiny = clamp_label_offset(tiny_far_offset, 2.0)
    dist_tiny = (clamped_tiny.x() ** 2 + clamped_tiny.y() ** 2) ** 0.5
    assert dist_tiny == pytest.approx(20.0)


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


# ---------------------------------------------------- Rohdaten-Bereinigung

def test_cleaning_candidates_require_all_points_to_exceed_threshold(loaded_main_window):
    # Nutzerentscheidung (Rueckfrage): ein Bild gilt nur als Ausreißer, wenn
    # AN JEDEM markierten Punkt der Schwellenwert ueberschritten wird -- ein
    # Bild, an dem nur EIN Punkt springt, darf NICHT markiert werden.
    mw = loaded_main_window
    frames = mw.recording.frames
    # Frame 2: BEIDE Punkte springen (Ausreißer, kehrt bei Frame 3 zurueck --
    # das erzeugt zwangslaeufig AUCH bei Frame 3 einen grossen dT-zum-
    # Vorbild, siehe Docstring von _compute_cleaning_candidates).
    frames[2, 1, 1] = 999.0
    frames[2, 5, 5] = 999.0
    # Frame 4: nur EIN Punkt springt -- darf nicht markiert werden.
    frames[4, 1, 1] = 999.0

    mw._cleaning_points = [(1, 1), (5, 5)]
    mw._cleaning_threshold = 50.0
    # Testet die AND-Logik, NICHT die Mittelungsbereich-Funktion (siehe
    # test_cleaning_kernel_size_*) -- Einzelpixel-Verhalten hier bewusst
    # unabhaengig vom aktuellen Standardwert von _cleaning_kernel_size fixiert.
    mw._cleaning_kernel_size = 1
    candidates = mw._compute_cleaning_candidates()
    assert candidates == {2, 3}, candidates
    assert 4 not in candidates


def test_cleaning_kernel_size_defaults_to_three_by_three(main_window):
    # Nutzerwunsch (auf Rueckfrage bestaetigt): NxN-Mittelung statt reinem
    # Einzelpixel, robuster gegen Sensor-Rauschen an genau einem Pixel --
    # Standard 3x3 statt 1x1.
    assert main_window._cleaning_kernel_size == 3
    assert main_window._cleaning_show_kernel_area is True


def test_cleaning_kernel_size_dilutes_single_pixel_noise_but_catches_area_wide_spikes(loaded_main_window):
    mw = loaded_main_window
    frames = mw.recording.frames
    frames[:] = 20.0
    # Frame 2: NUR das exakte Punkt-Pixel springt (moderat, dT=20) --
    # unter 3x3-Mittelung (8 unveraenderte Nachbarn) auf dT~2.2 verduennt,
    # bleibt also unter dem Schwellenwert.
    frames[2, 5, 5] = 40.0
    mw._cleaning_points = [(5, 5)]
    mw._cleaning_threshold = 15.0

    mw._cleaning_kernel_size = 1
    assert mw._compute_cleaning_candidates() == {2, 3}, (
        "bei Einzelpixel (1x1) muss der Sprung weiterhin erkannt werden"
    )

    mw._cleaning_kernel_size = 3
    assert mw._compute_cleaning_candidates() == set(), (
        "bei 3x3-Mittelung muss ein Rauschen an nur EINEM Pixel verduennt werden"
    )

    # Springt dagegen der GESAMTE 3x3-Bereich um den Punkt, bleibt die
    # Erkennung trotz Mittelung erhalten (kein Rauschen, echte Störung).
    frames[3, 4:7, 4:7] = 40.0
    assert mw._compute_cleaning_candidates() == {3, 4}


def test_cleaning_point_bounds_clip_to_image_edges(loaded_main_window):
    mw = loaded_main_window
    mw._cleaning_kernel_size = 5
    rows, cols = mw.recording.shape
    row0, row1, col0, col1 = mw._cleaning_point_bounds(0, 0)
    assert (row0, col0) == (0, 0), "am Bildrand darf der Bereich nicht ins Negative reichen"
    assert row1 <= rows and col1 <= cols
    row0, row1, col0, col1 = mw._cleaning_point_bounds(rows - 1, cols - 1)
    assert row1 == rows and col1 == cols


def test_draw_cleaning_point_markers_shows_area_rect_only_for_kernel_above_one(loaded_main_window):
    mw = loaded_main_window
    mw._cleaning_points = [(5, 5)]

    mw._cleaning_kernel_size = 1
    mw._draw_cleaning_point_markers()
    # 1x1: nur Kreuz + Nummern-Label, kein zusaetzliches Bereichs-Rechteck.
    assert len(mw._cleaning_point_markers) == 2

    mw._cleaning_kernel_size = 3
    mw._cleaning_show_kernel_area = True
    mw._draw_cleaning_point_markers()
    assert len(mw._cleaning_point_markers) == 3, "bei >1x1 UND aktiviertem Haken kommt das Bereichs-Rechteck dazu"

    mw._cleaning_show_kernel_area = False
    mw._draw_cleaning_point_markers()
    assert len(mw._cleaning_point_markers) == 2, "abgeschaltet zeigt auch >1x1 kein Bereichs-Rechteck"


def test_cleaning_dialog_kernel_combo_and_checkbox_update_main_window_state(loaded_main_window):
    from thermal_viewer.dialogs import DataCleaningDialog

    mw = loaded_main_window
    dlg = DataCleaningDialog(mw)
    try:
        assert dlg.combo_kernel.currentData() == mw._cleaning_kernel_size == 3
        assert dlg.chk_show_kernel_area.isChecked() == mw._cleaning_show_kernel_area is True

        dlg.combo_kernel.setCurrentIndex(dlg._kernel_sizes.index(7))
        assert mw._cleaning_kernel_size == 7

        dlg.chk_show_kernel_area.setChecked(False)
        assert mw._cleaning_show_kernel_area is False

        # sync_kernel_size_combo() muss die Combobox nachziehen, OHNE dabei
        # selbst wieder ein currentIndexChanged auszuloesen (sonst wuerde ein
        # Projekt-Laden mit z.B. Groesse 1 die Combobox auf 1 stellen, was
        # den Handler erneut aufriefe -- hier bereits identisch, aber die
        # Absicherung soll trotzdem gelten).
        mw._cleaning_kernel_size = 1
        dlg.sync_kernel_size_combo()
        assert dlg.combo_kernel.currentData() == 1
    finally:
        dlg.close()


def test_apply_cleaning_exclusions_skips_frames_in_curves_and_restores(loaded_main_window):
    mw = loaded_main_window
    mw.roi_entries[0].place(center_x=3, center_y=3, width=2, height=2)
    mw._recompute_curves()
    x_before, _ = mw.roi_entries[0].curve.getData()
    n = mw.recording.n_frames
    assert len(x_before) == n

    mw._apply_cleaning_exclusions({1, 2})
    x_after, _ = mw.roi_entries[0].curve.getData()
    assert len(x_after) == n - 2
    assert mw._excluded_frame_indices == {1, 2}

    # Wiederherstellen (leere Ausschluss-Menge) bringt die volle Kurve zurueck.
    mw._apply_cleaning_exclusions(set())
    x_restored, _ = mw.roi_entries[0].curve.getData()
    assert len(x_restored) == n
    assert mw._excluded_frame_indices == set()


def test_excluded_frames_are_skipped_during_stepping_but_directly_reachable(loaded_main_window):
    mw = loaded_main_window
    mw._excluded_frame_indices = {2}
    mw._show_frame(1)
    mw._step_frame(1)
    assert mw.current_index == 3, "Einzelschritt muss das ausgeblendete Bild 2 ueberspringen"

    # Direktes Ansteuern (Schieberegler/Zahlenfeld -> _show_frame) bleibt
    # bewusst moeglich, um ein ausgeblendetes Bild pruefen zu koennen.
    mw._show_frame(2)
    assert mw.current_index == 2


def test_cleaning_point_pick_mode_is_mutually_exclusive_with_ruler(loaded_main_window):
    mw = loaded_main_window
    mw._start_cleaning_point_pick()
    assert mw._cleaning_pick_armed is True

    mw._start_ruler_tool()
    assert mw._cleaning_pick_armed is False, "Lineal-Werkzeug muss den Punkt-Modus beenden"
    mw._cancel_ruler_tool()

    mw._start_cleaning_point_pick()
    mw._on_roi_place_toggled(mw.roi_entries[0], True)
    assert mw._cleaning_pick_armed is False, "ROI-Platzieren muss den Punkt-Modus beenden"
    mw._on_roi_place_toggled(mw.roi_entries[0], False)


def test_reload_clears_cleaning_points_and_exclusions(loaded_main_window, synthetic_recording_folder):
    mw = loaded_main_window
    mw._cleaning_points = [(1, 1)]
    mw._excluded_frame_indices = {1}
    # loaded_main_window hat bereits eine Aufnahme -- die neue Rueckfrage
    # (_confirm_discard_current_recording, siehe project_io.py) wird hier
    # als "Verwerfen" simuliert; ihr eigenes Verhalten prueft
    # test_confirm_discard_current_recording_save_discard_cancel.
    mw._confirm_discard_current_recording = lambda: True
    assert mw._load_paths(sorted(synthetic_recording_folder.glob("*.csv")))
    assert mw._cleaning_points == []
    assert mw._excluded_frame_indices == set()


def test_daten_menu_has_cleaning_and_disabled_tiff_import(main_window):
    daten_menu = None
    for action in main_window.menuBar().actions():
        if action.text().replace("&", "") == "Daten":
            daten_menu = action.menu()
            break
    assert daten_menu is not None
    texts = [a.text() for a in daten_menu.actions() if not a.isSeparator()]
    assert "Rohdaten säubern…" in texts
    assert "TIFF-Bilder importieren…" in texts
    tiff_action = next(a for a in daten_menu.actions() if a.text() == "TIFF-Bilder importieren…")
    assert not tiff_action.isEnabled()


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
        show_graph_source_choice=True, live_available=False,
        roi_entries=[(1, "ROI 1")],
    )
    dlg._content_selector.checks[1].setChecked(False)
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

    # Kein automatisch angehaengter Zaehler mehr (Nutzerwunsch "volle
    # Kontrolle ueber den Dateinamen") -- der Zeitstempel im Praefix macht
    # die Namen bereits eindeutig, siehe render_index_token/INDEX_TOKEN.
    written = sorted(p.name for p in stack_dir.glob("*.png"))
    assert written == [
        "Frame_2026-01-01_12-00-00_.png",
        "Frame_2026-01-01_12-00-01_.png",
        "Frame_2026-01-01_12-00-02_.png",
        "Frame_2026-01-01_12-00-03_.png",
        "Frame_2026-01-01_12-00-04_.png",
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


def _click_msgbox_button(role_text):
    """Simuliert per QMessageBox.exec-Patch den Klick auf den Knopf, dessen
    Text role_text enthaelt -- fuer die eigens gebauten (nicht die
    static-convenience-) QMessageBox-Rueckfragen dieser App, siehe
    _confirm_discard_current_recording/_ask_filename_mismatch."""
    def _exec(self):
        for b in self.buttons():
            if role_text in b.text():
                b.click()
                return self.result()
        self.reject()
        return self.result()
    return _exec


def test_confirm_discard_current_recording_skips_dialog_without_recording(main_window):
    assert main_window.recording is None
    assert main_window._confirm_discard_current_recording() is True


def test_confirm_discard_current_recording_discard_and_cancel(loaded_main_window, monkeypatch):
    mw = loaded_main_window
    monkeypatch.setattr(QtWidgets.QMessageBox, "exec", _click_msgbox_button("Verwerfen"))
    assert mw._confirm_discard_current_recording() is True

    monkeypatch.setattr(QtWidgets.QMessageBox, "exec", _click_msgbox_button("Abbrechen"))
    assert mw._confirm_discard_current_recording() is False


def test_confirm_discard_current_recording_save_success_and_own_cancel(loaded_main_window, tmp_path, monkeypatch):
    # Nutzerwunsch: vor dem Verwerfen der aktuellen Auswertung erst die
    # Moeglichkeit bieten, sie als Projekt zu speichern.
    mw = loaded_main_window
    monkeypatch.setattr(QtWidgets.QMessageBox, "exec", _click_msgbox_button("Speichern"))

    proj_path = tmp_path / "vorher.tvproj"
    monkeypatch.setattr(
        QtWidgets.QFileDialog, "getSaveFileName",
        staticmethod(lambda *a, **k: (str(proj_path), "")),
    )
    assert mw._confirm_discard_current_recording() is True
    assert proj_path.exists(), "der Speichern-Knopf haette das Projekt tatsaechlich schreiben muessen"

    # Bricht der Nutzer den Speichern-Dialog SELBST ab (kein Pfad gewaehlt),
    # gilt die gesamte Rueckfrage als Abbruch -- sonst wuerde die neue
    # Messreihe geladen, obwohl der Nutzer eigentlich erst speichern wollte.
    monkeypatch.setattr(
        QtWidgets.QFileDialog, "getSaveFileName",
        staticmethod(lambda *a, **k: ("", "")),
    )
    assert mw._confirm_discard_current_recording() is False


def test_save_and_load_project_roundtrips_cleaning_state(loaded_main_window, tmp_path, monkeypatch):
    # Nutzerwunsch: "wenn ich ein Projekt speichere/lade [möchte ich]
    # wirklich den VOLLSTÄNDIGEN Zustand des Programmes haben" -- betrifft
    # auch die Rohdaten-Bereinigung (Referenzpunkte/Schwellenwert/
    # ausgeblendete Bilder), nicht nur Messbereiche/Messungen/Maßstab.
    mw = loaded_main_window
    # (col, row) wie sie ein echter Bild-Klick liefert (_pixel_at_scene_pos
    # gibt immer int zurueck, siehe mouse_ops.py) -- NICHT float, sonst
    # bleibt der Bug unten (Regression) unentdeckt.
    mw._cleaning_points = [(3, 4), (10, 2)]
    mw._cleaning_kernel_size = 5
    mw._draw_cleaning_point_markers()
    mw._cleaning_threshold = 7.5
    mw._excluded_frame_indices = {1, 3}
    mw._recompute_curves()

    proj_path = tmp_path / "cleaning.tvproj"
    monkeypatch.setattr(
        QtWidgets.QFileDialog, "getSaveFileName",
        staticmethod(lambda *a, **k: (str(proj_path), "")),
    )
    assert mw._save_project() is True

    saved = json.loads(proj_path.read_text(encoding="utf-8"))
    assert saved["bereinigung_punkte"] == [{"x": 3, "y": 4}, {"x": 10, "y": 2}]
    assert saved["bereinigung_schwellenwert"] == 7.5
    assert saved["bereinigung_kernel_groesse"] == 5
    assert saved["bereinigung_ausgeblendete_frames"] == [1, 3]

    mw._cleaning_points = []
    mw._cleaning_kernel_size = 3
    mw._draw_cleaning_point_markers()
    mw._cleaning_threshold = 5.0
    mw._excluded_frame_indices = set()
    mw._recompute_curves()

    monkeypatch.setattr(
        QtWidgets.QFileDialog, "getOpenFileName",
        staticmethod(lambda *a, **k: (str(proj_path), "")),
    )
    mw._load_project()

    assert mw._cleaning_points == [(3, 4), (10, 2)]
    assert mw._cleaning_kernel_size == 5
    assert mw._cleaning_threshold == 7.5
    assert mw._excluded_frame_indices == {1, 3}
    # Regressionscheck: Bugfix -- ein aus der Projektdatei als float (statt
    # int) wiederhergestellter Referenzpunkt liess _compute_cleaning_
    # candidates() mit einem IndexError abstuerzen (numpy erlaubt keine
    # Float-Indizierung von frames[:, r, c]). Muss nach dem Laden anstands-
    # los durchlaufen, egal was die Kandidatenliste konkret enthaelt.
    mw._compute_cleaning_candidates()


def test_load_project_tolerates_non_integer_cleaning_points_in_file(loaded_main_window, tmp_path, monkeypatch):
    # Direkte Regression fuer denselben Bug wie oben, diesmal mit einer
    # handbearbeiteten/aelteren Projektdatei, die die Referenzpunkte als
    # echte JSON-Floats enthaelt (z.B. "x": 3.0 statt "x": 3).
    mw = loaded_main_window
    proj_path = tmp_path / "float_points.tvproj"
    proj_path.write_text(
        json.dumps({
            "format_version": 2,
            "quellordner": str(mw.recording.paths[0].parent),
            "bild_groesse_px": {"zeilen": mw.recording.shape[0], "spalten": mw.recording.shape[1]},
            "bereinigung_punkte": [{"x": 3.0, "y": 4.7}],
            "bereinigung_schwellenwert": 5.0,
            "bereinigung_ausgeblendete_frames": [],
        }),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        QtWidgets.QFileDialog, "getOpenFileName",
        staticmethod(lambda *a, **k: (str(proj_path), "")),
    )
    mw._load_project()

    assert mw._cleaning_points == [(3, 4)]
    assert all(isinstance(v, int) for p in mw._cleaning_points for v in p)
    mw._compute_cleaning_candidates()  # darf nicht mit IndexError abstuerzen
