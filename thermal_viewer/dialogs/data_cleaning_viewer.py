"""Interaktive Bildvorschau für den Bereinigungs-Dialog -- Referenzpunkte
werden AUSSCHLIESSLICH hier gesetzt/verschoben (Nutzerwunsch: "darin möchte
ich auch direkt die Triggerpunkte setzen können"), NICHT mehr im
Hauptfenster-Bild (das seit diesem Umbau ohnehin NIE ein von der Rohdaten-
Bereinigung ausgeblendetes Bild zeigen darf, siehe main_window/frame_nav.py:
_show_frame). Ein Klick auf freie Bildfläche fügt sofort einen neuen Punkt
hinzu (kein Arm/Disarm-Knopf mehr nötig); bestehende (aktivierte) Punkte
lassen sich per Ziehen verschieben, genau wie zuvor im Hauptfenster.

Übernimmt Farbverlauf des Hauptfensters nur LESEND bei jedem show_frame()-
Aufruf; die Pegel (Min/Max) werden bewusst PRO BILD automatisch ermittelt
(autoLevels=True) statt die aktuellen Hauptfenster-Pegel zu übernehmen --
dafür maximiert die Vorschau den Kontrast jedes einzelnen Bildes (gerade
beim Suchen nach Ausreißern hilfreich) und ist unabhängig davon nutzbar, ob
das Hauptfenster bereits ein Bild angezeigt hat (siehe Punkt 1: die
Bereinigung MUSS abgeschlossen sein, BEVOR das Hauptfenster überhaupt ein
Bild zeigt)."""
from __future__ import annotations

from functools import partial

import numpy as np
import pyqtgraph as pg
from qtpy import QtCore, QtGui, QtWidgets


def _point_label_text(number: int, delta: float | None) -> str:
    """Text der Punkt-Beschriftung im Bild -- Punktnummer + Temperatur-
    differenz zum VORHERIGEN Bild (derselbe Wert, der auch zur Ausreißer-
    Erkennung herangezogen wird, siehe main_window/data_cleaning_ops.py:
    _compute_cleaning_candidates). "–" beim allerersten Bild (kein Vorbild)."""
    delta_text = f"{delta:.1f}°C" if delta is not None else "–"
    return f"{number}: ΔT={delta_text}"


class CleaningPreviewViewer(QtWidgets.QWidget):
    def __init__(self, main_window, on_points_changed) -> None:
        """on_points_changed: aufrufbarer Callback (die Dialog-Methode
        refresh_points als gebundene Methode -- KEIN Closure, dasselbe
        Muster wie die bestehenden partial(self._on_x, i)-Verbindungen im
        Projekt), aufgerufen nach jeder Punkt-Änderung (hinzufügen/
        verschieben), damit der Dialog seine Liste/ΔT-Anzeigen/Kandidaten
        neu aufbaut."""
        super().__init__()
        self._mw = main_window
        self._on_points_changed = on_points_changed
        self._current_frame_index = 0
        self._point_items: list[dict] = []
        self._recording = None

        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        # Deutlich groesser als die vormalige reine "Vorschau" -- soll
        # analog zum Hauptfenster gross links stehen (Nutzerwunsch).
        self.glw = pg.GraphicsLayoutWidget()
        self.glw.setMinimumSize(560, 420)
        self.plot_item = self.glw.addPlot(row=0, col=0)
        self.plot_item.setAspectLocked(True)
        self.plot_item.invertY(True)
        self.plot_item.showGrid(x=False, y=False)
        self.view_box = self.plot_item.getViewBox()
        self.view_box.setMouseEnabled(x=False, y=False)
        self.view_box.setMenuEnabled(False)

        self.image_item = pg.ImageItem()
        self.plot_item.addItem(self.image_item)
        layout.addWidget(self.glw)

        self.lbl_frame_info = QtWidgets.QLabel("Kein Bild geladen.")
        layout.addWidget(self.lbl_frame_info)

        # Klick auf freie Bildflaeche fuegt sofort einen Punkt hinzu --
        # bewusst KEIN Live-Cursor-Hover (eigenständiges Fenster, siehe
        # Modul-Docstring).
        self.plot_item.scene().sigMouseClicked.connect(self._on_scene_clicked)

    def set_recording(self, recording) -> None:
        """Muss bei jeder (neu geladenen) Aufnahme erneut aufgerufen werden
        (siehe data_cleaning.py:refresh_points) -- setzt den sichtbaren
        Bildausschnitt passend zur Bildgroesse."""
        self._recording = recording
        if recording is not None:
            rows, cols = recording.shape
            self.view_box.setRange(xRange=(0, cols), yRange=(0, rows), padding=0.02)
        else:
            self.image_item.clear()
            self.lbl_frame_info.setText("Kein Bild geladen.")
            self._clear_points()

    def show_frame(self, frame_index: int) -> None:
        self._current_frame_index = frame_index
        if self._recording is None or not (0 <= frame_index < self._recording.n_frames):
            self.image_item.clear()
            self.lbl_frame_info.setText("Kein Bild geladen.")
            return
        frame = self._recording.frames[frame_index]
        self.image_item.setImage(frame, autoLevels=True)
        # Der Farbverlauf haengt am Histogramm-Gradienten des Hauptfensters
        # (siehe main_window/frame_nav.py:_apply_colormap), NICHT an dessen
        # image_item selbst -- image_item.getColorMap() liefert deshalb
        # immer None und waere hier der falsche Weg.
        colormap = self._mw.histogram.gradient.colorMap()
        if colormap is not None:
            self.image_item.setColorMap(colormap)

        excluded = frame_index in self._mw._excluded_frame_indices
        ts = self._recording.timestamps[frame_index]
        status = " -- AUSGEBLENDET (Rohdaten-Bereinigung)" if excluded else ""
        self.lbl_frame_info.setText(
            f"Bild {frame_index + 1}/{self._recording.n_frames} -- "
            f"{ts.strftime('%Y-%m-%d %H:%M:%S')}{status}"
        )
        self.draw_points()

    def _clear_points(self) -> None:
        for item in self._point_items:
            for key in ("dot", "label", "area"):
                obj = item.get(key)
                if obj is not None:
                    self.view_box.removeItem(obj)
        self._point_items = []

    def draw_points(self) -> None:
        """Baut alle Punkt-Markierungen NEU auf -- jeder AKTIVIERTE Punkt
        (siehe main_window/data_cleaning_ops.py:_set_cleaning_point_enabled)
        bekommt ein draggable pg.TargetItem (direkt im Bild verschiebbar)
        inkl. ΔT-Beschriftung zum aktuell angezeigten Bild und optionalem
        Mittelungsbereich-Rechteck; deaktivierte Punkte werden NICHT
        gezeichnet (bleiben aber in der Liste des Dialogs bestehen).
        self._point_items haelt pro Punkt ein dict {"dot", "label", "area"}
        (Werte None fuer deaktivierte Punkte) -- gleiche Reihenfolge wie
        self._mw._cleaning_points, damit _on_point_dragged() den Index
        eindeutig zuordnen kann."""
        self._clear_points()
        if self._recording is None:
            return
        rows, cols = self._recording.shape
        for i, (col, row, _logic, enabled) in enumerate(self._mw._cleaning_points):
            if not enabled:
                self._point_items.append({"dot": None, "label": None, "area": None})
                continue

            area = None
            if self._mw._cleaning_kernel_size > 1 and self._mw._cleaning_show_kernel_area:
                r = max(0, min(rows - 1, row))
                c = max(0, min(cols - 1, col))
                row0, row1, col0, col1 = self._mw._cleaning_point_bounds(r, c)
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
            dot.sigPositionChangeFinished.connect(partial(self._on_point_dragged, i))
            # Beschriftung folgt dem Marker schon WAEHREND des Ziehens
            # visuell mit (sigPositionChanged feuert laufend, anders als
            # sigPositionChangeFinished oben) -- KEINE Neuberechnung dabei,
            # die bleibt bis zum Loslassen an der alten Stelle gepinnt.
            dot.sigPositionChanged.connect(partial(self._on_point_drag_live, i))
            self.view_box.addItem(dot)

            delta = self._mw._cleaning_point_delta(i, self._current_frame_index)
            label = pg.TextItem(text=_point_label_text(i + 1, delta), color="#facc15", anchor=(0.5, 1.3))
            label.setPos(col + 0.5, row + 0.5)
            label.setZValue(12)
            self.view_box.addItem(label)

            self._point_items.append({"dot": dot, "label": label, "area": area})

    def _on_point_drag_live(self, index: int, target) -> None:
        if not (0 <= index < len(self._point_items)):
            return
        label = self._point_items[index].get("label")
        if label is not None:
            label.setPos(target.pos())

    def _on_point_dragged(self, index: int, target) -> None:
        if not (0 <= index < len(self._mw._cleaning_points)) or self._recording is None:
            return
        pos = target.pos()
        rows, cols = self._recording.shape
        col = max(0, min(cols - 1, int(np.floor(pos.x()))))
        row = max(0, min(rows - 1, int(np.floor(pos.y()))))
        _old_col, _old_row, logic, enabled = self._mw._cleaning_points[index]
        # sigPositionChangeFinished feuert GENAU einmal beim Loslassen (siehe
        # Modul-Docstring/undo_ops.py) -- ein einfacher Push hier reicht,
        # ohne die Zwischenschritte der laufenden Geste (sigPositionChanged/
        # _on_point_drag_live, rein visuell) einzeln zu beruecksichtigen.
        self._mw._push_undo_snapshot()
        self._mw._cleaning_points[index] = (col, row, logic, enabled)
        self.draw_points()
        self._on_points_changed()

    def _on_scene_clicked(self, event) -> None:
        """Klick auf freie Bildflaeche fuegt sofort einen neuen Punkt hinzu
        (Nutzerwunsch: kein Arm/Disarm-Knopf mehr) -- liegt der Klick NAH an
        einem bereits bestehenden (aktivierten) Punkt, wird NICHTS
        hinzugefuegt (verhindert Duplikate beim bloßen Anklicken eines
        bestehenden Punkts, z.B. zu Beginn eines Drags)."""
        if self._recording is None or event.button() != QtCore.Qt.LeftButton:
            return
        scene_pos = event.scenePos()
        if not self.view_box.sceneBoundingRect().contains(scene_pos):
            return
        view_pos = self.view_box.mapSceneToView(scene_pos)
        rows, cols = self._recording.shape
        col = int(np.floor(view_pos.x()))
        row = int(np.floor(view_pos.y()))
        if not (0 <= row < rows and 0 <= col < cols):
            return
        if self._near_existing_point(col, row):
            return
        self._mw._push_undo_snapshot()
        self._mw._cleaning_points.append((col, row, "and", True))
        self.draw_points()
        self._on_points_changed()

    def _near_existing_point(self, col: int, row: int, tolerance: float = 3.0) -> bool:
        for existing_col, existing_row, _logic, enabled in self._mw._cleaning_points:
            if enabled and (existing_col - col) ** 2 + (existing_row - row) ** 2 <= tolerance ** 2:
                return True
        return False
