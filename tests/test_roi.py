"""Tests fuer thermal_viewer/roi.py: Pixel-Grenzen-Berechnung und die
Mindestgroessen-Absicherung von AdjustableROI."""
from __future__ import annotations

from thermal_viewer.roi import AdjustableROI, bounds_px_for


def test_bounds_px_for_inside_image():
    row0, row1, col0, col1 = bounds_px_for(x=2, y=3, w=4, h=5, grid_shape=(20, 20))
    assert (row0, row1, col0, col1) == (3, 8, 2, 6)


def test_bounds_px_for_clips_negative_position_to_image_edge():
    row0, row1, col0, col1 = bounds_px_for(x=-5, y=-5, w=3, h=3, grid_shape=(10, 10))
    assert row0 == 0
    assert col0 == 0
    assert row1 > row0
    assert col1 > col0


def test_bounds_px_for_never_returns_empty_slice_when_fully_outside_image():
    # Bugfix-Invariante: ein ROI komplett ausserhalb des Bildes darf NIE zu
    # row0==row1/col0==col1 fuehren (sonst ergibt .mean() stillschweigend NaN).
    row0, row1, col0, col1 = bounds_px_for(x=1000, y=1000, w=5, h=5, grid_shape=(10, 10))
    assert row1 > row0
    assert col1 > col0
    assert row0 <= 9
    assert col0 <= 9


def test_bounds_px_for_clips_overhang_at_far_edge():
    row0, row1, col0, col1 = bounds_px_for(x=8, y=8, w=10, h=10, grid_shape=(10, 10))
    assert row1 == 10
    assert col1 == 10


def test_adjustable_roi_enforces_minimum_size(qapp):
    roi = AdjustableROI(pos=(0, 0), size=5, pen="#ff0000")
    roi.setSize([0.1, 0.1])
    width, height = roi.size()
    assert width >= AdjustableROI.MIN_SIZE
    assert height >= AdjustableROI.MIN_SIZE


def test_adjustable_roi_accepts_scalar_size(qapp):
    roi = AdjustableROI(pos=(0, 0), size=7, pen="#00ff00")
    width, height = roi.size()
    assert width == 7
    assert height == 7
