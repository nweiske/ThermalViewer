"""Frame-Navigation/Wiedergabe, Auswertungsbereich, Farbskala-Pegel sowie Legende/Farbverlauf."""
from __future__ import annotations


import numpy as np
import pyqtgraph as pg
from qtpy import QtWidgets

from ..data import (
    Recording,
)
from ..roi import bounds_px_for
from ..roi_entry import (
    RoiEntry,
)
from .constants import (
    COLORMAPS,
    COLORMAPS_BASE_REVERSED,
    MAX_FRAMES_WITH_SYMBOLS,
)


class _FrameNavMixin:
    def _set_recording(self, recording: Recording) -> None:
        self.recording = recording
        n = recording.n_frames
        rows, cols = recording.shape

        # Eine evtl. noch eingezeichnete Referenzlinie bezieht sich auf
        # Pixel-Koordinaten der ALTEN Aufnahme und waere auf dem neuen Bild
        # irrefuehrend platziert -- der Umrechnungsfaktor selbst (_px_to_mm)
        # bleibt bewusst bestehen (siehe Hinweis weiter unten), nur die
        # Visualisierung wird ausgeblendet.
        self._hide_ruler_visuals()
        self._cancel_measurement_tool()
        self._hide_measurement_visuals()

        for action in self._requires_recording_actions:
            action.setEnabled(True)
        self._refresh_scale_label()

        self._global_level_range = (
            (float(recording.frames.min()), float(recording.frames.max())) if n else None
        )

        self.frame_slider.blockSignals(True)
        self.frame_slider.setRange(0, n - 1)
        self.frame_slider.setValue(0)
        self.frame_slider.blockSignals(False)

        self.frame_spin.blockSignals(True)
        self.frame_spin.setRange(1, max(1, n))
        self.frame_spin.setValue(1)
        self.frame_spin.blockSignals(False)

        # Auswertungsstart/-ende (manuelles Festlegen des Bereichs) starten
        # standardmaessig beim ersten bzw. jeweils letzten Frame der neu
        # geladenen Aufnahme.
        self._eval_start_index = 0 if n else None
        self.spin_eval_start.blockSignals(True)
        self.spin_eval_start.setRange(1, max(1, n))
        self.spin_eval_start.setValue(1)
        self.spin_eval_start.blockSignals(False)

        self._eval_end_index = n - 1 if n else None
        self.spin_eval_end.blockSignals(True)
        self.spin_eval_end.setRange(1, max(1, n))
        self.spin_eval_end.setValue(max(1, n))
        self.spin_eval_end.blockSignals(False)
        self._update_timeline_markers()

        symbol = "o" if n <= MAX_FRAMES_WITH_SYMBOLS else None
        for entry in self.roi_entries:
            self._set_roi_geometry_ranges(entry, cols, rows)
            # Start/Ende-Zielbild der Interpolation: Standard weiterhin
            # erstes/letztes Bild der neu geladenen Aufnahme (bisheriges
            # Verhalten), aber jederzeit manuell aenderbar.
            entry.spin_interp_start_frame.setRange(1, max(1, n))
            entry.spin_interp_start_frame.setValue(1)
            entry.spin_interp_end_frame.setRange(1, max(1, n))
            entry.spin_interp_end_frame.setValue(max(1, n))
            # Bereits erfasste Interpolations-Keyframes (interp_start_frame/
            # -end_frame) auf die neue Aufnahme klemmen, NICHT verwerfen --
            # eine neu geladene Aufnahme kann kuerzer sein als die vorherige,
            # auf der die Keyframes urspruenglich gesetzt wurden. Ohne diese
            # Klemmung bliebe _interp_fraction() bei einem viel zu grossen
            # Nenner haengen und der Messbereich wuerde sein Ende NIE
            # erreichen, egal wie weit die neue (kuerzere) Aufnahme laeuft.
            max_idx = max(0, n - 1)
            if entry.interp_start_frame is not None:
                entry.interp_start_frame = min(entry.interp_start_frame, max_idx)
            if entry.interp_end_frame is not None:
                entry.interp_end_frame = min(entry.interp_end_frame, max_idx)
            entry.curve.setSymbol(symbol)
        self.live_curve.setSymbol(symbol)
        self.timeseries_live_curve.setSymbol(symbol)

        self._hover_row = None
        self._hover_col = None
        self._live_pinned = False
        self.live_cursor_marker.setVisible(False)
        self.live_cursor_label.setVisible(False)
        self.live_curve.clear()
        self.timeseries_live_curve.clear()
        self.live_label.setText(
            "Maus über das Bild bewegen, um den Temperaturverlauf am Cursor-Pixel live zu sehen. "
            "Linksklick fixiert die Stelle, Rechtsklick löst die Fixierung wieder."
        )

        self.view_box.setRange(xRange=(0, cols), yRange=(0, rows), padding=0.02)
        self.current_index = 0
        self._show_frame(0)
        self._recompute_curves()
        # t0 fuer den Laufzeit-Anzeigemodus (Zeitachse) bezieht sich auf DIESE
        # (neue) Aufnahme -- Anzeigemodus selbst (Uhrzeit/Laufzeit) bleibt wie
        # vom Nutzer gewaehlt bestehen, nur t0 wird aufgefrischt.
        self._apply_time_display_mode(self._time_display_mode)

        message = f"{n} Frame(s) geladen aus {recording.paths[0].parent}"
        if self._px_to_mm is not None:
            # Ein Massstab bleibt bewusst ueber einen Neuladevorgang hinweg
            # bestehen (z.B. gleicher Pruefstand/gleiche Kamera-Optik) -- bei
            # einer anderen Messreihe koennte er aber nicht mehr passen.
            message += "  |  Hinweis: Es ist noch ein zuvor definierter Maßstab aktiv, bitte auf Gültigkeit prüfen."
        if recording.had_duplicate_timestamps:
            message += (
                "  |  Achtung: mehrere Dateien hatten denselben Zeitstempel im "
                "Dateinamen (z.B. durch Kopieren) und wurden für die Zeitachse "
                "künstlich um je 1 ms auseinandergezogen."
            )
            QtWidgets.QMessageBox.warning(
                self,
                "Doppelte Zeitstempel erkannt",
                "Mehrere geladene Dateien tragen denselben Zeitstempel im Dateinamen "
                "(z.B. weil eine Datei kopiert wurde, ohne den Zeitstempel im Namen zu "
                "ändern). Für eine sinnvolle Zeitachse wurden diese Frames um je 1 ms "
                "auseinandergezogen. Für echte Messreihen sollte jede Datei einen "
                "eindeutigen Zeitstempel im Namen haben.",
            )
        if recording.skipped_files:
            message += f"  |  {len(recording.skipped_files)} Datei(en) übersprungen."
            details = "\n".join(f"- {p.name}: {err}" for p, err in recording.skipped_files)
            QtWidgets.QMessageBox.warning(
                self,
                "Einzelne Dateien übersprungen",
                f"{len(recording.skipped_files)} von "
                f"{n + len(recording.skipped_files)} ausgewählten Datei(en) konnten nicht "
                "geladen werden (kaputte/unlesbare CSV oder abweichende Bildauflösung) und "
                f"wurden übersprungen. Die übrigen {n} Frame(s) wurden normal geladen:\n\n"
                f"{details}",
            )
        self.statusBar().showMessage(message)

    # --------------------------------------------------------- Frame-Nav
    def _step_frame(self, delta: int) -> None:
        if self.recording is None or self.recording.n_frames == 0:
            return
        new_index = max(0, min(self.current_index + delta, self.recording.n_frames - 1))
        self.frame_slider.setValue(new_index)

    def _jump_to_first_frame(self) -> None:
        """Springt zum Auswertungsstart (Standard: erster Frame, per Spinbox
        "Auswertungsstart"/gruene Markierung in der Zeitleiste manuell nach
        hinten korrigierbar) -- genutzt sowohl von der Tastatur-Taste "Pos1"
        als auch von "Start festlegen" bei der Verlaufs-Interpolation."""
        if self.recording is None or self.recording.n_frames == 0:
            return
        target = self._eval_start_index if self._eval_start_index is not None else 0
        self._step_frame(target - self.current_index)

    def _jump_to_last_frame(self) -> None:
        """Springt zum Auswertungsende (Standard: letzter geladener Frame,
        per Spinbox "Auswertungsende"/rote Markierung in der Zeitleiste
        manuell nach unten korrigierbar) -- genutzt sowohl von der
        Tastatur-Taste "Ende" als auch von "Ende festlegen" bei der
        Verlaufs-Interpolation."""
        if self.recording is None or self.recording.n_frames == 0:
            return
        target = self._eval_end_index if self._eval_end_index is not None else self.recording.n_frames - 1
        self._step_frame(target - self.current_index)

    def _on_eval_start_changed(self, value: int) -> None:
        if self.recording is None:
            return
        new_start = value - 1
        current_end = self._eval_end_index if self._eval_end_index is not None else self.recording.n_frames - 1
        if new_start > current_end:
            # Start darf das Ende nicht ueberholen -- Ende folgt stattdessen
            # mit nach hinten (symmetrisch zu _on_eval_end_changed).
            self._eval_end_index = new_start
            self.spin_eval_end.blockSignals(True)
            self.spin_eval_end.setValue(new_start + 1)
            self.spin_eval_end.blockSignals(False)
        self._eval_start_index = new_start
        self._update_timeline_markers()
        self._refresh_all_interp_range_warnings()

    def _on_eval_end_changed(self, value: int) -> None:
        if self.recording is None:
            return
        new_end = value - 1
        current_start = self._eval_start_index if self._eval_start_index is not None else 0
        if new_end < current_start:
            self._eval_start_index = new_end
            self.spin_eval_start.blockSignals(True)
            self.spin_eval_start.setValue(new_end + 1)
            self.spin_eval_start.blockSignals(False)
        self._eval_end_index = new_end
        self._update_timeline_markers()
        self._refresh_all_interp_range_warnings()

    def _on_timeline_marker_dragged(self, which: str, value: int) -> None:
        if self.recording is None:
            return
        value = max(0, min(value, self.recording.n_frames - 1))
        if which == "start":
            self.spin_eval_start.setValue(value + 1)
        else:
            self.spin_eval_end.setValue(value + 1)

    def _update_timeline_markers(self) -> None:
        if self.recording is None or self.recording.n_frames == 0:
            self.frame_slider.set_markers(None, None)
            return
        self.frame_slider.set_markers(self._eval_start_index, self._eval_end_index)

    def _on_slider_changed(self, value: int) -> None:
        # Der Schieberegler ist intern 0-basiert (Frame-Index), das Zahlenfeld
        # daneben zeigt dem Nutzer wie die Statuszeile ("Frame 1/8") bewusst
        # 1-basierte Frame-Nummern, um Verwirrung zu vermeiden. Beide Widgets
        # werden zentral in _show_frame() synchron gehalten.
        self._show_frame(value)

    def _on_frame_spin_changed(self, value: int) -> None:
        self._show_frame(value - 1)

    def _level_mode(self) -> str:
        if self.radio_level_manual.isChecked():
            return "manual"
        return "global" if self.radio_level_global.isChecked() else "per_frame"

    def _set_level_mode(self, mode: str) -> None:
        """Setzt beide Radio-Gruppen (aeussere Automatisch/Manuell-Wahl und
        innere Pro-Bild/Gesamte-Serie-Unterwahl) konsistent auf den
        gewuenschten Modus-String -- zentrale Gegenstueck zu _level_mode(),
        genutzt beim Laden eines Projekts und beim Wiederherstellen nach
        einem temporaeren Override (z.B. Video-Export mit eigenen
        Einstellungen)."""
        if mode == "manual":
            self._set_widget_value(self.radio_level_manual, True, "setChecked")
        else:
            self._set_widget_value(self.radio_level_auto, True, "setChecked")
            sub_radio = self.radio_level_global if mode == "global" else self.radio_level_per_frame
            self._set_widget_value(sub_radio, True, "setChecked")
        self._on_level_mode_changed(None, True)

    @staticmethod
    def _set_widget_value(widget, value, setter_name: str = "setValue") -> None:
        """Setzt einen Widget-Wert, ohne dass dessen Change-Signal auf dem Weg
        dorthin ungewollt weitere Handler ausloest (z.B. beim programmatischen
        Wiederherstellen eines vorherigen Zustands)."""
        widget.blockSignals(True)
        getattr(widget, setter_name)(value)
        widget.blockSignals(False)

    def _set_level_spins(self, lo: float, hi: float) -> None:
        self._set_widget_value(self.spin_level_min, lo)
        self._set_widget_value(self.spin_level_max, hi)

    def _sync_histogram_levels(self, lo: float, hi: float) -> None:
        # Bugfix: self.image_item.setLevels() allein bewegt die eigene
        # Anzeige des HistogramLUTItem (Balken/Griffe direkt neben der
        # Farb-Legende) NICHT mit -- ohne diesen expliziten Aufruf blieb der
        # Balken nach dem Laden einer Messreihe bei seinem Konstruktions-
        # Default (0°) stehen, bis der Nutzer den Skalierungs-Modus manuell
        # nochmal umschaltete (Bugreport). blockSignals, da _set_level_spins
        # (vom Aufrufer direkt danach aufgerufen) dieselben Werte ohnehin
        # schon an die Spinboxen uebertraegt -- sonst wuerde
        # _on_histogram_levels_changed dieselbe Arbeit redundant wiederholen.
        self.histogram.blockSignals(True)
        self.histogram.setLevels(lo, hi)
        self.histogram.blockSignals(False)

    def _apply_levels_for_frame(self, frame: np.ndarray) -> None:
        mode = self._level_mode()
        if mode == "per_frame":
            self.image_item.setImage(frame, autoLevels=True)
            lo, hi = self.image_item.getLevels()
            self._sync_histogram_levels(lo, hi)
            self._set_level_spins(lo, hi)
        elif mode == "global" and self._global_level_range is not None:
            lo, hi = self._global_level_range
            self.image_item.setImage(frame, autoLevels=False)
            self.image_item.setLevels((lo, hi))
            self._sync_histogram_levels(lo, hi)
            self._set_level_spins(lo, hi)
        else:
            lo, hi = self.spin_level_min.value(), self.spin_level_max.value()
            self.image_item.setImage(frame, autoLevels=False)
            self.image_item.setLevels((lo, hi))
            self._sync_histogram_levels(lo, hi)

    @staticmethod
    def _interp_fraction(idx: int, start_idx: int, end_idx: int) -> float:
        """Frame-Index-Anteil von idx zwischen start_idx und end_idx, geklemmt
        auf [0, 1]. Gemeinsam genutzt von _update_interpolated_rois (Anzeige)
        und _recompute_curves (Kurvenberechnung), damit beide garantiert
        dieselbe Interpolations-Formel verwenden. Bewusst frame-index- statt
        zeitstempel-basiert (siehe RoiEntry.interp_start_frame)."""
        span = end_idx - start_idx
        if span <= 0:
            return 0.0
        return max(0.0, min(1.0, (idx - start_idx) / span))

    def _update_interpolated_rois(self, idx: int) -> None:
        for entry in self.roi_entries:
            if not entry.is_interp_ready():
                continue
            frac = self._interp_fraction(idx, entry.interp_start_frame, entry.interp_end_frame)
            entry.apply_interp_frame(frac)
            self._sync_roi_spinboxes(entry)

    def _update_roi_temperature_labels(self, idx: int, entries: list[RoiEntry] | None = None) -> None:
        """Aktualisiert die im Bild neben dem Namen angezeigte, aktuell
        gemittelte Temperatur der platzierten Messbereiche (Punkt 10).
        Direkt aus den Rohdaten des aktuellen Frames berechnet (statt aus
        entry.curve gelesen), damit die Anzeige unabhaengig davon korrekt
        ist, ob _recompute_curves() fuer diesen Frame bereits gelaufen ist.

        entries: optionale Teilmenge (Standard: alle Eintraege) -- z.B.
        waehrend eines ROI-Drags (_on_roi_region_changed, feuert laufend bei
        jeder Mausbewegung) wird bewusst NUR der gerade gezogene Messbereich
        neu berechnet statt bei jedem Zwischenschritt alle platzierten
        Messbereiche erneut durchzugehen, deren Temperatur sich dabei gar
        nicht aendert."""
        if self.recording is None or self.recording.n_frames == 0:
            return
        # idx nicht ungeprueft uebernehmen: self.current_index kann kurzzeitig
        # veraltet sein (z.B. waehrend eine Live-Ueberwachung die Aufnahme
        # gerade durch eine kleinere ersetzt hat, aber ein ROI-Drag noch aus
        # der alten Geometrie ein sigRegionChanged ausloest, siehe
        # _on_roi_region_changed) -- ohne Clamping fuehrte das zu einem
        # IndexError beim Zugriff auf self.recording.frames[idx].
        idx = max(0, min(idx, self.recording.n_frames - 1))
        shape = self.recording.shape
        for entry in (entries if entries is not None else self.roi_entries):
            if not entry.placed:
                continue
            if entry.is_interp_ready():
                frac = self._interp_fraction(idx, entry.interp_start_frame, entry.interp_end_frame)
                x, y, w, h = entry.interp_rect(frac)
                row0, row1, col0, col1 = bounds_px_for(x, y, w, h, shape)
            else:
                row0, row1, col0, col1 = entry.bounds_px(shape)
            temperature = float(entry.average(self.recording.frames[idx, row0:row1, col0:col1], row0, row1, col0, col1))
            entry.update_temperature_label(temperature)

    def _show_frame(self, idx: int) -> None:
        if self.recording is None or self.recording.n_frames == 0:
            return
        idx = max(0, min(idx, self.recording.n_frames - 1))
        self.current_index = idx
        # Schieberegler/Zahlenfeld hier zentral synchron halten, damit sie
        # auch bei direkten _show_frame()-Aufrufen ausserhalb der ueblichen
        # Slider-/Spin-Handler (z.B. Video-Export, initiales Laden) nicht vom
        # tatsaechlich angezeigten Frame abweichen koennen.
        self.frame_slider.blockSignals(True)
        self.frame_slider.setValue(idx)
        self.frame_slider.blockSignals(False)
        self.frame_spin.blockSignals(True)
        self.frame_spin.setValue(idx + 1)
        self.frame_spin.blockSignals(False)
        frame = self.recording.frames[idx]

        self._apply_levels_for_frame(frame)

        ts = self.recording.timestamps[idx]
        self.timestamp_label.setText("  " + ts.strftime("%Y-%m-%d %H:%M:%S"))

        unix = self.recording.unix_seconds()
        self.frame_marker.setValue(unix[idx])
        self.live_frame_marker.setValue(unix[idx])

        self._update_interpolated_rois(idx)
        self._update_roi_temperature_labels(idx)

        self._update_status_bar()

    def _on_play_toggled(self, checked: bool) -> None:
        if checked:
            if self.recording is None or self.recording.n_frames < 2:
                self.play_button.setChecked(False)
                return
            n = self.recording.n_frames
            start_idx = self._eval_start_index if self._eval_start_index is not None else 0
            end_idx = self._eval_end_index if self._eval_end_index is not None else n - 1
            # Wiedergabe bleibt standardmaessig auf den Auswertungsbereich
            # (gruene/rote Markierung) begrenzt -- nur wenn der Cursor manuell
            # AUSSERHALB dieses Bereichs steht, laeuft sie ungeklemmt bis zum
            # tatsaechlichen Ende der Aufnahme.
            self._play_clamped = start_idx <= self.current_index <= end_idx
            if self._play_clamped:
                if self.current_index >= end_idx:
                    # Wiedergabe war bereits am Ende des Bereichs angekommen --
                    # erneutes Starten faengt wieder beim Start des Bereichs an.
                    self.frame_slider.setValue(start_idx)
            elif self.current_index >= n - 1:
                # Wiedergabe war bereits am tatsaechlichen Ende angekommen --
                # erneutes Starten faengt wieder von vorne an.
                self.frame_slider.setValue(0)
            self.play_button.setText("⏸ Pause")
            interval = int(1000 / max(0.1, self.fps_spin.value()))
            self.play_timer.start(interval)
        else:
            self.play_button.setText("▶ Play")
            self.play_timer.stop()

    def _advance_frame(self) -> None:
        if self.recording is None:
            self.play_button.setChecked(False)
            return
        n = self.recording.n_frames
        nxt = self.current_index + 1
        if self._play_clamped:
            end_idx = self._eval_end_index if self._eval_end_index is not None else n - 1
            if nxt > end_idx:
                self.play_button.setChecked(False)
                return
        if nxt >= n:
            self.play_button.setChecked(False)
            return
        self.frame_slider.setValue(nxt)

    def _on_fps_changed(self, value: float) -> None:
        if self.play_timer.isActive():
            self.play_timer.setInterval(int(1000 / max(0.1, value)))

    # ---------------------------------------------------------- Legende
    def _apply_colormap(self) -> None:
        name = COLORMAPS[self.combo_cmap.currentIndex()][1]
        # skipCache=True: pg.colormap.get() liefert fuer denselben Namen
        # sonst immer dieselbe GECACHTE Instanz zurueck, und ColorMap.
        # reverse() aendert sie IN-PLACE (wie list.reverse()). Ohne
        # skipCache wuerde jeder Aufruf hier den globalen Cache kumulativ
        # weiterdrehen, statt deterministisch von der originalen
        # Reihenfolge auszugehen. (Ein manuell aus cmap.pos/cmap.color
        # zusammengebautes ColorMap ist KEINE brauchbare Alternative --
        # der Konstruktor interpretiert bereits normierte float-Farbwerte
        # dabei fälschlich nochmal als Byte-Werte und macht daraus eine
        # fast schwarze/durchsichtige LUT.)
        cmap = pg.colormap.get(name, skipCache=True)
        want_reversed = (name in COLORMAPS_BASE_REVERSED) != self.chk_cmap_invert.isChecked()
        if want_reversed:
            cmap.reverse()
        self.histogram.gradient.setColorMap(cmap)

    def _on_colormap_changed(self, _index: int) -> None:
        self._apply_colormap()

    def _on_colormap_invert_toggled(self, _checked: bool) -> None:
        self._apply_colormap()

    def _on_level_mode_changed(self, _button: QtWidgets.QAbstractButton | None, checked: bool) -> None:
        if not checked:
            return
        manual = self.radio_level_manual.isChecked()
        self.spin_level_min.setEnabled(manual)
        self.spin_level_max.setEnabled(manual)
        # Pro-Bild/Gesamte-Serie sind nur sinnvoll bedienbar, solange
        # "Automatisch" aktiv ist -- sonst mit "Manuell" verwechselbar.
        self.radio_level_per_frame.setEnabled(not manual)
        self.radio_level_global.setEnabled(not manual)
        self._show_frame(self.current_index)

    def _on_manual_level_radio_toggled(self, checked: bool) -> None:
        """Punkt 3 (Nutzerwunsch): eine manuelle, feste Skalierung gilt der
        Logik nach über die GESAMTE Messung (nicht pro Bild neu) -- "Über
        gesamte Messung" wird daher beim Umschalten auf "Manuell" rein visuell
        mit angehakt (bleibt ausgegraut/inaktiv, siehe _on_level_mode_changed),
        ohne die tatsächliche Automatik-Unterwahl zu verändern. Wird beim
        Zurückwechseln zu "Automatisch" per echtem Klick unverändert
        wiederhergestellt.

        NUR ueber .toggled auf radio_level_manual verbunden (nicht ueber die
        gemeinsame level_mode_group), da _set_level_mode() programmatische
        Wechsel (Projekt laden, temporärer Export-Override) stets mit
        blockSignals durchfuehrt -- genau diese sollen hier NICHT eingreifen,
        weil sie den Automatik-Modus dort bereits explizit und vollständig
        selbst festlegen."""
        if checked:
            self._auto_submode_before_manual = (
                "global" if self.radio_level_global.isChecked() else "per_frame"
            )
            self._set_widget_value(self.radio_level_global, True, "setChecked")
        elif self._auto_submode_before_manual is not None:
            sub_radio = (
                self.radio_level_global if self._auto_submode_before_manual == "global"
                else self.radio_level_per_frame
            )
            self._set_widget_value(sub_radio, True, "setChecked")
            self._auto_submode_before_manual = None

    def _on_level_spin_changed(self) -> None:
        if self._level_mode() != "manual":
            return
        lo, hi = self.spin_level_min.value(), self.spin_level_max.value()
        if hi <= lo:
            return
        self.histogram.setLevels(lo, hi)
        self.image_item.setLevels((lo, hi))

    def _on_histogram_levels_changed(self) -> None:
        lo, hi = self.histogram.getLevels()
        self._set_level_spins(lo, hi)

