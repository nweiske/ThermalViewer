"""Tests fuer thermal_viewer/widgets.py: LocaleTolerantDoubleSpinBox darf
sowohl Komma als auch Punkt beim Einlesen akzeptieren, waehrend die Anzeige
unveraendert dem aktuellen Locale folgt."""
from __future__ import annotations

from qtpy import QtGui

from thermal_viewer.widgets import LocaleTolerantDoubleSpinBox


def _spin(qapp, decimals: int = 1) -> LocaleTolerantDoubleSpinBox:
    spin = LocaleTolerantDoubleSpinBox()
    spin.setRange(-1000.0, 1000.0)
    spin.setDecimals(decimals)
    return spin


def test_value_from_text_accepts_comma_decimal(qapp):
    spin = _spin(qapp)
    assert spin.valueFromText("12,3") == 12.3


def test_value_from_text_accepts_dot_decimal(qapp):
    spin = _spin(qapp)
    assert spin.valueFromText("45,6".replace(",", ".")) == 45.6


def test_value_from_text_accepts_negative_values_both_separators(qapp):
    spin = _spin(qapp)
    assert spin.valueFromText("-12,5") == -12.5
    assert spin.valueFromText("-12.5") == -12.5


def test_validate_does_not_mutate_the_typed_text(qapp):
    # Bugfix-Invariante: validate() prueft auf einer NORMALISIERTEN Kopie,
    # gibt aber den vom Nutzer getippten Text UNVERAENDERT zurueck -- sonst
    # wuerde das gerade getippte Zeichen im Feld unter der Hand ausgetauscht.
    spin = _spin(qapp, decimals=2)
    state, text, pos = spin.validate("12,34", 5)
    assert text == "12,34"
    assert state != QtGui.QValidator.State.Invalid


def test_display_formatting_is_not_overridden():
    # Nur das EINLESEN (validate/valueFromText) wird toleranter gemacht --
    # die ANZEIGE (textFromValue) bleibt bewusst unveraendert (explizit
    # gewuenscht: "am Output nichts aendern"). Regressions-Wächter: schlaegt
    # fehl, falls jemand versehentlich auch textFromValue ueberschreibt.
    assert "textFromValue" not in LocaleTolerantDoubleSpinBox.__dict__
