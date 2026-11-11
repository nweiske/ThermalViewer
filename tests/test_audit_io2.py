"""Regressionstests der zweiten I/O-Audit-Runde (Projekt-Round-Trip, Undo,
Live-Ordner-Ueberwachung, Einstellungen)."""
from __future__ import annotations

import json

import pytest
from qtpy import QtCore, QtWidgets


def _rich_state(mw):
    """Setzt moeglichst viel vom nutzerseitig einstellbaren Zustand."""
    e1, e2 = mw.roi_entries[0], mw.roi_entries[1]
    e1.place(center_x=5, center_y=6, width=4, height=3)
    e1.chk_circular.setChecked(True)
    e1.chk_show_temperature.setChecked(False)
    e1.set_color("#123456")
    e1.list_item.setText("Mein ROI")
    e1.combo_stat_mode.setCurrentIndex(e1.combo_stat_mode.findData("max"))
    e1.interp_start = ((2.0, 3.0), (4.0, 4.0))
    e1.interp_start_frame = 1
    e1.interp_end = ((8.0, 9.0), (5.0, 5.0))
    e1.interp_end_frame = 3
    e1.chk_interp.setChecked(True)
    e2.place(center_x=12, center_y=12, width=2, height=2)
    e2.list_item.setCheckState(QtCore.Qt.CheckState.Unchecked)
    mw._add_roi_entry()
    mw.spin_eval_start.setValue(2)
    mw.spin_eval_end.setValue(4)
    mw.combo_cmap.setCurrentIndex(1)
    mw.chk_cmap_invert.setChecked(True)
    mw._set_level_mode("manual")
    mw.spin_level_min.setValue(21.0)
    mw.spin_level_max.setValue(33.0)
    mw._px_to_mm = 0.5
    mw._ruler_mm_value = 5.0
    mw._create_or_move_ruler_line((1.0, 1.0), (11.0, 1.0))
    mw._ruler_label_offset = QtCore.QPointF(1.0, 2.0)
    mw._ruler_color = "#00ff00"
    mw._refresh_scale_label()
    mw._create_measurement((2.0, 2.0), (6.0, 5.0), 5.0)
    mw._cleaning_points = [(3, 4, "or", True), (10, 2, "and", False)]
    mw._cleaning_kernel_size = 5
    mw._cleaning_threshold = 7.5
    mw._excluded_frame_indices = {1}
    mw._add_sample_height(7)
    mw._add_crosssection_entry("horizontal", 6)
    mw._crosssection_y_manual = True
    mw._crosssection_y_range = (3.0, 123.0)
    mw._crosssection_y_spacing = 10.0
    mw.roi_shrink_area.setPos((1, 1), update=False)
    mw.roi_shrink_area.setSize((6, 6))
    mw._shrinkage_color_area = "#112233"
    mw._shrinkage_color_contour = "#445566"
    mw._shrinkage_enabled = True


def test_state_dict_is_json_serialisable_and_stable_under_restore(loaded_main_window):
    mw = loaded_main_window
    _rich_state(mw)
    before = mw._build_project_state_dict()
    text = json.dumps(before)  # darf nicht an numpy-Typen scheitern
    mw._restoring_undo_snapshot = True
    try:
        mw._restore_project_state_dict(json.loads(text))
    finally:
        mw._restoring_undo_snapshot = False
    after = mw._build_project_state_dict()
    assert json.dumps(after, sort_keys=True) == json.dumps(before, sort_keys=True)


def _fresh_loaded_window(folder):
    from thermal_viewer.main_window import MainWindow

    mw2 = MainWindow()
    result = mw2._resolve_folder_and_pattern(folder)
    folder_path, pattern, fmt = result
    assert mw2._load_paths(sorted(folder_path.glob("*.csv")), pattern=pattern, strptime_fmt=fmt)
    return mw2


@pytest.mark.parametrize("via", ["file", "undo"])
def test_state_roundtrip_into_fresh_window(loaded_main_window, synthetic_recording_folder, tmp_path, monkeypatch, via):
    mw = loaded_main_window
    _rich_state(mw)
    before = mw._build_project_state_dict()
    mw2 = _fresh_loaded_window(synthetic_recording_folder)
    try:
        if via == "file":
            path = tmp_path / "p.tvproj"
            path.write_text(json.dumps(before), encoding="utf-8")
            monkeypatch.setattr(
                QtWidgets.QFileDialog, "getOpenFileName", staticmethod(lambda *a, **k: (str(path), ""))
            )
            mw2._load_project()
        else:
            mw2._restoring_undo_snapshot = True
            mw2._restore_project_state_dict(json.loads(json.dumps(before)))
            mw2._restoring_undo_snapshot = False
        after = mw2._build_project_state_dict()
    finally:
        mw2.close()
    for key in before:
        assert json.dumps(after[key], sort_keys=True) == json.dumps(before[key], sort_keys=True), key


def _load_payload(mw, monkeypatch, tmp_path, payload):
    path = tmp_path / "audit2.tvproj"
    path.write_text(json.dumps(payload), encoding="utf-8")
    monkeypatch.setattr(QtWidgets.QFileDialog, "getOpenFileName", staticmethod(lambda *a, **k: (str(path), "")))
    errors = []
    monkeypatch.setattr(QtWidgets.QMessageBox, "critical", staticmethod(lambda *a, **k: errors.append(a)))
    monkeypatch.setattr(QtWidgets.QMessageBox, "warning", staticmethod(lambda *a, **k: 0))
    monkeypatch.setattr(QtWidgets.QMessageBox, "information", staticmethod(lambda *a, **k: 0))
    mw._load_project()
    return errors


def _new_csv(mw, offset_s, value="20,0"):
    from datetime import datetime, timedelta

    folder = mw.recording.paths[0].parent
    ts = datetime(2026, 1, 1, 12, 0, 0) + timedelta(seconds=offset_s)
    rows, cols = mw.recording.shape
    lines = [";".join(value for _ in range(cols)) + ";" for _ in range(rows)]
    path = folder / f"Record_{ts.strftime('%Y-%m-%d_%H-%M-%S')}.csv"
    path.write_text("\n".join(lines), encoding="utf-8")
    return path


def test_load_project_with_unhashable_shrinkage_labels_still_loads_the_rest(
    loaded_main_window, tmp_path, monkeypatch
):
    mw = loaded_main_window
    payload = {
        "schwindung": {"kenngroesse": {}, "modus": ["width"], "kenngroesse_je_modus": {"area": [], "width": {}}},
        "probenhoehen": [{"nummer": 1, "zeile": 4}],
        "bereinigung_schwellenwert": 9.0,
    }
    errors = _load_payload(mw, monkeypatch, tmp_path, payload)
    assert not errors
    assert mw._shrinkage_mode == "area" and mw._shrinkage_metric == "flaeche"
    assert [e.row for e in mw._sample_height_entries] == [4]
    # Display-only-Pfad (Undo/Redo) darf an unhashbaren Werten ebenfalls nicht scheitern.
    state = mw._shrinkage_state_dict()
    state["modus"] = ["x"]
    state["kenngroesse_je_modus"] = {"area": {}, "width": []}
    mw._load_project_shrinkage({"schwindung": state}, recompute=False)


def test_load_project_skips_duplicate_or_invalid_sample_height_and_crosssection_numbers(
    loaded_main_window, tmp_path, monkeypatch
):
    mw = loaded_main_window
    payload = {
        "probenhoehen": [{"nummer": 1, "zeile": 2}, {"nummer": 1, "zeile": 3}, {"nummer": -4, "zeile": 5}],
        "querschnitte": [
            {"nummer": 2, "richtung": "vertical", "position": 3},
            {"nummer": 2, "richtung": "vertical", "position": 4},
            {"nummer": 0, "richtung": "horizontal"},
        ],
    }
    _load_payload(mw, monkeypatch, tmp_path, payload)
    assert [(e.number, e.row) for e in mw._sample_height_entries] == [(1, 2)]
    assert [(e.number, e.position) for e in mw._crosssection_entries] == [(2, 3)]


def test_project_load_unplaces_roi_the_file_lists_as_unplaced(loaded_main_window, tmp_path, monkeypatch):
    mw = loaded_main_window
    entry = mw.roi_entries[0]
    entry.place(8, 8, 4, 4)
    assert entry.placed
    _load_payload(mw, monkeypatch, tmp_path, {"rois": [{"index": 0, "platziert": False}]})
    assert not entry.placed
    assert not entry.roi.isVisible()


def test_undo_of_scale_setting_clears_roi_mm_label(loaded_main_window):
    mw = loaded_main_window
    entry = mw.roi_entries[0]
    entry.place(8, 8, 4, 4)
    mw._push_undo_snapshot()
    mw._px_to_mm = 0.5
    mw._ruler_mm_value = 5.0
    mw._create_or_move_ruler_line((1.0, 1.0), (11.0, 1.0))
    mw._refresh_scale_label()
    mw._update_roi_mm_label(entry)
    assert not entry.mm_label.isHidden()

    mw._on_undo()
    assert mw._px_to_mm is None
    assert entry.mm_label.isHidden()


def test_undo_and_redo_are_ignored_while_an_export_runs(loaded_main_window):
    mw = loaded_main_window
    entry = mw.roi_entries[0]
    mw._push_undo_snapshot()
    entry.place(8, 8, 4, 4)
    mw._export_busy = True
    try:
        mw._on_undo()
        assert entry.placed
        assert len(mw._undo_stack) == 1
    finally:
        mw._export_busy = False
    mw._on_undo()
    assert not entry.placed
    mw._export_busy = True
    try:
        mw._on_redo()
        assert not entry.placed
    finally:
        mw._export_busy = False


def test_loading_is_refused_while_an_export_runs(loaded_main_window, tmp_path, monkeypatch):
    mw = loaded_main_window
    old_recording = mw.recording
    mw._export_busy = True
    try:
        assert mw._load_paths(list(old_recording.paths)) is False
        _load_payload(mw, monkeypatch, tmp_path, {"bereinigung_schwellenwert": 9.0})
    finally:
        mw._export_busy = False
    assert mw.recording is old_recording
    assert mw._cleaning_threshold == 5.0


def test_undo_after_live_append_keeps_eval_end_following_and_remaps_exclusions(loaded_main_window):
    mw = loaded_main_window
    assert mw._eval_end_index == 4
    mw._excluded_frame_indices = {1}
    mw._push_undo_snapshot()
    mw._excluded_frame_indices = {1, 2}
    old_path_1 = mw.recording.paths[1]
    # Aelteres Bild wird nachgeliefert (Neusortierung) UND neueres am Ende.
    _new_csv(mw, -1)
    _new_csv(mw, 10)
    mw._watched_folder = mw.recording.paths[0].parent
    mw._check_for_new_files()
    assert mw.recording.n_frames == 7
    assert mw._eval_end_index == 6

    mw._on_undo()
    assert mw._eval_end_index == 6, "Undo darf das mitgewachsene Auswertungsende nicht zuruecksetzen"
    assert mw.recording.paths[2] == old_path_1
    assert mw._excluded_frame_indices == {2}


def test_live_poll_reports_a_broken_file_once_and_forgets_it_after_it_loads(loaded_main_window):
    mw = loaded_main_window
    path = _new_csv(mw, 20)
    good_text = path.read_text(encoding="utf-8")
    path.write_text("kaputt;1,0;\n", encoding="utf-8")
    mw._watched_folder = path.parent
    mw._check_for_new_files()
    assert mw.recording.n_frames == 5
    assert [p for p, _ in mw.recording.skipped_files] == [path]

    shown = []
    mw.statusBar().showMessage = lambda *a, **k: shown.append(a[0])
    mw._check_for_new_files()
    assert not any("nicht" in m and "gelesen" in m for m in shown), "dieselbe Datei erneut als neu gemeldet"

    path.write_text(good_text, encoding="utf-8")
    mw._check_for_new_files()
    assert mw.recording.n_frames == 6
    assert mw.recording.skipped_files == []


def test_save_project_failure_keeps_existing_file_and_leaves_no_tmp(
    loaded_main_window, tmp_path, monkeypatch
):
    mw = loaded_main_window
    target = tmp_path / "keep.tvproj"
    target.write_text("ALT", encoding="utf-8")
    monkeypatch.setattr(QtWidgets.QFileDialog, "getSaveFileName", staticmethod(lambda *a, **k: (str(target), "")))
    errors = []
    monkeypatch.setattr(QtWidgets.QMessageBox, "critical", staticmethod(lambda *a, **k: errors.append(a)))

    monkeypatch.setattr(mw, "_build_project_state_dict", lambda: {"x": object()})
    assert mw._save_project() is False
    assert len(errors) == 1
    assert target.read_text(encoding="utf-8") == "ALT"
    assert not (tmp_path / "keep.tvproj.tmp").exists()

    monkeypatch.undo()
    monkeypatch.setattr(QtWidgets.QFileDialog, "getSaveFileName", staticmethod(lambda *a, **k: (str(target), "")))
    assert mw._save_project() is True
    assert json.loads(target.read_text(encoding="utf-8"))["format_version"] == 2
    assert not (tmp_path / "keep.tvproj.tmp").exists()


def test_colormap_change_is_its_own_undo_step(loaded_main_window):
    mw = loaded_main_window
    entry = mw.roi_entries[0]
    mw._push_undo_snapshot()
    entry.place(8, 8, 4, 4)
    mw.combo_cmap.setCurrentIndex(2)
    mw.chk_cmap_invert.setChecked(True)

    mw._on_undo()  # nur "Invertieren"
    assert mw.chk_cmap_invert.isChecked() is False
    assert mw.combo_cmap.currentIndex() == 2 and entry.placed
    mw._on_undo()  # nur der Farbverlauf
    assert mw.combo_cmap.currentIndex() == 0 and entry.placed
    mw._on_undo()  # erst jetzt das Platzieren
    assert not entry.placed


def test_level_mode_and_manual_level_changes_are_undoable(loaded_main_window):
    mw = loaded_main_window
    assert mw._level_mode() == "global"
    mw.radio_level_manual.setChecked(True)
    assert mw._level_mode() == "manual"
    mw._on_undo()
    assert mw._level_mode() == "global"
    mw._on_redo()
    assert mw._level_mode() == "manual"

    mw._end_grouped_undo_edit()
    before = mw.spin_level_min.value()
    mw.spin_level_min.setValue(before + 1.0)
    mw._end_grouped_undo_edit()
    assert mw.spin_level_min.value() == before + 1.0
    mw._on_undo()
    assert mw.spin_level_min.value() == before
