"""Auswahl, ob/welche Maßstab-/Messungs-Visualisierungen zusätzlich in einen
Thermobild-Export aufgenommen werden."""
from __future__ import annotations

from functools import partial

from qtpy import QtWidgets


class ScaleContentSelector:
    """Ankreuzliste fuer Maßstab (Referenzlinie) und einzelne Messungen, die
    zusätzlich in einen Thermobild-Export aufgenommen werden sollen (Punkt 12,
    Nutzerwunsch: "genauso wie die ROIs einzeln an-/abwählbar... mit einem
    Button ALLE an-/abwählbar"). Die ROI-RECHTECKE selbst sind davon nicht
    betroffen -- die sind immer fester Bestandteil des Thermobilds, hier geht
    es nur um die BEIDEN zusätzlichen, oft nicht gewünschten Overlays
    Maßstab-Linie und Ad-hoc-Messungen. Gemeinsam genutzt von
    GraphicExportDialog und VideoExportDialog.

    Standard: alles AUS -- eine rein optionale Zusatzanzeige ("wenn man
    möchte", Nutzerformulierung), anders als z.B. die ROI-Kurven im Graphen-
    Export (dort Standard AN, da dort meist erwünscht).

    measurement_entries: (number, name)-Paare -- Auswahl läuft über die
    eindeutige Messungs-NUMMER, analog zu GraphContentSelector/RoiEntry
    (siehe dort für den Bugreport zu namensbasierter Auswahl)."""

    def __init__(
        self,
        ruler_available: bool,
        measurement_entries: list[tuple[int, str]],
        host_box: QtWidgets.QGroupBox | None = None,
    ) -> None:
        """host_box (Folgeanfrage, Video-Export): falls gesetzt, wird DESSEN
        Layout genutzt statt eine eigene neue Box zu erzeugen -- damit sich
        z.B. "Cursor im Bild" und diese Maßstab-/Messungs-Auswahl im Video-
        Export unter einem einzigen Bereich zusammenfassen lassen, statt in
        zwei separaten Kaesten nebeneinander zu stehen. Der Aufrufer haengt
        in diesem Fall vorher bereits eigene Inhalte in host_box, diese
        Klasse haengt ihre eigenen Widgets nur noch DARUNTER an. Ohne
        host_box (GraphicExportDialog/_export_image.py) unveraendertes
        Verhalten: eine eigene, neue Box."""
        self.group_box = host_box if host_box is not None else QtWidgets.QGroupBox("Maßstab && Messungen im Export")
        outer = self.group_box.layout()
        if outer is None:
            outer = QtWidgets.QVBoxLayout(self.group_box)
        self.chk_ruler: QtWidgets.QCheckBox | None = None
        self.checks: dict[int, QtWidgets.QCheckBox] = {}

        if not ruler_available and not measurement_entries:
            outer.addWidget(QtWidgets.QLabel("(Kein Maßstab und keine Messungen definiert.)"))
            return

        select_row = QtWidgets.QHBoxLayout()
        btn_all = QtWidgets.QPushButton("Alle auswählen")
        btn_none = QtWidgets.QPushButton("Keine auswählen")
        select_row.addWidget(btn_all)
        select_row.addWidget(btn_none)
        select_row.addStretch(1)
        outer.addLayout(select_row)

        if ruler_available:
            self.chk_ruler = QtWidgets.QCheckBox("Maßstab (Referenzlinie)")
            outer.addWidget(self.chk_ruler)

        if measurement_entries:
            grid = QtWidgets.QGridLayout()
            grid.setHorizontalSpacing(16)
            cols = 3
            for i, (number, name) in enumerate(measurement_entries):
                chk = QtWidgets.QCheckBox(name)
                self.checks[number] = chk
                grid.addWidget(chk, i // cols, i % cols)
            outer.addLayout(grid)

        btn_all.clicked.connect(partial(self._select_all, True))
        btn_none.clicked.connect(partial(self._select_all, False))

    def _select_all(self, checked: bool) -> None:
        if self.chk_ruler is not None:
            self.chk_ruler.setChecked(checked)
        for chk in self.checks.values():
            chk.setChecked(checked)

    def include_ruler(self) -> bool:
        return self.chk_ruler is not None and self.chk_ruler.isChecked()

    def included_measurement_numbers(self) -> set[int]:
        return {number for number, chk in self.checks.items() if chk.isChecked()}
