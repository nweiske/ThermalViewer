"""Wiederverwendbare pyqtgraph-/Qt-Bausteine rund um die Kurven-Graphen und
den Timeline-Schieberegler des Hauptfensters, unabhängig von MainWindow."""
from __future__ import annotations

import math
import time

import pyqtgraph as pg
import pyqtgraph.exporters as pg_exporters
from qtpy import QtCore, QtGui, QtSvg, QtWidgets

# Sekunden je Einheit fuer eine numerische Laufzeit-Anzeige ("dritte
# Zeitachse", Nutzerwunsch) -- Modul-Ebene statt Klassenattribut, damit
# TimeAxisItem.tickStrings() und MainWindow._format_runtime()/
# _runtime_export_value() dieselbe Tabelle nutzen, ohne dass TimeAxisItem
# dafuer von MainWindow abhaengen muesste.
_RUNTIME_UNIT_DIVISORS = {"s": 1.0, "min": 60.0, "h": 3600.0}


def _dash_pen(width: int) -> QtGui.QPen:
    """Gestrichelter grauer Stift fester Farbe, fuer die Frame-Marker in
    MainWindow._scaled_export_visuals (dort vor/nach dem Export in zwei
    Stiftbreiten gebraucht)."""
    return pg.mkPen("#888888", width=width, style=QtCore.Qt.DashLine)


def _fraction_of(index: int, last_index: int) -> float:
    """Position von index innerhalb [0, last_index], geklemmt auf [0, 1] --
    fuer die Zeitleiste in _draw_video_timeline_overlay (dort fuer Start/
    Ende/aktuellen Frame gebraucht)."""
    return max(0.0, min(1.0, index / last_index))


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


class _LoadProgressReporter:
    """Callable progress_cb fuer load_paths() (siehe MainWindow._load_paths) --
    haelt lediglich eine Referenz auf den QProgressDialog dieses einen
    Ladevorgangs; als Klasse statt einer Closure, damit sie unabhaengig von
    ihrem Erzeugungsort im Debugger benannt/inspiziert werden kann.

    setValue()/processEvents() werden zeitlich gedrosselt (max. ~20x/s) statt
    bei JEDER einzelnen Datei aufgerufen zu werden -- bei einigen hundert/
    tausend Dateien summierte sich der reine Overhead von processEvents()
    (inkl. Dialog-Repaint) pro Datei spuerbar auf und liess den ganzen
    Ladevorgang ruckelig/zaeh wirken (Bugreport: "scheint kurz zu hängen...
    schaut nicht flüssig aus"). Der letzte Aufruf (done == total) aktualisiert
    IMMER, damit der Dialog zuverlässig bei 100% ankommt."""

    _MIN_INTERVAL_S = 0.05

    def __init__(self, dialog: QtWidgets.QProgressDialog, extra_cb=None) -> None:
        self._dialog = dialog
        self._last_update = 0.0
        # Zusaetzlich zum modalen Dialog auch die persistente Aktivitaets-
        # anzeige unten links aktualisieren (siehe status_activity.py) --
        # optional, damit dieselbe throttlte Update-Rate fuer beide gilt,
        # ohne processEvents() zweimal aufzurufen.
        self._extra_cb = extra_cb

    def __call__(self, done: int, total: int) -> None:
        now = time.monotonic()
        if done < total and now - self._last_update < self._MIN_INTERVAL_S:
            return
        self._last_update = now
        self._dialog.setValue(done)
        if self._extra_cb is not None:
            self._extra_cb(done, total)
        QtWidgets.QApplication.processEvents()
