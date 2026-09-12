"""Temporäre Sichtbarkeits-/Skalierungs-Zustände rund um Export-Renderpfade
(Stiftbreiten/Legende hochskalieren, Maßstab/Messungen/Live-Cursor ein-/
ausblenden, UI während des Exports einfrieren, Zeitachsen für SVG umbauen) --
abgespalten von export_common.py (das die eher zustandslosen Export-
Hilfsfunktionen wie Formatierung/Zielordner/Zeitstempel-Auflösung behält),
da dieser Teil qualitativ ein eigenes Thema ist: fast ausschließlich
Context-Manager, die einen Zustand kurzzeitig ändern und danach exakt
wiederherstellen."""
from __future__ import annotations

import contextlib

import numpy as np
import pyqtgraph as pg
from qtpy import QtCore, QtGui, QtWidgets

from ..data import (
    zip_strict,
)
from ..plot_items import (
    TimeAxisItem,
    _dash_pen,
)


class _ExportVisualsMixin:
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
        for entry in self.measurements:
            entry.line.setPen(pg.mkPen(entry.color, width=round(3 * ps)))

        self.live_cursor_marker.setPen(pg.mkPen("#38bdf8", width=round(2 * ps)))
        self.live_curve.setPen(pg.mkPen("#38bdf8", width=round(2 * ps)))
        self.timeseries_live_curve.setPen(pg.mkPen("#38bdf8", width=round(2 * ps)))
        self.frame_marker.setPen(_dash_pen(round(1 * ps)))
        self.live_frame_marker.setPen(_dash_pen(round(1 * ps)))

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
            for entry in self.measurements:
                entry.line.setPen(pg.mkPen(entry.color, width=3))
            self.live_cursor_marker.setPen(pg.mkPen("#38bdf8", width=2))
            self.live_curve.setPen(pg.mkPen("#38bdf8", width=2))
            self.timeseries_live_curve.setPen(pg.mkPen("#38bdf8", width=2))
            self.frame_marker.setPen(_dash_pen(1))
            self.live_frame_marker.setPen(_dash_pen(1))
            for lg, t in zip_strict(legends, old_legend_transforms):
                lg.setTransform(t)

    @contextlib.contextmanager
    def _temporary_scale_visuals(self, include_ruler: bool, selected_numbers: set[int]):
        """Blendet waehrend eines Thermobild-Exports GENAU die vom Nutzer im
        Export-Dialog ausgewaehlten Maßstab-/Messungs-Visualisierungen ein
        (alle anderen aus) und stellt danach den vorherigen Sichtbarkeits-
        Zustand exakt wieder her (Punkt 12, Nutzerwunsch: "Maßstab +
        Messungen mit exportieren, wenn man möchte... genauso wie die ROIs
        einzeln an-/abwählbar"). Wirkt unabhängig von der aktuellen Live-
        Ansicht (siehe chk_scale_visible/_on_toggle_scale_visuals, Punkt 5) --
        eine fuer die Anzeige ausgeblendete Messung kann trotzdem gezielt mit
        exportiert werden und umgekehrt.

        Die ROI-RECHTECKE selbst sind davon nicht betroffen -- die sind immer
        fester Bestandteil des Thermobilds; hier geht es nur um die beiden
        ZUSAETZLICHEN Overlays Maßstab-Linie und Ad-hoc-Messungen."""
        prev_ruler_visible = (
            (self._ruler_line.isVisible(), self._ruler_text.isVisible())
            if self._ruler_line is not None and self._ruler_text is not None
            else None
        )
        prev_measurement_visible = {entry.number: entry.line.isVisible() for entry in self.measurements}

        if prev_ruler_visible is not None:
            # Nur zeigen, wenn tatsaechlich ein gueltiger Maßstab besteht --
            # sonst haette die Checkbox (falls z.B. aus einer frueheren
            # Aufnahme noch angehakt) keine sichtbare Wirkung.
            want_ruler = include_ruler and self._ruler_mm_value is not None
            self._ruler_line.setVisible(want_ruler)
            self._ruler_text.setVisible(want_ruler)
        for entry in self.measurements:
            visible = entry.number in selected_numbers
            entry.line.setVisible(visible)
            entry.text.setVisible(visible)
        try:
            yield
        finally:
            if prev_ruler_visible is not None:
                self._ruler_line.setVisible(prev_ruler_visible[0])
                self._ruler_text.setVisible(prev_ruler_visible[1])
            for entry in self.measurements:
                if entry.number in prev_measurement_visible:
                    visible = prev_measurement_visible[entry.number]
                    entry.line.setVisible(visible)
                    entry.text.setVisible(visible)

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

        for curve, (x, y) in zip_strict(curves, old_curve_data):
            if x is not None:
                curve.setData(np.asarray(x, dtype=float) - t0, y)
        for marker, value in zip_strict(markers, old_marker_values):
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
            for curve, (x, y) in zip_strict(curves, old_curve_data):
                if x is not None:
                    curve.setData(x, y)
            for marker, value in zip_strict(markers, old_marker_values):
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
        GraphContentSelector in dialogs/graph_selector.py fuer den Bugreport dazu
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

        if want_live and not prev_live_checked:
            self._show_live_curve_in_timeseries()
        elif not want_live and prev_live_checked:
            self._hide_live_curve_in_timeseries()
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
                self._hide_live_curve_in_timeseries()
            elif not want_live and prev_live_checked:
                self._show_live_curve_in_timeseries()
            if x_auto:
                vb.enableAutoRange(x=True)
            else:
                vb.setXRange(x0, x1, padding=0)
            if y_auto:
                vb.enableAutoRange(y=True)
            else:
                vb.setYRange(y0, y1, padding=0)

    def _show_live_curve_in_timeseries(self) -> None:
        """Blendet die Live-Cursor-Kurve im Zeitverlauf-Graphen ein -- von
        _temporary_graph_content in beide Richtungen genutzt (an-/wieder
        ausschalten), daher als eigene Methode statt zweifach dupliziert."""
        values = self._live_cursor_series(self._hover_row, self._hover_col)
        self.timeseries_live_curve.setData(self.recording.unix_seconds(), values)
        self.timeseries_legend.addItem(self.timeseries_live_curve, "Live (Cursor)")
        self.timeseries_live_curve.setVisible(True)

    def _hide_live_curve_in_timeseries(self) -> None:
        self.timeseries_legend.removeItem(self.timeseries_live_curve)
        self.timeseries_live_curve.setVisible(False)
