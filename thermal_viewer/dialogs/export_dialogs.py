"""Die beiden großen Export-Dialoge: Einzelgrafik (Thermobild +/- Kurve) und
Video/Bildstapel."""
from __future__ import annotations

from datetime import datetime

from qtpy import QtCore, QtWidgets

from ..data import render_filename_template
from ..widgets import LocaleTolerantDoubleSpinBox
from ._base import _disable_enter_auto_accept, _NoEnterAutoAccept
from .filename_tokens import INDEX_TOKEN, render_export_filename, render_index_token, render_runtime_token, sanitize_filename_prefix
from .graph_selector import GraphContentSelector, _CursorCurveLink
from .panels import AxisOverridePanel, ColorScaleOverridePanel, _color_scale_range_invalid
from .scale_selector import ScaleContentSelector


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
        show_graph_source_choice: bool = False,
        live_available: bool = False,
        roi_entries: list[tuple[int, str]] | None = None,
        current_axis_state: dict | None = None,
        show_scale_choice: bool = False,
        ruler_available: bool = False,
        measurement_entries: list[tuple[int, str]] | None = None,
    ):
        super().__init__(parent)
        self.setWindowTitle("Grafik exportieren")
        # Nutzerwunsch: "gleiche Wahlmoeglichkeiten wie beim Video-Export ...
        # zur Not auch in die Breite gehen (vom Fenster her)" -- zweispaltiger
        # Aufbau (siehe layout_top unten) statt einer einzigen, stetig nach
        # unten wachsenden Spalte, analog zu VideoExportDialog.
        self.setMinimumWidth(760)
        self._settings = settings
        self._show_mode_choice = show_mode_choice

        layout = QtWidgets.QVBoxLayout(self)
        layout_top = QtWidgets.QHBoxLayout()
        left_col = QtWidgets.QVBoxLayout()
        right_col = QtWidgets.QVBoxLayout()

        # LINKE Spalte: welche Kurve(n) -- einzelne Messbereiche und/oder
        # Live-Cursor -- sowie an welcher Position relativ zum Thermobild.
        # Punkt 7 (Nutzerwunsch): "Position" und "Cursor-Position im Bild
        # anzeigen" gehoerten bisher optisch NICHT erkennbar zu "Graph-
        # Inhalt" (nur per Einrueckung angedeutet, aber ausserhalb von dessen
        # eigener Box) -- beides jetzt DIREKT in dieselbe Box gehaengt.
        self._content_selector: GraphContentSelector | None = None
        self.combo_graph_position = None
        self.chk_cursor_position = None
        if show_graph_source_choice:
            self._content_selector = GraphContentSelector(
                roi_entries or [], live_available, default_live_checked=False
            )
            content_box_layout = self._content_selector.group_box.layout()

            # Position relativ zum Thermobild -- dieselben vier Optionen wie
            # beim Video-/Bildstapel-Export (Nutzerwunsch: "gib mir die
            # gleichen Wahlmoeglichkeiten"), nur relevant, solange ueberhaupt
            # eine KOMBINIERTE Datei entsteht (siehe _update_graph_position_
            # enabled unten).
            position_form = QtWidgets.QFormLayout()
            self.combo_graph_position = QtWidgets.QComboBox()
            self.combo_graph_position.addItem("Unter dem Bild", "unten")
            self.combo_graph_position.addItem("Über dem Bild", "oben")
            self.combo_graph_position.addItem("Links vom Bild", "links")
            self.combo_graph_position.addItem("Rechts vom Bild", "rechts")
            self.combo_graph_position.setCurrentIndex(self.combo_graph_position.findData("rechts"))  # Standard
            position_form.addRow("Position:", self.combo_graph_position)
            content_box_layout.addLayout(position_form)

            self.chk_cursor_position = QtWidgets.QCheckBox("Cursor-Position im Bild anzeigen")
            self.chk_cursor_position.setChecked(False)
            self.chk_cursor_position.setToolTip(
                "Blendet das Fadenkreuz samt Temperaturanzeige am (fixierten oder\n"
                "zuletzt mit der Maus angezeigten) Cursor-Pixel im exportierten\n"
                "Thermobild mit ein. Unabhängig von der Live-Cursor-KURVE oben\n"
                "einzeln steuerbar -- die Kurve setzt diese Option aber voraus."
            )
            content_box_layout.addWidget(self.chk_cursor_position)
            self._cursor_curve_link = _CursorCurveLink(self.chk_cursor_position, self._content_selector.chk_live)
            left_col.addWidget(self._content_selector.group_box)
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
            left_col.addWidget(self.chk_cursor_position)

        # Punkt 12 (Nutzerwunsch): Maßstab-Linie und einzelne Messungen
        # optional mit ins exportierte Thermobild aufnehmen -- nur relevant,
        # wenn dieser Dialog ueberhaupt fuer einen Thermobild-Export genutzt
        # wird (show_scale_choice=False fuer den reinen Graphen-Einzelexport,
        # siehe MainWindow._export_single_graph).
        self._scale_selector: ScaleContentSelector | None = None
        if show_scale_choice:
            self._scale_selector = ScaleContentSelector(ruler_available, measurement_entries or [])
            left_col.addWidget(self._scale_selector.group_box)

        # Punkt 8 (Nutzerwunsch, "besseres Platzmanagement"): Farbskala/
        # Legende in die ERSTE Spalte -- dieselbe Freiheit wie in der UI:
        # Farbverlauf/Invertiert/Skalierung unabhängig von der aktuell
        # angezeigten Einstellung für GENAU diesen Export wählbar (Bugreport:
        # "gebe mir dieselbe Freiheit wie in der UI").
        self._color_panel = ColorScaleOverridePanel(
            colormaps or [], current_colormap_index, current_invert,
            current_level_mode, current_min, current_max,
        )
        left_col.addWidget(self._color_panel.group_box)
        left_col.addStretch(1)
        layout_top.addLayout(left_col, 1)

        # RECHTE Spalte: Ausgabe (DPI, Dateien kombiniert/getrennt), Achsen,
        # Zeitachse -- alle uebrigen, von der Kurven-Auswahl unabhaengigen
        # Export-Einstellungen.
        # Punkt 7 (Nutzerwunsch): "Auflösung (DPI)" stand bisher ohne jede
        # Überschrift/Gruppierung ganz oben -- jetzt Teil derselben "Ausgabe"-
        # Box wie die Dateien-Auswahl (beides betrifft die AUSGABEDATEI(EN)).
        output_box = QtWidgets.QGroupBox("Ausgabe")
        output_layout = QtWidgets.QVBoxLayout(output_box)
        dpi_form = QtWidgets.QFormLayout()
        self.spin_dpi = QtWidgets.QSpinBox()
        self.spin_dpi.setRange(50, 1200)
        self.spin_dpi.setSingleStep(10)
        self.spin_dpi.setValue(default_dpi)
        dpi_form.addRow("Auflösung (DPI):", self.spin_dpi)
        output_layout.addLayout(dpi_form)

        self.chk_combined = None
        self.chk_separate = None
        if show_mode_choice:
            # Punkt 9 (Nutzerwunsch): Checkboxen statt sich gegenseitig
            # ausschliessender Radio-Buttons -- beide gleichzeitig angehakt
            # exportiert in EINEM Durchgang sowohl die kombinierte Grafik ALS
            # AUCH die zwei Einzeldateien (siehe _export_combined_image).
            self.chk_combined = QtWidgets.QCheckBox("Kombiniert (ein Bild: Thermobild + Kurve)")
            self.chk_separate = QtWidgets.QCheckBox("Getrennt (zwei Dateien: Bild und Kurve einzeln)")
            output_layout.addWidget(self.chk_combined)
            output_layout.addWidget(self.chk_separate)

            self.chk_combined.setChecked(bool(settings.value("export/combined_images", True, type=bool)))
            self.chk_separate.setChecked(bool(settings.value("export/separate_images", False, type=bool)))

            # Die Position relativ zum Bild ergibt nur einen Sinn, solange
            # ueberhaupt eine KOMBINIERTE Datei entsteht -- bei "nur Getrennt"
            # landen Bild und Kurve ohnehin in zwei unabhaengigen Dateien.
            if self.combo_graph_position is not None:
                self.chk_combined.toggled.connect(self._update_graph_position_enabled)
                self._update_graph_position_enabled()
        right_col.addWidget(output_box)

        # Nur anbieten, wenn dieser Export ueberhaupt einen Kurven-Graphen
        # enthaelt (current_axis_state wird von MainWindow nur dann
        # mitgegeben) -- fuer den reinen Thermobild-Einzelexport ergeben
        # Achsen-Einstellungen keinen Sinn.
        self._axis_panel: AxisOverridePanel | None = None
        if current_axis_state is not None:
            self._axis_panel = AxisOverridePanel(self, current_axis_state)
            right_col.addWidget(self._axis_panel.group_box)

        # Punkt (Folgeanfrage): "Zeitachse" stand bisher als eigene, lose
        # Zeile UNTERHALB der "Achsen"-Box statt sichtbar dazuzugehoeren,
        # obwohl beides dieselbe x-Achse des Kurven-Graphen betrifft --
        # jetzt direkt IN die "Achsen"-Box gehaengt (falls vorhanden).
        # Vorbelegung bewusst IMMER "Beide" (statt der gerade in der App
        # aktiven Uhrzeit/Laufzeit-Anzeige): Exporte profitieren von beiden
        # Achsen gleichzeitig, unabhaengig davon, was man sich waehrenddessen
        # gerade im Hauptfenster anschaut.
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
                "Graphen)."
            )
            self.combo_time_axis.setCurrentIndex(self.combo_time_axis.findData("both"))
            time_form.addRow("Zeitachse:", self.combo_time_axis)
            if self._axis_panel is not None:
                self._axis_panel.group_box.layout().addLayout(time_form)
            else:
                right_col.addLayout(time_form)
        right_col.addStretch(1)
        layout_top.addLayout(right_col, 1)
        layout.addLayout(layout_top)

        buttons = QtWidgets.QDialogButtonBox(
            QtWidgets.QDialogButtonBox.StandardButton.Ok | QtWidgets.QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self._on_accept)
        buttons.rejected.connect(self.reject)
        _disable_enter_auto_accept(buttons)
        layout.addWidget(buttons)

    def _update_graph_position_enabled(self) -> None:
        self.combo_graph_position.setEnabled(self.chk_combined.isChecked())

    def _on_accept(self) -> None:
        if self.chk_combined is not None and self.chk_separate is not None:
            if not self.chk_combined.isChecked() and not self.chk_separate.isChecked():
                QtWidgets.QMessageBox.information(
                    self, "Keine Auswahl",
                    "Bitte mindestens „Kombiniert“ oder „Getrennt“ auswählen."
                )
                return
        if self._content_selector is not None and not self._content_selector.has_any_selected():
            QtWidgets.QMessageBox.information(
                self, "Keine Auswahl",
                "Bitte mindestens einen Messbereich und/oder Live-Cursor auswählen."
            )
            return
        if self._color_panel.range_invalid():
            QtWidgets.QMessageBox.warning(
                self, "Ungültiger Bereich", "Bei der Farbskala muss „Max“ größer als „Min“ sein."
            )
            return
        if self._axis_panel is not None and self._axis_panel.incomplete():
            QtWidgets.QMessageBox.information(
                self, "Achsen nicht eingestellt",
                "Bitte auf „Einstellen…“ klicken, um eigene Achsen-Einstellungen festzulegen -- "
                "oder „Aktuelle Ansicht übernehmen“ wählen."
            )
            return
        self.accept()

    def dpi(self) -> int:
        return self.spin_dpi.value()

    def separate(self) -> bool:
        if self.chk_separate is None:
            return False
        value = self.chk_separate.isChecked()
        self._settings.setValue("export/separate_images", value)
        return value

    def combined(self) -> bool:
        """True, wenn (zusaetzlich zu/statt separate()) die kombinierte
        Grafik (ein Bild: Thermobild + Kurve) erzeugt werden soll -- ohne
        show_mode_choice (kein Graph in diesem Export) ist es immer die
        einzige erzeugte Datei."""
        if self.chk_combined is None:
            return True
        value = self.chk_combined.isChecked()
        self._settings.setValue("export/combined_images", value)
        return value

    def included_roi_numbers(self) -> set[int]:
        """Nummern (RoiEntry.number, eindeutig -- siehe GraphContentSelector)
        der ausgewählten Messbereiche -- leere Menge, falls der Dialog gar
        keine Graph-Inhalt-Auswahl anbietet (show_graph_source_choice=False)."""
        if self._content_selector is None:
            return set()
        return self._content_selector.included_numbers()

    def include_live(self) -> bool:
        return self._content_selector is not None and self._content_selector.include_live()

    def include_scale_ruler(self) -> bool:
        """Punkt 12: Maßstab-Linie mit ins exportierte Thermobild aufnehmen --
        False, falls der Dialog keine Maßstab/Messungs-Auswahl anbietet."""
        return self._scale_selector is not None and self._scale_selector.include_ruler()

    def included_scale_measurement_numbers(self) -> set[int]:
        """Nummern (MeasurementEntry.number) der Messungen, die mit ins
        exportierte Thermobild aufgenommen werden sollen (Punkt 12)."""
        if self._scale_selector is None:
            return set()
        return self._scale_selector.included_measurement_numbers()

    def graph_position(self) -> str:
        """"unten"/"oben"/"links"/"rechts" -- wo der Kurven-Graph relativ zum
        Thermobild platziert wird (Standard: "rechts"), dieselbe Konvention
        wie VideoExportDialog.graph_position(). "unten" als Vorbelegung,
        falls der Dialog gar keine Positions-Auswahl anbietet (kein Graph in
        diesem Export, show_graph_source_choice=False)."""
        if self.combo_graph_position is None:
            return "unten"
        return self.combo_graph_position.currentData()

    def has_graph_content(self) -> bool:
        """Ob ueberhaupt ein Graph exportiert werden soll -- False nur, wenn
        show_graph_source_choice=False war (kein Graph in diesem Export)."""
        return self._content_selector is not None

    def export_cursor_position(self) -> bool:
        return self.chk_cursor_position is not None and self.chk_cursor_position.isChecked()

    def use_custom_colors(self) -> bool:
        return self._color_panel.use_custom()

    def custom_colormap_index(self) -> int:
        return self._color_panel.colormap_index()

    def custom_invert(self) -> bool:
        return self._color_panel.invert()

    def custom_level_mode(self) -> str:
        return self._color_panel.level_mode()

    def custom_min_max(self) -> tuple[float, float]:
        return self._color_panel.min_max()

    def use_custom_axes(self) -> bool:
        return self._axis_panel is not None and self._axis_panel.use_custom()

    def custom_axis_overrides(self) -> dict | None:
        return self._axis_panel.custom_overrides() if self._axis_panel is not None else None

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
        sample_timestamp: datetime | None = None,
        timestamps: list[datetime] | None = None,
        current_axis_state: dict | None = None,
        settings: QtCore.QSettings | None = None,
        ruler_available: bool = False,
        measurement_entries: list[tuple[int, str]] | None = None,
    ):
        super().__init__(parent)
        self.setWindowTitle("Video / Bildstapel exportieren")
        self.setMinimumWidth(720)
        # Wie CsvColumnDialog/GraphicExportDialog fuer den LAUF-Einheit-
        # Combo (Punkt 6) -- Fallback auf dieselbe Org/App-Kennung wie
        # MainWindow._settings, falls ein Aufrufer (z.B. ein Test) keine
        # eigene QSettings-Instanz durchreicht.
        self._settings = settings if settings is not None else QtCore.QSettings("ThermalViewer", "ThermalViewer")
        self._sample_timestamp = sample_timestamp
        # Nur fuer die LIVE-Warnung bei nicht eindeutigem Dateiname-Muster
        # (siehe _update_filename_preview) -- die tatsaechlich verwendeten
        # Zeitstempel koennen beim echten Export noch abweichen (siehe
        # MainWindow._resolve_export_timestamps, Ersatz-Zeitplan ohne echte
        # Datei-Zeitstempel), die massgebliche Pruefung erfolgt daher ohnehin
        # dort nochmal.
        self._timestamps = timestamps

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
        # Standard bewusst MIT "IDX" (nicht nur "Frame_"): ohne automatisch
        # angehaengten Zaehler (Punkt 2, "volle Kontrolle ueber den Namen")
        # waere ein bloss "Frame_" lautender Praefix ab dem allerersten
        # Bildstapel-Export sofort mehrdeutig -- der Standard soll ohne
        # jede Anpassung bereits eindeutige Dateinamen ergeben.
        self.edit_image_prefix = QtWidgets.QLineEdit(f"Frame_{INDEX_TOKEN}_")
        self.edit_image_prefix.setToolTip(
            "Voller Dateiname (ohne Endung) für jedes exportierte Bild -- volle Kontrolle, es wird "
            "NICHTS automatisch angehängt. Unterstützt dieselben Zeitstempel-Platzhalter wie das "
            "Namensschema beim Laden (YYYY/MM/DD/hh/mm/ss), die mit dem echten Zeitstempel jedes "
            "Frames gefüllt werden, sowie „IDX“ für die fortlaufende, nullgefüllte Frame-Nummer -- "
            "GENAU an der Stelle, wo „IDX“ im Muster steht, z.B. „Frame_IDX_YYYY-MM-DD_hh-mm-ss“ -> "
            "Frame_1_2026-01-01_12-00-00.png. Zusätzlich „LAUFs“/„LAUFm“/„LAUFh“ für die verstrichene "
            "Aufnahmezeit in Sekunden/Minuten/Stunden (der Buchstabe nach „LAUF“ wählt die Einheit), "
            "z.B. „Frame_IDX_LAUFm“ -> Frame_1_000min.png -- praktisch, um Frames anhand der Laufzeit "
            "statt der Bildnummer zu benennen. Ergibt das Muster (z.B. weil es weder „IDX“ noch einen "
            "vollen Zeitstempel enthält) für mehrere Frames denselben Namen, erscheint beim Export "
            "eine Warnung -- spätere Frames würden sonst frühere überschreiben („LAUF...“ allein "
            "reicht dafür NICHT aus, da mehrere Frames dieselbe gerundete Sekunde/Minute/Stunde "
            "teilen können)."
        )
        image_form.addRow("Bildformat:", self.combo_image_format)
        image_form.addRow("Dateiname-Muster:", self.edit_image_prefix)
        self.lbl_filename_preview = QtWidgets.QLabel()
        image_form.addRow("Beispiel:", self.lbl_filename_preview)
        self.lbl_filename_warning = QtWidgets.QLabel(
            "⚠ Dieses Muster ergibt für mehrere Frames im gewählten Bereich denselben Dateinamen -- "
            "spätere Frames würden frühere überschreiben. „IDX“ ins Muster aufnehmen, um eine "
            "fortlaufende Nummer einzufügen."
        )
        self.lbl_filename_warning.setWordWrap(True)
        self.lbl_filename_warning.setStyleSheet("color:#b45309; font-weight:600;")
        self.lbl_filename_warning.setVisible(False)
        image_form.addRow("", self.lbl_filename_warning)
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

        self.radio_output_video.toggled.connect(self._update_output_mode_enabled)
        self.radio_output_images.toggled.connect(self._update_output_mode_enabled)
        self._update_output_mode_enabled()

        self.edit_image_prefix.textChanged.connect(self._update_filename_preview)
        self.combo_image_format.currentIndexChanged.connect(self._update_filename_preview)
        self.spin_start.valueChanged.connect(self._update_filename_preview)
        self.spin_end.valueChanged.connect(self._update_filename_preview)
        self._update_filename_preview()

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

        self.chk_legend.toggled.connect(self._update_legend_options_enabled)
        self.radio_current_settings.toggled.connect(self._update_legend_options_enabled)
        self.radio_custom_settings.toggled.connect(self._update_legend_options_enabled)
        self._update_legend_options_enabled()

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

        self._content_selector = GraphContentSelector(
            roi_entries or [], live_available, default_live_checked=False
        )
        graph_indent_col.addWidget(self._content_selector.group_box)

        self.combo_graph_position = QtWidgets.QComboBox()
        self.combo_graph_position.addItem("Unter dem Bild", "unten")
        self.combo_graph_position.addItem("Über dem Bild", "oben")
        self.combo_graph_position.addItem("Links vom Bild", "links")
        self.combo_graph_position.addItem("Rechts vom Bild", "rechts")
        self.combo_graph_position.setCurrentIndex(self.combo_graph_position.findData("rechts"))  # Standard
        position_form = QtWidgets.QFormLayout()
        position_form.addRow("Position:", self.combo_graph_position)
        graph_indent_col.addLayout(position_form)

        # Nur anbieten, wenn ueberhaupt ein Graph exportiert werden kann
        # (current_axis_state wird von MainWindow nur dann mitgegeben) --
        # Nutzerwunsch: "mehr Gestaltungsmoeglichkeiten ... Achsen-Labels/
        # Ticklabels/Schrittweite" auch beim Video-/Bildstapel-Export.
        self._axis_panel: AxisOverridePanel | None = None
        if current_axis_state is not None:
            self._axis_panel = AxisOverridePanel(self, current_axis_state)
            graph_indent_col.addWidget(self._axis_panel.group_box)

        graph_indent_row.addLayout(graph_indent_col)
        graph_layout.addLayout(graph_indent_row)

        self.chk_show_graph.toggled.connect(self._update_graph_export_enabled)
        self._update_graph_export_enabled(False)
        row2.addWidget(graph_box, 1)
        layout.addLayout(row2)

        # Eigener, vom Graphen UNABHAENGIGER Kasten fuer alles, was zusaetzlich
        # DIREKT AUF DEM THERMOBILD selbst eingeblendet wird -- Cursor
        # (Fadenkreuz + Live-Temperatur-Text) UND Maßstab/Messungen
        # (Folgeanfrage: "Cursor im Bild" und "Maßstab & Messungen im Export"
        # bitte unter einem einzigen Bereich zusammenfassen -- beides betrifft
        # ja dieselbe Sache: zusaetzliche Overlays auf dem Bild). Bewusst
        # getrennt von "Temperaturverlauf-Graph" (siehe Kommentar oben) und
        # daher auch nicht an "Graph mit exportieren" gekoppelt. Zusammen mit
        # "Zeitanzeige im Bild" in einer Zeile, da beides zusaetzliche
        # Einblendungen DIREKT AUF DEM BILD sind (im Unterschied zum
        # Graphen-Inhalt/-Position oben).
        cursor_box = QtWidgets.QGroupBox("Cursor & Maßstab im Bild")
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
        self._cursor_curve_link = _CursorCurveLink(self.chk_cursor_position, self._content_selector.chk_live)

        # Punkt 12 (Nutzerwunsch): Maßstab-Linie und einzelne Messungen
        # optional mit ins exportierte Video/Bildstapel aufnehmen -- jetzt
        # direkt IN diese Box gehaengt (host_box), statt in einer eigenen,
        # separaten Box weiter unten zu stehen (siehe ScaleContentSelector).
        cursor_layout.addSpacing(6)
        self._scale_selector = ScaleContentSelector(ruler_available, measurement_entries or [], host_box=cursor_box)

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

    def _update_output_mode_enabled(self) -> None:
        is_video = self.radio_output_video.isChecked()
        self.spin_fps.setEnabled(is_video)
        self.combo_image_format.setEnabled(not is_video)
        self.edit_image_prefix.setEnabled(not is_video)
        self.lbl_filename_preview.setEnabled(not is_video)
        self.lbl_filename_warning.setEnabled(not is_video)

    def _update_filename_preview(self) -> None:
        # sanitize_filename_prefix() zuerst (entfernt unter Windows/macOS/
        # Linux ungueltige Zeichen), render_filename_template() DANACH --
        # Zeitstempel-Platzhalter (YMDhms-Buchstaben) enthalten keines der
        # ungueltigen Zeichen, die Reihenfolge veraendert das Ergebnis
        # also nicht. Nur EIN Beispiel (statt zuvor zwei): zeigt den
        # tatsaechlichen, mit einem echten Zeitstempel gefuellten
        # Dateinamen (Nutzerwunsch), zwei nahezu identische Beispiele
        # brachten keinen Zusatznutzen.
        raw_prefix = sanitize_filename_prefix(self.edit_image_prefix.text(), fallback="Frame_")
        ext = self.combo_image_format.currentData() or ".png"
        count = max(1, self.spin_end.value() - self.spin_start.value() + 1)
        digits = len(str(count))
        has_index_token = INDEX_TOKEN in raw_prefix

        preview_prefix = raw_prefix
        if self._sample_timestamp is not None:
            preview_prefix = render_filename_template(preview_prefix, self._sample_timestamp)
        preview_prefix, _ = render_index_token(preview_prefix, 1, digits)
        # Vorschau-Beispiel zeigt Frame 1 -> Laufzeit 0 als LAUF-Beispielwert.
        preview_prefix, _ = render_runtime_token(preview_prefix, 0.0)
        # KEIN automatisch angehaengtes Suffix mehr (Nutzerwunsch: volle
        # Kontrolle ueber den Dateinamen) -- ohne "IDX" bleibt das Muster
        # fuer jeden Frame exakt so, wie eingegeben; ob das eindeutig ist,
        # prueft die Warnung unten bzw. verbindlich MainWindow._export_video.
        self.lbl_filename_preview.setText(f"{preview_prefix}{ext}")

        warn = False
        if not has_index_token and self._timestamps:
            start = self.spin_start.value() - 1
            end = self.spin_end.value() - 1
            window = self._timestamps[start : end + 1]
            if len(window) > 1:
                t0 = self._timestamps[0]
                # Ueber denselben gemeinsamen Renderer wie der echte Export
                # (render_export_filename) pruefen -- LAUF allein macht
                # ein Muster NICHT eindeutig (mehrere Frames koennen
                # dieselbe gerundete Minute/Stunde teilen), das faengt
                # dieser Vergleich automatisch mit ab.
                rendered = {
                    render_export_filename(raw_prefix, ts, (ts - t0).total_seconds(), i, digits)
                    for i, ts in enumerate(window, start=1)
                }
                warn = len(rendered) < len(window)
        self.lbl_filename_warning.setVisible(warn)

    def _update_legend_options_enabled(self) -> None:
        enabled = self.chk_legend.isChecked()
        self.radio_current_settings.setEnabled(enabled)
        self.radio_custom_settings.setEnabled(enabled)
        custom_enabled = enabled and self.radio_custom_settings.isChecked()
        for w in (self.combo_cmap, self.chk_invert, self.combo_level_mode, self.spin_min, self.spin_max):
            w.setEnabled(custom_enabled)

    def _update_graph_export_enabled(self, checked: bool) -> None:
        self._content_selector.group_box.setEnabled(checked)
        self.combo_graph_position.setEnabled(checked)
        if self._axis_panel is not None:
            self._axis_panel.group_box.setEnabled(checked)

    def _on_accept(self) -> None:
        if self.spin_end.value() < self.spin_start.value():
            QtWidgets.QMessageBox.warning(
                self, "Ungültiger Bereich", "Der End-Frame muss größer oder gleich dem Start-Frame sein."
            )
            return
        if self.chk_legend.isChecked() and _color_scale_range_invalid(
            self.radio_custom_settings, self.combo_level_mode, self.spin_min, self.spin_max
        ):
            QtWidgets.QMessageBox.warning(
                self, "Ungültiger Bereich", "Bei der Farbskala muss „Max“ größer als „Min“ sein."
            )
            return
        if self.chk_show_graph.isChecked() and not self._content_selector.has_any_selected():
            QtWidgets.QMessageBox.information(
                self, "Keine Auswahl",
                "Bitte mindestens einen Messbereich und/oder Live-Cursor für den Graphen auswählen."
            )
            return
        if (
            self.chk_show_graph.isChecked()
            and self._axis_panel is not None
            and self._axis_panel.incomplete()
        ):
            QtWidgets.QMessageBox.information(
                self, "Achsen nicht eingestellt",
                "Bitte auf „Einstellen…“ klicken, um eigene Achsen-Einstellungen festzulegen -- "
                "oder „Aktuelle Ansicht übernehmen“ wählen."
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

    def use_custom_axes(self) -> bool:
        return self._axis_panel is not None and self._axis_panel.use_custom()

    def custom_axis_overrides(self) -> dict | None:
        return self._axis_panel.custom_overrides() if self._axis_panel is not None else None

    def show_graph(self) -> bool:
        return self.chk_show_graph.isChecked()

    def included_roi_numbers(self) -> set[int]:
        return self._content_selector.included_numbers()

    def include_live(self) -> bool:
        return self._content_selector.include_live()

    def include_scale_ruler(self) -> bool:
        """Punkt 12: Maßstab-Linie mit ins exportierte Video/Bildstapel
        aufnehmen."""
        return self._scale_selector.include_ruler()

    def included_scale_measurement_numbers(self) -> set[int]:
        """Nummern (MeasurementEntry.number) der Messungen, die mit ins
        exportierte Video/Bildstapel aufgenommen werden sollen (Punkt 12)."""
        return self._scale_selector.included_measurement_numbers()

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
