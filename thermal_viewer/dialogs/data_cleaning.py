"""MODALER Dialog zur Rohdaten-Bereinigung (Ausreißer-Bilder anhand frei
markierter Referenzpunkte erkennen/ausblenden -- siehe main_window/
data_cleaning_ops.py für das Datenmodell/die Berechnungen).

Nutzerwunsch: dieser Schritt MUSS abgeschlossen sein, bevor überhaupt ein
Bild im Hauptfenster erscheint -- daher ECHT MODAL (blockiert das
Hauptfenster komplett, bis der Dialog geschlossen wird), anders als die
frühere, bewusst nicht-modale Fassung (die noch gleichzeitiges Klicken im
Hauptfenster-Bild erforderte, um Referenzpunkte zu setzen). Seit dem Umbau
setzt/verschiebt der Dialog Referenzpunkte AUSSCHLIESSLICH in seiner
eigenen, großen Bildvorschau (siehe data_cleaning_viewer.py) -- diese
Notwendigkeit entfällt damit, echte Modalität ist also unproblematisch.
Hält deshalb (anders als z.B. RulerLengthDialog) keinen eigenen
Ergebniszustand vor, der erst bei exec()/accept() ausgelesen wird, sondern
ruft bei jeder Aktion direkt auf MainWindow zurück (self._mw)."""
from __future__ import annotations

from functools import partial

from qtpy import QtCore, QtWidgets

from ..widgets import LocaleTolerantDoubleSpinBox
from ._base import _disable_enter_auto_accept, _NoEnterAutoAccept
from .data_cleaning_viewer import CleaningPreviewViewer


class DataCleaningDialog(_NoEnterAutoAccept, QtWidgets.QDialog):
    def __init__(self, main_window) -> None:
        super().__init__(main_window)
        self._mw = main_window
        self.setWindowTitle("Rohdaten säubern")
        self.setWindowModality(QtCore.Qt.WindowModality.ApplicationModal)
        self.setMinimumSize(900, 600)
        self.resize(1050, 680)

        root_layout = QtWidgets.QHBoxLayout(self)

        # -- LINKS: grosse Bildvorschau (analog zum Hauptfenster-Layout,
        # Nutzerwunsch) -----------------------------------------------
        left_column = QtWidgets.QVBoxLayout()
        self.preview = CleaningPreviewViewer(main_window, self.refresh_points)
        self.preview.set_recording(main_window.recording)
        left_column.addWidget(self.preview, 1)

        start_end_row = QtWidgets.QHBoxLayout()
        self.btn_set_eval_start = QtWidgets.QPushButton("Als Auswertungsstart")
        self.btn_set_eval_start.setToolTip(
            "Setzt den Auswertungsstart (dieselbe Einstellung wie im Hauptfenster) auf das "
            "gerade oben angezeigte Vorschaubild."
        )
        self.btn_set_eval_start.clicked.connect(self._on_set_eval_start_clicked)
        start_end_row.addWidget(self.btn_set_eval_start)
        self.btn_set_eval_end = QtWidgets.QPushButton("Als Auswertungsende")
        self.btn_set_eval_end.setToolTip(
            "Setzt das Auswertungsende (dieselbe Einstellung wie im Hauptfenster) auf das "
            "gerade oben angezeigte Vorschaubild."
        )
        self.btn_set_eval_end.clicked.connect(self._on_set_eval_end_clicked)
        start_end_row.addWidget(self.btn_set_eval_end)
        left_column.addLayout(start_end_row)
        root_layout.addLayout(left_column, 3)

        # -- RECHTS: Steuerelemente -------------------------------------
        right_column = QtWidgets.QVBoxLayout()

        intro = QtWidgets.QLabel(
            "Erkennt einzelne Ausreißer-Bilder (z.B. durch eine kurze Kamera-/Übertragungsstörung) "
            "anhand der Temperaturänderung zum jeweils VORHERIGEN Bild an frei markierten "
            "Referenzpunkten. Punkt in die Vorschau links klicken, um ihn zu setzen -- direkt im "
            "Bild ziehen, um ihn zu verschieben. Ausgeblendete Bilder bleiben erhalten (samt "
            "Zeitstempel) und lassen sich hier jederzeit wieder einzeln einblenden."
        )
        intro.setWordWrap(True)
        right_column.addWidget(intro)

        points_box = QtWidgets.QGroupBox("Referenzpunkte")
        points_layout = QtWidgets.QVBoxLayout(points_box)
        self.points_list = QtWidgets.QListWidget()
        self.points_list.setMaximumHeight(140)
        points_layout.addWidget(self.points_list)
        # self._point_row_widgets: pro Zeile ein dict mit den Widgets, die
        # refresh_point_deltas()/Tests danach wieder ansprechen (siehe
        # refresh_points).
        self._point_row_widgets: list[dict] = []
        logic_hint = QtWidgets.QLabel(
            "Häkchen: Punkt vorübergehend deaktivieren (zählt dann nicht mehr mit). Ein Bild gilt "
            "als Ausreißer, wenn ENTWEDER an ALLEN \"UND\"-Punkten ODER an MINDESTENS EINEM "
            "\"ODER\"-Punkt (je Zeile wählbar) die Temperaturänderung zum vorherigen Bild den "
            "Schwellenwert überschreitet."
        )
        logic_hint.setWordWrap(True)
        logic_hint.setStyleSheet("color:#6b7280;")
        points_layout.addWidget(logic_hint)

        # Mittelungsbereich + Anzeige in EINER kompakten Zeile statt zwei
        # separater, ausführlich beschrifteter Zeilen.
        kernel_row = QtWidgets.QHBoxLayout()
        kernel_row.addWidget(QtWidgets.QLabel("Mittelung:"))
        self._kernel_sizes = [1, 3, 5, 7, 9]
        self.combo_kernel = QtWidgets.QComboBox()
        for size in self._kernel_sizes:
            label = "1×1" if size == 1 else f"{size}×{size} (Ø)"
            self.combo_kernel.addItem(label, size)
        self.sync_kernel_size_combo()
        self.combo_kernel.currentIndexChanged.connect(self._on_kernel_size_changed)
        kernel_row.addWidget(self.combo_kernel)
        self.chk_show_kernel_area = QtWidgets.QCheckBox("Bereich zeigen")
        self.chk_show_kernel_area.setChecked(main_window._cleaning_show_kernel_area)
        self.chk_show_kernel_area.toggled.connect(self._on_show_kernel_area_toggled)
        kernel_row.addWidget(self.chk_show_kernel_area)
        kernel_row.addStretch(1)
        points_layout.addLayout(kernel_row)

        right_column.addWidget(points_box)

        threshold_form = QtWidgets.QFormLayout()
        self.spin_threshold = LocaleTolerantDoubleSpinBox()
        self.spin_threshold.setRange(0.1, 1000.0)
        self.spin_threshold.setDecimals(1)
        self.spin_threshold.setSuffix(" °C")
        self.spin_threshold.setValue(main_window._cleaning_threshold)
        self.spin_threshold.valueChanged.connect(self._on_threshold_changed)
        # Beendet die von _on_threshold_changed begonnene Eingabe-Sitzung
        # (siehe undo_ops.py), analog zu den ROI-Positions-Spinboxen.
        self.spin_threshold.editingFinished.connect(main_window._end_grouped_undo_edit)
        threshold_form.addRow("Schwellenwert (dT zum Vorbild):", self.spin_threshold)
        right_column.addLayout(threshold_form)

        # Manuelles Ausblenden: ein beliebiges Bild -- unabhaengig von
        # Referenzpunkten/Schwellenwert -- direkt per Bildnummer ausblenden
        # koennen. Dieselbe Spinbox steuert ZUSAETZLICH die Vorschau links
        # (tippt man hier "10" ein, zeigt die Vorschau sofort Bild 10).
        manual_row = QtWidgets.QHBoxLayout()
        manual_row.addWidget(QtWidgets.QLabel("Bild-Nr. (Vorschau links / manuell ausblenden):"))
        self.spin_manual_frame = QtWidgets.QSpinBox()
        self.spin_manual_frame.setMinimum(1)
        self.spin_manual_frame.setMaximum(max(1, main_window.recording.n_frames if main_window.recording else 1))
        self.spin_manual_frame.valueChanged.connect(self._on_manual_frame_changed)
        manual_row.addWidget(self.spin_manual_frame)
        self.btn_manual_exclude = QtWidgets.QPushButton("Bild ausblenden")
        self.btn_manual_exclude.clicked.connect(self._on_manual_exclude_clicked)
        manual_row.addWidget(self.btn_manual_exclude)
        manual_row.addStretch(1)
        right_column.addLayout(manual_row)

        # Segment-Ausblenden (Nutzerwunsch: "eine Option einfügen, die
        # ersten X Bilder ausblenden zu können ... am besten generell ganze
        # Segmente/Bereiche auf einmal ausblenden ... von Bild X bis Y, und
        # nachher dazu noch Bild K und nochmal den Bereich von A bis D") --
        # ergaenzt (nicht ersetzt) das einzelne Ausblenden oben: beide Zeilen
        # bauen auf derselben Vereinigungs-Menge auf (siehe _apply_cleaning_
        # exclusions), lassen sich also beliebig oft nacheinander kombinieren.
        range_row = QtWidgets.QHBoxLayout()
        range_row.addWidget(QtWidgets.QLabel("Bereich ausblenden: Bild"))
        max_frame = max(1, main_window.recording.n_frames if main_window.recording else 1)
        self.spin_range_start = QtWidgets.QSpinBox()
        self.spin_range_start.setRange(1, max_frame)
        range_row.addWidget(self.spin_range_start)
        range_row.addWidget(QtWidgets.QLabel("bis"))
        self.spin_range_end = QtWidgets.QSpinBox()
        self.spin_range_end.setRange(1, max_frame)
        range_row.addWidget(self.spin_range_end)
        self.btn_exclude_range = QtWidgets.QPushButton("Bereich ausblenden")
        self.btn_exclude_range.setToolTip(
            "Blendet alle Bilder von \"Bild X\" bis \"Bild Y\" (beide eingeschlossen) auf einmal "
            "aus -- zusätzlich zu bereits ausgeblendeten Bildern/Bereichen, beliebig oft "
            "nacheinander nutzbar (z.B. um mehrere Bereiche und einzelne Bilder zu kombinieren)."
        )
        self.btn_exclude_range.clicked.connect(self._on_exclude_range_clicked)
        range_row.addWidget(self.btn_exclude_range)
        range_row.addStretch(1)
        right_column.addLayout(range_row)

        self.candidates_area = QtWidgets.QScrollArea()
        self.candidates_area.setWidgetResizable(True)
        self.candidates_area.setMinimumHeight(140)
        self._candidates_container = QtWidgets.QWidget()
        self._candidates_layout = QtWidgets.QVBoxLayout(self._candidates_container)
        self._candidates_layout.addStretch(1)
        self.candidates_area.setWidget(self._candidates_container)
        right_column.addWidget(self.candidates_area, 1)
        self._candidate_checks: dict[int, QtWidgets.QCheckBox] = {}

        # Zusammenfassungszeile ganz unten, direkt vor den Knoepfen.
        self.lbl_summary = QtWidgets.QLabel()
        right_column.addWidget(self.lbl_summary)

        buttons = QtWidgets.QDialogButtonBox()
        self.btn_close = buttons.addButton("Schließen", QtWidgets.QDialogButtonBox.ButtonRole.RejectRole)
        self.btn_close.clicked.connect(self.close)
        _disable_enter_auto_accept(buttons)
        right_column.addWidget(buttons)

        root_layout.addLayout(right_column, 2)

        self._on_manual_frame_changed(self.spin_manual_frame.value())

    # -------------------------------------------------------- Auswertung
    def _on_set_eval_start_clicked(self) -> None:
        idx = self.spin_manual_frame.value() - 1
        self._mw.spin_eval_start.setValue(idx + 1)
        # setValue() loest ueber valueChanged _on_eval_start_changed() aus,
        # die (fuer Tastatur-Tipp-Sitzungen/Marker-Drags gedachte, siehe
        # undo_ops.py) _begin_grouped_undo_edit() ruft -- ein reiner
        # Knopfklick bekommt aber nie ein editingFinished, also hier
        # explizit schliessen, statt die Gruppierung offen zu lassen.
        self._mw._end_grouped_undo_edit()
        self._mw.statusBar().showMessage(f"Auswertungsstart auf Bild {idx + 1} gesetzt.", 4000)

    def _on_set_eval_end_clicked(self) -> None:
        idx = self.spin_manual_frame.value() - 1
        self._mw.spin_eval_end.setValue(idx + 1)
        self._mw._end_grouped_undo_edit()
        self._mw.statusBar().showMessage(f"Auswertungsende auf Bild {idx + 1} gesetzt.", 4000)

    # ------------------------------------------------------------- Punkte
    def _on_point_enabled_toggled(self, row_index: int, checked: bool) -> None:
        self._mw._push_undo_snapshot()
        self._mw._set_cleaning_point_enabled(row_index, checked)
        self.refresh_points()
        state = "aktiviert" if checked else "deaktiviert"
        self._mw.statusBar().showMessage(f"Referenzpunkt {row_index + 1} {state}.", 3000)

    def _on_point_remove_clicked(self, row_index: int) -> None:
        self._mw._push_undo_snapshot()
        self._mw._remove_cleaning_point(row_index)
        self.refresh_points()
        self._mw.statusBar().showMessage(f"Referenzpunkt {row_index + 1} entfernt.", 3000)

    def _on_point_logic_combo_changed(self, row_index: int, combo_index: int) -> None:
        if not (0 <= row_index < len(self._point_row_widgets)):
            return
        logic = self._point_row_widgets[row_index]["combo_logic"].itemData(combo_index)
        self._mw._push_undo_snapshot()
        self._mw._set_cleaning_point_logic(row_index, logic)
        self.refresh_candidates()
        self._mw.statusBar().showMessage(
            f"Punkt {row_index + 1}: Logik auf {'ODER' if logic == 'or' else 'UND'} gesetzt.", 3000,
        )

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
        self._mw._push_undo_snapshot()
        size = self.combo_kernel.itemData(index)
        self._mw._cleaning_kernel_size = size
        self.preview.draw_points()
        self.refresh_candidates()
        self._mw.statusBar().showMessage(f"Mittelungsbereich auf {size}×{size} gesetzt.", 3000)

    def _on_show_kernel_area_toggled(self, checked: bool) -> None:
        # Rein visuelle Einstellung, nicht Teil des gespeicherten Projekt-
        # zustands (siehe window.py-Kommentar bei _cleaning_show_kernel_area)
        # -- bewusst OHNE Undo-Snapshot, analog zu _on_toggle_scale_visuals.
        self._mw._cleaning_show_kernel_area = checked
        self.preview.draw_points()

    def refresh_points(self) -> None:
        """Baut die Punkte-Liste komplett neu auf -- jede Zeile bekommt eine
        Checkbox (Punkt aktiv/deaktiviert), Position, ΔT-Anzeige, UND/ODER-
        Combobox und einen kleinen "×"-Knopf zum endgültigen Entfernen
        (Nutzerwunsch: keine grossen eigenen Buttons mehr, siehe __init__)."""
        self.points_list.clear()
        self._point_row_widgets = []
        for i, (x, y, logic, enabled) in enumerate(self._mw._cleaning_points, start=1):
            row_widget = QtWidgets.QWidget()
            row_layout = QtWidgets.QHBoxLayout(row_widget)
            row_layout.setContentsMargins(4, 2, 4, 2)

            chk_enabled = QtWidgets.QCheckBox()
            chk_enabled.setToolTip(
                "Punkt vorübergehend deaktivieren -- zählt dann nicht mehr bei UND/ODER mit und "
                "wird in der Vorschau ausgeblendet, bleibt aber gespeichert."
            )
            chk_enabled.setChecked(enabled)
            chk_enabled.toggled.connect(partial(self._on_point_enabled_toggled, i - 1))
            row_layout.addWidget(chk_enabled)

            lbl_position = QtWidgets.QLabel(f"Punkt {i}: (x={x:.1f}, y={y:.1f})")
            row_layout.addWidget(lbl_position, 1)

            lbl_delta = QtWidgets.QLabel("ΔT: –")
            row_layout.addWidget(lbl_delta)

            combo_logic = QtWidgets.QComboBox()
            combo_logic.addItem("UND", "and")
            combo_logic.addItem("ODER", "or")
            combo_logic.setToolTip(
                "UND: dieser Punkt muss GLEICHZEITIG mit allen anderen UND-Punkten ausschlagen.\n"
                "ODER: dieser Punkt allein blendet das Bild schon aus."
            )
            combo_logic.setCurrentIndex(1 if logic == "or" else 0)
            combo_logic.currentIndexChanged.connect(partial(self._on_point_logic_combo_changed, i - 1))
            row_layout.addWidget(combo_logic)

            btn_remove = QtWidgets.QPushButton("×")
            btn_remove.setFixedSize(22, 22)
            btn_remove.setToolTip("Punkt endgültig entfernen")
            btn_remove.clicked.connect(partial(self._on_point_remove_clicked, i - 1))
            row_layout.addWidget(btn_remove)

            item = QtWidgets.QListWidgetItem()
            item.setSizeHint(row_widget.sizeHint())
            self.points_list.addItem(item)
            self.points_list.setItemWidget(item, row_widget)
            self._point_row_widgets.append({
                "checkbox": chk_enabled, "label_position": lbl_position,
                "label_delta": lbl_delta, "combo_logic": combo_logic, "remove_button": btn_remove,
            })
        self.refresh_point_deltas()
        self.preview.set_recording(self._mw.recording)
        # Frame-Anzahl kann sich seit dem Aufbau des Dialogs geaendert haben
        # (z.B. neue Aufnahme geladen, waehrend der Dialog noch offen war).
        max_frame = max(1, self._mw.recording.n_frames if self._mw.recording else 1)
        for spin in (self.spin_manual_frame, self.spin_range_start, self.spin_range_end):
            spin.setMaximum(max_frame)
        self._refresh_preview()
        self.refresh_candidates()

    def refresh_point_deltas(self) -> None:
        """Aktualisiert NUR die ΔT-Sub-Beschriftung jeder Zeile -- aufgerufen
        bei jeder Punkt-/Frame-Aenderung (siehe _refresh_preview), ohne die
        Liste komplett neu aufzubauen."""
        current_idx = self.spin_manual_frame.value() - 1
        for i, row in enumerate(self._point_row_widgets):
            delta = self._mw._cleaning_point_delta(i, current_idx)
            text = f"{delta:.1f}°C" if delta is not None else "–"
            row["label_delta"].setText(f"ΔT: {text}")

    # ---------------------------------------------------------- Schwelle
    def _on_threshold_changed(self, value: float) -> None:
        # Feuert live bei JEDEM Tastendruck/Pfeiltasten-Schritt -- gruppiert
        # wie die ROI-Positions-Spinboxen (siehe __init__/undo_ops.py).
        self._mw._begin_grouped_undo_edit()
        self._mw._cleaning_threshold = value
        self.refresh_candidates()
        # Laeuft wie andere Live-Statuszeilen (z.B. der Cursor-Wert in
        # mouse_ops.py) bei jeder Aenderung neu -- unkritisch, ueberschreibt
        # nur denselben Text.
        self._mw.statusBar().showMessage(f"Schwellenwert auf {value:g} gesetzt.", 3000)

    # --------------------------------------------------------- Vorschau
    def refresh_candidates(self) -> None:
        """Baut die Checkbox-Liste neu auf: die VEREINIGUNG aus bereits
        ausgeschlossenen Bildern (koennen hier wieder eingeblendet werden)
        und gerade NEU als Ausreißer erkannten Bildern (mit den aktuellen
        Punkten/Schwellenwert) -- jede Checkbox bedeutet "ausblenden",
        angehakt ist der jeweilige Standardzustand. Jede Neuberechnung
        wendet den Standardzustand SOFORT an (siehe _mw._apply_cleaning_
        exclusions unten), kein separater "Anwenden"-Knopf."""
        while self._candidate_checks:
            _, chk = self._candidate_checks.popitem()
            chk.setParent(None)
            chk.deleteLater()

        newly_flagged = self._mw._compute_cleaning_candidates() if self._mw._cleaning_points else set()
        union = sorted(newly_flagged | self._mw._excluded_frame_indices)
        # rebuild_dialog_on_reject=False: verhindert eine Endlosschleife,
        # falls diese Neuberechnung zufaellig wieder ALLE Bilder abdeckt
        # (siehe _apply_cleaning_exclusions).
        self._mw._apply_cleaning_exclusions(set(union), rebuild_dialog_on_reject=False)
        self._refresh_preview()

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

    def _on_candidate_checkbox_toggled(self, checked: bool) -> None:
        """Jeder Checkbox-Klick wirkt SOFORT auf Viewer/Kurven/Export -- kein
        separater "Anwenden"-Knopf. Baut die Ausschluss-Menge komplett aus
        dem aktuellen Zustand ALLER Checkboxen neu auf (nicht nur der
        geklickten), da mehrere Checkboxen gleichzeitig in der Liste stehen
        koennen."""
        exclude = {idx for idx, chk in self._candidate_checks.items() if chk.isChecked()}
        self._mw._push_undo_snapshot()
        self._mw._apply_cleaning_exclusions(exclude)
        self._refresh_preview()
        # sender() statt eines mit partial() gebundenen Index (wie bei den
        # Punkte-Zeilen oben) -- diese Checkboxen werden bei jedem
        # refresh_candidates() komplett neu aufgebaut, ein gebundener Index
        # waere hier also nicht robuster als die Objekt-Identitaet.
        sender = self.sender()
        idx = next((i for i, chk in self._candidate_checks.items() if chk is sender), None)
        if idx is not None:
            action = "ausgeschlossen" if checked else "wieder eingeschlossen"
            self._mw.statusBar().showMessage(f"Bild {idx + 1} {action}.", 3000)

    # -------------------------------------------------- Manuelle Vorschau
    def _on_manual_frame_changed(self, _value: int) -> None:
        """Diese Spinbox steuert die Vorschau links -- unabhaengig vom
        Hauptfenster, zeigt auch ein bereits ausgeblendetes Bild an."""
        self._refresh_preview()

    def _refresh_preview(self) -> None:
        idx = self.spin_manual_frame.value() - 1
        self.preview.show_frame(idx)
        self.refresh_point_deltas()

    def _on_manual_exclude_clicked(self) -> None:
        if self._mw.recording is None:
            return
        idx = self.spin_manual_frame.value() - 1
        if not (0 <= idx < self._mw.recording.n_frames):
            return
        exclude = set(self._mw._excluded_frame_indices)
        exclude.add(idx)
        self._mw._push_undo_snapshot()
        self._mw._apply_cleaning_exclusions(exclude)
        self.refresh_candidates()
        self._refresh_preview()
        self._mw.statusBar().showMessage(f"Bild {idx + 1} manuell ausgeschlossen.", 3000)

    def _on_exclude_range_clicked(self) -> None:
        """Blendet einen ganzen Bereich (Bild-Nr. "von".."bis", beide
        eingeschlossen) in EINEM Schritt aus -- Ergaenzung zum Ausblenden
        einzelner Bilder oben (Nutzerwunsch), z.B. um die ersten X Bilder
        einer Aufnahme (Sensor-Einschwingzeit) auf einen Schlag loszuwerden.
        Vertauschte Werte (Ende vor Start eingegeben) werden stillschweigend
        richtig herum interpretiert, statt einen leeren/falschen Bereich zu
        erzeugen."""
        if self._mw.recording is None:
            return
        start_idx = self.spin_range_start.value() - 1
        end_idx = self.spin_range_end.value() - 1
        if end_idx < start_idx:
            start_idx, end_idx = end_idx, start_idx
        exclude = set(self._mw._excluded_frame_indices)
        exclude.update(range(start_idx, end_idx + 1))
        self._mw._push_undo_snapshot()
        self._mw._apply_cleaning_exclusions(exclude)
        self.refresh_candidates()
        self._refresh_preview()
        self._mw.statusBar().showMessage(
            f"Bilder {start_idx + 1}-{end_idx + 1} ausgeschlossen.", 3000,
        )
