"""Regressionstests der Kern-Audit-Fixes (Schwindungsmessung, data.py)."""
from __future__ import annotations

from datetime import datetime

import numpy as np
import pytest

from thermal_viewer.data import (
    Recording,
    RecordingError,
    compile_filename_template,
    load_paths,
    parse_frame_text,
    parse_timestamp,
)
from thermal_viewer.main_window import shrinkage_ops as so


def _sample_frames(n: int = 3, rows: int = 20, cols: int = 40) -> np.ndarray:
    frames = np.full((n, rows, cols), 10.0, dtype=np.float32)
    frames[:, 4:16, 10:30] = 50.0
    return frames


def test_horizontal_blur_does_not_spread_nan_over_the_rest_of_the_row():
    row = np.full((1, 20), 10.0, dtype=np.float32)
    row[0, 5] = np.nan
    blurred = so._horizontal_blur_region(row, 3)
    assert np.isfinite(blurred).all()
    assert blurred[0, 15] == pytest.approx(10.0)


def test_snap_to_gradient_peak_ignores_non_finite_gradient():
    grad = np.array([0.0, np.nan, 1.0, 0.0, 0.0])
    assert so._snap_to_gradient_peak(grad, 2, 2, 5) == 2


def test_track_sample_blob_survives_nan_pixels():
    frames = _sample_frames()
    frames[:, 6, 3] = np.nan
    frames[:, 8, 20] = np.nan
    spans = so._track_sample_blob(frames, 0, 20, 0, 40, True, 10, 20)
    for idx in range(3):
        area, _rect, _round = so._spans_metrics(spans[idx])
        assert area > 0
        widths = [c1 - c0 for c0, c1 in spans[idx].values()]
        assert np.median(widths) == pytest.approx(20, abs=2)


def test_track_sample_width_in_window_survives_nan_pixels():
    frames = _sample_frames()
    frames[:, 9, 12] = np.nan
    band = (8, 12, 4, 36)
    refs = [(2, 18, 0, 3), (2, 18, 37, 40)]
    spans, _thresholds, _touching = so._track_sample_width_in_window(frames, band, refs, True)
    assert all(spans[i] for i in range(3))


def test_detect_polarity_area_ignores_nan_pixels():
    frame = np.full((20, 40), 10.0, dtype=np.float32)
    frame[4:16, 10:30] = 50.0
    frame[10, 20] = np.nan
    assert so._detect_polarity_area(frame, 0, 20, 0, 40) is True


def test_detect_polarity_reference_ignores_nan_pixels():
    frame = np.full((20, 40), 30.0, dtype=np.float32)
    frame[8:12, 10:30] = 5.0
    frame[9, 15] = np.nan
    band = (8, 12, 4, 36)
    refs = [(2, 18, 0, 3), (2, 18, 37, 40)]
    assert so._detect_polarity_reference(frame, band, refs) is False


def test_parse_timestamp_invalid_date_falls_back_to_mtime(tmp_path):
    path = tmp_path / "Record_2024-13-45_25-61-61.csv"
    path.write_text("1;2\n", encoding="utf-8")
    ts = parse_timestamp(path)
    assert isinstance(ts, datetime)
    assert ts.year >= 2000


def test_load_paths_survives_file_name_with_impossible_date(tmp_path):
    good = tmp_path / "Record_2024-01-01_10-00-00.csv"
    bad = tmp_path / "Record_2024-13-45_25-61-61.csv"
    for p in (good, bad):
        p.write_text("1,5;2,5\n3,5;4,5\n", encoding="utf-8")
    recording = load_paths([good, bad])
    assert recording.n_frames == 2


def test_filename_template_literal_percent_sign_is_escaped_for_strptime():
    pattern, fmt = compile_filename_template("100%_YYYY-MM-DD_hh-mm-ss")
    match = pattern.search("100%_2024-03-05_10-20-30")
    assert match is not None
    assert datetime.strptime(match.group(1), fmt) == datetime(2024, 3, 5, 10, 20, 30)


def test_parse_frame_text_rejects_hash_instead_of_silently_truncating():
    with pytest.raises(RecordingError):
        parse_frame_text("1,5;2,5 # Kommentar\n3,5;4,5\n")


def test_unix_seconds_survives_timestamps_before_1970():
    recording = Recording(
        timestamps=[datetime.min, datetime(2024, 1, 1)],
        frames=np.zeros((2, 2, 2), dtype=np.float32),
    )
    seconds = recording.unix_seconds()
    assert len(seconds) == 2 and seconds[0] < seconds[1]
