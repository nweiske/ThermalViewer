"""Speichern/Laden von .tvproj-Projektdateien."""
from __future__ import annotations

import json
import re
from pathlib import Path

from qtpy import QtCore, QtGui, QtWidgets

from ..data import (
    RecordingError,
    load_paths,
)
from ..dialogs import (
    ImportSettingsDialog,
)
from ..measurement import MAX_MEASUREMENT_COUNT
from ..plot_items import (
    _LoadProgressReporter,
)
from ..roi_entry import (
    DEFAULT_ROI_SIZE,
    MAX_ROI_COUNT,
    RoiEntry,
)


class _ProjectMixin:
    def _save_project(self) -> None:
        if self.recording is None:
            QtWidgets.QMessageBox.information(self, "Keine Daten", "Bitte zuerst eine Messreihe laden.")
            return

        path, _ = QtWidgets.QFileDialog.getSaveFileName(
            self, "Projekt speichern", "Projekt.tvproj", "Projekt-Datei (*.tvproj)"
        )
        if not path:
            return
        if not Path(path).suffix:
            path += ".tvproj"

        rois = []
        for entry in self.roi_entries:
            roi_data: dict = {
                # 0-basiert gespeichert (entry.number ist 1-basiert) fuer
                # Kompatibilitaet mit vor "beliebig viele ROIs" gespeicherten
                # Projektdateien; beim Laden wird ueber diese Nummer (nicht
                # eine reine Listen-Position) das passende ROI gefunden bzw.
                # bei Bedarf neu angelegt (siehe _load_project).
                "index": entry.number - 1,
                "name": entry.name,
                "farbe": entry.color,
                "sichtbar": entry.is_visible_checked(),
                "platziert": entry.placed,
                "interpolation_aktiv": entry.interp_enabled,
                "temperatur_anzeigen": entry.show_temperature,
                "kreisfoermig": entry.roi.is_circular,
            }
            if entry.placed:
                cx, cy = entry.center()
                roi_data["mittelpunkt"] = {"x": cx, "y": cy}
                roi_data["breite_px"] = entry.width()
                roi_data["hoehe_px"] = entry.height()
            if entry.interp_start is not None:
                (sx, sy), (sw, sh) = entry.interp_start
                roi_data["interpolation_start"] = {
                    "x": sx, "y": sy, "breite_px": sw, "hoehe_px": sh, "frame": entry.interp_start_frame,
                }
            if entry.interp_end is not None:
                (ex, ey), (ew, eh) = entry.interp_end
                roi_data["interpolation_ende"] = {
                    "x": ex, "y": ey, "breite_px": ew, "hoehe_px": eh, "frame": entry.interp_end_frame,
                }
            rois.append(roi_data)

        measurements = []
        for entry in self.measurements:
            p1, p2 = entry.endpoints()
            offset = entry.label_offset
            measurements.append({
                "name": entry.name,
                "farbe": entry.color,
                "start": {"x": p1.x(), "y": p1.y()},
                "ende": {"x": p2.x(), "y": p2.y()},
                "beschriftung_versatz": {"x": offset.x(), "y": offset.y()} if offset is not None else None,
            })

        rows, cols = self.recording.shape
        data = {
            "format_version": 2,
            "quellordner": str(self.recording.paths[0].parent) if self.recording.paths else None,
            "bild_groesse_px": {"zeilen": rows, "spalten": cols},
            "colormap_index": self.combo_cmap.currentIndex(),
            "colormap_invertiert": self.chk_cmap_invert.isChecked(),
            "level_mode": self._level_mode(),
            "level_min": self.spin_level_min.value(),
            "level_max": self.spin_level_max.value(),
            "px_zu_mm": self._px_to_mm,
            "massstab_farbe": self._ruler_color,
            "auswertungsstart_frame": self._eval_start_index,
            "auswertungsende_frame": self._eval_end_index,
            "rois": rois,
            "messungen": measurements,
        }

        try:
            Path(path).write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
        except OSError as exc:
            QtWidgets.QMessageBox.critical(self, "Fehler", f"Projekt konnte nicht gespeichert werden:\n{exc}")
            return

        self.statusBar().showMessage(f"Projekt gespeichert: {path}")

    @staticmethod
    def _parse_interp_point(
        data,
    ) -> tuple[tuple[float, float], tuple[float, float], int | None] | None:
        """Parst einen Interpolations-Keyframe ("interpolation_start"/"_ende")
        aus einer Projektdatei, inkl. optionalem Frame-Index ("frame") --
        Projektdateien von vor der Frame-Index-basierten Interpolation
        (siehe RoiEntry.interp_start_frame) haben dieses Feld nicht; der
        Aufrufer setzt in dem Fall einen sinnvollen Standard (erster/letzter
        Frame -- exakt das fruehere, zeitstempel-unabhaengige Verhalten von
        "Start"/"Ende festlegen"). Wirft TypeError/ValueError/KeyError bei
        fehlerhaften/fehlenden Pflichtwerten, statt sie stillschweigend zu
        uebernehmen -- der Aufrufer faengt das gezielt ab."""
        if data is None:
            return None
        if not isinstance(data, dict):
            raise TypeError("interpolation point must be a dict")
        x = float(data["x"])
        y = float(data["y"])
        w = float(data["breite_px"])
        h = float(data["hoehe_px"])
        frame = data.get("frame")
        frame_idx = int(frame) if isinstance(frame, int) and not isinstance(frame, bool) else None
        return (x, y), (w, h), frame_idx

    def _load_project(self) -> None:
        path, _ = QtWidgets.QFileDialog.getOpenFileName(
            self, "Projekt laden", "", "Projekt-Datei (*.tvproj)"
        )
        if not path:
            return
        try:
            data = json.loads(Path(path).read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            QtWidgets.QMessageBox.critical(self, "Fehler", f"Projekt konnte nicht geladen werden:\n{exc}")
            return

        if self.recording is None:
            # Moeglichst wenige Klicks, um ein Projekt OHNE bereits geladene
            # Messreihe zu oeffnen (Bugreport: "Keine Daten"-Fehler zwang
            # dazu, ERST manuell den Ordner zu laden): der im Projekt
            # gespeicherte Quellordner (siehe _save_project) wird dafuer
            # automatisch nachgeladen, sofern er noch existiert -- nur wenn
            # das nicht klappt, muss der Ordner einmalig manuell gewaehlt
            # werden.
            saved_folder = data.get("quellordner")
            saved_folder_exists = isinstance(saved_folder, str) and Path(saved_folder).is_dir()
            loaded = saved_folder_exists and self._load_folder(Path(saved_folder))
            if not loaded:
                # Zwei unterschiedliche Gruende sauber unterscheiden --
                # sonst behauptet die Meldung faelschlich "nicht gefunden",
                # obwohl der Ordner existiert, aber z.B. keine zum
                # Namensschema passenden Dateien enthaelt oder der
                # Namensschema-Abgleich abgebrochen wurde.
                if saved_folder and not saved_folder_exists:
                    hint = f" (gespeicherter Ordner „{saved_folder}“ nicht gefunden)"
                elif saved_folder:
                    hint = f" (Laden von „{saved_folder}“ nicht erfolgreich)"
                else:
                    hint = ""
                QtWidgets.QMessageBox.information(
                    self,
                    "Messreihe wählen",
                    f"Für dieses Projekt ist noch keine Messreihe geladen{hint}. "
                    "Bitte jetzt den passenden Ordner auswählen.",
                )
                folder = QtWidgets.QFileDialog.getExistingDirectory(self, "Ordner mit CSV-Messreihe wählen")
                if not folder or not self._load_folder(Path(folder)):
                    return

        saved_folder = data.get("quellordner")
        current_folder = str(self.recording.paths[0].parent) if self.recording.paths else None
        saved_size = data.get("bild_groesse_px")
        current_rows, current_cols = self.recording.shape
        size_mismatch = (
            isinstance(saved_size, dict)
            and (saved_size.get("zeilen"), saved_size.get("spalten")) != (current_rows, current_cols)
        )
        folder_mismatch = bool(saved_folder and current_folder and saved_folder != current_folder)
        if size_mismatch:
            QtWidgets.QMessageBox.warning(
                self,
                "Andere Bildauflösung",
                "Dieses Projekt wurde für eine Messreihe mit anderer Bildauflösung gespeichert "
                f"({saved_size.get('spalten')}x{saved_size.get('zeilen')} statt aktuell "
                f"{current_cols}x{current_rows}). Messbereich-Koordinaten außerhalb des Bildes "
                "wurden auf den Bildrand begrenzt – bitte Position/Größe der Messbereiche prüfen.",
            )
        elif folder_mismatch:
            QtWidgets.QMessageBox.information(
                self,
                "Anderer Quellordner",
                "Dieses Projekt wurde für eine andere Messreihe gespeichert:\n"
                f"{saved_folder}\n\n"
                "Es wird trotzdem auf die aktuell geladene Messreihe angewendet – bitte "
                "Messbereiche danach kurz prüfen.",
            )

        cmap_index = data.get("colormap_index")
        if isinstance(cmap_index, int) and 0 <= cmap_index < self.combo_cmap.count():
            self.combo_cmap.setCurrentIndex(cmap_index)
        self.chk_cmap_invert.setChecked(bool(data.get("colormap_invertiert", False)))

        level_mode = data.get("level_mode")
        if level_mode not in ("manual", "per_frame", "global"):
            # Abwaertskompatibilitaet zu Projektdateien von vor Punkt 1
            # (einfaches Bool "auto_levels" statt drei Modi).
            level_mode = "per_frame" if data.get("auto_levels", True) else "manual"
        self._set_level_mode(level_mode)
        if level_mode == "manual":
            self.spin_level_min.setValue(data.get("level_min", self.spin_level_min.value()))
            self.spin_level_max.setValue(data.get("level_max", self.spin_level_max.value()))

        px_to_mm = data.get("px_zu_mm")
        self._px_to_mm = float(px_to_mm) if isinstance(px_to_mm, (int, float)) else None

        ruler_color = data.get("massstab_farbe")
        if isinstance(ruler_color, str) and QtGui.QColor(ruler_color).isValid():
            self._ruler_color = ruler_color
            self._update_ruler_color_swatch()
            self._apply_ruler_color()

        self._refresh_scale_label()

        # Hinweis: "grafik_theme" aus alten Projektdateien (vor dem
        # einheitlichen Dunkelmodus-Umschalter) wird bewusst ignoriert -- die
        # Grafik-Darstellung folgt jetzt immer dem aktuellen App-Design.

        eval_start = data.get("auswertungsstart_frame")
        if isinstance(eval_start, int) and self.recording is not None and 0 <= eval_start < self.recording.n_frames:
            self._eval_start_index = eval_start
            self.spin_eval_start.blockSignals(True)
            self.spin_eval_start.setValue(eval_start + 1)
            self.spin_eval_start.blockSignals(False)

        eval_end = data.get("auswertungsende_frame")
        if isinstance(eval_end, int) and self.recording is not None and 0 <= eval_end < self.recording.n_frames:
            self._eval_end_index = eval_end
            self.spin_eval_end.blockSignals(True)
            self.spin_eval_end.setValue(eval_end + 1)
            self.spin_eval_end.blockSignals(False)

        if (isinstance(eval_start, int) or isinstance(eval_end, int)) and self.recording is not None:
            # Falls Start > Ende in der Datei stand (z.B. handbearbeitet):
            # Ende gewinnt, Start wird passend nachgezogen -- konsistent mit
            # der Live-Klemmung in _on_eval_start_changed.
            if (
                self._eval_start_index is not None
                and self._eval_end_index is not None
                and self._eval_start_index > self._eval_end_index
            ):
                self._eval_start_index = self._eval_end_index
                self.spin_eval_start.blockSignals(True)
                self.spin_eval_start.setValue(self._eval_start_index + 1)
                self.spin_eval_start.blockSignals(False)
            self._update_timeline_markers()

        touched_entries: list[RoiEntry] = []
        failed_indices: list[int] = []
        for roi_data in data.get("rois", []):
            if not isinstance(roi_data, dict):
                continue
            idx = roi_data.get("index")
            if not isinstance(idx, int) or idx < 0:
                continue
            if idx >= MAX_ROI_COUNT:
                # Verhindert, dass eine manipulierte/beschaedigte .tvproj-
                # Datei mit einer riesigen "index"-Zahl versucht, ebenso
                # viele ROI-Eintraege auf einmal anzulegen (siehe
                # MAX_ROI_COUNT) -- als fehlerhaft behandelt wie jeder
                # andere ungueltige ROI-Eintrag.
                failed_indices.append(idx)
                continue
            # Ueber die (0-basiert gespeicherte) Erzeugungsnummer statt einer
            # reinen Listen-Position zuordnen: bei "beliebig viele ROIs"
            # koennen Messbereiche entfernt worden sein, wodurch sich
            # Positionen verschieben. Existiert die Nummer noch nicht (z.B.
            # Projekt mit mehr ROIs als aktuell vorhanden), werden bei Bedarf
            # neue Messbereiche angelegt; eine Nummer eines FRUEHER bereits
            # entfernten ROIs wird dagegen uebersprungen (nicht rekonstruierbar).
            target_number = idx + 1
            entry = next((e for e in self.roi_entries if e.number == target_number), None)
            if entry is None:
                if target_number < self._roi_next_number:
                    continue
                while self._roi_next_number <= target_number:
                    entry = self._add_roi_entry()
            entry_failed = False

            # Grundangaben + Platzierung in einem eigenen try-Block: ein
            # spaeter fehlschlagender Interpolations-Block (siehe unten) darf
            # eine hier bereits erfolgreiche Platzierung nicht mehr rueckgaengig
            # machen bzw. von der Kurven-Neuberechnung ausschliessen.
            try:
                name = roi_data.get("name")
                if name:
                    entry.list_item.setText(name)

                color = roi_data.get("farbe")
                if color:
                    entry.set_color(color)

                entry.list_item.setCheckState(
                    QtCore.Qt.CheckState.Checked
                    if roi_data.get("sichtbar", True)
                    else QtCore.Qt.CheckState.Unchecked
                )

                entry.chk_show_temperature.setChecked(bool(roi_data.get("temperatur_anzeigen", True)))
                entry.chk_circular.setChecked(bool(roi_data.get("kreisfoermig", False)))

                mittelpunkt = roi_data.get("mittelpunkt")
                if roi_data.get("platziert") and isinstance(mittelpunkt, dict):
                    width = roi_data.get("breite_px")
                    height = roi_data.get("hoehe_px")
                    if width is None or height is None:
                        # Altes Projektformat (Punkt 2): eine einzelne
                        # "groesse" statt getrennter Breite/Hoehe.
                        width = height = roi_data.get("groesse", DEFAULT_ROI_SIZE)
                    cx = float(mittelpunkt.get("x", 0.0))
                    cy = float(mittelpunkt.get("y", 0.0))
                    width = float(width)
                    height = float(height)
                    # _set_widget_value (blockSignals) statt direktem .setValue():
                    # die vier Felder sind seit der Live-Uebernahme-Umstellung
                    # (siehe spin.valueChanged weiter oben) mit _on_roi_apply_clicked
                    # verbunden -- ohne Blockade wuerde JEDER der vier setValue()-
                    # Aufrufe hier bereits selbst einen (mangels der jeweils noch
                    # nicht gesetzten uebrigen drei Werte unvollstaendigen)
                    # Platzierungs-/Kurven-Neuberechnungs-Durchlauf ausloesen, statt
                    # dass -- wie beabsichtigt -- erst das anschliessende entry.place()
                    # unten mit den vollstaendigen Werten einmalig greift.
                    for spin, value in (
                        (entry.spin_x, cx), (entry.spin_y, cy),
                        (entry.spin_width, width), (entry.spin_height, height),
                    ):
                        self._set_widget_value(spin, value)
                    entry.place(
                        entry.spin_x.value(), entry.spin_y.value(),
                        entry.spin_width.value(), entry.spin_height.value(),
                    )
                    self._sync_roi_spinboxes(entry)
            except (TypeError, ValueError):
                # Ein einzelner fehlerhafter ROI-Eintrag (z.B. handbearbeitete
                # oder beschaedigte .tvproj-Datei) soll nicht verhindern, dass
                # die uebrigen, gueltigen Eintraege trotzdem angewendet werden.
                entry_failed = True

            # Verlaufs-Interpolation separat parsen/validieren: bei fehlerhaften
            # Werten wird der Interpolationszustand des ROI explizit auf "aus"
            # zurueckgesetzt, statt (mit evtl. nicht-numerischen Werten) stehen
            # zu bleiben -- sonst wuerde derselbe fehlerhafte Wert beim naechsten
            # Frame-Wechsel (_show_frame -> apply_interp_frame) ungefangen
            # erneut auftreten und die Wiedergabe abstuerzen lassen.
            # _reset_interp_arm_state() unbedingt VOR dem Ueberschreiben von
            # interp_start/interp_end aufrufen: chk_interp.setChecked() loest
            # _on_roi_interp_toggled() (das sonst zuruecksetzt) nur aus, wenn
            # sich der Haken-Zustand tatsaechlich aendert -- ein Start-/Ende-
            # Button, der gerade auf "Position uebernehmen" stand, wuerde
            # sonst beim naechsten Klick die frisch geladenen Werte sofort
            # wieder mit der aktuellen ROI-Position ueberschreiben.
            self._reset_interp_arm_state(entry)
            try:
                parsed_start = self._parse_interp_point(roi_data.get("interpolation_start"))
                parsed_end = self._parse_interp_point(roi_data.get("interpolation_ende"))
                n_frames = self.recording.n_frames if self.recording is not None else 0
                if parsed_start is not None:
                    (sx, sy), (sw, sh), sframe = parsed_start
                    entry.interp_start = ((sx, sy), (sw, sh))
                    # Alte Projektdateien (vor Frame-Index-basierter
                    # Interpolation) haben kein "frame"-Feld -- Standard war
                    # damals immer der erste Frame (siehe frueheres
                    # _step_frame(-self.current_index) bei "Start festlegen").
                    entry.interp_start_frame = sframe if sframe is not None else 0
                    # Zahlenfeld "Erstes Frame:" (1-basiert) synchron halten
                    # -- sonst zeigt es weiterhin den alten/Default-Wert,
                    # waehrend ein erneutes "Start festlegen" bereits zum
                    # (falschen) Zahlenfeld-Wert springt und den frisch
                    # geladenen Keyframe beim naechsten Klick ueberschreibt.
                    self._set_widget_value(entry.spin_interp_start_frame, entry.interp_start_frame + 1)
                else:
                    entry.interp_start = None
                    entry.interp_start_frame = None
                if parsed_end is not None:
                    (ex, ey), (ew, eh), eframe = parsed_end
                    entry.interp_end = ((ex, ey), (ew, eh))
                    entry.interp_end_frame = eframe if eframe is not None else max(0, n_frames - 1)
                    self._set_widget_value(entry.spin_interp_end_frame, entry.interp_end_frame + 1)
                else:
                    entry.interp_end = None
                    entry.interp_end_frame = None
                entry.chk_interp.setChecked(
                    bool(roi_data.get("interpolation_aktiv", False))
                    and entry.interp_start is not None
                    and entry.interp_end is not None
                )
                # chk_interp.setChecked() loest _on_roi_interp_toggled() (das
                # den Hinweis bereits aktualisiert) nur aus, wenn sich der
                # Haken-Zustand TATSAECHLICH aendert -- bleibt er gleich,
                # waehrend sich nur die (per _set_widget_value signalfrei
                # gesetzten) Start-/Ende-Frames aendern, explizit nachholen.
                self._refresh_interp_range_warning(entry, True)
                self._refresh_interp_range_warning(entry, False)
            except (TypeError, ValueError, KeyError):
                entry.interp_start = None
                entry.interp_end = None
                entry.interp_start_frame = None
                entry.interp_end_frame = None
                entry.chk_interp.blockSignals(True)
                entry.chk_interp.setChecked(False)
                entry.chk_interp.blockSignals(False)
                entry.interp_enabled = False
                entry.btn_interp_start.setEnabled(False)
                entry.btn_interp_end.setEnabled(False)
                self._refresh_interp_range_warning(entry, True)
                self._refresh_interp_range_warning(entry, False)
                entry_failed = True

            if entry_failed:
                failed_indices.append(idx)
            touched_entries.append(entry)

        if touched_entries:
            self._recompute_curves(entries=touched_entries)
        self._apply_interp_focus_visuals()

        # -- Messungen (Punkt 8) -- alte Eintraege verwerfen, aus der Datei
        # neu aufbauen. Ohne Ordnungsnummer-Zuordnung wie bei ROIs (keine
        # feste Anzahl vorab angelegter Standard-Messungen) -- einfach in
        # Datei-Reihenfolge neu erzeugen, gedeckelt durch MAX_MEASUREMENT_COUNT
        # (Schutz wie MAX_ROI_COUNT vor einer riesigen/manipulierten Liste).
        for entry in list(self.measurements):
            self._remove_measurement(entry)
        measurement_errors = 0
        if self._px_to_mm is not None:
            for m_data in data.get("messungen", [])[:MAX_MEASUREMENT_COUNT]:
                if not isinstance(m_data, dict):
                    measurement_errors += 1
                    continue
                try:
                    sx, sy = float(m_data["start"]["x"]), float(m_data["start"]["y"])
                    ex, ey = float(m_data["ende"]["x"]), float(m_data["ende"]["y"])
                except (KeyError, TypeError, ValueError):
                    measurement_errors += 1
                    continue
                pixel_distance = ((ex - sx) ** 2 + (ey - sy) ** 2) ** 0.5
                if pixel_distance < 1e-6:
                    measurement_errors += 1
                    continue
                entry = self._create_measurement((sx, sy), (ex, ey), pixel_distance)
                name = m_data.get("name")
                if isinstance(name, str) and name.strip():
                    entry.name = name.strip()
                    entry.name_edit.blockSignals(True)
                    entry.name_edit.setText(entry.name)
                    entry.name_edit.blockSignals(False)
                color = m_data.get("farbe")
                if isinstance(color, str) and QtGui.QColor(color).isValid():
                    entry.set_color(color)
                offset = m_data.get("beschriftung_versatz")
                if isinstance(offset, dict):
                    try:
                        entry.label_offset = QtCore.QPointF(float(offset["x"]), float(offset["y"]))
                    except (KeyError, TypeError, ValueError):
                        pass
                entry.update_text_position()
        # Sonst (kein Maßstab (mehr) definiert): gespeicherte Messungen
        # waeren ohne px-zu-mm-Umrechnung bedeutungslos, werden also nicht
        # wiederhergestellt -- kein Fehlerfall, daher keine Warnung.

        message = f"Projekt geladen: {path}"
        if failed_indices:
            message += f"  |  {len(failed_indices)} Messbereich-Eintrag/Einträge übersprungen (fehlerhaft)."
            QtWidgets.QMessageBox.warning(
                self,
                "Fehlerhafte Messbereich-Einträge übersprungen",
                "Folgende Messbereich-Einträge in der Projektdatei waren fehlerhaft und wurden "
                "übersprungen: " + ", ".join(f"Messbereich {i + 1}" for i in failed_indices),
            )
        if measurement_errors:
            message += f"  |  {measurement_errors} Messung(en) übersprungen (fehlerhaft)."
        self.statusBar().showMessage(message)

    def _load_paths(
        self, paths: list[Path], pattern: re.Pattern | None = None, strptime_fmt: str | None = None
    ) -> bool:
        """pattern/strptime_fmt: optionales, nur fuer DIESEN Ladevorgang
        geltendes Namensschema (siehe _resolve_folder_and_pattern) -- ohne
        Angabe gilt das aktive Standard-Namensschema
        (self._filename_pattern/_filename_strptime_fmt).

        Gibt zurueck, ob das Laden erfolgreich war -- Aufrufer, die danach
        noch Folgezustand setzen (z.B. _open_folder mit der Live-Ordner-
        Ueberwachung), duerfen das NUR bei True tun, sonst wuerde bei einem
        Fehler (z.B. defekte CSVs) unbemerkt auf den falschen/neuen Ordner
        umgeschaltet, waehrend die vorherige Aufnahme weiter angezeigt bleibt."""
        if not paths:
            QtWidgets.QMessageBox.warning(self, "Keine Dateien", "Es wurden keine CSV-Dateien gefunden.")
            return False
        pattern = self._filename_pattern if pattern is None else pattern
        strptime_fmt = self._filename_strptime_fmt if strptime_fmt is None else strptime_fmt
        import_settings = self._import_settings

        # Schleife statt Einzelversuch: schlaegt das Laden komplett fehl
        # (z.B. weil das Rohformat vom erwarteten abweicht), bietet
        # _offer_import_settings_retry an, den Datenimport-Manager zu
        # oeffnen und mit angepassten Einstellungen erneut zu versuchen --
        # analog zur Namensschema-Rueckfrage in _resolve_folder_and_pattern.
        while True:
            progress = QtWidgets.QProgressDialog("Lade Frames…", "Abbrechen", 0, len(paths), self)
            progress.setWindowModality(QtCore.Qt.WindowModal)
            progress.setMinimumDuration(300)

            progress_cb = _LoadProgressReporter(progress)
            try:
                # progress_cb() pumpt hier wiederholt processEvents() -- siehe
                # _paused_background_timers zum Grund, warum Live-Watch/
                # Wiedergabe dafuer pausiert sein muessen.
                with self._paused_background_timers():
                    recording = load_paths(
                        paths, progress_cb=progress_cb, pattern=pattern, strptime_fmt=strptime_fmt,
                        import_settings=import_settings,
                    )
                error: RecordingError | None = None
            except RecordingError as exc:
                recording = None
                error = exc
            progress.close()

            if error is None:
                break
            if self._offer_import_settings_retry(str(error)):
                dlg = ImportSettingsDialog(self, paths[0], import_settings, is_retry=True)
                if dlg.exec() == QtWidgets.QDialog.DialogCode.Accepted:
                    new_settings = dlg.settings()
                    if dlg.persist():
                        self._set_import_settings(new_settings, persist=True)
                    import_settings = new_settings
                    continue
            QtWidgets.QMessageBox.critical(self, "Fehler beim Laden", str(error))
            return False

        # Merken, mit welchem Namensschema/Datenimport-Format DIESE Aufnahme
        # tatsaechlich geladen wurde -- siehe _check_for_new_files
        # (Live-Ordner-Ueberwachung muss konsistent dasselbe Schema/Format
        # weiterverwenden, auch wenn es vom aktuellen Standard abweicht).
        self._active_filename_pattern = pattern
        self._active_filename_strptime_fmt = strptime_fmt
        self._active_import_settings = import_settings
        self._set_recording(recording)
        return True

