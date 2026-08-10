"""Hauptfenster: Thermobild links, ROI-/Legenden-Steuerung und
Zeitverlauf/Live-Cursor rechts als andockbare, frei in der Breite
verstellbare Panels.
"""
from __future__ import annotations

import json
from datetime import datetime
from functools import partial
from pathlib import Path

import numpy as np
import pyqtgraph as pg
from qtpy import QtCore, QtGui, QtWidgets

from .assets import ICON_PATH
from .data import Recording, RecordingError, load_paths
from .roi import SquareROI

pg.setConfigOptions(imageAxisOrder="row-major", antialias=True)

ROI_COLORS = ["#ef4444", "#22c55e", "#3b82f6", "#eab308", "#a855f7"]

COLORMAPS = [
    ("Ironbow", "CET-L17"),
    ("Inferno", "inferno"),
    ("Plasma", "plasma"),
    ("Viridis", "viridis"),
    ("Magma", "magma"),
    ("Turbo", "turbo"),
]

DEFAULT_ROI_SIZE = 20.0
# Ab so vielen Frames werden Punktmarker auf den Kurven ausgeblendet (nur
# noch Linie), damit es bei langen Aufnahmen nicht überladen wirkt. Bei
# wenigen Frames (z.B. nur 1) sind Marker nötig, sonst ist gar nichts zu
# sehen -- eine Linie braucht mindestens zwei Punkte.
MAX_FRAMES_WITH_SYMBOLS = 60


class RoiEntry:
    """Bündelt ein quadratisches ROI im Bild mit seiner Kurve im Zeitverlauf
    und den zugehörigen Steuer-Widgets im rechten Panel."""

    def __init__(self, index: int, color: str, view_box: pg.ViewBox, curve: pg.PlotDataItem):
        self.index = index
        self.color = color
        self.name = f"ROI {index + 1}"
        self.curve = curve
        self.default_size = DEFAULT_ROI_SIZE
        self.placed = False
        self.snapshot: tuple[tuple[float, float], tuple[float, float]] | None = None

        pen = pg.mkPen(color, width=2)
        hover_pen = pg.mkPen(color, width=3)
        self.roi = SquareROI([0, 0], DEFAULT_ROI_SIZE, pen=pen, hoverPen=hover_pen, removable=False)
        self.roi.setVisible(False)
        view_box.addItem(self.roi)

        # Namensbeschriftung direkt im Bild, oben links über dem ROI-Quadrat.
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
        self.spin_size: QtWidgets.QDoubleSpinBox | None = None

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

    def place(self, center_x: float, center_y: float, size: float) -> None:
        size = max(size, 1.0)
        pos = (center_x - size / 2, center_y - size / 2)
        self.roi.setSize([size, size])
        self.roi.setPos(list(pos))
        self.placed = True
        self.snapshot = (pos, (size, size))
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

    def size(self) -> float:
        w, h = self.roi.size()
        return max(w, h)

    def bounds_px(self, grid_shape: tuple[int, int]) -> tuple[int, int, int, int]:
        return self.roi.bounds_px(grid_shape)


class MainWindow(QtWidgets.QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("Thermo-Sequenz-Viewer")
        self.setWindowIcon(QtGui.QIcon(str(ICON_PATH)))
        self.resize(1600, 950)

        self.recording: Recording | None = None
        self.current_index = 0
        self._armed_entry: RoiEntry | None = None
        self._hover_row: int | None = None
        self._hover_col: int | None = None
        self.roi_entries: list[RoiEntry] = []

        self._build_image_canvas()
        self._build_plots()
        self._build_roi_entries()
        self._build_control_panel()
        self._build_toolbar()
        self._build_docks()
        self._build_menu()
        self._connect_scene_events()

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
        # reagieren noch auf Klicks/Ziehen. Über "Ansicht > Bild zurücksetzen"
        # bzw. beim Laden wird die Ansicht ohnehin passend eingestellt.
        self.view_box.setMouseEnabled(x=False, y=False)
        self.view_box.setMenuEnabled(False)

        self.image_item = pg.ImageItem()
        self.plot_item.addItem(self.image_item)

        self.histogram = pg.HistogramLUTItem()
        self.histogram.setImageItem(self.image_item)
        self.histogram.gradient.setColorMap(pg.colormap.get(COLORMAPS[0][1]))
        self.glw.addItem(self.histogram, row=0, col=1)

        self.setCentralWidget(self.glw)

    def _build_plots(self) -> None:
        self.timeseries_plot = pg.PlotWidget(axisItems={"bottom": pg.DateAxisItem()})
        self.timeseries_plot.setLabel("left", "Temperatur", units="°C")
        self.timeseries_plot.showGrid(x=True, y=True, alpha=0.3)
        self.timeseries_plot.addLegend(offset=(10, 10))
        self.frame_marker = pg.InfiniteLine(
            angle=90, movable=False, pen=pg.mkPen("#888888", width=1, style=QtCore.Qt.DashLine)
        )
        self.timeseries_plot.addItem(self.frame_marker)

        btn_export_timeseries = QtWidgets.QPushButton("Graph speichern…")
        btn_export_timeseries.clicked.connect(
            lambda: self._export_plot_image(
                self.timeseries_plot, "Zeitverlauf.png", self._timeseries_metadata
            )
        )
        self.timeseries_widget = QtWidgets.QWidget()
        timeseries_layout = QtWidgets.QVBoxLayout(self.timeseries_widget)
        timeseries_layout.setContentsMargins(4, 4, 4, 4)
        timeseries_layout.addWidget(self.timeseries_plot)
        timeseries_layout.addWidget(btn_export_timeseries)

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
            "Maus über das Bild bewegen, um den Temperaturverlauf am Cursor-Pixel live zu sehen."
        )
        self.live_label.setWordWrap(True)

        btn_export_live = QtWidgets.QPushButton("Graph speichern…")
        btn_export_live.clicked.connect(
            lambda: self._export_plot_image(self.live_plot, "Live-Verlauf.png", self._live_metadata)
        )

        self.live_widget = QtWidgets.QWidget()
        live_layout = QtWidgets.QVBoxLayout(self.live_widget)
        live_layout.setContentsMargins(4, 4, 4, 4)
        live_layout.addWidget(self.live_label)
        live_layout.addWidget(self.live_plot)
        live_layout.addWidget(btn_export_live)

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

        self.chk_auto_levels = QtWidgets.QCheckBox("Automatisch (Min/Max je Frame)")
        self.chk_auto_levels.setChecked(True)
        self.chk_auto_levels.toggled.connect(self._on_auto_levels_toggled)
        legend_layout.addWidget(self.chk_auto_levels, 1, 0, 1, 3)

        legend_layout.addWidget(QtWidgets.QLabel("Min:"), 2, 0)
        self.spin_level_min = QtWidgets.QDoubleSpinBox()
        self.spin_level_min.setRange(-100.0, 2000.0)
        self.spin_level_min.setDecimals(1)
        self.spin_level_min.setSuffix(" °C")
        self.spin_level_min.setEnabled(False)
        self.spin_level_min.valueChanged.connect(self._on_level_spin_changed)
        legend_layout.addWidget(self.spin_level_min, 2, 1)

        legend_layout.addWidget(QtWidgets.QLabel("Max:"), 3, 0)
        self.spin_level_max = QtWidgets.QDoubleSpinBox()
        self.spin_level_max.setRange(-100.0, 2000.0)
        self.spin_level_max.setDecimals(1)
        self.spin_level_max.setValue(50.0)
        self.spin_level_max.setSuffix(" °C")
        self.spin_level_max.setEnabled(False)
        self.spin_level_max.valueChanged.connect(self._on_level_spin_changed)
        legend_layout.addWidget(self.spin_level_max, 3, 1)

        self.histogram.sigLevelsChanged.connect(self._on_histogram_levels_changed)

        layout.addWidget(legend_box)

        # -- Standardgröße neuer ROIs ------------------------------------
        size_box = QtWidgets.QGroupBox("Neue Messbereiche")
        size_layout = QtWidgets.QHBoxLayout(size_box)
        size_layout.addWidget(QtWidgets.QLabel("Standardgröße:"))
        self.spin_default_size = QtWidgets.QDoubleSpinBox()
        self.spin_default_size.setRange(2, 5000)
        self.spin_default_size.setValue(DEFAULT_ROI_SIZE)
        self.spin_default_size.setSuffix(" px")
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

        name_edit = QtWidgets.QLineEdit(entry.name)
        name_edit.setFrame(False)
        name_edit.setToolTip("Name dieses ROI (wird auch in der Legende verwendet)")
        name_font = name_edit.font()
        name_font.setBold(True)
        name_font.setPointSize(name_font.pointSize() + 1)
        name_edit.setFont(name_font)
        name_edit.editingFinished.connect(partial(self._on_roi_name_changed, entry))
        grid.addWidget(name_edit, 0, 0, 1, 4)
        entry.name_edit = name_edit

        btn_color = QtWidgets.QPushButton()
        btn_color.setFixedSize(20, 20)
        btn_color.setCursor(QtCore.Qt.PointingHandCursor)
        btn_color.setToolTip("Farbe dieses ROI ändern")
        btn_color.setStyleSheet(
            f"background-color:{entry.color}; border:1px solid #333; border-radius:4px;"
        )
        btn_color.clicked.connect(partial(self._on_roi_color_clicked, entry))
        grid.addWidget(btn_color, 1, 0)
        entry.btn_color = btn_color

        chk_visible = QtWidgets.QCheckBox("sichtbar")
        chk_visible.setChecked(True)
        chk_visible.toggled.connect(partial(self._on_roi_visibility_toggled, entry))
        grid.addWidget(chk_visible, 1, 1)
        entry.chk_visible = chk_visible

        btn_place = QtWidgets.QPushButton("Im Bild platzieren")
        btn_place.setCheckable(True)
        btn_place.toggled.connect(partial(self._on_roi_place_toggled, entry))
        grid.addWidget(btn_place, 1, 2, 1, 2)
        entry.btn_place = btn_place

        grid.addWidget(QtWidgets.QLabel("X:"), 2, 0)
        spin_x = QtWidgets.QDoubleSpinBox()
        spin_x.setRange(0, 100000)
        spin_x.setDecimals(1)
        grid.addWidget(spin_x, 2, 1)
        entry.spin_x = spin_x

        grid.addWidget(QtWidgets.QLabel("Y:"), 2, 2)
        spin_y = QtWidgets.QDoubleSpinBox()
        spin_y.setRange(0, 100000)
        spin_y.setDecimals(1)
        grid.addWidget(spin_y, 2, 3)
        entry.spin_y = spin_y

        grid.addWidget(QtWidgets.QLabel("Größe:"), 3, 0)
        spin_size = QtWidgets.QDoubleSpinBox()
        spin_size.setRange(1, 100000)
        spin_size.setValue(entry.default_size)
        grid.addWidget(spin_size, 3, 1)
        entry.spin_size = spin_size

        btn_apply = QtWidgets.QPushButton("Übernehmen")
        btn_apply.setToolTip("Setzt dieses ROI exakt auf die eingegebenen Koordinaten/Größe.")
        btn_apply.clicked.connect(partial(self._on_roi_apply_clicked, entry))
        grid.addWidget(btn_apply, 3, 2)

        btn_reset = QtWidgets.QPushButton("Zurücksetzen")
        btn_reset.setToolTip("Stellt Position & Größe wieder her, wie sie zuletzt gesetzt wurden.")
        btn_reset.clicked.connect(partial(self._on_roi_reset_clicked, entry))
        grid.addWidget(btn_reset, 3, 3)

        return box

    def _build_toolbar(self) -> None:
        toolbar = self.addToolBar("Steuerung")
        toolbar.setMovable(False)

        act_open_folder = toolbar.addAction("Ordner öffnen…")
        act_open_folder.triggered.connect(self._open_folder)
        act_open_files = toolbar.addAction("Dateien öffnen…")
        act_open_files.triggered.connect(self._open_files)

        toolbar.addSeparator()

        self.play_action = toolbar.addAction("▶ Play")
        self.play_action.setCheckable(True)
        self.play_action.toggled.connect(self._on_play_toggled)

        toolbar.addWidget(QtWidgets.QLabel(" Frame: "))
        self.frame_slider = QtWidgets.QSlider(QtCore.Qt.Horizontal)
        self.frame_slider.setMinimumWidth(280)
        self.frame_slider.setRange(0, 0)
        self.frame_slider.valueChanged.connect(self._on_slider_changed)
        toolbar.addWidget(self.frame_slider)

        self.frame_spin = QtWidgets.QSpinBox()
        self.frame_spin.setRange(0, 0)
        self.frame_spin.valueChanged.connect(self._on_frame_spin_changed)
        toolbar.addWidget(self.frame_spin)

        toolbar.addWidget(QtWidgets.QLabel("  FPS: "))
        self.fps_spin = QtWidgets.QDoubleSpinBox()
        self.fps_spin.setRange(0.5, 60.0)
        self.fps_spin.setValue(10.0)
        self.fps_spin.valueChanged.connect(self._on_fps_changed)
        toolbar.addWidget(self.fps_spin)

        self.timestamp_label = QtWidgets.QLabel("  –")
        self.timestamp_label.setStyleSheet("font-weight: bold; padding-left: 8px;")
        toolbar.addWidget(self.timestamp_label)

        self.play_timer = QtCore.QTimer(self)
        self.play_timer.timeout.connect(self._advance_frame)

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
        file_menu = self.menuBar().addMenu("&Datei")
        act_open_folder = file_menu.addAction("Ordner öffnen…")
        act_open_folder.triggered.connect(self._open_folder)
        act_open_files = file_menu.addAction("Dateien öffnen…")
        act_open_files.triggered.connect(self._open_files)
        file_menu.addSeparator()
        act_quit = file_menu.addAction("Beenden")
        act_quit.triggered.connect(self.close)

        view_menu = self.menuBar().addMenu("&Ansicht")
        view_menu.addAction(self.control_dock.toggleViewAction())
        view_menu.addAction(self.timeseries_dock.toggleViewAction())
        view_menu.addAction(self.live_dock.toggleViewAction())

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

        self.frame_slider.blockSignals(True)
        self.frame_slider.setRange(0, n - 1)
        self.frame_slider.setValue(0)
        self.frame_slider.blockSignals(False)

        self.frame_spin.blockSignals(True)
        self.frame_spin.setRange(0, n - 1)
        self.frame_spin.setValue(0)
        self.frame_spin.blockSignals(False)

        symbol = "o" if n <= MAX_FRAMES_WITH_SYMBOLS else None
        for entry in self.roi_entries:
            entry.spin_x.setRange(0, cols)
            entry.spin_y.setRange(0, rows)
            entry.curve.setSymbol(symbol)
        self.live_curve.setSymbol(symbol)

        self.view_box.setRange(xRange=(0, cols), yRange=(0, rows), padding=0.02)
        self.current_index = 0
        self._show_frame(0)
        self._recompute_curves()

        message = f"{n} Frame(s) geladen aus {recording.paths[0].parent}"
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
        self.statusBar().showMessage(message)

    # --------------------------------------------------------- Frame-Nav
    def _on_slider_changed(self, value: int) -> None:
        self.frame_spin.blockSignals(True)
        self.frame_spin.setValue(value)
        self.frame_spin.blockSignals(False)
        self._show_frame(value)

    def _on_frame_spin_changed(self, value: int) -> None:
        self.frame_slider.blockSignals(True)
        self.frame_slider.setValue(value)
        self.frame_slider.blockSignals(False)
        self._show_frame(value)

    def _show_frame(self, idx: int) -> None:
        if self.recording is None or self.recording.n_frames == 0:
            return
        idx = max(0, min(idx, self.recording.n_frames - 1))
        self.current_index = idx
        frame = self.recording.frames[idx]

        auto = self.chk_auto_levels.isChecked()
        self.image_item.setImage(frame, autoLevels=auto)
        if auto:
            lo, hi = self.image_item.getLevels()
            for spin, value in ((self.spin_level_min, lo), (self.spin_level_max, hi)):
                spin.blockSignals(True)
                spin.setValue(value)
                spin.blockSignals(False)
        else:
            self.image_item.setLevels((self.spin_level_min.value(), self.spin_level_max.value()))

        ts = self.recording.timestamps[idx]
        self.timestamp_label.setText("  " + ts.strftime("%Y-%m-%d %H:%M:%S"))

        unix = self.recording.unix_seconds()
        self.frame_marker.setValue(unix[idx])
        self.live_frame_marker.setValue(unix[idx])
        self._update_status_bar()

    def _on_play_toggled(self, checked: bool) -> None:
        if checked:
            if self.recording is None or self.recording.n_frames < 2:
                self.play_action.setChecked(False)
                return
            self.play_action.setText("⏸ Pause")
            interval = int(1000 / max(0.1, self.fps_spin.value()))
            self.play_timer.start(interval)
        else:
            self.play_action.setText("▶ Play")
            self.play_timer.stop()

    def _advance_frame(self) -> None:
        if self.recording is None:
            self.play_action.setChecked(False)
            return
        nxt = self.current_index + 1
        if nxt >= self.recording.n_frames:
            self.play_action.setChecked(False)
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
                entry.spin_size.blockSignals(True)
                entry.spin_size.setValue(value)
                entry.spin_size.blockSignals(False)

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
            self._armed_entry = entry
            self.statusBar().showMessage(f"{entry.name}: Klick ins Bild zum Platzieren.")
        elif self._armed_entry is entry:
            self._armed_entry = None

    def _on_roi_apply_clicked(self, entry: RoiEntry) -> None:
        if self.recording is None:
            QtWidgets.QMessageBox.information(self, "Keine Daten", "Bitte zuerst eine Messreihe laden.")
            return
        entry.place(entry.spin_x.value(), entry.spin_y.value(), entry.spin_size.value())
        self._sync_roi_spinboxes(entry)
        self._recompute_curves(entries=[entry])

    def _on_roi_reset_clicked(self, entry: RoiEntry) -> None:
        if entry.snapshot is None:
            return
        entry.reset()
        self._sync_roi_spinboxes(entry)
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
        size = entry.size()
        for spin, value in (
            (entry.spin_x, cx),
            (entry.spin_y, cy),
            (entry.spin_size, size),
        ):
            spin.blockSignals(True)
            spin.setValue(value)
            spin.blockSignals(False)

    def _recompute_curves(self, entries: list[RoiEntry] | None = None) -> None:
        if self.recording is None:
            return
        entries = entries if entries is not None else self.roi_entries
        unix = self.recording.unix_seconds()
        shape = self.recording.shape
        for entry in entries:
            if not entry.placed:
                continue
            row0, row1, col0, col1 = entry.bounds_px(shape)
            values = self.recording.frames[:, row0:row1, col0:col1].mean(axis=(1, 2))
            entry.curve.setData(unix, values)
            entry.curve.setVisible(entry.chk_visible.isChecked())

    # ---------------------------------------------------------- Legende
    def _on_colormap_changed(self, index: int) -> None:
        name = COLORMAPS[index][1]
        self.histogram.gradient.setColorMap(pg.colormap.get(name))

    def _on_auto_levels_toggled(self, checked: bool) -> None:
        self.spin_level_min.setEnabled(not checked)
        self.spin_level_max.setEnabled(not checked)
        self._show_frame(self.current_index)

    def _on_level_spin_changed(self) -> None:
        if self.chk_auto_levels.isChecked():
            return
        lo, hi = self.spin_level_min.value(), self.spin_level_max.value()
        if hi <= lo:
            return
        self.histogram.setLevels(lo, hi)
        self.image_item.setLevels((lo, hi))

    def _on_histogram_levels_changed(self) -> None:
        lo, hi = self.histogram.getLevels()
        for spin, value in ((self.spin_level_min, lo), (self.spin_level_max, hi)):
            spin.blockSignals(True)
            spin.setValue(value)
            spin.blockSignals(False)

    # ------------------------------------------------------- Maus/Bild
    def _on_scene_mouse_clicked(self, event) -> None:
        if event.button() != QtCore.Qt.LeftButton:
            return
        if self._armed_entry is None or self.recording is None:
            return
        scene_pos = event.scenePos()
        if not self.view_box.sceneBoundingRect().contains(scene_pos):
            return
        view_pos = self.view_box.mapSceneToView(scene_pos)
        entry = self._armed_entry
        entry.place(view_pos.x(), view_pos.y(), entry.spin_size.value())
        self._sync_roi_spinboxes(entry)
        entry.btn_place.setChecked(False)
        self._armed_entry = None
        self._recompute_curves(entries=[entry])

    def _on_scene_mouse_moved(self, scene_pos: QtCore.QPointF) -> None:
        if self.recording is None:
            return
        if not self.view_box.sceneBoundingRect().contains(scene_pos):
            return
        view_pos = self.view_box.mapSceneToView(scene_pos)
        rows, cols = self.recording.shape
        col = int(np.floor(view_pos.x()))
        row = int(np.floor(view_pos.y()))
        if not (0 <= row < rows and 0 <= col < cols):
            return
        if (row, col) == (self._hover_row, self._hover_col):
            return
        self._hover_row, self._hover_col = row, col
        values = self.recording.frames[:, row, col]
        self.live_curve.setData(self.recording.unix_seconds(), values)
        self.live_label.setText(f"Cursor-Pixel: Zeile {row}, Spalte {col}")
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
    def _export_plot_image(self, plot_widget: pg.PlotWidget, suggested_name: str, metadata_fn) -> None:
        if self.recording is None:
            QtWidgets.QMessageBox.information(self, "Keine Daten", "Bitte zuerst eine Messreihe laden.")
            return

        filters = {
            "PNG-Bild (*.png)": ".png",
            "JPEG-Bild (*.jpg *.jpeg)": ".jpg",
            "Bitmap (*.bmp)": ".bmp",
            "TIFF-Bild (*.tiff *.tif)": ".tiff",
            "WebP-Bild (*.webp)": ".webp",
        }
        path, selected_filter = QtWidgets.QFileDialog.getSaveFileName(
            self, "Graph speichern", suggested_name, ";;".join(filters.keys())
        )
        if not path:
            return
        if not Path(path).suffix:
            path += filters.get(selected_filter, ".png")

        dpi, ok = QtWidgets.QInputDialog.getInt(
            self, "Auflösung", "DPI für den Export:", 150, 50, 1200, 10
        )
        if not ok:
            return

        scale = dpi / 96.0
        size = plot_widget.size()
        width_px = max(1, round(size.width() * scale))
        height_px = max(1, round(size.height() * scale))

        image = QtGui.QImage(width_px, height_px, QtGui.QImage.Format_ARGB32)
        image.fill(QtGui.QColor("white"))
        dots_per_meter = round(dpi / 0.0254)
        image.setDotsPerMeterX(dots_per_meter)
        image.setDotsPerMeterY(dots_per_meter)

        painter = QtGui.QPainter(image)
        painter.setRenderHint(QtGui.QPainter.Antialiasing)
        painter.scale(scale, scale)
        plot_widget.render(painter)
        painter.end()

        if not image.save(path):
            QtWidgets.QMessageBox.critical(self, "Fehler", f"Konnte Bild nicht speichern:\n{path}")
            return

        metadata = {
            "exportiert_am": datetime.now().isoformat(timespec="seconds"),
            "bilddatei": Path(path).name,
            "dpi": dpi,
            "bildgroesse_px": {"breite": width_px, "hoehe": height_px},
            **metadata_fn(),
        }
        meta_path = Path(path).with_suffix(".json")
        meta_path.write_text(json.dumps(metadata, indent=2, ensure_ascii=False), encoding="utf-8")

        self.statusBar().showMessage(f"Graph gespeichert: {path}  |  Metadaten: {meta_path.name}")

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
            }
            if entry.placed:
                cx, cy = entry.center()
                row0, row1, col0, col1 = entry.bounds_px(self.recording.shape)
                roi_info["mittelpunkt_px"] = {"x": cx, "y": cy}
                roi_info["groesse_px"] = entry.size()
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
