"""Aufbau des rechten Messbereich-Panels (Liste, Tabs, einzelne ROI-Zeilen)."""
from __future__ import annotations

from functools import partial

import pyqtgraph as pg
from qtpy import QtCore, QtWidgets

from ..measurement import MeasurementEntry
from ..roi_entry import (
    DEFAULT_ROI_SIZE,
    ROI_COLORS,
    RoiEntry,
    default_roi_name,
    roi_color_for_number,
)
from ..widgets import LocaleTolerantDoubleSpinBox
from .constants import (
    COLORMAPS,
    INTERP_END_CAPTURE_LABEL,
    INTERP_END_LABEL,
    INTERP_START_CAPTURE_LABEL,
    INTERP_START_LABEL,
    MAX_FRAMES_WITH_SYMBOLS,
)


class _RoiPanelBuildMixin:
    def _build_roi_entries(self) -> None:
        # Startbestand: die urspruengliche feste Anzahl (Farbpalettengroesse).
        # Weitere Messbereiche lassen sich danach jederzeit per "+"-Knopf im
        # ROI-Tab-Leiste hinzufuegen (siehe _add_roi_entry/_on_add_roi_clicked).
        for _ in range(len(ROI_COLORS)):
            self._add_roi_entry(build_row=False)

    def _add_roi_entry(self, build_row: bool = True) -> RoiEntry:
        """Legt einen neuen, leeren Messbereich an (Kurve, ROI-Rechteck,
        Bild-Beschriftung) und haengt ihn an self.roi_entries an. Mit
        build_row=True (Standard: beim Hinzufuegen zur Laufzeit) wird
        zusaetzlich sein Panel-Eintrag gebaut und ausgewaehlt -- beim
        initialen Aufbau der ersten 5 ROIs (build_row=False) existiert
        self.roi_list/roi_stack zu diesem Zeitpunkt noch nicht, das erledigt
        dort _build_control_panel."""
        number = self._roi_next_number
        self._roi_next_number += 1
        color = roi_color_for_number(number)
        curve = self.timeseries_plot.plot(
            pen=pg.mkPen(color, width=2),
            symbol="o",
            symbolSize=5,
            symbolBrush=color,
            symbolPen=None,
            name=default_roi_name(number),
        )
        entry = RoiEntry(number, color, self.view_box, curve)
        entry.roi.sigRegionChanged.connect(partial(self._on_roi_region_changed, entry))
        entry.roi.sigRegionChangeFinished.connect(partial(self._on_roi_region_finished, entry))
        # Ein (reiner, nicht ziehender) Klick auf den Messbereich im Bild
        # waehlt ihn auch im rechten Panel aus -- sigClicked feuert nur bei
        # einem echten Klick, nicht waehrend eines Ziehvorgangs zum
        # Verschieben/Skalieren.
        entry.roi.sigClicked.connect(partial(self._on_roi_clicked_in_image, entry))
        self.roi_entries.append(entry)
        if build_row:
            # Erst die Zeile (inkl. Spinboxen) bauen, DANACH ggf. deren
            # Wertebereiche an die laufende Aufnahme anpassen -- umgekehrt
            # gaebe es die Spinboxen noch gar nicht.
            self._add_roi_tab_page(entry)
            self._select_roi(entry)
        if self.recording is not None and entry.spin_x is not None:
            self._configure_roi_entry_for_recording(entry)
        return entry

    def _configure_roi_entry_for_recording(self, entry: RoiEntry) -> None:
        """Uebertraegt die aus der aktuellen Aufnahme abgeleiteten Grenzen/
        Darstellungsoptionen (Spinbox-Wertebereiche, Punktmarker-Sichtbarkeit)
        auf ein einzelnes ROI -- gemeinsam genutzt von _set_recording() (alle
        vorhandenen ROIs) und _add_roi_entry() (ein waehrend einer bereits
        laufenden Aufnahme neu hinzugefuegtes ROI)."""
        rows, cols = self.recording.shape
        self._set_roi_geometry_ranges(entry, cols, rows)
        entry.curve.setSymbol("o" if self.recording.n_frames <= MAX_FRAMES_WITH_SYMBOLS else None)
        # Start/Ende-Zielbild der Interpolation: ein waehrend einer laufenden
        # Aufnahme neu hinzugefuegtes ROI hatte diese sonst dauerhaft auf
        # (1, 1) geklemmt (Konstruktions-Default), weil nur _set_recording()/
        # _apply_appended_recording() die Wertebereiche sonst anpassen --
        # Standard hier wie bei einer frisch geladenen Aufnahme: erstes/
        # letztes Bild.
        n = self.recording.n_frames
        entry.spin_interp_start_frame.setRange(1, max(1, n))
        entry.spin_interp_start_frame.setValue(1)
        entry.spin_interp_end_frame.setRange(1, max(1, n))
        entry.spin_interp_end_frame.setValue(max(1, n))

    @staticmethod
    def _set_roi_geometry_ranges(entry: RoiEntry, cols: int, rows: int) -> None:
        """Setzt die Wertebereiche von X-/Y-Position und Breite/Höhe passend
        zur Bildgröße -- mit blockSignals: ein Bereichs-SCHRUMPFEN kann den
        aktuellen Wert stillschweigend klemmen (z.B. Standardhöhe 30 bei
        einem nur 24 Pixel hohen Bild), was OHNE Blockade denselben
        valueChanged-Handler wie eine echte Nutzereingabe ausgelöst hätte
        (siehe spin.valueChanged -> _on_roi_apply_clicked) und ein noch gar
        nicht platziertes ROI ungewollt "platziert" hätte."""
        for spin, lo, hi in (
            (entry.spin_x, 0, cols),
            (entry.spin_y, 0, rows),
            (entry.spin_width, 1, max(1, cols)),
            (entry.spin_height, 1, max(1, rows)),
        ):
            spin.blockSignals(True)
            spin.setRange(lo, hi)
            spin.blockSignals(False)

    def _build_control_panel(self) -> None:
        panel = QtWidgets.QWidget()
        layout = QtWidgets.QVBoxLayout(panel)
        layout.setAlignment(QtCore.Qt.AlignTop)

        # -- Legende / Colormap ---------------------------------------
        legend_box = QtWidgets.QGroupBox("Legende")
        legend_layout = QtWidgets.QGridLayout(legend_box)

        legend_layout.addWidget(QtWidgets.QLabel("Farbverlauf:"), 0, 0)
        self.combo_cmap = QtWidgets.QComboBox()
        for label, _name in COLORMAPS:
            self.combo_cmap.addItem(label)
        self.combo_cmap.currentIndexChanged.connect(self._on_colormap_changed)
        legend_layout.addWidget(self.combo_cmap, 0, 1, 1, 2)

        self.chk_cmap_invert = QtWidgets.QCheckBox("Invertieren")
        self.chk_cmap_invert.setToolTip("Kehrt den Farbverlauf der Legende um (kalt/warm vertauscht).")
        self.chk_cmap_invert.toggled.connect(self._on_colormap_invert_toggled)
        # Eingerueckt auf Spalte 1 (Hoehe der Farbverlauf-Combobox), damit
        # optisch klar ist, dass diese Option zum Farbverlauf darueber gehoert.
        legend_layout.addWidget(self.chk_cmap_invert, 1, 1, 1, 2)

        # Skalierungs-Modus zweistufig: eine AEUSSERE, garantiert exklusive
        # Wahl "Automatisch" vs. "Manuell" (level_mode_group) -- nie beide
        # gleichzeitig an oder aus -- und darunter/daneben eine INNERE Wahl
        # fuer die Automatik-Variante (level_auto_submode_group), die nur
        # relevant/bedienbar ist, solange "Automatisch" aktiv ist.
        self.radio_level_auto = QtWidgets.QRadioButton("Automatisch:")
        self.radio_level_auto.setToolTip(
            "Skalierung automatisch aus den Bilddaten ermitteln (siehe Auswahl rechts daneben)."
        )
        self.radio_level_manual = QtWidgets.QRadioButton("Manuell:")
        self.radio_level_manual.setToolTip(
            "Feste, selbst gewählte Grenzwerte (Felder \"Min\"/\"Max\" rechts) statt automatischer Skalierung."
        )
        self.level_mode_group = QtWidgets.QButtonGroup(self)
        self.level_mode_group.addButton(self.radio_level_auto)
        self.level_mode_group.addButton(self.radio_level_manual)
        self.radio_level_auto.setChecked(True)
        self.level_mode_group.buttonToggled.connect(self._on_level_mode_changed)
        # Eigene, zusaetzliche Verbindung NUR fuer echte Nutzerklicks auf
        # "Manuell"/"Automatisch" (Punkt 3) -- _set_level_mode() aendert
        # radio_level_manual/-auto stets ueber _set_widget_value() mit
        # blockSignals, ein programmatischer Moduswechsel (Projekt laden,
        # Export-Override) loest toggled() also NIE aus, siehe
        # _on_manual_level_radio_toggled fuer den Grund, warum das hier
        # wichtig ist.
        self.radio_level_manual.toggled.connect(self._on_manual_level_radio_toggled)

        self.radio_level_per_frame = QtWidgets.QRadioButton("Pro Bild")
        self.radio_level_per_frame.setToolTip(
            "Minimum/Maximum werden für jedes angezeigte Bild neu berechnet (Standard)."
        )
        self.radio_level_global = QtWidgets.QRadioButton("Über gesamte Messung")
        self.radio_level_global.setToolTip(
            "Ermittelt Minimum/Maximum einmalig über alle geladenen Frames und verwendet "
            "diesen Bereich durchgehend für die Legende (statt pro Bild neu zu skalieren)."
        )
        self.level_auto_submode_group = QtWidgets.QButtonGroup(self)
        self.level_auto_submode_group.addButton(self.radio_level_per_frame)
        self.level_auto_submode_group.addButton(self.radio_level_global)
        # Standard: "Über gesamte Messung" -- eine durchgehend gleichbleibende
        # Skalierung ist beim Betrachten/Vergleichen einzelner Frames einer
        # Messreihe meist hilfreicher als eine pro Bild neu springende.
        self.radio_level_global.setChecked(True)
        self.level_auto_submode_group.buttonToggled.connect(self._on_level_mode_changed)

        # Block-weise UNTEREINANDER statt nebeneinander: Automatisch-Block
        # (Pro Bild ueber Gesamte Serie) und Manuell-Block (Max ueber Min,
        # bewusst in dieser Reihenfolge) jeweils zweizeilig in derselben
        # (zweiten) Spalte -- dadurch fluchten Pro Bild und Max in dieser
        # Spalte, auch wenn sie inhaltlich zu verschiedenen Gruppen gehoeren.
        legend_layout.addWidget(self.radio_level_auto, 2, 0)
        legend_layout.addWidget(self.radio_level_per_frame, 2, 1)
        legend_layout.addWidget(self.radio_level_global, 3, 1)

        legend_layout.addWidget(self.radio_level_manual, 4, 0)
        legend_layout.addWidget(QtWidgets.QLabel("Max:"), 4, 1)
        self.spin_level_max = LocaleTolerantDoubleSpinBox()
        self.spin_level_max.setRange(-100.0, 2000.0)
        self.spin_level_max.setDecimals(1)
        self.spin_level_max.setValue(50.0)
        self.spin_level_max.setSuffix(" °C")
        self.spin_level_max.setEnabled(False)
        self.spin_level_max.valueChanged.connect(self._on_level_spin_changed)
        legend_layout.addWidget(self.spin_level_max, 4, 2)

        legend_layout.addWidget(QtWidgets.QLabel("Min:"), 5, 1)
        self.spin_level_min = LocaleTolerantDoubleSpinBox()
        self.spin_level_min.setRange(-100.0, 2000.0)
        self.spin_level_min.setDecimals(1)
        self.spin_level_min.setSuffix(" °C")
        self.spin_level_min.setEnabled(False)
        self.spin_level_min.valueChanged.connect(self._on_level_spin_changed)
        legend_layout.addWidget(self.spin_level_min, 5, 2)

        self.histogram.sigLevelsChanged.connect(self._on_histogram_levels_changed)

        # -- Maßstab (Lineal, Punkt 12) --------------------------------------
        # -- Maßstab & Messungen (Punkte 7/8/9) -------------------------------
        # Der Maßstab (Kalibrierung, px-zu-mm) bleibt EIN einzelner Wert wie
        # bisher. Darunter neu: beliebig viele benannte, farbige Ad-hoc-
        # Streckenmessungen (Nutzerwunsch: "beliebig viele (Größen-)Messungen
        # gleichzeitig"), deren Werte HIER in der Liste (Punkt 7: "oben
        # rechts in der UI mit ausgeben") direkt sichtbar sind -- bewusst
        # eine schlanke Zeilen-Liste statt eines Auswahl-/Detail-Musters wie
        # bei den Messbereichen (roi_list/roi_stack), da alle Werte
        # GLEICHZEITIG sichtbar sein sollen, nicht nur der gerade
        # ausgewählte.
        scale_box = QtWidgets.QGroupBox("Maßstab && Messungen")
        # Als self.-Attribut gehalten, damit layer_tabs_ops.py die
        # Sichtbarkeit dieses gesamten Panel-Abschnitts je nach aktivem
        # Ebenen-Tab steuern kann (siehe _apply_layer_tab_visibility).
        self.scale_box = scale_box
        scale_layout = QtWidgets.QVBoxLayout(scale_box)
        self.scale_label = QtWidgets.QLabel("Kein Maßstab definiert.")
        self.scale_label.setWordWrap(True)
        scale_layout.addWidget(self.scale_label)
        scale_buttons_row = QtWidgets.QHBoxLayout()
        btn_scale_set = QtWidgets.QPushButton("Festlegen…")
        btn_scale_set.setToolTip("Referenzlinie im Bild einzeichnen und ihre reale Länge in mm angeben.")
        btn_scale_set.clicked.connect(self._start_ruler_tool)
        scale_buttons_row.addWidget(btn_scale_set)
        self.btn_scale_clear = QtWidgets.QPushButton("Entfernen")
        self.btn_scale_clear.setToolTip("Entfernt den definierten Maßstab wieder (Größen werden nur noch in Pixeln angezeigt).")
        self.btn_scale_clear.setEnabled(False)
        self.btn_scale_clear.clicked.connect(self._clear_ruler_scale)
        scale_buttons_row.addWidget(self.btn_scale_clear)
        self.btn_ruler_color = QtWidgets.QPushButton()
        self.btn_ruler_color.setFixedSize(20, 20)
        self.btn_ruler_color.setCursor(QtCore.Qt.PointingHandCursor)
        self.btn_ruler_color.setToolTip(
            "Farbe der Referenzlinie ändern -- bei manchen Farbverläufen (z.B. \"Hot\") ist die "
            "Standardfarbe sonst kaum zu erkennen."
        )
        self.btn_ruler_color.clicked.connect(self._on_ruler_color_clicked)
        scale_buttons_row.addWidget(self.btn_ruler_color)
        scale_buttons_row.addStretch(1)
        # Punkt 5 (Nutzerwunsch): EINE Checkbox blendet Maßstab-Linie UND alle
        # Messungen gemeinsam im Thermobild aus/ein (z.B. um das Bild kurz
        # unverdeckt zu sehen), ohne sie zu löschen -- Werte/Zeilen im Panel
        # bleiben davon unberührt.
        self.chk_scale_visible = QtWidgets.QCheckBox("Anzeigen")
        self.chk_scale_visible.setChecked(True)
        self.chk_scale_visible.setToolTip(
            "Blendet die Maßstab-Linie und alle Messungen im Thermobild gemeinsam aus/ein."
        )
        self.chk_scale_visible.toggled.connect(self._on_toggle_scale_visuals)
        scale_buttons_row.addWidget(self.chk_scale_visible)
        scale_layout.addLayout(scale_buttons_row)
        self._update_ruler_color_swatch()

        measurements_header = QtWidgets.QHBoxLayout()
        measurements_header.addWidget(QtWidgets.QLabel("Messungen:"))
        self.btn_add_measurement = QtWidgets.QPushButton("Neue Messung")
        self.btn_add_measurement.setToolTip(
            "Strecke im Bild anklicken (Start-, dann Endpunkt) und mit dem oben definierten "
            "Maßstab in mm anzeigen -- ändert den Maßstab selbst NICHT, beliebig viele Messungen "
            "gleichzeitig möglich. Erst verfügbar, wenn ein Maßstab festgelegt ist."
        )
        self.btn_add_measurement.setEnabled(False)
        self.btn_add_measurement.clicked.connect(self._start_measurement_tool)
        measurements_header.addWidget(self.btn_add_measurement)
        measurements_header.addStretch(1)
        scale_layout.addLayout(measurements_header)

        self.measurements_container = QtWidgets.QVBoxLayout()
        self.measurements_container.setContentsMargins(0, 0, 0, 0)
        scale_layout.addLayout(self.measurements_container)
        # Bugfix: ohne dieses Stretch-Element am Ende wanderte JEGLICHE
        # "Slack"-Hoehe, die scale_box beim Nebeneinanderliegen mit legend_box
        # (top_row, siehe unten) zugeteilt bekommt, automatisch in
        # scale_label hinein (das einzige Widget hier ohne feste Groesse) --
        # scale_label blaehte sich dadurch unsichtbar auf. Jede neu
        # hinzugefuegte Messungs-Zeile verkleinerte diesen "Puffer" wieder,
        # wodurch buendig darunter/darueber liegende Widgets (Knopfzeile,
        # "Messungen:"-Kopf) sichtbar nach oben ruckten (Bugreport: "verschiebt
        # sich der Inhalt der gesamten Box nach oben"). Der Stretch faengt die
        # Slack-Hoehe jetzt gebuendelt am Ende ab, sodass alles darueber fest
        # an seinem Platz bleibt.
        scale_layout.addStretch(1)
        self.measurements: list[MeasurementEntry] = []
        self._measurement_next_number = 1

        # Die Hell/Dunkel-Wahl fuer Thermobild/Graph (Punkt 5, "Ansichts-
        # Manager") sass frueher als eigene "Ansicht"-Box hier im rechten
        # Panel -- Nutzerfeedback: "macht da, wo es jetzt ist, keinen Sinn".
        # Jetzt Teil des "Ansicht"-Menues (siehe _build_menu), zusammen mit
        # dem allgemeinen Dunkelmodus-Schalter, an den sie inhaltlich gehoert.
        #
        # Legende und Maßstab NEBENEINANDER statt untereinander -- spart
        # vertikalen Platz im rechten Panel, ohne die Legende aus diesem
        # (bewusst an dieser Stelle belassenen) Bereich zu verschieben.
        top_row = QtWidgets.QHBoxLayout()
        top_row.addWidget(legend_box, 3)
        top_row.addWidget(scale_box, 2)
        layout.addLayout(top_row)

        self._build_shrinkage_panel(layout)

        # -- ROI-Auswahl als senkrechte Namensliste + Inhaltsflaeche (statt
        # fuenf untereinander gestapelter Boxen ODER eines QTabWidget mit
        # senkrechter Reiterleiste) -- letzteres dreht die Beschriftung dort
        # um 90° (kaum lesbar) und unterstuetzt in dieser Anordnung keinen
        # Eck-Knopf mehr (der "+ Messbereich"-Knopf verschwand dadurch
        # komplett). Eine normale QListWidget-Liste zeigt die Namen
        # waagerecht/normal lesbar, laesst sich per Doppelklick direkt
        # umbenennen (spart den Umweg ueber das Namensfeld in der Zeile) und
        # der "+"-Knopf ist ein ganz normaler Knopf ohne Sondermechanik.
        self.roi_list = QtWidgets.QListWidget()
        self.roi_list.setToolTip(
            "Messbereich auswählen (aktiviert direkt \"Messbereich setzen\") -- Doppelklick zum "
            "Umbenennen."
        )
        self.roi_stack = QtWidgets.QStackedWidget()
        for entry in self.roi_entries:
            self._add_roi_tab_page(entry)
        self.roi_list.currentRowChanged.connect(self._on_roi_list_row_changed)
        self.roi_list.itemChanged.connect(self._on_roi_list_item_changed)

        self.btn_add_roi = QtWidgets.QPushButton("+ Messbereich")
        btn_add_roi = self.btn_add_roi
        btn_add_roi.setToolTip("Weiteren Messbereich hinzufügen (beliebig viele möglich).")
        btn_add_roi.clicked.connect(self._on_add_roi_clicked)

        list_column = QtWidgets.QVBoxLayout()
        list_column.addWidget(btn_add_roi)
        list_column.addWidget(self.roi_list, 1)
        list_container = QtWidgets.QWidget()
        list_container.setLayout(list_column)

        roi_split = QtWidgets.QSplitter(QtCore.Qt.Horizontal)
        roi_split.addWidget(list_container)
        roi_split.addWidget(self.roi_stack)
        roi_split.setStretchFactor(0, 0)
        roi_split.setStretchFactor(1, 1)
        roi_split.setSizes([130, 400])
        # Als self.-Attribut gehalten, damit layer_tabs_ops.py die
        # Sichtbarkeit dieses gesamten Panel-Abschnitts je nach aktivem
        # Ebenen-Tab steuern kann (siehe _apply_layer_tab_visibility).
        self.roi_split = roi_split

        layout.addWidget(roi_split, 1)
        if self.roi_list.count():
            self.roi_list.setCurrentRow(0)

        # Das GESAMTE Panel (Legende, Maßstab UND ROI-Auswahl) in einen
        # gemeinsamen Scrollbereich, damit bei knapper Dock-Hoehe alles
        # erreichbar bleibt (statt nur den Inhalt scrollen zu koennen).
        scroll = QtWidgets.QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(panel)
        self.control_panel = scroll

    def _add_roi_tab_page(self, entry: "RoiEntry") -> None:
        """Baut die Zeile eines Messbereichs, haengt sie an roi_stack an und
        legt den zugehoerigen Namens-Eintrag (inkl. Sichtbarkeits-Haekchen)
        in roi_list an."""
        row_widget = self._build_roi_row(entry)
        entry.tab_widget = row_widget
        self.roi_stack.addWidget(row_widget)

        item = QtWidgets.QListWidgetItem(entry.name)
        item.setFlags(item.flags() | QtCore.Qt.ItemIsEditable | QtCore.Qt.ItemIsUserCheckable)
        item.setCheckState(QtCore.Qt.CheckState.Checked)
        item.setToolTip("Doppelklick: umbenennen. Haekchen: sichtbar/ausgeblendet.")
        item.setData(QtCore.Qt.UserRole, entry)
        self.roi_list.addItem(item)
        entry.list_item = item

    def _select_roi(self, entry: "RoiEntry") -> None:
        """Waehlt den Listeneintrag eines Messbereichs aus -- loest darueber
        zuverlaessig denselben Wechsel-Ablauf aus wie ein Klick des Nutzers
        (Stack folgt, "Messbereich setzen" wird aktiviert), statt roi_stack
        direkt zu manipulieren."""
        if entry.list_item is not None:
            self.roi_list.setCurrentItem(entry.list_item)

    def _on_roi_clicked_in_image(self, entry: "RoiEntry", *_args) -> None:
        self._select_roi(entry)

    def _on_roi_list_row_changed(self, row: int) -> None:
        if row < 0:
            return
        item = self.roi_list.item(row)
        entry = item.data(QtCore.Qt.UserRole) if item is not None else None
        if entry is None:
            return
        self.roi_stack.setCurrentWidget(entry.tab_widget)
        # Auswahl aktiviert direkt "Messbereich setzen" fuer den neu
        # gewaehlten Messbereich, damit ein Klick ins Bild sofort platziert
        # werden kann, ohne extra den Knopf suchen zu muessen.
        if entry.btn_place is not None:
            entry.btn_place.setChecked(True)

    def _on_roi_list_item_changed(self, item: QtWidgets.QListWidgetItem) -> None:
        """Reagiert auf eine Aenderung eines Listeneintrags -- entweder der
        Name (Doppelklick-Bearbeitung) oder das Sichtbarkeits-Haekchen davor
        (ersetzt die vormalige separate "sichtbar"-Checkbox in der Zeile:
        direkt neben dem Namen ist auch ohne Beschriftung klar, was gemeint
        ist). Beide Aspekte werden hier zusammen (idempotent) angewendet,
        da itemChanged nicht mitteilt, welcher der beiden sich geaendert hat."""
        entry = item.data(QtCore.Qt.UserRole)
        if entry is None:
            return

        name = item.text().strip() or default_roi_name(entry.number)
        if item.text() != name:
            self.roi_list.blockSignals(True)
            item.setText(name)
            self.roi_list.blockSignals(False)
        if entry.name != name:
            entry.set_name(name)
            entry.tab_widget.setTitle(name)
            legend = self.timeseries_plot.getPlotItem().legend
            label = legend.getLabel(entry.curve) if legend is not None else None
            if label is not None:
                label.setText(name)

        visible = entry.is_visible_checked()
        entry.roi.setVisible(visible and entry.placed)
        entry.curve.setVisible(visible and entry.placed)
        entry.label.setVisible(visible and entry.placed)

    def _build_roi_row(self, entry: RoiEntry) -> QtWidgets.QGroupBox:
        # Box-Titel = ROI-Name (Umbenennen jetzt per Doppelklick in der
        # Liste links, siehe _on_roi_list_item_changed -- kein separates
        # Namensfeld mehr hier noetig).
        box = QtWidgets.QGroupBox(entry.name)
        outer = QtWidgets.QHBoxLayout(box)
        outer.setSpacing(6)
        grid = QtWidgets.QGridLayout()
        grid.setHorizontalSpacing(6)
        grid.setVerticalSpacing(4)
        outer.addLayout(grid, 1)
        # Laufender Zeilenzaehler statt hartkodierter Grid-Zeilennummern: eine
        # neue Zeile zwischendurch einzufuegen erfordert so nur einen
        # zusaetzlichen Block hier, statt an jeder folgenden addWidget/
        # addLayout-Stelle die Zeilennummer von Hand hochzuzaehlen (Quelle
        # sonst leicht uebersehener Ueberlappungen).
        row = 0

        # Farbe + "Messbereich setzen"/"Messbereich entfernen" alle in EINER
        # kompakten Zeile -- Farbe/setzen teilen sich eine Zelle per HBox.
        btn_color = QtWidgets.QPushButton()
        btn_color.setFixedSize(20, 20)
        btn_color.setCursor(QtCore.Qt.PointingHandCursor)
        btn_color.setToolTip("Farbe dieses Messbereichs ändern")
        btn_color.setStyleSheet(
            f"background-color:{entry.color}; border:1px solid #333; border-radius:4px;"
        )
        btn_color.clicked.connect(partial(self._on_roi_color_clicked, entry))
        entry.btn_color = btn_color

        btn_place = QtWidgets.QPushButton("Messbereich setzen")
        btn_place.setCheckable(True)
        btn_place.setToolTip("Aktivieren, dann im Bild\nklicken, um den Messbereich\ndort zu setzen.")
        btn_place.toggled.connect(partial(self._on_roi_place_toggled, entry))
        entry.btn_place = btn_place

        color_place_row = QtWidgets.QHBoxLayout()
        color_place_row.setSpacing(4)
        color_place_row.addWidget(btn_color)
        color_place_row.addWidget(btn_place, 1)
        grid.addLayout(color_place_row, row, 0, 1, 2)

        btn_remove = QtWidgets.QPushButton("Messbereich entfernen")
        btn_remove.setToolTip("Löscht diesen Messbereich\nunwiderruflich (Rechteck,\nBeschriftung, Kurve).")
        btn_remove.clicked.connect(partial(self._on_roi_remove_clicked, entry))
        grid.addWidget(btn_remove, row, 2, 1, 2)
        entry.btn_remove = btn_remove
        row += 1

        # Labels rechtsbuendig in ihrer Zelle, damit sie direkt an ihrem
        # Eingabefeld anliegen.
        grid.addWidget(
            QtWidgets.QLabel("X-Position:"), row, 0, alignment=QtCore.Qt.AlignRight | QtCore.Qt.AlignVCenter
        )
        spin_x = LocaleTolerantDoubleSpinBox()
        spin_x.setRange(0, 100000)
        spin_x.setDecimals(1)
        grid.addWidget(spin_x, row, 1)
        entry.spin_x = spin_x

        grid.addWidget(
            QtWidgets.QLabel("Y-Position:"), row, 2, alignment=QtCore.Qt.AlignRight | QtCore.Qt.AlignVCenter
        )
        spin_y = LocaleTolerantDoubleSpinBox()
        spin_y.setRange(0, 100000)
        spin_y.setDecimals(1)
        grid.addWidget(spin_y, row, 3)
        entry.spin_y = spin_y
        row += 1

        grid.addWidget(
            QtWidgets.QLabel("Breite:"), row, 0, alignment=QtCore.Qt.AlignRight | QtCore.Qt.AlignVCenter
        )
        spin_width = LocaleTolerantDoubleSpinBox()
        spin_width.setRange(1, 100000)
        spin_width.setValue(DEFAULT_ROI_SIZE)
        grid.addWidget(spin_width, row, 1)
        entry.spin_width = spin_width

        grid.addWidget(
            QtWidgets.QLabel("Höhe:"), row, 2, alignment=QtCore.Qt.AlignRight | QtCore.Qt.AlignVCenter
        )
        spin_height = LocaleTolerantDoubleSpinBox()
        spin_height.setRange(1, 100000)
        spin_height.setValue(DEFAULT_ROI_SIZE)
        grid.addWidget(spin_height, row, 3)
        entry.spin_height = spin_height

        # Jede Aenderung eines der vier Felder (Tippen, Pfeiltasten, Scrollrad)
        # wendet Position/Groesse sofort live an -- kein separater
        # "Übernehmen"-Knopf mehr noetig.
        for spin in (spin_x, spin_y, spin_width, spin_height):
            spin.valueChanged.connect(partial(self._on_roi_apply_clicked, entry))
        row += 1

        mm_label = QtWidgets.QLabel("")
        mm_label.setVisible(False)
        grid.addWidget(mm_label, row, 0, 1, 4)
        entry.mm_label = mm_label
        row += 1

        # -- Verlaufs-Interpolation (Punkt 3) ------------------------------
        chk_interp = QtWidgets.QCheckBox("Position/Größe über Zeit interpolieren (Start → Ende)")
        chk_interp.setToolTip("Messbereich wandert linear\nzwischen Start- und End-\nPosition/-Größe mit.")
        chk_interp.toggled.connect(partial(self._on_roi_interp_toggled, entry))
        grid.addWidget(chk_interp, row, 0, 1, 4)
        entry.chk_interp = chk_interp
        row += 1

        # Je eine eigene Zeile fuer Start/Ende (statt einer gemeinsamen
        # Reihe): "Erstes Frame:" - Eingabefeld - Knopf, darunter analog
        # "Letztes Frame:" -- Ziel-Frame frei waehlbar (Standard: erstes/
        # letztes Bild der Aufnahme, siehe _set_recording), der Knopf
        # springt zu genau diesem Frame und dient zugleich als Bestaetigung.
        grid.addWidget(
            QtWidgets.QLabel("Erstes Frame:"), row, 0, alignment=QtCore.Qt.AlignRight | QtCore.Qt.AlignVCenter
        )
        spin_interp_start_frame = QtWidgets.QSpinBox()
        spin_interp_start_frame.setRange(1, 1)
        spin_interp_start_frame.setToolTip("Bildnummer, die als Start-Zeitpunkt der Interpolation dient.")
        entry.spin_interp_start_frame = spin_interp_start_frame
        # Dezenter Hinweis (Punkt 2, Nutzerwunsch), falls dieses Frame
        # ausserhalb der aktuellen Auswertung liegt -- siehe
        # _refresh_interp_range_warning. Neben statt AUF der Spinbox, damit
        # er nicht mit deren Pfeil-Buttons ueberlappt.
        lbl_interp_start_warning = QtWidgets.QLabel("⚠")
        lbl_interp_start_warning.setStyleSheet("color:#b45309; font-weight:600;")
        lbl_interp_start_warning.setVisible(False)
        entry.lbl_interp_start_warning = lbl_interp_start_warning
        start_field_row = QtWidgets.QHBoxLayout()
        start_field_row.setContentsMargins(0, 0, 0, 0)
        start_field_row.addWidget(spin_interp_start_frame)
        start_field_row.addWidget(lbl_interp_start_warning)
        grid.addLayout(start_field_row, row, 1)
        spin_interp_start_frame.valueChanged.connect(partial(self._refresh_interp_range_warning, entry, True))

        btn_interp_start = QtWidgets.QPushButton(INTERP_START_LABEL)
        btn_interp_start.setToolTip(
            f"Springt zum links eingestellten Bild, positionieren,\ndann „{INTERP_START_CAPTURE_LABEL}“ klicken."
        )
        btn_interp_start.clicked.connect(partial(self._on_roi_interp_capture, entry, True))
        btn_interp_start.setEnabled(False)
        grid.addWidget(btn_interp_start, row, 2, 1, 2)
        entry.btn_interp_start = btn_interp_start
        row += 1

        grid.addWidget(
            QtWidgets.QLabel("Letztes Frame:"), row, 0, alignment=QtCore.Qt.AlignRight | QtCore.Qt.AlignVCenter
        )
        spin_interp_end_frame = QtWidgets.QSpinBox()
        spin_interp_end_frame.setRange(1, 1)
        spin_interp_end_frame.setToolTip("Bildnummer, die als Ende-Zeitpunkt der Interpolation dient.")
        entry.spin_interp_end_frame = spin_interp_end_frame
        lbl_interp_end_warning = QtWidgets.QLabel("⚠")
        lbl_interp_end_warning.setStyleSheet("color:#b45309; font-weight:600;")
        lbl_interp_end_warning.setVisible(False)
        entry.lbl_interp_end_warning = lbl_interp_end_warning
        end_field_row = QtWidgets.QHBoxLayout()
        end_field_row.setContentsMargins(0, 0, 0, 0)
        end_field_row.addWidget(spin_interp_end_frame)
        end_field_row.addWidget(lbl_interp_end_warning)
        grid.addLayout(end_field_row, row, 1)
        spin_interp_end_frame.valueChanged.connect(partial(self._refresh_interp_range_warning, entry, False))

        btn_interp_end = QtWidgets.QPushButton(INTERP_END_LABEL)
        btn_interp_end.setToolTip(
            f"Springt zum rechts eingestellten Bild, positionieren,\ndann „{INTERP_END_CAPTURE_LABEL}“ klicken."
        )
        btn_interp_end.clicked.connect(partial(self._on_roi_interp_capture, entry, False))
        btn_interp_end.setEnabled(False)
        grid.addWidget(btn_interp_end, row, 2, 1, 2)
        entry.btn_interp_end = btn_interp_end
        row += 1

        # Rechte Spalte, ueber die gesamte Zeilen-Hoehe: Anzeige-/
        # Auswertungsoptionen und "Quadrieren" kompakt untereinander.
        # "Übernehmen" entfaellt -- X-/Y-Position sowie Breite/Höhe wenden
        # sich jetzt bei JEDER Eingabefeld-Aenderung sofort selbst an (siehe
        # spin.valueChanged weiter oben), ein separater Knopf ist damit
        # ueberfluessig. "Zuruecksetzen" entfaellt ebenfalls (kaum genutzt,
        # kein klar erwartetes Verhalten).
        side_col = QtWidgets.QVBoxLayout()
        side_col.setSpacing(4)

        chk_show_temperature = QtWidgets.QCheckBox("Temperatur anzeigen")
        chk_show_temperature.setToolTip(
            "Zeigt die aktuelle Temperatur zusätzlich neben dem Namen im Bild an (Standard: an)."
        )
        chk_show_temperature.setChecked(True)
        chk_show_temperature.toggled.connect(partial(self._on_roi_show_temperature_toggled, entry))
        entry.chk_show_temperature = chk_show_temperature
        side_col.addWidget(chk_show_temperature)

        chk_circular = QtWidgets.QCheckBox("Kreis")
        chk_circular.setToolTip(
            "Zeichnet eine in Breite/Höhe eingeschriebene Ellipse statt eines Rechtecks und "
            "mittelt die Temperatur nur über die Pixel innerhalb dieser Fläche."
        )
        chk_circular.toggled.connect(partial(self._on_roi_circular_toggled, entry))
        entry.chk_circular = chk_circular
        side_col.addWidget(chk_circular)

        btn_square = QtWidgets.QPushButton("Quadrieren")
        btn_square.setToolTip("Höhe = Breite (Quadrat);\nMittelpunkt bleibt gleich.")
        btn_square.clicked.connect(partial(self._on_roi_square_reset_clicked, entry))
        side_col.addWidget(btn_square)

        side_col.addStretch(1)
        outer.addLayout(side_col)

        return box

