"""Regressionstests: Schwindungs-Bezug auf erstes SICHTBARES Bild, Ueberschreiben-Abfrage, atomares SVG-Schreiben."""

import numpy as np
import pytest
from qtpy import QtWidgets

SVG_TEXT = '<?xml version="1.0"?><svg xmlns="http://www.w3.org/2000/svg" width="1" height="1"/>'


def test_visible_edge_indices_skip_excluded_frames(loaded_main_window):
    mw = loaded_main_window
    n = mw.recording.n_frames
    mw._excluded_frame_indices = {0, n - 1}
    assert mw._visible_edge_indices(n) == (1, n - 2)
    mw._excluded_frame_indices = set()
    assert mw._visible_edge_indices(n) == (0, n - 1)


def test_shrinkage_percent_baseline_is_first_visible_frame(loaded_main_window):
    mw = loaded_main_window
    n = mw.recording.n_frames
    assert n >= 3
    series = np.linspace(100.0, 50.0, n)
    mw._shrinkage_result = {
        "areas_px": series, "rect_widths_px": series.copy(), "round_widths_px": series.copy(),
        "warmer": True, "spans": {},
    }
    mw._excluded_frame_indices = {0}
    mw._update_shrinkage_curve()
    _x, y = mw.shrinkage_curve.getData()
    assert y[0] == pytest.approx(0.0)


def _patch_box(monkeypatch, overwrite: bool):
    def clicked(self):
        buttons = {b.text(): b for b in self.buttons()}
        return buttons["Überschreiben" if overwrite else "Abbrechen"]

    monkeypatch.setattr(QtWidgets.QMessageBox, "exec", lambda self: 0)
    monkeypatch.setattr(QtWidgets.QMessageBox, "clickedButton", clicked)


def test_confirm_overwrite_without_existing_files_asks_nothing(loaded_main_window, tmp_path, monkeypatch):
    monkeypatch.setattr(
        QtWidgets.QMessageBox, "exec", lambda self: pytest.fail("darf nicht fragen")
    )
    assert loaded_main_window._confirm_overwrite([tmp_path / "neu.png", tmp_path / "neu.json"])


@pytest.mark.parametrize("overwrite", [True, False])
def test_confirm_overwrite_follows_the_users_choice(loaded_main_window, tmp_path, monkeypatch, overwrite):
    existing = tmp_path / "vorhanden.json"
    existing.write_text("{}", encoding="utf-8")
    _patch_box(monkeypatch, overwrite)
    assert loaded_main_window._confirm_overwrite([existing, tmp_path / "neu.png"]) is overwrite


def test_svg_write_replaces_target_atomically(loaded_main_window, tmp_path):
    mw = loaded_main_window
    target = tmp_path / "grafik.svg"
    target.write_text("alt", encoding="utf-8")
    part = mw._svg_temp_path(target)
    part.write_text(SVG_TEXT, encoding="utf-8")
    mw._finish_svg_write(part, target)
    assert target.read_text(encoding="utf-8") == SVG_TEXT
    assert not part.exists()


def test_svg_write_reports_unreplaceable_target_and_cleans_up(loaded_main_window, tmp_path):
    mw = loaded_main_window
    target = tmp_path / "ziel.svg"
    target.mkdir()  # ein Verzeichnis laesst sich nicht durch eine Datei ersetzen
    part = mw._svg_temp_path(target)
    part.write_text(SVG_TEXT, encoding="utf-8")
    with pytest.raises(OSError):
        mw._finish_svg_write(part, target)
    assert not part.exists()
