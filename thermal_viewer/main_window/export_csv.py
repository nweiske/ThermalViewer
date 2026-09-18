"""Werte-Export (CSV/JSON/Text)."""
from __future__ import annotations

import csv
import json
from pathlib import Path

import numpy as np
from qtpy import QtWidgets

from ..data import zip_strict
from ..plot_items import (
    _RUNTIME_UNIT_DIVISORS,
)


class _CsvExportMixin:
    def _export_csv(self) -> None:
        if self.recording is None:
            QtWidgets.QMessageBox.information(self, "Keine Daten", "Bitte zuerst eine Messreihe laden.")
            return
        placed_entries = [e for e in self.roi_entries if e.placed]
        live_available = self._hover_row is not None and self._hover_col is not None
        shrinkage_available = self._shrinkage_result is not None
        sample_height_entries = [e for e in self._sample_height_entries if e.widths_px is not None]
        if not placed_entries and not live_available and not shrinkage_available and not sample_height_entries:
            QtWidgets.QMessageBox.information(
                self,
                "Keine Daten",
                "Es ist weder ein Messbereich platziert noch ein Live-Cursor-Pixel gewählt "
                "(Maus über das Bild bewegen oder eine Stelle fixieren) noch eine "
                "Schwindungsmessung (Box oder Probenhöhe) berechnet.",
            )
            return

        dialog_entries = []
        for entry in placed_entries:
            w_mm = entry.width() * self._px_to_mm if self._px_to_mm is not None else None
            h_mm = entry.height() * self._px_to_mm if self._px_to_mm is not None else None
            dialog_entries.append({
                "name": entry.name,
                "width_px": entry.width(),
                "height_px": entry.height(),
                "width_mm": w_mm,
                "height_mm": h_mm,
            })
        # Nur noch EIN CSV-Export-Fenster (statt getrennter "Zeitverlauf-"/
        # "Live-Werte"-Menüpunkte, Nutzerwunsch): der Live-Cursor-Verlauf ist
        # -- sofern gerade verfuegbar -- als zusaetzliche, waehlbare Spalte
        # immer mit dabei, damit sich ROI- und Live-Daten in EINER Datei
        # exportieren lassen, statt zwingend zwei separate Exporte zu
        # benoetigen.
        live_index = None
        if live_available:
            live_index = len(dialog_entries)
            k = float(self._live_cursor_kernel_size)
            k_mm = k * self._px_to_mm if self._px_to_mm is not None else None
            dialog_entries.append({
                "name": "Live (Cursor)",
                "width_px": k,
                "height_px": k,
                "width_mm": k_mm,
                "height_mm": k_mm,
            })
        shrinkage_index = None
        shrinkage_is_area = False
        if shrinkage_available:
            shrinkage_index = len(dialog_entries)
            # width_px/height_px sind hier nur der informative Referenzwert
            # (erstes Bild der Messung), KEINE echte feste Box wie bei
            # ROIs/Live-Cursor -- der eigentliche Wert ist pro Bild
            # unterschiedlich (das ist ja gerade der Messwert). Name/Einheit
            # haengen von der aktuell gewaehlten Kenngroesse ab (siehe
            # shrinkage_ops.py). Der anfaengliche unit_suffix hier ist nur
            # ein Platzhalter -- CsvColumnDialog aktualisiert ihn sofort auf
            # den tatsaechlich gewaehlten Wert (Standard "%", siehe
            # shrinkage_row_index/_on_shrinkage_unit_changed dort), da die
            # Werteinheit (Nutzerwunsch: "%, mm, oder px exportieren
            # können, analog zur Laufzeitskala") dort per Dropdown gewaehlt
            # wird statt sich starr nach dem gesetzten Maßstab zu richten.
            is_area = self._shrinkage_metric_is_area()
            shrinkage_is_area = is_area
            key = self._shrinkage_metric_key()
            ref_value_px = float(self._shrinkage_result[key][0])
            scale = (self._px_to_mm ** 2 if is_area else self._px_to_mm) if self._px_to_mm is not None else None
            ref_value_mm = ref_value_px * scale if scale is not None else None
            dialog_entries.append({
                # Nutzerwunsch: nur "Schwindung", ohne die Kenngroesse in
                # Klammern (frueher z.B. "Schwindung (Breite (quaderförmig,
                # Median))") -- gilt sowohl fuer den festen Anzeige-Text als
                # auch fuer den editierbaren Spaltenname-Vorschlag, da
                # CsvColumnDialog beide direkt aus "name" ableitet (siehe
                # dort).
                "name": "Schwindung",
                "width_px": ref_value_px,
                "height_px": ref_value_px,
                "width_mm": ref_value_mm,
                "height_mm": ref_value_mm,
                "unit_suffix": "%",
            })
        # Probenhöhen (siehe sample_height_ops.py) -- eine Zeile je
        # berechneter Probenhöhe, GENAUSO %/mm/px-waehlbar wie die
        # Schwindungsmessung-Zeile oben (siehe sample_height_row_indices/
        # CsvColumnDialog.percent_unit), da beide dieselbe Art Messwert
        # (Breite ueber die Zeit) liefern -- nur eben pro benannter Zeile
        # statt einer einzigen Box.
        sample_height_indices: dict[int, object] = {}
        for entry in sample_height_entries:
            idx = len(dialog_entries)
            sample_height_indices[idx] = entry
            ref_value_px = float(entry.widths_px[0])
            ref_value_mm = ref_value_px * self._px_to_mm if self._px_to_mm is not None else None
            dialog_entries.append({
                "name": entry.name,
                "width_px": ref_value_px,
                "height_px": ref_value_px,
                "width_mm": ref_value_mm,
                "height_mm": ref_value_mm,
                "unit_suffix": "%",
            })
        runtime_column_labels = {"hhmmss": "HH:MM:SS", "s": "s", "min": "min", "h": "h"}
        runtime_header = f"Laufzeit ({runtime_column_labels[self._runtime_unit]})"
        reserved_names = ["Zeitstempel", runtime_header]
        if live_available:
            reserved_names.extend(["Live X-Achse", "Live Y-Achse"])
        # Lokaler statt Modul-Import: siehe Kommentar in ui_build.py bei
        # AxisSettingsDialog.
        from . import CsvColumnDialog

        column_dialog = CsvColumnDialog(
            self, dialog_entries, self._settings, reserved_names,
            shrinkage_row_index=shrinkage_index, shrinkage_is_area=shrinkage_is_area,
            sample_height_row_indices=list(sample_height_indices),
        )
        if column_dialog.exec() != QtWidgets.QDialog.DialogCode.Accepted:
            return
        included = column_dialog.included()
        names = column_dialog.column_names()
        export_format = column_dialog.format()
        include_extra_runtime = column_dialog.include_extra_runtime()
        extra_runtime_unit = column_dialog.extra_runtime_unit()
        extra_runtime_header = column_dialog.extra_runtime_header()
        shrinkage_value_unit = column_dialog.shrinkage_value_unit()

        # Format wird SCHON im Dialog gewaehlt (statt z.B. ueber den
        # Dateityp-Filter im Speichern-Dialog), damit Vorschlagsname/-endung
        # direkt dazu passen (Nutzerwunsch: CSV/JSON/Text statt nur CSV).
        format_info = {"csv": ("CSV-Datei (*.csv)", ".csv"), "json": ("JSON-Datei (*.json)", ".json"), "text": ("Text-Datei (*.txt)", ".txt")}
        filter_str, default_ext = format_info[export_format]
        default_name = f"Werte{default_ext}"
        default_path = str(Path(self._export_dir_hint()) / default_name) if self._export_dir_hint() else default_name
        path, _ = QtWidgets.QFileDialog.getSaveFileName(
            self, "Werte speichern", default_path, filter_str
        )
        if not path:
            return
        self._remember_export_dir(path)
        if not Path(path).suffix:
            path += default_ext

        t0 = self.recording.timestamps[0]
        # dialog_entries/names/included sind alle in derselben Reihenfolge
        # aufgebaut (echte Messbereiche zuerst, optional gefolgt von der
        # synthetischen Live-Cursor-Zeile, optional gefolgt von der Box-
        # Schwindungsmessung, optional gefolgt von je einer Zeile pro
        # berechneter Probenhöhe) -- live_index/shrinkage_index/
        # sample_height_indices identifizieren daher eindeutig, aus welcher
        # der vier Quellen Spalte i stammt.
        # Fuer den Live-Cursor kommen zusaetzlich seine (ueber die gesamte
        # Aufnahme konstante) Pixel-Koordinaten als eigene Spalten dazu --
        # frueher nur im separaten "Live-Werte als CSV"-Export enthalten,
        # jetzt Teil desselben einen Export-Fensters.
        header = ["Zeitstempel", runtime_header]
        if include_extra_runtime:
            header.append(extra_runtime_header)
        value_arrays: list[tuple[int, object]] = []
        for i, (name, inc) in enumerate(zip_strict(names, included)):
            if not inc:
                continue
            if i == live_index:
                header.extend(["Live X-Achse", "Live Y-Achse"])
                header.append(name)
                y = self._live_cursor_series(self._hover_row, self._hover_col)
            elif i == shrinkage_index:
                header.append(name)
                # Wie bei ROIs/Live-Cursor die VOLLE, ungekuerzte Reihe --
                # areas_px/rect_widths_px/round_widths_px sind bereits ueber
                # alle Frames (0..n-1) berechnet, das Ausblenden erledigt die
                # Zeilen-Schleife unten selbst.
                # Nutzerwunsch: Werteinheit im Export-Dialog waehlbar (%, mm,
                # px, analog zur Laufzeitskala, siehe CsvColumnDialog.
                # shrinkage_value_unit) statt starr an den gesetzten Maßstab
                # gekoppelt -- "percent" bildet GENAU dieselbe Formel wie der
                # Schwindungs-Graph selbst ab (siehe shrinkage_ops.py:
                # _update_shrinkage_curve), "mm"/"px" exportieren die
                # absoluten Werte der aktuell gewaehlten Kenngroesse.
                raw = self._shrinkage_result[self._shrinkage_metric_key()]
                if shrinkage_value_unit == "mm":
                    is_shrinkage_area = self._shrinkage_metric_is_area()
                    shrinkage_scale = (
                        (self._px_to_mm ** 2 if is_shrinkage_area else self._px_to_mm)
                        if self._px_to_mm is not None else 1.0
                    )
                    y = raw * shrinkage_scale
                elif shrinkage_value_unit == "px":
                    y = raw
                else:
                    first = float(raw[0])
                    y = (first - raw) / first * 100.0 if first else np.zeros_like(raw)
            elif i in sample_height_indices:
                header.append(name)
                # Dieselbe Werteinheit-Wahl wie bei der Schwindungsmessung-
                # Zeile oben, aber PRO Probenhöhe unabhängig waehlbar (siehe
                # CsvColumnDialog.percent_unit).
                sh_entry = sample_height_indices[i]
                raw = sh_entry.widths_px
                sh_unit = column_dialog.percent_unit(i)
                if sh_unit == "mm":
                    y = raw * self._px_to_mm if self._px_to_mm is not None else raw
                elif sh_unit == "px":
                    y = raw
                else:
                    first = float(raw[0])
                    y = (first - raw) / first * 100.0 if first else np.zeros_like(raw)
            else:
                header.append(name)
                # NICHT curve.getData(): die angezeigte Kurve laesst von der
                # Rohdaten-Bereinigung ausgeblendete Bilder bereits weg (siehe
                # _recompute_curves) und waere dadurch kuerzer als
                # self.recording.timestamps -- y[i] unten braucht die VOLLE,
                # ungekuerzte, per Frame-Index indizierbare Reihe (das
                # Ausblenden erledigt diese Schleife selbst, siehe "continue"
                # unten).
                y = self._roi_values_full(placed_entries[i])
            value_arrays.append((i, y))

        # Rohwerte EINMAL aufbauen (Zeitstempel/Laufzeit als Text, Live-
        # Position als Ganzzahl, Messwert als float) -- CSV/JSON/Text
        # unterscheiden sich danach nur noch in Trennzeichen bzw.
        # Zahlenformatierung, nicht in der Datenaufbereitung selbst. Bereits
        # hier auf 3 Nachkommastellen gerundet (wie _format_csv_number es
        # fuer CSV/Text ohnehin tut) -- sonst wuerde der JSON-Export (der
        # NICHT durch _format_csv_number laeuft) das Rundungsrauschen der
        # float32-Rohdaten ungerundet mit ausgeben (z.B. 20.200000762939453
        # statt 20.2).
        rows: list[list] = []
        for i, ts in enumerate(self.recording.timestamps):
            if i in self._excluded_frame_indices:
                # Von der Rohdaten-Bereinigung ausgeblendete Bilder (siehe
                # data_cleaning_ops.py) NICHT mit exportieren -- Zeitstempel
                # der uebrigen Zeilen bleiben unveraendert (kein Umnummerieren).
                continue
            elapsed = (ts - t0).total_seconds()
            runtime = self._runtime_export_value(elapsed)
            row: list = [ts.strftime("%Y-%m-%d %H:%M:%S"), runtime]
            if include_extra_runtime:
                # Unabhaengig von self._runtime_unit (der globalen Graph-
                # Einstellung) -- eigene, im Export-Dialog gewaehlte Einheit
                # (Punkt 2). Echte Zahl (kein komma-formatierter String),
                # damit auch der JSON-Export eine echte Zahl erhaelt (siehe
                # _runtime_export_value fuer denselben Grund).
                divisor = _RUNTIME_UNIT_DIVISORS[extra_runtime_unit]
                row.append(round(max(0.0, elapsed) / divisor, 3))
            for entry_idx, y in value_arrays:
                if entry_idx == live_index:
                    row.extend([self._hover_col, self._hover_row])
                row.append(round(float(y[i]), 3))
            rows.append(row)

        try:
            if export_format == "json":
                # Echte Zahlen mit Dezimalpunkt (JSON-Standard, locale-
                # unabhaengig) statt der komma-formatierten Text-Darstellung von
                # CSV/Text -- konsistent von jedem JSON-Parser lesbar.
                #
                # dict(zip(header, row)) setzt voraus, dass "header" keine
                # doppelten Eintraege enthaelt -- sonst wuerde eine Spalte
                # stillschweigend eine andere ueberschreiben und deren Werte
                # gingen verloren. CsvColumnDialog._on_accept lehnt doppelte
                # Spaltennamen bereits vor dem Schliessen des Dialogs ab
                # (siehe dort), diese Annahme ist also hier bereits erfuellt.
                records = [dict(zip_strict(header, row)) for row in rows]
                Path(path).write_text(json.dumps(records, indent=2, ensure_ascii=False), encoding="utf-8")
            else:
                # CSV (';') und Text (Tabulator) unterscheiden sich nur im
                # Trennzeichen -- beide nutzen wie die Rohdaten Dezimalkomma.
                delimiter = ";" if export_format == "csv" else "\t"
                with open(path, "w", newline="", encoding="utf-8-sig") as f:
                    writer = csv.writer(f, delimiter=delimiter)
                    writer.writerow(header)
                    for row in rows:
                        writer.writerow(
                            [self._format_csv_number(v) if isinstance(v, float) else v for v in row]
                        )
        except OSError as exc:
            QtWidgets.QMessageBox.critical(self, "Fehler", f"Konnte Werte nicht speichern:\n{exc}")
            return

        self.statusBar().showMessage(f"Werte gespeichert: {path}")

