"""Rohdaten-Bereinigung: einzelne Ausreißer-Bilder (z.B. durch eine kurze
Kamera-/Übertragungsstörung) anhand der Temperaturänderung zum jeweils
vorherigen Bild an frei markierten Referenzpunkten erkennen und aus
Kurven/Wiedergabe/Export ausblenden -- OHNE sie oder ihre Zeitstempel
wirklich zu löschen (self.recording bleibt unverändert), siehe
DataCleaningDialog (dialogs/data_cleaning.py) für die Bedienoberfläche.

Nutzerwunsch: "Video-/Bilderstapelexport + Genereller Datenexport: Rohdaten
müssen vor der eigentlichen Auswertung 'gesäubert' werden ... Einen/mehrere
Punkte im Bild markieren/setzen können, und damit vorselektieren (dT
zwischen einzelnen Bildern betrachten -- wenn massive Änderung dann diese
Bilder rausschmeißen (Zeitstempel beibehalten!))". Auf Rückfrage bestätigt:
(a) ein Bild gilt nur als Ausreißer, wenn AN JEDEM markierten Punkt der
Schwellenwert überschritten wird (nicht schon bei irgendeinem einzelnen),
(b) ausgeblendet statt endgültig gelöscht, mit Wiederherstellen-Liste,
(c) sowohl automatisch direkt nach dem Laden angeboten als auch jederzeit
danach über "Daten > Rohdaten säubern…" erreichbar."""
from __future__ import annotations

from functools import partial

import numpy as np
import pyqtgraph as pg
from qtpy import QtCore, QtGui, QtWidgets


class _DataCleaningMixin:
    def _open_data_cleaning_dialog(self) -> None:
        if self.recording is None:
            QtWidgets.QMessageBox.information(self, "Keine Daten", "Bitte zuerst eine Messreihe laden.")
            return
        from ..dialogs import DataCleaningDialog

        if self._cleaning_dialog is None:
            self._cleaning_dialog = DataCleaningDialog(self)
        self._cleaning_dialog.refresh_points()
        # Ebenen-Tabs (Nutzerwunsch): sowohl der Tab-Klick "Bereinigung" als
        # auch diese Menüaktion ("Daten > Rohdaten säubern…") laufen HIER
        # zusammen, damit beide Einstiege denselben synchronisierten Zustand
        # ergeben (siehe layer_tabs_ops.py -- setzt u.a. auch die
        # Sichtbarkeit dieses Dialogs, daher VOR dem expliziten show() unten).
        self._set_active_layer_tab("cleaning")
        self._cleaning_dialog.show()
        self._cleaning_dialog.raise_()
        self._cleaning_dialog.activateWindow()

    # --------------------------------------------------- Punkte markieren
    def _start_cleaning_point_pick(self) -> None:
        if self.recording is None:
            return
        if self._armed_entry is not None:
            self._armed_entry.btn_place.blockSignals(True)
            self._armed_entry.btn_place.setChecked(False)
            self._armed_entry.btn_place.blockSignals(False)
            self._armed_entry = None
        if self._ruler_armed:
            self._cancel_ruler_tool()
        if self._measurement_armed:
            self._cancel_measurement_tool()
        self._cleaning_pick_armed = True
        self.statusBar().showMessage("Referenzpunkt für Rohdaten-Bereinigung: Klick ins Bild zum Setzen.")

    def _cancel_cleaning_point_pick(self) -> None:
        self._cleaning_pick_armed = False
        if self._cleaning_dialog is not None:
            btn = self._cleaning_dialog.btn_add_point
            btn.blockSignals(True)
            btn.setChecked(False)
            btn.setText("Punkt hinzufügen (Bild anklicken)")
            btn.blockSignals(False)

    def _handle_cleaning_point_click(self, event) -> None:
        if event.button() != QtCore.Qt.LeftButton:
            self._cancel_cleaning_point_pick()
            self.statusBar().showMessage("Referenzpunkt-Werkzeug abgebrochen.", 3000)
            return
        row_col = self._pixel_at_scene_pos(event.scenePos())
        if row_col is None:
            return
        row, col = row_col
        self._cleaning_points.append((col, row))
        self._cleaning_pick_armed = False
        self._draw_cleaning_point_markers()
        if self._cleaning_dialog is not None:
            self._cleaning_dialog.point_added()
            self._cleaning_dialog.refresh_points()

    def _remove_cleaning_point(self, index: int) -> None:
        if 0 <= index < len(self._cleaning_points):
            del self._cleaning_points[index]
            self._draw_cleaning_point_markers()
            if self._cleaning_dialog is not None:
                self._cleaning_dialog.refresh_points()

    def _clear_cleaning_point_markers(self) -> None:
        for entry in self._cleaning_point_items:
            for item in (entry["dot"], entry["label"], entry["area"]):
                if item is not None:
                    self.view_box.removeItem(item)
        self._cleaning_point_items = []

    def _draw_cleaning_point_markers(self) -> None:
        """Baut Punkt-Markierungen NEU auf -- jeder Punkt bekommt ein
        draggable pg.TargetItem (Punkt 6, Nutzerwunsch: Punkte direkt im
        Bild verschieben koennen, statt sie nur setzen/entfernen zu
        koennen), eine Nummern-Beschriftung und optional das gestrichelte
        Mittelungsbereich-Rechteck. self._cleaning_point_items haelt pro
        Punkt ein dict {"dot", "label", "area"} -- gleiche Reihenfolge wie
        self._cleaning_points, damit _apply_cleaning_point_focus_visuals()
        und _on_cleaning_point_dragged() den Index eindeutig zuordnen
        koennen."""
        self._clear_cleaning_point_markers()
        shape = self.recording.shape if self.recording is not None else None
        for i, (col, row) in enumerate(self._cleaning_points):
            area = None
            if shape is not None and self._cleaning_kernel_size > 1 and self._cleaning_show_kernel_area:
                # Zeigt den tatsaechlich gemittelten Bereich als gestricheltes
                # Rechteck -- bei 1x1 gaebe es nichts zusaetzlich zum Punkt-
                # Kreuz zu zeigen (siehe _cleaning_show_kernel_area).
                rows, cols = shape
                r = max(0, min(rows - 1, row))
                c = max(0, min(cols - 1, col))
                row0, row1, col0, col1 = self._cleaning_point_bounds(r, c)
                area = QtWidgets.QGraphicsRectItem(col0, row0, col1 - col0, row1 - row0)
                area.setPen(pg.mkPen("#facc15", width=1.5, style=QtCore.Qt.DashLine))
                area.setBrush(QtGui.QBrush(QtGui.QColor(250, 204, 21, 40)))
                area.setZValue(11)
                self.view_box.addItem(area)

            dot = pg.TargetItem(
                pos=(col + 0.5, row + 0.5), size=14, symbol="x", movable=True,
                pen=pg.mkPen("#facc15", width=2), brush=pg.mkBrush("#facc15"),
                hoverPen=pg.mkPen("#fff7c2", width=2), hoverBrush=pg.mkBrush("#fff7c2"),
            )
            dot.setZValue(12)
            dot.sigPositionChangeFinished.connect(partial(self._on_cleaning_point_dragged, i))
            self.view_box.addItem(dot)

            label = pg.TextItem(text=str(i + 1), color="#facc15", anchor=(0.5, 1.3))
            label.setPos(col + 0.5, row + 0.5)
            label.setZValue(12)
            self.view_box.addItem(label)

            self._cleaning_point_items.append({"dot": dot, "label": label, "area": area})

        # Neu gezeichnete Punkte starten sichtbar (pg-Default) -- muessen
        # aber sofort die aktuelle Ebenen-Tab-Sichtbarkeit respektieren,
        # statt kurzzeitig auch auf einem anderen Tab aufzutauchen.
        self._apply_layer_tab_visibility()

    def _on_cleaning_point_dragged(self, index: int, target) -> None:
        """Reagiert auf das Ziehen eines Referenzpunkt-Markers im Bild
        (Punkt 6, Nutzerwunsch). Rundet auf die naechste Pixelmitte (die
        Bereinigung arbeitet mit ganzzahligen Pixel-Indizes, siehe
        _handle_cleaning_point_click) und aktualisiert self._cleaning_points
        UND die abhaengige Vorschau (Kandidaten-Liste im Dialog)."""
        if not (0 <= index < len(self._cleaning_points)) or self.recording is None:
            return
        pos = target.pos()
        rows, cols = self.recording.shape
        col = max(0, min(cols - 1, int(np.floor(pos.x()))))
        row = max(0, min(rows - 1, int(np.floor(pos.y()))))
        self._cleaning_points[index] = (col, row)
        self._draw_cleaning_point_markers()
        if self._cleaning_dialog is not None:
            self._cleaning_dialog.refresh_points()

    def _apply_cleaning_point_focus_visuals(self, focus_index: int | None) -> None:
        """Hebt EINEN Referenzpunkt hervor (voll sichtbar), alle anderen
        treten stark zurueck -- 1:1 nach dem Muster von
        _apply_interp_focus_visuals (roi_ops.py) fuer die Verlaufs-
        Interpolation. Wird vom DataCleaningDialog aufgerufen, wenn der
        Nutzer einen Punkt in der Liste auswaehlt/abwaehlt (Punkt 6,
        Nutzerwunsch)."""
        for i, entry in enumerate(self._cleaning_point_items):
            opacity = 1.0 if focus_index is None or i == focus_index else 0.12
            for item in (entry["dot"], entry["label"], entry["area"]):
                if item is not None:
                    item.setOpacity(opacity)

    # ------------------------------------------------------------ Analyse
    def _cleaning_point_bounds(self, row: int, col: int) -> tuple[int, int, int, int]:
        """Auf das Bild geclippter row0:row1, col0:col1-Bereich um einen
        Referenzpunkt, analog zu _live_cursor_bounds (mouse_ops.py), aber mit
        einer EIGENSTAENDIGEN Kernel-Groesse (_cleaning_kernel_size) statt der
        Live-Cursor-Einstellung -- unterschiedliche Zwecke, sollen sich nicht
        gegenseitig beeinflussen. row/col muessen bereits auf die Aufnahme
        geclempt sein (siehe Aufrufer)."""
        size = self._cleaning_kernel_size
        before = size // 2
        after = size - before
        rows, cols = self.recording.shape
        row0 = max(0, row - before)
        row1 = min(rows, row + after)
        col0 = max(0, col - before)
        col1 = min(cols, col + after)
        return row0, row1, col0, col1

    def _compute_cleaning_candidates(self) -> set[int]:
        """Bild-Indizes (0-basiert), die MIT den aktuell markierten Punkten
        und dem aktuellen Schwellenwert als Ausreißer erkannt wuerden -- ein
        Bild i (i>=1, das allererste Bild hat kein "vorheriges" und wird nie
        markiert) gilt als Ausreißer, wenn (je nach _cleaning_logic) AN JEDEM
        ("and", Standard) oder AN MINDESTENS EINEM ("or") markierten Punkt
        |T[i] - T[i-1]| den Schwellenwert überschreitet. Bei einer Kernel-
        Groesse > 1 (Standard, siehe _cleaning_kernel_size) wird dafuer nicht
        nur das exakte Pixel, sondern der ueber die NxN-Flaeche gemittelte
        Wert herangezogen (robuster gegen Sensor-Rauschen an genau einem
        Pixel) -- bei Groesse 1 bleibt es beim reinen Einzelpixel-Wert."""
        if self.recording is None or not self._cleaning_points:
            return set()
        frames = self.recording.frames
        rows, cols = self.recording.shape
        series_per_point = []
        for col, row in self._cleaning_points:
            r = max(0, min(rows - 1, row))
            c = max(0, min(cols - 1, col))
            if self._cleaning_kernel_size <= 1:
                series_per_point.append(frames[:, r, c].astype(float))
            else:
                row0, row1, col0, col1 = self._cleaning_point_bounds(r, c)
                series_per_point.append(frames[:, row0:row1, col0:col1].mean(axis=(1, 2)).astype(float))
        n = self.recording.n_frames
        threshold = self._cleaning_threshold
        combine = any if self._cleaning_logic == "or" else all
        flagged = set()
        for i in range(1, n):
            if combine(abs(series[i] - series[i - 1]) > threshold for series in series_per_point):
                flagged.add(i)
        return flagged

    def _apply_cleaning_exclusions(self, exclude: set[int]) -> None:
        if self.recording is not None and self.recording.n_frames > 0 and len(exclude) >= self.recording.n_frames:
            # Mindestens EIN Bild muss sichtbar bleiben: wuerde diese Auswahl
            # ALLE Bilder ausblenden, faende der Navigations-Ausweich-Sprung
            # unten (siehe self._skip_excluded_frame_index) keine gueltige
            # Stelle mehr und wuerde den zentralen Nutzer-Zusicherung
            # verletzen, dass ein ausgeblendetes Bild NIE mehr angezeigt
            # wird -- daher unveraendert ablehnen, statt in diesen
            # inkonsistenten Zustand zu geraten.
            QtWidgets.QMessageBox.information(
                self, "Nicht möglich",
                "Mindestens ein Bild muss sichtbar bleiben -- diese Auswahl würde alle Bilder "
                "der Aufnahme ausblenden und wurde nicht übernommen.",
            )
            if self._cleaning_dialog is not None:
                self._cleaning_dialog.refresh_candidates()
            return
        self._excluded_frame_indices = set(exclude)
        self._recompute_curves()
        self._update_shrinkage_curve()
        # Steht die Anzeige gerade auf einem Bild, das JETZT (neu) ausgeblendet
        # wurde, muss sofort auf die naechste sichtbare Stelle gesprungen
        # werden -- sonst bliebe genau dieses eigentlich unerwuenschte Bild
        # trotzdem im Viewer stehen (Nutzerwunsch: ausgeblendete Bilder duerfen
        # NIRGENDS in der GUI auftauchen, nicht nur bei Navigation danach).
        if self.current_index in self._excluded_frame_indices:
            corrected = self._skip_excluded_frame_index(self.current_index, 1)
            if corrected in self._excluded_frame_indices:
                corrected = self._skip_excluded_frame_index(self.current_index, -1)
            self._show_frame(corrected)
        self._update_status_bar()
        self.statusBar().showMessage(
            f"Rohdaten-Bereinigung angewendet: {len(self._excluded_frame_indices)} Bild(er) ausgeblendet.",
            5000,
        )
