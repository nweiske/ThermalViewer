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


def _wrap_in_scroll_area(dialog: QtWidgets.QDialog) -> tuple[QtWidgets.QVBoxLayout, QtWidgets.QVBoxLayout]:
    """Verpackt den gesamten Dialog-Inhalt in ein QScrollArea (Nutzerwunsch:
    "Packe die ganzen Widgets im Zweifel auch in ein Scrollfeld mit ein,
    dann kann man wenigstens Scrollen und sich den Export-Manager so groß
    hinziehen, wie man möchte") -- Hintergrund: die Export-Dialoge (Grafik-/
    Video-Export) haben durch die vielen optionalen Abschnitte (Vorschau,
    Achsen, Ebenen, ...) bereits ohne Scroll-Moeglichkeit den Bildschirm
    ueberragt (Bugreport: "das Fenster aus dem Bildschirm hinausragt").

    Gibt (outer_layout, content_layout) zurueck -- der Aufrufer baut seine
    gesamte bisherige UI unveraendert in content_layout auf (genau das
    Layout, das vorher direkt "QtWidgets.QVBoxLayout(self)" war), haengt
    die Dialog-Buttons aber bewusst an outer_layout statt an content_layout,
    damit sie IMMER sichtbar am unteren Fensterrand bleiben, unabhaengig
    vom Scroll-Zustand des Inhalts darueber."""
    outer_layout = QtWidgets.QVBoxLayout(dialog)
    outer_layout.setContentsMargins(0, 0, 0, 0)
    scroll_area = QtWidgets.QScrollArea()
    scroll_area.setWidgetResizable(True)
    scroll_area.setFrameShape(QtWidgets.QFrame.NoFrame)
    content = QtWidgets.QWidget()
    content_layout = QtWidgets.QVBoxLayout(content)
    scroll_area.setWidget(content)
    outer_layout.addWidget(scroll_area)
    return outer_layout, content_layout


def _cap_initial_dialog_height(dialog: QtWidgets.QDialog, margin: int = 60) -> None:
    """Begrenzt NUR die anfaengliche Fenstergroesse auf die verfuegbare
    Bildschirmhoehe -- per Fensterrand bleibt der Dialog (siehe
    _wrap_in_scroll_area, setWidgetResizable=True) trotzdem beliebig
    groesser/kleiner ziehbar, wie vom Nutzer gewuenscht. Am Ende von
    __init__ aufzurufen, NACHDEM der komplette Inhalt (inkl. Buttons)
    aufgebaut ist, da sonst dialog.sizeHint() noch nicht die volle
    tatsaechliche Inhaltshoehe kennt."""
    screen = QtWidgets.QApplication.primaryScreen()
    if screen is None:
        return
    available_height = screen.availableGeometry().height() - margin
    hint = dialog.sizeHint()
    if hint.height() > available_height:
        dialog.resize(hint.width(), available_height)


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
