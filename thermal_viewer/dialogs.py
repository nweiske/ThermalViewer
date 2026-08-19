"""Zusätzliche Dialogfenster für Export-Funktionen (Grafik, Video, CSV-Spalten)."""
from __future__ import annotations

from functools import partial

from qtpy import QtCore, QtWidgets


class GraphicExportDialog(QtWidgets.QDialog):
    """Fragt DPI ab und ob Bild + Kurve kombiniert oder getrennt gespeichert werden."""

    def __init__(self, parent, settings: QtCore.QSettings, default_dpi: int = 150):
        super().__init__(parent)
        self.setWindowTitle("Grafik exportieren")
        self._settings = settings

        layout = QtWidgets.QVBoxLayout(self)

        form = QtWidgets.QFormLayout()
        self.spin_dpi = QtWidgets.QSpinBox()
        self.spin_dpi.setRange(50, 1200)
        self.spin_dpi.setSingleStep(10)
        self.spin_dpi.setValue(default_dpi)
        form.addRow("Auflösung (DPI):", self.spin_dpi)
        layout.addLayout(form)

        mode_box = QtWidgets.QGroupBox("Dateien")
        mode_layout = QtWidgets.QVBoxLayout(mode_box)
        self.radio_combined = QtWidgets.QRadioButton("Kombiniert (ein Bild: Thermobild + Kurve)")
        self.radio_separate = QtWidgets.QRadioButton("Getrennt (zwei Dateien: Bild und Kurve einzeln)")
        mode_layout.addWidget(self.radio_combined)
        mode_layout.addWidget(self.radio_separate)
        layout.addWidget(mode_box)

        separate = bool(settings.value("export/separate_images", False, type=bool))
        self.radio_separate.setChecked(separate)
        self.radio_combined.setChecked(not separate)

        buttons = QtWidgets.QDialogButtonBox(
            QtWidgets.QDialogButtonBox.StandardButton.Ok | QtWidgets.QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def dpi(self) -> int:
        return self.spin_dpi.value()

    def separate(self) -> bool:
        value = self.radio_separate.isChecked()
        self._settings.setValue("export/separate_images", value)
        return value


class VideoExportDialog(QtWidgets.QDialog):
    """Fragt Frame-Bereich, FPS und Farbskalen-Einstellungen für den Video-Export ab."""

    def __init__(
        self,
        parent,
        n_frames: int,
        colormaps: list[tuple[str, str]],
        current_colormap_index: int,
        current_invert: bool,
        current_level_mode: str,
        current_min: float,
        current_max: float,
        current_fps: float,
    ):
        super().__init__(parent)
        self.setWindowTitle("Video exportieren")

        layout = QtWidgets.QVBoxLayout(self)

        range_box = QtWidgets.QGroupBox("Frame-Bereich")
        range_layout = QtWidgets.QFormLayout(range_box)
        # Frame-Nummern hier bewusst 1-basiert (wie ueberall sonst in der App,
        # z.B. Statuszeile "Frame 1/8") -- intern (frame_range()) wird auf
        # 0-basierte Indizes umgerechnet.
        last = max(1, n_frames)
        self.spin_start = QtWidgets.QSpinBox()
        self.spin_start.setRange(1, last)
        self.spin_end = QtWidgets.QSpinBox()
        self.spin_end.setRange(1, last)
        self.spin_end.setValue(last)
        range_layout.addRow("Von Frame:", self.spin_start)
        range_layout.addRow("Bis Frame:", self.spin_end)
        layout.addWidget(range_box)

        fps_form = QtWidgets.QFormLayout()
        self.spin_fps = QtWidgets.QDoubleSpinBox()
        self.spin_fps.setRange(0.5, 60.0)
        self.spin_fps.setValue(current_fps)
        fps_form.addRow("Wiedergabe-FPS im Video:", self.spin_fps)
        layout.addLayout(fps_form)

        legend_box = QtWidgets.QGroupBox("Farbskala / Legende")
        legend_layout = QtWidgets.QVBoxLayout(legend_box)
        self.chk_legend = QtWidgets.QCheckBox("Farbskala (Legende) im Video einblenden")
        self.chk_legend.setChecked(True)
        legend_layout.addWidget(self.chk_legend)

        self.radio_current_settings = QtWidgets.QRadioButton("Aktuelle Anzeige-Einstellungen übernehmen")
        self.radio_custom_settings = QtWidgets.QRadioButton("Eigene Einstellungen für dieses Video")
        self.radio_current_settings.setChecked(True)
        legend_layout.addWidget(self.radio_current_settings)
        legend_layout.addWidget(self.radio_custom_settings)

        custom_form = QtWidgets.QFormLayout()
        self.combo_cmap = QtWidgets.QComboBox()
        for label, _name in colormaps:
            self.combo_cmap.addItem(label)
        self.combo_cmap.setCurrentIndex(current_colormap_index)
        self.chk_invert = QtWidgets.QCheckBox("Invertiert")
        self.chk_invert.setChecked(current_invert)
        custom_form.addRow("Farbverlauf:", self.combo_cmap)
        custom_form.addRow("", self.chk_invert)

        self.combo_level_mode = QtWidgets.QComboBox()
        self.combo_level_mode.addItem("Manuell", "manual")
        self.combo_level_mode.addItem("Automatisch (pro Bild)", "per_frame")
        self.combo_level_mode.addItem("Automatisch (gesamte Serie)", "global")
        idx = self.combo_level_mode.findData(current_level_mode)
        self.combo_level_mode.setCurrentIndex(max(0, idx))
        custom_form.addRow("Skalierung:", self.combo_level_mode)

        self.spin_min = QtWidgets.QDoubleSpinBox()
        self.spin_min.setRange(-100.0, 2000.0)
        self.spin_min.setDecimals(1)
        self.spin_min.setValue(current_min)
        self.spin_max = QtWidgets.QDoubleSpinBox()
        self.spin_max.setRange(-100.0, 2000.0)
        self.spin_max.setDecimals(1)
        self.spin_max.setValue(current_max)
        custom_form.addRow("Min:", self.spin_min)
        custom_form.addRow("Max:", self.spin_max)
        legend_layout.addLayout(custom_form)
        layout.addWidget(legend_box)

        def _update_enabled() -> None:
            enabled = self.chk_legend.isChecked()
            self.radio_current_settings.setEnabled(enabled)
            self.radio_custom_settings.setEnabled(enabled)
            custom_enabled = enabled and self.radio_custom_settings.isChecked()
            for w in (self.combo_cmap, self.chk_invert, self.combo_level_mode, self.spin_min, self.spin_max):
                w.setEnabled(custom_enabled)

        self.chk_legend.toggled.connect(_update_enabled)
        self.radio_current_settings.toggled.connect(_update_enabled)
        self.radio_custom_settings.toggled.connect(_update_enabled)
        _update_enabled()

        buttons = QtWidgets.QDialogButtonBox(
            QtWidgets.QDialogButtonBox.StandardButton.Ok | QtWidgets.QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self._on_accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _on_accept(self) -> None:
        if self.spin_end.value() < self.spin_start.value():
            QtWidgets.QMessageBox.warning(
                self, "Ungültiger Bereich", "Der End-Frame muss größer oder gleich dem Start-Frame sein."
            )
            return
        self.accept()

    def frame_range(self) -> tuple[int, int]:
        # UI ist 1-basiert (siehe oben), Rueckgabe als 0-basierte Frame-Indizes.
        return self.spin_start.value() - 1, self.spin_end.value() - 1

    def fps(self) -> float:
        return self.spin_fps.value()

    def show_legend(self) -> bool:
        return self.chk_legend.isChecked()

    def use_custom_settings(self) -> bool:
        return self.chk_legend.isChecked() and self.radio_custom_settings.isChecked()

    def custom_colormap_index(self) -> int:
        return self.combo_cmap.currentIndex()

    def custom_invert(self) -> bool:
        return self.chk_invert.isChecked()

    def custom_level_mode(self) -> str:
        return self.combo_level_mode.currentData()

    def custom_min_max(self) -> tuple[float, float]:
        return self.spin_min.value(), self.spin_max.value()


class CsvColumnDialog(QtWidgets.QDialog):
    """Erlaubt individuelle Spaltennamen für die exportierten Messbereich-Werte."""

    def __init__(self, parent, entries: list[dict]):
        # entries: [{"name": str, "width_px": float, "height_px": float,
        #            "width_mm": float | None, "height_mm": float | None}, ...]
        super().__init__(parent)
        self.setWindowTitle("CSV-Spaltennamen")

        layout = QtWidgets.QVBoxLayout(self)
        layout.addWidget(QtWidgets.QLabel(
            "Spaltenüberschriften für die Messwerte anpassen (per Hand oder per Autofill):"
        ))

        self._edits: list[QtWidgets.QLineEdit] = []
        grid = QtWidgets.QGridLayout()
        for row, entry in enumerate(entries):
            grid.addWidget(QtWidgets.QLabel(entry["name"]), row, 0)
            edit = QtWidgets.QLineEdit(f'{entry["name"]} (°C)')
            grid.addWidget(edit, row, 1)
            self._edits.append(edit)

            btn_px = QtWidgets.QPushButton("Autofill: px")
            btn_px.setToolTip("Spaltenname mit Pixel-Größe befüllen")
            btn_px.clicked.connect(partial(self._autofill_px, edit, entry))
            grid.addWidget(btn_px, row, 2)

            btn_mm = QtWidgets.QPushButton("Autofill: mm")
            btn_mm.setToolTip("Spaltenname mit realer Größe (mm) befüllen")
            btn_mm.setEnabled(entry.get("width_mm") is not None)
            btn_mm.clicked.connect(partial(self._autofill_mm, edit, entry))
            grid.addWidget(btn_mm, row, 3)
        layout.addLayout(grid)

        buttons = QtWidgets.QDialogButtonBox(
            QtWidgets.QDialogButtonBox.StandardButton.Ok | QtWidgets.QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    @staticmethod
    def _autofill_px(edit: QtWidgets.QLineEdit, entry: dict) -> None:
        edit.setText(f'{entry["name"]} ({entry["width_px"]:.0f}x{entry["height_px"]:.0f} px)')

    @staticmethod
    def _autofill_mm(edit: QtWidgets.QLineEdit, entry: dict) -> None:
        # Deutsches Zahlenformat (Dezimalkomma), konsistent mit den uebrigen
        # Zahlenanzeigen der App (z.B. Massstab-Label, CSV-Werte).
        w = f'{entry["width_mm"]:.1f}'.replace(".", ",")
        h = f'{entry["height_mm"]:.1f}'.replace(".", ",")
        edit.setText(f'{entry["name"]} ({w}x{h} mm)')

    def column_names(self) -> list[str]:
        return [edit.text().strip() or "Messwert" for edit in self._edits]
