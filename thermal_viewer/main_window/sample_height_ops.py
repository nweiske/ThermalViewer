"""Probenhöhen: zusätzliche, unabhängige Schwindungsmessung über beliebig
viele (max. 10) benannte, horizontale Bildzeilen (Nutzerwunsch:
"Schwindungsmessung ähnlich zur Querschnittsfunktion ... drei/beliebig
viele mit Namen (max. 10 Linien), benennen können wie ROIs ... Probenhöhen
festlegen und einzeln ausgeben lassen ... über eine Art Schwellenwert
wieder die Kanten/Ränder der Probe finden und daraus dann pro Bild die
Probenbreite bestimmen") -- ERGÄNZT (ersetzt nicht) die bestehende
Ein-Box-Kontur-Messung in shrinkage_ops.py.

Jede Probenhöhe ist EINE feste Bildzeile, per Spinbox ODER direkt per Ziehen
der zugehörigen (horizontalen, gestrichelten) Linie im Thermobild
positionierbar -- analog zur Querschnitt-Linie (crosssection_ops.py), aber
dauerhaft (nicht nur Hover) und mit eigenem Namen/eigener Farbe wie ein ROI.
Die Kanten/Breite an dieser Zeile werden per Otsu-Schwellenwert bestimmt
(_track_sample_width_at_row in shrinkage_ops.py) -- UNABHÄNGIG von der Box-
Messung selbst (muss nicht aktiviert/berechnet sein), nutzt aber deren
Spaltenbereich (roi_shrink_area) mit, damit kein zusätzliches Eingabe-
Element nötig ist (siehe _sample_height_col_range).

Anders als die flächige Box (die laut deren Moduldocstring mehrere Sekunden
Rechenzeit beanspruchen kann) ist EINE Zeile schnell genug, um bei jeder
Positionsänderung sofort neu zu rechnen -- kein separater "Berechnen"-Knopf
nötig, die Kurve aktualisiert sich live.

Die Ergebnis-Kurve jeder Probenhöhe liegt auf DEMSELBEN Graphen wie die
Box-Kurve (self.shrinkage_plot) -- als Prozentwert relativ zum ersten Bild,
exakt dieselbe Formel/Achse wie shrinkage_ops.py:_update_shrinkage_curve,
damit beide Messarten direkt vergleichbar bleiben. Der CSV-Export bietet
dieselbe %/mm/px-Werteinheit-Wahl wie die Box-Messung (siehe
CsvColumnDialog.percent_unit/export_csv.py)."""
from __future__ import annotations

import colorsys
from functools import partial

import numpy as np
import pyqtgraph as pg
from qtpy import QtCore, QtGui, QtWidgets

from .shrinkage_ops import _detect_polarity_area, _track_sample_width_at_row

MAX_SAMPLE_HEIGHT_COUNT = 10
_SAMPLE_HEIGHT_COLORS = ["#60a5fa", "#f97316", "#c084fc", "#facc15", "#2dd4bf"]


def _sample_height_color(number: int) -> str:
    """Analog zu roi_entry.py:roi_color_for_number, aber eine eigene, von
    den ROI-/Schwindungsbox-Farben klar unterscheidbare Palette (Kollision
    waere hier besonders verwirrend, da Probenhöhen-Kurven auf demselben
    Graphen wie die Box-Kurve liegen)."""
    idx = number - 1
    if 0 <= idx < len(_SAMPLE_HEIGHT_COLORS):
        return _SAMPLE_HEIGHT_COLORS[idx]
    hue = (idx * 0.6180339887498949 + 0.37) % 1.0
    r, g, b = colorsys.hsv_to_rgb(hue, 0.65, 0.95)
    return "#%02x%02x%02x" % (round(r * 255), round(g * 255), round(b * 255))


class SampleHeightEntry:
    """Bündelt EINE benannte Probenhöhen-Zeile mit ihrer Linie im Bild und
    ihrer Kurve im Schwindungs-Graphen -- bewusst leichtgewichtiger als
    RoiEntry (roi_entry.py): keine Interpolation, kein Kreis-/Statistik-
    Modus, da eine feste Bildzeile keine dieser Konzepte braucht."""

    def __init__(self, number: int, color: str, view_box: pg.ViewBox, curve: pg.PlotDataItem):
        self.number = number
        self.color = color
        self.name = f"Probenhöhe {number}"
        self.row = 0
        self.enabled = True
        self.widths_px: np.ndarray | None = None
        # Absolute Bildspalten der zuletzt erkannten linken/rechten Kante,
        # EIN Wert je Bild (NaN, wo keine Kante gefunden wurde) -- Grundlage
        # der kleinen Kanten-Markierungen (siehe _rebuild_sample_height_
        # edge_ticks), separat von widths_px, da die Ticks die ABSOLUTE
        # Position brauchen, nicht nur die Differenz.
        self.left_edges_px: np.ndarray | None = None
        self.right_edges_px: np.ndarray | None = None
        self.curve = curve
        self.curve.opts["name"] = self.name

        self.line = pg.InfiniteLine(
            angle=0, movable=True,
            pen=pg.mkPen(color, width=1.5, style=QtCore.Qt.DashLine),
            hoverPen=pg.mkPen(color, width=2.5, style=QtCore.Qt.DashLine),
        )
        self.line.setVisible(False)
        view_box.addItem(self.line)

        # Name DIREKT AN der Linie im Thermobild (Nutzerwunsch: "bitte den
        # Namen mit zur jeweiligen Linie schreiben") -- analog zu
        # RoiEntry.label, an der linken Kante des Spaltenbereichs verankert
        # (siehe _update_sample_height_line_pos), damit er nicht mit den
        # Kanten-Markierungen ueberlappt, die je nach erkannter Breite an
        # wechselnder Stelle sitzen.
        self.name_label = pg.TextItem(text=self.name, color=color, anchor=(0, 1), fill=(0, 0, 0, 140))
        self.name_label.setVisible(False)
        view_box.addItem(self.name_label)

        # Kleine Kanten-Markierungen (Nutzerwunsch: "kleine vertikale
        # Linien ... die mir signalisieren, wo/welche Breite bzw. Kante
        # gerade vermutet/detektiert wird ... wirklich nur kleine Striche,
        # nicht wesentlich größer als die Start/Stop-Marker") -- EIN
        # PlotDataItem fuer BEIDE Kanten (links+rechts), per NaN-Trennung
        # und connect="finite" als zwei getrennte kurze Striche gezeichnet
        # (siehe _rebuild_sample_height_edge_ticks), statt zwei eigener
        # Items -- ein Item weniger pro Probenhöhe.
        self.edge_ticks = pg.PlotDataItem(pen=pg.mkPen(color, width=2))
        self.edge_ticks.setVisible(False)
        view_box.addItem(self.edge_ticks)

        # Von MainWindow gesetzt, hier nur Platzhalter fuer Typklarheit.
        self.spin_row: QtWidgets.QSpinBox | None = None
        self.edit_name: QtWidgets.QLineEdit | None = None
        self.btn_color: QtWidgets.QPushButton | None = None
        self.chk_enabled: QtWidgets.QCheckBox | None = None

    def set_name(self, name: str) -> None:
        self.name = name
        self.curve.opts["name"] = name
        self.name_label.setText(name)

    def set_color(self, color: str) -> None:
        self.color = color
        self.line.setPen(pg.mkPen(color, width=1.5, style=QtCore.Qt.DashLine))
        self.line.hoverPen = pg.mkPen(color, width=2.5, style=QtCore.Qt.DashLine)
        self.curve.setPen(pg.mkPen(color, width=2))
        self.name_label.setColor(color)
        self.edge_ticks.setPen(pg.mkPen(color, width=2))
        if self.btn_color is not None:
            self.btn_color.setStyleSheet(
                f"background-color:{color}; border:1px solid #333; border-radius:4px;"
            )

    def remove_from_view(self, view_box: pg.ViewBox, plot: pg.PlotWidget) -> None:
        view_box.removeItem(self.line)
        view_box.removeItem(self.name_label)
        view_box.removeItem(self.edge_ticks)
        plot.removeItem(self.curve)


class _SampleHeightMixin:
    def _build_sample_height_panel(self) -> None:
        box = QtWidgets.QGroupBox("Probenhöhen (zusätzliche, unabhängige Breitenmessung)")
        self._sample_height_groupbox = box
        layout = QtWidgets.QVBoxLayout(box)

        info = QtWidgets.QLabel(
            "Zusätzliche Methode neben der Box oben: beliebig viele (max. 10) benannte, "
            "horizontale Linien im Bild -- die Probenbreite wird per automatischem "
            "Schwellenwert an jeder einzelnen Zeile bestimmt (nutzt denselben Spaltenbereich "
            "wie die Box oben, unabhängig davon ob diese aktiviert ist). Ergebnis erscheint "
            "sofort als eigene Kurve im Schwindungs-Graphen, live bei jeder Verschiebung."
        )
        info.setWordWrap(True)
        info.setAlignment(QtCore.Qt.AlignJustify)
        info.setStyleSheet("color:#6b7280;")
        layout.addWidget(info)

        self.sample_height_list = QtWidgets.QListWidget()
        self.sample_height_list.setMaximumHeight(180)
        layout.addWidget(self.sample_height_list)

        self.btn_add_sample_height = QtWidgets.QPushButton("+ Probenhöhe")
        self.btn_add_sample_height.setToolTip(
            f"Weitere Probenhöhe hinzufügen (bis zu {MAX_SAMPLE_HEIGHT_COUNT})."
        )
        self.btn_add_sample_height.clicked.connect(self._on_add_sample_height_clicked)
        layout.addWidget(self.btn_add_sample_height)

    def _sample_height_col_range(self) -> tuple[int, int, int]:
        """(col0, col1, seed_col) -- Spaltenbereich der bestehenden
        Schwindungs-Box (roi_shrink_area) als gemeinsamer Suchbereich fuer
        ALLE Probenhöhen, UNABHAENGIG davon, ob die Box-Messung selbst
        aktiviert ist (die Box ist immer positioniert, siehe
        shrinkage_ops.py:_reset_shrinkage_state_for_recording). Fallback auf
        die volle Bildbreite, falls die Box (z.B. manuell) auf Breite 0
        gezogen wurde."""
        rows, cols = self.recording.shape
        _row0, _row1, col0, col1 = self.roi_shrink_area.bounds_px((rows, cols))
        if col1 <= col0:
            col0, col1 = 0, cols
        return col0, col1, (col0 + col1) // 2

    def _recompute_sample_height_entry(self, entry: SampleHeightEntry) -> None:
        if self.recording is None or self.recording.n_frames == 0:
            entry.widths_px = None
            entry.left_edges_px = None
            entry.right_edges_px = None
            self._update_sample_height_curve(entry)
            self._rebuild_sample_height_edge_ticks(entry)
            return
        rows, _cols = self.recording.shape
        row = max(0, min(rows - 1, entry.row))
        col0, col1, seed_col = self._sample_height_col_range()
        # Polaritaet lokal um die Zeile herum automatisch erkannt (analog
        # zur Box, siehe _detect_polarity_area), aber UNABHAENGIG von deren
        # eigenem Ergebnis berechnet -- eine Probenhöhe muss auch
        # funktionieren, ohne dass die Box-Messung je "Berechnen" gedrückt
        # hat.
        band0 = max(0, row - 5)
        band1 = min(rows, row + 6)
        frame_idx = min(self.current_index, self.recording.n_frames - 1)
        warmer = _detect_polarity_area(self.recording.frames[frame_idx], band0, band1, col0, col1)
        entry.widths_px, entry.left_edges_px, entry.right_edges_px = _track_sample_width_at_row(
            self.recording.frames, row, col0, col1, warmer, seed_col,
        )
        self._update_sample_height_curve(entry)
        self._update_sample_height_label_pos(entry)
        self._rebuild_sample_height_edge_ticks(entry)

    def _recompute_all_sample_heights(self) -> None:
        for entry in self._sample_height_entries:
            self._recompute_sample_height_entry(entry)

    def _update_sample_height_curve(self, entry: SampleHeightEntry) -> None:
        """Aktualisiert NUR die Kurve aus dem zuletzt berechneten Ergebnis
        (entry.widths_px) -- getrennt von _recompute_sample_height_entry,
        damit ein reiner Wechsel der ausgeblendeten Bilder (Rohdaten-
        Bereinigung, siehe data_cleaning_ops.py) nicht jedes Mal neu
        rechnen muss (dieselbe Aufteilung wie shrinkage_ops.py:
        _update_shrinkage_curve vs. _compute_shrinkage)."""
        if entry.widths_px is None or self.recording is None:
            entry.curve.clear()
            entry.curve.setVisible(False)
            return
        first = float(entry.widths_px[0])
        values = (first - entry.widths_px) / first * 100.0 if first else np.zeros_like(entry.widths_px)
        unix = self.recording.unix_seconds()
        if self._excluded_frame_indices:
            keep_mask = np.ones(len(unix), dtype=bool)
            keep_mask[list(self._excluded_frame_indices)] = False
            entry.curve.setData(unix[keep_mask], values[keep_mask])
        else:
            entry.curve.setData(unix, values)
        entry.curve.setVisible(entry.enabled and self._is_layer_tab_active("shrinkage"))

    def _update_all_sample_height_curves(self) -> None:
        for entry in self._sample_height_entries:
            self._update_sample_height_curve(entry)

    def _update_sample_height_line_pos(self, entry: SampleHeightEntry) -> None:
        entry.line.blockSignals(True)
        entry.line.setPos(entry.row + 0.5)
        entry.line.blockSignals(False)
        self._update_sample_height_label_pos(entry)

    def _update_sample_height_label_pos(self, entry: SampleHeightEntry) -> None:
        """Haelt den Namen DIREKT AN der Linie im Bild (Nutzerwunsch, siehe
        SampleHeightEntry.name_label) -- an der linken Kante des aktuellen
        Spaltenbereichs verankert (_sample_height_col_range), damit er
        nicht mit den Kanten-Markierungen ueberlappt (siehe
        _rebuild_sample_height_edge_ticks)."""
        if self.recording is None:
            return
        col0, _col1, _seed_col = self._sample_height_col_range()
        entry.name_label.setPos(col0, entry.row + 0.5)

    def _rebuild_sample_height_edge_ticks(self, entry: SampleHeightEntry) -> None:
        """Kleine Kanten-Markierungen an der zuletzt erkannten linken/
        rechten Kante DES AKTUELL ANGEZEIGTEN Bildes (Nutzerwunsch: "kleine
        vertikale Linien ... die mir signalisieren, wo/welche Breite bzw.
        Kante gerade vermutet/detektiert wird") -- analog zum "clear-and-
        redraw"-Muster der Schwindungs-Kontur-Ueberlagerung
        (shrinkage_ops.py:_rebuild_shrinkage_contour_overlay), aber pro
        Bild nur zwei kurze Striche statt einer ganzen Kontur. Bewusst
        KLEIN gehalten (2% der Bildhoehe, auf [1.5, 8] Bildpixel begrenzt)
        -- "wirklich nur kleine Striche, nicht wesentlich größer als die
        Start/Stop-Marker" (siehe plot_items.py:_EvalRangeSlider.paintEvent,
        dort 5 Bildschirm-Pixel als Referenzgroesse)."""
        if (
            entry.left_edges_px is None or self.recording is None
            or not (0 <= self.current_index < len(entry.left_edges_px))
        ):
            entry.edge_ticks.setData([], [])
            return
        left = entry.left_edges_px[self.current_index]
        right = entry.right_edges_px[self.current_index]
        if np.isnan(left) or np.isnan(right):
            entry.edge_ticks.setData([], [])
            return
        rows, _cols = self.recording.shape
        half = max(1.5, min(8.0, rows * 0.02))
        y_center = entry.row + 0.5
        xs = [left, left, np.nan, right, right]
        ys = [y_center - half, y_center + half, np.nan, y_center - half, y_center + half]
        entry.edge_ticks.setData(xs, ys, connect="finite")

    def _rebuild_all_sample_height_edge_ticks(self) -> None:
        for entry in self._sample_height_entries:
            self._rebuild_sample_height_edge_ticks(entry)

    def _apply_sample_height_visibility(self) -> None:
        """Analog zu shrinkage_ops.py:_apply_shrinkage_roi_visibility --
        folgt demselben "Schwindungsmessung"-Ebenen-Tab wie die Box."""
        tab_active = self._is_layer_tab_active("shrinkage") and self.recording is not None
        for entry in self._sample_height_entries:
            visible = tab_active and entry.enabled
            entry.line.setVisible(visible)
            entry.name_label.setVisible(visible)
            entry.curve.setVisible(visible and entry.widths_px is not None)
            entry.edge_ticks.setVisible(visible and entry.widths_px is not None)

    def _on_shrink_box_changed_for_sample_heights(self, *_args) -> None:
        """An roi_shrink_area.sigRegionChangeFinished angeschlossen (siehe
        _build_sample_height_panel-Aufrufstelle in roi_panel_build.py) --
        der Spaltenbereich ALLER Probenhöhen haengt an dieser Box
        (_sample_height_col_range), ein Verschieben/Groessenaendern
        derselben macht also eine Neuberechnung ALLER Probenhöhen noetig."""
        self._recompute_all_sample_heights()

    def _refresh_sample_height_rows(self) -> None:
        self.sample_height_list.clear()
        rows_max = max(0, self.recording.shape[0] - 1) if self.recording is not None else 0
        for i, entry in enumerate(self._sample_height_entries):
            row_widget = QtWidgets.QWidget()
            row_layout = QtWidgets.QHBoxLayout(row_widget)
            row_layout.setContentsMargins(4, 2, 4, 2)

            chk = QtWidgets.QCheckBox()
            chk.setChecked(entry.enabled)
            chk.setToolTip("Probenhöhe ein-/ausblenden (Linie im Bild + Kurve).")
            chk.toggled.connect(partial(self._on_sample_height_enabled_toggled, i))
            row_layout.addWidget(chk)
            entry.chk_enabled = chk

            edit_name = QtWidgets.QLineEdit(entry.name)
            edit_name.setToolTip("Name (frei editierbar, wie bei Messbereichen).")
            edit_name.editingFinished.connect(partial(self._on_sample_height_name_edited, i))
            row_layout.addWidget(edit_name, 1)
            entry.edit_name = edit_name

            row_layout.addWidget(QtWidgets.QLabel("Zeile:"))
            spin_row = QtWidgets.QSpinBox()
            spin_row.setRange(0, rows_max)
            spin_row.setValue(entry.row)
            spin_row.valueChanged.connect(partial(self._on_sample_height_row_spin_changed, i))
            spin_row.editingFinished.connect(self._end_grouped_undo_edit)
            row_layout.addWidget(spin_row)
            entry.spin_row = spin_row

            btn_color = QtWidgets.QPushButton()
            btn_color.setFixedSize(20, 20)
            btn_color.setCursor(QtCore.Qt.PointingHandCursor)
            btn_color.setToolTip("Farbe ändern.")
            btn_color.setStyleSheet(
                f"background-color:{entry.color}; border:1px solid #333; border-radius:4px;"
            )
            btn_color.clicked.connect(partial(self._on_sample_height_color_clicked, i))
            row_layout.addWidget(btn_color)
            entry.btn_color = btn_color

            btn_remove = QtWidgets.QPushButton("×")
            btn_remove.setFixedSize(22, 22)
            btn_remove.setToolTip("Probenhöhe endgültig entfernen.")
            btn_remove.clicked.connect(partial(self._on_sample_height_remove_clicked, i))
            row_layout.addWidget(btn_remove)

            item = QtWidgets.QListWidgetItem()
            item.setSizeHint(row_widget.sizeHint())
            self.sample_height_list.addItem(item)
            self.sample_height_list.setItemWidget(item, row_widget)
        self.btn_add_sample_height.setEnabled(len(self._sample_height_entries) < MAX_SAMPLE_HEIGHT_COUNT)

    def _create_sample_height_entry(self, number: int, color: str) -> SampleHeightEntry:
        """Baut EINEN neuen, voll verdrahteten Eintrag -- gemeinsame Stelle
        fuer den "+ Probenhöhe"-Knopf UND das Wiederherstellen aus einer
        Projektdatei/einem Undo-Snapshot (siehe project_io.py:
        _load_project_sample_heights), damit die Signal-Verdrahtung (siehe
        _on_sample_height_line_dragged) an GENAU einer Stelle passiert."""
        curve = self.shrinkage_plot.plot(pen=pg.mkPen(color, width=2), name=f"Probenhöhe {number}")
        entry = SampleHeightEntry(number, color, self.view_box, curve)
        # sigPositionChangeFinished statt sigDragged: feuert GENAU einmal
        # beim Loslassen (ein Undo-Schritt pro Geste, siehe
        # _on_sample_height_line_dragged), nicht laufend waehrend des
        # Ziehens -- ueber die Entry-Referenz gebunden (siehe dortiger
        # Kommentar), daher hier EINMALIG bei der Erzeugung statt in
        # _refresh_sample_height_rows.
        entry.line.sigPositionChangeFinished.connect(partial(self._on_sample_height_line_dragged, entry))
        return entry

    def _on_add_sample_height_clicked(self) -> None:
        if self.recording is None:
            QtWidgets.QMessageBox.information(self, "Keine Daten", "Bitte zuerst eine Messreihe laden.")
            return
        if len(self._sample_height_entries) >= MAX_SAMPLE_HEIGHT_COUNT:
            return
        self._push_undo_snapshot()
        number = self._sample_height_next_number
        self._sample_height_next_number += 1
        entry = self._create_sample_height_entry(number, _sample_height_color(number))
        rows, _cols = self.recording.shape
        entry.row = rows // 2
        self._update_sample_height_line_pos(entry)
        self._sample_height_entries.append(entry)
        self._set_active_layer_tab("shrinkage")
        self._refresh_sample_height_rows()
        self._recompute_sample_height_entry(entry)
        self._apply_sample_height_visibility()
        self.statusBar().showMessage(f"Probenhöhe „{entry.name}“ hinzugefügt.", 3000)

    def _on_sample_height_enabled_toggled(self, index: int, checked: bool) -> None:
        if not (0 <= index < len(self._sample_height_entries)):
            return
        self._push_undo_snapshot()
        self._sample_height_entries[index].enabled = checked
        self._apply_sample_height_visibility()

    def _on_sample_height_name_edited(self, index: int) -> None:
        if not (0 <= index < len(self._sample_height_entries)):
            return
        entry = self._sample_height_entries[index]
        text = entry.edit_name.text().strip() or f"Probenhöhe {entry.number}"
        if text == entry.name:
            return
        self._push_undo_snapshot()
        entry.set_name(text)

    def _on_sample_height_row_spin_changed(self, index: int, value: int) -> None:
        if not (0 <= index < len(self._sample_height_entries)):
            return
        # Feuert bei jedem Tastendruck/Pfeiltasten-Schritt -- gruppiert wie
        # die ROI-Positions-Spinboxen (siehe undo_ops.py), editingFinished
        # (angeschlossen in _refresh_sample_height_rows) beendet die
        # Eingabe-Sitzung wieder.
        self._begin_grouped_undo_edit()
        entry = self._sample_height_entries[index]
        entry.row = value
        self._update_sample_height_line_pos(entry)
        self._recompute_sample_height_entry(entry)

    def _on_sample_height_line_dragged(self, entry: SampleHeightEntry, line: pg.InfiniteLine) -> None:
        # Ueber die ENTRY-Referenz (nicht einen Listen-Index) gebunden,
        # einmalig bei der Erzeugung (siehe _on_add_sample_height_clicked)
        # -- anders als die uebrigen Zeilen-Widgets (siehe
        # _refresh_sample_height_rows) wird diese Verbindung NICHT bei jedem
        # Hinzufuegen/Entfernen neu aufgebaut, ein index-basierter partial()
        # wuerde also nach dem Entfernen eines DAVOR liegenden Eintrags auf
        # die falsche Probenhöhe zeigen.
        if entry not in self._sample_height_entries or self.recording is None:
            return
        # sigPositionChangeFinished feuert GENAU einmal beim Loslassen --
        # der Snapshot hier erfasst noch den ALTEN entry.row (wird erst
        # danach ueberschrieben), also genau den Zustand VOR diesem Drag
        # (dasselbe Ergebnis wie roi_shrink_area.sigRegionChangeStarted,
        # nur zeitlich am Ende statt am Anfang der Geste ausgeloest -- fuer
        # pg.InfiniteLine gibt es kein Start-Signal, siehe Modul-Docstring
        # undo_ops.py).
        self._push_undo_snapshot()
        rows, _cols = self.recording.shape
        entry.row = max(0, min(rows - 1, int(round(line.value() - 0.5))))
        if entry.spin_row is not None:
            entry.spin_row.blockSignals(True)
            entry.spin_row.setValue(entry.row)
            entry.spin_row.blockSignals(False)
        self._recompute_sample_height_entry(entry)
        self.statusBar().showMessage(f"„{entry.name}“ auf Zeile {entry.row} verschoben.", 3000)

    def _on_sample_height_color_clicked(self, index: int) -> None:
        if not (0 <= index < len(self._sample_height_entries)):
            return
        entry = self._sample_height_entries[index]
        color = QtWidgets.QColorDialog.getColor(QtGui.QColor(entry.color), self, "Farbe der Probenhöhe wählen")
        if not color.isValid():
            return
        self._push_undo_snapshot()
        entry.set_color(color.name())

    def _on_sample_height_remove_clicked(self, index: int) -> None:
        if not (0 <= index < len(self._sample_height_entries)):
            return
        self._push_undo_snapshot()
        entry = self._sample_height_entries.pop(index)
        entry.remove_from_view(self.view_box, self.shrinkage_plot)
        name = entry.name
        self._refresh_sample_height_rows()
        self.statusBar().showMessage(f"Probenhöhe „{name}“ entfernt.", 3000)

    def _reset_sample_heights_for_recording(self) -> None:
        """Bei jedem (Neu-)Laden einer Aufnahme (siehe frame_nav.py:
        _set_recording) -- Zeilen-Positionen der VORHERIGEN Aufnahme auf die
        neue Bildhoehe klemmen (NICHT verwerfen, gleiche Invariante wie bei
        ROIs, siehe roi_entry.py) und fuer die neuen Bilder neu berechnen.
        Liste/Namen/Farben bleiben ueber einen Reload hinweg erhalten."""
        if self.recording is None:
            for entry in self._sample_height_entries:
                entry.widths_px = None
                self._update_sample_height_curve(entry)
            self._apply_sample_height_visibility()
            return
        rows, _cols = self.recording.shape
        for entry in self._sample_height_entries:
            entry.row = max(0, min(rows - 1, entry.row))
            if entry.spin_row is not None:
                entry.spin_row.blockSignals(True)
                entry.spin_row.setRange(0, max(0, rows - 1))
                entry.spin_row.setValue(entry.row)
                entry.spin_row.blockSignals(False)
            self._update_sample_height_line_pos(entry)
        self._recompute_all_sample_heights()
        self._apply_sample_height_visibility()
