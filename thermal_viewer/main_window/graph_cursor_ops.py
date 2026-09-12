"""Koordinatenanzeige (X/Y) beim Hovern über die Zeitverlauf-/Live-Graphen
(Nutzerwunsch, Punkt 2) -- eine kleine, mitlaufende Einblendung unten
rechts im jeweiligen Graphen zeigt die Zeit- (je nach _time_display_mode,
siehe theming.py) und Temperatur-Koordinate an der Cursor-Position, analog
zur Live-Cursor-Anzeige im Thermobild (siehe mouse_ops.py).

Die Einblendung ist bewusst ein normales QLabel als Kind-Widget des
jeweiligen PlotWidget (Positionierung in Widget-/Bildschirm-Pixeln), NICHT
ein pg.TextItem in Daten-Koordinaten -- so bleibt die Ecke unten rechts
IMMER an der gleichen Stelle, unabhaengig vom aktuellen Zoom/Pan des
Graphen, ohne bei jeder Ansichtsänderung neu umgerechnet werden zu
muessen."""
from __future__ import annotations

from datetime import datetime

from qtpy import QtCore, QtWidgets


class _GraphCursorMixin:
    def _build_graph_cursor_overlay(self) -> None:
        self.lbl_graph_cursor_timeseries = self._make_graph_cursor_label(self.timeseries_plot)
        self.lbl_graph_cursor_live = self._make_graph_cursor_label(self.live_plot)

        self.timeseries_plot.scene().sigMouseMoved.connect(
            lambda pos: self._on_graph_mouse_moved(self.timeseries_plot, self.lbl_graph_cursor_timeseries, pos)
        )
        self.live_plot.scene().sigMouseMoved.connect(
            lambda pos: self._on_graph_mouse_moved(self.live_plot, self.lbl_graph_cursor_live, pos)
        )
        # sigMouseMoved feuert nur, WAEHREND sich die Maus innerhalb des
        # Graphen bewegt -- verlaesst sie ihn ganz (z.B. Richtung Panel),
        # kommt danach kein weiteres Signal mehr, die Einblendung bliebe
        # sonst mit dem letzten Stand haengen. Ein QWidget-eventFilter fuer
        # QEvent.Leave faengt genau diesen Fall ab (siehe eventFilter unten).
        self.timeseries_plot.installEventFilter(self)
        self.live_plot.installEventFilter(self)

    @staticmethod
    def _make_graph_cursor_label(plot_widget: QtWidgets.QWidget) -> QtWidgets.QLabel:
        label = QtWidgets.QLabel(plot_widget)
        label.setStyleSheet(
            "background-color: rgba(20, 20, 20, 170); color: #f5f5f5; "
            "padding: 2px 6px; border-radius: 3px; font-size: 11px;"
        )
        # Darf selbst keine Mausereignisse abfangen -- sonst wuerde die
        # Einblendung, sobald sie unter dem Cursor liegt, das darunter
        # liegende sigMouseMoved des Graphen blockieren.
        label.setAttribute(QtCore.Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        label.hide()
        return label

    def _on_graph_mouse_moved(self, plot_widget: QtWidgets.QWidget, label: QtWidgets.QLabel, scene_pos) -> None:
        view_box = plot_widget.getPlotItem().getViewBox()
        if (
            self.recording is None
            or self.recording.n_frames == 0
            or not view_box.sceneBoundingRect().contains(scene_pos)
        ):
            label.hide()
            return
        view_pos = view_box.mapSceneToView(scene_pos)
        x_text = self._format_graph_cursor_x(view_pos.x())
        label.setText(f"{x_text}   {view_pos.y():.1f} °C")
        label.adjustSize()
        margin = 6
        label.move(plot_widget.width() - label.width() - margin, plot_widget.height() - label.height() - margin)
        label.show()
        label.raise_()

    def _format_graph_cursor_x(self, unix_x: float) -> str:
        """x-Werte der Kurven sind immer Unix-Sekunden (siehe
        Recording.unix_seconds()) -- fuer die Anzeige je nach
        _time_display_mode (siehe theming.py) entweder als Uhrzeit oder als
        Laufzeit ab Aufnahmebeginn formatiert. Nutzt bewusst NICHT
        TimeAxisItem.tickStrings() direkt: das haengt intern von
        zoomLevel/tickValues() aus dem normalen Achsen-Zeichenzyklus ab und
        ist ausserhalb davon nicht sicher aufrufbar (AttributeError). Statt-
        dessen dieselben, bereits vorhandenen Bausteine wie Statuszeile/
        Export wiederverwenden (_format_runtime, datetime.fromtimestamp --
        Recording.unix_seconds() erzeugt seine Werte ebenfalls ueber
        naive datetime.timestamp(), also in derselben, lokalen Interpretation)."""
        if self._time_display_mode == "runtime":
            t0 = self.recording.unix_seconds()[0]
            return self._format_runtime(max(0.0, unix_x - t0))
        return datetime.fromtimestamp(unix_x).strftime("%Y-%m-%d %H:%M:%S")

    def eventFilter(self, obj, event) -> bool:
        if event.type() == QtCore.QEvent.Type.Leave:
            if obj is getattr(self, "timeseries_plot", None):
                self.lbl_graph_cursor_timeseries.hide()
            elif obj is getattr(self, "live_plot", None):
                self.lbl_graph_cursor_live.hide()
        return super().eventFilter(obj, event)
