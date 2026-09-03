"""Gemeinsame Fixtures fuer die gesamte Test-Suite.

Diese Suite ist das Build-Freigabe-Gate (siehe scripts/build_local.ps1 und
.github/workflows/release.yml): sie laeuft vor jedem Bauen der exe --
lokal wie in GitHub Actions -- und muss durchlaufen, bevor PyInstaller
ueberhaupt gestartet wird. Schlaegt ein Test fehl, bricht der jeweilige
Build-Schritt ab (pytest liefert einen Exit-Code != 0 zurueck).
"""
from __future__ import annotations

import os

# MUSS passieren, BEVOR irgendein Testmodul qtpy/Qt importiert -- sonst
# wuerde die App versuchen, ein echtes (in CI nicht vorhandenes) Fenster zu
# oeffnen. pytest importiert conftest.py vor den eigentlichen Testmodulen,
# daher ist der Zeitpunkt hier garantiert frueh genug. setdefault() statt
# direkter Zuweisung, damit ein von aussen (z.B. lokal) gesetzter Wert
# weiterhin Vorrang hat.
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from datetime import datetime, timedelta  # noqa: E402
from pathlib import Path  # noqa: E402

import pytest  # noqa: E402
from qtpy import QtCore, QtWidgets  # noqa: E402


@pytest.fixture(autouse=True)
def isolated_qsettings(tmp_path):
    """Verhindert, dass Tests die ECHTEN QSettings des Nutzers (Windows-
    Registry bzw. plattformeigener Ort) lesen oder ueberschreiben --
    MainWindow speichert dort u.a. Design, Farbverlauf, Dateinamens-Schema,
    zuletzt genutzte Live-Cursor-Bereichsgroesse. Route alle
    QSettings(organisation, anwendung)-Konstruktionsaufrufe waehrend JEDES
    Tests in eine isolierte, temporaere ini-Datei um (autouse=True, gilt
    also automatisch ueberall, ohne dass einzelne Tests das anfordern
    muessen).

    Bugfix: QSettings(organisation, anwendung) -- GENAU die Zwei-Argument-
    Form, die MainWindow tatsaechlich verwendet -- ignoriert
    setDefaultFormat()/setPath() vollstaendig und verwendet auf Windows
    IMMER NativeFormat (die echte Registry), egal was hier vorher gesetzt
    wurde (Qt-Eigenheit: dieser Konstruktor-Overload pinnt das Format fest
    auf NativeFormat). Die alte, nur auf setDefaultFormat/setPath gestuetzte
    Version dieser Fixture isolierte dadurch NICHTS -- jeder Testlauf las
    UND SCHRIEB (z.B. GraphicExportDialog.separate(), das seinen Wert beim
    blossen Auslesen zurueckschreibt) tatsaechlich in die echte Windows-
    Registry des Nutzers, und ein einmal dort haengengebliebener Wert
    beeinflusste JEDEN nachfolgenden Testlauf (auch in kuenftigen Sitzungen)
    unbemerkt weiter. Fix: die Klasse QtCore.QSettings selbst durch eine
    Unterklasse ersetzen, die IMMER explizit IniFormat + isolierten Pfad an
    den Basiskonstruktor durchreicht (denselben Ansatz nutzt bereits
    tests/smoke_test.py erfolgreich) -- das umgeht den NativeFormat-Zwang
    vollstaendig, unabhaengig davon, mit wie vielen Argumenten der Aufrufer
    QSettings(...) konstruiert."""
    real_qsettings = QtCore.QSettings
    settings_path = str(tmp_path / "settings.ini")

    class _IsolatedQSettings(real_qsettings):
        def __init__(self, *args, **kwargs):
            super().__init__(settings_path, real_qsettings.Format.IniFormat)

    QtCore.QSettings = _IsolatedQSettings
    try:
        yield
    finally:
        QtCore.QSettings = real_qsettings


@pytest.fixture(scope="session")
def qapp():
    """Eine einzige QApplication fuer den gesamten Testlauf (Qt erlaubt nur
    eine Instanz pro Prozess)."""
    app = QtWidgets.QApplication.instance()
    if app is None:
        app = QtWidgets.QApplication([])
    return app


@pytest.fixture
def synthetic_recording_folder(tmp_path: Path) -> Path:
    """Schreibt eine kleine, gueltige CSV-Messreihe (Standard-Namensschema
    "Record_YYYY-MM-DD_hh-mm-ss.csv", 20x20 Pixel, 5 Frames im
    Sekundenabstand ab 2026-01-01 12:00:00) nach tmp_path. 20x20 (statt z.B.
    nur 6x8) bewusst gross genug gewaehlt, damit ein 10x10-Live-Cursor-
    Fenster um ein zentrales Pixel (z.B. Zeile/Spalte 10) NICHT am Bildrand
    geclippt wird."""
    rows, cols, n_frames = 20, 20, 5
    t0 = datetime(2026, 1, 1, 12, 0, 0)
    for i in range(n_frames):
        ts = t0 + timedelta(seconds=i)
        name = f"Record_{ts.strftime('%Y-%m-%d_%H-%M-%S')}.csv"
        lines = []
        for r in range(rows):
            vals = [f"{(20 + i + r * 0.1 + c * 0.01):.1f}".replace(".", ",") for c in range(cols)]
            lines.append(";".join(vals) + ";")
        (tmp_path / name).write_text("\n".join(lines), encoding="utf-8")
    return tmp_path


@pytest.fixture
def main_window(qapp):
    """Frische, ungeladene MainWindow-Instanz -- pro Test neu, damit sich
    Zustand (ROIs, geladene Messreihe, ...) zwischen Tests nicht
    ueberschneidet."""
    from thermal_viewer.main_window import MainWindow

    mw = MainWindow()
    yield mw
    mw.close()


@pytest.fixture
def loaded_main_window(main_window, synthetic_recording_folder):
    """MainWindow mit bereits geladener synthetischer Messreihe (5 Frames)."""
    result = main_window._resolve_folder_and_pattern(synthetic_recording_folder)
    assert result is not None, "Namensschema-Abgleich fuer die synthetische Messreihe fehlgeschlagen"
    folder_path, pattern, strptime_fmt = result
    paths = sorted(folder_path.glob("*.csv"))
    ok = main_window._load_paths(paths, pattern=pattern, strptime_fmt=strptime_fmt)
    assert ok, "Laden der synthetischen Messreihe fehlgeschlagen"
    return main_window


@pytest.fixture
def roi_and_live_window(loaded_main_window):
    """loaded_main_window mit einem platzierten Messbereich UND einem
    fixierten Live-Cursor-Pixel -- Grundlage fuer die meisten Export-Tests."""
    mw = loaded_main_window
    mw._add_roi_entry()
    entry = mw.roi_entries[-1]
    entry.place(center_x=3, center_y=3, width=2, height=2)
    mw._recompute_curves()
    mw._update_live_cursor(2, 3)
    return mw
