"""Ordner-/Dateien-Import, Namensschema, Datenimport-Einstellungen, Live-Ordner-Überwachung."""
from __future__ import annotations

import re
from datetime import datetime, timedelta
from pathlib import Path

from qtpy import QtCore, QtWidgets

from ..data import (
    DEFAULT_FILENAME_TEMPLATE,
    ImportSettings,
    Recording,
    RecordingError,
    append_paths,
    compile_filename_template,
    files_matching_template,
    load_tiff_grayscale,
    render_filename_template,
    tiff_crop_to_temperature,
)
from ..dialogs import (
    FilenameTemplateDialog,
    ImportSettingsDialog,
    TiffImportDialog,
)
from .constants import (
    MAX_FRAMES_WITH_SYMBOLS,
)


class _ImportMixin:
    def _open_folder(self) -> None:
        folder = QtWidgets.QFileDialog.getExistingDirectory(self, "Ordner mit CSV-Messreihe wählen")
        if not folder:
            return
        if self._load_folder(Path(folder)):
            # Nutzerwunsch: "Direkt wenn ich die Daten einlade soll sich so
            # ein Dialog-Fenster öffnen" -- nicht-modal (siehe
            # DataCleaningDialog), der Nutzer kann ihn jederzeit ohne
            # Bereinigung schliessen; danach weiterhin ueber
            # "Daten > Rohdaten säubern…" erreichbar.
            self._open_data_cleaning_dialog()

    def _import_tiff_images(self) -> None:
        """Wandelt einzelne Graustufen-TIFF-Bilder (siehe TiffImportDialog
        und data.load_tiff_grayscale/tiff_crop_to_temperature für den vollen
        Hintergrund und die bewussten Einschränkungen -- nur echte
        Graustufenbilder, manuell angegebene Min-/Max-Temperatur, keinerlei
        automatische Kalibrierung) in Messdateien im normalen Format um.
        Die geschriebenen Dateien landen in einem selbst gewählten
        Zielordner und lassen sich danach ganz normal per "Ordner öffnen"
        laden (wird am Ende optional direkt angeboten)."""
        paths_str, _ = QtWidgets.QFileDialog.getOpenFileNames(
            self, "TIFF-Bilder auswählen", "", "TIFF-Bilder (*.tiff *.tif)"
        )
        if not paths_str:
            return
        paths = [Path(p) for p in paths_str]

        try:
            preview_gray = load_tiff_grayscale(paths[0])
        except RecordingError as exc:
            QtWidgets.QMessageBox.critical(self, "TIFF konnte nicht gelesen werden", str(exc))
            return

        import_dialog = TiffImportDialog(self, preview_gray, len(paths))
        if import_dialog.exec() != QtWidgets.QDialog.DialogCode.Accepted:
            return
        crop = import_dialog.crop_rect()
        t_min = import_dialog.min_temp()
        t_max = import_dialog.max_temp()

        dest = QtWidgets.QFileDialog.getExistingDirectory(
            self, "Zielordner für die umgerechneten Messdateien wählen"
        )
        if not dest:
            return
        dest_folder = Path(dest)

        progress = QtWidgets.QProgressDialog(
            "TIFF-Bilder werden umgerechnet…", "Abbrechen", 0, len(paths), self
        )
        progress.setWindowModality(QtCore.Qt.WindowModal)
        progress.setMinimumDuration(300)

        # Diese Kamera-Exporte tragen keinen im Dateinamen erkennbaren
        # echten Zeitstempel (siehe Analyse-Notizen) -- kuenstliche,
        # sekundengenau aufsteigende Zeitstempel (Reihenfolge = Datei-
        # Auswahlreihenfolge) reichen fuer DEFAULT_FILENAME_TEMPLATE und
        # ergeben eine sinnvoll abspielbare, aber rein kuenstliche Zeitachse.
        base_time = datetime.now().replace(microsecond=0)
        written = 0
        was_cancelled = False
        skipped: list[tuple[Path, str]] = []
        for n, path in enumerate(paths):
            if progress.wasCanceled():
                was_cancelled = True
                break
            progress.setValue(n)
            QtWidgets.QApplication.processEvents()
            try:
                gray = preview_gray if n == 0 else load_tiff_grayscale(path)
                if gray.shape != preview_gray.shape:
                    # Der Bildausschnitt (crop) wurde anhand der ERSTEN Datei
                    # gezogen -- eine andere Bildgroesse wuerde ihn an der
                    # falschen Stelle (oder ueber den Rand hinaus, was numpy
                    # beim Zuschneiden stillschweigend kappen wuerde) anwenden,
                    # statt eines klaren Fehlers. Lieber ueberspringen als
                    # eine unbemerkt falsch zugeschnittene Temperaturmatrix
                    # erzeugen.
                    raise RecordingError(
                        f"Bildgröße weicht von der Vorschau ab ({gray.shape[1]}×{gray.shape[0]} "
                        f"statt {preview_gray.shape[1]}×{preview_gray.shape[0]} px) -- der gewählte "
                        "Ausschnitt würde nicht passen."
                    )
                temp_array = tiff_crop_to_temperature(gray, crop, t_min, t_max)
            except RecordingError as exc:
                skipped.append((path, str(exc)))
                continue
            except Exception as exc:
                # Bewusst breit (siehe z.B. _export_single_graph): eine
                # einzelne kaputte/unerwartete Datei soll den kompletten
                # Stapel-Import nicht abbrechen, sondern nur diese eine Datei
                # uebersprungen werden -- wie beim normalen CSV-Laden
                # (_load_paths) auch.
                skipped.append((path, str(exc)))
                continue
            timestamp = base_time + timedelta(seconds=n)
            filename = render_filename_template(DEFAULT_FILENAME_TEMPLATE, timestamp) + ".csv"
            # Zeilenformat wie von der bestehenden Kamera-Software (siehe
            # data.ImportSettings-Standard: ';'-getrennt, Dezimalkomma, mit
            # abschliessendem ';'), damit die Dateien ohne jede
            # Datenimport-Anpassung normal ladbar sind.
            lines = [
                ";".join(f"{value:.2f}".replace(".", ",") for value in row) + ";"
                for row in temp_array
            ]
            try:
                (dest_folder / filename).write_text("\n".join(lines), encoding="utf-8-sig")
            except OSError as exc:
                skipped.append((path, f"Konnte nicht geschrieben werden: {exc}"))
                continue
            written += 1
        progress.setValue(len(paths))

        summary = f"{written} von {len(paths)} Datei(en) umgerechnet und in „{dest_folder}“ gespeichert."
        if was_cancelled:
            summary += " (Abgebrochen -- bereits geschriebene Dateien bleiben erhalten.)"
        if skipped:
            details = "\n".join(f"„{p.name}“: {reason}" for p, reason in skipped)
            QtWidgets.QMessageBox.warning(
                self, "TIFF-Import mit Warnungen", f"{summary}\n\nÜbersprungen:\n{details}"
            )
        else:
            self.statusBar().showMessage(summary, 6000)

        if written and QtWidgets.QMessageBox.question(
            self, "Ordner jetzt laden?", f"{summary}\n\nDiesen Ordner jetzt öffnen?",
            QtWidgets.QMessageBox.StandardButton.Yes | QtWidgets.QMessageBox.StandardButton.No,
            QtWidgets.QMessageBox.StandardButton.Yes,
        ) == QtWidgets.QMessageBox.StandardButton.Yes:
            self._load_folder(dest_folder)

    def _safe_folder_scan(self, folder: Path, scan_fn):
        """Fuehrt scan_fn() aus und faengt einen zwischen Auswahl und Scan
        unlesbar gewordenen Ordner (Netzlaufwerk getrennt, Ordner geloescht/
        umbenannt) einheitlich ab -- gemeinsam von _load_folder und
        _resolve_folder_and_pattern genutzt, statt denselben OSError-Dialog
        an zwei Stellen zu wiederholen. Gibt bei Erfolg das Ergebnis von
        scan_fn() zurueck (fuer beide Aufrufer stets eine Liste, ggf. leer),
        bei einem Lesefehler None."""
        try:
            return scan_fn()
        except OSError as exc:
            QtWidgets.QMessageBox.critical(
                self, "Ordner nicht lesbar", f"„{folder}“ konnte nicht gelesen werden:\n{exc}"
            )
            return None

    def _load_folder(self, folder: Path) -> bool:
        """Laedt eine komplette Messreihe aus folder (Namensschema-Abgleich,
        Live-Ueberwachung) -- gemeinsame Grundlage fuer "Ordner öffnen…" UND
        das automatische Nachladen des im Projekt gespeicherten Quellordners
        beim Laden eines Projekts ohne bereits geladene Messreihe (siehe
        _load_project). Gibt zurueck, ob das Laden erfolgreich war."""
        result = self._resolve_folder_and_pattern(folder)
        if result is None:
            return False
        folder_path, pattern, strptime_fmt = result
        paths = self._safe_folder_scan(folder_path, lambda: sorted(folder_path.glob("*.csv")))
        if paths is None:
            return False
        if not self._load_paths(paths, pattern=pattern, strptime_fmt=strptime_fmt):
            # Laden fehlgeschlagen (z.B. defekte CSVs) -- eine evtl. bereits
            # laufende Live-Ueberwachung eines ANDEREN Ordners darf dadurch
            # nicht auf diesen (nicht geladenen) Ordner umgehaengt werden.
            return False
        # Laeuft ab hier automatisch dauerhaft im Hintergrund weiter (kein
        # manuelles Ein-/Ausschalten mehr noetig) -- damit die App parallel
        # zu einer noch laufenden Messung genutzt werden kann, ohne dass
        # dafuer eine extra Einstellung gesetzt werden muss.
        self._watched_folder = folder_path
        self._live_watch_timer.start()
        self._refresh_idle_guidance()
        return True

    def _resolve_folder_and_pattern(self, folder: Path) -> tuple[Path, re.Pattern, str] | None:
        """Stellt sicher, dass mindestens eine ".csv"-Datei in folder zum
        AKTIVEN Namensschema passt, bevor tatsaechlich geladen wird (Punkt 5:
        "Falls im Ausgabe-Ordner keine Dateien gefunden werden können, die
        dem bisherigen Namensschema entsprechen").

        Fragt bei fehlendem Treffer per Dialog nach: neuen Ordner waehlen
        (Schleife mit demselben Schema), Namensschema fuer DIESEN Ordner
        anpassen (siehe FilenameTemplateDialog; per Haekchen dort optional
        auch dauerhaft als neuer Standard speicherbar -- Standard: nur fuer
        diesen einen Ladevorgang), oder abbrechen. Gibt bei Abbruch None,
        sonst (Ordner, Pattern, Format) zurueck -- Pattern/Format koennen vom
        aktuellen Standard abweichen (siehe oben)."""
        pattern, strptime_fmt = self._filename_pattern, self._filename_strptime_fmt
        while True:
            matches = self._safe_folder_scan(folder, lambda: files_matching_template(folder, pattern))
            if matches is None:
                return None
            if matches:
                return folder, pattern, strptime_fmt

            choice = self._ask_filename_mismatch(folder)
            if choice == "cancel":
                return None
            if choice == "new_folder":
                new_folder = QtWidgets.QFileDialog.getExistingDirectory(
                    self, "Ordner mit CSV-Messreihe wählen"
                )
                if not new_folder:
                    return None
                folder = Path(new_folder)
                continue
            # choice == "template"
            dlg = FilenameTemplateDialog(self, folder, self._filename_template)
            if dlg.exec() != QtWidgets.QDialog.DialogCode.Accepted:
                continue
            new_template = dlg.template()
            if dlg.persist():
                self._set_filename_template(new_template)
                pattern, strptime_fmt = self._filename_pattern, self._filename_strptime_fmt
            else:
                pattern, strptime_fmt = compile_filename_template(new_template)
            # Naechster Schleifendurchlauf findet garantiert einen Treffer --
            # FilenameTemplateDialog laesst OK nur zu, wenn das Template
            # bereits mindestens eine Datei in GENAU diesem Ordner trifft.

    def _ask_filename_mismatch(self, folder: Path) -> str:
        """Rueckfrage, wenn keine .csv-Datei in folder zum aktiven
        Namensschema passt -- "new_folder"/"template"/"cancel"."""
        box = QtWidgets.QMessageBox(self)
        box.setIcon(QtWidgets.QMessageBox.Icon.Warning)
        box.setWindowTitle("Namensschema nicht erkannt")
        box.setText(
            f"Im Ordner „{folder}“ passt keine CSV-Datei zum erwarteten Namensschema "
            f"(„{self._filename_template}“). Wie möchtest du fortfahren?"
        )
        btn_new_folder = box.addButton("Neuen Ordner wählen", QtWidgets.QMessageBox.ButtonRole.ActionRole)
        btn_template = box.addButton("Namenstemplate anpassen…", QtWidgets.QMessageBox.ButtonRole.ActionRole)
        box.addButton("Abbrechen", QtWidgets.QMessageBox.ButtonRole.RejectRole)
        box.setDefaultButton(btn_template)
        box.exec()
        clicked = box.clickedButton()
        if clicked is btn_new_folder:
            return "new_folder"
        if clicked is btn_template:
            return "template"
        return "cancel"

    def _set_filename_template(self, template: str) -> None:
        self._filename_template = template
        self._filename_pattern, self._filename_strptime_fmt = compile_filename_template(template)
        self._settings.setValue("filename_template", template)

    def _load_import_settings(self) -> ImportSettings:
        """Liest das global gespeicherte Datenimport-Standardformat aus
        QSettings -- jeder Wert einzeln (statt als zusammengesetztes Objekt),
        analog zu den uebrigen Einstellungen dieser App, mit Fallback auf den
        jeweiligen ImportSettings-Standardwert, falls (z.B. bei einem noch
        nie zuvor gespeicherten Wert oder einem Formatwechsel) ein Schluessel
        fehlt oder ungueltig ist."""
        s = self._settings
        defaults = ImportSettings()
        return ImportSettings(
            delimiter=str(s.value("import/delimiter", defaults.delimiter)),
            decimal_separator=str(s.value("import/decimal_separator", defaults.decimal_separator)),
            encoding=str(s.value("import/encoding", defaults.encoding)),
            skip_header_lines=int(s.value("import/skip_header_lines", defaults.skip_header_lines, type=int)),
            skip_footer_lines=int(s.value("import/skip_footer_lines", defaults.skip_footer_lines, type=int)),
            skip_leading_columns=int(
                s.value("import/skip_leading_columns", defaults.skip_leading_columns, type=int)
            ),
            skip_trailing_columns=int(
                s.value("import/skip_trailing_columns", defaults.skip_trailing_columns, type=int)
            ),
        )

    def _set_import_settings(self, settings: ImportSettings, persist: bool) -> None:
        """Uebernimmt settings fuer die aktuelle Sitzung -- bei persist=True
        zusaetzlich dauerhaft in QSettings gespeichert (analog zu
        _set_filename_template/ImportSettingsDialog.persist(): die Checkbox
        im Dialog ist standardmaessig AUS, gilt also nur fuer den jeweils
        aktuellen Ladevorgang, es sei denn der Nutzer haekt sie bewusst an)."""
        self._import_settings = settings
        if persist:
            s = self._settings
            s.setValue("import/delimiter", settings.delimiter)
            s.setValue("import/decimal_separator", settings.decimal_separator)
            s.setValue("import/encoding", settings.encoding)
            s.setValue("import/skip_header_lines", settings.skip_header_lines)
            s.setValue("import/skip_footer_lines", settings.skip_footer_lines)
            s.setValue("import/skip_leading_columns", settings.skip_leading_columns)
            s.setValue("import/skip_trailing_columns", settings.skip_trailing_columns)

    def _configure_import_settings(self) -> None:
        """Menüpunkt "Werkzeuge > Datenimport anpassen…": laesst den
        Datenimport-Manager unabhaengig von einem (fehlgeschlagenen)
        Ladevorgang oeffnen, z.B. um sich vorab auf eine kuenftige, noch
        unbekannte Messreihen-Quelle mit abweichendem Rohformat
        vorzubereiten. Braucht eine Beispieldatei zur Live-Vorschau --
        nutzt die erste Datei der aktuell geladenen Aufnahme, falls
        vorhanden, sonst fragt eine Dateiauswahl danach."""
        sample_path: Path | None = None
        if self.recording is not None and self.recording.paths:
            sample_path = self.recording.paths[0]
        else:
            path, _filter = QtWidgets.QFileDialog.getOpenFileName(
                self, "Beispieldatei für den Datenimport wählen", "", "CSV-Dateien (*.csv);;Alle Dateien (*)"
            )
            if not path:
                return
            sample_path = Path(path)

        dlg = ImportSettingsDialog(self, sample_path, self._import_settings)
        if dlg.exec() != QtWidgets.QDialog.DialogCode.Accepted:
            return
        self._set_import_settings(dlg.settings(), persist=dlg.persist())

    def _offer_import_settings_retry(self, error_message: str) -> bool:
        """Rueckfrage, wenn ein Ladevorgang komplett fehlgeschlagen ist
        (RecordingError aus load_paths, siehe _load_paths) -- bietet an, den
        Datenimport-Manager auf einer der betroffenen Dateien zu oeffnen und
        das Laden mit angepassten Einstellungen erneut zu versuchen, statt
        nur eine Fehlermeldung anzuzeigen. Analog zu _ask_filename_mismatch.
        Welche Datei genau geoeffnet wird (paths[0]), entscheidet ausschliesslich
        der Aufrufer (siehe project_io._load_paths) -- diese Methode ist reine
        Ja/Nein-Rueckfrage."""
        box = QtWidgets.QMessageBox(self)
        box.setIcon(QtWidgets.QMessageBox.Icon.Warning)
        box.setWindowTitle("Daten konnten nicht gelesen werden")
        box.setText(
            f"Die ausgewählten Dateien konnten nicht als Messreihe gelesen werden:\n\n{error_message}\n\n"
            f"Möglicherweise weicht das Rohformat vom erwarteten Format ab (z.B. andere Kopfzeilen, "
            f"anderes Trennzeichen). Datenimport anpassen und erneut versuchen?"
        )
        btn_adjust = box.addButton("Datenimport anpassen…", QtWidgets.QMessageBox.ButtonRole.ActionRole)
        box.addButton("Abbrechen", QtWidgets.QMessageBox.ButtonRole.RejectRole)
        box.setDefaultButton(btn_adjust)
        box.exec()
        return box.clickedButton() is btn_adjust

    def _check_for_new_files(self) -> None:
        """Wird alle 10s vom Live-Watch-Timer aufgerufen (siehe __init__):
        laedt neu im ueberwachten Ordner abgelegte CSV-Dateien nach, ohne die
        aktuelle Wiedergabeposition zu stoeren -- damit die App parallel zu
        einer laufenden Messung genutzt werden kann, ohne bei sehr haeufig
        neu abgelegten Dateien (z.B. alle 500ms) unbenutzbar zu werden (siehe
        fester 10s-Intervall statt eines Dateisystem-Watchers)."""
        if self.recording is None or self._watched_folder is None:
            return
        try:
            candidate_paths = sorted(self._watched_folder.glob("*.csv"))
        except OSError:
            return
        known = set(self.recording.paths)
        new_paths = [p for p in candidate_paths if p not in known]
        if not new_paths:
            return
        try:
            # _active_filename_pattern/-strptime_fmt/-import_settings (nicht
            # die evtl. abweichenden _filename_*/-_import_settings-
            # Standardwerte): muss zum Schema/Rohformat passen, mit dem DIESE
            # Aufnahme urspruenglich geladen wurde (siehe _load_paths), sonst
            # wuerden neu hinzukommende Dateien falsch/gar nicht bzw. gar
            # nicht mehr eingelesen.
            updated = append_paths(
                self.recording, new_paths,
                pattern=self._active_filename_pattern, strptime_fmt=self._active_filename_strptime_fmt,
                import_settings=self._active_import_settings,
            )
        except RecordingError:
            return
        if updated.n_frames != self.recording.n_frames:
            self._apply_appended_recording(updated)

    def _apply_appended_recording(self, updated: Recording) -> None:
        """Uebernimmt eine per Live-Ordner-Ueberwachung erweiterte Recording,
        OHNE (anders als _set_recording bei einem regulaeren Neuladen) die
        aktuelle Wiedergabeposition/Ansicht zu verwerfen. Folgt automatisch
        dem neuesten Frame nur, wenn die Anzeige zuvor bereits beim jeweils
        letzten Frame stand (typisches "live mitschauen")."""
        old_n = self.recording.n_frames
        was_at_latest = self.current_index >= old_n - 1
        # Auswertungsende folgt automatisch mit, wenn es zuvor ebenfalls
        # beim letzten Frame stand (analog zur Wiedergabeposition oben) --
        # wurde es bewusst frueher gesetzt, bleibt es unveraendert stehen.
        eval_end_was_at_latest = self._eval_end_index is None or self._eval_end_index >= old_n - 1
        added = updated.n_frames - old_n
        self.recording = updated
        n = updated.n_frames

        self._global_level_range = (
            (float(updated.frames.min()), float(updated.frames.max())) if n else None
        )

        self.frame_slider.blockSignals(True)
        self.frame_slider.setRange(0, max(0, n - 1))
        self.frame_slider.setValue(self.current_index)
        self.frame_slider.blockSignals(False)

        self.frame_spin.blockSignals(True)
        self.frame_spin.setRange(1, max(1, n))
        self.frame_spin.setValue(self.current_index + 1)
        self.frame_spin.blockSignals(False)

        self.spin_eval_start.blockSignals(True)
        self.spin_eval_start.setRange(1, max(1, n))
        self.spin_eval_start.setValue(max(1, (self._eval_start_index or 0) + 1))
        self.spin_eval_start.blockSignals(False)

        self.spin_eval_end.blockSignals(True)
        self.spin_eval_end.setRange(1, max(1, n))
        if eval_end_was_at_latest and n > 0:
            self._eval_end_index = n - 1
        self.spin_eval_end.setValue(max(1, (self._eval_end_index or 0) + 1))
        self.spin_eval_end.blockSignals(False)
        self._update_timeline_markers()

        symbol = "o" if n <= MAX_FRAMES_WITH_SYMBOLS else None
        for entry in self.roi_entries:
            # Nur die Obergrenze mitwachsen lassen -- ein bereits vom Nutzer
            # gewaehltes Start-/Ende-Zielbild fuer die Interpolation bleibt
            # beim Nachladen (Live-Ordner-Ueberwachung) unveraendert stehen.
            entry.spin_interp_start_frame.setRange(1, max(1, n))
            entry.spin_interp_end_frame.setRange(1, max(1, n))
            entry.curve.setSymbol(symbol)
        self.live_curve.setSymbol(symbol)
        self.timeseries_live_curve.setSymbol(symbol)

        self._recompute_curves()

        if was_at_latest and n > 0:
            self.current_index = n - 1
            self._show_frame(self.current_index)
            self.frame_slider.blockSignals(True)
            self.frame_slider.setValue(self.current_index)
            self.frame_slider.blockSignals(False)
            self.frame_spin.blockSignals(True)
            self.frame_spin.setValue(self.current_index + 1)
            self.frame_spin.blockSignals(False)

        self.statusBar().showMessage(
            f"Live-Überwachung: {added} neue(s) Frame(s) geladen ({n} insgesamt)."
        )

