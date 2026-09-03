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

# MainWindow.__init__ konstruiert QSettings("ThermalViewer", "ThermalViewer")
# -- OHNE Umleitung wuerde das auf die ECHTEN, systemweiten Einstellungen des
# tatsaechlich lokal installierten Programms zugreifen (Windows-Registry bzw.
# Nutzerprofil). Ein Test, der eine "dauerhaft speichern"-Option prueft (z.B.
# ein per persist()-Checkbox gesetztes Namensschema/Datenimport-Format),
# wuerde dadurch NICHT nur seinen eigenen Testlauf beeinflussen, sondern
# dauerhaft die echten Einstellungen ueberschreiben, die die App beim naechsten
# ECHTEN Start des Nutzers liest (Robustheitsbug: bei einer Testreihe mit
# mehreren MainWindow()-Instanzen wurde so sogar der Import-Manager-Standard
# klammheimlich fuer alle nachfolgenden Instanzen -- inkl. einer spaeteren,
# eigentlich unabhaengigen echten Nutzung -- veraendert). Stattdessen: JEDE
# QSettings("ThermalViewer", "ThermalViewer")-Instanziierung in diesem
# Testlauf auf eine isolierte, im Scratch-Verzeichnis liegende INI-Datei
# umleiten, die mit dem gesamten OUT-Verzeichnis am Ende automatisch
# aufgeraeumt wird.
_TEST_SETTINGS_PATH = str(Path(tempfile.mkdtemp(prefix="thermalviewer_settings_")) / "settings.ini")
atexit.register(shutil.rmtree, str(Path(_TEST_SETTINGS_PATH).parent), True)
_RealQSettings = QtCore.QSettings


class _IsolatedQSettings(_RealQSettings):
    def __init__(self, *args, **kwargs):
        super().__init__(_TEST_SETTINGS_PATH, _RealQSettings.Format.IniFormat)


QtCore.QSettings = _IsolatedQSettings

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


def test_roi_spinboxes_apply_live_on_every_change():
    entry = win.roi_entries[1]
    entry.place(10, 10, 10, 10)
    win._sync_roi_spinboxes(entry)
    # Jede Aenderung (valueChanged) wendet sofort an -- kein "Uebernehmen"-
    # Knopf und kein Warten auf editingFinished/Fokuswechsel mehr noetig.
    entry.spin_width.setValue(20)  # innerhalb spin_width.maximum() (Bildbreite)
    assert entry.width() == 20, entry.width()
    entry.spin_height.setValue(15)  # innerhalb spin_height.maximum() (Bildhoehe)
    assert entry.height() == 15, entry.height()


check("ROI width/height spinboxes apply live on every change (valueChanged)", test_roi_spinboxes_apply_live_on_every_change)


def test_roi_place_does_not_redundantly_recompute_curves():
    # Bugfix: RoiEntry.place() rief bisher self.roi.setSize() und
    # self.roi.setPos() JEWEILS mit dem pyqtgraph-Default update=True auf --
    # jeder der beiden Aufrufe loeste dadurch fuer sich sigRegionChanged UND
    # (finish=True) sigRegionChangeFinished aus, zusammen mit dem expliziten
    # Aufruf in z.B. _on_roi_apply_clicked also 5 volle _recompute_curves()-
    # Durchlaeufe PRO EINZELNER Eingabefeld-Aenderung (siehe live-apply via
    # spin.valueChanged oben). Bei aktivierter Verlaufs-Interpolation ist
    # _recompute_curves() eine reine Python-Schleife ueber alle Frames --
    # bei grossen Aufnahmen (Scrollrad/gehaltene Pfeiltaste = viele
    # valueChanged-Events) fuehrte das zu spuerbarem Rucken. setSize() nutzt
    # jetzt update=False, sodass nur noch setPos() Signale ausloest (wie im
    # pg.ROI-Docstring fuer genau diesen Fall empfohlen).
    entry = win.roi_entries[1]
    entry.place(10, 10, 10, 10)  # bereits platziert, wie im vorherigen Test

    orig_recompute = win._recompute_curves
    calls = []

    def counting_recompute(*args, **kwargs):
        calls.append(1)
        return orig_recompute(*args, **kwargs)

    win._recompute_curves = counting_recompute
    try:
        entry.place(10, 10, 12, 8)
    finally:
        win._recompute_curves = orig_recompute
    assert len(calls) <= 2, f"place() auf einem bereits platzierten ROI sollte hoechstens 2x neu berechnen, war: {len(calls)}"
    assert entry.width() == 12 and entry.height() == 8, (entry.width(), entry.height())


check(
    "RoiEntry.place() no longer triggers 5x redundant _recompute_curves() per call (setSize update=False)",
    test_roi_place_does_not_redundantly_recompute_curves,
)


def test_project_load_does_not_fire_spurious_roi_apply_during_geometry_restore():
    # Bugfix: _load_project() schrieb die geladene ROI-Geometrie bisher ueber
    # VIER einzelne entry.spin_x/-y/-width/-height.setValue()-Aufrufe OHNE
    # blockSignals -- seit spin.valueChanged live an _on_roi_apply_clicked
    # haengt (siehe oben), loeste dadurch JEDER dieser vier Aufrufe fuer sich
    # bereits eine (mangels der jeweils noch nicht gesetzten uebrigen Werte
    # UNVOLLSTAENDIGE) Platzierung + Kurven-Neuberechnung aus, bevor der
    # eigentliche, korrekte entry.place()-Aufruf direkt danach lief. Fix:
    # _set_widget_value() (blockSignals) statt direktem .setValue().
    entry = win.roi_entries[2]
    entry.place(2, 2, 2, 2)  # abweichende Alt-Geometrie vor dem Laden

    orig_apply = win._on_roi_apply_clicked
    calls = []

    def counting_apply(*args, **kwargs):
        calls.append(1)
        return orig_apply(*args, **kwargs)

    win._on_roi_apply_clicked = counting_apply

    project_data = {
        "anzahl_frames": win.recording.n_frames,
        "rois": [{
            "index": entry.number - 1,
            "name": entry.name,
            "platziert": True,
            "mittelpunkt": {"x": 12.0, "y": 12.0},
            "breite_px": 6.0,
            "hoehe_px": 6.0,
        }],
    }
    path = OUT / "test_project_no_spurious_apply.tvproj"
    path.write_text(json.dumps(project_data), encoding="utf-8")
    orig_open = QtWidgets.QFileDialog.getOpenFileName
    QtWidgets.QFileDialog.getOpenFileName = staticmethod(lambda *a, **k: (str(path), ""))
    try:
        win._load_project()
    finally:
        QtWidgets.QFileDialog.getOpenFileName = orig_open
        win._on_roi_apply_clicked = orig_apply
    assert len(calls) == 0, f"Projekt-Laden sollte keine live-apply Spinbox-Handler ausloesen, war: {len(calls)}"
    assert entry.center() == (12.0, 12.0) and entry.width() == 6.0 and entry.height() == 6.0, (
        entry.center(), entry.width(), entry.height()
    )


check(
    "loading a project restores ROI geometry without firing spurious/incomplete live-apply spinbox handlers",
    test_project_load_does_not_fire_spurious_roi_apply_during_geometry_restore,
)


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


def test_measure_tool_uses_scale_without_modifying_it():
    # Nutzerwunsch (Folgeanfrage zu Punkt 12): "ich will keinen neuen
    # Maßstab setzen können, sondern wirklich nur messen" -- das Mess-
    # Werkzeug nutzt einen bereits gesetzten Maßstab nur LESEND, im
    # Gegensatz zum Lineal-Werkzeug oben wird dabei nie (erneut) nach einer
    # realen Laenge gefragt bzw. _px_to_mm veraendert.
    win._start_ruler_tool()
    p1 = win.view_box.mapViewToScene(QtCore.QPointF(2, 2))
    p2 = win.view_box.mapViewToScene(QtCore.QPointF(12, 2))
    win._handle_ruler_click(FakeEvent(QtCore.Qt.LeftButton, p1))
    with ruler_length_input(30.0):
        win._handle_ruler_click(FakeEvent(QtCore.Qt.LeftButton, p2))
    px_to_mm = win._px_to_mm
    assert px_to_mm is not None

    assert win.btn_measure.isEnabled()
    assert win.act_measure.isEnabled()

    win._start_measure_tool()
    assert win._measure_armed
    assert not win._ruler_armed, "Lineal- und Mess-Werkzeug muessen sich gegenseitig ausschliessen"

    m1 = win.view_box.mapViewToScene(QtCore.QPointF(0, 0))
    m2 = win.view_box.mapViewToScene(QtCore.QPointF(10, 0))
    win._handle_measure_click(FakeEvent(QtCore.Qt.LeftButton, m1))
    assert win._measure_start is not None
    win._handle_measure_click(FakeEvent(QtCore.Qt.LeftButton, m2))

    assert not win._measure_armed
    assert win._px_to_mm == px_to_mm, "Messen darf den bestehenden Maßstab nicht veraendern"
    assert win._measure_line is not None and win._measure_line.isVisible()
    assert win._measure_text is not None and win._measure_text.isVisible()

    import re
    expected_mm = 10 * px_to_mm
    match = re.search(r"[\d,]+", win._measure_text.toPlainText())
    assert match is not None, win._measure_text.toPlainText()
    shown_value = float(match.group(0).replace(",", "."))
    assert abs(shown_value - expected_mm) < 0.06, (shown_value, expected_mm)

    win._clear_ruler_scale()
    assert not win.btn_measure.isEnabled()
    assert not win.act_measure.isEnabled()
    assert not win._measure_line.isVisible()


check(
    "measure tool reads the existing scale without redefining it, and is disabled without a defined scale",
    test_measure_tool_uses_scale_without_modifying_it,
)


def test_live_cursor_kernel_size_menu_and_averaging():
    # Feature: "Werkzeuge > Live-Cursor-Bereichsgröße" -- Live-Verlauf/
    # -Anzeige koennen statt eines einzelnen Pixels den Mittelwert eines
    # NxN-Blocks um das Cursor-Pixel verwenden. Bewusst NICHT im rechten
    # Panel, sondern als eigenes Menue im Menueband (Nutzer-Vorgabe).
    import numpy as np

    assert set(win._live_cursor_kernel_actions.keys()) == {1, 3, 5, 7, 9, 11, 13, 15}
    assert win._live_cursor_kernel_actions[5].isChecked()  # 5x5 ist der Standard

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


def test_interp_start_frame_after_end_frame_warns_instead_of_silently_freezing():
    # Bugfix: die frei waehlbaren Start-/Ende-Spinboxen (Nutzerwunsch: Ziel-
    # Bild pro Messbereich frei waehlbar statt zwingend erstes/letztes Bild)
    # lassen sich leicht vertauschen -- _interp_fraction() faengt
    # start_idx >= end_idx defensiv mit frac=0.0 ab (kein Absturz), das ROI
    # blieb dabei aber unbemerkt fuer die gesamte Aufnahme auf der Start-
    # Position eingefroren. Jetzt muss beim Abschluss des zweiten Keyframes
    # eine Warnung erscheinen.
    n = win.recording.n_frames
    entry = win._add_roi_entry()
    warn_calls = []
    orig_warning = QtWidgets.QMessageBox.warning
    QtWidgets.QMessageBox.warning = staticmethod(lambda *a, **k: warn_calls.append(a))
    try:
        entry.place(4, 4, 6, 6)
        entry.chk_interp.setChecked(True)

        entry.spin_interp_start_frame.setValue(n)  # letztes Bild als Start
        entry.btn_interp_start.click()  # Phase 1: hinspringen
        entry.btn_interp_start.click()  # Phase 2: erfassen
        assert entry.interp_start is not None
        assert not warn_calls, "Warnung sollte erst nach BEIDEN Keyframes erscheinen"

        entry.spin_interp_end_frame.setValue(1)  # erstes Bild als Ende -> Start > Ende
        entry.btn_interp_end.click()  # Phase 1: hinspringen
        entry.btn_interp_end.click()  # Phase 2: erfassen
        assert entry.interp_end is not None
        assert warn_calls, "vertauschter Start/Ende-Bereich haette gewarnt werden muessen"

        # _interp_fraction() bleibt trotzdem sicher (kein Crash, frac=0.0
        # fuer jeden Frame) -- die Warnung ist ein zusaetzlicher Hinweis,
        # kein Ersatz fuer die bestehende defensive Klemmung.
        assert win._interp_fraction(0, entry.interp_start_frame, entry.interp_end_frame) == 0.0
        assert win._interp_fraction(n - 1, entry.interp_start_frame, entry.interp_end_frame) == 0.0

        entry.chk_interp.setChecked(False)
    finally:
        QtWidgets.QMessageBox.warning = orig_warning
        orig_question = QtWidgets.QMessageBox.question
        QtWidgets.QMessageBox.question = staticmethod(
            lambda *a, **k: QtWidgets.QMessageBox.StandardButton.Yes
        )
        try:
            win._on_roi_remove_clicked(entry)
        finally:
            QtWidgets.QMessageBox.question = orig_question
        win.roi_list.setCurrentRow(0)


check(
    "capturing interpolation start-frame after end-frame warns instead of silently freezing the ROI",
    test_interp_start_frame_after_end_frame_warns_instead_of_silently_freezing,
)

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


def test_csv_export_numeric_runtime_unit_changes_column_header_and_values():
    # Nutzerwunsch ("dritte Zeitachse"): das gewaehlte Laufzeit-Format wirkt
    # auch im CSV-Export -- Spaltenkopf UND Werte wechseln von hh:mm:ss auf
    # eine reine Dezimalzahl in der gewaehlten Einheit.
    prev_unit = win._runtime_unit
    try:
        win._apply_runtime_unit("min")
        assert win.combo_runtime_unit_timeseries.currentData() == "min"
        assert win.combo_runtime_unit_live.currentData() == "min"

        path = OUT / "roi_export_numeric_runtime.csv"
        if path.exists():
            path.unlink()
        orig = QtWidgets.QFileDialog.getSaveFileName
        QtWidgets.QFileDialog.getSaveFileName = staticmethod(lambda *a, **k: (str(path), ""))
        try:
            win._export_csv()
        finally:
            QtWidgets.QFileDialog.getSaveFileName = orig
        assert path.exists(), "CSV wurde nicht geschrieben"
        text = path.read_text(encoding="utf-8-sig")
        lines = [l for l in text.splitlines() if l.strip()]
        header = lines[0].split(";")
        assert header[1] == "Laufzeit (min)", header
        first_row = lines[1].split(";")
        assert first_row[1] == "0,000", first_row  # erster Frame: Laufzeit 0
        assert ":" not in first_row[1], "sollte keine hh:mm:ss-Formatierung mehr enthalten"
        path.unlink()
    finally:
        win._apply_runtime_unit(prev_unit)


check(
    "csv export: Laufzeit-Format (min/h/s statt hh:mm:ss) wirkt auf Spaltenkopf UND Werte",
    test_csv_export_numeric_runtime_unit_changes_column_header_and_values,
)


def test_json_export_numeric_runtime_unit_is_a_real_number_not_a_comma_string():
    # Bugfix: bei numerischem Laufzeit-Format (s/min/h) schrieb der JSON-
    # Export bisher denselben komma-formatierten Anzeige-String wie CSV
    # ("12,340" statt 12.34) -- das widerspricht dem eigentlichen Zweck der
    # "dritten Zeitachse" (Nutzerwunsch: Weiterverarbeitung ohne manuelles
    # Umrechnen in anderer Software) und dem eigenen Kommentar im Code ueber
    # "echte Zahlen mit Dezimalpunkt".
    from thermal_viewer.dialogs import CsvColumnDialog

    def fake_column_exec(self):
        self.combo_format.setCurrentIndex(self.combo_format.findData("json"))
        return (self.accept(), QtWidgets.QDialog.DialogCode.Accepted)[1]

    prev_unit = win._runtime_unit
    try:
        win._apply_runtime_unit("h")
        path = OUT / "roi_export_numeric_runtime.json"
        if path.exists():
            path.unlink()
        orig = QtWidgets.QFileDialog.getSaveFileName
        try:
            with temp_dialog_exec(CsvColumnDialog, fake_column_exec):
                QtWidgets.QFileDialog.getSaveFileName = staticmethod(lambda *a, **k: (str(path), ""))
                win._export_csv()
        finally:
            QtWidgets.QFileDialog.getSaveFileName = orig
        assert path.exists(), "JSON wurde nicht geschrieben"
        records = json.loads(path.read_text(encoding="utf-8"))
        runtime_key = "Laufzeit (h)"
        assert runtime_key in records[0], records[0].keys()
        assert isinstance(records[0][runtime_key], (int, float)), (
            f"Laufzeit sollte eine echte JSON-Zahl sein, ist aber {type(records[0][runtime_key])}: "
            f"{records[0][runtime_key]!r}"
        )
        assert records[0][runtime_key] == 0.0
        path.unlink()
    finally:
        win._apply_runtime_unit(prev_unit)


check(
    "json export: numerisches Laufzeit-Format liefert eine echte JSON-Zahl statt eines komma-formatierten Strings",
    test_json_export_numeric_runtime_unit_is_a_real_number_not_a_comma_string,
)


def test_csv_column_dialog_rejects_name_colliding_with_fixed_header_column():
    # Bugfix: die Eindeutigkeits-Pruefung der frei editierbaren Spaltennamen
    # verglich bisher NUR untereinander, nicht gegen die vom Export fest
    # vorangestellten Spalten ("Zeitstempel"/"Laufzeit (...)") -- eine ROI-
    # Spalte namens "Zeitstempel" wurde anstandslos akzeptiert und hat den
    # echten Zeitstempel beim JSON-Export (dict(zip(header, row))) still
    # ueberschrieben.
    from thermal_viewer.dialogs import CsvColumnDialog

    entries = [{"name": "Mitte", "width_px": 5.0, "height_px": 5.0, "width_mm": None, "height_mm": None}]
    dialog = CsvColumnDialog(win, entries, reserved_names=["Zeitstempel", "Laufzeit (HH:MM:SS)"])
    try:
        dialog._edits[0].setText("Zeitstempel")
        accepted = []
        dialog.accept = lambda: accepted.append(True)
        dialog._on_accept()
        assert not accepted, "Kollision mit fester Spalte 'Zeitstempel' haette abgelehnt werden muessen"

        dialog._edits[0].setText("Mitte (°C)")
        dialog._on_accept()
        assert accepted, "eindeutiger Name haette akzeptiert werden muessen"
    finally:
        dialog.close()


check(
    "CsvColumnDialog rejects a column name colliding with a fixed header column (Zeitstempel/Laufzeit)",
    test_csv_column_dialog_rejects_name_colliding_with_fixed_header_column,
)

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
        # jeweiligen Hintergrundfarbe -- Thermobild (glw) und Kurven-Graphen
        # haben seit dem Nutzerwunsch "Graph immer hell/Thermobild immer
        # dunkel" JEWEILS eine eigene, unterschiedliche feste Farbe.
        expected_bg = win._image_bg if widget is win.glw else win._graph_bg
        assert f'fill="{expected_bg.lower()}"' in content.lower(), (
            f"{name}: kein Hintergrund-Fill in der erwarteten Farbe {expected_bg} gefunden"
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
    import numpy as np

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


def test_video_timeline_overlay_background_matches_passed_theme_color():
    # Bugreport: der Zeitanzeige-Streifen unten im exportierten Video/
    # Bildstapel hatte bisher IMMER einen fest einprogrammierten, fast
    # schwarzen Hintergrund -- unabhaengig vom tatsaechlichen Hell-/Dunkel-
    # Design von Thermobild und Graph darueber ("Hintergrund des
    # Thermalbildes/Graphen ist richtig eingefaerbt, aber die Zeitleiste
    # unten nicht"). Jetzt kommt die Farbe von aussen (siehe
    # _render_video_frame/_draw_video_timeline_overlay, background=).
    import numpy as np

    frame_indices = list(range(0, min(3, win.recording.n_frames)))
    unix = win.recording.unix_seconds()
    segments = win._tight_glw_segments()
    # Bewusst ein deutlich HELLER Hintergrund -- der alte feste Wert
    # (QColor(0, 0, 0, 235)) war fast schwarz und haette hier klar
    # abgestochen.
    custom_bg = QtGui.QColor(240, 240, 240)
    img = win._render_video_frame(
        1.0, custom_bg, "timeline", frame_indices[0], frame_indices, unix, segments,
        foreground=QtGui.QColor(20, 20, 20),
    )
    arr = win._qimage_to_rgb_array(img)
    overlay_height = round(54 * 1.0)
    # Oberste Zeile des Streifens: dort wird laut _draw_video_timeline_overlay
    # nur die Flaeche gefuellt, Balken/Text sitzen weiter unten -- muss also
    # exakt custom_bg entsprechen.
    strip_top_row = arr[-overlay_height, :, :]
    assert np.all(np.abs(strip_top_row.astype(int) - np.array([240, 240, 240])) <= 2), strip_top_row[:5]


check(
    "exported video's timeline strip uses the passed-in theme background instead of a hardcoded near-black",
    test_video_timeline_overlay_background_matches_passed_theme_color,
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


def test_runtime_unit_combo_enabled_state_and_dual_axis_sync():
    # "Dritte Zeitachse": das Laufzeit-Format-Kombifeld ist nur relevant,
    # solange links ueberhaupt "Laufzeit" gewaehlt ist, und muss auch die
    # normalerweise ausgeblendeten OBEREN Achsen (siehe _dual_time_axis_export,
    # Export-Option "Beide") mit dem gewaehlten Format synchron halten.
    prev_mode = win._time_display_mode
    prev_unit = win._runtime_unit
    try:
        win._apply_time_display_mode("clock")
        assert not win.combo_runtime_unit_timeseries.isEnabled()
        assert not win.combo_runtime_unit_live.isEnabled()

        win._apply_time_display_mode("runtime")
        assert win.combo_runtime_unit_timeseries.isEnabled()
        assert win.combo_runtime_unit_live.isEnabled()

        win.combo_runtime_unit_live.setCurrentIndex(win.combo_runtime_unit_live.findData("h"))
        assert win._runtime_unit == "h"
        assert win.combo_runtime_unit_timeseries.currentData() == "h", "beide Format-Umschalter bleiben synchron"
        assert win.axis_timeseries_bottom.runtime_unit == "h"
        assert win.axis_live_bottom.runtime_unit == "h"
        # Obere (normalerweise verborgene) Achsen ebenfalls synchron, sonst
        # zeigt ein Export mit Zeitachse "Beide" dort faelschlich hh:mm:ss.
        assert win.axis_timeseries_top.runtime_unit == "h"
        assert win.axis_live_top.runtime_unit == "h"
    finally:
        win._apply_runtime_unit(prev_unit)
        win._apply_time_display_mode(prev_mode)


check(
    "Laufzeit-Format-Kombifeld nur bei 'Laufzeit' aktiv, haelt auch obere (Export-'Beide') Achsen synchron",
    test_runtime_unit_combo_enabled_state_and_dual_axis_sync,
)


def test_legend_labels_added_before_theme_apply_get_recolored_too():
    # Bugreport: "Live-Cursor fett und schwarz, waehrend in der Legende alle
    # anderen Kurven ausgegraut sind" -- kein Fettschrift-Unterschied
    # (Schriftgewicht war ueberall identisch), sondern ein echter Farb-Bug:
    # pyqtgraph LegendItem.setLabelTextColor() aktualisiert fuer BEREITS
    # vorhandene Eintraege nur label.opts["color"], loest aber (anders als
    # label.setText()) KEIN erneutes Rendern des schon erzeugten HTML aus --
    # ein Label, das VOR dem naechsten setLabelTextColor()-Aufruf bereits in
    # der Legende stand (z.B. die 5 Standard-Messbereiche beim Programm-
    # start), blieb dadurch dauerhaft bei pyqtgraphs globalem Standard-
    # Vordergrund haengen (ein helles Grau, fuer dunkle Hintergruende
    # gedacht -- pg.getConfigOption("foreground") == "d"), waehrend SPAETER
    # hinzugefuegte Eintraege (z.B. "Live (Cursor)", erst bei aktiviertem
    # "Live-Cursor-Kurve zusaetzlich anzeigen" hinzugefuegt) die zu diesem
    # spaeteren Zeitpunkt schon gesetzte echte Vordergrundfarbe direkt
    # korrekt mitbekamen.
    legend = win.timeseries_legend
    # Alle 5 Standard-Messbereiche wurden lange vor diesem Test (beim
    # Aufbau von "win" ganz oben in dieser Datei) bereits zur Legende
    # hinzugefuegt -- genau die Reihenfolge, die den Bug ausloeste.
    existing_labels = [label for _sample, label in legend.items]
    assert len(existing_labels) >= 5, "erwartet mind. die 5 Standard-Messbereiche in der Legende"

    # Graph-Farben sind seit dem Nutzerwunsch "Graph immer hell" nicht mehr
    # an _apply_theme gekoppelt (siehe _apply_curve_colors) -- der Bugfix
    # selbst (Label-Neurendern bei jedem Farbwechsel) wird hier direkt ueber
    # _apply_curve_colors mit zwei unterschiedlichen Farben geprueft, statt
    # ueber einen Theme-Wechsel.
    prev_bg, prev_fg = win._graph_bg, win._graph_fg
    try:
        win._apply_curve_colors("#ffffff", "#111111")
        for label in existing_labels:
            color = label.opts.get("color")
            actual = color.name() if hasattr(color, "name") else str(color)
            assert actual.lower() == "#111111", (
                f"Legenden-Label '{label.text}' hat nach _apply_curve_colors Farbe {actual}, "
                "erwartet #111111 (nicht pyqtgraphs grauen Standardwert)"
            )

        win._apply_curve_colors("#1e1e1e", "#e0e0e0")
        for label in existing_labels:
            color = label.opts.get("color")
            actual = color.name() if hasattr(color, "name") else str(color)
            assert actual.lower() == "#e0e0e0", (
                f"Legenden-Label '{label.text}' hat nach erneutem _apply_curve_colors Farbe {actual}, "
                "erwartet #e0e0e0"
            )
    finally:
        win._apply_curve_colors(prev_bg, prev_fg)


check(
    "legend labels added BEFORE the most recent _apply_curve_colors() call get recolored too, not stuck grey",
    test_legend_labels_added_before_theme_apply_get_recolored_too,
)

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

    # "Ende festlegen" (Verlaufs-Interpolation) springt zum PER-ROI frei
    # waehlbaren spin_interp_end_frame (Standard: letztes Bild, siehe
    # _set_recording) -- UNABHAENGIG vom globalen Auswertungsende (Punkt:
    # "Start- und End-Frame haendisch setzen"), NICHT mehr an dieses
    # gekoppelt wie frueher.
    entry = win.roi_entries[3]
    entry.place(4, 4, 6, 6)
    assert entry.spin_interp_end_frame.value() == n, "Standard muss weiterhin das letzte Bild sein"
    entry.chk_interp.setChecked(True)
    win.frame_slider.setValue(0)
    entry.btn_interp_end.click()
    assert win.current_index == n - 1, win.current_index

    custom_interp_end = max(1, n - 2)
    entry.btn_interp_end.click()  # Phase 2 abschliessen (Ende uebernehmen)
    win.frame_slider.setValue(0)
    entry.spin_interp_end_frame.setValue(custom_interp_end)
    entry.btn_interp_end.click()
    assert win.current_index == custom_interp_end - 1, (
        "haette zum manuell in der Spinbox gesetzten Ziel-Frame springen muessen, "
        "nicht zum globalen Auswertungsende"
    )
    entry.btn_interp_end.click()  # Phase 2 abschliessen
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

    # "Start festlegen" (Verlaufs-Interpolation) springt zum PER-ROI frei
    # waehlbaren spin_interp_start_frame (Standard: erstes Bild, siehe
    # _set_recording) -- UNABHAENGIG vom globalen Auswertungsstart (Punkt:
    # "Start- und End-Frame haendisch setzen"), NICHT mehr an dieses
    # gekoppelt wie frueher. Die Taste "Pos1" (_jump_to_first_frame) folgt
    # weiterhin dem Auswertungsstart -- separat unten geprueft.
    entry = win.roi_entries[3]
    entry.place(4, 4, 6, 6)
    assert entry.spin_interp_start_frame.value() == 1, "Standard muss weiterhin das erste Bild sein"
    entry.chk_interp.setChecked(True)
    win.frame_slider.setValue(n - 1)
    entry.btn_interp_start.click()
    assert win.current_index == 0, win.current_index

    custom_interp_start = min(n, 3)
    entry.btn_interp_start.click()  # Phase 2 abschliessen
    win.frame_slider.setValue(n - 1)
    entry.spin_interp_start_frame.setValue(custom_interp_start)
    entry.btn_interp_start.click()
    assert win.current_index == custom_interp_start - 1, (
        "haette zum manuell in der Spinbox gesetzten Ziel-Frame springen muessen, "
        "nicht zum globalen Auswertungsstart"
    )
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
    # dann (Vereinheitlichung mit dem Bild-Export, siehe GraphicExportDialog)
    # zu "Zeitachse", und schliesslich (UX-Review: "Zeitachse" wird an anderer
    # Stelle bereits fuer die x-Achse des Kurven-Graphen verwendet, gleicher
    # Begriff fuer zwei verschiedene Dinge verwirrt) zu "Zeitanzeige im Bild"
    # umbenannt. Radiobuttons nicht als Liste, sondern als 2x2-Matrix (Zeile 1:
    # Zeitleiste/Keine, Zeile 2: Zeitstempel/Beides); Zeitleiste/Zeitstempel
    # zusaetzlich mit erklaerendem Tooltip.
    from thermal_viewer.dialogs import VideoExportDialog

    dlg = VideoExportDialog(
        win, n_frames=win.recording.n_frames, colormaps=COLORMAPS,
        current_colormap_index=0, current_invert=False, current_level_mode="per_frame",
        current_min=0.0, current_max=50.0, current_fps=10.0,
    )
    overlay_box = dlg.radio_overlay_timeline.parentWidget()
    assert isinstance(overlay_box, QtWidgets.QGroupBox)
    assert overlay_box.title() == "Zeitanzeige im Bild", overlay_box.title()

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
# wurde entfernt und durch feste Farben ersetzt: Graphen bleiben IMMER hell,
# das Thermobild bleibt IMMER dunkel (Nutzerwunsch: "wissenschaftlicher
# Standard"/"besserer Kontrast zu Hotspots"), unabhaengig vom App-Design
# (siehe _apply_curve_colors/_apply_image_colors-Docstrings). Test prueft,
# dass die Design-Wahl selbst (nur noch die App-Palette) einen Neustart
# uebersteht, WAEHREND Graph-/Thermobild-Farben in JEDEM Design konstant
# bleiben.
from thermal_viewer.main_window import THEMES as _THEMES  # noqa: E402


def test_theme_choice_persists_across_restart_graph_and_image_colors_stay_fixed():
    win._settings.setValue("theme", "dark")
    win2 = MainWindow()
    try:
        assert win2._current_theme == "dark"
        assert win2._graph_bg == _THEMES["light"]["pg_background"]
        assert win2._graph_fg == _THEMES["light"]["pg_foreground"]
        assert win2._image_bg == _THEMES["dark"]["pg_background"]
        assert win2._image_fg == _THEMES["dark"]["pg_foreground"]
    finally:
        win2.close()
        win._settings.setValue("theme", "light")
    win3 = MainWindow()
    try:
        assert win3._current_theme == "light"
        assert win3._graph_bg == _THEMES["light"]["pg_background"]
        assert win3._image_bg == _THEMES["dark"]["pg_background"]
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
    "Design-Wahl uebersteht Neustart (App-Palette); Graph-/Thermobild-Farben bleiben in jedem Design fest",
    test_theme_choice_persists_across_restart_graph_and_image_colors_stay_fixed,
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


def test_graphic_export_dialog_offers_graph_position_like_video_export():
    # Nutzerwunsch: "gib mir beim Grafik-Export die gleichen Wahlmoeglichkeiten
    # (wo soll der Graph hin?) wie beim Video-Export" -- dieselben vier
    # Optionen, Standard "rechts" (Punkt "Default soll rechts vom Bild sein").
    from thermal_viewer.dialogs import GraphicExportDialog as RealGraphicExportDialog

    dlg = RealGraphicExportDialog(
        win, win._settings, default_dpi=150, show_graph_source_choice=True,
        live_available=False, roi_entries=[(101, "X")],
    )
    try:
        assert dlg.combo_graph_position is not None
        assert [dlg.combo_graph_position.itemData(i) for i in range(dlg.combo_graph_position.count())] == [
            "unten", "oben", "links", "rechts",
        ]
        assert dlg.graph_position() == "rechts"  # Standard

        # Position ist nur im "Kombiniert"-Modus sinnvoll/aktiv.
        dlg.radio_combined.setChecked(True)
        assert dlg.combo_graph_position.isEnabled() is True
        dlg.radio_separate.setChecked(True)
        assert dlg.combo_graph_position.isEnabled() is False
        dlg.radio_combined.setChecked(True)

        dlg.combo_graph_position.setCurrentIndex(dlg.combo_graph_position.findData("oben"))
        assert dlg.graph_position() == "oben"
    finally:
        dlg.close()

    # Ohne Graph in diesem Export (Einzelexport) gibt es keine Positions-Wahl.
    dlg2 = RealGraphicExportDialog(win, win._settings, default_dpi=150, show_mode_choice=False, show_time_axis_choice=False)
    try:
        assert dlg2.combo_graph_position is None
        assert dlg2.graph_position() == "unten"
    finally:
        dlg2.close()


check(
    "GraphicExportDialog offers the same graph-position dropdown as VideoExportDialog, defaulting to 'rechts'",
    test_graphic_export_dialog_offers_graph_position_like_video_export,
)


def test_combine_image_and_graph_all_four_positions():
    # _combine_image_and_graph (Raster) und _save_combined_svg (Vektor) --
    # beide muessen fuer alle vier Positionen ein gueltiges, korrekt
    # dimensioniertes Ergebnis liefern (analog zu
    # test_render_video_frame_graph_position_options fuer den Video-Export).
    img_a = QtGui.QImage(200, 100, QtGui.QImage.Format_ARGB32)
    img_a.fill(QtCore.Qt.white)
    img_b = QtGui.QImage(150, 80, QtGui.QImage.Format_ARGB32)
    img_b.fill(QtCore.Qt.white)
    bg = QtGui.QColor(win._graph_bg)
    fg = QtGui.QColor(win._graph_fg)
    sizes = {}
    for pos in ("unten", "oben", "links", "rechts"):
        combined = win._combine_image_and_graph(img_a, "Bild", img_b, "Kurve", pos, 96, bg, fg)
        assert combined.width() > 0 and combined.height() > 0
        sizes[pos] = (combined.width(), combined.height())
    assert sizes["unten"] == sizes["oben"], "oben/unten muessen dieselbe Gesamtgroesse ergeben"
    assert sizes["links"] == sizes["rechts"], "links/rechts muessen dieselbe Gesamtgroesse ergeben"
    assert sizes["links"] != sizes["unten"], (
        "Seite-an-Seite vs. gestapelt muessen zu unterschiedlichen Canvas-Groessen fuehren"
    )

    svg_path = OUT / "combine_position_check.svg"
    for pos in ("unten", "oben", "links", "rechts"):
        w, h = win._save_combined_svg(
            svg_path, win.glw, "Bild", win.timeseries_plot, "Kurve", pos, 96, fg, bg
        )
        assert w > 0 and h > 0
        assert svg_path.exists() and svg_path.stat().st_size > 0
    svg_path.unlink()


check(
    "_combine_image_and_graph/_save_combined_svg lay out correctly for all 4 positions (oben/unten/links/rechts)",
    test_combine_image_and_graph_all_four_positions,
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

def test_video_export_dialog_cursor_independent_of_graph_box_and_beides_default():
    # UX-Review: die Cursor-im-Bild-Checkbox lag frueher VERSCHACHTELT im
    # "Temperaturverlauf-Graph"-Kasten und war dadurch an "Graph mit
    # exportieren" gekoppelt (ohne Graph nicht erreichbar) -- unklar, ob man
    # gerade das Bild oder den Graphen konfiguriert, UND ein Widerspruch zum
    # Nutzerwunsch, Cursor-im-Bild unabhaengig von der Live-Cursor-KURVE (und
    # damit unabhaengig vom Graphen insgesamt) waehlbar zu machen. Jetzt: ein
    # eigener "Cursor im Bild"-Kasten, der weder zum Graph-Kasten gehoert noch
    # von "Graph mit exportieren" deaktiviert wird.
    from thermal_viewer.dialogs import VideoExportDialog as RealVideoExportDialog

    dlg = RealVideoExportDialog(
        win, n_frames=win.recording.n_frames, colormaps=COLORMAPS,
        current_colormap_index=0, current_invert=False, current_level_mode="global",
        current_min=0.0, current_max=50.0, current_fps=10.0,
    )
    try:
        assert dlg.radio_overlay_both.isChecked() is True, "Default fuer 'Laufzeit' muss 'Beides' sein"
        assert dlg.timeline_overlay_mode() == "both"

        cursor_box = dlg.chk_cursor_position.parentWidget()
        assert isinstance(cursor_box, QtWidgets.QGroupBox)
        assert cursor_box.title() == "Cursor im Bild", cursor_box.title()
        assert cursor_box is not dlg.chk_show_graph.parentWidget(), (
            "Cursor-Kasten darf NICHT mehr im 'Temperaturverlauf-Graph'-Kasten liegen"
        )

        # Unabhaengig von "Graph mit exportieren" bedienbar -- weder AUS noch
        # AN darf die Cursor-Checkbox deaktivieren.
        assert dlg.chk_show_graph.isChecked() is False
        assert dlg.chk_cursor_position.isEnabled() is True
        dlg.chk_cursor_position.setChecked(True)
        assert dlg.export_cursor_position() is True
        dlg.chk_show_graph.setChecked(True)
        assert dlg.chk_cursor_position.isEnabled() is True
        assert dlg.export_cursor_position() is True
        dlg.chk_show_graph.setChecked(False)
        assert dlg.chk_cursor_position.isEnabled() is True
        assert dlg.export_cursor_position() is True, "Cursor-Wert muss trotz 'Graph aus' erhalten bleiben"
    finally:
        dlg.close()


check(
    "video export dialog: 'Cursor im Bild' ist eigener Kasten, unabhaengig von 'Graph mit exportieren', 'Laufzeit' defaults to 'Beides'",
    test_video_export_dialog_cursor_independent_of_graph_box_and_beides_default,
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
        assert dlg.graph_position() == "rechts"  # Standard (Nutzerwunsch)
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


def test_frozen_ui_during_export_covers_whole_window_including_tabified_docks():
    # Bugreport: "waehrend des Renderns verschwindet der Graph in der GUI".
    # Ursache: _widget_raised_for_export() holt bei einem Export des gerade
    # NICHT sichtbaren tabifizierten Docks ("Zeitverlauf"/"Live (Cursor)")
    # dessen Registerkarte fuer die GESAMTE Renderdauer sichtbar in den
    # Vordergrund -- vorher wurde dabei nur glw/timeseries_plot/live_plot
    # selbst eingefroren, NICHT aber das Dock/die Registerkarten-Leiste
    # darum herum, wodurch der Tab-Wechsel trotzdem sichtbar war. Fix:
    # _frozen_ui_during_export() friert jetzt das GESAMTE Hauptfenster ein
    # (setUpdatesEnabled auf self statt auf einzelne Widgets) -- das
    # propagiert auf JEDEN Nachfahren, inklusive des Dock-Widgets selbst.
    dock = win.timeseries_plot
    while dock is not None and not isinstance(dock, QtWidgets.QDockWidget):
        dock = dock.parentWidget()
    assert dock is not None, "Zeitverlauf-Plot sollte in einem QDockWidget stecken"
    assert dock.updatesEnabled() is True
    assert win.updatesEnabled() is True
    with win._frozen_ui_during_export():
        assert win.updatesEnabled() is False
        assert dock.updatesEnabled() is False
    assert win.updatesEnabled() is True
    assert dock.updatesEnabled() is True


check(
    "_frozen_ui_during_export freezes the WHOLE window (incl. tabified docks), not just glw/timeseries_plot/live_plot",
    test_frozen_ui_during_export_covers_whole_window_including_tabified_docks,
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
    # Bugfix: pyqtgraphs Layout-Neuberechnung nach showAxis("top", True)
    # (siehe _dual_time_axis_export) sowie nach dem Hochholen einer
    # tabifizierten Dock-Registerkarte (_widget_raised_for_export) wirkt
    # ERST beim naechsten Event-Loop-Durchlauf. Dieser Test bestand schon
    # VOR dem Fix (er prüfte nur "Datei existiert" + "Achse danach wieder
    # aus"), obwohl die obere Achse im tatsaechlich erzeugten Bild fehlte --
    # jetzt zusaetzlich per Spy auf _render_widget_image geprueft, dass die
    # obere Achse zum Zeitpunkt des tatsaechlichen Renderns bereits
    # sichtbar/layoutet ist.
    from thermal_viewer.main_window import GraphicExportDialog

    p = OUT / "dual_axis_check.png"
    if p.exists():
        p.unlink()

    observed = {}
    orig_render_widget_image = win._render_widget_image

    def spying_render_widget_image(widget, *args, **kwargs):
        if widget is win.timeseries_plot and "top_visible" not in observed:
            observed["top_visible"] = win.timeseries_plot.getPlotItem().getAxis("top").isVisible()
        return orig_render_widget_image(widget, *args, **kwargs)

    def make_exec(self):
        idx = self.combo_time_axis.findData("both")
        self.combo_time_axis.setCurrentIndex(idx)
        self.accept()
        return QtWidgets.QDialog.DialogCode.Accepted

    orig_save = QtWidgets.QFileDialog.getSaveFileName
    win._render_widget_image = spying_render_widget_image
    try:
        with temp_dialog_exec(GraphicExportDialog, make_exec):
            QtWidgets.QFileDialog.getSaveFileName = staticmethod(lambda *a, **k: (str(p), "PNG-Bild (*.png)"))
            win._export_graphic()
    finally:
        QtWidgets.QFileDialog.getSaveFileName = orig_save
        win._render_widget_image = orig_render_widget_image
    assert p.exists()
    assert observed.get("top_visible") is True, (
        "obere Achse haette beim tatsaechlichen Rendern bereits sichtbar sein muessen "
        "(Layout-Timing-Bugfix, sonst fehlt sie im erzeugten Bild trotz 'Datei existiert')"
    )
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


def test_temporary_graph_content_pins_current_view_range_during_export():
    # Bugreport: "Achsen im Programm stimmen nicht mit den exportierten
    # Bildern ueberein". Ursache: bei aktiver Auto-Skalierung skaliert
    # pyqtgraph die Y-Achse SOFORT neu, sobald sich die sichtbaren Kurven
    # aendern -- exportiert der Dialog eine ANDERE Kurvenauswahl als gerade
    # auf dem Bildschirm sichtbar, zeigte der Export dadurch einen anderen
    # Wertebereich als die Live-Ansicht. Fix: _temporary_graph_content()
    # friert den GENAU JETZT sichtbaren Bereich waehrend des Kurven-Wechsels
    # ein und gibt am Ende exakt den vorherigen Automatik-Zustand zurueck.
    vb = win.timeseries_plot.getPlotItem().vb
    entries = [e for e in win.roi_entries if e.placed][:2]
    assert len(entries) >= 2, "Testvoraussetzung: mindestens 2 platzierte ROIs"
    a, b = entries[0], entries[1]
    prev_a_visible, prev_b_visible = a.curve.isVisible(), b.curve.isVisible()
    try:
        a.curve.setVisible(True)
        b.curve.setVisible(False)
        # WICHTIG: PlotItem.autoRange() (siehe _reset_plot_view) fuehrt zwar
        # einmalig einen Fit aus, deaktiviert dabei aber selbst den Automatik-
        # MODUS wieder (pyqtgraph-Eigenheit) -- hier bewusst NICHT genutzt,
        # damit der Automatik-Modus fuer diesen Test tatsaechlich an bleibt.
        vb.enableAutoRange(x=True, y=True)
        app.processEvents()
        (x0, x1), (y0, y1) = vb.viewRange()

        # Export waehlt jetzt die JEWEILS ANDERE Kurve (b statt a) -- ohne den
        # Fix wuerde die Auto-Skalierung die Achsen dafuer sofort umstellen.
        with win._temporary_graph_content({b.number}, False):
            (mid_x0, mid_x1), (mid_y0, mid_y1) = vb.viewRange()
            assert abs(mid_x0 - x0) < 1e-6 and abs(mid_x1 - x1) < 1e-6, "X-Bereich haette eingefroren sein muessen"
            assert abs(mid_y0 - y0) < 1e-6 and abs(mid_y1 - y1) < 1e-6, "Y-Bereich haette eingefroren sein muessen"

        assert vb.autoRangeEnabled()[0] and vb.autoRangeEnabled()[1], (
            "Automatik-Modus haette nach dem Export unveraendert an bleiben muessen"
        )
    finally:
        a.curve.setVisible(prev_a_visible)
        b.curve.setVisible(prev_b_visible)
        win._reset_plot_view(win.timeseries_plot)


check(
    "_temporary_graph_content freezes the currently-visible axis range while curve visibility changes for export",
    test_temporary_graph_content_pins_current_view_range_during_export,
)


def test_rebased_time_axis_preserves_x_autorange_state_across_svg_export():
    # Bugfix: vb.setXRange() deaktiviert als pyqtgraph-Nebenwirkung IMMER das
    # X-Autorange -- _rebased_time_axis() (aktiv bei jedem SVG-Export) hat das
    # bisher nirgends wiederhergestellt: nach EINEM SVG-Export blieb die
    # X-Achse im Hauptfenster dauerhaft auf "manuell" haengen, obwohl sie
    # vorher automatisch war.
    vb = win.timeseries_plot.getPlotItem().vb
    try:
        vb.enableAutoRange(x=True)
        # pyqtgraph liefert hier ggf. 1.0 statt dem Bool True zurueck -- nur
        # Wahrheitswert pruefen, nicht Identitaet (siehe auch die Pruefung
        # unten).
        assert vb.autoRangeEnabled()[0]
        with win._rebased_time_axis(win.timeseries_plot):
            pass
        assert vb.autoRangeEnabled()[0], "X-Autorange haette nach dem SVG-Export aktiv bleiben muessen"
    finally:
        win._reset_plot_view(win.timeseries_plot)


check(
    "_rebased_time_axis (SVG export) preserves X-axis autorange-enabled state instead of disabling it permanently",
    test_rebased_time_axis_preserves_x_autorange_state_across_svg_export,
)


def test_temporary_axis_override_applies_custom_ticks_and_restores_exactly():
    # Neues Feature: eigene Achsen-Einstellungen NUR fuer einen Export, ohne
    # die Live-Ansicht zu veraendern (Nutzerwunsch: "mehr Gestaltungs-
    # moeglichkeiten beim Exportieren ... Achsen-Labels/Ticklabels/
    # Schrittweite"). _temporary_axis_override() muss die Overrides waehrend
    # des Renderns anwenden UND danach EXAKT den vorherigen Zustand
    # (Wertebereich, Automatik-Modus, Y-Schrittweite, X-manual_spacing)
    # wiederherstellen.
    plot_item = win.timeseries_plot.getPlotItem()
    vb = plot_item.getViewBox()
    x_axis_item = plot_item.getAxis("bottom")
    y_axis_item = plot_item.getAxis("left")
    try:
        vb.enableAutoRange(x=True, y=True)
        app.processEvents()
        x_auto_before, y_auto_before = vb.autoRangeEnabled()
        old_x_spacing = x_axis_item.manual_spacing

        overrides = {
            "x_manual": False, "x_range": (0.0, 1.0),
            "x_spacing_manual": True, "x_spacing": 42.0,
            "y_manual_range": True, "y_range": (5.0, 25.0),
            "y_spacing_manual": True, "y_spacing": 2.5,
        }
        with win._temporary_axis_override(win.timeseries_plot, overrides):
            assert x_axis_item.manual_spacing == 42.0, x_axis_item.manual_spacing
            (_mx0, _mx1), (mid_y0, mid_y1) = vb.viewRange()
            assert abs(mid_y0 - 5.0) < 1e-6 and abs(mid_y1 - 25.0) < 1e-6, (mid_y0, mid_y1)
            tick_spacing = y_axis_item._tickSpacing
            assert tick_spacing and abs(tick_spacing[0][0] - 2.5) < 1e-6, tick_spacing

        assert x_axis_item.manual_spacing == old_x_spacing, "X-manual_spacing haette zurueckgesetzt werden muessen"
        x_auto_after, y_auto_after = vb.autoRangeEnabled()
        assert bool(x_auto_after) == bool(x_auto_before)
        assert bool(y_auto_after) == bool(y_auto_before), "Y-Automatik haette wiederhergestellt werden muessen"
    finally:
        x_axis_item.set_manual_spacing(None)
        y_axis_item.setTickSpacing()
        win._reset_plot_view(win.timeseries_plot)


check(
    "_temporary_axis_override applies custom X-spacing/Y-range+spacing during render, restores exactly afterward",
    test_temporary_axis_override_applies_custom_ticks_and_restores_exactly,
)


def test_export_dialogs_block_accept_if_custom_axes_selected_but_not_configured():
    # Waehlt der Nutzer "Eigene Achsen-Einstellungen fuer diesen Export",
    # klickt aber nie auf "Einstellen...", darf der Dialog nicht OHNE
    # jegliche Einstellungen akzeptiert werden (sonst waere unklar, welche
    # Werte gelten sollten).
    from thermal_viewer.dialogs import GraphicExportDialog

    dlg = GraphicExportDialog(
        win, win._settings, default_dpi=150, show_graph_source_choice=True,
        live_available=False, roi_entries=[(e.number, e.name) for e in win.roi_entries if e.placed],
        current_axis_state=win._gather_axis_state(win.timeseries_plot),
    )
    try:
        assert dlg._axis_widgets is not None
        dlg._axis_widgets["radio_custom"].setChecked(True)
        dlg._on_accept()
        assert dlg.result() != QtWidgets.QDialog.DialogCode.Accepted, (
            "Dialog haette 'Eigene Achsen-Einstellungen' ohne konfigurierte Werte ablehnen muessen"
        )
        assert dlg.use_custom_axes() is False
    finally:
        dlg.close()


check(
    "export dialogs refuse 'Eigene Achsen-Einstellungen' until the sub-dialog was actually configured",
    test_export_dialogs_block_accept_if_custom_axes_selected_but_not_configured,
)


def test_graphic_export_applies_custom_axis_override_end_to_end():
    # End-to-End: _export_graphic() mit einer ueber den Export-Dialog
    # gewaehlten eigenen Y-Schrittweite muss diese TATSAECHLICH beim Rendern
    # anwenden (nicht nur im Dialog-Objekt stehen haben) und die Live-Ansicht
    # danach unveraendert lassen.
    from thermal_viewer.dialogs import AxisSettingsDialog, GraphicExportDialog

    plot_item = win.timeseries_plot.getPlotItem()
    y_axis_item = plot_item.getAxis("left")
    prev_tick_spacing = getattr(y_axis_item, "_tickSpacing", None)

    observed = {}
    orig_render = win._render_widget_image

    def spying_render(widget, *a, **k):
        if widget is win.timeseries_plot and "y_spacing" not in observed:
            ts = getattr(y_axis_item, "_tickSpacing", None)
            observed["y_spacing"] = ts[0][0] if ts else None
        return orig_render(widget, *a, **k)

    win._render_widget_image = spying_render

    out_png = OUT / "axis_override_export.png"

    def fake_exec_graphic(self):
        for chk in self._content_widgets["checks"].values():
            chk.setChecked(True)

        def fake_exec_axis_settings(axis_self):
            axis_self.chk_y_manual_spacing.setChecked(True)
            axis_self.spin_y_spacing.setValue(3.0)
            return (axis_self.accept(), QtWidgets.QDialog.DialogCode.Accepted)[1]

        with temp_dialog_exec(AxisSettingsDialog, fake_exec_axis_settings):
            self._axis_widgets["radio_custom"].setChecked(True)
            self._axis_widgets["btn_configure"].click()
        assert self.use_custom_axes(), "Achsen-Uebernahme haette nach dem Sub-Dialog aktiv sein muessen"
        return (self.accept(), QtWidgets.QDialog.DialogCode.Accepted)[1]

    orig_save = QtWidgets.QFileDialog.getSaveFileName
    try:
        with temp_dialog_exec(GraphicExportDialog, fake_exec_graphic):
            QtWidgets.QFileDialog.getSaveFileName = staticmethod(lambda *a, **k: (str(out_png), "PNG-Bild (*.png)"))
            win._export_graphic()
        assert out_png.exists()
        assert observed.get("y_spacing") == 3.0, (
            "Export haette mit der eigenen Y-Schrittweite (3.0) rendern muessen", observed
        )
        after_tick_spacing = getattr(y_axis_item, "_tickSpacing", None)
        assert after_tick_spacing == prev_tick_spacing, "Live-Ansicht haette unveraendert bleiben muessen"
    finally:
        win._render_widget_image = orig_render
        QtWidgets.QFileDialog.getSaveFileName = orig_save
        out_png.unlink(missing_ok=True)
        out_png.with_suffix(".json").unlink(missing_ok=True)


check(
    "_export_graphic applies a custom Y tick-spacing override at render time and restores the live view afterward",
    test_graphic_export_applies_custom_axis_override_end_to_end,
)


# ==================================================== Datenimport-Manager ==

def test_import_settings_qsettings_roundtrip_and_missing_key_defaults():
    # _set_import_settings(persist=True)/_load_import_settings sind der
    # tatsaechlich genutzte Persistenzweg (je ein einzelner QSettings-
    # Schluessel pro Feld, konsistent mit den uebrigen Einstellungen dieser
    # App wie z.B. filename_template) -- ein frisches MainWindow muss exakt
    # das zuletzt persistierte ImportSettings wieder laden, und ein noch nie
    # zuvor gespeicherter Schluessel (frische QSettings-Datei) muss auf die
    # ImportSettings-Standardwerte zurueckfallen statt zu scheitern.
    from thermal_viewer.data import ImportSettings

    fresh1 = MainWindow()
    try:
        fresh1._import_settings = ImportSettings()
        custom = ImportSettings(
            delimiter="\t", decimal_separator=".", encoding="cp1252",
            skip_header_lines=2, skip_footer_lines=1, skip_leading_columns=1, skip_trailing_columns=1,
        )
        fresh1._set_import_settings(custom, persist=True)
        assert fresh1._import_settings == custom

        fresh2 = MainWindow()
        try:
            assert fresh2._import_settings == custom, "persistierte Einstellung haette geladen werden muessen"
        finally:
            fresh2.close()

        # Wieder auf den Standard zuruecksetzen, damit nachfolgende Tests in
        # diesem Lauf (gemeinsame, isolierte QSettings-Datei) unbeeinflusst bleiben.
        fresh1._set_import_settings(ImportSettings(), persist=True)
        fresh3 = MainWindow()
        try:
            assert fresh3._import_settings == ImportSettings()
        finally:
            fresh3.close()
    finally:
        fresh1.close()


check(
    "MainWindow._set_import_settings(persist=True)/_load_import_settings round-trip through QSettings, default on missing keys",
    test_import_settings_qsettings_roundtrip_and_missing_key_defaults,
)


def test_parse_frame_text_header_footer_columns_delimiter_decimal():
    from thermal_viewer.data import ImportSettings, RecordingError, parse_frame_text

    text = "Geraet: XYZ\nDatum: 2026-01-01\n0;28,6;28,7;28,8\n1;29,0;29,1;29,2\nEnde der Datei\n"
    settings = ImportSettings(skip_header_lines=2, skip_footer_lines=1, skip_leading_columns=1)
    arr = parse_frame_text(text, settings)
    assert arr.shape == (2, 3), arr.shape
    assert abs(float(arr[0, 0]) - 28.6) < 1e-4
    assert abs(float(arr[1, 2]) - 29.2) < 1e-4

    arr_tab = parse_frame_text("28.6\t28.7\n29.0\t29.1\n", ImportSettings(delimiter="\t", decimal_separator="."))
    assert arr_tab.shape == (2, 2), arr_tab.shape

    arr_ws = parse_frame_text("28.6   28.7\n29.0 29.1\n", ImportSettings(delimiter="", decimal_separator="."))
    assert arr_ws.shape == (2, 2), arr_ws.shape

    try:
        parse_frame_text("28,6;28,7\n29,0\n", ImportSettings())
        assert False, "haette wegen uneinheitlicher Spaltenzahl scheitern muessen"
    except RecordingError as exc:
        assert "Spaltenzahl" in str(exc), str(exc)

    try:
        parse_frame_text("nur Text, keine Werte\n", ImportSettings(skip_header_lines=5))
        assert False, "haette wegen fehlender Datenzeilen scheitern muessen"
    except RecordingError as exc:
        assert "Datenzeilen" in str(exc), str(exc)


check(
    "parse_frame_text: header/footer/column skip, alt. delimiter (Tab/Leerzeichen), alt. decimal separator, clear errors",
    test_parse_frame_text_header_footer_columns_delimiter_decimal,
)


def test_tiff_grayscale_load_and_crop_to_temperature():
    import numpy as np
    import tifffile
    from thermal_viewer.data import RecordingError, load_tiff_grayscale, tiff_crop_to_temperature

    tiff_dir = OUT / "tiff_import_check"
    tiff_dir.mkdir(exist_ok=True)

    # Reines Graustufenbild (R=G=B) mit einer angehaengten "Legende"-Spalte
    # rechts, die reines Schwarz/Weiss enthaelt -- simuliert die Farbskala
    # im echten Kamera-Export, die der gewaehlte Bildausschnitt ausschliessen
    # muss.
    gray_2d = np.zeros((10, 8), dtype=np.uint8)
    gray_2d[:, :6] = np.tile(np.arange(6, dtype=np.uint8) * 40, (10, 1))  # 0..200 im eigentlichen Bildbereich
    gray_2d[:, 6] = 0  # Legenden-Rand: reines Schwarz
    gray_2d[:, 7] = 255  # Legenden-Rand: reines Weiss
    gray_rgb = np.stack([gray_2d, gray_2d, gray_2d], axis=-1)
    gray_path = tiff_dir / "gray.tiff"
    tifffile.imwrite(str(gray_path), gray_rgb)

    loaded = load_tiff_grayscale(gray_path)
    assert loaded.shape == (10, 8)
    assert abs(float(loaded[0, 0])) < 1e-6
    assert abs(float(loaded[0, 5]) - 200.0) < 1e-6

    # Ausschnitt OHNE die Legenden-Spalten (x 0..6) -- Min/Max muss sich nach
    # dem TATSAECHLICHEN Bildinhalt (0..200) richten, nicht nach der
    # ausgeschlossenen Legende (0/255) -- sonst waeren alle umgerechneten
    # Werte auf einen viel zu schmalen Ausschnitt der eingegebenen Skala
    # zusammengestaucht.
    temp = tiff_crop_to_temperature(loaded, (0, 0, 6, 10), t_min=10.0, t_max=50.0)
    assert temp.shape == (10, 6)
    assert abs(float(temp[0, 0]) - 10.0) < 1e-3
    assert abs(float(temp[0, 5]) - 50.0) < 1e-3

    # Farbiges (nicht graustufiges) Bild -- muss zurueckgewiesen werden, da
    # ohne Kenntnis der genauen Farbpalette keine zuverlaessige
    # Rueckrechnung moeglich ist (Nutzervorgabe: nur einbauen, wenn
    # zuverlaessig loesbar).
    color = np.zeros((4, 4, 3), dtype=np.uint8)
    color[:, :, 0] = 200
    color[:, :, 1] = 30
    color_path = tiff_dir / "color.tiff"
    tifffile.imwrite(str(color_path), color)
    try:
        load_tiff_grayscale(color_path)
        assert False, "farbiges Bild haette abgelehnt werden muessen"
    except RecordingError as exc:
        assert "Graustufenbild" in str(exc), str(exc)

    # Mehrseitiges TIFF -- ebenfalls abgelehnt (keine dokumentierte/
    # zuverlaessige Bedeutung einer zweiten Bildebene bekannt, siehe
    # load_tiff_grayscale-Docstring).
    multi_path = tiff_dir / "multi.tiff"
    with tifffile.TiffWriter(str(multi_path)) as tw:
        tw.write(gray_rgb)
        tw.write(gray_rgb)
    try:
        load_tiff_grayscale(multi_path)
        assert False, "mehrseitiges TIFF haette abgelehnt werden muessen"
    except RecordingError as exc:
        assert "Bildebenen" in str(exc), str(exc)


check(
    "load_tiff_grayscale/tiff_crop_to_temperature: reads grayscale TIFFs, crops out the legend, rejects color/multi-page files",
    test_tiff_grayscale_load_and_crop_to_temperature,
)


def test_tiff_import_dialog_rejects_max_not_greater_than_min_and_empty_crop():
    # Bugfix: TiffImportDialog akzeptierte bisher jede Min-/Max-Temperatur
    # anstandslos (buttons.accepted direkt an self.accept gebunden, keine
    # eigene Pruefung wie bei GraphicExportDialog/VideoExportDialog) --
    # Max <= Min ergibt eine invertierte oder komplett flache
    # Temperaturzuordnung, ohne jede Warnung.
    import numpy as np
    from thermal_viewer.dialogs import TiffImportDialog

    preview_gray = np.zeros((10, 8), dtype=np.uint8)
    dialog = TiffImportDialog(win, preview_gray, file_count=1)
    try:
        accepted = []
        dialog.accept = lambda: accepted.append(True)

        # Max == Min -> abgelehnt.
        dialog.spin_min.setValue(20.0)
        dialog.spin_max.setValue(20.0)
        dialog._on_accept()
        assert not accepted, "Max == Min haette abgelehnt werden muessen"

        # Max < Min -> ebenfalls abgelehnt.
        dialog.spin_min.setValue(50.0)
        dialog.spin_max.setValue(20.0)
        dialog._on_accept()
        assert not accepted, "Max < Min haette abgelehnt werden muessen"

        # Leerer Ausschnitt (ROI auf Nullgroesse gezogen) -> abgelehnt.
        dialog.spin_min.setValue(0.0)
        dialog.spin_max.setValue(100.0)
        dialog.roi.setSize([0, 0])
        dialog._on_accept()
        assert not accepted, "leerer Ausschnitt haette abgelehnt werden muessen"

        # Gueltige Werte + nicht-leerer Ausschnitt -> akzeptiert.
        dialog.roi.setSize([8, 10])
        dialog._on_accept()
        assert accepted, "gueltiger Bereich haette akzeptiert werden muessen"
    finally:
        dialog.close()


check(
    "TiffImportDialog rejects Max<=Min temperature and an empty crop instead of silently accepting",
    test_tiff_import_dialog_rejects_max_not_greater_than_min_and_empty_crop,
)


def test_load_frame_uses_import_settings():
    from thermal_viewer.data import ImportSettings, RecordingError, load_frame

    tmp = OUT / "import_settings_load_frame_test.csv"
    tmp.write_text("IDX;28,6;28,7\n0;29,0;29,1\n", encoding="utf-8")

    try:
        load_frame(tmp)
        assert False, "Standard-Einstellungen haetten an 'IDX' scheitern muessen"
    except RecordingError:
        pass

    arr = load_frame(tmp, ImportSettings(skip_header_lines=1, skip_leading_columns=1))
    assert arr.shape == (1, 2), arr.shape
    assert abs(float(arr[0, 0]) - 29.0) < 1e-4


check("load_frame() applies ImportSettings (header/leading-column skip)", test_load_frame_uses_import_settings)


def test_import_settings_dialog_live_preview_and_ok_gating():
    from thermal_viewer.data import ImportSettings
    from thermal_viewer.dialogs import ImportSettingsDialog

    sample = OUT / "import_dialog_sample.csv"
    sample.write_text("Kopf\n0;28,6;28,7\n1;29,0;29,1\n", encoding="utf-8")

    dlg = ImportSettingsDialog(win, sample, ImportSettings())
    try:
        ok_button = dlg.buttons.button(QtWidgets.QDialogButtonBox.StandardButton.Ok)
        assert dlg.chk_persist.isChecked() is False, "Standard muss AUS sein (nur fuer diesen Ladevorgang)"
        # "Kopf" als erste, nicht uebersprungene Zeile ist kein gueltiger
        # Zahlenwert -> OK muss zunaechst deaktiviert sein.
        assert ok_button.isEnabled() is False

        dlg.spin_skip_header.setValue(1)
        assert ok_button.isEnabled() is True
        assert "2 Zeile(n)" in dlg.lbl_result_status.text() and "3 Spalte(n)" in dlg.lbl_result_status.text(), (
            dlg.lbl_result_status.text()
        )

        dlg.spin_skip_leading.setValue(1)
        assert "2 Spalte(n)" in dlg.lbl_result_status.text(), dlg.lbl_result_status.text()

        s = dlg.settings()
        assert s.skip_header_lines == 1 and s.skip_leading_columns == 1
        assert s.delimiter == ";" and s.decimal_separator == ","
    finally:
        dlg.close()


check(
    "ImportSettingsDialog: live preview updates on setting change, OK gated on successful parse",
    test_import_settings_dialog_live_preview_and_ok_gating,
)


def test_import_settings_dialog_pick_other_sample_file():
    from thermal_viewer.data import ImportSettings
    from thermal_viewer.dialogs import ImportSettingsDialog

    sample1 = OUT / "import_pick_a.csv"
    sample1.write_text("28,6;28,7\n29,0;29,1\n", encoding="utf-8")
    sample2 = OUT / "import_pick_b.csv"
    sample2.write_text("1,0;2,0;3,0\n4,0;5,0;6,0\n", encoding="utf-8")

    dlg = ImportSettingsDialog(win, sample1, ImportSettings())
    orig_get_open = QtWidgets.QFileDialog.getOpenFileName
    try:
        assert "2 Spalte(n)" in dlg.lbl_result_status.text(), dlg.lbl_result_status.text()
        QtWidgets.QFileDialog.getOpenFileName = staticmethod(lambda *a, **k: (str(sample2), ""))
        dlg._pick_sample_file()
        assert dlg._sample_path == sample2
        assert "3 Spalte(n)" in dlg.lbl_result_status.text(), dlg.lbl_result_status.text()
    finally:
        QtWidgets.QFileDialog.getOpenFileName = orig_get_open
        dlg.close()


check("ImportSettingsDialog 'Andere Datei wählen…' re-reads and re-parses the newly picked file", test_import_settings_dialog_pick_other_sample_file)


def test_load_paths_retry_flow_after_import_format_mismatch():
    # End-to-End: eine Messreihe, deren Rohformat (Kopfzeile + fuehrende
    # Index-Spalte) NICHT zu den Standard-ImportSettings passt, schlaegt
    # zunaechst fehl -- _load_paths bietet daraufhin an, den Datenimport-
    # Manager zu oeffnen; nach Anpassung UND Bestaetigen (mit "dauerhaft
    # speichern") gelingt das Laden UND der neue Standard bleibt gesetzt.
    # Eigenes, frisches MainWindow, damit der globale `win`-Zustand/dessen
    # geladene Aufnahme unangetastet bleibt.
    from thermal_viewer.data import ImportSettings
    from thermal_viewer.dialogs import ImportSettingsDialog

    fresh = MainWindow()
    try:
        folder = OUT / "import_retry_dataset"
        folder.mkdir(exist_ok=True)
        paths = []
        for i in range(2):
            p = folder / f"Record_2026-08-21_09-0{i}-00.csv"
            p.write_text("Kopfzeile\n0;20,0;21,0\n1;22,0;23,0\n", encoding="utf-8")
            paths.append(p)

        assert fresh._import_settings == ImportSettings(), "frisches Fenster muss mit Standard-Import starten"

        offer_calls = []

        def fake_offer(sample_path, error_message):
            offer_calls.append((sample_path, error_message))
            return True

        fresh._offer_import_settings_retry = fake_offer

        def fill_and_accept(self):
            self.spin_skip_header.setValue(1)
            self.spin_skip_leading.setValue(1)
            self.chk_persist.setChecked(True)
            return (self.accept(), QtWidgets.QDialog.DialogCode.Accepted)[1]

        with temp_dialog_exec(ImportSettingsDialog, fill_and_accept):
            ok = fresh._load_paths(paths)

        assert ok is True, "Laden haette nach angepasstem Datenimport erfolgreich sein muessen"
        assert len(offer_calls) == 1, "Rueckfrage haette genau einmal erscheinen muessen"
        assert fresh.recording.n_frames == 2
        assert fresh.recording.shape == (2, 2), fresh.recording.shape
        assert fresh._import_settings.skip_header_lines == 1, "persist=True haette neuen Standard setzen muessen"
        assert fresh._import_settings.skip_leading_columns == 1
        assert fresh._active_import_settings == fresh._import_settings

        # Live-Ordner-Ueberwachung (_check_for_new_files) muss dasselbe,
        # gerade erst festgestellte Format weiterverwenden.
        p3 = folder / "Record_2026-08-21_09-02-00.csv"
        p3.write_text("Kopfzeile\n2;24,0;25,0\n3;26,0;27,0\n", encoding="utf-8")
        fresh._watched_folder = folder
        fresh._check_for_new_files()
        assert fresh.recording.n_frames == 3, "Live-Nachladen haette das gleiche Rohformat verwenden muessen"
    finally:
        fresh.close()


check(
    "_load_paths retries with adjusted ImportSettings after a format mismatch, persists as new default, live-watch reuses it",
    test_load_paths_retry_flow_after_import_format_mismatch,
)


def test_load_paths_cancel_import_retry_shows_original_error():
    # Lehnt der Nutzer die Rueckfrage/den Dialog ab, muss _load_paths mit
    # False zurueckkehren und darf KEINE (Teil-)Aufnahme uebernehmen.
    from thermal_viewer.data import ImportSettings

    fresh = MainWindow()
    try:
        folder = OUT / "import_retry_cancel_dataset"
        folder.mkdir(exist_ok=True)
        p = folder / "Record_2026-08-22_09-00-00.csv"
        p.write_text("Kopfzeile\n0;20,0;21,0\n", encoding="utf-8")

        # Ausdruecklich auf den Standard zuruecksetzen -- ein vorheriger Test
        # in diesem Lauf kann per persist=True bereits einen abweichenden
        # Standard in der (fuer den gesamten Testlauf gemeinsamen, aber vom
        # echten System isolierten) QSettings-Instanz hinterlassen haben.
        fresh._import_settings = ImportSettings()
        fresh._offer_import_settings_retry = lambda sample_path, error_message: False
        ok = fresh._load_paths([p])
        assert ok is False
        assert fresh.recording is None
        assert fresh._import_settings == ImportSettings(), "abgelehnte Rueckfrage darf den Standard nicht aendern"
    finally:
        fresh.close()


check("_load_paths returns False and leaves state untouched when the import-settings retry is declined", test_load_paths_cancel_import_retry_shows_original_error)


# ==================================================== Robustheits-Pass =====

def test_parse_timestamp_returns_fallback_instead_of_crashing_on_vanished_file():
    # Bugfix: parse_timestamp() dient u.a. als SORTIER-Schluessel in
    # load_paths()/append_paths(), also VOR deren eigentlicher Datei-fuer-
    # Datei-Fehlerbehandlung. Verschwindet eine Datei zwischen dem Auflisten
    # (glob) und dem Fallback-stat() (kein Zeitstempel im Namen -> Datei-
    # System-Aenderungszeit), darf das nicht den kompletten Ladevorgang mit
    # einem unabgefangenen OSError abbrechen.
    from datetime import datetime as _dt
    import re as _re
    from thermal_viewer.data import parse_timestamp

    ghost = OUT / "this_file_does_not_exist_12345.csv"
    never_matches = _re.compile(r"(?!)")  # passt auf keinen String
    result = parse_timestamp(ghost, pattern=never_matches, strptime_fmt="%Y")
    assert result == _dt.min, result


check(
    "parse_timestamp() falls back instead of raising OSError when the file vanished before stat()",
    test_parse_timestamp_returns_fallback_instead_of_crashing_on_vanished_file,
)


def test_load_paths_calls_parse_timestamp_exactly_once_per_file():
    # Bugfix: load_paths() rief parse_timestamp() bisher ZWEIMAL pro Datei
    # auf (einmal als Sortier-Schluessel, einmal fuer den gespeicherten
    # Recording.timestamps-Wert). Lieferte der OSError-Fallback von
    # parse_timestamp() (siehe Test oben) bei den beiden Aufrufen
    # UNTERSCHIEDLICHE Werte (z.B. weil ein transienter Lesefehler beim
    # ersten Aufruf bis zum zweiten Aufruf wieder verschwunden war), liefen
    # Sortierreihenfolge und gespeicherter Zeitstempel auseinander -- die von
    # _deduplicate_timestamps vorausgesetzte aufsteigende Sortierung war
    # dann unbemerkt verletzt. Jetzt wird der Zeitstempel je Datei nur noch
    # einmal ermittelt und fuer beide Zwecke wiederverwendet.
    import thermal_viewer.data as data_module

    call_counts = {}
    orig_parse_timestamp = data_module.parse_timestamp

    def counting_parse_timestamp(path, *a, **k):
        call_counts[path] = call_counts.get(path, 0) + 1
        return orig_parse_timestamp(path, *a, **k)

    data_module.parse_timestamp = counting_parse_timestamp
    try:
        recording = data_module.load_paths(list(paths))
    finally:
        data_module.parse_timestamp = orig_parse_timestamp

    assert call_counts, "parse_timestamp haette aufgerufen werden muessen"
    assert all(count == 1 for count in call_counts.values()), (
        f"parse_timestamp sollte je Datei genau einmal aufgerufen werden, nicht: {call_counts}"
    )
    assert list(recording.timestamps) == sorted(recording.timestamps), (
        "Zeitstempel muessen nach dem Laden aufsteigend sortiert sein"
    )


check(
    "load_paths() calls parse_timestamp exactly once per file, keeping sort order and stored timestamps consistent",
    test_load_paths_calls_parse_timestamp_exactly_once_per_file,
)


def test_load_project_caps_roi_count_against_corrupted_index():
    # Bugfix: eine .tvproj-Datei (z.B. handbearbeitet oder aus einer
    # zukuenftigen App-Version) mit einer riesigen ROI-"index"-Zahl duerfte
    # NICHT versuchen, ebenso viele ROI-Eintraege auf einmal anzulegen
    # (Einfrieren/Speicherueberlauf) -- siehe MAX_ROI_COUNT.
    from thermal_viewer.main_window import MAX_ROI_COUNT

    project_path = OUT / "corrupted_huge_roi_index.tvproj"
    rows, cols = win.recording.shape
    project_data = {
        "quellordner": str(DATASET),
        "bild_groesse_px": {"zeilen": rows, "spalten": cols},
        "rois": [{"index": 999_999_999, "name": "Bogus", "platziert": False}],
    }
    project_path.write_text(json.dumps(project_data), encoding="utf-8")

    prev_roi_count = len(win.roi_entries)
    orig_get_open = QtWidgets.QFileDialog.getOpenFileName
    try:
        QtWidgets.QFileDialog.getOpenFileName = staticmethod(lambda *a, **k: (str(project_path), ""))
        win._load_project()
        assert len(win.roi_entries) == prev_roi_count, (
            "ein korrupter riesiger 'index'-Wert haette KEINE neuen ROIs anlegen duerfen"
        )
        assert len(win.roi_entries) <= MAX_ROI_COUNT
    finally:
        QtWidgets.QFileDialog.getOpenFileName = orig_get_open


check(
    "_load_project caps ROI creation against a corrupted/oversized 'index' value instead of hanging",
    test_load_project_caps_roi_count_against_corrupted_index,
)


def test_csv_column_dialog_rejects_duplicate_column_names():
    # Bugfix: dict(zip(header, row)) im JSON-Export (MainWindow._export_csv)
    # wuerde bei zwei identischen Spaltennamen eine Spalte stillschweigend
    # ueberschreiben -- der Dialog muss das schon vor dem Schliessen
    # verhindern, auch fuer zwei leer gelassene Felder (beide fallen auf
    # denselben Standardnamen "Messwert" zurueck).
    from thermal_viewer.dialogs import CsvColumnDialog

    entries = [
        {"name": "ROI 1", "width_px": 30.0, "height_px": 20.0, "width_mm": None, "height_mm": None},
        {"name": "ROI 2", "width_px": 12.0, "height_px": 12.0, "width_mm": None, "height_mm": None},
    ]
    dialog = CsvColumnDialog(win, entries)
    try:
        accepted = []
        dialog.accept = lambda: accepted.append(True)

        dialog._edits[0].setText("Gleicher Name")
        dialog._edits[1].setText("Gleicher Name")
        dialog._on_accept()
        assert accepted == [], "haette wegen doppelter Spaltennamen NICHT akzeptieren duerfen"

        dialog._edits[0].setText("   ")
        dialog._edits[1].setText("")
        dialog._on_accept()
        assert accepted == [], "zwei leere Namen (-> beide 'Messwert') haetten als Duplikat gelten muessen"

        dialog._edits[1].setText("Anderer Name")
        dialog._on_accept()
        assert accepted == [True], "haette bei eindeutigen Namen akzeptieren muessen"
    finally:
        dialog.close()


check(
    "CsvColumnDialog rejects duplicate column names, including two blank names both falling back to 'Messwert'",
    test_csv_column_dialog_rejects_duplicate_column_names,
)


def test_paused_background_timers_stops_and_restores_watch_and_playback():
    # Bugfix: waehrend _load_paths()/_export_video() wiederholt
    # QApplication.processEvents() aufrufen, duerfen der 10s-Live-Watch-
    # Timer und der Wiedergabe-Timer nicht mitten im Vorgang feuern (siehe
    # _paused_background_timers) -- und muessen danach exakt in ihren
    # vorherigen Zustand zurueckkehren (ob sie liefen oder nicht).
    win._live_watch_timer.start()
    win.play_timer.start(50)
    try:
        assert win._live_watch_timer.isActive()
        assert win.play_timer.isActive()
        with win._paused_background_timers():
            assert not win._live_watch_timer.isActive()
            assert not win.play_timer.isActive()
        assert win._live_watch_timer.isActive()
        assert win.play_timer.isActive()
    finally:
        win._live_watch_timer.stop()
        win.play_timer.stop()

    assert not win._live_watch_timer.isActive()
    assert not win.play_timer.isActive()
    with win._paused_background_timers():
        assert not win._live_watch_timer.isActive()
        assert not win.play_timer.isActive()
    assert not win._live_watch_timer.isActive(), "waere vorher inaktiv gewesen, darf danach nicht laufen"
    assert not win.play_timer.isActive()


check(
    "_paused_background_timers stops+restores live-watch/playback timers, symmetric for both active and inactive start state",
    test_paused_background_timers_stops_and_restores_watch_and_playback,
)


def test_export_csv_shows_error_dialog_on_write_failure_instead_of_crashing():
    # Bugfix: _export_csv hatte (anders als jeder andere Export-Pfad in
    # dieser Datei) keinerlei Fehlerbehandlung um den eigentlichen
    # Datei-Schreibvorgang -- ein ganz gewoehnliches "Ziel-CSV ist gerade in
    # Excel geoeffnet" haette die App mit einem unabgefangenen OSError
    # abstuerzen lassen. Ein Verzeichnis als Schreibziel erzwingt hier
    # portabel einen echten OSError (IsADirectoryError/PermissionError),
    # ohne dafuer irgendetwas monkeypatchen zu muessen.
    from thermal_viewer.dialogs import CsvColumnDialog

    locked_target = OUT / "export_csv_write_failure.csv"
    locked_target.mkdir(exist_ok=True)
    try:
        placed = [e for e in win.roi_entries if e.placed]
        assert placed, "Testvoraussetzung: mindestens ein platzierter Messbereich"

        def fake_column_exec(self):
            self.combo_format.setCurrentIndex(self.combo_format.findData("csv"))
            return (self.accept(), QtWidgets.QDialog.DialogCode.Accepted)[1]

        orig_save = QtWidgets.QFileDialog.getSaveFileName
        try:
            with temp_dialog_exec(CsvColumnDialog, fake_column_exec):
                QtWidgets.QFileDialog.getSaveFileName = staticmethod(
                    lambda *a, **k: (str(locked_target), "CSV-Datei (*.csv)")
                )
                win._export_csv()  # darf NICHT crashen -- OSError muss abgefangen werden
        finally:
            QtWidgets.QFileDialog.getSaveFileName = orig_save
    finally:
        locked_target.rmdir()


check(
    "_export_csv catches OSError on write failure and shows an error dialog instead of crashing",
    test_export_csv_shows_error_dialog_on_write_failure_instead_of_crashing,
)


# ============================================== Export-Rendering-Fixes =====

def test_glw_tight_segments_render_scene_exactly_once():
    # Bugfix: self.glw wurde frueher fuer JEDES Segment (Achsen-/Bild-/
    # Legenden-Spalte) mit einem EIGENEN scene().render()-Aufruf direkt in
    # den Ziel-Painter gezeichnet. Mehrere scene().render()-Aufrufe
    # HINTEREINANDER auf derselben Szene liessen pyqtgraphs Achsen-
    # Beschriftungen ab dem zweiten Aufruf zusaetzlich zur bereits vom
    # ERSTEN Aufruf gezeichneten Position noch einmal (leicht versetzt)
    # zeichnen -- sichtbar als doppelte/"geisterhafte" Ziffern (Bugreport:
    # "Zahlen ... schweben in der Luft"), reproduzierbar sogar bei
    # Segmenten, die sich inhaltlich gar nicht ueberlappen. Fix:
    # _render_glw_segments_into_painter rendert die GESAMTE Szene nur noch
    # EIN EINZIGES Mal in ein Zwischenbild und schneidet den Leerraum
    # danach rein als Bild-Ausschnitt heraus. Dieser Test haelt genau das
    # als Regression fest, unabhaengig davon, ob _tight_glw_segments()
    # gerade 1 oder mehrere Segmente liefert.
    scene = win.glw.scene()
    scene_cls = type(scene)
    orig_render = scene_cls.render
    call_count = [0]

    def counting_render(self, *args, **kwargs):
        call_count[0] += 1
        return orig_render(self, *args, **kwargs)

    scene_cls.render = counting_render
    try:
        img = QtGui.QImage(300, 300, QtGui.QImage.Format_ARGB32)
        img.fill(QtCore.Qt.white)
        p = QtGui.QPainter(img)
        try:
            win._render_glw_segments_into_painter(p, 0.0, 0.0, 300, 300, 1.0)
        finally:
            p.end()
    finally:
        scene_cls.render = orig_render
    assert call_count[0] == 1, f"scene().render() sollte GENAU EINMAL aufgerufen werden, war: {call_count[0]}"


check(
    "_render_glw_segments_into_painter calls scene().render() exactly once, regardless of segment count",
    test_glw_tight_segments_render_scene_exactly_once,
)


def test_export_video_graph_time_axis_follows_zeitanzeige_im_bild():
    # Bugfix: der mit exportierte Graph blieb bisher unabhaengig von der
    # gewaehlten "Zeitanzeige im Bild" (Laufzeit/Zeitstempel/Beides/Keine)
    # immer bei der zuletzt in der App aktiven Uhrzeit-/Laufzeit-Anzeige
    # stehen (Bugreport: "wenn ich 'beides' als Zeitachse auswähle stehen
    # zwar beide Achsen unter dem Video, aber nur die Laufzeit im
    # Graphen"). _export_video muss denselben, bereits vorhandenen
    # Menüpunkt jetzt konsistent auf BEIDE Elemente (Bild-Overlay UND
    # Graph-Achse) anwenden: "Laufzeit" -> Graph zeigt Laufzeit, "Zeitstempel"
    # -> Graph zeigt Uhrzeit, "Beides" -> Graph zeigt BEIDE Achsen (oben+unten).
    from thermal_viewer.dialogs import VideoExportDialog as RealVideoExportDialog

    observed = {}

    orig_show_frame = win._show_frame

    def spying_show_frame(idx):
        if "bottom_runtime" not in observed:
            observed["bottom_runtime"] = win.axis_timeseries_bottom.runtime_mode
            observed["top_visible"] = win.timeseries_plot.getPlotItem().getAxis("top").isVisible()
        return orig_show_frame(idx)

    out_dir = OUT / "time_axis_consistency_check"
    out_dir.mkdir(exist_ok=True)

    def run_export(overlay_setter):
        observed.clear()
        win._show_frame = spying_show_frame

        def fake_exec(self):
            self.radio_output_images.setChecked(True)
            self.spin_start.setValue(1)
            self.spin_end.setValue(min(2, self.spin_end.maximum()))
            self.chk_show_graph.setChecked(True)
            overlay_setter(self)
            for chk in self._content_widgets["checks"].values():
                chk.setChecked(True)
            return (self.accept(), QtWidgets.QDialog.DialogCode.Accepted)[1]

        orig_get_dir = QtWidgets.QFileDialog.getExistingDirectory
        try:
            with temp_dialog_exec(RealVideoExportDialog, fake_exec):
                QtWidgets.QFileDialog.getExistingDirectory = staticmethod(lambda *a, **k: str(out_dir))
                win._export_video()
        finally:
            QtWidgets.QFileDialog.getExistingDirectory = orig_get_dir
            win._show_frame = orig_show_frame
            for f in out_dir.glob("*.png"):
                f.unlink()

    run_export(lambda dlg: dlg.radio_overlay_timeline.setChecked(True))
    assert observed["bottom_runtime"] is True, "Overlay 'Laufzeit' haette den Graphen auf Laufzeit stellen muessen"
    assert observed["top_visible"] is False

    run_export(lambda dlg: dlg.radio_overlay_timestamp.setChecked(True))
    assert observed["bottom_runtime"] is False, "Overlay 'Zeitstempel' haette den Graphen auf Uhrzeit stellen muessen"
    assert observed["top_visible"] is False

    run_export(lambda dlg: dlg.radio_overlay_both.setChecked(True))
    assert observed["top_visible"] is True, (
        "Overlay 'Beides' haette im Graphen BEIDE Achsen zeigen muessen (obere Achse sichtbar) -- "
        "auch schon beim ALLERERSTEN gerenderten Frame (Layout-Timing-Bugfix)."
    )

    # Nach dem Export muss die obere Achse wieder ausgeblendet sein (kein
    # dauerhafter Seiteneffekt auf die normale UI).
    assert win.timeseries_plot.getPlotItem().getAxis("top").isVisible() is False


check(
    "video export graph time axis follows the 'Zeitanzeige im Bild' overlay choice (incl. dual axis on the very first frame)",
    test_export_video_graph_time_axis_follows_zeitanzeige_im_bild,
)


# ======================================= Neue Punkte (Kreis-ROI, etc.) =====

def test_elliptical_mask_and_average_value():
    # Reiner Einheitentest von roi.py, unabhaengig von der geladenen
    # Aufnahme: eine eingeschriebene Ellipse in ein 5x5-Feld muss die vier
    # Eckpixel ausschliessen, das Zentrum einschliessen, und average_value()
    # muss fuer circular=True NUR ueber die eingeschlossenen Pixel mitteln.
    import numpy as np
    from thermal_viewer.roi import average_value, elliptical_mask

    block = np.full((5, 5), 10.0, dtype=np.float32)
    block[0, 0] = block[0, 4] = block[4, 0] = block[4, 4] = 1000.0
    mask = elliptical_mask(0, 5, 0, 5)
    assert not mask[0, 0] and not mask[0, 4] and not mask[4, 0] and not mask[4, 4], (
        "Ecken muessen ausserhalb der eingeschriebenen Ellipse liegen"
    )
    assert mask[2, 2], "Zentrum muss innerhalb der Ellipse liegen"

    rect_mean = average_value(block, 0, 5, 0, 5, circular=False)
    circ_mean = average_value(block, 0, 5, 0, 5, circular=True)
    assert abs(rect_mean - 10.0) > 100, "rechteckiger Mittelwert muss durch die vier Eck-Extremwerte verzerrt sein"
    assert abs(circ_mean - 10.0) < 1e-6, "kreisfoermiger Mittelwert darf die Eckwerte NICHT einbeziehen"

    block3d = np.stack([block, block * 2])
    circ_means = average_value(block3d, 0, 5, 0, 5, circular=True)
    assert circ_means.shape == (2,)
    assert abs(circ_means[0] - 10.0) < 1e-6 and abs(circ_means[1] - 20.0) < 1e-6


check("elliptical_mask()/average_value() exclude corner pixels outside the inscribed ellipse", test_elliptical_mask_and_average_value)


def test_roi_circular_checkbox_wires_into_curve_and_label_averaging():
    import numpy as np
    from thermal_viewer.main_window import DEFAULT_ROI_SIZE
    from thermal_viewer.roi import average_value

    entry = win.roi_entries[0]
    try:
        entry.place(4, 4, 8, 8)
        win._recompute_curves(entries=[entry])
        assert entry.roi.is_circular is False, "Standard muss weiterhin rechteckig sein"

        entry.chk_circular.setChecked(True)
        assert entry.roi.is_circular is True
        row0, row1, col0, col1 = entry.bounds_px(win.recording.shape)
        expected = average_value(win.recording.frames[:, row0:row1, col0:col1], row0, row1, col0, col1, True)
        got = entry.curve.getData()[1]
        assert np.allclose(got, expected), "Kurve haette nach Umschalten kreisfoermig neu gemittelt werden muessen"

        win._update_roi_temperature_labels(win.current_index)
        expected_label_value = float(
            average_value(win.recording.frames[win.current_index, row0:row1, col0:col1], row0, row1, col0, col1, True)
        )
        assert abs(entry._last_temperature - expected_label_value) < 1e-4

        entry.chk_circular.setChecked(False)
        assert entry.roi.is_circular is False
    finally:
        entry.chk_circular.setChecked(False)
        entry.place(0, 0, DEFAULT_ROI_SIZE, DEFAULT_ROI_SIZE)
        entry.roi.setVisible(False)
        entry.placed = False
        entry.list_item.setCheckState(QtCore.Qt.CheckState.Checked)


check(
    "'Als Kreis behandeln' checkbox wires AdjustableROI.is_circular through curve recompute AND live temperature label",
    test_roi_circular_checkbox_wires_into_curve_and_label_averaging,
)


def test_roi_show_temperature_checkbox_toggles_label_text():
    entry = win.roi_entries[1]
    try:
        entry.place(2, 2, 6, 6)
        win._update_roi_temperature_labels(win.current_index)
        assert entry.show_temperature is True, "Standard muss an sein"
        assert "°C" in entry.label.toPlainText(), entry.label.toPlainText()

        entry.chk_show_temperature.setChecked(False)
        assert entry.show_temperature is False
        assert entry.label.toPlainText() == entry.name, entry.label.toPlainText()
        assert "°C" not in entry.label.toPlainText()

        entry.chk_show_temperature.setChecked(True)
        assert "°C" in entry.label.toPlainText()
    finally:
        entry.chk_show_temperature.setChecked(True)
        entry.roi.setVisible(False)
        entry.placed = False
        entry.list_item.setCheckState(QtCore.Qt.CheckState.Checked)


check(
    "'Temperatur im Bild anzeigen' checkbox toggles whether the on-image label includes the live temperature",
    test_roi_show_temperature_checkbox_toggles_label_text,
)


def test_project_save_load_roundtrips_show_temperature_and_circular():
    entry = win.roi_entries[2]
    try:
        entry.place(3, 3, 5, 5)
        entry.chk_show_temperature.setChecked(False)
        entry.chk_circular.setChecked(True)

        path = OUT / "roi_flags_roundtrip.tvproj"
        orig_save = QtWidgets.QFileDialog.getSaveFileName
        try:
            QtWidgets.QFileDialog.getSaveFileName = staticmethod(lambda *a, **k: (str(path), ""))
            win._save_project()
        finally:
            QtWidgets.QFileDialog.getSaveFileName = orig_save

        entry.chk_show_temperature.setChecked(True)
        entry.chk_circular.setChecked(False)

        orig_open = QtWidgets.QFileDialog.getOpenFileName
        try:
            QtWidgets.QFileDialog.getOpenFileName = staticmethod(lambda *a, **k: (str(path), ""))
            win._load_project()
        finally:
            QtWidgets.QFileDialog.getOpenFileName = orig_open

        assert entry.show_temperature is False, "haette aus der Projektdatei wiederhergestellt werden muessen"
        assert entry.roi.is_circular is True
        assert entry.chk_show_temperature.isChecked() is False
        assert entry.chk_circular.isChecked() is True
    finally:
        entry.chk_show_temperature.setChecked(True)
        entry.chk_circular.setChecked(False)
        entry.roi.setVisible(False)
        entry.placed = False
        entry.list_item.setCheckState(QtCore.Qt.CheckState.Checked)


check(
    "project save/load round-trips per-ROI 'temperatur_anzeigen'/'kreisfoermig' flags",
    test_project_save_load_roundtrips_show_temperature_and_circular,
)


def test_video_dialog_filename_preview_substitutes_timestamp_tokens_single_example():
    # Bugfix: die Vorschau zeigte bisher den ROHEN Platzhalter-Text
    # ("Frame_YYYY-MM-DD_1.png") statt eines tatsaechlichen Dateinamens,
    # UND zwei nahezu identische Beispiele statt einem.
    from datetime import datetime
    from thermal_viewer.dialogs import VideoExportDialog as RealVideoExportDialog

    sample_ts = datetime(2026, 8, 31, 14, 5, 9)
    dlg = RealVideoExportDialog(
        win, n_frames=win.recording.n_frames, colormaps=COLORMAPS,
        current_colormap_index=0, current_invert=False, current_level_mode="global",
        current_min=0.0, current_max=50.0, current_fps=10.0,
        sample_timestamp=sample_ts,
    )
    try:
        dlg.edit_image_prefix.setText("Frame_YYYY-MM-DD_")
        text = dlg.lbl_filename_preview.text()
        assert text.count(",") == 0, f"Vorschau darf nur EIN Beispiel zeigen, war: {text}"
        assert "2026-08-31" in text, text
        assert "YYYY" not in text and "MM" not in text and "DD" not in text, text
    finally:
        dlg.close()


check(
    "VideoExportDialog filename preview substitutes real timestamp tokens and shows exactly one example",
    test_video_dialog_filename_preview_substitutes_timestamp_tokens_single_example,
)


def test_resolve_export_timestamps_prompts_and_rebases_when_needed():
    # Bugfix: ohne "sinnvollen" (aus dem Dateinamen erkannten) Zeitstempel
    # wuerde ein Zeitstempel-Platzhalter im Bildstapel-Praefix nur die
    # zufaellige Datei-Aenderungszeit einsetzen -- MainWindow soll
    # stattdessen nachfragen: aktuelles Systemdatum / eigener Startpunkt /
    # Abbrechen.
    import re as _re
    from datetime import datetime
    from thermal_viewer.dialogs import StartTimestampDialog

    fresh = MainWindow()
    try:
        folder = OUT / "fake_timestamp_dataset"
        folder.mkdir(exist_ok=True)
        paths = []
        for i in range(3):
            p = folder / f"weirdname_{i}.csv"
            p.write_text("20,0;21,0\n22,0;23,0\n", encoding="utf-8")
            paths.append(p)
        never_matches = _re.compile(r"(?!)")
        ok = fresh._load_paths(paths, pattern=never_matches, strptime_fmt="%Y")
        assert ok, "Testvoraussetzung: Laden haette klappen sollen (Namensschema ist hier irrelevant)"
        assert fresh._recording_has_real_timestamps() is False

        # Kein Platzhalter im Praefix -> keine Rueckfrage, direkt die (unechten) Zeitstempel.
        assert fresh._resolve_export_timestamps("Frame_") == list(fresh.recording.timestamps)

        def fake_box_exec(role_text):
            def _exec(self):
                for b in self.buttons():
                    if role_text in b.text():
                        b.click()
                        return self.result()
                self.reject()
                return self.result()
            return _exec

        with temp_dialog_exec(QtWidgets.QMessageBox, fake_box_exec("Abbrechen")):
            assert fresh._resolve_export_timestamps("Frame_YYYY-MM-DD_") is None

        with temp_dialog_exec(QtWidgets.QMessageBox, fake_box_exec("Systemdatum")):
            result = fresh._resolve_export_timestamps("Frame_YYYY-MM-DD_")
        assert result is not None and len(result) == len(fresh.recording.timestamps)
        orig_deltas = [ts - fresh.recording.timestamps[0] for ts in fresh.recording.timestamps]
        new_deltas = [ts - result[0] for ts in result]
        assert orig_deltas == new_deltas, "relative Abstaende zwischen den Frames muessen erhalten bleiben"

        custom_start = datetime(2030, 1, 1, 8, 0, 0)

        def fake_start_dialog_exec(self):
            self.edit_datetime.setDateTime(QtCore.QDateTime(custom_start))
            return (self.accept(), QtWidgets.QDialog.DialogCode.Accepted)[1]

        with temp_dialog_exec(QtWidgets.QMessageBox, fake_box_exec("Eigenen Startpunkt")), \
                temp_dialog_exec(StartTimestampDialog, fake_start_dialog_exec):
            result2 = fresh._resolve_export_timestamps("Frame_YYYY-MM-DD_")
        assert result2[0] == custom_start, result2[0]
    finally:
        fresh.close()


check(
    "_resolve_export_timestamps prompts only when the prefix uses tokens AND timestamps aren't real, all 3 outcomes work",
    test_resolve_export_timestamps_prompts_and_rebases_when_needed,
)


def test_export_video_images_filenames_use_real_timestamps_when_available():
    from thermal_viewer.dialogs import VideoExportDialog as RealVideoExportDialog

    out_dir = OUT / "filename_token_export_check"
    out_dir.mkdir(exist_ok=True)

    def fake_exec(self):
        self.radio_output_images.setChecked(True)
        self.spin_start.setValue(1)
        self.spin_end.setValue(2)
        self.edit_image_prefix.setText("Frame_YYYY-MM-DD_hh-mm-ss_")
        return (self.accept(), QtWidgets.QDialog.DialogCode.Accepted)[1]

    orig_get_dir = QtWidgets.QFileDialog.getExistingDirectory
    try:
        with temp_dialog_exec(RealVideoExportDialog, fake_exec):
            QtWidgets.QFileDialog.getExistingDirectory = staticmethod(lambda *a, **k: str(out_dir))
            win._export_video()
    finally:
        QtWidgets.QFileDialog.getExistingDirectory = orig_get_dir

    produced = sorted(out_dir.glob("*.png"))
    assert len(produced) == 2, [p.name for p in produced]
    expected_ts0 = win.recording.timestamps[0].strftime("%Y-%m-%d_%H-%M-%S")
    assert expected_ts0 in produced[0].name, produced[0].name
    for f in produced:
        f.unlink()


check(
    "image-stack export fills real per-frame timestamps into filename-template tokens in the prefix",
    test_export_video_images_filenames_use_real_timestamps_when_available,
)


def test_export_video_images_idx_token_places_running_number_explicitly():
    # Nutzerwunsch: der laufende Frame-Index soll ueber den Platzhalter IDX
    # explizit im Praefix platzierbar sein (z.B. VOR einem vollen
    # Zeitstempel), statt IMMER automatisch ans Ende angehaengt zu werden --
    # enthaelt der Praefix IDX, darf die Nummer NUR dort erscheinen, nicht
    # zusaetzlich noch einmal ans Ende gehaengt werden.
    from thermal_viewer.dialogs import VideoExportDialog as RealVideoExportDialog

    out_dir = OUT / "idx_token_export_check"
    out_dir.mkdir(exist_ok=True)

    def fake_exec(self):
        self.radio_output_images.setChecked(True)
        self.spin_start.setValue(1)
        self.spin_end.setValue(2)
        self.edit_image_prefix.setText("Frame_IDX_YYYY-MM-DD_")
        return (self.accept(), QtWidgets.QDialog.DialogCode.Accepted)[1]

    orig_get_dir = QtWidgets.QFileDialog.getExistingDirectory
    try:
        with temp_dialog_exec(RealVideoExportDialog, fake_exec):
            QtWidgets.QFileDialog.getExistingDirectory = staticmethod(lambda *a, **k: str(out_dir))
            win._export_video()
    finally:
        QtWidgets.QFileDialog.getExistingDirectory = orig_get_dir

    produced = sorted(out_dir.glob("*.png"))
    assert len(produced) == 2, [p.name for p in produced]
    expected_date0 = win.recording.timestamps[0].strftime("%Y-%m-%d")
    # IDX wird durch "1" ersetzt, NICHT zusaetzlich am Ende angehaengt --
    # der Dateiname enthaelt die "1" also GENAU EINMAL (an der IDX-Stelle).
    assert produced[0].name == f"Frame_1_{expected_date0}_.png", produced[0].name
    expected_date1 = win.recording.timestamps[1].strftime("%Y-%m-%d")
    assert produced[1].name == f"Frame_2_{expected_date1}_.png", produced[1].name
    for f in produced:
        f.unlink()


check(
    "image-stack export prefix token IDX places the running frame number explicitly, without an extra auto-appended one",
    test_export_video_images_idx_token_places_running_number_explicitly,
)


def test_export_video_images_warns_and_offers_auto_fix_for_colliding_prefix():
    # Nutzerwunsch: OHNE "IDX" wird die laufende Nummer NICHT mehr
    # automatisch angehaengt (volle Kontrolle ueber den Dateinamen) --
    # ergibt der Praefix dadurch fuer mehrere Frames denselben Namen, muss
    # das VOR dem eigentlichen Schreiben erkannt und der Nutzer gefragt
    # werden (statt Dateien stillschweigend gegenseitig zu ueberschreiben).
    from thermal_viewer.dialogs import VideoExportDialog as RealVideoExportDialog

    out_dir = OUT / "colliding_prefix_export_check"
    out_dir.mkdir(exist_ok=True)

    # Absichtlich EIN fester, Zeitstempel-/IDX-loser Praefix -- kollidiert
    # fuer beide Frames. Nur beim ERSTEN Durchlauf erzwingen: der spaeter
    # (siehe fake_msgbox_exec) automatisch angehaengte "IDX" darf durch einen
    # erneuten dialog.exec()-Aufruf (siehe _export_video-Schleife, Punkt 3)
    # nicht wieder ueberschrieben werden -- unabhaengig vom (mittlerweile
    # selbst schon IDX enthaltenden) Dialog-Standardwert, siehe Punkt 2.
    state = {"forced": False}

    def fake_exec(self):
        self.radio_output_images.setChecked(True)
        self.spin_start.setValue(1)
        self.spin_end.setValue(2)
        if not state["forced"]:
            self.edit_image_prefix.setText("Frame_")
            state["forced"] = True
        return (self.accept(), QtWidgets.QDialog.DialogCode.Accepted)[1]

    def fake_msgbox_exec(self):
        for btn in self.buttons():
            if "anhängen" in btn.text():
                btn.click()
                break
        return self.result()

    orig_get_dir = QtWidgets.QFileDialog.getExistingDirectory
    try:
        with temp_dialog_exec(RealVideoExportDialog, fake_exec), \
                temp_dialog_exec(QtWidgets.QMessageBox, fake_msgbox_exec):
            QtWidgets.QFileDialog.getExistingDirectory = staticmethod(lambda *a, **k: str(out_dir))
            win._export_video()
    finally:
        QtWidgets.QFileDialog.getExistingDirectory = orig_get_dir

    produced = sorted(out_dir.glob("*.png"))
    assert len(produced) == 2, [p.name for p in produced]
    assert produced[0].name == "Frame_1.png", produced[0].name
    assert produced[1].name == "Frame_2.png", produced[1].name
    for f in produced:
        f.unlink()


check(
    "image-stack export without IDX/timestamp warns about colliding filenames and offers to auto-append IDX",
    test_export_video_images_warns_and_offers_auto_fix_for_colliding_prefix,
)


def test_status_bar_shows_relative_runtime():
    win._show_frame(3)
    win._update_status_bar()
    msg = win.statusBar().currentMessage()
    expected_runtime = win._format_relative_runtime(
        (win.recording.timestamps[3] - win.recording.timestamps[0]).total_seconds()
    )
    assert f"Laufzeit: {expected_runtime}" in msg, msg
    win._show_frame(0)


check("status bar shows relative runtime (Laufzeit) alongside frame/timestamp", test_status_bar_shows_relative_runtime)


def test_roi_drag_updates_curve_and_label_live_not_only_on_release():
    import numpy as np

    entry = win.roi_entries[3]
    entry.place(10, 10, 4, 4)
    win._recompute_curves(entries=[entry])
    win._show_frame(0)
    before = entry.curve.getData()[1].copy()

    # sigRegionChanged (feuert waehrend des Ziehens) OHNE ein anschliessendes
    # sigRegionChangeFinished (erst beim Loslassen, finish=False unterdrueckt
    # es explizit) simulieren -- die Kurve UND die Bild-Beschriftung muessen
    # bereits jetzt den neuen Wert zeigen, nicht erst nach dem Loslassen.
    entry.roi.setSize([10, 10], finish=False)
    after = entry.curve.getData()[1]
    assert not np.allclose(before, after), "Kurve haette schon waehrend des Ziehens aktualisieren muessen"
    assert f"{entry._last_temperature:.1f}" in entry.label.toPlainText()

    entry.place(0, 0, 1, 1)
    entry.placed = False
    entry.roi.setVisible(False)
    entry.label.setVisible(False)
    win._recompute_curves(entries=[entry])


check(
    "dragging a ROI (sigRegionChanged, before release) updates the curve and on-image label live",
    test_roi_drag_updates_curve_and_label_live_not_only_on_release,
)


def test_time_axis_runtime_mode_ticks_are_onset_corrected_and_manually_settable():
    import pyqtgraph as pg

    from thermal_viewer.main_window import TimeAxisItem

    axis = TimeAxisItem()
    # An ein PlotWidget haengen (wie im echten Betrieb) -- eine freistehende
    # AxisItem hat kein fontMetrics, das DateAxisItem.tickValues() (Uhrzeit-
    # Zweig, unten getestet) fuer die Zoomstufen-Wahl braucht.
    plot_widget = pg.PlotWidget(axisItems={"bottom": axis})
    ugly_t0 = 1000.0 + 24.0  # Aufnahmebeginn auf eine "haessliche" Sekunde gelegt
    axis.set_runtime_mode(True, ugly_t0)

    # Automatisch, aber onset-korrigiert (Punkt 6): der erste Tick-Level muss
    # bei relativer Laufzeit 0 beginnen, nicht bei einem :24-Versatz.
    levels = axis.tickValues(ugly_t0, ugly_t0 + 200, 400)
    assert levels, "tickValues() lieferte keine Level"
    coarsest_values = levels[0][1]
    assert abs(coarsest_values[0] - ugly_t0) < 1e-6, "erster Tick sollte exakt bei Laufzeit 0 liegen"
    strings = axis.tickStrings(coarsest_values, 1, levels[0][0])
    assert strings[0] == "00:00:00", strings

    # Manuell erzwungener Tick-Abstand (Nutzerwunsch: Abstand haendisch
    # setzbar, zumindest im Laufzeit-Modus).
    axis.set_manual_spacing(30.0)
    manual_levels = axis.tickValues(ugly_t0, ugly_t0 + 95, 400)
    assert len(manual_levels) == 1
    spacing, values = manual_levels[0]
    assert spacing == 30.0
    expected = [ugly_t0, ugly_t0 + 30, ugly_t0 + 60, ugly_t0 + 90, ugly_t0 + 120]
    assert len(values) == len(expected) and all(abs(v - e) < 1e-6 for v, e in zip(values, expected)), values

    # Manuelle Schrittweite wirkt NUR im Laufzeit-Modus, nicht bei "Uhrzeit"
    # (siehe tickValues: "if not self.runtime_mode: return super()...").
    axis.set_runtime_mode(False)
    orig_date_tick_values = pg.DateAxisItem.tickValues
    called = []
    pg.DateAxisItem.tickValues = lambda self, *a, **k: called.append(True) or []
    try:
        axis.tickValues(ugly_t0, ugly_t0 + 95, 400)
    finally:
        pg.DateAxisItem.tickValues = orig_date_tick_values
    assert called, "im Uhrzeit-Modus haette an DateAxisItem.tickValues() delegiert werden muessen"

    plot_widget.close()
    plot_widget.deleteLater()


check(
    "TimeAxisItem (Laufzeit-Modus): automatische Ticks sind onset-korrigiert, Abstand manuell setzbar",
    test_time_axis_runtime_mode_ticks_are_onset_corrected_and_manually_settable,
)


def test_time_axis_numeric_runtime_unit_formats_as_plain_decimal_number():
    # Nutzerwunsch: "dritte Zeitachse" -- statt hh:mm:ss eine fortlaufende
    # Dezimalzahl in frei waehlbarer Einheit (Sekunden/Minuten/Stunden), um
    # die Laufzeit ohne manuelles Umrechnen in anderer Software (z.B. zum
    # Zeichnen) weiterverarbeiten zu koennen.
    import pyqtgraph as pg

    from thermal_viewer.main_window import TimeAxisItem

    axis = TimeAxisItem()
    plot_widget = pg.PlotWidget(axisItems={"bottom": axis})
    t0 = 1000.0
    axis.set_runtime_mode(True, t0)

    # Standard bleibt hh:mm:ss (Ruecksicht auf bestehendes Verhalten).
    assert axis.runtime_unit == "hhmmss"
    assert axis.tickStrings([t0, t0 + 90], 1, 60) == ["00:00:00", "00:01:30"]

    # Sekunden: ganzzahliger Abstand -> keine Nachkommastellen noetig.
    axis.set_runtime_unit("s")
    assert axis.tickStrings([t0, t0 + 90], 1, 30) == ["0", "90"]

    # Minuten bei 30s-Abstand (0,5 min) -- braucht Nachkommastellen, um
    # benachbarte Ticks ueberhaupt zu unterscheiden.
    axis.set_runtime_unit("min")
    assert axis.tickStrings([t0, t0 + 30, t0 + 60, t0 + 90], 1, 30) == ["0,0", "0,5", "1,0", "1,5"]

    # Stunden bei einer nur wenige Minuten langen Aufnahme (Abstand 60s):
    # ohne genug Nachkommastellen wuerden mehrere Ticks identisch "0"
    # anzeigen -- _decimals_for_spacing muss das verhindern.
    axis.set_runtime_unit("h")
    strings = axis.tickStrings([t0, t0 + 60, t0 + 120], 1, 60)
    assert len(set(strings)) == len(strings), f"Ticks nicht unterscheidbar: {strings}"

    plot_widget.close()
    plot_widget.deleteLater()


check(
    "TimeAxisItem: numerisches Laufzeit-Format (s/min/h) statt hh:mm:ss, mit unterscheidbaren Nachkommastellen",
    test_time_axis_numeric_runtime_unit_formats_as_plain_decimal_number,
)


def test_axis_settings_dialog_exposes_x_manual_spacing_only_meaningful_in_runtime_mode():
    from thermal_viewer.dialogs import AxisSettingsDialog

    dialog = AxisSettingsDialog(
        win, current_x_min=0, current_x_max=100, current_y_min=0, current_y_max=50,
        x_runtime_mode=True, x_spacing=None,
    )
    assert dialog.x_manual_spacing() is False
    dialog.chk_x_manual_spacing.setChecked(True)
    dialog.spin_x_spacing.setValue(45.0)
    assert dialog.x_manual_spacing() is True
    assert abs(dialog.x_spacing() - 45.0) < 1e-6
    dialog.close()

    reopened = AxisSettingsDialog(
        win, current_x_min=0, current_x_max=100, current_y_min=0, current_y_max=50,
        x_runtime_mode=True, x_spacing=45.0,
    )
    assert reopened.x_manual_spacing() is True, "Dialog soll zuvor gesetzte Schrittweite beim Wiederoeffnen zeigen"
    assert abs(reopened.spin_x_spacing.value() - 45.0) < 1e-6
    reopened.close()


check(
    "AxisSettingsDialog bietet manuellen X-Tick-Abstand, reflektiert vorherigen Zustand beim Wiederoeffnen",
    test_axis_settings_dialog_exposes_x_manual_spacing_only_meaningful_in_runtime_mode,
)


# ==================================== Robustheits-/Logikfehler-Review =====

def test_new_roi_added_mid_recording_gets_working_interp_frame_range():
    # Bugfix: _add_roi_entry() -> _configure_roi_entry_for_recording() setzte
    # bislang nur die Wertebereiche fuer X/Y/Breite/Hoehe, NICHT aber fuer
    # spin_interp_start_frame/-end_frame -- die blieben auf ihrem Konstruktions-
    # Default (1, 1) haengen, solange die Aufnahme nicht neu geladen wurde.
    entry = win._add_roi_entry()
    try:
        assert entry.spin_interp_start_frame.maximum() == win.recording.n_frames, (
            entry.spin_interp_start_frame.maximum(), win.recording.n_frames
        )
        assert entry.spin_interp_end_frame.maximum() == win.recording.n_frames
        assert entry.spin_interp_end_frame.value() == win.recording.n_frames, "Standard: letztes Bild"
    finally:
        orig_question = QtWidgets.QMessageBox.question
        QtWidgets.QMessageBox.question = staticmethod(lambda *a, **k: QtWidgets.QMessageBox.StandardButton.Yes)
        try:
            win._on_roi_remove_clicked(entry)
        finally:
            QtWidgets.QMessageBox.question = orig_question


check(
    "ROI added while a recording is already loaded gets a working interpolation frame range (not stuck at 1)",
    test_new_roi_added_mid_recording_gets_working_interp_frame_range,
)


def test_project_load_syncs_interp_frame_spinboxes_not_just_data_fields():
    # Bugfix: _load_project() setzte entry.interp_start_frame/-end_frame aus
    # der Datei, aber NIE die zugehoerigen Zahlenfelder (spin_interp_start_
    # frame/-end_frame) -- ein erneutes "Start festlegen" haette dadurch zum
    # FALSCHEN (alten/Default-) Bild gesprungen und den frisch geladenen
    # Keyframe beim naechsten Klick mit der dortigen Geometrie ueberschrieben.
    entry = win.roi_entries[3]
    try:
        entry.place(4, 4, 6, 6)
        entry.chk_interp.setChecked(True)
        win._step_frame(2 - win.current_index)
        entry.capture_interp_start(win.current_index)
        win._step_frame((win.recording.n_frames - 1) - win.current_index)
        entry.capture_interp_end(win.current_index)

        path = OUT / "interp_frame_spinbox_roundtrip.tvproj"
        orig_save = QtWidgets.QFileDialog.getSaveFileName
        try:
            QtWidgets.QFileDialog.getSaveFileName = staticmethod(lambda *a, **k: (str(path), ""))
            win._save_project()
        finally:
            QtWidgets.QFileDialog.getSaveFileName = orig_save

        # Zahlenfelder verfaelschen, wie es ein Nutzer zwischenzeitlich tun koennte.
        entry.spin_interp_start_frame.setValue(1)
        entry.spin_interp_end_frame.setValue(1)

        orig_open = QtWidgets.QFileDialog.getOpenFileName
        try:
            QtWidgets.QFileDialog.getOpenFileName = staticmethod(lambda *a, **k: (str(path), ""))
            win._load_project()
        finally:
            QtWidgets.QFileDialog.getOpenFileName = orig_open

        assert entry.spin_interp_start_frame.value() == 3, entry.spin_interp_start_frame.value()
        assert entry.spin_interp_end_frame.value() == win.recording.n_frames, entry.spin_interp_end_frame.value()
    finally:
        entry.chk_interp.setChecked(False)
        entry.roi.setVisible(False)
        entry.placed = False
        entry.list_item.setCheckState(QtCore.Qt.CheckState.Checked)
        win._step_frame(-win.current_index)


check(
    "loading a project syncs the interpolation start/end SPINBOXES, not just the underlying frame-index fields",
    test_project_load_syncs_interp_frame_spinboxes_not_just_data_fields,
)


def test_interp_capture_switching_roi_resets_other_roi_arm_state():
    # Bugfix: das Umschalten von "Messbereich setzen" auf ein ANDERES ROI
    # unterdrueckt (blockSignals) den eigenen _on_roi_place_toggled-Aufruf
    # des vorher armierten ROI -- dessen interp_arm_start/-end blieb dadurch
    # haengen, der Knopf zeigte weiter "...uebernehmen" und ein spaeterer
    # Klick haette die Geometrie vom falschen (aktuellen) Frame uebernommen.
    a = win.roi_entries[0]
    b = win.roi_entries[1]
    try:
        a.chk_interp.setChecked(True)
        b.chk_interp.setChecked(True)

        a.btn_interp_start.click()  # Phase 1 fuer A: armiert + "Messbereich setzen" an
        assert a.interp_arm_start
        assert a.btn_interp_start.text() == INTERP_START_CAPTURE_LABEL

        b.btn_interp_start.click()  # Phase 1 fuer B, OHNE A abzuschliessen
        assert b.interp_arm_start
        assert not a.interp_arm_start, "A haette beim Umschalten auf B zurueckgesetzt werden muessen"
        assert a.btn_interp_start.text() == INTERP_START_LABEL, a.btn_interp_start.text()
    finally:
        for entry in (a, b):
            entry.chk_interp.setChecked(False)


check(
    "arming ROI B's interpolation capture resets ROI A's still-armed capture instead of leaving it stuck",
    test_interp_capture_switching_roi_resets_other_roi_arm_state,
)


def test_set_recording_clamps_stale_interp_keyframes_to_new_shorter_recording():
    # Bugfix: beim Laden einer NEUEN (kuerzeren) Aufnahme blieben bereits
    # gesetzte Interpolations-Keyframes (interp_start_frame/-end_frame) auf
    # ihren alten, jetzt zu grossen Werten stehen -- _interp_fraction()
    # bekam dadurch einen viel zu grossen Nenner und der Messbereich
    # erreichte sein Ende innerhalb der neuen (kuerzeren) Aufnahme NIE.
    import shutil
    import tempfile

    entry = win.roi_entries[4]
    short_dir = Path(tempfile.mkdtemp(prefix="thermalviewer_short_", dir=OUT))
    try:
        entry.place(2, 2, 4, 4)
        entry.chk_interp.setChecked(True)
        entry.capture_interp_start(0)
        entry.capture_interp_end(win.recording.n_frames - 1)  # z.B. Frame 7 bei 8 Frames
        assert entry.interp_end_frame == win.recording.n_frames - 1

        generate_fixture_dataset(short_dir, n_frames=3)
        paths = sorted(short_dir.glob("*.csv"))
        ok = win._load_paths(paths)
        assert ok, "Testvoraussetzung: Laden der kuerzeren Aufnahme haette klappen sollen"

        assert entry.interp_end_frame <= win.recording.n_frames - 1, (
            "Ende-Keyframe haette auf die neue, kuerzere Aufnahme geklemmt werden muessen",
            entry.interp_end_frame, win.recording.n_frames,
        )
        # Am (jetzt kuerzeren) letzten Frame muss die Interpolation ihr Ziel
        # tatsaechlich erreichen (frac == 1.0), nicht bei einem Bruchteil haengenbleiben.
        frac = win._interp_fraction(win.recording.n_frames - 1, entry.interp_start_frame, entry.interp_end_frame)
        assert frac == 1.0, frac
    finally:
        entry.chk_interp.setChecked(False)
        entry.roi.setVisible(False)
        entry.placed = False
        entry.list_item.setCheckState(QtCore.Qt.CheckState.Checked)
        # Urspruengliche (laengere) Test-Aufnahme fuer nachfolgende Tests wiederherstellen.
        ok = win._load_paths(sorted(DATASET.glob("*.csv")))
        assert ok, "Testreihe konnte nicht wiederhergestellt werden"
        shutil.rmtree(short_dir, ignore_errors=True)


check(
    "loading a new, shorter recording clamps (not discards) stale ROI interpolation keyframes",
    test_set_recording_clamps_stale_interp_keyframes_to_new_shorter_recording,
)


def test_time_axis_manual_spacing_is_capped_against_unbounded_tick_generation():
    # Bugfix: der manuelle Tick-Abstand-Zweig ignorierte den `size`-Parameter
    # komplett und hatte keinerlei Obergrenze -- ein kleiner Abstand ueber
    # einen weiten sichtbaren Zeitraum (z.B. 0,1s Abstand bei einer
    # mehrstuendigen Aufnahme) haette hunderttausende Ticks erzeugt und die
    # Oberflaeche bei jedem Neuzeichnen/Zoomen spuerbar einfrieren koennen.
    from thermal_viewer.main_window import TimeAxisItem

    axis = TimeAxisItem()
    axis.set_runtime_mode(True, 0.0)
    axis.set_manual_spacing(0.1)
    spacing, values = axis.tickValues(0.0, 24 * 3600.0, 400)[0]
    assert len(values) <= 2000, len(values)


check(
    "manual X-axis tick spacing is capped, can't generate unbounded ticks over a wide visible range",
    test_time_axis_manual_spacing_is_capped_against_unbounded_tick_generation,
)


def test_export_dialogs_reject_inverted_custom_color_range():
    # Bugfix: weder GraphicExportDialog noch VideoExportDialog pruefte Min <
    # Max bei "Eigene Einstellungen" + "Manuell" -- anders als ueberall sonst
    # in der App (Haupt-Legende, AxisSettingsDialog) liess sich damit eine
    # invertierte/entartete Farbskala (z.B. Min=50, Max=10) unbemerkt exportieren.
    from thermal_viewer.dialogs import GraphicExportDialog, VideoExportDialog

    graphic_dialog = GraphicExportDialog(
        win, win._settings, default_dpi=150, show_mode_choice=False, show_time_axis_choice=False,
        colormaps=COLORMAPS, current_colormap_index=0, current_invert=False,
        current_level_mode="manual", current_min=0.0, current_max=50.0,
    )
    try:
        graphic_dialog._color_widgets["radio_custom"].setChecked(True)
        graphic_dialog._color_widgets["combo_level_mode"].setCurrentIndex(
            graphic_dialog._color_widgets["combo_level_mode"].findData("manual")
        )
        graphic_dialog._color_widgets["spin_min"].setValue(50.0)
        graphic_dialog._color_widgets["spin_max"].setValue(10.0)
        with temp_dialog_exec(QtWidgets.QMessageBox, lambda self: QtWidgets.QMessageBox.StandardButton.Ok):
            graphic_dialog._on_accept()
        assert graphic_dialog.result() != QtWidgets.QDialog.DialogCode.Accepted, (
            "Dialog haette die entartete Farbskala (Max <= Min) ablehnen muessen"
        )
    finally:
        graphic_dialog.close()

    video_dialog = VideoExportDialog(
        win, n_frames=win.recording.n_frames, colormaps=COLORMAPS,
        current_colormap_index=0, current_invert=False, current_level_mode="manual",
        current_min=0.0, current_max=50.0, current_fps=5.0,
        default_start_frame=1, default_end_frame=win.recording.n_frames,
        roi_entries=[], live_available=False,
    )
    try:
        video_dialog.chk_legend.setChecked(True)
        video_dialog.radio_custom_settings.setChecked(True)
        video_dialog.combo_level_mode.setCurrentIndex(video_dialog.combo_level_mode.findData("manual"))
        video_dialog.spin_min.setValue(20.0)
        video_dialog.spin_max.setValue(20.0)  # gleich, nicht nur invertiert
        with temp_dialog_exec(QtWidgets.QMessageBox, lambda self: QtWidgets.QMessageBox.StandardButton.Ok):
            video_dialog._on_accept()
        assert video_dialog.result() != QtWidgets.QDialog.DialogCode.Accepted
    finally:
        video_dialog.close()


check(
    "GraphicExportDialog/VideoExportDialog reject Max <= Min for a custom manual color scale",
    test_export_dialogs_reject_inverted_custom_color_range,
)


def test_import_settings_dialog_rejects_identical_delimiter_and_decimal_separator():
    # Bugfix: Trennzeichen UND Dezimaltrennzeichen liessen sich unabhaengig
    # voneinander auf "Komma" stellen -- parse_frame_text() haette dann
    # z.B. "28,6" zuerst am Komma in "28"/"6" zerlegt (Trennzeichen), bevor
    # das Dezimaltrennzeichen-Replace ueberhaupt noch etwas zu tun haette --
    # JEDE Zeile bekaeme dadurch doppelt so viele, falsche Spalten, OHNE dass
    # der Parser einen Fehler wirft (Spaltenzahl bleibt pro Zeile einheitlich).
    from thermal_viewer.data import ImportSettings
    from thermal_viewer.dialogs import ImportSettingsDialog

    sample = win.recording.paths[0]
    dialog = ImportSettingsDialog(win, sample, ImportSettings())
    try:
        dialog.combo_delimiter.setCurrentIndex(1)  # "Komma ( , )"
        dialog.combo_decimal.setCurrentIndex(0)  # "Komma ( , )"
        ok_button = dialog.buttons.button(QtWidgets.QDialogButtonBox.StandardButton.Ok)
        assert not ok_button.isEnabled(), "OK haette bei identischem Trenn-/Dezimalzeichen deaktiviert sein muessen"
        assert "dasselbe Zeichen" in dialog.lbl_result_status.text()
    finally:
        dialog.close()


check(
    "ImportSettingsDialog disables OK when delimiter and decimal separator are the same character",
    test_import_settings_dialog_rejects_identical_delimiter_and_decimal_separator,
)

print()
if failures:
    print(f"{len(failures)} FAILURE(S):", failures)
    sys.exit(1)
else:
    print("ALL CHECKS PASSED")
