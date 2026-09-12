"""Integrationstests fuer thermal_viewer/main_window.py -- deckt die
regressionstraechtigsten Bereiche ab: Design/Dunkelmodus, Live-Cursor-
Mittelung, Achsen-Reset/-Einstellungen, die vereinheitlichten Export-
Dialoge (Grafik/Werte/Video-Bildstapel) und Projekt speichern/laden."""
from __future__ import annotations

import json

import numpy as np
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


def test_cleaning_candidates_or_logic_flags_on_a_single_point(loaded_main_window):
    # Punkt 3 (Nutzerwunsch): im "ODER"-Modus reicht bereits EIN springender
    # Punkt, um ein Bild als Ausreißer zu markieren. Frame 2 (nicht das
    # letzte Bild der 5-Frame-Fixture) springt, damit der Ruecksprung bei
    # Frame 3 ebenfalls ein grosses dT erzeugt (gleiches Muster wie im
    # AND-Test oben).
    mw = loaded_main_window
    frames = mw.recording.frames
    frames[2, 1, 1] = 999.0  # nur EIN Punkt springt

    mw._cleaning_points = [(1, 1), (5, 5)]
    mw._cleaning_threshold = 50.0
    mw._cleaning_kernel_size = 1
    mw._cleaning_logic = "or"
    candidates = mw._compute_cleaning_candidates()
    assert candidates == {2, 3}, candidates

    mw._cleaning_logic = "and"
    assert mw._compute_cleaning_candidates() == set(), "im UND-Modus darf derselbe Einzel-Ausschlag nicht reichen"


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
    assert len(mw._cleaning_point_items) == 1
    assert mw._cleaning_point_items[0]["area"] is None

    mw._cleaning_kernel_size = 3
    mw._cleaning_show_kernel_area = True
    mw._draw_cleaning_point_markers()
    assert mw._cleaning_point_items[0]["area"] is not None, "bei >1x1 UND aktiviertem Haken kommt das Bereichs-Rechteck dazu"

    mw._cleaning_show_kernel_area = False
    mw._draw_cleaning_point_markers()
    assert mw._cleaning_point_items[0]["area"] is None, "abgeschaltet zeigt auch >1x1 kein Bereichs-Rechteck"


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


def test_cleaning_dialog_logic_radios_update_main_window_state(loaded_main_window):
    from thermal_viewer.dialogs import DataCleaningDialog

    mw = loaded_main_window
    dlg = DataCleaningDialog(mw)
    try:
        assert dlg.radio_logic_and.isChecked() is True
        assert mw._cleaning_logic == "and"

        dlg.radio_logic_or.setChecked(True)
        assert mw._cleaning_logic == "or"

        dlg.radio_logic_and.setChecked(True)
        assert mw._cleaning_logic == "and"

        mw._cleaning_logic = "or"
        dlg.sync_logic_radios()
        assert dlg.radio_logic_or.isChecked() is True
    finally:
        dlg.close()


def test_cleaning_dialog_checkbox_applies_immediately_without_apply_button(loaded_main_window):
    """Punkt 7 (Nutzerwunsch): kein separater "Anwenden"-Knopf mehr -- jede
    Checkbox in der Liste wirkt sofort auf self._excluded_frame_indices UND
    den angezeigten Frame."""
    from thermal_viewer.dialogs import DataCleaningDialog

    mw = loaded_main_window
    dlg = DataCleaningDialog(mw)
    try:
        assert not hasattr(dlg, "btn_apply")
        mw._excluded_frame_indices = {2}
        dlg.refresh_candidates()
        chk = dlg._candidate_checks[2]
        assert chk.isChecked() is True

        chk.setChecked(False)  # Bild 3 (Index 2) wieder einblenden
        assert mw._excluded_frame_indices == set()

        dlg.spin_manual_frame.setValue(4)
        dlg.btn_manual_exclude.click()
        assert mw._excluded_frame_indices == {3}
    finally:
        dlg.close()


def test_apply_cleaning_exclusions_navigates_away_from_now_hidden_current_frame(loaded_main_window):
    """Punkt 1 (Nutzerwunsch): steht die Anzeige gerade auf einem Bild, das
    JETZT ausgeblendet wird, darf dieses Bild nicht im Viewer stehen bleiben."""
    mw = loaded_main_window
    mw.frame_slider.setValue(2)
    assert mw.current_index == 2

    mw._apply_cleaning_exclusions({2})
    assert mw.current_index != 2
    assert mw.current_index not in mw._excluded_frame_indices


def test_slider_and_spin_navigation_skip_excluded_frames(loaded_main_window):
    """Punkt 1 (Nutzerwunsch): ausgeblendete Bilder sind ueber KEINEN
    Navigationsweg mehr erreichbar, nicht nur Play/Einzelschritt."""
    mw = loaded_main_window
    mw._excluded_frame_indices = {2}

    mw.frame_slider.setValue(0)
    assert mw.current_index == 0
    mw.frame_slider.setValue(2)  # direkt auf das ausgeblendete Bild ziehen
    assert mw.current_index == 3, "muss beim Vorwaertsziehen auf das naechste sichtbare Bild springen"

    mw.frame_slider.setValue(0)
    assert mw.current_index == 0
    mw.frame_spin.setValue(3)  # 1-basiert -> Index 2
    assert mw.current_index == 3, "muss auch ueber das Zahlenfeld springen"

    mw.frame_slider.setValue(4)
    assert mw.current_index == 4
    mw.frame_slider.setValue(2)  # rueckwaerts ziehen -> muss RUECKWAERTS ausweichen
    assert mw.current_index == 1


def test_dragging_cleaning_point_target_item_updates_point_and_redraws(loaded_main_window):
    """Punkt 6 (Nutzerwunsch): Referenzpunkte lassen sich direkt im Bild
    verschieben (pg.TargetItem statt starrem pg.ScatterPlotItem)."""
    mw = loaded_main_window
    mw._cleaning_points = [(2, 3)]
    mw._draw_cleaning_point_markers()
    dot = mw._cleaning_point_items[0]["dot"]
    assert dot.pos().x() == 2.5 and dot.pos().y() == 3.5

    # pg.TargetItem.setPos() alleine feuert nur sigPositionChanged (laufende
    # Bewegung); sigPositionChangeFinished kommt erst von einer echten
    # Maus-Drag-Geste -- hier wie auch sonst im Projekt ueblich (siehe
    # smoke_test.py::_on_measurement_line_dragged) direkt der Handler
    # aufgerufen, der normalerweise an dieses Signal gebunden ist.
    dot.setPos((8.4, 9.2))
    mw._on_cleaning_point_dragged(0, dot)
    assert mw._cleaning_points == [(8, 9)], mw._cleaning_points
    # Marker wurden komplett neu aufgebaut (neues dict/neues TargetItem an
    # der gerundeten Pixelmitte) -- das alte `dot`-Objekt ist danach verwaist.
    new_dot = mw._cleaning_point_items[0]["dot"]
    assert new_dot.pos().x() == 8.5 and new_dot.pos().y() == 9.5


def test_cleaning_point_focus_visuals_fade_all_but_selected(loaded_main_window):
    """Punkt 6 (Nutzerwunsch): Auswahl eines Punkts in der Liste hebt ihn
    hervor (analog zu _apply_interp_focus_visuals bei ROIs)."""
    mw = loaded_main_window
    mw._cleaning_points = [(2, 3), (8, 9)]
    mw._draw_cleaning_point_markers()

    mw._apply_cleaning_point_focus_visuals(0)
    assert mw._cleaning_point_items[0]["dot"].opacity() == 1.0
    assert mw._cleaning_point_items[1]["dot"].opacity() == pytest.approx(0.12)

    mw._apply_cleaning_point_focus_visuals(None)
    assert mw._cleaning_point_items[0]["dot"].opacity() == 1.0
    assert mw._cleaning_point_items[1]["dot"].opacity() == 1.0


def test_cleaning_dialog_point_list_selection_drives_focus_visuals(loaded_main_window):
    from thermal_viewer.dialogs import DataCleaningDialog

    mw = loaded_main_window
    mw._cleaning_points = [(2, 3), (8, 9)]
    mw._draw_cleaning_point_markers()
    dlg = DataCleaningDialog(mw)
    try:
        dlg.refresh_points()
        dlg.points_list.setCurrentRow(1)
        assert mw._cleaning_point_items[1]["dot"].opacity() == 1.0
        assert mw._cleaning_point_items[0]["dot"].opacity() == pytest.approx(0.12)

        dlg.close()
        assert mw._cleaning_point_items[0]["dot"].opacity() == 1.0
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


def test_reload_resets_manual_level_mode_to_global(loaded_main_window, synthetic_recording_folder):
    """Bugreport: im Level-Modus "Manuell" blieben Min/Max nach dem Laden
    einer NEUEN Aufnahme auf den alten (nicht mehr passenden) Werten stehen
    -- das Thermobild wirkte dadurch bei abweichendem Temperaturbereich
    komplett gesaettigt/einfarbig, "als haette es sich nicht aktualisiert"."""
    mw = loaded_main_window
    mw._set_level_mode("manual")
    mw.spin_level_min.setValue(500.0)
    mw.spin_level_max.setValue(600.0)
    assert mw._level_mode() == "manual"

    mw._confirm_discard_current_recording = lambda: True
    assert mw._load_paths(sorted(synthetic_recording_folder.glob("*.csv")))
    assert mw._level_mode() == "global", (
        "Manueller Level-Modus muss beim Laden einer neuen Aufnahme auf den "
        "sich automatisch anpassenden 'Global'-Modus zurueckfallen"
    )
    lo, hi = mw.image_item.getLevels()
    assert (lo, hi) != (500.0, 600.0), "Levels duerfen nicht auf dem alten manuellen Bereich stehen bleiben"


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


# ------------------------------------------------------- Graph-Cursor

def test_graph_mouse_moved_shows_coordinate_label_and_hides_outside_viewbox(loaded_main_window):
    """Punkt 2 (Nutzerwunsch): X/Y-Koordinatenanzeige beim Hovern über die
    Zeitverlaufs-Graphen."""
    mw = loaded_main_window
    view_box = mw.timeseries_plot.getPlotItem().getViewBox()
    view_box.setRange(xRange=(0, 100), yRange=(0, 50), padding=0)
    scene_pos = view_box.mapViewToScene(QtCore.QPointF(50, 25))

    # isHidden() statt isVisible() (siehe Kommentar bei _activity_progress
    # weiter oben): die Test-Fixture zeigt das Fenster nie tatsaechlich an,
    # isVisible() waere deshalb IMMER False, unabhaengig vom eigenen
    # show()/hide()-Aufruf.
    mw._on_graph_mouse_moved(mw.timeseries_plot, mw.lbl_graph_cursor_timeseries, scene_pos)
    assert not mw.lbl_graph_cursor_timeseries.isHidden()
    assert "°C" in mw.lbl_graph_cursor_timeseries.text()
    assert "25.0" in mw.lbl_graph_cursor_timeseries.text()

    # Ausserhalb der ViewBox (z.B. ueber der Achsenbeschriftung) -- Label
    # muss verschwinden statt eine irrefuehrende Koordinate zu zeigen.
    far_outside = QtCore.QPointF(scene_pos.x() - 10_000, scene_pos.y() - 10_000)
    mw._on_graph_mouse_moved(mw.timeseries_plot, mw.lbl_graph_cursor_timeseries, far_outside)
    assert mw.lbl_graph_cursor_timeseries.isHidden()


def test_graph_mouse_moved_hidden_without_recording(main_window):
    mw = main_window
    view_box = mw.timeseries_plot.getPlotItem().getViewBox()
    scene_pos = view_box.mapViewToScene(QtCore.QPointF(0, 0))
    mw._on_graph_mouse_moved(mw.timeseries_plot, mw.lbl_graph_cursor_timeseries, scene_pos)
    assert mw.lbl_graph_cursor_timeseries.isHidden()


def test_graph_cursor_label_follows_runtime_vs_clock_display_mode(loaded_main_window):
    mw = loaded_main_window
    view_box = mw.timeseries_plot.getPlotItem().getViewBox()
    unix = mw.recording.unix_seconds()
    view_box.setRange(xRange=(unix[0], unix[-1]), yRange=(0, 50), padding=0)
    scene_pos = view_box.mapViewToScene(QtCore.QPointF(unix[0], 25))

    mw._apply_time_display_mode("clock")
    mw._on_graph_mouse_moved(mw.timeseries_plot, mw.lbl_graph_cursor_timeseries, scene_pos)
    clock_text = mw.lbl_graph_cursor_timeseries.text()
    assert str(mw.recording.timestamps[0].year) in clock_text

    mw._apply_time_display_mode("runtime")
    mw._on_graph_mouse_moved(mw.timeseries_plot, mw.lbl_graph_cursor_timeseries, scene_pos)
    runtime_text = mw.lbl_graph_cursor_timeseries.text()
    assert runtime_text != clock_text
    assert "00:00:00" in runtime_text


def test_graph_cursor_eventFilter_hides_label_on_leave(loaded_main_window):
    mw = loaded_main_window
    view_box = mw.timeseries_plot.getPlotItem().getViewBox()
    view_box.setRange(xRange=(0, 100), yRange=(0, 50), padding=0)
    scene_pos = view_box.mapViewToScene(QtCore.QPointF(50, 25))
    mw._on_graph_mouse_moved(mw.timeseries_plot, mw.lbl_graph_cursor_timeseries, scene_pos)
    assert not mw.lbl_graph_cursor_timeseries.isHidden()

    leave_event = QtCore.QEvent(QtCore.QEvent.Type.Leave)
    mw.eventFilter(mw.timeseries_plot, leave_event)
    assert mw.lbl_graph_cursor_timeseries.isHidden()


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


def test_show_frame_with_pixel_source_idx_shows_other_pixels_but_own_timestamp(loaded_main_window):
    """Punkt 4 (Nutzerwunsch, Video-Export "Lücke füllen"): _show_frame()
    kann optional die BILDDATEN eines ANDEREN Frames anzeigen, waehrend
    Zeitstempel/Status/Marker weiterhin zum tatsaechlich angeforderten idx
    gehoeren -- self.recording selbst bleibt dabei unangetastet (Invariante:
    Frame-Indizes/Zeitstempel verschieben sich nie)."""
    mw = loaded_main_window
    mw._show_frame(3, pixel_source_idx=1)
    assert mw.current_index == 3
    assert mw.timestamp_label.text().strip() == mw.recording.timestamps[3].strftime("%Y-%m-%d %H:%M:%S")
    np.testing.assert_array_equal(mw.image_item.image, mw.recording.frames[1])
    assert not np.array_equal(mw.recording.frames[1], mw.recording.frames[3]), (
        "Testvoraussetzung: Frame 1 und 3 muessen sich tatsaechlich unterscheiden"
    )


def test_video_export_dialog_freeze_checkbox_visibility_and_getter(qapp):
    from thermal_viewer.dialogs import VideoExportDialog

    common_kwargs = dict(
        parent=None, n_frames=5, colormaps=[("Grau", "grey")], current_colormap_index=0,
        current_invert=False, current_level_mode="global", current_min=0.0, current_max=100.0,
        current_fps=5.0,
    )
    dlg_with = VideoExportDialog(**common_kwargs, has_excluded_frames=True)
    dlg_without = VideoExportDialog(**common_kwargs, has_excluded_frames=False)
    dlg_with.show()
    dlg_without.show()
    try:
        assert dlg_with.chk_freeze_excluded_pixels.isVisible() is True
        assert dlg_without.chk_freeze_excluded_pixels.isVisible() is False

        assert dlg_with.freeze_excluded_frame_pixels() is False  # Standard: aus
        dlg_with.chk_freeze_excluded_pixels.setChecked(True)
        assert dlg_with.freeze_excluded_frame_pixels() is True

        # Nur fuer den Video-Export (Rueckfrage bestaetigt): im Bildstapel-
        # Modus gilt die Option NICHT, auch wenn angehakt.
        dlg_with.radio_output_images.setChecked(True)
        assert dlg_with.freeze_excluded_frame_pixels() is False
        assert dlg_with.chk_freeze_excluded_pixels.isEnabled() is False
    finally:
        dlg_with.close()
        dlg_without.close()


# ------------------------------------------------ Schwindungsmessung

def test_detect_edge_in_window_finds_median_crossing_from_each_side():
    from thermal_viewer.main_window.shrinkage_ops import _detect_edge_in_window

    frame = np.full((4, 10), 10.0)
    frame[:, 3:7] = 50.0  # "Probe" (waermer) in Spalten 3..6, Rest Hintergrund

    # Box umfasst die gesamte Zeile (0..9) -- von links gesucht muss die
    # linke Kante (Spalte 3) gefunden werden, von rechts die rechte (Spalte 6).
    assert _detect_edge_in_window(frame, 0, 4, 0, 10, threshold=30.0, warmer=True, from_left=True) == 3
    assert _detect_edge_in_window(frame, 0, 4, 0, 10, threshold=30.0, warmer=True, from_left=False) == 6

    # "Kaelter als Hintergrund" -- Vorzeichen der Bedingung dreht sich um.
    cold_frame = np.full((4, 10), 50.0)
    cold_frame[:, 3:7] = 10.0
    assert _detect_edge_in_window(cold_frame, 0, 4, 0, 10, threshold=30.0, warmer=False, from_left=True) == 3

    # Keine Zeile ueberschreitet die Schwelle -- None statt Absturz/NaN.
    flat = np.full((4, 10), 10.0)
    assert _detect_edge_in_window(flat, 0, 4, 0, 10, threshold=30.0, warmer=True, from_left=True) is None

    # Box ausserhalb des Bildes (leere Region) -- ebenfalls None.
    assert _detect_edge_in_window(frame, 0, 4, 10, 10, threshold=30.0, warmer=True, from_left=True) is None


def test_track_edge_across_frames_recenters_box_and_falls_back_on_miss():
    from thermal_viewer.main_window.shrinkage_ops import _track_edge_across_frames

    # Box-Breite 10, Start bei Spalte 10 (Box [10,20)). Frame 0's Kante bei
    # Spalte 12 liegt NAHE am linken Rand dieser Box (nicht zentriert) --
    # nach dem Zentrieren um diese Kante (Box wird zu [7,17)) landet Frame
    # 1's tatsaechliche Kante bei Spalte 8 INNERHALB der neu zentrierten,
    # aber AUSSERHALB der urspruenglichen Box: nur mit echtem Nachziehen der
    # Box liefert die Funktion hier 8 statt (bei einer stehenbleibenden Box)
    # faelschlich 10.
    n_cols = 40
    frames = np.full((3, 4, n_cols), 10.0)
    frames[0, :, 12:] = 50.0
    frames[1, :, 8:] = 50.0
    # frames[2] bleibt ueberall 10.0 (kein Uebergang).

    result = _track_edge_across_frames(
        frames, order=[0, 1, 2], row0=0, row1=4, box_width=10,
        initial_col0=10, threshold=30.0, warmer=True, from_left=True, n_cols=n_cols,
    )
    assert result[0] == 12
    assert result[1] == 8, "Box muss sich um die bei Frame 0 gefundene Kante neu zentriert haben"
    assert result[2] == 8, "Ohne Treffer muss das Ergebnis des Vorgaengers uebernommen werden"


def test_track_edge_across_frames_falls_back_to_box_center_without_any_prior_hit():
    from thermal_viewer.main_window.shrinkage_ops import _track_edge_across_frames

    frames = np.full((1, 4, 20), 10.0)  # durchgehend flach -- nie ein Treffer
    result = _track_edge_across_frames(
        frames, order=[0], row0=0, row1=4, box_width=6,
        initial_col0=2, threshold=30.0, warmer=True, from_left=True, n_cols=20,
    )
    assert result[0] == 2 + 6 // 2  # Box-Mitte als Nothilfe


def _make_shrinking_recording_window(loaded_main_window):
    """Ersetzt die Frames der bereits geladenen Fixture-Aufnahme durch ein
    deterministisches, ueber die Zeit SCHRUMPFENDES helles Rechteck (rows
    5:15, urspruenglich Spalten 10:40 in einem 60 Spalten breiten Bild) --
    5 Frames, pro Frame je 1px pro Seite schmaler."""
    mw = loaded_main_window
    rows, cols, n = 20, 60, 5
    frames = np.full((n, rows, cols), 10.0, dtype=np.float32)
    for i in range(n):
        frames[i, 5:15, 10 + i:40 - i] = 50.0
    mw.recording.frames = frames
    mw._set_recording(mw.recording)
    return mw


def test_shrinkage_measurement_end_to_end_tracks_shrinking_width(loaded_main_window):
    mw = _make_shrinking_recording_window(loaded_main_window)
    mw.chk_shrinkage_enabled.setChecked(True)
    assert mw.roi_shrink_left.isVisible()

    rows, cols = mw.recording.shape
    mw.roi_shrink_left.setPos((5, 5), update=False)
    mw.roi_shrink_left.setSize((10, 10))
    mw.roi_shrink_right.setPos((35, 5), update=False)
    mw.roi_shrink_right.setSize((10, 10))

    mw.frame_slider.setValue(0)
    mw._on_shrinkage_set_ref_clicked()
    assert mw._shrinkage_ref_frame == 0

    mw.radio_shrinkage_warmer.setChecked(True)
    mw.spin_shrinkage_threshold.setValue(30.0)
    mw._on_shrinkage_compute_clicked()

    assert mw._shrinkage_result is not None
    widths = mw._shrinkage_result["widths_px"]
    assert len(widths) == 5
    # Das synthetische Rechteck wird pro Bild um 2px (1px je Seite) schmaler.
    assert list(widths) == sorted(widths, reverse=True), widths
    assert widths[0] - widths[-1] == pytest.approx(8.0, abs=1e-6)

    x, y = mw.shrinkage_curve.getData()
    assert len(x) == 5


def test_compute_shrinkage_width_excludes_cleaned_frames_from_edge_tracking(loaded_main_window):
    # Regressionsschutz: die Kantenverfolgung zentriert die Such-Box nach
    # JEDEM verarbeiteten Bild neu um dessen erkannte Kante -- ein von der
    # Rohdaten-Bereinigung ausgeblendetes (typischerweise gestoertes/
    # fehlerhaftes) Bild durfte diese Neu-Zentrierung bisher trotzdem
    # ausloesen, wodurch eine einzelne Stoerung die Box so weit von der
    # wahren Kante wegziehen konnte, dass ALLE nachfolgenden, eigentlich
    # guten Bilder ebenfalls falsch erkannt wurden. Bild 2 hier hat eine
    # Stoerung, die im linken Suchfenster eine Kante bei Spalte 8 vortaeuscht
    # (statt der wahren Kante bei Spalte 12) -- ausgeschlossen darf sie die
    # Verfolgung von Bild 3/4 nicht mehr beeinflussen.
    mw = loaded_main_window
    rows, cols, n = 20, 60, 5
    frames = np.full((n, rows, cols), 10.0, dtype=np.float32)
    for i in range(n):
        frames[i, 5:15, 10 + i:40 - i] = 50.0
    frames[2, 5:15, :] = 10.0
    frames[2, 5:15, 0:20] = 50.0
    mw.recording.frames = frames
    mw._set_recording(mw.recording)

    mw.chk_shrinkage_enabled.setChecked(True)
    mw.roi_shrink_left.setPos((8, 5), update=False)
    mw.roi_shrink_left.setSize((6, 10))
    mw.roi_shrink_right.setPos((35, 5), update=False)
    mw.roi_shrink_right.setSize((10, 10))
    mw.frame_slider.setValue(0)
    mw._on_shrinkage_set_ref_clicked()
    mw.radio_shrinkage_warmer.setChecked(True)
    mw.spin_shrinkage_threshold.setValue(30.0)

    mw._excluded_frame_indices = {2}
    mw._on_shrinkage_compute_clicked()

    left_edges = mw._shrinkage_result["left_edges"]
    # Wahre linke Kante von Bild i liegt bei Spalte 10+i -- unabhaengig von
    # der Stoerung in Bild 2 (dessen eigener Wert wird ohnehin nirgends
    # angezeigt/exportiert, siehe _update_shrinkage_curve/export_csv.py).
    assert left_edges[3] == 13
    assert left_edges[4] == 14


def test_shrinkage_toggle_controls_box_and_curve_visibility(loaded_main_window):
    # roi_shrink_left ist ein pg.ROI (QGraphicsItem), kein QWidget -- dessen
    # isVisible() ist (anders als bei echten QWidgets in diesen Tests, siehe
    # Kommentar bei _activity_progress) NICHT vom ungezeigten Hauptfenster
    # abhaengig, sondern spiegelt direkt den eigenen setVisible()-Aufruf.
    mw = loaded_main_window
    assert not mw.roi_shrink_left.isVisible()

    mw.chk_shrinkage_enabled.setChecked(True)
    assert mw.roi_shrink_left.isVisible()
    assert mw.spin_shrinkage_threshold.isEnabled()

    mw.chk_shrinkage_enabled.setChecked(False)
    assert not mw.roi_shrink_left.isVisible()
    assert not mw.spin_shrinkage_threshold.isEnabled()


def test_shrinkage_state_resets_on_reload(loaded_main_window, synthetic_recording_folder):
    mw = loaded_main_window
    mw.chk_shrinkage_enabled.setChecked(True)
    mw.frame_slider.setValue(1)
    mw._on_shrinkage_set_ref_clicked()
    assert mw._shrinkage_ref_frame == 1

    mw._confirm_discard_current_recording = lambda: True
    assert mw._load_paths(sorted(synthetic_recording_folder.glob("*.csv")))
    assert mw._shrinkage_ref_frame is None
    assert mw._shrinkage_result is None
    assert mw.lbl_shrinkage_ref.text() == "Startbild: nicht gesetzt"


def test_detect_edge_profile_returns_per_row_columns_with_nan_for_misses():
    from thermal_viewer.main_window.shrinkage_ops import _detect_edge_profile

    frame = np.full((4, 10), 10.0)
    frame[0, 3:] = 50.0
    frame[1, 5:] = 50.0
    # Zeile 2 bleibt flach -- kein Uebergang.
    frame[3, 2:] = 50.0

    profile = _detect_edge_profile(frame, 0, 4, 0, 10, threshold=30.0, warmer=True, from_left=True)
    assert profile[0] == 3
    assert profile[1] == 5
    assert np.isnan(profile[2])
    assert profile[3] == 2


def test_paired_row_widths_uses_only_overlapping_valid_rows():
    from thermal_viewer.main_window.shrinkage_ops import _paired_row_widths

    profile_l = np.array([2.0, 2.0, 2.0, np.nan])  # absolute Zeilen 0..3
    profile_r = np.array([8.0, 9.0, np.nan, 7.0])  # absolute Zeilen 1..4
    widths = _paired_row_widths(profile_l, row0_l=0, profile_r=profile_r, row0_r=1)
    # Ueberlappung ist Zeile 1..3; Zeile 3 hat aber KEINE rechte Kante (NaN
    # in profile_r an relativer Position 2) -- bleibt aussen vor.
    assert widths == [6.0, 7.0]


def _make_bulging_sample_window(loaded_main_window):
    """Ein einzelnes Bild mit einer GEWOELBTEN Kontur (wie das Seiten-Profil
    einer runden/zylindrischen Probe): in den meisten Zeilen ist die Probe
    10px breit (Spalten 25:35), in EINER mittleren Zeile ("Äquator") ist
    sie 20px breit (Spalten 20:40). Bei geraden/parallelen Kanten waere die
    Breite in jeder Zeile gleich -- hier absichtlich nicht, um rect- von
    round-Geometrie unterscheidbar zu machen."""
    mw = loaded_main_window
    rows, cols = 20, 60
    frame = np.full((rows, cols), 10.0, dtype=np.float32)
    for r in range(5, 15):
        left, right = (20, 40) if r == 9 else (25, 35)
        frame[r, left:right] = 50.0
    mw.recording.frames = np.array([frame], dtype=np.float32)
    # timestamps/paths auf dieselbe Laenge (1 Frame) kuerzen -- sonst
    # klaffen z.B. unix_seconds() (laenge timestamps) und widths_px (laenge
    # frames) auseinander (siehe _update_shrinkage_curve).
    mw.recording.timestamps = mw.recording.timestamps[:1]
    mw.recording.paths = mw.recording.paths[:1]
    mw._set_recording(mw.recording)
    return mw


def test_shrinkage_round_geometry_uses_widest_row_instead_of_median(loaded_main_window):
    # Nutzerfrage: ist die Schwindungsmessung geometrieabhaengig (Quader vs.
    # Zylinder)? Ja -- fuer eine gewoelbte Kontur unterschaetzt die (fuer
    # gerade Kanten richtige) Median-Bildung ueber die Boxzeilen die wahre
    # Breite systematisch. Der "rund/zylindrisch"-Modus muss stattdessen die
    # breiteste Zeile ("Äquator") finden.
    mw = _make_bulging_sample_window(loaded_main_window)
    mw.roi_shrink_left.setPos((15, 5), update=False)
    mw.roi_shrink_left.setSize((15, 10))
    mw.roi_shrink_right.setPos((30, 5), update=False)
    mw.roi_shrink_right.setSize((15, 10))
    mw.frame_slider.setValue(0)
    mw._on_shrinkage_set_ref_clicked()
    mw.radio_shrinkage_warmer.setChecked(True)
    mw.spin_shrinkage_threshold.setValue(30.0)

    assert mw.radio_shrinkage_rect.isChecked()
    mw._on_shrinkage_compute_clicked()
    rect_width = mw._shrinkage_result["widths_px"][0]
    assert mw._shrinkage_result["geometry"] == "rect"
    assert rect_width == pytest.approx(9.0)

    mw.radio_shrinkage_round.setChecked(True)
    mw._on_shrinkage_compute_clicked()
    round_width = mw._shrinkage_result["widths_px"][0]
    assert mw._shrinkage_result["geometry"] == "round"
    assert round_width == pytest.approx(19.0)
    assert round_width > rect_width


def test_csv_export_includes_shrinkage_width_column(loaded_main_window, tmp_path, monkeypatch):
    mw = _make_shrinking_recording_window(loaded_main_window)
    mw.roi_shrink_left.setPos((5, 5), update=False)
    mw.roi_shrink_left.setSize((10, 10))
    mw.roi_shrink_right.setPos((35, 5), update=False)
    mw.roi_shrink_right.setSize((10, 10))
    mw.frame_slider.setValue(0)
    mw._on_shrinkage_set_ref_clicked()
    mw.radio_shrinkage_warmer.setChecked(True)
    mw.spin_shrinkage_threshold.setValue(30.0)
    mw._on_shrinkage_compute_clicked()
    assert mw._shrinkage_result is not None
    widths = mw._shrinkage_result["widths_px"]

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

    # Kein Massstab gesetzt und kein ROI/Live-Cursor -- die Schwindungs-
    # Spalte muss trotzdem (als einzige Spalte) exportiert werden koennen
    # (Regressionsschutz fuer die erweiterte "keine Daten"-Pruefung).
    assert mw._px_to_mm is None
    mw._export_csv()

    records = json.loads(out_path.read_text(encoding="utf-8"))
    assert len(records) == mw.recording.n_frames
    col_key = next(k for k in records[0] if k.startswith("Schwindung (Breite)"))
    assert "px" in col_key
    for i, rec in enumerate(records):
        assert rec[col_key] == pytest.approx(round(float(widths[i]), 3))


def test_segment_area_px_counts_pixels_over_threshold():
    from thermal_viewer.main_window.shrinkage_ops import _segment_area_px

    frame = np.full((5, 5), 10.0)
    frame[1:4, 1:4] = 50.0  # 3x3 = 9 heisse Pixel
    assert _segment_area_px(frame, 0, 5, 0, 5, threshold=30.0, warmer=True) == 9
    # Umgekehrte Richtung ("kaelter als Hintergrund"): der Rest (16 Pixel).
    assert _segment_area_px(frame, 0, 5, 0, 5, threshold=30.0, warmer=False) == 16
    # Leere Box.
    assert _segment_area_px(frame, 0, 5, 5, 5, threshold=30.0, warmer=True) == 0


def test_shrinkage_mode_switch_clears_result_and_toggles_box_visibility(loaded_main_window):
    mw = _make_shrinking_recording_window(loaded_main_window)
    mw.chk_shrinkage_enabled.setChecked(True)
    assert mw.radio_shrinkage_mode_width.isChecked()
    assert mw.roi_shrink_left.isVisible()
    assert not mw.roi_shrink_area.isVisible()

    mw.roi_shrink_left.setPos((5, 5), update=False)
    mw.roi_shrink_left.setSize((10, 10))
    mw.roi_shrink_right.setPos((35, 5), update=False)
    mw.roi_shrink_right.setSize((10, 10))
    mw.frame_slider.setValue(0)
    mw._on_shrinkage_set_ref_clicked()
    mw._on_shrinkage_compute_clicked()
    assert mw._shrinkage_result is not None

    # Umschalten auf "Fläche" verwirft das Breiten-Ergebnis (gehoert zur
    # jeweils ANDEREN Messart) und tauscht die sichtbaren Boxen.
    mw.radio_shrinkage_mode_area.setChecked(True)
    assert mw._shrinkage_result is None
    assert mw.lbl_shrinkage_result.text() == "Noch nicht berechnet."
    assert mw.roi_shrink_area.isVisible()
    assert not mw.roi_shrink_left.isVisible()
    assert not mw.roi_shrink_right.isVisible()
    assert not mw.roi_shrink_width.isVisible()
    x, y = mw.shrinkage_curve.getData()
    assert x is None and y is None

    mw.radio_shrinkage_mode_width.setChecked(True)
    assert mw._shrinkage_result is None
    assert mw.roi_shrink_left.isVisible()
    assert not mw.roi_shrink_area.isVisible()


def test_shrinkage_area_mode_end_to_end_tracks_shrinking_area(loaded_main_window):
    # Nutzerwunsch: Schwindung zusaetzlich zur Breite auch ueber die Flaeche
    # bestimmbar machen -- geometrieunabhaengig, da einfach die Pixelzahl
    # ueber dem Schwellenwert in EINEM (nicht nachgefuehrten) Bereich
    # gezaehlt wird, statt zwei Flanken zu verfolgen.
    mw = _make_shrinking_recording_window(loaded_main_window)
    mw.chk_shrinkage_enabled.setChecked(True)
    mw.radio_shrinkage_mode_area.setChecked(True)
    assert mw.roi_shrink_area.isVisible()

    mw.radio_shrinkage_warmer.setChecked(True)
    mw.spin_shrinkage_threshold.setValue(30.0)
    # Kein Startbild noetig -- jedes Bild wird unabhaengig ausgewertet.
    mw._on_shrinkage_compute_clicked()

    assert mw._shrinkage_result is not None
    assert mw._shrinkage_result["mode"] == "area"
    areas = mw._shrinkage_result["areas_px"]
    # Das synthetische Rechteck ist 10 Zeilen hoch und pro Bild 2px (1px je
    # Seite) schmaler -- Flaeche = 10 * (30 - 2*i).
    assert list(areas) == [300.0, 280.0, 260.0, 240.0, 220.0]

    x, y = mw.shrinkage_curve.getData()
    assert len(x) == 5


def test_csv_export_includes_shrinkage_area_column(loaded_main_window, tmp_path, monkeypatch):
    mw = _make_shrinking_recording_window(loaded_main_window)
    mw.chk_shrinkage_enabled.setChecked(True)
    mw.radio_shrinkage_mode_area.setChecked(True)
    mw.radio_shrinkage_warmer.setChecked(True)
    mw.spin_shrinkage_threshold.setValue(30.0)
    mw._on_shrinkage_compute_clicked()
    assert mw._shrinkage_result is not None
    areas = mw._shrinkage_result["areas_px"]

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

    assert mw._px_to_mm is None
    mw._export_csv()

    records = json.loads(out_path.read_text(encoding="utf-8"))
    col_key = next(k for k in records[0] if k.startswith("Schwindung (Fläche)"))
    assert "px²" in col_key
    for i, rec in enumerate(records):
        assert rec[col_key] == pytest.approx(round(float(areas[i]), 3))


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
    mw._cleaning_logic = "or"
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
    assert saved["bereinigung_logik"] == "or"
    assert saved["bereinigung_ausgeblendete_frames"] == [1, 3]

    mw._cleaning_points = []
    mw._cleaning_kernel_size = 3
    mw._cleaning_logic = "and"
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
    assert mw._cleaning_logic == "or"
    assert mw._cleaning_threshold == 7.5
    assert mw._excluded_frame_indices == {1, 3}
    # Regressionscheck: Bugfix -- ein aus der Projektdatei als float (statt
    # int) wiederhergestellter Referenzpunkt liess _compute_cleaning_
    # candidates() mit einem IndexError abstuerzen (numpy erlaubt keine
    # Float-Indizierung von frames[:, r, c]). Muss nach dem Laden anstands-
    # los durchlaufen, egal was die Kandidatenliste konkret enthaelt.
    mw._compute_cleaning_candidates()


def test_save_and_load_project_roundtrips_shrinkage_width_state(loaded_main_window, tmp_path, monkeypatch):
    # Regressionsschutz: die Schwindungsmessung (shrinkage_ops.py) fehlte
    # bisher komplett im Projekt-Speichern/Laden -- Widerspruch zum
    # dokumentierten Nutzerwunsch "vollstaendiger Programmzustand" (siehe
    # _save_project). Boxen/Einstellungen muessen ueber einen Speichern-
    # /Laden-Zyklus erhalten bleiben, das Ergebnis wird aus ihnen neu
    # berechnet (kein eigener Rohwerte-Export).
    mw = loaded_main_window
    mw.roi_shrink_left.setPos((1, 1), update=False)
    mw.roi_shrink_left.setSize((3, 3))
    mw.roi_shrink_right.setPos((10, 1), update=False)
    mw.roi_shrink_right.setSize((3, 3))
    mw.spin_shrinkage_threshold.setValue(12.5)
    mw.radio_shrinkage_colder.setChecked(True)
    mw.radio_shrinkage_round.setChecked(True)
    mw.frame_slider.setValue(0)
    mw._on_shrinkage_set_ref_clicked()
    mw.chk_shrinkage_enabled.setChecked(True)
    assert mw._shrinkage_ref_frame == 0

    proj_path = tmp_path / "shrinkage.tvproj"
    monkeypatch.setattr(
        QtWidgets.QFileDialog, "getSaveFileName",
        staticmethod(lambda *a, **k: (str(proj_path), "")),
    )
    assert mw._save_project() is True

    saved = json.loads(proj_path.read_text(encoding="utf-8"))
    assert saved["schwindung"]["aktiviert"] is True
    assert saved["schwindung"]["messart"] == "width"
    assert saved["schwindung"]["schwellenwert"] == 12.5
    assert saved["schwindung"]["waermer"] is False
    assert saved["schwindung"]["geometrie"] == "round"
    assert saved["schwindung"]["startbild"] == 0
    assert saved["schwindung"]["box_links"]["x"] == 1.0

    # Zustand vor dem Laden komplett anders, damit der Roundtrip echt
    # etwas wiederherstellen muss statt zufaellig schon zu passen.
    mw.chk_shrinkage_enabled.setChecked(False)
    mw.roi_shrink_left.setPos((50, 50), update=False)
    mw.roi_shrink_left.setSize((5, 5))
    mw.spin_shrinkage_threshold.setValue(1.0)
    mw.radio_shrinkage_warmer.setChecked(True)
    mw.radio_shrinkage_rect.setChecked(True)
    mw._shrinkage_ref_frame = None

    monkeypatch.setattr(
        QtWidgets.QFileDialog, "getOpenFileName",
        staticmethod(lambda *a, **k: (str(proj_path), "")),
    )
    mw._load_project()

    assert mw.chk_shrinkage_enabled.isChecked() is True
    assert mw._shrinkage_enabled is True
    assert mw.radio_shrinkage_mode_width.isChecked() is True
    assert mw.spin_shrinkage_threshold.value() == 12.5
    assert mw.radio_shrinkage_colder.isChecked() is True
    assert mw.radio_shrinkage_round.isChecked() is True
    assert mw._shrinkage_ref_frame == 0
    assert tuple(mw.roi_shrink_left.pos()) == (1.0, 1.0)
    assert tuple(mw.roi_shrink_left.size()) == (3.0, 3.0)
    # Ergebnis wurde aus den wiederhergestellten Eingaben neu berechnet,
    # nicht leer gelassen.
    assert mw._shrinkage_result is not None
    assert mw._shrinkage_result["mode"] == "width"


def test_save_and_load_project_roundtrips_shrinkage_area_state(loaded_main_window, tmp_path, monkeypatch):
    # Gegenstueck zum Breiten-Test oben: die Flaechen-Messart braucht kein
    # Startbild und muss nach dem Laden trotzdem sofort ein Ergebnis haben
    # (siehe _load_project: "else: self._compute_shrinkage_area()").
    mw = loaded_main_window
    mw.roi_shrink_area.setPos((2, 2), update=False)
    mw.roi_shrink_area.setSize((8, 8))
    mw.radio_shrinkage_mode_area.setChecked(True)
    mw.spin_shrinkage_threshold.setValue(20.0)
    mw.chk_shrinkage_enabled.setChecked(True)

    proj_path = tmp_path / "shrinkage_area.tvproj"
    monkeypatch.setattr(
        QtWidgets.QFileDialog, "getSaveFileName",
        staticmethod(lambda *a, **k: (str(proj_path), "")),
    )
    assert mw._save_project() is True
    saved = json.loads(proj_path.read_text(encoding="utf-8"))
    assert saved["schwindung"]["messart"] == "area"
    assert saved["schwindung"]["box_flaeche"]["breite_px"] == 8.0

    mw.radio_shrinkage_mode_width.setChecked(True)
    mw.chk_shrinkage_enabled.setChecked(False)

    monkeypatch.setattr(
        QtWidgets.QFileDialog, "getOpenFileName",
        staticmethod(lambda *a, **k: (str(proj_path), "")),
    )
    mw._load_project()

    assert mw.radio_shrinkage_mode_area.isChecked() is True
    assert mw._shrinkage_mode == "area"
    assert tuple(mw.roi_shrink_area.size()) == (8.0, 8.0)
    assert mw._shrinkage_result is not None
    assert mw._shrinkage_result["mode"] == "area"


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


def test_apply_cleaning_exclusions_refuses_to_hide_every_frame(loaded_main_window, monkeypatch):
    # Regressionsschutz: wuerde eine Auswahl ALLE Bilder der Aufnahme
    # ausblenden, faende der Navigations-Ausweich-Sprung in
    # _apply_cleaning_exclusions kein sichtbares Bild mehr und wuerde die
    # zentrale Zusicherung verletzen, dass ein ausgeblendetes Bild NIE mehr
    # angezeigt wird -- muss daher unveraendert abgelehnt werden.
    mw = loaded_main_window
    n = mw.recording.n_frames
    assert n > 1
    shown = []
    monkeypatch.setattr(mw, "_show_frame", lambda idx, *a, **k: shown.append(idx))
    monkeypatch.setattr(
        QtWidgets.QMessageBox, "information",
        staticmethod(lambda *a, **k: QtWidgets.QMessageBox.StandardButton.Ok),
    )

    mw._apply_cleaning_exclusions(set(range(n)))

    assert mw._excluded_frame_indices == set()  # unveraendert, nichts uebernommen
    assert shown == []  # gar nicht erst versucht, ein (ausgeblendetes) Bild zu zeigen


def test_load_project_caps_exclusion_set_that_would_hide_every_frame(loaded_main_window, tmp_path, monkeypatch):
    # Regressionsschutz fuer denselben Bug wie oben, diesmal ueber eine
    # (z.B. von Hand bearbeitete) Projektdatei, die ALLE Bilder als
    # ausgeblendet listet.
    mw = loaded_main_window
    n = mw.recording.n_frames
    proj_path = tmp_path / "all_excluded.tvproj"
    proj_path.write_text(
        json.dumps({
            "format_version": 2,
            "quellordner": str(mw.recording.paths[0].parent),
            "bild_groesse_px": {"zeilen": mw.recording.shape[0], "spalten": mw.recording.shape[1]},
            "bereinigung_punkte": [],
            "bereinigung_schwellenwert": 5.0,
            "bereinigung_ausgeblendete_frames": list(range(n)),
        }),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        QtWidgets.QFileDialog, "getOpenFileName",
        staticmethod(lambda *a, **k: (str(proj_path), "")),
    )
    mw._load_project()

    assert len(mw._excluded_frame_indices) == n - 1
    assert mw._excluded_frame_indices != set(range(n))


# ------------------------------------------------------------ Ebenen-Tabs


def test_layer_tab_is_active_helper(loaded_main_window):
    mw = loaded_main_window
    assert mw._active_layer_tab == "all"
    for category in ("roi", "cleaning", "shrinkage", "scale"):
        assert mw._is_layer_tab_active(category)

    mw._set_active_layer_tab("roi")
    assert mw._is_layer_tab_active("roi")
    assert not mw._is_layer_tab_active("cleaning")
    assert not mw._is_layer_tab_active("shrinkage")
    assert not mw._is_layer_tab_active("scale")


def test_layer_tab_gates_roi_image_items(roi_and_live_window):
    mw = roi_and_live_window
    entry = mw.roi_entries[-1]  # roi_and_live_window platziert genau dieses (siehe conftest.py)
    assert entry.placed
    assert entry.roi.isVisible()
    assert entry.label.isVisible()

    mw._set_active_layer_tab("shrinkage")
    assert not entry.roi.isVisible()
    assert not entry.label.isVisible()

    mw._set_active_layer_tab("roi")
    assert entry.roi.isVisible()
    assert entry.label.isVisible()

    mw._set_active_layer_tab("all")
    assert entry.roi.isVisible()
    assert entry.label.isVisible()


def test_layer_tab_never_reveals_unplaced_or_user_hidden_roi(loaded_main_window):
    # Regressionsschutz: RoiEntry startet UNPLATZIERT versteckt
    # (RoiEntry.__init__: self.roi.setVisible(False)) und hat eine eigene
    # "sichtbar"-Checkbox in der Liste (entry.is_visible_checked(), siehe
    # _on_roi_list_item_changed) -- der Ebenen-Tab darf diese beiden
    # bestehenden Bedingungen nur EINSCHRAENKEN, niemals uebersteuern.
    mw = loaded_main_window
    unplaced = mw.roi_entries[0]
    assert not unplaced.placed
    assert not unplaced.roi.isVisible()

    # Auf "Alle" (Default-Tab) darf ein unplatziertes ROI trotzdem NICHT
    # sichtbar werden.
    mw._set_active_layer_tab("roi")
    assert not unplaced.roi.isVisible()
    mw._set_active_layer_tab("all")
    assert not unplaced.roi.isVisible()

    # Ein platziertes, aber ueber die eigene Checkbox ausgeblendetes ROI
    # bleibt ebenfalls versteckt, unabhaengig vom Tab.
    hidden_entry = mw.roi_entries[1]
    hidden_entry.place(10, 10, 5, 5)
    assert hidden_entry.roi.isVisible()
    hidden_entry.list_item.setCheckState(QtCore.Qt.CheckState.Unchecked)
    mw._on_roi_list_item_changed(hidden_entry.list_item)
    assert not hidden_entry.roi.isVisible()

    mw._set_active_layer_tab("roi")
    assert not hidden_entry.roi.isVisible()
    mw._set_active_layer_tab("all")
    assert not hidden_entry.roi.isVisible()


def test_layer_tab_gates_cleaning_point_image_items(loaded_main_window):
    mw = loaded_main_window
    mw._cleaning_points = [(3, 3)]
    mw._draw_cleaning_point_markers()
    item = mw._cleaning_point_items[0]
    assert item["dot"].isVisible()
    assert item["label"].isVisible()

    mw._set_active_layer_tab("roi")
    assert not item["dot"].isVisible()
    assert not item["label"].isVisible()

    mw._set_active_layer_tab("cleaning")
    assert item["dot"].isVisible()
    assert item["label"].isVisible()


def test_layer_tab_gates_panel_sections(loaded_main_window):
    # scale_box/_shrinkage_groupbox/roi_split sind echte QWidgets -- wie bei
    # _activity_progress gilt hier .isHidden() statt .isVisible(), da das
    # (nie per .show() gezeigte) MainWindow in Tests jedes Kind-Widget als
    # nicht sichtbar meldet, unabhaengig vom eigenen setVisible()-Aufruf.
    mw = loaded_main_window
    assert not mw.scale_box.isHidden()
    assert not mw._shrinkage_groupbox.isHidden()
    assert not mw.roi_split.isHidden()

    mw._set_active_layer_tab("roi")
    assert mw.scale_box.isHidden()
    assert mw._shrinkage_groupbox.isHidden()
    assert not mw.roi_split.isHidden()

    mw._set_active_layer_tab("scale")
    assert not mw.scale_box.isHidden()
    assert mw._shrinkage_groupbox.isHidden()
    assert mw.roi_split.isHidden()

    mw._set_active_layer_tab("all")
    assert not mw.scale_box.isHidden()
    assert not mw._shrinkage_groupbox.isHidden()
    assert not mw.roi_split.isHidden()


def test_layer_tab_combines_with_shrinkage_enabled_flag(loaded_main_window):
    # Die Schwindungs-Boxen haben eine EIGENE Sichtbarkeits-Bedingung
    # (Aktivieren-Checkbox) -- UND-verknuepft mit dem aktiven Tab, nicht vom
    # Tab allein bestimmt.
    mw = loaded_main_window
    mw._set_active_layer_tab("shrinkage")
    assert not mw.roi_shrink_left.isVisible()  # Checkbox noch aus

    mw.chk_shrinkage_enabled.setChecked(True)
    assert mw.roi_shrink_left.isVisible()

    mw._set_active_layer_tab("roi")
    assert not mw.roi_shrink_left.isVisible()  # jetzt vom Tab weg-gegated


def test_on_add_roi_clicked_switches_to_roi_tab(loaded_main_window):
    mw = loaded_main_window
    mw._set_active_layer_tab("scale")
    mw._on_add_roi_clicked()
    assert mw._active_layer_tab == "roi"


def test_shrinkage_enable_switches_tab_but_disable_does_not(loaded_main_window):
    mw = loaded_main_window
    mw._set_active_layer_tab("roi")
    mw.chk_shrinkage_enabled.setChecked(True)
    assert mw._active_layer_tab == "shrinkage"

    mw.chk_shrinkage_enabled.setChecked(False)
    assert mw._active_layer_tab == "shrinkage"  # Deaktivieren schaltet NICHT automatisch weg


def test_ruler_and_measurement_tools_switch_to_scale_tab(loaded_main_window):
    mw = loaded_main_window
    mw._set_active_layer_tab("roi")
    mw._start_ruler_tool()
    assert mw._active_layer_tab == "scale"

    mw._cancel_ruler_tool()
    mw._px_to_mm = 0.5  # _start_measurement_tool verlangt einen gesetzten Maßstab
    mw._set_active_layer_tab("roi")
    mw._start_measurement_tool()
    assert mw._active_layer_tab == "scale"


def test_open_data_cleaning_dialog_switches_tab_and_shows_dialog(loaded_main_window):
    mw = loaded_main_window
    mw._set_active_layer_tab("roi")
    mw._open_data_cleaning_dialog()
    assert mw._active_layer_tab == "cleaning"
    assert mw._cleaning_dialog is not None
    assert mw._cleaning_dialog.isVisible()

    mw._set_active_layer_tab("roi")
    assert not mw._cleaning_dialog.isVisible()

    mw._set_active_layer_tab("all")
    assert mw._cleaning_dialog.isVisible()


def test_cleaning_dialog_manual_close_reverts_tab_to_all(loaded_main_window):
    mw = loaded_main_window
    mw._open_data_cleaning_dialog()
    assert mw._active_layer_tab == "cleaning"

    mw._cleaning_dialog.close()
    assert mw._active_layer_tab == "all"


def test_layer_tab_bar_click_switches_tabs_and_opens_cleaning_dialog(loaded_main_window):
    from thermal_viewer.main_window.layer_tabs_ops import _LAYER_TAB_ORDER

    mw = loaded_main_window
    mw.layer_tab_bar.setCurrentIndex(_LAYER_TAB_ORDER.index("roi"))
    assert mw._active_layer_tab == "roi"

    mw.layer_tab_bar.setCurrentIndex(_LAYER_TAB_ORDER.index("cleaning"))
    assert mw._active_layer_tab == "cleaning"
    assert mw._cleaning_dialog is not None
    assert mw._cleaning_dialog.isVisible()

    mw.layer_tab_bar.setCurrentIndex(_LAYER_TAB_ORDER.index("all"))
    assert mw._active_layer_tab == "all"
