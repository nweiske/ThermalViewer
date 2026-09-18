"""Globaler Auffang für unbehandelte Ausnahmen in Qt-Slots (Menü-/Knopf-
Klicks, Signal-Handler, ...).

Bugreport: "Rohdatenbereinigung lässt sich über die UI nicht mehr öffnen ...
Button war/ist augenscheinlich funktionsfähig, es öffnet sich aber kein
Fenster". Konnte mit synthetischen Daten nicht reproduziert werden -- der
wahrscheinlichste Grund ist aber gar kein Logikfehler an dieser einen
Stelle, sondern ein grundsätzliches Problem: PySide6/Qt ruft bei einer
Python-Exception INNERHALB eines Slots (z.B. hinter triggered.connect(...))
zwar sys.excepthook auf, dessen STANDARD-Verhalten ist aber nur, den
Traceback auf stderr auszugeben, OHNE die Anwendung zu beenden -- der
Knopf/Menüpunkt wirkt danach weiterhin "funktionsfähig", nur die eigentlich
ausgelöste Aktion (hier: der Dialog) bleibt aus. Der release.yml-Build läuft
mit PyInstallers --windowed (siehe .github/workflows/release.yml) -- OHNE
angehängte Konsole geht dieser stderr-Text schlicht ins Leere, der Nutzer
sieht buchstäblich NICHTS. Dieses Modul ersetzt sys.excepthook durch einen
Handler, der (a) den vollen Traceback zusätzlich in eine Log-Datei im
Home-Verzeichnis schreibt und (b) eine sichtbare Fehlermeldung zeigt --
damit ein zukünftiges Auftreten (dieser oder jeder andere Slot-Fehler)
NICHT mehr spurlos verschwindet, sondern gemeldet und nachvollziehbar wird."""
from __future__ import annotations

import sys
import traceback
from datetime import datetime
from pathlib import Path

from qtpy import QtWidgets

_LOG_PATH = Path.home() / "ThermalViewer" / "fehlerprotokoll.log"


def install_global_excepthook() -> None:
    original_hook = sys.excepthook

    def _handle(exc_type, exc_value, exc_tb):
        # KeyboardInterrupt (Strg+C in einer evtl. angehängten Konsole)
        # unveraendert durchreichen -- kein Anwendungsfehler.
        if issubclass(exc_type, KeyboardInterrupt):
            original_hook(exc_type, exc_value, exc_tb)
            return
        text = "".join(traceback.format_exception(exc_type, exc_value, exc_tb))
        _write_log(text)
        _show_dialog(text)

    sys.excepthook = _handle


def _write_log(text: str) -> None:
    try:
        _LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        with _LOG_PATH.open("a", encoding="utf-8") as f:
            f.write(f"\n=== {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} ===\n{text}")
    except OSError:
        # Log-Verzeichnis nicht beschreibbar (z.B. schreibgeschuetztes
        # Medium) -- die sichtbare Meldung (_show_dialog) bleibt der
        # primaere Kanal, das Schreiben selbst darf hier keinesfalls eine
        # weitere Ausnahme auf dem eh schon fehlerhaften Pfad auswerfen.
        pass


def _show_dialog(text: str) -> None:
    app = QtWidgets.QApplication.instance()
    if app is None:
        return
    box = QtWidgets.QMessageBox()
    box.setIcon(QtWidgets.QMessageBox.Icon.Critical)
    box.setWindowTitle("Unerwarteter Fehler")
    box.setText(
        "Eine Aktion konnte nicht ausgeführt werden (unerwarteter Fehler).\n\n"
        f"Details wurden protokolliert unter:\n{_LOG_PATH}"
    )
    box.setDetailedText(text)
    box.setStandardButtons(QtWidgets.QMessageBox.StandardButton.Ok)
    box.exec()
