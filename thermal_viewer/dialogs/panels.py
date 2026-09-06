"""Wiederverwendbare Export-Einstellungs-Bloecke (Farbskala, Achsen), die
sowohl von GraphicExportDialog als auch von VideoExportDialog eingebunden
werden."""
from __future__ import annotations

from qtpy import QtWidgets

from ..widgets import LocaleTolerantDoubleSpinBox
from .misc_dialogs import AxisSettingsDialog


class ColorScaleOverridePanel:
    """Der wiederverwendbare "Aktuelle/Eigene Einstellungen"-Block
    (Farbverlauf, Invertiert, Skalierung, Min/Max) -- gemeinsam genutzt von
    GraphicExportDialog und VideoExportDialog, damit beide Export-Wege
    dieselbe Freiheit bieten, die Farbdarstellung unabhängig von der gerade
    aktiven Anzeige zu wählen (Bugreport: "gebe mir dieselbe Freiheit wie
    in der UI"). group_box wird vom Aufrufer selbst ins eigene Layout
    eingehängt."""

    def __init__(
        self,
        colormaps: list[tuple[str, str]],
        current_colormap_index: int,
        current_invert: bool,
        current_level_mode: str,
        current_min: float,
        current_max: float,
    ) -> None:
        self.group_box = QtWidgets.QGroupBox("Farbskala / Legende")
        layout = QtWidgets.QVBoxLayout(self.group_box)

        self.radio_current = QtWidgets.QRadioButton("Aktuelle Anzeige-Einstellungen übernehmen")
        self.radio_custom = QtWidgets.QRadioButton("Eigene Einstellungen für diesen Export")
        self.radio_current.setChecked(True)
        layout.addWidget(self.radio_current)
        layout.addWidget(self.radio_custom)

        # Eingerueckt unter "Eigene Einstellungen" -- nur bei dieser Wahl
        # ueberhaupt nutzbar (siehe _update_enabled unten), soll optisch klar
        # als deren Unterpunkte erkennbar sein.
        indent_row = QtWidgets.QHBoxLayout()
        indent_row.addSpacing(20)
        form = QtWidgets.QFormLayout()
        self.combo_cmap = QtWidgets.QComboBox()
        for label, _name in colormaps:
            self.combo_cmap.addItem(label)
        self.combo_cmap.setCurrentIndex(current_colormap_index)
        self.chk_invert = QtWidgets.QCheckBox("Invertiert")
        self.chk_invert.setChecked(current_invert)
        form.addRow("Farbverlauf:", self.combo_cmap)
        form.addRow("", self.chk_invert)

        self.combo_level_mode = QtWidgets.QComboBox()
        self.combo_level_mode.addItem("Manuell", "manual")
        self.combo_level_mode.addItem("Automatisch: Pro Bild", "per_frame")
        self.combo_level_mode.addItem("Automatisch: Über gesamte Messung", "global")
        idx = self.combo_level_mode.findData(current_level_mode)
        self.combo_level_mode.setCurrentIndex(max(0, idx))
        form.addRow("Skalierung:", self.combo_level_mode)

        self.spin_min = LocaleTolerantDoubleSpinBox()
        self.spin_min.setRange(-100.0, 2000.0)
        self.spin_min.setDecimals(1)
        self.spin_min.setValue(current_min)
        self.spin_max = LocaleTolerantDoubleSpinBox()
        self.spin_max.setRange(-100.0, 2000.0)
        self.spin_max.setDecimals(1)
        self.spin_max.setValue(current_max)
        form.addRow("Min:", self.spin_min)
        form.addRow("Max:", self.spin_max)
        indent_row.addLayout(form)
        layout.addLayout(indent_row)

        self.radio_current.toggled.connect(self._update_enabled)
        self.radio_custom.toggled.connect(self._update_enabled)
        self._update_enabled()

    def _update_enabled(self) -> None:
        custom_enabled = self.radio_custom.isChecked()
        for w in (self.combo_cmap, self.chk_invert, self.combo_level_mode, self.spin_min, self.spin_max):
            w.setEnabled(custom_enabled)

    def use_custom(self) -> bool:
        return self.radio_custom.isChecked()

    def colormap_index(self) -> int:
        return self.combo_cmap.currentIndex()

    def invert(self) -> bool:
        return self.chk_invert.isChecked()

    def level_mode(self) -> str:
        return self.combo_level_mode.currentData()

    def min_max(self) -> tuple[float, float]:
        return self.spin_min.value(), self.spin_max.value()

    def range_invalid(self) -> bool:
        return _color_scale_range_invalid(self.radio_custom, self.combo_level_mode, self.spin_min, self.spin_max)


class AxisOverridePanel:
    """Der wiederverwendbare "Aktuelle Ansicht/Eigene Achsen-Einstellungen"-
    Block -- gemeinsam genutzt von GraphicExportDialog und VideoExportDialog
    (Nutzerwunsch: "mehr Gestaltungsmoeglichkeiten beim Exportieren ...
    Achsen-Labels/Ticklabels/Schrittweite"). Oeffnet dafuer bewusst denselben
    AxisSettingsDialog wie "Achsen einstellen..." im Hauptfenster
    (Wiedererkennung, keine doppelt gepflegte UI) -- der Export wendet das
    Ergebnis aber nur WAEHREND des Renderns an und lässt die Live-Ansicht
    unangetastet (siehe MainWindow._temporary_axis_override).

    current_axis_state: von MainWindow._gather_axis_state(widget) --
    x_min/x_max (Sekunden seit Aufnahmebeginn), x_auto, x_runtime_mode,
    x_spacing, y_min/y_max, y_auto, y_spacing."""

    def __init__(self, parent_dialog: QtWidgets.QDialog, current_axis_state: dict) -> None:
        self._parent_dialog = parent_dialog
        self._current_axis_state = current_axis_state
        # None (noch nicht konfiguriert) oder ein dict mit denselben Feldern,
        # die AxisSettingsDialog liefert -- wird beim Export nur angewendet,
        # wenn radio_custom aktiv UND overrides gesetzt ist (siehe
        # use_custom()/custom_overrides()).
        self.overrides: dict | None = None

        self.group_box = QtWidgets.QGroupBox("Achsen")
        layout = QtWidgets.QVBoxLayout(self.group_box)

        self.radio_current = QtWidgets.QRadioButton("Aktuelle Ansicht übernehmen")
        self.radio_current.setToolTip("Übernimmt Wertebereich und Tick-Abstand exakt so, wie sie gerade im Hauptfenster angezeigt werden.")
        self.radio_custom = QtWidgets.QRadioButton("Eigene Achsen-Einstellungen für diesen Export")
        self.radio_current.setChecked(True)
        layout.addWidget(self.radio_current)
        layout.addWidget(self.radio_custom)

        # Eingerueckt unter "Eigene Achsen-Einstellungen" -- Knopf UND
        # Zusammenfassungstext gehoeren beide zu DIESER Option (nicht zu
        # "Aktuelle Ansicht übernehmen" darueber), Bugfix: der Text lautete
        # zuvor im nicht konfigurierten Zustand "(wie aktuell im Hauptfenster
        # angezeigt)" -- das beschreibt aber die ANDERE (obere) Option und wirkte
        # dadurch irrefuehrend, gerade weil er direkt unter "Eigene Achsen-
        # Einstellungen" steht.
        indent_row = QtWidgets.QHBoxLayout()
        indent_row.addSpacing(20)
        self.btn_configure = QtWidgets.QPushButton("Einstellen…")
        self.lbl_summary = QtWidgets.QLabel("(noch nicht konfiguriert)")
        self.lbl_summary.setWordWrap(True)
        indent_row.addWidget(self.btn_configure)
        indent_row.addWidget(self.lbl_summary, 1)
        layout.addLayout(indent_row)

        self.btn_configure.clicked.connect(self._open_sub_dialog)
        self.radio_current.toggled.connect(self._update_enabled)
        self.radio_custom.toggled.connect(self._update_enabled)
        self._update_enabled()

    def _update_summary(self) -> None:
        if self.overrides is None:
            self.lbl_summary.setText("(noch nicht konfiguriert)")
        else:
            self.lbl_summary.setText("Eigene Achsen-Einstellungen gewählt.")

    def _open_sub_dialog(self) -> None:
        ov = self.overrides
        state = self._current_axis_state
        dialog = AxisSettingsDialog(
            self._parent_dialog,
            current_x_min=ov["x_range"][0] if ov else state["x_min"],
            current_x_max=ov["x_range"][1] if ov else state["x_max"],
            current_y_min=ov["y_range"][0] if ov else state["y_min"],
            current_y_max=ov["y_range"][1] if ov else state["y_max"],
            x_manual=ov["x_manual"] if ov else not state["x_auto"],
            y_manual_range=ov["y_manual_range"] if ov else not state["y_auto"],
            y_spacing=(ov["y_spacing"] if ov and ov["y_spacing_manual"] else state["y_spacing"]),
            x_runtime_mode=state["x_runtime_mode"],
            x_spacing=(ov["x_spacing"] if ov and ov["x_spacing_manual"] else state["x_spacing"]),
        )
        if dialog.exec() != QtWidgets.QDialog.DialogCode.Accepted:
            return
        self.overrides = {
            "x_manual": dialog.x_manual(), "x_range": dialog.x_range(),
            "x_spacing_manual": dialog.x_manual_spacing(), "x_spacing": dialog.x_spacing(),
            "y_manual_range": dialog.y_manual_range(), "y_range": dialog.y_range(),
            "y_spacing_manual": dialog.y_manual_spacing(), "y_spacing": dialog.y_spacing(),
        }
        self.radio_custom.setChecked(True)
        self._update_summary()

    def _update_enabled(self) -> None:
        self.btn_configure.setEnabled(self.radio_custom.isChecked())

    def use_custom(self) -> bool:
        return self.radio_custom.isChecked() and self.overrides is not None

    def custom_overrides(self) -> dict | None:
        """Siehe MainWindow._gather_axis_state()/_temporary_axis_override()
        für die Bedeutung der Felder -- None, falls use_custom() False ist."""
        return self.overrides if self.use_custom() else None

    def incomplete(self) -> bool:
        """True, wenn "Eigene Achsen-Einstellungen" gewählt, aber noch nicht
        über "Einstellen…" konfiguriert wurde."""
        return self.radio_custom.isChecked() and self.overrides is None


def _color_scale_range_invalid(
    radio_custom: QtWidgets.QRadioButton,
    combo_level_mode: QtWidgets.QComboBox,
    spin_min: QtWidgets.QDoubleSpinBox,
    spin_max: QtWidgets.QDoubleSpinBox,
) -> bool:
    """True, wenn "Eigene Einstellungen" mit manueller Skalierung gewählt
    ist, aber Max <= Min steht -- gemeinsam von ColorScaleOverridePanel.
    range_invalid() (GraphicExportDialog) und VideoExportDialog._on_accept
    genutzt (VideoExportDialog baut seine Farbskala-Widgets wegen der
    zusätzlichen "Farbskala einblenden"-Checkbox selbst, nicht über
    ColorScaleOverridePanel, uebergibt hier aber dieselben vier Widget-
    Typen)."""
    return (
        radio_custom.isChecked()
        and combo_level_mode.currentData() == "manual"
        and spin_max.value() <= spin_min.value()
    )
