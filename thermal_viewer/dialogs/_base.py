"""Gemeinsame Basis fuer alle Export-/Import-Dialoge dieses Pakets."""
from __future__ import annotations

from qtpy import QtCore, QtWidgets


def _disable_enter_auto_accept(buttons: QtWidgets.QDialogButtonBox) -> None:
    """Entzieht den Standard-Knoepfen die "autoDefault"-Rolle.

    Fuer sich allein NICHT ausreichend, um ENTER in einem Zahlenfeld vom
    Schliessen des Dialogs abzuhalten (QDialog.keyPressEvent() findet in der
    Praxis trotzdem einen Knopf zum Ausloesen) -- siehe _NoEnterAutoAccept
    fuer den eigentlich wirksamen Teil des Fixes. Bleibt zusaetzlich
    gesetzt, damit auch ein rein optischer "Default-Rahmen" um den
    OK-Knopf gar nicht erst entsteht."""
    for button in buttons.buttons():
        button.setAutoDefault(False)
        button.setDefault(False)


class _NoEnterAutoAccept:
    """Mixin: verhindert, dass ENTER in einem beliebigen Eingabefeld des
    Dialogs (Spinbox, Zeilenfeld, ...) den Dialog sofort schliesst/uebernimmt.

    Bugfix: QDialog.keyPressEvent() sucht bei ENTER/RETURN -- unabhaengig
    davon, welches Kindwidget gerade den Fokus haelt -- selbststaendig nach
    einem passenden Knopf und loest dessen click() aus (autoDefault/default
    auf False zu setzen genuegt dafuer in der Praxis NICHT). Bei einem
    Zahlenfeld wie DPI/Frame-Bereich/Video-FPS fuehrte ein per ENTER
    bestaetigter Wert dadurch ungewollt sofort zum Schliessen des Dialogs
    (Bugreport: "Wert per ENTER aendern soll nur den Wert uebernehmen, nicht
    direkt zum Speichern-Dialog weiterspringen"). Hier wird ENTER/RETURN
    daher bereits VOR QDialog's eigener keyPressEvent-Behandlung abgefangen,
    ausser der Fokus liegt bereits direkt auf einem QPushButton (z.B. nach
    Tab-Navigation zum OK-Knopf) -- dort soll ENTER weiterhin ganz normal
    einen Klick ausloesen."""

    def keyPressEvent(self, event) -> None:
        if event.key() in (QtCore.Qt.Key_Enter, QtCore.Qt.Key_Return):
            if not isinstance(self.focusWidget(), QtWidgets.QPushButton):
                event.accept()
                return
        super().keyPressEvent(event)
