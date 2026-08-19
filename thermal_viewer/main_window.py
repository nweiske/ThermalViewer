"""Hauptfenster: Thermobild links, ROI-/Legenden-Steuerung und
Zeitverlauf/Live-Cursor rechts als andockbare, frei in der Breite
verstellbare Panels.
"""
from __future__ import annotations

import csv
import json
from datetime import datetime
from functools import partial
from pathlib import Path

import numpy as np
import pyqtgraph as pg
import pyqtgraph.exporters as pg_exporters
from qtpy import QtCore, QtGui, QtSvg, QtWidgets

from .assets import ICON_PATH
from .data import Recording, RecordingError, load_paths
from .dialogs import CsvColumnDialog, GraphicExportDialog, VideoExportDialog
from .roi import AdjustableROI, bounds_px_for

pg.setConfigOptions(imageAxisOrder="row-major", antialias=True)

ROI_COLORS = ["#ef4444", "#22c55e", "#3b82f6", "#eab308", "#a855f7"]

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

DEFAULT_ROI_SIZE = 20.0
# Ab so vielen Frames werden Punktmarker auf den Kurven ausgeblendet (nur
# noch Linie), damit es bei langen Aufnahmen nicht überladen wirkt. Bei
# wenigen Frames (z.B. nur 1) sind Marker nötig, sonst ist gar nichts zu
# sehen -- eine Linie braucht mindestens zwei Punkte.
MAX_FRAMES_WITH_SYMBOLS = 60

# Reihenfolge der Skalierungs-Modi (Punkt 1) -- der Index in dieser Liste ist
# zugleich die ID, mit der die zugehoerigen Radiobuttons in level_mode_group
# registriert werden (siehe _build_control_panel), damit _level_mode() die ID
# der Gruppe direkt statt einzelner isChecked()-Abfragen auswerten kann.
LEVEL_MODES = ["manual", "per_frame", "global"]


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

    def __init__(self, index: int, color: str, view_box: pg.ViewBox, curve: pg.PlotDataItem):
        self.index = index
        self.color = color
        self.name = f"ROI {index + 1}"
        self.curve = curve
        self.default_size = DEFAULT_ROI_SIZE
        self.placed = False
        self.snapshot: tuple[tuple[float, float], tuple[float, float]] | None = None
        # Verlaufs-Interpolation (Punkt 3): Start-/Ende-Geometrie je als
        # ((x, y), (w, h)) in Bildkoordinaten (oben-links), nicht Mittelpunkt.
        self.interp_enabled = False
        self.interp_start: tuple[tuple[float, float], tuple[float, float]] | None = None
        self.interp_end: tuple[tuple[float, float], tuple[float, float]] | None = None

        pen = pg.mkPen(color, width=2)
        hover_pen = pg.mkPen(color, width=3)
        self.roi = AdjustableROI([0, 0], DEFAULT_ROI_SIZE, pen=pen, hoverPen=hover_pen, removable=False)
        self.roi.setVisible(False)
        view_box.addItem(self.roi)

        # Namensbeschriftung direkt im Bild, oben links über dem ROI-Rechteck.
        self.label = pg.TextItem(text=self.name, color=color, anchor=(0, 1), fill=(0, 0, 0, 140))
        self.label.setVisible(False)
        view_box.addItem(self.label)

        # Werden von MainWindow gesetzt, hier nur als Platzhalter für Typklarheit.
        self.name_edit: QtWidgets.QLineEdit | None = None
        self.chk_visible: QtWidgets.QCheckBox | None = None
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

    def set_name(self, name: str) -> None:
        self.name = name
        self.curve.opts["name"] = name
        self.label.setText(name)

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

    def place(self, center_x: float, center_y: float, width: float, height: float) -> None:
        width = max(width, 1.0)
        height = max(height, 1.0)
        pos = (center_x - width / 2, center_y - height / 2)
        self.roi.setSize([width, height])
        self.roi.setPos(list(pos))
        self.placed = True
        self.snapshot = (pos, (width, height))
        visible = self.chk_visible.isChecked() if self.chk_visible else True
        self.roi.setVisible(visible)
        self.sync_label_pos()
        self.label.setVisible(visible)

    def reset(self) -> None:
        if self.snapshot is None:
            return
        pos, size = self.snapshot
        self.roi.setSize(list(size))
        self.roi.setPos(list(pos))
        self.sync_label_pos()

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

    def capture_interp_start(self) -> None:
        self.interp_start = (tuple(self.roi.pos()), tuple(self.roi.size()))

    def capture_interp_end(self) -> None:
        self.interp_end = (tuple(self.roi.pos()), tuple(self.roi.size()))

    def is_interp_ready(self) -> bool:
        """True, wenn Verlaufs-Interpolation aktiv UND beide Keyframes gesetzt
        sind -- einzige Stelle, die diese drei Bedingungen kombiniert, damit
        Anzeige (_update_interpolated_rois) und Kurvenberechnung
        (_recompute_curves) nicht unabhaengig voneinander auseinanderlaufen
        koennen."""
        return self.interp_enabled and self.interp_start is not None and self.interp_end is not None

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


class MainWindow(QtWidgets.QMainWindow):
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
        self.roi_entries: list[RoiEntry] = []
        self._current_theme = DEFAULT_THEME
        # Grafik-Darstellung (Punkt 13): "app" folgt dem App-Design, sonst
        # unabhaengig davon fest "light"/"dark".
        self._graph_theme_mode = "app"
        self._graph_bg = THEMES[DEFAULT_THEME]["pg_background"]
        self._graph_fg = THEMES[DEFAULT_THEME]["pg_foreground"]
        # Min/Max ueber alle Frames der aktuellen Aufnahme (Punkt 1), einmalig
        # beim Laden berechnet.
        self._global_level_range: tuple[float, float] | None = None
        # Maßstab (Punkt 12): mm pro Pixel, None = kein Maßstab definiert.
        self._px_to_mm: float | None = None
        self._ruler_armed = False
        self._ruler_start: tuple[float, float] | None = None
        self._ruler_line: pg.PlotDataItem | None = None

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

        saved_graph_mode = self._settings.value("graph_theme_mode", "app")
        idx = self.combo_graph_theme.findData(saved_graph_mode)
        if idx >= 0:
            self.combo_graph_theme.blockSignals(True)
            self.combo_graph_theme.setCurrentIndex(idx)
            self.combo_graph_theme.blockSignals(False)
            self._graph_theme_mode = saved_graph_mode

        saved_theme = self._settings.value("theme", DEFAULT_THEME)
        self._apply_theme(saved_theme if saved_theme in THEMES else DEFAULT_THEME)
        if self._graph_theme_mode != "app":
            # _apply_theme() aktualisiert die Graphen-Farben nur, wenn der Modus
            # "app" ist -- bei einem gespeicherten fixen Graphen-Design (Punkt
            # 13) muss es hier zusaetzlich einmalig angewendet werden, sonst
            # bleiben Bild/Kurven beim Start auf pyqtgraphs Standardfarben.
            fixed_theme = THEMES[self._graph_theme_mode]
            self._apply_graph_colors(fixed_theme["pg_background"], fixed_theme["pg_foreground"])

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
        self.timeline_bar = QtWidgets.QWidget()
        layout = QtWidgets.QHBoxLayout(self.timeline_bar)
        layout.setContentsMargins(6, 4, 6, 4)

        self.play_button = QtWidgets.QPushButton("▶ Play")
        self.play_button.setCheckable(True)
        self.play_button.toggled.connect(self._on_play_toggled)
        layout.addWidget(self.play_button)

        layout.addWidget(QtWidgets.QLabel(" Frame: "))
        self.frame_slider = QtWidgets.QSlider(QtCore.Qt.Horizontal)
        self.frame_slider.setMinimumWidth(120)
        self.frame_slider.setRange(0, 0)
        self.frame_slider.valueChanged.connect(self._on_slider_changed)
        layout.addWidget(self.frame_slider, 1)

        self.frame_spin = QtWidgets.QSpinBox()
        self.frame_spin.setRange(1, 1)
        self.frame_spin.valueChanged.connect(self._on_frame_spin_changed)
        layout.addWidget(self.frame_spin)

        layout.addWidget(QtWidgets.QLabel("  FPS: "))
        self.fps_spin = QtWidgets.QDoubleSpinBox()
        self.fps_spin.setRange(0.5, 60.0)
        self.fps_spin.setValue(10.0)
        self.fps_spin.valueChanged.connect(self._on_fps_changed)
        layout.addWidget(self.fps_spin)

        self.timestamp_label = QtWidgets.QLabel("  –")
        self.timestamp_label.setStyleSheet("font-weight: bold; padding-left: 8px;")
        layout.addWidget(self.timestamp_label)

        self.play_timer = QtCore.QTimer(self)
        self.play_timer.timeout.connect(self._advance_frame)

    def _build_plots(self) -> None:
        self.timeseries_plot = pg.PlotWidget(axisItems={"bottom": pg.DateAxisItem()})
        self.timeseries_plot.setLabel("left", "Temperatur", units="°C")
        self.timeseries_plot.showGrid(x=True, y=True, alpha=0.3)
        self.timeseries_plot.addLegend(offset=(10, 10))
        self.frame_marker = pg.InfiniteLine(
            angle=90, movable=False, pen=pg.mkPen("#888888", width=1, style=QtCore.Qt.DashLine)
        )
        self.timeseries_plot.addItem(self.frame_marker)

        btn_export_timeseries = QtWidgets.QPushButton("Grafik speichern…")
        btn_export_timeseries.setToolTip(
            "Speichert Thermobild (mit Position der Messbereiche) und Temperaturverlauf "
            "gemeinsam oder getrennt als Grafik(en)."
        )
        btn_export_timeseries.clicked.connect(self._export_timeseries_graphic)
        btn_export_timeseries_csv = QtWidgets.QPushButton("Werte als CSV…")
        btn_export_timeseries_csv.setToolTip(
            "Speichert die Temperaturwerte aller platzierten Messbereiche über die Zeit "
            "als CSV-Datei (z.B. zur Weiterverarbeitung in Excel)."
        )
        btn_export_timeseries_csv.clicked.connect(self._export_roi_csv)

        self.timeseries_widget = QtWidgets.QWidget()
        timeseries_layout = QtWidgets.QVBoxLayout(self.timeseries_widget)
        timeseries_layout.setContentsMargins(4, 4, 4, 4)
        timeseries_layout.addWidget(self.timeseries_plot)
        timeseries_buttons = QtWidgets.QHBoxLayout()
        timeseries_buttons.addWidget(btn_export_timeseries)
        timeseries_buttons.addWidget(btn_export_timeseries_csv)
        timeseries_layout.addLayout(timeseries_buttons)

        self.live_plot = pg.PlotWidget(axisItems={"bottom": pg.DateAxisItem()})
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

        btn_export_live = QtWidgets.QPushButton("Grafik speichern…")
        btn_export_live.setToolTip(
            "Speichert Thermobild (mit Position des Cursor-Pixels) und Temperaturverlauf "
            "gemeinsam oder getrennt als Grafik(en)."
        )
        btn_export_live.clicked.connect(self._export_live_graphic)
        btn_export_live_csv = QtWidgets.QPushButton("Werte als CSV…")
        btn_export_live_csv.setToolTip(
            "Speichert den Temperaturverlauf des Cursor-Pixels über die Zeit als CSV-Datei."
        )
        btn_export_live_csv.clicked.connect(self._export_live_csv)

        self.live_widget = QtWidgets.QWidget()
        live_layout = QtWidgets.QVBoxLayout(self.live_widget)
        live_layout.setContentsMargins(4, 4, 4, 4)
        live_layout.addWidget(self.live_label)
        live_layout.addWidget(self.live_plot)
        live_buttons = QtWidgets.QHBoxLayout()
        live_buttons.addWidget(btn_export_live)
        live_buttons.addWidget(btn_export_live_csv)
        live_layout.addLayout(live_buttons)

    def _build_roi_entries(self) -> None:
        for i, color in enumerate(ROI_COLORS):
            curve = self.timeseries_plot.plot(
                pen=pg.mkPen(color, width=2),
                symbol="o",
                symbolSize=5,
                symbolBrush=color,
                symbolPen=None,
                name=f"ROI {i + 1}",
            )
            entry = RoiEntry(i, color, self.view_box, curve)
            entry.roi.sigRegionChanged.connect(partial(self._on_roi_region_changed, entry))
            entry.roi.sigRegionChangeFinished.connect(partial(self._on_roi_region_finished, entry))
            self.roi_entries.append(entry)

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

        self.chk_cmap_invert = QtWidgets.QCheckBox("Invertiert")
        self.chk_cmap_invert.setToolTip("Kehrt den Farbverlauf der Legende um (kalt/warm vertauscht).")
        self.chk_cmap_invert.toggled.connect(self._on_colormap_invert_toggled)
        legend_layout.addWidget(self.chk_cmap_invert, 1, 0, 1, 3)

        self.level_mode_group = QtWidgets.QButtonGroup(self)
        self.radio_level_manual = QtWidgets.QRadioButton("Manuell")
        self.radio_level_manual.setToolTip(
            "Feste, selbst gewählte Grenzwerte (Felder \"Min\"/\"Max\" unten) statt automatischer Skalierung."
        )
        self.radio_level_per_frame = QtWidgets.QRadioButton("Automatisch (pro Bild)")
        self.radio_level_per_frame.setToolTip(
            "Minimum/Maximum werden für jedes angezeigte Bild neu berechnet (Standard)."
        )
        self.radio_level_global = QtWidgets.QRadioButton("Automatisch (gesamte Serie)")
        self.radio_level_global.setToolTip(
            "Ermittelt Minimum/Maximum einmalig über alle geladenen Frames und verwendet "
            "diesen Bereich durchgehend für die Legende (statt pro Bild neu zu skalieren)."
        )
        self.radio_level_per_frame.setChecked(True)
        for i, radio in enumerate((self.radio_level_manual, self.radio_level_per_frame, self.radio_level_global)):
            self.level_mode_group.addButton(radio, i)
            legend_layout.addWidget(radio, 2 + i, 0, 1, 3)
        self.level_mode_group.buttonToggled.connect(self._on_level_mode_changed)

        legend_layout.addWidget(QtWidgets.QLabel("Min:"), 5, 0)
        self.spin_level_min = QtWidgets.QDoubleSpinBox()
        self.spin_level_min.setRange(-100.0, 2000.0)
        self.spin_level_min.setDecimals(1)
        self.spin_level_min.setSuffix(" °C")
        self.spin_level_min.setEnabled(False)
        self.spin_level_min.valueChanged.connect(self._on_level_spin_changed)
        legend_layout.addWidget(self.spin_level_min, 5, 1)

        legend_layout.addWidget(QtWidgets.QLabel("Max:"), 6, 0)
        self.spin_level_max = QtWidgets.QDoubleSpinBox()
        self.spin_level_max.setRange(-100.0, 2000.0)
        self.spin_level_max.setDecimals(1)
        self.spin_level_max.setValue(50.0)
        self.spin_level_max.setSuffix(" °C")
        self.spin_level_max.setEnabled(False)
        self.spin_level_max.valueChanged.connect(self._on_level_spin_changed)
        legend_layout.addWidget(self.spin_level_max, 6, 1)

        self.histogram.sigLevelsChanged.connect(self._on_histogram_levels_changed)

        layout.addWidget(legend_box)

        # -- Grafik-Darstellung (Punkt 13): Hintergrund/Schrift der Grafiken
        # unabhaengig vom uebrigen App-Design waehlbar -----------------------
        graph_theme_box = QtWidgets.QGroupBox("Grafik-Darstellung")
        graph_theme_layout = QtWidgets.QHBoxLayout(graph_theme_box)
        graph_theme_layout.addWidget(QtWidgets.QLabel("Hintergrund:"))
        self.combo_graph_theme = QtWidgets.QComboBox()
        self.combo_graph_theme.setToolTip(
            "Hintergrund-/Schriftfarbe von Thermobild und Kurven-Graphen -- unabhängig vom "
            "übrigen App-Design (Ansicht > Design) wählbar, z.B. für helle Exporte bei "
            "dunklem App-Design."
        )
        self.combo_graph_theme.addItem("Wie App-Design", "app")
        self.combo_graph_theme.addItem("Hell (schwarze Schrift)", "light")
        self.combo_graph_theme.addItem("Dunkel (weiße Schrift)", "dark")
        self.combo_graph_theme.currentIndexChanged.connect(self._on_graph_theme_changed)
        graph_theme_layout.addWidget(self.combo_graph_theme, 1)
        layout.addWidget(graph_theme_box)

        # -- Maßstab (Lineal, Punkt 12) --------------------------------------
        scale_box = QtWidgets.QGroupBox("Maßstab")
        scale_layout = QtWidgets.QHBoxLayout(scale_box)
        self.scale_label = QtWidgets.QLabel("Kein Maßstab definiert.")
        scale_layout.addWidget(self.scale_label, 1)
        btn_scale_set = QtWidgets.QPushButton("Festlegen…")
        btn_scale_set.setToolTip("Referenzlinie im Bild einzeichnen und ihre reale Länge in mm angeben.")
        btn_scale_set.clicked.connect(self._start_ruler_tool)
        scale_layout.addWidget(btn_scale_set)
        self.btn_scale_clear = QtWidgets.QPushButton("Entfernen")
        self.btn_scale_clear.setToolTip("Entfernt den definierten Maßstab wieder (Größen werden nur noch in Pixeln angezeigt).")
        self.btn_scale_clear.setEnabled(False)
        self.btn_scale_clear.clicked.connect(self._clear_ruler_scale)
        scale_layout.addWidget(self.btn_scale_clear)
        layout.addWidget(scale_box)

        # -- Standardgröße neuer ROIs ------------------------------------
        size_box = QtWidgets.QGroupBox("Neue Messbereiche")
        size_layout = QtWidgets.QHBoxLayout(size_box)
        size_layout.addWidget(QtWidgets.QLabel("Standardgröße:"))
        self.spin_default_size = QtWidgets.QDoubleSpinBox()
        self.spin_default_size.setRange(2, 5000)
        self.spin_default_size.setValue(DEFAULT_ROI_SIZE)
        self.spin_default_size.setSuffix(" px")
        self.spin_default_size.setToolTip(
            "Kantenlänge, mit der neu platzierte Messbereiche starten (quadratisch). Breite und "
            "Höhe lassen sich danach pro Messbereich unabhängig voneinander anpassen."
        )
        self.spin_default_size.valueChanged.connect(self._on_default_size_changed)
        size_layout.addWidget(self.spin_default_size)
        size_layout.addStretch(1)
        layout.addWidget(size_box)

        # -- ROI-Zeilen ---------------------------------------------------
        for entry in self.roi_entries:
            layout.addWidget(self._build_roi_row(entry))

        layout.addStretch(1)

        scroll = QtWidgets.QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(panel)
        self.control_panel = scroll

    def _build_roi_row(self, entry: RoiEntry) -> QtWidgets.QGroupBox:
        # Kein statischer Box-Titel mehr -- an dessen Stelle tritt ein
        # editierbares Namensfeld (siehe name_edit unten), das wie ein Titel
        # aussieht, aber vom Nutzer umbenannt werden kann.
        box = QtWidgets.QGroupBox()
        grid = QtWidgets.QGridLayout(box)
        # Laufender Zeilenzaehler statt hartkodierter Grid-Zeilennummern: eine
        # neue Zeile zwischendurch einzufuegen erfordert so nur einen
        # zusaetzlichen Block hier, statt an jeder folgenden addWidget/
        # addLayout-Stelle die Zeilennummer von Hand hochzuzaehlen (Quelle
        # sonst leicht uebersehener Ueberlappungen).
        row = 0

        name_edit = QtWidgets.QLineEdit(entry.name)
        name_edit.setFrame(False)
        name_edit.setToolTip("Name dieses ROI (wird auch in der Legende verwendet)")
        name_font = name_edit.font()
        name_font.setBold(True)
        name_font.setPointSize(name_font.pointSize() + 1)
        name_edit.setFont(name_font)
        name_edit.editingFinished.connect(partial(self._on_roi_name_changed, entry))
        grid.addWidget(name_edit, row, 0, 1, 4)
        entry.name_edit = name_edit
        row += 1

        btn_color = QtWidgets.QPushButton()
        btn_color.setFixedSize(20, 20)
        btn_color.setCursor(QtCore.Qt.PointingHandCursor)
        btn_color.setToolTip("Farbe dieses ROI ändern")
        btn_color.setStyleSheet(
            f"background-color:{entry.color}; border:1px solid #333; border-radius:4px;"
        )
        btn_color.clicked.connect(partial(self._on_roi_color_clicked, entry))
        grid.addWidget(btn_color, row, 0)
        entry.btn_color = btn_color

        chk_visible = QtWidgets.QCheckBox("sichtbar")
        chk_visible.setChecked(True)
        chk_visible.toggled.connect(partial(self._on_roi_visibility_toggled, entry))
        grid.addWidget(chk_visible, row, 1)
        entry.chk_visible = chk_visible

        btn_place = QtWidgets.QPushButton("Im Bild platzieren")
        btn_place.setCheckable(True)
        btn_place.toggled.connect(partial(self._on_roi_place_toggled, entry))
        grid.addWidget(btn_place, row, 2, 1, 2)
        entry.btn_place = btn_place
        row += 1

        grid.addWidget(QtWidgets.QLabel("X:"), row, 0)
        spin_x = QtWidgets.QDoubleSpinBox()
        spin_x.setRange(0, 100000)
        spin_x.setDecimals(1)
        grid.addWidget(spin_x, row, 1)
        entry.spin_x = spin_x

        grid.addWidget(QtWidgets.QLabel("Y:"), row, 2)
        spin_y = QtWidgets.QDoubleSpinBox()
        spin_y.setRange(0, 100000)
        spin_y.setDecimals(1)
        grid.addWidget(spin_y, row, 3)
        entry.spin_y = spin_y
        row += 1

        grid.addWidget(QtWidgets.QLabel("Breite:"), row, 0)
        spin_width = QtWidgets.QDoubleSpinBox()
        spin_width.setRange(1, 100000)
        spin_width.setValue(entry.default_size)
        grid.addWidget(spin_width, row, 1)
        entry.spin_width = spin_width

        grid.addWidget(QtWidgets.QLabel("Höhe:"), row, 2)
        spin_height = QtWidgets.QDoubleSpinBox()
        spin_height.setRange(1, 100000)
        spin_height.setValue(entry.default_size)
        grid.addWidget(spin_height, row, 3)
        entry.spin_height = spin_height
        row += 1

        mm_label = QtWidgets.QLabel("")
        mm_label.setVisible(False)
        grid.addWidget(mm_label, row, 0, 1, 4)
        entry.mm_label = mm_label
        row += 1

        buttons_row = QtWidgets.QHBoxLayout()
        btn_apply = QtWidgets.QPushButton("Übernehmen")
        btn_apply.setToolTip("Setzt diesen Messbereich exakt auf die eingegebenen Koordinaten/Größe.")
        btn_apply.clicked.connect(partial(self._on_roi_apply_clicked, entry))
        buttons_row.addWidget(btn_apply)

        btn_reset = QtWidgets.QPushButton("Zurücksetzen")
        btn_reset.setToolTip("Stellt Position & Größe wieder her, wie sie zuletzt gesetzt wurden.")
        btn_reset.clicked.connect(partial(self._on_roi_reset_clicked, entry))
        buttons_row.addWidget(btn_reset)

        btn_square = QtWidgets.QPushButton("Reset")
        btn_square.setToolTip("Setzt Breite und Höhe wieder gleich (Quadrat); Mittelpunkt bleibt erhalten.")
        btn_square.clicked.connect(partial(self._on_roi_square_reset_clicked, entry))
        buttons_row.addWidget(btn_square)
        grid.addLayout(buttons_row, row, 0, 1, 4)
        row += 1

        # -- Verlaufs-Interpolation (Punkt 3) ------------------------------
        chk_interp = QtWidgets.QCheckBox("Position/Größe über Zeit interpolieren (Start → Ende)")
        chk_interp.setToolTip(
            "Wenn aktiv: Position und Größe werden linear über die Zeitachse zwischen einer "
            "Start-Geometrie (erstes Bild) und einer Ende-Geometrie (letztes Bild) berechnet. "
            "Standardmäßig aus -- ohne Aktivierung bleibt der Messbereich wie bisher fest stehen."
        )
        chk_interp.toggled.connect(partial(self._on_roi_interp_toggled, entry))
        grid.addWidget(chk_interp, row, 0, 1, 4)
        entry.chk_interp = chk_interp
        row += 1

        interp_row = QtWidgets.QHBoxLayout()
        btn_interp_start = QtWidgets.QPushButton("Start (erstes Bild) übernehmen")
        btn_interp_start.setToolTip(
            "Zum ersten Bild navigieren, Messbereich dort positionieren, dann hier klicken."
        )
        btn_interp_start.clicked.connect(partial(self._on_roi_interp_capture, entry, True))
        btn_interp_start.setEnabled(False)
        interp_row.addWidget(btn_interp_start)
        entry.btn_interp_start = btn_interp_start

        btn_interp_end = QtWidgets.QPushButton("Ende (letztes Bild) übernehmen")
        btn_interp_end.setToolTip(
            "Zum letzten Bild navigieren, Messbereich dort positionieren, dann hier klicken."
        )
        btn_interp_end.clicked.connect(partial(self._on_roi_interp_capture, entry, False))
        btn_interp_end.setEnabled(False)
        interp_row.addWidget(btn_interp_end)
        entry.btn_interp_end = btn_interp_end
        grid.addLayout(interp_row, row, 0, 1, 4)
        row += 1

        return box

    def _build_toolbar(self) -> None:
        toolbar = self.addToolBar("Steuerung")
        toolbar.setMovable(False)

        act_open_folder = toolbar.addAction("Ordner öffnen…")
        act_open_folder.triggered.connect(self._open_folder)
        act_open_files = toolbar.addAction("Dateien öffnen…")
        act_open_files.triggered.connect(self._open_files)

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

        self.resizeDocks(
            [self.control_dock, self.timeseries_dock], [320, 520], QtCore.Qt.Vertical
        )
        self.resizeDocks([self.control_dock, self.timeseries_dock], [420, 900], QtCore.Qt.Horizontal)

    def _build_menu(self) -> None:
        # Aktionen, die ohne geladene Messreihe ohnehin nur eine "Keine Daten"-
        # Meldung anzeigen wuerden, werden bis zum ersten Laden ausgegraut
        # (siehe _set_recording) -- klarer als ein Klick ins Leere.
        self._requires_recording_actions: list[QtGui.QAction] = []

        file_menu = self.menuBar().addMenu("&Datei")
        act_open_folder = file_menu.addAction("Ordner öffnen…")
        act_open_folder.triggered.connect(self._open_folder)
        act_open_files = file_menu.addAction("Dateien öffnen…")
        act_open_files.triggered.connect(self._open_files)
        file_menu.addSeparator()
        act_save_project = file_menu.addAction("Projekt speichern…")
        act_save_project.setToolTip(
            "Speichert Messbereiche (Position, Name, Farbe), Farbverlauf und Legenden-Limits "
            "in einer Projektdatei."
        )
        act_save_project.triggered.connect(self._save_project)
        act_load_project = file_menu.addAction("Projekt laden…")
        act_load_project.setToolTip(
            "Wendet eine zuvor gespeicherte Projektdatei auf die aktuell geladene Messreihe an."
        )
        act_load_project.triggered.connect(self._load_project)
        file_menu.addSeparator()
        act_quit = file_menu.addAction("Beenden")
        act_quit.triggered.connect(self.close)
        self._requires_recording_actions.append(act_save_project)

        export_menu = self.menuBar().addMenu("&Export")
        act_export_video = export_menu.addAction("Video exportieren…")
        act_export_video.setToolTip("Exportiert einen wählbaren Frame-Bereich als MP4-Video.")
        act_export_video.triggered.connect(self._export_video)
        export_menu.addSeparator()
        act_export_ts_graphic = export_menu.addAction("Zeitverlauf-Grafik exportieren…")
        act_export_ts_graphic.setToolTip(
            "Speichert Thermobild (mit Position der Messbereiche) und Temperaturverlauf "
            "gemeinsam oder getrennt als Grafik(en)."
        )
        act_export_ts_graphic.triggered.connect(self._export_timeseries_graphic)
        act_export_live_graphic = export_menu.addAction("Live-Grafik exportieren…")
        act_export_live_graphic.setToolTip(
            "Speichert Thermobild (mit Position des Cursor-Pixels) und Temperaturverlauf "
            "gemeinsam oder getrennt als Grafik(en)."
        )
        act_export_live_graphic.triggered.connect(self._export_live_graphic)
        export_menu.addSeparator()
        act_export_ts_csv = export_menu.addAction("Zeitverlauf-Werte als CSV…")
        act_export_ts_csv.setToolTip(
            "Speichert die Temperaturwerte aller platzierten Messbereiche über die Zeit als CSV-Datei."
        )
        act_export_ts_csv.triggered.connect(self._export_roi_csv)
        act_export_live_csv = export_menu.addAction("Live-Werte als CSV…")
        act_export_live_csv.setToolTip(
            "Speichert den Temperaturverlauf des Cursor-Pixels über die Zeit als CSV-Datei."
        )
        act_export_live_csv.triggered.connect(self._export_live_csv)
        self._requires_recording_actions.extend([
            act_export_video, act_export_ts_graphic, act_export_live_graphic,
            act_export_ts_csv, act_export_live_csv,
        ])

        tools_menu = self.menuBar().addMenu("&Werkzeuge")
        act_ruler = tools_menu.addAction("Maßstab festlegen…")
        act_ruler.setToolTip(
            "Referenzlinie im Bild einzeichnen und ihre reale Länge in mm angeben, um Messbereich-"
            "Größen zusätzlich in mm anzuzeigen."
        )
        act_ruler.triggered.connect(self._start_ruler_tool)
        self._requires_recording_actions.append(act_ruler)

        for action in self._requires_recording_actions:
            action.setEnabled(False)

        view_menu = self.menuBar().addMenu("&Ansicht")
        view_menu.addAction(self.control_dock.toggleViewAction())
        view_menu.addAction(self.timeseries_dock.toggleViewAction())
        view_menu.addAction(self.live_dock.toggleViewAction())

        view_menu.addSeparator()
        theme_menu = view_menu.addMenu("Design")
        self._theme_actions: dict[str, QtGui.QAction] = {}
        theme_group = QtGui.QActionGroup(self)
        theme_group.setExclusive(True)
        for key, theme in THEMES.items():
            act = theme_menu.addAction(theme["label"])
            act.setCheckable(True)
            act.triggered.connect(partial(self._on_theme_selected, key))
            theme_group.addAction(act)
            self._theme_actions[key] = act

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
            (QtCore.Qt.Key_Home, lambda: self._step_frame(-self.current_index)),
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
    def _on_theme_selected(self, key: str) -> None:
        self._apply_theme(key)
        self._settings.setValue("theme", key)

    def _apply_theme(self, key: str) -> None:
        theme = THEMES[key]
        self._current_theme = key

        app = QtWidgets.QApplication.instance()
        if app is not None:
            app.setStyle("Fusion")
            app.setPalette(self._dark_palette() if key == "dark" else app.style().standardPalette())

        if self._graph_theme_mode == "app":
            self._apply_graph_colors(theme["pg_background"], theme["pg_foreground"])

        if key in self._theme_actions:
            self._theme_actions[key].setChecked(True)

    def _apply_graph_colors(self, bg: str, fg: str) -> None:
        """Setzt Hintergrund-/Vordergrundfarbe der Grafik-Widgets (Thermobild,
        Zeitverlauf, Live-Kurve). Getrennt von _apply_theme, damit die
        Grafik-Darstellung (Punkt 13) unabhängig vom übrigen App-Design
        gewählt werden kann."""
        self.glw.setBackground(bg)
        self.timeseries_plot.setBackground(bg)
        self.live_plot.setBackground(bg)

        for plot_item in (self.plot_item, self.timeseries_plot.getPlotItem(), self.live_plot.getPlotItem()):
            for axis_name in ("left", "bottom", "right", "top"):
                axis = plot_item.getAxis(axis_name)
                axis.setPen(fg)
                axis.setTextPen(fg)

        self.histogram.axis.setPen(fg)
        self.histogram.axis.setTextPen(fg)

        legend = self.timeseries_plot.getPlotItem().legend
        if legend is not None:
            legend.setLabelTextColor(fg)

        self._graph_bg = bg
        self._graph_fg = fg

    def _on_graph_theme_changed(self, _index: int) -> None:
        mode = self.combo_graph_theme.currentData()
        self._graph_theme_mode = mode
        theme_key = self._current_theme if mode == "app" else mode
        theme = THEMES[theme_key]
        self._apply_graph_colors(theme["pg_background"], theme["pg_foreground"])
        self._settings.setValue("graph_theme_mode", mode)

    @staticmethod
    def _dark_palette() -> QtGui.QPalette:
        palette = QtGui.QPalette()
        window = QtGui.QColor("#2b2b2b")
        base = QtGui.QColor("#232323")
        text = QtGui.QColor("#e0e0e0")
        disabled_text = QtGui.QColor("#7a7a7a")
        highlight = QtGui.QColor("#3b82f6")

        palette.setColor(QtGui.QPalette.Window, window)
        palette.setColor(QtGui.QPalette.WindowText, text)
        palette.setColor(QtGui.QPalette.Base, base)
        palette.setColor(QtGui.QPalette.AlternateBase, window)
        palette.setColor(QtGui.QPalette.ToolTipBase, window)
        palette.setColor(QtGui.QPalette.ToolTipText, text)
        palette.setColor(QtGui.QPalette.Text, text)
        palette.setColor(QtGui.QPalette.Button, window)
        palette.setColor(QtGui.QPalette.ButtonText, text)
        palette.setColor(QtGui.QPalette.BrightText, QtGui.QColor("#ff5555"))
        palette.setColor(QtGui.QPalette.Highlight, highlight)
        palette.setColor(QtGui.QPalette.HighlightedText, QtGui.QColor("#ffffff"))
        palette.setColor(QtGui.QPalette.Disabled, QtGui.QPalette.WindowText, disabled_text)
        palette.setColor(QtGui.QPalette.Disabled, QtGui.QPalette.Text, disabled_text)
        palette.setColor(QtGui.QPalette.Disabled, QtGui.QPalette.ButtonText, disabled_text)
        return palette

    def _connect_scene_events(self) -> None:
        self.glw.scene().sigMouseMoved.connect(self._on_scene_mouse_moved)
        self.glw.scene().sigMouseClicked.connect(self._on_scene_mouse_clicked)

    # ------------------------------------------------------------ Laden
    def _open_folder(self) -> None:
        folder = QtWidgets.QFileDialog.getExistingDirectory(self, "Ordner mit CSV-Messreihe wählen")
        if not folder:
            return
        paths = sorted(Path(folder).glob("*.csv"))
        self._load_paths(paths)

    def _open_files(self) -> None:
        files, _ = QtWidgets.QFileDialog.getOpenFileNames(
            self, "CSV-Dateien wählen", filter="CSV-Dateien (*.csv)"
        )
        if not files:
            return
        self._load_paths([Path(f) for f in files])

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
                "index": entry.index,
                "name": entry.name,
                "farbe": entry.color,
                "sichtbar": entry.chk_visible.isChecked(),
                "platziert": entry.placed,
                "interpolation_aktiv": entry.interp_enabled,
            }
            if entry.placed:
                cx, cy = entry.center()
                roi_data["mittelpunkt"] = {"x": cx, "y": cy}
                roi_data["breite_px"] = entry.width()
                roi_data["hoehe_px"] = entry.height()
            if entry.interp_start is not None:
                (sx, sy), (sw, sh) = entry.interp_start
                roi_data["interpolation_start"] = {"x": sx, "y": sy, "breite_px": sw, "hoehe_px": sh}
            if entry.interp_end is not None:
                (ex, ey), (ew, eh) = entry.interp_end
                roi_data["interpolation_ende"] = {"x": ex, "y": ey, "breite_px": ew, "hoehe_px": eh}
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
            "default_roi_size": self.spin_default_size.value(),
            "px_zu_mm": self._px_to_mm,
            "grafik_theme": self.combo_graph_theme.currentData(),
            "rois": rois,
        }

        try:
            Path(path).write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
        except OSError as exc:
            QtWidgets.QMessageBox.critical(self, "Fehler", f"Projekt konnte nicht gespeichert werden:\n{exc}")
            return

        self.statusBar().showMessage(f"Projekt gespeichert: {path}")

    @staticmethod
    def _parse_interp_point(data) -> tuple[tuple[float, float], tuple[float, float]] | None:
        """Parst einen Interpolations-Keyframe ("interpolation_start"/"_ende")
        aus einer Projektdatei. Wirft TypeError/ValueError/KeyError bei
        fehlerhaften/fehlenden Werten, statt sie stillschweigend zu
        uebernehmen -- der Aufrufer faengt das gezielt ab."""
        if data is None:
            return None
        if not isinstance(data, dict):
            raise TypeError("interpolation point must be a dict")
        x = float(data["x"])
        y = float(data["y"])
        w = float(data["breite_px"])
        h = float(data["hoehe_px"])
        return (x, y), (w, h)

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
            QtWidgets.QMessageBox.information(
                self, "Keine Daten", "Bitte zuerst eine Messreihe laden, dann das Projekt anwenden."
            )
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
        radio_by_mode = {
            "manual": self.radio_level_manual,
            "per_frame": self.radio_level_per_frame,
            "global": self.radio_level_global,
        }
        radio_by_mode[level_mode].setChecked(True)
        if level_mode == "manual":
            self.spin_level_min.setValue(data.get("level_min", self.spin_level_min.value()))
            self.spin_level_max.setValue(data.get("level_max", self.spin_level_max.value()))

        self.spin_default_size.setValue(data.get("default_roi_size", DEFAULT_ROI_SIZE))

        px_to_mm = data.get("px_zu_mm")
        self._px_to_mm = float(px_to_mm) if isinstance(px_to_mm, (int, float)) else None
        self._refresh_scale_label()

        graph_theme = data.get("grafik_theme")
        if isinstance(graph_theme, str):
            idx = self.combo_graph_theme.findData(graph_theme)
            if idx >= 0:
                self.combo_graph_theme.setCurrentIndex(idx)

        touched_entries: list[RoiEntry] = []
        failed_indices: list[int] = []
        for roi_data in data.get("rois", []):
            if not isinstance(roi_data, dict):
                continue
            idx = roi_data.get("index")
            if not isinstance(idx, int) or not (0 <= idx < len(self.roi_entries)):
                continue
            entry = self.roi_entries[idx]
            entry_failed = False

            # Grundangaben + Platzierung in einem eigenen try-Block: ein
            # spaeter fehlschlagender Interpolations-Block (siehe unten) darf
            # eine hier bereits erfolgreiche Platzierung nicht mehr rueckgaengig
            # machen bzw. von der Kurven-Neuberechnung ausschliessen.
            try:
                name = roi_data.get("name")
                if name:
                    entry.name_edit.setText(name)
                    self._on_roi_name_changed(entry)

                color = roi_data.get("farbe")
                if color:
                    entry.set_color(color)

                entry.chk_visible.setChecked(roi_data.get("sichtbar", True))

                mittelpunkt = roi_data.get("mittelpunkt")
                if roi_data.get("platziert") and isinstance(mittelpunkt, dict):
                    width = roi_data.get("breite_px")
                    height = roi_data.get("hoehe_px")
                    if width is None or height is None:
                        # Altes Projektformat (Punkt 2): eine einzelne
                        # "groesse" statt getrennter Breite/Hoehe.
                        width = height = roi_data.get("groesse", entry.default_size)
                    cx = float(mittelpunkt.get("x", 0.0))
                    cy = float(mittelpunkt.get("y", 0.0))
                    width = float(width)
                    height = float(height)
                    entry.spin_x.setValue(cx)
                    entry.spin_y.setValue(cy)
                    entry.spin_width.setValue(width)
                    entry.spin_height.setValue(height)
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
            try:
                interp_start = self._parse_interp_point(roi_data.get("interpolation_start"))
                interp_end = self._parse_interp_point(roi_data.get("interpolation_ende"))
                entry.interp_start = interp_start
                entry.interp_end = interp_end
                entry.chk_interp.setChecked(
                    bool(roi_data.get("interpolation_aktiv", False))
                    and interp_start is not None
                    and interp_end is not None
                )
            except (TypeError, ValueError, KeyError):
                entry.interp_start = None
                entry.interp_end = None
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

    def _load_paths(self, paths: list[Path]) -> None:
        if not paths:
            QtWidgets.QMessageBox.warning(self, "Keine Dateien", "Es wurden keine CSV-Dateien gefunden.")
            return

        progress = QtWidgets.QProgressDialog("Lade Frames…", "Abbrechen", 0, len(paths), self)
        progress.setWindowModality(QtCore.Qt.WindowModal)
        progress.setMinimumDuration(300)

        def _cb(done: int, total: int) -> None:
            progress.setValue(done)
            QtWidgets.QApplication.processEvents()

        try:
            recording = load_paths(paths, progress_cb=_cb)
        except RecordingError as exc:
            QtWidgets.QMessageBox.critical(self, "Fehler beim Laden", str(exc))
            return
        finally:
            progress.close()

        self._set_recording(recording)

    def _set_recording(self, recording: Recording) -> None:
        self.recording = recording
        n = recording.n_frames
        rows, cols = recording.shape

        for action in self._requires_recording_actions:
            action.setEnabled(True)

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

        symbol = "o" if n <= MAX_FRAMES_WITH_SYMBOLS else None
        for entry in self.roi_entries:
            entry.spin_x.setRange(0, cols)
            entry.spin_y.setRange(0, rows)
            entry.spin_width.setRange(1, max(1, cols))
            entry.spin_height.setRange(1, max(1, rows))
            entry.curve.setSymbol(symbol)
        self.live_curve.setSymbol(symbol)

        self._hover_row = None
        self._hover_col = None
        self._live_pinned = False
        self.live_cursor_marker.setVisible(False)
        self.live_curve.clear()
        self.live_label.setText(
            "Maus über das Bild bewegen, um den Temperaturverlauf am Cursor-Pixel live zu sehen. "
            "Linksklick fixiert die Stelle, Rechtsklick löst die Fixierung wieder."
        )

        self.view_box.setRange(xRange=(0, cols), yRange=(0, rows), padding=0.02)
        self.current_index = 0
        self._show_frame(0)
        self._recompute_curves()

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

    def _jump_to_last_frame(self) -> None:
        if self.recording is None or self.recording.n_frames == 0:
            return
        self._step_frame(self.recording.n_frames - 1 - self.current_index)

    def _on_slider_changed(self, value: int) -> None:
        # Der Schieberegler ist intern 0-basiert (Frame-Index), das Zahlenfeld
        # daneben zeigt dem Nutzer wie die Statuszeile ("Frame 1/8") bewusst
        # 1-basierte Frame-Nummern, um Verwirrung zu vermeiden.
        self.frame_spin.blockSignals(True)
        self.frame_spin.setValue(value + 1)
        self.frame_spin.blockSignals(False)
        self._show_frame(value)

    def _on_frame_spin_changed(self, value: int) -> None:
        idx = value - 1
        self.frame_slider.blockSignals(True)
        self.frame_slider.setValue(idx)
        self.frame_slider.blockSignals(False)
        self._show_frame(idx)

    def _level_mode(self) -> str:
        checked_id = self.level_mode_group.checkedId()
        if 0 <= checked_id < len(LEVEL_MODES):
            return LEVEL_MODES[checked_id]
        return "per_frame"

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

    def _apply_levels_for_frame(self, frame: np.ndarray) -> None:
        mode = self._level_mode()
        if mode == "per_frame":
            self.image_item.setImage(frame, autoLevels=True)
            lo, hi = self.image_item.getLevels()
            self._set_level_spins(lo, hi)
        elif mode == "global" and self._global_level_range is not None:
            lo, hi = self._global_level_range
            self.image_item.setImage(frame, autoLevels=False)
            self.image_item.setLevels((lo, hi))
            self._set_level_spins(lo, hi)
        else:
            self.image_item.setImage(frame, autoLevels=False)
            self.image_item.setLevels((self.spin_level_min.value(), self.spin_level_max.value()))

    @staticmethod
    def _time_fraction(t: float, t0: float, t1: float) -> float:
        """Zeitanteil von t zwischen t0 und t1, geklemmt auf [0, 1]. Gemeinsam
        genutzt von _update_interpolated_rois (Anzeige) und _recompute_curves
        (Kurvenberechnung), damit beide garantiert dieselbe Interpolations-
        Formel verwenden."""
        span = t1 - t0
        if span <= 0:
            return 0.0
        return max(0.0, min(1.0, (t - t0) / span))

    def _update_interpolated_rois(self, idx: int, unix: np.ndarray) -> None:
        t0, t1 = unix[0], unix[-1]
        for entry in self.roi_entries:
            if not entry.is_interp_ready():
                continue
            frac = self._time_fraction(unix[idx], t0, t1)
            entry.apply_interp_frame(frac)
            self._sync_roi_spinboxes(entry)

    def _show_frame(self, idx: int) -> None:
        if self.recording is None or self.recording.n_frames == 0:
            return
        idx = max(0, min(idx, self.recording.n_frames - 1))
        self.current_index = idx
        frame = self.recording.frames[idx]

        self._apply_levels_for_frame(frame)

        ts = self.recording.timestamps[idx]
        self.timestamp_label.setText("  " + ts.strftime("%Y-%m-%d %H:%M:%S"))

        unix = self.recording.unix_seconds()
        self.frame_marker.setValue(unix[idx])
        self.live_frame_marker.setValue(unix[idx])

        self._update_interpolated_rois(idx, unix)

        self._update_status_bar()

    def _on_play_toggled(self, checked: bool) -> None:
        if checked:
            if self.recording is None or self.recording.n_frames < 2:
                self.play_button.setChecked(False)
                return
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
        nxt = self.current_index + 1
        if nxt >= self.recording.n_frames:
            self.play_button.setChecked(False)
            return
        self.frame_slider.setValue(nxt)

    def _on_fps_changed(self, value: float) -> None:
        if self.play_timer.isActive():
            self.play_timer.setInterval(int(1000 / max(0.1, value)))

    # -------------------------------------------------------------- ROI
    def _on_default_size_changed(self, value: float) -> None:
        for entry in self.roi_entries:
            entry.default_size = value
            if not entry.placed:
                self._set_widget_value(entry.spin_width, value)
                self._set_widget_value(entry.spin_height, value)

    def _on_roi_name_changed(self, entry: RoiEntry) -> None:
        name = entry.name_edit.text().strip()
        if not name:
            name = f"ROI {entry.index + 1}"
            entry.name_edit.setText(name)
        entry.set_name(name)
        legend = self.timeseries_plot.getPlotItem().legend
        label = legend.getLabel(entry.curve) if legend is not None else None
        if label is not None:
            label.setText(name)

    def _on_roi_color_clicked(self, entry: RoiEntry) -> None:
        color = QtWidgets.QColorDialog.getColor(
            QtGui.QColor(entry.color), self, f"Farbe für {entry.name}"
        )
        if not color.isValid():
            return
        entry.set_color(color.name())

    def _on_roi_visibility_toggled(self, entry: RoiEntry, checked: bool) -> None:
        entry.roi.setVisible(checked and entry.placed)
        entry.curve.setVisible(checked and entry.placed)
        entry.label.setVisible(checked and entry.placed)

    def _on_roi_place_toggled(self, entry: RoiEntry, checked: bool) -> None:
        if checked:
            for other in self.roi_entries:
                if other is not entry and other.btn_place.isChecked():
                    other.btn_place.blockSignals(True)
                    other.btn_place.setChecked(False)
                    other.btn_place.blockSignals(False)
            if self._ruler_armed:
                # Ruler- und ROI-Platzieren-Modus schliessen sich aus, sonst
                # wuerde ein Bildklick unbemerkt vom jeweils anderen Modus
                # "geschluckt" (siehe _on_scene_mouse_clicked).
                self._cancel_ruler_tool()
            self._armed_entry = entry
            self.statusBar().showMessage(f"{entry.name}: Klick ins Bild zum Platzieren.")
        elif self._armed_entry is entry:
            self._armed_entry = None

    def _on_roi_apply_clicked(self, entry: RoiEntry) -> None:
        if self.recording is None:
            QtWidgets.QMessageBox.information(self, "Keine Daten", "Bitte zuerst eine Messreihe laden.")
            return
        entry.place(entry.spin_x.value(), entry.spin_y.value(), entry.spin_width.value(), entry.spin_height.value())
        self._sync_roi_spinboxes(entry)
        self._recompute_curves(entries=[entry])

    def _on_roi_reset_clicked(self, entry: RoiEntry) -> None:
        if entry.snapshot is None:
            return
        entry.reset()
        self._sync_roi_spinboxes(entry)
        self._recompute_curves(entries=[entry])

    def _on_roi_square_reset_clicked(self, entry: RoiEntry) -> None:
        if not entry.placed:
            return
        side = max(entry.width(), entry.height())
        cx, cy = entry.center()
        entry.place(cx, cy, side, side)
        self._sync_roi_spinboxes(entry)
        self._recompute_curves(entries=[entry])

    def _on_roi_interp_toggled(self, entry: RoiEntry, checked: bool) -> None:
        entry.interp_enabled = checked
        entry.btn_interp_start.setEnabled(checked)
        entry.btn_interp_end.setEnabled(checked)
        if not checked:
            # Beim Deaktivieren bleibt der Messbereich an der zuletzt
            # interpolierten Stelle stehen (statische Fortsetzung); die
            # Start-/Ende-Keyframes bleiben erhalten, falls die Interpolation
            # spaeter wieder aktiviert wird.
            entry.snapshot = (tuple(entry.roi.pos()), tuple(entry.roi.size()))
        self._recompute_curves(entries=[entry])

    def _on_roi_interp_capture(self, entry: RoiEntry, is_start: bool) -> None:
        if self.recording is None or not entry.placed:
            return
        if is_start:
            entry.capture_interp_start()
            expected_idx = 0
        else:
            entry.capture_interp_end()
            expected_idx = self.recording.n_frames - 1
        label = "Start" if is_start else "Ende"
        if self.current_index != expected_idx:
            self.statusBar().showMessage(
                f"{entry.name}: {label}-Position übernommen. Hinweis: Sie befinden sich gerade nicht "
                f"auf {'dem ersten' if is_start else 'dem letzten'} Bild (aktuell Frame "
                f"{self.current_index + 1}).",
                6000,
            )
        else:
            self.statusBar().showMessage(f"{entry.name}: {label}-Position übernommen.", 4000)
        if entry.interp_start is not None and entry.interp_end is not None:
            self._recompute_curves(entries=[entry])

    def _on_roi_region_changed(self, entry: RoiEntry, *_args) -> None:
        self._sync_roi_spinboxes(entry)
        entry.sync_label_pos()

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
        t0, t1 = (unix[0], unix[-1]) if len(unix) else (0.0, 0.0)
        for entry in entries:
            if not entry.placed:
                continue
            if entry.is_interp_ready():
                values = np.empty(len(unix), dtype=np.float32)
                for i in range(len(unix)):
                    frac = self._time_fraction(unix[i], t0, t1)
                    x, y, w, h = entry.interp_rect(frac)
                    row0, row1, col0, col1 = bounds_px_for(x, y, w, h, shape)
                    values[i] = self.recording.frames[i, row0:row1, col0:col1].mean()
            else:
                row0, row1, col0, col1 = entry.bounds_px(shape)
                values = self.recording.frames[:, row0:row1, col0:col1].mean(axis=(1, 2))
            entry.curve.setData(unix, values)
            entry.curve.setVisible(entry.chk_visible.isChecked())

    # ---------------------------------------------------------- Legende
    def _apply_colormap(self) -> None:
        name = COLORMAPS[self.combo_cmap.currentIndex()][1]
        cmap = pg.colormap.get(name)
        if self.chk_cmap_invert.isChecked():
            cmap.reverse()
        self.histogram.gradient.setColorMap(cmap)

    def _on_colormap_changed(self, _index: int) -> None:
        self._apply_colormap()

    def _on_colormap_invert_toggled(self, _checked: bool) -> None:
        self._apply_colormap()

    def _on_level_mode_changed(self, _button: QtWidgets.QAbstractButton, checked: bool) -> None:
        if not checked:
            return
        manual = self._level_mode() == "manual"
        self.spin_level_min.setEnabled(manual)
        self.spin_level_max.setEnabled(manual)
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
        self._ruler_armed = True
        self._ruler_start = None
        if self._ruler_line is not None:
            self._ruler_line.setVisible(False)
        self.statusBar().showMessage("Maßstab: Startpunkt der Referenzlinie im Bild anklicken.")

    def _cancel_ruler_tool(self) -> None:
        self._ruler_armed = False
        self._ruler_start = None
        if self._ruler_line is not None:
            self._ruler_line.setVisible(False)

    def _clear_ruler_scale(self) -> None:
        self._px_to_mm = None
        self._refresh_scale_label()
        for entry in self.roi_entries:
            self._update_roi_mm_label(entry)

    def _refresh_scale_label(self) -> None:
        if self._px_to_mm is None:
            self.scale_label.setText("Kein Maßstab definiert.")
            self.btn_scale_clear.setEnabled(False)
        else:
            self.scale_label.setText(f"1 px ≈ {self._format_de(self._px_to_mm, 4)} mm")
            self.btn_scale_clear.setEnabled(True)

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
            if self._ruler_line is None:
                self._ruler_line = pg.PlotDataItem(pen=pg.mkPen("#facc15", width=2, style=QtCore.Qt.DashLine))
                self._ruler_line.setZValue(11)
                self.view_box.addItem(self._ruler_line)
            self._ruler_line.setData([point[0]], [point[1]])
            self._ruler_line.setVisible(True)
            self.statusBar().showMessage("Maßstab: jetzt den Endpunkt der Referenzlinie anklicken.")
            return

        start = self._ruler_start
        end = point
        self._ruler_line.setData([start[0], end[0]], [start[1], end[1]])
        pixel_distance = ((end[0] - start[0]) ** 2 + (end[1] - start[1]) ** 2) ** 0.5
        self._ruler_armed = False
        self._ruler_start = None
        self._ruler_line.setVisible(False)

        if pixel_distance < 1e-6:
            QtWidgets.QMessageBox.information(
                self, "Maßstab", "Start- und Endpunkt liegen zu nah beieinander, bitte erneut versuchen."
            )
            return

        mm_value, ok = QtWidgets.QInputDialog.getDouble(
            self, "Maßstab", "Länge dieser Linie in mm:", 10.0, 0.001, 1_000_000.0, 3
        )
        if not ok or mm_value <= 0:
            return
        self._px_to_mm = mm_value / pixel_distance
        self._refresh_scale_label()
        for entry in self.roi_entries:
            self._update_roi_mm_label(entry)
        self.statusBar().showMessage(
            f"Maßstab gesetzt: 1 px ≈ {self._format_de(self._px_to_mm, 4)} mm", 5000
        )

    # ------------------------------------------------------- Maus/Bild
    def _on_scene_mouse_clicked(self, event) -> None:
        if self.recording is None:
            return

        if self._ruler_armed:
            self._handle_ruler_click(event)
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

    def _update_live_cursor(self, row: int, col: int) -> None:
        self._hover_row, self._hover_col = row, col
        values = self.recording.frames[:, row, col]
        self.live_curve.setData(self.recording.unix_seconds(), values)
        self.live_label.setText(f"Cursor-Pixel: Zeile {row}, Spalte {col}")
        self.live_cursor_marker.setData([col + 0.5], [row + 0.5])
        self.live_cursor_marker.setVisible(True)
        self._update_status_bar()

    def _update_status_bar(self) -> None:
        if self.recording is None:
            return
        idx = self.current_index
        ts = self.recording.timestamps[idx].strftime("%Y-%m-%d %H:%M:%S")
        msg = f"Frame {idx + 1}/{self.recording.n_frames}  |  {ts}"
        if self._hover_row is not None and self._hover_col is not None:
            val = self.recording.frames[idx, self._hover_row, self._hover_col]
            msg += f"  |  Cursor: Zeile {self._hover_row}, Spalte {self._hover_col} = {val:.2f} °C"
        self.statusBar().showMessage(msg)

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

    @staticmethod
    def _scaled_size(widget: QtWidgets.QWidget, scale: float) -> tuple[int, int]:
        """Zielgroesse in Geraete-Pixeln fuer den Export eines Widgets mit
        gegebenem DPI-Skalierungsfaktor -- gemeinsam genutzt von
        _render_widget_image (Raster) und _save_widget_svg (Vektor), damit
        beide garantiert dieselbe Groesse fuer dasselbe Widget/denselben
        Faktor berechnen."""
        size = widget.size()
        width = max(1, round(size.width() * scale))
        height = max(1, round(size.height() * scale))
        return width, height

    @staticmethod
    def _render_widget_image(widget: QtWidgets.QWidget, scale: float, background: QtGui.QColor) -> QtGui.QImage:
        width_px, height_px = MainWindow._scaled_size(widget, scale)

        image = QtGui.QImage(width_px, height_px, QtGui.QImage.Format_ARGB32)
        image.fill(background)

        painter = QtGui.QPainter(image)
        painter.setRenderHint(QtGui.QPainter.Antialiasing)
        painter.scale(scale, scale)
        widget.render(painter)
        painter.end()
        return image

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
    def _combined_layout(dpi: int, top_size: tuple[int, int], bottom_size: tuple[int, int]) -> dict:
        """Gemeinsame Layout-Berechnung (Ränder/Zwischenraum/Titelhöhe/
        Gesamtgröße/Titel-Schrift) für die kombinierte Bild+Kurve-Grafik --
        von _stack_images_vertically (Raster) UND _save_combined_svg (Vektor)
        genutzt, damit beide exakt dasselbe Layout erzeugen."""
        margin = round(dpi * 0.15)
        gap = round(dpi * 0.12)
        title_height = round(dpi * 0.3)
        top_w, top_h = top_size
        bottom_w, bottom_h = bottom_size
        width = max(top_w, bottom_w) + 2 * margin
        height = 2 * margin + 2 * title_height + gap + top_h + bottom_h
        font = QtGui.QFont()
        font.setBold(True)
        font.setPointSizeF(max(9.0, dpi / 8.0))
        return {
            "margin": margin, "gap": gap, "title_height": title_height,
            "width": width, "height": height, "font": font,
        }

    @staticmethod
    def _centered_x(layout: dict, element_width: int) -> int:
        margin, width = layout["margin"], layout["width"]
        return margin + (width - 2 * margin - element_width) // 2

    @staticmethod
    def _stack_images_vertically(
        image_top: QtGui.QImage,
        title_top: str,
        image_bottom: QtGui.QImage,
        title_bottom: str,
        dpi: int,
        background: QtGui.QColor,
        foreground: QtGui.QColor,
    ) -> QtGui.QImage:
        """Setzt zwei bereits gerenderte Grafiken (Bild oben, Kurve unten) mit
        Überschriften untereinander zu einer Gesamtgrafik zusammen, damit
        Messposition und Temperaturverlauf gemeinsam sichtbar sind. Hintergrund-
        und Schriftfarbe folgen der aktuellen Grafik-Darstellung (Punkt 13),
        sonst wirkt die Grafik im Dunkel-Modus wie ein dunkler Fleck auf
        weissem Papier."""
        layout = MainWindow._combined_layout(
            dpi, (image_top.width(), image_top.height()), (image_bottom.width(), image_bottom.height())
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

        y = margin
        text_rect = QtCore.QRect(margin, y, width - 2 * margin, title_height)
        painter.drawText(text_rect, QtCore.Qt.AlignLeft | QtCore.Qt.AlignVCenter, title_top)
        y += title_height
        painter.drawImage(MainWindow._centered_x(layout, image_top.width()), y, image_top)
        y += image_top.height() + gap

        text_rect = QtCore.QRect(margin, y, width - 2 * margin, title_height)
        painter.drawText(text_rect, QtCore.Qt.AlignLeft | QtCore.Qt.AlignVCenter, title_bottom)
        y += title_height
        painter.drawImage(MainWindow._centered_x(layout, image_bottom.width()), y, image_bottom)

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

    @staticmethod
    def _save_widget_svg(widget: QtWidgets.QWidget, path: Path, scale: float) -> tuple[int, int]:
        width, height = MainWindow._scaled_size(widget, scale)
        generator = QtSvg.QSvgGenerator()
        generator.setFileName(str(path))
        generator.setSize(QtCore.QSize(width, height))
        generator.setViewBox(QtCore.QRect(0, 0, width, height))
        generator.setTitle("Thermo-Sequenz-Viewer Export")
        painter = QtGui.QPainter(generator)
        painter.setRenderHint(QtGui.QPainter.Antialiasing)
        painter.scale(scale, scale)
        widget.render(painter)
        painter.end()
        MainWindow._verify_file_written(path)
        return width, height

    @staticmethod
    def _save_combined_svg(
        path: Path,
        image_widget: QtWidgets.QWidget,
        title_top: str,
        curve_widget: QtWidgets.QWidget,
        title_bottom: str,
        dpi: int,
        foreground: QtGui.QColor,
    ) -> tuple[int, int]:
        """SVG-Entsprechung von _stack_images_vertically: zeichnet beide
        Widgets direkt (statt vorgerenderter QImages) auf einen gemeinsamen
        QSvgGenerator, damit z.B. der Kurvenverlauf als echte Vektorpfade
        statt als eingebettete Rastergrafik im SVG landet."""
        scale = dpi / 96.0
        img_w, img_h = MainWindow._scaled_size(image_widget, scale)
        curve_w, curve_h = MainWindow._scaled_size(curve_widget, scale)
        layout = MainWindow._combined_layout(dpi, (img_w, img_h), (curve_w, curve_h))
        margin, gap, title_height = layout["margin"], layout["gap"], layout["title_height"]
        width, height = layout["width"], layout["height"]

        generator = QtSvg.QSvgGenerator()
        generator.setFileName(str(path))
        generator.setSize(QtCore.QSize(width, height))
        generator.setViewBox(QtCore.QRect(0, 0, width, height))
        generator.setTitle("Thermo-Sequenz-Viewer Export")

        painter = QtGui.QPainter(generator)
        painter.setRenderHint(QtGui.QPainter.Antialiasing)
        painter.setFont(layout["font"])
        painter.setPen(foreground)

        y = margin
        painter.drawText(
            QtCore.QRect(margin, y, width - 2 * margin, title_height),
            QtCore.Qt.AlignLeft | QtCore.Qt.AlignVCenter, title_top,
        )
        y += title_height
        painter.save()
        painter.translate(MainWindow._centered_x(layout, img_w), y)
        painter.scale(scale, scale)
        image_widget.render(painter)
        painter.restore()
        y += img_h + gap

        painter.drawText(
            QtCore.QRect(margin, y, width - 2 * margin, title_height),
            QtCore.Qt.AlignLeft | QtCore.Qt.AlignVCenter, title_bottom,
        )
        y += title_height
        painter.save()
        painter.translate(MainWindow._centered_x(layout, curve_w), y)
        painter.scale(scale, scale)
        curve_widget.render(painter)
        painter.restore()

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
            return self._save_widget_svg(widget, path, scale)
        image = self._render_widget_image(widget, scale, background)
        if not image.save(str(path)):
            raise OSError(f"Konnte Bild nicht speichern: {path}")
        return image.width(), image.height()

    def _export_timeseries_graphic(self) -> None:
        self._export_combined_image(
            self.timeseries_plot, "Zeitverlauf_mit_Position.png", self._timeseries_metadata,
            "Temperaturverlauf (Messbereiche)",
        )

    def _export_live_graphic(self) -> None:
        self._export_combined_image(
            self.live_plot, "Live-Verlauf_mit_Position.png", self._live_metadata,
            "Live-Temperaturverlauf (Cursor-Pixel)",
        )

    def _export_combined_image(
        self, curve_widget: pg.PlotWidget, suggested_name: str, metadata_fn, curve_title: str
    ) -> None:
        """Speichert Thermobild (mit Position der Messbereiche/des Cursors)
        und den zugehörigen Temperaturverlauf -- wahlweise kombiniert als eine
        Grafik oder getrennt als zwei Dateien (Punkt 5)."""
        if self.recording is None:
            QtWidgets.QMessageBox.information(self, "Keine Daten", "Bitte zuerst eine Messreihe laden.")
            return

        export_dialog = GraphicExportDialog(self, self._settings, default_dpi=150)
        if export_dialog.exec() != QtWidgets.QDialog.DialogCode.Accepted:
            return
        dpi = export_dialog.dpi()
        separate = export_dialog.separate()

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
            return
        if not Path(path).suffix:
            path += filters.get(selected_filter, ".png")
        path_obj = Path(path)
        is_svg = path_obj.suffix.lower() == ".svg"

        bg = QtGui.QColor(self._graph_bg)
        fg = QtGui.QColor(self._graph_fg)
        scale = dpi / 96.0

        saved_paths: list[Path]
        sizes_px: dict[str, tuple[int, int]] = {}
        try:
            if separate:
                image_path = path_obj.with_name(f"{path_obj.stem}_Bild{path_obj.suffix}")
                curve_path = path_obj.with_name(f"{path_obj.stem}_Kurve{path_obj.suffix}")
                sizes_px[image_path.name] = self._save_single_part(self.glw, image_path, scale, bg, is_svg)
                sizes_px[curve_path.name] = self._save_single_part(curve_widget, curve_path, scale, bg, is_svg)
                saved_paths = [image_path, curve_path]
            elif is_svg:
                sizes_px[path_obj.name] = self._save_combined_svg(
                    path_obj, self.glw, "Position im Thermobild", curve_widget, curve_title, dpi, fg
                )
                saved_paths = [path_obj]
            else:
                image_scene = self._render_widget_image(self.glw, scale, bg)
                image_curve = self._render_widget_image(curve_widget, scale, bg)
                combined = self._stack_images_vertically(
                    image_scene, "Position im Thermobild", image_curve, curve_title, dpi, bg, fg
                )
                if not combined.save(path):
                    raise OSError(f"Konnte Bild nicht speichern: {path}")
                sizes_px[path_obj.name] = (combined.width(), combined.height())
                saved_paths = [path_obj]
        except OSError as exc:
            QtWidgets.QMessageBox.critical(self, "Fehler", f"Konnte Grafik nicht speichern:\n{exc}")
            return

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
        meta_path.write_text(json.dumps(metadata, indent=2, ensure_ascii=False), encoding="utf-8")

        self.statusBar().showMessage(
            f"Grafik gespeichert: {', '.join(p.name for p in saved_paths)}  |  Metadaten: {meta_path.name}"
        )

    def _export_roi_csv(self) -> None:
        if self.recording is None:
            QtWidgets.QMessageBox.information(self, "Keine Daten", "Bitte zuerst eine Messreihe laden.")
            return
        placed_entries = [e for e in self.roi_entries if e.placed]
        if not placed_entries:
            QtWidgets.QMessageBox.information(
                self, "Keine Messbereiche", "Es ist kein Messbereich platziert."
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
        column_dialog = CsvColumnDialog(self, dialog_entries)
        if column_dialog.exec() != QtWidgets.QDialog.DialogCode.Accepted:
            return
        column_names = column_dialog.column_names()

        path, _ = QtWidgets.QFileDialog.getSaveFileName(
            self, "ROI-Werte speichern", "ROI-Werte.csv", "CSV-Datei (*.csv)"
        )
        if not path:
            return
        if not Path(path).suffix:
            path += ".csv"

        t0 = self.recording.timestamps[0]
        curves = [entry.curve.getData() for entry in placed_entries]
        header = ["Laufzeit", "Zeitstempel"] + column_names

        with open(path, "w", newline="", encoding="utf-8-sig") as f:
            writer = csv.writer(f, delimiter=";")
            writer.writerow(header)
            for i, ts in enumerate(self.recording.timestamps):
                runtime = self._format_relative_runtime((ts - t0).total_seconds())
                row = [runtime, ts.strftime("%Y-%m-%d %H:%M:%S")]
                for _, y in curves:
                    row.append(self._format_csv_number(float(y[i])))
                writer.writerow(row)

        self.statusBar().showMessage(f"ROI-Werte gespeichert: {path}")

    def _export_live_csv(self) -> None:
        if self.recording is None:
            QtWidgets.QMessageBox.information(self, "Keine Daten", "Bitte zuerst eine Messreihe laden.")
            return
        if self._hover_row is None or self._hover_col is None:
            QtWidgets.QMessageBox.information(
                self,
                "Kein Cursor-Pixel",
                "Bitte zuerst mit der Maus über das Bild fahren (oder eine Stelle per "
                "Linksklick fixieren), um ein Pixel für den Live-Verlauf auszuwählen.",
            )
            return

        path, _ = QtWidgets.QFileDialog.getSaveFileName(
            self, "Live-Werte speichern", "Live-Werte.csv", "CSV-Datei (*.csv)"
        )
        if not path:
            return
        if not Path(path).suffix:
            path += ".csv"

        row, col = self._hover_row, self._hover_col
        t0 = self.recording.timestamps[0]
        values = self.recording.frames[:, row, col]

        with open(path, "w", newline="", encoding="utf-8-sig") as f:
            writer = csv.writer(f, delimiter=";")
            writer.writerow(["Laufzeit", "Zeitstempel", "Zeile", "Spalte", "Temperatur (°C)"])
            for i, ts in enumerate(self.recording.timestamps):
                runtime = self._format_relative_runtime((ts - t0).total_seconds())
                writer.writerow(
                    [runtime, ts.strftime("%Y-%m-%d %H:%M:%S"), row, col, self._format_csv_number(float(values[i]))]
                )

        self.statusBar().showMessage(f"Live-Werte gespeichert: {path}")

    def _capture_level_widgets_state(self) -> dict:
        """Schnappschuss von Farbverlauf/Skalierung, um ihn nach einem
        temporaeren Overrride (z.B. eigene Video-Export-Einstellungen)
        symmetrisch per _apply_level_widgets_state wiederherzustellen."""
        return {
            "cmap_index": self.combo_cmap.currentIndex(),
            "invert": self.chk_cmap_invert.isChecked(),
            "level_button": self.level_mode_group.checkedButton(),
            "level_min": self.spin_level_min.value(),
            "level_max": self.spin_level_max.value(),
        }

    def _apply_level_widgets_state(self, state: dict) -> None:
        self._set_widget_value(self.combo_cmap, state["cmap_index"], "setCurrentIndex")
        self._set_widget_value(self.chk_cmap_invert, state["invert"], "setChecked")
        self._apply_colormap()

        level_button = state["level_button"]
        if level_button is not None:
            self._set_widget_value(level_button, True, "setChecked")
        manual = level_button is self.radio_level_manual
        self.spin_level_min.setEnabled(manual)
        self.spin_level_max.setEnabled(manual)
        self._set_widget_value(self.spin_level_min, state["level_min"])
        self._set_widget_value(self.spin_level_max, state["level_max"])

    def _export_video(self) -> None:
        if self.recording is None or self.recording.n_frames == 0:
            QtWidgets.QMessageBox.information(self, "Keine Daten", "Bitte zuerst eine Messreihe laden.")
            return

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
        )
        if dialog.exec() != QtWidgets.QDialog.DialogCode.Accepted:
            return

        try:
            import imageio.v2 as imageio
        except ImportError:
            QtWidgets.QMessageBox.critical(
                self,
                "Fehlende Abhängigkeit",
                "Für den Video-Export wird das Paket 'imageio' (mit 'imageio-ffmpeg') benötigt, "
                "das in dieser Installation nicht verfügbar ist.",
            )
            return

        start_idx, end_idx = dialog.frame_range()
        fps = dialog.fps()
        show_legend = dialog.show_legend()
        use_custom = dialog.use_custom_settings()

        path, _ = QtWidgets.QFileDialog.getSaveFileName(
            self, "Video speichern", "Thermo-Video.mp4", "MP4-Video (*.mp4)"
        )
        if not path:
            return
        if not Path(path).suffix:
            path += ".mp4"

        # Aktuellen Anzeigezustand sichern, um ihn nach dem Export wiederherzustellen.
        prev_index = self.current_index
        prev_histogram_visible = self.histogram.isVisible()
        prev_level_state = self._capture_level_widgets_state()

        if use_custom:
            mode = dialog.custom_level_mode()
            target_radio = {
                "manual": self.radio_level_manual,
                "per_frame": self.radio_level_per_frame,
                "global": self.radio_level_global,
            }[mode]
            custom_min, custom_max = dialog.custom_min_max()
            self._apply_level_widgets_state({
                "cmap_index": dialog.custom_colormap_index(),
                "invert": dialog.custom_invert(),
                "level_button": target_radio,
                # Bei "pro Bild"/"gesamte Serie" spielen level_min/max fuer die
                # Anzeige keine Rolle (werden pro Frame ueberschrieben) -- nur
                # im manuellen Modus sind die eigenen Grenzwerte relevant.
                "level_min": custom_min if mode == "manual" else prev_level_state["level_min"],
                "level_max": custom_max if mode == "manual" else prev_level_state["level_max"],
            })

        self.histogram.setVisible(show_legend)

        frame_indices = list(range(start_idx, end_idx + 1))
        progress = QtWidgets.QProgressDialog("Video wird erstellt…", "Abbrechen", 0, len(frame_indices), self)
        progress.setWindowModality(QtCore.Qt.WindowModal)
        progress.setMinimumDuration(300)

        bg = QtGui.QColor(self._graph_bg)
        scale = 2.0  # feste, ordentliche Aufloesung fuer Video-Frames
        cancelled = False
        error_message: str | None = None
        try:
            with imageio.get_writer(path, fps=fps) as writer:
                for n, idx in enumerate(frame_indices):
                    if progress.wasCanceled():
                        cancelled = True
                        break
                    self._show_frame(idx)
                    image = self._render_widget_image(self.glw, scale, bg)
                    writer.append_data(self._qimage_to_rgb_array(image))
                    progress.setValue(n + 1)
                    QtWidgets.QApplication.processEvents()
        except (OSError, RuntimeError, ValueError) as exc:
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
            Path(path).unlink(missing_ok=True)
            QtWidgets.QMessageBox.critical(self, "Fehler", f"Video konnte nicht gespeichert werden:\n{error_message}")
            return
        if cancelled:
            Path(path).unlink(missing_ok=True)
            self.statusBar().showMessage("Video-Export abgebrochen.")
            return

        self.statusBar().showMessage(f"Video gespeichert: {path}")

    def _timeseries_metadata(self) -> dict:
        rows, cols = self.recording.shape
        rois = []
        for entry in self.roi_entries:
            roi_info: dict = {
                "index": entry.index + 1,
                "name": entry.name,
                "farbe": entry.color,
                "sichtbar": entry.chk_visible.isChecked(),
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

        return {
            "quellordner": str(self.recording.paths[0].parent) if self.recording.paths else None,
            "anzahl_frames": self.recording.n_frames,
            "bild_groesse_px": {"zeilen": rows, "spalten": cols},
            "zeitstempel": [t.isoformat() for t in self.recording.timestamps],
            "px_zu_mm": self._px_to_mm,
            "rois": rois,
        }

    def _live_metadata(self) -> dict:
        cursor = None
        if self._hover_row is not None and self._hover_col is not None:
            cursor = {"zeile": self._hover_row, "spalte": self._hover_col}
        return {
            "quellordner": str(self.recording.paths[0].parent) if self.recording.paths else None,
            "anzahl_frames": self.recording.n_frames,
            "zeitstempel": [t.isoformat() for t in self.recording.timestamps],
            "cursor_pixel": cursor,
        }
