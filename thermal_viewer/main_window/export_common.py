"""Gemeinsame, eher zustandslose Export-Hilfsfunktionen (Zielordner-Merken,
Fehleranzeige, Laufzeit-/Zahlen-Formatierung, Farbskala-Zustand erfassen/
anwenden, Zeitstempel-Auflösung, Zeitverlauf-Metadaten). Die eher
zustandsbehafteten Context-Manager rund um Export-Renderpfade (Stiftbreiten/
Legende skalieren, Maßstab/Live-Cursor ein-/ausblenden, UI einfrieren,
Zeitachsen für SVG umbauen) stehen in export_visuals.py."""
from __future__ import annotations

import traceback
from datetime import datetime
from pathlib import Path

from qtpy import QtWidgets

from ..data import (
    render_filename_template,
)
from ..dialogs import (
    StartTimestampDialog,
)
from ..plot_items import (
    _RUNTIME_UNIT_DIVISORS,
)


class _ExportCommonMixin:
    def _export_dir_hint(self) -> str:
        """Zuletzt verwendeter Export-Zielordner -- gemeinsam ueber ALLE
        Export-Wege hinweg (Grafik/Werte/Video/Bildstapel, Punkt 4:
        "dass sich das Programm dann den Zielordner merkt"), damit der
        Datei-Dialog nicht immer wieder im Standardordner startet. Leerer
        String, falls noch nie exportiert wurde -- QFileDialog interpretiert
        das wie "kein Vorschlag" und faellt auf sein eigenes Standardverhalten
        zurueck."""
        return self._settings.value("export/last_dir", "", type=str)

    def _remember_export_dir(self, path) -> None:
        """Merkt sich den Ordner von path (Elternordner bei einem Datei-Pfad,
        sonst der Ordner selbst) fuer _export_dir_hint(). Wird direkt NACH
        der Dialog-Rueckgabe aufgerufen -- auch wenn der eigentliche
        Schreibvorgang danach fehlschlaegt, merkt sich ein normaler
        Datei-Dialog ja ebenfalls den zuletzt gewaehlten Ordner, unabhaengig
        vom Ausgang."""
        p = Path(path)
        folder = p if p.is_dir() else p.parent
        self._settings.setValue("export/last_dir", str(folder))

    def _show_export_error(self, what: str, exc: Exception) -> None:
        """Zeigt einen Export-Fehler an -- MIT vollstaendigem Traceback unter
        "Details anzeigen…" statt nur str(exc). Die verschiedenen Export-Wege
        fangen bewusst BREIT (Exception statt nur OSError, siehe die
        jeweiligen Aufrufer): ein unerwarteter Zustand im mehrteiligen
        Renderpfad (Farbskala/Achsen-Ueberschreibung, dynamische Widget-
        Groessen) kann auch andere Exception-Typen werfen, deren blosse
        str()-Darstellung (z.B. "unsupported operand type(s) for -: 'NoneType'
        and 'int'") keinerlei Hinweis auf Datei/Zeile gibt -- ohne Traceback
        praktisch unmöglich zu diagnostizieren (Bugreport, bei dem genau das
        der Fall war). Der volle Traceback bleibt eingeklappt, um Nutzer nicht
        mit technischem Text zu ueberfordern, ist aber sofort verfuegbar, falls
        der Fehler gemeldet werden soll."""
        box = QtWidgets.QMessageBox(self)
        box.setIcon(QtWidgets.QMessageBox.Icon.Critical)
        box.setWindowTitle("Fehler")
        box.setText(f"{what}:\n{exc}")
        box.setDetailedText(traceback.format_exc())
        box.exec()

    @staticmethod
    def _format_de(value: float, decimals: int = 1) -> str:
        return f"{value:.{decimals}f}".replace(".", ",")

    @classmethod
    def _format_csv_number(cls, value: float) -> str:
        # Deutsches Zahlenformat (Dezimalkomma), passend zum ';'-Trennzeichen
        # und zum Format der eingelesenen CSV-Rohdaten -- damit die Datei in
        # einem deutsch lokalisierten Excel ohne Nacharbeit direkt aufgeht.
        return cls._format_de(value, 3)

    @staticmethod
    def _format_relative_runtime(seconds: float) -> str:
        # Relative Laufzeit ab 00:00:00 (Punkt 6); Stunden bewusst unbegrenzt
        # (nicht auf 24h umbrechend), da Aufnahmen laenger als einen Tag
        # dauern koennen.
        total = int(round(max(0.0, seconds)))
        hours, rem = divmod(total, 3600)
        minutes, secs = divmod(rem, 60)
        return f"{hours:02d}:{minutes:02d}:{secs:02d}"

    def _format_runtime(self, seconds: float) -> str:
        """Formatiert eine Laufzeit (Sekunden seit Aufnahmebeginn) gemaess
        dem aktuell gewaehlten Laufzeit-Format (siehe _apply_runtime_unit)
        -- hh:mm:ss (Standard) oder eine fortlaufende Dezimalzahl in
        Sekunden/Minuten/Stunden. Gemeinsam genutzt von Statuszeile,
        Video-/Bildstapel-Export-Overlay UND CSV-Export ("Laufzeit"-Spalte),
        damit die Laufzeit ueberall im Programm im selben, vom Nutzer
        gewaehlten Format erscheint (Nutzerwunsch: "dritte Zeitachse")."""
        if self._runtime_unit == "hhmmss":
            return self._format_relative_runtime(seconds)
        divisor = _RUNTIME_UNIT_DIVISORS[self._runtime_unit]
        decimals = 2 if self._runtime_unit == "s" else 3
        return self._format_de(max(0.0, seconds) / divisor, decimals)

    def _runtime_export_value(self, seconds: float) -> str | float:
        """Laufzeit-Wert fuer die "Laufzeit"-Tabellenspalte des Werte-Exports
        (_export_csv): bei hh:mm:ss zwangsläufig ein String, sonst eine
        ECHTE Zahl (nicht wie _format_runtime() ein komma-formatierter
        String) -- Zeitstempel/Messwerte landen im Zeilen-Array ebenfalls
        unformatiert und werden erst beim eigentlichen Schreiben je nach
        Format aufbereitet (_format_csv_number fuer CSV/Text, direkt fuer
        JSON). Ohne diese Trennung wuerde der JSON-Export bei numerischem
        Laufzeit-Format einen komma-formatierten String statt einer echten
        JSON-Zahl enthalten -- genau das, was diese "dritte Zeitachse"
        (Nutzerwunsch) fuer die Weiterverarbeitung in anderer Software
        vermeiden soll."""
        if self._runtime_unit == "hhmmss":
            return self._format_relative_runtime(seconds)
        divisor = _RUNTIME_UNIT_DIVISORS[self._runtime_unit]
        decimals = 2 if self._runtime_unit == "s" else 3
        return round(max(0.0, seconds) / divisor, decimals)

    def _capture_level_widgets_state(self) -> dict:
        """Schnappschuss von Farbverlauf/Skalierung, um ihn nach einem
        temporaeren Overrride (z.B. eigene Video-Export-Einstellungen)
        symmetrisch per _apply_level_widgets_state wiederherzustellen."""
        return {
            "cmap_index": self.combo_cmap.currentIndex(),
            "invert": self.chk_cmap_invert.isChecked(),
            "level_mode": self._level_mode(),
            "level_min": self.spin_level_min.value(),
            "level_max": self.spin_level_max.value(),
        }

    def _apply_level_widgets_state(self, state: dict) -> None:
        self._set_widget_value(self.combo_cmap, state["cmap_index"], "setCurrentIndex")
        self._set_widget_value(self.chk_cmap_invert, state["invert"], "setChecked")
        self._apply_colormap()

        # Bugfix: Min/Max MUESSEN vor _set_level_mode() gesetzt werden --
        # _set_level_mode() loest ueber _on_level_mode_changed() sofort ein
        # _show_frame() aus, das im manuellen Modus die AKTUELLEN Werte von
        # spin_level_min/max fuer die Anzeige liest. Wurden diese erst DANACH
        # gesetzt, zeigte der erste Repaint (und ein direkt anschliessender
        # Bild-/SVG-Export ohne weiteren _show_frame()-Aufruf) noch die alten
        # Grenzwerte statt der gerade uebergebenen. Beim Video-Export blieb
        # das bisher unbemerkt, weil dort ohnehin direkt danach erneut
        # _show_frame() aufgerufen wird.
        self._set_widget_value(self.spin_level_min, state["level_min"])
        self._set_widget_value(self.spin_level_max, state["level_max"])
        self._set_level_mode(state["level_mode"])

    def _apply_custom_color_dialog_state(self, dialog, prev_level_state: dict) -> None:
        """Uebernimmt die "Eigene Einstellungen"-Farbskala/Skalierung eines
        Export-Dialogs (GraphicExportDialog/VideoExportDialog -- beide bieten
        dieselben custom_colormap_index()/custom_invert()/custom_level_mode()/
        custom_min_max()-Methoden) in die aktuelle Anzeige. Gemeinsam genutzt
        von _export_single_graph/_export_combined_image/_export_video, statt
        denselben 7-zeiligen Block an drei Stellen zu wiederholen -- nur
        aufzurufen, wenn der Dialog "Eigene Einstellungen" gewaehlt hat.

        prev_level_state (von _capture_level_widgets_state(), VOR dem
        Override erfasst): bei "Automatisch" (pro Bild/gesamte Serie) spielt
        das Dialog-Min/Max keine Rolle (wird pro Frame ueberschrieben) --
        dafuer wird stattdessen der bisherige Anzeige-Wert uebernommen, damit
        _apply_level_widgets_state() ein vollstaendiges dict bekommt."""
        mode = dialog.custom_level_mode()
        custom_min, custom_max = dialog.custom_min_max()
        self._apply_level_widgets_state({
            "cmap_index": dialog.custom_colormap_index(),
            "invert": dialog.custom_invert(),
            "level_mode": mode,
            "level_min": custom_min if mode == "manual" else prev_level_state["level_min"],
            "level_max": custom_max if mode == "manual" else prev_level_state["level_max"],
        })

    def _recording_has_real_timestamps(self) -> bool:
        """Ob JEDE Datei der aktuell geladenen Aufnahme ihren Zeitstempel
        tatsaechlich aus dem Dateinamen bezieht (aktives Namensschema
        passt), statt auf den bedeutungslosen Datei-Aenderungszeit-Fallback
        von parse_timestamp() zurueckzufallen (siehe data.py) -- relevant
        fuer _resolve_export_timestamps(), wenn der Bildstapel-Export-
        Praefix Zeitstempel-Platzhalter enthaelt."""
        if self.recording is None or not self.recording.paths:
            return False
        return all(self._active_filename_pattern.search(p.stem) for p in self.recording.paths)

    def _resolve_export_timestamps(self, image_prefix: str) -> list[datetime] | None:
        """Liefert die je Frame fuer render_filename_template() im
        Bildstapel-Export zu verwendenden Zeitstempel. Im Normalfall
        einfach self.recording.timestamps -- nur wenn image_prefix
        UEBERHAUPT Zeitstempel-Platzhalter enthaelt UND diese nicht echt
        aus den Dateinamen stammen (siehe _recording_has_real_timestamps),
        fragt diese Methode nach, ob stattdessen das aktuelle Systemdatum
        oder ein selbst gewaehlter Startpunkt verwendet werden soll (relative
        Abstaende zwischen den Frames bleiben dabei erhalten). Gibt None
        zurueck, wenn der Nutzer abgebrochen hat -- der Aufrufer muss den
        Export dann seinerseits abbrechen."""
        # Zwei unterschiedliche Test-Zeitstempel durchrendern: identisches
        # Ergebnis bedeutet, dass image_prefix ueberhaupt keine Zeitstempel-
        # Platzhalter enthaelt (reiner Literaltext) -- dann ist die Frage nach
        # einem "sinnvollen" Zeitstempel gegenstandslos.
        probe_a = render_filename_template(image_prefix, datetime(2020, 1, 1))
        probe_b = render_filename_template(image_prefix, datetime(2021, 6, 15, 12, 30, 45))
        if probe_a == probe_b or self._recording_has_real_timestamps():
            return list(self.recording.timestamps)

        box = QtWidgets.QMessageBox(self)
        box.setIcon(QtWidgets.QMessageBox.Icon.Warning)
        box.setWindowTitle("Kein echter Zeitstempel bekannt")
        box.setText(
            "Der Dateiname-Präfix enthält Zeitstempel-Platzhalter (YYYY/MM/DD/hh/mm/ss), aber die "
            "geladenen Dateien haben keinen aus dem Dateinamen erkennbaren Zeitstempel (Namensschema "
            "passt nicht) -- ohne Angabe würde nur die zufällige Datei-Änderungszeit verwendet. "
            "Wie möchtest du fortfahren?"
        )
        btn_now = box.addButton("Aktuelles Systemdatum verwenden", QtWidgets.QMessageBox.ButtonRole.ActionRole)
        btn_custom = box.addButton("Eigenen Startpunkt festlegen…", QtWidgets.QMessageBox.ButtonRole.ActionRole)
        box.addButton("Abbrechen", QtWidgets.QMessageBox.ButtonRole.RejectRole)
        box.setDefaultButton(btn_custom)
        box.exec()
        clicked = box.clickedButton()
        if clicked is btn_now:
            start = datetime.now()
        elif clicked is btn_custom:
            dt_dialog = StartTimestampDialog(self, datetime.now())
            if dt_dialog.exec() != QtWidgets.QDialog.DialogCode.Accepted:
                return None
            start = dt_dialog.value()
        else:
            return None

        t0 = self.recording.timestamps[0]
        return [start + (ts - t0) for ts in self.recording.timestamps]

    def _timeseries_metadata(self) -> dict:
        rows, cols = self.recording.shape
        rois = []
        for entry in self.roi_entries:
            roi_info: dict = {
                "index": entry.number,
                "name": entry.name,
                "farbe": entry.color,
                "sichtbar": entry.is_visible_checked(),
                "platziert": entry.placed,
                "interpolation_aktiv": entry.interp_enabled,
            }
            if entry.placed:
                cx, cy = entry.center()
                row0, row1, col0, col1 = entry.bounds_px(self.recording.shape)
                roi_info["mittelpunkt_px"] = {"x": cx, "y": cy}
                roi_info["breite_px"] = entry.width()
                roi_info["hoehe_px"] = entry.height()
                if self._px_to_mm is not None:
                    roi_info["breite_mm"] = entry.width() * self._px_to_mm
                    roi_info["hoehe_mm"] = entry.height() * self._px_to_mm
                roi_info["grenzen_px"] = {
                    "zeile_von": row0,
                    "zeile_bis": row1,
                    "spalte_von": col0,
                    "spalte_bis": col1,
                }
            rois.append(roi_info)

        cursor = None
        if self._hover_row is not None and self._hover_col is not None:
            cursor = {"zeile": self._hover_row, "spalte": self._hover_col}

        return {
            "quellordner": str(self.recording.paths[0].parent) if self.recording.paths else None,
            "anzahl_frames": self.recording.n_frames,
            "bild_groesse_px": {"zeilen": rows, "spalten": cols},
            "zeitstempel": [t.isoformat() for t in self.recording.timestamps],
            "px_zu_mm": self._px_to_mm,
            "rois": rois,
            "live_cursor_pixel": cursor,
        }
