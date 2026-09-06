"""Kleinere, in sich abgeschlossene Dialoge: Maßstab-Länge, eigener
Start-Zeitpunkt für den Export, sowie die Achsen-Einstellungen (X/Y-
Wertebereich und -Schrittweite) eines einzelnen Kurven-Graphen."""
from __future__ import annotations

from datetime import datetime

from qtpy import QtCore, QtWidgets

from ..widgets import LocaleTolerantDoubleSpinBox
from ._base import _disable_enter_auto_accept, _NoEnterAutoAccept


class RulerLengthDialog(_NoEnterAutoAccept, QtWidgets.QDialog):
    """Fragt die reale Länge (in mm) der Maßstab-Referenzlinie ab -- sowohl
    beim erstmaligen Festlegen als auch beim späteren Nachbearbeiten per
    Doppelklick auf die Linie/Beschriftung (Punkt 11), damit nicht jedes Mal
    der komplette Maßstab gelöscht und neu gezeichnet werden muss."""

    def __init__(self, parent, current_mm: float = 10.0):
        super().__init__(parent)
        self.setWindowTitle("Maßstab")

        layout = QtWidgets.QVBoxLayout(self)
        form = QtWidgets.QFormLayout()
        self.spin_mm = LocaleTolerantDoubleSpinBox()
        self.spin_mm.setDecimals(3)
        self.spin_mm.setRange(0.001, 1_000_000.0)
        self.spin_mm.setValue(current_mm)
        self.spin_mm.setSuffix(" mm")
        form.addRow("Länge dieser Linie:", self.spin_mm)
        layout.addLayout(form)

        buttons = QtWidgets.QDialogButtonBox(
            QtWidgets.QDialogButtonBox.StandardButton.Ok | QtWidgets.QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        _disable_enter_auto_accept(buttons)
        layout.addWidget(buttons)

    def mm_value(self) -> float:
        return self.spin_mm.value()


class StartTimestampDialog(_NoEnterAutoAccept, QtWidgets.QDialog):
    """Fragt einen eigenen Start-Zeitpunkt (Datum + Uhrzeit) ab -- fuer den
    Bildstapel-Export, wenn der Dateiname-Präfix Zeitstempel-Platzhalter
    (YYYY/MM/DD/hh/mm/ss) enthält, die geladene Messreihe aber keinen
    echten, aus den Dateinamen erkannten Zeitstempel hat (siehe
    MainWindow._resolve_export_timestamps). Der gewählte Zeitpunkt wird als
    neuer Zeitstempel des ERSTEN Frames verwendet, die relativen Abstände
    zwischen den Frames bleiben dabei erhalten."""

    def __init__(self, parent, current: datetime):
        super().__init__(parent)
        self.setWindowTitle("Eigenen Startpunkt festlegen")

        layout = QtWidgets.QVBoxLayout(self)
        intro = QtWidgets.QLabel(
            "Zeitpunkt des ERSTEN Frames -- alle weiteren Frames übernehmen denselben "
            "zeitlichen Abstand wie in der geladenen Messreihe."
        )
        intro.setWordWrap(True)
        layout.addWidget(intro)

        form = QtWidgets.QFormLayout()
        self.edit_datetime = QtWidgets.QDateTimeEdit(QtCore.QDateTime(current))
        self.edit_datetime.setCalendarPopup(True)
        self.edit_datetime.setDisplayFormat("yyyy-MM-dd HH:mm:ss")
        form.addRow("Start-Zeitpunkt:", self.edit_datetime)
        layout.addLayout(form)

        buttons = QtWidgets.QDialogButtonBox(
            QtWidgets.QDialogButtonBox.StandardButton.Ok | QtWidgets.QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        _disable_enter_auto_accept(buttons)
        layout.addWidget(buttons)

    def value(self) -> datetime:
        # .toPython() (PySide6) vs .toPyDateTime() (PyQt5/6) unterscheiden
        # sich je nach Qt-Binding -- stattdessen ueber Date/Time-Komponenten
        # manuell zusammensetzen, das ist in JEDEM qtpy-Binding gleich
        # verfuegbar (wichtig fuer den PyQt5-Windows-7-Legacy-Build).
        qdt = self.edit_datetime.dateTime()
        d, t = qdt.date(), qdt.time()
        return datetime(d.year(), d.month(), d.day(), t.hour(), t.minute(), t.second())


class AxisSettingsDialog(_NoEnterAutoAccept, QtWidgets.QDialog):
    """Manueller Wertebereich (X/Y) und manuelle Schrittweite fuer EINEN
    Kurven-Graphen -- Ersatz fuer das schwer auffindbare "X/Y axis"-
    Untermenue im pyqtgraph-Standard-Rechtsklickmenue (Nutzerwunsch: "die
    Achsen ... nach belieben einstellen ... mehr Entscheidungsfreiheit").

    Die X-Achsen-Schrittweite wirkt nur im "Laufzeit"-Modus (echte Uhrzeit
    waehlt weiterhin automatisch gut lesbare Kalenderabstaende, z.B. alle
    5/15/30 Minuten) -- im Laufzeit-Modus sonst z.B. haesslich unrunde
    Werte wie 00:00:24, 00:01:24 statt 00:00:00, 00:01:00, weil pyqtgraph
    seine "schoenen" Intervalle an der ABSOLUTEN Uhrzeit statt am
    Aufnahmebeginn ausrichtet (siehe TimeAxisItem.tickValues)."""

    def __init__(
        self,
        parent,
        current_x_min: float,
        current_x_max: float,
        current_y_min: float,
        current_y_max: float,
        x_manual: bool = False,
        y_manual_range: bool = False,
        y_spacing: float | None = None,
        x_runtime_mode: bool = False,
        x_spacing: float | None = None,
    ):
        super().__init__(parent)
        self.setWindowTitle("Achsen einstellen")

        layout = QtWidgets.QVBoxLayout(self)

        x_box = QtWidgets.QGroupBox("X-Achse (Zeit)")
        x_layout = QtWidgets.QVBoxLayout(x_box)
        self.chk_x_manual = QtWidgets.QCheckBox("Wertebereich manuell festlegen")
        x_layout.addWidget(self.chk_x_manual)
        x_form = QtWidgets.QFormLayout()
        self.spin_x_min = LocaleTolerantDoubleSpinBox()
        self.spin_x_min.setRange(-1e12, 1e12)
        self.spin_x_min.setDecimals(1)
        self.spin_x_min.setSuffix(" s")
        self.spin_x_min.setValue(current_x_min)
        self.spin_x_max = LocaleTolerantDoubleSpinBox()
        self.spin_x_max.setRange(-1e12, 1e12)
        self.spin_x_max.setDecimals(1)
        self.spin_x_max.setSuffix(" s")
        self.spin_x_max.setValue(current_x_max)
        x_form.addRow("Von (Sekunden seit Aufnahmebeginn):", self.spin_x_min)
        x_form.addRow("Bis (Sekunden seit Aufnahmebeginn):", self.spin_x_max)
        x_layout.addLayout(x_form)

        self.chk_x_manual_spacing = QtWidgets.QCheckBox("Tick-Abstand (Laufzeit-Modus) manuell festlegen")
        x_layout.addWidget(self.chk_x_manual_spacing)
        x_spacing_form = QtWidgets.QFormLayout()
        self.spin_x_spacing = LocaleTolerantDoubleSpinBox()
        self.spin_x_spacing.setRange(0.1, 1e7)
        self.spin_x_spacing.setDecimals(1)
        self.spin_x_spacing.setSuffix(" s")
        self.spin_x_spacing.setValue(x_spacing if x_spacing is not None else 60.0)
        x_spacing_form.addRow("Hauptintervall:", self.spin_x_spacing)
        x_layout.addLayout(x_spacing_form)
        x_note = QtWidgets.QLabel(
            "Hinweis: Der Tick-Abstand wirkt nur, solange die Zeitachse auf „Laufzeit“ "
            "steht (Umschalter unter dem Graphen). Bei „Uhrzeit“ wählt sie weiterhin "
            "automatisch gut lesbare Kalenderabstände."
            if not x_runtime_mode
            else "Zeitachse steht aktuell auf „Laufzeit“ -- der Tick-Abstand wirkt sofort."
        )
        x_note.setWordWrap(True)
        x_layout.addWidget(x_note)
        layout.addWidget(x_box)

        y_box = QtWidgets.QGroupBox("Y-Achse (Temperatur)")
        y_layout = QtWidgets.QVBoxLayout(y_box)
        self.chk_y_manual_range = QtWidgets.QCheckBox("Wertebereich manuell festlegen")
        y_layout.addWidget(self.chk_y_manual_range)
        y_range_form = QtWidgets.QFormLayout()
        self.spin_y_min = LocaleTolerantDoubleSpinBox()
        self.spin_y_min.setRange(-273.15, 10000.0)
        self.spin_y_min.setDecimals(1)
        self.spin_y_min.setSuffix(" °C")
        self.spin_y_min.setValue(current_y_min)
        self.spin_y_max = LocaleTolerantDoubleSpinBox()
        self.spin_y_max.setRange(-273.15, 10000.0)
        self.spin_y_max.setDecimals(1)
        self.spin_y_max.setSuffix(" °C")
        self.spin_y_max.setValue(current_y_max)
        y_range_form.addRow("Min:", self.spin_y_min)
        y_range_form.addRow("Max:", self.spin_y_max)
        y_layout.addLayout(y_range_form)

        self.chk_y_manual_spacing = QtWidgets.QCheckBox("Schrittweite (Hauptintervall) manuell festlegen")
        y_layout.addWidget(self.chk_y_manual_spacing)
        y_spacing_form = QtWidgets.QFormLayout()
        self.spin_y_spacing = LocaleTolerantDoubleSpinBox()
        self.spin_y_spacing.setRange(0.01, 1000.0)
        self.spin_y_spacing.setDecimals(2)
        self.spin_y_spacing.setSuffix(" °C")
        self.spin_y_spacing.setValue(y_spacing if y_spacing is not None else max(0.01, (current_y_max - current_y_min) / 5))
        y_spacing_form.addRow("Hauptintervall:", self.spin_y_spacing)
        y_layout.addLayout(y_spacing_form)
        layout.addWidget(y_box)

        self.chk_x_manual.toggled.connect(self._update_axis_range_enabled)
        self.chk_x_manual_spacing.toggled.connect(self._update_axis_range_enabled)
        self.chk_y_manual_range.toggled.connect(self._update_axis_range_enabled)
        self.chk_y_manual_spacing.toggled.connect(self._update_axis_range_enabled)
        # Checkboxen spiegeln den TATSAECHLICH gerade aktiven Achsen-Zustand
        # wider (statt beim erneuten Oeffnen immer wieder bei "Automatisch"
        # zu starten) -- sonst wirkte ein zuvor gesetzter manueller Bereich
        # beim Wiederoeffnen faelschlich so, als waere er nie angewendet
        # worden.
        self.chk_x_manual.setChecked(x_manual)
        self.chk_x_manual_spacing.setChecked(x_spacing is not None)
        self.chk_y_manual_range.setChecked(y_manual_range)
        self.chk_y_manual_spacing.setChecked(y_spacing is not None)
        self._update_axis_range_enabled()

        buttons = QtWidgets.QDialogButtonBox(
            QtWidgets.QDialogButtonBox.StandardButton.Ok | QtWidgets.QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self._on_accept)
        buttons.rejected.connect(self.reject)
        _disable_enter_auto_accept(buttons)
        layout.addWidget(buttons)

    def _update_axis_range_enabled(self) -> None:
        self.spin_x_min.setEnabled(self.chk_x_manual.isChecked())
        self.spin_x_max.setEnabled(self.chk_x_manual.isChecked())
        self.spin_x_spacing.setEnabled(self.chk_x_manual_spacing.isChecked())
        self.spin_y_min.setEnabled(self.chk_y_manual_range.isChecked())
        self.spin_y_max.setEnabled(self.chk_y_manual_range.isChecked())
        self.spin_y_spacing.setEnabled(self.chk_y_manual_spacing.isChecked())

    def _on_accept(self) -> None:
        if self.chk_x_manual.isChecked() and self.spin_x_max.value() <= self.spin_x_min.value():
            QtWidgets.QMessageBox.warning(
                self, "Ungültiger Bereich", "Bei der X-Achse muss „Bis“ größer als „Von“ sein."
            )
            return
        if self.chk_y_manual_range.isChecked() and self.spin_y_max.value() <= self.spin_y_min.value():
            QtWidgets.QMessageBox.warning(
                self, "Ungültiger Bereich", "Bei der Y-Achse muss „Max“ größer als „Min“ sein."
            )
            return
        self.accept()

    def x_manual(self) -> bool:
        return self.chk_x_manual.isChecked()

    def x_range(self) -> tuple[float, float]:
        return self.spin_x_min.value(), self.spin_x_max.value()

    def x_manual_spacing(self) -> bool:
        return self.chk_x_manual_spacing.isChecked()

    def x_spacing(self) -> float:
        return self.spin_x_spacing.value()

    def y_manual_range(self) -> bool:
        return self.chk_y_manual_range.isChecked()

    def y_range(self) -> tuple[float, float]:
        return self.spin_y_min.value(), self.spin_y_max.value()

    def y_manual_spacing(self) -> bool:
        return self.chk_y_manual_spacing.isChecked()

    def y_spacing(self) -> float:
        return self.spin_y_spacing.value()
