"""Wiederverwendbare, angepasste Qt-Widgets (bewusst getrennt von roi.py, das
ausschliesslich pyqtgraph-Grafik-Items enthaelt)."""
from __future__ import annotations

from qtpy import QtGui, QtWidgets


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
