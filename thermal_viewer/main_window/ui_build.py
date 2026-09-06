"""Fensteraufbau: Bildbereich, Zeitleiste, Kurven-Graphen, Werkzeugleiste, Docks, Menü und Tastenkürzel."""
from __future__ import annotations

import contextlib
from functools import partial

import pyqtgraph as pg
from qtpy import QtCore, QtGui, QtWidgets

from ..plot_items import (
    TimeAxisItem,
    TimelineSlider,
    _StaysOpenMenu,
)
from ..widgets import LocaleTolerantDoubleSpinBox, UpwardSafeComboBox
from .constants import (
    COLORMAPS,
)


class _UIBuildMixin:
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
        # Palette in der Praxis nicht immer zuverlaessig, siehe _apply_window_theme) --
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
        # UpwardSafeComboBox statt QComboBox (Bugreport: "im Vollbild-Modus
        # geht das Dropdown unten aus dem Bildschirm raus") -- diese Zeile
        # sitzt direkt unterhalb der Kurven-Graphen, also nahe am unteren
        # Fensterrand.
        combo = UpwardSafeComboBox()
        combo.addItem("Uhrzeit", "clock")
        combo.addItem("Laufzeit", "runtime")
        combo.setToolTip("Zeigt die x-Achse als echte Uhrzeit oder als Laufzeit seit Aufnahmebeginn.")
        row.addWidget(combo)

        # Laufzeit-Format ("dritte Zeitachse", Nutzerwunsch): statt fix
        # hh:mm:ss auch eine fortlaufende Zahl in frei waehlbarer Einheit --
        # wirkt global (siehe _apply_runtime_unit), daher nur EIN Format je
        # Instanz noetig, hier aber zwei synchronisierte Umschalter (je
        # einer pro Graph), analog zum Uhrzeit/Laufzeit-Umschalter oben.
        format_combo = UpwardSafeComboBox()
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
        """Blendet im Rechtsklick-Menü des Kurven-Graphen alle Optionen aus,
        die fuer eine einfache Temperatur-ueber-Zeit-Kurve in dieser App ohne
        praktischen Nutzen sind und nur verwirren (Nutzerwunsch: "Schmeiße
        unnötige Optionen raus... Mouse Mode, Link Axis... da sind vermutlich
        noch weitere nicht-nutzbare Optionen drin"):

        - PlotItem-Menü: "Transforms", "Downsample", "Average", "Alpha",
          "Points" (fuer eine reine Linienkurve ohne Punktwolke irrelevant).
        - ViewBox-Menü (X/Y-Achsen-Untermenüs): "Link Axis" -- es gibt in
          dieser App nie einen anderen Plot, mit dem sich eine Achse
          verknuepfen liesse (die Combobox zeigt entsprechend IMMER nur den
          leeren Platzhaltereintrag).
        - ViewBox-Menü: der komplette "Mouse Mode"-Eintrag (3-Button-/
          1-Button-Modus) -- diese App nutzt durchgehend das feste
          Standard-Mausverhalten (Ziehen = Verschieben, Rad = Zoom), ein
          Umschalten auf einen anderen Modus wuerde nur zu unerwartetem
          Verhalten fuehren, ohne dass die App das irgendwo unterstuetzt/
          erklaert.

        "Grid", "View All" und die uebrigen X/Y-Achsen-Optionen (Auto/
        Manuell, Invertieren, Mausbedienung je Achse) bleiben -- die haben
        einen echten, nachvollziehbaren Effekt (siehe Bedienungsanleitung,
        Abschnitt 8)."""
        plot_item = plot_widget.getPlotItem()
        for name in ("Transforms", "Downsample", "Average", "Alpha", "Points"):
            plot_item.setContextMenuActionVisible(name, False)

        vb_menu = plot_item.getViewBox().menu
        for act in vb_menu.actions():
            if act.text() == "Mouse Mode":
                act.setVisible(False)
        for axis_ctrl in vb_menu.ctrl:
            axis_ctrl.label.setVisible(False)
            axis_ctrl.linkCombo.setVisible(False)

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
        x_axis_item = plot_item.getAxis("bottom")
        y_axis_item = plot_item.getAxis("left")
        t0 = (
            self.recording.unix_seconds()[0]
            if self.recording is not None and self.recording.n_frames
            else 0.0
        )

        # Lokaler statt Modul-Import: liest AxisSettingsDialog bei jedem
        # Aufruf frisch aus dem main_window-Paket -- damit Tests, die per
        # monkeypatch.setattr(main_window, "AxisSettingsDialog", ...) einen
        # Test-Dialog einsetzen, hier auch tatsaechlich greifen (ein Import
        # auf Modulebene wuerde stattdessen dauerhaft an die ORIGINAL-Klasse
        # binden, siehe Docstring von thermal_viewer/main_window/__init__.py).
        from . import AxisSettingsDialog

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

        # "Messbereiche" statt "ROI" -- einheitlich mit der Benennung ueberall
        # sonst in der deutschsprachigen UI ("Messbereich setzen"/"entfernen",
        # Fehlermeldungen); "ROI" bleibt nur intern (Code, Kommentare, Variablen).
        self.control_dock = QtWidgets.QDockWidget("Messbereiche && Legende", self)
        self.control_dock.setWidget(self.control_panel)
        self.control_dock.setAllowedAreas(side_areas)
        self.control_dock.setFeatures(dock_features)
        self.addDockWidget(QtCore.Qt.RightDockWidgetArea, self.control_dock)

        self.timeseries_dock = QtWidgets.QDockWidget("Zeitverlauf", self)
        self.timeseries_dock.setWidget(self.timeseries_widget)
        self.timeseries_dock.setAllowedAreas(side_areas)
        self.timeseries_dock.setFeatures(dock_features)
        # Der Name "Zeitverlauf" bleibt als windowTitle() erhalten (fuer den
        # Ein-/Ausblenden-Menuepunkt im "Ansicht"-Menue, siehe _build_menu),
        # die eigentliche Titelzeile ueber dem Graphen selbst blendet ein
        # leeres Platzhalter-Widget aus (Nutzerfeedback: "sollte offensicht-
        # lich sein, was mit dem Graphen gemeint ist" -- ohnehin redundant,
        # seitdem das frueher tabifizierte "Live (Cursor)"-Dock entfallen ist
        # und hier nur noch dieser eine Titel stehenbleibt statt zwischen
        # zwei Tabs zu unterscheiden). Ein-/Ausblenden bleibt weiterhin ueber
        # das "Ansicht"-Menue moeglich, nur Ziehen/Abdocken/Schliessen ueber
        # die (jetzt unsichtbare) Titelleiste selbst entfaellt.
        self.timeseries_dock.setTitleBarWidget(QtWidgets.QWidget())
        self.addDockWidget(QtCore.Qt.RightDockWidgetArea, self.timeseries_dock)

        # Das frueher separat tabifizierte "Live (Cursor)"-Dock entfaellt
        # (Nutzerwunsch: redundant, da der Live-Cursor-Verlauf laengst als
        # ein-/ausblendbare Kurve DIREKT im "Zeitverlauf"-Graphen erscheinen
        # kann, siehe chk_show_live_in_timeseries). self.live_widget/
        # live_plot/live_curve/axis_live_* bleiben unveraendert bestehen
        # (werden weiterhin von genereller Achsen-/Farbcode-/Export-Logik
        # referenziert, siehe _time_axis_widget_parts) -- sie werden nur nie
        # in ein sichtbares Dock gehaengt. QGraphicsScene.render() (fuer
        # jeden Export-Pfad) braucht dafuer keine Sichtbarkeit.
        self.live_dock = QtWidgets.QDockWidget("Live (Cursor)", self)
        self.live_dock.setWidget(self.live_widget)
        # Ohne addDockWidget() bleibt live_dock ein normales Kind-Widget des
        # (bereits sichtbaren) Hauptfensters -- Qt wuerde es sonst als
        # ueberlappendes, "schwebendes" Rechteck IM Hauptfenster anzeigen
        # statt es unsichtbar zu halten. hide() haelt es zuverlaessig
        # ausgeblendet.
        self.live_dock.hide()

        # "Zeitverlauf" explizit OBERHALB der Graphen statt an einer je nach
        # Qt-Stil/Plattform abweichenden Standardposition.
        self.setTabPosition(QtCore.Qt.RightDockWidgetArea, QtWidgets.QTabWidget.North)

        # control_dock und timeseries_dock liegen in DERSELBEN rechten Spalte
        # (live_dock ist seit Entfernung des redundanten "Live (Cursor)"-Tabs
        # nie sichtbar, siehe oben, gehoert also nicht mehr dazu) -- beide
        # haben also zwangslaeufig dieselbe Breite. Ein resizeDocks(...,
        # Horizontal) mit zwei WIDERSPRUECHLICHEN Breiten fuer Docks derselben
        # Spalte (frueherer Bug) fuehrte zu einer unvorhersehbaren/"komischen"
        # Anfangsbreite; hier genuegt EIN Wert fuer die ganze Spalte. Bild-
        # Spalte (links) und Docks (rechts) sollen sich sonst zu gleichen
        # Teilen (50:50) die Fensterbreite teilen.
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

        view_menu.addSeparator()
        # Zwei Voreinstellungs-Knoepfe (Folgeanfrage: "ich möchte zwei
        # 'Default'-Settings haben (Light-/Dark), die die GESAMTE UI
        # umstellen, UND Buttons, mit denen ich dieselben UI-Elemente
        # gezielt und unabhängig voneinander umstellen kann") -- setzen
        # Fenster-, Thermobild- UND Graph-Farbschema (siehe die drei
        # Untermenues darunter) in einem Rutsch auf denselben Wert. Bewusst
        # NICHT checkable/exklusiv: nach spaeteren unabhaengigen Aenderungen
        # an nur EINEM der drei Untermenues gibt es keinen einzelnen
        # gemeinsamen Zustand mehr, den ein Haekchen sinnvoll anzeigen
        # koennte -- diese beiden Knoepfe sind reine "Auf einen Schlag
        # zuruecksetzen"-Aktionen, kein dauerhafter Schalter.
        act_theme_all_light = view_menu.addAction("Alles: Hell")
        act_theme_all_light.setToolTip(
            "Setzt Fenster, Thermobild UND Graph gemeinsam auf Hell -- alle drei bleiben danach "
            "trotzdem weiterhin über die eigenen Untermenüs darunter unabhängig voneinander "
            "veränderbar."
        )
        act_theme_all_light.triggered.connect(partial(self._apply_default_theme, "light"))
        act_theme_all_dark = view_menu.addAction("Alles: Dunkel")
        act_theme_all_dark.setToolTip(
            "Setzt Fenster, Thermobild UND Graph gemeinsam auf Dunkel -- alle drei bleiben danach "
            "trotzdem weiterhin über die eigenen Untermenüs darunter unabhängig voneinander "
            "veränderbar."
        )
        act_theme_all_dark.triggered.connect(partial(self._apply_default_theme, "dark"))

        # Unabhaengig von den beiden "Alles: ..."-Knoepfen oben waehlbares
        # Hell/Dunkel fuer Fenster, Thermobild UND Graph JEWEILS EINZELN
        # (Punkt 5, "Ansichts-Manager", Nutzerwunsch: "Thermobild, der Graph
        # und die UI sollen frei und unabhaengig voneinander jeweils im
        # Dark-/Lightmode erscheinen können") -- je ein Untermenue mit zwei
        # sich gegenseitig ausschliessenden Optionen, analog zu "Live-
        # Cursor-Bereichsgröße" im Werkzeuge-Menue (siehe kernel_menu/
        # kernel_group weiter unten). Reihenfolge Fenster -> Thermobild ->
        # Graph: von aussen (Fensterrahmen) nach innen (Bildinhalte).
        self._window_theme_actions: dict[str, QtGui.QAction] = {}
        window_theme_menu = view_menu.addMenu("Fenster-Farbschema")
        window_theme_menu.setToolTip(
            "Hintergrund-/Schriftfarbe von Fenster, Menüs und Panels -- unabhaengig von "
            "Thermobild und Graph darunter, siehe auch die beiden \"Alles: ...\"-Knöpfe oben, "
            "die alle drei gemeinsam auf einen Schlag umschalten."
        )
        window_theme_group = QtGui.QActionGroup(self)
        window_theme_group.setExclusive(True)
        for key, label in (("light", "Hell"), ("dark", "Dunkel")):
            act = window_theme_menu.addAction(label)
            act.setCheckable(True)
            # Reihenfolge wichtig: addAction() MUSS vor triggered.connect()
            # passieren -- siehe ausfuehrliche Begruendung bei
            # image_theme_group weiter unten.
            window_theme_group.addAction(act)
            act.triggered.connect(partial(self._apply_window_theme, key))
            self._window_theme_actions[key] = act

        self._image_theme_actions: dict[str, QtGui.QAction] = {}
        image_theme_menu = view_menu.addMenu("Thermobild-Farbschema")
        image_theme_menu.setToolTip(
            "Hintergrund-/Schriftfarbe des Thermobilds -- unabhaengig vom Fenster-Farbschema "
            "oben und vom Graphen, gilt auch für alle Exporte (Bild/Video/Bildstapel)."
        )
        image_theme_group = QtGui.QActionGroup(self)
        image_theme_group.setExclusive(True)
        for key, label in (("light", "Hell"), ("dark", "Dunkel")):
            act = image_theme_menu.addAction(label)
            act.setCheckable(True)
            # Reihenfolge wichtig: addAction() MUSS vor triggered.connect()
            # passieren -- sonst laeuft unser eigener Slot (der die geklickte
            # Action per setChecked(True) erneut bestaetigt) VOR dem internen
            # Abwaehl-Mechanismus der Gruppe, wodurch die zuvor aktive Action
            # faelschlich angehakt bleibt (Bugreport: "der Punkt bleibt bei
            # 'hell' drin", per Testskript reproduziert und verifiziert).
            image_theme_group.addAction(act)
            act.triggered.connect(partial(self._apply_image_theme, key))
            self._image_theme_actions[key] = act

        self._graph_theme_actions: dict[str, QtGui.QAction] = {}
        graph_theme_menu = view_menu.addMenu("Graph-Farbschema")
        graph_theme_menu.setToolTip(
            "Hintergrund-/Schriftfarbe der Kurven-Graphen (Zeitverlauf/Live) -- unabhaengig vom "
            "Fenster-Farbschema oben und vom Thermobild, gilt auch für alle Exporte "
            "(Bild/Video/Bildstapel)."
        )
        graph_theme_group = QtGui.QActionGroup(self)
        graph_theme_group.setExclusive(True)
        for key, label in (("light", "Hell"), ("dark", "Dunkel")):
            act = graph_theme_menu.addAction(label)
            act.setCheckable(True)
            # Reihenfolge wichtig, siehe image_theme_group weiter oben.
            graph_theme_group.addAction(act)
            act.triggered.connect(partial(self._apply_graph_theme, key))
            self._graph_theme_actions[key] = act

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
        # "Länge messen…" hat keinen eigenen Menüpunkt mehr -- ersetzt durch
        # den Knopf "Neue Messung" im rechten Panel (Punkt 8, "Maßstab &
        # Messungen"), analog zum "+ Messbereich"-Knopf ohne Menü-Aequivalent.

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

