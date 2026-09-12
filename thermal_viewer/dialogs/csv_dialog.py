"""Werte-Export-Spaltenauswahl (CSV/JSON/Text) und der Dateinamensschema-
Anpassungsdialog fürs Laden bestehender Ordner mit abweichendem Muster."""
from __future__ import annotations

from functools import partial
from pathlib import Path

from qtpy import QtCore, QtWidgets

from ..data import compile_filename_template, validate_filename_template, zip_strict
from ._base import _disable_enter_auto_accept, _NoEnterAutoAccept


class CsvColumnDialog(_NoEnterAutoAccept, QtWidgets.QDialog):
    """Export-Auswahl fuer die CSV-Werte: welche Messbereiche ueberhaupt
    exportiert werden (Standard: alle) und mit welcher Spaltenueberschrift."""

    def __init__(
        self,
        parent,
        entries: list[dict],
        settings: QtCore.QSettings,
        reserved_names: list[str] | None = None,
    ):
        # entries: [{"name": str, "width_px": float, "height_px": float,
        #            "width_mm": float | None, "height_mm": float | None,
        #            "unit_suffix": str}, ...] ("unit_suffix" optional,
        #            Standard "°C")
        # Kann neben echten Messbereichen (Punkt 5) auch eine synthetische
        # "Live (Cursor)"-Zeile enthalten (width_px/height_px = Kantenlaenge
        # des Live-Cursor-Mittelungsfensters) -- fuer diese Zeile gilt exakt
        # dieselbe Auswahl-/Autofill-Logik wie fuer echte Messbereiche.
        # Ebenso kann eine Schwindungsmessungs-Zeile enthalten sein, deren
        # Wert kein Temperaturwert ist -- daher "unit_suffix" statt fest
        # "°C" (width_px/height_px sind dort nur die informative
        # Referenzbreite, keine echte Box).
        # reserved_names: die vom Aufrufer FEST vorangestellten Spalten
        # (z.B. "Zeitstempel", "Laufzeit (...)", "Live X-Achse"/"Live
        # Y-Achse") -- gegen diese wird zusaetzlich zur Eindeutigkeit unter
        # den frei editierbaren Namen selbst geprueft (siehe _on_accept).
        super().__init__(parent)
        self.setWindowTitle("Werte exportieren")
        self._settings = settings
        self._reserved_names = set(reserved_names or [])

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

        lbl_intro = QtWidgets.QLabel(
            "Spalten (Messbereiche und/oder Live-Cursor) für den Export auswählen und "
            "Spaltenüberschriften anpassen (per Hand oder per Autofill):"
        )
        lbl_intro.setWordWrap(True)
        layout.addWidget(lbl_intro)

        # Zusaetzliche, unabhaengig von der globalen Graph-Laufzeit-Einstellung
        # waehlbare Laufzeit-Spalte als fortlaufende Dezimalzahl (Nutzerwunsch:
        # "im Exportmanager eine entsprechende Option") -- Standard: AN (Nutzer-
        # wunsch: "setzte es als von Haus aus aktiviert"), landet als eigene
        # Zeile IM Grid direkt unter "ALLE" (Nutzerwunsch: "vor der ersten
        # ROI-Spalte"), siehe grid.addLayout(extra_runtime_row, 1, 0, 1, 4)
        # weiter unten.
        self.chk_extra_runtime = QtWidgets.QCheckBox("Zusätzliche fortlaufende Laufzeit-Spalte")
        self.chk_extra_runtime.setToolTip(
            "Fügt neben der normalen \"Laufzeit\"-Spalte eine weitere Spalte mit der "
            "verstrichenen Aufnahmezeit als reine Dezimalzahl in der gewählten Einheit "
            "hinzu -- unabhängig vom an den Graphen eingestellten Laufzeit-Format, "
            "praktisch zum direkten Weiterverarbeiten (z.B. Plotten) in anderer Software."
        )
        self.combo_extra_runtime_unit = QtWidgets.QComboBox()
        self.combo_extra_runtime_unit.addItem("Sekunden", "s")
        self.combo_extra_runtime_unit.addItem("Minuten", "min")
        self.combo_extra_runtime_unit.addItem("Stunden", "h")
        self.combo_extra_runtime_unit.setEnabled(False)
        self.chk_extra_runtime.toggled.connect(self.combo_extra_runtime_unit.setEnabled)
        self.chk_extra_runtime.setChecked(
            self._settings.value("export/extra_runtime_enabled", True, type=bool)
        )
        saved_unit = self._settings.value("export/extra_runtime_unit", "min")
        idx = self.combo_extra_runtime_unit.findData(saved_unit if saved_unit in ("s", "min", "h") else "min")
        self.combo_extra_runtime_unit.setCurrentIndex(max(0, idx))
        extra_runtime_row = QtWidgets.QHBoxLayout()
        extra_runtime_row.setContentsMargins(0, 0, 0, 0)
        extra_runtime_row.addWidget(self.chk_extra_runtime)
        extra_runtime_row.addWidget(self.combo_extra_runtime_unit)
        extra_runtime_row.addStretch(1)

        lbl_grid_header = QtWidgets.QLabel(
            "„px“/„mm“ ankreuzen (kombinierbar) -- der Spaltenname aktualisiert "
            "sich dabei sofort automatisch, bleibt danach aber frei editierbar. "
            "Standardmäßig aus, „ALLE“ markiert/entmarkiert die jeweilige Spalte "
            "für alle Zeilen auf einmal:"
        )
        lbl_grid_header.setWordWrap(True)
        layout.addWidget(lbl_grid_header)

        self._checks: list[QtWidgets.QCheckBox] = []
        self._edits: list[QtWidgets.QLineEdit] = []
        self._px_checks: list[QtWidgets.QCheckBox] = []
        self._mm_checks: list[QtWidgets.QCheckBox] = []
        self._bulk_update = False
        grid = QtWidgets.QGridLayout()

        # Kopfzeile: "ALLE" ueber der Ein-/Ausschluss-Spalte (Spalte 0) --
        # ersetzt die frueheren grossen "Alle auswählen"/"Keine auswählen"-
        # Knoepfe (Nutzerwunsch: einheitlich mit den bestehenden "ALLE px"/
        # "ALLE mm"-Sammel-Checkboxen in Spalte 3). Gleiches Muster:
        # _bulk_set_checked/_sync_all_checkbox, Ziel-Liste self._checks.
        self.chk_all = QtWidgets.QCheckBox("ALLE")
        self.chk_all.setToolTip("Alle Zeilen für den Export an-/abhaken.")
        grid.addWidget(self.chk_all, 0, 0)
        self.chk_all.toggled.connect(partial(self._bulk_set_checked, self._checks))

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

        # Direkt unter "ALLE", vor der ersten Messbereich-Zeile (Nutzerwunsch).
        grid.addLayout(extra_runtime_row, 1, 0, 1, 4)

        for offset, entry in enumerate(entries):
            row = offset + 2
            chk = QtWidgets.QCheckBox()
            chk.setChecked(True)
            chk.setToolTip(f"„{entry['name']}“ in den Export einschließen")
            grid.addWidget(chk, row, 0)
            self._checks.append(chk)
            chk.toggled.connect(partial(self._sync_all_checkbox, self.chk_all, self._checks))

            grid.addWidget(QtWidgets.QLabel(entry["name"]), row, 1)
            edit = QtWidgets.QLineEdit(f'{entry["name"]} ({entry.get("unit_suffix", "°C")})')
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

        # Anders als chk_px_all/chk_mm_all (deren Zeilen-Checkboxen ebenfalls
        # mit False starten, wodurch der Anfangszustand zufaellig schon
        # passt) starten die Zeilen-Checkboxen hier mit True (chk.setChecked(True)
        # oben, VOR dem toggled-Connect) -- ohne diesen expliziten Abgleich
        # bliebe chk_all auf seinem eigenen Default (nicht angehakt) haengen,
        # obwohl bereits alle Zeilen ausgewaehlt sind.
        self._sync_all_checkbox(self.chk_all, self._checks)

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
        # Spaltennamen sind frei editierbar (keine Eindeutigkeitspruefung wie
        # bei ROI-Namen generell) -- zwei gleiche Namen unter den fuer den
        # Export ausgewaehlten Spalten wuerden aber beim JSON-Export
        # (dict(zip(header, row)) in MainWindow._export_csv) stillschweigend
        # eine Spalte ueberschreiben und deren Werte verschwinden lassen.
        # Denselben Fallback wie column_names() anwenden (leerer Text ->
        # "Messwert"), sonst wuerden zwei leer gelassene Felder hier
        # faelschlich als "verschieden" durchgehen, obwohl beide am Ende
        # denselben Namen "Messwert" bekommen.
        included_names = [
            edit.text().strip() or "Messwert"
            for chk, edit in zip_strict(self._checks, self._edits) if chk.isChecked()
        ]
        duplicates = sorted({name for name in included_names if included_names.count(name) > 1})
        # Kollision mit den vom Aufrufer fest vorangestellten Spalten
        # ("Zeitstempel"/"Laufzeit (...)"/"Live X-Achse"/"Live Y-Achse", plus
        # die optionale zusaetzliche Laufzeit-Spalte weiter unten) ist
        # derselbe Fehlerfall wie zwei gleiche frei editierte Namen -- ohne
        # diese Pruefung koennte z.B. eine ROI-Spalte "Zeitstempel" genannt
        # werden und beim JSON-Export den echten Zeitstempel ueberschreiben.
        reserved_names = set(self._reserved_names)
        extra_header = self._extra_runtime_header()
        if extra_header is not None:
            reserved_names.add(extra_header)
        reserved_conflicts = sorted({name for name in included_names if name in reserved_names})
        if duplicates or reserved_conflicts:
            parts = []
            if duplicates:
                parts.append("mehrfach vergeben: " + ", ".join(f"„{d}“" for d in duplicates))
            if reserved_conflicts:
                parts.append(
                    "kollidieren mit einer festen Spalte (Zeitstempel/Laufzeit/Live-Achse/"
                    "fortlaufende Laufzeit): "
                    + ", ".join(f"„{d}“" for d in reserved_conflicts)
                )
            QtWidgets.QMessageBox.information(
                self, "Doppelte Spaltennamen",
                "Folgende Spaltenüberschriften sind für den Export nicht eindeutig -- " + "; ".join(parts),
            )
            return
        self._settings.setValue("export/extra_runtime_enabled", self.chk_extra_runtime.isChecked())
        self._settings.setValue("export/extra_runtime_unit", self.combo_extra_runtime_unit.currentData())
        self.accept()

    def _extra_runtime_header(self) -> str | None:
        """Spaltenueberschrift der optionalen zusaetzlichen Laufzeit-Spalte
        (Punkt 2), oder None wenn nicht aktiviert. Eigene Methode statt
        Inline-Berechnung, da sowohl _on_accept() (Eindeutigkeitspruefung)
        als auch der Aufrufer (extra_runtime_header()) denselben Text
        brauchen."""
        if not self.chk_extra_runtime.isChecked():
            return None
        unit_labels = {"s": "Sekunden", "min": "Minuten", "h": "Stunden"}
        unit_label = unit_labels[self.combo_extra_runtime_unit.currentData()]
        return f"Laufzeit (fortlaufend, {unit_label})"

    def include_extra_runtime(self) -> bool:
        return self.chk_extra_runtime.isChecked()

    def extra_runtime_unit(self) -> str:
        return self.combo_extra_runtime_unit.currentData()

    def extra_runtime_header(self) -> str | None:
        return self._extra_runtime_header()

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
        edit.setText(f'{entry["name"]}{suffix} ({entry.get("unit_suffix", "°C")})')

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
