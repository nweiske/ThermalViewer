"""MainWindow: setzt die Klasse aus den fachlichen Mixins dieses Pakets
zusammen und enthält deren gemeinsamen Konstruktor."""
from __future__ import annotations

from pathlib import Path

import pyqtgraph as pg
from qtpy import QtCore, QtGui, QtWidgets

from ..assets import ICON_PATH
from ..data import (
    DEFAULT_FILENAME_TEMPLATE,
    Recording,
    compile_filename_template,
    validate_filename_template,
)
from ..measurement import DraggableTextItem
from ..roi_entry import (
    RoiEntry,
)
from .constants import (
    DEFAULT_THEME,
    THEMES,
)
from .export_common import _ExportCommonMixin
from .export_visuals import _ExportVisualsMixin
from .export_csv import _CsvExportMixin
from .export_image import _ImageExportMixin
from .data_cleaning_ops import _DataCleaningMixin
from .export_video import _VideoExportMixin
from .frame_nav import _FrameNavMixin
from .graph_cursor_ops import _GraphCursorMixin
from .layer_tabs_ops import _LayerTabsMixin
from .shrinkage_ops import _ShrinkageMixin
from .import_ops import _ImportMixin
from .measurement_ops import _MeasurementMixin
from .mouse_ops import _MouseMixin
from .project_io import _ProjectMixin
from .render_pipeline import _RenderPipelineMixin
from .roi_ops import _RoiMixin
from .roi_panel_build import _RoiPanelBuildMixin
from .status_activity import _StatusActivityMixin
from .theming import _ThemeMixin
from .ui_build import _UIBuildMixin
from .ui_build_menu import _UIBuildMenuMixin

pg.setConfigOptions(imageAxisOrder="row-major", antialias=True)


class MainWindow(
    _UIBuildMixin,
    _UIBuildMenuMixin,
    _RoiPanelBuildMixin,
    _StatusActivityMixin,
    _ThemeMixin,
    _ImportMixin,
    _ProjectMixin,
    _FrameNavMixin,
    _RoiMixin,
    _MeasurementMixin,
    _DataCleaningMixin,
    _GraphCursorMixin,
    _LayerTabsMixin,
    _ShrinkageMixin,
    _MouseMixin,
    _ExportCommonMixin,
    _ExportVisualsMixin,
    _RenderPipelineMixin,
    _ImageExportMixin,
    _CsvExportMixin,
    _VideoExportMixin,
    QtWidgets.QMainWindow,
):
    # Reduzierter Stiftbreiten-Skalierungsfaktor NUR fuer den SVG-Export
    # (siehe _scaled_export_visuals) -- Vektor-Linien wirken bei identischer
    # Pixelbreite optisch kraeftiger als die entsprechende (leicht
    # antialiaste) Raster-Linie. Bugreport ("Kurvenlinien im SVG-Export
    # etwas zu dick", siehe datasets/Zeitverlauf_mit_Position_I_Kurve.svg --
    # stroke-width="4" bei 300 DPI): 0.65 war noch zu hoch, mit 0.5 ergibt
    # sich bei 300 DPI eine sichtbar duennere stroke-width="3", waehrend die
    # Standard-Aufloesung (150 DPI, stroke-width="2") unveraendert bleibt.
    _SVG_PEN_SCALE_FACTOR = 0.5

    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("Thermo-Sequenz-Viewer")
        self.setWindowIcon(QtGui.QIcon(str(ICON_PATH)))
        self.resize(1600, 950)

        self.recording: Recording | None = None
        self.current_index = 0
        self._armed_entry: RoiEntry | None = None
        # Linksklick ins Bild fixiert den Live-Cursor auf dieser Stelle (Verlauf
        # bleibt stehen, Mausbewegung wird ignoriert); Rechtsklick hebt die
        # Fixierung wieder auf und die Live-Ansicht folgt wieder der Maus.
        self._live_pinned = False
        self._hover_row: int | None = None
        self._hover_col: int | None = None
        # Kantenlaenge (ungerade Pixelzahl) des um das Cursor-Pixel
        # gemittelten Bereichs fuer Live-Verlauf/-Anzeige (Werkzeuge-Menue
        # "Live-Cursor-Bereichsgröße") -- Standard: 5x5.
        self._live_cursor_kernel_size = 5
        self.roi_entries: list[RoiEntry] = []
        # Naechste zu vergebende 1-basierte Erzeugungsnummer (siehe
        # RoiEntry.number) -- steigt monoton, auch nach Entfernen von
        # Messbereichen, damit Standardname/Farbe neuer ROIs nie eine
        # zuvor bereits vergebene Nummer wiederverwenden.
        self._roi_next_number = 1
        self._window_theme = DEFAULT_THEME
        # Graphen (Zeitverlauf/Live) und Thermobild haben JEWEILS eine per
        # Ansichts-Manager (Punkt 5) unabhaengig waehlbare Hell/Dunkel-
        # Farbgebung -- getrennt vom App-Design (Hell-/Dunkelmodus-Schalter,
        # betrifft nur die UI-Oberflaeche) UND voneinander. Platzhalter hier
        # (Graph hell/Thermobild dunkel, das bisherige feste Verhalten) --
        # die tatsaechlich geltenden, ggf. aus QSettings wiederhergestellten
        # Werte setzen _apply_image_theme/_apply_graph_theme etwas weiter
        # unten in __init__ (nach _build_control_panel, da deren Comboboxen
        # existieren muessen).
        self._image_theme = "dark"
        self._graph_theme = "light"
        self._graph_bg = THEMES["light"]["pg_background"]
        self._graph_fg = THEMES["light"]["pg_foreground"]
        self._image_bg = THEMES["dark"]["pg_background"]
        self._image_fg = THEMES["dark"]["pg_foreground"]
        # Min/Max ueber alle Frames der aktuellen Aufnahme (Punkt 1), einmalig
        # beim Laden berechnet.
        self._global_level_range: tuple[float, float] | None = None
        # Maßstab (Punkt 12): mm pro Pixel, None = kein Maßstab definiert.
        self._px_to_mm: float | None = None
        # Reale Laenge (mm) der Referenzlinie, UNABHAENGIG von ihrer aktuellen
        # Pixel-Distanz (Punkt 11) -- beim Ziehen der Endpunkte bleibt dieser
        # Wert konstant und _px_to_mm wird aus der neuen Pixel-Distanz neu
        # berechnet; beim Doppelklick-Bearbeiten ist es umgekehrt.
        self._ruler_mm_value: float | None = None
        self._ruler_armed = False
        self._ruler_start: tuple[float, float] | None = None
        # Waehrend der Klick-Klick-Erstellung (siehe _handle_ruler_click) nur
        # ein einfacher, nicht interaktiver Vorschau-Marker -- die fertige
        # Linie (self._ruler_line) ist ein ziehbares LineSegmentROI.
        self._ruler_preview_marker: pg.PlotDataItem | None = None
        self._ruler_line: pg.LineSegmentROI | None = None
        self._ruler_text: DraggableTextItem | None = None
        # None = Beschriftung folgt automatisch dem Linien-Mittelpunkt; nach
        # manuellem Verschieben (Punkt 9) haelt dieser Versatz relativ zum
        # Mittelpunkt fest (siehe DraggableTextItem/_on_ruler_label_moved).
        self._ruler_label_offset: QtCore.QPointF | None = None
        # Anpassbar (siehe btn_ruler_color), da eine feste Farbe bei manchen
        # Farbverlaeufen (z.B. "Hot") auf der Referenzlinie kaum zu erkennen
        # waere.
        self._ruler_color = "#ff2d55"
        # Punkt 5 (Nutzerwunsch): eine einzelne Checkbox blendet Maßstab-Linie
        # UND alle Messungen gemeinsam im Thermobild aus/ein, ohne sie zu
        # loeschen -- siehe chk_scale_visible/_on_toggle_scale_visuals. Gilt
        # nur fuer AKTUELL gueltige (also nach dem letzten Laden neu
        # definierte) Visualisierungen; durch _set_recording bereits
        # ausgeblendete, auf die vorherige Aufnahme bezogene Alt-Geometrie
        # wird dadurch nicht wieder eingeblendet (siehe dortiger Kommentar).
        self._scale_visuals_visible = True
        # Punkt 3 (Nutzerwunsch): merkt sich die zuletzt echt vom Nutzer
        # gewaehlte Automatik-Unterwahl (Pro Bild/Über gesamte Messung), waehrend
        # "Manuell" aktiv ist -- siehe _on_manual_level_radio_toggled.
        self._auto_submode_before_manual: str | None = None
        # Mess-Werkzeuge (Punkt 1 Folgeanfrage zu Punkt 12, seit Punkt 8
        # "beliebig viele (Größen-)Messungen gleichzeitig" eine LISTE statt
        # einer einzelnen Messung, siehe self.measurements/MeasurementEntry
        # in _build_control_panel): nutzen einen bereits definierten Maßstab
        # (_px_to_mm) nur LESEND, um beliebige Strecken im Bild in mm
        # anzuzeigen -- im Gegensatz zum Lineal-Werkzeug oben wird dabei nie
        # _px_to_mm (neu) gesetzt. Die folgenden drei Felder gelten nur
        # WAEHREND der Zwei-Klick-Erfassung einer NEUEN Messung (danach lebt
        # der Zustand in der jeweiligen MeasurementEntry).
        self._measurement_armed = False
        self._measurement_start: tuple[float, float] | None = None
        self._measurement_preview_marker: pg.PlotDataItem | None = None
        # Rohdaten-Bereinigung (Nutzerwunsch: "vor der eigentlichen Auswertung
        # säubern" -- einzelne Ausreißer-Bilder, z.B. durch eine kurze
        # Kamera-/Übertragungsstörung, per dT-Schwellenwert an frei markierten
        # Referenzpunkten erkennen und aus Kurven/Wiedergabe/Export ausblenden,
        # OHNE sie oder ihre Zeitstempel wirklich zu loeschen -- jederzeit über
        # "Daten > Rohdaten säubern…" einzeln wieder einblendbar). Siehe
        # data_cleaning_ops.py für die vollständige Logik.
        self._cleaning_pick_armed = False
        self._cleaning_points: list[tuple[int, int]] = []
        # Pro Punkt ein dict {"dot": pg.TargetItem, "label": pg.TextItem,
        # "area": QGraphicsRectItem|None} -- gleiche Reihenfolge wie
        # _cleaning_points, siehe data_cleaning_ops.py.
        self._cleaning_point_items: list[dict] = []
        self._cleaning_threshold = 5.0
        # Kantenlaenge (ungerade Pixelzahl, wie beim Live-Cursor -- siehe
        # _live_cursor_kernel_size in mouse_ops.py, aber bewusst EIGENSTAENDIG
        # statt gemeinsam genutzt, da unterschiedliche Zwecke) des um jeden
        # Referenzpunkt gemittelten Bereichs: Standard 3x3 statt eines reinen
        # Einzelpixels -- robuster gegen Sensor-Rauschen an genau EINEM Pixel
        # (Nutzerwunsch, auf Rueckfrage bestaetigt).
        self._cleaning_kernel_size = 3
        # Rein visuelle Einstellung (nicht in .tvproj gespeichert): zeigt den
        # gemittelten Bereich als gestricheltes Rechteck um jeden Punkt an,
        # sofern _cleaning_kernel_size > 1 -- bei 1x1 gäbe es nichts
        # zusätzlich zum Punkt-Kreuz selbst zu zeigen.
        self._cleaning_show_kernel_area = True
        # Verknuepfungslogik mehrerer Referenzpunkte (Nutzerwunsch): "and"
        # (Standard, bisheriges Verhalten) verlangt eine Ueberschreitung AN
        # JEDEM Punkt, "or" bereits an EINEM einzigen Punkt -- siehe
        # _compute_cleaning_candidates in data_cleaning_ops.py.
        self._cleaning_logic = "and"
        self._excluded_frame_indices: set[int] = set()
        self._cleaning_dialog = None
        # Registerkarten ("Ebenen"/Masken) über dem Thermobild (Nutzerwunsch:
        # "das Thermobild wird recht voll") -- siehe layer_tabs_ops.py. "all"
        # entspricht dem bisherigen, ungefilterten Verhalten (alles sichtbar).
        self._active_layer_tab = "all"
        # Schwindungsmessung (Punkt 9, Nutzerwunsch, experimentell -- siehe
        # shrinkage_ops.py): EINE aktive Messung (wie das Maßstab-Werkzeug),
        # standardmaessig deaktiviert/ausgeblendet, um Nutzer, die sie nicht
        # brauchen, nicht mit drei zusaetzlichen Bild-Bereichen zu stoeren.
        self._shrinkage_enabled = False
        self._shrinkage_ref_frame: int | None = None
        # Messart (Nutzerwunsch, zusaetzlich zur Breitenmessung): "width"
        # (Standard, bisheriges Verhalten, zwei verfolgte Flanken-Boxen) oder
        # "area" (EIN Bereich, Probe wird je Bild per Schwellenwert
        # segmentiert und die Pixelzahl als Flaeche gezaehlt -- von Haus aus
        # geometrieunabhaengig, siehe shrinkage_ops.py).
        self._shrinkage_mode = "width"
        # Zeitachsen-Anzeige beider Kurven-Graphen: "clock" (echte Uhrzeit,
        # Standard) oder "runtime" (relative Laufzeit ab Aufnahmebeginn) --
        # ueber je einen Umschalter unten rechts an beiden Graphen wählbar,
        # gemeinsam synchronisiert (siehe _apply_time_display_mode).
        self._time_display_mode = "clock"
        # Format der Laufzeit-Anzeige (Nutzerwunsch: "dritte Zeitachse" mit
        # frei waehlbarer, fortlaufender Einheit statt hh:mm:ss, um die
        # Laufzeit ohne manuelles Umrechnen in anderer Software weiter-
        # verarbeiten zu koennen) -- "hhmmss" (Standard) oder "s"/"min"/"h".
        # EIN globales Format statt einer eigenen Auswahl je Export-Manager:
        # wirkt automatisch ueberall dort, wo "Laufzeit" angezeigt wird
        # (Graph-Achse, Video-/Bildstapel-Export, CSV-Export, Statuszeile),
        # siehe _apply_runtime_unit/_format_runtime.
        self._runtime_unit = "hhmmss"
        # Manuell festlegbarer Start/Ende der Auswertung (0-basierter
        # Frame-Index, None solange keine Aufnahme geladen ist) -- Standard
        # ist der erste bzw. jeweils letzte geladene Frame, per Spinbox oder
        # direktem Ziehen an der gruenen/roten Markierung im Frame-Regler
        # aenderbar (z.B. wenn eine Aufnahme ueber den eigentlich
        # interessanten Zeitraum hinaus weiterlief). Steuert u.a. das Ziel
        # von "Start"/"Ende festlegen" bei der Verlaufs-Interpolation
        # (_jump_to_first_frame/_jump_to_last_frame) sowie den standardmaessig
        # auf diesen Bereich begrenzten Wiedergabe-Loop (_play_clamped, siehe
        # _on_play_toggled).
        self._eval_start_index: int | None = None
        self._eval_end_index: int | None = None
        # Waehrend einer laufenden Wiedergabe: True, wenn die Wiedergabe beim
        # Play-Start innerhalb von [_eval_start_index, _eval_end_index] stand
        # und deshalb an diesem Bereich geloopt/gestoppt wird; wurde der
        # Cursor manuell AUSSERHALB dieses Bereichs positioniert, laeuft die
        # Wiedergabe stattdessen ungeklemmt bis zum tatsaechlichen Ende.
        self._play_clamped = False
        # Live-Ordner-Ueberwachung (Programm soll parallel zu einer laufenden
        # Messung nutzbar sein): laeuft immer automatisch im Hintergrund,
        # sobald ein Ordner geladen ist (_open_folder/_load_folder) -- keine
        # separate Einstellung dafuer, da es keinen Nachteil hat, wenn gerade
        # nichts Neues dazukommt (_check_for_new_files kehrt dann sofort
        # zurueck). Ein einfacher 10s-Timer statt eines Dateisystem-
        # Watchers, damit auch sehr haeufig neu abgelegte Dateien (z.B. alle
        # 500ms) die App nicht durch staendiges Nachladen bremsen.
        self._watched_folder: Path | None = None
        self._live_watch_timer = QtCore.QTimer(self)
        self._live_watch_timer.setInterval(10_000)
        self._live_watch_timer.timeout.connect(self._check_for_new_files)

        self._settings = QtCore.QSettings("ThermalViewer", "ThermalViewer")

        self._build_image_canvas()
        self._build_plots()
        self._build_roi_entries()
        self._build_control_panel()
        # Erst jetzt existieren alle Panel-Abschnitte (scale_box/
        # _shrinkage_groupbox/roi_split), auf die _apply_layer_tab_visibility
        # zugreift -- Default-Tab "all" aendert am Startzustand nichts
        # sichtbar (alles bleibt an), macht den Zustand aber von Anfang an
        # konsistent.
        self._apply_layer_tab_visibility()
        self._build_toolbar()
        self._build_docks()
        self._build_menu()
        self._build_shortcuts()
        self._build_status_activity()
        self._connect_scene_events()
        self._build_graph_cursor_overlay()

        # Fuer die beiden Kurven-Graphen soll ein Rechtsklick "Exportieren"
        # exakt denselben Weg wie der Export-Menü-Punkt "Grafik
        # exportieren…" nehmen (inkl. Kombiniert/Getrennt- und Graph-Inhalt-
        # Auswahl) -- nicht nur einen aehnlich aussehenden, aber auf diesen
        # einen Graphen beschraenkten Dialog. Fuer das Thermobild selbst
        # gibt es keinen direkten Menü-Eintrag -- bleibt daher beim
        # bisherigen, auf dieses eine Widget beschraenkten Einzel-Export.
        self._bind_native_export(self.glw, suggested_name="Thermobild.png")
        self._bind_native_export(self.timeseries_plot, self._export_graphic)
        # self.live_plot bekommt bewusst KEIN eigenes Rechtsklick-Export-
        # Binding mehr -- es hat ohnehin nie sein eigenes Grafik-Export-
        # Fenster gehabt (_export_graphic exportiert immer self.timeseries_plot,
        # unabhaengig davon, ueber welches der beiden Widgets es ausgeloest
        # wurde) und ist seit Entfernung des redundanten "Live (Cursor)"-Docks
        # (siehe _build_docks) ohnehin nie sichtbar/rechtsklickbar.

        # _build_image_canvas() setzt vorlaeufig die rohe, unkorrigierte
        # Farbpalette (kein combo_cmap/chk_cmap_invert existierte zu dem
        # Zeitpunkt noch) -- jetzt einmalig durch die tatsaechliche Logik
        # (inkl. COLORMAPS_BASE_REVERSED-Korrektur) ersetzen.
        self._apply_colormap()

        saved_kernel_size = self._settings.value("live_cursor/kernel_size", 5, type=int)
        if saved_kernel_size in self._live_cursor_kernel_actions:
            self._live_cursor_kernel_size = saved_kernel_size
            self._live_cursor_kernel_actions[saved_kernel_size].setChecked(True)

        saved_window_theme = self._settings.value("window_theme", DEFAULT_THEME)
        self._apply_window_theme(saved_window_theme if saved_window_theme in THEMES else DEFAULT_THEME)
        # Graphen-/Thermobild-Farben sind seit dem Nutzerwunsch "Graph immer
        # hell, Thermobild immer dunkel" NICHT mehr Teil von _apply_window_theme,
        # sondern seit dem Ansichts-Manager (Punkt 5) je eigenstaendig per
        # QSettings gemerkt -- Vorgabewerte (Graph hell/Thermobild dunkel)
        # entsprechen dem bisherigen festen Verhalten, bleiben also fuer
        # bestehende Nutzer unveraendert, sind aber jetzt umschaltbar (siehe
        # "Ansicht"-Menue in _build_menu, _apply_image_theme/_apply_graph_theme).
        saved_image_theme = self._settings.value("image_theme", "dark")
        saved_graph_theme = self._settings.value("graph_theme", "light")
        self._apply_image_theme(saved_image_theme if saved_image_theme in THEMES else "dark")
        self._apply_graph_theme(saved_graph_theme if saved_graph_theme in THEMES else "light")

        saved_runtime_unit = self._settings.value("runtime_unit", "hhmmss")
        self._apply_runtime_unit(saved_runtime_unit if saved_runtime_unit in ("hhmmss", "s", "min", "h") else "hhmmss")

        saved_time_mode = self._settings.value("time_display_mode", "clock")
        self._apply_time_display_mode(saved_time_mode if saved_time_mode in ("clock", "runtime") else "clock")

        # Dateinamens-Schema (Punkt 5): standardmaessig "Record_YYYY-MM-DD_
        # hh-mm-ss", per QSettings dauerhaft ueberschreibbar (siehe
        # FilenameTemplateDialog/_set_filename_template). _active_* haelt
        # zusaetzlich fest, welches Schema die AKTUELL geladene Aufnahme
        # tatsaechlich verwendet hat -- kann vom Standard abweichen, wenn der
        # Nutzer beim letzten "Ordner öffnen…" ein nur EINMALIG (nicht
        # dauerhaft) geltendes Schema gewaehlt hat; die Live-Ordner-
        # Ueberwachung (_check_for_new_files) muss dieses (nicht das
        # Standard-)Schema weiterverwenden, sonst koennten neu hinzukommende
        # Dateien derselben Aufnahme nicht mehr korrekt eingeordnet werden.
        saved_template = self._settings.value("filename_template", None)
        if isinstance(saved_template, str) and validate_filename_template(saved_template) is None:
            self._filename_template = saved_template
        else:
            self._filename_template = DEFAULT_FILENAME_TEMPLATE
        self._filename_pattern, self._filename_strptime_fmt = compile_filename_template(self._filename_template)
        self._active_filename_pattern = self._filename_pattern
        self._active_filename_strptime_fmt = self._filename_strptime_fmt

        # Datenimport-Manager (Punkt: "Programm zeitnah auf andere Dateien
        # erweitern ... Import-Manager, mit dem wir Dateien lesen und zum
        # Einladen vorbereiten koennen"): analog zum Namensschema oben ein
        # global per QSettings persistierbares Standard-Rohformat
        # (Trennzeichen/Dezimaltrennzeichen/Kodierung/Kopf-Fusszeilen/
        # Spalten), plus _active_import_settings fuer das Format, mit dem
        # die AKTUELL geladene Aufnahme tatsaechlich geladen wurde (siehe
        # _check_for_new_files -- Live-Ordner-Ueberwachung muss konsistent
        # dasselbe Format weiterverwenden).
        self._import_settings = self._load_import_settings()
        self._active_import_settings = self._import_settings

