"""Hauptfenster: Thermobild links, ROI-/Legenden-Steuerung und
Zeitverlauf/Live-Cursor rechts als andockbare, frei in der Breite
verstellbare Panels.
"""
from __future__ import annotations

import colorsys
import contextlib
import csv
import json
import math
import re
from datetime import datetime, timedelta
from functools import partial
from pathlib import Path

import numpy as np
import pyqtgraph as pg
import pyqtgraph.exporters as pg_exporters
from qtpy import QtCore, QtGui, QtSvg, QtWidgets

from .assets import ICON_PATH
from .data import (
    DEFAULT_FILENAME_TEMPLATE,
    ImportSettings,
    Recording,
    RecordingError,
    append_paths,
    compile_filename_template,
    files_matching_template,
    load_paths,
    load_tiff_grayscale,
    render_filename_template,
    tiff_crop_to_temperature,
    validate_filename_template,
)
from .dialogs import (
    AxisSettingsDialog,
    CsvColumnDialog,
    FilenameTemplateDialog,
    GraphicExportDialog,
    ImportSettingsDialog,
    RulerLengthDialog,
    StartTimestampDialog,
    TiffImportDialog,
    VideoExportDialog,
    INDEX_TOKEN,
    render_index_token,
)
from .roi import AdjustableROI, average_value, bounds_px_for
from .widgets import LocaleTolerantDoubleSpinBox

pg.setConfigOptions(imageAxisOrder="row-major", antialias=True)

ROI_COLORS = ["#ef4444", "#22c55e", "#3b82f6", "#eab308", "#a855f7"]
# Standardnamen der ersten 5 Messbereiche (typische Anordnung eines
# Kreuzmusters); weitere (beliebig viele) Messbereiche darueber hinaus
# heissen weiterhin schlicht "ROI n" (siehe default_roi_name).
DEFAULT_ROI_NAMES = ["Oben", "Links", "Mitte", "Rechts", "Unten"]
# Obergrenze fuer "beliebig viele ROIs" -- schuetzt _load_project (siehe dort)
# vor einem riesigen/manipulierten Erzeugungsnummer-Wert ("index") in einer
# .tvproj-Datei, der sonst versuchen wuerde, ebenso viele ROI-Eintraege (je
# ein Plot, eine Listenzeile, Spinboxen, ein Grafik-Item) auf einmal
# anzulegen -- ein Einfrieren/Speicherueberlauf ohne Fortschrittsanzeige oder
# Abbruchmoeglichkeit.
MAX_ROI_COUNT = 500


def default_roi_name(number: int) -> str:
    """Standardname eines Messbereichs (1-basierte Erzeugungsnummer) --
    einzige Stelle, die diese Zuordnung kennt, damit Neuanlage und ein
    Zuruecksetzen auf einen leeren Namen (siehe _on_roi_list_item_changed)
    garantiert denselben Namen ergeben."""
    idx = number - 1
    if 0 <= idx < len(DEFAULT_ROI_NAMES):
        return DEFAULT_ROI_NAMES[idx]
    return f"ROI {number}"


def roi_color_for_number(number: int) -> str:
    """Liefert eine Farbe fuer den n-ten (1-basiert) angelegten Messbereich.
    Die ersten len(ROI_COLORS) verwenden die vertraute feste Palette;
    darueber hinaus (beliebig viele weitere Messbereiche) werden zusaetzliche,
    gut unterscheidbare Farben ueber den Goldenen Schnitt im HSV-Farbraum
    verteilt (freie Nachbearbeitung per Farbwahl bleibt jederzeit möglich)."""
    idx = number - 1
    if 0 <= idx < len(ROI_COLORS):
        return ROI_COLORS[idx]
    hue = (idx * 0.6180339887498949) % 1.0
    r, g, b = colorsys.hsv_to_rgb(hue, 0.65, 0.95)
    return "#%02x%02x%02x" % (round(r * 255), round(g * 255), round(b * 255))

# Zwei feste Farbschemata (Hell/Dunkel) fuer Bild- und Kurven-Widgets sowie
# die restliche Qt-Oberflaeche. "Hell" entspricht dem klassischen weissen
# Hintergrund; "Dunkel" ist das bisherige (unabsichtliche) Erscheinungsbild
# mit schwarzem Bildhintergrund, jetzt als bewusste Wahl mit passender
# Restoberflaeche.
THEMES = {
    "light": {
        "label": "Hell",
        "pg_background": "#ffffff",
        "pg_foreground": "#000000",
    },
    "dark": {
        "label": "Dunkel",
        "pg_background": "#1e1e1e",
        "pg_foreground": "#e0e0e0",
    },
}
DEFAULT_THEME = "light"

COLORMAPS = [
    ("Ironbow", "CET-L17"),
    ("Inferno", "inferno"),
    ("Plasma", "plasma"),
    ("Viridis", "viridis"),
    ("Magma", "magma"),
    ("Turbo", "turbo"),
    ("Graustufen", "CET-L1"),
    ("Hot", "CET-L3"),
    ("Cividis", "cividis"),
    ("Coolwarm", "CET-D1"),
    ("Rainbow", "CET-R2"),
]

# pyqtgraph-IDs, deren Farbverlauf in seiner Rohform (Stopp bei 0.0 ->
# Stopp bei 1.0) entgegen der uebrigen Paletten NICHT von dunkel/kalt nach
# hell/warm verlaeuft -- geprueft anhand der tatsaechlichen LUT-Werte
# (CET-L17 "Ironbow" geht z.B. von WEISS bei 0.0 zu DUNKELBLAU bei 1.0,
# waehrend z.B. Inferno/Turbo/Hot/... bereits korrekt dunkel->hell laufen).
# Diese Paletten werden in _apply_colormap() standardmaessig (Haken
# "Invertiert" AUS) zusaetzlich gespiegelt, damit auch sie kalt=dunkel,
# heiss=hell zeigen.
COLORMAPS_BASE_REVERSED = {"CET-L17"}

DEFAULT_ROI_SIZE = 30.0
# Ab so vielen Frames werden Punktmarker auf den Kurven ausgeblendet (nur
# noch Linie), damit es bei langen Aufnahmen nicht überladen wirkt. Bei
# wenigen Frames (z.B. nur 1) sind Marker nötig, sonst ist gar nichts zu
# sehen -- eine Linie braucht mindestens zwei Punkte.
MAX_FRAMES_WITH_SYMBOLS = 60

# Beschriftungen der Start-/Ende-Buttons der Verlaufs-Interpolation, sowohl im
# Ruhezustand als auch (siehe _on_roi_interp_capture) waehrend des zweistufigen
# Ablaufs "hinspringen -> Messbereich setzen -> hier klicken zum Uebernehmen".
# Bewusst OHNE festen Frame-Bezug im Text (frueher "(1. Bild)"/"(letztes
# Bild)") -- das Ziel-Bild ist jetzt per Spinbox frei waehlbar (Standard:
# weiterhin erstes/letztes Bild), siehe spin_interp_start_frame/-end_frame.
INTERP_START_LABEL = "Start festlegen…"
INTERP_END_LABEL = "Ende festlegen…"
# Eigene Beschriftung je Start/Ende (statt eines gemeinsamen "Position
# übernehmen"): sind beide Buttons gleichzeitig armiert (Start armiert, dann
# ohne abzuschliessen auch Ende angeklickt), waeren sonst zwei Buttons mit
# identischem Text nicht mehr unterscheidbar.
INTERP_START_CAPTURE_LABEL = "Start übernehmen"
INTERP_END_CAPTURE_LABEL = "Ende übernehmen"


def _patch_pg_exporters() -> None:
    """Entfernt den defekten/unerwuenschten "Matplotlib Window"-Export aus
    pyqtgraphs nativem Rechtsklick-Export-Menü und ersetzt den eigenen
    SVG-Exporter durch eine zuverlaessigere QSvgGenerator-basierte Variante.

    pyqtgraphs eingebauter SVGExporter serialisiert Pfade per Hand in XML und
    wirft dabei bei unseren Kurven-Plots (Legende + Datumsachse) reproduzierbar
    "ValueError: not enough values to unpack" beim Zerlegen von
    Pfad-Koordinaten (siehe SVGExporter.correctCoordinates). Die
    QSvgGenerator-Variante nutzt stattdessen Qts eigenen SVG-Malvorgang ueber
    dieselbe Szene-Render-Pipeline, die auch ImageExporter verwendet
    (Exporter.render), und ist damit deutlich robuster.
    """
    # Beide Anpassungen greifen auf undokumentierte pyqtgraph-Interna zu
    # (Exporters-Liste, Matplotlib-Submodul, SVGExporter.export-Signatur).
    # Falls eine zukuenftige pyqtgraph-Version diese Struktur aendert, soll
    # das NICHT den App-Start crashen -- lieber bleibt die (evtl. wieder
    # fehlerhafte/vorhandene) Original-Funktionalitaet bestehen.
    try:
        pg_exporters.Exporter.Exporters = [
            exp for exp in pg_exporters.Exporter.Exporters
            if exp is not pg_exporters.Matplotlib.MatplotlibExporter
        ]
    except AttributeError:
        pass

    def _reliable_svg_export(self, fileName=None, toBytes=False, copy=False):
        if fileName is None and not toBytes and not copy:
            self.fileSaveDialog(filter="Scalable Vector Graphics (*.svg)")
            return None
        source_rect = self.getSourceRect()
        target_rect = self.getTargetRect()
        width = max(1, int(round(target_rect.width())))
        height = max(1, int(round(target_rect.height())))
        generator = QtSvg.QSvgGenerator()
        generator.setSize(QtCore.QSize(width, height))
        generator.setViewBox(QtCore.QRect(0, 0, width, height))
        generator.setTitle("Thermo-Sequenz-Viewer Export")
        buf = None
        if toBytes:
            buf = QtCore.QBuffer()
            buf.open(QtCore.QIODevice.OpenModeFlag.WriteOnly)
            generator.setOutputDevice(buf)
        else:
            generator.setFileName(fileName)
        painter = QtGui.QPainter(generator)
        self.render(painter, QtCore.QRectF(target_rect), source_rect)
        painter.end()
        if toBytes:
            return bytes(buf.data())
        return None

    try:
        pg_exporters.SVGExporter.export = _reliable_svg_export
    except AttributeError:
        pass


_patch_pg_exporters()


class RoiEntry:
    """Bündelt ein frei skalierbares ROI im Bild mit seiner Kurve im
    Zeitverlauf und den zugehörigen Steuer-Widgets im rechten Panel."""

    def __init__(self, number: int, color: str, view_box: pg.ViewBox, curve: pg.PlotDataItem):
        # 1-basierte Erzeugungsnummer -- rein fuer den Standardnamen/Farbwahl
        # (siehe roi_color_for_number), KEINE Listen-/Tab-Position (siehe
        # tab_widget fuer letzteres): Messbereiche koennen jederzeit entfernt
        # werden, wodurch Positionen sich verschieben wuerden.
        self.number = number
        self.color = color
        self.name = default_roi_name(number)
        self.curve = curve
        # Von MainWindow._add_roi_tab_page gesetzt -- Referenz auf die eigene
        # Seite im roi_stack (QStackedWidget), damit deren AKTUELLE Position
        # jederzeit zuverlaessig ueber roi_stack.indexOf(entry.tab_widget)
        # ermittelbar ist (statt einer moeglicherweise veralteten festen
        # Nummer). list_item ist der zugehoerige Eintrag in der
        # ROI-Namensliste (roi_list) links daneben.
        self.tab_widget: QtWidgets.QWidget | None = None
        self.list_item: QtWidgets.QListWidgetItem | None = None
        self.placed = False
        # Verlaufs-Interpolation (Punkt 3): Start-/Ende-Geometrie je als
        # ((x, y), (w, h)) in Bildkoordinaten (oben-links), nicht Mittelpunkt.
        self.interp_enabled = False
        self.interp_start: tuple[tuple[float, float], tuple[float, float]] | None = None
        self.interp_end: tuple[tuple[float, float], tuple[float, float]] | None = None
        # Frame-Index (nicht Zeitstempel!) der beiden Keyframes -- die
        # Interpolation laeuft linear ueber den Frame-Index zwischen diesen
        # beiden Werten, NICHT ueber reale Zeitstempel: bei unregelmaessig
        # getakteten Aufnahmen (z.B. Pausen waehrend einer Live-Aufnahme)
        # wuerde eine zeitbasierte Interpolation ihr Ziel je nach
        # Aufnahmeluecken schon deutlich vor dem tatsaechlichen Ende-Keyframe
        # erreichen. Frame-Index-basiert garantiert, dass die Bewegung exakt
        # ueber die gesamte gewaehlte Bildspanne (Start- bis Ende-Keyframe)
        # verteilt ist.
        self.interp_start_frame: int | None = None
        self.interp_end_frame: int | None = None
        # Zweistufiger Erfassungs-Ablauf der Start-/Ende-Buttons (siehe
        # MainWindow._on_roi_interp_capture): True zwischen "zum Bild gesprungen"
        # und "Position uebernommen".
        self.interp_arm_start = False
        self.interp_arm_end = False

        # Ob die Live-Temperatur neben dem Namen im Bild mit angezeigt wird
        # (Standard: an) -- siehe _refresh_label_text/chk_show_temperature.
        self.show_temperature = True

        pen = pg.mkPen(color, width=2)
        hover_pen = pg.mkPen(color, width=3)
        self.roi = AdjustableROI([0, 0], DEFAULT_ROI_SIZE, pen=pen, hoverPen=hover_pen, removable=False)
        self.roi.setVisible(False)
        view_box.addItem(self.roi)

        # Namensbeschriftung direkt im Bild, oben links über dem ROI-Rechteck.
        # Zeigt zusaetzlich (zweite Zeile) die aktuelle gemittelte Temperatur
        # dieses Messbereichs (Punkt 10, siehe update_temperature_label).
        self._last_temperature: float | None = None
        self.label = pg.TextItem(text=self.name, color=color, anchor=(0, 1), fill=(0, 0, 0, 140))
        self.label.setVisible(False)
        view_box.addItem(self.label)

        # Werden von MainWindow gesetzt, hier nur als Platzhalter für Typklarheit.
        self.btn_place: QtWidgets.QPushButton | None = None
        self.btn_color: QtWidgets.QPushButton | None = None
        self.spin_x: QtWidgets.QDoubleSpinBox | None = None
        self.spin_y: QtWidgets.QDoubleSpinBox | None = None
        self.spin_width: QtWidgets.QDoubleSpinBox | None = None
        self.spin_height: QtWidgets.QDoubleSpinBox | None = None
        self.mm_label: QtWidgets.QLabel | None = None
        self.chk_interp: QtWidgets.QCheckBox | None = None
        self.btn_interp_start: QtWidgets.QPushButton | None = None
        self.btn_interp_end: QtWidgets.QPushButton | None = None
        self.spin_interp_start_frame: QtWidgets.QSpinBox | None = None
        self.spin_interp_end_frame: QtWidgets.QSpinBox | None = None
        self.btn_remove: QtWidgets.QPushButton | None = None
        self.chk_show_temperature: QtWidgets.QCheckBox | None = None
        self.chk_circular: QtWidgets.QCheckBox | None = None

    def set_name(self, name: str) -> None:
        self.name = name
        self.curve.opts["name"] = name
        self._refresh_label_text()

    def _refresh_label_text(self) -> None:
        # Punkt: Live-Temperatur RECHTS NEBEN dem Namen (statt einer eigenen
        # Zeile darunter) -- kompaktere Beschriftung im Bild. Per
        # chk_show_temperature (Standard: an) individuell abschaltbar, ohne
        # den Namen selbst auszublenden.
        if self._last_temperature is None or not self.show_temperature:
            self.label.setText(self.name)
        else:
            self.label.setText(f"{self.name}: {self._last_temperature:.1f} °C")

    def average(self, block: np.ndarray, row0: int, row1: int, col0: int, col1: int):
        """Mittelt block (siehe average_value) -- rechteckig oder, falls
        dieser Messbereich "als Kreis behandeln" aktiviert hat
        (self.roi.is_circular), nur ueber die in die Bounding-Box
        eingeschriebene Ellipse. Einzige Stelle, die diese Unterscheidung
        kennt, damit sie an jeder Aufrufstelle (Live-Beschriftung,
        Kurvenberechnung) automatisch konsistent greift."""
        return average_value(block, row0, row1, col0, col1, self.roi.is_circular)

    def update_temperature_label(self, temperature: float) -> None:
        """Aktualisiert die im Bild angezeigte Beschriftung um die aktuell
        gemittelte Temperatur dieses Messbereichs (Punkt 10) -- wird bei
        jedem Frame-Wechsel fuer alle platzierten Messbereiche neu
        aufgerufen, damit der Wert live mitlaeuft."""
        self._last_temperature = temperature
        self._refresh_label_text()

    def set_color(self, color: str) -> None:
        self.color = color
        self.roi.setPen(pg.mkPen(color, width=2))
        self.roi.hoverPen = pg.mkPen(color, width=3)
        self.curve.setPen(pg.mkPen(color, width=2))
        self.curve.setSymbolBrush(color)
        self.label.setColor(color)
        if self.btn_color is not None:
            self.btn_color.setStyleSheet(
                f"background-color:{color}; border:1px solid #333; border-radius:4px;"
            )

    def sync_label_pos(self) -> None:
        x, y = self.roi.pos()
        self.label.setPos(x, y)

    def is_visible_checked(self) -> bool:
        if self.list_item is None:
            return True
        return self.list_item.checkState() == QtCore.Qt.CheckState.Checked

    def place(self, center_x: float, center_y: float, width: float, height: float) -> None:
        width = max(width, 1.0)
        height = max(height, 1.0)
        pos = (center_x - width / 2, center_y - height / 2)
        # update=False auf setSize: setPos() direkt danach loest ohnehin eine
        # eigene sigRegionChanged/sigRegionChangeFinished-Emission aus (siehe
        # pg.ROI.setPos-Docstring "You can then use stateChanged() to complete
        # the state change") -- ohne update=False wuerden Groesse UND Position
        # hier JEWEILS EINZELN je zwei Signale ausloesen, wodurch jeder Aufruf
        # von place() (z.B. bei jeder Eingabefeld-Aenderung, siehe
        # spin.valueChanged) die Kurven-Neuberechnung mehrfach redundant
        # anstossen wuerde.
        self.roi.setSize([width, height], update=False)
        self.roi.setPos(list(pos))
        self.placed = True
        visible = self.is_visible_checked()
        self.roi.setVisible(visible)
        self.sync_label_pos()
        self.label.setVisible(visible)

    def center(self) -> tuple[float, float]:
        x, y = self.roi.pos()
        w, h = self.roi.size()
        return x + w / 2, y + h / 2

    def width(self) -> float:
        return float(self.roi.size()[0])

    def height(self) -> float:
        return float(self.roi.size()[1])

    def bounds_px(self, grid_shape: tuple[int, int]) -> tuple[int, int, int, int]:
        return self.roi.bounds_px(grid_shape)

    def remove_from_view_box(self, view_box: pg.ViewBox) -> None:
        """Loest ROI-Rechteck und Namens-Beschriftung endgueltig aus dem Bild
        (siehe MainWindow._on_roi_remove_clicked fuer Kurve/Tab, die hier
        nicht bekannt sind)."""
        view_box.removeItem(self.roi)
        view_box.removeItem(self.label)

    def capture_interp_start(self, frame_idx: int) -> None:
        self.interp_start = (tuple(self.roi.pos()), tuple(self.roi.size()))
        self.interp_start_frame = frame_idx

    def capture_interp_end(self, frame_idx: int) -> None:
        self.interp_end = (tuple(self.roi.pos()), tuple(self.roi.size()))
        self.interp_end_frame = frame_idx

    def is_interp_ready(self) -> bool:
        """True, wenn Verlaufs-Interpolation aktiv UND beide Keyframes
        (Geometrie UND Frame-Index) gesetzt sind -- einzige Stelle, die diese
        Bedingungen kombiniert, damit Anzeige (_update_interpolated_rois) und
        Kurvenberechnung (_recompute_curves) nicht unabhaengig voneinander
        auseinanderlaufen koennen."""
        return (
            self.interp_enabled
            and self.interp_start is not None
            and self.interp_end is not None
            and self.interp_start_frame is not None
            and self.interp_end_frame is not None
        )

    def interp_rect(self, frac: float) -> tuple[float, float, float, float]:
        (x0, y0), (w0, h0) = self.interp_start
        (x1, y1), (w1, h1) = self.interp_end
        x = x0 + (x1 - x0) * frac
        y = y0 + (y1 - y0) * frac
        w = w0 + (w1 - w0) * frac
        h = h0 + (h1 - h0) * frac
        return x, y, w, h

    def apply_interp_frame(self, frac: float) -> None:
        if self.interp_start is None or self.interp_end is None:
            return
        x, y, w, h = self.interp_rect(frac)
        self.roi.blockSignals(True)
        self.roi.setSize([max(w, 1.0), max(h, 1.0)])
        self.roi.setPos([x, y])
        self.roi.blockSignals(False)
        self.sync_label_pos()


# Sekunden je Einheit fuer eine numerische Laufzeit-Anzeige ("dritte
# Zeitachse", Nutzerwunsch) -- Modul-Ebene statt Klassenattribut, damit
# TimeAxisItem.tickStrings() und MainWindow._format_runtime()/
# _runtime_export_value() dieselbe Tabelle nutzen, ohne dass TimeAxisItem
# dafuer von MainWindow abhaengen muesste.
_RUNTIME_UNIT_DIVISORS = {"s": 1.0, "min": 60.0, "h": 3600.0}


class TimeAxisItem(pg.DateAxisItem):
    """Zeitachse fuer beide Kurven-Graphen, die wahlweise die echte Uhrzeit
    (Standard, Datum/Uhrzeit-Beschriftung wie gewohnt via DateAxisItem) oder
    die relative Laufzeit (HH:MM:SS ab Aufnahmebeginn) anzeigt. Die
    zugrundeliegenden x-Werte der Kurven bleiben in BEIDEN Modi Unix-Sekunden
    (unveraendert) -- nur die Tick-BESCHRIFTUNG wechselt, dadurch ist keine
    Neuberechnung/Neuzuweisung der Kurvendaten beim Umschalten noetig."""

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.runtime_mode = False
        self.t0 = 0.0
        # Nur waehrend eines SVG-Exports temporaer != 0 gesetzt (siehe
        # MainWindow._rebased_time_axis) -- wird zu jedem Tick-Wert
        # zurueckaddiert, BEVOR die normale Beschriftungslogik (echte
        # Uhrzeit oder Laufzeit) laeuft, damit die angezeigte Beschriftung
        # trotz kuenstlich verkleinerter Achsenwerte unveraendert korrekt
        # bleibt.
        self.export_offset = 0.0
        # Fester Tick-Abstand in Sekunden, NUR im Laufzeit-Modus wirksam
        # (siehe tickValues) -- None = automatisch. Ueber "Achsen
        # einstellen..." pro Graph setzbar (Punkt 6: DateAxisItem waehlt
        # sonst kalender-/uhrzeit-ausgerichtete Intervalle, die relativ zum
        # Aufnahmebeginn haesslich unrunde Werte ergeben, z.B. 00:00:24,
        # 00:01:24 statt 00:00:00, 00:01:00).
        self.manual_spacing: float | None = None
        # Format der Laufzeit-Beschriftung (nur wirksam bei runtime_mode):
        # "hhmmss" (Standard) oder eine fortlaufende Zahl in "s"/"min"/"h" --
        # Nutzerwunsch: eine "dritte Zeitachse" mit frei waehlbarer Einheit,
        # um die Laufzeit ohne manuelles Umrechnen in anderer Software
        # weiterverarbeiten zu koennen (siehe MainWindow._apply_runtime_unit).
        self.runtime_unit = "hhmmss"

    def set_runtime_mode(self, enabled: bool, t0: float = 0.0) -> None:
        if enabled == self.runtime_mode and t0 == self.t0:
            return
        self.runtime_mode = enabled
        self.t0 = t0
        self.picture = None
        self.update()

    def set_runtime_unit(self, unit: str) -> None:
        if unit == self.runtime_unit:
            return
        self.runtime_unit = unit
        self.picture = None
        self.update()

    def set_manual_spacing(self, spacing: float | None) -> None:
        if spacing == self.manual_spacing:
            return
        self.manual_spacing = spacing
        self.picture = None
        self.update()

    def tickValues(self, minVal, maxVal, size):
        if not self.runtime_mode:
            return super().tickValues(minVal, maxVal, size)
        if self.manual_spacing:
            spacing = self.manual_spacing
            first = self.t0 + math.floor((minVal - self.t0) / spacing) * spacing
            # Harte Obergrenze: ohne sie koennte ein sehr kleiner manueller
            # Abstand ueber einen sehr weiten sichtbaren Zeitraum (z.B.
            # 0,1 s Abstand bei einer mehrstuendigen Aufnahme) hunderttausende
            # Ticks erzeugen und die Oberflaeche bei jedem Neuzeichnen/Zoomen
            # spuerbar einfrieren -- die automatische Zweig weiter unten hat
            # dieses Limit implizit ueber pyqtgraphs eigene Dichte-Steuerung,
            # dieser manuelle Zweig braucht es explizit.
            max_ticks = 2000
            values = []
            v = first
            while v <= maxVal + spacing and len(values) < max_ticks:
                values.append(v)
                v += spacing
            return [(spacing, values)]
        # Automatisch, aber relativ zum Aufnahmebeginn (t0) statt absolut
        # kalenderausgerichtet -- DateAxisItem.tickValues() wuerde sonst
        # "schoene" ABSOLUTE Uhrzeiten waehlen, die relativ zu t0 einen
        # unrunden Versatz ergeben (siehe manual_spacing-Kommentar oben).
        # pg.AxisItem.tickValues() (Basisklasse, nicht DateAxisItem) liefert
        # dieselbe "schoene Zahl"-Logik, aber rein linear -- auf die um t0
        # verschobenen Werte angewendet, landet der erste Tick exakt bei
        # Laufzeit 0.
        levels = pg.AxisItem.tickValues(self, minVal - self.t0, maxVal - self.t0, size)
        return [(spacing, [v + self.t0 for v in values]) for spacing, values in levels]

    def tickStrings(self, values, scale, spacing):
        if self.export_offset:
            values = [v + self.export_offset for v in values]
        if not self.runtime_mode:
            return super().tickStrings(values, scale, spacing)
        total_seconds = [max(0.0, v - self.t0) for v in values]
        if self.runtime_unit != "hhmmss":
            divisor = _RUNTIME_UNIT_DIVISORS[self.runtime_unit]
            value_spacing = (spacing / divisor) if spacing else 0.0
            decimals = self._decimals_for_spacing(value_spacing)
            return [f"{seconds / divisor:.{decimals}f}".replace(".", ",") for seconds in total_seconds]
        strings = []
        for seconds in total_seconds:
            total = int(round(seconds))
            hours, rem = divmod(total, 3600)
            minutes, secs = divmod(rem, 60)
            strings.append(f"{hours:02d}:{minutes:02d}:{secs:02d}")
        return strings

    @staticmethod
    def _decimals_for_spacing(value_spacing: float) -> int:
        """Anzahl Nachkommastellen, damit benachbarte Ticks (deren Abstand
        in der Zieleinheit value_spacing betraegt) sich in der Beschriftung
        tatsaechlich unterscheiden -- z.B. Einheit "Stunden" bei einer nur
        wenige Minuten langen Aufnahme wuerde sonst (0 Nachkommastellen)
        fuer jeden Tick "0" anzeigen."""
        if value_spacing <= 0 or value_spacing >= 1:
            return 0
        return min(4, max(1, -int(math.floor(math.log10(value_spacing)))))


class TimelineSlider(QtWidgets.QSlider):
    """Frame-Schieberegler mit zusaetzlichen farbigen Markierungen fuer den
    manuell festlegbaren Auswertungsstart-/-ende-Frame (siehe
    MainWindow._eval_start_index/_eval_end_index). Die Markierungen lassen
    sich direkt per Maus-Drag an ihrer jeweiligen Position verschieben (siehe
    markerDragged), unabhaengig vom normalen Klick-zum-Springen-Verhalten des
    Schiebereglers selbst."""

    markerDragged = QtCore.Signal(str, int)

    _HIT_TOLERANCE_PX = 7

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.start_marker: int | None = None
        self.end_marker: int | None = None
        self._dragging: str | None = None

    def set_markers(self, start: int | None, end: int | None) -> None:
        self.start_marker = start
        self.end_marker = end
        self.update()

    def _marker_x(self, value: int) -> int:
        opt = QtWidgets.QStyleOptionSlider()
        self.initStyleOption(opt)
        style = self.style()
        groove = style.subControlRect(QtWidgets.QStyle.CC_Slider, opt, QtWidgets.QStyle.SC_SliderGroove, self)
        handle = style.subControlRect(QtWidgets.QStyle.CC_Slider, opt, QtWidgets.QStyle.SC_SliderHandle, self)
        span = max(1, groove.width() - handle.width())
        pos = QtWidgets.QStyle.sliderPositionFromValue(self.minimum(), self.maximum(), value, span)
        return groove.x() + pos + handle.width() // 2

    def _value_from_x(self, x: int) -> int:
        opt = QtWidgets.QStyleOptionSlider()
        self.initStyleOption(opt)
        style = self.style()
        groove = style.subControlRect(QtWidgets.QStyle.CC_Slider, opt, QtWidgets.QStyle.SC_SliderGroove, self)
        handle = style.subControlRect(QtWidgets.QStyle.CC_Slider, opt, QtWidgets.QStyle.SC_SliderHandle, self)
        span = max(1, groove.width() - handle.width())
        pos = max(0, min(x - groove.x() - handle.width() // 2, span))
        return QtWidgets.QStyle.sliderValueFromPosition(self.minimum(), self.maximum(), pos, span)

    def _marker_at(self, pos: QtCore.QPoint) -> str | None:
        if self.maximum() <= self.minimum():
            return None
        for name, value in (("start", self.start_marker), ("end", self.end_marker)):
            if value is None:
                continue
            x = self._marker_x(max(self.minimum(), min(value, self.maximum())))
            if abs(pos.x() - x) <= self._HIT_TOLERANCE_PX:
                return name
        return None

    def mousePressEvent(self, event) -> None:
        hit = self._marker_at(event.pos())
        if hit is not None:
            self._dragging = hit
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:
        if self._dragging is not None:
            value = self._value_from_x(event.pos().x())
            if self._dragging == "start":
                self.start_marker = value
            else:
                self.end_marker = value
            self.update()
            self.markerDragged.emit(self._dragging, value)
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:
        if self._dragging is not None:
            self._dragging = None
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def paintEvent(self, event) -> None:
        super().paintEvent(event)
        if self.maximum() <= self.minimum():
            return
        painter = QtGui.QPainter(self)
        painter.setRenderHint(QtGui.QPainter.Antialiasing)
        h = self.height()
        for value, color in ((self.start_marker, "#22c55e"), (self.end_marker, "#ef4444")):
            if value is None:
                continue
            x = self._marker_x(max(self.minimum(), min(value, self.maximum())))
            painter.setPen(QtGui.QPen(QtGui.QColor(color), 2))
            painter.drawLine(x, 0, x, 5)
            painter.drawLine(x, h - 5, x, h)
        painter.end()


class _StaysOpenMenu(QtWidgets.QMenu):
    """QMenu, das nach dem Anklicken eines ANKREUZBAREN Eintrags NICHT
    schliesst (Standard-Qt-Verhalten schliesst jedes Menue nach jedem
    Klick, auch bei Checkboxen) -- fuer Menues mit mehreren unabhaengigen
    Checkboxen wie "Ansicht" (Bugreport: "möchte nicht jedes Mal das Menü
    erneut ausklappen müssen"). Schliesst weiterhin normal bei Klick auf
    einen NICHT-ankreuzbaren Eintrag oder ausserhalb des Menues."""

    def mouseReleaseEvent(self, event: QtGui.QMouseEvent) -> None:
        action = self.activeAction()
        if action is not None and action.isCheckable() and action.isEnabled():
            action.trigger()
            event.accept()
            return
        super().mouseReleaseEvent(event)


class MainWindow(QtWidgets.QMainWindow):
    # Reduzierter Stiftbreiten-Skalierungsfaktor NUR fuer den SVG-Export
    # (siehe _scaled_export_visuals) -- Vektor-Linien wirken bei identischer
    # Pixelbreite optisch kraeftiger als die entsprechende (leicht
    # antialiaste) Raster-Linie. Bugreport ("Kurvenlinien im SVG-Export
    # etwas zu dick", siehe datasets/Zeitverlauf_mit_Position_I_Kurve.svg --
    # stroke-width="4" bei 300 DPI): 0.65 war noch zu hoch, mit 0.5 ergibt
    # sich bei 300 DPI eine sichtbar duennere stroke-width="3", waehrend die
    # Standard-Aufloesung (150 DPI, stroke-width="2") unveraendert bleibt.
    _SVG_PEN_SCALE_FACTOR = 0.5

    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("Thermo-Sequenz-Viewer")
        self.setWindowIcon(QtGui.QIcon(str(ICON_PATH)))
        self.resize(1600, 950)

        self.recording: Recording | None = None
        self.current_index = 0
        self._armed_entry: RoiEntry | None = None
        # Linksklick ins Bild fixiert den Live-Cursor auf dieser Stelle (Verlauf
        # bleibt stehen, Mausbewegung wird ignoriert); Rechtsklick hebt die
        # Fixierung wieder auf und die Live-Ansicht folgt wieder der Maus.
        self._live_pinned = False
        self._hover_row: int | None = None
        self._hover_col: int | None = None
        # Kantenlaenge (ungerade Pixelzahl) des um das Cursor-Pixel
        # gemittelten Bereichs fuer Live-Verlauf/-Anzeige (Werkzeuge-Menue
        # "Live-Cursor-Bereichsgröße") -- Standard: 5x5.
        self._live_cursor_kernel_size = 5
        self.roi_entries: list[RoiEntry] = []
        # Naechste zu vergebende 1-basierte Erzeugungsnummer (siehe
        # RoiEntry.number) -- steigt monoton, auch nach Entfernen von
        # Messbereichen, damit Standardname/Farbe neuer ROIs nie eine
        # zuvor bereits vergebene Nummer wiederverwenden.
        self._roi_next_number = 1
        self._current_theme = DEFAULT_THEME
        # Graphen (Zeitverlauf/Live) und Thermobild haben JEWEILS eine feste,
        # vom App-Design (Hell-/Dunkelmodus-Schalter) UNABHAENGIGE Farbgebung
        # (Nutzerwunsch): Graphen bleiben immer HELL (wissenschaftlicher
        # Standard, gut lesbar auch beim Einfuegen in Berichte/Ausdrucke),
        # das Thermobild bleibt immer DUNKEL (besserer Kontrast zu Hotspots)
        # -- unabhaengig davon, ob die uebrige Oberflaeche gerade hell oder
        # dunkel ist. Siehe _apply_curve_colors/_apply_image_colors, einmalig
        # beim Start angewendet (NICHT mehr Teil von _apply_theme).
        self._graph_bg = THEMES["light"]["pg_background"]
        self._graph_fg = THEMES["light"]["pg_foreground"]
        self._image_bg = THEMES["dark"]["pg_background"]
        self._image_fg = THEMES["dark"]["pg_foreground"]
        # Min/Max ueber alle Frames der aktuellen Aufnahme (Punkt 1), einmalig
        # beim Laden berechnet.
        self._global_level_range: tuple[float, float] | None = None
        # Maßstab (Punkt 12): mm pro Pixel, None = kein Maßstab definiert.
        self._px_to_mm: float | None = None
        # Reale Laenge (mm) der Referenzlinie, UNABHAENGIG von ihrer aktuellen
        # Pixel-Distanz (Punkt 11) -- beim Ziehen der Endpunkte bleibt dieser
        # Wert konstant und _px_to_mm wird aus der neuen Pixel-Distanz neu
        # berechnet; beim Doppelklick-Bearbeiten ist es umgekehrt.
        self._ruler_mm_value: float | None = None
        self._ruler_armed = False
        self._ruler_start: tuple[float, float] | None = None
        # Waehrend der Klick-Klick-Erstellung (siehe _handle_ruler_click) nur
        # ein einfacher, nicht interaktiver Vorschau-Marker -- die fertige
        # Linie (self._ruler_line) ist ein ziehbares LineSegmentROI.
        self._ruler_preview_marker: pg.PlotDataItem | None = None
        self._ruler_line: pg.LineSegmentROI | None = None
        self._ruler_text: pg.TextItem | None = None
        # Anpassbar (siehe btn_ruler_color), da eine feste Farbe bei manchen
        # Farbverlaeufen (z.B. "Hot") auf der Referenzlinie kaum zu erkennen
        # waere.
        self._ruler_color = "#ff2d55"
        # Mess-Werkzeug (Punkt 1, Folgeanfrage zu Punkt 12): nutzt einen
        # bereits definierten Maßstab (_px_to_mm) nur LESEND, um beliebige
        # Strecken im Bild in mm anzuzeigen -- im Gegensatz zum Lineal-
        # Werkzeug oben wird dabei nie _px_to_mm (neu) gesetzt.
        self._measure_armed = False
        self._measure_start: tuple[float, float] | None = None
        self._measure_preview_marker: pg.PlotDataItem | None = None
        self._measure_line: pg.LineSegmentROI | None = None
        self._measure_text: pg.TextItem | None = None
        self._measure_color = "#2dd4bf"
        # Zeitachsen-Anzeige beider Kurven-Graphen: "clock" (echte Uhrzeit,
        # Standard) oder "runtime" (relative Laufzeit ab Aufnahmebeginn) --
        # ueber je einen Umschalter unten rechts an beiden Graphen wählbar,
        # gemeinsam synchronisiert (siehe _apply_time_display_mode).
        self._time_display_mode = "clock"
        # Format der Laufzeit-Anzeige (Nutzerwunsch: "dritte Zeitachse" mit
        # frei waehlbarer, fortlaufender Einheit statt hh:mm:ss, um die
        # Laufzeit ohne manuelles Umrechnen in anderer Software weiter-
        # verarbeiten zu koennen) -- "hhmmss" (Standard) oder "s"/"min"/"h".
        # EIN globales Format statt einer eigenen Auswahl je Export-Manager:
        # wirkt automatisch ueberall dort, wo "Laufzeit" angezeigt wird
        # (Graph-Achse, Video-/Bildstapel-Export, CSV-Export, Statuszeile),
        # siehe _apply_runtime_unit/_format_runtime.
        self._runtime_unit = "hhmmss"
        # Manuell festlegbarer Start/Ende der Auswertung (0-basierter
        # Frame-Index, None solange keine Aufnahme geladen ist) -- Standard
        # ist der erste bzw. jeweils letzte geladene Frame, per Spinbox oder
        # direktem Ziehen an der gruenen/roten Markierung im Frame-Regler
        # aenderbar (z.B. wenn eine Aufnahme ueber den eigentlich
        # interessanten Zeitraum hinaus weiterlief). Steuert u.a. das Ziel
        # von "Start"/"Ende festlegen" bei der Verlaufs-Interpolation
        # (_jump_to_first_frame/_jump_to_last_frame) sowie den standardmaessig
        # auf diesen Bereich begrenzten Wiedergabe-Loop (_play_clamped, siehe
        # _on_play_toggled).
        self._eval_start_index: int | None = None
        self._eval_end_index: int | None = None
        # Waehrend einer laufenden Wiedergabe: True, wenn die Wiedergabe beim
        # Play-Start innerhalb von [_eval_start_index, _eval_end_index] stand
        # und deshalb an diesem Bereich geloopt/gestoppt wird; wurde der
        # Cursor manuell AUSSERHALB dieses Bereichs positioniert, laeuft die
        # Wiedergabe stattdessen ungeklemmt bis zum tatsaechlichen Ende.
        self._play_clamped = False
        # Live-Ordner-Ueberwachung (Programm soll parallel zu einer laufenden
        # Messung nutzbar sein): laeuft immer automatisch im Hintergrund,
        # sobald ein Ordner geladen ist (_open_folder/_load_folder) -- keine
        # separate Einstellung dafuer, da es keinen Nachteil hat, wenn gerade
        # nichts Neues dazukommt (_check_for_new_files kehrt dann sofort
        # zurueck). Ein einfacher 10s-Timer statt eines Dateisystem-
        # Watchers, damit auch sehr haeufig neu abgelegte Dateien (z.B. alle
        # 500ms) die App nicht durch staendiges Nachladen bremsen.
        self._watched_folder: Path | None = None
        self._live_watch_timer = QtCore.QTimer(self)
        self._live_watch_timer.setInterval(10_000)
        self._live_watch_timer.timeout.connect(self._check_for_new_files)

        self._settings = QtCore.QSettings("ThermalViewer", "ThermalViewer")

        self._build_image_canvas()
        self._build_plots()
        self._build_roi_entries()
        self._build_control_panel()
        self._build_toolbar()
        self._build_docks()
        self._build_menu()
        self._build_shortcuts()
        self._connect_scene_events()

        # Fuer die beiden Kurven-Graphen soll ein Rechtsklick "Exportieren"
        # exakt denselben Weg wie der Export-Menü-Punkt "Grafik
        # exportieren…" nehmen (inkl. Kombiniert/Getrennt- und Graph-Inhalt-
        # Auswahl) -- nicht nur einen aehnlich aussehenden, aber auf diesen
        # einen Graphen beschraenkten Dialog. Fuer das Thermobild selbst
        # gibt es keinen direkten Menü-Eintrag -- bleibt daher beim
        # bisherigen, auf dieses eine Widget beschraenkten Einzel-Export.
        self._bind_native_export(self.glw, suggested_name="Thermobild.png")
        self._bind_native_export(self.timeseries_plot, self._export_graphic)
        self._bind_native_export(self.live_plot, self._export_graphic)

        # _build_image_canvas() setzt vorlaeufig die rohe, unkorrigierte
        # Farbpalette (kein combo_cmap/chk_cmap_invert existierte zu dem
        # Zeitpunkt noch) -- jetzt einmalig durch die tatsaechliche Logik
        # (inkl. COLORMAPS_BASE_REVERSED-Korrektur) ersetzen.
        self._apply_colormap()

        saved_kernel_size = self._settings.value("live_cursor/kernel_size", 5, type=int)
        if saved_kernel_size in self._live_cursor_kernel_actions:
            self._live_cursor_kernel_size = saved_kernel_size
            self._live_cursor_kernel_actions[saved_kernel_size].setChecked(True)

        saved_theme = self._settings.value("theme", DEFAULT_THEME)
        self._apply_theme(saved_theme if saved_theme in THEMES else DEFAULT_THEME)
        # Graphen-/Thermobild-Farben sind seit dem Nutzerwunsch "Graph immer
        # hell, Thermobild immer dunkel" NICHT mehr Teil von _apply_theme --
        # hier einmalig mit ihren festen Werten (siehe __init__) anwenden.
        self._apply_curve_colors(self._graph_bg, self._graph_fg)
        self._apply_image_colors(self._image_bg, self._image_fg)

        saved_runtime_unit = self._settings.value("runtime_unit", "hhmmss")
        self._apply_runtime_unit(saved_runtime_unit if saved_runtime_unit in ("hhmmss", "s", "min", "h") else "hhmmss")

        saved_time_mode = self._settings.value("time_display_mode", "clock")
        self._apply_time_display_mode(saved_time_mode if saved_time_mode in ("clock", "runtime") else "clock")

        # Dateinamens-Schema (Punkt 5): standardmaessig "Record_YYYY-MM-DD_
        # hh-mm-ss", per QSettings dauerhaft ueberschreibbar (siehe
        # FilenameTemplateDialog/_set_filename_template). _active_* haelt
        # zusaetzlich fest, welches Schema die AKTUELL geladene Aufnahme
        # tatsaechlich verwendet hat -- kann vom Standard abweichen, wenn der
        # Nutzer beim letzten "Ordner öffnen…" ein nur EINMALIG (nicht
        # dauerhaft) geltendes Schema gewaehlt hat; die Live-Ordner-
        # Ueberwachung (_check_for_new_files) muss dieses (nicht das
        # Standard-)Schema weiterverwenden, sonst koennten neu hinzukommende
        # Dateien derselben Aufnahme nicht mehr korrekt eingeordnet werden.
        saved_template = self._settings.value("filename_template", None)
        if isinstance(saved_template, str) and validate_filename_template(saved_template) is None:
            self._filename_template = saved_template
        else:
            self._filename_template = DEFAULT_FILENAME_TEMPLATE
        self._filename_pattern, self._filename_strptime_fmt = compile_filename_template(self._filename_template)
        self._active_filename_pattern = self._filename_pattern
        self._active_filename_strptime_fmt = self._filename_strptime_fmt

        # Datenimport-Manager (Punkt: "Programm zeitnah auf andere Dateien
        # erweitern ... Import-Manager, mit dem wir Dateien lesen und zum
        # Einladen vorbereiten koennen"): analog zum Namensschema oben ein
        # global per QSettings persistierbares Standard-Rohformat
        # (Trennzeichen/Dezimaltrennzeichen/Kodierung/Kopf-Fusszeilen/
        # Spalten), plus _active_import_settings fuer das Format, mit dem
        # die AKTUELL geladene Aufnahme tatsaechlich geladen wurde (siehe
        # _check_for_new_files -- Live-Ordner-Ueberwachung muss konsistent
        # dasselbe Format weiterverwenden).
        self._import_settings = self._load_import_settings()
        self._active_import_settings = self._import_settings

        self.statusBar().showMessage("Bereit. Bitte Ordner oder Dateien laden (Datei-Menü oder Symbolleiste).")

    # ------------------------------------------------------------------ UI
    def _build_image_canvas(self) -> None:
        self.glw = pg.GraphicsLayoutWidget()
        self.plot_item = self.glw.addPlot(row=0, col=0)
        self.plot_item.setAspectLocked(True)
        self.plot_item.invertY(True)
        self.plot_item.showGrid(x=False, y=False)
        self.view_box = self.plot_item.getViewBox()
        # Bild soll fest stehen: kein Verschieben/Zoomen per Maus, nur ROIs
        # reagieren noch auf Klicks/Ziehen. Beim Laden einer Messreihe wird
        # die sichtbare Ansicht ohnehin passend auf die Bildgroesse eingestellt
        # (siehe _set_recording), ein manuelles Zuruecksetzen ist daher nicht
        # noetig.
        self.view_box.setMouseEnabled(x=False, y=False)
        self.view_box.setMenuEnabled(False)

        self.image_item = pg.ImageItem()
        self.plot_item.addItem(self.image_item)

        # Markiert dauerhaft das zuletzt mit der Maus angefahrene Pixel im
        # Bild, damit beim Export des Live-Verlaufs erkennbar ist, an
        # welcher Stelle im Bild die Kurve gemessen wurde (bleibt bis zum
        # nächsten Hover bzw. bis eine neue Aufnahme geladen wird stehen).
        self.live_cursor_marker = pg.ScatterPlotItem(
            size=16, symbol="+", pen=pg.mkPen("#38bdf8", width=2), brush=None
        )
        self.live_cursor_marker.setZValue(10)
        self.live_cursor_marker.setVisible(False)
        self.plot_item.addItem(self.live_cursor_marker)

        # Zeigt die Live-Temperatur DES AKTUELLEN FRAMES direkt am
        # Cursor-Kreuz im Bild an (statt nur im Live-Graph/der Statuszeile) --
        # aktualisiert sich sowohl bei Mausbewegung als auch beim
        # Frame-Wechsel waehrend der Wiedergabe (siehe _update_live_cursor_label).
        self.live_cursor_label = pg.TextItem(text="", color="#38bdf8", anchor=(0, 1), fill=(0, 0, 0, 160))
        self.live_cursor_label.setZValue(11)
        self.live_cursor_label.setVisible(False)
        self.plot_item.addItem(self.live_cursor_label)

        self.histogram = pg.HistogramLUTItem()
        self.histogram.setImageItem(self.image_item)
        self.histogram.gradient.setColorMap(pg.colormap.get(COLORMAPS[0][1]))
        self.glw.addItem(self.histogram, row=0, col=1)

        self._build_timeline_bar()

        central = QtWidgets.QWidget()
        central_layout = QtWidgets.QVBoxLayout(central)
        central_layout.setContentsMargins(0, 0, 0, 0)
        central_layout.setSpacing(0)
        central_layout.addWidget(self.glw, 1)
        central_layout.addWidget(self.timeline_bar, 0)
        self.setCentralWidget(central)

    def _build_timeline_bar(self) -> None:
        # Zeitleiste/Wiedergabe-Steuerung (Punkt 9): unterhalb des Thermobilds
        # statt oben in einer Symbolleiste, damit sie nur die Breite des
        # linken Frames einnimmt (gleiche Spalte im zentralen Layout wie
        # self.glw) statt der gesamten Fensterbreite.
        #
        # Zwei Zeilen statt einer: eine einzelne, immer laenger werdende
        # Zeile (Play/Slider/Frame/Auswertungsstart/-ende/FPS/Zeitstempel)
        # zwingt sonst -- sobald echte Frame-Zahlen/ein echter Zeitstempel
        # geladen sind -- ihre Mindestbreite auf das GESAMTE Hauptfenster
        # (central widget + rechte Docks muessen alle hineinpassen), wodurch
        # das Fenster beim Laden ungewollt breiter wird bzw. die rechten
        # Docks unerwuenscht gequetscht werden.
        self.timeline_bar = QtWidgets.QWidget()
        outer = QtWidgets.QVBoxLayout(self.timeline_bar)
        outer.setContentsMargins(6, 4, 6, 4)
        outer.setSpacing(2)

        row1 = QtWidgets.QHBoxLayout()
        outer.addLayout(row1)

        self.play_button = QtWidgets.QPushButton("▶ Play")
        self.play_button.setCheckable(True)
        self.play_button.toggled.connect(self._on_play_toggled)
        row1.addWidget(self.play_button)

        row1.addWidget(QtWidgets.QLabel(" Frame: "))
        self.frame_slider = TimelineSlider(QtCore.Qt.Horizontal)
        self.frame_slider.setMinimumWidth(120)
        self.frame_slider.setRange(0, 0)
        self.frame_slider.setToolTip(
            "Grüne Markierung: Auswertungsstart. Rote Markierung: Auswertungsende.\n"
            "Beide lassen sich direkt hier per Ziehen verschieben."
        )
        self.frame_slider.valueChanged.connect(self._on_slider_changed)
        self.frame_slider.markerDragged.connect(self._on_timeline_marker_dragged)
        row1.addWidget(self.frame_slider, 1)

        self.frame_spin = QtWidgets.QSpinBox()
        self.frame_spin.setRange(1, 1)
        self.frame_spin.valueChanged.connect(self._on_frame_spin_changed)
        row1.addWidget(self.frame_spin)

        row1.addWidget(QtWidgets.QLabel("  FPS: "))
        self.fps_spin = LocaleTolerantDoubleSpinBox()
        self.fps_spin.setRange(0.5, 60.0)
        self.fps_spin.setValue(10.0)
        self.fps_spin.valueChanged.connect(self._on_fps_changed)
        row1.addWidget(self.fps_spin)

        self.timestamp_label = QtWidgets.QLabel("  –")
        # Fett + Einzug bewusst NICHT per setStyleSheet (Qt uebernimmt bei
        # stylesheet-gestylten Widgets eine spaeter geaenderte QApplication-
        # Palette in der Praxis nicht immer zuverlaessig, siehe _apply_theme) --
        # QFont/Contents-Margins sind von diesem Palette-Caching nicht betroffen.
        bold_font = self.timestamp_label.font()
        bold_font.setBold(True)
        self.timestamp_label.setFont(bold_font)
        self.timestamp_label.setContentsMargins(8, 0, 0, 0)
        row1.addWidget(self.timestamp_label)

        row2 = QtWidgets.QHBoxLayout()
        outer.addLayout(row2)

        row2.addWidget(QtWidgets.QLabel("Auswertungsstart: "))
        self.spin_eval_start = QtWidgets.QSpinBox()
        self.spin_eval_start.setRange(1, 1)
        self.spin_eval_start.setToolTip(
            "Erster Frame, der als Start gilt -- z.B. für „Start festlegen“ bei der\n"
            "Verlaufs-Interpolation (grüne Markierung im Frame-Regler, auch direkt\n"
            "per Ziehen verschiebbar). Die Wiedergabe bleibt standardmäßig auf\n"
            "diesen Bereich begrenzt."
        )
        self.spin_eval_start.valueChanged.connect(self._on_eval_start_changed)
        row2.addWidget(self.spin_eval_start)

        row2.addWidget(QtWidgets.QLabel("  Auswertungsende: "))
        self.spin_eval_end = QtWidgets.QSpinBox()
        self.spin_eval_end.setRange(1, 1)
        self.spin_eval_end.setToolTip(
            "Letzter Frame, der als Ende gilt -- z.B. für „Ende festlegen“ bei der\n"
            "Verlaufs-Interpolation (rote Markierung im Frame-Regler, auch direkt\n"
            "per Ziehen verschiebbar). Die Wiedergabe bleibt standardmäßig auf\n"
            "diesen Bereich begrenzt."
        )
        self.spin_eval_end.valueChanged.connect(self._on_eval_end_changed)
        row2.addWidget(self.spin_eval_end)
        row2.addStretch(1)

        self.play_timer = QtCore.QTimer(self)
        self.play_timer.timeout.connect(self._advance_frame)

    def _build_time_display_row(
        self, plot_widget: pg.PlotWidget
    ) -> tuple[QtWidgets.QHBoxLayout, QtWidgets.QComboBox, QtWidgets.QComboBox]:
        """Zeile mit Achsen-Reset-/Achsen-Einstellen-Knoepfen und
        rechtsbuendigem Uhrzeit/Laufzeit-Umschalter (plus Laufzeit-Format,
        siehe unten), unterhalb eines Kurven-Graphen platziert (also unten
        rechts an diesem Graphen, Punkt 9/Punkt 5)."""
        row = QtWidgets.QHBoxLayout()
        btn_reset_view = QtWidgets.QPushButton("Achsen zurücksetzen")
        btn_reset_view.setToolTip(
            "Setzt Zoom/Verschieben dieses Graphen zurück (X- und Y-Achse wieder auf den "
            "kompletten Datenbereich) -- falls per Maus verzoomt/verschoben wurde."
        )
        btn_reset_view.clicked.connect(partial(self._reset_plot_view, plot_widget))
        row.addWidget(btn_reset_view)

        btn_axis_settings = QtWidgets.QPushButton("Achsen einstellen…")
        btn_axis_settings.setToolTip(
            "Y-Achse (Temperatur): Wertebereich und/oder Schrittweite manuell festlegen. "
            "X-Achse (Zeit): Wertebereich manuell festlegen (eine feste Schrittweite ist dort "
            "nicht wählbar, siehe Dialog) -- Alternative zum pyqtgraph-eigenen, schwerer "
            "auffindbaren Rechtsklick-Menü „X/Y axis“."
        )
        btn_axis_settings.clicked.connect(partial(self._open_axis_settings, plot_widget))
        row.addWidget(btn_axis_settings)
        row.addStretch(1)
        row.addWidget(QtWidgets.QLabel("Zeitachse:"))
        combo = QtWidgets.QComboBox()
        combo.addItem("Uhrzeit", "clock")
        combo.addItem("Laufzeit", "runtime")
        combo.setToolTip("Zeigt die x-Achse als echte Uhrzeit oder als Laufzeit seit Aufnahmebeginn.")
        row.addWidget(combo)

        # Laufzeit-Format ("dritte Zeitachse", Nutzerwunsch): statt fix
        # hh:mm:ss auch eine fortlaufende Zahl in frei waehlbarer Einheit --
        # wirkt global (siehe _apply_runtime_unit), daher nur EIN Format je
        # Instanz noetig, hier aber zwei synchronisierte Umschalter (je
        # einer pro Graph), analog zum Uhrzeit/Laufzeit-Umschalter oben.
        format_combo = QtWidgets.QComboBox()
        format_combo.addItem("hh:mm:ss", "hhmmss")
        format_combo.addItem("Laufzeit in Sekunden", "s")
        format_combo.addItem("Laufzeit in Minuten", "min")
        format_combo.addItem("Laufzeit in Stunden", "h")
        format_combo.setToolTip(
            "Format der Laufzeit-Anzeige -- \"hh:mm:ss\" (Standard) oder eine fortlaufende "
            "Dezimalzahl in der gewählten Einheit (erleichtert das Weiterverarbeiten/Zeichnen in "
            "anderer Software, ohne die Zeit vorher selbst umrechnen zu müssen). Gilt einheitlich "
            "überall, wo die Laufzeit angezeigt wird: hier, im Video-/Bildstapel-Export, im "
            "CSV-Export und in der Statuszeile. Nur wirksam, solange links „Laufzeit“ gewählt ist."
        )
        format_combo.setEnabled(False)
        row.addWidget(format_combo)
        return row, combo, format_combo

    @staticmethod
    def _trim_plot_context_menu(plot_widget: pg.PlotWidget) -> None:
        """Blendet die pyqtgraph-Standard-Menüpunkte "Transforms", "Downsample",
        "Average", "Alpha" und "Points" im Rechtsklick-Menü aus -- fuer eine
        einfache Temperatur-ueber-Zeit-Kurve ohne praktischen Nutzen und nur
        Ablenkung (Nutzerwunsch: "Schmeiße unnötige Optionen raus"). "Grid"
        bleibt (nuetzlich), ebenso das eigentliche ViewBox-Menü ("View All",
        "X/Y axis", "Mouse Mode" -- siehe Bedienungsanleitung, Abschnitt 8)."""
        plot_item = plot_widget.getPlotItem()
        for name in ("Transforms", "Downsample", "Average", "Alpha", "Points"):
            plot_item.setContextMenuActionVisible(name, False)

    @staticmethod
    def _reset_plot_view(plot_widget: pg.PlotWidget) -> None:
        # Bugfix: enableAutoRange() liess den Auto-Fit-Modus dauerhaft AN
        # (pyqtgraph passt die Achsen danach bei JEDER weiteren
        # Datenaenderung/Groessenaenderung automatisch neu an) -- in
        # Kombination mit der dynamischen Achsenbeschriftungsbreite fuehrte
        # das dazu, dass der sichtbare Bereich bei mehrfachem Klicken immer
        # weiter schrumpfte, statt stabil auf den vollen Datenbereich zu
        # bleiben (Bugreport: "schrumpft immer weiter"). autoRange() allein
        # passt die Achsen genau EINMAL an den vollen Datenbereich an, ohne
        # den Dauer-Modus zu aktivieren -- danach bleibt die Ansicht stabil,
        # bis der Nutzer erneut manuell zoomt/verschiebt oder den Knopf
        # wieder anklickt.
        plot_widget.getPlotItem().autoRange()

    def _gather_axis_state(self, plot_widget: pg.PlotWidget) -> dict:
        """Liest den aktuellen Achsen-Zustand eines Kurven-Graphen aus --
        gemeinsam genutzt von _open_axis_settings() (Live-Ansicht) und den
        Export-Dialogen (GraphicExportDialog/VideoExportDialog,
        current_axis_state=..., siehe _temporary_axis_override fuer die
        Anwendung waehrend des Exports)."""
        plot_item = plot_widget.getPlotItem()
        vb = plot_item.getViewBox()
        (x0, x1), (y0, y1) = vb.viewRange()
        t0 = (
            self.recording.unix_seconds()[0]
            if self.recording is not None and self.recording.n_frames
            else 0.0
        )
        x_auto, y_auto = vb.autoRangeEnabled()
        x_axis_item = plot_item.getAxis("bottom")
        y_axis_item = plot_item.getAxis("left")
        # _tickSpacing ist ein privates pyqtgraph-Attribut (keine oeffentliche
        # Abfragemethode vorhanden) -- getattr(..., None) faengt ab, falls
        # sich das in einer kuenftigen pyqtgraph-Version aendert/entfaellt.
        tick_spacing = getattr(y_axis_item, "_tickSpacing", None)
        current_y_spacing = tick_spacing[0][0] if tick_spacing else None
        return {
            "x_min": x0 - t0, "x_max": x1 - t0, "x_auto": x_auto,
            "x_runtime_mode": x_axis_item.runtime_mode, "x_spacing": x_axis_item.manual_spacing,
            "y_min": y0, "y_max": y1, "y_auto": y_auto, "y_spacing": current_y_spacing,
        }

    @contextlib.contextmanager
    def _temporary_axis_override(self, plot_widget: pg.PlotWidget, overrides: dict | None):
        """Wendet -- falls overrides gesetzt ist (siehe GraphicExportDialog/
        VideoExportDialog.custom_axis_overrides()) -- eigene Achsen-
        Einstellungen NUR fuer die Dauer des Renderns auf plot_widget an und
        stellt danach exakt den vorherigen Zustand wieder her; die Live-
        Ansicht im Hauptfenster bleibt dabei unangetastet (Nutzerwunsch:
        "mehr Gestaltungsmoeglichkeiten beim Exportieren ... Achsen-Labels").
        overrides is None -> kein Eingriff (die aktuelle Ansicht wird 1:1
        exportiert, siehe _temporary_graph_content/_rebased_time_axis fuer
        den dazugehoerigen "Achsen stimmen nicht ueberein"-Bugfix)."""
        if overrides is None:
            yield
            return
        plot_item = plot_widget.getPlotItem()
        vb = plot_item.getViewBox()
        x_axis_item = plot_item.getAxis("bottom")
        y_axis_item = plot_item.getAxis("left")
        t0 = (
            self.recording.unix_seconds()[0]
            if self.recording is not None and self.recording.n_frames
            else 0.0
        )

        x_auto, y_auto = vb.autoRangeEnabled()
        old_x_range = vb.viewRange()[0]
        old_y_range = vb.viewRange()[1]
        old_x_spacing = x_axis_item.manual_spacing
        old_y_tick_spacing = getattr(y_axis_item, "_tickSpacing", None)

        if overrides["x_manual"]:
            xmin, xmax = overrides["x_range"]
            vb.setXRange(t0 + xmin, t0 + xmax, padding=0)
        x_axis_item.set_manual_spacing(overrides["x_spacing"] if overrides["x_spacing_manual"] else None)
        if overrides["y_manual_range"]:
            ymin, ymax = overrides["y_range"]
            vb.setYRange(ymin, ymax, padding=0)
        if overrides["y_spacing_manual"]:
            spacing = overrides["y_spacing"]
            y_axis_item.setTickSpacing(major=spacing, minor=spacing / 5)
        else:
            y_axis_item.setTickSpacing()

        try:
            yield
        finally:
            if x_auto:
                vb.enableAutoRange(x=True)
            else:
                vb.setXRange(old_x_range[0], old_x_range[1], padding=0)
            x_axis_item.set_manual_spacing(old_x_spacing)
            if y_auto:
                vb.enableAutoRange(y=True)
            else:
                vb.setYRange(old_y_range[0], old_y_range[1], padding=0)
            if old_y_tick_spacing:
                spacing = old_y_tick_spacing[0][0]
                y_axis_item.setTickSpacing(major=spacing, minor=spacing / 5)
            else:
                y_axis_item.setTickSpacing()

    def _open_axis_settings(self, plot_widget: pg.PlotWidget) -> None:
        """Oeffnet den "Achsen einstellen…"-Dialog fuer GENAU diesen Graphen
        (Nutzerwunsch: Schrittweite/Wertebereich frei waehlbar statt nur
        ueber das pyqtgraph-eigene, schwer auffindbare Rechtsklick-Menü)."""
        current = self._gather_axis_state(plot_widget)
        plot_item = plot_widget.getPlotItem()
        vb = plot_item.getViewBox()
        x_axis_item = plot_item.getAxis("bottom")
        y_axis_item = plot_item.getAxis("left")
        t0 = (
            self.recording.unix_seconds()[0]
            if self.recording is not None and self.recording.n_frames
            else 0.0
        )

        dialog = AxisSettingsDialog(
            self,
            current_x_min=current["x_min"], current_x_max=current["x_max"],
            current_y_min=current["y_min"], current_y_max=current["y_max"],
            x_manual=not current["x_auto"], y_manual_range=not current["y_auto"],
            y_spacing=current["y_spacing"],
            x_runtime_mode=current["x_runtime_mode"], x_spacing=current["x_spacing"],
        )
        if dialog.exec() != QtWidgets.QDialog.DialogCode.Accepted:
            return

        if dialog.x_manual():
            xmin, xmax = dialog.x_range()
            plot_item.setXRange(t0 + xmin, t0 + xmax, padding=0)
        else:
            plot_item.enableAutoRange(x=True)

        x_axis_item.set_manual_spacing(dialog.x_spacing() if dialog.x_manual_spacing() else None)

        if dialog.y_manual_range():
            ymin, ymax = dialog.y_range()
            plot_item.setYRange(ymin, ymax, padding=0)
        else:
            plot_item.enableAutoRange(y=True)

        if dialog.y_manual_spacing():
            spacing = dialog.y_spacing()
            y_axis_item.setTickSpacing(major=spacing, minor=spacing / 5)
        else:
            y_axis_item.setTickSpacing()

        self.statusBar().showMessage("Achsen-Einstellungen übernommen.")

    def _build_plots(self) -> None:
        self.axis_timeseries_bottom = TimeAxisItem()
        # Obere Zweit-Achse, standardmaessig ausgeblendet -- wird nur
        # waehrend eines Grafik-Exports mit Zeitachse "Beide" kurzzeitig
        # eingeblendet, um Uhrzeit UND Laufzeit gleichzeitig zu zeigen
        # (siehe _dual_time_axis_export). Bereits bei der Konstruktion des
        # PlotWidget uebergeben, da pyqtgraph eine spaeter per
        # setAxisItems() ersetzte Achse nicht zuverlaessig ins Layout
        # integriert.
        self.axis_timeseries_top = TimeAxisItem(orientation="top")
        self.timeseries_plot = pg.PlotWidget(
            axisItems={"bottom": self.axis_timeseries_bottom, "top": self.axis_timeseries_top}
        )
        self.timeseries_plot.getPlotItem().showAxis("top", False)
        self.timeseries_plot.setLabel("left", "Temperatur", units="°C")
        self.timeseries_plot.showGrid(x=True, y=True, alpha=0.3)
        self.timeseries_legend = self.timeseries_plot.addLegend(offset=(10, 10))
        self.frame_marker = pg.InfiniteLine(
            angle=90, movable=False, pen=pg.mkPen("#888888", width=1, style=QtCore.Qt.DashLine)
        )
        self.timeseries_plot.addItem(self.frame_marker)

        # Zusaetzliche, standardmaessig ausgeblendete Kurve fuer den
        # Live-Cursor-Verlauf DIREKT im Zeitverlauf-Graphen (Punkt 8) -- eine
        # eigene PlotDataItem-Instanz, da ein und dasselbe pyqtgraph-Item
        # nicht gleichzeitig auf zwei Plots liegen kann; ihre Daten werden
        # in _update_live_cursor() parallel zu self.live_curve gepflegt.
        self.timeseries_live_curve = pg.PlotDataItem(
            pen=pg.mkPen("#38bdf8", width=2),
            symbol="o", symbolSize=5, symbolBrush="#38bdf8", symbolPen=None,
        )
        self.timeseries_live_curve.setVisible(False)
        self.timeseries_plot.addItem(self.timeseries_live_curve)

        # Export-Buttons hier bewusst entfernt (siehe Menü „Export“ und das
        # native Rechtsklick-Kontextmenü auf dem Graphen selbst) -- doppelte,
        # unklar benannte Buttons ("Grafik speichern…"/"Werte exportieren…",
        # ohne erkennbaren Bezug zum jeweiligen Graphen) sorgten für Verwirrung.
        self.timeseries_widget = QtWidgets.QWidget()
        timeseries_layout = QtWidgets.QVBoxLayout(self.timeseries_widget)
        timeseries_layout.setContentsMargins(4, 4, 4, 4)
        timeseries_layout.addWidget(self.timeseries_plot)

        # Punkt 8: zusaetzlich (opt-in), den Live-Cursor-Verlauf direkt mit in
        # diesen Graphen einzublenden, statt extra ins Live-Panel wechseln zu
        # muessen.
        self.chk_show_live_in_timeseries = QtWidgets.QCheckBox("Live-Cursor-Kurve zusätzlich anzeigen")
        self.chk_show_live_in_timeseries.setToolTip(
            "Blendet den Temperaturverlauf des Live-Cursor-Pixels zusätzlich zu den "
            "Messbereichen in diesem Graphen ein (dieselbe Kurve wie im Live-Panel)."
        )
        self.chk_show_live_in_timeseries.toggled.connect(self._on_show_live_in_timeseries_toggled)
        timeseries_layout.addWidget(self.chk_show_live_in_timeseries)

        ts_time_row, self.combo_time_display_timeseries, self.combo_runtime_unit_timeseries = (
            self._build_time_display_row(self.timeseries_plot)
        )
        timeseries_layout.addLayout(ts_time_row)

        self.axis_live_bottom = TimeAxisItem()
        self.axis_live_top = TimeAxisItem(orientation="top")
        self.live_plot = pg.PlotWidget(
            axisItems={"bottom": self.axis_live_bottom, "top": self.axis_live_top}
        )
        self.live_plot.getPlotItem().showAxis("top", False)
        self.live_plot.setLabel("left", "Temperatur", units="°C")
        self.live_plot.showGrid(x=True, y=True, alpha=0.3)
        self.live_curve = self.live_plot.plot(
            pen=pg.mkPen("#38bdf8", width=2),
            symbol="o",
            symbolSize=5,
            symbolBrush="#38bdf8",
            symbolPen=None,
        )
        self.live_frame_marker = pg.InfiniteLine(
            angle=90, movable=False, pen=pg.mkPen("#888888", width=1, style=QtCore.Qt.DashLine)
        )
        self.live_plot.addItem(self.live_frame_marker)

        self.live_label = QtWidgets.QLabel(
            "Maus über das Bild bewegen, um den Temperaturverlauf am Cursor-Pixel live zu sehen. "
            "Linksklick fixiert die Stelle, Rechtsklick löst die Fixierung wieder."
        )
        self.live_label.setWordWrap(True)

        self.live_widget = QtWidgets.QWidget()
        live_layout = QtWidgets.QVBoxLayout(self.live_widget)
        live_layout.setContentsMargins(4, 4, 4, 4)
        live_layout.addWidget(self.live_label)
        live_layout.addWidget(self.live_plot)
        live_time_row, self.combo_time_display_live, self.combo_runtime_unit_live = (
            self._build_time_display_row(self.live_plot)
        )
        live_layout.addLayout(live_time_row)

        self._time_display_combos = [self.combo_time_display_timeseries, self.combo_time_display_live]
        for combo in self._time_display_combos:
            combo.currentIndexChanged.connect(self._on_time_display_changed)
        self._runtime_unit_combos = [self.combo_runtime_unit_timeseries, self.combo_runtime_unit_live]
        for combo in self._runtime_unit_combos:
            combo.currentIndexChanged.connect(self._on_runtime_unit_changed)

        self._trim_plot_context_menu(self.timeseries_plot)
        self._trim_plot_context_menu(self.live_plot)

    def _build_roi_entries(self) -> None:
        # Startbestand: die urspruengliche feste Anzahl (Farbpalettengroesse).
        # Weitere Messbereiche lassen sich danach jederzeit per "+"-Knopf im
        # ROI-Tab-Leiste hinzufuegen (siehe _add_roi_entry/_on_add_roi_clicked).
        for _ in range(len(ROI_COLORS)):
            self._add_roi_entry(build_row=False)

    def _add_roi_entry(self, build_row: bool = True) -> RoiEntry:
        """Legt einen neuen, leeren Messbereich an (Kurve, ROI-Rechteck,
        Bild-Beschriftung) und haengt ihn an self.roi_entries an. Mit
        build_row=True (Standard: beim Hinzufuegen zur Laufzeit) wird
        zusaetzlich sein Panel-Eintrag gebaut und ausgewaehlt -- beim
        initialen Aufbau der ersten 5 ROIs (build_row=False) existiert
        self.roi_list/roi_stack zu diesem Zeitpunkt noch nicht, das erledigt
        dort _build_control_panel."""
        number = self._roi_next_number
        self._roi_next_number += 1
        color = roi_color_for_number(number)
        curve = self.timeseries_plot.plot(
            pen=pg.mkPen(color, width=2),
            symbol="o",
            symbolSize=5,
            symbolBrush=color,
            symbolPen=None,
            name=default_roi_name(number),
        )
        entry = RoiEntry(number, color, self.view_box, curve)
        entry.roi.sigRegionChanged.connect(partial(self._on_roi_region_changed, entry))
        entry.roi.sigRegionChangeFinished.connect(partial(self._on_roi_region_finished, entry))
        # Ein (reiner, nicht ziehender) Klick auf den Messbereich im Bild
        # waehlt ihn auch im rechten Panel aus -- sigClicked feuert nur bei
        # einem echten Klick, nicht waehrend eines Ziehvorgangs zum
        # Verschieben/Skalieren.
        entry.roi.sigClicked.connect(partial(self._on_roi_clicked_in_image, entry))
        self.roi_entries.append(entry)
        if build_row:
            # Erst die Zeile (inkl. Spinboxen) bauen, DANACH ggf. deren
            # Wertebereiche an die laufende Aufnahme anpassen -- umgekehrt
            # gaebe es die Spinboxen noch gar nicht.
            self._add_roi_tab_page(entry)
            self._select_roi(entry)
        if self.recording is not None and entry.spin_x is not None:
            self._configure_roi_entry_for_recording(entry)
        return entry

    def _configure_roi_entry_for_recording(self, entry: RoiEntry) -> None:
        """Uebertraegt die aus der aktuellen Aufnahme abgeleiteten Grenzen/
        Darstellungsoptionen (Spinbox-Wertebereiche, Punktmarker-Sichtbarkeit)
        auf ein einzelnes ROI -- gemeinsam genutzt von _set_recording() (alle
        vorhandenen ROIs) und _add_roi_entry() (ein waehrend einer bereits
        laufenden Aufnahme neu hinzugefuegtes ROI)."""
        rows, cols = self.recording.shape
        self._set_roi_geometry_ranges(entry, cols, rows)
        entry.curve.setSymbol("o" if self.recording.n_frames <= MAX_FRAMES_WITH_SYMBOLS else None)
        # Start/Ende-Zielbild der Interpolation: ein waehrend einer laufenden
        # Aufnahme neu hinzugefuegtes ROI hatte diese sonst dauerhaft auf
        # (1, 1) geklemmt (Konstruktions-Default), weil nur _set_recording()/
        # _apply_appended_recording() die Wertebereiche sonst anpassen --
        # Standard hier wie bei einer frisch geladenen Aufnahme: erstes/
        # letztes Bild.
        n = self.recording.n_frames
        entry.spin_interp_start_frame.setRange(1, max(1, n))
        entry.spin_interp_start_frame.setValue(1)
        entry.spin_interp_end_frame.setRange(1, max(1, n))
        entry.spin_interp_end_frame.setValue(max(1, n))

    @staticmethod
    def _set_roi_geometry_ranges(entry: RoiEntry, cols: int, rows: int) -> None:
        """Setzt die Wertebereiche von X-/Y-Position und Breite/Höhe passend
        zur Bildgröße -- mit blockSignals: ein Bereichs-SCHRUMPFEN kann den
        aktuellen Wert stillschweigend klemmen (z.B. Standardhöhe 30 bei
        einem nur 24 Pixel hohen Bild), was OHNE Blockade denselben
        valueChanged-Handler wie eine echte Nutzereingabe ausgelöst hätte
        (siehe spin.valueChanged -> _on_roi_apply_clicked) und ein noch gar
        nicht platziertes ROI ungewollt "platziert" hätte."""
        for spin, lo, hi in (
            (entry.spin_x, 0, cols),
            (entry.spin_y, 0, rows),
            (entry.spin_width, 1, max(1, cols)),
            (entry.spin_height, 1, max(1, rows)),
        ):
            spin.blockSignals(True)
            spin.setRange(lo, hi)
            spin.blockSignals(False)

    def _build_control_panel(self) -> None:
        panel = QtWidgets.QWidget()
        layout = QtWidgets.QVBoxLayout(panel)
        layout.setAlignment(QtCore.Qt.AlignTop)

        # -- Legende / Colormap ---------------------------------------
        legend_box = QtWidgets.QGroupBox("Legende")
        legend_layout = QtWidgets.QGridLayout(legend_box)

        legend_layout.addWidget(QtWidgets.QLabel("Farbverlauf:"), 0, 0)
        self.combo_cmap = QtWidgets.QComboBox()
        for label, _name in COLORMAPS:
            self.combo_cmap.addItem(label)
        self.combo_cmap.currentIndexChanged.connect(self._on_colormap_changed)
        legend_layout.addWidget(self.combo_cmap, 0, 1, 1, 2)

        self.chk_cmap_invert = QtWidgets.QCheckBox("Invertieren")
        self.chk_cmap_invert.setToolTip("Kehrt den Farbverlauf der Legende um (kalt/warm vertauscht).")
        self.chk_cmap_invert.toggled.connect(self._on_colormap_invert_toggled)
        # Eingerueckt auf Spalte 1 (Hoehe der Farbverlauf-Combobox), damit
        # optisch klar ist, dass diese Option zum Farbverlauf darueber gehoert.
        legend_layout.addWidget(self.chk_cmap_invert, 1, 1, 1, 2)

        # Skalierungs-Modus zweistufig: eine AEUSSERE, garantiert exklusive
        # Wahl "Automatisch" vs. "Manuell" (level_mode_group) -- nie beide
        # gleichzeitig an oder aus -- und darunter/daneben eine INNERE Wahl
        # fuer die Automatik-Variante (level_auto_submode_group), die nur
        # relevant/bedienbar ist, solange "Automatisch" aktiv ist.
        self.radio_level_auto = QtWidgets.QRadioButton("Automatisch:")
        self.radio_level_auto.setToolTip(
            "Skalierung automatisch aus den Bilddaten ermitteln (siehe Auswahl rechts daneben)."
        )
        self.radio_level_manual = QtWidgets.QRadioButton("Manuell:")
        self.radio_level_manual.setToolTip(
            "Feste, selbst gewählte Grenzwerte (Felder \"Min\"/\"Max\" rechts) statt automatischer Skalierung."
        )
        self.level_mode_group = QtWidgets.QButtonGroup(self)
        self.level_mode_group.addButton(self.radio_level_auto)
        self.level_mode_group.addButton(self.radio_level_manual)
        self.radio_level_auto.setChecked(True)
        self.level_mode_group.buttonToggled.connect(self._on_level_mode_changed)

        self.radio_level_per_frame = QtWidgets.QRadioButton("Pro Bild")
        self.radio_level_per_frame.setToolTip(
            "Minimum/Maximum werden für jedes angezeigte Bild neu berechnet (Standard)."
        )
        self.radio_level_global = QtWidgets.QRadioButton("Über gesamte Messung")
        self.radio_level_global.setToolTip(
            "Ermittelt Minimum/Maximum einmalig über alle geladenen Frames und verwendet "
            "diesen Bereich durchgehend für die Legende (statt pro Bild neu zu skalieren)."
        )
        self.level_auto_submode_group = QtWidgets.QButtonGroup(self)
        self.level_auto_submode_group.addButton(self.radio_level_per_frame)
        self.level_auto_submode_group.addButton(self.radio_level_global)
        # Standard: "Über gesamte Messung" -- eine durchgehend gleichbleibende
        # Skalierung ist beim Betrachten/Vergleichen einzelner Frames einer
        # Messreihe meist hilfreicher als eine pro Bild neu springende.
        self.radio_level_global.setChecked(True)
        self.level_auto_submode_group.buttonToggled.connect(self._on_level_mode_changed)

        # Block-weise UNTEREINANDER statt nebeneinander: Automatisch-Block
        # (Pro Bild ueber Gesamte Serie) und Manuell-Block (Max ueber Min,
        # bewusst in dieser Reihenfolge) jeweils zweizeilig in derselben
        # (zweiten) Spalte -- dadurch fluchten Pro Bild und Max in dieser
        # Spalte, auch wenn sie inhaltlich zu verschiedenen Gruppen gehoeren.
        legend_layout.addWidget(self.radio_level_auto, 2, 0)
        legend_layout.addWidget(self.radio_level_per_frame, 2, 1)
        legend_layout.addWidget(self.radio_level_global, 3, 1)

        legend_layout.addWidget(self.radio_level_manual, 4, 0)
        legend_layout.addWidget(QtWidgets.QLabel("Max:"), 4, 1)
        self.spin_level_max = LocaleTolerantDoubleSpinBox()
        self.spin_level_max.setRange(-100.0, 2000.0)
        self.spin_level_max.setDecimals(1)
        self.spin_level_max.setValue(50.0)
        self.spin_level_max.setSuffix(" °C")
        self.spin_level_max.setEnabled(False)
        self.spin_level_max.valueChanged.connect(self._on_level_spin_changed)
        legend_layout.addWidget(self.spin_level_max, 4, 2)

        legend_layout.addWidget(QtWidgets.QLabel("Min:"), 5, 1)
        self.spin_level_min = LocaleTolerantDoubleSpinBox()
        self.spin_level_min.setRange(-100.0, 2000.0)
        self.spin_level_min.setDecimals(1)
        self.spin_level_min.setSuffix(" °C")
        self.spin_level_min.setEnabled(False)
        self.spin_level_min.valueChanged.connect(self._on_level_spin_changed)
        legend_layout.addWidget(self.spin_level_min, 5, 2)

        self.histogram.sigLevelsChanged.connect(self._on_histogram_levels_changed)

        # Grafik-Darstellung (Hintergrund/Schrift der Grafiken, unabhaengig
        # vom App-Design) sitzt jetzt als Untermenue in "Ansicht" (siehe
        # _build_menu), passend neben "Ansicht > Design" statt hier im ROI-/
        # Legende-Panel, wo es thematisch nicht hingehoerte.

        # -- Maßstab (Lineal, Punkt 12) --------------------------------------
        scale_box = QtWidgets.QGroupBox("Maßstab")
        scale_layout = QtWidgets.QVBoxLayout(scale_box)
        self.scale_label = QtWidgets.QLabel("Kein Maßstab definiert.")
        self.scale_label.setWordWrap(True)
        scale_layout.addWidget(self.scale_label)
        scale_buttons_row = QtWidgets.QHBoxLayout()
        btn_scale_set = QtWidgets.QPushButton("Festlegen…")
        btn_scale_set.setToolTip("Referenzlinie im Bild einzeichnen und ihre reale Länge in mm angeben.")
        btn_scale_set.clicked.connect(self._start_ruler_tool)
        scale_buttons_row.addWidget(btn_scale_set)
        self.btn_scale_clear = QtWidgets.QPushButton("Entfernen")
        self.btn_scale_clear.setToolTip("Entfernt den definierten Maßstab wieder (Größen werden nur noch in Pixeln angezeigt).")
        self.btn_scale_clear.setEnabled(False)
        self.btn_scale_clear.clicked.connect(self._clear_ruler_scale)
        scale_buttons_row.addWidget(self.btn_scale_clear)
        self.btn_ruler_color = QtWidgets.QPushButton()
        self.btn_ruler_color.setFixedSize(20, 20)
        self.btn_ruler_color.setCursor(QtCore.Qt.PointingHandCursor)
        self.btn_ruler_color.setToolTip(
            "Farbe der Referenzlinie ändern -- bei manchen Farbverläufen (z.B. \"Hot\") ist die "
            "Standardfarbe sonst kaum zu erkennen."
        )
        self.btn_ruler_color.clicked.connect(self._on_ruler_color_clicked)
        scale_buttons_row.addWidget(self.btn_ruler_color)
        self.btn_measure = QtWidgets.QPushButton("Messen…")
        self.btn_measure.setToolTip(
            "Strecke im Bild anklicken und mit dem oben definierten Maßstab in mm anzeigen -- "
            "ändert den Maßstab selbst NICHT. Erst verfügbar, wenn ein Maßstab festgelegt ist."
        )
        self.btn_measure.setEnabled(False)
        self.btn_measure.clicked.connect(self._start_measure_tool)
        scale_buttons_row.addWidget(self.btn_measure)
        scale_buttons_row.addStretch(1)
        scale_layout.addLayout(scale_buttons_row)
        self._update_ruler_color_swatch()

        # Legende und Maßstab NEBENEINANDER statt untereinander -- spart
        # vertikalen Platz im rechten Panel, ohne die Legende aus diesem
        # (bewusst an dieser Stelle belassenen) Bereich zu verschieben.
        top_row = QtWidgets.QHBoxLayout()
        top_row.addWidget(legend_box, 3)
        top_row.addWidget(scale_box, 2)
        layout.addLayout(top_row)

        # -- ROI-Auswahl als senkrechte Namensliste + Inhaltsflaeche (statt
        # fuenf untereinander gestapelter Boxen ODER eines QTabWidget mit
        # senkrechter Reiterleiste) -- letzteres dreht die Beschriftung dort
        # um 90° (kaum lesbar) und unterstuetzt in dieser Anordnung keinen
        # Eck-Knopf mehr (der "+ Messbereich"-Knopf verschwand dadurch
        # komplett). Eine normale QListWidget-Liste zeigt die Namen
        # waagerecht/normal lesbar, laesst sich per Doppelklick direkt
        # umbenennen (spart den Umweg ueber das Namensfeld in der Zeile) und
        # der "+"-Knopf ist ein ganz normaler Knopf ohne Sondermechanik.
        self.roi_list = QtWidgets.QListWidget()
        self.roi_list.setToolTip(
            "Messbereich auswählen (aktiviert direkt \"Messbereich setzen\") -- Doppelklick zum "
            "Umbenennen."
        )
        self.roi_stack = QtWidgets.QStackedWidget()
        for entry in self.roi_entries:
            self._add_roi_tab_page(entry)
        self.roi_list.currentRowChanged.connect(self._on_roi_list_row_changed)
        self.roi_list.itemChanged.connect(self._on_roi_list_item_changed)

        self.btn_add_roi = QtWidgets.QPushButton("+ Messbereich")
        btn_add_roi = self.btn_add_roi
        btn_add_roi.setToolTip("Weiteren Messbereich hinzufügen (beliebig viele möglich).")
        btn_add_roi.clicked.connect(self._on_add_roi_clicked)

        list_column = QtWidgets.QVBoxLayout()
        list_column.addWidget(btn_add_roi)
        list_column.addWidget(self.roi_list, 1)
        list_container = QtWidgets.QWidget()
        list_container.setLayout(list_column)

        roi_split = QtWidgets.QSplitter(QtCore.Qt.Horizontal)
        roi_split.addWidget(list_container)
        roi_split.addWidget(self.roi_stack)
        roi_split.setStretchFactor(0, 0)
        roi_split.setStretchFactor(1, 1)
        roi_split.setSizes([130, 400])

        layout.addWidget(roi_split, 1)
        if self.roi_list.count():
            self.roi_list.setCurrentRow(0)

        # Das GESAMTE Panel (Legende, Maßstab UND ROI-Auswahl) in einen
        # gemeinsamen Scrollbereich, damit bei knapper Dock-Hoehe alles
        # erreichbar bleibt (statt nur den Inhalt scrollen zu koennen).
        scroll = QtWidgets.QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(panel)
        self.control_panel = scroll

    def _add_roi_tab_page(self, entry: "RoiEntry") -> None:
        """Baut die Zeile eines Messbereichs, haengt sie an roi_stack an und
        legt den zugehoerigen Namens-Eintrag (inkl. Sichtbarkeits-Haekchen)
        in roi_list an."""
        row_widget = self._build_roi_row(entry)
        entry.tab_widget = row_widget
        self.roi_stack.addWidget(row_widget)

        item = QtWidgets.QListWidgetItem(entry.name)
        item.setFlags(item.flags() | QtCore.Qt.ItemIsEditable | QtCore.Qt.ItemIsUserCheckable)
        item.setCheckState(QtCore.Qt.CheckState.Checked)
        item.setToolTip("Doppelklick: umbenennen. Haekchen: sichtbar/ausgeblendet.")
        item.setData(QtCore.Qt.UserRole, entry)
        self.roi_list.addItem(item)
        entry.list_item = item

    def _select_roi(self, entry: "RoiEntry") -> None:
        """Waehlt den Listeneintrag eines Messbereichs aus -- loest darueber
        zuverlaessig denselben Wechsel-Ablauf aus wie ein Klick des Nutzers
        (Stack folgt, "Messbereich setzen" wird aktiviert), statt roi_stack
        direkt zu manipulieren."""
        if entry.list_item is not None:
            self.roi_list.setCurrentItem(entry.list_item)

    def _on_roi_clicked_in_image(self, entry: "RoiEntry", *_args) -> None:
        self._select_roi(entry)

    def _on_roi_list_row_changed(self, row: int) -> None:
        if row < 0:
            return
        item = self.roi_list.item(row)
        entry = item.data(QtCore.Qt.UserRole) if item is not None else None
        if entry is None:
            return
        self.roi_stack.setCurrentWidget(entry.tab_widget)
        # Auswahl aktiviert direkt "Messbereich setzen" fuer den neu
        # gewaehlten Messbereich, damit ein Klick ins Bild sofort platziert
        # werden kann, ohne extra den Knopf suchen zu muessen.
        if entry.btn_place is not None:
            entry.btn_place.setChecked(True)

    def _on_roi_list_item_changed(self, item: QtWidgets.QListWidgetItem) -> None:
        """Reagiert auf eine Aenderung eines Listeneintrags -- entweder der
        Name (Doppelklick-Bearbeitung) oder das Sichtbarkeits-Haekchen davor
        (ersetzt die vormalige separate "sichtbar"-Checkbox in der Zeile:
        direkt neben dem Namen ist auch ohne Beschriftung klar, was gemeint
        ist). Beide Aspekte werden hier zusammen (idempotent) angewendet,
        da itemChanged nicht mitteilt, welcher der beiden sich geaendert hat."""
        entry = item.data(QtCore.Qt.UserRole)
        if entry is None:
            return

        name = item.text().strip() or default_roi_name(entry.number)
        if item.text() != name:
            self.roi_list.blockSignals(True)
            item.setText(name)
            self.roi_list.blockSignals(False)
        if entry.name != name:
            entry.set_name(name)
            entry.tab_widget.setTitle(name)
            legend = self.timeseries_plot.getPlotItem().legend
            label = legend.getLabel(entry.curve) if legend is not None else None
            if label is not None:
                label.setText(name)

        visible = entry.is_visible_checked()
        entry.roi.setVisible(visible and entry.placed)
        entry.curve.setVisible(visible and entry.placed)
        entry.label.setVisible(visible and entry.placed)

    def _build_roi_row(self, entry: RoiEntry) -> QtWidgets.QGroupBox:
        # Box-Titel = ROI-Name (Umbenennen jetzt per Doppelklick in der
        # Liste links, siehe _on_roi_list_item_changed -- kein separates
        # Namensfeld mehr hier noetig).
        box = QtWidgets.QGroupBox(entry.name)
        outer = QtWidgets.QHBoxLayout(box)
        outer.setSpacing(6)
        grid = QtWidgets.QGridLayout()
        grid.setHorizontalSpacing(6)
        grid.setVerticalSpacing(4)
        outer.addLayout(grid, 1)
        # Laufender Zeilenzaehler statt hartkodierter Grid-Zeilennummern: eine
        # neue Zeile zwischendurch einzufuegen erfordert so nur einen
        # zusaetzlichen Block hier, statt an jeder folgenden addWidget/
        # addLayout-Stelle die Zeilennummer von Hand hochzuzaehlen (Quelle
        # sonst leicht uebersehener Ueberlappungen).
        row = 0

        # Farbe + "Messbereich setzen"/"Messbereich entfernen" alle in EINER
        # kompakten Zeile -- Farbe/setzen teilen sich eine Zelle per HBox.
        btn_color = QtWidgets.QPushButton()
        btn_color.setFixedSize(20, 20)
        btn_color.setCursor(QtCore.Qt.PointingHandCursor)
        btn_color.setToolTip("Farbe dieses Messbereichs ändern")
        btn_color.setStyleSheet(
            f"background-color:{entry.color}; border:1px solid #333; border-radius:4px;"
        )
        btn_color.clicked.connect(partial(self._on_roi_color_clicked, entry))
        entry.btn_color = btn_color

        btn_place = QtWidgets.QPushButton("Messbereich setzen")
        btn_place.setCheckable(True)
        btn_place.setToolTip("Aktivieren, dann im Bild\nklicken, um den Messbereich\ndort zu setzen.")
        btn_place.toggled.connect(partial(self._on_roi_place_toggled, entry))
        entry.btn_place = btn_place

        color_place_row = QtWidgets.QHBoxLayout()
        color_place_row.setSpacing(4)
        color_place_row.addWidget(btn_color)
        color_place_row.addWidget(btn_place, 1)
        grid.addLayout(color_place_row, row, 0, 1, 2)

        btn_remove = QtWidgets.QPushButton("Messbereich entfernen")
        btn_remove.setToolTip("Löscht diesen Messbereich\nunwiderruflich (Rechteck,\nBeschriftung, Kurve).")
        btn_remove.clicked.connect(partial(self._on_roi_remove_clicked, entry))
        grid.addWidget(btn_remove, row, 2, 1, 2)
        entry.btn_remove = btn_remove
        row += 1

        # Labels rechtsbuendig in ihrer Zelle, damit sie direkt an ihrem
        # Eingabefeld anliegen.
        grid.addWidget(
            QtWidgets.QLabel("X-Position:"), row, 0, alignment=QtCore.Qt.AlignRight | QtCore.Qt.AlignVCenter
        )
        spin_x = LocaleTolerantDoubleSpinBox()
        spin_x.setRange(0, 100000)
        spin_x.setDecimals(1)
        grid.addWidget(spin_x, row, 1)
        entry.spin_x = spin_x

        grid.addWidget(
            QtWidgets.QLabel("Y-Position:"), row, 2, alignment=QtCore.Qt.AlignRight | QtCore.Qt.AlignVCenter
        )
        spin_y = LocaleTolerantDoubleSpinBox()
        spin_y.setRange(0, 100000)
        spin_y.setDecimals(1)
        grid.addWidget(spin_y, row, 3)
        entry.spin_y = spin_y
        row += 1

        grid.addWidget(
            QtWidgets.QLabel("Breite:"), row, 0, alignment=QtCore.Qt.AlignRight | QtCore.Qt.AlignVCenter
        )
        spin_width = LocaleTolerantDoubleSpinBox()
        spin_width.setRange(1, 100000)
        spin_width.setValue(DEFAULT_ROI_SIZE)
        grid.addWidget(spin_width, row, 1)
        entry.spin_width = spin_width

        grid.addWidget(
            QtWidgets.QLabel("Höhe:"), row, 2, alignment=QtCore.Qt.AlignRight | QtCore.Qt.AlignVCenter
        )
        spin_height = LocaleTolerantDoubleSpinBox()
        spin_height.setRange(1, 100000)
        spin_height.setValue(DEFAULT_ROI_SIZE)
        grid.addWidget(spin_height, row, 3)
        entry.spin_height = spin_height

        # Jede Aenderung eines der vier Felder (Tippen, Pfeiltasten, Scrollrad)
        # wendet Position/Groesse sofort live an -- kein separater
        # "Übernehmen"-Knopf mehr noetig.
        for spin in (spin_x, spin_y, spin_width, spin_height):
            spin.valueChanged.connect(partial(self._on_roi_apply_clicked, entry))
        row += 1

        mm_label = QtWidgets.QLabel("")
        mm_label.setVisible(False)
        grid.addWidget(mm_label, row, 0, 1, 4)
        entry.mm_label = mm_label
        row += 1

        # -- Verlaufs-Interpolation (Punkt 3) ------------------------------
        chk_interp = QtWidgets.QCheckBox("Position/Größe über Zeit interpolieren (Start → Ende)")
        chk_interp.setToolTip("Messbereich wandert linear\nzwischen Start- und End-\nPosition/-Größe mit.")
        chk_interp.toggled.connect(partial(self._on_roi_interp_toggled, entry))
        grid.addWidget(chk_interp, row, 0, 1, 4)
        entry.chk_interp = chk_interp
        row += 1

        # Je eine eigene Zeile fuer Start/Ende (statt einer gemeinsamen
        # Reihe): "Erstes Frame:" - Eingabefeld - Knopf, darunter analog
        # "Letztes Frame:" -- Ziel-Frame frei waehlbar (Standard: erstes/
        # letztes Bild der Aufnahme, siehe _set_recording), der Knopf
        # springt zu genau diesem Frame und dient zugleich als Bestaetigung.
        grid.addWidget(
            QtWidgets.QLabel("Erstes Frame:"), row, 0, alignment=QtCore.Qt.AlignRight | QtCore.Qt.AlignVCenter
        )
        spin_interp_start_frame = QtWidgets.QSpinBox()
        spin_interp_start_frame.setRange(1, 1)
        spin_interp_start_frame.setToolTip("Bildnummer, die als Start-Zeitpunkt der Interpolation dient.")
        grid.addWidget(spin_interp_start_frame, row, 1)
        entry.spin_interp_start_frame = spin_interp_start_frame

        btn_interp_start = QtWidgets.QPushButton(INTERP_START_LABEL)
        btn_interp_start.setToolTip(
            f"Springt zum links eingestellten Bild, positionieren,\ndann „{INTERP_START_CAPTURE_LABEL}“ klicken."
        )
        btn_interp_start.clicked.connect(partial(self._on_roi_interp_capture, entry, True))
        btn_interp_start.setEnabled(False)
        grid.addWidget(btn_interp_start, row, 2, 1, 2)
        entry.btn_interp_start = btn_interp_start
        row += 1

        grid.addWidget(
            QtWidgets.QLabel("Letztes Frame:"), row, 0, alignment=QtCore.Qt.AlignRight | QtCore.Qt.AlignVCenter
        )
        spin_interp_end_frame = QtWidgets.QSpinBox()
        spin_interp_end_frame.setRange(1, 1)
        spin_interp_end_frame.setToolTip("Bildnummer, die als Ende-Zeitpunkt der Interpolation dient.")
        grid.addWidget(spin_interp_end_frame, row, 1)
        entry.spin_interp_end_frame = spin_interp_end_frame

        btn_interp_end = QtWidgets.QPushButton(INTERP_END_LABEL)
        btn_interp_end.setToolTip(
            f"Springt zum rechts eingestellten Bild, positionieren,\ndann „{INTERP_END_CAPTURE_LABEL}“ klicken."
        )
        btn_interp_end.clicked.connect(partial(self._on_roi_interp_capture, entry, False))
        btn_interp_end.setEnabled(False)
        grid.addWidget(btn_interp_end, row, 2, 1, 2)
        entry.btn_interp_end = btn_interp_end
        row += 1

        # Rechte Spalte, ueber die gesamte Zeilen-Hoehe: Anzeige-/
        # Auswertungsoptionen und "Quadrieren" kompakt untereinander.
        # "Übernehmen" entfaellt -- X-/Y-Position sowie Breite/Höhe wenden
        # sich jetzt bei JEDER Eingabefeld-Aenderung sofort selbst an (siehe
        # spin.valueChanged weiter oben), ein separater Knopf ist damit
        # ueberfluessig. "Zuruecksetzen" entfaellt ebenfalls (kaum genutzt,
        # kein klar erwartetes Verhalten).
        side_col = QtWidgets.QVBoxLayout()
        side_col.setSpacing(4)

        chk_show_temperature = QtWidgets.QCheckBox("Temperatur anzeigen")
        chk_show_temperature.setToolTip(
            "Zeigt die aktuelle Temperatur zusätzlich neben dem Namen im Bild an (Standard: an)."
        )
        chk_show_temperature.setChecked(True)
        chk_show_temperature.toggled.connect(partial(self._on_roi_show_temperature_toggled, entry))
        entry.chk_show_temperature = chk_show_temperature
        side_col.addWidget(chk_show_temperature)

        chk_circular = QtWidgets.QCheckBox("Kreis")
        chk_circular.setToolTip(
            "Zeichnet eine in Breite/Höhe eingeschriebene Ellipse statt eines Rechtecks und "
            "mittelt die Temperatur nur über die Pixel innerhalb dieser Fläche."
        )
        chk_circular.toggled.connect(partial(self._on_roi_circular_toggled, entry))
        entry.chk_circular = chk_circular
        side_col.addWidget(chk_circular)

        btn_square = QtWidgets.QPushButton("Quadrieren")
        btn_square.setToolTip("Höhe = Breite (Quadrat);\nMittelpunkt bleibt gleich.")
        btn_square.clicked.connect(partial(self._on_roi_square_reset_clicked, entry))
        side_col.addWidget(btn_square)

        side_col.addStretch(1)
        outer.addLayout(side_col)

        return box

    def _build_toolbar(self) -> None:
        toolbar = self.addToolBar("Steuerung")
        toolbar.setMovable(False)

        act_open_folder = toolbar.addAction("Ordner öffnen…")
        act_open_folder.triggered.connect(self._open_folder)

    def _build_docks(self) -> None:
        self.setDockOptions(
            QtWidgets.QMainWindow.AnimatedDocks
            | QtWidgets.QMainWindow.AllowNestedDocks
            | QtWidgets.QMainWindow.AllowTabbedDocks
        )

        # Nur links/rechts andockbar (wie unter Windows üblich) und über den
        # Schließen-Knopf ausblendbar ("minimieren") -- wieder einblendbar
        # über das Ansicht-Menü.
        side_areas = QtCore.Qt.LeftDockWidgetArea | QtCore.Qt.RightDockWidgetArea
        dock_features = (
            QtWidgets.QDockWidget.DockWidgetMovable
            | QtWidgets.QDockWidget.DockWidgetFloatable
            | QtWidgets.QDockWidget.DockWidgetClosable
        )

        self.control_dock = QtWidgets.QDockWidget("ROI && Legende", self)
        self.control_dock.setWidget(self.control_panel)
        self.control_dock.setAllowedAreas(side_areas)
        self.control_dock.setFeatures(dock_features)
        self.addDockWidget(QtCore.Qt.RightDockWidgetArea, self.control_dock)

        self.timeseries_dock = QtWidgets.QDockWidget("Zeitverlauf", self)
        self.timeseries_dock.setWidget(self.timeseries_widget)
        self.timeseries_dock.setAllowedAreas(side_areas)
        self.timeseries_dock.setFeatures(dock_features)
        self.addDockWidget(QtCore.Qt.RightDockWidgetArea, self.timeseries_dock)

        self.live_dock = QtWidgets.QDockWidget("Live (Cursor)", self)
        self.live_dock.setWidget(self.live_widget)
        self.live_dock.setAllowedAreas(side_areas)
        self.live_dock.setFeatures(dock_features)
        self.addDockWidget(QtCore.Qt.RightDockWidgetArea, self.live_dock)

        self.tabifyDockWidget(self.timeseries_dock, self.live_dock)
        self.timeseries_dock.raise_()
        # "Zeitverlauf"/"Live (Cursor)" explizit OBERHALB der Graphen statt
        # an einer je nach Qt-Stil/Plattform abweichenden Standardposition.
        self.setTabPosition(QtCore.Qt.RightDockWidgetArea, QtWidgets.QTabWidget.North)

        # control_dock und timeseries_dock/live_dock liegen in DERSELBEN
        # rechten Spalte (timeseries_dock/live_dock sind ausserdem
        # tabifiziert) -- sie haben also zwangslaeufig dieselbe Breite. Ein
        # resizeDocks(..., Horizontal) mit zwei WIDERSPRUECHLICHEN Breiten
        # fuer Docks derselben Spalte (frueherer Bug) fuehrte zu einer
        # unvorhersehbaren/"komischen" Anfangsbreite; hier genuegt EIN Wert
        # fuer die ganze Spalte. Bild-Spalte (links) und Docks (rechts) sollen
        # sich sonst zu gleichen Teilen (50:50) die Fensterbreite teilen.
        self.resizeDocks(
            [self.control_dock, self.timeseries_dock], [420, 500], QtCore.Qt.Vertical
        )
        self.resizeDocks([self.control_dock], [self.width() // 2], QtCore.Qt.Horizontal)

    def _build_menu(self) -> None:
        # Aktionen, die ohne geladene Messreihe ohnehin nur eine "Keine Daten"-
        # Meldung anzeigen wuerden, werden bis zum ersten Laden ausgegraut
        # (siehe _set_recording) -- klarer als ein Klick ins Leere.
        self._requires_recording_actions: list[QtGui.QAction] = []

        file_menu = self.menuBar().addMenu("&Datei")
        act_open_folder = file_menu.addAction("Ordner öffnen…")
        act_open_folder.triggered.connect(self._open_folder)
        act_import_tiff = file_menu.addAction("TIFF-Bilder importieren…")
        act_import_tiff.setToolTip(
            "Wandelt einzelne Graustufen-TIFF-Bilder (z.B. ein unkoloriertes „Intensität (DL)“-"
            "Rohbild ohne eingebettete Kalibrierung) in Messdateien im normalen Format um -- "
            "erfordert eine MANUELL angegebene Min-/Max-Temperatur (unkalibrierte Schätzung, "
            "Auswertung auf eigene Gefahr) sowie einen Bildausschnitt ohne Farbskala/Legende."
        )
        act_import_tiff.triggered.connect(self._import_tiff_images)
        file_menu.addSeparator()
        act_save_project = file_menu.addAction("Projekt speichern…")
        act_save_project.setToolTip(
            "Speichert Messbereiche (Position, Name, Farbe), Farbverlauf und Legenden-Limits "
            "in einer Projektdatei."
        )
        act_save_project.triggered.connect(self._save_project)
        act_load_project = file_menu.addAction("Projekt laden…")
        act_load_project.setToolTip(
            "Wendet eine gespeicherte Projektdatei an -- ist noch keine Messreihe geladen, wird "
            "deren gespeicherter Quellordner automatisch mitgeladen (falls noch vorhanden)."
        )
        act_load_project.triggered.connect(self._load_project)
        file_menu.addSeparator()
        act_quit = file_menu.addAction("Beenden")
        act_quit.triggered.connect(self.close)
        self._requires_recording_actions.append(act_save_project)

        # _StaysOpenMenu (statt einer per addMenu(str) erzeugten normalen
        # QMenu): dieses Menue enthaelt mehrere unabhaengige Checkboxen
        # (Panel-Sichtbarkeit, Dunkelmodus) -- bleibt nach jedem Ankreuzen
        # offen, statt sich wie ein Standard-QMenu sofort zu schliessen.
        view_menu = _StaysOpenMenu("&Ansicht", self)
        self.menuBar().addMenu(view_menu)
        view_menu.addAction(self.control_dock.toggleViewAction())
        view_menu.addAction(self.timeseries_dock.toggleViewAction())
        view_menu.addAction(self.live_dock.toggleViewAction())

        view_menu.addSeparator()
        # Ein einziger Umschalter statt getrennter "Design"/"Grafik-
        # Darstellung"-Untermenues (Nutzerwunsch: "nur noch einen einzigen
        # Knopf ... zwischen Dark-/Light-Mode hin und her wechseln") --
        # betrifft App-Oberflaeche UND Thermobild/Kurven-Graphen gemeinsam.
        self.act_dark_mode = view_menu.addAction("Dunkelmodus")
        self.act_dark_mode.setCheckable(True)
        self.act_dark_mode.setToolTip(
            "Wechselt zwischen hellem und dunklem Erscheinungsbild -- gilt einheitlich für die "
            "gesamte Oberfläche inkl. Thermobild und Kurven-Graphen."
        )
        self.act_dark_mode.toggled.connect(self._on_dark_mode_toggled)

        tools_menu = self.menuBar().addMenu("&Werkzeuge")
        act_import_settings = tools_menu.addAction("Datenimport anpassen…")
        act_import_settings.setToolTip(
            "Datenimport-Manager: bereitet Messdateien mit abweichendem Rohformat (z.B. "
            "zusätzliche Kopfzeilen, eine führende Index-Spalte, anderes Trennzeichen) fürs "
            "Einlesen vor -- mit Live-Vorschau gegen eine echte Beispieldatei. Nicht Teil des "
            "Namensschemas (Dateinamen, siehe Datei-Menü) -- betrifft nur den INHALT der Dateien."
        )
        act_import_settings.triggered.connect(self._configure_import_settings)
        tools_menu.addSeparator()
        act_ruler = tools_menu.addAction("Maßstab festlegen…")
        act_ruler.setToolTip(
            "Referenzlinie im Bild einzeichnen und ihre reale Länge in mm angeben, um Messbereich-"
            "Größen zusätzlich in mm anzuzeigen."
        )
        act_ruler.triggered.connect(self._start_ruler_tool)
        self._requires_recording_actions.append(act_ruler)

        self.act_measure = tools_menu.addAction("Länge messen…")
        self.act_measure.setToolTip(
            "Strecke im Bild anklicken und mit dem bereits festgelegten Maßstab in mm anzeigen -- "
            "ändert den Maßstab selbst NICHT. Erst verfügbar, wenn ein Maßstab festgelegt ist."
        )
        self.act_measure.triggered.connect(self._start_measure_tool)
        self.act_measure.setEnabled(False)

        kernel_menu = tools_menu.addMenu("Live-Cursor-Bereichsgröße")
        kernel_menu.setToolTip(
            "Legt fest, wie viele Pixel um den Live-Cursor (Maus im Thermobild) herum "
            "für den Live-Verlauf/die Live-Anzeige gemittelt werden."
        )
        self._live_cursor_kernel_actions: dict[int, QtGui.QAction] = {}
        kernel_group = QtGui.QActionGroup(self)
        kernel_group.setExclusive(True)
        # Ausschliesslich ungerade Kantenlaengen (echtes Mittelpunkt-Pixel,
        # keine geraden Groessen wie das frueher enthaltene 10x10 mehr).
        for size in (1, 3, 5, 7, 9, 11, 13, 15):
            if size == 1:
                label = "1×1 Pixel"
            elif size == 5:
                label = "5×5 Pixel (Mittelwert, Standard)"
            else:
                label = f"{size}×{size} Pixel (Mittelwert)"
            act = kernel_menu.addAction(label)
            act.setCheckable(True)
            act.triggered.connect(partial(self._on_live_cursor_kernel_selected, size))
            kernel_group.addAction(act)
            self._live_cursor_kernel_actions[size] = act
        self._live_cursor_kernel_actions[5].setChecked(True)

        export_menu = self.menuBar().addMenu("&Export")
        act_export_video = export_menu.addAction("Video / Bildstapel exportieren…")
        act_export_video.setToolTip(
            "Exportiert einen wählbaren Frame-Bereich als MP4-, AVI- oder WebM-Video, oder "
            "wahlweise als Bildstapel (eine Bilddatei pro Frame)."
        )
        act_export_video.triggered.connect(self._export_video)
        export_menu.addSeparator()
        # Nur noch EIN Grafik- und EIN CSV-Export-Fenster (statt getrennter
        # "Zeitverlauf-"/"Live-"-Varianten) -- welche Kurve(n) tatsaechlich mit
        # hinein sollen, waehlt der jeweilige Dialog selbst per Haekchen
        # (Nutzerwunsch: "nur noch ein einziges CSV/-Bild-Export Fenster").
        act_export_graphic = export_menu.addAction("Grafik exportieren…")
        act_export_graphic.setToolTip(
            "Speichert Thermobild (mit Position der Messbereiche/des Cursors) und "
            "Temperaturverlauf gemeinsam oder getrennt als Grafik(en) -- welche Kurve(n) "
            "(Messbereiche und/oder Live-Cursor) dabei sind, wählt der Dialog selbst."
        )
        act_export_graphic.triggered.connect(self._export_graphic)
        export_menu.addSeparator()
        act_export_csv = export_menu.addAction("Werte exportieren…")
        act_export_csv.setToolTip(
            "Speichert die Temperaturwerte aller platzierten Messbereiche und/oder des "
            "Live-Cursor-Pixels wählbar über die Zeit als CSV-, JSON- oder Text-Datei."
        )
        act_export_csv.triggered.connect(self._export_csv)
        self._requires_recording_actions.extend([
            act_export_video, act_export_graphic, act_export_csv,
        ])

        for action in self._requires_recording_actions:
            action.setEnabled(False)

    def _build_shortcuts(self) -> None:
        # Standardkontext (WindowShortcut) reicht: Qt bevorzugt bei fokussierten
        # Text-/Zahlenfeldern automatisch deren eigene Cursor-Navigation
        # (Pfeiltasten/Pos1/Ende) gegenueber diesen Shortcuts, d.h. Tippen in
        # ROI-Namen/Spinboxen wird dadurch nicht gestoert (empirisch geprueft).
        shortcut_specs = [
            (QtCore.Qt.Key_Right, lambda: self._step_frame(1)),
            (QtCore.Qt.Key_Left, lambda: self._step_frame(-1)),
            (QtCore.Qt.Key_PageUp, lambda: self._step_frame(10)),
            (QtCore.Qt.Key_PageDown, lambda: self._step_frame(-10)),
            (QtCore.Qt.Key_Home, self._jump_to_first_frame),
            (QtCore.Qt.Key_End, self._jump_to_last_frame),
            (QtCore.Qt.Key_Space, self._on_space_pressed),
        ]
        self._nav_shortcuts: list[QtGui.QShortcut] = []
        for key, slot in shortcut_specs:
            shortcut = QtGui.QShortcut(QtGui.QKeySequence(key), self)
            shortcut.activated.connect(slot)
            self._nav_shortcuts.append(shortcut)

    def _on_space_pressed(self) -> None:
        # Anders als bei Text-/Zahlenfeldern (siehe oben) uebernimmt Qt bei
        # fokussierten anwaehlbaren Buttons/Checkboxen NICHT automatisch
        # deren eigene Leertaste-Aktivierung -- ohne diesen Sonderfall wuerde
        # ein Druck auf Leertaste in einer fokussierten "sichtbar"-Checkbox
        # oder dem "Im Bild platzieren"-Knopf nur die Wiedergabe
        # starten/stoppen, statt (wie beim nativen Qt-Verhalten erwartet)
        # den fokussierten Button/die Checkbox umzuschalten.
        focus_widget = QtWidgets.QApplication.focusWidget()
        if isinstance(focus_widget, QtWidgets.QAbstractButton) and focus_widget.isCheckable():
            focus_widget.toggle()
            return
        self.play_button.toggle()

    # -------------------------------------------------------------- Design
    def _on_dark_mode_toggled(self, checked: bool) -> None:
        self._apply_theme("dark" if checked else "light")
        self._settings.setValue("theme", self._current_theme)

    def _apply_theme(self, key: str) -> None:
        theme = THEMES[key]
        self._current_theme = key

        app = QtWidgets.QApplication.instance()
        if app is not None:
            app.setStyle("Fusion")
            # Bugfix: "app.style().standardPalette()" fuer Hell klingt neutral,
            # liefert unter Windows aber die vom Betriebssystem-Design
            # abgeleitete Palette -- ist dort der Windows-eigene Dunkelmodus
            # aktiv, blieb das Fenster trotz Umschalten auf "Hell" faktisch
            # dunkel (Bugreport: "Warum ist das gesamte Fenster immernoch im
            # Darkmode?"). Beide Modi verwenden jetzt eine explizit fest
            # definierte Palette, unabhaengig vom OS-Design.
            app.setPalette(self._dark_palette() if key == "dark" else self._light_palette())
            # Widgets mit eigenem setStyleSheet (z.B. Zeitstempel-Anzeige) haben
            # in der Praxis nicht immer zuverlaessig die neue QApplication-
            # Palette uebernommen -- explizites Neu-Polieren erzwingt die
            # Aktualisierung (Bugreport: Zeitstempel blieb nach Dunkel->Hell-
            # Wechsel in grauer, auf hellem Hintergrund kaum lesbarer Schrift).
            for widget in app.allWidgets():
                widget.style().unpolish(widget)
                widget.style().polish(widget)
                widget.update()

        if hasattr(self, "act_dark_mode"):
            self.act_dark_mode.blockSignals(True)
            self.act_dark_mode.setChecked(key == "dark")
            self.act_dark_mode.blockSignals(False)

    def _apply_curve_colors(self, bg: str, fg: str) -> None:
        """Setzt Hintergrund-/Vordergrundfarbe der beiden Kurven-Graphen
        (Zeitverlauf, Live) -- bewusst FEST (immer hell, siehe self._graph_bg/
        self._graph_fg in __init__), NICHT mehr an das App-Design gekoppelt
        (Nutzerwunsch: "Hintergrund der Graphen auch im Dunkelmodus hell
        lassen, wissenschaftlicher Standard"). Getrennt von
        _apply_image_colors (Thermobild), da beide seit diesem Wunsch
        unabhaengige, unterschiedliche Farben haben."""
        self.timeseries_plot.setBackground(bg)
        self.live_plot.setBackground(bg)

        for plot_item in (self.timeseries_plot.getPlotItem(), self.live_plot.getPlotItem()):
            for axis_name in ("left", "bottom", "right", "top"):
                axis = plot_item.getAxis(axis_name)
                axis.setPen(fg)
                axis.setTextPen(fg)

        legend = self.timeseries_plot.getPlotItem().legend
        if legend is not None:
            legend.setLabelTextColor(fg)
            # Bugfix (pyqtgraph): LegendItem.setLabelTextColor() aktualisiert
            # nur legend.opts["labelTextColor"] -- fuer BEREITS vorhandene
            # Eintraege ruft es lediglich LabelItem.setAttr("color", ...) auf,
            # was nur das opts-dict des Labels aendert, aber (anders als
            # setText()) KEIN erneutes Rendern des schon erzeugten HTML
            # ausloest. Ein Messbereich, dessen Kurve VOR diesem Aufruf schon
            # in der Legende stand (z.B. die 5 Standard-Messbereiche beim
            # Programmstart), blieb dadurch dauerhaft bei der Farbe haengen,
            # die beim urspruenglichen Hinzufuegen galt (LabelItem faellt bei
            # color=None auf pyqtgraphs globalen Standard-Vordergrund zurueck
            # -- ein helles Grau, eigentlich fuer dunkle Hintergruende
            # gedacht) -- waehrend NEU hinzugefuegte Eintraege (z.B. "Live
            # (Cursor)", erst bei aktiviertem "Live-Cursor-Kurve zusaetzlich
            # anzeigen" hinzugefuegt) die zu diesem spaeteren Zeitpunkt schon
            # gesetzte echte Vordergrundfarbe direkt korrekt mitbekamen.
            # Bugreport: "Live-Cursor fett und schwarz, waehrend in der
            # Legende alle anderen Kurven ausgegraut sind" -- kein Fett-
            # Unterschied (Schriftgewicht war ueberall gleich), sondern
            # GENAU dieser Farb-Bug. Fix: jedes bestehende Label explizit
            # per setText() neu rendern lassen.
            for _sample, label in legend.items:
                label.setText(label.text, color=fg)

        self._graph_bg = bg
        self._graph_fg = fg

    def _apply_image_colors(self, bg: str, fg: str) -> None:
        """Setzt Hintergrund-/Vordergrundfarbe des Thermobild-Widgets --
        bewusst FEST (immer dunkel, siehe self._image_bg/self._image_fg in
        __init__), NICHT mehr an das App-Design gekoppelt (Nutzerwunsch:
        "Hintergrund der Wärmebilder auch im hellen Modus dunkel lassen,
        besserer Kontrast zu Hotspots"). Getrennt von _apply_curve_colors,
        siehe dort."""
        self.glw.setBackground(bg)
        for axis_name in ("left", "bottom", "right", "top"):
            axis = self.plot_item.getAxis(axis_name)
            axis.setPen(fg)
            axis.setTextPen(fg)
        self.histogram.axis.setPen(fg)
        self.histogram.axis.setTextPen(fg)
        self._image_bg = bg
        self._image_fg = fg

    def _on_time_display_changed(self, _index: int) -> None:
        combo = self.sender()
        self._apply_time_display_mode(combo.currentData())

    def _apply_time_display_mode(self, mode: str) -> None:
        """Schaltet die x-Achsen-Beschriftung beider Kurven-Graphen zwischen
        echter Uhrzeit und relativer Laufzeit um -- beide Umschalter (je
        einer pro Graph) bleiben synchron, da beide Graphen dieselbe
        Zeitachse abbilden."""
        self._time_display_mode = mode
        t0 = self.recording.unix_seconds()[0] if self.recording is not None and self.recording.n_frames else 0.0
        runtime = mode == "runtime"
        self.axis_timeseries_bottom.set_runtime_mode(runtime, t0)
        self.axis_live_bottom.set_runtime_mode(runtime, t0)
        for combo in self._time_display_combos:
            combo.blockSignals(True)
            idx = combo.findData(mode)
            if idx >= 0:
                combo.setCurrentIndex(idx)
            combo.blockSignals(False)
        # Das Laufzeit-Format ist nur relevant, solange ueberhaupt "Laufzeit"
        # (statt "Uhrzeit") gezeigt wird.
        for combo in self._runtime_unit_combos:
            combo.setEnabled(runtime)
        self._settings.setValue("time_display_mode", mode)

    def _on_runtime_unit_changed(self, _index: int) -> None:
        combo = self.sender()
        self._apply_runtime_unit(combo.currentData())

    def _apply_runtime_unit(self, unit: str) -> None:
        """Setzt das Laufzeit-Format ("dritte Zeitachse", Nutzerwunsch) --
        "hhmmss" (Standard) oder eine fortlaufende Zahl in "s"/"min"/"h".
        Wirkt global: beide Graph-Achsen (auch waehrend eines Exports, da
        dieser dieselben TimeAxisItem-Instanzen wiederverwendet, siehe
        _temporary_time_display_mode/_dual_time_axis_export) UND
        _format_runtime() (Statuszeile, Video-/Bildstapel-Export-Overlay,
        CSV-Export) greifen auf denselben self._runtime_unit zurueck."""
        self._runtime_unit = unit
        # Auch die (normalerweise ausgeblendeten) OBEREN Zeitachsen mit
        # synchron halten -- sie werden nur waehrend eines Grafik-/Video-
        # Exports mit Zeitachse "Beide" kurz sichtbar (siehe
        # _dual_time_axis_export) und muessten sonst dort faelschlich immer
        # bei "hhmmss" (dem TimeAxisItem-Standardwert) bleiben, unabhaengig
        # vom hier gewaehlten Format.
        self.axis_timeseries_bottom.set_runtime_unit(unit)
        self.axis_live_bottom.set_runtime_unit(unit)
        self.axis_timeseries_top.set_runtime_unit(unit)
        self.axis_live_top.set_runtime_unit(unit)
        for combo in self._runtime_unit_combos:
            combo.blockSignals(True)
            idx = combo.findData(unit)
            if idx >= 0:
                combo.setCurrentIndex(idx)
            combo.blockSignals(False)
        self._settings.setValue("runtime_unit", unit)

    @staticmethod
    def _dark_palette() -> QtGui.QPalette:
        """Vollstaendige dunkle Palette fuer den Fusion-Stil.

        Bugfix: die vorherige Version setzte nur Window/Base/Text/Button
        & Co., aber NICHT Light/Midlight/Dark/Mid/Shadow/Link -- genau
        diese Rollen nutzt Fusion fuer 3D-Kanten/Schattierungen (Rahmen
        von GroupBox/Buttons, Schieberegler-Rille, Scrollbalken,
        deaktivierte Bedienelemente). Ohne sie blieben solche Elemente
        auf ihren urspruenglichen HELLEN Standardwerten haengen, wodurch
        der Dunkelmodus fleckig/unvollstaendig wirkte (Bugreport:
        "funktioniert noch nicht flaechendeckend/sauber")."""
        palette = QtGui.QPalette()
        window = QtGui.QColor("#2b2b2b")
        base = QtGui.QColor("#232323")
        alternate_base = QtGui.QColor("#2f2f2f")
        button = QtGui.QColor("#3a3a3a")
        text = QtGui.QColor("#e0e0e0")
        disabled_text = QtGui.QColor("#7a7a7a")
        highlight = QtGui.QColor("#3b82f6")
        link = QtGui.QColor("#60a5fa")

        palette.setColor(QtGui.QPalette.Window, window)
        palette.setColor(QtGui.QPalette.WindowText, text)
        palette.setColor(QtGui.QPalette.Base, base)
        palette.setColor(QtGui.QPalette.AlternateBase, alternate_base)
        palette.setColor(QtGui.QPalette.ToolTipBase, window)
        palette.setColor(QtGui.QPalette.ToolTipText, text)
        palette.setColor(QtGui.QPalette.Text, text)
        palette.setColor(QtGui.QPalette.Button, button)
        palette.setColor(QtGui.QPalette.ButtonText, text)
        palette.setColor(QtGui.QPalette.BrightText, QtGui.QColor("#ff5555"))
        palette.setColor(QtGui.QPalette.Highlight, highlight)
        palette.setColor(QtGui.QPalette.HighlightedText, QtGui.QColor("#ffffff"))
        # 3D-Schattierungen (Rahmen, Rillen, Trennlinien) -- ohne diese vier
        # bleiben GroupBox-Rahmen, Schieberegler-Rille etc. hell (siehe oben).
        palette.setColor(QtGui.QPalette.Light, QtGui.QColor("#4a4a4a"))
        palette.setColor(QtGui.QPalette.Midlight, QtGui.QColor("#3f3f3f"))
        palette.setColor(QtGui.QPalette.Dark, QtGui.QColor("#1a1a1a"))
        palette.setColor(QtGui.QPalette.Mid, QtGui.QColor("#2f2f2f"))
        palette.setColor(QtGui.QPalette.Shadow, QtGui.QColor("#0d0d0d"))
        palette.setColor(QtGui.QPalette.Link, link)
        palette.setColor(QtGui.QPalette.LinkVisited, link)
        if hasattr(QtGui.QPalette, "PlaceholderText"):
            palette.setColor(QtGui.QPalette.PlaceholderText, disabled_text)
        palette.setColor(QtGui.QPalette.Disabled, QtGui.QPalette.WindowText, disabled_text)
        palette.setColor(QtGui.QPalette.Disabled, QtGui.QPalette.Text, disabled_text)
        palette.setColor(QtGui.QPalette.Disabled, QtGui.QPalette.ButtonText, disabled_text)
        palette.setColor(QtGui.QPalette.Disabled, QtGui.QPalette.Base, window)
        palette.setColor(QtGui.QPalette.Disabled, QtGui.QPalette.Button, window)
        palette.setColor(QtGui.QPalette.Disabled, QtGui.QPalette.Highlight, QtGui.QColor("#454545"))
        palette.setColor(QtGui.QPalette.Disabled, QtGui.QPalette.HighlightedText, disabled_text)
        return palette

    @staticmethod
    def _light_palette() -> QtGui.QPalette:
        """Vollstaendige helle Palette fuer den Fusion-Stil, Gegenstueck zu
        _dark_palette() -- wird explizit gesetzt statt sich auf
        app.style().standardPalette() zu verlassen (siehe _apply_theme fuer
        den Grund: diese folgt unter Windows dem OS-Design)."""
        palette = QtGui.QPalette()
        window = QtGui.QColor("#efefef")
        base = QtGui.QColor("#ffffff")
        alternate_base = QtGui.QColor("#f5f5f5")
        button = QtGui.QColor("#efefef")
        text = QtGui.QColor("#000000")
        disabled_text = QtGui.QColor("#a0a0a0")
        highlight = QtGui.QColor("#3b82f6")
        link = QtGui.QColor("#2563eb")

        palette.setColor(QtGui.QPalette.Window, window)
        palette.setColor(QtGui.QPalette.WindowText, text)
        palette.setColor(QtGui.QPalette.Base, base)
        palette.setColor(QtGui.QPalette.AlternateBase, alternate_base)
        palette.setColor(QtGui.QPalette.ToolTipBase, QtGui.QColor("#ffffdc"))
        palette.setColor(QtGui.QPalette.ToolTipText, text)
        palette.setColor(QtGui.QPalette.Text, text)
        palette.setColor(QtGui.QPalette.Button, button)
        palette.setColor(QtGui.QPalette.ButtonText, text)
        palette.setColor(QtGui.QPalette.BrightText, QtGui.QColor("#cc0000"))
        palette.setColor(QtGui.QPalette.Highlight, highlight)
        palette.setColor(QtGui.QPalette.HighlightedText, QtGui.QColor("#ffffff"))
        palette.setColor(QtGui.QPalette.Light, QtGui.QColor("#ffffff"))
        palette.setColor(QtGui.QPalette.Midlight, QtGui.QColor("#e3e3e3"))
        palette.setColor(QtGui.QPalette.Dark, QtGui.QColor("#a0a0a0"))
        palette.setColor(QtGui.QPalette.Mid, QtGui.QColor("#b8b8b8"))
        palette.setColor(QtGui.QPalette.Shadow, QtGui.QColor("#767676"))
        palette.setColor(QtGui.QPalette.Link, link)
        palette.setColor(QtGui.QPalette.LinkVisited, link)
        if hasattr(QtGui.QPalette, "PlaceholderText"):
            palette.setColor(QtGui.QPalette.PlaceholderText, disabled_text)
        palette.setColor(QtGui.QPalette.Disabled, QtGui.QPalette.WindowText, disabled_text)
        palette.setColor(QtGui.QPalette.Disabled, QtGui.QPalette.Text, disabled_text)
        palette.setColor(QtGui.QPalette.Disabled, QtGui.QPalette.ButtonText, disabled_text)
        palette.setColor(QtGui.QPalette.Disabled, QtGui.QPalette.Base, window)
        palette.setColor(QtGui.QPalette.Disabled, QtGui.QPalette.Button, window)
        palette.setColor(QtGui.QPalette.Disabled, QtGui.QPalette.Highlight, QtGui.QColor("#d4d4d4"))
        palette.setColor(QtGui.QPalette.Disabled, QtGui.QPalette.HighlightedText, disabled_text)
        return palette

    def _connect_scene_events(self) -> None:
        self.glw.scene().sigMouseMoved.connect(self._on_scene_mouse_moved)
        self.glw.scene().sigMouseClicked.connect(self._on_scene_mouse_clicked)

    # ------------------------------------------------------------ Laden
    def _open_folder(self) -> None:
        folder = QtWidgets.QFileDialog.getExistingDirectory(self, "Ordner mit CSV-Messreihe wählen")
        if not folder:
            return
        self._load_folder(Path(folder))

    def _import_tiff_images(self) -> None:
        """Wandelt einzelne Graustufen-TIFF-Bilder (siehe TiffImportDialog
        und data.load_tiff_grayscale/tiff_crop_to_temperature für den vollen
        Hintergrund und die bewussten Einschränkungen -- nur echte
        Graustufenbilder, manuell angegebene Min-/Max-Temperatur, keinerlei
        automatische Kalibrierung) in Messdateien im normalen Format um.
        Die geschriebenen Dateien landen in einem selbst gewählten
        Zielordner und lassen sich danach ganz normal per "Ordner öffnen"
        laden (wird am Ende optional direkt angeboten)."""
        paths_str, _ = QtWidgets.QFileDialog.getOpenFileNames(
            self, "TIFF-Bilder auswählen", "", "TIFF-Bilder (*.tiff *.tif)"
        )
        if not paths_str:
            return
        paths = [Path(p) for p in paths_str]

        try:
            preview_gray = load_tiff_grayscale(paths[0])
        except RecordingError as exc:
            QtWidgets.QMessageBox.critical(self, "TIFF konnte nicht gelesen werden", str(exc))
            return

        import_dialog = TiffImportDialog(self, preview_gray, len(paths))
        if import_dialog.exec() != QtWidgets.QDialog.DialogCode.Accepted:
            return
        crop = import_dialog.crop_rect()
        t_min = import_dialog.min_temp()
        t_max = import_dialog.max_temp()

        dest = QtWidgets.QFileDialog.getExistingDirectory(
            self, "Zielordner für die umgerechneten Messdateien wählen"
        )
        if not dest:
            return
        dest_folder = Path(dest)

        progress = QtWidgets.QProgressDialog(
            "TIFF-Bilder werden umgerechnet…", "Abbrechen", 0, len(paths), self
        )
        progress.setWindowModality(QtCore.Qt.WindowModal)
        progress.setMinimumDuration(300)

        # Diese Kamera-Exporte tragen keinen im Dateinamen erkennbaren
        # echten Zeitstempel (siehe Analyse-Notizen) -- kuenstliche,
        # sekundengenau aufsteigende Zeitstempel (Reihenfolge = Datei-
        # Auswahlreihenfolge) reichen fuer DEFAULT_FILENAME_TEMPLATE und
        # ergeben eine sinnvoll abspielbare, aber rein kuenstliche Zeitachse.
        base_time = datetime.now().replace(microsecond=0)
        written = 0
        was_cancelled = False
        skipped: list[tuple[Path, str]] = []
        for n, path in enumerate(paths):
            if progress.wasCanceled():
                was_cancelled = True
                break
            progress.setValue(n)
            QtWidgets.QApplication.processEvents()
            try:
                gray = preview_gray if n == 0 else load_tiff_grayscale(path)
                if gray.shape != preview_gray.shape:
                    # Der Bildausschnitt (crop) wurde anhand der ERSTEN Datei
                    # gezogen -- eine andere Bildgroesse wuerde ihn an der
                    # falschen Stelle (oder ueber den Rand hinaus, was numpy
                    # beim Zuschneiden stillschweigend kappen wuerde) anwenden,
                    # statt eines klaren Fehlers. Lieber ueberspringen als
                    # eine unbemerkt falsch zugeschnittene Temperaturmatrix
                    # erzeugen.
                    raise RecordingError(
                        f"Bildgröße weicht von der Vorschau ab ({gray.shape[1]}×{gray.shape[0]} "
                        f"statt {preview_gray.shape[1]}×{preview_gray.shape[0]} px) -- der gewählte "
                        "Ausschnitt würde nicht passen."
                    )
                temp_array = tiff_crop_to_temperature(gray, crop, t_min, t_max)
            except RecordingError as exc:
                skipped.append((path, str(exc)))
                continue
            except Exception as exc:
                # Bewusst breit (siehe z.B. _export_single_graph): eine
                # einzelne kaputte/unerwartete Datei soll den kompletten
                # Stapel-Import nicht abbrechen, sondern nur diese eine Datei
                # uebersprungen werden -- wie beim normalen CSV-Laden
                # (_load_paths) auch.
                skipped.append((path, str(exc)))
                continue
            timestamp = base_time + timedelta(seconds=n)
            filename = render_filename_template(DEFAULT_FILENAME_TEMPLATE, timestamp) + ".csv"
            # Zeilenformat wie von der bestehenden Kamera-Software (siehe
            # data.ImportSettings-Standard: ';'-getrennt, Dezimalkomma, mit
            # abschliessendem ';'), damit die Dateien ohne jede
            # Datenimport-Anpassung normal ladbar sind.
            lines = [
                ";".join(f"{value:.2f}".replace(".", ",") for value in row) + ";"
                for row in temp_array
            ]
            try:
                (dest_folder / filename).write_text("\n".join(lines), encoding="utf-8-sig")
            except OSError as exc:
                skipped.append((path, f"Konnte nicht geschrieben werden: {exc}"))
                continue
            written += 1
        progress.setValue(len(paths))

        summary = f"{written} von {len(paths)} Datei(en) umgerechnet und in „{dest_folder}“ gespeichert."
        if was_cancelled:
            summary += " (Abgebrochen -- bereits geschriebene Dateien bleiben erhalten.)"
        if skipped:
            details = "\n".join(f"„{p.name}“: {reason}" for p, reason in skipped)
            QtWidgets.QMessageBox.warning(
                self, "TIFF-Import mit Warnungen", f"{summary}\n\nÜbersprungen:\n{details}"
            )
        else:
            self.statusBar().showMessage(summary, 6000)

        if written and QtWidgets.QMessageBox.question(
            self, "Ordner jetzt laden?", f"{summary}\n\nDiesen Ordner jetzt öffnen?",
            QtWidgets.QMessageBox.StandardButton.Yes | QtWidgets.QMessageBox.StandardButton.No,
            QtWidgets.QMessageBox.StandardButton.Yes,
        ) == QtWidgets.QMessageBox.StandardButton.Yes:
            self._load_folder(dest_folder)

    def _safe_folder_scan(self, folder: Path, scan_fn):
        """Fuehrt scan_fn() aus und faengt einen zwischen Auswahl und Scan
        unlesbar gewordenen Ordner (Netzlaufwerk getrennt, Ordner geloescht/
        umbenannt) einheitlich ab -- gemeinsam von _load_folder und
        _resolve_folder_and_pattern genutzt, statt denselben OSError-Dialog
        an zwei Stellen zu wiederholen. Gibt bei Erfolg das Ergebnis von
        scan_fn() zurueck (fuer beide Aufrufer stets eine Liste, ggf. leer),
        bei einem Lesefehler None."""
        try:
            return scan_fn()
        except OSError as exc:
            QtWidgets.QMessageBox.critical(
                self, "Ordner nicht lesbar", f"„{folder}“ konnte nicht gelesen werden:\n{exc}"
            )
            return None

    def _load_folder(self, folder: Path) -> bool:
        """Laedt eine komplette Messreihe aus folder (Namensschema-Abgleich,
        Live-Ueberwachung) -- gemeinsame Grundlage fuer "Ordner öffnen…" UND
        das automatische Nachladen des im Projekt gespeicherten Quellordners
        beim Laden eines Projekts ohne bereits geladene Messreihe (siehe
        _load_project). Gibt zurueck, ob das Laden erfolgreich war."""
        result = self._resolve_folder_and_pattern(folder)
        if result is None:
            return False
        folder_path, pattern, strptime_fmt = result
        paths = self._safe_folder_scan(folder_path, lambda: sorted(folder_path.glob("*.csv")))
        if paths is None:
            return False
        if not self._load_paths(paths, pattern=pattern, strptime_fmt=strptime_fmt):
            # Laden fehlgeschlagen (z.B. defekte CSVs) -- eine evtl. bereits
            # laufende Live-Ueberwachung eines ANDEREN Ordners darf dadurch
            # nicht auf diesen (nicht geladenen) Ordner umgehaengt werden.
            return False
        # Laeuft ab hier automatisch dauerhaft im Hintergrund weiter (kein
        # manuelles Ein-/Ausschalten mehr noetig) -- damit die App parallel
        # zu einer noch laufenden Messung genutzt werden kann, ohne dass
        # dafuer eine extra Einstellung gesetzt werden muss.
        self._watched_folder = folder_path
        self._live_watch_timer.start()
        return True

    def _resolve_folder_and_pattern(self, folder: Path) -> tuple[Path, re.Pattern, str] | None:
        """Stellt sicher, dass mindestens eine ".csv"-Datei in folder zum
        AKTIVEN Namensschema passt, bevor tatsaechlich geladen wird (Punkt 5:
        "Falls im Ausgabe-Ordner keine Dateien gefunden werden können, die
        dem bisherigen Namensschema entsprechen").

        Fragt bei fehlendem Treffer per Dialog nach: neuen Ordner waehlen
        (Schleife mit demselben Schema), Namensschema fuer DIESEN Ordner
        anpassen (siehe FilenameTemplateDialog; per Haekchen dort optional
        auch dauerhaft als neuer Standard speicherbar -- Standard: nur fuer
        diesen einen Ladevorgang), oder abbrechen. Gibt bei Abbruch None,
        sonst (Ordner, Pattern, Format) zurueck -- Pattern/Format koennen vom
        aktuellen Standard abweichen (siehe oben)."""
        pattern, strptime_fmt = self._filename_pattern, self._filename_strptime_fmt
        while True:
            matches = self._safe_folder_scan(folder, lambda: files_matching_template(folder, pattern))
            if matches is None:
                return None
            if matches:
                return folder, pattern, strptime_fmt

            choice = self._ask_filename_mismatch(folder)
            if choice == "cancel":
                return None
            if choice == "new_folder":
                new_folder = QtWidgets.QFileDialog.getExistingDirectory(
                    self, "Ordner mit CSV-Messreihe wählen"
                )
                if not new_folder:
                    return None
                folder = Path(new_folder)
                continue
            # choice == "template"
            dlg = FilenameTemplateDialog(self, folder, self._filename_template)
            if dlg.exec() != QtWidgets.QDialog.DialogCode.Accepted:
                continue
            new_template = dlg.template()
            if dlg.persist():
                self._set_filename_template(new_template)
                pattern, strptime_fmt = self._filename_pattern, self._filename_strptime_fmt
            else:
                pattern, strptime_fmt = compile_filename_template(new_template)
            # Naechster Schleifendurchlauf findet garantiert einen Treffer --
            # FilenameTemplateDialog laesst OK nur zu, wenn das Template
            # bereits mindestens eine Datei in GENAU diesem Ordner trifft.

    def _ask_filename_mismatch(self, folder: Path) -> str:
        """Rueckfrage, wenn keine .csv-Datei in folder zum aktiven
        Namensschema passt -- "new_folder"/"template"/"cancel"."""
        box = QtWidgets.QMessageBox(self)
        box.setIcon(QtWidgets.QMessageBox.Icon.Warning)
        box.setWindowTitle("Namensschema nicht erkannt")
        box.setText(
            f"Im Ordner „{folder}“ passt keine CSV-Datei zum erwarteten Namensschema "
            f"(„{self._filename_template}“). Wie möchtest du fortfahren?"
        )
        btn_new_folder = box.addButton("Neuen Ordner wählen", QtWidgets.QMessageBox.ButtonRole.ActionRole)
        btn_template = box.addButton("Namenstemplate anpassen…", QtWidgets.QMessageBox.ButtonRole.ActionRole)
        box.addButton("Abbrechen", QtWidgets.QMessageBox.ButtonRole.RejectRole)
        box.setDefaultButton(btn_template)
        box.exec()
        clicked = box.clickedButton()
        if clicked is btn_new_folder:
            return "new_folder"
        if clicked is btn_template:
            return "template"
        return "cancel"

    def _set_filename_template(self, template: str) -> None:
        self._filename_template = template
        self._filename_pattern, self._filename_strptime_fmt = compile_filename_template(template)
        self._settings.setValue("filename_template", template)

    def _load_import_settings(self) -> ImportSettings:
        """Liest das global gespeicherte Datenimport-Standardformat aus
        QSettings -- jeder Wert einzeln (statt als zusammengesetztes Objekt),
        analog zu den uebrigen Einstellungen dieser App, mit Fallback auf den
        jeweiligen ImportSettings-Standardwert, falls (z.B. bei einem noch
        nie zuvor gespeicherten Wert oder einem Formatwechsel) ein Schluessel
        fehlt oder ungueltig ist."""
        s = self._settings
        defaults = ImportSettings()
        return ImportSettings(
            delimiter=str(s.value("import/delimiter", defaults.delimiter)),
            decimal_separator=str(s.value("import/decimal_separator", defaults.decimal_separator)),
            encoding=str(s.value("import/encoding", defaults.encoding)),
            skip_header_lines=int(s.value("import/skip_header_lines", defaults.skip_header_lines, type=int)),
            skip_footer_lines=int(s.value("import/skip_footer_lines", defaults.skip_footer_lines, type=int)),
            skip_leading_columns=int(
                s.value("import/skip_leading_columns", defaults.skip_leading_columns, type=int)
            ),
            skip_trailing_columns=int(
                s.value("import/skip_trailing_columns", defaults.skip_trailing_columns, type=int)
            ),
        )

    def _set_import_settings(self, settings: ImportSettings, persist: bool) -> None:
        """Uebernimmt settings fuer die aktuelle Sitzung -- bei persist=True
        zusaetzlich dauerhaft in QSettings gespeichert (analog zu
        _set_filename_template/ImportSettingsDialog.persist(): die Checkbox
        im Dialog ist standardmaessig AUS, gilt also nur fuer den jeweils
        aktuellen Ladevorgang, es sei denn der Nutzer haekt sie bewusst an)."""
        self._import_settings = settings
        if persist:
            s = self._settings
            s.setValue("import/delimiter", settings.delimiter)
            s.setValue("import/decimal_separator", settings.decimal_separator)
            s.setValue("import/encoding", settings.encoding)
            s.setValue("import/skip_header_lines", settings.skip_header_lines)
            s.setValue("import/skip_footer_lines", settings.skip_footer_lines)
            s.setValue("import/skip_leading_columns", settings.skip_leading_columns)
            s.setValue("import/skip_trailing_columns", settings.skip_trailing_columns)

    def _configure_import_settings(self) -> None:
        """Menüpunkt "Werkzeuge > Datenimport anpassen…": laesst den
        Datenimport-Manager unabhaengig von einem (fehlgeschlagenen)
        Ladevorgang oeffnen, z.B. um sich vorab auf eine kuenftige, noch
        unbekannte Messreihen-Quelle mit abweichendem Rohformat
        vorzubereiten. Braucht eine Beispieldatei zur Live-Vorschau --
        nutzt die erste Datei der aktuell geladenen Aufnahme, falls
        vorhanden, sonst fragt eine Dateiauswahl danach."""
        sample_path: Path | None = None
        if self.recording is not None and self.recording.paths:
            sample_path = self.recording.paths[0]
        else:
            path, _filter = QtWidgets.QFileDialog.getOpenFileName(
                self, "Beispieldatei für den Datenimport wählen", "", "CSV-Dateien (*.csv);;Alle Dateien (*)"
            )
            if not path:
                return
            sample_path = Path(path)

        dlg = ImportSettingsDialog(self, sample_path, self._import_settings)
        if dlg.exec() != QtWidgets.QDialog.DialogCode.Accepted:
            return
        self._set_import_settings(dlg.settings(), persist=dlg.persist())

    def _offer_import_settings_retry(self, sample_path: Path, error_message: str) -> bool:
        """Rueckfrage, wenn ein Ladevorgang komplett fehlgeschlagen ist
        (RecordingError aus load_paths, siehe _load_paths) -- bietet an, den
        Datenimport-Manager auf einer der betroffenen Dateien zu oeffnen und
        das Laden mit angepassten Einstellungen erneut zu versuchen, statt
        nur eine Fehlermeldung anzuzeigen. Analog zu _ask_filename_mismatch."""
        box = QtWidgets.QMessageBox(self)
        box.setIcon(QtWidgets.QMessageBox.Icon.Warning)
        box.setWindowTitle("Daten konnten nicht gelesen werden")
        box.setText(
            f"Die ausgewählten Dateien konnten nicht als Messreihe gelesen werden:\n\n{error_message}\n\n"
            f"Möglicherweise weicht das Rohformat vom erwarteten Format ab (z.B. andere Kopfzeilen, "
            f"anderes Trennzeichen). Datenimport anpassen und erneut versuchen?"
        )
        btn_adjust = box.addButton("Datenimport anpassen…", QtWidgets.QMessageBox.ButtonRole.ActionRole)
        box.addButton("Abbrechen", QtWidgets.QMessageBox.ButtonRole.RejectRole)
        box.setDefaultButton(btn_adjust)
        box.exec()
        return box.clickedButton() is btn_adjust

    def _check_for_new_files(self) -> None:
        """Wird alle 10s vom Live-Watch-Timer aufgerufen (siehe __init__):
        laedt neu im ueberwachten Ordner abgelegte CSV-Dateien nach, ohne die
        aktuelle Wiedergabeposition zu stoeren -- damit die App parallel zu
        einer laufenden Messung genutzt werden kann, ohne bei sehr haeufig
        neu abgelegten Dateien (z.B. alle 500ms) unbenutzbar zu werden (siehe
        fester 10s-Intervall statt eines Dateisystem-Watchers)."""
        if self.recording is None or self._watched_folder is None:
            return
        try:
            candidate_paths = sorted(self._watched_folder.glob("*.csv"))
        except OSError:
            return
        known = set(self.recording.paths)
        new_paths = [p for p in candidate_paths if p not in known]
        if not new_paths:
            return
        try:
            # _active_filename_pattern/-strptime_fmt/-import_settings (nicht
            # die evtl. abweichenden _filename_*/-_import_settings-
            # Standardwerte): muss zum Schema/Rohformat passen, mit dem DIESE
            # Aufnahme urspruenglich geladen wurde (siehe _load_paths), sonst
            # wuerden neu hinzukommende Dateien falsch/gar nicht bzw. gar
            # nicht mehr eingelesen.
            updated = append_paths(
                self.recording, new_paths,
                pattern=self._active_filename_pattern, strptime_fmt=self._active_filename_strptime_fmt,
                import_settings=self._active_import_settings,
            )
        except RecordingError:
            return
        if updated.n_frames != self.recording.n_frames:
            self._apply_appended_recording(updated)

    def _apply_appended_recording(self, updated: Recording) -> None:
        """Uebernimmt eine per Live-Ordner-Ueberwachung erweiterte Recording,
        OHNE (anders als _set_recording bei einem regulaeren Neuladen) die
        aktuelle Wiedergabeposition/Ansicht zu verwerfen. Folgt automatisch
        dem neuesten Frame nur, wenn die Anzeige zuvor bereits beim jeweils
        letzten Frame stand (typisches "live mitschauen")."""
        old_n = self.recording.n_frames
        was_at_latest = self.current_index >= old_n - 1
        # Auswertungsende folgt automatisch mit, wenn es zuvor ebenfalls
        # beim letzten Frame stand (analog zur Wiedergabeposition oben) --
        # wurde es bewusst frueher gesetzt, bleibt es unveraendert stehen.
        eval_end_was_at_latest = self._eval_end_index is None or self._eval_end_index >= old_n - 1
        added = updated.n_frames - old_n
        self.recording = updated
        n = updated.n_frames

        self._global_level_range = (
            (float(updated.frames.min()), float(updated.frames.max())) if n else None
        )

        self.frame_slider.blockSignals(True)
        self.frame_slider.setRange(0, max(0, n - 1))
        self.frame_slider.setValue(self.current_index)
        self.frame_slider.blockSignals(False)

        self.frame_spin.blockSignals(True)
        self.frame_spin.setRange(1, max(1, n))
        self.frame_spin.setValue(self.current_index + 1)
        self.frame_spin.blockSignals(False)

        self.spin_eval_start.blockSignals(True)
        self.spin_eval_start.setRange(1, max(1, n))
        self.spin_eval_start.setValue(max(1, (self._eval_start_index or 0) + 1))
        self.spin_eval_start.blockSignals(False)

        self.spin_eval_end.blockSignals(True)
        self.spin_eval_end.setRange(1, max(1, n))
        if eval_end_was_at_latest and n > 0:
            self._eval_end_index = n - 1
        self.spin_eval_end.setValue(max(1, (self._eval_end_index or 0) + 1))
        self.spin_eval_end.blockSignals(False)
        self._update_timeline_markers()

        symbol = "o" if n <= MAX_FRAMES_WITH_SYMBOLS else None
        for entry in self.roi_entries:
            # Nur die Obergrenze mitwachsen lassen -- ein bereits vom Nutzer
            # gewaehltes Start-/Ende-Zielbild fuer die Interpolation bleibt
            # beim Nachladen (Live-Ordner-Ueberwachung) unveraendert stehen.
            entry.spin_interp_start_frame.setRange(1, max(1, n))
            entry.spin_interp_end_frame.setRange(1, max(1, n))
            entry.curve.setSymbol(symbol)
        self.live_curve.setSymbol(symbol)
        self.timeseries_live_curve.setSymbol(symbol)

        self._recompute_curves()

        if was_at_latest and n > 0:
            self.current_index = n - 1
            self._show_frame(self.current_index)
            self.frame_slider.blockSignals(True)
            self.frame_slider.setValue(self.current_index)
            self.frame_slider.blockSignals(False)
            self.frame_spin.blockSignals(True)
            self.frame_spin.setValue(self.current_index + 1)
            self.frame_spin.blockSignals(False)

        self.statusBar().showMessage(
            f"Live-Überwachung: {added} neue(s) Frame(s) geladen ({n} insgesamt)."
        )

    # ------------------------------------------------------- Projektdatei
    def _save_project(self) -> None:
        if self.recording is None:
            QtWidgets.QMessageBox.information(self, "Keine Daten", "Bitte zuerst eine Messreihe laden.")
            return

        path, _ = QtWidgets.QFileDialog.getSaveFileName(
            self, "Projekt speichern", "Projekt.tvproj", "Projekt-Datei (*.tvproj)"
        )
        if not path:
            return
        if not Path(path).suffix:
            path += ".tvproj"

        rois = []
        for entry in self.roi_entries:
            roi_data: dict = {
                # 0-basiert gespeichert (entry.number ist 1-basiert) fuer
                # Kompatibilitaet mit vor "beliebig viele ROIs" gespeicherten
                # Projektdateien; beim Laden wird ueber diese Nummer (nicht
                # eine reine Listen-Position) das passende ROI gefunden bzw.
                # bei Bedarf neu angelegt (siehe _load_project).
                "index": entry.number - 1,
                "name": entry.name,
                "farbe": entry.color,
                "sichtbar": entry.is_visible_checked(),
                "platziert": entry.placed,
                "interpolation_aktiv": entry.interp_enabled,
                "temperatur_anzeigen": entry.show_temperature,
                "kreisfoermig": entry.roi.is_circular,
            }
            if entry.placed:
                cx, cy = entry.center()
                roi_data["mittelpunkt"] = {"x": cx, "y": cy}
                roi_data["breite_px"] = entry.width()
                roi_data["hoehe_px"] = entry.height()
            if entry.interp_start is not None:
                (sx, sy), (sw, sh) = entry.interp_start
                roi_data["interpolation_start"] = {
                    "x": sx, "y": sy, "breite_px": sw, "hoehe_px": sh, "frame": entry.interp_start_frame,
                }
            if entry.interp_end is not None:
                (ex, ey), (ew, eh) = entry.interp_end
                roi_data["interpolation_ende"] = {
                    "x": ex, "y": ey, "breite_px": ew, "hoehe_px": eh, "frame": entry.interp_end_frame,
                }
            rois.append(roi_data)

        rows, cols = self.recording.shape
        data = {
            "format_version": 2,
            "quellordner": str(self.recording.paths[0].parent) if self.recording.paths else None,
            "bild_groesse_px": {"zeilen": rows, "spalten": cols},
            "colormap_index": self.combo_cmap.currentIndex(),
            "colormap_invertiert": self.chk_cmap_invert.isChecked(),
            "level_mode": self._level_mode(),
            "level_min": self.spin_level_min.value(),
            "level_max": self.spin_level_max.value(),
            "px_zu_mm": self._px_to_mm,
            "massstab_farbe": self._ruler_color,
            "auswertungsstart_frame": self._eval_start_index,
            "auswertungsende_frame": self._eval_end_index,
            "rois": rois,
        }

        try:
            Path(path).write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
        except OSError as exc:
            QtWidgets.QMessageBox.critical(self, "Fehler", f"Projekt konnte nicht gespeichert werden:\n{exc}")
            return

        self.statusBar().showMessage(f"Projekt gespeichert: {path}")

    @staticmethod
    def _parse_interp_point(
        data,
    ) -> tuple[tuple[float, float], tuple[float, float], int | None] | None:
        """Parst einen Interpolations-Keyframe ("interpolation_start"/"_ende")
        aus einer Projektdatei, inkl. optionalem Frame-Index ("frame") --
        Projektdateien von vor der Frame-Index-basierten Interpolation
        (siehe RoiEntry.interp_start_frame) haben dieses Feld nicht; der
        Aufrufer setzt in dem Fall einen sinnvollen Standard (erster/letzter
        Frame -- exakt das fruehere, zeitstempel-unabhaengige Verhalten von
        "Start"/"Ende festlegen"). Wirft TypeError/ValueError/KeyError bei
        fehlerhaften/fehlenden Pflichtwerten, statt sie stillschweigend zu
        uebernehmen -- der Aufrufer faengt das gezielt ab."""
        if data is None:
            return None
        if not isinstance(data, dict):
            raise TypeError("interpolation point must be a dict")
        x = float(data["x"])
        y = float(data["y"])
        w = float(data["breite_px"])
        h = float(data["hoehe_px"])
        frame = data.get("frame")
        frame_idx = int(frame) if isinstance(frame, int) and not isinstance(frame, bool) else None
        return (x, y), (w, h), frame_idx

    def _load_project(self) -> None:
        path, _ = QtWidgets.QFileDialog.getOpenFileName(
            self, "Projekt laden", "", "Projekt-Datei (*.tvproj)"
        )
        if not path:
            return
        try:
            data = json.loads(Path(path).read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            QtWidgets.QMessageBox.critical(self, "Fehler", f"Projekt konnte nicht geladen werden:\n{exc}")
            return

        if self.recording is None:
            # Moeglichst wenige Klicks, um ein Projekt OHNE bereits geladene
            # Messreihe zu oeffnen (Bugreport: "Keine Daten"-Fehler zwang
            # dazu, ERST manuell den Ordner zu laden): der im Projekt
            # gespeicherte Quellordner (siehe _save_project) wird dafuer
            # automatisch nachgeladen, sofern er noch existiert -- nur wenn
            # das nicht klappt, muss der Ordner einmalig manuell gewaehlt
            # werden.
            saved_folder = data.get("quellordner")
            saved_folder_exists = isinstance(saved_folder, str) and Path(saved_folder).is_dir()
            loaded = saved_folder_exists and self._load_folder(Path(saved_folder))
            if not loaded:
                # Zwei unterschiedliche Gruende sauber unterscheiden --
                # sonst behauptet die Meldung faelschlich "nicht gefunden",
                # obwohl der Ordner existiert, aber z.B. keine zum
                # Namensschema passenden Dateien enthaelt oder der
                # Namensschema-Abgleich abgebrochen wurde.
                if saved_folder and not saved_folder_exists:
                    hint = f" (gespeicherter Ordner „{saved_folder}“ nicht gefunden)"
                elif saved_folder:
                    hint = f" (Laden von „{saved_folder}“ nicht erfolgreich)"
                else:
                    hint = ""
                QtWidgets.QMessageBox.information(
                    self,
                    "Messreihe wählen",
                    f"Für dieses Projekt ist noch keine Messreihe geladen{hint}. "
                    "Bitte jetzt den passenden Ordner auswählen.",
                )
                folder = QtWidgets.QFileDialog.getExistingDirectory(self, "Ordner mit CSV-Messreihe wählen")
                if not folder or not self._load_folder(Path(folder)):
                    return

        saved_folder = data.get("quellordner")
        current_folder = str(self.recording.paths[0].parent) if self.recording.paths else None
        saved_size = data.get("bild_groesse_px")
        current_rows, current_cols = self.recording.shape
        size_mismatch = (
            isinstance(saved_size, dict)
            and (saved_size.get("zeilen"), saved_size.get("spalten")) != (current_rows, current_cols)
        )
        folder_mismatch = bool(saved_folder and current_folder and saved_folder != current_folder)
        if size_mismatch:
            QtWidgets.QMessageBox.warning(
                self,
                "Andere Bildauflösung",
                "Dieses Projekt wurde für eine Messreihe mit anderer Bildauflösung gespeichert "
                f"({saved_size.get('spalten')}x{saved_size.get('zeilen')} statt aktuell "
                f"{current_cols}x{current_rows}). Messbereich-Koordinaten außerhalb des Bildes "
                "wurden auf den Bildrand begrenzt – bitte Position/Größe der Messbereiche prüfen.",
            )
        elif folder_mismatch:
            QtWidgets.QMessageBox.information(
                self,
                "Anderer Quellordner",
                "Dieses Projekt wurde für eine andere Messreihe gespeichert:\n"
                f"{saved_folder}\n\n"
                "Es wird trotzdem auf die aktuell geladene Messreihe angewendet – bitte "
                "Messbereiche danach kurz prüfen.",
            )

        cmap_index = data.get("colormap_index")
        if isinstance(cmap_index, int) and 0 <= cmap_index < self.combo_cmap.count():
            self.combo_cmap.setCurrentIndex(cmap_index)
        self.chk_cmap_invert.setChecked(bool(data.get("colormap_invertiert", False)))

        level_mode = data.get("level_mode")
        if level_mode not in ("manual", "per_frame", "global"):
            # Abwaertskompatibilitaet zu Projektdateien von vor Punkt 1
            # (einfaches Bool "auto_levels" statt drei Modi).
            level_mode = "per_frame" if data.get("auto_levels", True) else "manual"
        self._set_level_mode(level_mode)
        if level_mode == "manual":
            self.spin_level_min.setValue(data.get("level_min", self.spin_level_min.value()))
            self.spin_level_max.setValue(data.get("level_max", self.spin_level_max.value()))

        px_to_mm = data.get("px_zu_mm")
        self._px_to_mm = float(px_to_mm) if isinstance(px_to_mm, (int, float)) else None

        ruler_color = data.get("massstab_farbe")
        if isinstance(ruler_color, str) and QtGui.QColor(ruler_color).isValid():
            self._ruler_color = ruler_color
            self._update_ruler_color_swatch()
            self._apply_ruler_color()

        self._refresh_scale_label()

        # Hinweis: "grafik_theme" aus alten Projektdateien (vor dem
        # einheitlichen Dunkelmodus-Umschalter) wird bewusst ignoriert -- die
        # Grafik-Darstellung folgt jetzt immer dem aktuellen App-Design.

        eval_start = data.get("auswertungsstart_frame")
        if isinstance(eval_start, int) and self.recording is not None and 0 <= eval_start < self.recording.n_frames:
            self._eval_start_index = eval_start
            self.spin_eval_start.blockSignals(True)
            self.spin_eval_start.setValue(eval_start + 1)
            self.spin_eval_start.blockSignals(False)

        eval_end = data.get("auswertungsende_frame")
        if isinstance(eval_end, int) and self.recording is not None and 0 <= eval_end < self.recording.n_frames:
            self._eval_end_index = eval_end
            self.spin_eval_end.blockSignals(True)
            self.spin_eval_end.setValue(eval_end + 1)
            self.spin_eval_end.blockSignals(False)

        if (isinstance(eval_start, int) or isinstance(eval_end, int)) and self.recording is not None:
            # Falls Start > Ende in der Datei stand (z.B. handbearbeitet):
            # Ende gewinnt, Start wird passend nachgezogen -- konsistent mit
            # der Live-Klemmung in _on_eval_start_changed.
            if (
                self._eval_start_index is not None
                and self._eval_end_index is not None
                and self._eval_start_index > self._eval_end_index
            ):
                self._eval_start_index = self._eval_end_index
                self.spin_eval_start.blockSignals(True)
                self.spin_eval_start.setValue(self._eval_start_index + 1)
                self.spin_eval_start.blockSignals(False)
            self._update_timeline_markers()

        touched_entries: list[RoiEntry] = []
        failed_indices: list[int] = []
        for roi_data in data.get("rois", []):
            if not isinstance(roi_data, dict):
                continue
            idx = roi_data.get("index")
            if not isinstance(idx, int) or idx < 0:
                continue
            if idx >= MAX_ROI_COUNT:
                # Verhindert, dass eine manipulierte/beschaedigte .tvproj-
                # Datei mit einer riesigen "index"-Zahl versucht, ebenso
                # viele ROI-Eintraege auf einmal anzulegen (siehe
                # MAX_ROI_COUNT) -- als fehlerhaft behandelt wie jeder
                # andere ungueltige ROI-Eintrag.
                failed_indices.append(idx)
                continue
            # Ueber die (0-basiert gespeicherte) Erzeugungsnummer statt einer
            # reinen Listen-Position zuordnen: bei "beliebig viele ROIs"
            # koennen Messbereiche entfernt worden sein, wodurch sich
            # Positionen verschieben. Existiert die Nummer noch nicht (z.B.
            # Projekt mit mehr ROIs als aktuell vorhanden), werden bei Bedarf
            # neue Messbereiche angelegt; eine Nummer eines FRUEHER bereits
            # entfernten ROIs wird dagegen uebersprungen (nicht rekonstruierbar).
            target_number = idx + 1
            entry = next((e for e in self.roi_entries if e.number == target_number), None)
            if entry is None:
                if target_number < self._roi_next_number:
                    continue
                while self._roi_next_number <= target_number:
                    entry = self._add_roi_entry()
            entry_failed = False

            # Grundangaben + Platzierung in einem eigenen try-Block: ein
            # spaeter fehlschlagender Interpolations-Block (siehe unten) darf
            # eine hier bereits erfolgreiche Platzierung nicht mehr rueckgaengig
            # machen bzw. von der Kurven-Neuberechnung ausschliessen.
            try:
                name = roi_data.get("name")
                if name:
                    entry.list_item.setText(name)

                color = roi_data.get("farbe")
                if color:
                    entry.set_color(color)

                entry.list_item.setCheckState(
                    QtCore.Qt.CheckState.Checked
                    if roi_data.get("sichtbar", True)
                    else QtCore.Qt.CheckState.Unchecked
                )

                entry.chk_show_temperature.setChecked(bool(roi_data.get("temperatur_anzeigen", True)))
                entry.chk_circular.setChecked(bool(roi_data.get("kreisfoermig", False)))

                mittelpunkt = roi_data.get("mittelpunkt")
                if roi_data.get("platziert") and isinstance(mittelpunkt, dict):
                    width = roi_data.get("breite_px")
                    height = roi_data.get("hoehe_px")
                    if width is None or height is None:
                        # Altes Projektformat (Punkt 2): eine einzelne
                        # "groesse" statt getrennter Breite/Hoehe.
                        width = height = roi_data.get("groesse", DEFAULT_ROI_SIZE)
                    cx = float(mittelpunkt.get("x", 0.0))
                    cy = float(mittelpunkt.get("y", 0.0))
                    width = float(width)
                    height = float(height)
                    # _set_widget_value (blockSignals) statt direktem .setValue():
                    # die vier Felder sind seit der Live-Uebernahme-Umstellung
                    # (siehe spin.valueChanged weiter oben) mit _on_roi_apply_clicked
                    # verbunden -- ohne Blockade wuerde JEDER der vier setValue()-
                    # Aufrufe hier bereits selbst einen (mangels der jeweils noch
                    # nicht gesetzten uebrigen drei Werte unvollstaendigen)
                    # Platzierungs-/Kurven-Neuberechnungs-Durchlauf ausloesen, statt
                    # dass -- wie beabsichtigt -- erst das anschliessende entry.place()
                    # unten mit den vollstaendigen Werten einmalig greift.
                    for spin, value in (
                        (entry.spin_x, cx), (entry.spin_y, cy),
                        (entry.spin_width, width), (entry.spin_height, height),
                    ):
                        self._set_widget_value(spin, value)
                    entry.place(
                        entry.spin_x.value(), entry.spin_y.value(),
                        entry.spin_width.value(), entry.spin_height.value(),
                    )
                    self._sync_roi_spinboxes(entry)
            except (TypeError, ValueError):
                # Ein einzelner fehlerhafter ROI-Eintrag (z.B. handbearbeitete
                # oder beschaedigte .tvproj-Datei) soll nicht verhindern, dass
                # die uebrigen, gueltigen Eintraege trotzdem angewendet werden.
                entry_failed = True

            # Verlaufs-Interpolation separat parsen/validieren: bei fehlerhaften
            # Werten wird der Interpolationszustand des ROI explizit auf "aus"
            # zurueckgesetzt, statt (mit evtl. nicht-numerischen Werten) stehen
            # zu bleiben -- sonst wuerde derselbe fehlerhafte Wert beim naechsten
            # Frame-Wechsel (_show_frame -> apply_interp_frame) ungefangen
            # erneut auftreten und die Wiedergabe abstuerzen lassen.
            # _reset_interp_arm_state() unbedingt VOR dem Ueberschreiben von
            # interp_start/interp_end aufrufen: chk_interp.setChecked() loest
            # _on_roi_interp_toggled() (das sonst zuruecksetzt) nur aus, wenn
            # sich der Haken-Zustand tatsaechlich aendert -- ein Start-/Ende-
            # Button, der gerade auf "Position uebernehmen" stand, wuerde
            # sonst beim naechsten Klick die frisch geladenen Werte sofort
            # wieder mit der aktuellen ROI-Position ueberschreiben.
            self._reset_interp_arm_state(entry)
            try:
                parsed_start = self._parse_interp_point(roi_data.get("interpolation_start"))
                parsed_end = self._parse_interp_point(roi_data.get("interpolation_ende"))
                n_frames = self.recording.n_frames if self.recording is not None else 0
                if parsed_start is not None:
                    (sx, sy), (sw, sh), sframe = parsed_start
                    entry.interp_start = ((sx, sy), (sw, sh))
                    # Alte Projektdateien (vor Frame-Index-basierter
                    # Interpolation) haben kein "frame"-Feld -- Standard war
                    # damals immer der erste Frame (siehe frueheres
                    # _step_frame(-self.current_index) bei "Start festlegen").
                    entry.interp_start_frame = sframe if sframe is not None else 0
                    # Zahlenfeld "Erstes Frame:" (1-basiert) synchron halten
                    # -- sonst zeigt es weiterhin den alten/Default-Wert,
                    # waehrend ein erneutes "Start festlegen" bereits zum
                    # (falschen) Zahlenfeld-Wert springt und den frisch
                    # geladenen Keyframe beim naechsten Klick ueberschreibt.
                    self._set_widget_value(entry.spin_interp_start_frame, entry.interp_start_frame + 1)
                else:
                    entry.interp_start = None
                    entry.interp_start_frame = None
                if parsed_end is not None:
                    (ex, ey), (ew, eh), eframe = parsed_end
                    entry.interp_end = ((ex, ey), (ew, eh))
                    entry.interp_end_frame = eframe if eframe is not None else max(0, n_frames - 1)
                    self._set_widget_value(entry.spin_interp_end_frame, entry.interp_end_frame + 1)
                else:
                    entry.interp_end = None
                    entry.interp_end_frame = None
                entry.chk_interp.setChecked(
                    bool(roi_data.get("interpolation_aktiv", False))
                    and entry.interp_start is not None
                    and entry.interp_end is not None
                )
            except (TypeError, ValueError, KeyError):
                entry.interp_start = None
                entry.interp_end = None
                entry.interp_start_frame = None
                entry.interp_end_frame = None
                entry.chk_interp.blockSignals(True)
                entry.chk_interp.setChecked(False)
                entry.chk_interp.blockSignals(False)
                entry.interp_enabled = False
                entry.btn_interp_start.setEnabled(False)
                entry.btn_interp_end.setEnabled(False)
                entry_failed = True

            if entry_failed:
                failed_indices.append(idx)
            touched_entries.append(entry)

        if touched_entries:
            self._recompute_curves(entries=touched_entries)
        self._apply_interp_focus_visuals()

        message = f"Projekt geladen: {path}"
        if failed_indices:
            message += f"  |  {len(failed_indices)} ROI-Eintrag/Einträge übersprungen (fehlerhaft)."
            QtWidgets.QMessageBox.warning(
                self,
                "Fehlerhafte ROI-Einträge übersprungen",
                "Folgende Messbereich-Einträge in der Projektdatei waren fehlerhaft und wurden "
                "übersprungen: " + ", ".join(f"ROI {i + 1}" for i in failed_indices),
            )
        self.statusBar().showMessage(message)

    def _load_paths(
        self, paths: list[Path], pattern: re.Pattern | None = None, strptime_fmt: str | None = None
    ) -> bool:
        """pattern/strptime_fmt: optionales, nur fuer DIESEN Ladevorgang
        geltendes Namensschema (siehe _resolve_folder_and_pattern) -- ohne
        Angabe gilt das aktive Standard-Namensschema
        (self._filename_pattern/_filename_strptime_fmt).

        Gibt zurueck, ob das Laden erfolgreich war -- Aufrufer, die danach
        noch Folgezustand setzen (z.B. _open_folder mit der Live-Ordner-
        Ueberwachung), duerfen das NUR bei True tun, sonst wuerde bei einem
        Fehler (z.B. defekte CSVs) unbemerkt auf den falschen/neuen Ordner
        umgeschaltet, waehrend die vorherige Aufnahme weiter angezeigt bleibt."""
        if not paths:
            QtWidgets.QMessageBox.warning(self, "Keine Dateien", "Es wurden keine CSV-Dateien gefunden.")
            return False
        pattern = self._filename_pattern if pattern is None else pattern
        strptime_fmt = self._filename_strptime_fmt if strptime_fmt is None else strptime_fmt
        import_settings = self._import_settings

        # Schleife statt Einzelversuch: schlaegt das Laden komplett fehl
        # (z.B. weil das Rohformat vom erwarteten abweicht), bietet
        # _offer_import_settings_retry an, den Datenimport-Manager zu
        # oeffnen und mit angepassten Einstellungen erneut zu versuchen --
        # analog zur Namensschema-Rueckfrage in _resolve_folder_and_pattern.
        while True:
            progress = QtWidgets.QProgressDialog("Lade Frames…", "Abbrechen", 0, len(paths), self)
            progress.setWindowModality(QtCore.Qt.WindowModal)
            progress.setMinimumDuration(300)

            def _cb(done: int, total: int) -> None:
                progress.setValue(done)
                QtWidgets.QApplication.processEvents()

            try:
                # _cb() pumpt hier wiederholt processEvents() -- siehe
                # _paused_background_timers zum Grund, warum Live-Watch/
                # Wiedergabe dafuer pausiert sein muessen.
                with self._paused_background_timers():
                    recording = load_paths(
                        paths, progress_cb=_cb, pattern=pattern, strptime_fmt=strptime_fmt,
                        import_settings=import_settings,
                    )
                error: RecordingError | None = None
            except RecordingError as exc:
                recording = None
                error = exc
            progress.close()

            if error is None:
                break
            if self._offer_import_settings_retry(paths[0], str(error)):
                dlg = ImportSettingsDialog(self, paths[0], import_settings, is_retry=True)
                if dlg.exec() == QtWidgets.QDialog.DialogCode.Accepted:
                    new_settings = dlg.settings()
                    if dlg.persist():
                        self._set_import_settings(new_settings, persist=True)
                    import_settings = new_settings
                    continue
            QtWidgets.QMessageBox.critical(self, "Fehler beim Laden", str(error))
            return False

        # Merken, mit welchem Namensschema/Datenimport-Format DIESE Aufnahme
        # tatsaechlich geladen wurde -- siehe _check_for_new_files
        # (Live-Ordner-Ueberwachung muss konsistent dasselbe Schema/Format
        # weiterverwenden, auch wenn es vom aktuellen Standard abweicht).
        self._active_filename_pattern = pattern
        self._active_filename_strptime_fmt = strptime_fmt
        self._active_import_settings = import_settings
        self._set_recording(recording)
        return True

    def _set_recording(self, recording: Recording) -> None:
        self.recording = recording
        n = recording.n_frames
        rows, cols = recording.shape

        # Eine evtl. noch eingezeichnete Referenzlinie bezieht sich auf
        # Pixel-Koordinaten der ALTEN Aufnahme und waere auf dem neuen Bild
        # irrefuehrend platziert -- der Umrechnungsfaktor selbst (_px_to_mm)
        # bleibt bewusst bestehen (siehe Hinweis weiter unten), nur die
        # Visualisierung wird ausgeblendet.
        self._hide_ruler_visuals()
        self._cancel_measure_tool()
        self._hide_measure_visuals()

        for action in self._requires_recording_actions:
            action.setEnabled(True)
        self._refresh_scale_label()

        self._global_level_range = (
            (float(recording.frames.min()), float(recording.frames.max())) if n else None
        )

        self.frame_slider.blockSignals(True)
        self.frame_slider.setRange(0, n - 1)
        self.frame_slider.setValue(0)
        self.frame_slider.blockSignals(False)

        self.frame_spin.blockSignals(True)
        self.frame_spin.setRange(1, max(1, n))
        self.frame_spin.setValue(1)
        self.frame_spin.blockSignals(False)

        # Auswertungsstart/-ende (manuelles Festlegen des Bereichs) starten
        # standardmaessig beim ersten bzw. jeweils letzten Frame der neu
        # geladenen Aufnahme.
        self._eval_start_index = 0 if n else None
        self.spin_eval_start.blockSignals(True)
        self.spin_eval_start.setRange(1, max(1, n))
        self.spin_eval_start.setValue(1)
        self.spin_eval_start.blockSignals(False)

        self._eval_end_index = n - 1 if n else None
        self.spin_eval_end.blockSignals(True)
        self.spin_eval_end.setRange(1, max(1, n))
        self.spin_eval_end.setValue(max(1, n))
        self.spin_eval_end.blockSignals(False)
        self._update_timeline_markers()

        symbol = "o" if n <= MAX_FRAMES_WITH_SYMBOLS else None
        for entry in self.roi_entries:
            self._set_roi_geometry_ranges(entry, cols, rows)
            # Start/Ende-Zielbild der Interpolation: Standard weiterhin
            # erstes/letztes Bild der neu geladenen Aufnahme (bisheriges
            # Verhalten), aber jederzeit manuell aenderbar.
            entry.spin_interp_start_frame.setRange(1, max(1, n))
            entry.spin_interp_start_frame.setValue(1)
            entry.spin_interp_end_frame.setRange(1, max(1, n))
            entry.spin_interp_end_frame.setValue(max(1, n))
            # Bereits erfasste Interpolations-Keyframes (interp_start_frame/
            # -end_frame) auf die neue Aufnahme klemmen, NICHT verwerfen --
            # eine neu geladene Aufnahme kann kuerzer sein als die vorherige,
            # auf der die Keyframes urspruenglich gesetzt wurden. Ohne diese
            # Klemmung bliebe _interp_fraction() bei einem viel zu grossen
            # Nenner haengen und der Messbereich wuerde sein Ende NIE
            # erreichen, egal wie weit die neue (kuerzere) Aufnahme laeuft.
            max_idx = max(0, n - 1)
            if entry.interp_start_frame is not None:
                entry.interp_start_frame = min(entry.interp_start_frame, max_idx)
            if entry.interp_end_frame is not None:
                entry.interp_end_frame = min(entry.interp_end_frame, max_idx)
            entry.curve.setSymbol(symbol)
        self.live_curve.setSymbol(symbol)
        self.timeseries_live_curve.setSymbol(symbol)

        self._hover_row = None
        self._hover_col = None
        self._live_pinned = False
        self.live_cursor_marker.setVisible(False)
        self.live_cursor_label.setVisible(False)
        self.live_curve.clear()
        self.timeseries_live_curve.clear()
        self.live_label.setText(
            "Maus über das Bild bewegen, um den Temperaturverlauf am Cursor-Pixel live zu sehen. "
            "Linksklick fixiert die Stelle, Rechtsklick löst die Fixierung wieder."
        )

        self.view_box.setRange(xRange=(0, cols), yRange=(0, rows), padding=0.02)
        self.current_index = 0
        self._show_frame(0)
        self._recompute_curves()
        # t0 fuer den Laufzeit-Anzeigemodus (Zeitachse) bezieht sich auf DIESE
        # (neue) Aufnahme -- Anzeigemodus selbst (Uhrzeit/Laufzeit) bleibt wie
        # vom Nutzer gewaehlt bestehen, nur t0 wird aufgefrischt.
        self._apply_time_display_mode(self._time_display_mode)

        message = f"{n} Frame(s) geladen aus {recording.paths[0].parent}"
        if self._px_to_mm is not None:
            # Ein Massstab bleibt bewusst ueber einen Neuladevorgang hinweg
            # bestehen (z.B. gleicher Pruefstand/gleiche Kamera-Optik) -- bei
            # einer anderen Messreihe koennte er aber nicht mehr passen.
            message += "  |  Hinweis: Es ist noch ein zuvor definierter Maßstab aktiv, bitte auf Gültigkeit prüfen."
        if recording.had_duplicate_timestamps:
            message += (
                "  |  Achtung: mehrere Dateien hatten denselben Zeitstempel im "
                "Dateinamen (z.B. durch Kopieren) und wurden für die Zeitachse "
                "künstlich um je 1 ms auseinandergezogen."
            )
            QtWidgets.QMessageBox.warning(
                self,
                "Doppelte Zeitstempel erkannt",
                "Mehrere geladene Dateien tragen denselben Zeitstempel im Dateinamen "
                "(z.B. weil eine Datei kopiert wurde, ohne den Zeitstempel im Namen zu "
                "ändern). Für eine sinnvolle Zeitachse wurden diese Frames um je 1 ms "
                "auseinandergezogen. Für echte Messreihen sollte jede Datei einen "
                "eindeutigen Zeitstempel im Namen haben.",
            )
        if recording.skipped_files:
            message += f"  |  {len(recording.skipped_files)} Datei(en) übersprungen."
            details = "\n".join(f"- {p.name}: {err}" for p, err in recording.skipped_files)
            QtWidgets.QMessageBox.warning(
                self,
                "Einzelne Dateien übersprungen",
                f"{len(recording.skipped_files)} von "
                f"{n + len(recording.skipped_files)} ausgewählten Datei(en) konnten nicht "
                "geladen werden (kaputte/unlesbare CSV oder abweichende Bildauflösung) und "
                f"wurden übersprungen. Die übrigen {n} Frame(s) wurden normal geladen:\n\n"
                f"{details}",
            )
        self.statusBar().showMessage(message)

    # --------------------------------------------------------- Frame-Nav
    def _step_frame(self, delta: int) -> None:
        if self.recording is None or self.recording.n_frames == 0:
            return
        new_index = max(0, min(self.current_index + delta, self.recording.n_frames - 1))
        self.frame_slider.setValue(new_index)

    def _jump_to_first_frame(self) -> None:
        """Springt zum Auswertungsstart (Standard: erster Frame, per Spinbox
        "Auswertungsstart"/gruene Markierung in der Zeitleiste manuell nach
        hinten korrigierbar) -- genutzt sowohl von der Tastatur-Taste "Pos1"
        als auch von "Start festlegen" bei der Verlaufs-Interpolation."""
        if self.recording is None or self.recording.n_frames == 0:
            return
        target = self._eval_start_index if self._eval_start_index is not None else 0
        self._step_frame(target - self.current_index)

    def _jump_to_last_frame(self) -> None:
        """Springt zum Auswertungsende (Standard: letzter geladener Frame,
        per Spinbox "Auswertungsende"/rote Markierung in der Zeitleiste
        manuell nach unten korrigierbar) -- genutzt sowohl von der
        Tastatur-Taste "Ende" als auch von "Ende festlegen" bei der
        Verlaufs-Interpolation."""
        if self.recording is None or self.recording.n_frames == 0:
            return
        target = self._eval_end_index if self._eval_end_index is not None else self.recording.n_frames - 1
        self._step_frame(target - self.current_index)

    def _on_eval_start_changed(self, value: int) -> None:
        if self.recording is None:
            return
        new_start = value - 1
        current_end = self._eval_end_index if self._eval_end_index is not None else self.recording.n_frames - 1
        if new_start > current_end:
            # Start darf das Ende nicht ueberholen -- Ende folgt stattdessen
            # mit nach hinten (symmetrisch zu _on_eval_end_changed).
            self._eval_end_index = new_start
            self.spin_eval_end.blockSignals(True)
            self.spin_eval_end.setValue(new_start + 1)
            self.spin_eval_end.blockSignals(False)
        self._eval_start_index = new_start
        self._update_timeline_markers()

    def _on_eval_end_changed(self, value: int) -> None:
        if self.recording is None:
            return
        new_end = value - 1
        current_start = self._eval_start_index if self._eval_start_index is not None else 0
        if new_end < current_start:
            self._eval_start_index = new_end
            self.spin_eval_start.blockSignals(True)
            self.spin_eval_start.setValue(new_end + 1)
            self.spin_eval_start.blockSignals(False)
        self._eval_end_index = new_end
        self._update_timeline_markers()

    def _on_timeline_marker_dragged(self, which: str, value: int) -> None:
        if self.recording is None:
            return
        value = max(0, min(value, self.recording.n_frames - 1))
        if which == "start":
            self.spin_eval_start.setValue(value + 1)
        else:
            self.spin_eval_end.setValue(value + 1)

    def _update_timeline_markers(self) -> None:
        if self.recording is None or self.recording.n_frames == 0:
            self.frame_slider.set_markers(None, None)
            return
        self.frame_slider.set_markers(self._eval_start_index, self._eval_end_index)

    def _on_slider_changed(self, value: int) -> None:
        # Der Schieberegler ist intern 0-basiert (Frame-Index), das Zahlenfeld
        # daneben zeigt dem Nutzer wie die Statuszeile ("Frame 1/8") bewusst
        # 1-basierte Frame-Nummern, um Verwirrung zu vermeiden. Beide Widgets
        # werden zentral in _show_frame() synchron gehalten.
        self._show_frame(value)

    def _on_frame_spin_changed(self, value: int) -> None:
        self._show_frame(value - 1)

    def _level_mode(self) -> str:
        if self.radio_level_manual.isChecked():
            return "manual"
        return "global" if self.radio_level_global.isChecked() else "per_frame"

    def _set_level_mode(self, mode: str) -> None:
        """Setzt beide Radio-Gruppen (aeussere Automatisch/Manuell-Wahl und
        innere Pro-Bild/Gesamte-Serie-Unterwahl) konsistent auf den
        gewuenschten Modus-String -- zentrale Gegenstueck zu _level_mode(),
        genutzt beim Laden eines Projekts und beim Wiederherstellen nach
        einem temporaeren Override (z.B. Video-Export mit eigenen
        Einstellungen)."""
        if mode == "manual":
            self._set_widget_value(self.radio_level_manual, True, "setChecked")
        else:
            self._set_widget_value(self.radio_level_auto, True, "setChecked")
            sub_radio = self.radio_level_global if mode == "global" else self.radio_level_per_frame
            self._set_widget_value(sub_radio, True, "setChecked")
        self._on_level_mode_changed(None, True)

    @staticmethod
    def _set_widget_value(widget, value, setter_name: str = "setValue") -> None:
        """Setzt einen Widget-Wert, ohne dass dessen Change-Signal auf dem Weg
        dorthin ungewollt weitere Handler ausloest (z.B. beim programmatischen
        Wiederherstellen eines vorherigen Zustands)."""
        widget.blockSignals(True)
        getattr(widget, setter_name)(value)
        widget.blockSignals(False)

    def _set_level_spins(self, lo: float, hi: float) -> None:
        self._set_widget_value(self.spin_level_min, lo)
        self._set_widget_value(self.spin_level_max, hi)

    def _sync_histogram_levels(self, lo: float, hi: float) -> None:
        # Bugfix: self.image_item.setLevels() allein bewegt die eigene
        # Anzeige des HistogramLUTItem (Balken/Griffe direkt neben der
        # Farb-Legende) NICHT mit -- ohne diesen expliziten Aufruf blieb der
        # Balken nach dem Laden einer Messreihe bei seinem Konstruktions-
        # Default (0°) stehen, bis der Nutzer den Skalierungs-Modus manuell
        # nochmal umschaltete (Bugreport). blockSignals, da _set_level_spins
        # (vom Aufrufer direkt danach aufgerufen) dieselben Werte ohnehin
        # schon an die Spinboxen uebertraegt -- sonst wuerde
        # _on_histogram_levels_changed dieselbe Arbeit redundant wiederholen.
        self.histogram.blockSignals(True)
        self.histogram.setLevels(lo, hi)
        self.histogram.blockSignals(False)

    def _apply_levels_for_frame(self, frame: np.ndarray) -> None:
        mode = self._level_mode()
        if mode == "per_frame":
            self.image_item.setImage(frame, autoLevels=True)
            lo, hi = self.image_item.getLevels()
            self._sync_histogram_levels(lo, hi)
            self._set_level_spins(lo, hi)
        elif mode == "global" and self._global_level_range is not None:
            lo, hi = self._global_level_range
            self.image_item.setImage(frame, autoLevels=False)
            self.image_item.setLevels((lo, hi))
            self._sync_histogram_levels(lo, hi)
            self._set_level_spins(lo, hi)
        else:
            lo, hi = self.spin_level_min.value(), self.spin_level_max.value()
            self.image_item.setImage(frame, autoLevels=False)
            self.image_item.setLevels((lo, hi))
            self._sync_histogram_levels(lo, hi)

    @staticmethod
    def _interp_fraction(idx: int, start_idx: int, end_idx: int) -> float:
        """Frame-Index-Anteil von idx zwischen start_idx und end_idx, geklemmt
        auf [0, 1]. Gemeinsam genutzt von _update_interpolated_rois (Anzeige)
        und _recompute_curves (Kurvenberechnung), damit beide garantiert
        dieselbe Interpolations-Formel verwenden. Bewusst frame-index- statt
        zeitstempel-basiert (siehe RoiEntry.interp_start_frame)."""
        span = end_idx - start_idx
        if span <= 0:
            return 0.0
        return max(0.0, min(1.0, (idx - start_idx) / span))

    def _update_interpolated_rois(self, idx: int) -> None:
        for entry in self.roi_entries:
            if not entry.is_interp_ready():
                continue
            frac = self._interp_fraction(idx, entry.interp_start_frame, entry.interp_end_frame)
            entry.apply_interp_frame(frac)
            self._sync_roi_spinboxes(entry)

    def _update_roi_temperature_labels(self, idx: int, entries: list[RoiEntry] | None = None) -> None:
        """Aktualisiert die im Bild neben dem Namen angezeigte, aktuell
        gemittelte Temperatur der platzierten Messbereiche (Punkt 10).
        Direkt aus den Rohdaten des aktuellen Frames berechnet (statt aus
        entry.curve gelesen), damit die Anzeige unabhaengig davon korrekt
        ist, ob _recompute_curves() fuer diesen Frame bereits gelaufen ist.

        entries: optionale Teilmenge (Standard: alle Eintraege) -- z.B.
        waehrend eines ROI-Drags (_on_roi_region_changed, feuert laufend bei
        jeder Mausbewegung) wird bewusst NUR der gerade gezogene Messbereich
        neu berechnet statt bei jedem Zwischenschritt alle platzierten
        Messbereiche erneut durchzugehen, deren Temperatur sich dabei gar
        nicht aendert."""
        if self.recording is None or self.recording.n_frames == 0:
            return
        # idx nicht ungeprueft uebernehmen: self.current_index kann kurzzeitig
        # veraltet sein (z.B. waehrend eine Live-Ueberwachung die Aufnahme
        # gerade durch eine kleinere ersetzt hat, aber ein ROI-Drag noch aus
        # der alten Geometrie ein sigRegionChanged ausloest, siehe
        # _on_roi_region_changed) -- ohne Clamping fuehrte das zu einem
        # IndexError beim Zugriff auf self.recording.frames[idx].
        idx = max(0, min(idx, self.recording.n_frames - 1))
        shape = self.recording.shape
        for entry in (entries if entries is not None else self.roi_entries):
            if not entry.placed:
                continue
            if entry.is_interp_ready():
                frac = self._interp_fraction(idx, entry.interp_start_frame, entry.interp_end_frame)
                x, y, w, h = entry.interp_rect(frac)
                row0, row1, col0, col1 = bounds_px_for(x, y, w, h, shape)
            else:
                row0, row1, col0, col1 = entry.bounds_px(shape)
            temperature = float(entry.average(self.recording.frames[idx, row0:row1, col0:col1], row0, row1, col0, col1))
            entry.update_temperature_label(temperature)

    def _show_frame(self, idx: int) -> None:
        if self.recording is None or self.recording.n_frames == 0:
            return
        idx = max(0, min(idx, self.recording.n_frames - 1))
        self.current_index = idx
        # Schieberegler/Zahlenfeld hier zentral synchron halten, damit sie
        # auch bei direkten _show_frame()-Aufrufen ausserhalb der ueblichen
        # Slider-/Spin-Handler (z.B. Video-Export, initiales Laden) nicht vom
        # tatsaechlich angezeigten Frame abweichen koennen.
        self.frame_slider.blockSignals(True)
        self.frame_slider.setValue(idx)
        self.frame_slider.blockSignals(False)
        self.frame_spin.blockSignals(True)
        self.frame_spin.setValue(idx + 1)
        self.frame_spin.blockSignals(False)
        frame = self.recording.frames[idx]

        self._apply_levels_for_frame(frame)

        ts = self.recording.timestamps[idx]
        self.timestamp_label.setText("  " + ts.strftime("%Y-%m-%d %H:%M:%S"))

        unix = self.recording.unix_seconds()
        self.frame_marker.setValue(unix[idx])
        self.live_frame_marker.setValue(unix[idx])

        self._update_interpolated_rois(idx)
        self._update_roi_temperature_labels(idx)

        self._update_status_bar()

    def _on_play_toggled(self, checked: bool) -> None:
        if checked:
            if self.recording is None or self.recording.n_frames < 2:
                self.play_button.setChecked(False)
                return
            n = self.recording.n_frames
            start_idx = self._eval_start_index if self._eval_start_index is not None else 0
            end_idx = self._eval_end_index if self._eval_end_index is not None else n - 1
            # Wiedergabe bleibt standardmaessig auf den Auswertungsbereich
            # (gruene/rote Markierung) begrenzt -- nur wenn der Cursor manuell
            # AUSSERHALB dieses Bereichs steht, laeuft sie ungeklemmt bis zum
            # tatsaechlichen Ende der Aufnahme.
            self._play_clamped = start_idx <= self.current_index <= end_idx
            if self._play_clamped:
                if self.current_index >= end_idx:
                    # Wiedergabe war bereits am Ende des Bereichs angekommen --
                    # erneutes Starten faengt wieder beim Start des Bereichs an.
                    self.frame_slider.setValue(start_idx)
            elif self.current_index >= n - 1:
                # Wiedergabe war bereits am tatsaechlichen Ende angekommen --
                # erneutes Starten faengt wieder von vorne an.
                self.frame_slider.setValue(0)
            self.play_button.setText("⏸ Pause")
            interval = int(1000 / max(0.1, self.fps_spin.value()))
            self.play_timer.start(interval)
        else:
            self.play_button.setText("▶ Play")
            self.play_timer.stop()

    def _advance_frame(self) -> None:
        if self.recording is None:
            self.play_button.setChecked(False)
            return
        n = self.recording.n_frames
        nxt = self.current_index + 1
        if self._play_clamped:
            end_idx = self._eval_end_index if self._eval_end_index is not None else n - 1
            if nxt > end_idx:
                self.play_button.setChecked(False)
                return
        if nxt >= n:
            self.play_button.setChecked(False)
            return
        self.frame_slider.setValue(nxt)

    def _on_fps_changed(self, value: float) -> None:
        if self.play_timer.isActive():
            self.play_timer.setInterval(int(1000 / max(0.1, value)))

    # -------------------------------------------------------------- ROI
    def _on_add_roi_clicked(self) -> None:
        self._add_roi_entry()

    def _on_roi_remove_clicked(self, entry: RoiEntry) -> None:
        answer = QtWidgets.QMessageBox.question(
            self,
            "Messbereich entfernen",
            f"„{entry.name}“ inkl. Zeitverlauf-Kurve endgültig entfernen?\nDies kann nicht "
            "rückgängig gemacht werden.",
            QtWidgets.QMessageBox.StandardButton.Yes | QtWidgets.QMessageBox.StandardButton.No,
            QtWidgets.QMessageBox.StandardButton.No,
        )
        if answer != QtWidgets.QMessageBox.StandardButton.Yes:
            return

        if self._armed_entry is entry:
            self._armed_entry = None
        entry.remove_from_view_box(self.view_box)
        legend = self.timeseries_plot.getPlotItem().legend
        if legend is not None:
            legend.removeItem(entry.curve)
        self.timeseries_plot.removeItem(entry.curve)

        self.roi_stack.removeWidget(entry.tab_widget)
        entry.tab_widget.deleteLater()
        if entry.list_item is not None:
            list_row = self.roi_list.row(entry.list_item)
            if list_row >= 0:
                self.roi_list.takeItem(list_row)
            entry.list_item = None

        self.roi_entries.remove(entry)
        self._apply_interp_focus_visuals()
        self.statusBar().showMessage(f"„{entry.name}“ entfernt.", 4000)

    def _on_roi_color_clicked(self, entry: RoiEntry) -> None:
        color = QtWidgets.QColorDialog.getColor(
            QtGui.QColor(entry.color), self, f"Farbe für {entry.name}"
        )
        if not color.isValid():
            return
        entry.set_color(color.name())

    def _on_roi_place_toggled(self, entry: RoiEntry, checked: bool) -> None:
        if checked:
            for other in self.roi_entries:
                if other is not entry and other.btn_place.isChecked():
                    other.btn_place.blockSignals(True)
                    other.btn_place.setChecked(False)
                    other.btn_place.blockSignals(False)
                    # blockSignals oben unterdrueckt others eigenen
                    # _on_roi_place_toggled-Aufruf (der sonst _armed_entry
                    # aufraeumen wuerde) -- ohne dieses explizite Zuruecksetzen
                    # bliebe ein gerade laufender Start-/Ende-Erfassungsvorgang
                    # (siehe _on_roi_interp_capture) an "other" haengen: dessen
                    # Knopf zeigt weiter "...uebernehmen", ein spaeterer Klick
                    # wuerde dann die Geometrie vom FALSCHEN (aktuellen) Frame
                    # als Keyframe uebernehmen.
                    if other.interp_arm_start or other.interp_arm_end:
                        self._reset_interp_arm_state(other)
            self._apply_interp_focus_visuals()
            if self._ruler_armed:
                # Ruler- und ROI-Platzieren-Modus schliessen sich aus, sonst
                # wuerde ein Bildklick unbemerkt vom jeweils anderen Modus
                # "geschluckt" (siehe _on_scene_mouse_clicked).
                self._cancel_ruler_tool()
            if self._measure_armed:
                self._cancel_measure_tool()
            self._armed_entry = entry
            self.statusBar().showMessage(f"{entry.name}: Klick ins Bild zum Platzieren.")
        elif self._armed_entry is entry:
            self._armed_entry = None

    def _on_roi_apply_clicked(self, entry: RoiEntry, *_args) -> None:
        # *_args faengt den von spin.valueChanged(float) mitgesendeten neuen
        # Wert ab -- diese Methode braucht ihn nicht, da sie ohnehin alle
        # vier Felder direkt aus den Spinboxen liest (siehe unten).
        if self.recording is None:
            # Nicht-blockierender Statuszeilen-Hinweis statt eines
            # QMessageBox: diese Methode feuert live bei JEDER Aenderung
            # (auch einzelnen Tastendruecken/Pfeiltasten) der vier Spinboxen
            # -- ein modaler Dialog wuerde dabei bei jedem Versuch erneut
            # aufpoppen und die Eingabe unterbrechen.
            self.statusBar().showMessage("Bitte zuerst eine Messreihe laden.", 4000)
            return
        entry.place(entry.spin_x.value(), entry.spin_y.value(), entry.spin_width.value(), entry.spin_height.value())
        self._sync_roi_spinboxes(entry)
        self._recompute_curves(entries=[entry])

    def _on_roi_square_reset_clicked(self, entry: RoiEntry) -> None:
        if not entry.placed:
            return
        side = entry.width()
        cx, cy = entry.center()
        entry.place(cx, cy, side, side)
        self._sync_roi_spinboxes(entry)
        self._recompute_curves(entries=[entry])

    @staticmethod
    def _reset_interp_arm_state(entry: RoiEntry) -> None:
        """Bricht einen evtl. laufenden zweistufigen Erfassungs-Vorgang
        (Start-/Ende-Button stand gerade auf "Position uebernehmen") ab.
        Muss unbedingt aufgerufen werden, wenn sich interp_start/interp_end
        ausserhalb dieses Ablaufs aendern (z.B. Projekt laden) -- sonst wuerde
        ein spaeterer Klick auf den haengengebliebenen Button den frisch
        gesetzten Wert sofort wieder ueberschreiben."""
        entry.interp_arm_start = False
        entry.interp_arm_end = False
        entry.btn_interp_start.setText(INTERP_START_LABEL)
        entry.btn_interp_end.setText(INTERP_END_LABEL)

    def _apply_interp_focus_visuals(self) -> None:
        """Waehrend eine Verlaufs-Interpolation gerade per Start-/Ende-Knopf
        erfasst wird, ruecken alle ANDEREN Messbereiche visuell in den
        Hintergrund (stark verblasst), damit der gerade bearbeitete im Bild
        eindeutig im Fokus bleibt. Dessen eigene Deckkraft ist waehrend der
        Start-Erfassung voll, waehrend der (spaeteren) Ende-Erfassung leicht
        reduziert -- als Hinweis, dass die angezeigte Geometrie noch vom
        Start stammt, bis sie neu positioniert wird. Ohne laufende Erfassung
        (keine ROI aktuell armiert) sind alle wieder voll sichtbar."""
        focus_entry = next(
            (e for e in self.roi_entries if e.interp_arm_start or e.interp_arm_end), None
        )
        for entry in self.roi_entries:
            if focus_entry is None:
                opacity = 1.0
            elif entry is focus_entry:
                opacity = 1.0 if entry.interp_arm_start else 0.55
            else:
                opacity = 0.12
            entry.roi.setOpacity(opacity)
            entry.label.setOpacity(opacity)

    def _on_roi_show_temperature_toggled(self, entry: RoiEntry, checked: bool) -> None:
        entry.show_temperature = checked
        entry._refresh_label_text()

    def _on_roi_circular_toggled(self, entry: RoiEntry, checked: bool) -> None:
        entry.roi.is_circular = checked
        entry.roi.update()  # erzwingt Neuzeichnen mit dem geaenderten Umriss
        if self.recording is not None and entry.placed:
            self._recompute_curves(entries=[entry])
            self._update_roi_temperature_labels(self.current_index)

    def _on_roi_interp_toggled(self, entry: RoiEntry, checked: bool) -> None:
        entry.interp_enabled = checked
        entry.btn_interp_start.setEnabled(checked)
        entry.btn_interp_end.setEnabled(checked)
        self._reset_interp_arm_state(entry)
        self._apply_interp_focus_visuals()
        # Beim Deaktivieren bleibt der Messbereich einfach an seiner
        # aktuellen (zuletzt interpolierten) Geometrie stehen -- die
        # Start-/Ende-Keyframes bleiben erhalten, falls die Interpolation
        # spaeter wieder aktiviert wird.
        self._recompute_curves(entries=[entry])

    def _on_roi_interp_capture(self, entry: RoiEntry, is_start: bool) -> None:
        if self.recording is None:
            return
        button = entry.btn_interp_start if is_start else entry.btn_interp_end
        label = "Start" if is_start else "Ende"
        armed = entry.interp_arm_start if is_start else entry.interp_arm_end

        if not armed:
            # Phase 1: erst zum passenden Bild springen UND "Messbereich
            # setzen" aktivieren, damit der Nutzer den Messbereich dort per
            # Klick ins Bild direkt positionieren/erstellen kann -- auch
            # wenn er (z.B. bei einem frisch angelegten ROI) noch gar nicht
            # platziert ist, statt dass ein Klick auf diesen Knopf bis dahin
            # wirkungslos bleibt.
            capture_label = INTERP_START_CAPTURE_LABEL if is_start else INTERP_END_CAPTURE_LABEL
            # Ziel-Frame kommt aus der jeweiligen Spinbox (1-basiert, Standard
            # erstes/letztes Bild -- siehe _set_recording), NICHT mehr fest
            # aus dem globalen Auswertungsstart/-ende (Nutzerwunsch: Start-/
            # Ende-Frame der Interpolation pro Messbereich haendisch setzen).
            if is_start:
                target_frame = entry.spin_interp_start_frame.value() - 1
                self._step_frame(target_frame - self.current_index)
                entry.interp_arm_start = True
            else:
                target_frame = entry.spin_interp_end_frame.value() - 1
                self._step_frame(target_frame - self.current_index)
                entry.interp_arm_end = True
            button.setText(capture_label)
            entry.btn_place.setChecked(True)
            self._apply_interp_focus_visuals()
            self.statusBar().showMessage(
                f"{entry.name}: Messbereich für {label} im Bild anklicken/positionieren, dann "
                f"erneut auf „{capture_label}“ klicken.",
                6000,
            )
            return

        if not entry.placed:
            QtWidgets.QMessageBox.information(
                self,
                "Kein Messbereich gesetzt",
                f"Bitte zuerst den Messbereich für {label} im Bild anklicken/positionieren.",
            )
            return

        # Phase 2: aktuelle Geometrie als Keyframe uebernehmen.
        if is_start:
            entry.interp_arm_start = False
            entry.capture_interp_start(self.current_index)
        else:
            entry.interp_arm_end = False
            entry.capture_interp_end(self.current_index)
        button.setText(INTERP_START_LABEL if is_start else INTERP_END_LABEL)
        self._apply_interp_focus_visuals()
        self.statusBar().showMessage(f"{entry.name}: {label}-Position übernommen.", 4000)
        if entry.interp_start is not None and entry.interp_end is not None:
            if entry.interp_start_frame >= entry.interp_end_frame:
                # _interp_fraction() faengt start_idx >= end_idx defensiv mit
                # frac=0.0 ab (kein Absturz/keine Exception) -- das ROI bliebe
                # dabei aber unbemerkt fuer die gesamte Aufnahme auf der
                # Start-Position eingefroren. Die freien Start-/Ende-Spinboxen
                # (Nutzerwunsch: frei waehlbares Ziel-Bild statt zwingend
                # erstes/letztes Bild) erlauben diese Vertauschung leicht --
                # deshalb hier explizit warnen statt still falsch zu rechnen.
                QtWidgets.QMessageBox.warning(
                    self, "Ungültiger Bereich",
                    f"{entry.name}: Das Start-Bild (Nr. {entry.interp_start_frame + 1}) muss vor dem "
                    f"Ende-Bild (Nr. {entry.interp_end_frame + 1}) liegen -- sonst bleibt der "
                    "Messbereich während der gesamten Aufnahme auf der Start-Position eingefroren. "
                    "Bitte Start-/Ende-Bildnummer korrigieren.",
                )
            self._recompute_curves(entries=[entry])

    def _on_roi_region_changed(self, entry: RoiEntry, *_args) -> None:
        # Feuert laufend waehrend des Ziehens (nicht erst beim Loslassen wie
        # sigRegionChangeFinished) -- Kurve und Bild-Beschriftung sollen dabei
        # live mitlaufen statt erst nach dem Loslassen zu aktualisieren.
        self._sync_roi_spinboxes(entry)
        entry.sync_label_pos()
        if self.recording is not None and entry.placed:
            self._recompute_curves(entries=[entry])
            self._update_roi_temperature_labels(self.current_index, entries=[entry])

    def _on_roi_region_finished(self, entry: RoiEntry, *_args) -> None:
        if not entry.placed:
            return
        self._recompute_curves(entries=[entry])

    def _sync_roi_spinboxes(self, entry: RoiEntry) -> None:
        cx, cy = entry.center()
        for spin, value in (
            (entry.spin_x, cx),
            (entry.spin_y, cy),
            (entry.spin_width, entry.width()),
            (entry.spin_height, entry.height()),
        ):
            self._set_widget_value(spin, value)
        self._update_roi_mm_label(entry)

    def _update_roi_mm_label(self, entry: RoiEntry) -> None:
        if entry.mm_label is None:
            return
        if self._px_to_mm is None or not entry.placed:
            entry.mm_label.setVisible(False)
            return
        w_mm = entry.width() * self._px_to_mm
        h_mm = entry.height() * self._px_to_mm
        entry.mm_label.setText(f"≈ {self._format_de(w_mm)} × {self._format_de(h_mm)} mm")
        entry.mm_label.setVisible(True)

    def _recompute_curves(self, entries: list[RoiEntry] | None = None) -> None:
        if self.recording is None:
            return
        entries = entries if entries is not None else self.roi_entries
        unix = self.recording.unix_seconds()
        shape = self.recording.shape
        for entry in entries:
            if not entry.placed:
                continue
            if entry.is_interp_ready():
                values = np.empty(len(unix), dtype=np.float32)
                for i in range(len(unix)):
                    frac = self._interp_fraction(i, entry.interp_start_frame, entry.interp_end_frame)
                    x, y, w, h = entry.interp_rect(frac)
                    row0, row1, col0, col1 = bounds_px_for(x, y, w, h, shape)
                    values[i] = entry.average(self.recording.frames[i, row0:row1, col0:col1], row0, row1, col0, col1)
            else:
                row0, row1, col0, col1 = entry.bounds_px(shape)
                values = entry.average(self.recording.frames[:, row0:row1, col0:col1], row0, row1, col0, col1)
            entry.curve.setData(unix, values)
            entry.curve.setVisible(entry.is_visible_checked())

    # ---------------------------------------------------------- Legende
    def _apply_colormap(self) -> None:
        name = COLORMAPS[self.combo_cmap.currentIndex()][1]
        # skipCache=True: pg.colormap.get() liefert fuer denselben Namen
        # sonst immer dieselbe GECACHTE Instanz zurueck, und ColorMap.
        # reverse() aendert sie IN-PLACE (wie list.reverse()). Ohne
        # skipCache wuerde jeder Aufruf hier den globalen Cache kumulativ
        # weiterdrehen, statt deterministisch von der originalen
        # Reihenfolge auszugehen. (Ein manuell aus cmap.pos/cmap.color
        # zusammengebautes ColorMap ist KEINE brauchbare Alternative --
        # der Konstruktor interpretiert bereits normierte float-Farbwerte
        # dabei fälschlich nochmal als Byte-Werte und macht daraus eine
        # fast schwarze/durchsichtige LUT.)
        cmap = pg.colormap.get(name, skipCache=True)
        want_reversed = (name in COLORMAPS_BASE_REVERSED) != self.chk_cmap_invert.isChecked()
        if want_reversed:
            cmap.reverse()
        self.histogram.gradient.setColorMap(cmap)

    def _on_colormap_changed(self, _index: int) -> None:
        self._apply_colormap()

    def _on_colormap_invert_toggled(self, _checked: bool) -> None:
        self._apply_colormap()

    def _on_level_mode_changed(self, _button: QtWidgets.QAbstractButton | None, checked: bool) -> None:
        if not checked:
            return
        manual = self.radio_level_manual.isChecked()
        self.spin_level_min.setEnabled(manual)
        self.spin_level_max.setEnabled(manual)
        # Pro-Bild/Gesamte-Serie sind nur sinnvoll bedienbar, solange
        # "Automatisch" aktiv ist -- sonst mit "Manuell" verwechselbar.
        self.radio_level_per_frame.setEnabled(not manual)
        self.radio_level_global.setEnabled(not manual)
        self._show_frame(self.current_index)

    def _on_level_spin_changed(self) -> None:
        if self._level_mode() != "manual":
            return
        lo, hi = self.spin_level_min.value(), self.spin_level_max.value()
        if hi <= lo:
            return
        self.histogram.setLevels(lo, hi)
        self.image_item.setLevels((lo, hi))

    def _on_histogram_levels_changed(self) -> None:
        lo, hi = self.histogram.getLevels()
        self._set_level_spins(lo, hi)

    # ------------------------------------------------------- Maßstab (Lineal)
    def _start_ruler_tool(self) -> None:
        if self.recording is None:
            QtWidgets.QMessageBox.information(self, "Keine Daten", "Bitte zuerst eine Messreihe laden.")
            return
        if self._armed_entry is not None:
            # Siehe _on_roi_place_toggled: beide Modi schliessen sich aus.
            self._armed_entry.btn_place.blockSignals(True)
            self._armed_entry.btn_place.setChecked(False)
            self._armed_entry.btn_place.blockSignals(False)
            self._armed_entry = None
        if self._measure_armed:
            self._cancel_measure_tool()
        self._ruler_armed = True
        self._ruler_start = None
        # Eine evtl. noch von der letzten Messung angezeigte, gueltige Linie/
        # mm-Beschriftung bleibt hier bewusst sichtbar -- sie wird erst beim
        # tatsaechlichen ersten Klick (siehe _handle_ruler_click) durch die
        # neue Messung ueberschrieben. So geht die Anzeige des noch aktiven
        # Massstabs nicht schon durch blosses Oeffnen des Werkzeugs verloren.
        self.statusBar().showMessage("Maßstab: Startpunkt der Referenzlinie im Bild anklicken.")

    def _cancel_ruler_tool(self) -> None:
        if self._ruler_start is not None:
            # Es wurde bereits ein neuer Startpunkt gesetzt (der die Daten
            # einer evtl. zuvor gueltigen Linie schon überschrieben hat) --
            # dieser unvollstaendige Rest ergibt ausgeblendet mehr Sinn.
            self._hide_ruler_visuals()
        self._ruler_armed = False
        self._ruler_start = None

    def _hide_ruler_visuals(self) -> None:
        if self._ruler_preview_marker is not None:
            self._ruler_preview_marker.setVisible(False)
        if self._ruler_line is not None:
            self._ruler_line.setVisible(False)
        if self._ruler_text is not None:
            self._ruler_text.setVisible(False)

    def _update_ruler_color_swatch(self) -> None:
        self.btn_ruler_color.setStyleSheet(
            f"background-color:{self._ruler_color}; border:1px solid #333; border-radius:4px;"
        )

    def _apply_ruler_color(self) -> None:
        if self._ruler_line is not None:
            self._ruler_line.setPen(pg.mkPen(self._ruler_color, width=3))
        if self._ruler_text is not None:
            self._ruler_text.setColor(self._ruler_color)

    def _on_ruler_color_clicked(self) -> None:
        color = QtWidgets.QColorDialog.getColor(QtGui.QColor(self._ruler_color), self, "Farbe der Maßstablinie")
        if not color.isValid():
            return
        self._ruler_color = color.name()
        self._update_ruler_color_swatch()
        self._apply_ruler_color()

    def _clear_ruler_scale(self) -> None:
        self._px_to_mm = None
        self._ruler_mm_value = None
        self._hide_ruler_visuals()
        self._refresh_scale_label()
        for entry in self.roi_entries:
            self._update_roi_mm_label(entry)

    def _refresh_scale_label(self) -> None:
        has_scale = self._px_to_mm is not None
        if has_scale:
            self.scale_label.setText(f"1 px ≈ {self._format_de(self._px_to_mm, 4)} mm")
        else:
            self.scale_label.setText("Kein Maßstab definiert.")
        self.btn_scale_clear.setEnabled(has_scale)
        can_measure = has_scale and self.recording is not None
        self.btn_measure.setEnabled(can_measure)
        self.act_measure.setEnabled(can_measure)
        if not has_scale:
            # Ohne Maßstab ergibt eine laufende/angezeigte Messung keinen Sinn
            # mehr (siehe _handle_measure_click, das ebenfalls von _px_to_mm
            # abhaengt).
            self._cancel_measure_tool()
            self._hide_measure_visuals()

    def _handle_ruler_click(self, event) -> None:
        if event.button() != QtCore.Qt.LeftButton:
            self._cancel_ruler_tool()
            self.statusBar().showMessage("Maßstab-Werkzeug abgebrochen.", 3000)
            return
        scene_pos = event.scenePos()
        if not self.view_box.sceneBoundingRect().contains(scene_pos):
            return
        view_pos = self.view_box.mapSceneToView(scene_pos)
        point = (view_pos.x(), view_pos.y())

        if self._ruler_start is None:
            self._ruler_start = point
            # Nur eine einfache, nicht interaktive Vorschau waehrend der
            # Klick-Klick-Erstellung -- die fertige, ziehbare Linie
            # (self._ruler_line) entsteht erst unten nach Bestaetigung der
            # Laenge.
            if self._ruler_preview_marker is None:
                self._ruler_preview_marker = pg.PlotDataItem(
                    pen=pg.mkPen(self._ruler_color, width=3),
                    symbol="o",
                    symbolSize=8,
                    symbolBrush=self._ruler_color,
                    symbolPen="#ffffff",
                )
                self._ruler_preview_marker.setZValue(11)
                self.view_box.addItem(self._ruler_preview_marker)
            if self._ruler_text is None:
                self._ruler_text = pg.TextItem(color=self._ruler_color, anchor=(0.5, 0), fill=(0, 0, 0, 160))
                self._ruler_text.setZValue(11)
                self.view_box.addItem(self._ruler_text)
            if self._ruler_line is not None:
                self._ruler_line.setVisible(False)
            self._ruler_text.setVisible(False)
            self._ruler_preview_marker.setData([point[0]], [point[1]])
            self._ruler_preview_marker.setVisible(True)
            self.statusBar().showMessage("Maßstab: jetzt den Endpunkt der Referenzlinie anklicken.")
            return

        start = self._ruler_start
        end = point
        self._ruler_preview_marker.setData([start[0], end[0]], [start[1], end[1]])
        pixel_distance = ((end[0] - start[0]) ** 2 + (end[1] - start[1]) ** 2) ** 0.5
        self._ruler_armed = False
        self._ruler_start = None
        # Linie bleibt sichtbar (auch waehrend des folgenden, blockierenden
        # Eingabedialogs), damit der Nutzer tatsaechlich sieht, welche Strecke
        # er gerade in mm beziffert -- vorher wurde sie hier bereits wieder
        # ausgeblendet, sodass nie eine sichtbare Linie zu sehen war.
        if pixel_distance < 1e-6:
            self._hide_ruler_visuals()
            QtWidgets.QMessageBox.information(
                self, "Maßstab", "Start- und Endpunkt liegen zu nah beieinander, bitte erneut versuchen."
            )
            return

        length_dialog = RulerLengthDialog(self, current_mm=self._ruler_mm_value or 10.0)
        if length_dialog.exec() != QtWidgets.QDialog.DialogCode.Accepted:
            self._hide_ruler_visuals()
            return
        mm_value = length_dialog.mm_value()

        self._ruler_preview_marker.setVisible(False)
        self._create_or_move_ruler_line(start, end)
        self._px_to_mm = mm_value / pixel_distance
        self._ruler_mm_value = mm_value
        self._update_ruler_text_position()
        self._refresh_scale_label()
        for entry in self.roi_entries:
            self._update_roi_mm_label(entry)
        self.statusBar().showMessage(
            f"Maßstab gesetzt: 1 px ≈ {self._format_de(self._px_to_mm, 4)} mm", 5000
        )

    def _create_or_move_ruler_line(self, start: tuple[float, float], end: tuple[float, float]) -> None:
        """Erzeugt die fertige, ziehbare Maßstab-Linie (Punkt 11) oder setzt
        eine bereits bestehende neu -- ein LineSegmentROI statt der zuvor
        starren PlotDataItem, damit sich beide Endpunkte per Maus nachträglich
        verschieben lassen, ohne den Maßstab komplett neu zeichnen zu müssen."""
        if self._ruler_line is not None:
            self.view_box.removeItem(self._ruler_line)
        self._ruler_line = pg.LineSegmentROI(
            positions=[list(start), list(end)], pen=pg.mkPen(self._ruler_color, width=3)
        )
        self._ruler_line.setZValue(11)
        self.view_box.addItem(self._ruler_line)
        self._ruler_line.sigRegionChangeFinished.connect(self._on_ruler_line_dragged)

    def _on_ruler_line_dragged(self) -> None:
        """Nach dem Ziehen eines Endpunkts (Punkt 11): die reale Länge
        (self._ruler_mm_value) bleibt fest, px-zu-mm wird aus der neuen
        Pixel-Distanz neu berechnet -- entspricht einer Nachkalibrierung ohne
        den Maßstab neu setzen zu müssen."""
        if self._ruler_line is None or self._ruler_mm_value is None:
            return
        p1, p2 = self._ruler_line.listPoints()
        pixel_distance = (p2 - p1).length()
        if pixel_distance < 1e-6:
            # Degenerierter Zustand (beide Punkte uebereinander) -- bisherige
            # Kalibrierung beibehalten, statt durch Null zu teilen.
            return
        self._px_to_mm = self._ruler_mm_value / pixel_distance
        self._update_ruler_text_position()
        self._refresh_scale_label()
        for entry in self.roi_entries:
            self._update_roi_mm_label(entry)

    def _update_ruler_text_position(self) -> None:
        if self._ruler_line is None or self._ruler_text is None or self._ruler_mm_value is None:
            return
        p1, p2 = self._ruler_line.listPoints()
        mid = (p1 + p2) / 2
        self._ruler_text.setText(f"{self._format_de(self._ruler_mm_value, 1)} mm")
        self._ruler_text.setPos(mid.x(), mid.y())
        self._ruler_text.setVisible(True)

    def _ruler_hit_test(self, scene_pos: QtCore.QPointF) -> bool:
        """Prueft, ob scene_pos auf der Maßstab-Linie oder ihrer mm-
        Beschriftung liegt -- fuer die Doppelklick-Bearbeitung (Punkt 11)."""
        if self._ruler_line is not None and self._ruler_line.isVisible():
            shape = self._ruler_line.mapToScene(self._ruler_line.shape())
            if shape.contains(scene_pos):
                return True
        if self._ruler_text is not None and self._ruler_text.isVisible():
            if self._ruler_text.sceneBoundingRect().contains(scene_pos):
                return True
        return False

    def _edit_ruler_length(self) -> None:
        """Doppelklick auf die Maßstab-Linie/-Beschriftung (Punkt 11): erlaubt,
        die reale Länge (mm) direkt zu ändern, OHNE die aktuellen Endpunkte
        anzutasten -- Gegenstück zum Ziehen der Endpunkte (dort bleibt die
        Länge fest, hier bleiben die Endpunkte fest)."""
        if self._ruler_line is None or self._ruler_mm_value is None:
            return
        length_dialog = RulerLengthDialog(self, current_mm=self._ruler_mm_value)
        if length_dialog.exec() != QtWidgets.QDialog.DialogCode.Accepted:
            return
        p1, p2 = self._ruler_line.listPoints()
        pixel_distance = (p2 - p1).length()
        if pixel_distance < 1e-6:
            return
        self._ruler_mm_value = length_dialog.mm_value()
        self._px_to_mm = self._ruler_mm_value / pixel_distance
        self._update_ruler_text_position()
        self._refresh_scale_label()
        for entry in self.roi_entries:
            self._update_roi_mm_label(entry)
        self.statusBar().showMessage(
            f"Maßstab aktualisiert: 1 px ≈ {self._format_de(self._px_to_mm, 4)} mm", 5000
        )

    # ------------------------------------------------------- Messen (nutzt Maßstab)
    def _start_measure_tool(self) -> None:
        if self.recording is None:
            QtWidgets.QMessageBox.information(self, "Keine Daten", "Bitte zuerst eine Messreihe laden.")
            return
        if self._px_to_mm is None:
            QtWidgets.QMessageBox.information(
                self, "Kein Maßstab", "Bitte zuerst über \"Festlegen…\" einen Maßstab definieren."
            )
            return
        if self._armed_entry is not None:
            self._armed_entry.btn_place.blockSignals(True)
            self._armed_entry.btn_place.setChecked(False)
            self._armed_entry.btn_place.blockSignals(False)
            self._armed_entry = None
        if self._ruler_armed:
            self._cancel_ruler_tool()
        self._measure_armed = True
        self._measure_start = None
        self.statusBar().showMessage("Messen: Startpunkt der Strecke im Bild anklicken.")

    def _cancel_measure_tool(self) -> None:
        if self._measure_start is not None:
            self._hide_measure_visuals()
        self._measure_armed = False
        self._measure_start = None

    def _hide_measure_visuals(self) -> None:
        if self._measure_preview_marker is not None:
            self._measure_preview_marker.setVisible(False)
        if self._measure_line is not None:
            self._measure_line.setVisible(False)
        if self._measure_text is not None:
            self._measure_text.setVisible(False)

    def _handle_measure_click(self, event) -> None:
        if event.button() != QtCore.Qt.LeftButton:
            self._cancel_measure_tool()
            self.statusBar().showMessage("Mess-Werkzeug abgebrochen.", 3000)
            return
        scene_pos = event.scenePos()
        if not self.view_box.sceneBoundingRect().contains(scene_pos):
            return
        view_pos = self.view_box.mapSceneToView(scene_pos)
        point = (view_pos.x(), view_pos.y())

        if self._measure_start is None:
            self._measure_start = point
            if self._measure_preview_marker is None:
                self._measure_preview_marker = pg.PlotDataItem(
                    pen=pg.mkPen(self._measure_color, width=3),
                    symbol="o",
                    symbolSize=8,
                    symbolBrush=self._measure_color,
                    symbolPen="#ffffff",
                )
                self._measure_preview_marker.setZValue(11)
                self.view_box.addItem(self._measure_preview_marker)
            if self._measure_text is None:
                self._measure_text = pg.TextItem(color=self._measure_color, anchor=(0.5, 0), fill=(0, 0, 0, 160))
                self._measure_text.setZValue(11)
                self.view_box.addItem(self._measure_text)
            if self._measure_line is not None:
                self._measure_line.setVisible(False)
            self._measure_text.setVisible(False)
            self._measure_preview_marker.setData([point[0]], [point[1]])
            self._measure_preview_marker.setVisible(True)
            self.statusBar().showMessage("Messen: jetzt den Endpunkt der Strecke anklicken.")
            return

        start = self._measure_start
        end = point
        self._measure_armed = False
        self._measure_start = None
        pixel_distance = ((end[0] - start[0]) ** 2 + (end[1] - start[1]) ** 2) ** 0.5
        if pixel_distance < 1e-6 or self._px_to_mm is None:
            self._hide_measure_visuals()
            return

        self._measure_preview_marker.setVisible(False)
        self._create_or_move_measure_line(start, end)
        mm_value = pixel_distance * self._px_to_mm
        self._update_measure_text_position(mm_value)
        self.statusBar().showMessage(
            f"Gemessen: {self._format_de(mm_value, 2)} mm ({self._format_de(pixel_distance, 1)} px) "
            "-- Maßstab dabei unverändert.",
            6000,
        )

    def _create_or_move_measure_line(self, start: tuple[float, float], end: tuple[float, float]) -> None:
        if self._measure_line is not None:
            self.view_box.removeItem(self._measure_line)
        self._measure_line = pg.LineSegmentROI(
            positions=[list(start), list(end)], pen=pg.mkPen(self._measure_color, width=3)
        )
        self._measure_line.setZValue(11)
        self.view_box.addItem(self._measure_line)
        self._measure_line.sigRegionChangeFinished.connect(self._on_measure_line_dragged)

    def _on_measure_line_dragged(self) -> None:
        """Endpunkte der Mess-Strecke nachtraeglich verschoben: mm-Anzeige mit
        dem AKTUELLEN Maßstab neu berechnen (im Unterschied zur Lineal-Linie
        wird hier nie in _px_to_mm zurückgerechnet -- Messen ist rein lesend)."""
        if self._measure_line is None or self._px_to_mm is None:
            return
        p1, p2 = self._measure_line.listPoints()
        pixel_distance = (p2 - p1).length()
        mm_value = pixel_distance * self._px_to_mm
        self._update_measure_text_position(mm_value)

    def _update_measure_text_position(self, mm_value: float) -> None:
        if self._measure_line is None or self._measure_text is None:
            return
        p1, p2 = self._measure_line.listPoints()
        mid = (p1 + p2) / 2
        self._measure_text.setText(f"{self._format_de(mm_value, 2)} mm")
        self._measure_text.setPos(mid.x(), mid.y())
        self._measure_text.setVisible(True)

    # ------------------------------------------------------- Maus/Bild
    def _on_scene_mouse_clicked(self, event) -> None:
        if self.recording is None:
            return

        if self._ruler_armed:
            self._handle_ruler_click(event)
            return

        if self._measure_armed:
            self._handle_measure_click(event)
            return

        if event.double() and self._ruler_hit_test(event.scenePos()):
            self._edit_ruler_length()
            return

        if self._armed_entry is not None:
            # Ein ROI wartet auf Platzierung per Linksklick -- andere Klicks
            # (z.B. ein versehentlicher Rechtsklick) sollen in der
            # Zwischenzeit nicht zusaetzlich die Live-Ansicht veraendern.
            if event.button() != QtCore.Qt.LeftButton:
                return
            scene_pos = event.scenePos()
            if not self.view_box.sceneBoundingRect().contains(scene_pos):
                return
            view_pos = self.view_box.mapSceneToView(scene_pos)
            entry = self._armed_entry
            entry.place(view_pos.x(), view_pos.y(), entry.spin_width.value(), entry.spin_height.value())
            self._sync_roi_spinboxes(entry)
            entry.btn_place.setChecked(False)
            self._armed_entry = None
            self._recompute_curves(entries=[entry])
            return

        if event.button() == QtCore.Qt.RightButton:
            if not self._live_pinned:
                return
            self._live_pinned = False
            row_col = self._pixel_at_scene_pos(event.scenePos())
            if row_col is not None:
                self._update_live_cursor(*row_col)
            elif self._hover_row is not None and self._hover_col is not None:
                # Rechtsklick lag ausserhalb des Bildes -- Fixierung trotzdem
                # aufheben und den "(fixiert)"-Hinweis im Label entfernen,
                # sonst bleibt er stehen, bis die Maus zufaellig auf ein
                # anderes Pixel als das zuletzt fixierte wandert.
                self.live_label.setText(
                    f"Cursor-Pixel: Zeile {self._hover_row}, Spalte {self._hover_col}"
                )
            self.statusBar().showMessage("Live-Ansicht folgt wieder dem Mauscursor.", 3000)
            return

        if event.button() == QtCore.Qt.LeftButton:
            row_col = self._pixel_at_scene_pos(event.scenePos())
            if row_col is None:
                return
            row, col = row_col
            self._live_pinned = True
            self._update_live_cursor(row, col)
            self.live_label.setText(
                f"Cursor-Pixel: Zeile {row}, Spalte {col}  (fixiert – Rechtsklick ins Bild zum Lösen)"
            )
            self.statusBar().showMessage(
                f"Live-Ansicht fixiert auf Zeile {row}, Spalte {col}. Rechtsklick ins Bild löst die Fixierung wieder.",
                5000,
            )

    def _on_scene_mouse_moved(self, scene_pos: QtCore.QPointF) -> None:
        if self.recording is None or self._live_pinned:
            return
        row_col = self._pixel_at_scene_pos(scene_pos)
        if row_col is None:
            return
        row, col = row_col
        if (row, col) == (self._hover_row, self._hover_col):
            return
        self._update_live_cursor(row, col)

    def _pixel_at_scene_pos(self, scene_pos: QtCore.QPointF) -> tuple[int, int] | None:
        if self.recording is None:
            return None
        if not self.view_box.sceneBoundingRect().contains(scene_pos):
            return None
        view_pos = self.view_box.mapSceneToView(scene_pos)
        rows, cols = self.recording.shape
        col = int(np.floor(view_pos.x()))
        row = int(np.floor(view_pos.y()))
        if not (0 <= row < rows and 0 <= col < cols):
            return None
        return row, col

    def _live_cursor_bounds(self, row: int, col: int) -> tuple[int, int, int, int]:
        """Auf das Bild geclippter row0:row1, col0:col1-Bereich um das
        Cursor-Pixel, dessen Kantenlaenge ueber "Werkzeuge > Live-Cursor-
        Bereichsgröße" einstellbar ist (Standard: 1x1, d.h. genau dieses
        eine Pixel -- bisheriges Verhalten).

        "before"/"after" statt eines einzelnen "half": fuer die -- ausschliesslich
        ungeraden -- waehlbaren Groessen (1/3/5/7/9/11/13/15) ist before==after-1==half,
        das Cursor-Pixel liegt also immer exakt in der Mitte des Bereichs."""
        size = self._live_cursor_kernel_size
        before = size // 2
        after = size - before
        rows, cols = self.recording.shape
        row0 = max(0, row - before)
        row1 = min(rows, row + after)
        col0 = max(0, col - before)
        col1 = min(cols, col + after)
        return row0, row1, col0, col1

    def _live_cursor_series(self, row: int, col: int) -> np.ndarray:
        row0, row1, col0, col1 = self._live_cursor_bounds(row, col)
        if self._live_cursor_kernel_size == 1:
            return self.recording.frames[:, row, col]
        return self.recording.frames[:, row0:row1, col0:col1].mean(axis=(1, 2))

    def _live_cursor_value(self, idx: int, row: int, col: int) -> float:
        row0, row1, col0, col1 = self._live_cursor_bounds(row, col)
        if self._live_cursor_kernel_size == 1:
            return float(self.recording.frames[idx, row, col])
        return float(self.recording.frames[idx, row0:row1, col0:col1].mean())

    def _on_live_cursor_kernel_selected(self, size: int) -> None:
        self._live_cursor_kernel_size = size
        self._settings.setValue("live_cursor/kernel_size", size)
        if self._hover_row is not None and self._hover_col is not None:
            self._update_live_cursor(self._hover_row, self._hover_col)

    def _update_live_cursor(self, row: int, col: int) -> None:
        self._hover_row, self._hover_col = row, col
        values = self._live_cursor_series(row, col)
        unix = self.recording.unix_seconds()
        self.live_curve.setData(unix, values)
        if self.chk_show_live_in_timeseries.isChecked():
            self.timeseries_live_curve.setData(unix, values)
        suffix = "" if self._live_cursor_kernel_size == 1 else (
            f" ({self._live_cursor_kernel_size}×{self._live_cursor_kernel_size}-Mittel)"
        )
        self.live_label.setText(f"Cursor-Pixel: Zeile {row}, Spalte {col}{suffix}")
        self.live_cursor_marker.setData([col + 0.5], [row + 0.5])
        self.live_cursor_marker.setVisible(True)
        self._update_status_bar()

    def _on_show_live_in_timeseries_toggled(self, checked: bool) -> None:
        if checked:
            if self._hover_row is not None and self._hover_col is not None:
                values = self._live_cursor_series(self._hover_row, self._hover_col)
                self.timeseries_live_curve.setData(self.recording.unix_seconds(), values)
            self.timeseries_legend.addItem(self.timeseries_live_curve, "Live (Cursor)")
        else:
            self.timeseries_legend.removeItem(self.timeseries_live_curve)
        self.timeseries_live_curve.setVisible(checked)

    def _update_status_bar(self) -> None:
        if self.recording is None:
            return
        idx = self.current_index
        ts = self.recording.timestamps[idx].strftime("%Y-%m-%d %H:%M:%S")
        runtime = self._format_runtime(
            (self.recording.timestamps[idx] - self.recording.timestamps[0]).total_seconds()
        )
        msg = f"Frame {idx + 1}/{self.recording.n_frames}  |  {ts}  |  Laufzeit: {runtime}"
        if self._hover_row is not None and self._hover_col is not None:
            val = self._live_cursor_value(idx, self._hover_row, self._hover_col)
            msg += f"  |  Cursor: Zeile {self._hover_row}, Spalte {self._hover_col} = {val:.2f} °C"
        self.statusBar().showMessage(msg)
        self._update_live_cursor_label()

    def _update_live_cursor_label(self) -> None:
        """Zeigt die Temperatur DES AKTUELLEN FRAMES am Cursor-Kreuz direkt
        im Thermobild an -- wird sowohl bei Mausbewegung (_update_live_cursor)
        als auch bei jedem Frame-Wechsel (_show_frame -> _update_status_bar)
        aufgerufen, damit der Wert waehrend der Wiedergabe automatisch
        mitlaeuft, ohne dass die Maus bewegt werden muss."""
        if self.recording is None or self._hover_row is None or self._hover_col is None:
            self.live_cursor_label.setVisible(False)
            return
        val = self._live_cursor_value(self.current_index, self._hover_row, self._hover_col)
        self.live_cursor_label.setText(f"{val:.1f} °C")
        self.live_cursor_label.setPos(self._hover_col + 0.6, self._hover_row - 0.6)
        self.live_cursor_label.setVisible(True)

    # --------------------------------------------------------- Export
    @staticmethod
    def _format_de(value: float, decimals: int = 1) -> str:
        return f"{value:.{decimals}f}".replace(".", ",")

    @classmethod
    def _format_csv_number(cls, value: float) -> str:
        # Deutsches Zahlenformat (Dezimalkomma), passend zum ';'-Trennzeichen
        # und zum Format der eingelesenen CSV-Rohdaten -- damit die Datei in
        # einem deutsch lokalisierten Excel ohne Nacharbeit direkt aufgeht.
        return cls._format_de(value, 3)

    @staticmethod
    def _format_relative_runtime(seconds: float) -> str:
        # Relative Laufzeit ab 00:00:00 (Punkt 6); Stunden bewusst unbegrenzt
        # (nicht auf 24h umbrechend), da Aufnahmen laenger als einen Tag
        # dauern koennen.
        total = int(round(max(0.0, seconds)))
        hours, rem = divmod(total, 3600)
        minutes, secs = divmod(rem, 60)
        return f"{hours:02d}:{minutes:02d}:{secs:02d}"

    def _format_runtime(self, seconds: float) -> str:
        """Formatiert eine Laufzeit (Sekunden seit Aufnahmebeginn) gemaess
        dem aktuell gewaehlten Laufzeit-Format (siehe _apply_runtime_unit)
        -- hh:mm:ss (Standard) oder eine fortlaufende Dezimalzahl in
        Sekunden/Minuten/Stunden. Gemeinsam genutzt von Statuszeile,
        Video-/Bildstapel-Export-Overlay UND CSV-Export ("Laufzeit"-Spalte),
        damit die Laufzeit ueberall im Programm im selben, vom Nutzer
        gewaehlten Format erscheint (Nutzerwunsch: "dritte Zeitachse")."""
        if self._runtime_unit == "hhmmss":
            return self._format_relative_runtime(seconds)
        divisor = _RUNTIME_UNIT_DIVISORS[self._runtime_unit]
        decimals = 2 if self._runtime_unit == "s" else 3
        return self._format_de(max(0.0, seconds) / divisor, decimals)

    def _runtime_export_value(self, seconds: float) -> str | float:
        """Laufzeit-Wert fuer die "Laufzeit"-Tabellenspalte des Werte-Exports
        (_export_csv): bei hh:mm:ss zwangsläufig ein String, sonst eine
        ECHTE Zahl (nicht wie _format_runtime() ein komma-formatierter
        String) -- Zeitstempel/Messwerte landen im Zeilen-Array ebenfalls
        unformatiert und werden erst beim eigentlichen Schreiben je nach
        Format aufbereitet (_format_csv_number fuer CSV/Text, direkt fuer
        JSON). Ohne diese Trennung wuerde der JSON-Export bei numerischem
        Laufzeit-Format einen komma-formatierten String statt einer echten
        JSON-Zahl enthalten -- genau das, was diese "dritte Zeitachse"
        (Nutzerwunsch) fuer die Weiterverarbeitung in anderer Software
        vermeiden soll."""
        if self._runtime_unit == "hhmmss":
            return self._format_relative_runtime(seconds)
        divisor = _RUNTIME_UNIT_DIVISORS[self._runtime_unit]
        decimals = 2 if self._runtime_unit == "s" else 3
        return round(max(0.0, seconds) / divisor, decimals)

    @staticmethod
    def _scaled_size(widget: QtWidgets.QWidget, scale: float, align: int = 1) -> tuple[int, int]:
        """Zielgroesse in Geraete-Pixeln fuer den Export eines Widgets mit
        gegebenem DPI-Skalierungsfaktor -- gemeinsam genutzt von
        _render_widget_image (Raster) und _save_widget_svg (Vektor), damit
        beide garantiert dieselbe Groesse fuer dasselbe Widget/denselben
        Faktor berechnen.

        align > 1 rundet Breite/Hoehe zusaetzlich auf ein Vielfaches davon
        AUF (nie ab, damit nichts abgeschnitten wird) -- genutzt fuer den
        Video-Export (align=16), damit ffmpeg (macro_block_size=16) das
        Bild nicht selbst mit einer Warnung nachtraeglich vergroessern muss
        (siehe _export_video)."""
        size = widget.size()
        width = max(1, round(size.width() * scale))
        height = max(1, round(size.height() * scale))
        if align > 1:
            width = -(-width // align) * align
            height = -(-height // align) * align
        return width, height

    @contextlib.contextmanager
    def _scaled_export_visuals(self, scale: float, pen_scale: float | None = None):
        """Skaliert Linienbreiten (Messbereich-Rahmen/-Kurven, Massstabslinie,
        Fadenkreuz/Live-Cursor, gestrichelte Frame-Marker) UND die Legenden-
        Schrift kurzzeitig um den Export-Skalierungsfaktor hoch und stellt
        sie danach zuverlaessig wieder her.

        pen_scale (Default: scale) steuert NUR die Stiftbreiten separat von
        der sonstigen Skalierung (Legende, Zielgroesse) -- fuer den SVG-
        Export bewusst kleiner gewaehlt als scale: Vektor-Linien (SVG,
        "non-scaling-stroke", scharfkantig, voll deckend) wirken bei
        IDENTISCHER Pixelbreite optisch deutlich kraeftiger/dicker als
        die entsprechende Raster-Linie (leicht antialiast/weicher) --
        Bugreport: "Linien noch etwas (zu) dick" ausschliesslich im SVG-
        Export, waehrend der Raster-Export (identische Stiftbreiten-
        Berechnung) explizit als passend bestaetigt wurde.

        Stiftbreiten: noetig, weil sie in pyqtgraph "kosmetisch" sind
        (konstante GERAETE-Pixelbreite, unabhaengig vom Painter-Transform) --
        beim Rendern auf eine groessere Zielflaeche (_render_widget_image/
        _save_widget_svg, ueber QGraphicsScene.render() mit vergroessertem
        Zielrechteck) blieben Linien dadurch ohne diese Anpassung im
        Verhaeltnis zur Bildgroesse unlesbar duenn.

        Andere SCHRIFTGROESSEN (Achsen-Ticks, ROI-Beschriftung, Massstabs-/
        Live-Cursor-Text) werden bewusst NICHT angefasst: normaler Text
        unterliegt -- anders als kosmetische Stifte -- dem Painter-Transform
        ganz normal und wird von QGraphicsScene.render() dadurch bereits
        automatisch im exakt richtigen Verhaeltnis mitskaliert; zusaetzliches
        explizites Hochskalieren wuerde sich damit zu einem quadratischen
        Faktor addieren (Bugreport: Achsenbeschriftung bei hoher Export-DPI
        unbrauchbar riesig).

        Die LEGENDE ist die einzige Ausnahme: pyqtgraphs LegendItem setzt
        das QGraphicsItem-Flag ItemIgnoresTransformations (fuer konstante
        Lesbarkeit unabhaengig vom Zoom der Kurve) -- dadurch ignoriert sie
        auch GENAU den Skalierungs-Transform, den QGraphicsScene.render()
        fuer den Export aufspannt, und bliebe ohne explizites Hochskalieren
        bei jeder Export-Aufloesung bei ihrer winzigen Bildschirmgroesse
        (Bugreport: "Legendenskalierung passt nicht mehr"). Skaliert wird
        dafuer NICHT die Schrift selbst (label.setText(..., size=...) --
        frueherer Versuch, siehe Git-Historie: liess bei WIEDERHOLTEM
        Export die Legende jedes Mal ein Stueck weiter/permanent
        anwachsen, offenbar weil pyqtgraphs QGraphicsGridLayout eine
        Restaurierung ueber erneutes label.setText()+updateSize() nicht
        zuverlaessig vollstaendig rueckgaengig macht), sondern per direktem
        QGraphicsItem.setTransform() auf die Legende selbst -- das bleibt
        von ItemIgnoresTransformations UNBERUEHRT (nur die geerbten
        Transforms der Szene/des Views werden ignoriert, die EIGENE
        Transform des Items wird weiterhin angewendet) und ist als reine
        Matrix-Zuweisung garantiert exakt und verlustfrei reversibel."""
        if scale <= 1.0:
            yield
            return
        ps = scale if pen_scale is None else pen_scale

        for entry in self.roi_entries:
            entry.roi.setPen(pg.mkPen(entry.color, width=round(2 * ps)))
            entry.roi.hoverPen = pg.mkPen(entry.color, width=round(3 * ps))
            entry.curve.setPen(pg.mkPen(entry.color, width=round(2 * ps)))

        had_ruler = self._ruler_line is not None
        if had_ruler:
            self._ruler_line.setPen(pg.mkPen(self._ruler_color, width=round(3 * ps)))

        def dash_pen(width: int) -> QtGui.QPen:
            return pg.mkPen("#888888", width=width, style=QtCore.Qt.DashLine)

        self.live_cursor_marker.setPen(pg.mkPen("#38bdf8", width=round(2 * ps)))
        self.live_curve.setPen(pg.mkPen("#38bdf8", width=round(2 * ps)))
        self.timeseries_live_curve.setPen(pg.mkPen("#38bdf8", width=round(2 * ps)))
        self.frame_marker.setPen(dash_pen(round(1 * ps)))
        self.live_frame_marker.setPen(dash_pen(round(1 * ps)))

        legends = [lg for lg in (self.timeseries_legend,) if lg is not None]
        old_legend_transforms = [lg.transform() for lg in legends]
        for lg in legends:
            lg.setTransform(QtGui.QTransform.fromScale(scale, scale))

        try:
            yield
        finally:
            for entry in self.roi_entries:
                entry.set_color(entry.color)
            if had_ruler:
                self._ruler_line.setPen(pg.mkPen(self._ruler_color, width=3))
            self.live_cursor_marker.setPen(pg.mkPen("#38bdf8", width=2))
            self.live_curve.setPen(pg.mkPen("#38bdf8", width=2))
            self.timeseries_live_curve.setPen(pg.mkPen("#38bdf8", width=2))
            self.frame_marker.setPen(dash_pen(1))
            self.live_frame_marker.setPen(dash_pen(1))
            for lg, t in zip(legends, old_legend_transforms):
                lg.setTransform(t)

    @contextlib.contextmanager
    def _maybe_hidden_live_cursor(self, include_cursor: bool):
        """Blendet Fadenkreuz + Temperaturanzeige am Cursor-Pixel
        (live_cursor_marker/live_cursor_label) waehrend eines Bild- ODER
        Video-Exports kurzzeitig aus, falls die Option "Cursor-Position mit
        exportieren" im jeweiligen Export-Dialog NICHT angehakt ist
        (Standard) -- sonst wuerde eine gerade fixierte/zuletzt angezeigte
        Maus-Markierung ungewollt Teil der exportierten Grafik/des Videos.

        Setzt dafuer zusaetzlich _hover_row/_hover_col kurzzeitig auf None:
        beim Bild-Export allein wuerde das einmalige setVisible(False) hier
        genuegen (ein einzelner Render-Aufruf), beim VIDEO-Export ruft aber
        jeder einzelne Frame ueber _show_frame() -> _update_status_bar() ->
        _update_live_cursor_label() erneut setVisible(True) fuer das Label
        auf, sobald ein Hover-Pixel bekannt ist -- ohne die Hover-Position
        selbst zu leeren waere das Ausblenden also nur beim allerersten
        Frame wirksam gewesen. Stellt beides danach zuverlaessig wieder
        her."""
        if include_cursor:
            yield
            return
        marker_was_visible = self.live_cursor_marker.isVisible()
        label_was_visible = self.live_cursor_label.isVisible()
        hover_row, hover_col = self._hover_row, self._hover_col
        self.live_cursor_marker.setVisible(False)
        self.live_cursor_label.setVisible(False)
        self._hover_row = self._hover_col = None
        try:
            yield
        finally:
            self._hover_row, self._hover_col = hover_row, hover_col
            self.live_cursor_marker.setVisible(marker_was_visible)
            self.live_cursor_label.setVisible(label_was_visible)

    @contextlib.contextmanager
    def _frozen_ui_during_export(self):
        """Verhindert JEDE sichtbare Aenderung des Hauptfensters waehrend
        eines Exports -- muss als AEUSSERSTER Context-Manager verwendet
        werden (als erstes betreten, als letztes verlassen), damit wirklich
        NICHTS von dem, was die anderen Export-Context-Manager waehrenddessen
        tun, je auf dem Bildschirm sichtbar wird.

        Bugreport: "waehrend des Renderns verschwindet der Graph in der
        GUI -- die UI soll sich beim Exportieren nicht veraendern". Ursache
        war NICHT nur die hochskalierte Linienbreite/Legende (siehe
        _scaled_export_visuals), sondern vor allem _widget_raised_for_export:
        "Zeitverlauf" und "Live (Cursor)" sind tabifizierte Docks -- ein
        Export des jeweils NICHT gerade sichtbaren Tabs holt diesen fuer die
        GESAMTE Renderdauer sichtbar in den Vordergrund (fuer ein korrektes
        Layout noetig), wodurch der vom Nutzer gerade betrachtete Graph
        buchstaeblich durch den anderen ersetzt wurde, bis der Export fertig
        war. Fix: setUpdatesEnabled(False) auf dem GESAMTEN Hauptfenster
        (statt nur auf einzelnen Kurven-/Bild-Widgets) unterbindet jedes
        Neuzeichnen im gesamten Fenster -- Tab-Wechsel, Achsen-/Kurven-
        Aenderungen, Farbskala etc. eingeschlossen -- unabhaengig davon, WAS
        die anderen Context-Manager waehrenddessen konkret veraendern.
        QProgressDialog bleibt davon unberuehrt (eigenes Top-Level-Fenster).

        setUpdatesEnabled(False) unterbindet nur das BILDSCHIRM-Neuzeichnen
        -- QGraphicsScene.render() (fuer die eigentlichen Video-/Bild-Frames)
        liest den aktuellen Item-Zustand direkt aus der Szene und ist davon
        unberuehrt, liefert also weiterhin korrekt gerenderte Frames. Nach
        Wiederaktivieren springt die Anzeige direkt auf ihren finalen
        (urspruenglichen) Zustand, ohne je einen der zwischenzeitlichen
        Export-Zustaende sichtbar gezeigt zu haben."""
        self.setUpdatesEnabled(False)
        try:
            yield
        finally:
            self.setUpdatesEnabled(True)

    @contextlib.contextmanager
    def _paused_background_timers(self):
        """Pausiert die Live-Ordner-Ueberwachung (_live_watch_timer) und die
        Wiedergabe (play_timer) fuer die Dauer eines laengeren Vorgangs, der
        wiederholt QApplication.processEvents() aufruft (Laden mit
        Fortschrittsanzeige, Video-/Bildstapel-Export je Frame).

        Ohne dieses Pausieren koennte der alle 10s unbeaufsichtigt
        feuernde Live-Watch-Timer (_check_for_new_files) oder der
        Wiedergabe-Timer (_advance_frame) MITTEN in einem solchen Vorgang
        auf einem der processEvents()-Aufrufe zum Zug kommen und
        self.recording per _apply_appended_recording austauschen bzw.
        current_index weiterschalten -- waehrend z.B. _export_video mit
        lokal EINMALIG eingefrorenen Werten (Frame-Bereich, Zeitstempel-
        Array, Fortschrittsanzeige-Gesamtzahl) weiterarbeitet. Das waere
        kein sauberer Fehler, sondern eine leise inkonsistente/beschaedigte
        Ausgabe. Beide Timer werden nur dann wieder gestartet, wenn sie
        vorher tatsaechlich liefen."""
        was_watching = self._live_watch_timer.isActive()
        was_playing = self.play_timer.isActive()
        self._live_watch_timer.stop()
        self.play_timer.stop()
        try:
            yield
        finally:
            if was_watching:
                self._live_watch_timer.start()
            if was_playing:
                self.play_timer.start()

    @contextlib.contextmanager
    def _temporary_time_display_mode(self, mode: str | None):
        """Ueberschreibt Uhrzeit/Laufzeit-Anzeige BEIDER Zeitachsen nur fuer
        die Dauer eines Grafik-Exports (GraphicExportDialog.time_axis_mode())
        -- OHNE die eigentliche UI-Combobox oder die QSettings-Voreinstellung
        zu veraendern (Bugreport: "gebe mir dieselbe Freiheit wie in der
        UI"). mode=None (Standard: "Wie aktuell in der Anwendung") oder
        bereits aktiver Modus: keine Aenderung noetig. Anders als
        _apply_time_display_mode() (siehe dort) fasst diese Methode bewusst
        NICHT die _time_display_combos/QSettings an, da eine einmalige
        Export-Wahl nicht die dauerhafte Anzeige-Voreinstellung des Nutzers
        veraendern soll."""
        if mode is None or mode == self._time_display_mode:
            yield
            return
        t0 = self.recording.unix_seconds()[0] if self.recording is not None and self.recording.n_frames else 0.0
        runtime = mode == "runtime"
        old_ts = (self.axis_timeseries_bottom.runtime_mode, self.axis_timeseries_bottom.t0)
        old_live = (self.axis_live_bottom.runtime_mode, self.axis_live_bottom.t0)
        self.axis_timeseries_bottom.set_runtime_mode(runtime, t0)
        self.axis_live_bottom.set_runtime_mode(runtime, t0)
        try:
            yield
        finally:
            self.axis_timeseries_bottom.set_runtime_mode(*old_ts)
            self.axis_live_bottom.set_runtime_mode(*old_live)

    @contextlib.contextmanager
    def _dual_time_axis_export(self, widget: QtWidgets.QWidget):
        """Blendet fuer die Dauer eines Grafik-Exports zusaetzlich die
        (normalerweise ausgeblendete) OBERE Zeitachse ein und zeigt dort den
        jeweils ANDEREN Anzeigemodus als die untere Achse -- fuer die
        Export-Option "Beide" (Punkt 4: eine einzelne Grafik mit Uhrzeit UND
        Laufzeit gleichzeitig, statt zwei getrennter Dateien). Betrifft nur
        den Export-Vorgang selbst; die normale UI zeigt die obere Achse
        weiterhin nie an."""
        parts = self._time_axis_widget_parts(widget)
        if parts is None:
            yield
            return
        bottom_axis, top_axis, _curves, _markers = parts
        plot_item = widget.getPlotItem()
        other_is_runtime = not bottom_axis.runtime_mode
        old_top_state = (top_axis.runtime_mode, top_axis.t0)
        plot_item.showAxis("top", True)
        top_axis.set_runtime_mode(other_is_runtime, bottom_axis.t0)
        try:
            yield
        finally:
            plot_item.showAxis("top", False)
            top_axis.set_runtime_mode(*old_top_state)

    def _time_axis_widget_parts(
        self, widget: QtWidgets.QWidget
    ) -> tuple[TimeAxisItem, TimeAxisItem, list[pg.PlotDataItem], list[pg.InfiniteLine]] | None:
        """Ordnet einem Kurven-Widget seine untere/obere Zeitachse, Kurven und
        Frame-Marker zu -- Hilfsfunktion fuer _rebased_time_axis/
        _dual_time_axis_export. Gibt None fuer Widgets ohne eigene Zeitachse
        zurueck (z.B. das Thermobild self.glw)."""
        if widget is self.timeseries_plot:
            curves = [entry.curve for entry in self.roi_entries]
            if self.timeseries_live_curve.isVisible():
                curves.append(self.timeseries_live_curve)
            return (
                self.axis_timeseries_bottom,
                self.axis_timeseries_top,
                curves,
                [self.frame_marker],
            )
        if widget is self.live_plot:
            return self.axis_live_bottom, self.axis_live_top, [self.live_curve], [self.live_frame_marker]
        return None

    @contextlib.contextmanager
    def _rebased_time_axis(self, widget: QtWidgets.QWidget):
        """SVG-spezifischer Bugfix: die Kurven auf der Zeitachse nutzen als
        x-Werte absolute Unix-Sekunden (~1,8 Milliarden). Qt serialisiert
        Transform-Matrizen UND Pfadkoordinaten im SVG jedoch nur mit ca. 6
        signifikanten Stellen -- bei so grossen Absolutwerten reicht das
        bei weitem nicht aus, um die (um Groessenordnungen kleineren)
        Unterschiede zwischen einzelnen Kurvenpunkten darzustellen: alle
        x-Koordinaten landen im SVG-Text als (fast) derselbe gerundete
        Wert, die Kurve kollabiert zu einer Linie/verschwindet
        (Bugreport: "im SVG-Graphen fehlen die Kurven"). Der Raster-Export
        ist NICHT betroffen, da Qt dort in voller Praezision direkt
        rasterisiert statt den Umweg ueber eine Text-Serialisierung mit
        begrenzten Nachkommastellen zu nehmen.

        Fix: waehrend des SVG-Renderns werden alle betroffenen x-Werte
        (Kurven, Frame-Marker) sowie der sichtbare x-Anzeigebereich um den
        ersten Zeitstempel der Aufnahme (t0) nach unten verschoben, sodass
        nur noch kleine, praezise darstellbare Zahlen im SVG landen. Die
        Achsen-BESCHRIFTUNG bleibt dabei unveraendert korrekt, da
        TimeAxisItem.export_offset genau diesen t0 wieder zu jedem
        Tick-Wert addiert, BEVOR er die echte Uhrzeit/Laufzeit berechnet.
        Nach dem Rendern wird alles exakt auf die Original-Werte
        zurueckgesetzt."""
        parts = self._time_axis_widget_parts(widget)
        if parts is None or self.recording is None or not self.recording.n_frames:
            yield
            return
        bottom_axis, top_axis, curves, markers = parts

        t0 = float(self.recording.unix_seconds()[0])
        old_curve_data = [c.getData() for c in curves]
        old_marker_values = [m.value() for m in markers]
        vb = widget.getPlotItem().vb
        old_range = vb.viewRange()[0]
        # Bugfix: setXRange() deaktiviert als Nebenwirkung IMMER das
        # X-Autorange der ViewBox (pyqtgraph-Default disableAutoRange=True) --
        # ohne dieses Merken/Zuruecksetzen blieb die X-Achse nach JEDEM
        # SVG-Export dauerhaft auf "manuell" haengen, obwohl sie vorher auf
        # "automatisch" stand (Bugreport: "Achsen im Programm stimmen nicht
        # mehr mit den exportierten Bildern ueberein" -- ein SVG-Export
        # veraenderte damit unbemerkt den Live-Zustand der App selbst).
        x_auto = vb.autoRangeEnabled()[0]

        for curve, (x, y) in zip(curves, old_curve_data):
            if x is not None:
                curve.setData(np.asarray(x, dtype=float) - t0, y)
        for marker, value in zip(markers, old_marker_values):
            marker.setValue(value - t0)
        vb.setXRange(old_range[0] - t0, old_range[1] - t0, padding=0)
        bottom_axis.export_offset = t0
        # Obere Achse ebenfalls setzen (harmlos, falls gerade ausgeblendet) --
        # relevant fuer die Export-Option "Beide" (_dual_time_axis_export),
        # bei der die obere Achse waehrend des SVG-Exports sichtbar ist und
        # denselben (verschobenen) Wertebereich der ViewBox anzeigt.
        top_axis.export_offset = t0

        try:
            yield
        finally:
            for curve, (x, y) in zip(curves, old_curve_data):
                if x is not None:
                    curve.setData(x, y)
            for marker, value in zip(markers, old_marker_values):
                marker.setValue(value)
            if x_auto:
                vb.enableAutoRange(x=True)
            else:
                vb.setXRange(old_range[0], old_range[1], padding=0)
            bottom_axis.export_offset = 0.0
            top_axis.export_offset = 0.0

    @contextlib.contextmanager
    def _widget_raised_for_export(self, widget: QtWidgets.QWidget):
        """Stellt sicher, dass ein zu exportierendes Widget tatsaechlich
        sichtbar/fertig layoutet ist, bevor gerendert wird.

        Bugfix: "Zeitverlauf" und "Live (Cursor)" sind tabifizierte Docks --
        der jeweils NICHT gerade aktive Tab wird von Qt nie vollstaendig
        layoutet und behaelt eine winzige/veraltete Groesse (z.B. 252x54
        statt 712x450), solange er nicht mindestens einmal sichtbar war.
        Ein Export dieses Widgets (z.B. "Live-Verlauf exportieren", waehrend
        gerade der "Zeitverlauf"-Tab im Vordergrund ist) rendert dadurch
        einen viel zu kleinen Ausschnitt, in dem fuer Achsenbeschriftung/
        Tick-Text kaum noch Platz ist -- sichtbar als "fehlende
        Achsenbeschriftung". Loesung: die zugehoerige Dock-Registerkarte
        IMMER kurz in den Vordergrund holen (widget.isVisible() ist dafuer
        KEINE zuverlaessige Erkennung -- meldet fuer eine im Hintergrund
        liegende Dock-Registerkarte trotzdem True, obwohl das Layout noch
        nicht aktuell ist; visibleRegion().isEmpty() spiegelt den
        tatsaechlichen Sichtbarkeitszustand dagegen korrekt wider), danach
        zuverlaessig die zuvor sichtbare Registerkarte wiederherstellen
        (kein sichtbarer Sprung fuer den Nutzer, da nur waehrend eines
        synchron laufenden Exports)."""
        dock = widget
        while dock is not None and not isinstance(dock, QtWidgets.QDockWidget):
            dock = dock.parentWidget()
        if dock is None:
            yield
            return
        group = [dock] + list(self.tabifiedDockWidgets(dock))
        previously_visible = next((d for d in group if not d.visibleRegion().isEmpty()), dock)
        dock.raise_()
        QtWidgets.QApplication.processEvents()
        try:
            yield
        finally:
            if previously_visible is not None:
                previously_visible.raise_()
            QtWidgets.QApplication.processEvents()

    @staticmethod
    def _visible_scene_rect(widget: QtWidgets.QWidget) -> QtCore.QRectF:
        """Der aktuell sichtbare Ausschnitt eines QGraphicsView-basierten
        Widgets (glw/PlotWidget) in Szenen-Koordinaten -- als Quellrechteck
        fuer QGraphicsScene.render() genutzt, damit der Export exakt denselben
        Bildausschnitt zeigt wie die Bildschirmanzeige."""
        return widget.mapToScene(widget.viewport().rect()).boundingRect()

    def _render_widget_image(
        self, widget: QtWidgets.QWidget, scale: float, background: QtGui.QColor, align: int = 1
    ) -> QtGui.QImage:
        """Rendert ein QGraphicsView-basiertes Widget (glw/PlotWidget) in ein
        QImage bei beliebiger Aufloesung -- ueber QGraphicsScene.render()
        direkt auf die Szene, genau wie pyqtgraphs eigener ImageExporter das
        fuer denselben Zweck tut. OHNE das sichtbare Widget selbst zu
        veraendern (kein Resize/Reparent/Verstecken noetig): ein blosses
        painter.scale() vor widget.render() lieferte hier nur einen falsch
        berechneten Ausschnitt, ein tatsaechliches Resizen des LIVE-Widgets
        loeste das zwar, liess dabei aber kurzzeitig sichtbar den
        entsprechenden Bereich im Hauptfenster leer werden/"springen"
        (Video-/Grafik-Export-Bugreports). scene().render() umgeht beide
        Probleme, da es komplett am sichtbaren Widget vorbei direkt in die
        Zielgrafik rendert. align siehe _scaled_size. Fuer self.glw werden
        (wie im Video-Export) die durch setAspectLocked() entstehenden
        leeren Raender links/rechts automatisch mit herausgeschnitten
        (siehe _widget_export_size/_render_widget_into_painter)."""
        width_px, height_px = self._widget_export_size(widget, scale, align)
        image = QtGui.QImage(width_px, height_px, QtGui.QImage.Format_ARGB32)
        image.fill(background)
        painter = QtGui.QPainter(image)
        painter.setRenderHint(QtGui.QPainter.Antialiasing)
        self._render_widget_into_painter(painter, widget, width_px, height_px, scale)
        painter.end()
        return image

    def _tight_glw_segments(self) -> list[QtCore.QRectF]:
        """Zerlegt den sichtbaren Bereich von self.glw in horizontale
        Segmente OHNE den durch plot_item.setAspectLocked(True) erzeugten
        leeren Rand links/rechts vom Thermobild (die ViewBox fuellt sonst
        die kuerzere Achse mit Leerraum auf, um das Bild-Seitenverhaeltnis
        beizubehalten -- bei einer Widget-Breite, die nicht zum
        Bild-Seitenverhaeltnis passt, faellt dieser Leerraum ansonsten
        deutlich sichtbar zwischen Achse/Bild bzw. Bild/Farbskala aus,
        Bugreport: "unnötige leere Stellen" -- sowohl im Video- als auch im
        Bild-/SVG-Export von self.glw, siehe _widget_export_size/
        _render_widget_into_painter, die diese Segmente fuer beide Export-
        Wege gemeinsam nutzen).

        Gibt bei fehlender Aufnahme oder wenn kein nennenswerter Leerraum
        vorhanden ist ein einzelnes Segment (den vollen Sichtbereich)
        zurueck -- dann wird wie zuvor in einem Zug gerendert. Sonst drei
        Segmente (Achsen-Bereich | Bild ohne Leerraum | Farbskala/Legenden-
        Bereich), die nahtlos nebeneinander in den Ziel-Canvas gerendert
        werden."""
        full = self._visible_scene_rect(self.glw)
        if self.recording is None:
            return [full]
        rows, cols = self.recording.shape
        viewbox_rect = self.view_box.sceneBoundingRect()
        p0 = self.view_box.mapViewToScene(QtCore.QPointF(0, 0))
        p1 = self.view_box.mapViewToScene(QtCore.QPointF(cols, 0))
        image_left, image_right = min(p0.x(), p1.x()), max(p0.x(), p1.x())
        left_gap = max(0.0, image_left - viewbox_rect.left())
        right_gap = max(0.0, viewbox_rect.right() - image_right)
        if left_gap + right_gap < 4:
            return [full]
        return [
            QtCore.QRectF(full.left(), full.top(), viewbox_rect.left() - full.left(), full.height()),
            QtCore.QRectF(image_left, full.top(), image_right - image_left, full.height()),
            QtCore.QRectF(viewbox_rect.right(), full.top(), full.right() - viewbox_rect.right(), full.height()),
        ]

    def _widget_export_size(
        self, widget: QtWidgets.QWidget, scale: float, align: int = 1
    ) -> tuple[int, int]:
        """Zielgroesse fuer den Export von widget -- fuer self.glw ueber die
        leerraum-freien Segmente (siehe _tight_glw_segments), sonst wie
        gewohnt ueber _scaled_size."""
        if widget is self.glw:
            segments = self._tight_glw_segments()
            width = max(1, round(sum(seg.width() for seg in segments) * scale))
            height = max(1, round(segments[0].height() * scale))
            if align > 1:
                width = -(-width // align) * align
                height = -(-height // align) * align
            return width, height
        return self._scaled_size(widget, scale, align)

    def _render_widget_into_painter(
        self, painter: QtGui.QPainter, widget: QtWidgets.QWidget, width_px: int, height_px: int, scale: float
    ) -> None:
        """Rendert widget in den aktuellen (ggf. bereits uebersetzten)
        painter -- fuer self.glw ueber _render_glw_segments_into_painter
        (leerraum-freier Ausschnitt, siehe dort), sonst als ein einzelnes
        Rechteck. Backend-unabhaengig (funktioniert fuer einen QImage- UND
        einen QSvgGenerator-Painter gleichermassen), gemeinsam genutzt von
        _render_widget_image (Raster) und _save_widget_svg/_save_combined_svg
        (Vektor). width_px/height_px muessen exakt dem Ergebnis von
        _widget_export_size(widget, scale) entsprechen (Aufrufer-Pflicht),
        damit Zielgroesse und tatsaechlich gerenderter Bereich uebereinstimmen."""
        if widget is self.glw:
            self._render_glw_segments_into_painter(painter, 0.0, 0.0, width_px, height_px, scale)
            return
        widget.scene().render(
            painter, QtCore.QRectF(0, 0, width_px, height_px), self._visible_scene_rect(widget)
        )

    def _render_glw_segments_into_painter(
        self,
        painter: QtGui.QPainter,
        x: float,
        y: float,
        width_px: int,
        height_px: int,
        scale: float,
        segments: list[QtCore.QRectF] | None = None,
    ) -> None:
        """Zeichnet self.glw leerraum-getrimmt (siehe _tight_glw_segments) an
        Position (x, y) in painter -- GENAU EIN scene().render()-Aufruf fuer
        die gesamte Szene; das Herausschneiden des Leerraums passiert
        ANSCHLIESSEND rein als Bild-Ausschnitt (drawImage mit Teil-Source-
        Rects aus einem einmalig gerenderten Zwischenbild), NICHT ueber
        mehrere source-/target-verschiedene scene().render()-Aufrufe.

        Bugfix: mehrere scene().render()-Aufrufe HINTEREINANDER auf
        DERSELBEN Szene (frueher: ein Aufruf je Segment, direkt in den
        Ziel-Painter) fuehrten dazu, dass Achsen-Beschriftungen (self.glw
        hat sowohl die Bild- als auch die Farbskala-Achse) ab dem zweiten
        Aufruf zusaetzlich zur bereits vom ERSTEN Aufruf gezeichneten
        Position noch EINMAL (leicht versetzt) gezeichnet wurden -- sichtbar
        als doppelte/"geisterhafte", ueber dem Bild schwebende Ziffern
        (Bugreport: "Zahlen ... schweben in der Luft"). Reproduzierbar auch
        bei zwei Aufrufen mit inhaltlich UEBERHAUPT NICHT ueberlappenden
        Source-Rects (z.B. Achsen-Spalte gefolgt von der Bild-Spalte) --
        offenbar ein Seiteneffekt wiederholter scene().render()-Aufrufe auf
        pyqtgraphs intern gecachte Achsen-Beschriftungen, nicht ein
        geometrisches Ueberlappungsproblem. Betraf Video-, Bildstapel-,
        Grafik- UND SVG-Export gleichermassen, ueberall dort, wo
        _tight_glw_segments() mehr als ein Segment liefert (Bild-
        Seitenverhaeltnis passt nicht exakt zur Widget-Breite).

        segments: optional VORAB berechnete Segmente (siehe _render_video_frame,
        dort einmalig vor der Frame-Schleife ermittelt, damit die
        Legenden-Beschriftung nicht durch automatische Farbskalierung von
        Frame zu Frame leicht unterschiedliche Bildgroessen erzeugt) --
        ohne Angabe wird frisch neu berechnet (fuer Einzelbild-/SVG-Export
        ausreichend, dort gibt es keine Frame-zu-Frame-Konsistenz zu wahren)."""
        segments = self._tight_glw_segments() if segments is None else segments
        full = self._visible_scene_rect(self.glw)
        full_w = max(1, round(full.width() * scale))
        full_h = max(1, round(full.height() * scale))
        full_image = QtGui.QImage(full_w, full_h, QtGui.QImage.Format_ARGB32_Premultiplied)
        full_image.fill(QtCore.Qt.GlobalColor.transparent)
        scene_painter = QtGui.QPainter(full_image)
        scene_painter.setRenderHint(QtGui.QPainter.Antialiasing)
        self.glw.scene().render(scene_painter, QtCore.QRectF(0, 0, full_w, full_h), full)
        scene_painter.end()

        # WICHTIG: drawImage(target, image, sourceRect) statt eines vorab per
        # QImage.copy() zugeschnittenen Bilds zu verwenden, wuerde fuer einen
        # QSvgGenerator-Painter (Vektor-Export) das sourceRect-Argument
        # ignorieren und stattdessen das GESAMTE full_image (alle Segmente
        # zusammen) in jedes der schmalen Ziel-Rechtecke hineinquetschen --
        # sichtbar als mehrfach wiederholte/verzerrte Kopie der kompletten
        # Szene im SVG-Export (Bugreport: "Thermobild schaut zerstört aus").
        # Fuer einen normalen QImage-Painter waere das source-Rect-Argument
        # zwar korrekt, ein vorab zugeschnittenes QImage funktioniert dort
        # aber genauso -- daher hier einheitlich fuer BEIDE Painter-Typen.
        x_offset = x
        for seg in segments:
            seg_target_width = seg.width() * scale
            src_x0 = round((seg.left() - full.left()) * scale)
            src_x1 = round((seg.right() - full.left()) * scale)
            src_w = max(1, src_x1 - src_x0)
            cropped = full_image.copy(src_x0, 0, src_w, full_h)
            painter.drawImage(QtCore.QRectF(x_offset, y, seg_target_width, height_px), cropped)
            x_offset += seg_target_width

    def _render_video_frame(
        self,
        scale: float,
        background: QtGui.QColor,
        overlay_mode: str,
        idx: int,
        frame_indices: list[int],
        unix: np.ndarray,
        segments: list[QtCore.QRectF],
        graph_widget: QtWidgets.QWidget | None = None,
        graph_position: str = "unten",
        foreground: QtGui.QColor | None = None,
    ) -> QtGui.QImage:
        """Wie _render_widget_image(self.glw, ...), erweitert um (a) einen
        optionalen Zeitanzeige-Streifen unten im Bild (Punkt "Zeitanzeige im
        Video" im Video-Export-Dialog) und (b) einen optionalen Kurven-
        Graphen (timeseries_plot ODER live_plot), frei positionierbar ueber/
        unter/links/rechts vom Thermobild (graph_position), mit derselben
        wandernden Zeit-Markierungslinie (frame_marker/live_frame_marker),
        die _show_frame() ohnehin schon pro Frame aktualisiert -- also
        "genauso wie in der UI" (Bugreport). Haengt den Zeitanzeige-Streifen
        NACH allem anderen an (statt es zu ueberdecken) und rundet erst die
        GESAMTGROESSE auf ein Vielfaches von 16 auf, damit das Endergebnis
        weiterhin ffmpeg-kompatibel bleibt (siehe _scaled_size). Rendert das
        Thermobild in mehreren nebeneinanderliegenden Segmenten (siehe
        _tight_glw_segments), um den durch das aspect-locked Thermobild
        sonst verschwendeten Leerraum links/rechts zu entfernen.

        segments wird von _export_video EINMALIG vor der Frame-Schleife
        berechnet (nicht pro Frame neu): bei automatischer Farbskalierung
        ("pro Bild"/"über gesamte Messung") aendert sich die Ziffernzahl der
        Min-/Max-Beschriftung der Legende von Frame zu Frame, wodurch
        pyqtgraph die Farbskalen-Spalte (und damit die ViewBox-Grenzen)
        minimal nachjustieren kann -- pro Frame neu berechnete Segmente
        ergaben dadurch leicht unterschiedliche Bildgroessen zwischen Frames
        (Bugreport/Crash: "All images in a movie should have same size").
        graph_widget aendert seine Groesse dagegen nie zwischen Frames
        (fixe Achsenspanne, nur die Markierungslinie wandert), ein einmaliges
        Berechnen ausserhalb dieser Methode ist dafuer daher nicht noetig."""
        source_height = segments[0].height()
        source_width = sum(seg.width() for seg in segments)
        base_width = max(1, round(source_width * scale))
        base_height = max(1, round(source_height * scale))

        graph_width = graph_height = 0
        gap = 0
        if graph_widget is not None:
            graph_width, graph_height = self._scaled_size(graph_widget, scale)
            gap = round(10 * scale)

        side_by_side = graph_position in ("links", "rechts")
        if graph_widget is None:
            content_width, content_height = base_width, base_height
        elif side_by_side:
            content_width = base_width + gap + graph_width
            content_height = max(base_height, graph_height)
        else:
            content_width = max(base_width, graph_width)
            content_height = base_height + gap + graph_height

        overlay_height = round(54 * scale) if overlay_mode != "none" else 0
        aligned_width = -(-content_width // 16) * 16
        aligned_height = -(-(content_height + overlay_height) // 16) * 16

        image = QtGui.QImage(aligned_width, aligned_height, QtGui.QImage.Format_ARGB32)
        image.fill(background)
        painter = QtGui.QPainter(image)
        painter.setRenderHint(QtGui.QPainter.Antialiasing)

        # Ohne Graph bleibt das Verhalten exakt wie zuvor (Bild bei (0, 0)),
        # damit bestehende Pixel-Positionen (z.B. der Zeitleisten-Marker)
        # unveraendert bleiben. Mit Graph wird je nach Position zentriert
        # bzw. neben den Graphen gesetzt.
        if graph_widget is None:
            image_x, image_y = 0.0, 0.0
        elif graph_position == "oben":
            image_x, image_y = max(0.0, (content_width - base_width) / 2), graph_height + gap
        elif graph_position == "links":
            image_x, image_y = graph_width + gap, max(0.0, (content_height - base_height) / 2)
        elif graph_position == "rechts":
            image_x, image_y = 0.0, max(0.0, (content_height - base_height) / 2)
        else:  # "unten" (Standard)
            image_x, image_y = max(0.0, (content_width - base_width) / 2), 0.0

        self._render_glw_segments_into_painter(
            painter, image_x, image_y, base_width, base_height, scale, segments=segments
        )

        if graph_widget is not None:
            if graph_position == "oben":
                graph_x, graph_y = max(0.0, (content_width - graph_width) / 2), 0.0
            elif graph_position == "links":
                graph_x, graph_y = 0.0, max(0.0, (content_height - graph_height) / 2)
            elif graph_position == "rechts":
                graph_x, graph_y = base_width + gap, max(0.0, (content_height - graph_height) / 2)
            else:  # "unten" (Standard)
                graph_x, graph_y = max(0.0, (content_width - graph_width) / 2), base_height + gap
            # _render_widget_into_painter zeichnet stets ab (0, 0) -- fuer
            # die Platzierung wird der Painter selbst per translate() auf
            # die Zielposition verschoben.
            painter.save()
            painter.translate(graph_x, graph_y)
            self._render_widget_into_painter(painter, graph_widget, graph_width, graph_height, scale)
            painter.restore()

        if overlay_mode != "none":
            strip_rect = QtCore.QRectF(0, content_height, aligned_width, aligned_height - content_height)
            self._draw_video_timeline_overlay(
                painter, strip_rect, scale, overlay_mode, idx, frame_indices, unix,
                background=background, foreground=foreground,
            )
        painter.end()
        return image

    def _draw_video_timeline_overlay(
        self,
        painter: QtGui.QPainter,
        rect: QtCore.QRectF,
        scale: float,
        mode: str,
        idx: int,
        frame_indices: list[int],
        unix: np.ndarray,
        background: QtGui.QColor | None = None,
        foreground: QtGui.QColor | None = None,
    ) -> None:
        """Zeichnet den Zeitanzeige-Streifen (siehe _render_video_frame) --
        "Zeitleiste": Fortschrittsbalken (gruen/rot wie die Markierungen im
        echten Frame-Regler, siehe TimelineSlider) plus verstrichene/
        gesamte Laufzeit; "Zeitstempel": reales Datum/Uhrzeit dieses Bilds;
        "Beides": Balken + beide Texte.

        Die Zeitleiste bildet bewusst die GESAMTE Aufnahme ab (nicht nur
        den exportierten Ausschnitt): gruene/rote Markierung sitzen an
        ihrer TATSAECHLICHEN relativen Position innerhalb der gesamten
        Aufnahme (nicht mehr fix an den Rändern der Leiste), der
        wandernde Punkt bewegt sich entsprechend weiterhin zwischen
        beiden -- nur eben nicht mehr ueber die volle Leistenbreite,
        sondern nur innerhalb des exportierten (hervorgehobenen)
        Abschnitts. Der Video-INHALT selbst bleibt unveraendert auf genau
        diesen Abschnitt beschraenkt, die Leiste zeigt nur zusaetzlich
        dessen Einbettung in die Gesamtaufnahme (Bugreport: "tatsächliche
        Position relativ zum Gesamtvideo").

        background/foreground (Bugfix): der Streifen bekam bisher IMMER einen
        festen, fast schwarzen Hintergrund samt hellem Text -- unabhaengig
        vom tatsaechlich aktiven Hell-/Dunkel-Design von Thermobild und Graph
        darueber (Bugreport: "Hintergrund des Thermalbildes/Graphen ist
        richtig eingefaerbt, aber die Zeitleiste unten nicht"). Beide Farben
        kommen jetzt von aussen (self._graph_bg/self._graph_fg, siehe
        _export_video) und fallen nur mangels Angabe (aeltere Aufrufe/Tests)
        auf die frueheren festen Werte zurueck."""
        background = background if background is not None else QtGui.QColor(0, 0, 0, 235)
        foreground = foreground if foreground is not None else QtGui.QColor("#e5e7eb")
        painter.save()
        painter.setPen(QtCore.Qt.NoPen)
        painter.setBrush(background)
        painter.drawRect(rect)

        margin = round(18 * scale)
        groove_x0 = rect.left() + margin
        groove_x1 = rect.right() - margin
        start_idx, end_idx = frame_indices[0], frame_indices[-1]
        last_idx = max(1, self.recording.n_frames - 1)

        def frac_of(i: int) -> float:
            return max(0.0, min(1.0, i / last_idx))

        start_frac = frac_of(start_idx)
        end_frac = frac_of(end_idx)
        frac = frac_of(idx)

        text_top = rect.top()
        if mode in ("timeline", "both"):
            bar_y = rect.top() + rect.height() * 0.34
            tick_h = round(7 * scale)
            start_x = groove_x0 + (groove_x1 - groove_x0) * start_frac
            end_x = groove_x0 + (groove_x1 - groove_x0) * end_frac
            marker_x = groove_x0 + (groove_x1 - groove_x0) * frac
            # Volle Aufnahme als duenne graue Linie ueber die gesamte Breite.
            painter.setPen(QtGui.QPen(QtGui.QColor("#555555"), max(1, round(2 * scale))))
            painter.drawLine(QtCore.QPointF(groove_x0, bar_y), QtCore.QPointF(groove_x1, bar_y))
            # Exportierter Ausschnitt (zwischen gruen/rot) hervorgehoben.
            painter.setPen(QtGui.QPen(QtGui.QColor("#38bdf8"), max(2, round(3 * scale))))
            painter.drawLine(QtCore.QPointF(start_x, bar_y), QtCore.QPointF(end_x, bar_y))
            painter.setPen(QtGui.QPen(QtGui.QColor("#22c55e"), max(2, round(3 * scale))))
            painter.drawLine(QtCore.QPointF(start_x, bar_y - tick_h), QtCore.QPointF(start_x, bar_y + tick_h))
            painter.setPen(QtGui.QPen(QtGui.QColor("#ef4444"), max(2, round(3 * scale))))
            painter.drawLine(QtCore.QPointF(end_x, bar_y - tick_h), QtCore.QPointF(end_x, bar_y + tick_h))
            marker_r = round(5 * scale)
            painter.setPen(QtCore.Qt.NoPen)
            painter.setBrush(QtGui.QColor("#38bdf8"))
            painter.drawEllipse(QtCore.QPointF(marker_x, bar_y), marker_r, marker_r)
            text_top = bar_y + tick_h + round(4 * scale)

        lines = []
        if mode in ("timeline", "both"):
            # Bugfix: Laufzeit relativ zum GESAMTEN Aufnahmebeginn (unix[0]),
            # nicht zum Start des exportierten Ausschnitts (start_idx) --
            # sonst begann die Anzeige bei einem Video-Export ab z.B. Index
            # 10 (Laufzeit 00:00:30) faelschlich wieder bei 00:00:00 statt
            # bei genau der Laufzeit, die auch im Hauptfenster (Laufzeit-
            # Anzeige, siehe TimeAxisItem.t0 = unix_seconds()[0]) fuer
            # diesen Frame angezeigt wird. Der Fortschrittsbalken (frac)
            # bleibt bewusst relativ zum exportierten Ausschnitt.
            elapsed = float(unix[idx] - unix[0])
            total = float(unix[end_idx] - unix[0])
            unit_suffix = "" if self._runtime_unit == "hhmmss" else f" {self._runtime_unit}"
            lines.append(f"{self._format_runtime(elapsed)} / {self._format_runtime(total)}{unit_suffix}")
        if mode in ("timestamp", "both"):
            lines.append(self.recording.timestamps[idx].strftime("%Y-%m-%d %H:%M:%S"))

        font = QtGui.QFont()
        font.setPixelSize(max(12, round(15 * scale)))
        painter.setFont(font)
        painter.setPen(foreground)
        text_rect = QtCore.QRectF(groove_x0, text_top, groove_x1 - groove_x0, rect.bottom() - text_top)
        painter.drawText(text_rect, QtCore.Qt.AlignLeft | QtCore.Qt.AlignVCenter, "    ".join(lines))
        painter.restore()

    @staticmethod
    def _qimage_to_rgb_array(image: QtGui.QImage) -> np.ndarray:
        converted = image.convertToFormat(QtGui.QImage.Format_RGB888)
        width, height = converted.width(), converted.height()
        bytes_per_line = converted.bytesPerLine()
        buffer = converted.constBits()
        arr = np.frombuffer(buffer, dtype=np.uint8, count=bytes_per_line * height)
        arr = arr.reshape(height, bytes_per_line)[:, : width * 3].reshape(height, width, 3)
        return arr.copy()

    @staticmethod
    def _combined_layout(
        dpi: int, first_size: tuple[int, int], second_size: tuple[int, int], vertical: bool = True
    ) -> dict:
        """Gemeinsame Layout-Berechnung (Ränder/Zwischenraum/Titelhöhe/
        Gesamtgröße/Titel-Schrift) für die kombinierte Bild+Kurve-Grafik --
        von _combine_image_and_graph (Raster) UND _save_combined_svg (Vektor)
        genutzt, damit beide exakt dasselbe Layout erzeugen.

        first_size/second_size beziehen sich auf die ZEICHEN-Reihenfolge
        (oben/links zuerst, dann unten/rechts -- siehe _combined_panel_order),
        NICHT zwingend auf Bild/Graph -- welches Element zuerst kommt, hängt
        von der gewählten Position ab (Punkt "gleiche Wahlmöglichkeiten wie
        beim Video-Export: oben/unten/links/rechts"). vertical=True stapelt
        untereinander (bisheriges Verhalten), False setzt beide Panels mit je
        eigenem Titel NEBENEINANDER.

        Schriftgroesse/Raender werden bewusst ueber setPixelSize() und den
        Skalierungsfaktor (dpi/96, dieselbe Konvention wie ueberall sonst im
        Export) berechnet, NICHT ueber setPointSizeF(dpi/8): Beim Raster-Pfad
        setzt _combine_image_and_graph fuer korrekte Druck-Metadaten
        setDotsPerMeterX/Y auf dem Ziel-QImage -- das aendert dessen
        logische DPI, wodurch ein ueber Punktgroesse gesetzter Font dort ein
        ZWEITES Mal mit der DPI skaliert wuerde (quadratisch statt linear).
        Bei z.B. 300 DPI ergab das einen ~3x zu grossen Titel, der den Bereich
        darunter grossflaechig ueberdeckte. setPixelSize() ist von der
        logischen DPI des Zielgeraets unabhaengig und liefert auf beiden
        Pfaden (QImage UND QSvgGenerator) exakt dieselbe sichtbare Groesse."""
        scale = dpi / 96.0
        margin = round(18 * scale)
        gap = round(14 * scale)
        title_px = max(16, round(22 * scale))
        title_height = round(title_px * 1.6)
        w1, h1 = first_size
        w2, h2 = second_size
        if vertical:
            width = max(w1, w2) + 2 * margin
            height = 2 * margin + 2 * title_height + gap + h1 + h2
        else:
            width = 2 * margin + gap + w1 + w2
            height = 2 * margin + title_height + max(h1, h2)
        font = QtGui.QFont()
        font.setBold(True)
        font.setPixelSize(title_px)
        return {
            "margin": margin, "gap": gap, "title_height": title_height,
            "width": width, "height": height, "font": font, "vertical": vertical,
        }

    @staticmethod
    def _combined_panel_order(position: str) -> tuple[bool, bool]:
        """Liefert (vertical, image_first) fuer eine gegebene Graph-Position
        ("unten"/"oben"/"links"/"rechts" -- wo der GRAPH relativ zum
        Thermobild sitzt, siehe VideoExportDialog/GraphicExportDialog.
        graph_position()). image_first=True: Bild wird zuerst (oben bzw.
        links) gezeichnet, der Graph danach -- sonst umgekehrt."""
        vertical = position in ("oben", "unten")
        image_first = position in ("unten", "rechts")
        return vertical, image_first

    @staticmethod
    def _centered_x(layout: dict, element_width: int) -> int:
        margin, width = layout["margin"], layout["width"]
        return margin + (width - 2 * margin - element_width) // 2

    @staticmethod
    def _combine_image_and_graph(
        image: QtGui.QImage,
        image_title: str,
        graph: QtGui.QImage,
        graph_title: str,
        position: str,
        dpi: int,
        background: QtGui.QColor,
        foreground: QtGui.QColor,
    ) -> QtGui.QImage:
        """Setzt zwei bereits gerenderte Grafiken (Thermobild + Kurve) mit
        Überschriften zu einer Gesamtgrafik zusammen -- position ("unten"/
        "oben"/"links"/"rechts", siehe _combined_panel_order) legt fest, WO
        der Graph relativ zum Bild landet (Nutzerwunsch: "gleiche
        Wahlmöglichkeiten wie beim Video-Export", Standard: "rechts").
        Ehemals _stack_images_vertically (nur "unten"). Hintergrund- und
        Schriftfarbe folgen der aktuellen Grafik-Darstellung (Punkt 13),
        sonst wirkt die Grafik im Dunkel-Modus wie ein dunkler Fleck auf
        weissem Papier."""
        vertical, image_first = MainWindow._combined_panel_order(position)
        panels = (
            [(image, image_title), (graph, graph_title)] if image_first
            else [(graph, graph_title), (image, image_title)]
        )
        (first_img, first_title), (second_img, second_title) = panels
        layout = MainWindow._combined_layout(
            dpi, (first_img.width(), first_img.height()), (second_img.width(), second_img.height()), vertical
        )
        margin, gap, title_height = layout["margin"], layout["gap"], layout["title_height"]
        width, height = layout["width"], layout["height"]

        combined = QtGui.QImage(width, height, QtGui.QImage.Format_ARGB32)
        combined.fill(background)
        dots_per_meter = round(dpi / 0.0254)
        combined.setDotsPerMeterX(dots_per_meter)
        combined.setDotsPerMeterY(dots_per_meter)

        painter = QtGui.QPainter(combined)
        painter.setRenderHint(QtGui.QPainter.Antialiasing)
        painter.setFont(layout["font"])
        painter.setPen(foreground)

        if vertical:
            y = margin
            for img, title in panels:
                text_rect = QtCore.QRect(margin, y, width - 2 * margin, title_height)
                painter.drawText(text_rect, QtCore.Qt.AlignLeft | QtCore.Qt.AlignVCenter, title)
                y += title_height
                painter.drawImage(MainWindow._centered_x(layout, img.width()), y, img)
                y += img.height() + gap
        else:
            x = margin
            for img, title in panels:
                text_rect = QtCore.QRect(x, margin, img.width(), title_height)
                painter.drawText(text_rect, QtCore.Qt.AlignLeft | QtCore.Qt.AlignVCenter, title)
                painter.drawImage(x, margin + title_height, img)
                x += img.width() + gap

        painter.end()
        return combined

    @staticmethod
    def _verify_file_written(path: Path) -> None:
        """QSvgGenerator meldet Schreibfehler nicht per Rueckgabewert/Exception
        (anders als QImage.save()) -- ohne diese explizite Pruefung wuerde ein
        fehlgeschlagener SVG-Export (z.B. Zielordner nicht beschreibbar) im
        Gegensatz zum Raster-Pfad (der ok_image/ok_curve prueft) unbemerkt
        durchrutschen."""
        if not path.exists() or path.stat().st_size == 0:
            raise OSError(f"Datei wurde nicht geschrieben: {path}")

    def _save_widget_svg(
        self, widget: QtWidgets.QWidget, path: Path, scale: float, background: QtGui.QColor
    ) -> tuple[int, int]:
        width, height = self._widget_export_size(widget, scale)
        generator = QtSvg.QSvgGenerator()
        generator.setFileName(str(path))
        # Bugfix: Achsen-Tick-Beschriftung wird von pyqtgraph ueber ein
        # intern gecachtes QPicture gezeichnet (AxisItem.paint()); beim
        # Abspielen (QPicture.play()) auf einen QSvgGenerator mit einer
        # resolution() != 96 wird die Schrift dadurch QUADRATISCH zu gross
        # (einmal durch den normalen Render-Skalierungsfaktor, ein zweites
        # Mal durch die abweichende Geraete-Aufloesung beim Picture-Replay --
        # empirisch verifiziert: bei resolution()=scale*96 tauchte in den
        # Transform-Matrizen der Achsentext-Gruppen exakt scale*scale statt
        # scale auf, waehrend Linien/Pfade UND unsere eigene, per
        # setTransform() skalierte Legende korrekt nur einfach skaliert
        # blieben). Fix: resolution() bleibt konstant bei 96 (verhindert den
        # Bug), setSize() bekommt dafuer bewusst die UNskalierte (logische)
        # Widget-Groesse -- die Kombination aus setSize()/resolution()
        # ergibt weiterhin die korrekte physische mm-Groesse (Physische
        # Groesse bleibt unabhaengig von der gewaehlten Export-DPI
        # konstant), waehrend setViewBox() weiterhin die volle,
        # hochaufgeloeste Ziel-Pixelgroesse traegt (das liefert die
        # gewuenschte zusaetzliche Detailschaerfe bei hoeherer DPI).
        logical_width, logical_height = self._widget_export_size(widget, 1.0)
        generator.setSize(QtCore.QSize(logical_width, logical_height))
        generator.setViewBox(QtCore.QRect(0, 0, width, height))
        generator.setResolution(96)
        generator.setTitle("Thermo-Sequenz-Viewer Export")
        painter = QtGui.QPainter(generator)
        painter.setRenderHint(QtGui.QPainter.Antialiasing)
        # Bugfix: anders als bei _render_widget_image (image.fill(background)
        # vor dem Rendern) fehlte hier bislang jegliche Hintergrundfuellung --
        # QSvgGenerator liefert dafuer KEINEN automatischen Hintergrund, das
        # SVG blieb weiss/transparent statt in der Graphen-Hintergrundfarbe.
        # Dagegen zeichnen sich helle Achsen-/Text-/Gitterfarben (z.B. im
        # dunklen Theme) kaum bis gar nicht ab -- sichtbar als "leeres"/
        # "weisses" SVG ohne erkennbare Beschriftung.
        painter.fillRect(QtCore.QRectF(0, 0, width, height), background)
        # scene().render() statt widget.render() -- siehe _render_widget_image
        # fuer den Grund (kein Resize des sichtbaren Widgets noetig/gewollt).
        # Fuer self.glw ohne die durch setAspectLocked() entstehenden leeren
        # Raender (siehe _render_widget_into_painter/_tight_glw_segments).
        self._render_widget_into_painter(painter, widget, width, height, scale)
        painter.end()
        self._verify_file_written(path)
        return width, height

    def _save_combined_svg(
        self,
        path: Path,
        image_widget: QtWidgets.QWidget,
        image_title: str,
        curve_widget: QtWidgets.QWidget,
        curve_title: str,
        position: str,
        dpi: int,
        foreground: QtGui.QColor,
        background: QtGui.QColor,
    ) -> tuple[int, int]:
        """SVG-Entsprechung von _combine_image_and_graph: zeichnet beide
        Widgets direkt (statt vorgerenderter QImages) auf einen gemeinsamen
        QSvgGenerator, damit z.B. der Kurvenverlauf als echte Vektorpfade
        statt als eingebettete Rastergrafik im SVG landet. position siehe
        _combined_panel_order."""
        scale = dpi / 96.0
        img_w, img_h = self._widget_export_size(image_widget, scale)
        curve_w, curve_h = self._widget_export_size(curve_widget, scale)
        vertical, image_first = MainWindow._combined_panel_order(position)
        image_panel = (image_widget, image_title, img_w, img_h, True)
        curve_panel = (curve_widget, curve_title, curve_w, curve_h, False)
        panels = [image_panel, curve_panel] if image_first else [curve_panel, image_panel]
        (_, _, w1, h1, _), (_, _, w2, h2, _) = panels
        layout = MainWindow._combined_layout(dpi, (w1, h1), (w2, h2), vertical)
        margin, gap, title_height = layout["margin"], layout["gap"], layout["title_height"]
        width, height = layout["width"], layout["height"]

        # Logisches (96-DPI-aequivalentes) Gegenstueck des obigen Layouts,
        # nur fuer generator.setSize() -- siehe _save_widget_svg fuer den
        # vollen Grund (Achsen-Tick-Beschriftung von image_widget/
        # curve_widget wuerde bei generator.resolution() != 96 quadratisch
        # zu gross, da pyqtgraph sie ueber ein gecachtes QPicture zeichnet,
        # dessen Text beim Abspielen auf ein hoeher aufgeloestes Zielgeraet
        # zusaetzlich skaliert wird).
        logical_img = self._widget_export_size(image_widget, 1.0)
        logical_curve = self._widget_export_size(curve_widget, 1.0)
        logical_first, logical_second = (
            (logical_img, logical_curve) if image_first else (logical_curve, logical_img)
        )
        logical_layout = MainWindow._combined_layout(96, logical_first, logical_second, vertical)

        generator = QtSvg.QSvgGenerator()
        generator.setFileName(str(path))
        generator.setSize(QtCore.QSize(logical_layout["width"], logical_layout["height"]))
        generator.setViewBox(QtCore.QRect(0, 0, width, height))
        generator.setResolution(96)
        generator.setTitle("Thermo-Sequenz-Viewer Export")

        painter = QtGui.QPainter(generator)
        painter.setRenderHint(QtGui.QPainter.Antialiasing)
        # Bugfix: siehe _save_widget_svg -- ohne explizite Fuellung bleibt
        # der SVG-Hintergrund weiss/transparent statt in der Graphen-
        # Hintergrundfarbe, wodurch helle Achsen-/Text-/Gitterfarben (z.B. im
        # dunklen Theme) kaum sichtbar sind.
        painter.fillRect(QtCore.QRectF(0, 0, width, height), background)
        painter.setFont(layout["font"])
        painter.setPen(foreground)

        # scene().render() statt widget.render() -- siehe _render_widget_image
        # fuer den Grund (kein Resize der sichtbaren Widgets noetig/gewollt).
        def _render_panel(widget: QtWidgets.QWidget, w: int, h: int, is_image: bool, x: int, y: int) -> None:
            painter.save()
            painter.translate(x, y)
            if is_image:
                self._render_widget_into_painter(painter, widget, w, h, scale)
            else:
                widget.scene().render(painter, QtCore.QRectF(0, 0, w, h), MainWindow._visible_scene_rect(widget))
            painter.restore()

        if vertical:
            y = margin
            for widget, title, w, h, is_image in panels:
                painter.drawText(
                    QtCore.QRect(margin, y, width - 2 * margin, title_height),
                    QtCore.Qt.AlignLeft | QtCore.Qt.AlignVCenter, title,
                )
                y += title_height
                _render_panel(widget, w, h, is_image, MainWindow._centered_x(layout, w), y)
                y += h + gap
        else:
            x = margin
            for widget, title, w, h, is_image in panels:
                painter.drawText(
                    QtCore.QRect(x, margin, w, title_height),
                    QtCore.Qt.AlignLeft | QtCore.Qt.AlignVCenter, title,
                )
                _render_panel(widget, w, h, is_image, x, margin + title_height)
                x += w + gap

        painter.end()
        MainWindow._verify_file_written(path)
        return width, height

    def _save_single_part(
        self, widget: QtWidgets.QWidget, path: Path, scale: float, background: QtGui.QColor, is_svg: bool
    ) -> tuple[int, int]:
        """Speichert EIN Widget (Bild ODER Kurve) als eigenstaendige Datei --
        gemeinsamer Einstiegspunkt fuer den "getrennt"-Modus von
        _export_combined_image, unabhaengig vom Zielformat (SVG oder Raster).
        Wirft OSError bei Fehlschlag, damit der Aufrufer EINE einheitliche
        Fehlerbehandlung fuer beide Formate nutzen kann."""
        if is_svg:
            return self._save_widget_svg(widget, path, scale, background)
        image = self._render_widget_image(widget, scale, background)
        if not image.save(str(path)):
            raise OSError(f"Konnte Bild nicht speichern: {path}")
        return image.width(), image.height()

    @contextlib.contextmanager
    def _temporary_graph_content(self, selected_numbers: set[int], include_live: bool):
        """Blendet im Zeitverlauf-Graphen (self.timeseries_plot) genau die
        gewaehlten Kurven ein -- einzelne Messbereiche per NUMMER
        (selected_numbers, RoiEntry.number) und optional die Live-Cursor-Kurve
        (include_live) -- und stellt danach exakt den vorherigen
        Anzeigezustand wieder her. Gemeinsam genutzt von Grafik-, Video- und
        Bildstapel-Export (Nutzerwunsch: "einzelne ROIs (+Live-Cursor) zur
        Auswahl", "beides unabhängig voneinander möglich").

        Bewusst ueber die eindeutige Nummer statt den (frei umbenennbaren,
        nicht auf Eindeutigkeit geprueften) Namen identifiziert -- siehe
        _build_graph_content_selector in dialogs.py fuer den Bugreport dazu
        (zwei gleichnamige Messbereiche liessen sich sonst im Export-Dialog
        nicht mehr unabhaengig voneinander auswaehlen).

        Ersetzt die vorherige, nur einseitig ("immer dazuschalten, nie
        wegschalten") arbeitende _temporarily_show_live_in_timeseries.

        Achsen-Bugfix ("Achsen im Export stimmen nicht mit der Anzeige im
        Programm ueberein"): die Kurven-Auswahl fuer den Export weicht haeufig
        von der gerade AUF DEM BILDSCHIRM sichtbaren ab (z.B. Export-Dialog
        exportiert "alle" Messbereiche, obwohl im Hauptfenster nur ein Teil
        eingeblendet ist). Steht die Achse dabei auf Automatisch, wuerde
        pyqtgraph beim Sichtbarkeits-Wechsel oben SOFORT auf den neuen
        (Export-)Kurvensatz neu skalieren -- der exportierte Wertebereich
        waere dann ein ANDERER als der, den der Nutzer gerade vor sich sieht.
        Fix: den GENAU JETZT sichtbaren Wertebereich einfrieren, bevor die
        Kurven-Sichtbarkeit umgeschaltet wird, und am Ende (nach dem
        Wiederherstellen der urspruenglichen Kurven) den Automatik-Modus
        exakt so zurueckgeben, wie er vorher war."""
        vb = self.timeseries_plot.getPlotItem().vb
        x_auto, y_auto = vb.autoRangeEnabled()
        (x0, x1), (y0, y1) = vb.viewRange()

        prev_curve_visible = {}
        for entry in self.roi_entries:
            if not entry.placed:
                continue
            prev_curve_visible[entry.number] = entry.curve.isVisible()
            entry.curve.setVisible(entry.number in selected_numbers)

        prev_live_checked = self.chk_show_live_in_timeseries.isChecked()
        has_live_pixel = self._hover_row is not None and self._hover_col is not None
        want_live = include_live and has_live_pixel

        def _show_live() -> None:
            values = self._live_cursor_series(self._hover_row, self._hover_col)
            self.timeseries_live_curve.setData(self.recording.unix_seconds(), values)
            self.timeseries_legend.addItem(self.timeseries_live_curve, "Live (Cursor)")
            self.timeseries_live_curve.setVisible(True)

        def _hide_live() -> None:
            self.timeseries_legend.removeItem(self.timeseries_live_curve)
            self.timeseries_live_curve.setVisible(False)

        if want_live and not prev_live_checked:
            _show_live()
        elif not want_live and prev_live_checked:
            _hide_live()
        # Erst NACH dem Umschalten von Kurven/Live-Cursor pinnen -- das
        # pinnt exakt den Bereich, der dem Nutzer gerade angezeigt wurde,
        # unabhaengig davon, welche Kurven jetzt fuer den Export sichtbar sind.
        vb.setXRange(x0, x1, padding=0)
        vb.setYRange(y0, y1, padding=0)
        try:
            yield
        finally:
            for entry in self.roi_entries:
                if entry.number in prev_curve_visible:
                    entry.curve.setVisible(prev_curve_visible[entry.number])
            if want_live and not prev_live_checked:
                _hide_live()
            elif not want_live and prev_live_checked:
                _show_live()
            if x_auto:
                vb.enableAutoRange(x=True)
            else:
                vb.setXRange(x0, x1, padding=0)
            if y_auto:
                vb.enableAutoRange(y=True)
            else:
                vb.setYRange(y0, y1, padding=0)

    def _export_graphic(self) -> None:
        """Einziges Grafik-Export-Fenster (statt getrennter "Zeitverlauf-"/
        "Live-Grafik"-Menüpunkte, Nutzerwunsch: "nur noch ein einziges CSV/-
        Bild-Export Fenster"). Der Dialog fragt ab, welche Kurven -- einzelne
        Messbereiche und/oder Live-Cursor -- tatsächlich exportiert werden
        sollen (Nutzerwunsch: "einzelne ROIs (+Live-Cursor) zur Auswahl");
        beides landet gemeinsam in EINEM Graphen (self.timeseries_plot, siehe
        _temporary_graph_content)."""
        if self.recording is None:
            QtWidgets.QMessageBox.information(self, "Keine Daten", "Bitte zuerst eine Messreihe laden.")
            return
        live_available = self._hover_row is not None and self._hover_col is not None
        roi_entries = [(e.number, e.name) for e in self.roi_entries if e.placed]

        export_dialog = GraphicExportDialog(
            self, self._settings, default_dpi=150,
            colormaps=COLORMAPS,
            current_colormap_index=self.combo_cmap.currentIndex(),
            current_invert=self.chk_cmap_invert.isChecked(),
            current_level_mode=self._level_mode(),
            current_min=self.spin_level_min.value(),
            current_max=self.spin_level_max.value(),
            current_time_axis_mode=self._time_display_mode,
            show_graph_source_choice=True,
            live_available=live_available,
            roi_entries=roi_entries,
            current_axis_state=self._gather_axis_state(self.timeseries_plot),
        )
        # Schleife statt einmaligem exec() (Punkt 3): bricht der Nutzer den
        # nachfolgenden Speichern-Dialog ab (siehe _export_combined_image,
        # Rueckgabewert True = "Speichern-Dialog abgebrochen"), geht es
        # zurueck zu GENAU diesem (bereits ausgefuellten) Dialog-Objekt statt
        # alle Einstellungen zu verwerfen.
        while True:
            if export_dialog.exec() != QtWidgets.QDialog.DialogCode.Accepted:
                return

            selected_numbers = export_dialog.included_roi_numbers()
            include_live = export_dialog.include_live() and live_available
            curve_widget = self.timeseries_plot
            suggested_name = "Zeitverlauf_mit_Position.png"
            if selected_numbers and include_live:
                curve_title = "Temperaturverlauf (Messbereiche + Live-Cursor)"
            elif include_live:
                curve_title = "Temperaturverlauf (Live-Cursor)"
            else:
                curve_title = "Temperaturverlauf (Messbereiche)"

            with self._temporary_graph_content(selected_numbers, include_live), \
                    self._temporary_axis_override(curve_widget, export_dialog.custom_axis_overrides()):
                retry = self._export_combined_image(
                    export_dialog, curve_widget, suggested_name, self._timeseries_metadata, curve_title
                )
            if not retry:
                return

    def _bind_native_export(
        self, widget: QtWidgets.QWidget, combined_export_fn=None, suggested_name: str | None = None
    ) -> None:
        """Ersetzt den Rechtsklick-Menüeintrag "Export…" von pyqtgraph durch
        unseren eigenen Grafik-Export.

        suggested_name ist NUR im Einzelexport-Fall (combined_export_fn=None)
        relevant und dort auch Pflicht -- vorher wurde er als dritter,
        eigentlich fuer combined_export_fn=None gedachter Parameter an ALLEN
        drei Aufrufstellen mitgegeben, blieb bei den beiden combined_export_fn-
        Aufrufen (Zeitverlauf-/Live-Graph) aber vollstaendig wirkungslos --
        totes, irrefuehrendes Argument (Bugreport: unklar, warum "Live-
        Verlauf.png" nirgendwo tatsaechlich als Dateiname auftaucht).

        Zwei Gruende: (1) einheitliches Erscheinungsbild -- ein Rechtsklick
        soll zum selben Dialog/DPI-Feld fuehren wie der Export-Menüpunkt,
        nicht zu pyqtgraphs eigenem, anders aussehendem Mini-Dialog; (2)
        pyqtgraphs eigener SVG-Export (pg.exporters.SVGExporter) laesst bei
        Kurven-Graphen mit Legende/DateAxisItem in der Praxis die Kurven
        selbst weg (nur das Koordinatensystem landet im SVG) -- unser
        eigener, bereits fuer den Export-Menü-Weg verwendeter SVG-Exporter
        (QSvgGenerator + QGraphicsScene.render(), siehe _save_widget_svg) ist
        davon nicht betroffen und wird dadurch automatisch auch hier genutzt.

        combined_export_fn (optional): ruft bei Klick GENAU dieselbe Methode
        auf wie der entsprechende Export-Menü-Punkt (_export_graphic), statt
        des sonst genutzten, auf dieses eine Widget beschraenkten Einzel-
        Export-Dialogs -- fuer ein
        Rechtsklick-Menü, das sich exakt wie das Menüband verhaelt
        (Bugreport: Rechtsklick "Exportieren" sollte ins selbe Menü wie
        ueber das Menüband fuehren)."""
        scene = widget.scene()
        action = scene.contextMenu[0]
        action.setText("Grafik speichern…")
        action.triggered.disconnect()
        if combined_export_fn is not None:
            action.triggered.connect(combined_export_fn)
        else:
            assert suggested_name is not None, "suggested_name ist im Einzelexport-Fall Pflicht"
            action.triggered.connect(partial(self._export_single_graph, widget, suggested_name))

    def _export_single_graph(self, widget: QtWidgets.QWidget, suggested_name: str) -> None:
        """Exportiert GENAU diesen einen Graphen (Thermobild ODER eine der
        beiden Kurven) -- Gegenstueck zu _export_combined_image, das immer
        Thermobild+Kurve zusammen exportiert. Nutzt bewusst denselben Dialog
        (nur ohne Kombiniert/Getrennt-Auswahl) und denselben Renderer, damit
        sich beide Export-Wege einheitlich verhalten."""
        if self.recording is None:
            QtWidgets.QMessageBox.information(self, "Keine Daten", "Bitte zuerst eine Messreihe laden.")
            return

        export_dialog = GraphicExportDialog(
            self, self._settings, default_dpi=150, show_mode_choice=False, show_time_axis_choice=False,
            colormaps=COLORMAPS,
            current_colormap_index=self.combo_cmap.currentIndex(),
            current_invert=self.chk_cmap_invert.isChecked(),
            current_level_mode=self._level_mode(),
            current_min=self.spin_level_min.value(),
            current_max=self.spin_level_max.value(),
        )
        # Schleife statt einmaligem exec() (Punkt 3): bricht der Nutzer den
        # nachfolgenden Speichern-Dialog ab, geht es zurueck zu GENAU diesem
        # (bereits ausgefuellten) Dialog-Objekt statt alle Einstellungen zu
        # verwerfen -- ein erneuter export_dialog.exec() zeigt automatisch
        # wieder den zuletzt eingestellten Zustand derselben Instanz.
        filters = {
            "PNG-Bild (*.png)": ".png",
            "JPEG-Bild (*.jpg *.jpeg)": ".jpg",
            "Bitmap (*.bmp)": ".bmp",
            "TIFF-Bild (*.tiff *.tif)": ".tiff",
            "WebP-Bild (*.webp)": ".webp",
            "SVG-Vektorgrafik (*.svg)": ".svg",
        }
        while True:
            if export_dialog.exec() != QtWidgets.QDialog.DialogCode.Accepted:
                return
            dpi = export_dialog.dpi()
            include_cursor = export_dialog.export_cursor_position()
            use_custom_colors = export_dialog.use_custom_colors()

            path, selected_filter = QtWidgets.QFileDialog.getSaveFileName(
                self, "Grafik speichern", suggested_name, ";;".join(filters.keys())
            )
            if not path:
                continue
            break
        if not Path(path).suffix:
            path += filters.get(selected_filter, ".png")
        path_obj = Path(path)
        is_svg = path_obj.suffix.lower() == ".svg"

        # Thermobild (glw) und Kurven-Graphen haben seit dem Nutzerwunsch
        # "Graph immer hell/Thermobild immer dunkel" jeweils eine eigene,
        # feste Hintergrundfarbe (siehe __init__/_apply_image_colors/
        # _apply_curve_colors) -- welche hier zutrifft, haengt davon ab,
        # WELCHES der beiden Widgets gerade einzeln exportiert wird.
        bg = QtGui.QColor(self._image_bg if widget is self.glw else self._graph_bg)
        scale = dpi / 96.0
        pen_scale = scale * self._SVG_PEN_SCALE_FACTOR if is_svg else scale

        prev_level_state = self._capture_level_widgets_state() if use_custom_colors else None
        try:
            # Das Anwenden der Eigene-Einstellungen-Farbskala INNERHALB des
            # try -- sonst wuerde ein Fehler hier (vor dem try) die Anzeige
            # dauerhaft im Export-Zustand belassen, weil das finally unten
            # (das prev_level_state wiederherstellt) dann nie erreicht wird.
            if use_custom_colors:
                self._apply_custom_color_dialog_state(export_dialog, prev_level_state)
            # Kein geklammertes Mehrzeilen-with (Python 3.10+) -- der
            # Windows-7-Legacy-Build laeuft unter Python 3.8. _frozen_ui_
            # during_export() GANZ AUSSEN (siehe dort): _widget_raised_for_
            # export() holt bei einem tabifizierten Dock (z.B. "Live" waehrend
            # "Zeitverlauf" exportiert wird) den Export-Tab kurz sichtbar in
            # den Vordergrund -- ohne die Sperre wuerde der Nutzer diesen
            # Tab-Wechsel als kurzes Aufblitzen sehen.
            with self._frozen_ui_during_export(), \
                    self._widget_raised_for_export(widget), \
                    self._maybe_hidden_live_cursor(include_cursor), \
                    (self._rebased_time_axis(widget) if is_svg else contextlib.nullcontext()), \
                    self._scaled_export_visuals(scale, pen_scale):
                width, height = self._save_single_part(widget, path_obj, scale, bg, is_svg)
        except Exception as exc:
            # Bewusst breit gefangen (statt nur OSError): der Renderpfad
            # (QPainter/QSvgGenerator, dynamische Farbskala/Achsen-Zustand)
            # kann bei einem unerwarteten Zustand auch andere Exception-Typen
            # werfen (z.B. IndexError/AttributeError) -- ohne diesen breiten
            # Fang wuerde so ein Fehler ohne jeden Dialog nur als Konsolen-
            # Traceback durchschlagen, statt dass der Nutzer ueberhaupt
            # erfaehrt, dass der Export fehlgeschlagen ist.
            QtWidgets.QMessageBox.critical(self, "Fehler", f"Konnte Grafik nicht speichern:\n{exc}")
            return
        finally:
            if use_custom_colors:
                self._apply_level_widgets_state(prev_level_state)

        metadata = {
            "exportiert_am": datetime.now().isoformat(timespec="seconds"),
            "datei": path_obj.name,
            "bildgroesse_px": {"breite": width, "hoehe": height},
            "dpi": dpi,
            "quellordner": str(self.recording.paths[0].parent) if self.recording.paths else None,
        }
        meta_path = path_obj.with_suffix(".json")
        meta_path.write_text(json.dumps(metadata, indent=2, ensure_ascii=False), encoding="utf-8")
        self.statusBar().showMessage(f"Grafik gespeichert: {path_obj.name}  |  Metadaten: {meta_path.name}")

    def _export_combined_image(
        self,
        export_dialog: GraphicExportDialog,
        curve_widget: pg.PlotWidget,
        suggested_name: str,
        metadata_fn,
        curve_title: str,
    ) -> bool:
        """Speichert Thermobild (mit Position der Messbereiche, optional auch
        des Cursors -- siehe GraphicExportDialog.export_cursor_position(),
        Standard aus) und den zugehörigen Temperaturverlauf -- wahlweise
        kombiniert als eine Grafik oder getrennt als zwei Dateien (Punkt 5).
        export_dialog ist bereits ausgefuellt/bestaetigt (siehe _export_graphic
        -- dort wird VOR dem Aufruf entschieden, welcher curve_widget/
        metadata_fn/curve_title ueberhaupt zum Einsatz kommt).

        Rueckgabe (Punkt 3): True, wenn der Speichern-Dialog abgebrochen
        wurde -- der Aufrufer soll dann zurueck zum (unveraendert
        ausgefuellten) export_dialog springen statt komplett abzubrechen.
        False in allen anderen Faellen (Erfolg oder bereits gemeldeter
        Fehler)."""
        dpi = export_dialog.dpi()
        separate = export_dialog.separate()
        graph_position = export_dialog.graph_position()
        include_cursor = export_dialog.export_cursor_position()
        use_custom_colors = export_dialog.use_custom_colors()
        time_axis_mode = export_dialog.time_axis_mode()
        time_axis_ctx = (
            self._dual_time_axis_export(curve_widget) if time_axis_mode == "both"
            else self._temporary_time_display_mode(time_axis_mode)
        )

        filters = {
            "PNG-Bild (*.png)": ".png",
            "JPEG-Bild (*.jpg *.jpeg)": ".jpg",
            "Bitmap (*.bmp)": ".bmp",
            "TIFF-Bild (*.tiff *.tif)": ".tiff",
            "WebP-Bild (*.webp)": ".webp",
            "SVG-Vektorgrafik (*.svg)": ".svg",
        }
        path, selected_filter = QtWidgets.QFileDialog.getSaveFileName(
            self, "Grafik speichern", suggested_name, ";;".join(filters.keys())
        )
        if not path:
            return True
        if not Path(path).suffix:
            path += filters.get(selected_filter, ".png")
        path_obj = Path(path)
        is_svg = path_obj.suffix.lower() == ".svg"

        # Thermobild (glw) und Kurven-Graph haben seit dem Nutzerwunsch
        # "Graph immer hell/Thermobild immer dunkel" jeweils eine eigene,
        # feste Farbe (siehe __init__/_apply_image_colors/_apply_curve_
        # colors) -- die AEUSSERE Leinwand (Rand/Zwischenraum/Titeltext der
        # kombinierten Grafik, siehe _combine_image_and_graph/
        # _save_combined_svg) nutzt dabei bewusst die Graph-Farben, da der
        # Graph (anders als das Thermobild) nicht immer Teil des Exports ist
        # und der helle "Papier"-Rahmen zum wissenschaftlichen Standard passt.
        image_bg = QtGui.QColor(self._image_bg)
        bg = QtGui.QColor(self._graph_bg)
        fg = QtGui.QColor(self._graph_fg)
        scale = dpi / 96.0
        pen_scale = scale * self._SVG_PEN_SCALE_FACTOR if is_svg else scale

        saved_paths: list[Path]
        sizes_px: dict[str, tuple[int, int]] = {}
        prev_level_state = self._capture_level_widgets_state() if use_custom_colors else None
        try:
            # Innerhalb des try (siehe _export_single_graph fuer den vollen
            # Grund): sonst bliebe die Anzeige bei einem Fehler hier
            # dauerhaft im Export-Farbzustand haengen, weil das
            # wiederherstellende finally unten nie erreicht wird.
            if use_custom_colors:
                self._apply_custom_color_dialog_state(export_dialog, prev_level_state)
            with self._frozen_ui_during_export(), \
                    self._widget_raised_for_export(curve_widget), \
                    self._maybe_hidden_live_cursor(include_cursor), \
                    time_axis_ctx, \
                    (self._rebased_time_axis(curve_widget) if is_svg else contextlib.nullcontext()), \
                    self._paused_background_timers(), \
                    self._scaled_export_visuals(scale, pen_scale):
                # Bugfix: siehe _export_video fuer den vollen Grund -- das
                # Einblenden der oberen Zeitachse (time_axis_ctx, "Beide")
                # und das Hochholen einer tabifizierten Dock-Registerkarte
                # (_widget_raised_for_export) wirken bei pyqtgraph ERST nach
                # dem naechsten Event-Loop-Durchlauf. Ohne diesen Aufruf
                # fehlte die obere Achse im Export vollstaendig (kein
                # weiterer Frame/processEvents()-Aufruf folgt hier wie beim
                # Video, der das "von selbst" korrigieren wuerde).
                QtWidgets.QApplication.processEvents()
                if separate:
                    image_path = path_obj.with_name(f"{path_obj.stem}_Bild{path_obj.suffix}")
                    curve_path = path_obj.with_name(f"{path_obj.stem}_Kurve{path_obj.suffix}")
                    sizes_px[image_path.name] = self._save_single_part(self.glw, image_path, scale, image_bg, is_svg)
                    sizes_px[curve_path.name] = self._save_single_part(curve_widget, curve_path, scale, bg, is_svg)
                    saved_paths = [image_path, curve_path]
                elif is_svg:
                    sizes_px[path_obj.name] = self._save_combined_svg(
                        path_obj, self.glw, "Position im Thermobild", curve_widget, curve_title,
                        graph_position, dpi, fg, bg,
                    )
                    saved_paths = [path_obj]
                else:
                    image_scene = self._render_widget_image(self.glw, scale, image_bg)
                    image_curve = self._render_widget_image(curve_widget, scale, bg)
                    combined = self._combine_image_and_graph(
                        image_scene, "Position im Thermobild", image_curve, curve_title, graph_position, dpi, bg, fg
                    )
                    if not combined.save(path):
                        raise OSError(f"Konnte Bild nicht speichern: {path}")
                    sizes_px[path_obj.name] = (combined.width(), combined.height())
                    saved_paths = [path_obj]
        except Exception as exc:
            # Bewusst breit (siehe _export_single_graph) -- derselbe
            # mehrteilige Renderpfad (Thermobild + Kurve, ggf. SVG) kann auch
            # andere Exception-Typen als OSError werfen.
            QtWidgets.QMessageBox.critical(self, "Fehler", f"Konnte Grafik nicht speichern:\n{exc}")
            return False
        finally:
            if use_custom_colors:
                self._apply_level_widgets_state(prev_level_state)

        metadata = {
            "exportiert_am": datetime.now().isoformat(timespec="seconds"),
            "dateien": [p.name for p in saved_paths],
            "bildgroessen_px": {
                name: {"breite": w, "hoehe": h} for name, (w, h) in sizes_px.items()
            },
            "dpi": dpi,
            **metadata_fn(),
        }
        meta_path = path_obj.with_suffix(".json")
        try:
            meta_path.write_text(json.dumps(metadata, indent=2, ensure_ascii=False), encoding="utf-8")
        except OSError as exc:
            # Die Grafik selbst ist zu diesem Zeitpunkt bereits erfolgreich
            # gespeichert (siehe try/except weiter oben) -- ein Fehler hier
            # (Datentraeger voll, Zielordner inzwischen schreibgeschuetzt,
            # ".json" von einem anderen Programm gesperrt) betrifft nur die
            # zusaetzliche Metadaten-Datei und soll das nicht als kompletten
            # Fehlschlag melden.
            QtWidgets.QMessageBox.warning(
                self, "Metadaten nicht gespeichert",
                f"Die Grafik wurde gespeichert, die Metadaten-Datei „{meta_path.name}“ konnte aber "
                f"nicht geschrieben werden:\n{exc}",
            )
            self.statusBar().showMessage(
                f"Grafik gespeichert: {', '.join(p.name for p in saved_paths)}  |  Metadaten fehlgeschlagen"
            )
            return False

        self.statusBar().showMessage(
            f"Grafik gespeichert: {', '.join(p.name for p in saved_paths)}  |  Metadaten: {meta_path.name}"
        )
        return False

    def _export_csv(self) -> None:
        if self.recording is None:
            QtWidgets.QMessageBox.information(self, "Keine Daten", "Bitte zuerst eine Messreihe laden.")
            return
        placed_entries = [e for e in self.roi_entries if e.placed]
        live_available = self._hover_row is not None and self._hover_col is not None
        if not placed_entries and not live_available:
            QtWidgets.QMessageBox.information(
                self,
                "Keine Daten",
                "Es ist weder ein Messbereich platziert noch ein Live-Cursor-Pixel gewählt "
                "(Maus über das Bild bewegen oder eine Stelle fixieren).",
            )
            return

        dialog_entries = []
        for entry in placed_entries:
            w_mm = entry.width() * self._px_to_mm if self._px_to_mm is not None else None
            h_mm = entry.height() * self._px_to_mm if self._px_to_mm is not None else None
            dialog_entries.append({
                "name": entry.name,
                "width_px": entry.width(),
                "height_px": entry.height(),
                "width_mm": w_mm,
                "height_mm": h_mm,
            })
        # Nur noch EIN CSV-Export-Fenster (statt getrennter "Zeitverlauf-"/
        # "Live-Werte"-Menüpunkte, Nutzerwunsch): der Live-Cursor-Verlauf ist
        # -- sofern gerade verfuegbar -- als zusaetzliche, waehlbare Spalte
        # immer mit dabei, damit sich ROI- und Live-Daten in EINER Datei
        # exportieren lassen, statt zwingend zwei separate Exporte zu
        # benoetigen.
        if live_available:
            k = float(self._live_cursor_kernel_size)
            k_mm = k * self._px_to_mm if self._px_to_mm is not None else None
            dialog_entries.append({
                "name": "Live (Cursor)",
                "width_px": k,
                "height_px": k,
                "width_mm": k_mm,
                "height_mm": k_mm,
            })
        runtime_column_labels = {"hhmmss": "HH:MM:SS", "s": "s", "min": "min", "h": "h"}
        runtime_header = f"Laufzeit ({runtime_column_labels[self._runtime_unit]})"
        reserved_names = ["Zeitstempel", runtime_header]
        if live_available:
            reserved_names.extend(["Live X-Achse", "Live Y-Achse"])
        column_dialog = CsvColumnDialog(self, dialog_entries, reserved_names)
        if column_dialog.exec() != QtWidgets.QDialog.DialogCode.Accepted:
            return
        included = column_dialog.included()
        names = column_dialog.column_names()
        export_format = column_dialog.format()

        # Format wird SCHON im Dialog gewaehlt (statt z.B. ueber den
        # Dateityp-Filter im Speichern-Dialog), damit Vorschlagsname/-endung
        # direkt dazu passen (Nutzerwunsch: CSV/JSON/Text statt nur CSV).
        format_info = {"csv": ("CSV-Datei (*.csv)", ".csv"), "json": ("JSON-Datei (*.json)", ".json"), "text": ("Text-Datei (*.txt)", ".txt")}
        filter_str, default_ext = format_info[export_format]
        path, _ = QtWidgets.QFileDialog.getSaveFileName(
            self, "Werte speichern", f"Werte{default_ext}", filter_str
        )
        if not path:
            return
        if not Path(path).suffix:
            path += default_ext

        t0 = self.recording.timestamps[0]
        # dialog_entries/names/included sind alle in derselben Reihenfolge
        # aufgebaut (echte Messbereiche zuerst, optional gefolgt von der
        # synthetischen Live-Cursor-Zeile) -- Index i identifiziert daher
        # eindeutig, ob Spalte i aus einem echten ROI oder dem Live-Cursor
        # stammt. Fuer den Live-Cursor kommen zusaetzlich seine (ueber die
        # gesamte Aufnahme konstante) Pixel-Koordinaten als eigene Spalten
        # dazu -- frueher nur im separaten "Live-Werte als CSV"-Export
        # enthalten, jetzt Teil desselben einen Export-Fensters.
        header = ["Zeitstempel", runtime_header]
        value_arrays: list[tuple[int, object]] = []
        for i, (name, inc) in enumerate(zip(names, included)):
            if not inc:
                continue
            is_live = i >= len(placed_entries)
            if is_live:
                header.extend(["Live X-Achse", "Live Y-Achse"])
            header.append(name)
            if is_live:
                y = self._live_cursor_series(self._hover_row, self._hover_col)
            else:
                _, y = placed_entries[i].curve.getData()
            value_arrays.append((i, y))

        # Rohwerte EINMAL aufbauen (Zeitstempel/Laufzeit als Text, Live-
        # Position als Ganzzahl, Messwert als float) -- CSV/JSON/Text
        # unterscheiden sich danach nur noch in Trennzeichen bzw.
        # Zahlenformatierung, nicht in der Datenaufbereitung selbst. Bereits
        # hier auf 3 Nachkommastellen gerundet (wie _format_csv_number es
        # fuer CSV/Text ohnehin tut) -- sonst wuerde der JSON-Export (der
        # NICHT durch _format_csv_number laeuft) das Rundungsrauschen der
        # float32-Rohdaten ungerundet mit ausgeben (z.B. 20.200000762939453
        # statt 20.2).
        rows: list[list] = []
        for i, ts in enumerate(self.recording.timestamps):
            runtime = self._runtime_export_value((ts - t0).total_seconds())
            row: list = [ts.strftime("%Y-%m-%d %H:%M:%S"), runtime]
            for entry_idx, y in value_arrays:
                if entry_idx >= len(placed_entries):
                    row.extend([self._hover_col, self._hover_row])
                row.append(round(float(y[i]), 3))
            rows.append(row)

        try:
            if export_format == "json":
                # Echte Zahlen mit Dezimalpunkt (JSON-Standard, locale-
                # unabhaengig) statt der komma-formatierten Text-Darstellung von
                # CSV/Text -- konsistent von jedem JSON-Parser lesbar.
                #
                # dict(zip(header, row)) setzt voraus, dass "header" keine
                # doppelten Eintraege enthaelt -- sonst wuerde eine Spalte
                # stillschweigend eine andere ueberschreiben und deren Werte
                # gingen verloren. CsvColumnDialog._on_accept lehnt doppelte
                # Spaltennamen bereits vor dem Schliessen des Dialogs ab
                # (siehe dort), diese Annahme ist also hier bereits erfuellt.
                records = [dict(zip(header, row)) for row in rows]
                Path(path).write_text(json.dumps(records, indent=2, ensure_ascii=False), encoding="utf-8")
            else:
                # CSV (';') und Text (Tabulator) unterscheiden sich nur im
                # Trennzeichen -- beide nutzen wie die Rohdaten Dezimalkomma.
                delimiter = ";" if export_format == "csv" else "\t"
                with open(path, "w", newline="", encoding="utf-8-sig") as f:
                    writer = csv.writer(f, delimiter=delimiter)
                    writer.writerow(header)
                    for row in rows:
                        writer.writerow(
                            [self._format_csv_number(v) if isinstance(v, float) else v for v in row]
                        )
        except OSError as exc:
            QtWidgets.QMessageBox.critical(self, "Fehler", f"Konnte Werte nicht speichern:\n{exc}")
            return

        self.statusBar().showMessage(f"Werte gespeichert: {path}")

    def _capture_level_widgets_state(self) -> dict:
        """Schnappschuss von Farbverlauf/Skalierung, um ihn nach einem
        temporaeren Overrride (z.B. eigene Video-Export-Einstellungen)
        symmetrisch per _apply_level_widgets_state wiederherzustellen."""
        return {
            "cmap_index": self.combo_cmap.currentIndex(),
            "invert": self.chk_cmap_invert.isChecked(),
            "level_mode": self._level_mode(),
            "level_min": self.spin_level_min.value(),
            "level_max": self.spin_level_max.value(),
        }

    def _apply_level_widgets_state(self, state: dict) -> None:
        self._set_widget_value(self.combo_cmap, state["cmap_index"], "setCurrentIndex")
        self._set_widget_value(self.chk_cmap_invert, state["invert"], "setChecked")
        self._apply_colormap()

        # Bugfix: Min/Max MUESSEN vor _set_level_mode() gesetzt werden --
        # _set_level_mode() loest ueber _on_level_mode_changed() sofort ein
        # _show_frame() aus, das im manuellen Modus die AKTUELLEN Werte von
        # spin_level_min/max fuer die Anzeige liest. Wurden diese erst DANACH
        # gesetzt, zeigte der erste Repaint (und ein direkt anschliessender
        # Bild-/SVG-Export ohne weiteren _show_frame()-Aufruf) noch die alten
        # Grenzwerte statt der gerade uebergebenen. Beim Video-Export blieb
        # das bisher unbemerkt, weil dort ohnehin direkt danach erneut
        # _show_frame() aufgerufen wird.
        self._set_widget_value(self.spin_level_min, state["level_min"])
        self._set_widget_value(self.spin_level_max, state["level_max"])
        self._set_level_mode(state["level_mode"])

    def _apply_custom_color_dialog_state(self, dialog, prev_level_state: dict) -> None:
        """Uebernimmt die "Eigene Einstellungen"-Farbskala/Skalierung eines
        Export-Dialogs (GraphicExportDialog/VideoExportDialog -- beide bieten
        dieselben custom_colormap_index()/custom_invert()/custom_level_mode()/
        custom_min_max()-Methoden) in die aktuelle Anzeige. Gemeinsam genutzt
        von _export_single_graph/_export_combined_image/_export_video, statt
        denselben 7-zeiligen Block an drei Stellen zu wiederholen -- nur
        aufzurufen, wenn der Dialog "Eigene Einstellungen" gewaehlt hat.

        prev_level_state (von _capture_level_widgets_state(), VOR dem
        Override erfasst): bei "Automatisch" (pro Bild/gesamte Serie) spielt
        das Dialog-Min/Max keine Rolle (wird pro Frame ueberschrieben) --
        dafuer wird stattdessen der bisherige Anzeige-Wert uebernommen, damit
        _apply_level_widgets_state() ein vollstaendiges dict bekommt."""
        mode = dialog.custom_level_mode()
        custom_min, custom_max = dialog.custom_min_max()
        self._apply_level_widgets_state({
            "cmap_index": dialog.custom_colormap_index(),
            "invert": dialog.custom_invert(),
            "level_mode": mode,
            "level_min": custom_min if mode == "manual" else prev_level_state["level_min"],
            "level_max": custom_max if mode == "manual" else prev_level_state["level_max"],
        })

    def _recording_has_real_timestamps(self) -> bool:
        """Ob JEDE Datei der aktuell geladenen Aufnahme ihren Zeitstempel
        tatsaechlich aus dem Dateinamen bezieht (aktives Namensschema
        passt), statt auf den bedeutungslosen Datei-Aenderungszeit-Fallback
        von parse_timestamp() zurueckzufallen (siehe data.py) -- relevant
        fuer _resolve_export_timestamps(), wenn der Bildstapel-Export-
        Praefix Zeitstempel-Platzhalter enthaelt."""
        if self.recording is None or not self.recording.paths:
            return False
        return all(self._active_filename_pattern.search(p.stem) for p in self.recording.paths)

    def _resolve_export_timestamps(self, image_prefix: str) -> list[datetime] | None:
        """Liefert die je Frame fuer render_filename_template() im
        Bildstapel-Export zu verwendenden Zeitstempel. Im Normalfall
        einfach self.recording.timestamps -- nur wenn image_prefix
        UEBERHAUPT Zeitstempel-Platzhalter enthaelt UND diese nicht echt
        aus den Dateinamen stammen (siehe _recording_has_real_timestamps),
        fragt diese Methode nach, ob stattdessen das aktuelle Systemdatum
        oder ein selbst gewaehlter Startpunkt verwendet werden soll (relative
        Abstaende zwischen den Frames bleiben dabei erhalten). Gibt None
        zurueck, wenn der Nutzer abgebrochen hat -- der Aufrufer muss den
        Export dann seinerseits abbrechen."""
        # Zwei unterschiedliche Test-Zeitstempel durchrendern: identisches
        # Ergebnis bedeutet, dass image_prefix ueberhaupt keine Zeitstempel-
        # Platzhalter enthaelt (reiner Literaltext) -- dann ist die Frage nach
        # einem "sinnvollen" Zeitstempel gegenstandslos.
        probe_a = render_filename_template(image_prefix, datetime(2020, 1, 1))
        probe_b = render_filename_template(image_prefix, datetime(2021, 6, 15, 12, 30, 45))
        if probe_a == probe_b or self._recording_has_real_timestamps():
            return list(self.recording.timestamps)

        box = QtWidgets.QMessageBox(self)
        box.setIcon(QtWidgets.QMessageBox.Icon.Warning)
        box.setWindowTitle("Kein echter Zeitstempel bekannt")
        box.setText(
            "Der Dateiname-Präfix enthält Zeitstempel-Platzhalter (YYYY/MM/DD/hh/mm/ss), aber die "
            "geladenen Dateien haben keinen aus dem Dateinamen erkennbaren Zeitstempel (Namensschema "
            "passt nicht) -- ohne Angabe würde nur die zufällige Datei-Änderungszeit verwendet. "
            "Wie möchtest du fortfahren?"
        )
        btn_now = box.addButton("Aktuelles Systemdatum verwenden", QtWidgets.QMessageBox.ButtonRole.ActionRole)
        btn_custom = box.addButton("Eigenen Startpunkt festlegen…", QtWidgets.QMessageBox.ButtonRole.ActionRole)
        box.addButton("Abbrechen", QtWidgets.QMessageBox.ButtonRole.RejectRole)
        box.setDefaultButton(btn_custom)
        box.exec()
        clicked = box.clickedButton()
        if clicked is btn_now:
            start = datetime.now()
        elif clicked is btn_custom:
            dt_dialog = StartTimestampDialog(self, datetime.now())
            if dt_dialog.exec() != QtWidgets.QDialog.DialogCode.Accepted:
                return None
            start = dt_dialog.value()
        else:
            return None

        t0 = self.recording.timestamps[0]
        return [start + (ts - t0) for ts in self.recording.timestamps]

    def _export_video(self) -> None:
        if self.recording is None or self.recording.n_frames == 0:
            QtWidgets.QMessageBox.information(self, "Keine Daten", "Bitte zuerst eine Messreihe laden.")
            return

        default_start = self._eval_start_index if self._eval_start_index is not None else 0
        default_end = (
            self._eval_end_index if self._eval_end_index is not None else self.recording.n_frames - 1
        )
        live_available = self._hover_row is not None and self._hover_col is not None
        dialog = VideoExportDialog(
            self,
            n_frames=self.recording.n_frames,
            colormaps=COLORMAPS,
            current_colormap_index=self.combo_cmap.currentIndex(),
            current_invert=self.chk_cmap_invert.isChecked(),
            current_level_mode=self._level_mode(),
            current_min=self.spin_level_min.value(),
            current_max=self.spin_level_max.value(),
            current_fps=self.fps_spin.value(),
            default_start_frame=default_start + 1,
            default_end_frame=default_end + 1,
            roi_entries=[(e.number, e.name) for e in self.roi_entries if e.placed],
            live_available=live_available,
            sample_timestamp=self.recording.timestamps[0] if self.recording.timestamps else None,
            timestamps=self.recording.timestamps or None,
            current_axis_state=self._gather_axis_state(self.timeseries_plot),
        )
        # Schleife statt einmaligem exec() (Punkt 3): bricht der Nutzer den
        # NACHFOLGENDEN Datei-/Ordner-Dialog ab (z.B. weil ihm ein Fehler im
        # Export-Manager selbst auffaellt), geht es zurueck zu GENAU diesem
        # (bereits ausgefuellten) Dialog-Objekt statt alles zu verwerfen --
        # ein erneuter dialog.exec() zeigt automatisch wieder den zuletzt
        # eingestellten Zustand, weil dieselbe Instanz wiederverwendet wird.
        while True:
            if dialog.exec() != QtWidgets.QDialog.DialogCode.Accepted:
                return

            output_mode = dialog.output_mode()
            if output_mode == "video":
                try:
                    import imageio.v2 as imageio
                except ImportError:
                    QtWidgets.QMessageBox.critical(
                        self,
                        "Fehlende Abhängigkeit",
                        "Für den Video-Export wird das Paket 'imageio' (mit 'imageio-ffmpeg') benötigt, "
                        "das in dieser Installation nicht verfügbar ist.",
                    )
                    continue

            start_idx, end_idx = dialog.frame_range()
            fps = dialog.fps()
            show_legend = dialog.show_legend()
            use_custom = dialog.use_custom_settings()
            overlay_mode = dialog.timeline_overlay_mode()
            include_cursor = dialog.export_cursor_position()
            graph_widget = None
            graph_position = "unten"
            selected_roi_numbers: set[int] = set()
            include_live_curve = False
            axis_overrides = None
            if dialog.show_graph():
                graph_widget = self.timeseries_plot
                graph_position = dialog.graph_position()
                selected_roi_numbers = dialog.included_roi_numbers()
                include_live_curve = dialog.include_live()
                axis_overrides = dialog.custom_axis_overrides()
            # "Zeitanzeige im Bild" (overlay_mode) galt bisher NUR fuer den ins
            # Bild eingebrannten Text-Streifen -- der mit exportierte Graph
            # blieb unabhaengig davon immer bei der gerade in der App aktiven
            # Uhrzeit-/Laufzeit-Anzeige stehen (Bugreport: "wenn ich 'beides'
            # als Zeitachse auswähle stehen zwar beide Achsen unter dem Video,
            # aber nur die Laufzeit im Graphen"). Denselben, bereits
            # vorhandenen Menüpunkt jetzt konsistent fuer BEIDE Elemente nutzen,
            # statt eine zweite, separate Zeitachsen-Auswahl einzufuehren.
            # "Keine" (kein Zeit-Overlay im Bild) hat keine Entsprechung im
            # Graphen -- dort bleibt die aktuelle App-Anzeige unveraendert.
            graph_time_axis_mode = {"timeline": "runtime", "timestamp": "clock"}.get(overlay_mode)

            if output_mode == "video":
                video_filters = {
                    "MP4-Video (*.mp4)": ".mp4",
                    "AVI-Video (*.avi)": ".avi",
                    "WebM-Video (*.webm)": ".webm",
                }
                path, selected_filter = QtWidgets.QFileDialog.getSaveFileName(
                    self, "Video speichern", "Thermo-Video.mp4", ";;".join(video_filters.keys())
                )
                if not path:
                    continue
                if not Path(path).suffix:
                    path += video_filters.get(selected_filter, ".mp4")

                # WebM erlaubt (anders als MP4/AVI) keinen H.264-Videostream --
                # imageio/ffmpeg wuerden sonst mit dem Default-Codec "libx264"
                # scheitern. VP9 ist im mitgelieferten ffmpeg-Binary enthalten und
                # produziert ein regelkonformes WebM.
                video_writer_kwargs = {"fps": fps}
                if Path(path).suffix.lower() == ".webm":
                    video_writer_kwargs["codec"] = "libvpx-vp9"
            else:
                folder = QtWidgets.QFileDialog.getExistingDirectory(self, "Ordner für Bildstapel wählen")
                if not folder:
                    continue
                image_ext = dialog.image_format()
                # dialog.image_prefix() saeubert bereits selbst (sanitize_filename_prefix
                # in dialogs.py, gemeinsam mit der Live-Vorschau im Dialog genutzt) --
                # Zeichen, die unter Windows/macOS/Linux in Dateinamen ungueltig sind
                # bzw. (bei "/" oder "\") ungewollt Unterordner erzeugen wuerden.
                image_prefix = dialog.image_prefix()
                export_timestamps = self._resolve_export_timestamps(image_prefix)
                if export_timestamps is None:
                    continue

                # Punkt 2 (Nutzerwunsch "volle Kontrolle ueber den Namen"): kein
                # automatisch angehaengter Zaehler mehr, wenn "IDX" fehlt --
                # stattdessen hier verbindlich (mit den TATSAECHLICH fuers
                # Rendern verwendeten Zeitstempeln) pruefen, ob das Muster fuer
                # den gewaehlten Frame-Bereich ueberhaupt eindeutige Namen
                # ergibt, und sonst nachfragen statt Dateien stillschweigend
                # gegenseitig zu ueberschreiben.
                if INDEX_TOKEN not in image_prefix:
                    rendered_names = [
                        render_filename_template(image_prefix, export_timestamps[idx])
                        for idx in range(start_idx, end_idx + 1)
                    ]
                    if len(set(rendered_names)) < len(rendered_names):
                        box = QtWidgets.QMessageBox(self)
                        box.setIcon(QtWidgets.QMessageBox.Icon.Warning)
                        box.setWindowTitle("Dateiname nicht eindeutig")
                        box.setText(
                            f"Das Dateiname-Muster „{image_prefix}“ ergibt für mehrere der "
                            f"{len(rendered_names)} exportierten Frames denselben Namen -- spätere "
                            f"Frames würden frühere überschreiben. Soll „{INDEX_TOKEN}“ automatisch "
                            "angehängt werden (fortlaufende Nummer), oder möchtest du das Muster "
                            "selbst anpassen?"
                        )
                        btn_fix = box.addButton(
                            f"„{INDEX_TOKEN}“ anhängen", QtWidgets.QMessageBox.ButtonRole.AcceptRole
                        )
                        box.addButton("Selbst anpassen…", QtWidgets.QMessageBox.ButtonRole.RejectRole)
                        box.setDefaultButton(btn_fix)
                        box.exec()
                        if box.clickedButton() is btn_fix:
                            dialog.edit_image_prefix.setText(image_prefix + INDEX_TOKEN)
                        continue
            break

        # Aktuellen Anzeigezustand sichern, um ihn nach dem Export wiederherzustellen.
        prev_index = self.current_index
        prev_histogram_visible = self.histogram.isVisible()
        prev_level_state = self._capture_level_widgets_state()

        frame_indices = list(range(start_idx, end_idx + 1))
        progress_label = "Video wird erstellt…" if output_mode == "video" else "Bildstapel wird erstellt…"
        progress = QtWidgets.QProgressDialog(progress_label, "Abbrechen", 0, len(frame_indices), self)
        progress.setWindowModality(QtCore.Qt.WindowModal)
        progress.setMinimumDuration(300)

        # Basis-Fuellfarbe der Video-/Bildstapel-Leinwand (Leerraum um das
        # Thermobild/zwischen Bild und Graph, Zeitanzeige-Streifen) -- das
        # Thermobild ist in JEDEM Export dabei (der Graph nur optional),
        # daher dessen feste, dunkle Farbe (siehe __init__/_apply_image_
        # colors). Jedes einzelne Widget rendert innerhalb dieser Flaeche
        # trotzdem mit seiner EIGENEN aktuellen Hintergrundfarbe (glw dunkel,
        # Graph hell) -- siehe _render_glw_segments_into_painter/
        # _render_widget_into_painter, die direkt die LIVE-Szene zeichnen.
        bg = QtGui.QColor(self._image_bg)
        fg = QtGui.QColor(self._image_fg)
        scale = 2.0  # feste, ordentliche Aufloesung fuer Video-/Bildstapel-Frames
        unix = self.recording.unix_seconds()
        # Fuer den Bildstapel schon geschriebene Dateien, um sie bei
        # Abbruch/Fehler wieder zu entfernen (analog zum einzelnen
        # Video-Pfad) -- sonst bliebe ein unvollstaendiger, verwirrender
        # Rest-Bildstapel im Zielordner liegen.
        written_paths: list[Path] = []
        cancelled = False
        error_message: str | None = None
        try:
            # use_custom/Legende-Sichtbarkeit erst HIER (innerhalb des try)
            # anwenden -- siehe _export_single_graph/_export_combined_image
            # fuer den vollen Grund: sonst bliebe die Anzeige bei einem
            # Fehler hier dauerhaft im Export-Zustand haengen, weil das
            # wiederherstellende finally unten nie erreicht wird.
            if use_custom:
                self._apply_custom_color_dialog_state(dialog, prev_level_state)
            self.histogram.setVisible(show_legend)
            # _render_video_frame rendert pro Frame direkt ueber
            # QGraphicsScene.render() -- das sichtbare self.glw-Widget wird
            # dabei nie veraendert (kein Resize/Verstecken), es verschwindet
            # also waehrend des Renderns nicht mehr aus dem Hauptfenster.
            # Rundet Breite/Hoehe (inkl. optionalem Zeitanzeige-Streifen) auf
            # ein Vielfaches von 16 auf, damit ffmpeg das Bild nicht selbst
            # mit einer Warnung nachtraeglich vergroessern muss.
            #
            # _frozen_ui_during_export() steht bewusst GANZ AUSSEN (zuerst
            # betreten, zuletzt verlassen) -- alle Context-Manager danach
            # (Tab-Vordergrundholen, Kurven-/Achsen-/Zeitanzeige-Umschalten)
            # veraendern die sichtbaren Widgets, sollen dabei aber NIE
            # tatsaechlich auf dem Bildschirm sichtbar werden (siehe
            # _frozen_ui_during_export fuer den vollen Bugreport-Hintergrund).
            with self._frozen_ui_during_export(), \
                    self._maybe_hidden_live_cursor(include_cursor), \
                    (self._widget_raised_for_export(graph_widget) if graph_widget is not None
                     else contextlib.nullcontext()), \
                    (self._temporary_graph_content(selected_roi_numbers, include_live_curve)
                     if graph_widget is not None else contextlib.nullcontext()), \
                    (self._temporary_axis_override(graph_widget, axis_overrides)
                     if graph_widget is not None else contextlib.nullcontext()), \
                    (self._dual_time_axis_export(graph_widget)
                     if graph_widget is not None and overlay_mode == "both"
                     else self._temporary_time_display_mode(
                         graph_time_axis_mode if graph_widget is not None else None
                     )), \
                    self._paused_background_timers(), \
                    self._scaled_export_visuals(scale):
                # Bugfix: das Ein-/Ausblenden der oberen Zeitachse
                # (_dual_time_axis_export, "Beides") und das Hochholen einer
                # tabifizierten Dock-Registerkarte (_widget_raised_for_export)
                # loesen bei pyqtgraph eine ERST BEIM NAECHSTEN Event-Loop-
                # Durchlauf tatsaechlich wirksame Neuberechnung des Layouts
                # aus. Ohne diesen Aufruf hier zeigte GENAU der ERSTE
                # gerenderte Frame die obere Achse noch nicht (ab dem
                # zweiten Frame -- nach dem naechsten processEvents() in der
                # Schleife unten -- korrekt), da vorher noch kein
                # Event-Loop-Durchlauf stattgefunden hatte.
                QtWidgets.QApplication.processEvents()
                # EINMALIG (nicht pro Frame) berechnet -- siehe
                # _render_video_frame fuer den Grund (sonst leicht
                # unterschiedliche Bildgroessen zwischen Frames bei
                # automatischer Farbskalierung).
                self._show_frame(frame_indices[0])
                segments = self._tight_glw_segments()
                if output_mode == "video":
                    with imageio.get_writer(path, **video_writer_kwargs) as writer:
                        for n, idx in enumerate(frame_indices):
                            if progress.wasCanceled():
                                cancelled = True
                                break
                            self._show_frame(idx)
                            image = self._render_video_frame(
                                scale, bg, overlay_mode, idx, frame_indices, unix, segments,
                                graph_widget, graph_position, foreground=fg,
                            )
                            writer.append_data(self._qimage_to_rgb_array(image))
                            progress.setValue(n + 1)
                            QtWidgets.QApplication.processEvents()
                else:
                    digits = len(str(len(frame_indices)))
                    for n, idx in enumerate(frame_indices):
                        if progress.wasCanceled():
                            cancelled = True
                            break
                        self._show_frame(idx)
                        image = self._render_video_frame(
                            scale, bg, overlay_mode, idx, frame_indices, unix, segments,
                            graph_widget, graph_position, foreground=fg,
                        )
                        # Zeitstempel-Platzhalter (YYYY/MM/DD/hh/mm/ss) im
                        # Praefix werden mit dem Zeitstempel dieses Frames
                        # gefuellt (Nutzerwunsch) -- export_timestamps ist
                        # entweder direkt self.recording.timestamps (echter,
                        # aus dem Dateinamen erkannter Zeitstempel) oder,
                        # falls nicht verfuegbar, ein vom Nutzer bestaetigter
                        # Ersatz-Zeitplan (siehe _resolve_export_timestamps).
                        # Enthaelt der Praefix den Platzhalter IDX (siehe
                        # render_index_token), wird die laufende Nummer GENAU
                        # dort eingesetzt. Ohne IDX bleibt das Muster exakt so
                        # stehen, wie eingegeben -- KEIN automatisch
                        # angehaengter Zaehler mehr (Nutzerwunsch: volle
                        # Kontrolle ueber den Dateinamen); dass das Muster in
                        # diesem Fall eindeutige Namen ergibt, ist bereits vor
                        # dieser Schleife geprueft (siehe Eindeutigkeits-
                        # Pruefung weiter oben).
                        rendered_prefix = render_filename_template(image_prefix, export_timestamps[idx])
                        rendered_prefix, _has_index_token = render_index_token(rendered_prefix, n + 1, digits)
                        frame_path = Path(folder) / f"{rendered_prefix}{image_ext}"
                        if not image.save(str(frame_path)):
                            raise OSError(f"Konnte Bild nicht speichern: {frame_path}")
                        written_paths.append(frame_path)
                        progress.setValue(n + 1)
                        QtWidgets.QApplication.processEvents()
        except Exception as exc:
            # Bewusst breit (statt nur OSError/RuntimeError/ValueError): der
            # Renderpfad pro Frame (Legende/Achsen-Zustand, Zeitstempel-
            # Platzhalter, Overlay-Zeichnung) kann auch andere Exception-Typen
            # werfen -- ohne diesen breiten Fang wuerde die Aufraeum-Logik
            # unten (unvollstaendige Video-/Bildstapel-Datei loeschen) bei
            # einem solchen Fehler uebersprungen und ein kaputter Rest liegen
            # bleiben, ohne dass der Nutzer je einen Fehlerdialog sieht.
            error_message = str(exc)
            cancelled = True
        finally:
            progress.close()
            # Anzeigezustand wiederherstellen.
            self.histogram.setVisible(prev_histogram_visible)
            if use_custom:
                self._apply_level_widgets_state(prev_level_state)
            self._show_frame(prev_index)

        if error_message is not None:
            if output_mode == "video":
                Path(path).unlink(missing_ok=True)
            else:
                for p in written_paths:
                    p.unlink(missing_ok=True)
            what = "Video" if output_mode == "video" else "Bildstapel"
            QtWidgets.QMessageBox.critical(self, "Fehler", f"{what} konnte nicht gespeichert werden:\n{error_message}")
            return
        if cancelled:
            if output_mode == "video":
                Path(path).unlink(missing_ok=True)
            else:
                for p in written_paths:
                    p.unlink(missing_ok=True)
            what = "Video-Export" if output_mode == "video" else "Bildstapel-Export"
            self.statusBar().showMessage(f"{what} abgebrochen.")
            return

        if output_mode == "video":
            self.statusBar().showMessage(f"Video gespeichert: {path}")
        else:
            self.statusBar().showMessage(f"Bildstapel gespeichert: {len(written_paths)} Bilder in {folder}")

    def _timeseries_metadata(self) -> dict:
        rows, cols = self.recording.shape
        rois = []
        for entry in self.roi_entries:
            roi_info: dict = {
                "index": entry.number,
                "name": entry.name,
                "farbe": entry.color,
                "sichtbar": entry.is_visible_checked(),
                "platziert": entry.placed,
                "interpolation_aktiv": entry.interp_enabled,
            }
            if entry.placed:
                cx, cy = entry.center()
                row0, row1, col0, col1 = entry.bounds_px(self.recording.shape)
                roi_info["mittelpunkt_px"] = {"x": cx, "y": cy}
                roi_info["breite_px"] = entry.width()
                roi_info["hoehe_px"] = entry.height()
                if self._px_to_mm is not None:
                    roi_info["breite_mm"] = entry.width() * self._px_to_mm
                    roi_info["hoehe_mm"] = entry.height() * self._px_to_mm
                roi_info["grenzen_px"] = {
                    "zeile_von": row0,
                    "zeile_bis": row1,
                    "spalte_von": col0,
                    "spalte_bis": col1,
                }
            rois.append(roi_info)

        cursor = None
        if self._hover_row is not None and self._hover_col is not None:
            cursor = {"zeile": self._hover_row, "spalte": self._hover_col}

        return {
            "quellordner": str(self.recording.paths[0].parent) if self.recording.paths else None,
            "anzahl_frames": self.recording.n_frames,
            "bild_groesse_px": {"zeilen": rows, "spalten": cols},
            "zeitstempel": [t.isoformat() for t in self.recording.timestamps],
            "px_zu_mm": self._px_to_mm,
            "rois": rois,
            "live_cursor_pixel": cursor,
        }
