"""Frei skalierbares Rechteck-ROI (Region of Interest) für pyqtgraph-Bilder.

AdjustableROI erlaubt beliebige Seitenverhältnisse: An den vier Ecken wird
proportional skaliert (Breite/Höhe-Verhältnis bleibt erhalten), an den vier
Kanten wird nur die jeweils betroffene Dimension verändert. Für exakt
quadratische Startgrößen (Standard beim Platzieren neuer Messbereiche) genügt
es, beim Erzeugen size als Skalar zu übergeben.
"""
from __future__ import annotations

import pyqtgraph as pg


def bounds_px_for(x: float, y: float, w: float, h: float, grid_shape: tuple[int, int]) -> tuple[int, int, int, int]:
    """Liefert (row0, row1, col0, col1) als Integer-Grenzen, geclippt auf grid_shape.

    Eigenstaendige Funktion (statt nur Methode), damit auch fuer zeitlich
    interpolierte ROI-Rechtecke (siehe RoiEntry.interp_rect in main_window.py),
    die keinem echten ROI-Objekt entsprechen, dieselbe Grenzen-Logik gilt.
    """
    rows, cols = grid_shape
    col0 = int(round(x))
    row0 = int(round(y))
    col1 = int(round(x + w))
    row1 = int(round(y + h))
    # col0/row0 auf den letzten gueltigen Index (cols-1/rows-1) statt auf cols/
    # rows clippen: liegt das ROI komplett ausserhalb des Bildes, garantiert
    # das trotzdem eine gueltige 1-Pixel-Spanne am naechstgelegenen Bildrand,
    # statt eines leeren Slices (der bei .mean() stillschweigend NaN ergibt).
    col0 = max(0, min(col0, cols - 1))
    row0 = max(0, min(row0, rows - 1))
    col1 = max(col0 + 1, min(col1, cols))
    row1 = max(row0 + 1, min(row1, rows))
    return row0, row1, col0, col1


class AdjustableROI(pg.RectROI):
    # Untergrenze fuer Breite/Hoehe beim Ziehen der Handles. Ohne diese
    # Absicherung liesse sich ein ROI beliebig klein (sub-Pixel) ziehen,
    # waehrend die Breite/Hoehe-Spinboxen im rechten Panel (Range ab 1 px)
    # den Wert stillschweigend auf 1 klemmen -- das ROI und seine Anzeige
    # wuerden dann auseinanderlaufen.
    MIN_SIZE = 1.0

    def __init__(self, pos, size, pen, **kwargs):
        if not isinstance(size, (list, tuple)):
            size = [size, size]
        super().__init__(pos, list(size), pen=pen, **kwargs)
        # Ecken: proportionale Skalierung (Seitenverhaeltnis bleibt erhalten).
        self.addScaleHandle([1, 1], [0, 0], lockAspect=True)
        self.addScaleHandle([0, 0], [1, 1], lockAspect=True)
        self.addScaleHandle([1, 0], [0, 1], lockAspect=True)
        self.addScaleHandle([0, 1], [1, 0], lockAspect=True)
        # Kanten: nur die jeweils betroffene Dimension aendert sich.
        self.addScaleHandle([1, 0.5], [0, 0.5])
        self.addScaleHandle([0, 0.5], [1, 0.5])
        self.addScaleHandle([0.5, 0], [0.5, 1])
        self.addScaleHandle([0.5, 1], [0.5, 0])

        self._enforcing_min_size = False
        self.sigRegionChanged.connect(self._enforce_min_size)

    def _enforce_min_size(self) -> None:
        if self._enforcing_min_size:
            return
        w, h = self.size()
        clamped_w, clamped_h = max(w, self.MIN_SIZE), max(h, self.MIN_SIZE)
        if clamped_w != w or clamped_h != h:
            self._enforcing_min_size = True
            self.setSize([clamped_w, clamped_h], update=False)
            self._enforcing_min_size = False

    def bounds_px(self, grid_shape: tuple[int, int]) -> tuple[int, int, int, int]:
        (x, y) = self.pos()
        (w, h) = self.size()
        return bounds_px_for(x, y, w, h, grid_shape)
