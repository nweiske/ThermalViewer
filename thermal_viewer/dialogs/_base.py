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
    vom Scroll-Zustand des Inhalts darueber. outer_layout behaelt bewusst
    seine normalen (nicht auf 0 gesetzten) Rand-Abstaende -- der Aufrufer
    haengt die Dialog-Buttons direkt an outer_layout (siehe oben), ein
    auf 0 gesetzter Rand liess diese bisher flächig an den Fensterkanten
    links/rechts/unten kleben statt des ueblichen Dialog-Randes
    (Bugfix). Der Inhalt selbst bekommt seinen Rand weiterhin ganz normal
    ueber content_layouts eigene (Default-)Contents-Margins."""
    outer_layout = QtWidgets.QVBoxLayout(dialog)
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
    tatsaechliche Inhaltshoehe kennt.

    Bugfix: nutzt den Bildschirm, auf dem der Dialog tatsaechlich
    erscheint (dialog.screen(), wie schon in widgets.py:
    UpwardSafeComboBox.showPopup), NICHT QApplication.primaryScreen() --
    auf einem Mehrschirm-System mit einem kleineren sekundaeren Monitor
    waere sonst weiterhin die (ggf. groessere) PRIMAERE Bildschirmhoehe
    massgeblich, und der Dialog koennte auf dem tatsaechlich genutzten
    Schirm trotzdem ueber dessen Rand hinausragen -- genau der Bug, den
    diese Funktion beheben soll.

    Bugfix (Breite): dialog.sizeHint() allein unterschaetzt oft die
    tatsaechlich benoetigte Breite, weil QScrollArea.sizeHint() (anders
    als das sizeHint() seines eigenen Inhalts-Widgets) sehr konservativ
    ist -- ohne Korrektur oeffnete sich z.B. VideoExportDialog (vier
    gleich hohe Boxen inkl. Vorschau in einer Zeile) serienmaessig mit
    einer unnoetigen Horizontal-Scrollbar, obwohl der Bildschirm genug
    Breite dafuer haette. Daher zusaetzlich das sizeHint() des
    gescrollten Inhalts-Widgets heranziehen, falls vorhanden."""
    screen = dialog.screen() if hasattr(dialog, "screen") else None
    if screen is None:
        screen = QtWidgets.QApplication.primaryScreen()
    if screen is None:
        return
    available = screen.availableGeometry()
    available_height = available.height() - margin
    # Bugfix: QLayout cached sein sizeHint() und aktualisiert es nicht
    # IMMER sofort bei setVisible() auf einem Kind (z.B. wenn eine Box erst
    # per enable_preview() NACH dem ersten Aufruf dieser Funktion sichtbar
    # wird) -- die invalidate()/activate() HIER, VOR dem dialog.sizeHint()
    # unten, sorgt dafuer, dass sowohl Breite als auch Hoehe aus hint die
    # AKTUELLE (nicht die veraltete) Inhaltsgroesse widerspiegeln. Ein
    # frueherer Stand rief dialog.sizeHint() bereits VOR dieser Aktualisierung
    # ab und reparierte danach nur noch die Breite separat (ueber
    # content_width unten) -- hint.height() blieb dabei veraltet und konnte
    # eine gerade erst sichtbar gewordene, hoehen-relevante Box uebersehen.
    scroll_area = dialog.findChild(QtWidgets.QScrollArea)
    if scroll_area is not None and scroll_area.widget() is not None:
        content_layout = scroll_area.widget().layout()
        if content_layout is not None:
            content_layout.invalidate()
            content_layout.activate()
    hint = dialog.sizeHint()
    width = hint.width()
    if scroll_area is not None and scroll_area.widget() is not None:
        # Platz fuer eine evtl. noetige Scrollbar/Rahmen grob mit einrechnen,
        # ohne hier bereits auf die (noch nicht final bekannte) Scrollbar-
        # Sichtbarkeit angewiesen zu sein.
        scrollbar_allowance = QtWidgets.QApplication.style().pixelMetric(
            QtWidgets.QStyle.PM_ScrollBarExtent
        )
        content_width = scroll_area.widget().sizeHint().width() + scrollbar_allowance
        width = max(width, content_width)
    width = min(width, available.width() - margin)
    height = min(hint.height(), available_height)
    if width > dialog.width() or hint.height() > available_height:
        dialog.resize(width, height)


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
