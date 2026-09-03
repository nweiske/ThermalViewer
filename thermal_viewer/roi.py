"""Frei skalierbares Rechteck-ROI (Region of Interest) für pyqtgraph-Bilder.

AdjustableROI erlaubt beliebige Seitenverhältnisse: An den vier Ecken wird
proportional skaliert (Breite/Höhe-Verhältnis bleibt erhalten), an den vier
Kanten wird nur die jeweils betroffene Dimension verändert. Für exakt
quadratische Startgrößen (Standard beim Platzieren neuer Messbereiche) genügt
es, beim Erzeugen size als Skalar zu übergeben.
"""
from __future__ import annotations

from functools import lru_cache

import numpy as np
import pyqtgraph as pg
from qtpy import QtCore, QtGui


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


@lru_cache(maxsize=256)
def _elliptical_mask_for_shape(h: int, w: int) -> np.ndarray:
    """Die eigentliche Ellipsen-Maskenberechnung -- haengt NUR von der
    Groesse (h, w) ab, nicht von der Position (row0/col0). Gecacht, weil
    ein zeitlich interpolierter, "als Kreis behandelter" Messbereich
    (_recompute_curves in main_window.py) diese Maske sonst pro Frame
    (potenziell tausende Male je Kurvenberechnung, z.B. bei jedem
    Zwischenschritt eines ROI-Drags) neu aus np.ogrid heraus aufbauen
    wuerde, obwohl die gerundete Pixel-Breite/-Hoehe ueber viele
    aufeinanderfolgende Frames hinweg meist unveraendert bleibt."""
    cy, cx = (h - 1) / 2.0, (w - 1) / 2.0
    ry, rx = h / 2.0, w / 2.0
    yy, xx = np.ogrid[:h, :w]
    return ((xx - cx) / rx) ** 2 + ((yy - cy) / ry) ** 2 <= 1.0


def elliptical_mask(row0: int, row1: int, col0: int, col1: int) -> np.ndarray:
    """Boolesche Maske der Groesse (row1-row0, col1-col0): True fuer Pixel
    innerhalb der Ellipse, die genau in dieses Rechteck eingeschrieben ist
    (Mittelpunkt = Rechteck-Mittelpunkt, Halbachsen = halbe Breite/Hoehe) --
    fuer die Temperaturmittelung eines "als Kreis behandelten" Messbereichs
    (AdjustableROI.is_circular), damit nur Pixel innerhalb des Kreises/der
    Ellipse einfliessen, nicht die gesamte rechteckige Bounding-Box."""
    h = max(1, row1 - row0)
    w = max(1, col1 - col0)
    return _elliptical_mask_for_shape(h, w)


def average_value(block: np.ndarray, row0: int, row1: int, col0: int, col1: int, circular: bool):
    """Mittelt block (2D: ein Frame-Ausschnitt, oder 3D: mehrere Frames mit
    Achse 0 = Zeit) ueber die raeumlichen Achsen -- rechteckig (gesamte
    Bounding-Box) oder, wenn circular=True, nur ueber die Pixel innerhalb
    der per elliptical_mask() eingeschriebenen Ellipse. Gemeinsame Stelle
    fuer alle ROI-Mittelwertbildungen (Live-Beschriftung, Kurvenberechnung),
    damit "als Kreis behandeln" ueberall konsistent wirkt."""
    if not circular:
        return block.mean() if block.ndim == 2 else block.mean(axis=(1, 2))
    mask = elliptical_mask(row0, row1, col0, col1)
    if block.ndim == 2:
        return block[mask].mean()
    return block[:, mask].mean(axis=1)


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

        # "Als Kreis behandeln": zeichnet eine in die Bounding-Box (Breite x
        # Hoehe, weiterhin ueber dieselben Ecken-/Kanten-Handles frei
        # einstellbar) eingeschriebene Ellipse statt eines Rechtecks, UND
        # die Temperaturmittelung (siehe average_value/elliptical_mask)
        # beruecksichtigt nur Pixel innerhalb dieser Ellipse. Bewusst keine
        # eigene ROI-Klasse (z.B. pg.EllipseROI) -- dieselben Handles/
        # dieselbe bounds_px()-Bounding-Box bleiben erhalten, nur die
        # Darstellung/Mittelung aendert sich, per Checkbox jederzeit
        # umschaltbar ohne die Messbereich-Geometrie zu verlieren.
        self.is_circular = False

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

    def paint(self, p, opt, widget) -> None:
        if not self.is_circular:
            super().paint(p, opt, widget)
            return
        # Kopie von pg.ROI.paint(), nur drawRect -> drawEllipse (siehe
        # is_circular oben) -- Handles/Bounding-Box bleiben unveraendert
        # rechteckig, nur der gezeichnete Umriss wird zur eingeschriebenen
        # Ellipse.
        r = QtCore.QRectF(0, 0, self.state["size"][0], self.state["size"][1]).normalized()
        p.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing, self._antialias)
        p.setPen(self.currentPen)
        p.translate(r.left(), r.top())
        p.scale(r.width(), r.height())
        p.drawEllipse(QtCore.QRectF(0, 0, 1, 1))
