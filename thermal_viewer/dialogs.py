"""Zusätzliche Dialogfenster für Export- (Grafik, Video, CSV-Spalten) und
Import-Funktionen (Namensschema-Anpassung beim Laden)."""
from __future__ import annotations

from functools import partial
from pathlib import Path

from qtpy import QtCore, QtWidgets

from .data import compile_filename_template, validate_filename_template
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
    group_box = QtWidgets.QGroupBox("Farbdarstellung")
    layout = QtWidgets.QVBoxLayout(group_box)

    radio_current = QtWidgets.QRadioButton("Aktuelle Anzeige-Einstellungen übernehmen")
    radio_custom = QtWidgets.QRadioButton("Eigene Einstellungen für diesen Export")
    radio_current.setChecked(True)
    layout.addWidget(radio_current)
    layout.addWidget(radio_custom)

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
    layout.addLayout(form)

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
    ):
        super().__init__(parent)
        self.setWindowTitle("Grafik exportieren")
        self._settings = settings
        self._show_mode_choice = show_mode_choice

        layout = QtWidgets.QVBoxLayout(self)

        # Nur noch EIN Grafik-Export-Fenster statt getrennter "Zeitverlauf-"/
        # "Live-Grafik"-Menüpunkte (Nutzerwunsch): hier wird gewählt, welche
        # Kurve(n) tatsächlich mit exportiert werden sollen.
        self.chk_include_timeseries = None
        self.chk_include_live = None
        if show_graph_source_choice:
            source_box = QtWidgets.QGroupBox("Graph-Inhalt")
            source_layout = QtWidgets.QVBoxLayout(source_box)
            self.chk_include_timeseries = QtWidgets.QCheckBox("Zeitverlauf (Messbereiche)")
            self.chk_include_timeseries.setChecked(True)
            self.chk_include_timeseries.setToolTip(
                "Temperaturverlauf aller platzierten Messbereiche."
            )
            source_layout.addWidget(self.chk_include_timeseries)
            self.chk_include_live = QtWidgets.QCheckBox("Live-Cursor-Kurve")
            self.chk_include_live.setChecked(live_available)
            self.chk_include_live.setEnabled(live_available)
            self.chk_include_live.setToolTip(
                "Temperaturverlauf des fixierten/zuletzt mit der Maus gezeigten Cursor-Pixels."
                if live_available else
                "Kein Live-Cursor-Pixel gewählt (Maus über das Bild bewegen oder eine Stelle "
                "fixieren, um diese Option zu aktivieren)."
            )
            source_layout.addWidget(self.chk_include_live)
            layout.addWidget(source_box)

        form = QtWidgets.QFormLayout()
        self.spin_dpi = QtWidgets.QSpinBox()
        self.spin_dpi.setRange(50, 1200)
        self.spin_dpi.setSingleStep(10)
        self.spin_dpi.setValue(default_dpi)
        form.addRow("Auflösung (DPI):", self.spin_dpi)
        layout.addLayout(form)

        self.radio_combined = None
        self.radio_separate = None
        if show_mode_choice:
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

        self.chk_cursor_position = QtWidgets.QCheckBox("Cursor-Position mit exportieren")
        self.chk_cursor_position.setChecked(False)
        self.chk_cursor_position.setToolTip(
            "Blendet das Fadenkreuz samt Temperaturanzeige am (fixierten oder\n"
            "zuletzt mit der Maus angezeigten) Cursor-Pixel im exportierten\n"
            "Thermobild mit ein. Standardmäßig aus, damit die Grafik nicht\n"
            "ungewollt eine Maus-/Debug-Markierung enthält."
        )
        layout.addWidget(self.chk_cursor_position)

        buttons = QtWidgets.QDialogButtonBox(
            QtWidgets.QDialogButtonBox.StandardButton.Ok | QtWidgets.QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self._on_accept)
        buttons.rejected.connect(self.reject)
        _disable_enter_auto_accept(buttons)
        layout.addWidget(buttons)

    def _on_accept(self) -> None:
        if (
            self.chk_include_timeseries is not None
            and not self.chk_include_timeseries.isChecked()
            and not self.chk_include_live.isChecked()
        ):
            QtWidgets.QMessageBox.information(
                self, "Keine Auswahl", "Bitte mindestens eine Kurve (Zeitverlauf und/oder Live-Cursor) auswählen."
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

    def include_timeseries(self) -> bool:
        return self.chk_include_timeseries is None or self.chk_include_timeseries.isChecked()

    def include_live(self) -> bool:
        return self.chk_include_live is not None and self.chk_include_live.isChecked()

    def export_cursor_position(self) -> bool:
        return self.chk_cursor_position.isChecked()

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
    ):
        super().__init__(parent)
        self.setWindowTitle("Video / Bildstapel exportieren")

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
        output_layout.addLayout(image_form)
        layout.addWidget(output_box)

        range_box = QtWidgets.QGroupBox("Frame-Bereich")
        range_layout = QtWidgets.QFormLayout(range_box)
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
        range_layout.addRow("Von Frame:", self.spin_start)
        range_layout.addRow("Bis Frame:", self.spin_end)
        layout.addWidget(range_box)

        fps_form = QtWidgets.QFormLayout()
        self.spin_fps = LocaleTolerantDoubleSpinBox()
        self.spin_fps.setRange(0.5, 60.0)
        self.spin_fps.setValue(current_fps)
        fps_form.addRow("Wiedergabe-FPS im Video:", self.spin_fps)
        layout.addLayout(fps_form)

        def _update_output_mode_enabled() -> None:
            is_video = self.radio_output_video.isChecked()
            self.spin_fps.setEnabled(is_video)
            self.combo_image_format.setEnabled(not is_video)
            self.edit_image_prefix.setEnabled(not is_video)

        self.radio_output_video.toggled.connect(_update_output_mode_enabled)
        self.radio_output_images.toggled.connect(_update_output_mode_enabled)
        _update_output_mode_enabled()

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

        self.spin_min = LocaleTolerantDoubleSpinBox()
        self.spin_min.setRange(-100.0, 2000.0)
        self.spin_min.setDecimals(1)
        self.spin_min.setValue(current_min)
        self.spin_max = LocaleTolerantDoubleSpinBox()
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

        # Graph (Temperaturverlauf) zusaetzlich zum Thermobild im Export --
        # mit der ohnehin schon vorhandenen wandernden Markierungslinie
        # (frame_marker/live_frame_marker), genau wie im Hauptfenster
        # (Bugreport: "genauso wie in der UI").
        graph_box = QtWidgets.QGroupBox("Temperaturverlauf-Graph")
        graph_layout = QtWidgets.QVBoxLayout(graph_box)
        self.chk_show_graph = QtWidgets.QCheckBox("Graph mit anzeigen")
        self.chk_show_graph.setToolTip(
            "Zeigt den gewählten Kurven-Graphen (mit der wandernden Zeit-Markierung, "
            "genau wie im Hauptfenster) zusätzlich im Export an."
        )
        graph_layout.addWidget(self.chk_show_graph)
        self.combo_graph_source = QtWidgets.QComboBox()
        self.combo_graph_source.addItem("Zeitverlauf (Messbereiche)", "timeseries")
        self.combo_graph_source.addItem("Live (Cursor-Pixel)", "live")
        self.combo_graph_source.setEnabled(False)
        self.chk_show_graph.toggled.connect(self.combo_graph_source.setEnabled)
        self.combo_graph_position = QtWidgets.QComboBox()
        self.combo_graph_position.addItem("Unter dem Bild", "unten")
        self.combo_graph_position.addItem("Über dem Bild", "oben")
        self.combo_graph_position.addItem("Links vom Bild", "links")
        self.combo_graph_position.addItem("Rechts vom Bild", "rechts")
        self.combo_graph_position.setEnabled(False)
        self.chk_show_graph.toggled.connect(self.combo_graph_position.setEnabled)
        graph_form = QtWidgets.QFormLayout()
        graph_form.addRow("Graph:", self.combo_graph_source)
        graph_form.addRow("Position:", self.combo_graph_position)
        graph_layout.addLayout(graph_form)

        # Hierher verschoben (statt eigener Punkt weiter unten) -- gehoert
        # inhaltlich zum Thermobild-Teil des Videos, wird aber nur zusammen
        # mit dem Graphen als sinnvoll empfunden (Nutzerwunsch).
        self.chk_cursor_position = QtWidgets.QCheckBox("Cursor-Position mit exportieren")
        self.chk_cursor_position.setChecked(False)
        self.chk_cursor_position.setToolTip(
            "Blendet das Fadenkreuz samt Temperaturanzeige am (fixierten oder\n"
            "zuletzt mit der Maus angezeigten) Cursor-Pixel im exportierten\n"
            "Video/Bildstapel mit ein. Standardmäßig aus, damit der Export nicht\n"
            "ungewollt eine Maus-/Debug-Markierung enthält."
        )
        graph_layout.addWidget(self.chk_cursor_position)
        layout.addWidget(graph_box)

        # Bereich einheitlich "Zeitachse" genannt (wie beim Bild-Export, siehe
        # GraphicExportDialog) -- die einzelne Option darin heisst "Laufzeit"
        # (Fortschrittsbalken mit verstrichener Zeit), um sie klar vom
        # "Zeitstempel" (reales Datum/Uhrzeit) zu unterscheiden. Zusaetzliche
        # Tooltips erklaeren, was genau jede Option im Export einblendet
        # (Bugreport: unklar, wie die jeweilige Anzeige am Ende aussieht).
        overlay_box = QtWidgets.QGroupBox("Zeitachse")
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
        layout.addWidget(overlay_box)

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
        self.accept()

    def frame_range(self) -> tuple[int, int]:
        # UI ist 1-basiert (siehe oben), Rueckgabe als 0-basierte Frame-Indizes.
        return self.spin_start.value() - 1, self.spin_end.value() - 1

    def output_mode(self) -> str:
        return "video" if self.radio_output_video.isChecked() else "images"

    def image_format(self) -> str:
        return self.combo_image_format.currentData()

    def image_prefix(self) -> str:
        return self.edit_image_prefix.text().strip() or "Frame_"

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

    def graph_source(self) -> str:
        return self.combo_graph_source.currentData()

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
            "sich dabei sofort automatisch, bleibt danach aber frei editierbar:"
        ))
        grid_header.addStretch(1)
        layout.addLayout(grid_header)

        self._checks: list[QtWidgets.QCheckBox] = []
        self._edits: list[QtWidgets.QLineEdit] = []
        grid = QtWidgets.QGridLayout()
        for row, entry in enumerate(entries):
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
            chk_px.setChecked(True)
            chk_px.setToolTip("Pixel-Größe in den Spaltennamen aufnehmen")
            chk_mm = QtWidgets.QCheckBox("mm")
            chk_mm.setToolTip("Reale Größe in mm in den Spaltennamen aufnehmen (benötigt gesetzten Maßstab)")
            chk_mm.setEnabled(has_mm)
            unit_row = QtWidgets.QHBoxLayout()
            unit_row.setContentsMargins(0, 0, 0, 0)
            for w in (chk_px, chk_mm):
                unit_row.addWidget(w)
                chk.toggled.connect(w.setEnabled if w is chk_px else partial(self._update_mm_checkbox_enabled, w, entry))
            unit_widget = QtWidgets.QWidget()
            unit_widget.setLayout(unit_row)
            grid.addWidget(unit_widget, row, 3)

            # Klick auf "px" oder "mm" aktualisiert den Spaltennamen sofort --
            # kein separater "Übernehmen"-Knopf mehr noetig. Der Name bleibt
            # danach trotzdem frei editierbar (Autofill ueberschreibt ihn nur
            # bei einem erneuten Klick auf eines der beiden Haekchen).
            chk_px.toggled.connect(partial(self._apply_autofill, edit, entry, chk_px, chk_mm))
            chk_mm.toggled.connect(partial(self._apply_autofill, edit, entry, chk_px, chk_mm))
        layout.addLayout(grid)

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
