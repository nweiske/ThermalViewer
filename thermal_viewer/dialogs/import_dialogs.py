"""Dialoge für den Datenimport: Rohformat-Anpassung (Datenimport-Manager)
und den TIFF-Bild-Import mit manueller Temperaturzuordnung."""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pyqtgraph as pg
from qtpy import QtCore, QtGui, QtWidgets

from ..data import ImportSettings, RecordingError, parse_frame_text
from ..widgets import LocaleTolerantDoubleSpinBox
from ._base import _disable_enter_auto_accept, _NoEnterAutoAccept

# Feste Presets statt frei editierbarer Felder -- verhindert ungueltige
# Kombinationen (z.B. ein leeres Dezimaltrennzeichen) und deckt die in der
# Praxis vorkommenden Roh-Exportformate ab. "" als Trennzeichen-Wert steht
# fuer "beliebig viele Leerzeichen" (siehe ImportSettings/_parse_data_line
# in data.py -- str.split() ohne Argument statt eines festen Trennzeichens).
_DELIMITER_OPTIONS: list[tuple[str, str]] = [
    ("Semikolon ( ; )", ";"),
    ("Komma ( , )", ","),
    ("Tabulator", "\t"),
    ("Senkrechter Strich ( | )", "|"),
    ("Leerzeichen (beliebig viele)", ""),
]
_DECIMAL_OPTIONS: list[tuple[str, str]] = [
    ("Komma ( , )", ","),
    ("Punkt ( . )", "."),
]
_ENCODING_OPTIONS: list[tuple[str, str]] = [
    ("UTF-8 (Standard)", "utf-8-sig"),
    ("Windows-1252 / Latin-1", "cp1252"),
    ("UTF-16", "utf-16"),
]


class ImportSettingsDialog(_NoEnterAutoAccept, QtWidgets.QDialog):
    """Datenimport-Manager: bereitet rohe Messdateien mit abweichendem
    Format (zusaetzliche Kopf-/Fusszeilen, eine fuehrende Index-Spalte,
    anderes Trennzeichen/Dezimaltrennzeichen/Kodierung) fuers Einlesen vor
    -- mit sofortiger Live-Vorschau gegen eine echte Beispieldatei, damit
    das Ergebnis VOR dem eigentlichen Laden sichtbar ist.

    Hintergrund: die App wird in Kuerze auch Messreihen aus anderen
    Quellen/Geraeten einlesen koennen sollen, deren genaues Rohformat noch
    nicht bekannt ist (noch keine Testdateien vorhanden) -- dieser Dialog
    macht das feste, bisher fest einprogrammierte CSV-Format
    (';'-getrennt, Dezimalkomma, keine Kopfzeilen) an zentraler Stelle
    nutzerseitig anpassbar, statt es im Code fest zu verdrahten."""

    _MAX_RAW_PREVIEW_LINES = 40
    _PARSED_PREVIEW_ROWS = 6
    _PARSED_PREVIEW_COLS = 8

    def __init__(self, parent, sample_path: Path, settings: ImportSettings, *, is_retry: bool = False):
        super().__init__(parent)
        self.setWindowTitle("Datenimport anpassen")
        self.setMinimumSize(780, 620)
        self._sample_path = Path(sample_path)
        # Cache fuer den zuletzt gelesenen Dateiinhalt: nur Pfad und
        # Kodierung beeinflussen, WAS von der Platte gelesen wird -- alle
        # anderen Einstellungen (Kopf-/Fusszeilen, Trennzeichen, Spalten)
        # wirken nur auf den bereits im Speicher liegenden Text. Ohne diesen
        # Cache laese _refresh() bei JEDER Spinbox-Aenderung (Klick auf einen
        # der Pfeile) die komplette Beispieldatei erneut von der Platte --
        # bei einer grossen Datei spuerbares Rucken pro Klick.
        self._cache_key: tuple[Path, str] | None = None
        self._cached_text = ""
        self._cached_read_error: str | None = None

        layout = QtWidgets.QVBoxLayout(self)

        intro = QtWidgets.QLabel(
            "Legt fest, wie eine rohe Messdatei in ein Temperatur-Raster umgewandelt wird -- "
            "nützlich, wenn Messreihen aus einer anderen Quelle ein abweichendes Format "
            "mitbringen (z.B. zusätzliche Kopfzeilen, eine führende Index-Spalte, ein anderes "
            "Trennzeichen). Die Vorschau unten zeigt sofort, ob die aktuelle Einstellung auf "
            "die Beispieldatei passt."
        )
        intro.setWordWrap(True)
        layout.addWidget(intro)

        file_row = QtWidgets.QHBoxLayout()
        file_row.addWidget(QtWidgets.QLabel("Beispieldatei:"))
        self.lbl_sample_path = QtWidgets.QLabel(str(self._sample_path))
        self.lbl_sample_path.setTextInteractionFlags(QtCore.Qt.TextInteractionFlag.TextSelectableByMouse)
        file_row.addWidget(self.lbl_sample_path, 1)
        btn_pick = QtWidgets.QPushButton("Andere Datei wählen…")
        btn_pick.clicked.connect(self._pick_sample_file)
        file_row.addWidget(btn_pick)
        layout.addLayout(file_row)

        # Einstellungen und Roh-Vorschau nebeneinander, damit die Wirkung
        # einer Aenderung direkt neben den tatsaechlichen Kopfzeilen/dem
        # tatsaechlichen Trennzeichen der Beispieldatei sichtbar ist.
        top_row = QtWidgets.QHBoxLayout()

        form_box = QtWidgets.QGroupBox("Einstellungen")
        form = QtWidgets.QFormLayout(form_box)
        self.combo_delimiter = QtWidgets.QComboBox()
        for label, _value in _DELIMITER_OPTIONS:
            self.combo_delimiter.addItem(label)
        form.addRow("Trennzeichen:", self.combo_delimiter)

        self.combo_decimal = QtWidgets.QComboBox()
        for label, _value in _DECIMAL_OPTIONS:
            self.combo_decimal.addItem(label)
        form.addRow("Dezimaltrennzeichen:", self.combo_decimal)

        self.combo_encoding = QtWidgets.QComboBox()
        for label, _value in _ENCODING_OPTIONS:
            self.combo_encoding.addItem(label)
        form.addRow("Zeichenkodierung:", self.combo_encoding)

        self.spin_skip_header = QtWidgets.QSpinBox()
        self.spin_skip_header.setRange(0, 500)
        self.spin_skip_header.setToolTip("Anzahl der Zeilen am Dateianfang, die keine Messwerte enthalten.")
        form.addRow("Kopfzeilen überspringen:", self.spin_skip_header)

        self.spin_skip_footer = QtWidgets.QSpinBox()
        self.spin_skip_footer.setRange(0, 500)
        self.spin_skip_footer.setToolTip("Anzahl der Zeilen am Dateiende, die keine Messwerte enthalten.")
        form.addRow("Fußzeilen überspringen:", self.spin_skip_footer)

        self.spin_skip_leading = QtWidgets.QSpinBox()
        self.spin_skip_leading.setRange(0, 100)
        self.spin_skip_leading.setToolTip("Anzahl der Spalten am Zeilenanfang, die keine Messwerte enthalten (z.B. eine Index-Spalte).")
        form.addRow("Erste Spalte(n) entfernen:", self.spin_skip_leading)

        self.spin_skip_trailing = QtWidgets.QSpinBox()
        self.spin_skip_trailing.setRange(0, 100)
        self.spin_skip_trailing.setToolTip("Anzahl der Spalten am Zeilenende, die keine Messwerte enthalten.")
        form.addRow("Letzte Spalte(n) entfernen:", self.spin_skip_trailing)

        top_row.addWidget(form_box, 1)

        raw_box = QtWidgets.QGroupBox("Rohdaten (Ausschnitt)")
        raw_layout = QtWidgets.QVBoxLayout(raw_box)
        self.raw_preview = QtWidgets.QPlainTextEdit()
        self.raw_preview.setReadOnly(True)
        self.raw_preview.setLineWrapMode(QtWidgets.QPlainTextEdit.LineWrapMode.NoWrap)
        self.raw_preview.setFont(QtGui.QFont("Consolas", 9))
        raw_layout.addWidget(self.raw_preview)
        top_row.addWidget(raw_box, 1)

        layout.addLayout(top_row)

        result_box = QtWidgets.QGroupBox("Ergebnis-Vorschau")
        result_layout = QtWidgets.QVBoxLayout(result_box)
        self.lbl_result_status = QtWidgets.QLabel()
        self.lbl_result_status.setWordWrap(True)
        result_layout.addWidget(self.lbl_result_status)
        self.result_preview = QtWidgets.QPlainTextEdit()
        self.result_preview.setReadOnly(True)
        self.result_preview.setLineWrapMode(QtWidgets.QPlainTextEdit.LineWrapMode.NoWrap)
        self.result_preview.setFont(QtGui.QFont("Consolas", 9))
        self.result_preview.setFixedHeight(140)
        result_layout.addWidget(self.result_preview)
        layout.addWidget(result_box)

        self.chk_persist = QtWidgets.QCheckBox("Als neue Standardeinstellung dauerhaft speichern")
        if is_retry:
            # Waehrend eines konkreten Ladevorgangs (siehe MainWindow._load_paths):
            # unmarkiert gilt die Anpassung wirklich nur fuer DIESEN einen Versuch,
            # der Session-Standard bleibt unveraendert.
            self.chk_persist.setToolTip(
                "Aus: gilt nur für diesen einen Ladevorgang. An: wird als neuer Standard für "
                "künftige Ladevorgänge gespeichert."
            )
        else:
            # Eigenstaendig ueber "Werkzeuge > Datenimport anpassen…" geoeffnet
            # (kein Ladevorgang laeuft gerade) -- ein OK hier hat nur dann
            # ueberhaupt einen Effekt, wenn es den Session-Standard aendert,
            # sonst waere der Dialog ausserhalb einer Fehlerbehebung wirkungslos.
            self.chk_persist.setToolTip(
                "Aus: gilt nur für die aktuelle Sitzung (bis zum Beenden des Programms). "
                "An: wird zusätzlich dauerhaft als neuer Standard gespeichert."
            )
        layout.addWidget(self.chk_persist)

        self.buttons = QtWidgets.QDialogButtonBox(
            QtWidgets.QDialogButtonBox.StandardButton.Ok | QtWidgets.QDialogButtonBox.StandardButton.Cancel
        )
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)
        _disable_enter_auto_accept(self.buttons)
        layout.addWidget(self.buttons)

        self._select_combo_value(self.combo_delimiter, _DELIMITER_OPTIONS, settings.delimiter)
        self._select_combo_value(self.combo_decimal, _DECIMAL_OPTIONS, settings.decimal_separator)
        self._select_combo_value(self.combo_encoding, _ENCODING_OPTIONS, settings.encoding)
        self.spin_skip_header.setValue(settings.skip_header_lines)
        self.spin_skip_footer.setValue(settings.skip_footer_lines)
        self.spin_skip_leading.setValue(settings.skip_leading_columns)
        self.spin_skip_trailing.setValue(settings.skip_trailing_columns)

        for combo in (self.combo_delimiter, self.combo_decimal, self.combo_encoding):
            combo.currentIndexChanged.connect(self._refresh)
        for spin in (self.spin_skip_header, self.spin_skip_footer, self.spin_skip_leading, self.spin_skip_trailing):
            spin.valueChanged.connect(self._refresh)

        self._refresh()

    @staticmethod
    def _select_combo_value(combo: QtWidgets.QComboBox, options: list[tuple[str, str]], value: str) -> None:
        for i, (_label, opt_value) in enumerate(options):
            if opt_value == value:
                combo.setCurrentIndex(i)
                return
        combo.setCurrentIndex(0)

    def _pick_sample_file(self) -> None:
        path, _filter = QtWidgets.QFileDialog.getOpenFileName(
            self, "Beispieldatei wählen", str(self._sample_path.parent),
            "CSV-Dateien (*.csv);;Alle Dateien (*)",
        )
        if not path:
            return
        self._sample_path = Path(path)
        self.lbl_sample_path.setText(str(self._sample_path))
        self._refresh()

    def _refresh(self) -> None:
        settings = self.settings()
        cache_key = (self._sample_path, settings.encoding)
        if cache_key != self._cache_key:
            try:
                self._cached_text = self._sample_path.read_text(encoding=settings.encoding)
                self._cached_read_error = None
            except (OSError, LookupError, UnicodeDecodeError) as exc:
                self._cached_text = ""
                self._cached_read_error = f"Beispieldatei konnte nicht gelesen werden: {exc}"
            self._cache_key = cache_key
        text = self._cached_text
        read_error = self._cached_read_error

        self.raw_preview.setPlainText(
            "\n".join(text.splitlines()[: self._MAX_RAW_PREVIEW_LINES]) or "(leer)"
        )

        ok = False
        if read_error is not None:
            self.lbl_result_status.setText(f"⚠ {read_error}")
            self.result_preview.setPlainText("")
        elif settings.delimiter and settings.delimiter == settings.decimal_separator:
            # Trennzeichen und Dezimaltrennzeichen duerfen nicht dasselbe
            # Zeichen sein: parse_frame_text() wuerde sonst z.B. "28,6"
            # zuerst am Komma in "28"/"6" zerlegen (Trennzeichen), bevor das
            # anschliessende Dezimaltrennzeichen-Replace ueberhaupt noch
            # etwas zu tun haette -- jede Zeile bekaeme dadurch doppelt so
            # viele (falsche, halbierte) Spalten, OHNE dass der Parser einen
            # Fehler wirft (die Spaltenzahl bleibt ja pro Zeile einheitlich).
            # Ohne diese explizite Pruefung wuerde das Ergebnis-Vorschau
            # "erfolgreich" aussehen, obwohl die Werte stillschweigend
            # kaputt sind.
            self.lbl_result_status.setText(
                "⚠ Trennzeichen und Dezimaltrennzeichen dürfen nicht dasselbe Zeichen sein."
            )
            self.result_preview.setPlainText("")
        else:
            try:
                array = parse_frame_text(text, settings)
            except RecordingError as exc:
                self.lbl_result_status.setText(f"⚠ {exc}")
                self.result_preview.setPlainText("")
            else:
                ok = True
                rows, cols = array.shape
                self.lbl_result_status.setText(f"✓ Erkannt: {rows} Zeile(n) × {cols} Spalte(n)")
                corner = array[: self._PARSED_PREVIEW_ROWS, : self._PARSED_PREVIEW_COLS]
                lines = ["  ".join(f"{v:7.2f}" for v in row) for row in corner]
                if cols > self._PARSED_PREVIEW_COLS:
                    lines = [line + "  …" for line in lines]
                if rows > self._PARSED_PREVIEW_ROWS:
                    lines.append("…")
                self.result_preview.setPlainText("\n".join(lines))

        ok_button = self.buttons.button(QtWidgets.QDialogButtonBox.StandardButton.Ok)
        if ok_button is not None:
            ok_button.setEnabled(ok)

    def settings(self) -> ImportSettings:
        return ImportSettings(
            delimiter=_DELIMITER_OPTIONS[self.combo_delimiter.currentIndex()][1],
            decimal_separator=_DECIMAL_OPTIONS[self.combo_decimal.currentIndex()][1],
            encoding=_ENCODING_OPTIONS[self.combo_encoding.currentIndex()][1],
            skip_header_lines=self.spin_skip_header.value(),
            skip_footer_lines=self.spin_skip_footer.value(),
            skip_leading_columns=self.spin_skip_leading.value(),
            skip_trailing_columns=self.spin_skip_trailing.value(),
        )

    def persist(self) -> bool:
        return self.chk_persist.isChecked()


class TiffImportDialog(_NoEnterAutoAccept, QtWidgets.QDialog):
    """Fragt Bildausschnitt (zum Ausschliessen von Farbskala/Legende) sowie
    Min-/Max-Temperatur für den TIFF-Import ab -- siehe
    MainWindow._import_tiff_images() und data.tiff_crop_to_temperature() für
    die eigentliche Umrechnung.

    Bewusst KEINE automatische Kalibrierung/Metadaten-Auswertung: die
    Original-TIFFs enthalten keine verlässlichen, dokumentierten
    Kalibrierdaten (siehe Analyse-Notizen) -- einzig zulässiger Kompromiss
    (Nutzervorgabe) ist eine manuell angegebene Min-/Max-Temperatur, deutlich
    als unkalibrierte Schätzung gekennzeichnet."""

    def __init__(self, parent, preview_gray: np.ndarray, file_count: int):
        super().__init__(parent)
        self.setWindowTitle("TIFF-Bilder importieren")
        self.setMinimumSize(720, 640)
        self._gray = preview_gray
        height, width = preview_gray.shape

        layout = QtWidgets.QVBoxLayout(self)

        warning = QtWidgets.QLabel(
            "⚠ Unkalibrierte Schätzung: Die Grauwerte werden rein linear zwischen den unten "
            "angegebenen Temperaturen interpoliert -- OHNE echte radiometrische Kalibrierung der "
            "Kamera. Nur verwenden, wenn Min-/Max-Temperatur für den gewählten Ausschnitt "
            "tatsächlich bekannt sind (z.B. von der Farbskala der Original-Software abgelesen). "
            "Auswertung auf eigene Gefahr!"
        )
        warning.setWordWrap(True)
        warning.setStyleSheet("color:#b91c1c; font-weight:600;")
        layout.addWidget(warning)

        if file_count > 1:
            note = QtWidgets.QLabel(
                f"Ausschnitt UND Temperaturbereich gelten für ALLE {file_count} ausgewählten "
                "Dateien gemeinsam (Vorschau zeigt nur die erste Datei) -- bei unterschiedlichen "
                "Temperaturbereichen je Datei einzeln importieren."
            )
            note.setWordWrap(True)
            layout.addWidget(note)

        instructions = QtWidgets.QLabel(
            "Blaues Rechteck im Bild auf den reinen Messbereich ziehen -- Farbskala/Legende/"
            "Beschriftungen des Original-Exports AUSSERHALB des Rechtecks lassen, sonst "
            "verfälschen deren Extremwerte (reines Schwarz/Weiß) die Min-/Max-Zuordnung."
        )
        instructions.setWordWrap(True)
        layout.addWidget(instructions)

        self.plot = pg.PlotWidget()
        self.plot.setAspectLocked(True)
        self.plot.invertY(True)
        self.plot.showGrid(x=False, y=False)
        self.plot.getPlotItem().hideAxis("bottom")
        self.plot.getPlotItem().hideAxis("left")
        view_box = self.plot.getPlotItem().getViewBox()
        view_box.setMouseEnabled(x=False, y=False)
        view_box.setMenuEnabled(False)
        self.image_item = pg.ImageItem(preview_gray)
        self.plot.addItem(self.image_item)
        self.roi = pg.RectROI(
            [0, 0], [width, height], pen=pg.mkPen("#38bdf8", width=2), maxBounds=QtCore.QRectF(0, 0, width, height)
        )
        # RectROI fuegt selbst schon einen Skalier-Ziehpunkt unten rechts
        # hinzu (Anker oben links) -- ein zusaetzlicher addScaleHandle an
        # GENAU derselben Position/demselben Anker wuerde dort einen exakt
        # doppelten (ueberlappenden) Ziehpunkt erzeugen. Hier nur den noch
        # fehlenden Ziehpunkt oben links (Anker unten rechts) ergaenzen,
        # damit sich das Rechteck von BEIDEN gegenueberliegenden Ecken aus
        # ziehen laesst.
        self.roi.addScaleHandle([0, 0], [1, 1])
        self.plot.addItem(self.roi)
        layout.addWidget(self.plot, 1)

        self.lbl_crop = QtWidgets.QLabel()
        layout.addWidget(self.lbl_crop)

        form = QtWidgets.QFormLayout()
        self.spin_min = LocaleTolerantDoubleSpinBox()
        self.spin_min.setRange(-273.15, 10000.0)
        self.spin_min.setDecimals(2)
        self.spin_min.setSuffix(" °C")
        self.spin_min.setValue(0.0)
        self.spin_min.setToolTip("Temperatur des DUNKELSTEN Pixels im gewählten Ausschnitt.")
        form.addRow("Min-Temperatur (dunkelster Pixel):", self.spin_min)
        self.spin_max = LocaleTolerantDoubleSpinBox()
        self.spin_max.setRange(-273.15, 10000.0)
        self.spin_max.setDecimals(2)
        self.spin_max.setSuffix(" °C")
        self.spin_max.setValue(100.0)
        self.spin_max.setToolTip("Temperatur des HELLSTEN Pixels im gewählten Ausschnitt.")
        form.addRow("Max-Temperatur (hellster Pixel):", self.spin_max)
        layout.addLayout(form)

        self.chk_confirm = QtWidgets.QCheckBox(
            "Mir ist bewusst, dass dies eine unkalibrierte Schätzung ist, und ich kenne die "
            "korrekten Grenzwerte für diesen Ausschnitt."
        )
        layout.addWidget(self.chk_confirm)

        self.buttons = QtWidgets.QDialogButtonBox(
            QtWidgets.QDialogButtonBox.StandardButton.Ok | QtWidgets.QDialogButtonBox.StandardButton.Cancel
        )
        self._btn_ok = self.buttons.button(QtWidgets.QDialogButtonBox.StandardButton.Ok)
        self._btn_ok.setEnabled(False)
        self.chk_confirm.toggled.connect(self._btn_ok.setEnabled)
        self.buttons.accepted.connect(self._on_accept)
        self.buttons.rejected.connect(self.reject)
        layout.addWidget(self.buttons)

        self.roi.sigRegionChanged.connect(self._update_crop_label)
        self._update_crop_label()

    def _on_accept(self) -> None:
        x0, y0, x1, y1 = self.crop_rect()
        if x1 <= x0 or y1 <= y0:
            QtWidgets.QMessageBox.warning(
                self, "Ungültiger Ausschnitt", "Bitte einen nicht-leeren Bildausschnitt wählen."
            )
            return
        if self.spin_max.value() <= self.spin_min.value():
            QtWidgets.QMessageBox.warning(
                self, "Ungültiger Bereich",
                "Die Max-Temperatur muss größer als die Min-Temperatur sein -- sonst ergibt "
                "sich eine invertierte oder flache (unbrauchbare) Temperaturzuordnung.",
            )
            return
        self.accept()

    def _update_crop_label(self) -> None:
        x0, y0, x1, y1 = self.crop_rect()
        region = self._gray[y0:y1, x0:x1]
        if region.size:
            self.lbl_crop.setText(
                f"Ausschnitt: {x1 - x0}×{y1 - y0} px bei ({x0}, {y0})  |  "
                f"Grauwerte im Ausschnitt: {region.min():.0f}–{region.max():.0f}"
            )
        else:
            self.lbl_crop.setText("Ausschnitt: leer -- bitte Rechteck vergrößern.")

    def crop_rect(self) -> tuple[int, int, int, int]:
        """(x0, y0, x1, y1) in Bild-Pixelkoordinaten, auf die Bildgrenzen
        geklemmt und normalisiert (x0<=x1, y0<=y1) -- unabhängig davon, in
        welche Richtung der Nutzer das ROI-Rechteck gezogen hat."""
        height, width = self._gray.shape
        pos = self.roi.pos()
        size = self.roi.size()
        x0 = max(0, min(width, round(pos.x())))
        y0 = max(0, min(height, round(pos.y())))
        x1 = max(0, min(width, round(pos.x() + size.x())))
        y1 = max(0, min(height, round(pos.y() + size.y())))
        return (min(x0, x1), min(y0, y1), max(x0, x1), max(y0, y1))

    def min_temp(self) -> float:
        return self.spin_min.value()

    def max_temp(self) -> float:
        return self.spin_max.value()
