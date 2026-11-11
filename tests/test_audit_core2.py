"""Regressionstests der zweiten Kern-Audit-Runde (Schwindungsmessung, Probenhoehen, data.py)."""
from __future__ import annotations

from datetime import datetime, timedelta

import numpy as np
import pytest
from qtpy import QtWidgets

from thermal_viewer.data import RecordingError, load_paths, parse_frame_text, ImportSettings
from thermal_viewer.main_window import shrinkage_ops as so


@pytest.fixture(autouse=True)
def _no_modal_boxes(monkeypatch):
    # Ein modaler Dialog wuerde den Offscreen-Test endlos blockieren.
    for name in ("warning", "information", "critical", "question"):
        monkeypatch.setattr(QtWidgets.QMessageBox, name, staticmethod(lambda *a, **k: None))


def _shrinking_window(mw, n: int = 5):
    rows, cols = 20, 60
    frames = np.full((n, rows, cols), 10.0, dtype=np.float32)
    for i in range(n):
        frames[i, 5:15, 10 + i:40 - i] = 50.0
    rec = mw.recording
    rec.frames = frames
    rec.paths = [rec.paths[0].with_name(f"Record_2026-01-01_12-00-{i:02d}.csv") for i in range(n)]
    rec.timestamps = [datetime(2026, 1, 1, 12, 0, 0) + timedelta(seconds=i) for i in range(n)]
    mw._set_recording(rec)
    mw.chk_shrinkage_enabled.setChecked(True)
    mw.roi_shrink_area.setPos((5, 3), update=False)
    mw.roi_shrink_area.setSize((50, 14))
    return mw


# --- reine Tracking-Funktionen --------------------------------------------------------------

@pytest.mark.parametrize("shape", [(1, 1), (1, 7), (7, 1), (2, 2), (3, 3), (40, 1)])
def test_tracking_survives_degenerate_boxes(shape):
    rng = np.random.default_rng(1)
    frames = rng.normal(20, 5, (3, *shape)).astype(np.float32)
    rows, cols = shape
    spans = so._track_sample_blob(frames, 0, rows, 0, cols, True, rows // 2, cols // 2)
    assert set(spans) == {0, 1, 2}
    for frame_spans in spans.values():
        assert all(value >= 0 for value in so._spans_metrics(frame_spans))
    widths, lefts, rights = so._track_sample_width_at_row(frames, rows // 2, 0, cols, True, cols // 2)
    assert len(widths) == len(lefts) == len(rights) == 3


def test_width_window_tracking_survives_nan_and_inf_pixels():
    frames = np.full((3, 20, 40), 10.0, dtype=np.float32)
    frames[:, 4:16, 10:30] = 50.0
    frames[:, 8, 12] = np.nan
    frames[:, 9, 25] = np.inf
    band = (6, 14, 4, 36)
    refs = [(6, 14, 0, 3), (6, 14, 37, 40)]
    warmer = so._detect_polarity_reference(frames[0], band, refs)
    spans, thresholds, touching = so._track_sample_width_in_window(frames, band, refs, warmer)
    assert warmer is True
    assert np.isfinite(thresholds).all()
    assert all(so._spans_metrics(spans[i])[1] > 0 for i in range(3))


# --- Fenster-Ebene ----------------------------------------------------------------------------

def test_compute_with_single_frame_and_degenerate_boxes(loaded_main_window):
    mw = _shrinking_window(loaded_main_window, n=1)
    mw._add_sample_height(8)
    for pos, size in (((500, 500), (10, 10)), ((-50, -50), (10, 10)), ((5, 5), (1, 1)), ((59.5, 19.5), (30, 30))):
        mw.roi_shrink_area.setPos(pos, update=False)
        mw.roi_shrink_area.setSize(size)
        mw._on_shrinkage_compute_clicked()
        assert len(mw._shrinkage_result["areas_px"]) == 1
        assert "0.00 %" in mw.lbl_shrinkage_result.text()
    mw.combo_shrinkage_mode.setCurrentIndex(mw.combo_shrinkage_mode.findData("width"))
    mw._on_shrinkage_compute_clicked()
    assert mw._shrinkage_result is not None
    assert len(mw._sample_height_entries[0].widths_px) == 1


def test_stale_shrinkage_result_with_wrong_length_is_discarded_not_plotted(loaded_main_window):
    mw = _shrinking_window(loaded_main_window)
    mw._on_shrinkage_compute_clicked()
    assert mw._shrinkage_result is not None
    # Aufnahme verlaengern, ohne dass irgendein Aufraeum-Pfad lief.
    rec = mw.recording
    rec.frames = np.concatenate([rec.frames, rec.frames[-1:]])
    rec.timestamps = rec.timestamps + [rec.timestamps[-1] + timedelta(seconds=1)]
    mw._update_shrinkage_curve()
    assert mw._shrinkage_result is None
    assert not mw.shrinkage_curve.isVisible()
    assert mw.lbl_shrinkage_result.text() == "Noch nicht berechnet."


def test_stale_sample_height_widths_with_wrong_length_do_not_crash_curve(loaded_main_window):
    mw = _shrinking_window(loaded_main_window)
    entry = mw._add_sample_height(8)
    assert len(entry.widths_px) == 5
    entry.widths_px = entry.widths_px[:3]
    mw._update_sample_height_curve(entry)
    assert entry.widths_px is None and entry.left_edges_px is None
    mw._rebuild_sample_height_edge_ticks(entry)


def test_out_of_range_excluded_index_does_not_break_curves(loaded_main_window):
    mw = _shrinking_window(loaded_main_window)
    mw._on_shrinkage_compute_clicked()
    entry = mw._add_sample_height(8)
    mw._excluded_frame_indices = {1, 99}
    mw._update_shrinkage_curve()
    mw._update_sample_height_curve(entry)
    x, y = mw.shrinkage_curve.getData()
    assert len(x) == len(y) == 4


def test_percent_baseline_skips_excluded_first_frame_in_curve_and_label(loaded_main_window):
    mw = _shrinking_window(loaded_main_window)
    mw._on_shrinkage_compute_clicked()
    widths = mw._shrinkage_result["areas_px"]
    mw._excluded_frame_indices = {0}
    mw._update_shrinkage_curve()
    _x, y = mw.shrinkage_curve.getData()
    assert y[0] == pytest.approx(0.0)
    assert y[-1] == pytest.approx((widths[1] - widths[-1]) / widths[1] * 100.0)


def test_all_but_one_frame_excluded_gives_zero_shrinkage(loaded_main_window):
    mw = _shrinking_window(loaded_main_window)
    mw._on_shrinkage_compute_clicked()
    entry = mw._add_sample_height(8)
    mw._excluded_frame_indices = {0, 1, 2, 3}
    mw._update_shrinkage_curve()
    mw._update_sample_height_curve(entry)
    assert "0.00 %" in mw.lbl_shrinkage_result.text()
    x, y = mw.shrinkage_curve.getData()
    assert len(x) == 1 and y[0] == pytest.approx(0.0)


def test_live_watch_timer_is_restored_even_if_tracking_raises(loaded_main_window, monkeypatch):
    mw = _shrinking_window(loaded_main_window)
    mw._live_watch_timer.start()

    def boom(*_a, **_k):
        raise RuntimeError("kaputt")

    monkeypatch.setattr(so, "_track_sample_blob", boom)
    with pytest.raises(RuntimeError):
        mw._on_shrinkage_compute_clicked()
    assert mw._live_watch_timer.isActive()
    assert QtWidgets.QApplication.overrideCursor() is None
    mw._live_watch_timer.stop()


def test_live_watch_timer_is_restored_even_if_process_events_raises(loaded_main_window, monkeypatch):
    mw = _shrinking_window(loaded_main_window)
    mw._live_watch_timer.start()

    def boom(*_a, **_k):
        raise RuntimeError("kaputt")

    monkeypatch.setattr(QtWidgets.QApplication, "processEvents", staticmethod(boom))
    with pytest.raises(RuntimeError):
        mw._on_shrinkage_compute_clicked()
    monkeypatch.undo()
    assert mw._live_watch_timer.isActive()
    assert QtWidgets.QApplication.overrideCursor() is None
    mw._live_watch_timer.stop()


# --- data.py ----------------------------------------------------------------------------------

def _write_csv(path, rows):
    path.write_text("\n".join(";".join(f"{v:.1f}".replace(".", ",") for v in row) + ";" for row in rows), encoding="utf-8")


def test_load_paths_stack_keeps_order_values_dtype_and_skips_odd_shape(tmp_path):
    t0 = datetime(2026, 1, 1, 12, 0, 0)
    names = []
    for i in range(4):
        name = tmp_path / f"Record_{(t0 + timedelta(seconds=i)).strftime('%Y-%m-%d_%H-%M-%S')}.csv"
        # Dateien 0,1,3 haben 2x3 Pixel, Datei 2 nur 1x3 (abweichende Aufloesung).
        rows = [[i, i + 1, i + 2]] if i == 2 else [[i, i + 1, i + 2], [i + 10, i + 11, i + 12]]
        _write_csv(name, rows)
        names.append(name)
    rec = load_paths(list(reversed(names)))
    assert rec.frames.shape == (3, 2, 3)
    assert rec.frames.dtype == np.float32
    assert rec.frames.flags["C_CONTIGUOUS"]
    assert [p.name for p in rec.paths] == [names[0].name, names[1].name, names[3].name]
    assert rec.frames[2, 1, 0] == pytest.approx(13.0)
    assert len(rec.skipped_files) == 1 and rec.skipped_files[0][0] == names[2]


def test_load_paths_single_frame(tmp_path):
    name = tmp_path / "Record_2026-01-01_12-00-00.csv"
    _write_csv(name, [[1, 2], [3, 4]])
    rec = load_paths([name])
    assert rec.n_frames == 1 and rec.frames.shape == (1, 2, 2)


@pytest.mark.parametrize("text", ["", "\n\n", ";;;\n;\n"])
def test_parse_frame_text_rejects_empty_content(text):
    with pytest.raises(RecordingError):
        parse_frame_text(text)


def test_parse_frame_text_ragged_and_hash_values_are_reported_not_crashing():
    with pytest.raises(RecordingError):
        parse_frame_text("1,0;2,0;3,0\n1,0;2,0\n")
    with pytest.raises(RecordingError):
        parse_frame_text("1,0;2,0 # Kommentar\n")
    arr = parse_frame_text("1.5\t2.5\n3.5\t4.5\n", ImportSettings(delimiter="\t", decimal_separator="."))
    assert arr.shape == (2, 2) and arr[1, 1] == pytest.approx(4.5)
