"""Querschnitt-Graph (Nutzerwunsch): horizontaler/vertikaler Temperatur-
Schnitt durch das AKTUELL angezeigte Bild an einer frei waehlbaren Position
-- zeigt, wie sich die Temperatur ueber eine ganze Bildzeile/-spalte
verteilt, inkl. Markierungen, wo Messbereiche (ROIs) bzw. die erkannte
Schwindungs-Kontur diese Zeile/Spalte schneiden.

Nutzt bewusst eine EIGENE, von self._hover_row/self._hover_col (dem
normalen Live-Cursor der anderen Tabs, siehe mouse_ops.py) UNABHAENGIGE
Positions-Ablage (self._crosssection_row/self._crosssection_col) samt
eigenem Fixier-Flag (self._crosssection_pinned) -- Nutzerwunsch: dasselbe
Verhalten wie der normale Live-Cursor (frei der Maus folgend, Linksklick
fixiert, Rechtsklick loest wieder), aber unabhaengig von dessen Fixierung
in den anderen Tabs. Waehrend der "Querschnitt"-Tab tatsaechlich im
Vordergrund ist (siehe _crosssection_tab_active, verwendet dasselbe
visibleRegion()-Muster wie export_visuals.py:_widget_raised_for_export):
  - Maus-Hover OHNE Fixierung laesst die Position (und die gestrichelte
    Linie im Thermobild) live der Maus folgen (_handle_crosssection_hover),
  - ein Linksklick fixiert die Position (Zeile UND Spalte gemeinsam, wie
    ein Fadenkreuz), ein Rechtsklick loest die Fixierung wieder,
  - die Linie im Thermobild ist ein ECHTES, per pyqtgraph ziehbares
    InfiniteLine (movable=True, siehe ui_build.py) -- Ziehen fixiert
    ebenfalls (_on_crosssection_image_line_dragged),
  - Pfeiltasten verschieben die Position pixelgenau UND fixieren sie
    (siehe ui_build.py:_build_shortcuts, _on_key_step_col/_row),
  - zwei Spinboxen erlauben dieselbe Eingabe zahlenwertig (fixieren
    ebenfalls).
Ausserhalb dieses Tabs sind Maus-Hover/-Klick wirkungslos, Pfeiltasten
fallen auf ihre bisherige Bedeutung (Frame vor/zurueck) zurueck.

Marker-Muster: bei jedem Update werden zuerst ALLE vorher gezeichneten
Marker entfernt und komplett neu aufgebaut (dasselbe "clear-and-redraw" wie
_rebuild_shrinkage_contour_overlay in shrinkage_ops.py) -- robuster als
einzelne Marker-Objekte ueber mehrere Aufrufe hinweg nachzuverfolgen. ROI-/
Bounding-Box-/Kontur-Marker folgen zusaetzlich dem aktiven Ebenen-Tab
(siehe layer_tabs_ops.py:_is_layer_tab_active), damit der Graph nicht mehr
Marker zeigt, als das Thermobild selbst gerade an Overlays einblendet."""
from __future__ import annotations

import numpy as np
import pyqtgraph as pg
from qtpy import QtCore


class _CrossSectionMixin:
    def _crosssection_tab_active(self) -> bool:
        """visibleRegion().isEmpty() statt isVisible() -- Letzteres meldet
        fuer eine im Hintergrund liegende, tabifizierte Dock-Registerkarte
        faelschlich weiterhin True (siehe export_visuals.py fuer dieselbe,
        dort schon geloeste Problematik)."""
        return not self.crosssection_dock.visibleRegion().isEmpty()

    def _on_crosssection_dock_visibility_changed(self, _visible: bool) -> None:
        self._update_crosssection_image_line()

    def _reset_crosssection_state_for_recording(self) -> None:
        """Bei jedem (Neu-)Laden einer Aufnahme (siehe frame_nav.py:
        _set_recording): Spinbox-Wertebereiche auf die neue Bildgroesse
        setzen und die Position auf die Bildmitte zuruecksetzen -- eine
        Position der VORHERIGEN Aufnahme waere bei abweichender Groesse
        bedeutungslos oder sogar ausserhalb des neuen Bildes."""
        rows, cols = self.recording.shape
        self.spin_crosssection_row.blockSignals(True)
        self.spin_crosssection_row.setRange(0, max(0, rows - 1))
        self.spin_crosssection_row.blockSignals(False)
        self.spin_crosssection_col.blockSignals(True)
        self.spin_crosssection_col.setRange(0, max(0, cols - 1))
        self.spin_crosssection_col.blockSignals(False)
        self._crosssection_pinned = False
        self._set_crosssection_position(rows // 2, cols // 2, pin=False)

    def _on_crosssection_direction_changed(self, _checked: bool = True) -> None:
        # Verbunden mit BEIDEN Radiobuttons' toggled (siehe ui_build.py) --
        # ein Wechsel feuert es zweimal (einmal True, einmal False); der
        # Parameter wird bewusst ignoriert und der tatsaechliche Zustand
        # stattdessen frisch abgefragt, damit beide Aufrufe verlaesslich
        # dasselbe (korrekte) Ergebnis liefern.
        self._crosssection_direction = (
            "horizontal" if self.radio_crosssection_horizontal.isChecked() else "vertical"
        )
        self._update_crosssection_plot()
        self._update_crosssection_image_line()

    def _on_crosssection_row_spin_changed(self, value: int) -> None:
        col = self._crosssection_col if self._crosssection_col is not None else 0
        self._set_crosssection_position(value, col)

    def _on_crosssection_col_spin_changed(self, value: int) -> None:
        row = self._crosssection_row if self._crosssection_row is not None else 0
        self._set_crosssection_position(row, value)

    def _set_crosssection_position(self, row: int, col: int, *, pin: bool = True) -> None:
        """Zentraler Setter -- haelt self._crosssection_row/_col UND die
        beiden Spinboxen (blockSignals-geschuetzt, um keine doppelte
        Aktualisierung ueber deren valueChanged auszuloesen) synchron,
        egal ob der Aufruf von einem Klick, einer Pfeiltaste, einer
        Spinbox oder einem Linien-Drag kommt. pin=True (Standard) markiert
        die Position zusaetzlich als bewusst fixiert (self._crosssection_
        pinned) -- reines Maus-Hover ruft mit pin=False auf, damit es die
        Position NICHT dauerhaft festnagelt (siehe _handle_crosssection_hover)."""
        self._crosssection_row = row
        self._crosssection_col = col
        if pin:
            self._crosssection_pinned = True
        self.spin_crosssection_row.blockSignals(True)
        self.spin_crosssection_row.setValue(row)
        self.spin_crosssection_row.blockSignals(False)
        self.spin_crosssection_col.blockSignals(True)
        self.spin_crosssection_col.setValue(col)
        self.spin_crosssection_col.blockSignals(False)
        self._update_crosssection_plot()
        self._update_crosssection_image_line()

    def _handle_crosssection_hover(self, scene_pos) -> None:
        """Reines Maus-Hover (kein Klick) -- NUR wirksam, waehrend die
        Position nicht fixiert ist (self._crosssection_pinned), sonst
        identisches Verhalten zum normalen Live-Cursor der anderen Tabs
        (siehe mouse_ops.py:_on_scene_mouse_moved)."""
        if self._crosssection_pinned:
            return
        row_col = self._pixel_at_scene_pos(scene_pos)
        if row_col is None:
            return
        row, col = row_col
        if (row, col) == (self._crosssection_row, self._crosssection_col):
            return
        self._set_crosssection_position(row, col, pin=False)

    def _handle_crosssection_click(self, event) -> None:
        """NUR waehrend der "Querschnitt"-Tab aktiv ist (siehe
        mouse_ops.py:_on_scene_mouse_clicked) -- Linksklick fixiert die
        Fadenkreuz-Position auf die angeklickte Stelle (Zeile UND Spalte
        gemeinsam), Rechtsklick loest eine bestehende Fixierung wieder,
        exakt das Gegenstueck-Muster des normalen Live-Cursors (siehe
        mouse_ops.py, Rechtsklick-Zweig dort)."""
        if event.button() == QtCore.Qt.RightButton:
            if not self._crosssection_pinned:
                return
            self._crosssection_pinned = False
            row_col = self._pixel_at_scene_pos(event.scenePos())
            if row_col is not None:
                self._set_crosssection_position(*row_col, pin=False)
            self.statusBar().showMessage("Querschnitt-Position folgt wieder dem Mauscursor.", 3000)
            return
        if event.button() != QtCore.Qt.LeftButton:
            return
        row_col = self._pixel_at_scene_pos(event.scenePos())
        if row_col is None:
            return
        row, col = row_col
        self._set_crosssection_position(row, col)
        self.statusBar().showMessage(f"Querschnitt-Position fixiert auf Zeile {row}, Spalte {col}.", 4000)

    def _on_crosssection_image_line_dragged(self, line) -> None:
        """sigDragged (im Unterschied zu sigPositionChanged) feuert
        AUSSCHLIESSLICH bei echtem User-Drag -- eigene programmatische
        setPos()-Aufrufe (siehe _update_crosssection_image_line) loesen es
        nicht aus, also keine Rueckkopplungs-Schleife. Ziehen fixiert die
        Position ebenfalls (pin=True, Standard von _set_crosssection_position)."""
        if self.recording is None or self._crosssection_row is None or self._crosssection_col is None:
            return
        rows, cols = self.recording.shape
        value = line.value()
        if self.radio_crosssection_horizontal.isChecked():
            row = max(0, min(rows - 1, int(round(value - 0.5))))
            self._set_crosssection_position(row, self._crosssection_col)
        else:
            col = max(0, min(cols - 1, int(round(value - 0.5))))
            self._set_crosssection_position(self._crosssection_row, col)

    def _on_key_step_col(self, delta: int) -> None:
        """Pfeiltaste Links/Rechts -- verschiebt die Querschnitt-Spalte,
        solange dessen Tab im Vordergrund ist, sonst (bisheriges Verhalten)
        einen Frame vor/zurueck."""
        if self._crosssection_tab_active():
            self._nudge_crosssection(0, delta)
        else:
            self._step_frame(delta)

    def _on_key_step_row(self, delta: int) -> None:
        """Pfeiltaste Hoch/Runter -- nur innerhalb des Querschnitt-Tabs
        belegt (verschiebt die Zeile), sonst wirkungslos (bisher ebenfalls
        unbelegt)."""
        if self._crosssection_tab_active():
            self._nudge_crosssection(delta, 0)

    def _nudge_crosssection(self, d_row: int, d_col: int) -> None:
        if self.recording is None:
            return
        rows, cols = self.recording.shape
        row = self._crosssection_row if self._crosssection_row is not None else rows // 2
        col = self._crosssection_col if self._crosssection_col is not None else cols // 2
        row = max(0, min(rows - 1, row + d_row))
        col = max(0, min(cols - 1, col + d_col))
        self._set_crosssection_position(row, col)

    def _update_crosssection_image_line(self) -> None:
        """Duenne, gestrichelte Linie DIREKT IM THERMOBILD an der aktuellen
        Schnitt-Position (siehe ui_build.py:_build_image_canvas) -- nur
        sichtbar, waehrend der Querschnitt-Tab tatsaechlich im Vordergrund
        ist (sonst wuerde sie ohne den zugehoerigen Graphen nur verwirren)."""
        if (
            not self._crosssection_tab_active()
            or self.recording is None
            or self._crosssection_row is None
            or self._crosssection_col is None
        ):
            self.crosssection_image_line.setVisible(False)
            return
        rows, cols = self.recording.shape
        if self.radio_crosssection_horizontal.isChecked():
            self.crosssection_image_line.setAngle(0)
            # setBounds begrenzt das Ziehen der Linie (siehe
            # _on_crosssection_image_line_dragged) automatisch auf den
            # Bildbereich -- kein eigener Clamping-Code fuer die Maus-
            # Geste noetig.
            self.crosssection_image_line.setBounds([0, rows])
            self.crosssection_image_line.setPos(self._crosssection_row + 0.5)
        else:
            self.crosssection_image_line.setAngle(90)
            self.crosssection_image_line.setBounds([0, cols])
            self.crosssection_image_line.setPos(self._crosssection_col + 0.5)
        self.crosssection_image_line.setVisible(True)

    def _update_crosssection_plot(self) -> None:
        """Zentrale Aktualisierung -- aufgerufen bei jeder Positionsaenderung
        (Klick/Pfeiltaste/Spinbox), jedem Frame-Wechsel (siehe mouse_ops.py:
        _update_status_bar) UND jedem Ebenen-Tab-Wechsel (siehe
        layer_tabs_ops.py). No-op-sicher ohne Aufnahme/Position."""
        for item in self._crosssection_markers:
            self.crosssection_plot.removeItem(item)
        self._crosssection_markers = []

        if self.recording is None or self._crosssection_row is None or self._crosssection_col is None:
            self.crosssection_curve.clear()
            return

        horizontal = self.radio_crosssection_horizontal.isChecked()
        self._crosssection_direction = "horizontal" if horizontal else "vertical"
        frame = self.recording.frames[self.current_index]
        rows, cols = self.recording.shape

        if horizontal:
            row = self._crosssection_row
            values = frame[row, :]
            xs = np.arange(cols)
            self.crosssection_plot.setLabel("bottom", "Spalte")
            self.lbl_crosssection_position.setText(f"Horizontaler Schnitt durch Zeile {row}.")
            cursor_pos = self._crosssection_col
        else:
            col = self._crosssection_col
            values = frame[:, col]
            xs = np.arange(rows)
            self.crosssection_plot.setLabel("bottom", "Zeile")
            self.lbl_crosssection_position.setText(f"Vertikaler Schnitt durch Spalte {col}.")
            cursor_pos = self._crosssection_row
        self.crosssection_curve.setData(xs, values)

        self._add_crosssection_marker(cursor_pos, "#888888", None)
        if self._is_layer_tab_active("roi"):
            for entry in self.roi_entries:
                if entry.placed and entry.is_visible_checked():
                    self._add_crosssection_roi_markers(entry, horizontal)
        if self._is_layer_tab_active("shrinkage"):
            self._add_crosssection_shrinkage_box_markers(horizontal)
            self._add_crosssection_contour_markers(horizontal)

    def _add_crosssection_marker(self, pos: float, color: str, label: str | None) -> None:
        """Eine einzelne, vertikale Markierungslinie im Querschnitt-Graphen
        an X-Position `pos` (die X-Achse ist dort IMMER der Pixel-Index der
        gewaehlten Zeile/Spalte, angle=90 also unabhaengig von der
        Schnittrichtung immer richtig)."""
        line = pg.InfiniteLine(
            pos=pos, angle=90, movable=False,
            pen=pg.mkPen(color, width=1, style=QtCore.Qt.DashLine),
            label=label, labelOpts={"color": color} if label else None,
        )
        self.crosssection_plot.addItem(line)
        self._crosssection_markers.append(line)

    def _add_crosssection_roi_markers(self, entry, horizontal: bool) -> None:
        """Zeichnet -- NUR wenn die aktuelle Zeile/Spalte tatsaechlich
        innerhalb der ROI-Box liegt -- deren beide Raender als Marker, in
        der Farbe des jeweiligen Messbereichs."""
        rows, cols = self.recording.shape
        row0, row1, col0, col1 = entry.bounds_px((rows, cols))
        if horizontal:
            if not (row0 <= self._crosssection_row < row1):
                return
            edges = (col0, col1)
        else:
            if not (col0 <= self._crosssection_col < col1):
                return
            edges = (row0, row1)
        for edge in edges:
            self._add_crosssection_marker(edge, entry.color, entry.name)

    def _add_crosssection_shrinkage_box_markers(self, horizontal: bool) -> None:
        """Zeichnet -- analog zu _add_crosssection_roi_markers -- die
        Raender der Schwindungsmessung-Bounding-Box (roi_shrink_area,
        siehe shrinkage_ops.py) in deren Boxfarbe, NUR wenn die Messung
        aktiviert ist (die Box im Thermobild ueberhaupt sichtbar ist) UND
        die aktuelle Zeile/Spalte sie tatsaechlich schneidet -- unabhaengig
        davon, ob "Berechnen" schon einmal ausgefuehrt wurde (die Box
        selbst ist schon vorher im Bild sichtbar, siehe Nutzerwunsch:
        "Ich möchte im 'Querschnitt'-Graphen auch die Boundingbox sehen")."""
        if not self._shrinkage_enabled:
            return
        rows, cols = self.recording.shape
        row0, row1, col0, col1 = self.roi_shrink_area.bounds_px((rows, cols))
        if horizontal:
            if not (row0 <= self._crosssection_row < row1):
                return
            edges = (col0, col1)
        else:
            if not (col0 <= self._crosssection_col < col1):
                return
            edges = (row0, row1)
        for edge in edges:
            self._add_crosssection_marker(edge, self._shrinkage_color_area, "Messbereich")

    def _add_crosssection_contour_markers(self, horizontal: bool) -> None:
        """Zeichnet die beiden Raender der erkannten Schwindungs-Kontur
        (siehe shrinkage_ops.py:_track_sample_blob) an der aktuellen Zeile/
        Spalte -- fuer eine Spalte (vertikaler Schnitt) gibt es dafuer
        keinen direkten Index, ein kurzer Scan ueber die (typischerweise
        wenige hundert) Zeilen des aktuellen Bildes liefert die oberste/
        unterste Zeile, an der die Kontur diese Spalte ueberdeckt."""
        result = self._shrinkage_result
        if result is None or not self._shrinkage_enabled:
            return
        spans = result["spans"].get(self.current_index, {})
        if not spans:
            return
        if horizontal:
            span = spans.get(self._crosssection_row)
            if span is None:
                return
            edges = span
        else:
            rows_hit = [r for r, (c0, c1) in spans.items() if c0 <= self._crosssection_col <= c1]
            if not rows_hit:
                return
            edges = (min(rows_hit), max(rows_hit))
        for edge in edges:
            self._add_crosssection_marker(edge, self._shrinkage_color_contour, "Kontur")
