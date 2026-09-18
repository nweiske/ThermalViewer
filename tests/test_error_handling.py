"""Test für den globalen Ausnahme-Auffang (siehe thermal_viewer/error_handling.py)
-- Bugreport-Hintergrund: eine Ausnahme INNERHALB eines Qt-Slots verschwand
bisher spurlos (Standard-sys.excepthook schreibt nur auf stderr, das bei
PyInstallers --windowed-Build ins Leere geht), was z.B. "Rohdaten
säubern…" scheinbar wirkungslos wirken ließ. install_global_excepthook()
muss stattdessen (a) den Traceback in eine Log-Datei schreiben und (b) eine
sichtbare Meldung zeigen."""
from __future__ import annotations

import sys

import pytest

from thermal_viewer.error_handling import install_global_excepthook


@pytest.fixture(autouse=True)
def _restore_excepthook():
    original = sys.excepthook
    yield
    sys.excepthook = original


def test_install_global_excepthook_replaces_sys_excepthook(qapp):
    original = sys.excepthook
    install_global_excepthook()
    assert sys.excepthook is not original


def test_unhandled_exception_is_logged_and_shown(qapp, tmp_path, monkeypatch):
    log_path = tmp_path / "fehlerprotokoll.log"
    monkeypatch.setattr("thermal_viewer.error_handling._LOG_PATH", log_path)

    shown = []
    monkeypatch.setattr(
        "thermal_viewer.error_handling._show_dialog", lambda text: shown.append(text)
    )

    install_global_excepthook()
    try:
        raise ValueError("boom")
    except ValueError:
        sys.excepthook(*sys.exc_info())

    assert log_path.exists()
    logged = log_path.read_text(encoding="utf-8")
    assert "ValueError: boom" in logged
    assert len(shown) == 1
    assert "ValueError: boom" in shown[0]


def test_keyboard_interrupt_passes_through_to_original_hook(qapp, monkeypatch):
    calls = []
    monkeypatch.setattr(sys, "excepthook", lambda *a: calls.append(a))
    install_global_excepthook()
    try:
        raise KeyboardInterrupt()
    except KeyboardInterrupt:
        sys.excepthook(*sys.exc_info())

    assert len(calls) == 1
    assert calls[0][0] is KeyboardInterrupt


def test_log_write_failure_does_not_raise(qapp, tmp_path, monkeypatch):
    """Ein nicht beschreibbares Log-Verzeichnis (z.B. schreibgeschuetztes
    Medium) darf den Handler selbst nicht zum Absturz bringen -- die
    sichtbare Meldung (_show_dialog) bleibt der primaere Kanal. Simuliert
    hier plattformunabhaengig ueber eine DATEI (statt eines Ordners) als
    Pfad-Bestandteil -- mkdir(parents=True) darunter schlaegt dadurch auf
    Windows UND Linux gleichermaessig fehl (NotADirectoryError, eine
    OSError-Unterklasse)."""
    blocking_file = tmp_path / "not_a_directory"
    blocking_file.write_text("x", encoding="utf-8")
    monkeypatch.setattr(
        "thermal_viewer.error_handling._LOG_PATH", blocking_file / "sub" / "log.txt"
    )
    shown = []
    monkeypatch.setattr(
        "thermal_viewer.error_handling._show_dialog", lambda text: shown.append(text)
    )
    install_global_excepthook()
    try:
        raise ValueError("boom")
    except ValueError:
        sys.excepthook(*sys.exc_info())
    assert len(shown) == 1
