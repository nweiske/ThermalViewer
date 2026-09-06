"""Wiederverwendbare, angepasste Qt-Widgets (bewusst getrennt von roi.py, das
ausschliesslich pyqtgraph-Grafik-Items enthaelt)."""
from __future__ import annotations

from qtpy import QtCore, QtGui, QtWidgets


class LocaleTolerantDoubleSpinBox(QtWidgets.QDoubleSpinBox):
    """QDoubleSpinBox, die beim Tippen SOWOHL Punkt ALS AUCH Komma als
    Dezimaltrennzeichen akzeptiert (Punkt 12) -- unabhaengig davon, ob das
    System-Locale eigentlich nur eines von beiden vorsieht. Nur das
    EINLESEN wird toleranter gemacht (validate/valueFromText); die ANZEIGE
    (textFromValue, z.B. weiterhin Komma bei deutschem Locale) bleibt
    bewusst UNVERAENDERT, wie explizit gewuenscht ("am Output nichts
    aendern")."""

    def _normalized(self, text: str) -> str:
        decimal_point = self.locale().decimalPoint()
        other = "," if decimal_point == "." else "."
        return text.replace(other, decimal_point)

    def validate(self, text: str, pos: int) -> tuple[QtGui.QValidator.State, str, int]:
        # Auf der NORMALISIERTEN Kopie pruefen (damit z.B. "10,5" bei
        # englischem Locale trotzdem als gueltig erkannt wird), aber den
        # ORIGINALEN Text unveraendert zurueckgeben -- sonst wuerde das
        # gerade getippte Zeichen im Feld unter der Hand ausgetauscht.
        state, _normalized_text, _pos = super().validate(self._normalized(text), pos)
        return state, text, pos

    def valueFromText(self, text: str) -> float:
        return super().valueFromText(self._normalized(text))


class UpwardSafeComboBox(QtWidgets.QComboBox):
    """QComboBox, deren Dropdown-Liste sich noetigenfalls NACH OBEN statt nach
    unten oeffnet (Bugreport: "im Vollbild-Modus geht das Dropdown unten aus
    dem Bildschirm raus -- ich kann die letzte Option nicht auswaehlen, weil
    ich sie gar nicht sehe"). Betrifft vor allem Comboboxen nahe am unteren
    Fensterrand, z.B. die "Zeitachse"-Auswahl direkt unterhalb der Kurven-
    Graphen -- Qt richtet die Popup-Liste sonst IMMER nach unten aus und
    verschiebt sie zwar bei Bedarf nach links/rechts (damit sie horizontal
    auf den Bildschirm passt), aber nicht nach oben."""

    def showPopup(self) -> None:
        super().showPopup()
        popup = self.view().window()
        screen = self.screen() if hasattr(self, "screen") else None
        if popup is None or screen is None:
            return
        available = screen.availableGeometry()
        popup_geom = popup.geometry()
        overflow = (popup_geom.y() + popup_geom.height()) - (available.y() + available.height())
        if overflow <= 0:
            return
        # Combobox-Oberkante als untere Kante der nach oben geklappten Liste
        # nehmen (direkt anschliessend, wie eine normale nach-unten-Liste
        # direkt an der Unterkante ansetzt).
        combo_top_global = self.mapToGlobal(QtCore.QPoint(0, 0)).y()
        new_y = max(available.y(), combo_top_global - popup_geom.height())
        popup.move(popup_geom.x(), new_y)
