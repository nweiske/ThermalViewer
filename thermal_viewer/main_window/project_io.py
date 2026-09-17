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
from .shrinkage_ops import _SHRINKAGE_METRIC_LABELS


def _shrink_box_data(roi) -> dict:
    """Position/Groesse einer Schwindungsmessungs-Box (roi_shrink_*, siehe
    shrinkage_ops.py) als JSON-faehiges dict fuer _save_project -- eigene
    Modul-Funktion statt in _save_project verschachtelt, damit sie wie
    _parse_interp_point unabhaengig lesbar/testbar bleibt."""
    x, y = roi.pos()
    w, h = roi.size()
    return {"x": float(x), "y": float(y), "breite_px": float(w), "hoehe_px": float(h)}


def _apply_shrink_box_data(roi, box, rows: int, cols: int) -> None:
    """Gegenstueck zu _shrink_box_data fuer _load_project -- setzt Position/
    Groesse aus einem geladenen box-dict, auf die aktuelle Bildgroesse
    geklemmt (siehe size_mismatch-Behandlung weiter oben in _load_project).
    Ungueltige/fehlende Werte werden stillschweigend uebersprungen (die Box
    behaelt dann ihre bisherige Position), wie bei anderen tolerant
    geparsten Projektdatei-Feldern in dieser Datei."""
    if not isinstance(box, dict):
        return
    try:
        x, y = float(box["x"]), float(box["y"])
        w, h = float(box["breite_px"]), float(box["hoehe_px"])
    except (KeyError, TypeError, ValueError):
        return
    w = max(1.0, min(w, cols))
    h = max(1.0, min(h, rows))
    x = max(0.0, min(x, cols - w))
    y = max(0.0, min(y, rows - h))
    roi.setPos((x, y), update=False)
    roi.setSize((w, h))


class _ProjectMixin:
    def _confirm_discard_current_recording(self) -> bool:
        """Rueckfrage vor dem Laden einer WEITEREN Messreihe, waehrend
        bereits eine andere geladen ist -- siehe _reset_state_for_new_
        recording (frame_nav.py): Messbereiche, Messungen, Maßstab und die
        Rohdaten-Bereinigung der aktuellen Auswertung wuerden dabei
        vollstaendig verworfen. Nutzerwunsch: vorher fragen, ob der aktuelle
        Stand als Projekt gespeichert werden soll, statt das kommentarlos
        geschehen zu lassen. Gibt zurueck, ob der Ladevorgang fortgesetzt
        werden soll (True bei "Verwerfen" oder erfolgreichem "Speichern",
        False bei "Abbrechen" oder abgebrochenem Speichern-Dialog)."""
        if self.recording is None:
            return True
        box = QtWidgets.QMessageBox(self)
        box.setIcon(QtWidgets.QMessageBox.Icon.Warning)
        box.setWindowTitle("Aktuelle Auswertung verwerfen?")
        box.setText(
            "Es ist bereits eine Messreihe geladen. Messbereiche, Messungen, Maßstab und die "
            "Rohdaten-Bereinigung dieser Auswertung gehen beim Laden einer neuen Messreihe "
            "vollständig verloren, wenn sie nicht vorher als Projekt gespeichert werden.\n\n"
            "Wie möchtest du fortfahren?"
        )
        btn_save = box.addButton("Speichern & fortfahren…", QtWidgets.QMessageBox.ButtonRole.ActionRole)
        btn_discard = box.addButton("Verwerfen", QtWidgets.QMessageBox.ButtonRole.DestructiveRole)
        btn_cancel = box.addButton("Abbrechen", QtWidgets.QMessageBox.ButtonRole.RejectRole)
        # Bewusst "Abbrechen" als Default (statt z.B. "Verwerfen"): ein
        # versehentliches Enter/Leertaste soll nicht die destruktive Aktion
        # ausloesen.
        box.setDefaultButton(btn_cancel)
        box.exec()
        clicked = box.clickedButton()
        if clicked is btn_discard:
            return True
        if clicked is btn_save:
            return self._save_project()
        return False

    def _save_project(self) -> bool:
        if self.recording is None:
            QtWidgets.QMessageBox.information(self, "Keine Daten", "Bitte zuerst eine Messreihe laden.")
            return False

        path, _ = QtWidgets.QFileDialog.getSaveFileName(
            self, "Projekt speichern", "Projekt.tvproj", "Projekt-Datei (*.tvproj)"
        )
        if not path:
            return False
        if not Path(path).suffix:
            path += ".tvproj"

        data = self._build_project_state_dict()

        try:
            Path(path).write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
        except OSError as exc:
            QtWidgets.QMessageBox.critical(self, "Fehler", f"Projekt konnte nicht gespeichert werden:\n{exc}")
            return False

        self.statusBar().showMessage(f"Projekt gespeichert: {path}")
        return True

    def _build_project_state_dict(self) -> dict:
        """Baut das komplette Zustands-dict, wie es "Projekt speichern…"
        als JSON schreibt -- reine Konstruktion, KEIN Dialog/Datei-I/O,
        setzt self.recording is not None voraus. Ausgelagert aus
        _save_project(), damit dieselbe dict-Form auch fuer einen In-
        Memory-Snapshot (Undo/Redo, siehe undo_ops.py) wiederverwendet
        werden kann, ohne ueber eine echte Datei zu gehen."""
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
                "statistik": entry.stat_mode,
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
            # Rohdaten-Bereinigung (siehe data_cleaning_ops.py) -- Nutzerwunsch:
            # "wenn ich ein Projekt speichere/lade [möchte ich] wirklich den
            # VOLLSTÄNDIGEN Zustand des Programmes haben". Referenzpunkte sind
            # Pixelkoordinaten (x=col, y=row, gesetzt in der Bildvorschau des
            # Bereinigungs-Dialogs); "logic" gilt PRO Punkt, "aktiv" haelt die
            # Deaktivieren-Checkbox pro Punkt fest.
            "bereinigung_punkte": [
                {"x": x, "y": y, "logic": logic, "aktiv": enabled}
                for x, y, logic, enabled in self._cleaning_points
            ],
            "bereinigung_schwellenwert": self._cleaning_threshold,
            "bereinigung_kernel_groesse": self._cleaning_kernel_size,
            "bereinigung_ausgeblendete_frames": sorted(self._excluded_frame_indices),
            # Schwindungsmessung (siehe shrinkage_ops.py) -- wie bei der
            # Rohdaten-Bereinigung Nutzerwunsch "vollstaendiger Programm-
            # zustand". Das berechnete ERGEBNIS selbst (areas_px/rect_
            # widths_px/round_widths_px) wird bewusst NICHT gespeichert
            # (abgeleitete Daten, wie die ROI-Kurven auch) -- beim Laden
            # wird es aus den hier gespeicherten Eingaben neu berechnet
            # (siehe _load_project_shrinkage). KEIN Schwellenwert/keine
            # "wärmer/kälter"-Auswahl/kein Messart-Modus mehr (siehe
            # shrinkage_ops.py-Moduldocstring) -- die Probe wird immer mit
            # EINER Box vormarkiert, "kenngroesse" waehlt nur nachtraeglich
            # aus, was daraus berechnet/angezeigt wird. "box_flaeche"/
            # "box_flaeche_farbe" tragen bewusst denselben Schluesselnamen
            # wie vor diesem Umbau -- alte Projektdateien laden ihre
            # Boxposition/-farbe dadurch weiterhin korrekt.
            "schwindung": {
                "aktiviert": self._shrinkage_enabled,
                "kenngroesse": self._shrinkage_metric,
                "box_flaeche": _shrink_box_data(self.roi_shrink_area),
                "box_flaeche_farbe": self._shrinkage_color_area,
                # Neu (Nutzerwunsch: Kontur-Farbe waehlbar statt fest Rot) --
                # fehlt bei alten Projektdateien, _load_project_shrinkage
                # behaelt dann den aktuellen/Standard-Wert.
                "kontur_farbe": self._shrinkage_color_contour,
            },
        }
        return data

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

        # Ab hier auf mehrere kleine, je fuer sich lesbare/testbare Schritte
        # aufgeteilt (dieselbe Reihenfolge wie zuvor als ein einziger,
        # sehr langer Methodenkoerper) -- failed_indices/measurement_errors
        # werden fuer die Abschluss-Meldung ganz unten zurueckgereicht.
        if not self._load_project_resolve_recording(data):
            return
        self._load_project_show_mismatch_warnings(data)
        self._load_project_display_settings(data)
        self._load_project_eval_range(data)
        failed_indices = self._load_project_rois(data)
        measurement_errors = self._load_project_measurements(data)
        self._load_project_cleaning(data)
        self._load_project_shrinkage(data)
        # Ebenen-Tabs (layer_tabs_ops.py): Messbereiche koennen waehrend des
        # Ladens neu platziert worden sein (entry.place() setzt dabei seine
        # eigene Sichtbarkeit OHNE Kenntnis der aktuell aktiven Ebene) --
        # hier einmal zentral neu anwenden, damit z.B. ein waehrend des
        # Ladens auf "Schwindungsmessung" stehender Tab nicht ploetzlich
        # auch Messbereiche zeigt.
        self._apply_layer_tab_visibility()
        # Ein per Datei-Dialog geladenes Projekt ersetzt den Zustand komplett
        # (nicht ueber undo_ops.py::_restore_project_state_dict, das den
        # Undo-Stack selbst nicht anfasst) -- eine History darueber hinweg
        # waere sinnlos/irrefuehrend (siehe undo_ops.py).
        self._clear_undo_history()

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

    def _restore_project_state_dict(self, data: dict) -> None:
        """Wendet einen In-Memory-Zustands-Snapshot (dieselbe Form wie
        _build_project_state_dict()/eine echte .tvproj-Datei) direkt auf
        die laufende UI an -- fuer Undo/Redo (siehe undo_ops.py). Ruft
        bewusst NUR die dialogfreien Sub-Methoden von _load_project() auf
        (nicht _load_project_resolve_recording/_load_project_show_mismatch_
        warnings, die Datei-Dialoge/Warnungen zeigen koennen) -- ein Undo/
        Redo betrifft immer dieselbe, bereits geladene Aufnahme, nie einen
        Wechsel der Aufnahme selbst. _load_project_shrinkage bekommt
        recompute=False: die Schwindungs-Neuberechnung kann laut deren
        Moduldocstring mehrere Sekunden dauern, was bei jedem einzelnen
        Rueckgaengig/Wiederholen voellig unangemessen waere."""
        self._load_project_display_settings(data)
        self._load_project_eval_range(data)
        self._load_project_rois(data, full_replace=True)
        self._load_project_measurements(data)
        self._load_project_cleaning(data)
        self._load_project_shrinkage(data, recompute=False)
        self._apply_layer_tab_visibility()

    def _load_project_resolve_recording(self, data: dict) -> bool:
        """Sorgt dafuer, dass beim Laden eines Projekts eine Messreihe
        geladen ist -- laedt bei Bedarf automatisch den gespeicherten
        Quellordner nach bzw. fragt einmalig manuell danach (Bugreport:
        "Keine Daten"-Fehler zwang bisher dazu, ERST manuell den Ordner zu
        laden). Gibt False zurueck, wenn am Ende immer noch keine Messreihe
        geladen ist -- _load_project bricht dann komplett ab."""
        if self.recording is not None:
            return True
        saved_folder = data.get("quellordner")
        saved_folder_exists = isinstance(saved_folder, str) and Path(saved_folder).is_dir()
        if saved_folder_exists and self._load_folder(Path(saved_folder)):
            return True
        # Zwei unterschiedliche Gruende sauber unterscheiden -- sonst
        # behauptet die Meldung faelschlich "nicht gefunden", obwohl der
        # Ordner existiert, aber z.B. keine zum Namensschema passenden
        # Dateien enthaelt oder der Namensschema-Abgleich abgebrochen wurde.
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
        return bool(folder) and self._load_folder(Path(folder))

    def _load_project_show_mismatch_warnings(self, data: dict) -> None:
        """Warnt, falls das Projekt fuer eine andere Bildaufloesung bzw.
        einen anderen Quellordner gespeichert wurde als die aktuell geladene
        Messreihe -- rein informativ, aendert nichts am weiteren Laden."""
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

    def _load_project_display_settings(self, data: dict) -> None:
        """Farbverlauf, Pegel-Modus, Maßstab (px-zu-mm) und Referenzlinien-
        Farbe aus der Projektdatei uebernehmen. Hinweis: "grafik_theme" aus
        alten Projektdateien (vor dem einheitlichen Dunkelmodus-Umschalter)
        wird bewusst ignoriert -- die Grafik-Darstellung folgt jetzt immer
        dem aktuellen App-Design."""
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

    def _load_project_eval_range(self, data: dict) -> None:
        """Auswertungsstart/-ende aus der Projektdatei uebernehmen."""
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

    def _load_project_rois(self, data: dict, full_replace: bool = False) -> list[int]:
        """Baut die Messbereiche (beliebig viele ROIs) aus der Projektdatei
        auf -- bestehende Eintraege werden ueber ihre (0-basiert
        gespeicherte) Erzeugungsnummer wiedergefunden bzw. bei Bedarf neu
        angelegt. Gibt die 0-basierten Indizes fehlerhafter/uebersprungener
        Eintraege zurueck (fuer die Abschluss-Meldung in _load_project).

        full_replace=True (NUR vom Undo/Redo-Pfad, siehe undo_ops.py/
        _restore_project_state_dict) schaltet zwei Dinge zusaetzlich an --
        beides waere fuer ein normal per "Projekt laden…" geoeffnetes
        .tvproj falsch/riskant, fuer einen Undo/Redo-Snapshot INNERHALB
        derselben Sitzung aber notwendig:
        1. Eine Nummer eines FRUEHER bereits entfernten ROIs wird nicht mehr
           uebersprungen, sondern neu angelegt -- ein Redo von "ROI
           hinzugefuegt" bzw. ein Undo von "ROI entfernt" MUSS genau so ein
           ROI wiederherstellen, dessen Nummer bereits kleiner als
           self._roi_next_number ist. Fuer ein fremdes/altes .tvproj wäre
           das Wiederbeleben eines bewusst geloeschten ROIs dagegen falsch.
        2. Jedes lebende ROI, das im eingehenden data KEINE Entsprechung
           mehr hat, wird entfernt (mirror von _load_project_measurements,
           das bestehende Messungen VORHER komplett raeumt) -- ein Undo-
           Snapshot ist immer eine VOLLSTAENDIGE Zustands-Kopie, ein
           fehlendes ROI bedeutet dort zweifelsfrei "existierte zu diesem
           Zeitpunkt nicht". Ein normal geladenes .tvproj ist dagegen nicht
           zwingend vollstaendig (z.B. ein bewusst kleineres/aelteres
           Projekt) -- vorhandene ROIs, die es nicht erwaehnt, bleiben dort
           unangetastet stehen, wie schon vor diesem Parameter."""
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
                if target_number < self._roi_next_number and not full_replace:
                    continue
                if target_number < self._roi_next_number:
                    # full_replace: die Nummer wurde frueher schon einmal
                    # vergeben (sonst waere sie nicht < _roi_next_number) --
                    # ein neues ROI MIT genau dieser Nummer anlegen (OHNE
                    # self._roi_next_number zu aendern), statt der unteren
                    # while-Schleife, die nur fuer noch nie vergebene
                    # (>= _roi_next_number) Nummern gedacht ist.
                    entry = self._create_roi_entry_with_number(target_number)
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

                stat_mode = roi_data.get("statistik", "mean")
                combo_idx = entry.combo_stat_mode.findData(stat_mode)
                entry.combo_stat_mode.blockSignals(True)
                entry.combo_stat_mode.setCurrentIndex(max(0, combo_idx))
                entry.combo_stat_mode.blockSignals(False)
                entry.stat_mode = stat_mode if combo_idx >= 0 else "mean"

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

        if full_replace:
            # Siehe Docstring Punkt 2 -- NUR fuer den Undo/Redo-Pfad: ein
            # ROI ohne Entsprechung im (dort immer vollstaendigen) Snapshot
            # wird entfernt, mirror von _load_project_measurements.
            referenced_numbers = {
                roi_data["index"] + 1
                for roi_data in data.get("rois", [])
                if (
                    isinstance(roi_data, dict) and isinstance(roi_data.get("index"), int)
                    and roi_data.get("index") >= 0
                )
            }
            for entry in list(self.roi_entries):
                if entry.number not in referenced_numbers:
                    self._remove_roi_entry(entry)

        if touched_entries:
            self._recompute_curves(entries=touched_entries)
        self._apply_interp_focus_visuals()
        return failed_indices

    def _load_project_measurements(self, data: dict) -> int:
        """Baut die Messungen (Maßstab-Werkzeug) aus der Projektdatei auf --
        alte Eintraege werden verworfen, aus der Datei neu erzeugt (keine
        Ordnungsnummer-Zuordnung wie bei ROIs noetig, da es keine feste
        Anzahl vorab angelegter Standard-Messungen gibt), gedeckelt durch
        MAX_MEASUREMENT_COUNT (Schutz wie MAX_ROI_COUNT vor einer riesigen/
        manipulierten Liste). Gibt die Anzahl uebersprungener/fehlerhafter
        Eintraege zurueck (fuer die Abschluss-Meldung in _load_project)."""
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
        return measurement_errors

    def _load_project_cleaning(self, data: dict) -> None:
        """Rohdaten-Bereinigung (Referenzpunkte, Schwellenwert, Kernel-
        Groesse, UND/ODER-Logik, ausgeblendete Bilder) aus der Projektdatei
        uebernehmen -- Nutzerwunsch: vollstaendiger Programmzustand beim
        Speichern/Laden eines Projekts (siehe _save_project). Alte Punkte/
        Markierungen werden verworfen, aus der Datei neu aufgebaut."""
        points_data = data.get("bereinigung_punkte")
        # Migrations-Fallback fuer Projektdateien vor Punkt 2 (Nutzerwunsch:
        # UND/ODER war frueher GLOBAL statt pro Punkt) -- fehlt "logic" an
        # einem Punkt, gilt ersatzweise der alte globale Wert der Datei
        # (falls vorhanden), sonst "and". Fehlendes "aktiv" (Dateien vor der
        # Deaktivieren-Checkbox) defaultet auf True.
        legacy_logic = data.get("bereinigung_logik")
        legacy_default_logic = legacy_logic if legacy_logic in ("and", "or") else "and"
        cleaning_points: list[tuple[int, int, str, bool]] = []
        if isinstance(points_data, list):
            for p in points_data:
                if not isinstance(p, dict):
                    continue
                try:
                    # int (nicht float): Referenzpunkte sind Pixel-Indizes --
                    # ein float-Wert wuerde _compute_cleaning_candidates()
                    # spaeter mit einem IndexError abstuerzen lassen (numpy
                    # erlaubt keine Float-Indizierung von frames[:, r, c]).
                    # int(float(...)) statt direktem int(...): akzeptiert
                    # sowohl "10" als auch "10.0"/"10.7" aus (z.B.
                    # handbearbeiteten) Projektdateien gleichermassen.
                    x, y = int(float(p["x"])), int(float(p["y"]))
                except (KeyError, TypeError, ValueError):
                    continue
                logic = p.get("logic")
                if logic not in ("and", "or"):
                    logic = legacy_default_logic
                enabled = p.get("aktiv")
                if not isinstance(enabled, bool):
                    enabled = True
                cleaning_points.append((x, y, logic, enabled))
        self._cleaning_points = cleaning_points

        threshold = data.get("bereinigung_schwellenwert")
        if isinstance(threshold, (int, float)) and not isinstance(threshold, bool):
            self._cleaning_threshold = float(threshold)

        kernel_size = data.get("bereinigung_kernel_groesse")
        if (
            isinstance(kernel_size, int) and not isinstance(kernel_size, bool)
            and kernel_size in (1, 3, 5, 7, 9)
        ):
            self._cleaning_kernel_size = kernel_size

        excluded_data = data.get("bereinigung_ausgeblendete_frames")
        n_frames = self.recording.n_frames if self.recording is not None else 0
        if isinstance(excluded_data, list):
            loaded_excluded = {
                int(i) for i in excluded_data
                if isinstance(i, int) and not isinstance(i, bool) and 0 <= i < n_frames
            }
            if n_frames > 0 and len(loaded_excluded) >= n_frames:
                # Mindestens ein Bild muss sichtbar bleiben (siehe
                # _apply_cleaning_exclusions) -- eine (z.B. von Hand
                # bearbeitete) Projektdatei, die ALLE Bilder ausblendet,
                # wuerde sonst denselben inkonsistenten Zustand erzeugen wie
                # das direkte Anwenden ueber den Bereinigungs-Dialog.
                loaded_excluded.discard(max(loaded_excluded))
            self._excluded_frame_indices = loaded_excluded
        else:
            self._excluded_frame_indices = set()
        self._recompute_curves()
        # Punkt 5 (Nutzerwunsch): das aktuell angezeigte Bild (typischerweise
        # Bild 0, siehe _load_project_resolve_recording -> _set_recording ->
        # _show_frame(0), das bereits VOR dieser Methode lief) kann durch die
        # gerade geladenen Ausschluesse jetzt selbst ausgeblendet sein --
        # _show_frame() korrigiert das zentral (siehe frame_nav.py), ein
        # blosses Setzen von _excluded_frame_indices oben rendert aber nichts
        # neu, daher hier explizit erneut aufrufen.
        if self.current_index in self._excluded_frame_indices:
            self._show_frame(self.current_index)

        if self._cleaning_dialog is not None:
            self._cleaning_dialog.spin_threshold.blockSignals(True)
            self._cleaning_dialog.spin_threshold.setValue(self._cleaning_threshold)
            self._cleaning_dialog.spin_threshold.blockSignals(False)
            self._cleaning_dialog.sync_kernel_size_combo()
            self._cleaning_dialog.refresh_points()

    def _load_project_shrinkage(self, data: dict, recompute: bool = True) -> None:
        """Schwindungsmessung (Box, Kenngroesse, Aktivieren-Haken) aus der
        Projektdatei uebernehmen -- Nutzerwunsch: vollstaendiger Programm-
        zustand beim Speichern/Laden eines Projekts (siehe _save_project).
        Kein Schwellenwert/keine "wärmer/kälter"-Auswahl/kein Messart-Modus
        mehr zu laden (siehe shrinkage_ops.py-Moduldocstring) -- die Probe
        wird immer mit EINER Box vormarkiert, alles andere automatisch
        erkannt. recompute=False (Undo/Redo, siehe undo_ops.py) ueberspringt
        die Neuberechnung am Ende -- die kann bei aktivierter Messung laut
        shrinkage_ops.py-Moduldocstring mehrere SEKUNDEN dauern, waere also
        bei jedem einzelnen Rueckgaengig/Wiederholen voellig unangemessen."""
        shrink_data = data.get("schwindung")
        if isinstance(shrink_data, dict) and self.recording is not None:
            self._shrinkage_result = None
            self.lbl_shrinkage_result.setText("Noch nicht berechnet.")
            self.lbl_shrinkage_result.setToolTip("")
            self.shrinkage_curve.clear()
            rows, cols = self.recording.shape

            _apply_shrink_box_data(self.roi_shrink_area, shrink_data.get("box_flaeche"), rows, cols)

            color = shrink_data.get("box_flaeche_farbe")
            if isinstance(color, str) and QtGui.QColor(color).isValid():
                self._shrinkage_color_area = color
            self._apply_shrinkage_box_colors()

            contour_color = shrink_data.get("kontur_farbe")
            if isinstance(contour_color, str) and QtGui.QColor(contour_color).isValid():
                self._shrinkage_color_contour = contour_color
            self._apply_shrinkage_contour_color()

            # Migration: aeltere Projektdateien (vor der Vereinheitlichung
            # auf EINE Box + nachtraeglich waehlbare Kenngroesse) hatten
            # stattdessen "geometrie" ("rect"/"round") ohne "kenngroesse" --
            # daraus die naeheste neue Kenngroesse ableiten.
            metric = shrink_data.get("kenngroesse")
            if metric not in _SHRINKAGE_METRIC_LABELS:
                geometrie = shrink_data.get("geometrie")
                if geometrie == "round":
                    metric = "breite_rund"
                elif geometrie == "rect":
                    metric = "breite_rechteckig"
                else:
                    metric = "flaeche"
            self._shrinkage_metric = metric
            combo_idx = self.combo_shrinkage_metric.findData(metric)
            self.combo_shrinkage_metric.blockSignals(True)
            self.combo_shrinkage_metric.setCurrentIndex(max(0, combo_idx))
            self.combo_shrinkage_metric.blockSignals(False)

            enabled = bool(shrink_data.get("aktiviert", False))
            self._shrinkage_enabled = enabled
            self.chk_shrinkage_enabled.blockSignals(True)
            self.chk_shrinkage_enabled.setChecked(enabled)
            self.chk_shrinkage_enabled.blockSignals(False)
            self._set_shrinkage_controls_enabled(enabled)
            self._apply_shrinkage_roi_visibility()

            # Das fruehere Ergebnis wird NICHT als Rohwerte gespeichert (nur
            # die Eingaben oben) -- wird stattdessen aus den geladenen
            # Einstellungen neu berechnet, statt nach dem Laden leer/
            # veraltet dazustehen (ausser beim Undo/Redo-Wiederherstellen,
            # siehe recompute-Parameter oben).
            if recompute:
                self._compute_shrinkage()

        self._update_shrinkage_curve()
        self._update_status_bar()

    def _load_paths(
        self, paths: list[Path], pattern: re.Pattern | None = None, strptime_fmt: str | None = None,
        *, defer_display: bool = False,
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
        if not self._confirm_discard_current_recording():
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

            progress_cb = _LoadProgressReporter(
                progress,
                extra_cb=lambda done, total: self._set_activity_progress(
                    f"Lade Frames… ({done}/{total})", done / total if total else None
                ),
            )
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
            self._refresh_idle_guidance()

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
        self._set_recording(recording, defer_display=defer_display)
        return True

