"""Tests fuer thermal_viewer/data.py: Namensschema-Kompilierung/-Validierung/
-Erzeugung, CSV-Frame-Parsing und das Laden/Zusammenfuehren von Messreihen."""
from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from thermal_viewer.data import (
    DEFAULT_FILENAME_TEMPLATE,
    RecordingError,
    _deduplicate_timestamps,
    append_paths,
    compile_filename_template,
    load_frame,
    load_paths,
    render_filename_template,
    validate_filename_template,
)


def test_default_template_uses_lowercase_hour_token():
    # Regression: Stunden-Platzhalter ist bewusst klein "hh" (nicht "HH"),
    # um ihn von "MM" (Monat) eindeutig zu unterscheiden.
    assert "hh" in DEFAULT_FILENAME_TEMPLATE
    assert "HH" not in DEFAULT_FILENAME_TEMPLATE


@pytest.mark.parametrize("template", [
    "Record_YYYY-MM-DD_hh-mm-ss",
    "IMG_YYYYMMDD_hhmmss",
    "Messung_YYYY-MM-DD_hh-mm-ss_Ende",
])
def test_validate_filename_template_accepts_complete_templates(template):
    assert validate_filename_template(template) is None


def test_validate_filename_template_rejects_missing_token():
    message = validate_filename_template("Record_YYYY-MM-DD")
    assert message is not None
    assert "hh" in message


def test_validate_filename_template_rejects_duplicate_token():
    message = validate_filename_template("Record_YYYY-YYYY-MM-DD_hh-mm-ss")
    assert message is not None


def test_literal_prefix_containing_token_letters_is_not_misparsed():
    # Bugfix-Regression: "Messung_" enthaelt zufaellig "ss" -- darf nicht
    # als Sekunden-Platzhalter gelesen werden, weil davor ein Buchstabe steht.
    pattern, fmt = compile_filename_template("Messung_YYYY-MM-DD_hh-mm-ss")
    match = pattern.search("Messung_2026-01-01_12-00-00")
    assert match is not None
    assert datetime.strptime(match.group(1), fmt) == datetime(2026, 1, 1, 12, 0, 0)


def test_compile_filename_template_roundtrips_with_parse():
    pattern, fmt = compile_filename_template("Record_YYYY-MM-DD_hh-mm-ss")
    ts = datetime(2026, 3, 4, 5, 6, 7)
    name = f"Record_{ts.strftime('%Y-%m-%d_%H-%M-%S')}"
    match = pattern.search(name)
    assert match is not None
    assert datetime.strptime(match.group(1), fmt) == ts


def test_render_filename_template_fills_all_tokens_from_timestamp():
    ts = datetime(2026, 1, 2, 3, 4, 5)
    result = render_filename_template("Frame_YYYY-MM-DD_hh-mm-ss_", ts)
    assert result == "Frame_2026-01-02_03-04-05_"


def test_render_filename_template_leaves_pure_literals_unchanged():
    assert render_filename_template("Messung", datetime(2026, 1, 1)) == "Messung"


def test_render_and_compile_use_consistent_tokenization():
    # render_filename_template ist die Umkehrung von compile_filename_template
    # -- beide muessen exakt dieselben Stellen als Platzhalter erkennen.
    ts = datetime(2026, 6, 7, 8, 9, 10)
    rendered = render_filename_template("Messung_YYYY-MM-DD_hh-mm-ss_Ende", ts)
    pattern, fmt = compile_filename_template("Messung_YYYY-MM-DD_hh-mm-ss_Ende")
    match = pattern.search(rendered)
    assert match is not None
    assert datetime.strptime(match.group(1), fmt) == ts


def test_load_frame_parses_german_decimal_comma(tmp_path):
    path = tmp_path / "frame.csv"
    path.write_text("1,5;2,25;\n3,0;4,75;\n", encoding="utf-8")
    frame = load_frame(path)
    assert frame.shape == (2, 2)
    assert frame[0, 0] == pytest.approx(1.5)
    assert frame[1, 1] == pytest.approx(4.75)


def test_load_frame_raises_on_empty_file(tmp_path):
    path = tmp_path / "empty.csv"
    path.write_text("", encoding="utf-8")
    with pytest.raises(RecordingError):
        load_frame(path)


def test_deduplicate_timestamps_forces_strictly_increasing_sequence():
    t = datetime(2026, 1, 1, 0, 0, 0)
    adjusted, changed = _deduplicate_timestamps([t, t, t])
    assert changed is True
    assert adjusted == sorted(adjusted)
    assert len(set(adjusted)) == 3


def test_deduplicate_timestamps_leaves_increasing_sequence_untouched():
    t0 = datetime(2026, 1, 1)
    timestamps = [t0, t0 + timedelta(seconds=1), t0 + timedelta(seconds=2)]
    adjusted, changed = _deduplicate_timestamps(timestamps)
    assert changed is False
    assert adjusted == timestamps


def test_load_paths_skips_broken_files_but_keeps_valid_ones(tmp_path):
    good1 = tmp_path / "Record_2026-01-01_12-00-00.csv"
    good1.write_text("1,0;2,0;\n3,0;4,0;\n", encoding="utf-8")
    good2 = tmp_path / "Record_2026-01-01_12-00-01.csv"
    good2.write_text("1,0;2,0;\n3,0;4,0;\n", encoding="utf-8")
    broken = tmp_path / "Record_2026-01-01_12-00-02.csv"
    broken.write_text("", encoding="utf-8")

    recording = load_paths([good1, good2, broken])

    assert recording.n_frames == 2
    assert len(recording.skipped_files) == 1


def test_load_paths_skips_mismatched_resolution(tmp_path):
    p1 = tmp_path / "Record_2026-01-01_12-00-00.csv"
    p1.write_text("1,0;2,0;\n3,0;4,0;\n", encoding="utf-8")
    p2 = tmp_path / "Record_2026-01-01_12-00-01.csv"
    p2.write_text("1,0;2,0;\n3,0;4,0;\n", encoding="utf-8")
    odd_shape = tmp_path / "Record_2026-01-01_12-00-02.csv"
    odd_shape.write_text("1,0;2,0;3,0;\n", encoding="utf-8")

    recording = load_paths([p1, p2, odd_shape])

    assert recording.n_frames == 2
    assert len(recording.skipped_files) == 1


def test_load_paths_raises_when_nothing_loadable(tmp_path):
    broken = tmp_path / "Record_2026-01-01_12-00-00.csv"
    broken.write_text("", encoding="utf-8")
    with pytest.raises(RecordingError):
        load_paths([broken])


def test_append_paths_merges_and_sorts_by_timestamp(tmp_path):
    p1 = tmp_path / "Record_2026-01-01_12-00-00.csv"
    p1.write_text("1,0;2,0;\n3,0;4,0;\n", encoding="utf-8")
    p2 = tmp_path / "Record_2026-01-01_12-00-02.csv"
    p2.write_text("1,0;2,0;\n3,0;4,0;\n", encoding="utf-8")
    recording = load_paths([p1, p2])

    p_mid = tmp_path / "Record_2026-01-01_12-00-01.csv"
    p_mid.write_text("1,0;2,0;\n3,0;4,0;\n", encoding="utf-8")
    updated = append_paths(recording, [p_mid])

    assert updated.n_frames == 3
    assert updated.timestamps == sorted(updated.timestamps)


def test_append_paths_ignores_already_loaded_files(tmp_path):
    p1 = tmp_path / "Record_2026-01-01_12-00-00.csv"
    p1.write_text("1,0;2,0;\n3,0;4,0;\n", encoding="utf-8")
    recording = load_paths([p1])

    updated = append_paths(recording, [p1])

    assert updated.n_frames == 1
