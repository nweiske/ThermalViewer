"""Werte-Export (CSV/JSON/Text)."""
from __future__ import annotations

import csv
import json
from pathlib import Path

from qtpy import QtWidgets

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
        if not placed_entries and not live_available:
            QtWidgets.QMessageBox.information(
                self,
                "Keine Daten",
                "Es ist weder ein Messbereich platziert noch ein Live-Cursor-Pixel gewählt "
                "(Maus über das Bild bewegen oder eine Stelle fixieren).",
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
        if live_available:
            k = float(self._live_cursor_kernel_size)
            k_mm = k * self._px_to_mm if self._px_to_mm is not None else None
            dialog_entries.append({
                "name": "Live (Cursor)",
                "width_px": k,
                "height_px": k,
                "width_mm": k_mm,
                "height_mm": k_mm,
            })
        runtime_column_labels = {"hhmmss": "HH:MM:SS", "s": "s", "min": "min", "h": "h"}
        runtime_header = f"Laufzeit ({runtime_column_labels[self._runtime_unit]})"
        reserved_names = ["Zeitstempel", runtime_header]
        if live_available:
            reserved_names.extend(["Live X-Achse", "Live Y-Achse"])
        # Lokaler statt Modul-Import: siehe Kommentar in ui_build.py bei
        # AxisSettingsDialog.
        from . import CsvColumnDialog

        column_dialog = CsvColumnDialog(self, dialog_entries, self._settings, reserved_names)
        if column_dialog.exec() != QtWidgets.QDialog.DialogCode.Accepted:
            return
        included = column_dialog.included()
        names = column_dialog.column_names()
        export_format = column_dialog.format()
        include_extra_runtime = column_dialog.include_extra_runtime()
        extra_runtime_unit = column_dialog.extra_runtime_unit()
        extra_runtime_header = column_dialog.extra_runtime_header()

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
        # synthetischen Live-Cursor-Zeile) -- Index i identifiziert daher
        # eindeutig, ob Spalte i aus einem echten ROI oder dem Live-Cursor
        # stammt. Fuer den Live-Cursor kommen zusaetzlich seine (ueber die
        # gesamte Aufnahme konstante) Pixel-Koordinaten als eigene Spalten
        # dazu -- frueher nur im separaten "Live-Werte als CSV"-Export
        # enthalten, jetzt Teil desselben einen Export-Fensters.
        header = ["Zeitstempel", runtime_header]
        if include_extra_runtime:
            header.append(extra_runtime_header)
        value_arrays: list[tuple[int, object]] = []
        for i, (name, inc) in enumerate(zip(names, included, strict=True)):
            if not inc:
                continue
            is_live = i >= len(placed_entries)
            if is_live:
                header.extend(["Live X-Achse", "Live Y-Achse"])
            header.append(name)
            if is_live:
                y = self._live_cursor_series(self._hover_row, self._hover_col)
            else:
                _, y = placed_entries[i].curve.getData()
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
                if entry_idx >= len(placed_entries):
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
                records = [dict(zip(header, row, strict=True)) for row in rows]
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

