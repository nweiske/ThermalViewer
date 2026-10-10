"""Tests fuer thermal_viewer/data.py: Namensschema-Kompilierung/-Validierung/
-Erzeugung, CSV-Frame-Parsing und das Laden/Zusammenfuehren von Messreihen."""
from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from thermal_viewer.data import (
    DEFAULT_FILENAME_TEMPLATE,
    ImportSettings,
    RecordingError,
    _deduplicate_timestamps,
    append_paths,
    compile_filename_template,
    load_frame,
    load_paths,
    parse_frame_text,
    render_filename_template,
    tiff_crop_to_temperature,
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


def test_parse_frame_text_fast_path_matches_fallback_exactly():
    # Bugfix (Performance): parse_frame_text() versucht zuerst einen
    # np.loadtxt()-basierten Fastpath (siehe _try_fast_parse_lines), bevor
    # es auf die alte, zeilenweise Python-Schleife zurueckfaellt -- beide
    # MUESSEN fuer dieselbe Datei exakt dasselbe Ergebnis liefern. Erzwingt
    # den Fallback hier direkt ueber monkeypatch-freies Abschalten (leere
    # Zeilenliste wuerde den Fastpath ohnehin ueberspringen) -- stattdessen
    # wird der oeffentliche Pfad einmal normal aufgerufen und zusaetzlich
    # der interne Fallback-Teil isoliert ueber _parse_data_line nachgebaut,
    # um beide Pfade ohne Monkeypatch direkt zu vergleichen.
    import numpy as np

    from thermal_viewer.data import _parse_data_line, _select_data_lines

    rng = np.random.default_rng(7)
    values = rng.uniform(10.0, 60.0, size=(12, 17)).astype(np.float32)
    lines = [";".join(f"{v:.2f}".replace(".", ",") for v in row) + ";" for row in values]
    text = "\n".join(lines)
    settings = ImportSettings()

    fast = parse_frame_text(text, settings)
    reference = np.array(
        [_parse_data_line(line, settings) for line in _select_data_lines(text, settings)],
        dtype=np.float32,
    )
    assert fast.shape == reference.shape == values.shape
    assert np.allclose(fast, reference, atol=1e-4)
    assert np.allclose(fast, values, atol=1e-2)  # Rundung auf 2 Nachkommastellen beim Erzeugen


def test_parse_frame_text_fast_path_falls_back_on_malformed_value():
    # Ein ungueltiger Zahlenwert laesst np.loadtxt scheitern -- der Fastpath
    # faengt das ab (gibt None zurueck) und die Fehlermeldung muss weiterhin
    # exakt vom bewaehrten, zeilenweisen Fallback stammen (mit der
    # vollstaendigen Zeile im Text, nicht einer numpy-internen Meldung).
    settings = ImportSettings()
    text = "1,0;2,0;abc;4,0\n5,0;6,0;7,0;8,0"
    with pytest.raises(RecordingError, match="Ungültiger Zahlenwert in Zeile"):
        parse_frame_text(text, settings)


def test_parse_frame_text_fast_path_falls_back_on_negative_skip_trailing_columns():
    # Bugfix: ein NEGATIVES skip_trailing_columns (ueber QSettings moeglich,
    # die Dialog-Spinbox selbst erlaubt nur 0-100) liess _try_fast_parse_
    # lines() ein stop > arr.shape[1] berechnen -- numpy-Slicing klemmte das
    # frueher STILLSCHWEIGEND auf die volle Spaltenzahl, waehrend derselbe
    # Settings-Wert im Fallback (parts[:-skip_trailing_columns], bei
    # negativem Wert ein POSITIVES Slice-Ende) ein voellig anderes, viel
    # kuerzeres Ergebnis lieferte. Beide Pfade muessen identisch sein.
    settings = ImportSettings(skip_trailing_columns=-2)
    text = "1,0;2,0;3,0;4,0;5,0\n6,0;7,0;8,0;9,0;10,0"
    result = parse_frame_text(text, settings)
    assert result.tolist() == [[1.0, 2.0], [6.0, 7.0]]


def test_tiff_crop_to_temperature_clamps_out_of_range_crop():
    # Bugfix (Verteidigung): ein negatives x0/y0 wuerde ohne Klemmung per
    # numpy-Wraparound-Indexierung (statt eines klaren Fehlers oder des
    # erwarteten Ausschnitts) einen verschobenen, falschen Bildausschnitt
    # liefern. Der einzige Aufrufer (TiffImportDialog) klemmt bereits selbst,
    # diese tiefste Parsing-Schicht soll trotzdem robust bleiben.
    import numpy as np

    gray = np.arange(100, dtype=np.float64).reshape(10, 10)
    # crop=(x0, y0, x1, y1) -- negative/ueberschiessende Werte werden auf
    # [0, Breite]/[0, Hoehe] geklemmt, identisch zum bereits geklemmten
    # Ausschnitt (0, 0, 10, 10).
    clamped = tiff_crop_to_temperature(gray, (-5, -5, 20, 20), t_min=0.0, t_max=1.0)
    reference = tiff_crop_to_temperature(gray, (0, 0, 10, 10), t_min=0.0, t_max=1.0)
    assert np.array_equal(clamped, reference)


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
