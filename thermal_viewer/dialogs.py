"""Zusätzliche Dialogfenster für Export- (Grafik, Video, CSV-Spalten) und
Import-Funktionen (Namensschema-Anpassung beim Laden)."""
from __future__ import annotations

import re
from functools import partial
from pathlib import Path

from qtpy import QtCore, QtGui, QtWidgets

from .data import ImportSettings, RecordingError, compile_filename_template, parse_frame_text, validate_filename_template
from .widgets import LocaleTolerantDoubleSpinBox


def _build_color_scale_override(
    colormaps: list[tuple[str, str]],
    current_colormap_index: int,
    current_invert: bool,
    current_level_mode: str,
    current_min: float,
    current_max: float,
) -> dict:
    """Baut den wiederverwendbaren "Aktuelle/Eigene Einstellungen"-Block
    (Farbverlauf, Invertiert, Skalierung, Min/Max) -- gemeinsam genutzt von
    GraphicExportDialog und VideoExportDialog, damit beide Export-Wege
    dieselbe Freiheit bieten, die Farbdarstellung unabhängig von der gerade
    aktiven Anzeige zu wählen (Bugreport: "gebe mir dieselbe Freiheit wie
    in der UI"). Gibt die erzeugten Widgets als dict zurück; group_box wird
    vom Aufrufer selbst ins eigene Layout eingehängt."""
    group_box = QtWidgets.QGroupBox("Farbskala / Legende")
    layout = QtWidgets.QVBoxLayout(group_box)

    radio_current = QtWidgets.QRadioButton("Aktuelle Anzeige-Einstellungen übernehmen")
    radio_custom = QtWidgets.QRadioButton("Eigene Einstellungen für diesen Export")
    radio_current.setChecked(True)
    layout.addWidget(radio_current)
    layout.addWidget(radio_custom)

    # Eingerueckt unter "Eigene Einstellungen" -- nur bei dieser Wahl
    # ueberhaupt nutzbar (siehe _update_enabled unten), soll optisch klar
    # als deren Unterpunkte erkennbar sein.
    indent_row = QtWidgets.QHBoxLayout()
    indent_row.addSpacing(20)
    form = QtWidgets.QFormLayout()
    combo_cmap = QtWidgets.QComboBox()
    for label, _name in colormaps:
        combo_cmap.addItem(label)
    combo_cmap.setCurrentIndex(current_colormap_index)
    chk_invert = QtWidgets.QCheckBox("Invertiert")
    chk_invert.setChecked(current_invert)
    form.addRow("Farbverlauf:", combo_cmap)
    form.addRow("", chk_invert)

    combo_level_mode = QtWidgets.QComboBox()
    combo_level_mode.addItem("Manuell", "manual")
    combo_level_mode.addItem("Automatisch: Pro Bild", "per_frame")
    combo_level_mode.addItem("Automatisch: Über gesamte Messung", "global")
    idx = combo_level_mode.findData(current_level_mode)
    combo_level_mode.setCurrentIndex(max(0, idx))
    form.addRow("Skalierung:", combo_level_mode)

    spin_min = LocaleTolerantDoubleSpinBox()
    spin_min.setRange(-100.0, 2000.0)
    spin_min.setDecimals(1)
    spin_min.setValue(current_min)
    spin_max = LocaleTolerantDoubleSpinBox()
    spin_max.setRange(-100.0, 2000.0)
    spin_max.setDecimals(1)
    spin_max.setValue(current_max)
    form.addRow("Min:", spin_min)
    form.addRow("Max:", spin_max)
    indent_row.addLayout(form)
    layout.addLayout(indent_row)

    def _update_enabled() -> None:
        custom_enabled = radio_custom.isChecked()
        for w in (combo_cmap, chk_invert, combo_level_mode, spin_min, spin_max):
            w.setEnabled(custom_enabled)

    radio_current.toggled.connect(_update_enabled)
    radio_custom.toggled.connect(_update_enabled)
    _update_enabled()

    return {
        "group_box": group_box,
        "radio_current": radio_current,
        "radio_custom": radio_custom,
        "combo_cmap": combo_cmap,
        "chk_invert": chk_invert,
        "combo_level_mode": combo_level_mode,
        "spin_min": spin_min,
        "spin_max": spin_max,
    }


_INVALID_FILENAME_CHARS = re.compile(r'[\\/:*?"<>|]')


def sanitize_filename_prefix(prefix: str, fallback: str = "Frame") -> str:
    """Ersetzt unter Windows/macOS/Linux in Dateinamen ungueltige Zeichen
    (bzw. "/"/"\\", die ungewollt Unterordner erzeugen wuerden) durch "_" --
    gemeinsam genutzt von der Live-Dateiname-Vorschau in VideoExportDialog
    UND dem tatsaechlichen Bildstapel-Export (MainWindow._export_video),
    damit die Vorschau niemals einen Dateinamen zeigt, der beim
    tatsaechlichen Speichern anders aussehen wuerde."""
    return _INVALID_FILENAME_CHARS.sub("_", prefix).strip() or fallback


def _build_graph_content_selector(
    roi_entries: list[tuple[int, str]],
    live_available: bool,
    default_live_checked: bool = False,
) -> dict:
    """Baut eine Ankreuzliste, welche Kurven (einzelne Messbereiche und/oder
    die Live-Cursor-Kurve) in den exportierten Graphen aufgenommen werden --
    gemeinsam genutzt von GraphicExportDialog und VideoExportDialog. Standard:
    alle Messbereiche an, Live-Cursor aus. Die Live-Cursor-Checkbox wird vom
    Aufrufer per _wire_cursor_curve_dependency() mit der Cursor-im-Bild-
    Option gekoppelt (Kurve setzt Cursor-im-Bild voraus).

    roi_entries: (number, name)-Paare -- Auswahl laeuft bewusst ueber die
    eindeutige ROI-NUMMER statt ueber den (frei umbenennbaren, NICHT auf
    Eindeutigkeit geprueften) Namen. Bugfix: bei zwei gleichnamigen
    Messbereichen ueberschrieb ein namensbasiertes dict eine der beiden
    Checkboxen stillschweigend, "Alle auswaehlen" traf dann nur noch eine
    von beiden und der Export konnte nicht mehr zwischen ihnen unterscheiden."""
    group_box = QtWidgets.QGroupBox("Graph-Inhalt")
    outer = QtWidgets.QVBoxLayout(group_box)

    select_row = QtWidgets.QHBoxLayout()
    btn_all = QtWidgets.QPushButton("Alle auswählen")
    btn_none = QtWidgets.QPushButton("Keine auswählen")
    select_row.addWidget(btn_all)
    select_row.addWidget(btn_none)
    select_row.addStretch(1)
    outer.addLayout(select_row)

    checks: dict[int, QtWidgets.QCheckBox] = {}
    if roi_entries:
        grid = QtWidgets.QGridLayout()
        grid.setHorizontalSpacing(16)
        cols = 3
        for i, (number, name) in enumerate(roi_entries):
            chk = QtWidgets.QCheckBox(name)
            chk.setChecked(True)
            checks[number] = chk
            grid.addWidget(chk, i // cols, i % cols)
        outer.addLayout(grid)
    else:
        outer.addWidget(QtWidgets.QLabel("(Keine platzierten Messbereiche vorhanden.)"))

    chk_live = QtWidgets.QCheckBox("Live-Cursor")
    chk_live.setChecked(default_live_checked and live_available)
    chk_live.setEnabled(live_available)
    chk_live.setToolTip(
        "Temperaturverlauf des fixierten/zuletzt mit der Maus gezeigten Cursor-Pixels. "
        "Erfordert „Cursor-Position im Bild anzeigen“ (siehe unten)."
        if live_available else
        "Kein Live-Cursor-Pixel gewählt (Maus über das Bild bewegen oder eine Stelle "
        "fixieren, um diese Option zu aktivieren)."
    )
    outer.addWidget(chk_live)

    def _select_all(checked: bool) -> None:
        for chk in checks.values():
            chk.setChecked(checked)

    btn_all.clicked.connect(partial(_select_all, True))
    btn_none.clicked.connect(partial(_select_all, False))

    return {"group_box": group_box, "checks": checks, "chk_live": chk_live}


def _wire_cursor_curve_dependency(
    chk_cursor_image: QtWidgets.QCheckBox, chk_cursor_curve: QtWidgets.QCheckBox
) -> None:
    """Koppelt "Cursor-Position im Bild anzeigen" mit der Live-Cursor-Kurve
    im Graphen (Nutzerwunsch: "beides soll unabhängig voneinander möglich
    sein, aber nicht Kurve ohne Cursor"): beide bleiben einzeln umschaltbar,
    aber Kurve EIN erzwingt Cursor-im-Bild EIN, und Cursor-im-Bild AUS
    erzwingt Kurve AUS. Ein Sperr-Flag verhindert dabei eine Signal-
    Rueckkopplung zwischen den beiden verbundenen toggled-Handlern."""
    guard = {"active": False}

    def _on_curve_toggled(checked: bool) -> None:
        if guard["active"] or not checked or chk_cursor_image.isChecked():
            return
        guard["active"] = True
        try:
            chk_cursor_image.setChecked(True)
        finally:
            guard["active"] = False

    def _on_image_toggled(checked: bool) -> None:
        if guard["active"] or checked or not chk_cursor_curve.isChecked():
            return
        guard["active"] = True
        try:
            chk_cursor_curve.setChecked(False)
        finally:
            guard["active"] = False

    chk_cursor_curve.toggled.connect(_on_curve_toggled)
    chk_cursor_image.toggled.connect(_on_image_toggled)


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


class GraphicExportDialog(_NoEnterAutoAccept, QtWidgets.QDialog):
    """Fragt DPI ab und (falls show_mode_choice) ob Bild + Kurve kombiniert
    oder getrennt gespeichert werden. Mit show_mode_choice=False (Export
    eines einzelnen Graphen, z.B. ueber dessen Rechtsklick-Menü) entfaellt
    die Kombiniert/Getrennt-Auswahl -- ansonsten identisches Fenster, damit
    Einzelgraph- und Menü-Export einheitlich wirken."""

    def __init__(
        self,
        parent,
        settings: QtCore.QSettings,
        default_dpi: int = 150,
        show_mode_choice: bool = True,
        show_time_axis_choice: bool = True,
        colormaps: list[tuple[str, str]] | None = None,
        current_colormap_index: int = 0,
        current_invert: bool = False,
        current_level_mode: str = "global",
        current_min: float = 0.0,
        current_max: float = 50.0,
        current_time_axis_mode: str = "clock",
        show_graph_source_choice: bool = False,
        live_available: bool = False,
        roi_entries: list[tuple[int, str]] | None = None,
    ):
        super().__init__(parent)
        self.setWindowTitle("Grafik exportieren")
        self.setMinimumWidth(480)
        self._settings = settings
        self._show_mode_choice = show_mode_choice

        layout = QtWidgets.QVBoxLayout(self)

        # Nur noch EIN Grafik-Export-Fenster statt getrennter "Zeitverlauf-"/
        # "Live-Grafik"-Menüpunkte (Nutzerwunsch): hier wird gewählt, welche
        # Kurve(n) -- einzelne Messbereiche und/oder Live-Cursor -- tatsächlich
        # mit exportiert werden sollen.
        self._content_widgets = None
        self.chk_cursor_position = None
        if show_graph_source_choice:
            self._content_widgets = _build_graph_content_selector(
                roi_entries or [], live_available, default_live_checked=False
            )
            layout.addWidget(self._content_widgets["group_box"])

            # Eingerueckt unter "Graph-Inhalt" (gehoert inhaltlich zusammen,
            # siehe _wire_cursor_curve_dependency: Live-Cursor-Kurve setzt
            # diese Option voraus).
            cursor_row = QtWidgets.QHBoxLayout()
            cursor_row.addSpacing(20)
            self.chk_cursor_position = QtWidgets.QCheckBox("Cursor-Position im Bild anzeigen")
            self.chk_cursor_position.setChecked(False)
            self.chk_cursor_position.setToolTip(
                "Blendet das Fadenkreuz samt Temperaturanzeige am (fixierten oder\n"
                "zuletzt mit der Maus angezeigten) Cursor-Pixel im exportierten\n"
                "Thermobild mit ein. Unabhängig von der Live-Cursor-KURVE oben\n"
                "einzeln steuerbar -- die Kurve setzt diese Option aber voraus."
            )
            cursor_row.addWidget(self.chk_cursor_position)
            cursor_row.addStretch(1)
            layout.addLayout(cursor_row)
            _wire_cursor_curve_dependency(self.chk_cursor_position, self._content_widgets["chk_live"])
        else:
            # Kein Graph in diesem Export (z.B. Einzelexport des Thermobilds
            # per Rechtsklick) -- die Option bleibt trotzdem sinnvoll, hier
            # aber ohne Kopplung an eine (nicht vorhandene) Kurve.
            self.chk_cursor_position = QtWidgets.QCheckBox("Cursor-Position im Bild anzeigen")
            self.chk_cursor_position.setChecked(False)
            self.chk_cursor_position.setToolTip(
                "Blendet das Fadenkreuz samt Temperaturanzeige am (fixierten oder\n"
                "zuletzt mit der Maus angezeigten) Cursor-Pixel im exportierten\n"
                "Thermobild mit ein. Standardmäßig aus, damit die Grafik nicht\n"
                "ungewollt eine Maus-/Debug-Markierung enthält."
            )
            layout.addWidget(self.chk_cursor_position)

        # DPI und Kombiniert/Getrennt nebeneinander statt untereinander --
        # beides sind kurze, unabhaengige Ausgabe-Einstellungen.
        top_row = QtWidgets.QHBoxLayout()
        form = QtWidgets.QFormLayout()
        self.spin_dpi = QtWidgets.QSpinBox()
        self.spin_dpi.setRange(50, 1200)
        self.spin_dpi.setSingleStep(10)
        self.spin_dpi.setValue(default_dpi)
        form.addRow("Auflösung (DPI):", self.spin_dpi)
        top_row.addLayout(form)

        self.radio_combined = None
        self.radio_separate = None
        if show_mode_choice:
            mode_box = QtWidgets.QGroupBox("Dateien")
            mode_layout = QtWidgets.QVBoxLayout(mode_box)
            self.radio_combined = QtWidgets.QRadioButton("Kombiniert (ein Bild: Thermobild + Kurve)")
            self.radio_separate = QtWidgets.QRadioButton("Getrennt (zwei Dateien: Bild und Kurve einzeln)")
            mode_layout.addWidget(self.radio_combined)
            mode_layout.addWidget(self.radio_separate)
            top_row.addWidget(mode_box, 1)

            separate = bool(settings.value("export/separate_images", False, type=bool))
            self.radio_separate.setChecked(separate)
            self.radio_combined.setChecked(not separate)
        else:
            top_row.addStretch(1)
        layout.addLayout(top_row)

        # Dieselbe Freiheit wie in der UI: Farbverlauf/Invertiert/Skalierung
        # unabhängig von der aktuell angezeigten Einstellung für GENAU diesen
        # Export wählbar (Bugreport: "gebe mir dieselbe Freiheit wie in der UI").
        self._color_widgets = _build_color_scale_override(
            colormaps or [], current_colormap_index, current_invert,
            current_level_mode, current_min, current_max,
        )
        layout.addWidget(self._color_widgets["group_box"])

        self.combo_time_axis = None
        if show_time_axis_choice:
            time_form = QtWidgets.QFormLayout()
            self.combo_time_axis = QtWidgets.QComboBox()
            self.combo_time_axis.addItem("Uhrzeit", "clock")
            self.combo_time_axis.addItem("Laufzeit", "runtime")
            self.combo_time_axis.addItem("Beide", "both")
            self.combo_time_axis.setToolTip(
                "Zeigt die x-Achse des Kurven-Graphen als echte Uhrzeit, als Laufzeit seit "
                "Aufnahmebeginn, oder BEIDE gleichzeitig (zusätzliche zweite Achse oben am "
                "Graphen). Vorbelegt mit der gerade in der Anwendung aktiven Anzeige."
            )
            idx = self.combo_time_axis.findData(current_time_axis_mode)
            self.combo_time_axis.setCurrentIndex(max(0, idx))
            time_form.addRow("Zeitachse:", self.combo_time_axis)
            layout.addLayout(time_form)

        buttons = QtWidgets.QDialogButtonBox(
            QtWidgets.QDialogButtonBox.StandardButton.Ok | QtWidgets.QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self._on_accept)
        buttons.rejected.connect(self.reject)
        _disable_enter_auto_accept(buttons)
        layout.addWidget(buttons)

    def _on_accept(self) -> None:
        if self._content_widgets is not None:
            any_roi = any(chk.isChecked() for chk in self._content_widgets["checks"].values())
            if not any_roi and not self._content_widgets["chk_live"].isChecked():
                QtWidgets.QMessageBox.information(
                    self, "Keine Auswahl",
                    "Bitte mindestens einen Messbereich und/oder Live-Cursor auswählen."
                )
                return
        self.accept()

    def dpi(self) -> int:
        return self.spin_dpi.value()

    def separate(self) -> bool:
        if self.radio_separate is None:
            return False
        value = self.radio_separate.isChecked()
        self._settings.setValue("export/separate_images", value)
        return value

    def included_roi_numbers(self) -> set[int]:
        """Nummern (RoiEntry.number, eindeutig -- siehe _build_graph_content_selector)
        der ausgewählten Messbereiche -- leere Menge, falls der Dialog gar
        keine Graph-Inhalt-Auswahl anbietet (show_graph_source_choice=False)."""
        if self._content_widgets is None:
            return set()
        return {number for number, chk in self._content_widgets["checks"].items() if chk.isChecked()}

    def include_live(self) -> bool:
        return self._content_widgets is not None and self._content_widgets["chk_live"].isChecked()

    def has_graph_content(self) -> bool:
        """Ob ueberhaupt ein Graph exportiert werden soll -- False nur, wenn
        show_graph_source_choice=False war (kein Graph in diesem Export)."""
        return self._content_widgets is not None

    def export_cursor_position(self) -> bool:
        return self.chk_cursor_position is not None and self.chk_cursor_position.isChecked()

    def use_custom_colors(self) -> bool:
        return self._color_widgets["radio_custom"].isChecked()

    def custom_colormap_index(self) -> int:
        return self._color_widgets["combo_cmap"].currentIndex()

    def custom_invert(self) -> bool:
        return self._color_widgets["chk_invert"].isChecked()

    def custom_level_mode(self) -> str:
        return self._color_widgets["combo_level_mode"].currentData()

    def custom_min_max(self) -> tuple[float, float]:
        return self._color_widgets["spin_min"].value(), self._color_widgets["spin_max"].value()

    def time_axis_mode(self) -> str:
        """"clock"/"runtime"/"both", oder "clock" als Vorbelegung, falls der
        Dialog gar keine Zeitachsen-Wahl anbietet (show_time_axis_choice=False,
        z.B. beim Einzelexport des Thermobilds ohne Kurve)."""
        if self.combo_time_axis is None:
            return "clock"
        return self.combo_time_axis.currentData()


class VideoExportDialog(_NoEnterAutoAccept, QtWidgets.QDialog):
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
        default_start_frame: int = 1,
        default_end_frame: int | None = None,
        roi_entries: list[tuple[int, str]] | None = None,
        live_available: bool = False,
    ):
        super().__init__(parent)
        self.setWindowTitle("Video / Bildstapel exportieren")
        self.setMinimumWidth(720)

        layout = QtWidgets.QVBoxLayout(self)

        # Bildstapel (Punkt: "neben einem Video auch einen Bilderstapel
        # exportieren") nutzt exakt dieselbe Frame-Bereich-/Farbskalen-/
        # Zeitachsen-/Graph-Konfiguration wie der Video-Export -- nur die
        # FPS (kein Video-Zeitverhalten) und das Ziel (Ordner + eine Datei
        # pro Frame statt einer einzelnen Video-Datei) unterscheiden sich.
        output_box = QtWidgets.QGroupBox("Ausgabeform")
        output_layout = QtWidgets.QVBoxLayout(output_box)
        self.radio_output_video = QtWidgets.QRadioButton("Video-Datei (MP4/AVI/WebM)")
        self.radio_output_images = QtWidgets.QRadioButton("Bildstapel (eine Bilddatei pro Frame)")
        self.radio_output_video.setChecked(True)
        output_layout.addWidget(self.radio_output_video)
        output_layout.addWidget(self.radio_output_images)

        # Eingerueckt, um optisch klar als Unterpunkte von "Bildstapel"
        # erkennbar zu sein (Nutzerwunsch: Zusammengehoeriges einruecken).
        image_indent_row = QtWidgets.QHBoxLayout()
        image_indent_row.addSpacing(20)
        image_form = QtWidgets.QFormLayout()
        self.combo_image_format = QtWidgets.QComboBox()
        for label, ext in (
            ("PNG-Bild (*.png)", ".png"),
            ("JPEG-Bild (*.jpg)", ".jpg"),
            ("Bitmap (*.bmp)", ".bmp"),
            ("TIFF-Bild (*.tiff)", ".tiff"),
            ("WebP-Bild (*.webp)", ".webp"),
        ):
            self.combo_image_format.addItem(label, ext)
        self.edit_image_prefix = QtWidgets.QLineEdit("Frame_")
        self.edit_image_prefix.setToolTip(
            "Gemeinsamer Dateiname-Anfang für alle Bilder -- direkt gefolgt vom Frame-Index "
            "(ein Trennzeichen wie „_“ davor bitte selbst mit eintippen). Unterstützt dieselben "
            "Zeitstempel-Platzhalter wie das Namensschema beim Laden (YYYY/MM/DD/hh/mm/ss), die "
            "mit dem echten Zeitstempel jedes Frames gefüllt werden, z.B. "
            "„Frame_YYYY-MM-DD_hh-mm-ss_“ -> Frame_2026-01-01_12-00-00_1.png, "
            "Frame_2026-01-01_12-00-01_2.png, …"
        )
        image_form.addRow("Bildformat:", self.combo_image_format)
        image_form.addRow("Dateiname-Präfix:", self.edit_image_prefix)
        self.lbl_filename_preview = QtWidgets.QLabel()
        image_form.addRow("Beispiel:", self.lbl_filename_preview)
        image_indent_row.addLayout(image_form)
        output_layout.addLayout(image_indent_row)
        layout_top = QtWidgets.QHBoxLayout()
        layout_top.addWidget(output_box, 1)

        # Frame-Bereich (Von/Bis nebeneinander statt untereinander) + FPS
        # in derselben rechten Spalte wie "Ausgabeform" (Nutzerwunsch:
        # Export-Fenster nicht nur nach unten wachsen lassen).
        range_box = QtWidgets.QGroupBox("Frame-Bereich && Tempo")
        range_outer = QtWidgets.QVBoxLayout(range_box)
        # Frame-Nummern hier bewusst 1-basiert (wie ueberall sonst in der App,
        # z.B. Statuszeile "Frame 1/8") -- intern (frame_range()) wird auf
        # 0-basierte Indizes umgerechnet. Vorbelegt mit dem aktuell in der
        # UI gesetzten Auswertungsstart/-ende statt immer dem vollen Bereich,
        # damit der Standardfall (Export genau des ausgewerteten Abschnitts)
        # ohne manuelles Nachjustieren funktioniert.
        last = max(1, n_frames)
        default_end_frame = last if default_end_frame is None else default_end_frame
        self.spin_start = QtWidgets.QSpinBox()
        self.spin_start.setRange(1, last)
        self.spin_start.setValue(min(max(1, default_start_frame), last))
        self.spin_end = QtWidgets.QSpinBox()
        self.spin_end.setRange(1, last)
        self.spin_end.setValue(min(max(1, default_end_frame), last))
        range_row = QtWidgets.QHBoxLayout()
        from_form = QtWidgets.QFormLayout()
        from_form.addRow("Von Frame:", self.spin_start)
        range_row.addLayout(from_form)
        to_form = QtWidgets.QFormLayout()
        to_form.addRow("Bis Frame:", self.spin_end)
        range_row.addLayout(to_form)
        range_outer.addLayout(range_row)

        fps_form = QtWidgets.QFormLayout()
        self.spin_fps = LocaleTolerantDoubleSpinBox()
        self.spin_fps.setRange(0.5, 60.0)
        self.spin_fps.setValue(current_fps)
        fps_form.addRow("Wiedergabe-FPS im Video:", self.spin_fps)
        range_outer.addLayout(fps_form)
        layout_top.addWidget(range_box, 1)
        layout.addLayout(layout_top)

        def _update_output_mode_enabled() -> None:
            is_video = self.radio_output_video.isChecked()
            self.spin_fps.setEnabled(is_video)
            self.combo_image_format.setEnabled(not is_video)
            self.edit_image_prefix.setEnabled(not is_video)
            self.lbl_filename_preview.setEnabled(not is_video)

        self.radio_output_video.toggled.connect(_update_output_mode_enabled)
        self.radio_output_images.toggled.connect(_update_output_mode_enabled)
        _update_output_mode_enabled()

        def _update_filename_preview() -> None:
            prefix = sanitize_filename_prefix(self.edit_image_prefix.text(), fallback="Frame_")
            ext = self.combo_image_format.currentData() or ".png"
            count = max(1, self.spin_end.value() - self.spin_start.value() + 1)
            digits = len(str(count))
            self.lbl_filename_preview.setText(f"{prefix}{1:0{digits}d}{ext}, {prefix}{2:0{digits}d}{ext}, …")

        self.edit_image_prefix.textChanged.connect(_update_filename_preview)
        self.combo_image_format.currentIndexChanged.connect(_update_filename_preview)
        self.spin_start.valueChanged.connect(_update_filename_preview)
        self.spin_end.valueChanged.connect(_update_filename_preview)
        _update_filename_preview()

        row2 = QtWidgets.QHBoxLayout()

        legend_box = QtWidgets.QGroupBox("Farbskala / Legende")
        legend_layout = QtWidgets.QVBoxLayout(legend_box)
        self.chk_legend = QtWidgets.QCheckBox("Farbskala (Legende) einblenden")
        self.chk_legend.setChecked(True)
        legend_layout.addWidget(self.chk_legend)

        self.radio_current_settings = QtWidgets.QRadioButton("Aktuelle Anzeige-Einstellungen übernehmen")
        self.radio_custom_settings = QtWidgets.QRadioButton("Eigene Einstellungen für dieses Video")
        self.radio_current_settings.setChecked(True)
        legend_layout.addWidget(self.radio_current_settings)
        legend_layout.addWidget(self.radio_custom_settings)

        # Eingerueckt unter "Eigene Einstellungen" (nur bei aktivierter Legende
        # UND dieser Wahl ueberhaupt nutzbar, siehe _update_enabled unten).
        custom_indent_row = QtWidgets.QHBoxLayout()
        custom_indent_row.addSpacing(20)
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
        self.combo_level_mode.addItem("Automatisch: Pro Bild", "per_frame")
        self.combo_level_mode.addItem("Automatisch: Über gesamte Messung", "global")
        idx = self.combo_level_mode.findData(current_level_mode)
        self.combo_level_mode.setCurrentIndex(max(0, idx))
        custom_form.addRow("Skalierung:", self.combo_level_mode)

        minmax_row = QtWidgets.QHBoxLayout()
        self.spin_min = LocaleTolerantDoubleSpinBox()
        self.spin_min.setRange(-100.0, 2000.0)
        self.spin_min.setDecimals(1)
        self.spin_min.setValue(current_min)
        self.spin_max = LocaleTolerantDoubleSpinBox()
        self.spin_max.setRange(-100.0, 2000.0)
        self.spin_max.setDecimals(1)
        self.spin_max.setValue(current_max)
        min_form = QtWidgets.QFormLayout()
        min_form.addRow("Min:", self.spin_min)
        minmax_row.addLayout(min_form)
        max_form = QtWidgets.QFormLayout()
        max_form.addRow("Max:", self.spin_max)
        minmax_row.addLayout(max_form)
        custom_form.addRow(minmax_row)
        custom_indent_row.addLayout(custom_form)
        legend_layout.addLayout(custom_indent_row)
        row2.addWidget(legend_box, 1)

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

        # Graph (Temperaturverlauf) zusaetzlich zum Thermobild im Export --
        # mit der ohnehin schon vorhandenen wandernden Markierungslinie
        # (frame_marker/live_frame_marker), genau wie im Hauptfenster
        # (Bugreport: "genauso wie in der UI"). Punkt: "Graph mit anzeigen"
        # -> "Graph mit exportieren" (klarer, da es um den fertigen Export
        # geht, nicht die aktuelle Anzeige).
        graph_box = QtWidgets.QGroupBox("Temperaturverlauf-Graph")
        graph_layout = QtWidgets.QVBoxLayout(graph_box)
        self.chk_show_graph = QtWidgets.QCheckBox("Graph mit exportieren")
        self.chk_show_graph.setToolTip(
            "Zeigt den gewählten Kurven-Graphen (mit der wandernden Zeit-Markierung, "
            "genau wie im Hauptfenster) zusätzlich im Export an."
        )
        graph_layout.addWidget(self.chk_show_graph)

        # Eingerueckt unter "Graph mit exportieren" -- Inhalt/Position sind
        # nur relevant, wenn ueberhaupt ein Graph exportiert wird. Der Cursor
        # lebt bewusst NICHT mehr hier drin (siehe cursor_box weiter unten):
        # er zeigt sich auf dem THERMOBILD, nicht im Graphen, war hier aber
        # frueher verschachtelt -- dadurch war unklar, ob man gerade das Bild
        # oder den Graphen konfiguriert (UX-Feedback: "im Bereich
        # 'Temperaturverlauf-Graph' weiß ich manchmal nicht, was ich hier
        # konkret konfiguriere... das Bild? den Graphen?"), und der Cursor war
        # ausserdem ungewollt an "Graph mit exportieren" gekoppelt (ohne
        # Graph nicht erreichbar) -- Widerspruch zum Nutzerwunsch, Cursor-im-
        # Bild und Live-Cursor-KURVE unabhaengig voneinander waehlbar zu
        # machen (nur die Kurve setzt den Cursor voraus, nicht umgekehrt).
        graph_indent_row = QtWidgets.QHBoxLayout()
        graph_indent_row.addSpacing(20)
        graph_indent_col = QtWidgets.QVBoxLayout()

        self._content_widgets = _build_graph_content_selector(
            roi_entries or [], live_available, default_live_checked=False
        )
        graph_indent_col.addWidget(self._content_widgets["group_box"])

        self.combo_graph_position = QtWidgets.QComboBox()
        self.combo_graph_position.addItem("Unter dem Bild", "unten")
        self.combo_graph_position.addItem("Über dem Bild", "oben")
        self.combo_graph_position.addItem("Links vom Bild", "links")
        self.combo_graph_position.addItem("Rechts vom Bild", "rechts")
        position_form = QtWidgets.QFormLayout()
        position_form.addRow("Position:", self.combo_graph_position)
        graph_indent_col.addLayout(position_form)

        graph_indent_row.addLayout(graph_indent_col)
        graph_layout.addLayout(graph_indent_row)

        def _update_graph_enabled(checked: bool) -> None:
            self._content_widgets["group_box"].setEnabled(checked)
            self.combo_graph_position.setEnabled(checked)

        self.chk_show_graph.toggled.connect(_update_graph_enabled)
        _update_graph_enabled(False)
        row2.addWidget(graph_box, 1)
        layout.addLayout(row2)

        # Eigener, vom Graphen UNABHAENGIGER Kasten fuer den Cursor IM BILD
        # (Fadenkreuz + Live-Temperatur-Text direkt auf dem Thermobild) --
        # bewusst getrennt von "Temperaturverlauf-Graph" (siehe Kommentar
        # oben) und daher auch nicht an "Graph mit exportieren" gekoppelt.
        # Zusammen mit "Zeitanzeige im Bild" in einer Zeile, da beides
        # zusaetzliche Einblendungen DIREKT AUF DEM BILD sind (im Unterschied
        # zum Graphen-Inhalt/-Position oben).
        cursor_box = QtWidgets.QGroupBox("Cursor im Bild")
        cursor_layout = QtWidgets.QVBoxLayout(cursor_box)
        self.chk_cursor_position = QtWidgets.QCheckBox("Cursor-Position im Bild anzeigen")
        self.chk_cursor_position.setChecked(False)
        self.chk_cursor_position.setToolTip(
            "Blendet das Fadenkreuz samt Temperaturanzeige am (fixierten oder\n"
            "zuletzt mit der Maus angezeigten) Cursor-Pixel im exportierten\n"
            "Video/Bildstapel mit ein. Unabhängig von „Graph mit exportieren“\n"
            "und der Live-Cursor-KURVE im Graphen einzeln steuerbar -- die\n"
            "Kurve setzt diese Option aber voraus."
        )
        cursor_layout.addWidget(self.chk_cursor_position)
        _wire_cursor_curve_dependency(self.chk_cursor_position, self._content_widgets["chk_live"])

        # Bewusst NICHT "Zeitachse" genannt (frueherer Stand): dieser Name wird
        # an anderer Stelle (Hauptfenster-Steuerung, GraphicExportDialog) schon
        # fuer die x-Achsen-Anzeige des KURVEN-GRAPHEN verwendet
        # (Uhrzeit/Laufzeit/Beide) -- hier geht es dagegen um einen Text/Balken,
        # der direkt IN DAS BILD/VIDEO eingebrannt wird, ein anderes Konzept.
        # Gleicher Begriff fuer zwei unterschiedliche Dinge sorgte fuer genau
        # die Verwechslungsgefahr, die im UX-Review vermieden werden sollte
        # (Bugreport: "Sind alle Namen der Optionen ... intuitiv, oder koennte
        # ich etwas missverstehen?"). Die einzelne Option "Laufzeit" (Fortschritts-
        # balken mit verstrichener Zeit) bleibt klar vom "Zeitstempel" (reales
        # Datum/Uhrzeit) unterschieden; Tooltips erklaeren, was genau jede
        # Option im Export einblendet (Bugreport: unklar, wie die jeweilige
        # Anzeige am Ende aussieht).
        overlay_box = QtWidgets.QGroupBox("Zeitanzeige im Bild")
        overlay_grid = QtWidgets.QGridLayout(overlay_box)
        self.radio_overlay_timeline = QtWidgets.QRadioButton("Laufzeit")
        self.radio_overlay_timeline.setToolTip(
            "Fortschrittsbalken unten im Bild mit der seit Aufnahmebeginn "
            "verstrichenen Zeit (HH:MM:SS) -- wie der Frame-Regler im Hauptfenster."
        )
        self.radio_overlay_none = QtWidgets.QRadioButton("Keine")
        self.radio_overlay_none.setToolTip("Kein zusätzlicher Zeit-Balken im Export.")
        self.radio_overlay_timestamp = QtWidgets.QRadioButton("Zeitstempel")
        self.radio_overlay_timestamp.setToolTip(
            "Reales Aufnahmedatum/-uhrzeit (JJJJ-MM-TT HH:MM:SS) des jeweiligen Frames "
            "als Text unten im Bild."
        )
        self.radio_overlay_both = QtWidgets.QRadioButton("Beides")
        self.radio_overlay_both.setToolTip("Laufzeit UND Zeitstempel gemeinsam unten im Bild.")
        self.radio_overlay_both.setChecked(True)
        overlay_grid.addWidget(self.radio_overlay_timeline, 0, 0)
        overlay_grid.addWidget(self.radio_overlay_none, 0, 1)
        overlay_grid.addWidget(self.radio_overlay_timestamp, 1, 0)
        overlay_grid.addWidget(self.radio_overlay_both, 1, 1)

        # Cursor-im-Bild und Zeitanzeige-im-Bild nebeneinander -- beides sind
        # zusaetzliche Einblendungen direkt auf dem Bild/Video (im Unterschied
        # zum Graphen-Kasten oben), daher hier bewusst als eigenes Zeilenpaar
        # gruppiert statt einzeln untereinander.
        overlay_row = QtWidgets.QHBoxLayout()
        overlay_row.addWidget(cursor_box, 1)
        overlay_row.addWidget(overlay_box, 1)
        layout.addLayout(overlay_row)

        buttons = QtWidgets.QDialogButtonBox(
            QtWidgets.QDialogButtonBox.StandardButton.Ok | QtWidgets.QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self._on_accept)
        buttons.rejected.connect(self.reject)
        _disable_enter_auto_accept(buttons)
        layout.addWidget(buttons)

    def _on_accept(self) -> None:
        if self.spin_end.value() < self.spin_start.value():
            QtWidgets.QMessageBox.warning(
                self, "Ungültiger Bereich", "Der End-Frame muss größer oder gleich dem Start-Frame sein."
            )
            return
        if self.chk_show_graph.isChecked():
            any_roi = any(chk.isChecked() for chk in self._content_widgets["checks"].values())
            if not any_roi and not self._content_widgets["chk_live"].isChecked():
                QtWidgets.QMessageBox.information(
                    self, "Keine Auswahl",
                    "Bitte mindestens einen Messbereich und/oder Live-Cursor für den Graphen auswählen."
                )
                return
        self.accept()

    def frame_range(self) -> tuple[int, int]:
        # UI ist 1-basiert (siehe oben), Rueckgabe als 0-basierte Frame-Indizes.
        return self.spin_start.value() - 1, self.spin_end.value() - 1

    def output_mode(self) -> str:
        return "video" if self.radio_output_video.isChecked() else "images"

    def image_format(self) -> str:
        return self.combo_image_format.currentData()

    def image_prefix(self) -> str:
        return sanitize_filename_prefix(self.edit_image_prefix.text(), fallback="Frame_")

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

    def export_cursor_position(self) -> bool:
        return self.chk_cursor_position.isChecked()

    def custom_level_mode(self) -> str:
        return self.combo_level_mode.currentData()

    def custom_min_max(self) -> tuple[float, float]:
        return self.spin_min.value(), self.spin_max.value()

    def show_graph(self) -> bool:
        return self.chk_show_graph.isChecked()

    def included_roi_numbers(self) -> set[int]:
        return {number for number, chk in self._content_widgets["checks"].items() if chk.isChecked()}

    def include_live(self) -> bool:
        return self._content_widgets["chk_live"].isChecked()

    def graph_position(self) -> str:
        return self.combo_graph_position.currentData()

    def timeline_overlay_mode(self) -> str:
        if self.radio_overlay_both.isChecked():
            return "both"
        if self.radio_overlay_timestamp.isChecked():
            return "timestamp"
        if self.radio_overlay_timeline.isChecked():
            return "timeline"
        return "none"


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


class AxisSettingsDialog(_NoEnterAutoAccept, QtWidgets.QDialog):
    """Manueller Wertebereich (X/Y) und manuelle Y-Schrittweite fuer EINEN
    Kurven-Graphen -- Ersatz fuer das schwer auffindbare "X/Y axis"-
    Untermenue im pyqtgraph-Standard-Rechtsklickmenue (Nutzerwunsch: "die
    Achsen ... nach belieben einstellen ... mehr Entscheidungsfreiheit").

    Bewusst OHNE X-Achsen-Schrittweite: die Zeitachse ist eine
    DateAxisItem, die ihre Tick-Intervalle automatisch anhand
    kalendertypischer, gut lesbarer Abstaende waehlt (z.B. alle 5/15/30
    Minuten) -- ein frei waehlbarer Sekundenwert wuerde dort in der Praxis
    zu haesslichen, nicht-runden Intervallen fuehren (siehe Hinweistext
    unten im Dialog)."""

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
        x_note = QtWidgets.QLabel(
            "Hinweis: Eine feste Schrittweite ist für die Zeitachse nicht wählbar -- "
            "sie wählt automatisch gut lesbare Kalenderabstände (z.B. alle 5/15/30 "
            "Minuten), abhängig vom sichtbaren Zeitraum."
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

        def _update_enabled() -> None:
            self.spin_x_min.setEnabled(self.chk_x_manual.isChecked())
            self.spin_x_max.setEnabled(self.chk_x_manual.isChecked())
            self.spin_y_min.setEnabled(self.chk_y_manual_range.isChecked())
            self.spin_y_max.setEnabled(self.chk_y_manual_range.isChecked())
            self.spin_y_spacing.setEnabled(self.chk_y_manual_spacing.isChecked())

        self.chk_x_manual.toggled.connect(_update_enabled)
        self.chk_y_manual_range.toggled.connect(_update_enabled)
        self.chk_y_manual_spacing.toggled.connect(_update_enabled)
        # Checkboxen spiegeln den TATSAECHLICH gerade aktiven Achsen-Zustand
        # wider (statt beim erneuten Oeffnen immer wieder bei "Automatisch"
        # zu starten) -- sonst wirkte ein zuvor gesetzter manueller Bereich
        # beim Wiederoeffnen faelschlich so, als waere er nie angewendet
        # worden.
        self.chk_x_manual.setChecked(x_manual)
        self.chk_y_manual_range.setChecked(y_manual_range)
        self.chk_y_manual_spacing.setChecked(y_spacing is not None)
        _update_enabled()

        buttons = QtWidgets.QDialogButtonBox(
            QtWidgets.QDialogButtonBox.StandardButton.Ok | QtWidgets.QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self._on_accept)
        buttons.rejected.connect(self.reject)
        _disable_enter_auto_accept(buttons)
        layout.addWidget(buttons)

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

    def y_manual_range(self) -> bool:
        return self.chk_y_manual_range.isChecked()

    def y_range(self) -> tuple[float, float]:
        return self.spin_y_min.value(), self.spin_y_max.value()

    def y_manual_spacing(self) -> bool:
        return self.chk_y_manual_spacing.isChecked()

    def y_spacing(self) -> float:
        return self.spin_y_spacing.value()


class CsvColumnDialog(_NoEnterAutoAccept, QtWidgets.QDialog):
    """Export-Auswahl fuer die CSV-Werte: welche Messbereiche ueberhaupt
    exportiert werden (Standard: alle) und mit welcher Spaltenueberschrift."""

    def __init__(self, parent, entries: list[dict]):
        # entries: [{"name": str, "width_px": float, "height_px": float,
        #            "width_mm": float | None, "height_mm": float | None}, ...]
        # Kann neben echten Messbereichen (Punkt 5) auch eine synthetische
        # "Live (Cursor)"-Zeile enthalten (width_px/height_px = Kantenlaenge
        # des Live-Cursor-Mittelungsfensters) -- fuer diese Zeile gilt exakt
        # dieselbe Auswahl-/Autofill-Logik wie fuer echte Messbereiche.
        super().__init__(parent)
        self.setWindowTitle("Werte exportieren")

        layout = QtWidgets.QVBoxLayout(self)

        format_form = QtWidgets.QFormLayout()
        self.combo_format = QtWidgets.QComboBox()
        self.combo_format.addItem("CSV (';'-getrennt, Dezimalkomma)", "csv")
        self.combo_format.addItem("JSON", "json")
        self.combo_format.addItem("Text (Tab-getrennt, Dezimalkomma)", "text")
        self.combo_format.setToolTip(
            "CSV/Text unterscheiden sich nur im Trennzeichen (';' bzw. Tabulator) -- beide "
            "nutzen wie die Rohdaten Dezimalkomma. JSON nutzt echte Zahlen mit Dezimalpunkt "
            "(Standard-Zahlenformat in JSON, unabhängig vom Locale)."
        )
        format_form.addRow("Format:", self.combo_format)
        layout.addLayout(format_form)

        layout.addWidget(QtWidgets.QLabel(
            "Spalten (Messbereiche und/oder Live-Cursor) für den Export auswählen und "
            "Spaltenüberschriften anpassen (per Hand oder per Autofill):"
        ))

        select_row = QtWidgets.QHBoxLayout()
        btn_select_all = QtWidgets.QPushButton("Alle auswählen")
        btn_select_all.clicked.connect(partial(self._set_all_checked, True))
        select_row.addWidget(btn_select_all)
        btn_select_none = QtWidgets.QPushButton("Keine auswählen")
        btn_select_none.clicked.connect(partial(self._set_all_checked, False))
        select_row.addWidget(btn_select_none)
        select_row.addStretch(1)
        layout.addLayout(select_row)

        grid_header = QtWidgets.QHBoxLayout()
        grid_header.addWidget(QtWidgets.QLabel(
            "„px“/„mm“ ankreuzen (kombinierbar) -- der Spaltenname aktualisiert "
            "sich dabei sofort automatisch, bleibt danach aber frei editierbar. "
            "Standardmäßig aus, „ALLE“ markiert/entmarkiert die jeweilige Spalte "
            "für alle Zeilen auf einmal:"
        ))
        grid_header.addStretch(1)
        layout.addLayout(grid_header)

        self._checks: list[QtWidgets.QCheckBox] = []
        self._edits: list[QtWidgets.QLineEdit] = []
        self._px_checks: list[QtWidgets.QCheckBox] = []
        self._mm_checks: list[QtWidgets.QCheckBox] = []
        self._bulk_update = False
        grid = QtWidgets.QGridLayout()

        # Kopfzeile mit den beiden "ALLE"-Sammel-Checkboxen fuer px/mm.
        self.chk_px_all = QtWidgets.QCheckBox("ALLE px")
        self.chk_px_all.setToolTip("Pixel-Größe für alle (auswählbaren) Zeilen auf einmal an-/abhaken.")
        self.chk_mm_all = QtWidgets.QCheckBox("ALLE mm")
        self.chk_mm_all.setToolTip("Reale Größe in mm für alle (auswählbaren) Zeilen auf einmal an-/abhaken.")
        header_unit_row = QtWidgets.QHBoxLayout()
        header_unit_row.setContentsMargins(0, 0, 0, 0)
        header_unit_row.addWidget(self.chk_px_all)
        header_unit_row.addWidget(self.chk_mm_all)
        header_unit_widget = QtWidgets.QWidget()
        header_unit_widget.setLayout(header_unit_row)
        grid.addWidget(header_unit_widget, 0, 3)

        for offset, entry in enumerate(entries):
            row = offset + 1
            chk = QtWidgets.QCheckBox()
            chk.setChecked(True)
            chk.setToolTip(f"„{entry['name']}“ in den Export einschließen")
            grid.addWidget(chk, row, 0)
            self._checks.append(chk)

            grid.addWidget(QtWidgets.QLabel(entry["name"]), row, 1)
            edit = QtWidgets.QLineEdit(f'{entry["name"]} (°C)')
            chk.toggled.connect(edit.setEnabled)
            grid.addWidget(edit, row, 2)
            self._edits.append(edit)

            has_mm = entry.get("width_mm") is not None
            chk_px = QtWidgets.QCheckBox("px")
            chk_px.setChecked(False)
            chk_px.setToolTip("Pixel-Größe in den Spaltennamen aufnehmen")
            chk_mm = QtWidgets.QCheckBox("mm")
            chk_mm.setChecked(False)
            chk_mm.setToolTip("Reale Größe in mm in den Spaltennamen aufnehmen (benötigt gesetzten Maßstab)")
            chk_mm.setEnabled(has_mm)
            self._px_checks.append(chk_px)
            self._mm_checks.append(chk_mm)
            unit_row = QtWidgets.QHBoxLayout()
            unit_row.setContentsMargins(0, 0, 0, 0)
            for w in (chk_px, chk_mm):
                unit_row.addWidget(w)
                chk.toggled.connect(w.setEnabled if w is chk_px else partial(self._update_mm_checkbox_enabled, w, entry))
            unit_widget = QtWidgets.QWidget()
            unit_widget.setLayout(unit_row)
            grid.addWidget(unit_widget, row, 3)

            # Ein-/Ausschluss der ganzen Zeile aendert, welche px/mm-
            # Checkboxen ueberhaupt "relevant" (aktiviert) sind -- NACH den
            # obigen setEnabled()-Verbindungen angehaengt, damit die "ALLE"-
            # Sammel-Checkbox stets den bereits aktualisierten Aktiviert-
            # Zustand sieht (Signal-Reihenfolge = Verbindungsreihenfolge).
            chk.toggled.connect(partial(self._sync_all_checkbox, self.chk_px_all, self._px_checks))
            chk.toggled.connect(partial(self._sync_all_checkbox, self.chk_mm_all, self._mm_checks))

            # Klick auf "px" oder "mm" aktualisiert den Spaltennamen sofort --
            # kein separater "Übernehmen"-Knopf mehr noetig. Der Name bleibt
            # danach trotzdem frei editierbar (Autofill ueberschreibt ihn nur
            # bei einem erneuten Klick auf eines der beiden Haekchen).
            chk_px.toggled.connect(partial(self._apply_autofill, edit, entry, chk_px, chk_mm))
            chk_mm.toggled.connect(partial(self._apply_autofill, edit, entry, chk_px, chk_mm))
            chk_px.toggled.connect(partial(self._sync_all_checkbox, self.chk_px_all, self._px_checks))
            chk_mm.toggled.connect(partial(self._sync_all_checkbox, self.chk_mm_all, self._mm_checks))
        layout.addLayout(grid)

        # Ohne gesetzten Massstab ist "mm" fuer JEDE Zeile deaktiviert -- die
        # Sammel-Checkbox waere dann klickbar, haette aber nie irgendeine
        # Wirkung. Von Anfang an deaktivieren statt eines wirkungslosen Hakens.
        if not any(entry.get("width_mm") is not None for entry in entries):
            self.chk_mm_all.setEnabled(False)
            self.chk_mm_all.setToolTip("Kein Maßstab gesetzt -- reale Größe in mm nicht verfügbar.")

        self.chk_px_all.toggled.connect(partial(self._bulk_set_checked, self._px_checks))
        self.chk_mm_all.toggled.connect(partial(self._bulk_set_checked, self._mm_checks))

        buttons = QtWidgets.QDialogButtonBox(
            QtWidgets.QDialogButtonBox.StandardButton.Ok | QtWidgets.QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self._on_accept)
        buttons.rejected.connect(self.reject)
        _disable_enter_auto_accept(buttons)
        layout.addWidget(buttons)

    def _set_all_checked(self, checked: bool) -> None:
        for chk in self._checks:
            chk.setChecked(checked)

    @staticmethod
    def _update_mm_checkbox_enabled(chk_unit: QtWidgets.QCheckBox, entry: dict, checked: bool) -> None:
        chk_unit.setEnabled(checked and entry.get("width_mm") is not None)

    def _bulk_set_checked(self, checks: list[QtWidgets.QCheckBox], checked: bool) -> None:
        """Setzt alle (aktivierten) px- bzw. mm-Checkboxen auf einmal --
        Handler der "ALLE"-Sammel-Checkbox. Das Sperr-Flag verhindert, dass
        jede einzelne dadurch ausgeloeste toggled()-Rueckmeldung
        (_sync_all_checkbox) die Sammel-Checkbox waehrend des Durchlaufs
        selbst wieder veraendert."""
        self._bulk_update = True
        try:
            for chk in checks:
                if chk.isEnabled():
                    chk.setChecked(checked)
        finally:
            self._bulk_update = False

    def _sync_all_checkbox(
        self, master: QtWidgets.QCheckBox, checks: list[QtWidgets.QCheckBox], *_args
    ) -> None:
        """Haelt die "ALLE"-Sammel-Checkbox konsistent, wenn eine einzelne
        Zeile manuell (de-)aktiviert wird -- angehakt, sobald alle aktuell
        auswählbaren (aktivierten) Zeilen-Checkboxen angehakt sind."""
        if self._bulk_update:
            return
        relevant = [c for c in checks if c.isEnabled()]
        all_checked = bool(relevant) and all(c.isChecked() for c in relevant)
        master.blockSignals(True)
        master.setChecked(all_checked)
        master.blockSignals(False)

    def _on_accept(self) -> None:
        if not any(chk.isChecked() for chk in self._checks):
            QtWidgets.QMessageBox.information(
                self, "Keine Auswahl", "Bitte mindestens eine Spalte für den Export auswählen."
            )
            return
        self.accept()

    @staticmethod
    def _apply_autofill(
        edit: QtWidgets.QLineEdit,
        entry: dict,
        chk_px: QtWidgets.QCheckBox,
        chk_mm: QtWidgets.QCheckBox,
        *_args,
    ) -> None:
        # *_args faengt das von QCheckBox.toggled mitgesendete bool-Argument
        # ab (Signal-Handler, direkt per partial() an toggled gehaengt).
        # Deutsches Zahlenformat (Dezimalkomma), konsistent mit den uebrigen
        # Zahlenanzeigen der App (z.B. Massstab-Label, CSV-Werte). Beliebig
        # kombinierbar (z.B. px UND mm gleichzeitig im Spaltennamen), da der
        # Nutzer beide Groessenangaben gleichzeitig sehen wollte.
        parts = []
        if chk_px.isChecked():
            parts.append(f'{entry["width_px"]:.0f}x{entry["height_px"]:.0f} px')
        if chk_mm.isChecked() and entry.get("width_mm") is not None:
            w = f'{entry["width_mm"]:.1f}'.replace(".", ",")
            h = f'{entry["height_mm"]:.1f}'.replace(".", ",")
            parts.append(f"{w}x{h} mm")
        suffix = f" ({', '.join(parts)})" if parts else ""
        edit.setText(f'{entry["name"]}{suffix} (°C)')

    def included(self) -> list[bool]:
        return [chk.isChecked() for chk in self._checks]

    def column_names(self) -> list[str]:
        return [edit.text().strip() or "Messwert" for edit in self._edits]

    def format(self) -> str:
        return self.combo_format.currentData()


class FilenameTemplateDialog(_NoEnterAutoAccept, QtWidgets.QDialog):
    """Fragt ein eigenes Dateinamens-Template ab, wenn im gewaehlten Ordner
    keine Datei zum aktuell aktiven Namensschema passt (siehe
    MainWindow._open_folder) -- mit Live-Vorschau, welche der tatsaechlich
    vorhandenen ".csv"-Dateien zum gerade eingegebenen Template passen
    wuerden, damit der Nutzer das Ergebnis vor dem Bestaetigen pruefen kann."""

    _MAX_PREVIEW_ITEMS = 30

    def __init__(self, parent, folder: Path, current_template: str):
        super().__init__(parent)
        self.setWindowTitle("Namensschema anpassen")
        self._all_csv_files = sorted(p for p in Path(folder).glob("*.csv") if p.is_file())

        layout = QtWidgets.QVBoxLayout(self)

        intro = QtWidgets.QLabel(
            f"Im Ordner „{folder}“ passt keine Datei zum aktuellen Namensschema. "
            "Bitte das tatsächliche Namensschema eingeben (ohne „.csv“):"
        )
        intro.setWordWrap(True)
        layout.addWidget(intro)

        form = QtWidgets.QFormLayout()
        self.edit_template = QtWidgets.QLineEdit(current_template)
        form.addRow("Namensschema:", self.edit_template)
        layout.addLayout(form)

        help_label = QtWidgets.QLabel(
            "Platzhalter (Groß-/Kleinschreibung beachten!): YYYY = Jahr (4-stellig), "
            "MM = Monat, DD = Tag, hh = Stunde, mm = Minute, ss = Sekunde (jeweils "
            "2-stellig). Alle anderen Zeichen (z.B. „Record_“, „-“, „_“) müssen genau "
            "so im Dateinamen stehen.\n"
            "Beispiel: „Record_YYYY-MM-DD_hh-mm-ss“ passt zu "
            "„Record_2026-08-24_14-30-00.csv“ -- auch feste Textteile, die "
            "zufällig wie ein Platzhalter aussehen (z.B. das „ss“ in "
            "„Messung_“), werden korrekt als normaler Text erkannt, solange "
            "sie nicht direkt an einen echten Platzhalter anschließen."
        )
        help_label.setWordWrap(True)
        layout.addWidget(help_label)

        self.chk_persist = QtWidgets.QCheckBox("Als neues Standard-Namensschema dauerhaft speichern")
        self.chk_persist.setChecked(False)
        self.chk_persist.setToolTip(
            "Standardmäßig gilt dieses Namensschema nur für den jetzt zu ladenden Ordner "
            "(das bisherige Schema bleibt beim nächsten Mal wieder aktiv). Angehakt wird "
            "es stattdessen dauerhaft gespeichert und ab sofort automatisch verwendet."
        )
        layout.addWidget(self.chk_persist)

        layout.addWidget(QtWidgets.QLabel("Live-Vorschau der passenden Dateien in diesem Ordner:"))
        self.status_label = QtWidgets.QLabel()
        self.status_label.setWordWrap(True)
        layout.addWidget(self.status_label)
        self.preview_list = QtWidgets.QListWidget()
        self.preview_list.setMaximumHeight(160)
        layout.addWidget(self.preview_list)

        self.buttons = QtWidgets.QDialogButtonBox(
            QtWidgets.QDialogButtonBox.StandardButton.Ok | QtWidgets.QDialogButtonBox.StandardButton.Cancel
        )
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)
        _disable_enter_auto_accept(self.buttons)
        layout.addWidget(self.buttons)

        self.edit_template.textChanged.connect(self._update_preview)
        self._update_preview()

    def _update_preview(self) -> None:
        template = self.edit_template.text()
        ok_button = self.buttons.button(QtWidgets.QDialogButtonBox.StandardButton.Ok)
        self.preview_list.clear()

        error = validate_filename_template(template)
        if error is not None:
            self.status_label.setText(f"⚠ {error}")
            ok_button.setEnabled(False)
            return

        pattern, _fmt = compile_filename_template(template)
        matched = [p for p in self._all_csv_files if pattern.search(p.stem)]
        for p in matched[: self._MAX_PREVIEW_ITEMS]:
            self.preview_list.addItem(p.name)
        if len(matched) > self._MAX_PREVIEW_ITEMS:
            self.preview_list.addItem(f"… und {len(matched) - self._MAX_PREVIEW_ITEMS} weitere")

        total = len(self._all_csv_files)
        if not matched:
            self.status_label.setText(f"⚠ 0 von {total} CSV-Datei(en) im Ordner passen zu diesem Schema.")
            ok_button.setEnabled(False)
        else:
            self.status_label.setText(f"{len(matched)} von {total} CSV-Datei(en) im Ordner passen zu diesem Schema.")
            ok_button.setEnabled(True)

    def template(self) -> str:
        return self.edit_template.text()

    def persist(self) -> bool:
        return self.chk_persist.isChecked()


# Feste Presets statt frei editierbarer Felder -- verhindert ungueltige
# Kombinationen (z.B. ein leeres Dezimaltrennzeichen) und deckt die in der
# Praxis vorkommenden Roh-Exportformate ab. "" als Trennzeichen-Wert steht
# fuer "beliebig viele Leerzeichen" (siehe ImportSettings/_parse_data_line
# in data.py -- str.split() ohne Argument statt eines festen Trennzeichens).
_DELIMITER_OPTIONS: list[tuple[str, str]] = [
    ("Semikolon ( ; )", ";"),
    ("Komma ( , )", ","),
    ("Tabulator", "\t"),
    ("Senkrechter Strich ( | )", "|"),
    ("Leerzeichen (beliebig viele)", ""),
]
_DECIMAL_OPTIONS: list[tuple[str, str]] = [
    ("Komma ( , )", ","),
    ("Punkt ( . )", "."),
]
_ENCODING_OPTIONS: list[tuple[str, str]] = [
    ("UTF-8 (Standard)", "utf-8-sig"),
    ("Windows-1252 / Latin-1", "cp1252"),
    ("UTF-16", "utf-16"),
]


class ImportSettingsDialog(_NoEnterAutoAccept, QtWidgets.QDialog):
    """Datenimport-Manager: bereitet rohe Messdateien mit abweichendem
    Format (zusaetzliche Kopf-/Fusszeilen, eine fuehrende Index-Spalte,
    anderes Trennzeichen/Dezimaltrennzeichen/Kodierung) fuers Einlesen vor
    -- mit sofortiger Live-Vorschau gegen eine echte Beispieldatei, damit
    das Ergebnis VOR dem eigentlichen Laden sichtbar ist.

    Hintergrund: die App wird in Kuerze auch Messreihen aus anderen
    Quellen/Geraeten einlesen koennen sollen, deren genaues Rohformat noch
    nicht bekannt ist (noch keine Testdateien vorhanden) -- dieser Dialog
    macht das feste, bisher fest einprogrammierte CSV-Format
    (';'-getrennt, Dezimalkomma, keine Kopfzeilen) an zentraler Stelle
    nutzerseitig anpassbar, statt es im Code fest zu verdrahten."""

    _MAX_RAW_PREVIEW_LINES = 40
    _PARSED_PREVIEW_ROWS = 6
    _PARSED_PREVIEW_COLS = 8

    def __init__(self, parent, sample_path: Path, settings: ImportSettings):
        super().__init__(parent)
        self.setWindowTitle("Datenimport anpassen")
        self.setMinimumSize(780, 620)
        self._sample_path = Path(sample_path)

        layout = QtWidgets.QVBoxLayout(self)

        intro = QtWidgets.QLabel(
            "Legt fest, wie eine rohe Messdatei in ein Temperatur-Raster umgewandelt wird -- "
            "nützlich, wenn Messreihen aus einer anderen Quelle ein abweichendes Format "
            "mitbringen (z.B. zusätzliche Kopfzeilen, eine führende Index-Spalte, ein anderes "
            "Trennzeichen). Die Vorschau unten zeigt sofort, ob die aktuelle Einstellung auf "
            "die Beispieldatei passt."
        )
        intro.setWordWrap(True)
        layout.addWidget(intro)

        file_row = QtWidgets.QHBoxLayout()
        file_row.addWidget(QtWidgets.QLabel("Beispieldatei:"))
        self.lbl_sample_path = QtWidgets.QLabel(str(self._sample_path))
        self.lbl_sample_path.setTextInteractionFlags(QtCore.Qt.TextInteractionFlag.TextSelectableByMouse)
        file_row.addWidget(self.lbl_sample_path, 1)
        btn_pick = QtWidgets.QPushButton("Andere Datei wählen…")
        btn_pick.clicked.connect(self._pick_sample_file)
        file_row.addWidget(btn_pick)
        layout.addLayout(file_row)

        # Einstellungen und Roh-Vorschau nebeneinander, damit die Wirkung
        # einer Aenderung direkt neben den tatsaechlichen Kopfzeilen/dem
        # tatsaechlichen Trennzeichen der Beispieldatei sichtbar ist.
        top_row = QtWidgets.QHBoxLayout()

        form_box = QtWidgets.QGroupBox("Einstellungen")
        form = QtWidgets.QFormLayout(form_box)
        self.combo_delimiter = QtWidgets.QComboBox()
        for label, _value in _DELIMITER_OPTIONS:
            self.combo_delimiter.addItem(label)
        form.addRow("Trennzeichen:", self.combo_delimiter)

        self.combo_decimal = QtWidgets.QComboBox()
        for label, _value in _DECIMAL_OPTIONS:
            self.combo_decimal.addItem(label)
        form.addRow("Dezimaltrennzeichen:", self.combo_decimal)

        self.combo_encoding = QtWidgets.QComboBox()
        for label, _value in _ENCODING_OPTIONS:
            self.combo_encoding.addItem(label)
        form.addRow("Zeichenkodierung:", self.combo_encoding)

        self.spin_skip_header = QtWidgets.QSpinBox()
        self.spin_skip_header.setRange(0, 500)
        self.spin_skip_header.setToolTip("Anzahl der Zeilen am Dateianfang, die keine Messwerte enthalten.")
        form.addRow("Kopfzeilen überspringen:", self.spin_skip_header)

        self.spin_skip_footer = QtWidgets.QSpinBox()
        self.spin_skip_footer.setRange(0, 500)
        self.spin_skip_footer.setToolTip("Anzahl der Zeilen am Dateiende, die keine Messwerte enthalten.")
        form.addRow("Fußzeilen überspringen:", self.spin_skip_footer)

        self.spin_skip_leading = QtWidgets.QSpinBox()
        self.spin_skip_leading.setRange(0, 100)
        self.spin_skip_leading.setToolTip("Anzahl der Spalten am Zeilenanfang, die keine Messwerte enthalten (z.B. eine Index-Spalte).")
        form.addRow("Erste Spalte(n) entfernen:", self.spin_skip_leading)

        self.spin_skip_trailing = QtWidgets.QSpinBox()
        self.spin_skip_trailing.setRange(0, 100)
        self.spin_skip_trailing.setToolTip("Anzahl der Spalten am Zeilenende, die keine Messwerte enthalten.")
        form.addRow("Letzte Spalte(n) entfernen:", self.spin_skip_trailing)

        top_row.addWidget(form_box, 1)

        raw_box = QtWidgets.QGroupBox("Rohdaten (Ausschnitt)")
        raw_layout = QtWidgets.QVBoxLayout(raw_box)
        self.raw_preview = QtWidgets.QPlainTextEdit()
        self.raw_preview.setReadOnly(True)
        self.raw_preview.setLineWrapMode(QtWidgets.QPlainTextEdit.LineWrapMode.NoWrap)
        self.raw_preview.setFont(QtGui.QFont("Consolas", 9))
        raw_layout.addWidget(self.raw_preview)
        top_row.addWidget(raw_box, 1)

        layout.addLayout(top_row)

        result_box = QtWidgets.QGroupBox("Ergebnis-Vorschau")
        result_layout = QtWidgets.QVBoxLayout(result_box)
        self.lbl_result_status = QtWidgets.QLabel()
        self.lbl_result_status.setWordWrap(True)
        result_layout.addWidget(self.lbl_result_status)
        self.result_preview = QtWidgets.QPlainTextEdit()
        self.result_preview.setReadOnly(True)
        self.result_preview.setLineWrapMode(QtWidgets.QPlainTextEdit.LineWrapMode.NoWrap)
        self.result_preview.setFont(QtGui.QFont("Consolas", 9))
        self.result_preview.setFixedHeight(140)
        result_layout.addWidget(self.result_preview)
        layout.addWidget(result_box)

        self.chk_persist = QtWidgets.QCheckBox("Als neue Standardeinstellung dauerhaft speichern")
        self.chk_persist.setToolTip(
            "Aus: gilt nur für diesen einen Ladevorgang. An: wird als neuer Standard für "
            "künftige Ladevorgänge gespeichert."
        )
        layout.addWidget(self.chk_persist)

        self.buttons = QtWidgets.QDialogButtonBox(
            QtWidgets.QDialogButtonBox.StandardButton.Ok | QtWidgets.QDialogButtonBox.StandardButton.Cancel
        )
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)
        _disable_enter_auto_accept(self.buttons)
        layout.addWidget(self.buttons)

        self._select_combo_value(self.combo_delimiter, _DELIMITER_OPTIONS, settings.delimiter)
        self._select_combo_value(self.combo_decimal, _DECIMAL_OPTIONS, settings.decimal_separator)
        self._select_combo_value(self.combo_encoding, _ENCODING_OPTIONS, settings.encoding)
        self.spin_skip_header.setValue(settings.skip_header_lines)
        self.spin_skip_footer.setValue(settings.skip_footer_lines)
        self.spin_skip_leading.setValue(settings.skip_leading_columns)
        self.spin_skip_trailing.setValue(settings.skip_trailing_columns)

        for combo in (self.combo_delimiter, self.combo_decimal, self.combo_encoding):
            combo.currentIndexChanged.connect(self._refresh)
        for spin in (self.spin_skip_header, self.spin_skip_footer, self.spin_skip_leading, self.spin_skip_trailing):
            spin.valueChanged.connect(self._refresh)

        self._refresh()

    @staticmethod
    def _select_combo_value(combo: QtWidgets.QComboBox, options: list[tuple[str, str]], value: str) -> None:
        for i, (_label, opt_value) in enumerate(options):
            if opt_value == value:
                combo.setCurrentIndex(i)
                return
        combo.setCurrentIndex(0)

    def _pick_sample_file(self) -> None:
        path, _filter = QtWidgets.QFileDialog.getOpenFileName(
            self, "Beispieldatei wählen", str(self._sample_path.parent),
            "CSV-Dateien (*.csv);;Alle Dateien (*)",
        )
        if not path:
            return
        self._sample_path = Path(path)
        self.lbl_sample_path.setText(str(self._sample_path))
        self._refresh()

    def _refresh(self) -> None:
        settings = self.settings()
        try:
            text = self._sample_path.read_text(encoding=settings.encoding)
            read_error: str | None = None
        except (OSError, LookupError, UnicodeDecodeError) as exc:
            text = ""
            read_error = f"Beispieldatei konnte nicht gelesen werden: {exc}"

        self.raw_preview.setPlainText(
            "\n".join(text.splitlines()[: self._MAX_RAW_PREVIEW_LINES]) or "(leer)"
        )

        ok = False
        if read_error is not None:
            self.lbl_result_status.setText(f"⚠ {read_error}")
            self.result_preview.setPlainText("")
        else:
            try:
                array = parse_frame_text(text, settings)
            except RecordingError as exc:
                self.lbl_result_status.setText(f"⚠ {exc}")
                self.result_preview.setPlainText("")
            else:
                ok = True
                rows, cols = array.shape
                self.lbl_result_status.setText(f"✓ Erkannt: {rows} Zeile(n) × {cols} Spalte(n)")
                corner = array[: self._PARSED_PREVIEW_ROWS, : self._PARSED_PREVIEW_COLS]
                lines = ["  ".join(f"{v:7.2f}" for v in row) for row in corner]
                if cols > self._PARSED_PREVIEW_COLS:
                    lines = [line + "  …" for line in lines]
                if rows > self._PARSED_PREVIEW_ROWS:
                    lines.append("…")
                self.result_preview.setPlainText("\n".join(lines))

        ok_button = self.buttons.button(QtWidgets.QDialogButtonBox.StandardButton.Ok)
        if ok_button is not None:
            ok_button.setEnabled(ok)

    def settings(self) -> ImportSettings:
        return ImportSettings(
            delimiter=_DELIMITER_OPTIONS[self.combo_delimiter.currentIndex()][1],
            decimal_separator=_DECIMAL_OPTIONS[self.combo_decimal.currentIndex()][1],
            encoding=_ENCODING_OPTIONS[self.combo_encoding.currentIndex()][1],
            skip_header_lines=self.spin_skip_header.value(),
            skip_footer_lines=self.spin_skip_footer.value(),
            skip_leading_columns=self.spin_skip_leading.value(),
            skip_trailing_columns=self.spin_skip_trailing.value(),
        )

    def persist(self) -> bool:
        return self.chk_persist.isChecked()
