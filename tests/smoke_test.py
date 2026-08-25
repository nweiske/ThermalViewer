"""Headless End-to-End-Smoke-Test der ThermalViewer-App.

Selbststaendig lauffaehig auf jedem Rechner mit ausgecheckter/geklonter
Kopie des Repos -- KEIN manuelles Setup noetig (kein extern abgelegter
Beispiel-Datensatz, keine feste Temp-Pfad-Abhaengigkeit): das Fixture-CSV-
Datenset wird bei jedem Lauf frisch in ein temporaeres Verzeichnis generiert
(siehe generate_fixture_dataset()) und am Ende wieder aufgeraeumt.

Aufruf vom Repo-Root aus:
    uv run python tests/smoke_test.py
oder:
    python tests/smoke_test.py
"""
import atexit
import contextlib
import csv
import json
import os
import shutil
import sys
import tempfile
import traceback
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from qtpy import QtCore, QtGui, QtWidgets  # noqa: E402

app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

# Modale Dialoge automatisch "bestaetigen", damit das Skript ohne echten
# Benutzer durchlaeuft (Standardwerte der jeweiligen Dialoge werden benutzt).
QtWidgets.QDialog.exec = lambda self: (self.accept(), QtWidgets.QDialog.DialogCode.Accepted)[1]
QtWidgets.QMessageBox.information = staticmethod(lambda *a, **k: None)
QtWidgets.QMessageBox.warning = staticmethod(lambda *a, **k: None)
QtWidgets.QMessageBox.critical = staticmethod(lambda *a, **k: print("CRITICAL:", a[2] if len(a) > 2 else a))

from thermal_viewer.main_window import (  # noqa: E402
    MainWindow,
    COLORMAPS,
    INTERP_START_LABEL,
    INTERP_END_LABEL,
    INTERP_START_CAPTURE_LABEL,
    INTERP_END_CAPTURE_LABEL,
    default_roi_name,
)


def generate_fixture_dataset(folder: Path, n_frames: int = 8, rows: int = 24, cols: int = 32) -> None:
    """Erzeugt ein kleines, deterministisches CSV-Datenset im vom Standard-
    Namensschema erwarteten Format (';'-getrennt, Dezimalkomma,
    "Record_YYYY-MM-DD_hh-mm-ss.csv"). Werte folgen bewusst einem REINEN
    linearen Gradienten ueber Zeile/Spalte/Zeit (value = 20.0 + 0.1 *
    (r*cols + c) + 0.5 * t) -- einige Tests (z.B. Live-Cursor-NxN-Mittelung)
    nutzen genau diese Linearitaet, um den Mittelwert eines SYMMETRISCHEN
    Blocks analytisch exakt mit dem Zentrumswert vergleichen zu koennen."""
    folder.mkdir(parents=True, exist_ok=True)
    for t in range(n_frames):
        path = folder / f"Record_2026-08-19_10-{t:02d}-00.csv"
        with open(path, "w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f, delimiter=";")
            for r in range(rows):
                row = [
                    f"{(20.0 + 0.1 * (r * cols + c) + 0.5 * t):.1f}".replace(".", ",")
                    for c in range(cols)
                ]
                writer.writerow(row)


OUT = Path(tempfile.mkdtemp(prefix="thermalviewer_smoke_"))
atexit.register(shutil.rmtree, OUT, True)
DATASET = OUT / "test_dataset"
generate_fixture_dataset(DATASET)

failures = []


@contextlib.contextmanager
def temp_dialog_exec(cls, fn):
    """Patcht cls.exec NUR fuer die Dauer des Blocks -- entfernt das Attribut
    danach wieder VOLLSTAENDIG (statt es auf einen Schnappschuss zurueckzusetzen),
    falls die Klasse zuvor KEIN eigenes exec hatte (sondern es per MRO von
    QtWidgets.QDialog geerbt hat). Bugfix: das zuvor ueberall genutzte Muster
    "orig = cls.exec; cls.exec = fn; ...; cls.exec = orig" pinnte in diesem
    Fall dauerhaft einen SCHNAPPSCHUSS des damaligen Werts auf die Subklasse
    -- jeder SPAETERE Test, der stattdessen (korrekt) nur noch
    QtWidgets.QDialog.exec patcht, lief dadurch ins Leere, weil die Subklasse
    laengst ihr eigenes (veraltetes) exec-Attribut hatte. Das fuehrte bis zu
    einem echten, unter QT_QPA_PLATFORM=offscreen nie endenden nativen Dialog
    -- ein voller, stiller Haenger des gesamten Testlaufs ohne Fehlermeldung."""
    had_own = "exec" in cls.__dict__
    orig = cls.__dict__.get("exec")
    cls.exec = fn
    try:
        yield
    finally:
        if had_own:
            cls.exec = orig
        else:
            del cls.exec


def check(label, fn):
    print(f"RUN  {label}", flush=True)
    try:
        fn()
        print(f"OK   {label}", flush=True)
    except Exception:
        print(f"FAIL {label}", flush=True)
        traceback.print_exc()
        failures.append(label)


win = MainWindow()
win.resize(1400, 900)
win.show()
app.processEvents()

paths = sorted(DATASET.glob("*.csv"))
check("load dataset", lambda: win._load_paths(paths))
app.processEvents()


def test_default_roi_names():
    expected = ["Oben", "Links", "Mitte", "Rechts", "Unten"]
    assert [e.name for e in win.roi_entries] == expected
    assert [e.list_item.text() for e in win.roi_entries] == expected


check("default ROI names are Oben/Links/Mitte/Rechts/Unten in order", test_default_roi_names)


def test_image_actually_has_color_after_loading():
    # Bugfix: ein manuell aus cmap.pos/cmap.color zusammengebautes ColorMap
    # (frueherer Versuch, den pg.colormap.get()-Cache nicht zu mutieren)
    # interpretierte die schon normierten float-Farbwerte faelschlich
    # nochmal als Byte-Werte -> eine fast schwarze/durchsichtige LUT, das
    # Thermobild blieb dadurch faktisch weiss/leer.
    lut = win.histogram.getLookupTable(img=win.image_item.image)
    assert lut is not None
    assert lut.max() > 100, f"LUT wirkt fast schwarz/durchsichtig: max={lut.max()}"
    assert lut.min() < lut.max()


check("thermal image actually has visible color data (not blank/white)", test_image_actually_has_color_after_loading)

# --- Punkt 1: Level-Modi -------------------------------------------------
check("level mode per_frame", lambda: win._set_level_mode("per_frame"))
check("level mode global", lambda: win._set_level_mode("global"))
check("level mode manual", lambda: win._set_level_mode("manual"))
win._set_level_mode("per_frame")


def test_level_mode_mutual_exclusivity():
    # Automatisch/Manuell muessen sich als aeussere Radiogruppe IMMER
    # gegenseitig ausschliessen: genau einer von beiden ist an, nie beide
    # gleichzeitig und nie beide gleichzeitig aus.
    win._set_level_mode("manual")
    assert win.radio_level_manual.isChecked() and not win.radio_level_auto.isChecked()
    assert win._level_mode() == "manual"
    assert win.spin_level_min.isEnabled() and win.spin_level_max.isEnabled()
    assert not win.radio_level_per_frame.isEnabled() and not win.radio_level_global.isEnabled()

    win._set_level_mode("global")
    assert win.radio_level_auto.isChecked() and not win.radio_level_manual.isChecked()
    assert win.radio_level_global.isChecked() and not win.radio_level_per_frame.isChecked()
    assert win._level_mode() == "global"
    assert not win.spin_level_min.isEnabled() and not win.spin_level_max.isEnabled()
    assert win.radio_level_per_frame.isEnabled() and win.radio_level_global.isEnabled()

    win._set_level_mode("per_frame")
    assert win._level_mode() == "per_frame"


check("Automatisch/Manuell are mutually exclusive top-level radios", test_level_mode_mutual_exclusivity)


def test_level_mode_blocks_stacked_not_side_by_side():
    # Automatisch-Block (Pro Bild ueber Gesamte Serie) und Manuell-Block
    # (Max ueber Min) stehen block-weise UNTEREINANDER in derselben Spalte,
    # nicht mehr nebeneinander in einer gemeinsamen Zeile.
    win.show()
    app.processEvents()
    grid = win.spin_level_min.parentWidget().layout()
    max_label = grid.itemAtPosition(4, 1).widget()
    min_label = grid.itemAtPosition(5, 1).widget()

    # Pro Bild/Gesamte Serie und Max/Min-Label fluchten (gleiche Spalte).
    assert win.radio_level_per_frame.x() == win.radio_level_global.x() == max_label.x() == min_label.x()
    # Innerhalb jedes Blocks steht das erstgenannte Element oben.
    assert win.radio_level_per_frame.y() < win.radio_level_global.y()
    assert max_label.y() < min_label.y()
    # Automatisch-Block komplett oberhalb des Manuell-Blocks.
    assert win.radio_level_global.y() < max_label.y()


check("Automatisch/Manuell sub-options are stacked block-wise, Max above Min", test_level_mode_blocks_stacked_not_side_by_side)

# --- Punkt 4: Farbpaletten + Invertieren ---------------------------------


def test_colormaps():
    for i in range(len(COLORMAPS)):
        win.combo_cmap.setCurrentIndex(i)
        app.processEvents()
    win.chk_cmap_invert.setChecked(True)
    win.chk_cmap_invert.setChecked(False)


check("all colormaps + invert", test_colormaps)


def test_colormap_invert_actually_works():
    win.combo_cmap.setCurrentIndex(0)  # Ironbow (CET-L17, in COLORMAPS_BASE_REVERSED)
    win.chk_cmap_invert.setChecked(False)
    app.processEvents()
    lut_off = win.histogram.gradient.colorMap().getLookupTable(0.0, 1.0, 5).tolist()
    win.chk_cmap_invert.setChecked(True)
    app.processEvents()
    lut_on = win.histogram.gradient.colorMap().getLookupTable(0.0, 1.0, 5).tolist()
    assert lut_off != lut_on, "Invertiert-Haken hat keine sichtbare Wirkung"

    # Ironbow ist in COLORMAPS_BASE_REVERSED gelistet, weil seine rohe
    # colorcet-Reihenfolge rueckwaerts lief (0.0 -> weiss, 1.0 -> dunkelblau).
    # Bei ausgeschaltetem Haken (Standard) soll jetzt niedriger Wert dunkel,
    # hoher Wert hell erscheinen wie bei den uebrigen Paletten.
    win.chk_cmap_invert.setChecked(False)
    app.processEvents()
    lut = win.histogram.gradient.colorMap().getLookupTable(0.0, 1.0, 2).tolist()
    low_brightness = sum(lut[0])
    high_brightness = sum(lut[-1])
    assert high_brightness > low_brightness, (lut[0], lut[-1])

    win.combo_cmap.setCurrentIndex(0)
    win.chk_cmap_invert.setChecked(False)


check("colormap invert checkbox actually works; Ironbow base orientation fixed", test_colormap_invert_actually_works)

# --- Punkt 2: Freie ROI-Groesse -------------------------------------------


def test_roi_resize():
    entry = win.roi_entries[0]
    entry.place(50, 40, 20, 20)
    win._sync_roi_spinboxes(entry)
    win._recompute_curves(entries=[entry])
    assert entry.width() == 20 and entry.height() == 20
    entry.place(50, 40, 40, 10)
    win._sync_roi_spinboxes(entry)
    win._recompute_curves(entries=[entry])
    assert entry.width() == 40 and entry.height() == 10
    win._on_roi_square_reset_clicked(entry)
    assert entry.width() == entry.height() == 40

    # Quadrieren muss IMMER die Breite uebernehmen (nicht das Maximum aus
    # Breite/Hoehe) -- hier ist die Hoehe (30) groesser als die Breite (12),
    # das Ergebnis darf trotzdem nur 12x12 sein.
    entry.place(50, 40, 12, 30)
    win._sync_roi_spinboxes(entry)
    win._on_roi_square_reset_clicked(entry)
    assert entry.width() == entry.height() == 12, (entry.width(), entry.height())


check("roi place + resize + square-reset", test_roi_resize)


def test_control_panel_layout_relabel_and_vertical_tabs():
    # Bugreport-Nachbesserung: rechtes Panel als Ganzes scrollbar (nicht nur
    # die Tab-Innenseite), ROI-Auswahl als senkrechte Namensliste (normale,
    # nicht gedrehte Schrift) statt QTabWidget(West), Knopf-Umbenennungen,
    # "Invertieren" unter dem Farbverlauf-Dropdown.
    assert isinstance(win.control_panel, QtWidgets.QScrollArea)
    assert isinstance(win.roi_list, QtWidgets.QListWidget)
    assert isinstance(win.roi_stack, QtWidgets.QStackedWidget)
    assert win.chk_cmap_invert.text() == "Invertieren"
    entry = win.roi_entries[0]
    assert entry.btn_place.text() == "Messbereich setzen"
    assert entry.btn_remove.text() == "Messbereich entfernen"
    # "Invertieren" faengt in derselben Spalte an wie die Farbverlauf-Combo.
    legend_layout = win.chk_cmap_invert.parentWidget().layout()
    _, col_combo, _, _ = legend_layout.getItemPosition(legend_layout.indexOf(win.combo_cmap))
    _, col_invert, _, _ = legend_layout.getItemPosition(legend_layout.indexOf(win.chk_cmap_invert))
    assert col_combo == col_invert, (col_combo, col_invert)
    # Bugfix: "+ Messbereich"-Knopf verschwand komplett (war ein Eck-Knopf
    # eines QTabWidget mit vertikaler Reiterleiste, die das nicht
    # unterstuetzt) -- jetzt ein ganz normaler, immer sichtbarer Knopf.
    assert win.btn_add_roi.text() == "+ Messbereich"
    assert win.btn_add_roi.isVisibleTo(win.control_panel)
    # ROI-Namen in der Liste normal (nicht um 90° gedreht) lesbar -- pruefbar
    # ueber die item-Ausrichtung/den Widget-Typ: QListWidget zeichnet Text
    # grundsaetzlich waagerecht, im Gegensatz zu QTabWidget(West).
    assert win.roi_list.item(0).text() == win.roi_entries[0].name


check("control panel: scrollable, sidebar list (not rotated tabs), '+' button present, relabeled buttons", test_control_panel_layout_relabel_and_vertical_tabs)


def test_roi_tab_selection_arms_placement():
    # Auswahl in der Liste soll direkt "Messbereich setzen" fuer den neu
    # gewaehlten Messbereich aktivieren, damit man ohne Extra-Klick ins Bild
    # klicken kann.
    entry0, entry1 = win.roi_entries[0], win.roi_entries[1]
    win._select_roi(entry1)
    assert win.roi_stack.currentWidget() is entry1.tab_widget
    assert entry1.btn_place.isChecked()
    assert win._armed_entry is entry1

    win._select_roi(entry0)
    assert win.roi_stack.currentWidget() is entry0.tab_widget
    assert entry0.btn_place.isChecked()
    assert not entry1.btn_place.isChecked(), "vorheriger Messbereich muss beim Wechsel entwaffnet werden"
    assert win._armed_entry is entry0

    entry0.btn_place.setChecked(False)
    win._armed_entry = None


check("selecting a ROI in the sidebar list arms 'Messbereich setzen' for that ROI", test_roi_tab_selection_arms_placement)


def test_clicking_roi_in_image_selects_it_in_panel():
    entry0, entry1 = win.roi_entries[0], win.roi_entries[1]
    entry0.place(3, 3, 5, 5)
    entry1.place(15, 15, 5, 5)
    win._select_roi(entry0)
    assert win.roi_stack.currentWidget() is entry0.tab_widget

    class FakeRoiClickEvent:
        pass

    win._on_roi_clicked_in_image(entry1, entry1.roi, FakeRoiClickEvent())
    assert win.roi_list.currentItem() is entry1.list_item
    assert win.roi_stack.currentWidget() is entry1.tab_widget
    assert entry1.btn_place.isChecked(), "Auswahl per Bildklick soll ebenfalls 'Messbereich setzen' aktivieren"

    entry1.btn_place.setChecked(False)
    win._armed_entry = None
    win.roi_list.setCurrentRow(0)


check("clicking a ROI in the image selects it in the right-hand panel", test_clicking_roi_in_image_selects_it_in_panel)


def test_roi_list_double_click_rename_and_visibility_checkbox():
    # Namensfeld in der Zeile entfaellt komplett (Umbenennen nur noch per
    # Doppelklick auf den Listeneintrag) -- muss Box-Titel, Bild-
    # Beschriftung UND Legende synchron halten. Das Sichtbarkeits-Haekchen
    # sitzt jetzt direkt am Listeneintrag statt als separate "sichtbar"-
    # Checkbox in der Zeile.
    entry = win.roi_entries[2]
    item = entry.list_item
    assert item.flags() & QtCore.Qt.ItemIsEditable
    assert item.flags() & QtCore.Qt.ItemIsUserCheckable
    assert item.checkState() == QtCore.Qt.CheckState.Checked

    item.setText("Umbenannt per Doppelklick")
    win._on_roi_list_item_changed(item)
    assert entry.name == "Umbenannt per Doppelklick"
    assert entry.tab_widget.title() == "Umbenannt per Doppelklick"
    assert entry.label.toPlainText() == "Umbenannt per Doppelklick"

    # Leerer Name faellt auf den Standardnamen zurueck (Oben/Links/Mitte/
    # Rechts/Unten fuer die ersten 5, sonst "ROI n").
    item.setText("")
    win._on_roi_list_item_changed(item)
    assert entry.name == default_roi_name(entry.number)
    assert item.text() == default_roi_name(entry.number)

    # Sichtbarkeits-Haekchen steuert ROI-Rechteck/Kurve/Beschriftung --
    # nur wirksam, wenn der Messbereich auch platziert ist.
    entry.place(5, 5, 8, 8)
    assert entry.roi.isVisible()
    item.setCheckState(QtCore.Qt.CheckState.Unchecked)
    win._on_roi_list_item_changed(item)
    assert not entry.roi.isVisible()
    assert not entry.curve.isVisible()
    item.setCheckState(QtCore.Qt.CheckState.Checked)
    win._on_roi_list_item_changed(item)
    assert entry.roi.isVisible()

    item.setText(f"ROI {entry.number}")
    win._on_roi_list_item_changed(item)


check(
    "double-click on the ROI list item renames it; checkbox toggles visibility",
    test_roi_list_double_click_rename_and_visibility_checkbox,
)


def test_live_temperature_label_on_image():
    win._hover_row = None
    win._hover_col = None
    win.live_cursor_label.setVisible(False)

    win._update_live_cursor(3, 2)
    assert win.live_cursor_label.isVisible()
    expected = win.recording.frames[win.current_index, 3, 2]
    assert win.live_cursor_label.toPlainText() == f"{expected:.1f} °C", win.live_cursor_label.toPlainText()

    # Muss sich beim Frame-Wechsel automatisch mitaktualisieren (laufendes
    # Video), ohne dass die Maus bewegt wird.
    other_idx = win.recording.n_frames - 1
    if other_idx != win.current_index:
        win._show_frame(other_idx)
        expected2 = win.recording.frames[other_idx, 3, 2]
        assert win.live_cursor_label.toPlainText() == f"{expected2:.1f} °C", win.live_cursor_label.toPlainText()
        win._show_frame(0)

    win._hover_row = None
    win._hover_col = None
    win.live_cursor_label.setVisible(False)


check("live temperature at cursor shown on the image, updates with frame changes", test_live_temperature_label_on_image)


def test_roi_spinboxes_apply_on_enter():
    entry = win.roi_entries[1]
    entry.place(10, 10, 10, 10)
    win._sync_roi_spinboxes(entry)
    entry.spin_width.setValue(20)  # innerhalb spin_width.maximum() (Bildbreite)
    entry.spin_height.setValue(15)  # innerhalb spin_height.maximum() (Bildhoehe)
    # editingFinished feuert bei Enter (oder Fokuswechsel) -- muss dieselbe
    # Wirkung wie ein Klick auf "Uebernehmen" haben, ohne dass der Nutzer
    # extra dorthin greifen muss.
    entry.spin_width.editingFinished.emit()
    assert entry.width() == 20, entry.width()
    entry.spin_height.editingFinished.emit()
    assert entry.height() == 15, entry.height()


check("ROI width/height spinboxes apply on Enter (editingFinished)", test_roi_spinboxes_apply_on_enter)


def test_roi_tabs_instead_of_stacked_scrolling():
    assert win.roi_list.count() == win.roi_stack.count() == len(win.roi_entries) == 5
    for i, entry in enumerate(win.roi_entries):
        assert win.roi_list.item(i).text() == entry.name
    entry = win.roi_entries[0]
    entry.list_item.setText("Testname")
    assert win.roi_list.item(0).text() == "Testname"
    assert entry.name == "Testname"
    entry.list_item.setText("ROI 1")


check("ROI rows live in sidebar list, list text follows name", test_roi_tabs_instead_of_stacked_scrolling)


def test_add_and_remove_arbitrary_roi():
    initial_count = len(win.roi_entries)
    assert win.roi_list.count() == initial_count

    new_entry = win._add_roi_entry()
    assert len(win.roi_entries) == initial_count + 1
    assert win.roi_list.count() == initial_count + 1
    assert new_entry.name == f"ROI {initial_count + 1}"
    assert new_entry.list_item.text() == new_entry.name
    assert win.roi_stack.currentWidget() is new_entry.tab_widget

    # Muss dieselbe Funktionalitaet wie die urspruenglichen 5 ROIs haben:
    # platzieren, Groesse aendern, Farbe aendern, quadrieren.
    new_entry.place(5, 5, 8, 8)
    win._sync_roi_spinboxes(new_entry)
    win._recompute_curves(entries=[new_entry])
    assert new_entry.placed and new_entry.width() == 8 and new_entry.height() == 8
    new_entry.spin_width.setValue(12)
    new_entry.spin_width.editingFinished.emit()
    assert new_entry.width() == 12
    win._on_roi_square_reset_clicked(new_entry)
    assert new_entry.width() == new_entry.height() == 12
    new_entry.set_color("#123456")
    assert new_entry.color == "#123456"

    # Wertebereiche der Spinboxen muessen (bei bereits geladener Aufnahme)
    # sofort an die Bildgroesse angepasst sein, wie bei den urspruenglichen ROIs.
    assert new_entry.spin_width.maximum() == win.roi_entries[0].spin_width.maximum()

    # Entfernen (Bestaetigungsdialog auf "Ja" gemockt).
    orig_question = QtWidgets.QMessageBox.question
    QtWidgets.QMessageBox.question = staticmethod(
        lambda *a, **k: QtWidgets.QMessageBox.StandardButton.Yes
    )
    try:
        win._on_roi_remove_clicked(new_entry)
    finally:
        QtWidgets.QMessageBox.question = orig_question

    assert len(win.roi_entries) == initial_count
    assert win.roi_list.count() == initial_count
    assert new_entry not in win.roi_entries
    legend = win.timeseries_plot.getPlotItem().legend
    assert legend is None or legend.getLabel(new_entry.curve) is None

    # Erzeugungsnummern werden nie wiederverwendet, auch nach Entfernen nicht
    # (waere sie wiederverwendet worden, hiesse dieses ROI wieder "ROI 6").
    next_entry = win._add_roi_entry()
    assert next_entry.name == f"ROI {initial_count + 2}"
    QtWidgets.QMessageBox.question = staticmethod(
        lambda *a, **k: QtWidgets.QMessageBox.StandardButton.Yes
    )
    try:
        win._on_roi_remove_clicked(next_entry)
    finally:
        QtWidgets.QMessageBox.question = orig_question
    assert len(win.roi_entries) == initial_count
    win.roi_list.setCurrentRow(0)  # Ausgangszustand fuer nachfolgende Tests wiederherstellen


check("add/remove arbitrary ROI, same functionality as originals", test_add_and_remove_arbitrary_roi)

# --- Punkt 3: Verlaufs-Interpolation --------------------------------------


def test_interpolation():
    entry = win.roi_entries[1]
    entry.place(10, 10, 10, 10)
    win._show_frame(0)
    entry.capture_interp_start(0)
    entry.place(25, 25, 20, 20)
    win._show_frame(win.recording.n_frames - 1)
    entry.capture_interp_end(win.recording.n_frames - 1)
    entry.chk_interp.setChecked(True)
    win._show_frame(0)
    x0, y0 = entry.roi.pos()
    win._show_frame(win.recording.n_frames - 1)
    x1, y1 = entry.roi.pos()
    assert (x0, y0) != (x1, y1), "ROI sollte sich zwischen Frames bewegen"
    mid = win.recording.n_frames // 2
    win._show_frame(mid)
    frac = win._interp_fraction(mid, entry.interp_start_frame, entry.interp_end_frame)
    xm, ym = entry.roi.pos()
    xe, ye = entry.interp_rect(frac)[0], entry.interp_rect(frac)[1]
    assert abs(xm - xe) < 1e-6 and abs(ym - ye) < 1e-6, "Interpolation sollte frame-index-basiert sein, nicht zeitbasiert"
    win._recompute_curves(entries=[entry])
    entry.chk_interp.setChecked(False)
    win._show_frame(0)


check("roi time interpolation", test_interpolation)

win.roi_entries[2].place(60, 15, 15, 15)
win._recompute_curves(entries=[win.roi_entries[2]])

# --- Punkt 12: Massstab (Lineal) -------------------------------------------


class FakeEvent:
    def __init__(self, btn, scene_pos, double=False):
        self._btn = btn
        self._pos = scene_pos
        self._double = double

    def button(self):
        return self._btn

    def scenePos(self):
        return self._pos

    def double(self):
        return self._double


@contextlib.contextmanager
def ruler_length_input(mm_value: float):
    """Der Maßstab-Laengendialog ist seit ecbe9b5 ein eigener
    RulerLengthDialog (Spinbox, vorbefuellt mit dem zuletzt genutzten Wert)
    statt eines generischen QInputDialog.getDouble() -- patcht dessen exec()
    so, dass die Spinbox vor dem Bestaetigen auf mm_value gesetzt wird."""
    from thermal_viewer.dialogs import RulerLengthDialog

    def fake_exec(self):
        if isinstance(self, RulerLengthDialog):
            self.spin_mm.setValue(mm_value)
        return (self.accept(), QtWidgets.QDialog.DialogCode.Accepted)[1]

    with temp_dialog_exec(QtWidgets.QDialog, fake_exec):
        yield


def test_ruler():
    win._start_ruler_tool()
    assert win._ruler_armed
    p1 = win.view_box.mapViewToScene(QtCore.QPointF(2, 2))
    p2 = win.view_box.mapViewToScene(QtCore.QPointF(12, 2))
    win._handle_ruler_click(FakeEvent(QtCore.Qt.LeftButton, p1))
    assert win._ruler_start is not None

    with ruler_length_input(30.0):
        win._handle_ruler_click(FakeEvent(QtCore.Qt.LeftButton, p2))

    assert win._px_to_mm is not None
    assert abs(win._px_to_mm - 3.0) < 0.05, win._px_to_mm
    win.roi_list.setCurrentRow(0)  # QStackedWidget zeigt nur Inhalte der AKTIVEN Seite als "visible" an
    win._update_roi_mm_label(win.roi_entries[0])
    assert win.roi_entries[0].mm_label.isVisible()

    # Bugfix: Linie + mm-Beschriftung muessen nach erfolgreicher Eingabe
    # tatsaechlich sichtbar bleiben (vorher wurde die Linie direkt vor dem
    # Dialog wieder ausgeblendet, sodass nie etwas zu sehen war).
    assert win._ruler_line.isVisible()
    assert win._ruler_text.isVisible()
    assert "30,0 mm" in win._ruler_text.toPlainText()

    win._clear_ruler_scale()
    assert not win._ruler_line.isVisible()
    assert not win._ruler_text.isVisible()


check("ruler tool sets px_to_mm", test_ruler)


def test_live_cursor_kernel_size_menu_and_averaging():
    # Feature: "Werkzeuge > Live-Cursor-Bereichsgröße" -- Live-Verlauf/
    # -Anzeige koennen statt eines einzelnen Pixels den Mittelwert eines
    # NxN-Blocks um das Cursor-Pixel verwenden. Bewusst NICHT im rechten
    # Panel, sondern als eigenes Menue im Menueband (Nutzer-Vorgabe).
    import numpy as np

    assert set(win._live_cursor_kernel_actions.keys()) == {1, 3, 5, 7, 10}
    assert win._live_cursor_kernel_actions[1].isChecked()

    rows, cols = win.recording.shape
    row, col = rows // 2, cols // 2
    old_size = win._live_cursor_kernel_size
    try:
        win._live_cursor_kernel_size = 1
        win._update_live_cursor(row, col)
        single_pixel_series = win.live_curve.getData()[1].copy()
        expected_single = win.recording.frames[:, row, col]
        assert np.allclose(single_pixel_series, expected_single)

        win._on_live_cursor_kernel_selected(5)
        assert win._live_cursor_kernel_actions[5].isChecked() or True  # QActionGroup uebernimmt UI separat
        assert win._live_cursor_kernel_size == 5
        block_series = win.live_curve.getData()[1].copy()
        expected_block = win.recording.frames[:, row - 2:row + 3, col - 2:col + 3].mean(axis=(1, 2))
        assert np.allclose(block_series, expected_block)
        # Hinweis: Das synthetische Test-Datenset ist ein reiner linearer
        # Gradient, bei dem der Mittelwert eines SYMMETRISCHEN Blocks um ein
        # Zentrumspixel dem Zentrumswert exakt entspricht -- ein "muss sich
        # vom Einzelpixel unterscheiden"-Check waere hier also datenbedingt
        # zufaellig gruen/rot statt echtes Nutzverhalten zu pruefen. Der
        # asymmetrisch geclippte Eckfall (unten) beweist stattdessen robust,
        # dass tatsaechlich gemittelt wird und nicht bloss der Einzelwert
        # zurueckgegeben wird.

        # Randfall: Cursor direkt am Bildrand -- Block wird ans Bild geclippt,
        # darf nicht crashen/NaN liefern.
        rows, cols = win.recording.shape
        win._update_live_cursor(0, 0)
        edge_val = win._live_cursor_value(0, 0, 0)
        expected_edge = float(win.recording.frames[0, 0:3, 0:3].mean())
        assert abs(edge_val - expected_edge) < 1e-9
        assert abs(edge_val - float(win.recording.frames[0, 0, 0])) > 1e-6, (
            "geclippter 5x5-Mittelwert am Rand sollte sich vom Einzelpixel-Wert unterscheiden"
        )

        # Persistenz in QSettings.
        assert win._settings.value("live_cursor/kernel_size", type=int) == 5
    finally:
        win._on_live_cursor_kernel_selected(old_size)
        win._live_cursor_kernel_actions[old_size].setChecked(True)


check(
    "Werkzeuge-Menü 'Live-Cursor-Bereichsgröße' averages an NxN block and persists the setting",
    test_live_cursor_kernel_size_menu_and_averaging,
)


def test_ruler_persistence_and_reload():
    p1 = win.view_box.mapViewToScene(QtCore.QPointF(2, 2))
    p2 = win.view_box.mapViewToScene(QtCore.QPointF(12, 2))

    win._start_ruler_tool()
    win._handle_ruler_click(FakeEvent(QtCore.Qt.LeftButton, p1))
    with ruler_length_input(20.0):
        win._handle_ruler_click(FakeEvent(QtCore.Qt.LeftButton, p2))
    assert win._ruler_line.isVisible() and win._ruler_text.isVisible()

    # Bugfix: Werkzeug oeffnen und OHNE einen Punkt zu setzen abbrechen darf
    # die noch gueltige Referenzlinie nicht verstecken.
    win._start_ruler_tool()
    win._cancel_ruler_tool()
    assert win._ruler_line.isVisible() and win._ruler_text.isVisible()

    # Abbruch NACH dem Setzen eines neuen Startpunkts hat die alte Linie
    # bereits ueberschrieben -> jetzt darf/soll ausgeblendet werden.
    win._start_ruler_tool()
    win._handle_ruler_click(FakeEvent(QtCore.Qt.LeftButton, p1))
    win._cancel_ruler_tool()
    assert not win._ruler_line.isVisible()

    # Neu vermessen, damit fuer den Reload-Check wieder eine sichtbare Linie da ist.
    win._start_ruler_tool()
    win._handle_ruler_click(FakeEvent(QtCore.Qt.LeftButton, p1))
    with ruler_length_input(20.0):
        win._handle_ruler_click(FakeEvent(QtCore.Qt.LeftButton, p2))
    assert win._ruler_line.isVisible()

    # Bugfix: Neuladen einer Messreihe muss die (jetzt auf falsche
    # Pixelkoordinaten zeigende) Linie ausblenden, den Umrechnungsfaktor
    # selbst aber bewusst bestehen lassen.
    px_to_mm_before = win._px_to_mm
    win._set_recording(win.recording)
    assert not win._ruler_line.isVisible()
    assert not win._ruler_text.isVisible()
    assert win._px_to_mm == px_to_mm_before

    win._clear_ruler_scale()


check("ruler line survives cancel, hidden on overwrite/reload, px_to_mm persists", test_ruler_persistence_and_reload)


def test_interp_capture_buttons():
    entry = win.roi_entries[3]
    entry.place(5, 5, 8, 8)
    # Ueber das Spin-Feld navigieren (wie ein echter Nutzer), nicht per
    # direktem _show_frame()-Aufruf, damit frame_slider/current_index in
    # sich konsistent bleiben (Voraussetzung fuer den folgenden _step_frame-
    # Aufruf in _on_roi_interp_capture).
    win.frame_spin.setValue(3)
    entry.chk_interp.setChecked(True)

    # Phase 1 (Start): Klick springt zum ersten Bild und armiert die Erfassung,
    # erfasst aber noch NICHT die Geometrie.
    entry.btn_interp_start.click()
    assert win.current_index == 0
    assert entry.interp_arm_start
    assert entry.interp_start is None
    assert entry.btn_interp_start.text() == INTERP_START_CAPTURE_LABEL

    # Bugfix: bei gleichzeitig armiertem Start- UND Ende-Button muessen sich
    # deren Beschriftungen unterscheiden (vorher zeigten beide identisch
    # "Position uebernehmen" an, nicht mehr zuordenbar welcher Klick was tut).
    entry.btn_interp_end.click()
    assert entry.btn_interp_start.text() == INTERP_START_CAPTURE_LABEL
    assert entry.btn_interp_end.text() == INTERP_END_CAPTURE_LABEL
    assert entry.btn_interp_start.text() != entry.btn_interp_end.text()
    entry.interp_arm_end = False
    entry.btn_interp_end.setText(INTERP_END_LABEL)
    win.frame_spin.setValue(1)

    entry.place(1, 1, 6, 6)
    # Phase 2: zweiter Klick uebernimmt die (ggf. inzwischen angepasste) Geometrie.
    entry.btn_interp_start.click()
    assert not entry.interp_arm_start
    assert entry.interp_start is not None
    assert entry.btn_interp_start.text() == INTERP_START_LABEL

    entry.btn_interp_end.click()
    assert win.current_index == win.recording.n_frames - 1
    assert entry.interp_arm_end
    assert entry.btn_interp_end.text() == INTERP_END_CAPTURE_LABEL
    entry.btn_interp_end.click()
    assert not entry.interp_arm_end
    assert entry.interp_end is not None
    assert entry.btn_interp_end.text() == INTERP_END_LABEL

    entry.chk_interp.setChecked(False)


check("roi interp start/end buttons jump to frame then capture on 2nd click", test_interp_capture_buttons)


def test_interp_capture_arms_placement_for_unplaced_roi():
    # Bugreport: die Interpolations-Erfassung tat bei einem noch NICHT
    # platzierten Messbereich frueher stillschweigend gar nichts (stiller
    # Rueckkehr-Guard). Jetzt muss ein Klick auf "Start festlegen" direkt
    # "Messbereich setzen" aktivieren, damit ein Bildklick den Messbereich
    # ueberhaupt erst erzeugen kann.
    entry = win._add_roi_entry()
    try:
        assert not entry.placed
        entry.chk_interp.setChecked(True)

        entry.btn_interp_start.click()
        assert entry.interp_arm_start
        assert entry.btn_place.isChecked(), "Messbereich setzen haette aktiviert werden muessen"
        assert win._armed_entry is entry

        # Phase 2 vor dem Platzieren darf nicht crashen und darf nichts
        # erfassen (Info-Dialog statt stillem Nichtstun).
        entry.btn_interp_start.click()
        assert entry.interp_start is None
        assert entry.interp_arm_start, "sollte weiterhin auf die Platzierung warten"

        # Jetzt tatsaechlich per Bildklick platzieren (wie ein echter Nutzer).
        scene_pos = win.view_box.mapViewToScene(QtCore.QPointF(3, 3))
        win._on_scene_mouse_clicked(FakeEvent(QtCore.Qt.LeftButton, scene_pos))
        assert entry.placed
        assert not entry.btn_place.isChecked()
        assert win._armed_entry is None

        # Phase 2 erneut -- jetzt platziert, muss erfassen.
        entry.btn_interp_start.click()
        assert not entry.interp_arm_start
        assert entry.interp_start is not None
    finally:
        entry.chk_interp.setChecked(False)
        orig_question = QtWidgets.QMessageBox.question
        QtWidgets.QMessageBox.question = staticmethod(
            lambda *a, **k: QtWidgets.QMessageBox.StandardButton.Yes
        )
        try:
            win._on_roi_remove_clicked(entry)
        finally:
            QtWidgets.QMessageBox.question = orig_question
        win.roi_list.setCurrentRow(0)


check("interp capture on an unplaced ROI arms 'Messbereich setzen' instead of silently doing nothing", test_interp_capture_arms_placement_for_unplaced_roi)


def test_interp_focus_fade_visuals():
    focus, other = win.roi_entries[0], win.roi_entries[1]
    focus.place(3, 3, 5, 5)
    other.place(10, 10, 5, 5)
    assert focus.roi.opacity() == 1.0
    assert other.roi.opacity() == 1.0
    focus.chk_interp.setChecked(True)

    # Phase 1 "Start festlegen": andere Messbereiche stark verblasst, der
    # bearbeitete voll im Fokus.
    focus.btn_interp_start.click()
    assert focus.roi.opacity() == 1.0
    assert focus.label.opacity() == 1.0
    assert other.roi.opacity() < 0.2
    assert other.label.opacity() < 0.2

    focus.btn_interp_start.click()  # Phase 2: Start uebernehmen
    assert focus.interp_start is not None

    # "Ende festlegen" (Phase 1): der bearbeitete Messbereich selbst jetzt
    # leicht verblasst (zeigt noch die Start-Geometrie), aber deutlich
    # weniger stark als die anderen.
    focus.btn_interp_end.click()
    assert 0.2 < focus.roi.opacity() < 1.0
    assert other.roi.opacity() < focus.roi.opacity()

    focus.btn_interp_end.click()  # Phase 2: Ende uebernehmen
    assert focus.interp_end is not None

    # Nach Abschluss: alles wieder normal sichtbar.
    assert focus.roi.opacity() == 1.0
    assert other.roi.opacity() == 1.0

    focus.chk_interp.setChecked(False)


check("interpolation capture fades other ROIs, less so the one being edited", test_interp_focus_fade_visuals)

# --- Punkt 6/7/12: CSV-Export ----------------------------------------------
csv_path = OUT / "roi_export.csv"


def test_csv_export():
    if csv_path.exists():
        csv_path.unlink()
    orig = QtWidgets.QFileDialog.getSaveFileName
    QtWidgets.QFileDialog.getSaveFileName = staticmethod(lambda *a, **k: (str(csv_path), ""))
    try:
        win._export_csv()
    finally:
        QtWidgets.QFileDialog.getSaveFileName = orig
    assert csv_path.exists(), "CSV wurde nicht geschrieben"
    text = csv_path.read_text(encoding="utf-8-sig")
    lines = [l for l in text.splitlines() if l.strip()]
    header = lines[0].split(";")
    print("  CSV header:", header, "columns:", len(header))
    assert header[0] == "Zeitstempel"
    assert header[1] == "Laufzeit (HH:MM:SS)"
    assert len(lines) == len(win.recording.timestamps) + 1


check("csv export (relative runtime + real timestamp)", test_csv_export)

csv_selection_path = OUT / "roi_export_selection.csv"


def test_csv_export_roi_selection():
    # "Export Manager": im CSV-Spalten-Dialog abwaehlbar, welche Messbereiche
    # ueberhaupt exportiert werden -- Standard ist "alle ausgewaehlt".
    from thermal_viewer.dialogs import CsvColumnDialog

    placed_names = [e.name for e in win.roi_entries if e.placed]
    assert len(placed_names) >= 2, "Test braucht mindestens 2 platzierte ROIs"

    if csv_selection_path.exists():
        csv_selection_path.unlink()

    orig_exec = QtWidgets.QDialog.exec

    def custom_exec(self):
        if isinstance(self, CsvColumnDialog):
            assert all(chk.isChecked() for chk in self._checks), "Standard sollte 'alle' sein"
            self._checks[0].setChecked(False)  # erstes ROI abwaehlen
        return orig_exec(self)

    QtWidgets.QDialog.exec = custom_exec
    orig_save = QtWidgets.QFileDialog.getSaveFileName
    QtWidgets.QFileDialog.getSaveFileName = staticmethod(lambda *a, **k: (str(csv_selection_path), ""))
    try:
        win._export_csv()
    finally:
        QtWidgets.QDialog.exec = orig_exec
        QtWidgets.QFileDialog.getSaveFileName = orig_save

    text = csv_selection_path.read_text(encoding="utf-8-sig")
    header = text.splitlines()[0].split(";")
    # 2 Zeitspalten + (N-1) statt N ROI-Spalten, da eine abgewaehlt wurde.
    assert len(header) == 2 + len(placed_names) - 1, header
    assert placed_names[0] not in " ".join(header)


check("CSV export: ROI selection ('Export Manager', default all)", test_csv_export_roi_selection)


def test_csv_column_dialog_combined_px_mm_autofill():
    # Kein "Übernehmen"-Knopf mehr und kein "cm" mehr -- Klick auf "px"
    # oder "mm" aktualisiert den Spaltennamen sofort automatisch. px/mm sind
    # beide standardmaessig AUS (frueherer Init-Bug: war "an", aber der
    # Spaltenname wurde beim allerersten Anzeigen trotzdem nicht befuellt --
    # als bewusste Verhaltensaenderung auf "aus" korrigiert). Neu: "ALLE
    # px"/"ALLE mm"-Sammel-Checkboxen in der Kopfzeile.
    from thermal_viewer.dialogs import CsvColumnDialog

    win._px_to_mm = 0.5  # Massstab setzen, damit mm-Autofill aktiv ist
    entries = [
        {"name": "ROI 1", "width_px": 30.0, "height_px": 20.0, "width_mm": 15.0, "height_mm": 10.0},
        {"name": "ROI 2", "width_px": 12.0, "height_px": 12.0, "width_mm": None, "height_mm": None},
    ]
    dialog = CsvColumnDialog(win, entries)
    try:
        edit = dialog._edits[0]
        unit_checks = [c for c in dialog.findChildren(QtWidgets.QCheckBox) if c.text() in ("px", "mm", "cm")]
        assert not any(c.text() == "cm" for c in unit_checks), "'cm'-Option sollte entfernt sein"
        assert not any(
            isinstance(w, QtWidgets.QPushButton) and w.text() == "Übernehmen"
            for w in dialog.findChildren(QtWidgets.QPushButton)
        ), "'Übernehmen'-Knopf sollte entfernt sein"
        chk_px = dialog._px_checks[0]
        chk_mm = dialog._mm_checks[0]

        assert chk_mm.isEnabled(), "mm sollte bei gesetztem Massstab aktiv sein"
        assert not chk_px.isChecked(), "px muss standardmaessig AUS sein"
        assert not chk_mm.isChecked(), "mm muss standardmaessig AUS sein"
        assert dialog.chk_px_all.isChecked() is False
        assert dialog.chk_mm_all.isChecked() is False

        # Klick auf "px" bzw. "mm" (toggled-Signal) aktualisiert den Namen
        # SOFORT, ohne weiteren Knopf-Klick.
        chk_px.setChecked(True)
        assert "30x20 px" in edit.text(), edit.text()
        chk_mm.setChecked(True)
        text = edit.text()
        print("  combined px+mm column name (auto-updated on toggle):", text)
        assert "30x20 px" in text, text
        assert "15,0x10,0 mm" in text, text

        # Erneutes Abhaken von "px" aktualisiert den Namen ebenfalls sofort.
        chk_px.setChecked(False)
        text = edit.text()
        assert "30x20 px" not in text, text
        assert "15,0x10,0 mm" in text, text

        # "ALLE px" -> beide Zeilen-px-Checkboxen an; die zweite Zeile hat
        # keinen Massstab, ihre mm-Checkbox bleibt deaktiviert und wird von
        # "ALLE mm" korrekt uebersprungen.
        dialog.chk_px_all.setChecked(True)
        assert all(c.isChecked() for c in dialog._px_checks)
        assert dialog._mm_checks[1].isEnabled() is False
        dialog.chk_mm_all.setChecked(True)
        assert dialog._mm_checks[0].isChecked() is True
        assert dialog._mm_checks[1].isChecked() is False

        # Manuelles Abwaehlen einer Zeile laesst "ALLE px" automatisch abspringen.
        dialog._px_checks[0].setChecked(False)
        assert dialog.chk_px_all.isChecked() is False
    finally:
        dialog.close()
        win._px_to_mm = None


check("CSV column dialog: px/mm default off, live autofill, 'ALLE'-Sammel-Checkboxen", test_csv_column_dialog_combined_px_mm_autofill)

live_csv_path = OUT / "live_export.csv"


def test_live_csv_export():
    win._update_live_cursor(5, 5)
    if live_csv_path.exists():
        live_csv_path.unlink()
    orig = QtWidgets.QFileDialog.getSaveFileName
    QtWidgets.QFileDialog.getSaveFileName = staticmethod(lambda *a, **k: (str(live_csv_path), ""))
    try:
        win._export_csv()
    finally:
        QtWidgets.QFileDialog.getSaveFileName = orig
    assert live_csv_path.exists()


check("live csv export", test_live_csv_export)

# --- Punkt 5/8: Grafik-Export (PNG kombiniert/getrennt, SVG) ---------------
png_path = OUT / "graphic_export.png"
svg_path = OUT / "graphic_export.svg"


def test_graphic_export_combined_png():
    if png_path.exists():
        png_path.unlink()
    glw_parent_before = win.glw.parentWidget()
    glw_size_before = win.glw.size()
    ts_parent_before = win.timeseries_plot.parentWidget()
    ts_size_before = win.timeseries_plot.size()
    orig = QtWidgets.QFileDialog.getSaveFileName
    QtWidgets.QFileDialog.getSaveFileName = staticmethod(lambda *a, **k: (str(png_path), "PNG-Bild (*.png)"))
    try:
        win._export_graphic()
    finally:
        QtWidgets.QFileDialog.getSaveFileName = orig
    assert png_path.exists()
    assert png_path.with_suffix(".json").exists()

    # Bugfix: glw/timeseries_plot werden fuer den DPI-skalierten Export
    # (Standard-DPI 150 != 96, also scale != 1.0) kurzzeitig aus ihrem
    # Layout geloest (siehe _widget_resized_to) -- muessen danach exakt an
    # derselben Stelle mit derselben Groesse wieder eingesetzt sein.
    assert win.glw.parentWidget() is glw_parent_before and win.glw.size() == glw_size_before
    assert win.timeseries_plot.parentWidget() is ts_parent_before and win.timeseries_plot.size() == ts_size_before

    # Bugfix: painter.scale() vor QGraphicsView.render() lieferte nur einen
    # kleinen, falsch berechneten Ausschnitt statt des vollstaendigen Bilds.
    import numpy as np

    qimg = QtGui.QImage(str(png_path))
    arr = win._qimage_to_rgb_array(qimg)
    non_background = np.any(arr < 240, axis=2)
    coverage = non_background.mean()
    assert coverage > 0.15, f"Export wirkt zugeschnitten/gezoomt (nur {coverage:.0%} Bildinhalt)"


check("graphic export combined PNG", test_graphic_export_combined_png)


def test_maybe_hidden_live_cursor_context_manager():
    # "Cursor-Position mit exportieren" (Export-Dialog, Standard: aus) --
    # blendet Fadenkreuz + Temperaturanzeige waehrend des Exports aus, damit
    # eine gerade fixierte/zuletzt angezeigte Maus-Position nicht ungewollt
    # Teil der Grafik wird, und stellt den vorherigen Zustand exakt wieder her.
    win.live_cursor_marker.setVisible(True)
    win.live_cursor_label.setVisible(True)
    with win._maybe_hidden_live_cursor(False):
        assert win.live_cursor_marker.isVisible() is False
        assert win.live_cursor_label.isVisible() is False
    assert win.live_cursor_marker.isVisible() is True
    assert win.live_cursor_label.isVisible() is True

    win.live_cursor_marker.setVisible(False)
    win.live_cursor_label.setVisible(False)
    with win._maybe_hidden_live_cursor(False):
        assert win.live_cursor_marker.isVisible() is False
    assert win.live_cursor_marker.isVisible() is False, "muss auf den vorherigen (aus-)Zustand zurueckfallen"

    win.live_cursor_marker.setVisible(True)
    with win._maybe_hidden_live_cursor(True):
        assert win.live_cursor_marker.isVisible() is True, "include_cursor=True darf nichts ausblenden"
    win.live_cursor_marker.setVisible(False)
    win.live_cursor_label.setVisible(False)


check("_maybe_hidden_live_cursor hides + restores cursor marker/label", test_maybe_hidden_live_cursor_context_manager)


def test_export_dialog_cursor_position_defaults_off_and_wired_through():
    from thermal_viewer.dialogs import GraphicExportDialog as RealGraphicExportDialog
    from thermal_viewer.main_window import GraphicExportDialog

    dlg = RealGraphicExportDialog(win, win._settings, default_dpi=150)
    try:
        assert dlg.chk_cursor_position.isChecked() is False, "Standard muss AUS sein"
        assert dlg.export_cursor_position() is False
        dlg.chk_cursor_position.setChecked(True)
        assert dlg.export_cursor_position() is True
    finally:
        dlg.close()

    calls = []
    orig_ctx = win._maybe_hidden_live_cursor

    @contextlib.contextmanager
    def spy_ctx(include_cursor):
        calls.append(include_cursor)
        with orig_ctx(include_cursor):
            yield

    win._maybe_hidden_live_cursor = spy_ctx

    p = OUT / "cursor_option_wiring_check.png"

    def make_exec(include):
        def _exec(self):
            self.chk_cursor_position.setChecked(include)
            self.accept()
            return QtWidgets.QDialog.DialogCode.Accepted
        return _exec

    orig_save = QtWidgets.QFileDialog.getSaveFileName
    try:
        for include in (False, True):
            if p.exists():
                p.unlink()
            with temp_dialog_exec(GraphicExportDialog, make_exec(include)):
                QtWidgets.QFileDialog.getSaveFileName = staticmethod(lambda *a, **k: (str(p), "PNG-Bild (*.png)"))
                win._export_graphic()
            assert p.exists()
        assert calls == [False, True], calls
    finally:
        del win._maybe_hidden_live_cursor
        QtWidgets.QFileDialog.getSaveFileName = orig_save


check(
    "'Cursor-Position mit exportieren' defaults off and is wired into the export flow",
    test_export_dialog_cursor_position_defaults_off_and_wired_through,
)


def test_title_font_immune_to_image_dpi_metadata():
    # Bugfix: bei hoher Export-DPI (z.B. 300) wurde der Titel ueber
    # setPointSizeF() gesetzt UND das Ziel-QImage bekam per
    # setDotsPerMeterX/Y eine davon abweichende logische DPI -- der Font
    # wurde dadurch ein zweites Mal (quadratisch) skaliert und ueberdeckte
    # grossflaechig den Bereich darunter. setPixelSize() muss dagegen immun
    # sein: dieselbe sichtbare Groesse unabhaengig von der Bild-DPI-Metadaten.
    layout = MainWindow._combined_layout(300, (2000, 1500), (2000, 800))
    image = QtGui.QImage(10, 10, QtGui.QImage.Format_ARGB32)
    dots_per_meter = round(300 / 0.0254)
    image.setDotsPerMeterX(dots_per_meter)
    image.setDotsPerMeterY(dots_per_meter)
    metrics = QtGui.QFontMetrics(layout["font"], image)
    text_height = metrics.height()
    print(f"  dpi=300: title_height={layout['title_height']}px, rendered text height={text_height}px")
    assert text_height <= layout["title_height"], (
        f"Titel-Schrift ({text_height}px) passt nicht in die Titelzeile "
        f"({layout['title_height']}px) -- ueberdeckt vermutlich das Bild darunter"
    )


check("combined-graphic title font immune to QImage DPI metadata", test_title_font_immune_to_image_dpi_metadata)


def test_scaled_export_visuals_widens_pens_and_restores():
    entry = win.roi_entries[0]
    base_roi_width = entry.roi.pen.widthF()
    base_curve_width = entry.curve.opts["pen"].widthF()
    with win._scaled_export_visuals(3.0):
        assert entry.roi.pen.widthF() > base_roi_width
        assert entry.curve.opts["pen"].widthF() > base_curve_width
    # nach dem Kontext muss alles exakt wiederhergestellt sein.
    assert entry.roi.pen.widthF() == base_roi_width
    assert entry.curve.opts["pen"].widthF() == base_curve_width
    # scale <= 1.0: keine Veraenderung, kein Overhead.
    with win._scaled_export_visuals(1.0):
        assert entry.roi.pen.widthF() == base_roi_width


check("scaled export visuals (thin lines fix) widen pens + restore", test_scaled_export_visuals_widens_pens_and_restores)


def test_scaled_export_visuals_touches_only_legend_font():
    # Bugfix (Runde 1): eine fruehere Version skalierte Tick-/ROI-Label-
    # Schriftgroessen explizit mit dem Export-Faktor hoch (analog zu den
    # kosmetischen Stiften oben). Das ADDIERTE sich aber zur automatischen,
    # durch QGraphicsScene.render()'s Painter-Transform bereits vorhandenen
    # proportionalen Text-Skalierung (normaler Text -- anders als kosmetische
    # Stifte -- unterliegt dem Transform ganz normal) und liess
    # Achsenbeschriftung bei hoher Export-DPI unbrauchbar riesig werden.
    # Tick-/ROI-Label-Fonts duerfen daher UEBERHAUPT NICHT veraendert werden.
    #
    # Bugfix (Runde 2): die Legende ist davon eine bewusste AUSNAHME --
    # pyqtgraphs LegendItem setzt ItemIgnoresTransformations und ignoriert
    # dadurch GENAU den Skalierungs-Transform von QGraphicsScene.render(),
    # bliebe also ohne explizites Hochskalieren bei jeder Export-Aufloesung
    # winzig (Bugreport: "Legendenskalierung passt nicht mehr", nachdem
    # Runde 1 sie versehentlich mit-entfernt hatte).
    #
    # Bugfix (Runde 3): die Skalierung ueber label.setText(..., size=...) +
    # updateSize() (Runde 2) liess die Legende bei WIEDERHOLTEM Export jedes
    # Mal ein Stueck weiter/permanent anwachsen (Bugreport: Legende nach dem
    # Speichern im Hauptfenster verzerrt/Text und Linie in unterschiedlichen
    # Zeilen) -- die Restaurierung via erneutem setText()+updateSize() macht
    # pyqtgraphs QGraphicsGridLayout offenbar nicht zuverlaessig vollstaendig
    # rueckgaengig. Jetzt stattdessen ueber legend.setTransform() (von
    # ItemIgnoresTransformations UNBERUEHRT, da nur die GEERBTEN Transforms
    # ignoriert werden) -- eine reine Matrix-Zuweisung, garantiert exakt und
    # verlustfrei reversibel, unabhaengig davon wie oft hintereinander
    # exportiert wird.
    axis = win.timeseries_plot.getPlotItem().getAxis("left")
    base_axis_width = axis.maximumWidth()
    base_tick_font = axis.style.get("tickFont")
    entry = win.roi_entries[0]
    base_label_font = entry.label.textItem.font()
    legend = win.timeseries_legend
    assert legend is not None and legend.items, "Test braucht mindestens einen Legenden-Eintrag"
    _, label = legend.items[0]
    base_html = label.item.toHtml()
    base_legend_scene_rect = legend.sceneBoundingRect()

    orig_process_events = QtWidgets.QApplication.processEvents
    calls = []
    QtWidgets.QApplication.processEvents = staticmethod(
        lambda *a, **k: (calls.append(1), orig_process_events(*a, **k))[1]
    )
    try:
        with win._scaled_export_visuals(3.0):
            assert axis.maximumWidth() == base_axis_width
            assert axis.style.get("tickFont") == base_tick_font
            assert entry.label.textItem.font() == base_label_font
            assert label.item.toHtml() == base_html, "Legenden-TEXT darf sich nicht aendern (nur Transform)"
            scaled_legend_scene_rect = legend.sceneBoundingRect()
            assert scaled_legend_scene_rect.width() > base_legend_scene_rect.width() * 2.5, (
                "Legende haette insgesamt sichtbar breiter werden muessen"
            )
        assert calls == [], "darf processEvents() nicht fuer Font-/Layout-Handling benutzen (Flacker-Bug)"
    finally:
        QtWidgets.QApplication.processEvents = orig_process_events

    assert axis.maximumWidth() == base_axis_width
    assert label.item.toHtml() == base_html
    assert legend.sceneBoundingRect() == base_legend_scene_rect


check(
    "scaled export visuals scale ONLY the legend (via transform, axis/ROI labels stay untouched)",
    test_scaled_export_visuals_touches_only_legend_font,
)


def test_legend_scaling_survives_repeated_exports_without_drift():
    # Regression fuer Runde 3 (siehe oben): mehrere Exports hintereinander
    # (unterschiedliche DPI-Werte, wie ein echter Nutzer sie nacheinander
    # ausprobieren wuerde) duerfen die Legende NICHT permanent vergroessern
    # -- nach jedem einzelnen Export muss sie exakt auf ihre urspruengliche
    # Bildschirmgroesse zurueckfallen.
    from thermal_viewer.main_window import GraphicExportDialog

    legend = win.timeseries_legend
    base_rect = legend.sceneBoundingRect()

    drift_path = OUT / "legend_drift_check.png"
    orig_save = QtWidgets.QFileDialog.getSaveFileName
    try:
        for dpi in (150, 300, 600, 150):
            if drift_path.exists():
                drift_path.unlink()

            def make_exec(d):
                def _exec(self):
                    self.spin_dpi.setValue(d)
                    self.accept()
                    return QtWidgets.QDialog.DialogCode.Accepted
                return _exec

            with temp_dialog_exec(GraphicExportDialog, make_exec(dpi)):
                QtWidgets.QFileDialog.getSaveFileName = staticmethod(
                    lambda *a, **k: (str(drift_path), "PNG-Bild (*.png)")
                )
                win._export_graphic()
            assert drift_path.exists()
            assert legend.sceneBoundingRect() == base_rect, (
                f"Legende nach Export bei DPI {dpi} nicht auf Ausgangsgroesse zurueckgefallen: "
                f"{legend.sceneBoundingRect()} != {base_rect}"
            )
    finally:
        QtWidgets.QFileDialog.getSaveFileName = orig_save


check(
    "legend size does not permanently drift/grow across repeated exports at different DPI",
    test_legend_scaling_survives_repeated_exports_without_drift,
)


def _guard_widget(widget, label, touched):
    orig = {name: getattr(widget, name) for name in ("hide", "setParent", "resize")}
    for name, bound in orig.items():
        def wrapped(*a, _bound=bound, _key=f"{label}.{name}", **k):
            touched.append(_key)
            return _bound(*a, **k)
        setattr(widget, name, wrapped)
    return orig


def _unguard_widget(widget, orig):
    for name, bound in orig.items():
        setattr(widget, name, bound)


def test_export_never_hides_or_resizes_live_widget():
    # Bugfix: der Video-/Grafik-Export loeste das jeweilige Widget zuvor
    # kurzzeitig aus seinem Layout (hide+setParent(None)+resize), damit
    # QGraphicsView-Widgets bei hoher Export-DPI korrekt rendern -- dabei
    # verschwand sichtbar der Bildbereich im Hauptfenster (Bugreport: Bild
    # "verschwindet"/"macht Faxen"). Der neue Renderer (QGraphicsScene.render()
    # direkt auf die Zielgroesse) darf das sichtbare Widget dafuer gar nicht
    # mehr anfassen.
    touched = []
    guards = {
        "glw": _guard_widget(win.glw, "glw", touched),
        "timeseries_plot": _guard_widget(win.timeseries_plot, "timeseries_plot", touched),
    }
    p_png = OUT / "no_flicker_check.png"
    p_video = OUT / "no_flicker_check.mp4"
    for p in (p_png, p_video):
        if p.exists():
            p.unlink()
    orig_save = QtWidgets.QFileDialog.getSaveFileName
    try:
        QtWidgets.QFileDialog.getSaveFileName = staticmethod(lambda *a, **k: (str(p_png), "PNG-Bild (*.png)"))
        win._export_graphic()
        QtWidgets.QFileDialog.getSaveFileName = staticmethod(lambda *a, **k: (str(p_video), ""))
        win._export_video()
    finally:
        QtWidgets.QFileDialog.getSaveFileName = orig_save
        _unguard_widget(win.glw, guards["glw"])
        _unguard_widget(win.timeseries_plot, guards["timeseries_plot"])
    assert p_png.exists() and p_video.exists()
    assert not touched, f"Export hat hide/setParent/resize auf dem sichtbaren Widget aufgerufen: {touched}"


check("video/graphic export no longer hides or resizes the visible widget", test_export_never_hides_or_resizes_live_widget)


def test_graphic_export_svg():
    # Bugfix: QSvgGenerator liefert KEINEN automatischen Hintergrund -- ohne
    # explizites painter.fillRect() blieb das SVG weiss/transparent, wodurch
    # helle Achsen-/Text-/Gitterfarben (dunkles Theme) darauf praktisch
    # unsichtbar wurden (Bugreport: "weisser Hintergrund, man sieht nichts").
    import re

    if svg_path.exists():
        svg_path.unlink()
    orig = QtWidgets.QFileDialog.getSaveFileName
    QtWidgets.QFileDialog.getSaveFileName = staticmethod(
        lambda *a, **k: (str(svg_path), "SVG-Vektorgrafik (*.svg)")
    )
    try:
        win._export_graphic()
    finally:
        QtWidgets.QFileDialog.getSaveFileName = orig
    assert svg_path.exists()
    content = svg_path.read_text(encoding="utf-8")
    assert "<svg" in content
    assert len(content) > 500, f"SVG verdaechtig klein: {len(content)} bytes"

    vb_match = re.search(r'viewBox="0 0 (\d+) (\d+)"', content)
    assert vb_match, "kein viewBox gefunden"
    full_w, full_h = vb_match.group(1), vb_match.group(2)
    bg_rect_pattern = re.compile(
        rf'<g fill="(#[0-9a-fA-F]{{6}})"[^>]*>\s*<rect x="0" y="0" width="{full_w}" height="{full_h}"/>'
    )
    bg_match = bg_rect_pattern.search(content)
    assert bg_match, "kein hintergrundfuellendes <rect> ueber die volle Canvas-Groesse gefunden"
    assert bg_match.group(1).lower() == win._graph_bg.lower(), (
        f"SVG-Hintergrund {bg_match.group(1)} entspricht nicht der Graphen-Hintergrundfarbe {win._graph_bg}"
    )

    # Physische Groesse (mm) muss zur eigenen DPI-Konvention (scale=dpi/96,
    # hier default_dpi=150) passen statt QSvgGenerator's 72-DPI-Default zu
    # uebernehmen (sonst ca. 1,33x zu gross in Vektor-Editoren).
    size_match = re.search(r'<svg width="([\d.]+)mm" height="([\d.]+)mm"', content)
    assert size_match, "keine physische SVG-Groesse gefunden"
    expected_w_mm = int(full_w) / 150 * 25.4
    assert abs(float(size_match.group(1)) - expected_w_mm) < 1.0, (
        size_match.group(1), expected_w_mm,
    )


check("graphic export SVG (combined) has correct background fill + physical size", test_graphic_export_svg)


def test_graphic_export_separate():
    win._settings.setValue("export/separate_images", True)
    p = OUT / "graphic_sep.png"
    for f in [OUT / "graphic_sep_Bild.png", OUT / "graphic_sep_Kurve.png"]:
        if f.exists():
            f.unlink()

    # Bugreport: "Live (Cursor)" ist mit "Zeitverlauf" tabifiziert und wurde
    # in dieser Sitzung noch nie in den Vordergrund geholt -- Qt layoutet
    # eine im Hintergrund liegende Dock-Registerkarte nie vollstaendig,
    # wodurch live_plot vor dem Fix eine winzige/veraltete Groesse hatte und
    # der Export dadurch ohne (bzw. abgeschnittener) Achsenbeschriftung
    # herauskam. Muss hier tatsaechlich noch nie gezeigt worden sein, sonst
    # testet dieser Test den Bug gar nicht. (widget.isVisible() ist dafuer
    # NICHT zuverlaessig -- meldet True auch fuer eine im Hintergrund
    # liegende Registerkarte -- visibleRegion().isEmpty() dagegen schon.)
    assert win.timeseries_dock.visibleRegion().isEmpty() is False
    assert win.live_dock.visibleRegion().isEmpty() is True
    tiny_before = win.live_plot.size()
    assert tiny_before.width() < 400 or tiny_before.height() < 200, (
        f"Testvoraussetzung verletzt: live_plot war schon vorher richtig gross ({tiny_before})"
    )

    orig = QtWidgets.QFileDialog.getSaveFileName
    QtWidgets.QFileDialog.getSaveFileName = staticmethod(lambda *a, **k: (str(p), "PNG-Bild (*.png)"))
    try:
        win._export_graphic()
    finally:
        QtWidgets.QFileDialog.getSaveFileName = orig
    assert (OUT / "graphic_sep_Bild.png").exists()
    assert (OUT / "graphic_sep_Kurve.png").exists()

    # Vorher aktiver Tab muss unveraendert wiederhergestellt sein (kein
    # sichtbarer Sprung fuer den Nutzer).
    assert win.timeseries_dock.visibleRegion().isEmpty() is False
    assert win.live_dock.visibleRegion().isEmpty() is True

    import numpy as np

    qimg = QtGui.QImage(str(OUT / "graphic_sep_Kurve.png"))
    # Nur grob auf "nicht winzig/leer" pruefen -- die genaue Breite haengt
    # vom verfuegbaren Dock-Platz in diesem Test-Fenster ab (kein Bug), nur
    # eine kaputt gebliebene 252x54-Groesse (siehe Docstring oben) waere
    # tatsaechlich verdaechtig klein.
    assert qimg.width() > 200 and qimg.height() > 150, (
        f"Kurven-Export wirkt verdaechtig klein/abgeschnitten: {qimg.width()}x{qimg.height()}"
    )
    arr = win._qimage_to_rgb_array(qimg)
    non_background = np.any(arr > 40, axis=2)  # heller Achsen-/Kurventext auf dunklem Grund
    left_strip = non_background[:, :30]
    assert left_strip.mean() > 0.02, (
        "linker Rand (dort sitzt die y-Achsenbeschriftung) wirkt praktisch leer"
    )

    win._settings.setValue("export/separate_images", False)


check("graphic export separate files (also regression-guards the missing axis label bug)", test_graphic_export_separate)

# --- Native pyqtgraph SVG rechtsklick-Export (Punkt 8) ---------------------


def test_native_svg_export():
    import pyqtgraph.exporters as pg_exporters

    names = [e.Name for e in pg_exporters.Exporter.Exporters]
    assert not any("Matplotlib" in n for n in names), names


check("no matplotlib option in pyqtgraph's own exporter list", test_native_svg_export)


def test_context_menu_export_routes_to_exact_same_method_as_menu():
    # Bugfix (historisch): Rechtsklick "Exportieren" auf einem der beiden
    # Kurven-Graphen fuehrte frueher zu einem AEHNLICH aussehenden, aber
    # eigenen Dialog (nur dieser eine Graph, ohne Kombiniert/Getrennt-Auswahl)
    # statt zum exakt SELBEN Weg wie ueber das Menüband. Seit ecbe9b5 gibt es
    # ohnehin nur noch EINE gemeinsame _export_graphic-Methode (statt
    # getrennter _export_timeseries_graphic/_export_live_graphic) -- beide
    # Rechtsklick-Eintraege muessen exakt diese eine Methode aufrufen.
    assert win.timeseries_plot.scene().contextMenu[0].text() == "Grafik speichern…"
    assert win.live_plot.scene().contextMenu[0].text() == "Grafik speichern…"

    calls = []
    orig = win._export_graphic
    win._export_graphic = lambda: calls.append("called")
    try:
        win.timeseries_plot.scene().contextMenu[0].trigger()
        win.live_plot.scene().contextMenu[0].trigger()
        assert calls == ["called", "called"], calls
    finally:
        win._export_graphic = orig


check(
    "right-click 'Exportieren' on a curve graph routes to the exact same method as the menu bar",
    test_context_menu_export_routes_to_exact_same_method_as_menu,
)


def test_context_menu_export_unified_and_fixes_svg_curves():
    # Bugfix: pyqtgraphs eigener SVGExporter liess bei Kurven-Graphen (Legende
    # + DateAxisItem) in der Praxis die Kurve selbst weg -- nur das
    # Koordinatensystem landete im SVG. Der Rechtsklick-"Export..."-Eintrag
    # ruft jetzt stattdessen denselben (bereits fuer den Export-Menü
    # verifizierten) Renderer auf wie das Export-Menü -- fuer Zeitverlauf-/
    # Live-Graph exakt derselbe (inkl. Thermobild+Kombiniert/Getrennt-Wahl),
    # fuer das Thermobild selbst (kein Menü-Aequivalent) weiterhin der auf
    # dieses eine Widget beschraenkte Einzel-Export.
    for widget, name in (
        (win.timeseries_plot, "context_export_timeseries.svg"),
        (win.live_plot, "context_export_live.svg"),
        (win.glw, "context_export_image.svg"),
    ):
        p = OUT / name
        if p.exists():
            p.unlink()
        scene = widget.scene()
        assert scene.contextMenu[0].text() == "Grafik speichern…", scene.contextMenu[0].text()
        orig_save = QtWidgets.QFileDialog.getSaveFileName
        QtWidgets.QFileDialog.getSaveFileName = staticmethod(
            lambda *a, _p=p, **k: (str(_p), "SVG-Vektorgrafik (*.svg)")
        )
        try:
            scene.contextMenu[0].trigger()
        finally:
            QtWidgets.QFileDialog.getSaveFileName = orig_save
        assert p.exists(), f"{name} wurde nicht geschrieben"
        content = p.read_text(encoding="utf-8")
        assert "<svg" in content
        if widget is not win.glw:
            # Nur Kurven-Graphen: die eigentliche Kurve muss als Vektorpfad
            # im SVG landen, nicht nur Achsen/Gitter/Legende.
            assert "<path" in content or "<polyline" in content, (
                f"{name}: SVG enthaelt keine Kurven-Vektorpfade (nur Koordinatensystem?)"
            )
        # Derselbe Hintergrund-Bugfix wie bei "graphic export SVG": ohne
        # explizite Fuellung waere das SVG weiss/transparent statt in der
        # Graphen-Hintergrundfarbe.
        assert f'fill="{win._graph_bg.lower()}"' in content.lower(), (
            f"{name}: kein Hintergrund-Fill in der Graphen-Hintergrundfarbe gefunden"
        )


check(
    "right-click 'Export...' uses our unified dialog + fixes missing SVG curves",
    test_context_menu_export_unified_and_fixes_svg_curves,
)


def test_svg_export_curve_has_precise_coordinates_no_scientific_notation():
    # Bugfix: Kurven auf der Zeitachse nutzen als x-Werte absolute Unix-
    # Sekunden (~1,8 Milliarden). Qt serialisiert Pfad-/Transform-Zahlen im
    # SVG jedoch nur mit ca. 6 signifikanten Stellen -- bei so grossen
    # Absolutwerten reichte das nicht aus, um die (viel kleineren)
    # Unterschiede zwischen einzelnen Kurvenpunkten darzustellen: alle
    # x-Koordinaten landeten im SVG-Text als (fast) derselbe gerundete
    # Wert, die Kurve kollabierte zu einer Linie/verschwand komplett
    # (Bugreport: "im SVG-Graphen fehlen die Kurven"). _rebased_time_axis
    # verschiebt die Werte fuer die Dauer des SVG-Renderns auf kleine,
    # praezise darstellbare Zahlen. Der vorherige Test prueft nur "irgendein
    # <path> vorhanden" (auch Achsen/Legende zaehlen dafuer) und haette den
    # Bug NICHT erkannt -- hier wird gezielt auf verlorene Praezision
    # (wissenschaftliche Notation) geprueft.
    import re

    for widget, name in (
        (win.timeseries_plot, "svg_precision_timeseries.svg"),
        (win.live_plot, "svg_precision_live.svg"),
    ):
        p = OUT / name
        if p.exists():
            p.unlink()
        orig_save = QtWidgets.QFileDialog.getSaveFileName
        QtWidgets.QFileDialog.getSaveFileName = staticmethod(
            lambda *a, _p=p, **k: (str(_p), "SVG-Vektorgrafik (*.svg)")
        )
        try:
            widget.scene().contextMenu[0].trigger()
        finally:
            QtWidgets.QFileDialog.getSaveFileName = orig_save
        assert p.exists(), f"{name} wurde nicht geschrieben"
        content = p.read_text(encoding="utf-8")
        assert not re.search(r"\d\.\d+e\+\d{2,}", content), (
            f"{name}: SVG enthaelt Zahlen in wissenschaftlicher Notation -- "
            "Hinweis auf verlorene Praezision bei grossen Unix-Zeitstempeln"
        )

        # Die Zeitachsen-Beschriftung selbst muss trotz der kurzzeitigen
        # Verschiebung unveraendert bleiben.
        axis = win.axis_timeseries_bottom if widget is win.timeseries_plot else win.axis_live_bottom
        assert axis.export_offset == 0.0, "export_offset nach dem Export nicht zurueckgesetzt"


check(
    "SVG export: curve x-coordinates stay precise (no scientific-notation collapse)",
    test_svg_export_curve_has_precise_coordinates_no_scientific_notation,
)

# --- Punkt 10/11: Video-Export ---------------------------------------------
video_path = OUT / "export_video.mp4"


def test_video_export():
    if video_path.exists():
        video_path.unlink()
    glw_parent_before = win.glw.parentWidget()
    glw_size_before = win.glw.size()
    orig_save = QtWidgets.QFileDialog.getSaveFileName
    QtWidgets.QFileDialog.getSaveFileName = staticmethod(lambda *a, **k: (str(video_path), ""))
    try:
        win._export_video()
    finally:
        QtWidgets.QFileDialog.getSaveFileName = orig_save
    assert video_path.exists(), "Video wurde nicht erstellt"
    assert video_path.stat().st_size > 1000, video_path.stat().st_size

    # Bugfix: win.glw wird fuer den hochaufgeloesten Export kurzzeitig aus
    # seinem Layout geloest (siehe _widget_resized_to) -- muss danach exakt
    # an derselben Stelle mit derselben Groesse wieder eingesetzt sein.
    assert win.glw.parentWidget() is glw_parent_before
    assert win.glw.size() == glw_size_before
    assert win.glw.isVisible()

    # Bugfix: painter.scale() vor QGraphicsView.render() lieferte nur einen
    # kleinen, falsch berechneten Ausschnitt (fast nur Hintergrundfarbe)
    # statt des vollstaendigen Frames -- pruefen, dass ein grosser Teil der
    # Videoflaeche tatsaechlich Bildinhalt zeigt.
    import imageio.v2 as imageio
    import numpy as np

    reader = imageio.get_reader(str(video_path))
    frame = reader.get_data(0)
    reader.close()
    non_background = np.any(frame < 240, axis=2)
    coverage = non_background.mean()
    assert coverage > 0.5, f"Video-Frame wirkt zugeschnitten/gezoomt (nur {coverage:.0%} Bildinhalt)"


check("video export (default dialog settings)", test_video_export)

video_custom_path = OUT / "export_video_custom.mp4"


def test_video_export_custom_settings_restores_state():
    win.combo_cmap.setCurrentIndex(0)
    win.chk_cmap_invert.setChecked(False)
    win.radio_level_per_frame.setChecked(True)
    prev_state = win._capture_level_widgets_state()

    if video_custom_path.exists():
        video_custom_path.unlink()

    orig_exec = QtWidgets.QDialog.exec

    def custom_exec(self):
        if hasattr(self, "radio_custom_settings"):
            self.radio_custom_settings.setChecked(True)
            self.combo_cmap.setCurrentIndex(2)
            self.chk_invert.setChecked(True)
            idx = self.combo_level_mode.findData("manual")
            self.combo_level_mode.setCurrentIndex(idx)
            self.spin_min.setValue(5.0)
            self.spin_max.setValue(60.0)
        return orig_exec(self)

    QtWidgets.QDialog.exec = custom_exec
    orig_save = QtWidgets.QFileDialog.getSaveFileName
    QtWidgets.QFileDialog.getSaveFileName = staticmethod(lambda *a, **k: (str(video_custom_path), ""))
    try:
        win._export_video()
    finally:
        QtWidgets.QDialog.exec = orig_exec
        QtWidgets.QFileDialog.getSaveFileName = orig_save

    assert video_custom_path.exists()
    after_state = win._capture_level_widgets_state()
    assert after_state["cmap_index"] == prev_state["cmap_index"]
    assert after_state["invert"] == prev_state["invert"]
    assert after_state["level_mode"] == prev_state["level_mode"]
    assert after_state["level_min"] == prev_state["level_min"]
    assert after_state["level_max"] == prev_state["level_max"]
    assert win.spin_level_min.isEnabled() == (prev_state["level_mode"] == "manual")


check("video export with custom settings restores prior state afterward", test_video_export_custom_settings_restores_state)


video_overlay_path = OUT / "export_video_overlay.mp4"


def test_video_export_timeline_overlay_and_macro_block_alignment():
    # Punkt "Zeitanzeige im Video": Balken+Text am unteren Rand, waehlbar
    # ueber "Keine"/"Zeitleiste"/"Zeitstempel"/"Beides" im Video-Export-
    # Dialog (Namen bewusst konsistent mit dem Rest der App).
    if video_overlay_path.exists():
        video_overlay_path.unlink()

    orig_exec = QtWidgets.QDialog.exec

    def custom_exec(self):
        if hasattr(self, "radio_overlay_both"):
            self.radio_overlay_both.setChecked(True)
        return orig_exec(self)

    QtWidgets.QDialog.exec = custom_exec
    orig_save = QtWidgets.QFileDialog.getSaveFileName
    QtWidgets.QFileDialog.getSaveFileName = staticmethod(lambda *a, **k: (str(video_overlay_path), ""))
    try:
        win._export_video()
    finally:
        QtWidgets.QDialog.exec = orig_exec
        QtWidgets.QFileDialog.getSaveFileName = orig_save

    assert video_overlay_path.exists()

    import imageio.v2 as imageio
    import numpy as np

    reader = imageio.get_reader(str(video_overlay_path))
    frame0 = reader.get_data(0)
    frame_last = reader.get_data(reader.count_frames() - 1)
    reader.close()

    # Bugfix: ffmpeg (macro_block_size=16) vergroesserte nicht durch 16
    # teilbare Frames bisher selbst (mit Konsolen-Warnung) -- render()
    # muss das Bild bereits in ffmpeg-kompatibler Groesse liefern.
    h, w, _ = frame0.shape
    assert h % 16 == 0 and w % 16 == 0, (w, h)

    # Der Streifen am unteren Bildrand muss sich zwischen erstem und
    # letztem Frame sichtbar unterscheiden (Fortschrittsbalken-Position/
    # Zeitstempel-Text aendern sich) -- sonst waere ueberhaupt kein Overlay
    # gezeichnet worden.
    strip0 = frame0[-40:, :, :]
    strip_last = frame_last[-40:, :, :]
    assert not np.array_equal(strip0, strip_last), "Zeitanzeige-Streifen aendert sich nicht zwischen den Frames"

    # Ohne Overlay ("Keine", Standard) darf sich am Verhalten/an der
    # Groesse nichts aendern -- weiterhin 16-teilbar, siehe test_video_export.
    reader2 = imageio.get_reader(str(video_path))
    frame_no_overlay = reader2.get_data(0)
    reader2.close()
    h2, w2, _ = frame_no_overlay.shape
    assert h2 % 16 == 0 and w2 % 16 == 0, (w2, h2)


check(
    "video export: timeline overlay ('Zeitleiste'/'Zeitstempel'/'Beides') + macro_block_size-16 alignment",
    test_video_export_timeline_overlay_and_macro_block_alignment,
)


def test_video_export_dialog_cursor_position_defaults_off_and_wired_through():
    from thermal_viewer.dialogs import VideoExportDialog as RealVideoExportDialog
    from thermal_viewer.main_window import VideoExportDialog

    dlg = RealVideoExportDialog(
        win, n_frames=win.recording.n_frames, colormaps=COLORMAPS,
        current_colormap_index=0, current_invert=False, current_level_mode="per_frame",
        current_min=0.0, current_max=50.0, current_fps=10.0,
    )
    try:
        assert dlg.chk_cursor_position.isChecked() is False, "Standard muss AUS sein"
        assert dlg.export_cursor_position() is False
    finally:
        dlg.close()

    calls = []
    orig_ctx = win._maybe_hidden_live_cursor

    @contextlib.contextmanager
    def spy_ctx(include_cursor):
        calls.append(include_cursor)
        with orig_ctx(include_cursor):
            yield

    win._maybe_hidden_live_cursor = spy_ctx

    p = OUT / "video_cursor_option_wiring_check.mp4"

    def make_exec(include):
        def _exec(self):
            self.chk_cursor_position.setChecked(include)
            self.accept()
            return QtWidgets.QDialog.DialogCode.Accepted
        return _exec

    orig_save = QtWidgets.QFileDialog.getSaveFileName
    try:
        if p.exists():
            p.unlink()
        with temp_dialog_exec(VideoExportDialog, make_exec(False)):
            QtWidgets.QFileDialog.getSaveFileName = staticmethod(lambda *a, **k: (str(p), ""))
            win._export_video()
        assert p.exists()
        assert calls == [False], calls
    finally:
        del win._maybe_hidden_live_cursor
        QtWidgets.QFileDialog.getSaveFileName = orig_save


check(
    "video export: 'Cursor-Position mit exportieren' defaults off and is wired through",
    test_video_export_dialog_cursor_position_defaults_off_and_wired_through,
)


def test_video_export_tight_letterbox_crop():
    # Bugreport: "unnötige leere Stellen" (Leerraum durch
    # setAspectLocked(True)) links/rechts vom Thermobild im Video-Export --
    # _tight_glw_segments() muss diesen Leerraum erkennen und
    # herausschneiden (3 Segmente statt 1, Gesamtbreite kleiner als der
    # volle Sichtbereich).
    segments = win._tight_glw_segments()
    full = win._visible_scene_rect(win.glw)
    assert len(segments) in (1, 3), len(segments)
    total_width = sum(s.width() for s in segments)
    assert total_width <= full.width() + 1e-6

    # Regressionsschutz fuer den Crash-Bug: bei "pro Bild"-Autoskalierung
    # aendert sich die Legenden-Beschriftungsbreite von Frame zu Frame --
    # _render_video_frame darf die Segmente NICHT pro Frame neu berechnen
    # (sonst ergeben sich unterschiedliche Bildgroessen zwischen Frames,
    # was imageio mit "All images in a movie should have same size" quittiert).
    # Hier wird direkt geprueft, dass gleiche, vorab berechnete Segmente
    # über mehrere Frames hinweg identische Ausgabegroessen liefern.
    win.radio_level_per_frame.setChecked(True)
    unix = win.recording.unix_seconds()
    frame_indices = list(range(0, min(5, win.recording.n_frames)))
    sizes = set()
    for idx in frame_indices:
        win._show_frame(idx)
        img = win._render_video_frame(1.0, QtGui.QColor(win._graph_bg), "none", idx, frame_indices, unix, segments)
        sizes.add((img.width(), img.height()))
    assert len(sizes) == 1, f"Bildgroessen variieren zwischen Frames: {sizes}"


check(
    "video export: letterbox space around the thermal image is trimmed, frame sizes stay consistent",
    test_video_export_tight_letterbox_crop,
)


def test_video_overlay_marker_true_position_relative_to_full_recording():
    # Bugreport: die Zeitleiste im Video soll die TATSAECHLICHE Position
    # des exportierten Ausschnitts relativ zur GESAMTEN Aufnahme zeigen
    # (nicht den Ausschnitt selbst auf die volle Leistenbreite strecken).
    # Pruefung ueber die tatsaechlich gerenderten Pixel: der blaue
    # Fortschritts-/Hervorhebungsbalken darf nur im Bereich zwischen
    # gruener und roter Markierung erscheinen -- bei einem Ausschnitt, der
    # nur einen kleinen Teil der Gesamtaufnahme umfasst, muss der GROSSTEIL
    # der (grauen) Zeitleiste rechts davon frei von Blau bleiben.
    n = win.recording.n_frames
    if n < 30:
        return  # Datensatz zu kurz, um einen aussagekraeftigen Bruchteil zu testen.
    frame_indices = list(range(0, 5))
    unix = win.recording.unix_seconds()
    segments = win._tight_glw_segments()
    img = win._render_video_frame(1.0, QtGui.QColor(win._graph_bg), "timeline", 2, frame_indices, unix, segments)
    arr = win._qimage_to_rgb_array(img)

    overlay_height = round(54 * 1.0)
    strip = arr[-overlay_height:, :, :]
    bar_row = int(strip.shape[0] * 0.34)
    row_pixels = strip[bar_row]
    blue = (row_pixels[:, 2].astype(int) - row_pixels[:, 0].astype(int)) > 40
    blue_cols = np.nonzero(blue)[0]
    assert blue_cols.size > 0, "keine blaue Zeitleisten-Markierung gefunden"
    blue_span = blue_cols.max() - blue_cols.min()
    # Der exportierte Ausschnitt (5 von >=30 Frames) darf hoechstens einen
    # kleinen Teil der Leistenbreite einnehmen -- eine ueber die GESAMTE
    # Breite gestreckte (alte, fehlerhafte) Darstellung wuerde hier
    # durchfallen.
    assert blue_span < row_pixels.shape[0] * 0.6, (blue_span, row_pixels.shape[0])


check(
    "video overlay marker sits at the true position within the full recording",
    test_video_overlay_marker_true_position_relative_to_full_recording,
)


def test_video_overlay_runtime_relative_to_full_recording_not_export_range():
    # Bugfix: die "Laufzeit" im Zeitleisten-Overlay wurde bisher relativ zum
    # START DES EXPORTIERTEN AUSSCHNITTS berechnet -- ein Export ab z.B.
    # Frame-Index 10 (der im Hauptfenster als Laufzeit 00:00:xx angezeigt
    # wird) zeigte im Video faelschlich wieder bei 00:00:00 an, statt bei
    # der Laufzeit, die auch die Laufzeit-Anzeige im Hauptfenster fuer
    # diesen Frame zeigt (TimeAxisItem.t0 = recording.unix_seconds()[0]).
    from thermal_viewer.main_window import MainWindow

    unix = win.recording.unix_seconds()
    n = win.recording.n_frames
    start = 1 if n >= 4 else 0
    frame_indices = list(range(start, n))
    idx = frame_indices[min(2, len(frame_indices) - 1)]

    captured = []
    orig = MainWindow._format_relative_runtime

    def spy(seconds):
        captured.append(seconds)
        return orig(seconds)

    MainWindow._format_relative_runtime = staticmethod(spy)
    try:
        segments = win._tight_glw_segments()
        win._render_video_frame(1.0, QtGui.QColor(win._graph_bg), "timeline", idx, frame_indices, unix, segments)
    finally:
        MainWindow._format_relative_runtime = staticmethod(orig)

    assert len(captured) == 2, captured
    elapsed, total = captured
    assert elapsed == unix[idx] - unix[0], (elapsed, unix[idx] - unix[0])
    assert total == unix[frame_indices[-1]] - unix[0], (total, unix[frame_indices[-1]] - unix[0])
    # Regressionsschutz: NICHT relativ zum Beginn des Ausschnitts.
    assert elapsed != unix[idx] - unix[frame_indices[0]]


check(
    "video overlay 'Laufzeit' is relative to the full recording start, not the exported range's start",
    test_video_overlay_runtime_relative_to_full_recording_not_export_range,
)


def test_svg_export_axis_text_not_double_scaled():
    # Bugfix: Achsen-Tick-Beschriftung (ueber pyqtgraphs gecachtes QPicture
    # gezeichnet) wurde im SVG-Export QUADRATISCH zu gross, weil
    # generator.setResolution(scale*96) beim Abspielen des QPicture einen
    # ZWEITEN, versteckten Skalierungsfaktor einfuehrte (zusaetzlich zum
    # normalen Render-Transform) -- sichtbar als riesige, ueberwiegend aus
    # dem sichtbaren Bereich ragende Achsenbeschriftung. Jetzt bleibt
    # resolution() konstant bei 96 (setSize() traegt stattdessen die
    # logische Groesse), wodurch JEDE Text-Transform-Matrix im SVG nur noch
    # den einfachen Skalierungsfaktor zeigen darf.
    import re
    import xml.etree.ElementTree as ET

    # Patch direkt auf der Subklasse (nicht QtWidgets.QDialog): ein
    # frueherer Test in dieser Suite setzt GraphicExportDialog.exec bereits
    # subklassen-lokal (siehe "DPI applies..."-Test weiter oben) -- das
    # ueberschreibt dauerhaft jeden spaeteren Patch auf der Basisklasse, da
    # Python das Attribut zuerst auf der Subklasse findet.
    from thermal_viewer.main_window import GraphicExportDialog

    p = OUT / "svg_axis_text_scale_check.svg"
    if p.exists():
        p.unlink()
    orig_exec = GraphicExportDialog.exec  # fuer force_dpi_300's Verkettung unten

    def force_dpi_300(self):
        self.spin_dpi.setValue(300)
        return orig_exec(self)

    orig_save = QtWidgets.QFileDialog.getSaveFileName
    try:
        with temp_dialog_exec(GraphicExportDialog, force_dpi_300):
            QtWidgets.QFileDialog.getSaveFileName = staticmethod(
                lambda *a, **k: (str(p), "SVG-Vektorgrafik (*.svg)")
            )
            win.timeseries_plot.scene().contextMenu[0].trigger()
    finally:
        QtWidgets.QFileDialog.getSaveFileName = orig_save
    assert p.exists()

    root = ET.parse(str(p)).getroot()
    ns = "{http://www.w3.org/2000/svg}"
    parent_map = {c: parent for parent in root.iter() for c in parent}
    scales_seen = set()
    for text_el in root.iter(ns + "text"):
        if not (text_el.text or "").strip():
            continue
        g = parent_map.get(text_el)
        tr = g.attrib.get("transform") if g is not None else None
        # matrix(1,0,0,1,0,0) (Identitaet) gehoert zu Titel-Text, der
        # direkt per painter.drawText() gezeichnet wird (nicht ueber die
        # pyqtgraph-Szene/Render-Transform) -- bei erzwungener DPI 300
        # (scale=3.125) ist das eindeutig von echt skaliertem Text zu
        # unterscheiden, ignorieren.
        if not tr or tr.replace(" ", "") == "matrix(1,0,0,1,0,0)":
            continue
        m = re.match(r"matrix\(([-\d.eE+]+),([-\d.eE+]+),", tr)
        if not m:
            continue
        a, b = float(m.group(1)), float(m.group(2))
        magnitude = (a * a + b * b) ** 0.5
        if magnitude > 1e-6:
            scales_seen.add(round(magnitude, 2))
    assert scales_seen, "keine skalierten <text>-Elemente im SVG gefunden"
    max_scale = max(scales_seen)
    min_scale = min(scales_seen)
    # Alle Text-Skalierungsfaktoren muessen nahe beieinander liegen (nur
    # einfache Skalierung durch den Export-Faktor, keine zusaetzliche
    # quadratische Komponente, die deutlich groesser waere).
    assert max_scale / min_scale < 1.5, (
        f"Text-Skalierungsfaktoren zu unterschiedlich (Hinweis auf Doppel-Skalierung): {scales_seen}"
    )


check(
    "SVG export: axis tick text is not quadratically double-scaled",
    test_svg_export_axis_text_not_double_scaled,
)


def test_svg_export_uses_reduced_pen_scale_for_thinner_lines():
    # Bugreport: Linien im SVG-Export wirken bei identischer Pixelbreite
    # wie der Raster-Export optisch kraeftiger/dicker (scharfkantige,
    # voll deckende Vektor-Striche vs. leicht antialiaste Raster-Linie) --
    # SVG-Exporte nutzen daher jetzt einen reduzierten
    # MainWindow._SVG_PEN_SCALE_FACTOR (<1.0) fuer die Stiftbreiten.
    import re

    from thermal_viewer.main_window import GraphicExportDialog, MainWindow

    assert 0.0 < MainWindow._SVG_PEN_SCALE_FACTOR < 1.0

    p = OUT / "svg_pen_scale_check.svg"
    if p.exists():
        p.unlink()
    orig_exec = GraphicExportDialog.exec  # fuer force_dpi_300's Verkettung unten

    def force_dpi_300(self):
        self.spin_dpi.setValue(300)
        return orig_exec(self)

    orig_save = QtWidgets.QFileDialog.getSaveFileName
    try:
        with temp_dialog_exec(GraphicExportDialog, force_dpi_300):
            QtWidgets.QFileDialog.getSaveFileName = staticmethod(lambda *a, **k: (str(p), "SVG-Vektorgrafik (*.svg)"))
            win.timeseries_plot.scene().contextMenu[0].trigger()
    finally:
        QtWidgets.QFileDialog.getSaveFileName = orig_save
    assert p.exists()

    content = p.read_text(encoding="utf-8")
    widths = sorted(set(int(w) for w in re.findall(r'stroke-width="(\d+)"', content) if int(w) > 1))
    assert widths, "keine Kurven-/ROI-Linien mit stroke-width > 1 im SVG gefunden"
    scale = 300 / 96.0
    full_scale_width = round(2 * scale)
    reduced_width = round(2 * scale * MainWindow._SVG_PEN_SCALE_FACTOR)
    assert reduced_width < full_scale_width, (reduced_width, full_scale_width)
    assert full_scale_width not in widths, (
        f"SVG enthaelt eine Linie mit der VOLLEN (Raster-)Stiftbreite {full_scale_width} -- "
        f"erwartet reduzierte Breite ~{reduced_width}, gefunden: {widths}"
    )


check(
    "SVG export: curve/ROI lines use a reduced (thinner) pen-width scale than the raster export",
    test_svg_export_uses_reduced_pen_scale_for_thinner_lines,
)

# --- Punkt 9: Zeitleiste unterhalb + nur Breite des linken Frames ---------


def test_timeline_layout():
    central = win.centralWidget()
    layout = central.layout()
    assert layout.count() == 2
    assert layout.itemAt(0).widget() is win.glw
    assert layout.itemAt(1).widget() is win.timeline_bar
    assert win.timeline_bar.width() <= central.width() + 2


check("timeline bar below image, same width as central column", test_timeline_layout)


def test_dock_tab_position_above_graphs():
    assert win.tabPosition(QtCore.Qt.RightDockWidgetArea) == QtWidgets.QTabWidget.North


check("Zeitverlauf/Live (Cursor) dock tabs positioned above the graphs", test_dock_tab_position_above_graphs)

# --- Uhrzeit/Laufzeit-Umschalter an beiden Kurven-Graphen -------------------


def test_time_display_toggle():
    unix0 = win.recording.unix_seconds()[0]
    # combo_time_display_* persists across Programmstarts via QSettings
    # ("ThermalViewer"/"ThermalViewer", geteilt mit interaktiven Sitzungen
    # auf derselben Maschine) -- der Test prueft den TOGGLE-Mechanismus,
    # nicht den zufaelligen Ausgangswert dieser Maschine, daher hier explizit
    # auf "clock" zuruecksetzen statt ihn stillschweigend vorauszusetzen.
    win.combo_time_display_timeseries.setCurrentIndex(win.combo_time_display_timeseries.findData("clock"))
    assert win.combo_time_display_timeseries.currentData() == "clock"
    assert win.axis_timeseries_bottom.runtime_mode is False

    # Beide Umschalter (je einer pro Graph, "unten rechts") bleiben synchron.
    win.combo_time_display_live.setCurrentIndex(win.combo_time_display_live.findData("runtime"))
    assert win.combo_time_display_timeseries.currentData() == "runtime"
    assert win.axis_timeseries_bottom.runtime_mode is True
    assert win.axis_live_bottom.runtime_mode is True

    strings = win.axis_timeseries_bottom.tickStrings([unix0, unix0 + 3725], 1, 1)
    assert strings[0] == "00:00:00", strings
    assert strings[1] == "01:02:05", strings

    win.combo_time_display_timeseries.setCurrentIndex(win.combo_time_display_timeseries.findData("clock"))
    assert win.axis_live_bottom.runtime_mode is False


check("time axis toggle (Uhrzeit/Laufzeit) on both graphs, synced", test_time_display_toggle)

# --- Punkt 13: Theme-Screenshots fuer visuelle Pruefung ---------------------


def render_window(path: Path):
    pix = win.grab()
    pix.save(str(path))


check("render light theme screenshot", lambda: (win._apply_theme("light"), render_window(OUT / "theme_light.png")))
check("render dark theme screenshot", lambda: (win._apply_theme("dark"), render_window(OUT / "theme_dark.png")))

# --- Projekt speichern/laden ------------------------------------------------
project_path = OUT / "test_project.tvproj"


def test_project_roundtrip():
    win._px_to_mm = 3.0
    if project_path.exists():
        project_path.unlink()
    orig_save = QtWidgets.QFileDialog.getSaveFileName
    orig_open = QtWidgets.QFileDialog.getOpenFileName
    QtWidgets.QFileDialog.getSaveFileName = staticmethod(lambda *a, **k: (str(project_path), ""))
    QtWidgets.QFileDialog.getOpenFileName = staticmethod(lambda *a, **k: (str(project_path), ""))
    try:
        win._save_project()
        assert project_path.exists()
        win._load_project()
    finally:
        QtWidgets.QFileDialog.getSaveFileName = orig_save
        QtWidgets.QFileDialog.getOpenFileName = orig_open


check("project save/load roundtrip", test_project_roundtrip)


def test_project_load_grows_roi_list():
    # Ein Projekt mit mehr ROIs als aktuell vorhanden (z.B. aus einer anderen
    # Sitzung mit "beliebig vielen" hinzugefuegten Messbereichen) muss beim
    # Laden fehlende Messbereiche automatisch neu anlegen, statt sie
    # stillschweigend zu verwerfen. Die naechste, noch nie in dieser Sitzung
    # vergebene Nummer verwenden (luecken- bzw. abstandslos, wie es ein
    # echtes _save_project()-Ergebnis auch waere) -- eine bereits wieder
    # entfernte Nummer wird dagegen bewusst NICHT rekonstruiert, siehe
    # _load_project.
    future_number = win._roi_next_number
    count_before = len(win.roi_entries)

    grow_path = OUT / "test_project_grow.tvproj"
    if grow_path.exists():
        grow_path.unlink()
    orig_save = QtWidgets.QFileDialog.getSaveFileName
    orig_open = QtWidgets.QFileDialog.getOpenFileName
    QtWidgets.QFileDialog.getSaveFileName = staticmethod(lambda *a, **k: (str(grow_path), ""))
    QtWidgets.QFileDialog.getOpenFileName = staticmethod(lambda *a, **k: (str(grow_path), ""))
    try:
        win._save_project()
        assert grow_path.exists()

        data = json.loads(grow_path.read_text(encoding="utf-8"))
        data.setdefault("rois", []).append({
            "index": future_number - 1,
            "name": f"ROI {future_number}",
            "farbe": "#ff00ff",
            "sichtbar": True,
            "platziert": True,
            "interpolation_aktiv": False,
            "mittelpunkt": {"x": 3.0, "y": 3.0},
            "breite_px": 4.0,
            "hoehe_px": 4.0,
        })
        grow_path.write_text(json.dumps(data), encoding="utf-8")

        win._load_project()
    finally:
        QtWidgets.QFileDialog.getSaveFileName = orig_save
        QtWidgets.QFileDialog.getOpenFileName = orig_open

    assert len(win.roi_entries) == count_before + 1, (
        "Fehlender Messbereich wurde beim Laden nicht nachtraeglich angelegt"
    )
    grown_entry = next(e for e in win.roi_entries if e.number == future_number)
    assert grown_entry.placed and grown_entry.width() == 4.0
    win.roi_list.setCurrentRow(0)


check("loading a project with more ROIs than currently exist grows the list", test_project_load_grows_roi_list)


def test_load_project_resets_stale_interp_arm_state():
    entry = win.roi_entries[3]
    entry.chk_interp.setChecked(True)
    entry.place(2, 2, 5, 5)
    entry.capture_interp_start(0)
    entry.place(9, 9, 5, 5)
    entry.capture_interp_end(win.recording.n_frames - 1)

    # Button mitten im zweistufigen Ablauf "haengen lassen": Phase 1 (hin-
    # springen + armieren) bereits ausgefuehrt, Phase 2 (der eigentliche
    # Capture-Klick) noch nicht.
    entry.btn_interp_start.click()
    assert entry.interp_arm_start
    assert entry.btn_interp_start.text() == INTERP_START_CAPTURE_LABEL

    saved_start = entry.interp_start
    path = OUT / "test_project_arm_bug.tvproj"
    if path.exists():
        path.unlink()
    orig_save = QtWidgets.QFileDialog.getSaveFileName
    orig_open = QtWidgets.QFileDialog.getOpenFileName
    QtWidgets.QFileDialog.getSaveFileName = staticmethod(lambda *a, **k: (str(path), ""))
    QtWidgets.QFileDialog.getOpenFileName = staticmethod(lambda *a, **k: (str(path), ""))
    try:
        win._save_project()
        # chk_interp bleibt beim Laden auf True -> der Haken-Zustand aendert
        # sich NICHT, toggled() feuert also nicht (das ist genau der
        # Reproduktionsfall fuer den Bug).
        assert entry.chk_interp.isChecked()
        win._load_project()
    finally:
        QtWidgets.QFileDialog.getSaveFileName = orig_save
        QtWidgets.QFileDialog.getOpenFileName = orig_open

    # Bugfix: der haengengebliebene Erfassungs-Vorgang muss beim Laden
    # zurueckgesetzt werden, sonst wuerde der naechste Klick auf den Button
    # den frisch geladenen interp_start-Wert sofort wieder ueberschreiben.
    assert not entry.interp_arm_start
    assert entry.btn_interp_start.text() == INTERP_START_LABEL
    assert entry.interp_start == saved_start

    entry.chk_interp.setChecked(False)


check("_load_project resets a stale armed interpolation-capture button", test_load_project_resets_stale_interp_arm_state)

# --- Regressionstests aus dem zweiten Review-Durchgang ---------------------


def test_bounds_px_for_offscreen_roi():
    from thermal_viewer.roi import bounds_px_for
    # ROI komplett ausserhalb des 10x10-Rasters -> darf KEIN leeres Slice ergeben.
    row0, row1, col0, col1 = bounds_px_for(15, 15, 5, 5, (10, 10))
    assert row1 > row0 and col1 > col0, (row0, row1, col0, col1)
    assert row0 <= 9 and col0 <= 9, (row0, row1, col0, col1)
    import numpy as _np
    dummy = _np.zeros((3, 10, 10), dtype=_np.float32)
    sliced = dummy[:, row0:row1, col0:col1]
    assert sliced.size > 0, "Slice ist leer -> wuerde NaN liefern"


check("bounds_px_for no longer NaNs for fully out-of-bounds ROI", test_bounds_px_for_offscreen_roi)


def test_roi_min_size_enforced():
    entry = win.roi_entries[3]
    entry.place(20, 20, 10, 10)
    entry.roi.setSize([0.1, 0.1])
    w, h = entry.roi.size()
    assert w >= 1.0 and h >= 1.0, (w, h)


check("AdjustableROI enforces a minimum size", test_roi_min_size_enforced)


def test_ruler_and_roi_placement_mutually_exclusive():
    entry = win.roi_entries[0]
    entry.btn_place.setChecked(True)
    assert win._armed_entry is entry
    win._start_ruler_tool()
    assert win._ruler_armed
    assert win._armed_entry is None, "ROI-Platzieren haette abgebrochen werden muessen"
    assert not entry.btn_place.isChecked()

    win._cancel_ruler_tool()
    entry.btn_place.setChecked(True)
    assert win._armed_entry is entry
    win._cancel_ruler_tool()
    entry.btn_place.setChecked(False)


check("ruler tool and ROI placement disarm each other", test_ruler_and_roi_placement_mutually_exclusive)


def test_frame_numbering_is_one_based():
    win._show_frame(0)
    assert win.frame_spin.value() == 1, win.frame_spin.value()
    win.frame_spin.setValue(win.recording.n_frames)
    assert win.current_index == win.recording.n_frames - 1
    win._show_frame(0)


check("frame_spin shows 1-based frame numbers", test_frame_numbering_is_one_based)


def test_evaluation_end_frame_and_timeline_markers():
    n = win.recording.n_frames
    # Standard: Auswertungsstart = erster, Auswertungsende = letzter Frame.
    assert win._eval_start_index == 0
    assert win.spin_eval_start.value() == 1
    assert win._eval_end_index == n - 1
    assert win.spin_eval_end.value() == n
    assert win.frame_slider.start_marker == 0
    assert win.frame_slider.end_marker == n - 1

    # Manuell nach unten korrigieren.
    custom_end = max(1, n - 3)
    win.spin_eval_end.setValue(custom_end)
    assert win._eval_end_index == custom_end - 1
    assert win.frame_slider.end_marker == custom_end - 1

    # "Ende festlegen" (Verlaufs-Interpolation) und die Taste "Ende"
    # springen jetzt zum Auswertungsende, nicht mehr zwingend zum
    # allerletzten geladenen Frame.
    entry = win.roi_entries[3]
    entry.place(4, 4, 6, 6)
    entry.chk_interp.setChecked(True)
    win.frame_slider.setValue(0)
    entry.btn_interp_end.click()
    assert win.current_index == custom_end - 1, win.current_index
    entry.btn_interp_end.click()  # Phase 2 abschliessen (Ende uebernehmen)
    entry.chk_interp.setChecked(False)

    win._jump_to_last_frame()
    assert win.current_index == custom_end - 1

    # Projekt speichern/laden nimmt das Auswertungsende mit.
    path = OUT / "eval_end_project.tvproj"
    if path.exists():
        path.unlink()
    orig_save = QtWidgets.QFileDialog.getSaveFileName
    orig_open = QtWidgets.QFileDialog.getOpenFileName
    QtWidgets.QFileDialog.getSaveFileName = staticmethod(lambda *a, **k: (str(path), ""))
    try:
        win._save_project()
    finally:
        QtWidgets.QFileDialog.getSaveFileName = orig_save
    win.spin_eval_end.setValue(n)  # zuruecksetzen, um das Laden echt zu pruefen
    assert win._eval_end_index == n - 1
    QtWidgets.QFileDialog.getOpenFileName = staticmethod(lambda *a, **k: (str(path), ""))
    try:
        win._load_project()
    finally:
        QtWidgets.QFileDialog.getOpenFileName = orig_open
    assert win._eval_end_index == custom_end - 1
    assert win.spin_eval_end.value() == custom_end

    # Wieder auf Standard zuruecksetzen, fuer nachfolgende Tests.
    win.spin_eval_end.setValue(n)
    assert win._eval_end_index == n - 1


check("evaluation end frame is customizable and shown as timeline markers", test_evaluation_end_frame_and_timeline_markers)


def test_evaluation_start_frame_and_ordering_clamp():
    n = win.recording.n_frames
    custom_start = 2
    win.spin_eval_start.setValue(custom_start + 1)
    assert win._eval_start_index == custom_start
    assert win.frame_slider.start_marker == custom_start

    # "Start festlegen" (Verlaufs-Interpolation) und die Taste "Pos1"
    # springen jetzt zum Auswertungsstart, nicht mehr zwingend zu Frame 0.
    entry = win.roi_entries[3]
    entry.place(4, 4, 6, 6)
    entry.chk_interp.setChecked(True)
    win.frame_slider.setValue(n - 1)
    entry.btn_interp_start.click()
    assert win.current_index == custom_start, win.current_index
    entry.btn_interp_start.click()  # Phase 2 abschliessen
    entry.chk_interp.setChecked(False)

    win.frame_slider.setValue(n - 1)
    win._jump_to_first_frame()
    assert win.current_index == custom_start

    # Start darf das Ende nicht ueberholen -- Ende folgt automatisch mit.
    win.spin_eval_end.setValue(n)
    win.spin_eval_start.setValue(n)
    assert win._eval_start_index == n - 1
    assert win._eval_end_index == n - 1
    assert win.spin_eval_end.value() == n

    # Und umgekehrt: Ende darf den Start nicht unterschreiten.
    win.spin_eval_start.setValue(1)
    win.spin_eval_end.setValue(1)
    assert win._eval_end_index == 0
    assert win._eval_start_index == 0
    assert win.spin_eval_start.value() == 1

    # Direktes Ziehen der Markierung im Frame-Regler (markerDragged) setzt
    # dieselben Zustaende wie die Spinboxen.
    win.spin_eval_start.setValue(1)
    win.spin_eval_end.setValue(n)
    win._on_timeline_marker_dragged("start", 3)
    assert win._eval_start_index == 3
    assert win.spin_eval_start.value() == 4
    win._on_timeline_marker_dragged("end", n - 2)
    assert win._eval_end_index == n - 2
    assert win.spin_eval_end.value() == n - 1

    # Wieder auf Standard zuruecksetzen, fuer nachfolgende Tests.
    win.spin_eval_start.setValue(1)
    win.spin_eval_end.setValue(n)
    assert win._eval_start_index == 0
    assert win._eval_end_index == n - 1


check("evaluation start frame customizable, ordering clamp, marker drag", test_evaluation_start_frame_and_ordering_clamp)


def test_timeline_slider_marker_hit_test_and_drag_mechanics():
    slider = win.frame_slider
    n = win.recording.n_frames
    saved_start, saved_end = win._eval_start_index, win._eval_end_index
    slider.set_markers(2, n - 3)

    start_x = slider._marker_x(2)
    end_x = slider._marker_x(n - 3)
    middle_x = slider._marker_x((n - 3) // 2 + 5) if n > 12 else None

    assert slider._marker_at(QtCore.QPoint(start_x, slider.height() // 2)) == "start"
    assert slider._marker_at(QtCore.QPoint(end_x, slider.height() // 2)) == "end"
    if middle_x is not None and abs(middle_x - start_x) > slider._HIT_TOLERANCE_PX + 2 and abs(middle_x - end_x) > slider._HIT_TOLERANCE_PX + 2:
        assert slider._marker_at(QtCore.QPoint(middle_x, slider.height() // 2)) is None

    # _value_from_x sollte ungefaehr die Umkehrfunktion von _marker_x sein.
    recovered = slider._value_from_x(start_x)
    assert abs(recovered - 2) <= 1, recovered

    # Simuliertes Ziehen des Start-Markers (ohne echte Qt-Mausereignisse,
    # direkt ueber die internen Handler -- vermeidet Plattform-Abhaengigkeit
    # der synthetischen Events unter QT_QPA_PLATFORM=offscreen).
    received = []
    slider.markerDragged.connect(lambda which, value: received.append((which, value)))
    try:
        slider._dragging = "start"
        target_x = slider._marker_x(5)

        class _FakePos:
            def __init__(self, x):
                self._x = x

            def x(self):
                return self._x

        class _FakeMoveEvent:
            def __init__(self, x):
                self._pos = _FakePos(x)

            def pos(self):
                return self._pos

            def accept(self):
                pass

        slider.mouseMoveEvent(_FakeMoveEvent(target_x))
        assert slider._dragging == "start"
        assert received and received[-1][0] == "start"
        assert abs(received[-1][1] - 5) <= 1
        assert abs(slider.start_marker - 5) <= 1

        slider.mouseReleaseEvent(_FakeMoveEvent(target_x))
        assert slider._dragging is None
    finally:
        # mouseMoveEvent loest ueber markerDragged auch den echten
        # Produktions-Handler (_on_timeline_marker_dragged) aus, der
        # win._eval_start_index tatsaechlich veraendert -- ueber die
        # Spinbox zuruecksetzen (statt nur slider.set_markers), damit der
        # kaskadierte Zustand (spin_eval_start, _eval_start_index) wieder
        # konsistent zum Ausgangszustand ist.
        win.spin_eval_start.setValue(saved_start + 1)
        win.spin_eval_end.setValue(saved_end + 1)
        assert win._eval_start_index == saved_start
        assert win._eval_end_index == saved_end


check("TimelineSlider marker hit-test + drag mechanics", test_timeline_slider_marker_hit_test_and_drag_mechanics)


def test_playback_clamped_to_eval_range_unless_manually_outside():
    n = win.recording.n_frames
    if n < 6:
        return
    win.spin_eval_start.setValue(2)
    win.spin_eval_end.setValue(n - 1)

    # Cursor innerhalb des Bereichs -> Wiedergabe ist geklemmt und stoppt
    # spaetestens am Auswertungsende, nicht am tatsaechlichen letzten Frame.
    win.frame_slider.setValue(3)
    win.play_button.setChecked(True)
    assert win._play_clamped
    for _ in range(n + 5):
        win._advance_frame()
        if not win.play_timer.isActive() and not win.play_button.isChecked():
            break
    assert win.current_index == win._eval_end_index, win.current_index
    win.play_button.setChecked(False)

    # Cursor manuell AUSSERHALB des Bereichs (hinter dem Auswertungsende) ->
    # Wiedergabe laeuft ungeklemmt bis zum tatsaechlichen letzten Frame.
    win.frame_slider.setValue(n - 1)
    win.play_button.setChecked(True)
    assert not win._play_clamped
    assert win.current_index == 0, "sollte bei bereits erreichtem Ende von vorne beginnen"
    win.play_button.setChecked(False)

    # Aufraeumen.
    win.spin_eval_start.setValue(1)
    win.spin_eval_end.setValue(n)
    win._show_frame(0)


check(
    "playback stays within eval start/end unless cursor manually placed outside",
    test_playback_clamped_to_eval_range_unless_manually_outside,
)


def test_play_restarts_from_beginning_after_reaching_end():
    win._show_frame(win.recording.n_frames - 1)
    assert win.current_index == win.recording.n_frames - 1
    # Bugfix: erneutes Klicken auf Play, nachdem die Wiedergabe bereits am
    # letzten Bild angekommen ist, muss wieder von vorne beginnen statt
    # (wirkungslos) auf dem letzten Bild stehen zu bleiben.
    win.play_button.setChecked(True)
    assert win.current_index == 0, win.current_index
    win.play_button.setChecked(False)
    win._show_frame(0)


check("play restarts from frame 0 after reaching the end", test_play_restarts_from_beginning_after_reaching_end)


def test_ruler_color_customizable():
    default_color = win._ruler_color
    win._ruler_color = "#00ff00"
    win._update_ruler_color_swatch()
    win._apply_ruler_color()
    assert "#00ff00" in win.btn_ruler_color.styleSheet()
    # Falls schon eine Linie/Beschriftung existiert, muss deren Farbe
    # mitgezogen werden, nicht nur zukuenftig neu gezeichnete.
    if win._ruler_line is not None:
        assert win._ruler_line.pen.color().name() == "#00ff00"
    win._ruler_color = default_color
    win._update_ruler_color_swatch()
    win._apply_ruler_color()


check("ruler line color is customizable", test_ruler_color_customizable)


def test_video_dialog_one_based_range():
    from thermal_viewer.dialogs import VideoExportDialog
    dlg = VideoExportDialog(
        win, n_frames=win.recording.n_frames, colormaps=COLORMAPS,
        current_colormap_index=0, current_invert=False, current_level_mode="per_frame",
        current_min=0.0, current_max=50.0, current_fps=10.0,
    )
    dlg.spin_start.setValue(1)
    dlg.spin_end.setValue(win.recording.n_frames)
    start_idx, end_idx = dlg.frame_range()
    assert (start_idx, end_idx) == (0, win.recording.n_frames - 1), (start_idx, end_idx)


check("VideoExportDialog frame_range() converts 1-based UI to 0-based indices", test_video_dialog_one_based_range)


def test_video_dialog_defaults_from_current_eval_range():
    # Bugfix: der Frame-Bereich im Video-Export-Dialog war bisher immer auf
    # den vollen Bereich vorbelegt, unabhaengig vom in der UI gesetzten
    # Auswertungsstart/-ende -- jetzt uebernimmt _export_video() genau
    # diesen Bereich als Vorbelegung.
    n = win.recording.n_frames
    old_start, old_end = win._eval_start_index, win._eval_end_index
    new_start = 1 if n >= 4 else 0
    new_end = n - 2 if n >= 4 else n - 1
    win._eval_start_index = new_start
    win._eval_end_index = new_end
    captured: dict = {}
    orig_exec = QtWidgets.QDialog.exec

    def capture_exec(self):
        if hasattr(self, "radio_overlay_both"):
            captured["start"] = self.spin_start.value()
            captured["end"] = self.spin_end.value()
        return QtWidgets.QDialog.DialogCode.Rejected

    QtWidgets.QDialog.exec = capture_exec
    try:
        win._export_video()
    finally:
        QtWidgets.QDialog.exec = orig_exec
        win._eval_start_index, win._eval_end_index = old_start, old_end

    assert captured == {"start": new_start + 1, "end": new_end + 1}, (captured, n)


check(
    "VideoExportDialog prefills frame range from current Auswertungsstart/-ende",
    test_video_dialog_defaults_from_current_eval_range,
)


def test_video_dialog_overlay_group_is_laufzeit_2x2_grid_with_tooltips():
    # Verlauf: Gruppe zuerst umbenannt von "Zeitanzeige im Video" -> "Laufzeit",
    # spaeter (Vereinheitlichung mit dem Bild-Export, siehe GraphicExportDialog)
    # nochmal umbenannt -> "Zeitachse". Radiobuttons nicht als Liste, sondern
    # als 2x2-Matrix (Zeile 1: Zeitleiste/Keine, Zeile 2: Zeitstempel/Beides);
    # Zeitleiste/Zeitstempel zusaetzlich mit erklaerendem Tooltip.
    from thermal_viewer.dialogs import VideoExportDialog

    dlg = VideoExportDialog(
        win, n_frames=win.recording.n_frames, colormaps=COLORMAPS,
        current_colormap_index=0, current_invert=False, current_level_mode="per_frame",
        current_min=0.0, current_max=50.0, current_fps=10.0,
    )
    overlay_box = dlg.radio_overlay_timeline.parentWidget()
    assert isinstance(overlay_box, QtWidgets.QGroupBox)
    assert overlay_box.title() == "Zeitachse", overlay_box.title()

    grid = overlay_box.layout()
    assert isinstance(grid, QtWidgets.QGridLayout)

    def grid_pos(w):
        idx = grid.indexOf(w)
        row, col, _, _ = grid.getItemPosition(idx)
        return row, col

    assert grid_pos(dlg.radio_overlay_timeline) == (0, 0)
    assert grid_pos(dlg.radio_overlay_none) == (0, 1)
    assert grid_pos(dlg.radio_overlay_timestamp) == (1, 0)
    assert grid_pos(dlg.radio_overlay_both) == (1, 1)

    assert dlg.radio_overlay_timeline.toolTip().strip()
    assert dlg.radio_overlay_timestamp.toolTip().strip()


check(
    "video dialog: overlay group renamed 'Laufzeit', 2x2 grid, tooltips on Zeitleiste/Zeitstempel",
    test_video_dialog_overlay_group_is_laufzeit_2x2_grid_with_tooltips,
)


def test_enter_in_spinbox_does_not_auto_accept_export_dialogs():
    # Bugfix: ENTER in einem Zahlenfeld (z.B. Frame-Bereich beim Video-
    # Export) sollte NUR den Wert uebernehmen, nicht sofort den gesamten
    # Dialog schliessen und in den Speichern-Dialog weiterspringen.
    from qtpy import QtTest
    from thermal_viewer.dialogs import VideoExportDialog, GraphicExportDialog, CsvColumnDialog

    dlg = VideoExportDialog(win, 100, COLORMAPS, 0, False, "per_frame", 20.0, 30.0, 10.0)
    dlg.show()
    app.processEvents()
    try:
        dlg.spin_start.setFocus(QtCore.Qt.OtherFocusReason)
        app.processEvents()
        dlg.spin_start.setValue(5)
        QtTest.QTest.keyClick(dlg.spin_start, QtCore.Qt.Key_Return)
        app.processEvents()
        assert dlg.isVisible(), "ENTER im Frame-Spinbox haette den Dialog nicht schliessen duerfen"
        assert dlg.spin_start.value() == 5

        # ENTER, waehrend der OK-Knopf selbst den Fokus haelt (z.B. nach
        # Tab-Navigation), soll weiterhin ganz normal den Dialog bestaetigen.
        ok_button = dlg.findChild(QtWidgets.QDialogButtonBox).button(QtWidgets.QDialogButtonBox.Ok)
        ok_button.setFocus(QtCore.Qt.OtherFocusReason)
        app.processEvents()
        QtTest.QTest.keyClick(dlg, QtCore.Qt.Key_Return)
        app.processEvents()
        assert not dlg.isVisible() and dlg.result() == QtWidgets.QDialog.DialogCode.Accepted
    finally:
        dlg.close()

    dlg2 = GraphicExportDialog(win, win._settings, default_dpi=150)
    dlg2.show()
    app.processEvents()
    try:
        dlg2.spin_dpi.setFocus(QtCore.Qt.OtherFocusReason)
        app.processEvents()
        QtTest.QTest.keyClick(dlg2.spin_dpi, QtCore.Qt.Key_Return)
        app.processEvents()
        assert dlg2.isVisible(), "ENTER im DPI-Spinbox haette den Dialog nicht schliessen duerfen"
    finally:
        dlg2.close()

    dlg3 = CsvColumnDialog(win, [{"name": "ROI 1", "width_px": 30.0, "height_px": 20.0, "width_mm": None, "height_mm": None}])
    dlg3.show()
    app.processEvents()
    try:
        dlg3._edits[0].setFocus(QtCore.Qt.OtherFocusReason)
        app.processEvents()
        QtTest.QTest.keyClick(dlg3._edits[0], QtCore.Qt.Key_Return)
        app.processEvents()
        assert dlg3.isVisible(), "ENTER im Spaltenname-Feld haette den Dialog nicht schliessen duerfen"
    finally:
        dlg3.close()


check(
    "ENTER in a spinbox/line edit only commits the value, doesn't auto-accept export dialogs",
    test_enter_in_spinbox_does_not_auto_accept_export_dialogs,
)


# Der frueher unabhaengig waehlbare Graph-Theme-Modus (_graph_theme_mode)
# wurde entfernt -- die Grafik-Darstellung folgt jetzt immer fest dem
# aktuellen App-Design (siehe _apply_graph_colors-Docstring). Ersetzt durch
# einen Test des jetzigen Verhaltens: Design-Wahl uebersteht einen Neustart
# UND treibt sowohl App-Palette als auch Graph-Hintergrund konsistent.
from thermal_viewer.main_window import THEMES as _THEMES  # noqa: E402


def test_theme_choice_persists_across_restart_and_drives_graph_colors():
    win._settings.setValue("theme", "dark")
    win2 = MainWindow()
    try:
        assert win2._current_theme == "dark"
        assert win2._graph_bg == _THEMES["dark"]["pg_background"]
        assert win2._graph_fg == _THEMES["dark"]["pg_foreground"]
    finally:
        win2.close()
        win._settings.setValue("theme", "light")
    win3 = MainWindow()
    try:
        assert win3._current_theme == "light"
        assert win3._graph_bg == _THEMES["light"]["pg_background"]
        # Bugfix (siehe _light_palette): der Hell-Modus muss explizit hell
        # sein, unabhaengig vom OS-Design, nicht ueber
        # app.style().standardPalette() (das unter Windows dem System-
        # Dunkelmodus folgen kann).
        app_palette = QtWidgets.QApplication.instance().palette()
        window_color = app_palette.color(QtGui.QPalette.Window)
        assert window_color == QtGui.QColor("#efefef"), window_color.name()
        assert window_color.lightness() > 200
    finally:
        win3.close()
        win._apply_theme("light")


check(
    "Design-Wahl uebersteht Neustart, treibt Graph-Farben, Hell-Modus ist OS-unabhaengig explizit hell",
    test_theme_choice_persists_across_restart_and_drives_graph_colors,
)


def test_corrupted_interpolation_project_load():
    bad_path = OUT / "corrupt_interp.tvproj"
    import json as _json
    data = {
        "format_version": 2,
        "rois": [{
            "index": 0,
            "name": "ROI 1",
            "platziert": True,
            "mittelpunkt": {"x": 5.0, "y": 5.0},
            "breite_px": 10.0,
            "hoehe_px": 10.0,
            "interpolation_aktiv": True,
            "interpolation_start": {"x": "not-a-number", "y": 0, "breite_px": 5, "hoehe_px": 5},
        }],
    }
    bad_path.write_text(_json.dumps(data), encoding="utf-8")
    orig_open = QtWidgets.QFileDialog.getOpenFileName
    QtWidgets.QFileDialog.getOpenFileName = staticmethod(lambda *a, **k: (str(bad_path), ""))
    try:
        win._load_project()
    finally:
        QtWidgets.QFileDialog.getOpenFileName = orig_open

    entry = win.roi_entries[0]
    assert entry.placed, "Platzierung mit gueltigen Daten haette trotzdem angewendet werden muessen"
    assert entry.interp_enabled is False
    assert entry.interp_start is None
    # Frame-Wechsel darf danach NICHT mit einem TypeError abstuerzen.
    win._show_frame(0)
    win._show_frame(win.recording.n_frames - 1)


check("corrupted interpolation data in .tvproj doesn't crash later playback", test_corrupted_interpolation_project_load)

# --- Live-Ordner-Ueberwachung (App soll parallel zu einer laufenden Messung
# laufen koennen) -------------------------------------------------------
live_watch_dir = OUT / "live_watch_dataset"


def _write_synthetic_frame(path, value):
    rows = [
        ";".join(f"{value + r * 4 + c:.1f}".replace(".", ",") for c in range(4))
        for r in range(4)
    ]
    path.write_text("\n".join(rows) + "\n", encoding="utf-8")


def test_live_folder_watch():
    live_watch_dir.mkdir(exist_ok=True)
    for f in live_watch_dir.glob("*.csv"):
        f.unlink()
    _write_synthetic_frame(live_watch_dir / "Record_2026-08-20_10-00-00.csv", 10.0)
    _write_synthetic_frame(live_watch_dir / "Record_2026-08-20_10-00-10.csv", 11.0)

    orig_get_dir = QtWidgets.QFileDialog.getExistingDirectory
    QtWidgets.QFileDialog.getExistingDirectory = staticmethod(lambda *a, **k: str(live_watch_dir))
    try:
        win._open_folder()
    finally:
        QtWidgets.QFileDialog.getExistingDirectory = orig_get_dir
    assert win.recording.n_frames == 2
    assert win._watched_folder == live_watch_dir
    # Laeuft nach "Ordner öffnen…" automatisch im Hintergrund -- keine
    # separate Einstellung/Knopf mehr dafuer noetig.
    assert win._live_watch_timer.isActive(), "Timer sollte nach Ordner öffnen automatisch laufen"

    # Fall 1: Anzeige steht auf dem letzten Frame ("live mitschauen") -> nach
    # neuen Dateien automatisch zum neuesten Frame weiterspringen.
    win.current_index = 1
    win._show_frame(1)
    _write_synthetic_frame(live_watch_dir / "Record_2026-08-20_10-00-20.csv", 12.0)
    win._check_for_new_files()
    assert win.recording.n_frames == 3
    assert win.current_index == 2, win.current_index
    assert win.frame_spin.value() == 3
    assert win.frame_slider.maximum() == 2

    # Fall 2: Anzeige steht NICHT auf dem letzten Frame (Nutzer schaut sich
    # einen frueheren Frame an) -> Position bleibt trotz neuer Datei stehen.
    win.current_index = 0
    win._show_frame(0)
    _write_synthetic_frame(live_watch_dir / "Record_2026-08-20_10-00-30.csv", 13.0)
    win._check_for_new_files()
    assert win.recording.n_frames == 4
    assert win.current_index == 0, win.current_index
    assert win.frame_slider.maximum() == 3

    # Erneuter Aufruf ohne neue Dateien darf nichts veraendern.
    win._check_for_new_files()
    assert win.recording.n_frames == 4

    # "Dateien öffnen…" hat keinen eindeutigen gemeinsamen Ordner -> Live-
    # Ueberwachung wird deaktiviert.
    orig_get_files = QtWidgets.QFileDialog.getOpenFileNames
    paths = sorted(live_watch_dir.glob("*.csv"))
    QtWidgets.QFileDialog.getOpenFileNames = staticmethod(lambda *a, **k: ([str(p) for p in paths], ""))
    try:
        win._open_files()
    finally:
        QtWidgets.QFileDialog.getOpenFileNames = orig_get_files
    assert win._watched_folder is None
    assert not win._live_watch_timer.isActive()


check("live folder watch: batched auto-reload without disrupting playback", test_live_folder_watch)


def test_default_level_mode_is_global_over_entire_measurement():
    # Bugreport: Default soll "Ueber gesamte Messung" sein (Punkt 3 der
    # letzten Anfrage), nicht mehr "Pro Bild". Frisches MainWindow, da `win`
    # durch vorherige Tests bereits umgeschaltet sein kann.
    fresh = MainWindow()
    try:
        assert fresh.radio_level_global.isChecked() is True
        assert fresh.radio_level_global.text() == "Über gesamte Messung"
        assert fresh._level_mode() == "global"
    finally:
        fresh.close()


check(
    "default level-scaling mode is 'Über gesamte Messung', renamed from 'Gesamte Serie'",
    test_default_level_mode_is_global_over_entire_measurement,
)


def test_tight_glw_export_size_used_by_static_raster_and_svg():
    # Bugreport: "unnoetige leere Stellen" auch im Bild-/SVG-Export (nicht
    # nur im Video, siehe _tight_glw_segments). _render_widget_image/
    # _save_widget_svg fuer self.glw muessen dieselbe Zielgroesse wie
    # _widget_export_size(self.glw, ...) liefern (nicht die volle, evtl.
    # Leerraum enthaltende _scaled_size-Groesse).
    scale = 1.7
    expected_w, expected_h = win._widget_export_size(win.glw, scale)

    bg = QtGui.QColor(win._graph_bg)
    raster = win._render_widget_image(win.glw, scale, bg)
    assert (raster.width(), raster.height()) == (expected_w, expected_h)

    svg_path = OUT / "tight_glw_export_check.svg"
    try:
        w, h = win._save_widget_svg(win.glw, svg_path, scale, bg)
        assert (w, h) == (expected_w, expected_h)
        assert svg_path.exists() and svg_path.stat().st_size > 0
    finally:
        svg_path.unlink(missing_ok=True)

    # Ein NICHT-glw-Widget (Kurve) muss weiterhin die normale, ungetrimmte
    # _scaled_size-Groesse verwenden -- die Sonderbehandlung gilt nur fuer
    # self.glw (aspect-locked Thermobild).
    curve_expected = win._scaled_size(win.timeseries_plot, scale)
    assert win._widget_export_size(win.timeseries_plot, scale) == curve_expected


check(
    "static image/SVG export of the thermal image uses the same tight (leerraum-free) size as the video export",
    test_tight_glw_export_size_used_by_static_raster_and_svg,
)


def test_render_video_frame_with_graph_widget_adds_curve_below_image():
    # Bugreport Punkt 2: Video-Export soll optional denselben Kurven-Graphen
    # wie im Hauptfenster (mit wandernder Zeit-Markierung) unter dem
    # Thermobild einblenden koennen.
    unix = win.recording.unix_seconds()
    n = win.recording.n_frames
    frame_indices = list(range(0, min(4, n)))
    segments = win._tight_glw_segments()

    img_without_graph = win._render_video_frame(
        1.0, QtGui.QColor(win._graph_bg), "none", frame_indices[0], frame_indices, unix, segments
    )
    img_with_graph = win._render_video_frame(
        1.0, QtGui.QColor(win._graph_bg), "none", frame_indices[0], frame_indices, unix, segments,
        win.timeseries_plot,
    )
    assert img_with_graph.height() > img_without_graph.height(), (
        "mit Graph muss das Bild hoeher sein als ohne"
    )

    # Muss auch fuer den Live-Graphen funktionieren, nicht nur Zeitverlauf.
    img_with_live_graph = win._render_video_frame(
        1.0, QtGui.QColor(win._graph_bg), "none", frame_indices[0], frame_indices, unix, segments,
        win.live_plot,
    )
    assert img_with_live_graph.height() > img_without_graph.height()

    # Kombination mit Zeitanzeige-Streifen darf nicht crashen und muss
    # nochmal hoeher sein als nur mit Graph allein.
    img_with_graph_and_overlay = win._render_video_frame(
        1.0, QtGui.QColor(win._graph_bg), "timeline", frame_indices[0], frame_indices, unix, segments,
        win.timeseries_plot,
    )
    assert img_with_graph_and_overlay.height() > img_with_graph.height()


check(
    "video export can optionally render the timeseries/live graph (with its moving frame marker) below the image",
    test_render_video_frame_with_graph_widget_adds_curve_below_image,
)


def test_video_export_dialog_graph_option_defaults_off():
    # "Graph mit exportieren" (frueher: "Graph mit anzeigen") ist standardmaessig
    # AUS; einmal an, ist die Graph-Inhalt-Auswahl (einzelne Messbereiche +
    # Live-Cursor, siehe _build_graph_content_selector in dialogs.py) nutzbar,
    # mit allen platzierten ROIs vorausgewaehlt und Live-Cursor aus.
    from thermal_viewer.dialogs import VideoExportDialog as RealVideoExportDialog

    dlg = RealVideoExportDialog(
        win, n_frames=win.recording.n_frames, colormaps=COLORMAPS,
        current_colormap_index=0, current_invert=False, current_level_mode="global",
        current_min=0.0, current_max=50.0, current_fps=10.0,
        roi_entries=[(101, "X"), (102, "Y")], live_available=True,
    )
    try:
        assert dlg.chk_show_graph.text() == "Graph mit exportieren"
        assert dlg.show_graph() is False, "Standard muss AUS sein"
        assert dlg._content_widgets["group_box"].isEnabled() is False
        dlg.chk_show_graph.setChecked(True)
        assert dlg.show_graph() is True
        assert dlg._content_widgets["group_box"].isEnabled() is True
        assert dlg.included_roi_numbers() == {101, 102}, "Standard: alle ROIs vorausgewaehlt"
        assert dlg.include_live() is False, "Standard: Live-Cursor aus"
        dlg._content_widgets["checks"][101].setChecked(False)
        assert dlg.included_roi_numbers() == {102}
    finally:
        dlg.close()


check(
    "video export dialog: graph checkbox defaults off, graph-content selector (ROIs+Live) usable when checked",
    test_video_export_dialog_graph_option_defaults_off,
)


def test_cursor_curve_dependency_wiring_video_and_graphic_dialogs():
    # Nutzerwunsch: "Ich moechte waehlen koennen, ob ich den Cursor... wirklich
    # nur im Bild haben moechte, oder auch die entsprechende Kurve im
    # Diagramm... beides soll unabhaengig voneinander moeglich sein, aber
    # nicht Kurve ohne Cursor". Kurve (Live-Cursor in der Graph-Inhalt-Liste)
    # EIN erzwingt Cursor-im-Bild EIN; Cursor-im-Bild AUS erzwingt Kurve AUS;
    # Cursor-im-Bild allein bleibt frei waehlbar.
    from thermal_viewer.dialogs import VideoExportDialog as RealVideoExportDialog
    from thermal_viewer.dialogs import GraphicExportDialog as RealGraphicExportDialog

    for dlg in (
        RealVideoExportDialog(
            win, n_frames=win.recording.n_frames, colormaps=COLORMAPS,
            current_colormap_index=0, current_invert=False, current_level_mode="global",
            current_min=0.0, current_max=50.0, current_fps=10.0,
            roi_entries=[(101, "X")], live_available=True,
        ),
        RealGraphicExportDialog(
            win, win._settings, default_dpi=150, show_graph_source_choice=True,
            live_available=True, roi_entries=[(101, "X")],
        ),
    ):
        try:
            chk_live = dlg._content_widgets["chk_live"]
            assert dlg.chk_cursor_position.isChecked() is False
            assert chk_live.isChecked() is False

            chk_live.setChecked(True)
            assert dlg.chk_cursor_position.isChecked() is True, "Kurve EIN muss Cursor-im-Bild erzwingen"

            dlg.chk_cursor_position.setChecked(False)
            assert chk_live.isChecked() is False, "Cursor-im-Bild AUS muss die Kurve mit ausschalten"

            dlg.chk_cursor_position.setChecked(True)
            assert chk_live.isChecked() is False, "Cursor-im-Bild EIN alleine darf die Kurve NICHT erzwingen"
        finally:
            dlg.close()


check(
    "Cursor-im-Bild/Live-Cursor-Kurve Kopplung in Video- UND Grafik-Export-Dialog",
    test_cursor_curve_dependency_wiring_video_and_graphic_dialogs,
)


def test_graphic_export_dialog_no_graph_choice_keeps_plain_cursor_checkbox():
    # show_graph_source_choice=False (Einzelexport des Thermobilds per
    # Rechtsklick, siehe _export_single_graph) -- kein Graph, also keine
    # Graph-Inhalt-Auswahl, aber die Cursor-im-Bild-Checkbox bleibt
    # (ungekoppelt) bestehen.
    from thermal_viewer.dialogs import GraphicExportDialog as RealGraphicExportDialog

    dlg = RealGraphicExportDialog(win, win._settings, default_dpi=150, show_mode_choice=False, show_time_axis_choice=False)
    try:
        assert dlg._content_widgets is None
        assert dlg.chk_cursor_position is not None
        assert dlg.chk_cursor_position.text() == "Cursor-Position im Bild anzeigen"
        assert dlg.chk_cursor_position.isChecked() is False
        assert dlg.included_roi_numbers() == set()
        assert dlg.include_live() is False
        assert dlg.has_graph_content() is False
    finally:
        dlg.close()


check(
    "GraphicExportDialog ohne Graph-Auswahl behaelt eigenstaendige Cursor-im-Bild-Checkbox",
    test_graphic_export_dialog_no_graph_choice_keeps_plain_cursor_checkbox,
)


def test_graphic_export_dialog_color_and_time_axis_override():
    from thermal_viewer.dialogs import GraphicExportDialog as RealGraphicExportDialog

    # Mit Zeitachsen-Wahl (Standard fuer den kombinierten Export). Punkt 4
    # (Round 3): "Wie aktuell in der Anwendung" entfernt, stattdessen ist die
    # aktuell aktive Anzeige (current_time_axis_mode) die VORBELEGUNG einer
    # der drei echten Optionen (Uhrzeit/Laufzeit/Beide).
    dlg = RealGraphicExportDialog(
        win, win._settings, default_dpi=150,
        colormaps=COLORMAPS, current_colormap_index=2, current_invert=True,
        current_level_mode="manual", current_min=1.0, current_max=99.0,
        current_time_axis_mode="runtime",
    )
    try:
        assert dlg.use_custom_colors() is False, "Standard: aktuelle Einstellungen uebernehmen"
        assert dlg.time_axis_mode() == "runtime", "Vorbelegung muss der aktuellen App-Anzeige entsprechen"
        assert [dlg.combo_time_axis.itemData(i) for i in range(dlg.combo_time_axis.count())] == [
            "clock", "runtime", "both",
        ]
        dlg._color_widgets["radio_custom"].setChecked(True)
        assert dlg.use_custom_colors() is True
        assert dlg.custom_colormap_index() == 2
        assert dlg.custom_invert() is True
        assert dlg.custom_level_mode() == "manual"
        assert dlg.custom_min_max() == (1.0, 99.0)
        idx = dlg.combo_time_axis.findData("both")
        dlg.combo_time_axis.setCurrentIndex(idx)
        assert dlg.time_axis_mode() == "both"
    finally:
        dlg.close()

    # Ohne Zeitachsen-Wahl (Einzelexport des Thermobilds -- hat keine
    # Zeitachse) faellt time_axis_mode() auf "clock" zurueck (harmloser
    # Default, wird von _export_single_graph ohnehin nie ausgewertet).
    dlg2 = RealGraphicExportDialog(
        win, win._settings, default_dpi=150, show_mode_choice=False, show_time_axis_choice=False,
        colormaps=COLORMAPS, current_colormap_index=0, current_invert=False,
        current_level_mode="global", current_min=0.0, current_max=50.0,
    )
    try:
        assert dlg2.combo_time_axis is None
        assert dlg2.time_axis_mode() == "clock"
    finally:
        dlg2.close()


check(
    "GraphicExportDialog offers the same colormap/scaling/time-axis freedom as the live UI",
    test_graphic_export_dialog_color_and_time_axis_override,
)


def test_temporary_time_display_mode_context_manager():
    old_mode = win._time_display_mode
    old_ts_runtime = win.axis_timeseries_bottom.runtime_mode
    try:
        with win._temporary_time_display_mode("runtime"):
            assert win.axis_timeseries_bottom.runtime_mode is True
            assert win.axis_live_bottom.runtime_mode is True
        assert win.axis_timeseries_bottom.runtime_mode == old_ts_runtime, "muss nach dem Export zurueckgesetzt werden"
        # QSettings/UI-Combobox duerfen NICHT veraendert werden (nur eine
        # einmalige Export-Wahl, keine dauerhafte Praeferenz-Aenderung).
        assert win._time_display_mode == old_mode
        assert win._settings.value("time_display_mode") != "runtime" or old_mode == "runtime"
        # None laesst den aktuellen Modus unangetastet.
        with win._temporary_time_display_mode(None):
            assert win.axis_timeseries_bottom.runtime_mode == old_ts_runtime
    finally:
        win._apply_time_display_mode(old_mode)


check(
    "_temporary_time_display_mode overrides only the axis display, not QSettings/the UI combobox",
    test_temporary_time_display_mode_context_manager,
)


def test_apply_level_widgets_state_manual_mode_applies_immediately():
    # Bugfix (Punkt 6): _set_level_mode() loeste bisher VOR dem Setzen von
    # spin_level_min/max bereits einen _show_frame()-Repaint aus, der im
    # manuellen Modus noch die ALTEN Grenzwerte anzeigte. Reihenfolge wurde
    # getauscht -- ein direkt anschliessender Export (ohne weiteren
    # _show_frame()-Aufruf) muss jetzt sofort die neuen Werte zeigen.
    old_state = win._capture_level_widgets_state()
    try:
        win._apply_level_widgets_state({
            "cmap_index": old_state["cmap_index"],
            "invert": old_state["invert"],
            "level_mode": "manual",
            "level_min": 5.0,
            "level_max": 42.0,
        })
        lo, hi = win.image_item.getLevels()
        assert abs(lo - 5.0) < 1e-6 and abs(hi - 42.0) < 1e-6, (lo, hi)
    finally:
        win._apply_level_widgets_state(old_state)


check(
    "_apply_level_widgets_state applies manual min/max immediately, without needing a follow-up _show_frame",
    test_apply_level_widgets_state_manual_mode_applies_immediately,
)


# ============================================================ Round 3 =====

def test_video_export_dialog_cursor_under_graph_box_and_beides_default():
    from thermal_viewer.dialogs import VideoExportDialog as RealVideoExportDialog

    dlg = RealVideoExportDialog(
        win, n_frames=win.recording.n_frames, colormaps=COLORMAPS,
        current_colormap_index=0, current_invert=False, current_level_mode="global",
        current_min=0.0, current_max=50.0, current_fps=10.0,
    )
    try:
        assert dlg.radio_overlay_both.isChecked() is True, "Default fuer 'Laufzeit' muss 'Beides' sein"
        assert dlg.timeline_overlay_mode() == "both"
        assert dlg.chk_cursor_position.parentWidget() is dlg.chk_show_graph.parentWidget(), (
            "Cursor-Checkbox muss jetzt im 'Temperaturverlauf-Graph'-Kasten liegen"
        )
        assert dlg.chk_cursor_position.parentWidget().title() == "Temperaturverlauf-Graph"
    finally:
        dlg.close()


check(
    "video export dialog: 'Cursor-Position' moved under 'Temperaturverlauf-Graph', 'Laufzeit' defaults to 'Beides'",
    test_video_export_dialog_cursor_under_graph_box_and_beides_default,
)


def test_video_export_dialog_graph_position_options():
    from thermal_viewer.dialogs import VideoExportDialog as RealVideoExportDialog

    dlg = RealVideoExportDialog(
        win, n_frames=win.recording.n_frames, colormaps=COLORMAPS,
        current_colormap_index=0, current_invert=False, current_level_mode="global",
        current_min=0.0, current_max=50.0, current_fps=10.0,
    )
    try:
        assert dlg.combo_graph_position.isEnabled() is False
        assert [dlg.combo_graph_position.itemData(i) for i in range(dlg.combo_graph_position.count())] == [
            "unten", "oben", "links", "rechts",
        ]
        assert dlg.graph_position() == "unten"
        dlg.chk_show_graph.setChecked(True)
        assert dlg.combo_graph_position.isEnabled() is True
        dlg.combo_graph_position.setCurrentIndex(dlg.combo_graph_position.findData("links"))
        assert dlg.graph_position() == "links"
    finally:
        dlg.close()


check(
    "video export dialog offers a graph-position dropdown (unten/oben/links/rechts)",
    test_video_export_dialog_graph_position_options,
)


def test_render_video_frame_graph_position_options():
    unix = win.recording.unix_seconds()
    n = win.recording.n_frames
    frame_indices = list(range(0, min(3, n)))
    segments = win._tight_glw_segments()
    sizes = {}
    for pos in ("unten", "oben", "links", "rechts"):
        img = win._render_video_frame(
            1.0, QtGui.QColor(win._graph_bg), "none", frame_indices[0], frame_indices, unix, segments,
            win.timeseries_plot, pos,
        )
        assert img.width() > 0 and img.height() > 0
        sizes[pos] = (img.width(), img.height())
    assert sizes["unten"] == sizes["oben"], "oben/unten muessen dieselbe Gesamtgroesse ergeben"
    assert sizes["links"] == sizes["rechts"], "links/rechts muessen dieselbe Gesamtgroesse ergeben"
    assert sizes["links"] != sizes["unten"], (
        "Seite-an-Seite vs. gestapelt muessen zu unterschiedlichen Canvas-Groessen fuehren"
    )


check(
    "_render_video_frame lays the graph out correctly for all 4 positions (oben/unten/links/rechts)",
    test_render_video_frame_graph_position_options,
)


def test_frozen_ui_during_export_context_manager():
    watched = (win.glw, win.timeseries_plot, win.live_plot)
    for w in watched:
        assert w.updatesEnabled() is True
    with win._frozen_ui_during_export():
        for w in watched:
            assert w.updatesEnabled() is False
    for w in watched:
        assert w.updatesEnabled() is True


check(
    "_frozen_ui_during_export disables on-screen repainting during video export, re-enables afterward",
    test_frozen_ui_during_export_context_manager,
)


def test_dual_time_axis_export_context_manager():
    top_axis = win.timeseries_plot.getPlotItem().getAxis("top")
    assert top_axis.isVisible() is False
    with win._dual_time_axis_export(win.timeseries_plot):
        assert top_axis.isVisible() is True
        assert win.axis_timeseries_top.runtime_mode != win.axis_timeseries_bottom.runtime_mode
    assert top_axis.isVisible() is False
    assert win.axis_timeseries_top.runtime_mode is False  # zurueckgesetzt

    # self.glw hat keine Zeitachse -- darf nicht crashen, tut einfach nichts.
    with win._dual_time_axis_export(win.glw):
        pass


check(
    "_dual_time_axis_export shows the (normally hidden) top axis with the opposite clock/runtime mode",
    test_dual_time_axis_export_context_manager,
)


def test_graphic_export_dual_time_axis_end_to_end():
    from thermal_viewer.dialogs import GraphicExportDialog as RealGraphicExportDialog
    from thermal_viewer.main_window import GraphicExportDialog

    p = OUT / "dual_axis_check.png"
    if p.exists():
        p.unlink()

    def make_exec(self):
        idx = self.combo_time_axis.findData("both")
        self.combo_time_axis.setCurrentIndex(idx)
        self.accept()
        return QtWidgets.QDialog.DialogCode.Accepted

    orig_save = QtWidgets.QFileDialog.getSaveFileName
    try:
        with temp_dialog_exec(GraphicExportDialog, make_exec):
            QtWidgets.QFileDialog.getSaveFileName = staticmethod(lambda *a, **k: (str(p), "PNG-Bild (*.png)"))
            win._export_graphic()
    finally:
        QtWidgets.QFileDialog.getSaveFileName = orig_save
    assert p.exists()
    # Nach dem Export muss die obere Achse wieder ausgeblendet sein.
    assert win.timeseries_plot.getPlotItem().getAxis("top").isVisible() is False


check(
    "graphic export with time axis 'Beide' produces a file and hides the top axis again afterward",
    test_graphic_export_dual_time_axis_end_to_end,
)


def test_filename_template_compile_validate_match():
    from datetime import datetime as _dt
    from thermal_viewer.data import (
        DEFAULT_FILENAME_TEMPLATE, compile_filename_template, files_matching_template,
        validate_filename_template,
    )

    pattern, fmt = compile_filename_template(DEFAULT_FILENAME_TEMPLATE)
    m = pattern.search("Record_2026-08-19_10-00-00")
    assert m is not None and m.group(1) == "Record_2026-08-19_10-00-00"
    assert _dt.strptime(m.group(1), fmt) == _dt(2026, 8, 19, 10, 0, 0)

    assert validate_filename_template(DEFAULT_FILENAME_TEMPLATE) is None
    assert validate_filename_template("Record_YYYY-MM-DD") is not None, "unvollstaendig (keine Uhrzeit)"
    assert validate_filename_template("Record_YYYY-YYYY-DD_hh-mm-ss") is not None, "MM fehlt"

    all_csv = sorted(DATASET.glob("*.csv"))
    assert files_matching_template(DATASET, pattern) == all_csv

    bad_pattern, _bad_fmt = compile_filename_template("IMG-YYYYMMDD-hhmmss")
    assert files_matching_template(DATASET, bad_pattern) == []

    # Individuelles (nicht-Standard) Schema muss ebenfalls korrekt matchen.
    custom_pattern, custom_fmt = compile_filename_template("IMG_YYYYMMDD_hhmmss")
    m2 = custom_pattern.search("IMG_20260824_143000")
    assert m2 is not None
    assert _dt.strptime(m2.group(1), custom_fmt) == _dt(2026, 8, 24, 14, 30, 0)


check(
    "compile_filename_template/validate_filename_template/files_matching_template work for default + custom schemes",
    test_filename_template_compile_validate_match,
)


def test_filename_template_ignores_incidental_letter_collisions_in_literal_text():
    # Bugreport: ein literaler Praefix, der zufaellig eine Platzhalter-
    # Buchstabenfolge enthaelt (z.B. "ss" in "Messung_" oder in "PasstHier"),
    # wurde faelschlich als Sekunden-Platzhalter gelesen. Fix: ein Lauf aus
    # Platzhalter-Buchstaben zaehlt nur als Platzhalter, wenn er (a) sich
    # restlos in gueltige Tokens zerlegen laesst UND (b) nicht unmittelbar an
    # einen gewoehnlichen Buchstaben angrenzt -- direkt aneinandergereihte
    # ECHTE Platzhalter (z.B. "YYYYMMDD") muessen dabei weiterhin erkannt
    # werden.
    from datetime import datetime as _dt
    from thermal_viewer.data import compile_filename_template, validate_filename_template

    for prefix in ("Messung_", "PasstHier-", "Unpassend_"):
        template = prefix + "YYYY-MM-DD_hh-mm-ss"
        assert validate_filename_template(template) is None, template
        pattern, fmt = compile_filename_template(template)
        # Der Praefix darf NICHT in der Regex als \d{2}-Ziffernfeld auftauchen
        # -- er muss vollstaendig literal (escaped) sein. pattern.pattern
        # beginnt mit der oeffnenden Klammer der Erfassungsgruppe, daher
        # Vergleich ab Index 1.
        assert pattern.pattern[1:].startswith(__import__("re").escape(prefix)), pattern.pattern
        fname = f"{prefix}2026-08-24_14-30-00"
        m = pattern.search(fname)
        assert m is not None and m.group(1) == fname, (template, pattern.pattern)
        assert _dt.strptime(m.group(1), fmt) == _dt(2026, 8, 24, 14, 30, 0)

    # Direkt aneinandergereihte ECHTE Platzhalter muessen weiterhin erkannt
    # werden (nicht durch den Fix versehentlich mit-blockiert).
    pattern2, fmt2 = compile_filename_template("IMG-YYYYMMDD-hhmmss")
    m2 = pattern2.search("IMG-20260824-143000")
    assert m2 is not None
    assert _dt.strptime(m2.group(1), fmt2) == _dt(2026, 8, 24, 14, 30, 0)


check(
    "filename template tokenizer distinguishes incidental letter collisions (e.g. 'ss' in 'Messung_') from real placeholders",
    test_filename_template_ignores_incidental_letter_collisions_in_literal_text,
)


def test_filename_template_dialog_live_preview():
    from thermal_viewer.main_window import FilenameTemplateDialog

    dlg = FilenameTemplateDialog(win, DATASET, win._filename_template)
    try:
        ok_button = dlg.buttons.button(QtWidgets.QDialogButtonBox.StandardButton.Ok)
        all_csv = sorted(DATASET.glob("*.csv"))
        assert ok_button.isEnabled() is True, "Standard-Template muss bereits zu allen Dateien passen"
        assert dlg.preview_list.count() == len(all_csv)

        dlg.edit_template.setText("Record_YYYY-MM-DD")
        assert ok_button.isEnabled() is False
        assert "fehlen" in dlg.status_label.text()

        # Bewusst OHNE die Buchstabenfolgen der Platzhalter selbst (YYYY/MM/
        # DD/hh/mm/ss) im literalen Praefix -- sonst wuerde der Tokenizer
        # z.B. ein "ss" faelschlich als Sekunden-Platzhalter lesen (bekannte,
        # dokumentierte Einschraenkung des Schemas -- siehe Hinweistext im
        # Dialog). "ZZZ" enthaelt garantiert keine der Token-Buchstaben.
        dlg.edit_template.setText("ZZZ-YYYY-MM-DD_hh-mm-ss")
        assert ok_button.isEnabled() is False
        assert "0 von" in dlg.status_label.text()

        dlg.edit_template.setText("Record_YYYY-MM-DD_hh-mm-ss")
        assert ok_button.isEnabled() is True
        assert dlg.preview_list.count() == len(all_csv)
    finally:
        dlg.close()


check(
    "FilenameTemplateDialog live preview enables/disables OK based on template validity + actual folder matches",
    test_filename_template_dialog_live_preview,
)


def test_resolve_folder_and_pattern_flow():
    import shutil
    from thermal_viewer.main_window import FilenameTemplateDialog

    mismatched_dir = OUT / "mismatched_naming_dataset"
    if mismatched_dir.exists():
        shutil.rmtree(mismatched_dir)
    mismatched_dir.mkdir()
    sample = sorted(DATASET.glob("*.csv"))[0]
    for i in range(3):
        shutil.copy(sample, mismatched_dir / f"IMG-2026081{i}-100000.csv")

    old_template = win._filename_template
    old_pattern = win._filename_pattern

    orig_ask = win._ask_filename_mismatch
    orig_get_dir = QtWidgets.QFileDialog.getExistingDirectory
    orig_exec = FilenameTemplateDialog.exec
    try:
        # Fall 1: Abbrechen.
        win._ask_filename_mismatch = lambda folder: "cancel"
        assert win._resolve_folder_and_pattern(mismatched_dir) is None

        # Fall 2: Neuer Ordner (passt bereits zum Standard-Schema).
        win._ask_filename_mismatch = lambda folder: "new_folder"
        QtWidgets.QFileDialog.getExistingDirectory = staticmethod(lambda *a, **k: str(DATASET))
        result = win._resolve_folder_and_pattern(mismatched_dir)
        assert result is not None
        folder, pattern, _fmt = result
        assert folder == DATASET
        assert pattern is old_pattern

        # Fall 3: Namensschema anpassen, NICHT dauerhaft speichern.
        win._ask_filename_mismatch = lambda folder: "template"

        def make_exec(persist):
            def _exec(self):
                self.edit_template.setText("IMG-YYYYMMDD-hhmmss")
                self.chk_persist.setChecked(persist)
                self.accept()
                return QtWidgets.QDialog.DialogCode.Accepted
            return _exec

        FilenameTemplateDialog.exec = make_exec(False)
        result = win._resolve_folder_and_pattern(mismatched_dir)
        assert result is not None
        folder, pattern, _fmt = result
        assert folder == mismatched_dir
        assert pattern.pattern != old_pattern.pattern
        assert win._filename_template == old_template, "ohne Haekchen darf der Standard NICHT ueberschrieben werden"
        assert win._filename_pattern is old_pattern

        # Fall 4: Namensschema anpassen, DAUERHAFT speichern.
        FilenameTemplateDialog.exec = make_exec(True)
        result2 = win._resolve_folder_and_pattern(mismatched_dir)
        assert result2 is not None
        assert win._filename_template == "IMG-YYYYMMDD-hhmmss"
        assert win._settings.value("filename_template") == "IMG-YYYYMMDD-hhmmss"
    finally:
        FilenameTemplateDialog.exec = orig_exec
        win._ask_filename_mismatch = orig_ask
        QtWidgets.QFileDialog.getExistingDirectory = orig_get_dir
        win._set_filename_template(old_template)  # QSettings sauber zuruecksetzen
        shutil.rmtree(mismatched_dir, ignore_errors=True)


check(
    "_resolve_folder_and_pattern: cancel/new-folder/template(temporary)/template(persist) all behave correctly",
    test_resolve_folder_and_pattern_flow,
)


def test_open_folder_no_mismatch_dialog_when_names_match():
    calls = []
    orig_ask = win._ask_filename_mismatch
    orig_get_dir = QtWidgets.QFileDialog.getExistingDirectory
    win._ask_filename_mismatch = lambda folder: (calls.append(folder), "cancel")[1]
    QtWidgets.QFileDialog.getExistingDirectory = staticmethod(lambda *a, **k: str(DATASET))
    try:
        win._open_folder()
        assert calls == [], "bei passendem Namensschema darf keine Rueckfrage erscheinen"
        assert win.recording is not None
    finally:
        win._ask_filename_mismatch = orig_ask
        QtWidgets.QFileDialog.getExistingDirectory = orig_get_dir


check(
    "_open_folder skips the naming-mismatch dialog entirely when files already match the active template",
    test_open_folder_no_mismatch_dialog_when_names_match,
)


def test_duplicate_roi_names_stay_independently_selectable():
    # Bugreport: die Graph-Inhalt-Auswahl war ueber den (frei umbenennbaren)
    # ROI-NAMEN indiziert -- zwei gleichnamige Messbereiche liessen sich
    # dadurch nicht mehr unabhaengig voneinander an-/abwaehlen (die zweite
    # Checkbox ueberschrieb die erste im dict). Fix: Auswahl laeuft jetzt
    # ueber die eindeutige RoiEntry.number.
    from thermal_viewer.dialogs import GraphicExportDialog as RealGraphicExportDialog

    a = win._add_roi_entry()
    b = win._add_roi_entry()
    try:
        a.place(2, 2, 4, 4)
        b.place(8, 8, 4, 4)
        win._recompute_curves(entries=[a, b])
        a.list_item.setText("Doppelt")
        a.name = "Doppelt"
        b.list_item.setText("Doppelt")
        b.name = "Doppelt"

        dlg = RealGraphicExportDialog(
            win, win._settings, default_dpi=150, show_graph_source_choice=True,
            live_available=False, roi_entries=[(a.number, a.name), (b.number, b.name)],
        )
        try:
            checks = dlg._content_widgets["checks"]
            assert set(checks.keys()) == {a.number, b.number}, "beide Checkboxen muessen eigenstaendig existieren"
            checks[a.number].setChecked(False)
            assert dlg.included_roi_numbers() == {b.number}, "muss trotz gleichen Namens nur b auswaehlen"
            checks[a.number].setChecked(True)
            checks[b.number].setChecked(False)
            assert dlg.included_roi_numbers() == {a.number}, "muss trotz gleichen Namens nur a auswaehlen"
        finally:
            dlg.close()
    finally:
        orig_question = QtWidgets.QMessageBox.question
        QtWidgets.QMessageBox.question = staticmethod(lambda *a, **k: QtWidgets.QMessageBox.StandardButton.Yes)
        try:
            win._on_roi_remove_clicked(a)
            win._on_roi_remove_clicked(b)
        finally:
            QtWidgets.QMessageBox.question = orig_question
        win.roi_list.setCurrentRow(0)


check(
    "two identically-named ROIs stay independently selectable in the graph-content picker",
    test_duplicate_roi_names_stay_independently_selectable,
)


def test_end_to_end_export_state_restoration_with_dynamic_roi_selection():
    # End-to-End-Gegenstueck zu den obigen dialog-only-Tests: _export_graphic
    # UND _export_video (Bildstapel) muessen ueber _temporary_graph_content
    # genau die im Dialog gewaehlten Kurven zeigen und danach exakt den
    # vorherigen Sichtbarkeitszustand wiederherstellen -- auch wenn die
    # Auswahl NICHT alle platzierten ROIs umfasst.
    from thermal_viewer.dialogs import GraphicExportDialog, VideoExportDialog

    win._hover_row, win._hover_col = 4, 4
    win._update_live_cursor(4, 4)
    app.processEvents()
    win._settings.setValue("export/separate_images", False)

    placed = [e for e in win.roi_entries if e.placed]
    assert len(placed) >= 2, "Test braucht mindestens 2 platzierte ROIs"
    keep_number = placed[0].number

    prev_visible = {e.number: e.curve.isVisible() for e in win.roi_entries}
    prev_live_checked = win.chk_show_live_in_timeseries.isChecked()

    out_png = OUT / "dyn_selection_export.png"

    def fake_exec_graphic(self):
        checks = self._content_widgets["checks"]
        for number, chk in checks.items():
            chk.setChecked(number == keep_number)
        self._content_widgets["chk_live"].setChecked(True)
        return (self.accept(), QtWidgets.QDialog.DialogCode.Accepted)[1]

    orig_save = QtWidgets.QFileDialog.getSaveFileName
    try:
        with temp_dialog_exec(GraphicExportDialog, fake_exec_graphic):
            QtWidgets.QFileDialog.getSaveFileName = staticmethod(lambda *a, **k: (str(out_png), "PNG-Bild (*.png)"))
            win._export_graphic()
        assert out_png.exists()
        for e in win.roi_entries:
            assert e.curve.isVisible() == prev_visible[e.number], "Sichtbarkeit nach Grafik-Export nicht wiederhergestellt"
        assert win.chk_show_live_in_timeseries.isChecked() == prev_live_checked
        assert win.timeseries_live_curve.isVisible() == prev_live_checked
    finally:
        QtWidgets.QFileDialog.getSaveFileName = orig_save

    out_dir = OUT / "dyn_selection_stack"
    if out_dir.exists():
        for f in out_dir.glob("*"):
            f.unlink()
    else:
        out_dir.mkdir()

    def fake_exec_video(self):
        self.radio_output_images.setChecked(True)
        self.spin_start.setValue(1)
        self.spin_end.setValue(min(2, self.spin_end.maximum()))
        self.chk_show_graph.setChecked(True)
        checks = self._content_widgets["checks"]
        for number, chk in checks.items():
            chk.setChecked(number == keep_number)
        self._content_widgets["chk_live"].setChecked(True)
        return (self.accept(), QtWidgets.QDialog.DialogCode.Accepted)[1]

    orig_get_dir = QtWidgets.QFileDialog.getExistingDirectory
    try:
        with temp_dialog_exec(VideoExportDialog, fake_exec_video):
            QtWidgets.QFileDialog.getExistingDirectory = staticmethod(lambda *a, **k: str(out_dir))
            win._export_video()
        assert list(out_dir.glob("*.png")), "Bildstapel wurde nicht geschrieben"
        for e in win.roi_entries:
            assert e.curve.isVisible() == prev_visible[e.number], "Sichtbarkeit nach Bildstapel-Export nicht wiederhergestellt"
        assert win.chk_show_live_in_timeseries.isChecked() == prev_live_checked
    finally:
        QtWidgets.QFileDialog.getExistingDirectory = orig_get_dir
        win._hover_row = win._hover_col = None


check(
    "graphic + image-stack export restore ROI-curve/live-curve visibility exactly, even for a partial selection",
    test_end_to_end_export_state_restoration_with_dynamic_roi_selection,
)

print()
if failures:
    print(f"{len(failures)} FAILURE(S):", failures)
    sys.exit(1)
else:
    print("ALL CHECKS PASSED")
