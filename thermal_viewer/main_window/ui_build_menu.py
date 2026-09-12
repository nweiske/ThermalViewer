"""Aufbau der Menüleiste (Datei/Daten/Ansicht/Werkzeuge/Export) -- eigene Datei
(abgespalten von ui_build.py, das den Bildbereich/die Graphen/Docks baut),
da die Menüleiste ein klar eigenständiger, rein additiver Aufbauschritt ist."""
from __future__ import annotations

from functools import partial

from qtpy import QtGui

from ..plot_items import (
    _StaysOpenMenu,
)


class _UIBuildMenuMixin:
    def _build_menu(self) -> None:
        # Aktionen, die ohne geladene Messreihe ohnehin nur eine "Keine Daten"-
        # Meldung anzeigen wuerden, werden bis zum ersten Laden ausgegraut
        # (siehe _set_recording) -- klarer als ein Klick ins Leere. Jede der
        # fuenf Menue-Aufbau-Methoden unten haengt ihre eigenen "braucht eine
        # Messreihe"-Aktionen an dieselbe Liste an.
        self._requires_recording_actions: list[QtGui.QAction] = []
        self._build_file_menu()
        self._build_data_menu()
        self._build_view_menu()
        self._build_tools_menu()
        self._build_export_menu()
        for action in self._requires_recording_actions:
            action.setEnabled(False)

    def _build_file_menu(self) -> None:
        file_menu = self.menuBar().addMenu("&Datei")
        act_open_folder = file_menu.addAction("Ordner öffnen…")
        act_open_folder.triggered.connect(self._open_folder)
        file_menu.addSeparator()
        act_save_project = file_menu.addAction("Projekt speichern…")
        act_save_project.setToolTip(
            "Speichert Messbereiche (Position, Name, Farbe), Farbverlauf und Legenden-Limits "
            "in einer Projektdatei."
        )
        act_save_project.triggered.connect(self._save_project)
        act_load_project = file_menu.addAction("Projekt laden…")
        act_load_project.setToolTip(
            "Wendet eine gespeicherte Projektdatei an -- ist noch keine Messreihe geladen, wird "
            "deren gespeicherter Quellordner automatisch mitgeladen (falls noch vorhanden)."
        )
        act_load_project.triggered.connect(self._load_project)
        file_menu.addSeparator()
        act_quit = file_menu.addAction("Beenden")
        act_quit.triggered.connect(self.close)
        self._requires_recording_actions.append(act_save_project)

    def _build_data_menu(self) -> None:
        # Eigenes Menue fuer alles, was die geladenen ROHDATEN selbst
        # betrifft (nicht nur das FENSTER-Layout wie "Ansicht" oder
        # Einzel-Werkzeuge wie "Werkzeuge") -- Nutzerwunsch: "ein neuer
        # Menüpunkt oben in der Menüleiste ... 'Daten' ... wo man auch den
        # Zugriff auf die ganzen TIFF-Import-Optionen hätte".
        data_menu = self.menuBar().addMenu("&Daten")
        act_clean_data = data_menu.addAction("Rohdaten säubern…")
        act_clean_data.setToolTip(
            "Erkennt einzelne Ausreißer-Bilder (z.B. durch eine kurze Kamera-/Übertragungsstörung) "
            "anhand der Temperaturänderung zu frei markierten Referenzpunkten und blendet sie aus "
            "Kurven/Wiedergabe/Export aus -- ohne sie oder ihre Zeitstempel zu löschen, jederzeit "
            "einzeln wieder einblendbar."
        )
        act_clean_data.triggered.connect(self._open_data_cleaning_dialog)
        self._requires_recording_actions.append(act_clean_data)
        data_menu.addSeparator()
        act_import_tiff = data_menu.addAction("TIFF-Bilder importieren…")
        # Vorerst deaktiviert (Nutzerwunsch: "ausgrauen und erstmal tot liegen
        # lassen") -- Funktion/Code bleiben unangetastet fuer eine spaetere
        # Ueberarbeitung, nur der Menuepunkt ist bis dahin nicht anklickbar.
        act_import_tiff.setEnabled(False)
        act_import_tiff.setToolTip(
            "Vorübergehend deaktiviert.\n\n"
            "Wandelt einzelne Graustufen-TIFF-Bilder (z.B. ein unkoloriertes „Intensität (DL)“-"
            "Rohbild ohne eingebettete Kalibrierung) in Messdateien im normalen Format um -- "
            "erfordert eine MANUELL angegebene Min-/Max-Temperatur (unkalibrierte Schätzung, "
            "Auswertung auf eigene Gefahr) sowie einen Bildausschnitt ohne Farbskala/Legende."
        )
        act_import_tiff.triggered.connect(self._import_tiff_images)

    def _build_view_menu(self) -> None:
        # _StaysOpenMenu (statt einer per addMenu(str) erzeugten normalen
        # QMenu): dieses Menue enthaelt mehrere unabhaengige Checkboxen
        # (Panel-Sichtbarkeit, Dunkelmodus) -- bleibt nach jedem Ankreuzen
        # offen, statt sich wie ein Standard-QMenu sofort zu schliessen.
        view_menu = _StaysOpenMenu("&Ansicht", self)
        self.menuBar().addMenu(view_menu)
        view_menu.addAction(self.control_dock.toggleViewAction())
        view_menu.addAction(self.timeseries_dock.toggleViewAction())

        view_menu.addSeparator()
        # Zwei Voreinstellungs-Knoepfe (Folgeanfrage: "ich möchte zwei
        # 'Default'-Settings haben (Light-/Dark), die die GESAMTE UI
        # umstellen, UND Buttons, mit denen ich dieselben UI-Elemente
        # gezielt und unabhängig voneinander umstellen kann") -- setzen
        # Fenster-, Thermobild- UND Graph-Farbschema (siehe die drei
        # Untermenues darunter) in einem Rutsch auf denselben Wert. Bewusst
        # NICHT checkable/exklusiv: nach spaeteren unabhaengigen Aenderungen
        # an nur EINEM der drei Untermenues gibt es keinen einzelnen
        # gemeinsamen Zustand mehr, den ein Haekchen sinnvoll anzeigen
        # koennte -- diese beiden Knoepfe sind reine "Auf einen Schlag
        # zuruecksetzen"-Aktionen, kein dauerhafter Schalter.
        act_theme_all_light = view_menu.addAction("Alles: Hell")
        act_theme_all_light.setToolTip(
            "Setzt Fenster, Thermobild UND Graph gemeinsam auf Hell -- alle drei bleiben danach "
            "trotzdem weiterhin über die eigenen Untermenüs darunter unabhängig voneinander "
            "veränderbar."
        )
        act_theme_all_light.triggered.connect(partial(self._apply_default_theme, "light"))
        act_theme_all_dark = view_menu.addAction("Alles: Dunkel")
        act_theme_all_dark.setToolTip(
            "Setzt Fenster, Thermobild UND Graph gemeinsam auf Dunkel -- alle drei bleiben danach "
            "trotzdem weiterhin über die eigenen Untermenüs darunter unabhängig voneinander "
            "veränderbar."
        )
        act_theme_all_dark.triggered.connect(partial(self._apply_default_theme, "dark"))

        # Unabhaengig von den beiden "Alles: ..."-Knoepfen oben waehlbares
        # Hell/Dunkel fuer Fenster, Thermobild UND Graph JEWEILS EINZELN
        # (Punkt 5, "Ansichts-Manager", Nutzerwunsch: "Thermobild, der Graph
        # und die UI sollen frei und unabhaengig voneinander jeweils im
        # Dark-/Lightmode erscheinen können") -- je ein Untermenue mit zwei
        # sich gegenseitig ausschliessenden Optionen, analog zu "Live-
        # Cursor-Bereichsgröße" im Werkzeuge-Menue (siehe kernel_menu/
        # kernel_group weiter unten). Reihenfolge Fenster -> Thermobild ->
        # Graph: von aussen (Fensterrahmen) nach innen (Bildinhalte).
        self._window_theme_actions: dict[str, QtGui.QAction] = {}
        window_theme_menu = view_menu.addMenu("Fenster-Farbschema")
        window_theme_menu.setToolTip(
            "Hintergrund-/Schriftfarbe von Fenster, Menüs und Panels -- unabhaengig von "
            "Thermobild und Graph darunter, siehe auch die beiden \"Alles: ...\"-Knöpfe oben, "
            "die alle drei gemeinsam auf einen Schlag umschalten."
        )
        window_theme_group = QtGui.QActionGroup(self)
        window_theme_group.setExclusive(True)
        for key, label in (("light", "Hell"), ("dark", "Dunkel")):
            act = window_theme_menu.addAction(label)
            act.setCheckable(True)
            # Reihenfolge wichtig: addAction() MUSS vor triggered.connect()
            # passieren -- siehe ausfuehrliche Begruendung bei
            # image_theme_group weiter unten.
            window_theme_group.addAction(act)
            act.triggered.connect(partial(self._apply_window_theme, key))
            self._window_theme_actions[key] = act

        self._image_theme_actions: dict[str, QtGui.QAction] = {}
        image_theme_menu = view_menu.addMenu("Thermobild-Farbschema")
        image_theme_menu.setToolTip(
            "Hintergrund-/Schriftfarbe des Thermobilds -- unabhaengig vom Fenster-Farbschema "
            "oben und vom Graphen, gilt auch für alle Exporte (Bild/Video/Bildstapel)."
        )
        image_theme_group = QtGui.QActionGroup(self)
        image_theme_group.setExclusive(True)
        for key, label in (("light", "Hell"), ("dark", "Dunkel")):
            act = image_theme_menu.addAction(label)
            act.setCheckable(True)
            # Reihenfolge wichtig: addAction() MUSS vor triggered.connect()
            # passieren -- sonst laeuft unser eigener Slot (der die geklickte
            # Action per setChecked(True) erneut bestaetigt) VOR dem internen
            # Abwaehl-Mechanismus der Gruppe, wodurch die zuvor aktive Action
            # faelschlich angehakt bleibt (Bugreport: "der Punkt bleibt bei
            # 'hell' drin", per Testskript reproduziert und verifiziert).
            image_theme_group.addAction(act)
            act.triggered.connect(partial(self._apply_image_theme, key))
            self._image_theme_actions[key] = act

        self._graph_theme_actions: dict[str, QtGui.QAction] = {}
        graph_theme_menu = view_menu.addMenu("Graph-Farbschema")
        graph_theme_menu.setToolTip(
            "Hintergrund-/Schriftfarbe der Kurven-Graphen (Zeitverlauf/Live) -- unabhaengig vom "
            "Fenster-Farbschema oben und vom Thermobild, gilt auch für alle Exporte "
            "(Bild/Video/Bildstapel)."
        )
        graph_theme_group = QtGui.QActionGroup(self)
        graph_theme_group.setExclusive(True)
        for key, label in (("light", "Hell"), ("dark", "Dunkel")):
            act = graph_theme_menu.addAction(label)
            act.setCheckable(True)
            # Reihenfolge wichtig, siehe image_theme_group weiter oben.
            graph_theme_group.addAction(act)
            act.triggered.connect(partial(self._apply_graph_theme, key))
            self._graph_theme_actions[key] = act

    def _build_tools_menu(self) -> None:
        tools_menu = self.menuBar().addMenu("&Werkzeuge")
        act_import_settings = tools_menu.addAction("Datenimport anpassen…")
        act_import_settings.setToolTip(
            "Datenimport-Manager: bereitet Messdateien mit abweichendem Rohformat (z.B. "
            "zusätzliche Kopfzeilen, eine führende Index-Spalte, anderes Trennzeichen) fürs "
            "Einlesen vor -- mit Live-Vorschau gegen eine echte Beispieldatei. Nicht Teil des "
            "Namensschemas (Dateinamen, siehe Datei-Menü) -- betrifft nur den INHALT der Dateien."
        )
        act_import_settings.triggered.connect(self._configure_import_settings)
        tools_menu.addSeparator()
        act_ruler = tools_menu.addAction("Maßstab festlegen…")
        act_ruler.setToolTip(
            "Referenzlinie im Bild einzeichnen und ihre reale Länge in mm angeben, um Messbereich-"
            "Größen zusätzlich in mm anzuzeigen."
        )
        act_ruler.triggered.connect(self._start_ruler_tool)
        self._requires_recording_actions.append(act_ruler)
        # "Länge messen…" hat keinen eigenen Menüpunkt mehr -- ersetzt durch
        # den Knopf "Neue Messung" im rechten Panel (Punkt 8, "Maßstab &
        # Messungen"), analog zum "+ Messbereich"-Knopf ohne Menü-Aequivalent.

        kernel_menu = tools_menu.addMenu("Live-Cursor-Bereichsgröße")
        kernel_menu.setToolTip(
            "Legt fest, wie viele Pixel um den Live-Cursor (Maus im Thermobild) herum "
            "für den Live-Verlauf/die Live-Anzeige gemittelt werden."
        )
        self._live_cursor_kernel_actions: dict[int, QtGui.QAction] = {}
        kernel_group = QtGui.QActionGroup(self)
        kernel_group.setExclusive(True)
        # Ausschliesslich ungerade Kantenlaengen (echtes Mittelpunkt-Pixel,
        # keine geraden Groessen wie das frueher enthaltene 10x10 mehr).
        for size in (1, 3, 5, 7, 9, 11, 13, 15):
            if size == 1:
                label = "1×1 Pixel"
            elif size == 5:
                label = "5×5 Pixel (Mittelwert, Standard)"
            else:
                label = f"{size}×{size} Pixel (Mittelwert)"
            act = kernel_menu.addAction(label)
            act.setCheckable(True)
            act.triggered.connect(partial(self._on_live_cursor_kernel_selected, size))
            kernel_group.addAction(act)
            self._live_cursor_kernel_actions[size] = act
        self._live_cursor_kernel_actions[5].setChecked(True)

    def _build_export_menu(self) -> None:
        export_menu = self.menuBar().addMenu("&Export")
        act_export_video = export_menu.addAction("Video / Bildstapel exportieren…")
        act_export_video.setToolTip(
            "Exportiert einen wählbaren Frame-Bereich als MP4-, AVI- oder WebM-Video, oder "
            "wahlweise als Bildstapel (eine Bilddatei pro Frame)."
        )
        act_export_video.triggered.connect(self._export_video)
        export_menu.addSeparator()
        # Nur noch EIN Grafik- und EIN CSV-Export-Fenster (statt getrennter
        # "Zeitverlauf-"/"Live-"-Varianten) -- welche Kurve(n) tatsaechlich mit
        # hinein sollen, waehlt der jeweilige Dialog selbst per Haekchen
        # (Nutzerwunsch: "nur noch ein einziges CSV/-Bild-Export Fenster").
        act_export_graphic = export_menu.addAction("Grafik exportieren…")
        act_export_graphic.setToolTip(
            "Speichert Thermobild (mit Position der Messbereiche/des Cursors) und "
            "Temperaturverlauf gemeinsam oder getrennt als Grafik(en) -- welche Kurve(n) "
            "(Messbereiche und/oder Live-Cursor) dabei sind, wählt der Dialog selbst."
        )
        act_export_graphic.triggered.connect(self._export_graphic)
        export_menu.addSeparator()
        act_export_csv = export_menu.addAction("Werte exportieren…")
        act_export_csv.setToolTip(
            "Speichert die Temperaturwerte aller platzierten Messbereiche und/oder des "
            "Live-Cursor-Pixels wählbar über die Zeit als CSV-, JSON- oder Text-Datei."
        )
        act_export_csv.triggered.connect(self._export_csv)
        self._requires_recording_actions.extend([
            act_export_video, act_export_graphic, act_export_csv,
        ])
