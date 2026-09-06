"""Messbereich (ROI)-Interaktion: Platzieren, Farbe, Interpolation, Kurvenberechnung."""
from __future__ import annotations


import numpy as np
from qtpy import QtGui, QtWidgets

from ..roi import bounds_px_for
from ..roi_entry import (
    RoiEntry,
)
from .constants import (
    INTERP_END_CAPTURE_LABEL,
    INTERP_END_LABEL,
    INTERP_START_CAPTURE_LABEL,
    INTERP_START_LABEL,
)


class _RoiMixin:
    def _on_add_roi_clicked(self) -> None:
        self._add_roi_entry()

    def _on_roi_remove_clicked(self, entry: RoiEntry) -> None:
        answer = QtWidgets.QMessageBox.question(
            self,
            "Messbereich entfernen",
            f"„{entry.name}“ inkl. Zeitverlauf-Kurve endgültig entfernen?\nDies kann nicht "
            "rückgängig gemacht werden.",
            QtWidgets.QMessageBox.StandardButton.Yes | QtWidgets.QMessageBox.StandardButton.No,
            QtWidgets.QMessageBox.StandardButton.No,
        )
        if answer != QtWidgets.QMessageBox.StandardButton.Yes:
            return

        if self._armed_entry is entry:
            self._armed_entry = None
        entry.remove_from_view_box(self.view_box)
        legend = self.timeseries_plot.getPlotItem().legend
        if legend is not None:
            legend.removeItem(entry.curve)
        self.timeseries_plot.removeItem(entry.curve)

        self.roi_stack.removeWidget(entry.tab_widget)
        entry.tab_widget.deleteLater()
        if entry.list_item is not None:
            list_row = self.roi_list.row(entry.list_item)
            if list_row >= 0:
                self.roi_list.takeItem(list_row)
            entry.list_item = None

        self.roi_entries.remove(entry)
        self._apply_interp_focus_visuals()
        self.statusBar().showMessage(f"„{entry.name}“ entfernt.", 4000)

    def _on_roi_color_clicked(self, entry: RoiEntry) -> None:
        color = QtWidgets.QColorDialog.getColor(
            QtGui.QColor(entry.color), self, f"Farbe für {entry.name}"
        )
        if not color.isValid():
            return
        entry.set_color(color.name())

    def _on_roi_place_toggled(self, entry: RoiEntry, checked: bool) -> None:
        if checked:
            for other in self.roi_entries:
                if other is not entry and other.btn_place.isChecked():
                    other.btn_place.blockSignals(True)
                    other.btn_place.setChecked(False)
                    other.btn_place.blockSignals(False)
                    # blockSignals oben unterdrueckt others eigenen
                    # _on_roi_place_toggled-Aufruf (der sonst _armed_entry
                    # aufraeumen wuerde) -- ohne dieses explizite Zuruecksetzen
                    # bliebe ein gerade laufender Start-/Ende-Erfassungsvorgang
                    # (siehe _on_roi_interp_capture) an "other" haengen: dessen
                    # Knopf zeigt weiter "...uebernehmen", ein spaeterer Klick
                    # wuerde dann die Geometrie vom FALSCHEN (aktuellen) Frame
                    # als Keyframe uebernehmen.
                    if other.interp_arm_start or other.interp_arm_end:
                        self._reset_interp_arm_state(other)
            self._apply_interp_focus_visuals()
            if self._ruler_armed:
                # Ruler- und ROI-Platzieren-Modus schliessen sich aus, sonst
                # wuerde ein Bildklick unbemerkt vom jeweils anderen Modus
                # "geschluckt" (siehe _on_scene_mouse_clicked).
                self._cancel_ruler_tool()
            if self._measurement_armed:
                self._cancel_measurement_tool()
            self._armed_entry = entry
            self.statusBar().showMessage(f"{entry.name}: Klick ins Bild zum Platzieren.")
        elif self._armed_entry is entry:
            self._armed_entry = None

    def _on_roi_apply_clicked(self, entry: RoiEntry, *_args) -> None:
        # *_args faengt den von spin.valueChanged(float) mitgesendeten neuen
        # Wert ab -- diese Methode braucht ihn nicht, da sie ohnehin alle
        # vier Felder direkt aus den Spinboxen liest (siehe unten).
        if self.recording is None:
            # Nicht-blockierender Statuszeilen-Hinweis statt eines
            # QMessageBox: diese Methode feuert live bei JEDER Aenderung
            # (auch einzelnen Tastendruecken/Pfeiltasten) der vier Spinboxen
            # -- ein modaler Dialog wuerde dabei bei jedem Versuch erneut
            # aufpoppen und die Eingabe unterbrechen.
            self.statusBar().showMessage("Bitte zuerst eine Messreihe laden.", 4000)
            return
        entry.place(entry.spin_x.value(), entry.spin_y.value(), entry.spin_width.value(), entry.spin_height.value())
        self._sync_roi_spinboxes(entry)
        self._recompute_curves(entries=[entry])

    def _on_roi_square_reset_clicked(self, entry: RoiEntry) -> None:
        if not entry.placed:
            return
        side = entry.width()
        cx, cy = entry.center()
        entry.place(cx, cy, side, side)
        self._sync_roi_spinboxes(entry)
        self._recompute_curves(entries=[entry])

    @staticmethod
    def _reset_interp_arm_state(entry: RoiEntry) -> None:
        """Bricht einen evtl. laufenden zweistufigen Erfassungs-Vorgang
        (Start-/Ende-Button stand gerade auf "Position uebernehmen") ab.
        Muss unbedingt aufgerufen werden, wenn sich interp_start/interp_end
        ausserhalb dieses Ablaufs aendern (z.B. Projekt laden) -- sonst wuerde
        ein spaeterer Klick auf den haengengebliebenen Button den frisch
        gesetzten Wert sofort wieder ueberschreiben."""
        entry.interp_arm_start = False
        entry.interp_arm_end = False
        entry.btn_interp_start.setText(INTERP_START_LABEL)
        entry.btn_interp_end.setText(INTERP_END_LABEL)

    def _apply_interp_focus_visuals(self) -> None:
        """Waehrend eine Verlaufs-Interpolation gerade per Start-/Ende-Knopf
        erfasst wird, ruecken alle ANDEREN Messbereiche visuell in den
        Hintergrund (stark verblasst), damit der gerade bearbeitete im Bild
        eindeutig im Fokus bleibt. Dessen eigene Deckkraft ist waehrend der
        Start-Erfassung voll, waehrend der (spaeteren) Ende-Erfassung leicht
        reduziert -- als Hinweis, dass die angezeigte Geometrie noch vom
        Start stammt, bis sie neu positioniert wird. Ohne laufende Erfassung
        (keine ROI aktuell armiert) sind alle wieder voll sichtbar."""
        focus_entry = next(
            (e for e in self.roi_entries if e.interp_arm_start or e.interp_arm_end), None
        )
        for entry in self.roi_entries:
            if focus_entry is None:
                opacity = 1.0
            elif entry is focus_entry:
                opacity = 1.0 if entry.interp_arm_start else 0.55
            else:
                opacity = 0.12
            entry.roi.setOpacity(opacity)
            entry.label.setOpacity(opacity)

    def _on_roi_show_temperature_toggled(self, entry: RoiEntry, checked: bool) -> None:
        entry.show_temperature = checked
        entry._refresh_label_text()

    def _on_roi_circular_toggled(self, entry: RoiEntry, checked: bool) -> None:
        entry.roi.is_circular = checked
        entry.roi.update()  # erzwingt Neuzeichnen mit dem geaenderten Umriss
        if self.recording is not None and entry.placed:
            self._recompute_curves(entries=[entry])
            self._update_roi_temperature_labels(self.current_index)

    def _on_roi_interp_toggled(self, entry: RoiEntry, checked: bool) -> None:
        entry.interp_enabled = checked
        entry.btn_interp_start.setEnabled(checked)
        entry.btn_interp_end.setEnabled(checked)
        self._reset_interp_arm_state(entry)
        self._apply_interp_focus_visuals()
        # Der Hinweis (Punkt 2) ist nur relevant, WAEHREND die Interpolation
        # tatsaechlich aktiv ist -- bei ausgeschalteter Interpolation dienen
        # die Spinboxen lediglich als Sprungziel fuer "Start/Ende festlegen".
        self._refresh_interp_range_warning(entry, True)
        self._refresh_interp_range_warning(entry, False)
        # Beim Deaktivieren bleibt der Messbereich einfach an seiner
        # aktuellen (zuletzt interpolierten) Geometrie stehen -- die
        # Start-/Ende-Keyframes bleiben erhalten, falls die Interpolation
        # spaeter wieder aktiviert wird.
        self._recompute_curves(entries=[entry])

    def _refresh_interp_range_warning(self, entry: RoiEntry, is_start: bool, _value: int = 0) -> None:
        """Blendet einen dezenten, nicht-blockierenden Hinweis neben
        „Erstes/Letztes Frame:“ ein, wenn das dort eingetragene Frame
        AUSSERHALB der aktuellen Auswertung ([_eval_start_index,
        _eval_end_index]) liegt (Punkt 2, Nutzerwunsch) -- die Interpolation
        bleibt dabei uneingeschraenkt moeglich, der Hinweis macht nur
        darauf aufmerksam, dass der Teil ausserhalb der Auswertung sich
        dort nicht auswirkt. _value nimmt das (ungenutzte) valueChanged-
        Signalargument entgegen, damit diese Methode direkt als Slot
        verbunden werden kann."""
        spin = entry.spin_interp_start_frame if is_start else entry.spin_interp_end_frame
        label = entry.lbl_interp_start_warning if is_start else entry.lbl_interp_end_warning
        if spin is None or label is None:
            return
        outside = (
            bool(entry.interp_enabled)
            and self._eval_start_index is not None
            and self._eval_end_index is not None
            and not (self._eval_start_index <= spin.value() - 1 <= self._eval_end_index)
        )
        label.setVisible(outside)
        if outside:
            label.setToolTip(
                f"Dieses Bild liegt außerhalb der aktuellen Auswertung (Bild "
                f"{self._eval_start_index + 1}–{self._eval_end_index + 1}) -- die Interpolation "
                "bleibt trotzdem möglich, wirkt sich innerhalb der Auswertung aber nur bis zu "
                "deren Rand aus."
            )

    def _refresh_all_interp_range_warnings(self) -> None:
        """Aktualisiert den Hinweis (siehe _refresh_interp_range_warning) fuer
        ALLE Messbereiche auf einmal -- gebraucht, wenn sich die Auswertung
        selbst aendert (_on_eval_start_changed/_on_eval_end_changed) oder
        eine neue Aufnahme geladen wird (_set_recording)."""
        for entry in self.roi_entries:
            self._refresh_interp_range_warning(entry, True)
            self._refresh_interp_range_warning(entry, False)

    def _on_roi_interp_capture(self, entry: RoiEntry, is_start: bool) -> None:
        if self.recording is None:
            return
        button = entry.btn_interp_start if is_start else entry.btn_interp_end
        label = "Start" if is_start else "Ende"
        armed = entry.interp_arm_start if is_start else entry.interp_arm_end

        if not armed:
            # Phase 1: erst zum passenden Bild springen UND "Messbereich
            # setzen" aktivieren, damit der Nutzer den Messbereich dort per
            # Klick ins Bild direkt positionieren/erstellen kann -- auch
            # wenn er (z.B. bei einem frisch angelegten ROI) noch gar nicht
            # platziert ist, statt dass ein Klick auf diesen Knopf bis dahin
            # wirkungslos bleibt.
            capture_label = INTERP_START_CAPTURE_LABEL if is_start else INTERP_END_CAPTURE_LABEL
            # Ziel-Frame kommt aus der jeweiligen Spinbox (1-basiert, Standard
            # erstes/letztes Bild -- siehe _set_recording), NICHT mehr fest
            # aus dem globalen Auswertungsstart/-ende (Nutzerwunsch: Start-/
            # Ende-Frame der Interpolation pro Messbereich haendisch setzen).
            if is_start:
                target_frame = entry.spin_interp_start_frame.value() - 1
                self._step_frame(target_frame - self.current_index)
                entry.interp_arm_start = True
            else:
                target_frame = entry.spin_interp_end_frame.value() - 1
                self._step_frame(target_frame - self.current_index)
                entry.interp_arm_end = True
            button.setText(capture_label)
            entry.btn_place.setChecked(True)
            self._apply_interp_focus_visuals()
            self.statusBar().showMessage(
                f"{entry.name}: Messbereich für {label} im Bild anklicken/positionieren, dann "
                f"erneut auf „{capture_label}“ klicken.",
                6000,
            )
            return

        if not entry.placed:
            QtWidgets.QMessageBox.information(
                self,
                "Kein Messbereich gesetzt",
                f"Bitte zuerst den Messbereich für {label} im Bild anklicken/positionieren.",
            )
            return

        # Phase 2: aktuelle Geometrie als Keyframe uebernehmen.
        if is_start:
            entry.interp_arm_start = False
            entry.capture_interp_start(self.current_index)
        else:
            entry.interp_arm_end = False
            entry.capture_interp_end(self.current_index)
        button.setText(INTERP_START_LABEL if is_start else INTERP_END_LABEL)
        self._apply_interp_focus_visuals()
        self.statusBar().showMessage(f"{entry.name}: {label}-Position übernommen.", 4000)
        if entry.interp_start is not None and entry.interp_end is not None:
            if entry.interp_start_frame >= entry.interp_end_frame:
                # _interp_fraction() faengt start_idx >= end_idx defensiv mit
                # frac=0.0 ab (kein Absturz/keine Exception) -- das ROI bliebe
                # dabei aber unbemerkt fuer die gesamte Aufnahme auf der
                # Start-Position eingefroren. Die freien Start-/Ende-Spinboxen
                # (Nutzerwunsch: frei waehlbares Ziel-Bild statt zwingend
                # erstes/letztes Bild) erlauben diese Vertauschung leicht --
                # deshalb hier explizit warnen statt still falsch zu rechnen.
                QtWidgets.QMessageBox.warning(
                    self, "Ungültiger Bereich",
                    f"{entry.name}: Das Start-Bild (Nr. {entry.interp_start_frame + 1}) muss vor dem "
                    f"Ende-Bild (Nr. {entry.interp_end_frame + 1}) liegen -- sonst bleibt der "
                    "Messbereich während der gesamten Aufnahme auf der Start-Position eingefroren. "
                    "Bitte Start-/Ende-Bildnummer korrigieren.",
                )
            self._recompute_curves(entries=[entry])

    def _on_roi_region_changed(self, entry: RoiEntry, *_args) -> None:
        # Feuert laufend waehrend des Ziehens (nicht erst beim Loslassen wie
        # sigRegionChangeFinished) -- Kurve und Bild-Beschriftung sollen dabei
        # live mitlaufen statt erst nach dem Loslassen zu aktualisieren.
        self._sync_roi_spinboxes(entry)
        entry.sync_label_pos()
        if self.recording is not None and entry.placed:
            self._recompute_curves(entries=[entry])
            self._update_roi_temperature_labels(self.current_index, entries=[entry])

    def _on_roi_region_finished(self, entry: RoiEntry, *_args) -> None:
        if not entry.placed:
            return
        self._recompute_curves(entries=[entry])

    def _sync_roi_spinboxes(self, entry: RoiEntry) -> None:
        cx, cy = entry.center()
        for spin, value in (
            (entry.spin_x, cx),
            (entry.spin_y, cy),
            (entry.spin_width, entry.width()),
            (entry.spin_height, entry.height()),
        ):
            self._set_widget_value(spin, value)
        self._update_roi_mm_label(entry)

    def _update_roi_mm_label(self, entry: RoiEntry) -> None:
        if entry.mm_label is None:
            return
        if self._px_to_mm is None or not entry.placed:
            entry.mm_label.setVisible(False)
            return
        w_mm = entry.width() * self._px_to_mm
        h_mm = entry.height() * self._px_to_mm
        entry.mm_label.setText(f"≈ {self._format_de(w_mm)} × {self._format_de(h_mm)} mm")
        entry.mm_label.setVisible(True)

    def _recompute_curves(self, entries: list[RoiEntry] | None = None) -> None:
        if self.recording is None:
            return
        entries = entries if entries is not None else self.roi_entries
        unix = self.recording.unix_seconds()
        shape = self.recording.shape
        for entry in entries:
            if not entry.placed:
                continue
            if entry.is_interp_ready():
                values = np.empty(len(unix), dtype=np.float32)
                for i in range(len(unix)):
                    frac = self._interp_fraction(i, entry.interp_start_frame, entry.interp_end_frame)
                    x, y, w, h = entry.interp_rect(frac)
                    row0, row1, col0, col1 = bounds_px_for(x, y, w, h, shape)
                    values[i] = entry.average(self.recording.frames[i, row0:row1, col0:col1], row0, row1, col0, col1)
            else:
                row0, row1, col0, col1 = entry.bounds_px(shape)
                values = entry.average(self.recording.frames[:, row0:row1, col0:col1], row0, row1, col0, col1)
            entry.curve.setData(unix, values)
            entry.curve.setVisible(entry.is_visible_checked())

