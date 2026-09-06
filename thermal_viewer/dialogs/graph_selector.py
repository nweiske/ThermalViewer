"""Auswahl, welche Kurven in einen Export aufgenommen werden, und die
Kopplung zwischen "Cursor im Bild" und der Live-Cursor-Kurve."""
from __future__ import annotations

from functools import partial

from qtpy import QtWidgets


class GraphContentSelector:
    """Ankreuzliste, welche Kurven (einzelne Messbereiche und/oder die
    Live-Cursor-Kurve) in den exportierten Graphen aufgenommen werden --
    gemeinsam genutzt von GraphicExportDialog und VideoExportDialog. Standard:
    alle Messbereiche an, Live-Cursor aus. Die Live-Cursor-Checkbox wird vom
    Aufrufer per _CursorCurveLink mit der Cursor-im-Bild-Option gekoppelt
    (Kurve setzt Cursor-im-Bild voraus).

    roi_entries: (number, name)-Paare -- Auswahl laeuft bewusst ueber die
    eindeutige ROI-NUMMER statt ueber den (frei umbenennbaren, NICHT auf
    Eindeutigkeit geprueften) Namen. Bugfix: bei zwei gleichnamigen
    Messbereichen ueberschrieb ein namensbasiertes dict eine der beiden
    Checkboxen stillschweigend, "Alle auswaehlen" traf dann nur noch eine
    von beiden und der Export konnte nicht mehr zwischen ihnen unterscheiden."""

    def __init__(
        self,
        roi_entries: list[tuple[int, str]],
        live_available: bool,
        default_live_checked: bool = False,
    ) -> None:
        self.group_box = QtWidgets.QGroupBox("Graph-Inhalt")
        outer = QtWidgets.QVBoxLayout(self.group_box)

        select_row = QtWidgets.QHBoxLayout()
        btn_all = QtWidgets.QPushButton("Alle auswählen")
        btn_none = QtWidgets.QPushButton("Keine auswählen")
        select_row.addWidget(btn_all)
        select_row.addWidget(btn_none)
        select_row.addStretch(1)
        outer.addLayout(select_row)

        self.checks: dict[int, QtWidgets.QCheckBox] = {}
        if roi_entries:
            grid = QtWidgets.QGridLayout()
            grid.setHorizontalSpacing(16)
            cols = 3
            for i, (number, name) in enumerate(roi_entries):
                chk = QtWidgets.QCheckBox(name)
                chk.setChecked(True)
                self.checks[number] = chk
                grid.addWidget(chk, i // cols, i % cols)
            outer.addLayout(grid)
        else:
            outer.addWidget(QtWidgets.QLabel("(Keine platzierten Messbereiche vorhanden.)"))

        self.chk_live = QtWidgets.QCheckBox("Live-Cursor")
        self.chk_live.setChecked(default_live_checked and live_available)
        self.chk_live.setEnabled(live_available)
        self.chk_live.setToolTip(
            "Temperaturverlauf des fixierten/zuletzt mit der Maus gezeigten Cursor-Pixels. "
            "Erfordert „Cursor-Position im Bild anzeigen“ (siehe unten)."
            if live_available else
            "Kein Live-Cursor-Pixel gewählt (Maus über das Bild bewegen oder eine Stelle "
            "fixieren, um diese Option zu aktivieren)."
        )
        outer.addWidget(self.chk_live)

        btn_all.clicked.connect(partial(self._select_all, True))
        btn_none.clicked.connect(partial(self._select_all, False))

    def _select_all(self, checked: bool) -> None:
        # Bugfix: "Alle auswaehlen"/"Keine auswaehlen" liessen chk_live bisher
        # unangetastet (nur die ROI-Checkboxen wurden umgeschaltet) -- beim
        # Abwaehlen wird sie immer mit ausgeschaltet, beim Auswaehlen nur,
        # wenn ueberhaupt ein Live-Cursor-Pixel verfuegbar ist (sonst bleibt
        # sie wie bisher deaktiviert).
        for chk in self.checks.values():
            chk.setChecked(checked)
        self.chk_live.setChecked(checked and self.chk_live.isEnabled())

    def included_numbers(self) -> set[int]:
        return {number for number, chk in self.checks.items() if chk.isChecked()}

    def include_live(self) -> bool:
        return self.chk_live.isChecked()

    def has_any_selected(self) -> bool:
        return bool(self.included_numbers()) or self.include_live()


class _CursorCurveLink:
    """Koppelt "Cursor-Position im Bild anzeigen" mit der Live-Cursor-Kurve
    im Graphen (Nutzerwunsch: "beides soll unabhängig voneinander möglich
    sein, aber nicht Kurve ohne Cursor"): beide bleiben einzeln umschaltbar,
    aber Kurve EIN erzwingt Cursor-im-Bild EIN, und Cursor-im-Bild AUS
    erzwingt Kurve AUS. self._guard verhindert dabei eine Signal-
    Rueckkopplung zwischen den beiden verbundenen toggled-Handlern."""

    def __init__(self, chk_cursor_image: QtWidgets.QCheckBox, chk_cursor_curve: QtWidgets.QCheckBox) -> None:
        self._chk_cursor_image = chk_cursor_image
        self._chk_cursor_curve = chk_cursor_curve
        self._guard = False
        chk_cursor_curve.toggled.connect(self._on_curve_toggled)
        chk_cursor_image.toggled.connect(self._on_image_toggled)

    def _on_curve_toggled(self, checked: bool) -> None:
        if self._guard or not checked or self._chk_cursor_image.isChecked():
            return
        self._guard = True
        try:
            self._chk_cursor_image.setChecked(True)
        finally:
            self._guard = False

    def _on_image_toggled(self, checked: bool) -> None:
        if self._guard or checked or not self._chk_cursor_curve.isChecked():
            return
        self._guard = True
        try:
            self._chk_cursor_curve.setChecked(False)
        finally:
            self._guard = False
