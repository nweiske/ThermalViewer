"""Fensteraufbau: Bildbereich, Zeitleiste, Kurven-Graphen, Docks, Menü und Tastenkürzel."""
from __future__ import annotations

import contextlib
from functools import partial

import pyqtgraph as pg
from qtpy import QtCore, QtGui, QtWidgets

from ..plot_items import (
    TimeAxisItem,
    TimelineSlider,
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

        # Nutzerwunsch: die aktuelle Zeile/Spalte des Querschnitt-Graphen
        # (siehe crosssection_ops.py) als duenne, gestrichelte Linie direkt im
        # Thermobild sehen -- gleiche Dicke wie die Messbereich-Boxen
        # (width=2), gestrichelt, damit sie sich davon unterscheidet. Nur
        # sichtbar, waehrend der "Querschnitt"-Tab tatsaechlich im
        # Vordergrund ist (siehe _update_crosssection_image_line). EIN
        # InfiniteLine-Objekt, dessen Winkel/Position je nach gewaehlter
        # Richtung umgeschaltet wird, statt zwei separater Linien -- der
        # Nutzer sprach nur von "der" Linie fuer den jeweils aktiven Schnitt.
        # movable=True (Nutzerwunsch: "per Drag/and Drop zusätzlich
        # verschieben können") macht sie per pyqtgraph nativ ziehbar -- ein
        # eigener hoverPen zeigt beim Drueberfahren an, dass sie greifbar
        # ist. sigDragged (siehe crosssection_ops.py) feuert ausschliesslich
        # bei echtem User-Drag, nie bei unseren eigenen setPos()-Aufrufen.
        self.crosssection_image_line = pg.InfiniteLine(
            angle=0, movable=True,
            pen=pg.mkPen("#38bdf8", width=2, style=QtCore.Qt.DashLine),
            hoverPen=pg.mkPen("#facc15", width=2, style=QtCore.Qt.DashLine),
        )
        self.crosssection_image_line.setZValue(10)
        self.crosssection_image_line.setVisible(False)
        self.plot_item.addItem(self.crosssection_image_line)
        self.crosssection_image_line.sigDragged.connect(self._on_crosssection_image_line_dragged)

        self.histogram = pg.HistogramLUTItem()
        self.histogram.setImageItem(self.image_item)
        self.histogram.gradient.setColorMap(pg.colormap.get(COLORMAPS[0][1]))
        self.glw.addItem(self.histogram, row=0, col=1)

        self._build_timeline_bar()

        central = QtWidgets.QWidget()
        central_layout = QtWidgets.QVBoxLayout(central)
        central_layout.setContentsMargins(0, 0, 0, 0)
        central_layout.setSpacing(0)
        # Ebenen-Tabs oberhalb des Thermobilds (Nutzerwunsch: "Thermobild
        # wird recht voll") -- siehe layer_tabs_ops.py.
        central_layout.addWidget(self._build_layer_tab_bar(), 0)
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
        # Beendet die von _on_eval_start_changed/_on_eval_end_changed ueber
        # _begin_grouped_undo_edit() begonnene Eingabe-Sitzung (siehe
        # undo_ops.py) -- markerDragged selbst loest ueber spin.setValue()
        # NIE ein editingFinished aus (das feuert nur bei echter Tastatur-
        # Fokus-Interaktion), ohne dieses eigene "fertig"-Signal wuerde die
        # Gruppierung nach einem Marker-Drag fuer immer offen bleiben.
        self.frame_slider.markerDragFinished.connect(self._end_grouped_undo_edit)
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
        self.spin_eval_start.editingFinished.connect(self._end_grouped_undo_edit)
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
        self.spin_eval_end.editingFinished.connect(self._end_grouped_undo_edit)
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
        # Bugfix: der obere Rand des Graphen schnitt den hoechsten Kurvenwert
        # ab, da pyqtgraphs eigener Auto-Range-Standard hier zu knapp
        # ausfaellt und dieser Wert bisher nirgends explizit gesetzt wurde.
        # 8% Puffer statt der urspruenglich versuchten 5% -- bei nahezu
        # flachen Kurven (kleine Werteschwankung) blieb der Rand mit 5%
        # kaum wahrnehmbar (Bugreport: "sehe ich nicht/ist nicht da"), analog
        # zum bestehenden padding=0.02-Muster beim Thermobild (frame_nav.py:
        # _set_recording), hier aber als DAUERHAFTE ViewBox-Einstellung statt
        # einmaligem setRange(), da sich der Y-Bereich laufend automatisch
        # neu bestimmt.
        #
        # ZWEITER Bugfix (der eigentliche Grund, warum trotz obigem Padding
        # weiterhin KEIN Rand zu sehen war): setDefaultPadding() beeinflusst
        # nur, welcher WERTEBEREICH angezeigt wird -- es reserviert KEINEN
        # zusaetzlichen PIXEL-Platz am oberen Widget-Rand. Ohne eine obere
        # Zeitachse (showAxis("top", False)) reicht die ViewBox-Zeichenflaeche
        # bis exakt zur obersten Bildzeile des Widgets; eine "schoene"
        # Gitterlinie/Achsenbeschriftung, die zufaellig nahe am oberen Ende
        # des (gepolsterten) Wertebereichs liegt, wird dadurch trotzdem
        # praktisch AM Widget-Rand gezeichnet (empirisch verifiziert: auch
        # 30% Padding aenderte am Pixel-Ergebnis nichts). setContentsMargins
        # auf dem PlotItem selbst reserviert dagegen einen ECHTEN, festen
        # Leerraum -- in der Hintergrundfarbe des Graphen (folgt also
        # automatisch Hell-/Dunkelmodus, siehe theming.py) -- in dem
        # garantiert weder Gitter noch Achsenbeschriftung erscheinen.
        self.timeseries_plot.getPlotItem().getViewBox().setDefaultPadding(0.08)
        self.timeseries_plot.getPlotItem().setContentsMargins(0, 18, 0, 0)

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

        # Eigener, zweiter Graph NUR für die Schwindungsmessung (siehe
        # shrinkage_ops.py) -- eine eigene Kenngröße mit eigener Einheit
        # (mm/mm²/px/px²), die auf der °C-Achse des Zeitverlauf-Graphen
        # fachlich nicht sinnvoll mitgeplottet werden kann. Lebt in einem
        # EIGENEN Dock/Tab (siehe _build_docks) statt im selben Widget wie
        # der Zeitverlauf-Graph -- Nutzerwunsch: "nicht einfach unter die
        # Temperaturkurven quetschen, sondern in einen eigenen Plot (wie
        # damals den Live-Cursor) packen", analog zum frueher tabifizierten
        # "Live (Cursor)"-Dock. Folgt dem GLOBAL geteilten Uhrzeit/Laufzeit-
        # Anzeigemodus passiv mit (siehe theming.py:_apply_time_display_mode/
        # _apply_runtime_unit), bekommt aber -- wie der Zeitverlauf-Graph --
        # eine EIGENE Achsen-Einstellen-/Zurücksetzen-Zeile (siehe unten).
        # Der ganze Dock/Tab ist nur sichtbar, solange die Schwindungsmessung
        # aktiviert ist UND ein Ergebnis vorliegt (siehe shrinkage_ops.py:
        # _on_shrinkage_enabled_toggled). Zoom/Pan bewusst NICHT mit
        # timeseries_plot gekoppelt (siehe Begruendung unten).
        self.axis_shrinkage_bottom = TimeAxisItem()
        self.axis_shrinkage_top = TimeAxisItem(orientation="top")
        self.shrinkage_plot = pg.PlotWidget(
            axisItems={"bottom": self.axis_shrinkage_bottom, "top": self.axis_shrinkage_top}
        )
        self.shrinkage_plot.getPlotItem().showAxis("top", False)
        self.shrinkage_plot.setLabel("left", "Schwindung")
        self.shrinkage_plot.showGrid(x=True, y=True, alpha=0.3)
        self.shrinkage_legend = self.shrinkage_plot.addLegend(offset=(10, 10))
        self.shrinkage_frame_marker = pg.InfiniteLine(
            angle=90, movable=False, pen=pg.mkPen("#888888", width=1, style=QtCore.Qt.DashLine)
        )
        self.shrinkage_plot.addItem(self.shrinkage_frame_marker)
        # KEIN setXLink(self.timeseries_plot): eine X-Achsen-Kopplung ueber
        # pg.ViewBox.setXLink() beeinflusst nachweislich (durch Tests
        # aufgedeckt) die exakten Fliesskomma-Werte, die _rebased_time_axis
        # (export_visuals.py) beim SVG-Export berechnet -- dieselbe grosse
        # Unix-Zeitstempel-Praezisions-Problematik, die dort bereits einmal
        # behoben wurde, tauchte durch die Kopplung erneut auf. Zoom/Pan
        # bleiben deshalb bewusst UNABHAENGIG zwischen den beiden Graphen.
        self.shrinkage_plot.getPlotItem().getViewBox().setDefaultPadding(0.08)
        self.shrinkage_plot.getPlotItem().setContentsMargins(0, 18, 0, 0)

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

        # Eigenes Widget/Dock fuer die Schwindungsmessung (siehe Begruendung
        # oben) -- gleicher Aufbau wie timeseries_widget: Graph plus eigene
        # Achsen-Zeile (Achsen zurücksetzen/einstellen, Zeitachse-Umschalter).
        self.shrinkage_widget = QtWidgets.QWidget()
        shrinkage_layout = QtWidgets.QVBoxLayout(self.shrinkage_widget)
        shrinkage_layout.setContentsMargins(4, 4, 4, 4)
        shrinkage_layout.addWidget(self.shrinkage_plot)
        shrinkage_time_row, self.combo_time_display_shrinkage, self.combo_runtime_unit_shrinkage = (
            self._build_time_display_row(self.shrinkage_plot)
        )
        shrinkage_layout.addLayout(shrinkage_time_row)

        # Querschnitt-Graph (Nutzerwunsch): horizontaler/vertikaler
        # Temperatur-Schnitt durch das AKTUELL angezeigte Bild an der
        # Cursor-/fixierten Position (siehe crosssection_ops.py) -- KEINE
        # Zeitachse (X ist hier ein Pixel-Index), daher kein
        # _build_time_display_row wie bei den drei Kurven-Graphen oben, nur
        # der generische _reset_plot_view-Knopf. Als eigenes, TABIFIZIERTES
        # Dock gebaut (siehe _build_docks) statt als eigenes Fenster --
        # jedes Dock in dieser App ist ueber DockWidgetFloatable ohnehin
        # frei zu einem schwebenden Fenster herausziehbar, die "Tab oder
        # Fenster"-Frage stellt sich dadurch nicht wirklich.
        self.crosssection_plot = pg.PlotWidget()
        self.crosssection_plot.setLabel("left", "Temperatur", units="°C")
        self.crosssection_plot.showGrid(x=True, y=True, alpha=0.3)
        self.crosssection_plot.getPlotItem().getViewBox().setDefaultPadding(0.08)
        self.crosssection_curve = self.crosssection_plot.plot(pen=pg.mkPen("#38bdf8", width=2))
        self._trim_plot_context_menu(self.crosssection_plot)

        self.lbl_crosssection_position = QtWidgets.QLabel(
            "Position per Linksklick ins Bild, Pfeiltasten oder den beiden Feldern rechts wählen, "
            "um den Temperatur-Querschnitt der jeweiligen Zeile/Spalte zu sehen. Der normale, der "
            "Maus folgende Live-Cursor ist in diesem Tab deaktiviert."
        )
        self.lbl_crosssection_position.setWordWrap(True)

        self.radio_crosssection_horizontal = QtWidgets.QRadioButton("Horizontal (Zeile)")
        self.radio_crosssection_vertical = QtWidgets.QRadioButton("Vertikal (Spalte)")
        self.radio_crosssection_horizontal.setChecked(True)
        self.radio_crosssection_horizontal.toggled.connect(self._on_crosssection_direction_changed)
        self.radio_crosssection_vertical.toggled.connect(self._on_crosssection_direction_changed)
        crosssection_direction_row = QtWidgets.QHBoxLayout()
        crosssection_direction_row.addWidget(QtWidgets.QLabel("Richtung:"))
        crosssection_direction_row.addWidget(self.radio_crosssection_horizontal)
        crosssection_direction_row.addWidget(self.radio_crosssection_vertical)
        crosssection_direction_row.addStretch(1)
        btn_crosssection_reset_view = QtWidgets.QPushButton("Achsen zurücksetzen")
        btn_crosssection_reset_view.clicked.connect(partial(self._reset_plot_view, self.crosssection_plot))
        crosssection_direction_row.addWidget(btn_crosssection_reset_view)

        # Nutzerwunsch: die Position auch zeilen-/spaltengenau ueber ein
        # Eingabefeld waehlen koennen, statt nur per Klick/Pfeiltasten.
        # Beide Felder sind IMMER zusammen sichtbar (unabhaengig von der
        # gewaehlten Richtung), da der Fadenkreuz-Punkt selbst immer beide
        # Koordinaten hat -- die Richtung entscheidet nur, welche davon den
        # Schnitt bestimmt (siehe crosssection_ops.py:_update_crosssection_plot).
        self.spin_crosssection_row = QtWidgets.QSpinBox()
        self.spin_crosssection_row.setRange(0, 0)
        self.spin_crosssection_row.valueChanged.connect(self._on_crosssection_row_spin_changed)
        self.spin_crosssection_col = QtWidgets.QSpinBox()
        self.spin_crosssection_col.setRange(0, 0)
        self.spin_crosssection_col.valueChanged.connect(self._on_crosssection_col_spin_changed)
        crosssection_position_row = QtWidgets.QHBoxLayout()
        crosssection_position_row.addWidget(QtWidgets.QLabel("Zeile (Y):"))
        crosssection_position_row.addWidget(self.spin_crosssection_row)
        crosssection_position_row.addWidget(QtWidgets.QLabel("Spalte (X):"))
        crosssection_position_row.addWidget(self.spin_crosssection_col)
        crosssection_position_row.addStretch(1)

        self.crosssection_widget = QtWidgets.QWidget()
        crosssection_layout = QtWidgets.QVBoxLayout(self.crosssection_widget)
        crosssection_layout.setContentsMargins(4, 4, 4, 4)
        crosssection_layout.addWidget(self.lbl_crosssection_position)
        crosssection_layout.addWidget(self.crosssection_plot)
        crosssection_layout.addLayout(crosssection_direction_row)
        crosssection_layout.addLayout(crosssection_position_row)

        self.axis_live_bottom = TimeAxisItem()
        self.axis_live_top = TimeAxisItem(orientation="top")
        self.live_plot = pg.PlotWidget(
            axisItems={"bottom": self.axis_live_bottom, "top": self.axis_live_top}
        )
        self.live_plot.getPlotItem().showAxis("top", False)
        self.live_plot.setLabel("left", "Temperatur", units="°C")
        self.live_plot.showGrid(x=True, y=True, alpha=0.3)
        self.live_plot.getPlotItem().getViewBox().setDefaultPadding(0.08)
        self.live_plot.getPlotItem().setContentsMargins(0, 18, 0, 0)
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

        self._time_display_combos = [
            self.combo_time_display_timeseries, self.combo_time_display_live, self.combo_time_display_shrinkage,
        ]
        for combo in self._time_display_combos:
            combo.currentIndexChanged.connect(self._on_time_display_changed)
        self._runtime_unit_combos = [
            self.combo_runtime_unit_timeseries, self.combo_runtime_unit_live, self.combo_runtime_unit_shrinkage,
        ]
        for combo in self._runtime_unit_combos:
            combo.currentIndexChanged.connect(self._on_runtime_unit_changed)

        self._trim_plot_context_menu(self.timeseries_plot)
        self._trim_plot_context_menu(self.live_plot)
        self._trim_plot_context_menu(self.shrinkage_plot)

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

        # "Werkzeuge" statt der veralteten "Messbereiche & Legende" --
        # das Panel deckt seit dem Umbau vier gleichrangige Reiter ab
        # (Legende/Temperatur-Messung/Schwindungsmessung/Maßstab), von denen
        # "Messbereiche & Legende" nur noch zwei benannt hätte. Der Name
        # bleibt als windowTitle() erhalten (fuer den Ein-/Ausblenden-
        # Menuepunkt im "Ansicht"-Menue), die sichtbare Titelzeile samt
        # Rahmen darueber blendet -- wie bei timeseries_dock -- ein leeres
        # Platzhalter-Widget aus (Nutzerfeedback: die Ueberschrift/Umrandung
        # wirkte hier nur wie ein weiterer, ueberfluessiger Rahmen um das
        # ohnehin schon in eigene Registerkarten gegliederte Panel).
        self.control_dock = QtWidgets.QDockWidget("Werkzeuge", self)
        self.control_dock.setWidget(self.control_panel)
        self.control_dock.setAllowedAreas(side_areas)
        self.control_dock.setFeatures(dock_features)
        self.control_dock.setTitleBarWidget(QtWidgets.QWidget())
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

        # Eigener Tab "Schwindung" NEBEN "Zeitverlauf" (Nutzerwunsch: nicht
        # unter die Temperaturkurven quetschen, sondern -- wie frueher der
        # "Live (Cursor)"-Tab -- als eigener, gleichrangiger Tab). Die
        # eigentliche Titelzeile bleibt hier bewusst SICHTBAR (kein
        # setTitleBarWidget(...)): sie liefert -- anders als bei
        # timeseries_dock/control_dock oben -- den einzigen Anhaltspunkt, an
        # dem der von Qt automatisch erzeugten Tab-Leiste zu erkennen ist,
        # WELCHER der beiden Tabs gerade aktiv ist. Von Anfang an sichtbar
        # (Nutzerfeedback: der Tab soll NICHT erst nach "Aktivieren"/
        # "Berechnen" auftauchen, sondern von Beginn an da sein -- der Graph
        # zeigt bis zum ersten "Berechnen" einfach leer).
        self.shrinkage_dock = QtWidgets.QDockWidget("Schwindung", self)
        self.shrinkage_dock.setWidget(self.shrinkage_widget)
        self.shrinkage_dock.setAllowedAreas(side_areas)
        self.shrinkage_dock.setFeatures(dock_features)
        self.addDockWidget(QtCore.Qt.RightDockWidgetArea, self.shrinkage_dock)
        self.tabifyDockWidget(self.timeseries_dock, self.shrinkage_dock)

        # Querschnitt-Graph (siehe _build_plots/crosssection_ops.py) --
        # gleiches Tabifizierungs-Muster wie "Schwindung" oben.
        self.crosssection_dock = QtWidgets.QDockWidget("Querschnitt", self)
        self.crosssection_dock.setWidget(self.crosssection_widget)
        self.crosssection_dock.setAllowedAreas(side_areas)
        self.crosssection_dock.setFeatures(dock_features)
        self.addDockWidget(QtCore.Qt.RightDockWidgetArea, self.crosssection_dock)
        self.tabifyDockWidget(self.timeseries_dock, self.crosssection_dock)
        # Nutzerwunsch: die Schnitt-Linie im Thermobild UND der normale
        # Live-Cursor sollen sich danach richten, ob dieser Tab gerade
        # tatsaechlich im Vordergrund ist -- visibilityChanged feuert auch
        # beim reinen Tab-Wechsel innerhalb einer Dock-Gruppe (nicht nur beim
        # echten Ein-/Ausblenden), siehe crosssection_ops.py.
        self.crosssection_dock.visibilityChanged.connect(self._on_crosssection_dock_visibility_changed)

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
        # nie sichtbar, siehe oben, gehoert also nicht mehr dazu; shrinkage_
        # dock ist mit timeseries_dock TABIFIZIERT, teilt sich also dieselbe
        # Flaeche statt eigene Hoehe zu beanspruchen) -- beide haben also
        # zwangslaeufig dieselbe Breite. Ein resizeDocks(...,
        # Horizontal) mit zwei WIDERSPRUECHLICHEN Breiten fuer Docks derselben
        # Spalte (frueherer Bug) fuehrte zu einer unvorhersehbaren/"komischen"
        # Anfangsbreite; hier genuegt EIN Wert fuer die ganze Spalte. Bild-
        # Spalte (links) und Docks (rechts) sollen sich sonst zu gleichen
        # Teilen (50:50) die Fensterbreite teilen.
        self.resizeDocks(
            [self.control_dock, self.timeseries_dock], [420, 500], QtCore.Qt.Vertical
        )
        self.resizeDocks([self.control_dock], [self.width() // 2], QtCore.Qt.Horizontal)

        # tabifyDockWidget() bringt IMMER den zuletzt tabifizierten Dock
        # (hier: shrinkage_dock) in den Vordergrund -- ohne dieses explizite
        # Zurückholen wäre "Schwindung" (i.d.R. noch leer) statt
        # "Zeitverlauf" der zuerst sichtbare Tab, was sowohl fürs normale
        # Arbeiten als auch für den Grafik-/SVG-Export (der die Sichtbarkeit
        # von timeseries_dock voraussetzt) falsch wäre.
        self.timeseries_dock.raise_()

    def _build_shortcuts(self) -> None:
        # Standardkontext (WindowShortcut) reicht: Qt bevorzugt bei fokussierten
        # Text-/Zahlenfeldern automatisch deren eigene Cursor-Navigation
        # (Pfeiltasten/Pos1/Ende) gegenueber diesen Shortcuts, d.h. Tippen in
        # ROI-Namen/Spinboxen wird dadurch nicht gestoert (empirisch geprueft).
        shortcut_specs = [
            # Nutzerwunsch: waehrend der "Querschnitt"-Tab im Vordergrund ist,
            # verschieben Pfeiltasten stattdessen die Schnitt-Position (siehe
            # crosssection_ops.py:_on_key_step_col/_row) -- sonst (normaler
            # Fall) wie bisher Frame vor/zurueck. Hoch/Runter sind ausserhalb
            # dieses Tabs bewusst wirkungslos (bisher gar nicht belegt).
            (QtCore.Qt.Key_Right, partial(self._on_key_step_col, 1)),
            (QtCore.Qt.Key_Left, partial(self._on_key_step_col, -1)),
            (QtCore.Qt.Key_Up, partial(self._on_key_step_row, -1)),
            (QtCore.Qt.Key_Down, partial(self._on_key_step_row, 1)),
            (QtCore.Qt.Key_PageUp, lambda: self._step_frame(10)),
            (QtCore.Qt.Key_PageDown, lambda: self._step_frame(-10)),
            (QtCore.Qt.Key_Home, self._jump_to_first_frame),
            (QtCore.Qt.Key_End, self._jump_to_last_frame),
            (QtCore.Qt.Key_Space, self._on_space_pressed),
            # Nutzerwunsch: Tasten 1-5 armieren direkt den jeweiligen
            # Standard-Messbereich zum Platzieren (siehe roi_ops.py:
            # _on_arm_roi_shortcut) -- Ziffern werden von einem fokussierten
            # Spinbox-/Textfeld weiterhin automatisch als Zahlen-Eingabe
            # vorrangig behandelt (Qt ShortcutOverride, dasselbe Prinzip wie
            # oben bei Pfeiltasten/Pos1/Ende).
            (QtCore.Qt.Key_1, partial(self._on_arm_roi_shortcut, 1)),
            (QtCore.Qt.Key_2, partial(self._on_arm_roi_shortcut, 2)),
            (QtCore.Qt.Key_3, partial(self._on_arm_roi_shortcut, 3)),
            (QtCore.Qt.Key_4, partial(self._on_arm_roi_shortcut, 4)),
            (QtCore.Qt.Key_5, partial(self._on_arm_roi_shortcut, 5)),
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

