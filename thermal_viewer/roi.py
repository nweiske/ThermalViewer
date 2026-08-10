"""Quadratische ROI (Region of Interest) für pyqtgraph-Bilder.

pyqtgraph bringt kein natives Quadrat-ROI mit; RectROI erlaubt beliebige
Seitenverhältnisse. SquareROI erzwingt beim Ziehen des Skalier-Handles
gleiche Breite/Höhe, bietet aber weiterhin freies Verschieben und
Größenänderung per Maus.
"""
from __future__ import annotations

import pyqtgraph as pg


class SquareROI(pg.RectROI):
    def __init__(self, pos, size, pen, **kwargs):
        super().__init__(pos, [size, size], pen=pen, **kwargs)
        self.addScaleHandle([1, 1], [0, 0])
        self._enforcing = False
        self.sigRegionChanged.connect(self._enforce_square)

    def _enforce_square(self):
        if self._enforcing:
            return
        w, h = self.size()
        if abs(w - h) > 1e-3:
            self._enforcing = True
            side = max(w, h, 1.0)
            self.setSize([side, side], update=False)
            self._enforcing = False

    def bounds_px(self, grid_shape: tuple[int, int]) -> tuple[int, int, int, int]:
        """Liefert (row0, row1, col0, col1) als Integer-Grenzen, geclippt auf grid_shape."""
        (x, y) = self.pos()
        (w, h) = self.size()
        rows, cols = grid_shape
        col0 = int(round(x))
        row0 = int(round(y))
        col1 = int(round(x + w))
        row1 = int(round(y + h))
        col0 = max(0, min(col0, cols))
        col1 = max(col0 + 1, min(col1, cols))
        row0 = max(0, min(row0, rows))
        row1 = max(row0 + 1, min(row1, rows))
        return row0, row1, col0, col1
