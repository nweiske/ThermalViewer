"""Messbereich (ROI)-Domänenobjekt: bündelt das im Bild sichtbare, frei
skalierbare Rechteck mit seiner Kurve im Zeitverlauf und den zugehörigen
Steuer-Widgets im rechten Panel -- siehe roi.py für die reine
pyqtgraph-Geometrie (AdjustableROI), die dieses Modul nur verwendet."""
from __future__ import annotations

import colorsys

import numpy as np
import pyqtgraph as pg
from qtpy import QtCore, QtWidgets

from .roi import AdjustableROI, average_value

ROI_COLORS = ["#ef4444", "#22c55e", "#3b82f6", "#eab308", "#a855f7"]
# Standardnamen der ersten 5 Messbereiche (typische Anordnung eines
# Kreuzmusters); weitere (beliebig viele) Messbereiche darueber hinaus
# heissen weiterhin schlicht "ROI n" (siehe default_roi_name).
DEFAULT_ROI_NAMES = ["Oben", "Links", "Mitte", "Rechts", "Unten"]
DEFAULT_ROI_SIZE = 30.0
# Obergrenze fuer "beliebig viele ROIs" -- schuetzt MainWindow._load_project
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


def mm_value_de(value: float, decimals: int = 2) -> str:
    return f"{value:.{decimals}f}".replace(".", ",")


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
        # Dezenter, nicht-blockierender Hinweis (Nutzerwunsch, siehe
        # MainWindow._refresh_interp_range_warning): sichtbar, wenn das
        # jeweils eingestellte Frame ausserhalb von [_eval_start_index,
        # _eval_end_index] liegt -- Interpolation bleibt trotzdem moeglich.
        self.lbl_interp_start_warning: QtWidgets.QLabel | None = None
        self.lbl_interp_end_warning: QtWidgets.QLabel | None = None
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
