"""Maus-/Live-Cursor-Interaktion im Thermobild."""
from __future__ import annotations

import numpy as np
from qtpy import QtCore


class _MouseMixin:
    def _on_scene_mouse_clicked(self, event) -> None:
        if self.recording is None:
            return

        if self._ruler_armed:
            self._handle_ruler_click(event)
            return

        if self._measurement_armed:
            self._handle_measurement_click(event)
            return

        if self._cleaning_pick_armed:
            self._handle_cleaning_point_click(event)
            return

        if event.double() and self._ruler_hit_test(event.scenePos()):
            self._edit_ruler_length()
            return

        if self._armed_entry is not None:
            # Ein ROI wartet auf Platzierung per Linksklick -- andere Klicks
            # (z.B. ein versehentlicher Rechtsklick) sollen in der
            # Zwischenzeit nicht zusaetzlich die Live-Ansicht veraendern.
            if event.button() != QtCore.Qt.LeftButton:
                return
            scene_pos = event.scenePos()
            if not self.view_box.sceneBoundingRect().contains(scene_pos):
                return
            view_pos = self.view_box.mapSceneToView(scene_pos)
            entry = self._armed_entry
            entry.place(view_pos.x(), view_pos.y(), entry.spin_width.value(), entry.spin_height.value())
            self._sync_roi_spinboxes(entry)
            entry.btn_place.setChecked(False)
            self._armed_entry = None
            self._recompute_curves(entries=[entry])
            self._refresh_idle_guidance()
            return

        if event.button() == QtCore.Qt.RightButton:
            if not self._live_pinned:
                return
            self._live_pinned = False
            row_col = self._pixel_at_scene_pos(event.scenePos())
            if row_col is not None:
                self._update_live_cursor(*row_col)
            elif self._hover_row is not None and self._hover_col is not None:
                # Rechtsklick lag ausserhalb des Bildes -- Fixierung trotzdem
                # aufheben und den "(fixiert)"-Hinweis im Label entfernen,
                # sonst bleibt er stehen, bis die Maus zufaellig auf ein
                # anderes Pixel als das zuletzt fixierte wandert.
                self.live_label.setText(
                    f"Cursor-Pixel: Zeile {self._hover_row}, Spalte {self._hover_col}"
                )
            self.statusBar().showMessage("Live-Ansicht folgt wieder dem Mauscursor.", 3000)
            return

        if event.button() == QtCore.Qt.LeftButton:
            row_col = self._pixel_at_scene_pos(event.scenePos())
            if row_col is None:
                return
            row, col = row_col
            self._live_pinned = True
            self._update_live_cursor(row, col)
            self.live_label.setText(
                f"Cursor-Pixel: Zeile {row}, Spalte {col}  (fixiert – Rechtsklick ins Bild zum Lösen)"
            )
            self.statusBar().showMessage(
                f"Live-Ansicht fixiert auf Zeile {row}, Spalte {col}. Rechtsklick ins Bild löst die Fixierung wieder.",
                5000,
            )

    def _on_scene_mouse_moved(self, scene_pos: QtCore.QPointF) -> None:
        if self.recording is None or self._live_pinned:
            return
        row_col = self._pixel_at_scene_pos(scene_pos)
        if row_col is None:
            return
        row, col = row_col
        if (row, col) == (self._hover_row, self._hover_col):
            return
        self._update_live_cursor(row, col)

    def _pixel_at_scene_pos(self, scene_pos: QtCore.QPointF) -> tuple[int, int] | None:
        if self.recording is None:
            return None
        if not self.view_box.sceneBoundingRect().contains(scene_pos):
            return None
        view_pos = self.view_box.mapSceneToView(scene_pos)
        rows, cols = self.recording.shape
        col = int(np.floor(view_pos.x()))
        row = int(np.floor(view_pos.y()))
        if not (0 <= row < rows and 0 <= col < cols):
            return None
        return row, col

    def _live_cursor_bounds(self, row: int, col: int) -> tuple[int, int, int, int]:
        """Auf das Bild geclippter row0:row1, col0:col1-Bereich um das
        Cursor-Pixel, dessen Kantenlaenge ueber "Werkzeuge > Live-Cursor-
        Bereichsgröße" einstellbar ist (Standard: 1x1, d.h. genau dieses
        eine Pixel -- bisheriges Verhalten).

        "before"/"after" statt eines einzelnen "half": fuer die -- ausschliesslich
        ungeraden -- waehlbaren Groessen (1/3/5/7/9/11/13/15) ist before==after-1==half,
        das Cursor-Pixel liegt also immer exakt in der Mitte des Bereichs."""
        size = self._live_cursor_kernel_size
        before = size // 2
        after = size - before
        rows, cols = self.recording.shape
        row0 = max(0, row - before)
        row1 = min(rows, row + after)
        col0 = max(0, col - before)
        col1 = min(cols, col + after)
        return row0, row1, col0, col1

    def _live_cursor_series(self, row: int, col: int) -> np.ndarray:
        row0, row1, col0, col1 = self._live_cursor_bounds(row, col)
        if self._live_cursor_kernel_size == 1:
            return self.recording.frames[:, row, col]
        return self.recording.frames[:, row0:row1, col0:col1].mean(axis=(1, 2))

    def _live_cursor_value(self, idx: int, row: int, col: int) -> float:
        row0, row1, col0, col1 = self._live_cursor_bounds(row, col)
        if self._live_cursor_kernel_size == 1:
            return float(self.recording.frames[idx, row, col])
        return float(self.recording.frames[idx, row0:row1, col0:col1].mean())

    def _on_live_cursor_kernel_selected(self, size: int) -> None:
        self._live_cursor_kernel_size = size
        self._settings.setValue("live_cursor/kernel_size", size)
        if self._hover_row is not None and self._hover_col is not None:
            self._update_live_cursor(self._hover_row, self._hover_col)

    def _update_live_cursor(self, row: int, col: int) -> None:
        self._hover_row, self._hover_col = row, col
        values = self._live_cursor_series(row, col)
        unix = self.recording.unix_seconds()
        self.live_curve.setData(unix, values)
        if self.chk_show_live_in_timeseries.isChecked():
            self.timeseries_live_curve.setData(unix, values)
        suffix = "" if self._live_cursor_kernel_size == 1 else (
            f" ({self._live_cursor_kernel_size}×{self._live_cursor_kernel_size}-Mittel)"
        )
        self.live_label.setText(f"Cursor-Pixel: Zeile {row}, Spalte {col}{suffix}")
        self.live_cursor_marker.setData([col + 0.5], [row + 0.5])
        self.live_cursor_marker.setVisible(True)
        self._update_status_bar()

    def _on_show_live_in_timeseries_toggled(self, checked: bool) -> None:
        if checked:
            if self._hover_row is not None and self._hover_col is not None:
                values = self._live_cursor_series(self._hover_row, self._hover_col)
                self.timeseries_live_curve.setData(self.recording.unix_seconds(), values)
            self.timeseries_legend.addItem(self.timeseries_live_curve, "Live (Cursor)")
        else:
            self.timeseries_legend.removeItem(self.timeseries_live_curve)
        self.timeseries_live_curve.setVisible(checked)

    def _update_status_bar(self) -> None:
        if self.recording is None:
            return
        idx = self.current_index
        ts = self.recording.timestamps[idx].strftime("%Y-%m-%d %H:%M:%S")
        runtime = self._format_runtime(
            (self.recording.timestamps[idx] - self.recording.timestamps[0]).total_seconds()
        )
        msg = f"Frame {idx + 1}/{self.recording.n_frames}  |  {ts}  |  Laufzeit: {runtime}"
        if idx in self._excluded_frame_indices:
            msg += "  |  ⚠ Von der Rohdaten-Bereinigung ausgeblendet (Kurven/Export überspringen es)."
        if self._hover_row is not None and self._hover_col is not None:
            val = self._live_cursor_value(idx, self._hover_row, self._hover_col)
            msg += f"  |  Cursor: Zeile {self._hover_row}, Spalte {self._hover_col} = {val:.2f} °C"
        self.statusBar().showMessage(msg)
        self._update_live_cursor_label()

    def _update_live_cursor_label(self) -> None:
        """Zeigt die Temperatur DES AKTUELLEN FRAMES am Cursor-Kreuz direkt
        im Thermobild an -- wird sowohl bei Mausbewegung (_update_live_cursor)
        als auch bei jedem Frame-Wechsel (_show_frame -> _update_status_bar)
        aufgerufen, damit der Wert waehrend der Wiedergabe automatisch
        mitlaeuft, ohne dass die Maus bewegt werden muss."""
        if self.recording is None or self._hover_row is None or self._hover_col is None:
            self.live_cursor_label.setVisible(False)
            return
        val = self._live_cursor_value(self.current_index, self._hover_row, self._hover_col)
        self.live_cursor_label.setText(f"{val:.1f} °C")
        self.live_cursor_label.setPos(self._hover_col + 0.6, self._hover_row - 0.6)
        self.live_cursor_label.setVisible(True)

