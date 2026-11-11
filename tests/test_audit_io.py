"""Regressionstests der I/O-Audit-Runde (Projektdatei laden/speichern, Undo,
Live-Ordner-Ueberwachung, Aufnahme-Wechsel)."""
from __future__ import annotations

import json
from datetime import datetime, timedelta

from qtpy import QtWidgets


def _load_project_file(mw, monkeypatch, tmp_path, payload, *, raw: bytes | None = None):
    path = tmp_path / "audit.tvproj"
    if raw is not None:
        path.write_bytes(raw)
    else:
        path.write_text(payload if isinstance(payload, str) else json.dumps(payload), encoding="utf-8")
    monkeypatch.setattr(QtWidgets.QFileDialog, "getOpenFileName", staticmethod(lambda *a, **k: (str(path), "")))
    errors = []
    monkeypatch.setattr(QtWidgets.QMessageBox, "critical", staticmethod(lambda *a, **k: errors.append(a)))
    monkeypatch.setattr(QtWidgets.QMessageBox, "warning", staticmethod(lambda *a, **k: 0))
    monkeypatch.setattr(QtWidgets.QMessageBox, "information", staticmethod(lambda *a, **k: 0))
    mw._load_project()
    return errors


def _base(mw, **extra):
    data = {
        "format_version": 2,
        "quellordner": str(mw.recording.paths[0].parent),
        "bild_groesse_px": {"zeilen": mw.recording.shape[0], "spalten": mw.recording.shape[1]},
    }
    data.update(extra)
    return data


def test_load_project_rejects_binary_and_non_object_json(loaded_main_window, tmp_path, monkeypatch):
    mw = loaded_main_window
    errors = _load_project_file(mw, monkeypatch, tmp_path, None, raw=b"\xff\xfe\x00\x80binary")
    assert len(errors) == 1
    errors = _load_project_file(mw, monkeypatch, tmp_path, "[1, 2, 3]")
    assert len(errors) == 1
    errors = _load_project_file(mw, monkeypatch, tmp_path, "null")
    assert len(errors) == 1


def test_load_project_ignores_nonfinite_and_huge_numbers(loaded_main_window, tmp_path, monkeypatch):
    mw = loaded_main_window
    text = json.dumps(_base(mw, level_mode="manual", px_zu_mm=1.0)).replace(
        '"px_zu_mm": 1.0', '"px_zu_mm": NaN, "level_min": NaN, "level_max": 1e400, '
        '"bereinigung_schwellenwert": Infinity, "bereinigung_punkte": [{"x": 1e400, "y": 3}, {"x": 99, "y": -5}], '
        '"probenhoehen": [{"nummer": 1e400}, {"nummer": 3, "zeile": Infinity}], '
        '"querschnitte": [{"nummer": 1e400, "richtung": "horizontal"}], '
        '"querschnitt_y_achse": {"manuell": true, "y_min": 0, "y_max": ' + "9" * 400 + "}"
    )
    errors = _load_project_file(mw, monkeypatch, tmp_path, text)
    assert not errors
    assert mw._px_to_mm is None
    assert mw._cleaning_threshold == 5.0
    # Punkt mit unendlicher Koordinate verworfen, der andere auf das Bild geklemmt.
    assert mw._cleaning_points == [(mw.recording.shape[1] - 1, 0, "and", True)]
    assert len(mw._sample_height_entries) == 1
    assert mw._sample_height_entries[0].row == 0
    assert mw._crosssection_entries == []
    assert mw._crosssection_y_manual is False


def test_load_project_tolerates_null_lists_and_huge_measurements(loaded_main_window, tmp_path, monkeypatch):
    mw = loaded_main_window
    data = _base(mw, px_zu_mm=0.5, rois=None, messungen=None)
    assert not _load_project_file(mw, monkeypatch, tmp_path, data)
    data = _base(mw, px_zu_mm=0.5, messungen=[
        {"start": {"x": 0, "y": 0}, "ende": {"x": 1e300, "y": 1e300}},
        {"start": {"x": 0, "y": 0}, "ende": {"x": 5, "y": 0}, "beschriftung_versatz": {"x": float("nan"), "y": 1}},
    ])
    assert not _load_project_file(mw, monkeypatch, tmp_path, data)
    assert len(mw.measurements) == 1
    assert mw.measurements[0].label_offset is None


def test_load_project_rejects_nonfinite_interpolation_keyframe(loaded_main_window, tmp_path, monkeypatch):
    mw = loaded_main_window
    text = json.dumps(_base(mw, rois=[{
        "index": 0, "platziert": True, "mittelpunkt": {"x": 5, "y": 5}, "breite_px": 4, "hoehe_px": 4,
        "interpolation_aktiv": True,
        "interpolation_start": {"x": 1, "y": 1, "breite_px": 2, "hoehe_px": 2, "frame": 0},
        "interpolation_ende": {"x": "NANX", "y": 1, "breite_px": 2, "hoehe_px": 2, "frame": 4},
    }])).replace('"NANX"', "NaN")
    _load_project_file(mw, monkeypatch, tmp_path, text)
    entry = mw.roi_entries[0]
    assert entry.interp_start is None and entry.interp_end is None
    assert entry.interp_enabled is False


def test_undo_of_roi_placement_unplaces_the_roi(loaded_main_window):
    mw = loaded_main_window
    entry = mw.roi_entries[0]
    assert not entry.placed
    mw._push_undo_snapshot()
    entry.place(8, 8, 4, 4)
    mw._recompute_curves(entries=[entry])
    assert entry.placed and entry.roi.isVisible()

    mw._on_undo()
    assert not entry.placed
    assert not entry.roi.isVisible()
    assert not entry.label.isVisible()
    x_data = entry.curve.getData()[0]
    assert x_data is None or len(x_data) == 0
    assert not any(e.placed for e in mw.roi_entries)

    mw._on_redo()
    assert entry.placed and entry.roi.isVisible()


def _set_ruler(mw, start, end, mm):
    mw._create_or_move_ruler_line(start, end)
    mw._ruler_mm_value = mm
    mw._px_to_mm = mm / ((end[0] - start[0]) ** 2 + (end[1] - start[1]) ** 2) ** 0.5
    mw._update_ruler_text_position()


def test_ruler_line_survives_save_load_round_trip(loaded_main_window, tmp_path, monkeypatch):
    mw = loaded_main_window
    _set_ruler(mw, (2.0, 3.0), (12.0, 3.0), 5.0)
    state = mw._build_project_state_dict()
    assert state["massstab_linie"]["mm"] == 5.0

    mw._clear_ruler_scale()
    assert mw._ruler_mm_value is None
    _load_project_file(mw, monkeypatch, tmp_path, state)
    assert mw._ruler_mm_value == 5.0
    assert mw._ruler_line is not None and mw._ruler_line.isVisible()
    assert mw._ruler_text is not None
    # Nach dem Laden laesst sich die Länge wie gewohnt editieren/ziehen: px_zu_mm bleibt konsistent.
    mw._on_ruler_line_dragged()
    assert abs(mw._px_to_mm - 0.5) < 1e-9


def test_undo_of_ruler_setting_removes_stale_ruler_line(loaded_main_window):
    mw = loaded_main_window
    mw._push_undo_snapshot()
    _set_ruler(mw, (2.0, 3.0), (12.0, 3.0), 5.0)
    mw._apply_scale_visuals_visibility()
    assert mw._ruler_line.isVisible()

    mw._on_undo()
    assert mw._px_to_mm is None
    assert mw._ruler_mm_value is None
    assert not mw._ruler_line.isVisible()
    assert mw._ruler_text is None or not mw._ruler_text.isVisible()

    mw._on_redo()
    assert mw._px_to_mm is not None
    assert mw._ruler_mm_value == 5.0
    assert mw._ruler_line.isVisible()


def test_load_paths_stops_old_live_watch_before_new_recording(loaded_main_window, monkeypatch, tmp_path):
    mw = loaded_main_window
    paths = list(mw.recording.paths)
    mw._watched_folder = paths[0].parent
    mw._live_watch_timer.start()
    monkeypatch.setattr(mw, "_confirm_discard_current_recording", lambda: True)
    seen = {}
    original = mw._set_recording

    def spy(recording, **kwargs):
        seen["watched"] = mw._watched_folder
        seen["active"] = mw._live_watch_timer.isActive()
        return original(recording, **kwargs)

    monkeypatch.setattr(mw, "_set_recording", spy)
    assert mw._load_paths(paths)
    assert seen == {"watched": None, "active": False}


def test_loading_new_recording_cancels_armed_ruler_tool(loaded_main_window, monkeypatch):
    mw = loaded_main_window
    mw._ruler_armed = True
    mw._ruler_start = (3.0, 3.0)
    monkeypatch.setattr(mw, "_confirm_discard_current_recording", lambda: True)
    assert mw._load_paths(list(mw.recording.paths))
    assert mw._ruler_armed is False
    assert mw._ruler_start is None


def test_live_append_remaps_frame_indices_after_resort(loaded_main_window):
    mw = loaded_main_window
    folder = mw.recording.paths[0].parent
    entry = mw.roi_entries[0]
    entry.interp_start_frame = 1
    entry.interp_end_frame = 4
    mw._excluded_frame_indices = {1}
    old_path_1 = mw.recording.paths[1]

    # Aelteres Bild wird nachgeliefert -> alles rutscht um einen Index nach hinten.
    ts = datetime(2026, 1, 1, 11, 59, 59)
    rows, cols = mw.recording.shape
    lines = [";".join("20,0" for _ in range(cols)) + ";" for _ in range(rows)]
    (folder / f"Record_{ts.strftime('%Y-%m-%d_%H-%M-%S')}.csv").write_text("\n".join(lines), encoding="utf-8")
    mw._watched_folder = folder
    mw._check_for_new_files()

    assert mw.recording.n_frames == 6
    assert mw.recording.paths[2] == old_path_1
    assert mw._excluded_frame_indices == {2}
    assert entry.interp_start_frame == 2
    assert entry.interp_end_frame == 5


def test_live_append_resets_stale_shrinkage_polarity_text(loaded_main_window):
    mw = loaded_main_window
    folder = mw.recording.paths[0].parent
    mw._shrinkage_result = {"areas_px": [1.0] * 5, "warmer": True}
    mw.lbl_shrinkage_polarity.setText("Polarität: Probe ist wärmer.")
    ts = datetime(2026, 1, 1, 12, 0, 0) + timedelta(seconds=10)
    rows, cols = mw.recording.shape
    lines = [";".join("20,0" for _ in range(cols)) + ";" for _ in range(rows)]
    (folder / f"Record_{ts.strftime('%Y-%m-%d_%H-%M-%S')}.csv").write_text("\n".join(lines), encoding="utf-8")
    mw._watched_folder = folder
    mw._check_for_new_files()
    assert mw._shrinkage_result is None
    assert "automatisch erkannt" in mw.lbl_shrinkage_polarity.text()


def test_project_load_resets_stale_shrinkage_polarity_text(loaded_main_window, tmp_path, monkeypatch):
    mw = loaded_main_window
    mw.lbl_shrinkage_polarity.setText("Polarität: Probe ist wärmer.")
    state = mw._shrinkage_state_dict()
    state["box_flaeche"]["breite_px"] = 7.0
    _load_project_file(mw, monkeypatch, tmp_path, _base(mw, schwindung=state))
    assert "automatisch erkannt" in mw.lbl_shrinkage_polarity.text()


def test_unexpected_load_error_does_not_leave_activity_bar_busy(loaded_main_window, monkeypatch):
    import pytest

    from thermal_viewer.main_window import project_io

    mw = loaded_main_window
    monkeypatch.setattr(mw, "_confirm_discard_current_recording", lambda: True)

    def failing_load(paths, progress_cb=None, **kwargs):
        progress_cb(1, 2)
        raise MemoryError("zu gross")

    monkeypatch.setattr(project_io, "load_paths", failing_load)
    with pytest.raises(MemoryError):
        mw._load_paths(list(mw.recording.paths))
    assert mw._activity_busy is False
    assert not mw._activity_progress.isVisible()
    assert "Lade Frames" not in mw._activity_label.text()
