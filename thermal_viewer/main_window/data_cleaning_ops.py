"""Rohdaten-Bereinigung: einzelne Ausreißer-Bilder (z.B. durch eine kurze
Kamera-/Übertragungsstörung) anhand der Temperaturänderung zum jeweils
vorherigen Bild an frei markierten Referenzpunkten erkennen und aus
Kurven/Wiedergabe/Export ausblenden -- OHNE sie oder ihre Zeitstempel
wirklich zu löschen (self.recording bleibt unverändert). Referenzpunkte
werden AUSSCHLIESSLICH in der eigenen, modalen Bildvorschau des
Bereinigungs-Dialogs gesetzt/verschoben (siehe dialogs/data_cleaning.py,
dialogs/data_cleaning_viewer.py) -- diese Datei enthält nur noch das reine
Datenmodell/die Berechnungen, KEINE Bild-Darstellung auf dem Hauptfenster
mehr (die gibt es seit dem Umbau dafür nicht mehr).

Nutzerwunsch: "Video-/Bilderstapelexport + Genereller Datenexport: Rohdaten
müssen vor der eigentlichen Auswertung 'gesäubert' werden ... Einen/mehrere
Punkte im Bild markieren/setzen können, und damit vorselektieren (dT
zwischen einzelnen Bildern betrachten -- wenn massive Änderung dann diese
Bilder rausschmeißen (Zeitstempel beibehalten!))" -- inzwischen erweitert um
UND/ODER PRO Punkt (statt global) und eine Aktivieren/Deaktivieren-Checkbox
pro Punkt (deaktiviert zählt nicht mehr mit, bleibt aber gespeichert), (b)
ausgeblendet statt endgültig gelöscht, mit Wiederherstellen-Liste, (c) dieser
gesamte Schritt MUSS vor dem allerersten angezeigten Bild im Hauptfenster
abgeschlossen werden (modaler Dialog direkt nach "Ordner öffnen…", siehe
import_ops.py/frame_nav.py), danach jederzeit über "Daten > Rohdaten
säubern…" erneut erreichbar."""
from __future__ import annotations

import numpy as np
from qtpy import QtWidgets


class _DataCleaningMixin:
    def _open_data_cleaning_dialog(self) -> None:
        if self.recording is None:
            QtWidgets.QMessageBox.information(self, "Keine Daten", "Bitte zuerst eine Messreihe laden.")
            return
        from ..dialogs import DataCleaningDialog

        if self._cleaning_dialog is None:
            self._cleaning_dialog = DataCleaningDialog(self)
        self._cleaning_dialog.refresh_points()
        # Modal (Nutzerwunsch): blockiert das Hauptfenster, bis der Dialog
        # geschlossen wird. Referenzpunkte werden ausschliesslich in der
        # EIGENEN Bildvorschau des Dialogs gesetzt (siehe data_cleaning_
        # viewer.py) -- es muss also nicht mehr gleichzeitig im Hauptfenster
        # geklickt werden koennen, wie es die frühere nicht-modale Variante
        # noch erforderte.
        self._cleaning_dialog.exec()

    def _remove_cleaning_point(self, index: int) -> None:
        if 0 <= index < len(self._cleaning_points):
            del self._cleaning_points[index]

    def _set_cleaning_point_logic(self, index: int, logic: str) -> None:
        """Setzt die UND/ODER-Zugehoerigkeit EINES Punkts (siehe
        data_cleaning.py: die Combobox pro Zeile) -- der Aufrufer stoesst
        danach selbst die Neuberechnung/Anwendung an (refresh_candidates)."""
        if not (0 <= index < len(self._cleaning_points)) or logic not in ("and", "or"):
            return
        col, row, _old_logic, enabled = self._cleaning_points[index]
        self._cleaning_points[index] = (col, row, logic, enabled)

    def _set_cleaning_point_enabled(self, index: int, enabled: bool) -> None:
        """Deaktiviert/reaktiviert EINEN Punkt (Checkbox pro Zeile,
        Nutzerwunsch) -- ein deaktivierter Punkt zaehlt bei der Ausreißer-
        Erkennung nicht mehr mit (siehe _compute_cleaning_candidates) und
        wird in der Bildvorschau nicht mehr gezeichnet, bleibt aber
        gespeichert und jederzeit wieder aktivierbar."""
        if not (0 <= index < len(self._cleaning_points)):
            return
        col, row, logic, _old_enabled = self._cleaning_points[index]
        self._cleaning_points[index] = (col, row, logic, enabled)

    # ------------------------------------------------------------ Analyse
    def _cleaning_point_bounds(self, row: int, col: int) -> tuple[int, int, int, int]:
        """Auf das Bild geclippter row0:row1, col0:col1-Bereich um einen
        Referenzpunkt, analog zu _live_cursor_bounds (mouse_ops.py), aber mit
        einer EIGENSTAENDIGEN Kernel-Groesse (_cleaning_kernel_size) statt der
        Live-Cursor-Einstellung -- unterschiedliche Zwecke, sollen sich nicht
        gegenseitig beeinflussen. row/col muessen bereits auf die Aufnahme
        geclempt sein (siehe Aufrufer)."""
        size = self._cleaning_kernel_size
        before = size // 2
        after = size - before
        rows, cols = self.recording.shape
        row0 = max(0, row - before)
        row1 = min(rows, row + after)
        col0 = max(0, col - before)
        col1 = min(cols, col + after)
        return row0, row1, col0, col1

    def _cleaning_point_series(self, point_index: int) -> np.ndarray:
        """Zeitreihe (ein Wert je Bild) fuer EINEN Referenzpunkt -- Einzel-
        pixel oder ueber die NxN-Flaeche gemittelt (siehe
        _cleaning_point_bounds), je nach _cleaning_kernel_size. Gemeinsam
        genutzt von _compute_cleaning_candidates (alle Punkte, vektorisiert
        ueber alle Bilder) UND _cleaning_point_delta (ein einzelner Punkt/
        aktuelles Bild) -- vermeidet doppelte Kernel-Logik."""
        col, row, _logic, _enabled = self._cleaning_points[point_index]
        frames = self.recording.frames
        rows, cols = self.recording.shape
        r = max(0, min(rows - 1, row))
        c = max(0, min(cols - 1, col))
        if self._cleaning_kernel_size <= 1:
            return frames[:, r, c].astype(float)
        row0, row1, col0, col1 = self._cleaning_point_bounds(r, c)
        return frames[:, row0:row1, col0:col1].mean(axis=(1, 2)).astype(float)

    def _cleaning_point_delta(self, point_index: int, frame_index: int) -> float | None:
        """Temperaturdifferenz (Betrag) EINES Referenzpunkts zwischen
        frame_index und dem VORHERIGEN Bild -- derselbe Wert, der auch zur
        Ausreißer-Erkennung herangezogen wird (siehe _compute_cleaning_
        candidates). None beim allerersten Bild (kein Vorbild) oder ohne
        geladene Aufnahme."""
        if (
            self.recording is None
            or frame_index <= 0
            or not (0 <= point_index < len(self._cleaning_points))
        ):
            return None
        series = self._cleaning_point_series(point_index)
        return float(abs(series[frame_index] - series[frame_index - 1]))

    def _compute_cleaning_candidates(self) -> set[int]:
        """Bild-Indizes (0-basiert), die MIT den aktuell markierten (und
        AKTIVIERTEN, siehe _set_cleaning_point_enabled) Punkten und dem
        aktuellen Schwellenwert als Ausreißer erkannt wuerden -- ein Bild i
        (i>=1, das allererste Bild hat kein "vorheriges" und wird nie
        markiert) gilt als Ausreißer, wenn ENTWEDER an ALLEN "UND"-Punkten
        ODER an MINDESTENS EINEM "ODER"-Punkt |T[i] - T[i-1]| den
        Schwellenwert überschreitet (UND/ODER gilt PRO Punkt -- eine der
        beiden Gruppen kann leer sein, dann zaehlt nur die andere). Bei
        einer Kernel-Groesse > 1 (Standard, siehe _cleaning_kernel_size)
        wird dafuer nicht nur das exakte Pixel, sondern der ueber die
        NxN-Flaeche gemittelte Wert herangezogen (robuster gegen Sensor-
        Rauschen an genau einem Pixel) -- bei Groesse 1 bleibt es beim
        reinen Einzelpixel-Wert."""
        if self.recording is None or not self._cleaning_points:
            return set()
        n = self.recording.n_frames
        threshold = self._cleaning_threshold
        enabled_indices = [i for i, p in enumerate(self._cleaning_points) if p[3]]
        if not enabled_indices:
            return set()
        series_per_point = {i: self._cleaning_point_series(i) for i in enabled_indices}
        and_indices = [i for i in enabled_indices if self._cleaning_points[i][2] != "or"]
        or_indices = [i for i in enabled_indices if self._cleaning_points[i][2] == "or"]
        flagged = set()
        for i in range(1, n):
            and_hit = bool(and_indices) and all(
                abs(series_per_point[j][i] - series_per_point[j][i - 1]) > threshold for j in and_indices
            )
            or_hit = bool(or_indices) and any(
                abs(series_per_point[j][i] - series_per_point[j][i - 1]) > threshold for j in or_indices
            )
            if and_hit or or_hit:
                flagged.add(i)
        return flagged

    def _apply_cleaning_exclusions(self, exclude: set[int], *, rebuild_dialog_on_reject: bool = True) -> None:
        """rebuild_dialog_on_reject=False: von refresh_candidates() selbst
        genutzt (jede Neuberechnung wendet sich sofort an), damit ein
        Ablehnen hier NICHT wieder refresh_candidates() aufruft -- sonst
        Endlosschleife, falls die neu berechnete Vorschau (newly_flagged
        vereint mit dem bereits ausgeblendeten Rest) zufaellig wieder alle
        Bilder abdeckt."""
        if self.recording is not None and self.recording.n_frames > 0 and len(exclude) >= self.recording.n_frames:
            # Mindestens EIN Bild muss sichtbar bleiben: wuerde diese Auswahl
            # ALLE Bilder ausblenden, faende der Navigations-Ausweich-Sprung
            # unten (siehe self._skip_excluded_frame_index) keine gueltige
            # Stelle mehr und wuerde den zentralen Nutzer-Zusicherung
            # verletzen, dass ein ausgeblendetes Bild NIE mehr angezeigt
            # wird -- daher unveraendert ablehnen, statt in diesen
            # inkonsistenten Zustand zu geraten.
            QtWidgets.QMessageBox.information(
                self, "Nicht möglich",
                "Mindestens ein Bild muss sichtbar bleiben -- diese Auswahl würde alle Bilder "
                "der Aufnahme ausblenden und wurde nicht übernommen.",
            )
            if rebuild_dialog_on_reject and self._cleaning_dialog is not None:
                self._cleaning_dialog.refresh_candidates()
            return
        self._excluded_frame_indices = set(exclude)
        self._recompute_curves()
        self._update_shrinkage_curve()
        # Steht die Anzeige gerade auf einem Bild, das JETZT (neu) ausgeblendet
        # wurde, muss sofort auf die naechste sichtbare Stelle gesprungen
        # werden -- sonst bliebe genau dieses eigentlich unerwuenschte Bild
        # trotzdem im Viewer stehen. _show_frame() selbst erkennt und korrigiert
        # das zentral (siehe frame_nav.py) -- hier genuegt daher ein einfacher
        # erneuter Aufruf mit dem UNVERAENDERTEN Index.
        if self.current_index in self._excluded_frame_indices:
            self._show_frame(self.current_index)
        self._update_status_bar()
        self.statusBar().showMessage(
            f"Rohdaten-Bereinigung angewendet: {len(self._excluded_frame_indices)} Bild(er) ausgeblendet.",
            5000,
        )
