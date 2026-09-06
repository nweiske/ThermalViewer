"""Low-Level-Render-Pipeline für Bild-/Video-/SVG-Export (Widget-Rendering, Layout-Kombination, Zeitleisten-Overlay)."""
from __future__ import annotations

from pathlib import Path

import numpy as np
from qtpy import QtCore, QtGui, QtSvg, QtWidgets

from ..plot_items import (
    _fraction_of,
)


class _RenderPipelineMixin:
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
        graph_background: QtGui.QColor | None = None,
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
            # Bugfix ("Hintergrund im Graph schwarz" beim Video-Export):
            # _render_widget_into_painter rendert fuer Nicht-glw-Widgets ueber
            # widget.scene().render() DIREKT auf die Szene -- pyqtgraphs
            # GraphicsView.setBackground() setzt aber nur die Hintergrund-
            # farbe der VIEW (fuer deren eigenes paintEvent), NICHT die der
            # QGraphicsScene selbst. Ein direkter scene().render()-Aufruf
            # geht daher am View-Hintergrund vorbei und liesse den
            # Graph-Bereich transparent -- sichtbar wurde stattdessen der
            # dunkle Basis-Fill dieses gesamten Canvas (self._image_bg)
            # durchscheinen. _render_widget_image (fuer den funktionierenden
            # Bild-Export) vermeidet das, indem es JEDES Widget in ein
            # eigenes, vorab korrekt gefuelltes QImage rendert -- hier wird
            # stattdessen nur der Graph-Zielbereich explizit mit seiner
            # EIGENEN (nicht der Bild-)Hintergrundfarbe vorgefuellt, bevor
            # die Szene darauf gezeichnet wird.
            if graph_background is not None:
                painter.fillRect(QtCore.QRectF(graph_x, graph_y, graph_width, graph_height), graph_background)
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

        start_frac = _fraction_of(start_idx, last_idx)
        end_frac = _fraction_of(end_idx, last_idx)
        frac = _fraction_of(idx, last_idx)

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
        vertical, image_first = _RenderPipelineMixin._combined_panel_order(position)
        panels = (
            [(image, image_title), (graph, graph_title)] if image_first
            else [(graph, graph_title), (image, image_title)]
        )
        (first_img, first_title), (second_img, second_title) = panels
        layout = _RenderPipelineMixin._combined_layout(
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
                painter.drawImage(_RenderPipelineMixin._centered_x(layout, img.width()), y, img)
                y += img.height() + gap
        else:
            # Bugfix (Punkt 10): beide Panels zeichneten bisher unabhaengig
            # von ihrer Hoehe auf DERSELBEN y-Position (margin + title_height)
            # -- bei deutlich unterschiedlich hohen Panels (typischerweise:
            # ein hohes, quadratisches Thermobild neben einem flachen,
            # breiten Graphen) wirkte das Ergebnis unausgewogen/schief statt
            # ordentlich zueinander ausgerichtet (Bugreport: "das Diagramm
            # bitte Mittig"). Der VIDEO-/Bildstapel-Export zentriert das bei
            # "links"/"rechts" bereits genauso (siehe _render_video_frame) --
            # hier fehlte das Gegenstueck fuer den Raster-/SVG-Grafikexport.
            row_height = max(first_img.height(), second_img.height())
            x = margin
            for img, title in panels:
                text_rect = QtCore.QRect(x, margin, img.width(), title_height)
                painter.drawText(text_rect, QtCore.Qt.AlignLeft | QtCore.Qt.AlignVCenter, title)
                image_y = margin + title_height + (row_height - img.height()) // 2
                painter.drawImage(x, image_y, img)
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
        vertical, image_first = self._combined_panel_order(position)
        image_panel = (image_widget, image_title, img_w, img_h, True)
        curve_panel = (curve_widget, curve_title, curve_w, curve_h, False)
        panels = [image_panel, curve_panel] if image_first else [curve_panel, image_panel]
        (_, _, w1, h1, _), (_, _, w2, h2, _) = panels
        layout = self._combined_layout(dpi, (w1, h1), (w2, h2), vertical)
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
        logical_layout = self._combined_layout(96, logical_first, logical_second, vertical)

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
        if vertical:
            y = margin
            for widget, title, w, h, is_image in panels:
                painter.drawText(
                    QtCore.QRect(margin, y, width - 2 * margin, title_height),
                    QtCore.Qt.AlignLeft | QtCore.Qt.AlignVCenter, title,
                )
                y += title_height
                self._render_svg_panel(painter, scale, widget, w, h, is_image, self._centered_x(layout, w), y)
                y += h + gap
        else:
            # Siehe _combine_image_and_graph (Punkt 10) fuer den vollen
            # Grund -- dasselbe Zentrierungs-Gegenstueck fuer den SVG-Pfad.
            row_height = max(h1, h2)
            x = margin
            for widget, title, w, h, is_image in panels:
                painter.drawText(
                    QtCore.QRect(x, margin, w, title_height),
                    QtCore.Qt.AlignLeft | QtCore.Qt.AlignVCenter, title,
                )
                panel_y = margin + title_height + (row_height - h) // 2
                self._render_svg_panel(painter, scale, widget, w, h, is_image, x, panel_y)
                x += w + gap

        painter.end()
        self._verify_file_written(path)
        return width, height

    def _render_svg_panel(
        self,
        painter: QtGui.QPainter,
        scale: float,
        widget: QtWidgets.QWidget,
        w: int,
        h: int,
        is_image: bool,
        x: int,
        y: int,
    ) -> None:
        """Zeichnet ein einzelnes Panel (Bild oder Kurve) von _save_combined_svg
        an Position (x, y) -- fuer beide Panels (vertikal/horizontal) gleich,
        daher als eigene Methode statt zweifach dupliziert."""
        painter.save()
        painter.translate(x, y)
        if is_image:
            self._render_widget_into_painter(painter, widget, w, h, scale)
        else:
            widget.scene().render(painter, QtCore.QRectF(0, 0, w, h), self._visible_scene_rect(widget))
        painter.restore()

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
