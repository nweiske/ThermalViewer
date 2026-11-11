"""Regressionstests der abschliessenden Bugjagd: Tab-Spalten, Basis bei fehlgeschlagener Erkennung,
reservierte Dateinamen, vektorisierte Bereinigungskandidaten, Werkzeuge bei Projekt laden/Undo."""

from datetime import datetime

import numpy as np
import pytest

from thermal_viewer.data import ImportSettings, _clean_data_line, _parse_data_line
from thermal_viewer.dialogs.filename_tokens import render_export_filename


def test_tab_delimiter_keeps_empty_first_column():
    settings = ImportSettings(delimiter="\t", decimal_separator=".")
    assert _clean_data_line("\t1.5\t2.5\t", settings) == "\t1.5\t2.5"
    settings_skip = ImportSettings(delimiter="\t", decimal_separator=".", skip_leading_columns=1)
    assert _parse_data_line("\t1.5\t2.5\t", settings_skip) == [1.5, 2.5]
    assert _clean_data_line("\t\t", settings) == ""
    assert _clean_data_line("  1;2;  ", ImportSettings()) == "1;2"


def test_visible_edge_indices_skip_failed_detection(loaded_main_window):
    mw = loaded_main_window
    n = mw.recording.n_frames
    values = np.linspace(100.0, 50.0, n)
    values[0] = 0.0
    values[-1] = np.nan
    mw._excluded_frame_indices = set()
    assert mw._visible_edge_indices(n, values) == (1, n - 2)
    assert mw._visible_edge_indices(n) == (0, n - 1)


def test_shrinkage_curve_survives_failed_first_frame(loaded_main_window):
    mw = loaded_main_window
    n = mw.recording.n_frames
    series = np.linspace(100.0, 50.0, n)
    series[0] = 0.0
    mw._shrinkage_result = {
        "areas_px": series, "rect_widths_px": series.copy(), "round_widths_px": series.copy(),
        "warmer": True, "spans": {},
    }
    mw._update_shrinkage_curve()
    _x, y = mw.shrinkage_curve.getData()
    assert y[1] == pytest.approx(0.0)
    assert y[-1] > 0.0


def test_reserved_name_after_index_substitution():
    ts = datetime(2024, 1, 1, 12, 0, 0)
    assert render_export_filename("COMIDX", ts, 0.0, 1, 1) == "_COM1"
    assert render_export_filename("Bild_IDX", ts, 0.0, 1, 3) == "Bild_001"


def test_cleaning_candidates_vectorised_matches_loop(loaded_main_window):
    mw = loaded_main_window
    n = mw.recording.n_frames
    rng = np.random.default_rng(3)
    series = {0: rng.normal(0, 3, n), 1: rng.normal(0, 3, n), 2: rng.normal(0, 3, n)}
    mw._cleaning_points = [(0, 0, "and", True), (1, 1, "and", True), (2, 2, "or", True)]
    mw._cleaning_threshold = 2.0
    mw._cleaning_point_series = lambda i: series[i]
    expected = set()
    for i in range(1, n):
        and_hit = all(abs(series[j][i] - series[j][i - 1]) > 2.0 for j in (0, 1))
        or_hit = abs(series[2][i] - series[2][i - 1]) > 2.0
        if and_hit or or_hit:
            expected.add(i)
    assert mw._compute_cleaning_candidates() == expected


def test_cancel_all_armed_tools_clears_every_mode(loaded_main_window):
    mw = loaded_main_window
    mw._ruler_armed = True
    mw._measurement_armed = True
    mw._sample_height_armed = True
    mw._cancel_all_armed_tools()
    assert not mw._ruler_armed
    assert not mw._measurement_armed
    assert not mw._sample_height_armed
    assert mw._armed_entry is None
    assert mw._crosssection_entry_armed_direction is None


def test_restore_project_state_cancels_armed_tools(loaded_main_window):
    mw = loaded_main_window
    state = mw._build_project_state_dict()
    mw._measurement_armed = True
    mw._restore_project_state_dict(state)
    assert not mw._measurement_armed


def test_escape_cancels_armed_tools(loaded_main_window):
    mw = loaded_main_window
    mw._on_escape_pressed()  # ohne Werkzeug: no-op
    mw._measurement_armed = True
    mw._sample_height_armed = True
    mw._on_escape_pressed()
    assert not mw._any_tool_armed()
    assert "abgebrochen" in mw.statusBar().currentMessage()
    keys = {shortcut.key().toString() for shortcut in mw._nav_shortcuts}
    assert "Esc" in keys


def test_closing_with_unsaved_changes_asks_and_can_cancel(loaded_main_window, monkeypatch):
    from qtpy import QtGui, QtWidgets

    mw = loaded_main_window
    mw._confirm_unsaved_on_close = True
    assert not mw._has_unsaved_changes()
    mw._push_undo_snapshot()
    assert mw._has_unsaved_changes()

    def fake_exec(self):
        return 0

    chosen = {"text": "Abbrechen"}

    def fake_clicked(self):
        return next(b for b in self.buttons() if b.text() == chosen["text"])

    monkeypatch.setattr(QtWidgets.QMessageBox, "exec", fake_exec)
    monkeypatch.setattr(QtWidgets.QMessageBox, "clickedButton", fake_clicked)

    event = QtGui.QCloseEvent()
    mw.closeEvent(event)
    assert not event.isAccepted()

    chosen["text"] = "Verwerfen"
    event = QtGui.QCloseEvent()
    mw.closeEvent(event)
    assert event.isAccepted()

    chosen["text"] = "Speichern"
    monkeypatch.setattr(mw, "_save_project", lambda: False)
    event = QtGui.QCloseEvent()
    mw.closeEvent(event)
    assert not event.isAccepted()
    monkeypatch.setattr(mw, "_save_project", lambda: True)
    event = QtGui.QCloseEvent()
    mw.closeEvent(event)
    assert event.isAccepted()
    mw._confirm_unsaved_on_close = False


def test_saving_and_clearing_history_reset_dirty_state(loaded_main_window, tmp_path, monkeypatch):
    from qtpy import QtWidgets

    mw = loaded_main_window
    mw._push_undo_snapshot()
    assert mw._has_unsaved_changes()
    target = tmp_path / "p.tvproj"
    monkeypatch.setattr(QtWidgets.QFileDialog, "getSaveFileName", staticmethod(lambda *a, **k: (str(target), "")))
    assert mw._save_project()
    assert not mw._has_unsaved_changes()
    mw._on_undo()
    assert mw._has_unsaved_changes()
    mw._clear_undo_history()
    assert not mw._has_unsaved_changes()


def test_filename_template_requires_valid_date(tmp_path):
    from thermal_viewer.data import compile_filename_template, files_matching_template, has_valid_timestamp

    pattern, fmt = compile_filename_template("YYYY-MM-DD_hh-mm-ss")
    good, bad = tmp_path / "2024-01-02_03-04-05.csv", tmp_path / "2024-13-02_03-04-05.csv"
    good.write_text("1")
    bad.write_text("1")
    assert has_valid_timestamp(good.stem, pattern, fmt)
    assert not has_valid_timestamp(bad.stem, pattern, fmt)
    assert has_valid_timestamp(bad.stem, pattern)
    assert files_matching_template(tmp_path, pattern, fmt) == [good]
    assert files_matching_template(tmp_path, pattern) == sorted([good, bad])


def test_garbage_import_settings_fall_back_to_defaults(loaded_main_window):
    mw = loaded_main_window
    s = mw._settings
    keys = {
        "import/delimiter": "abc", "import/decimal_separator": ",,", "import/encoding": "no-such-codec",
        "import/skip_header_lines": "x", "import/skip_footer_lines": -5, "import/skip_leading_columns": 10**9,
    }
    old = {k: s.value(k) for k in keys}
    try:
        for k, v in keys.items():
            s.setValue(k, v)
        settings = mw._load_import_settings()
        defaults = ImportSettings()
        assert settings.delimiter == defaults.delimiter
        assert settings.decimal_separator == defaults.decimal_separator
        assert settings.encoding == defaults.encoding
        assert settings.skip_header_lines == 0
        assert settings.skip_footer_lines == 0
        assert settings.skip_leading_columns == 100000
    finally:
        for k, v in old.items():
            s.remove(k) if v is None else s.setValue(k, v)


def test_roi_dragged_outside_is_pulled_back_into_image(loaded_main_window):
    mw = loaded_main_window
    rows, cols = mw.recording.shape
    entry = mw.roi_entries[0]
    entry.place(cols + 50, -40, 4, 4)
    mw._on_roi_region_finished(entry)
    cx, cy = entry.center()
    assert 0 <= cx <= cols and 0 <= cy <= rows
    assert entry.spin_x.value() == pytest.approx(cx)
