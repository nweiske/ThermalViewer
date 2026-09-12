"""Video-/Bildstapel-Export."""
from __future__ import annotations

import contextlib
import traceback
from pathlib import Path

from qtpy import QtCore, QtGui, QtWidgets

from ..dialogs import (
    INDEX_TOKEN,
    render_export_filename,
)
from .constants import (
    COLORMAPS,
)


class _VideoExportMixin:
    def _export_video(self) -> None:
        if self.recording is None or self.recording.n_frames == 0:
            QtWidgets.QMessageBox.information(self, "Keine Daten", "Bitte zuerst eine Messreihe laden.")
            return

        default_start = self._eval_start_index if self._eval_start_index is not None else 0
        default_end = (
            self._eval_end_index if self._eval_end_index is not None else self.recording.n_frames - 1
        )
        live_available = self._hover_row is not None and self._hover_col is not None
        # Lokaler statt Modul-Import: siehe Kommentar in ui_build.py bei
        # AxisSettingsDialog.
        from . import VideoExportDialog

        dialog = VideoExportDialog(
            self,
            n_frames=self.recording.n_frames,
            colormaps=COLORMAPS,
            current_colormap_index=self.combo_cmap.currentIndex(),
            current_invert=self.chk_cmap_invert.isChecked(),
            current_level_mode=self._level_mode(),
            current_min=self.spin_level_min.value(),
            current_max=self.spin_level_max.value(),
            current_fps=self.fps_spin.value(),
            default_start_frame=default_start + 1,
            default_end_frame=default_end + 1,
            roi_entries=[(e.number, e.name) for e in self.roi_entries if e.placed],
            live_available=live_available,
            sample_timestamp=self.recording.timestamps[0] if self.recording.timestamps else None,
            timestamps=self.recording.timestamps or None,
            current_axis_state=self._gather_axis_state(self.timeseries_plot),
            settings=self._settings,
            ruler_available=self._px_to_mm is not None,
            measurement_entries=[(e.number, e.name) for e in self.measurements],
            has_excluded_frames=bool(self._excluded_frame_indices),
        )
        # Schleife statt einmaligem exec() (Punkt 3): bricht der Nutzer den
        # NACHFOLGENDEN Datei-/Ordner-Dialog ab (z.B. weil ihm ein Fehler im
        # Export-Manager selbst auffaellt), geht es zurueck zu GENAU diesem
        # (bereits ausgefuellten) Dialog-Objekt statt alles zu verwerfen --
        # ein erneuter dialog.exec() zeigt automatisch wieder den zuletzt
        # eingestellten Zustand, weil dieselbe Instanz wiederverwendet wird.
        while True:
            if dialog.exec() != QtWidgets.QDialog.DialogCode.Accepted:
                return

            output_mode = dialog.output_mode()
            if output_mode == "video":
                try:
                    import imageio.v2 as imageio
                except ImportError:
                    QtWidgets.QMessageBox.critical(
                        self,
                        "Fehlende Abhängigkeit",
                        "Für den Video-Export wird das Paket 'imageio' (mit 'imageio-ffmpeg') benötigt, "
                        "das in dieser Installation nicht verfügbar ist.",
                    )
                    continue

            start_idx, end_idx = dialog.frame_range()
            fps = dialog.fps()
            show_legend = dialog.show_legend()
            use_custom = dialog.use_custom_settings()
            overlay_mode = dialog.timeline_overlay_mode()
            include_cursor = dialog.export_cursor_position()
            freeze_excluded_pixels = dialog.freeze_excluded_frame_pixels()
            include_scale_ruler = dialog.include_scale_ruler()
            selected_scale_numbers = dialog.included_scale_measurement_numbers()
            graph_widget = None
            graph_position = "unten"
            selected_roi_numbers: set[int] = set()
            include_live_curve = False
            axis_overrides = None
            if dialog.show_graph():
                graph_widget = self.timeseries_plot
                graph_position = dialog.graph_position()
                selected_roi_numbers = dialog.included_roi_numbers()
                include_live_curve = dialog.include_live()
                axis_overrides = dialog.custom_axis_overrides()
            # "Zeitanzeige im Bild" (overlay_mode) galt bisher NUR fuer den ins
            # Bild eingebrannten Text-Streifen -- der mit exportierte Graph
            # blieb unabhaengig davon immer bei der gerade in der App aktiven
            # Uhrzeit-/Laufzeit-Anzeige stehen (Bugreport: "wenn ich 'beides'
            # als Zeitachse auswähle stehen zwar beide Achsen unter dem Video,
            # aber nur die Laufzeit im Graphen"). Denselben, bereits
            # vorhandenen Menüpunkt jetzt konsistent fuer BEIDE Elemente nutzen,
            # statt eine zweite, separate Zeitachsen-Auswahl einzufuehren.
            # "Keine" (kein Zeit-Overlay im Bild) hat keine Entsprechung im
            # Graphen -- dort bleibt die aktuelle App-Anzeige unveraendert.
            graph_time_axis_mode = {"timeline": "runtime", "timestamp": "clock"}.get(overlay_mode)

            if output_mode == "video":
                video_filters = {
                    "MP4-Video (*.mp4)": ".mp4",
                    "AVI-Video (*.avi)": ".avi",
                    "WebM-Video (*.webm)": ".webm",
                }
                default_name = "Thermo-Video.mp4"
                default_path = (
                    str(Path(self._export_dir_hint()) / default_name) if self._export_dir_hint() else default_name
                )
                path, selected_filter = QtWidgets.QFileDialog.getSaveFileName(
                    self, "Video speichern", default_path, ";;".join(video_filters.keys())
                )
                if not path:
                    continue
                self._remember_export_dir(path)
                if not Path(path).suffix:
                    path += video_filters.get(selected_filter, ".mp4")

                # WebM erlaubt (anders als MP4/AVI) keinen H.264-Videostream --
                # imageio/ffmpeg wuerden sonst mit dem Default-Codec "libx264"
                # scheitern. VP9 ist im mitgelieferten ffmpeg-Binary enthalten und
                # produziert ein regelkonformes WebM.
                video_writer_kwargs = {"fps": fps}
                if Path(path).suffix.lower() == ".webm":
                    video_writer_kwargs["codec"] = "libvpx-vp9"
            else:
                folder = QtWidgets.QFileDialog.getExistingDirectory(
                    self, "Ordner für Bildstapel wählen", self._export_dir_hint()
                )
                if not folder:
                    continue
                self._remember_export_dir(folder)
                image_ext = dialog.image_format()
                # dialog.image_prefix() saeubert bereits selbst (sanitize_filename_prefix
                # in dialogs/filename_tokens.py, gemeinsam mit der Live-Vorschau im Dialog genutzt) --
                # Zeichen, die unter Windows/macOS/Linux in Dateinamen ungueltig sind
                # bzw. (bei "/" oder "\") ungewollt Unterordner erzeugen wuerden.
                image_prefix = dialog.image_prefix()
                export_timestamps = self._resolve_export_timestamps(image_prefix)
                if export_timestamps is None:
                    continue

                # Punkt 2 (Nutzerwunsch "volle Kontrolle ueber den Namen"): kein
                # automatisch angehaengter Zaehler mehr, wenn "IDX" fehlt --
                # stattdessen hier verbindlich (mit den TATSAECHLICH fuers
                # Rendern verwendeten Zeitstempeln) pruefen, ob das Muster fuer
                # den gewaehlten Frame-Bereich ueberhaupt eindeutige Namen
                # ergibt, und sonst nachfragen statt Dateien stillschweigend
                # gegenseitig zu ueberschreiben.
                if INDEX_TOKEN not in image_prefix:
                    digits = len(str(end_idx - start_idx + 1))
                    rendered_names = [
                        render_export_filename(
                            image_prefix, export_timestamps[idx],
                            (export_timestamps[idx] - export_timestamps[0]).total_seconds(),
                            idx - start_idx + 1, digits,
                        )
                        for idx in range(start_idx, end_idx + 1)
                    ]
                    if len(set(rendered_names)) < len(rendered_names):
                        box = QtWidgets.QMessageBox(self)
                        box.setIcon(QtWidgets.QMessageBox.Icon.Warning)
                        box.setWindowTitle("Dateiname nicht eindeutig")
                        box.setText(
                            f"Das Dateiname-Muster „{image_prefix}“ ergibt für mehrere der "
                            f"{len(rendered_names)} exportierten Frames denselben Namen -- spätere "
                            f"Frames würden frühere überschreiben. Soll „{INDEX_TOKEN}“ automatisch "
                            "angehängt werden (fortlaufende Nummer), oder möchtest du das Muster "
                            "selbst anpassen?"
                        )
                        btn_fix = box.addButton(
                            f"„{INDEX_TOKEN}“ anhängen", QtWidgets.QMessageBox.ButtonRole.AcceptRole
                        )
                        box.addButton("Selbst anpassen…", QtWidgets.QMessageBox.ButtonRole.RejectRole)
                        box.setDefaultButton(btn_fix)
                        box.exec()
                        if box.clickedButton() is btn_fix:
                            dialog.edit_image_prefix.setText(image_prefix + INDEX_TOKEN)
                        continue
            break

        # Aktuellen Anzeigezustand sichern, um ihn nach dem Export wiederherzustellen.
        prev_index = self.current_index
        prev_histogram_visible = self.histogram.isVisible()
        prev_level_state = self._capture_level_widgets_state()

        # Von der Rohdaten-Bereinigung ausgeblendete Bilder (siehe
        # data_cleaning_ops.py) werden im Regelfall NICHT mit exportiert --
        # sie bleiben in self.recording unveraendert (samt Zeitstempel) und
        # lassen sich dort jederzeit wieder einblenden, sollen aber wie in
        # den Kurven auch im Export/in der Wiedergabe uebersprungen werden.
        # Punkt 4 (Nutzerwunsch, NUR Video-Export): mit aktivierter
        # "Lücke füllen"-Option (freeze_excluded_pixels) bleibt JEDER Index
        # im Bereich erhalten (Frame-Anzahl/Zeitstempel im Video bleiben
        # dadurch unveraendert) -- pixel_source_for liefert stattdessen pro
        # ausgeblendetem Index den zuletzt SICHTBAREN Index, dessen Bilddaten
        # angezeigt werden sollen (Fallback vorwaerts, falls die Aufnahme mit
        # ausgeblendeten Bildern beginnt).
        pixel_source_for: dict[int, int] = {}
        if freeze_excluded_pixels:
            frame_indices = list(range(start_idx, end_idx + 1))
            # Sucht rueckwaerts UEBER DIE GESAMTE AUFNAHME (nicht nur
            # innerhalb des Exportbereichs) nach dem letzten sichtbaren Bild
            # VOR dem Bereich -- ohne diesen Seed wuerden ausgeblendete
            # Bilder ganz am ANFANG des Exportbereichs (oder ein komplett
            # ausgeblendeter Exportbereich, dessen sichtbare Nachbar-Bilder
            # ausserhalb liegen) mangels "vorherigem" Bild innerhalb des
            # Bereichs unaufgeloest bleiben und faelschlich ihre EIGENEN
            # (eigentlich ausgeblendeten) Pixel zeigen wuerden.
            last_visible = None
            if start_idx > 0:
                seed = self._skip_excluded_frame_index(start_idx - 1, -1)
                if seed not in self._excluded_frame_indices:
                    last_visible = seed
            pending: list[int] = []  # ausgeblendete Indizes ohne bisher bekanntes vorheriges Bild
            for i in frame_indices:
                if i not in self._excluded_frame_indices:
                    last_visible = i
                    for p in pending:
                        pixel_source_for[p] = i  # kein vorheriges sichtbares Bild -> naechstes danach
                    pending = []
                elif last_visible is not None:
                    pixel_source_for[i] = last_visible
                else:
                    pending.append(i)
            # pending bleibt nur dann unaufgeloest, wenn WEDER vor noch
            # innerhalb des Exportbereichs irgendein sichtbares Bild
            # existiert (sichtbare Bilder liegen dann ausschliesslich NACH
            # dem Bereich) -- dann zeigt der Aufrufer (siehe unten) mangels
            # sinnvollem Ersatz die eigenen (ausgeblendeten) Pixel dieses
            # Frames.
        else:
            frame_indices = [i for i in range(start_idx, end_idx + 1) if i not in self._excluded_frame_indices]
        if not frame_indices:
            QtWidgets.QMessageBox.information(
                self, "Keine Bilder",
                "Alle Bilder im gewählten Bereich sind von der Rohdaten-Bereinigung ausgeblendet "
                "(„Daten > Rohdaten säubern…“) -- bitte einzelne davon dort wieder einblenden oder "
                "einen anderen Bereich wählen."
            )
            return
        progress_label = "Video wird erstellt…" if output_mode == "video" else "Bildstapel wird erstellt…"
        progress = QtWidgets.QProgressDialog(progress_label, "Abbrechen", 0, len(frame_indices), self)
        progress.setWindowModality(QtCore.Qt.WindowModal)
        progress.setMinimumDuration(300)

        # Basis-Fuellfarbe der Video-/Bildstapel-Leinwand (Leerraum um das
        # Thermobild/zwischen Bild und Graph, Zeitanzeige-Streifen) -- das
        # Thermobild ist in JEDEM Export dabei (der Graph nur optional),
        # daher dessen feste, dunkle Farbe (siehe __init__/_apply_image_
        # colors). Das Thermobild-Segment rendert innerhalb dieser Flaeche
        # zuverlaessig mit seiner EIGENEN Hintergrundfarbe (siehe
        # _render_glw_segments_into_painter). Der optionale Graph-Bereich
        # dagegen NICHT von selbst (Bugreport: "Hintergrund im Graph
        # schwarz") -- _render_video_frame fuellt ihn deshalb explizit mit
        # graph_bg vor, bevor die Graph-Szene darauf gezeichnet wird (siehe
        # dort).
        bg = QtGui.QColor(self._image_bg)
        fg = QtGui.QColor(self._image_fg)
        graph_bg = QtGui.QColor(self._graph_bg)
        scale = 2.0  # feste, ordentliche Aufloesung fuer Video-/Bildstapel-Frames
        unix = self.recording.unix_seconds()
        # Fuer den Bildstapel schon geschriebene Dateien, um sie bei
        # Abbruch/Fehler wieder zu entfernen (analog zum einzelnen
        # Video-Pfad) -- sonst bliebe ein unvollstaendiger, verwirrender
        # Rest-Bildstapel im Zielordner liegen.
        written_paths: list[Path] = []
        cancelled = False
        error_message: str | None = None
        error_traceback: str | None = None
        try:
            # use_custom/Legende-Sichtbarkeit erst HIER (innerhalb des try)
            # anwenden -- siehe _export_single_graph/_export_combined_image
            # fuer den vollen Grund: sonst bliebe die Anzeige bei einem
            # Fehler hier dauerhaft im Export-Zustand haengen, weil das
            # wiederherstellende finally unten nie erreicht wird.
            if use_custom:
                self._apply_custom_color_dialog_state(dialog, prev_level_state)
            self.histogram.setVisible(show_legend)
            # _render_video_frame rendert pro Frame direkt ueber
            # QGraphicsScene.render() -- das sichtbare self.glw-Widget wird
            # dabei nie veraendert (kein Resize/Verstecken), es verschwindet
            # also waehrend des Renderns nicht mehr aus dem Hauptfenster.
            # Rundet Breite/Hoehe (inkl. optionalem Zeitanzeige-Streifen) auf
            # ein Vielfaches von 16 auf, damit ffmpeg das Bild nicht selbst
            # mit einer Warnung nachtraeglich vergroessern muss.
            #
            # _frozen_ui_during_export() steht bewusst GANZ AUSSEN (zuerst
            # betreten, zuletzt verlassen) -- alle Context-Manager danach
            # (Tab-Vordergrundholen, Kurven-/Achsen-/Zeitanzeige-Umschalten)
            # veraendern die sichtbaren Widgets, sollen dabei aber NIE
            # tatsaechlich auf dem Bildschirm sichtbar werden (siehe
            # _frozen_ui_during_export fuer den vollen Bugreport-Hintergrund).
            with self._frozen_ui_during_export(), \
                    self._maybe_hidden_live_cursor(include_cursor), \
                    self._temporary_scale_visuals(include_scale_ruler, selected_scale_numbers), \
                    (self._widget_raised_for_export(graph_widget) if graph_widget is not None
                     else contextlib.nullcontext()), \
                    (self._temporary_graph_content(selected_roi_numbers, include_live_curve)
                     if graph_widget is not None else contextlib.nullcontext()), \
                    (self._temporary_axis_override(graph_widget, axis_overrides)
                     if graph_widget is not None else contextlib.nullcontext()), \
                    (self._dual_time_axis_export(graph_widget)
                     if graph_widget is not None and overlay_mode == "both"
                     else self._temporary_time_display_mode(
                         graph_time_axis_mode if graph_widget is not None else None
                     )), \
                    self._paused_background_timers(), \
                    self._scaled_export_visuals(scale):
                # Bugfix: das Ein-/Ausblenden der oberen Zeitachse
                # (_dual_time_axis_export, "Beides") und das Hochholen einer
                # tabifizierten Dock-Registerkarte (_widget_raised_for_export)
                # loesen bei pyqtgraph eine ERST BEIM NAECHSTEN Event-Loop-
                # Durchlauf tatsaechlich wirksame Neuberechnung des Layouts
                # aus. Ohne diesen Aufruf hier zeigte GENAU der ERSTE
                # gerenderte Frame die obere Achse noch nicht (ab dem
                # zweiten Frame -- nach dem naechsten processEvents() in der
                # Schleife unten -- korrekt), da vorher noch kein
                # Event-Loop-Durchlauf stattgefunden hatte.
                QtWidgets.QApplication.processEvents()
                # EINMALIG (nicht pro Frame) berechnet -- siehe
                # _render_video_frame fuer den Grund (sonst leicht
                # unterschiedliche Bildgroessen zwischen Frames bei
                # automatischer Farbskalierung).
                first_idx = frame_indices[0]
                if first_idx in pixel_source_for:
                    self._show_frame(first_idx, pixel_source_for[first_idx])
                else:
                    self._show_frame(first_idx)
                segments = self._tight_glw_segments()
                if output_mode == "video":
                    with imageio.get_writer(path, **video_writer_kwargs) as writer:
                        for n, idx in enumerate(frame_indices):
                            if progress.wasCanceled():
                                cancelled = True
                                break
                            # Nur bei tatsaechlicher Ersatz-Pixelquelle (Punkt 4,
                            # "Lücke füllen") den zweiten Parameter mitgeben --
                            # der weitaus haeufigere Normalfall (kein Ausschluss)
                            # bleibt bewusst der bisherige Ein-Parameter-Aufruf.
                            if idx in pixel_source_for:
                                self._show_frame(idx, pixel_source_for[idx])
                            else:
                                self._show_frame(idx)
                            image = self._render_video_frame(
                                scale, bg, overlay_mode, idx, frame_indices, unix, segments,
                                graph_widget, graph_position, foreground=fg, graph_background=graph_bg,
                            )
                            writer.append_data(self._qimage_to_rgb_array(image))
                            progress.setValue(n + 1)
                            QtWidgets.QApplication.processEvents()
                else:
                    digits = len(str(len(frame_indices)))
                    for n, idx in enumerate(frame_indices):
                        if progress.wasCanceled():
                            cancelled = True
                            break
                        self._show_frame(idx)
                        image = self._render_video_frame(
                            scale, bg, overlay_mode, idx, frame_indices, unix, segments,
                            graph_widget, graph_position, foreground=fg, graph_background=graph_bg,
                        )
                        # Zeitstempel-Platzhalter (YYYY/MM/DD/hh/mm/ss) im
                        # Praefix werden mit dem Zeitstempel dieses Frames
                        # gefuellt (Nutzerwunsch) -- export_timestamps ist
                        # entweder direkt self.recording.timestamps (echter,
                        # aus dem Dateinamen erkannter Zeitstempel) oder,
                        # falls nicht verfuegbar, ein vom Nutzer bestaetigter
                        # Ersatz-Zeitplan (siehe _resolve_export_timestamps).
                        # Enthaelt der Praefix den Platzhalter IDX, wird die
                        # laufende Nummer GENAU dort eingesetzt; LAUFs/LAUFm/
                        # LAUFh (Punkt 6) analog durch die verstrichene
                        # Aufnahmezeit (ab export_timestamps[0], derselben
                        # Bezugsgroesse wie die "Laufzeit"-Spalte im Werte-
                        # Export) in der per Tokenname gewaehlten Einheit --
                        # beides gemeinsam ueber render_export_filename (siehe
                        # dialogs/filename_tokens.py). Ohne IDX bleibt das
                        # Muster exakt so stehen, wie eingegeben -- KEIN
                        # automatisch angehaengter Zaehler mehr (Nutzerwunsch:
                        # volle Kontrolle ueber den Dateinamen); dass das
                        # Muster in diesem Fall eindeutige Namen ergibt, ist
                        # bereits vor dieser Schleife geprueft (siehe
                        # Eindeutigkeits-Pruefung weiter oben, nutzt denselben
                        # Renderer).
                        rendered_prefix = render_export_filename(
                            image_prefix, export_timestamps[idx],
                            (export_timestamps[idx] - export_timestamps[0]).total_seconds(),
                            n + 1, digits,
                        )
                        frame_path = Path(folder) / f"{rendered_prefix}{image_ext}"
                        if not image.save(str(frame_path)):
                            raise OSError(f"Konnte Bild nicht speichern: {frame_path}")
                        written_paths.append(frame_path)
                        progress.setValue(n + 1)
                        QtWidgets.QApplication.processEvents()
        except Exception as exc:
            # Bewusst breit (statt nur OSError/RuntimeError/ValueError): der
            # Renderpfad pro Frame (Legende/Achsen-Zustand, Zeitstempel-
            # Platzhalter, Overlay-Zeichnung) kann auch andere Exception-Typen
            # werfen -- ohne diesen breiten Fang wuerde die Aufraeum-Logik
            # unten (unvollstaendige Video-/Bildstapel-Datei loeschen) bei
            # einem solchen Fehler uebersprungen und ein kaputter Rest liegen
            # bleiben, ohne dass der Nutzer je einen Fehlerdialog sieht.
            error_message = str(exc)
            error_traceback = traceback.format_exc()
            cancelled = True
        finally:
            progress.close()
            # Anzeigezustand wiederherstellen.
            self.histogram.setVisible(prev_histogram_visible)
            if use_custom:
                self._apply_level_widgets_state(prev_level_state)
            self._show_frame(prev_index)

        if error_message is not None:
            if output_mode == "video":
                Path(path).unlink(missing_ok=True)
            else:
                for p in written_paths:
                    p.unlink(missing_ok=True)
            what = "Video" if output_mode == "video" else "Bildstapel"
            box = QtWidgets.QMessageBox(self)
            box.setIcon(QtWidgets.QMessageBox.Icon.Critical)
            box.setWindowTitle("Fehler")
            box.setText(f"{what} konnte nicht gespeichert werden:\n{error_message}")
            # Siehe _show_export_error (export_common.py) fuer die Begruendung:
            # str(exc) allein (z.B. "unsupported operand type(s) for -:
            # 'NoneType' and 'int'") gibt keinerlei Hinweis auf Datei/Zeile --
            # der Fehler wird sonst hier gefangen und weiter unten (nach dem
            # Aufraeumen der unvollstaendigen Datei/des Bildstapels) erst
            # angezeigt, daher wird der Traceback bereits im except-Block oben
            # gesichert (sys.exc_info() waere an dieser Stelle nicht mehr
            # zuverlaessig verfuegbar).
            if error_traceback:
                box.setDetailedText(error_traceback)
            box.exec()
            return
        if cancelled:
            if output_mode == "video":
                Path(path).unlink(missing_ok=True)
            else:
                for p in written_paths:
                    p.unlink(missing_ok=True)
            what = "Video-Export" if output_mode == "video" else "Bildstapel-Export"
            self.statusBar().showMessage(f"{what} abgebrochen.")
            return

        if output_mode == "video":
            self.statusBar().showMessage(f"Video gespeichert: {path}")
        else:
            self.statusBar().showMessage(f"Bildstapel gespeichert: {len(written_paths)} Bilder in {folder}")

