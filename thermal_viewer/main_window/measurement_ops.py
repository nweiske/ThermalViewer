"""Maßstab (Lineal) und Ad-hoc-Streckenmessungen im Thermobild."""
from __future__ import annotations

from functools import partial

import pyqtgraph as pg
from qtpy import QtCore, QtGui, QtWidgets

from ..dialogs import (
    RulerLengthDialog,
)
from ..measurement import MEASUREMENT_PREVIEW_COLOR, DraggableTextItem, MeasurementEntry, clamp_label_offset
from ..roi_entry import (
    mm_value_de,
    roi_color_for_number,
)


class _MeasurementMixin:
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
        if self._measurement_armed:
            self._cancel_measurement_tool()
        if self._cleaning_pick_armed:
            self._cancel_cleaning_point_pick()
        self._ruler_armed = True
        self._ruler_start = None
        # Eine evtl. noch von der letzten Messung angezeigte, gueltige Linie/
        # mm-Beschriftung bleibt hier bewusst sichtbar -- sie wird erst beim
        # tatsaechlichen ersten Klick (siehe _handle_ruler_click) durch die
        # neue Messung ueberschrieben. So geht die Anzeige des noch aktiven
        # Massstabs nicht schon durch blosses Oeffnen des Werkzeugs verloren.
        self.statusBar().showMessage("Maßstab: Startpunkt der Referenzlinie im Bild anklicken.")

    def _cancel_ruler_tool(self) -> None:
        if self._ruler_start is not None:
            # Es wurde bereits ein neuer Startpunkt gesetzt (der die Daten
            # einer evtl. zuvor gueltigen Linie schon überschrieben hat) --
            # dieser unvollstaendige Rest ergibt ausgeblendet mehr Sinn.
            self._hide_ruler_visuals()
        self._ruler_armed = False
        self._ruler_start = None

    def _hide_ruler_visuals(self) -> None:
        if self._ruler_preview_marker is not None:
            self._ruler_preview_marker.setVisible(False)
        if self._ruler_line is not None:
            self._ruler_line.setVisible(False)
        if self._ruler_text is not None:
            self._ruler_text.setVisible(False)

    def _update_ruler_color_swatch(self) -> None:
        self.btn_ruler_color.setStyleSheet(
            f"background-color:{self._ruler_color}; border:1px solid #333; border-radius:4px;"
        )

    def _apply_ruler_color(self) -> None:
        if self._ruler_line is not None:
            self._ruler_line.setPen(pg.mkPen(self._ruler_color, width=3))
        if self._ruler_text is not None:
            self._ruler_text.setColor(self._ruler_color)

    def _on_ruler_color_clicked(self) -> None:
        color = QtWidgets.QColorDialog.getColor(QtGui.QColor(self._ruler_color), self, "Farbe der Maßstablinie")
        if not color.isValid():
            return
        self._ruler_color = color.name()
        self._update_ruler_color_swatch()
        self._apply_ruler_color()

    def _clear_ruler_scale(self) -> None:
        self._px_to_mm = None
        self._ruler_mm_value = None
        self._ruler_label_offset = None
        self._hide_ruler_visuals()
        self._refresh_scale_label()
        for entry in self.roi_entries:
            self._update_roi_mm_label(entry)

    def _refresh_scale_label(self) -> None:
        has_scale = self._px_to_mm is not None
        if has_scale:
            self.scale_label.setText(f"1 px ≈ {self._format_de(self._px_to_mm, 4)} mm")
        else:
            self.scale_label.setText("Kein Maßstab definiert.")
        self.btn_scale_clear.setEnabled(has_scale)
        can_measure = has_scale and self.recording is not None
        self.btn_add_measurement.setEnabled(can_measure)
        if not has_scale:
            # Ohne Maßstab ergibt eine laufende/angezeigte Messung keinen Sinn
            # mehr (siehe _handle_measurement_click, das ebenfalls von
            # _px_to_mm abhaengt).
            self._cancel_measurement_tool()
            self._hide_measurement_visuals()

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
            # Nur eine einfache, nicht interaktive Vorschau waehrend der
            # Klick-Klick-Erstellung -- die fertige, ziehbare Linie
            # (self._ruler_line) entsteht erst unten nach Bestaetigung der
            # Laenge.
            if self._ruler_preview_marker is None:
                self._ruler_preview_marker = pg.PlotDataItem(
                    pen=pg.mkPen(self._ruler_color, width=3),
                    symbol="o",
                    symbolSize=8,
                    symbolBrush=self._ruler_color,
                    symbolPen="#ffffff",
                )
                self._ruler_preview_marker.setZValue(11)
                self.view_box.addItem(self._ruler_preview_marker)
            if self._ruler_text is None:
                self._ruler_text = DraggableTextItem(
                    color=self._ruler_color, anchor=(0.5, 0), fill=(0, 0, 0, 160),
                    on_moved=self._on_ruler_label_moved, on_double_clicked=self._edit_ruler_length,
                    clamp_fn=self._clamp_ruler_label_pos,
                )
                self._ruler_text.setZValue(11)
                self.view_box.addItem(self._ruler_text)
            if self._ruler_line is not None:
                self._ruler_line.setVisible(False)
            self._ruler_text.setVisible(False)
            self._ruler_preview_marker.setData([point[0]], [point[1]])
            self._ruler_preview_marker.setVisible(True)
            self.statusBar().showMessage("Maßstab: jetzt den Endpunkt der Referenzlinie anklicken.")
            return

        start = self._ruler_start
        end = point
        self._ruler_preview_marker.setData([start[0], end[0]], [start[1], end[1]])
        pixel_distance = ((end[0] - start[0]) ** 2 + (end[1] - start[1]) ** 2) ** 0.5
        self._ruler_armed = False
        self._ruler_start = None
        # Linie bleibt sichtbar (auch waehrend des folgenden, blockierenden
        # Eingabedialogs), damit der Nutzer tatsaechlich sieht, welche Strecke
        # er gerade in mm beziffert -- vorher wurde sie hier bereits wieder
        # ausgeblendet, sodass nie eine sichtbare Linie zu sehen war.
        if pixel_distance < 1e-6:
            self._hide_ruler_visuals()
            QtWidgets.QMessageBox.information(
                self, "Maßstab", "Start- und Endpunkt liegen zu nah beieinander, bitte erneut versuchen."
            )
            return

        length_dialog = RulerLengthDialog(self, current_mm=self._ruler_mm_value or 10.0)
        if length_dialog.exec() != QtWidgets.QDialog.DialogCode.Accepted:
            self._hide_ruler_visuals()
            return
        mm_value = length_dialog.mm_value()

        self._ruler_preview_marker.setVisible(False)
        self._ruler_label_offset = None  # neue Linie -> Beschriftung startet wieder am Mittelpunkt
        self._create_or_move_ruler_line(start, end)
        self._ruler_line.setVisible(self._scale_visuals_visible)
        self._px_to_mm = mm_value / pixel_distance
        self._ruler_mm_value = mm_value
        self._update_ruler_text_position()
        self._refresh_scale_label()
        for entry in self.roi_entries:
            self._update_roi_mm_label(entry)
        self.statusBar().showMessage(
            f"Maßstab gesetzt: 1 px ≈ {self._format_de(self._px_to_mm, 4)} mm", 5000
        )

    def _create_or_move_ruler_line(self, start: tuple[float, float], end: tuple[float, float]) -> None:
        """Erzeugt die fertige, ziehbare Maßstab-Linie (Punkt 11) oder setzt
        eine bereits bestehende neu -- ein LineSegmentROI statt der zuvor
        starren PlotDataItem, damit sich beide Endpunkte per Maus nachträglich
        verschieben lassen, ohne den Maßstab komplett neu zeichnen zu müssen."""
        if self._ruler_line is not None:
            self.view_box.removeItem(self._ruler_line)
        self._ruler_line = pg.LineSegmentROI(
            positions=[list(start), list(end)], pen=pg.mkPen(self._ruler_color, width=3)
        )
        self._ruler_line.setZValue(11)
        self.view_box.addItem(self._ruler_line)
        self._ruler_line.sigRegionChangeFinished.connect(self._on_ruler_line_dragged)

    def _on_ruler_line_dragged(self) -> None:
        """Nach dem Ziehen eines Endpunkts (Punkt 11): die reale Länge
        (self._ruler_mm_value) bleibt fest, px-zu-mm wird aus der neuen
        Pixel-Distanz neu berechnet -- entspricht einer Nachkalibrierung ohne
        den Maßstab neu setzen zu müssen."""
        if self._ruler_line is None or self._ruler_mm_value is None:
            return
        p1, p2 = self._ruler_line.listPoints()
        pixel_distance = (p2 - p1).length()
        if pixel_distance < 1e-6:
            # Degenerierter Zustand (beide Punkte uebereinander) -- bisherige
            # Kalibrierung beibehalten, statt durch Null zu teilen.
            return
        self._px_to_mm = self._ruler_mm_value / pixel_distance
        self._update_ruler_text_position()
        self._refresh_scale_label()
        for entry in self.roi_entries:
            self._update_roi_mm_label(entry)

    def _update_ruler_text_position(self) -> None:
        if self._ruler_line is None or self._ruler_text is None or self._ruler_mm_value is None:
            return
        p1, p2 = self._ruler_line.listPoints()
        mid = (p1 + p2) / 2
        pos = mid if self._ruler_label_offset is None else mid + self._ruler_label_offset
        self._ruler_text.setText(f"{self._format_de(self._ruler_mm_value, 1)} mm")
        self._ruler_text.setPos(pos.x(), pos.y())
        self._ruler_text.setVisible(self._scale_visuals_visible)

    def _clamp_ruler_label_pos(self, raw_pos: QtCore.QPointF) -> QtCore.QPointF:
        """clamp_fn fuer self._ruler_text (siehe DraggableTextItem.
        mouseMoveEvent) -- haelt die Maßstab-Beschriftung schon WAEHREND des
        Ziehens in der naeheren Umgebung der Maßstab-Linie, statt sie erst
        nach dem Loslassen zurueckspringen zu lassen (Bugreport: "kann die
        noch quer durchs Bild ziehen")."""
        if self._ruler_line is None:
            return raw_pos
        p1, p2 = self._ruler_line.listPoints()
        mid = (p1 + p2) / 2
        offset = clamp_label_offset(raw_pos - mid, (p2 - p1).length())
        return QtCore.QPointF(mid.x() + offset.x(), mid.y() + offset.y())

    def _on_ruler_label_moved(self) -> None:
        """Nutzer hat die Maßstab-Beschriftung manuell verschoben (Punkt 9,
        gilt fuer Maßstab UND Messungen gleichermassen) -- Versatz zum
        Linien-Mittelpunkt einfrieren (bereits waehrend des Ziehens durch
        _clamp_ruler_label_pos auf die naehere Umgebung begrenzt), siehe
        _on_measurement_label_moved fuer das Gegenstueck bei Messungen."""
        if self._ruler_line is None or self._ruler_text is None:
            return
        p1, p2 = self._ruler_line.listPoints()
        mid = (p1 + p2) / 2
        offset = clamp_label_offset(self._ruler_text.pos() - mid, (p2 - p1).length())
        self._ruler_label_offset = offset
        self._ruler_text.setPos(mid.x() + offset.x(), mid.y() + offset.y())

    def _ruler_hit_test(self, scene_pos: QtCore.QPointF) -> bool:
        """Prueft, ob scene_pos auf der Maßstab-Linie liegt -- fuer die
        Doppelklick-Bearbeitung (Punkt 11) ueber den generischen Szene-Klick
        (_on_scene_mouse_clicked). Die Beschriftung selbst braucht das NICHT
        (mehr): DraggableTextItem.mouseDoubleClickEvent() faengt einen
        Doppelklick auf die Beschriftung bereits direkt am Item ab und ruft
        _edit_ruler_length() unmittelbar auf (siehe on_double_clicked in
        _handle_ruler_click) -- ueber diese Methode zusaetzlich geprueft,
        wuerde derselbe Doppelklick den Dialog zweimal hintereinander oeffnen."""
        if self._ruler_line is not None and self._ruler_line.isVisible():
            shape = self._ruler_line.mapToScene(self._ruler_line.shape())
            if shape.contains(scene_pos):
                return True
        return False

    def _edit_ruler_length(self) -> None:
        """Doppelklick auf die Maßstab-Linie/-Beschriftung (Punkt 11): erlaubt,
        die reale Länge (mm) direkt zu ändern, OHNE die aktuellen Endpunkte
        anzutasten -- Gegenstück zum Ziehen der Endpunkte (dort bleibt die
        Länge fest, hier bleiben die Endpunkte fest)."""
        if self._ruler_line is None or self._ruler_mm_value is None:
            return
        length_dialog = RulerLengthDialog(self, current_mm=self._ruler_mm_value)
        if length_dialog.exec() != QtWidgets.QDialog.DialogCode.Accepted:
            return
        p1, p2 = self._ruler_line.listPoints()
        pixel_distance = (p2 - p1).length()
        if pixel_distance < 1e-6:
            return
        self._ruler_mm_value = length_dialog.mm_value()
        self._px_to_mm = self._ruler_mm_value / pixel_distance
        self._update_ruler_text_position()
        self._refresh_scale_label()
        for entry in self.roi_entries:
            self._update_roi_mm_label(entry)
        self.statusBar().showMessage(
            f"Maßstab aktualisiert: 1 px ≈ {self._format_de(self._px_to_mm, 4)} mm", 5000
        )

    # ------------------------------------------------------- Messungen (nutzen Maßstab)
    def _start_measurement_tool(self) -> None:
        if self.recording is None:
            QtWidgets.QMessageBox.information(self, "Keine Daten", "Bitte zuerst eine Messreihe laden.")
            return
        if self._px_to_mm is None:
            QtWidgets.QMessageBox.information(
                self, "Kein Maßstab", "Bitte zuerst über \"Festlegen…\" einen Maßstab definieren."
            )
            return
        if self._armed_entry is not None:
            self._armed_entry.btn_place.blockSignals(True)
            self._armed_entry.btn_place.setChecked(False)
            self._armed_entry.btn_place.blockSignals(False)
            self._armed_entry = None
        if self._ruler_armed:
            self._cancel_ruler_tool()
        if self._cleaning_pick_armed:
            self._cancel_cleaning_point_pick()
        self._measurement_armed = True
        self._measurement_start = None
        self.statusBar().showMessage("Neue Messung: Startpunkt der Strecke im Bild anklicken.")

    def _cancel_measurement_tool(self) -> None:
        """Bricht nur eine GERADE laufende Zwei-Klick-Erfassung ab -- bereits
        fertig platzierte Messungen (self.measurements) bleiben unberuehrt,
        siehe _hide_measurement_visuals() dafuer."""
        if self._measurement_preview_marker is not None:
            self._measurement_preview_marker.setVisible(False)
        self._measurement_armed = False
        self._measurement_start = None

    def _hide_measurement_visuals(self) -> None:
        """Blendet ALLE platzierten Messungen aus (z.B. beim Laden einer neuen
        Aufnahme, deren Pixel-Koordinaten nichts mehr mit den alten zu tun
        haben) -- Eintraege/Werte in der Liste bleiben erhalten, dieselbe
        Konvention wie bei _hide_ruler_visuals fuer den Maßstab."""
        if self._measurement_preview_marker is not None:
            self._measurement_preview_marker.setVisible(False)
        for entry in self.measurements:
            entry.line.setVisible(False)
            entry.text.setVisible(False)

    def _handle_measurement_click(self, event) -> None:
        if event.button() != QtCore.Qt.LeftButton:
            self._cancel_measurement_tool()
            self.statusBar().showMessage("Mess-Werkzeug abgebrochen.", 3000)
            return
        scene_pos = event.scenePos()
        if not self.view_box.sceneBoundingRect().contains(scene_pos):
            return
        view_pos = self.view_box.mapSceneToView(scene_pos)
        point = (view_pos.x(), view_pos.y())

        if self._measurement_start is None:
            self._measurement_start = point
            if self._measurement_preview_marker is None:
                self._measurement_preview_marker = pg.PlotDataItem(
                    pen=pg.mkPen(MEASUREMENT_PREVIEW_COLOR, width=3),
                    symbol="o",
                    symbolSize=8,
                    symbolBrush=MEASUREMENT_PREVIEW_COLOR,
                    symbolPen="#ffffff",
                )
                self._measurement_preview_marker.setZValue(11)
                self.view_box.addItem(self._measurement_preview_marker)
            self._measurement_preview_marker.setData([point[0]], [point[1]])
            self._measurement_preview_marker.setVisible(True)
            self.statusBar().showMessage("Neue Messung: jetzt den Endpunkt der Strecke anklicken.")
            return

        start = self._measurement_start
        end = point
        self._measurement_armed = False
        self._measurement_start = None
        self._measurement_preview_marker.setVisible(False)
        pixel_distance = ((end[0] - start[0]) ** 2 + (end[1] - start[1]) ** 2) ** 0.5
        if pixel_distance < 1e-6 or self._px_to_mm is None:
            return

        entry = self._create_measurement(start, end, pixel_distance)
        self.statusBar().showMessage(
            f"{entry.name}: {self._format_de(entry.mm_value, 2)} mm ({self._format_de(pixel_distance, 1)} px) "
            "-- Maßstab dabei unverändert.",
            6000,
        )

    def _create_measurement(
        self, start: tuple[float, float], end: tuple[float, float], pixel_distance: float
    ) -> "MeasurementEntry":
        """Erstellt eine neue MeasurementEntry aus einer per Zwei-Klick
        erfassten Strecke, haengt Bild-Objekte (Linie/Beschriftung) UND die
        zugehoerige Zeile im rechten Panel an (Punkt 8: "beliebig viele
        Messungen gleichzeitig")."""
        number = self._measurement_next_number
        self._measurement_next_number += 1
        color = roi_color_for_number(number)
        entry = MeasurementEntry(number, color, self.view_box, start, end, self._on_measurement_label_moved)
        entry.line.sigRegionChangeFinished.connect(partial(self._on_measurement_line_dragged, entry))
        entry.line.setVisible(self._scale_visuals_visible)
        entry.mm_value = pixel_distance * self._px_to_mm
        entry.update_text_position()
        self.measurements.append(entry)
        self._add_measurement_row(entry)
        return entry

    def _add_measurement_row(self, entry: "MeasurementEntry") -> None:
        """Baut die Panel-Zeile einer Messung (Farb-Swatch, editierbarer Name,
        live mm-Wert, Entfernen-Knopf, Punkt 7: Werte direkt in der UI sichtbar
        statt nur im Bild) und haengt sie unten an measurements_container an."""
        row = QtWidgets.QWidget()
        row_layout = QtWidgets.QHBoxLayout(row)
        row_layout.setContentsMargins(0, 0, 0, 0)

        color_button = QtWidgets.QPushButton()
        color_button.setFixedSize(18, 18)
        color_button.setCursor(QtCore.Qt.PointingHandCursor)
        color_button.setToolTip("Farbe dieser Messung ändern.")
        color_button.clicked.connect(partial(self._on_measurement_color_clicked, entry))
        row_layout.addWidget(color_button)
        entry.color_button = color_button
        entry.set_color(entry.color)

        name_edit = QtWidgets.QLineEdit(entry.name)
        name_edit.setToolTip("Name dieser Messung -- erscheint auch als Beschriftung im Bild.")
        name_edit.textChanged.connect(partial(self._on_measurement_name_changed, entry))
        row_layout.addWidget(name_edit, 1)
        entry.name_edit = name_edit

        value_label = QtWidgets.QLabel(f"{mm_value_de(entry.mm_value)} mm")
        value_label.setMinimumWidth(70)
        row_layout.addWidget(value_label)
        entry.value_label = value_label

        remove_button = QtWidgets.QPushButton("✕")
        remove_button.setFixedSize(22, 22)
        remove_button.setToolTip("Diese Messung entfernen.")
        remove_button.clicked.connect(partial(self._remove_measurement, entry))
        row_layout.addWidget(remove_button)

        entry.row_widget = row
        self.measurements_container.addWidget(row)

    def _on_measurement_name_changed(self, entry: "MeasurementEntry", text: str) -> None:
        entry.name = text.strip() or f"Messung {entry.number}"
        entry.update_text_position()

    def _on_measurement_color_clicked(self, entry: "MeasurementEntry") -> None:
        color = QtWidgets.QColorDialog.getColor(QtGui.QColor(entry.color), self, "Farbe der Messung")
        if not color.isValid():
            return
        entry.set_color(color.name())

    def _remove_measurement(self, entry: "MeasurementEntry") -> None:
        entry.remove_from_view_box(self.view_box)
        if entry.row_widget is not None:
            self.measurements_container.removeWidget(entry.row_widget)
            entry.row_widget.setParent(None)
            entry.row_widget.deleteLater()
        self.measurements.remove(entry)

    def _on_measurement_line_dragged(self, entry: "MeasurementEntry") -> None:
        """Endpunkte einer Mess-Strecke nachtraeglich verschoben: mm-Anzeige
        mit dem AKTUELLEN Maßstab neu berechnen (im Unterschied zur
        Lineal-Linie wird hier nie in _px_to_mm zurückgerechnet -- Messen ist
        rein lesend)."""
        if self._px_to_mm is None:
            return
        p1, p2 = entry.endpoints()
        pixel_distance = (p2 - p1).length()
        entry.mm_value = pixel_distance * self._px_to_mm
        entry.update_text_position()

    def _on_measurement_label_moved(self, entry: "MeasurementEntry") -> None:
        """Nutzer hat die Beschriftung einer Messung manuell verschoben (Punkt
        9) -- Versatz zum Linien-Mittelpunkt einfrieren (bereits waehrend des
        Ziehens durch MeasurementEntry._clamp_label_pos auf die naehere
        Umgebung begrenzt), damit sie bei spaeteren Aktualisierungen (Linie
        gezogen, Wert geaendert) dort bleibt statt zurueckzuspringen."""
        p1, p2 = entry.endpoints()
        mid = (p1 + p2) / 2
        offset = clamp_label_offset(entry.text.pos() - mid, (p2 - p1).length())
        entry.label_offset = offset
        entry.text.setPos(mid.x() + offset.x(), mid.y() + offset.y())

    def _on_toggle_scale_visuals(self, checked: bool) -> None:
        """Checkbox "Anzeigen" (Punkt 5, Maßstab && Messungen): blendet
        Maßstab-Linie UND alle Messungen gemeinsam im Thermobild aus/ein --
        Panel-Zeilen/Werte bleiben unberührt, es werden nur die Bild-Objekte
        (Linie + Beschriftung) versteckt. Wirkt bewusst nur auf AKTUELL
        gültige Geometrie: eine bereits durch _set_recording (neue Aufnahme
        geladen, siehe dortiger Kommentar) ausgeblendete Alt-Linie/-Messung
        bleibt unabhängig von dieser Checkbox versteckt, statt mit veralteten
        Bild-Koordinaten der vorherigen Aufnahme wieder aufzutauchen."""
        self._scale_visuals_visible = checked
        if self._ruler_line is not None and self._ruler_mm_value is not None:
            self._ruler_line.setVisible(checked)
            self._update_ruler_text_position()
        for entry in self.measurements:
            entry.line.setVisible(checked)
            entry.update_text_position()

