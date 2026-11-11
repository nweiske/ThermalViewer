"""Regressionstests der Abschluss-Pruefung (Werkzeug-Ausschluss, Live-Timer waehrend Berechnung)."""
import numpy as np


def _arm_crosssection(mw):
    assert mw._start_crosssection_entry_tool("horizontal")
    assert mw._crosssection_entry_armed_direction == "horizontal"


def test_starting_ruler_cancels_armed_crosssection_tool(loaded_main_window):
    mw = loaded_main_window
    _arm_crosssection(mw)
    mw._start_ruler_tool()
    assert mw._crosssection_entry_armed_direction is None
    assert mw._ruler_armed


def test_starting_measurement_cancels_armed_crosssection_tool(loaded_main_window):
    mw = loaded_main_window
    mw._px_to_mm = 0.5
    _arm_crosssection(mw)
    assert mw._start_measurement_tool()
    assert mw._crosssection_entry_armed_direction is None
    assert mw._measurement_armed


def test_starting_sample_height_cancels_armed_crosssection_tool(loaded_main_window):
    mw = loaded_main_window
    _arm_crosssection(mw)
    assert mw._start_sample_height_tool()
    assert mw._crosssection_entry_armed_direction is None
    assert mw._sample_height_armed


def test_shrinkage_compute_restarts_live_watch_timer(loaded_main_window):
    mw = loaded_main_window
    rows, cols = mw.recording.shape
    mw.recording.frames = np.full_like(mw.recording.frames, 10.0)
    mw._watched_folder = mw.recording.paths[0].parent
    mw._live_watch_timer.start()
    try:
        mw._compute_shrinkage()
        assert mw._live_watch_timer.isActive()
    finally:
        mw._live_watch_timer.stop()
        mw._watched_folder = None
