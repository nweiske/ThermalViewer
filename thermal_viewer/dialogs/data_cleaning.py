"""Nicht-modaler Dialog zur Rohdaten-Bereinigung (Ausreißer-Bilder anhand
frei markierter Referenzpunkte erkennen/ausblenden -- siehe
main_window/data_cleaning_ops.py für die eigentliche Logik).

Bewusst NICHT-MODAL (anders als die uebrigen Dialoge dieses Pakets): der
Nutzer muss waehrend dieser Dialog offen ist weiterhin auf das Thermobild
klicken koennen, um Referenzpunkte zu setzen -- ein modaler Dialog wuerde
das Hauptfenster dafuer blockieren. Haelt deshalb (anders als z.B.
RulerLengthDialog) keinen eigenen Ergebniszustand vor, der erst bei
exec()/accept() ausgelesen wird, sondern ruft bei jeder Aktion direkt auf
MainWindow zurueck (self._mw) und wird von dort per refresh_*() wieder mit
dem aktuellen Zustand synchronisiert."""
from __future__ import annotations

from qtpy import QtCore, QtWidgets

from ..widgets import LocaleTolerantDoubleSpinBox
from ._base import _disable_enter_auto_accept, _NoEnterAutoAccept


class DataCleaningDialog(_NoEnterAutoAccept, QtWidgets.QDialog):
    def __init__(self, main_window) -> None:
        super().__init__(main_window)
        self._mw = main_window
        self.setWindowTitle("Rohdaten säubern")
        self.setWindowModality(QtCore.Qt.WindowModality.NonModal)
        self.setMinimumWidth(480)

        layout = QtWidgets.QVBoxLayout(self)

        intro = QtWidgets.QLabel(
            "Erkennt einzelne Ausreißer-Bilder (z.B. durch eine kurze Kamera-/Übertragungsstörung) "
            "anhand der Temperaturänderung zum jeweils VORHERIGEN Bild an frei markierten "
            "Referenzpunkten (wahlweise über eine kleine Fläche um jeden Punkt gemittelt, siehe "
            "unten) -- ausgeblendete Bilder bleiben erhalten (samt Zeitstempel) und lassen sich "
            "hier jederzeit wieder einzeln einblenden."
        )
        intro.setWordWrap(True)
        layout.addWidget(intro)

        points_box = QtWidgets.QGroupBox("Referenzpunkte im Bild")
        points_layout = QtWidgets.QVBoxLayout(points_box)
        self.btn_add_point = QtWidgets.QPushButton("Punkt hinzufügen (Bild anklicken)")
        self.btn_add_point.setCheckable(True)
        self.btn_add_point.toggled.connect(self._on_add_point_toggled)
        points_layout.addWidget(self.btn_add_point)
        self.points_list = QtWidgets.QListWidget()
        self.points_list.setMaximumHeight(90)
        # Punkt 6 (Nutzerwunsch): Auswahl in der Liste hebt genau diesen
        # Punkt im Bild hervor (alle anderen treten zurueck) -- erleichtert
        # das Zuordnen bei mehreren nah beieinander liegenden Punkten.
        self.points_list.currentRowChanged.connect(self._on_point_selection_changed)
        points_layout.addWidget(self.points_list)
        self.btn_remove_point = QtWidgets.QPushButton("Ausgewählten Punkt entfernen")
        self.btn_remove_point.clicked.connect(self._on_remove_point_clicked)
        points_layout.addWidget(self.btn_remove_point)

        # Mittelungsbereich (Nutzerwunsch, auf Rueckfrage bestaetigt): jeder
        # Punkt kann statt eines reinen Einzelpixels ueber eine kleine NxN-
        # Flaeche gemittelt werden (robuster gegen Sensor-Rauschen an genau
        # einem Pixel) -- EIGENSTAENDIGE Einstellung, unabhaengig von der
        # Live-Cursor-Bereichsgröße (Werkzeuge-Menü), da unterschiedliche
        # Zwecke. self._kernel_sizes/self.combo_kernel siehe
        # _on_kernel_size_changed/sync_kernel_size_combo.
        kernel_form = QtWidgets.QFormLayout()
        self._kernel_sizes = [1, 3, 5, 7, 9]
        self.combo_kernel = QtWidgets.QComboBox()
        for size in self._kernel_sizes:
            label = "1×1 Pixel (kein Mittelwert)" if size == 1 else f"{size}×{size} Pixel (gemittelt)"
            self.combo_kernel.addItem(label, size)
        self.sync_kernel_size_combo()
        self.combo_kernel.currentIndexChanged.connect(self._on_kernel_size_changed)
        kernel_form.addRow("Mittelungsbereich je Punkt:", self.combo_kernel)
        points_layout.addLayout(kernel_form)

        self.chk_show_kernel_area = QtWidgets.QCheckBox("Bereich im Bild anzeigen")
        self.chk_show_kernel_area.setChecked(main_window._cleaning_show_kernel_area)
        self.chk_show_kernel_area.toggled.connect(self._on_show_kernel_area_toggled)
        points_layout.addWidget(self.chk_show_kernel_area)

        layout.addWidget(points_box)

        threshold_form = QtWidgets.QFormLayout()
        self.spin_threshold = LocaleTolerantDoubleSpinBox()
        self.spin_threshold.setRange(0.1, 1000.0)
        self.spin_threshold.setDecimals(1)
        self.spin_threshold.setSuffix(" °C")
        self.spin_threshold.setValue(main_window._cleaning_threshold)
        self.spin_threshold.valueChanged.connect(self._on_threshold_changed)
        threshold_form.addRow("Schwellenwert (dT zum Vorbild):", self.spin_threshold)
        layout.addLayout(threshold_form)

        # Verknuepfungslogik mehrerer Referenzpunkte (Nutzerwunsch): bisher
        # war nur "UND" (an jedem Punkt) moeglich -- "ODER" (an mindestens
        # einem Punkt) ist z.B. sinnvoll, wenn Stoerungen typischerweise nur
        # lokal an EINER Stelle im Bild auftreten statt gleichzeitig ueberall.
        logic_row = QtWidgets.QHBoxLayout()
        logic_row.addWidget(QtWidgets.QLabel("Verknüpfung mehrerer Punkte:"))
        self.radio_logic_and = QtWidgets.QRadioButton("Alle Punkte (UND)")
        self.radio_logic_or = QtWidgets.QRadioButton("Mindestens ein Punkt (ODER)")
        self._logic_group = QtWidgets.QButtonGroup(self)
        self._logic_group.addButton(self.radio_logic_and)
        self._logic_group.addButton(self.radio_logic_or)
        if main_window._cleaning_logic == "or":
            self.radio_logic_or.setChecked(True)
        else:
            self.radio_logic_and.setChecked(True)
        self.radio_logic_and.toggled.connect(self._on_logic_changed)
        logic_row.addWidget(self.radio_logic_and)
        logic_row.addWidget(self.radio_logic_or)
        logic_row.addStretch(1)
        layout.addLayout(logic_row)

        logic_hint = QtWidgets.QLabel(
            "Ein Bild gilt als Ausreißer, wenn AN JEDEM (UND) bzw. AN MINDESTENS EINEM (ODER) "
            "markierten Referenzpunkt (bzw. dessen gemitteltem Bereich) die Temperaturänderung "
            "zum vorherigen Bild den Schwellenwert überschreitet."
        )
        logic_hint.setWordWrap(True)
        logic_hint.setStyleSheet("color:#6b7280;")
        layout.addWidget(logic_hint)

        self.lbl_summary = QtWidgets.QLabel()
        layout.addWidget(self.lbl_summary)

        # Manuelles Ausblenden (Nutzerwunsch, Punkt 5): ein beliebiges Bild
        # -- unabhaengig von Referenzpunkten/Schwellenwert -- direkt per
        # Bildnummer ausblenden koennen. Wieder-Einblenden geschieht ueber
        # die Checkbox-Liste unten (jedes ausgeblendete Bild erscheint dort).
        manual_row = QtWidgets.QHBoxLayout()
        manual_row.addWidget(QtWidgets.QLabel("Bild-Nr. manuell ausblenden:"))
        self.spin_manual_frame = QtWidgets.QSpinBox()
        self.spin_manual_frame.setMinimum(1)
        self.spin_manual_frame.setMaximum(max(1, main_window.recording.n_frames if main_window.recording else 1))
        manual_row.addWidget(self.spin_manual_frame)
        self.btn_manual_exclude = QtWidgets.QPushButton("Bild ausblenden")
        self.btn_manual_exclude.clicked.connect(self._on_manual_exclude_clicked)
        manual_row.addWidget(self.btn_manual_exclude)
        manual_row.addStretch(1)
        layout.addLayout(manual_row)

        self.candidates_area = QtWidgets.QScrollArea()
        self.candidates_area.setWidgetResizable(True)
        self.candidates_area.setMinimumHeight(180)
        self._candidates_container = QtWidgets.QWidget()
        self._candidates_layout = QtWidgets.QVBoxLayout(self._candidates_container)
        self._candidates_layout.addStretch(1)
        self.candidates_area.setWidget(self._candidates_container)
        layout.addWidget(self.candidates_area, 1)
        self._candidate_checks: dict[int, QtWidgets.QCheckBox] = {}

        # Kein separater "Anwenden"-Knopf mehr (Nutzerwunsch, Punkt 7): jede
        # Checkbox unten wirkt SOFORT auf die Auswertung/den Viewer, siehe
        # _on_candidate_checkbox_toggled.
        buttons = QtWidgets.QDialogButtonBox()
        self.btn_close = buttons.addButton("Schließen", QtWidgets.QDialogButtonBox.ButtonRole.RejectRole)
        self.btn_close.clicked.connect(self.close)
        _disable_enter_auto_accept(buttons)
        layout.addWidget(buttons)

    # ------------------------------------------------------------- Punkte
    def _on_add_point_toggled(self, checked: bool) -> None:
        if checked:
            self._mw._start_cleaning_point_pick()
            self.btn_add_point.setText("Jetzt ins Bild klicken…")
        else:
            self._mw._cancel_cleaning_point_pick()
            self.btn_add_point.setText("Punkt hinzufügen (Bild anklicken)")

    def _on_remove_point_clicked(self) -> None:
        row = self.points_list.currentRow()
        if row < 0:
            return
        self._mw._remove_cleaning_point(row)

    def _on_point_selection_changed(self, row: int) -> None:
        self._mw._apply_cleaning_point_focus_visuals(row if row >= 0 else None)

    # -------------------------------------------------- Mittelungsbereich
    def sync_kernel_size_combo(self) -> None:
        """Stellt die Combobox auf den aktuellen _mw._cleaning_kernel_size
        ein, OHNE currentIndexChanged auszuloesen -- fuer die Erstbefuellung
        beim Aufbau UND fuer das Nachziehen nach "Projekt laden…" (siehe
        project_io.py)."""
        self.combo_kernel.blockSignals(True)
        size = self._mw._cleaning_kernel_size
        if size in self._kernel_sizes:
            self.combo_kernel.setCurrentIndex(self._kernel_sizes.index(size))
        self.combo_kernel.blockSignals(False)

    def _on_kernel_size_changed(self, index: int) -> None:
        self._mw._cleaning_kernel_size = self.combo_kernel.itemData(index)
        self._mw._draw_cleaning_point_markers()
        self.refresh_candidates()

    def _on_show_kernel_area_toggled(self, checked: bool) -> None:
        self._mw._cleaning_show_kernel_area = checked
        self._mw._draw_cleaning_point_markers()

    # ------------------------------------------------------------- Logik
    def sync_logic_radios(self) -> None:
        """Analog zu sync_kernel_size_combo() -- fuer das Nachziehen nach
        "Projekt laden…" (siehe project_io.py)."""
        self.radio_logic_and.blockSignals(True)
        self.radio_logic_or.blockSignals(True)
        if self._mw._cleaning_logic == "or":
            self.radio_logic_or.setChecked(True)
        else:
            self.radio_logic_and.setChecked(True)
        self.radio_logic_and.blockSignals(False)
        self.radio_logic_or.blockSignals(False)

    def _on_logic_changed(self, _checked: bool) -> None:
        self._mw._cleaning_logic = "and" if self.radio_logic_and.isChecked() else "or"
        self.refresh_candidates()

    def point_added(self) -> None:
        """Von MainWindow._handle_cleaning_point_click aufgerufen, nachdem ein
        Punkt tatsaechlich gesetzt wurde -- der Knopf bleibt bewusst NICHT
        dauerhaft aktiv (ein Klick = ein Punkt, wie beim Maßstab-Werkzeug),
        damit ein normaler Klick ins Bild danach wieder die Live-Cursor-
        Anzeige steuert statt versehentlich einen weiteren Punkt zu setzen."""
        self.btn_add_point.blockSignals(True)
        self.btn_add_point.setChecked(False)
        self.btn_add_point.blockSignals(False)
        self.btn_add_point.setText("Punkt hinzufügen (Bild anklicken)")

    def refresh_points(self) -> None:
        self.points_list.clear()
        for i, (x, y) in enumerate(self._mw._cleaning_points, start=1):
            self.points_list.addItem(f"Punkt {i}: (x={x:.1f}, y={y:.1f})")
        # Frame-Anzahl kann sich seit dem Aufbau des Dialogs geaendert haben
        # (z.B. neue Aufnahme geladen, waehrend der Dialog noch offen war).
        self.spin_manual_frame.setMaximum(max(1, self._mw.recording.n_frames if self._mw.recording else 1))
        self.refresh_candidates()

    # ---------------------------------------------------------- Schwelle
    def _on_threshold_changed(self, value: float) -> None:
        self._mw._cleaning_threshold = value
        self.refresh_candidates()

    # --------------------------------------------------------- Vorschau
    def refresh_candidates(self) -> None:
        """Baut die Checkbox-Liste neu auf: die VEREINIGUNG aus bereits
        ausgeschlossenen Bildern (koennen hier wieder eingeblendet werden)
        und gerade NEU als Ausreißer erkannten Bildern (mit den aktuellen
        Punkten/Schwellenwert) -- jede Checkbox bedeutet "ausblenden",
        angehakt ist der jeweilige Standardzustand. Jede Checkbox wirkt
        SOFORT (siehe _on_candidate_checkbox_toggled), kein separater
        "Anwenden"-Knopf mehr (Nutzerwunsch, Punkt 7)."""
        while self._candidate_checks:
            _, chk = self._candidate_checks.popitem()
            chk.setParent(None)
            chk.deleteLater()

        newly_flagged = self._mw._compute_cleaning_candidates() if self._mw._cleaning_points else set()
        union = sorted(newly_flagged | self._mw._excluded_frame_indices)

        self.lbl_summary.setText(
            f"{len(newly_flagged)} von {self._mw.recording.n_frames if self._mw.recording else 0} "
            f"Bild(ern) neu als Ausreißer erkannt -- {len(union)} insgesamt in der Liste unten "
            "(inkl. bereits zuvor ausgeblendeter)."
            if self._mw._cleaning_points or self._mw._excluded_frame_indices
            else "Noch keine Referenzpunkte markiert."
        )

        timestamps = self._mw.recording.timestamps if self._mw.recording else []
        for idx in union:
            ts = timestamps[idx].strftime("%Y-%m-%d %H:%M:%S") if idx < len(timestamps) else "?"
            reason = "neu erkannt" if idx in newly_flagged else "bereits ausgeblendet"
            chk = QtWidgets.QCheckBox(f"Bild {idx + 1} ({ts}) -- {reason}")
            # setChecked() VOR dem Verbinden von toggled(): der Anfangszustand
            # (Standard: ausgeblendet) soll die Auswertung nicht nochmal
            # unnoetig neu anwenden -- self._mw._excluded_frame_indices ist
            # zu diesem Zeitpunkt (Neuaufbau der Liste) bereits konsistent.
            chk.setChecked(True)
            chk.toggled.connect(self._on_candidate_checkbox_toggled)
            self._candidate_checks[idx] = chk
            self._candidates_layout.insertWidget(self._candidates_layout.count() - 1, chk)

    def _on_candidate_checkbox_toggled(self, _checked: bool) -> None:
        """Jeder Checkbox-Klick wirkt SOFORT auf Viewer/Kurven/Export
        (Nutzerwunsch, Punkt 7) -- kein separater "Anwenden"-Knopf mehr.
        Baut die Ausschluss-Menge komplett aus dem aktuellen Zustand ALLER
        Checkboxen neu auf (nicht nur der geklickten), da mehrere Checkboxen
        gleichzeitig in der Liste stehen koennen."""
        exclude = {idx for idx, chk in self._candidate_checks.items() if chk.isChecked()}
        self._mw._apply_cleaning_exclusions(exclude)

    # -------------------------------------------------- Manuelles Ausblenden
    def _on_manual_exclude_clicked(self) -> None:
        if self._mw.recording is None:
            return
        idx = self.spin_manual_frame.value() - 1
        if not (0 <= idx < self._mw.recording.n_frames):
            return
        exclude = set(self._mw._excluded_frame_indices)
        exclude.add(idx)
        self._mw._apply_cleaning_exclusions(exclude)
        self.refresh_candidates()

    def closeEvent(self, event) -> None:
        self._mw._cancel_cleaning_point_pick()
        self._mw._apply_cleaning_point_focus_visuals(None)
        # Ebenen-Tabs (Nutzerwunsch): schliesst der Nutzer den Dialog manuell
        # waehrend "Bereinigung" aktiv ist, faellt die Tab-Auswahl zurueck auf
        # "Alle" statt einen Tab ohne sichtbaren Dialog stehen zu lassen.
        # Unter "Alle" selbst (Dialog zufaellig offen) keine Sonderbehandlung.
        if self._mw._active_layer_tab == "cleaning":
            self._mw._set_active_layer_tab("all")
        super().closeEvent(event)
