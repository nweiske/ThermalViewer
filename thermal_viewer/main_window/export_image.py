"""Export von Einzelgrafiken (Thermobild und/oder Kurven-Graph)."""
from __future__ import annotations

import contextlib
import json
from datetime import datetime
from functools import partial
from pathlib import Path
from typing import TYPE_CHECKING

import pyqtgraph as pg
from qtpy import QtGui, QtWidgets

from .constants import (
    COLORMAPS,
)

if TYPE_CHECKING:
    from ..dialogs import GraphicExportDialog


class _ImageExportMixin:
    def _export_graphic(self) -> None:
        """Einziges Grafik-Export-Fenster (statt getrennter "Zeitverlauf-"/
        "Live-Grafik"-Menüpunkte, Nutzerwunsch: "nur noch ein einziges CSV/-
        Bild-Export Fenster"). Der Dialog fragt ab, welche Kurven -- einzelne
        Messbereiche und/oder Live-Cursor -- tatsächlich exportiert werden
        sollen (Nutzerwunsch: "einzelne ROIs (+Live-Cursor) zur Auswahl");
        beides landet gemeinsam in EINEM Graphen (self.timeseries_plot, siehe
        _temporary_graph_content)."""
        if self.recording is None:
            QtWidgets.QMessageBox.information(self, "Keine Daten", "Bitte zuerst eine Messreihe laden.")
            return
        live_available = self._hover_row is not None and self._hover_col is not None
        roi_entries = [(e.number, e.name) for e in self.roi_entries if e.placed]

        # Lokaler statt Modul-Import: siehe Kommentar in ui_build.py bei
        # AxisSettingsDialog -- greift so auch fuer per monkeypatch
        # eingesetzte Test-Dialoge.
        from . import GraphicExportDialog

        export_dialog = GraphicExportDialog(
            self, self._settings, default_dpi=150,
            colormaps=COLORMAPS,
            current_colormap_index=self.combo_cmap.currentIndex(),
            current_invert=self.chk_cmap_invert.isChecked(),
            current_level_mode=self._level_mode(),
            current_min=self.spin_level_min.value(),
            current_max=self.spin_level_max.value(),
            show_graph_source_choice=True,
            live_available=live_available,
            roi_entries=roi_entries,
            current_axis_state=self._gather_axis_state(self.timeseries_plot),
            show_scale_choice=True,
            ruler_available=self._px_to_mm is not None,
            measurement_entries=[(e.number, e.name) for e in self.measurements],
        )
        # Schleife statt einmaligem exec() (Punkt 3): bricht der Nutzer den
        # nachfolgenden Speichern-Dialog ab (siehe _export_combined_image,
        # Rueckgabewert True = "Speichern-Dialog abgebrochen"), geht es
        # zurueck zu GENAU diesem (bereits ausgefuellten) Dialog-Objekt statt
        # alle Einstellungen zu verwerfen.
        while True:
            if export_dialog.exec() != QtWidgets.QDialog.DialogCode.Accepted:
                return

            selected_numbers = export_dialog.included_roi_numbers()
            include_live = export_dialog.include_live() and live_available
            curve_widget = self.timeseries_plot
            suggested_name = "Zeitverlauf_mit_Position.png"
            if selected_numbers and include_live:
                curve_title = "Temperaturverlauf (Messbereiche + Live-Cursor)"
            elif include_live:
                curve_title = "Temperaturverlauf (Live-Cursor)"
            else:
                curve_title = "Temperaturverlauf (Messbereiche)"

            with self._temporary_graph_content(selected_numbers, include_live), \
                    self._temporary_axis_override(curve_widget, export_dialog.custom_axis_overrides()):
                retry = self._export_combined_image(
                    export_dialog, curve_widget, suggested_name, self._timeseries_metadata, curve_title
                )
            if not retry:
                return

    def _bind_native_export(
        self, widget: QtWidgets.QWidget, combined_export_fn=None, suggested_name: str | None = None
    ) -> None:
        """Ersetzt den Rechtsklick-Menüeintrag "Export…" von pyqtgraph durch
        unseren eigenen Grafik-Export.

        suggested_name ist NUR im Einzelexport-Fall (combined_export_fn=None)
        relevant und dort auch Pflicht -- vorher wurde er als dritter,
        eigentlich fuer combined_export_fn=None gedachter Parameter an ALLEN
        drei Aufrufstellen mitgegeben, blieb bei den beiden combined_export_fn-
        Aufrufen (Zeitverlauf-/Live-Graph) aber vollstaendig wirkungslos --
        totes, irrefuehrendes Argument (Bugreport: unklar, warum "Live-
        Verlauf.png" nirgendwo tatsaechlich als Dateiname auftaucht).

        Zwei Gruende: (1) einheitliches Erscheinungsbild -- ein Rechtsklick
        soll zum selben Dialog/DPI-Feld fuehren wie der Export-Menüpunkt,
        nicht zu pyqtgraphs eigenem, anders aussehendem Mini-Dialog; (2)
        pyqtgraphs eigener SVG-Export (pg.exporters.SVGExporter) laesst bei
        Kurven-Graphen mit Legende/DateAxisItem in der Praxis die Kurven
        selbst weg (nur das Koordinatensystem landet im SVG) -- unser
        eigener, bereits fuer den Export-Menü-Weg verwendeter SVG-Exporter
        (QSvgGenerator + QGraphicsScene.render(), siehe _save_widget_svg) ist
        davon nicht betroffen und wird dadurch automatisch auch hier genutzt.

        combined_export_fn (optional): ruft bei Klick GENAU dieselbe Methode
        auf wie der entsprechende Export-Menü-Punkt (_export_graphic), statt
        des sonst genutzten, auf dieses eine Widget beschraenkten Einzel-
        Export-Dialogs -- fuer ein
        Rechtsklick-Menü, das sich exakt wie das Menüband verhaelt
        (Bugreport: Rechtsklick "Exportieren" sollte ins selbe Menü wie
        ueber das Menüband fuehren)."""
        scene = widget.scene()
        action = scene.contextMenu[0]
        action.setText("Grafik speichern…")
        action.triggered.disconnect()
        if combined_export_fn is not None:
            action.triggered.connect(combined_export_fn)
        else:
            assert suggested_name is not None, "suggested_name ist im Einzelexport-Fall Pflicht"
            action.triggered.connect(partial(self._export_single_graph, widget, suggested_name))

    def _export_single_graph(self, widget: QtWidgets.QWidget, suggested_name: str) -> None:
        """Exportiert GENAU diesen einen Graphen (Thermobild ODER eine der
        beiden Kurven) -- Gegenstueck zu _export_combined_image, das immer
        Thermobild+Kurve zusammen exportiert. Nutzt bewusst denselben Dialog
        (nur ohne Kombiniert/Getrennt-Auswahl) und denselben Renderer, damit
        sich beide Export-Wege einheitlich verhalten."""
        if self.recording is None:
            QtWidgets.QMessageBox.information(self, "Keine Daten", "Bitte zuerst eine Messreihe laden.")
            return

        # Lokaler statt Modul-Import: siehe Kommentar in ui_build.py bei
        # AxisSettingsDialog.
        from . import GraphicExportDialog

        is_image_export = widget is self.glw
        export_dialog = GraphicExportDialog(
            self, self._settings, default_dpi=150, show_mode_choice=False, show_time_axis_choice=False,
            colormaps=COLORMAPS,
            current_colormap_index=self.combo_cmap.currentIndex(),
            current_invert=self.chk_cmap_invert.isChecked(),
            current_level_mode=self._level_mode(),
            current_min=self.spin_level_min.value(),
            current_max=self.spin_level_max.value(),
            show_scale_choice=is_image_export,
            ruler_available=is_image_export and self._px_to_mm is not None,
            measurement_entries=[(e.number, e.name) for e in self.measurements] if is_image_export else [],
        )
        # Schleife statt einmaligem exec() (Punkt 3): bricht der Nutzer den
        # nachfolgenden Speichern-Dialog ab, geht es zurueck zu GENAU diesem
        # (bereits ausgefuellten) Dialog-Objekt statt alle Einstellungen zu
        # verwerfen -- ein erneuter export_dialog.exec() zeigt automatisch
        # wieder den zuletzt eingestellten Zustand derselben Instanz.
        filters = {
            "PNG-Bild (*.png)": ".png",
            "JPEG-Bild (*.jpg *.jpeg)": ".jpg",
            "Bitmap (*.bmp)": ".bmp",
            "TIFF-Bild (*.tiff *.tif)": ".tiff",
            "WebP-Bild (*.webp)": ".webp",
            "SVG-Vektorgrafik (*.svg)": ".svg",
        }
        while True:
            if export_dialog.exec() != QtWidgets.QDialog.DialogCode.Accepted:
                return
            dpi = export_dialog.dpi()
            include_cursor = export_dialog.export_cursor_position()
            include_scale_ruler = export_dialog.include_scale_ruler()
            selected_scale_numbers = export_dialog.included_scale_measurement_numbers()
            use_custom_colors = export_dialog.use_custom_colors()

            default_path = str(Path(self._export_dir_hint()) / suggested_name) if self._export_dir_hint() else suggested_name
            path, selected_filter = QtWidgets.QFileDialog.getSaveFileName(
                self, "Grafik speichern", default_path, ";;".join(filters.keys())
            )
            if not path:
                continue
            self._remember_export_dir(path)
            break
        if not Path(path).suffix:
            path += filters.get(selected_filter, ".png")
        path_obj = Path(path)
        is_svg = path_obj.suffix.lower() == ".svg"

        # Thermobild (glw) und Kurven-Graphen haben seit dem Nutzerwunsch
        # "Graph immer hell/Thermobild immer dunkel" jeweils eine eigene,
        # feste Hintergrundfarbe (siehe __init__/_apply_image_colors/
        # _apply_curve_colors) -- welche hier zutrifft, haengt davon ab,
        # WELCHES der beiden Widgets gerade einzeln exportiert wird.
        bg = QtGui.QColor(self._image_bg if widget is self.glw else self._graph_bg)
        scale = dpi / 96.0
        pen_scale = scale * self._SVG_PEN_SCALE_FACTOR if is_svg else scale

        prev_level_state = self._capture_level_widgets_state() if use_custom_colors else None
        try:
            # Das Anwenden der Eigene-Einstellungen-Farbskala INNERHALB des
            # try -- sonst wuerde ein Fehler hier (vor dem try) die Anzeige
            # dauerhaft im Export-Zustand belassen, weil das finally unten
            # (das prev_level_state wiederherstellt) dann nie erreicht wird.
            if use_custom_colors:
                self._apply_custom_color_dialog_state(export_dialog, prev_level_state)
            # Kein geklammertes Mehrzeilen-with (Python 3.10+) -- der
            # Windows-7-Legacy-Build laeuft unter Python 3.8. _frozen_ui_
            # during_export() GANZ AUSSEN (siehe dort): _widget_raised_for_
            # export() holt bei einem tabifizierten Dock (z.B. "Live" waehrend
            # "Zeitverlauf" exportiert wird) den Export-Tab kurz sichtbar in
            # den Vordergrund -- ohne die Sperre wuerde der Nutzer diesen
            # Tab-Wechsel als kurzes Aufblitzen sehen.
            with self._frozen_ui_during_export(), \
                    self._widget_raised_for_export(widget), \
                    self._maybe_hidden_live_cursor(include_cursor), \
                    self._temporary_scale_visuals(include_scale_ruler, selected_scale_numbers), \
                    (self._rebased_time_axis(widget) if is_svg else contextlib.nullcontext()), \
                    self._scaled_export_visuals(scale, pen_scale):
                width, height = self._save_single_part(widget, path_obj, scale, bg, is_svg)
        except Exception as exc:
            # Bewusst breit gefangen (statt nur OSError): der Renderpfad
            # (QPainter/QSvgGenerator, dynamische Farbskala/Achsen-Zustand)
            # kann bei einem unerwarteten Zustand auch andere Exception-Typen
            # werfen (z.B. IndexError/AttributeError) -- ohne diesen breiten
            # Fang wuerde so ein Fehler ohne jeden Dialog nur als Konsolen-
            # Traceback durchschlagen, statt dass der Nutzer ueberhaupt
            # erfaehrt, dass der Export fehlgeschlagen ist.
            self._show_export_error("Konnte Grafik nicht speichern", exc)
            return
        finally:
            if use_custom_colors:
                self._apply_level_widgets_state(prev_level_state)

        metadata = {
            "exportiert_am": datetime.now().isoformat(timespec="seconds"),
            "datei": path_obj.name,
            "bildgroesse_px": {"breite": width, "hoehe": height},
            "dpi": dpi,
            "quellordner": str(self.recording.paths[0].parent) if self.recording.paths else None,
        }
        meta_path = path_obj.with_suffix(".json")
        meta_path.write_text(json.dumps(metadata, indent=2, ensure_ascii=False), encoding="utf-8")
        self.statusBar().showMessage(f"Grafik gespeichert: {path_obj.name}  |  Metadaten: {meta_path.name}")

    def _export_combined_image(
        self,
        export_dialog: GraphicExportDialog,
        curve_widget: pg.PlotWidget,
        suggested_name: str,
        metadata_fn,
        curve_title: str,
    ) -> bool:
        """Speichert Thermobild (mit Position der Messbereiche, optional auch
        des Cursors -- siehe GraphicExportDialog.export_cursor_position(),
        Standard aus) und den zugehörigen Temperaturverlauf -- wahlweise
        kombiniert als eine Grafik oder getrennt als zwei Dateien (Punkt 5).
        export_dialog ist bereits ausgefuellt/bestaetigt (siehe _export_graphic
        -- dort wird VOR dem Aufruf entschieden, welcher curve_widget/
        metadata_fn/curve_title ueberhaupt zum Einsatz kommt).

        Rueckgabe (Punkt 3): True, wenn der Speichern-Dialog abgebrochen
        wurde -- der Aufrufer soll dann zurueck zum (unveraendert
        ausgefuellten) export_dialog springen statt komplett abzubrechen.
        False in allen anderen Faellen (Erfolg oder bereits gemeldeter
        Fehler)."""
        dpi = export_dialog.dpi()
        want_separate = export_dialog.separate()
        want_combined = export_dialog.combined()
        graph_position = export_dialog.graph_position()
        include_cursor = export_dialog.export_cursor_position()
        include_scale_ruler = export_dialog.include_scale_ruler()
        selected_scale_numbers = export_dialog.included_scale_measurement_numbers()
        use_custom_colors = export_dialog.use_custom_colors()
        time_axis_mode = export_dialog.time_axis_mode()
        time_axis_ctx = (
            self._dual_time_axis_export(curve_widget) if time_axis_mode == "both"
            else self._temporary_time_display_mode(time_axis_mode)
        )

        filters = {
            "PNG-Bild (*.png)": ".png",
            "JPEG-Bild (*.jpg *.jpeg)": ".jpg",
            "Bitmap (*.bmp)": ".bmp",
            "TIFF-Bild (*.tiff *.tif)": ".tiff",
            "WebP-Bild (*.webp)": ".webp",
            "SVG-Vektorgrafik (*.svg)": ".svg",
        }
        default_path = str(Path(self._export_dir_hint()) / suggested_name) if self._export_dir_hint() else suggested_name
        path, selected_filter = QtWidgets.QFileDialog.getSaveFileName(
            self, "Grafik speichern", default_path, ";;".join(filters.keys())
        )
        if not path:
            return True
        self._remember_export_dir(path)
        if not Path(path).suffix:
            path += filters.get(selected_filter, ".png")
        path_obj = Path(path)
        is_svg = path_obj.suffix.lower() == ".svg"

        # Thermobild (glw) und Kurven-Graph haben seit dem Nutzerwunsch
        # "Graph immer hell/Thermobild immer dunkel" jeweils eine eigene,
        # feste Farbe (siehe __init__/_apply_image_colors/_apply_curve_
        # colors) -- die AEUSSERE Leinwand (Rand/Zwischenraum/Titeltext der
        # kombinierten Grafik, siehe _combine_image_and_graph/
        # _save_combined_svg) nutzt dabei bewusst die Graph-Farben, da der
        # Graph (anders als das Thermobild) nicht immer Teil des Exports ist
        # und der helle "Papier"-Rahmen zum wissenschaftlichen Standard passt.
        image_bg = QtGui.QColor(self._image_bg)
        bg = QtGui.QColor(self._graph_bg)
        fg = QtGui.QColor(self._graph_fg)
        scale = dpi / 96.0
        pen_scale = scale * self._SVG_PEN_SCALE_FACTOR if is_svg else scale

        sizes_px: dict[str, tuple[int, int]] = {}
        prev_level_state = self._capture_level_widgets_state() if use_custom_colors else None
        try:
            # Innerhalb des try (siehe _export_single_graph fuer den vollen
            # Grund): sonst bliebe die Anzeige bei einem Fehler hier
            # dauerhaft im Export-Farbzustand haengen, weil das
            # wiederherstellende finally unten nie erreicht wird.
            if use_custom_colors:
                self._apply_custom_color_dialog_state(export_dialog, prev_level_state)
            with self._frozen_ui_during_export(), \
                    self._widget_raised_for_export(curve_widget), \
                    self._maybe_hidden_live_cursor(include_cursor), \
                    self._temporary_scale_visuals(include_scale_ruler, selected_scale_numbers), \
                    time_axis_ctx, \
                    (self._rebased_time_axis(curve_widget) if is_svg else contextlib.nullcontext()), \
                    self._paused_background_timers(), \
                    self._scaled_export_visuals(scale, pen_scale):
                # Bugfix: siehe _export_video fuer den vollen Grund -- das
                # Einblenden der oberen Zeitachse (time_axis_ctx, "Beide")
                # und das Hochholen einer tabifizierten Dock-Registerkarte
                # (_widget_raised_for_export) wirken bei pyqtgraph ERST nach
                # dem naechsten Event-Loop-Durchlauf. Ohne diesen Aufruf
                # fehlte die obere Achse im Export vollstaendig (kein
                # weiterer Frame/processEvents()-Aufruf folgt hier wie beim
                # Video, der das "von selbst" korrigieren wuerde).
                QtWidgets.QApplication.processEvents()
                # Punkt 9 (Nutzerwunsch): "Kombiniert" und "Getrennt" sind
                # unabhaengige Checkboxen -- beide angehakt erzeugt in EINEM
                # Durchgang sowohl die kombinierte Grafik als auch die zwei
                # Einzeldateien, statt sich gegenseitig auszuschliessen.
                saved_paths = []
                if want_separate:
                    image_path = path_obj.with_name(f"{path_obj.stem}_Bild{path_obj.suffix}")
                    curve_path = path_obj.with_name(f"{path_obj.stem}_Kurve{path_obj.suffix}")
                    sizes_px[image_path.name] = self._save_single_part(self.glw, image_path, scale, image_bg, is_svg)
                    sizes_px[curve_path.name] = self._save_single_part(curve_widget, curve_path, scale, bg, is_svg)
                    saved_paths += [image_path, curve_path]
                if want_combined:
                    if is_svg:
                        sizes_px[path_obj.name] = self._save_combined_svg(
                            path_obj, self.glw, "Position im Thermobild", curve_widget, curve_title,
                            graph_position, dpi, fg, bg,
                        )
                    else:
                        image_scene = self._render_widget_image(self.glw, scale, image_bg)
                        image_curve = self._render_widget_image(curve_widget, scale, bg)
                        combined_image = self._combine_image_and_graph(
                            image_scene, "Position im Thermobild", image_curve, curve_title, graph_position, dpi, bg, fg
                        )
                        if not combined_image.save(path):
                            raise OSError(f"Konnte Bild nicht speichern: {path}")
                        sizes_px[path_obj.name] = (combined_image.width(), combined_image.height())
                    saved_paths.append(path_obj)
        except Exception as exc:
            # Bewusst breit (siehe _export_single_graph) -- derselbe
            # mehrteilige Renderpfad (Thermobild + Kurve, ggf. SVG) kann auch
            # andere Exception-Typen als OSError werfen.
            self._show_export_error("Konnte Grafik nicht speichern", exc)
            return False
        finally:
            if use_custom_colors:
                self._apply_level_widgets_state(prev_level_state)

        metadata = {
            "exportiert_am": datetime.now().isoformat(timespec="seconds"),
            "dateien": [p.name for p in saved_paths],
            "bildgroessen_px": {
                name: {"breite": w, "hoehe": h} for name, (w, h) in sizes_px.items()
            },
            "dpi": dpi,
            **metadata_fn(),
        }
        meta_path = path_obj.with_suffix(".json")
        try:
            meta_path.write_text(json.dumps(metadata, indent=2, ensure_ascii=False), encoding="utf-8")
        except OSError as exc:
            # Die Grafik selbst ist zu diesem Zeitpunkt bereits erfolgreich
            # gespeichert (siehe try/except weiter oben) -- ein Fehler hier
            # (Datentraeger voll, Zielordner inzwischen schreibgeschuetzt,
            # ".json" von einem anderen Programm gesperrt) betrifft nur die
            # zusaetzliche Metadaten-Datei und soll das nicht als kompletten
            # Fehlschlag melden.
            QtWidgets.QMessageBox.warning(
                self, "Metadaten nicht gespeichert",
                f"Die Grafik wurde gespeichert, die Metadaten-Datei „{meta_path.name}“ konnte aber "
                f"nicht geschrieben werden:\n{exc}",
            )
            self.statusBar().showMessage(
                f"Grafik gespeichert: {', '.join(p.name for p in saved_paths)}  |  Metadaten fehlgeschlagen"
            )
            return False

        self.statusBar().showMessage(
            f"Grafik gespeichert: {', '.join(p.name for p in saved_paths)}  |  Metadaten: {meta_path.name}"
        )
        return False

