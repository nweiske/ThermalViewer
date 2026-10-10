"""Registerkarten ("Ebenen"/Masken) über dem Thermobild (Nutzerwunsch: "das
Thermobild wird recht voll") -- vier Tabs "Alle" / "Temperatur-Messung" /
"Schwindungsmessung" / "Maßstab" blenden jeweils nur die zur aktiven Ebene
gehörigen Bild-Overlays und Panel-Abschnitte ein; "Alle" zeigt (wie das
bisherige, ungefilterte Verhalten) alles gleichzeitig.

Startet eine Aktion, die eindeutig zu einer Ebene gehört (z.B. "+ Messbereich"
klicken), schaltet der jeweilige Handler selbst auf den passenden Tab um
(siehe die `_set_active_layer_tab(...)`-Aufrufe in roi_ops.py/shrinkage_ops.py/
measurement_ops.py) -- bewusst auf Button-/Checkbox-Handler-Ebene und nicht in
den tieferliegenden Hilfsfunktionen, damit programmatische Aufrufe (v.a. aus
Tests) nicht ungewollt Tabs umschalten.

"Bereinigung" hat KEINEN eigenen Tab mehr (weder oben am Bild noch im rechten
Panel) -- die Rohdaten-Bereinigung setzt ihre Referenzpunkte seit dem Umbau
ausschließlich in ihrer eigenen, modalen Bildvorschau (siehe
dialogs/data_cleaning_viewer.py) und hat damit keine Bild-Overlays auf dem
Hauptfenster mehr, die ein Tab hier steuern müsste. Erreichbar ausschließlich
über "Daten > Rohdaten säubern…" (siehe data_cleaning_ops.py)."""
from __future__ import annotations

from qtpy import QtWidgets

_LAYER_TAB_ORDER = ["all", "roi", "shrinkage", "scale", "querschnitt"]
_LAYER_TAB_LABELS = {
    "all": "Alle",
    "roi": "Temperatur-Messung",
    "shrinkage": "Schwindungsmessung",
    "scale": "Maßstab",
    "querschnitt": "Querschnitte",
}

# Eigenes Tab-Set des RECHTEN PANELS (siehe roi_panel_build.py:
# self.panel_tab_widget), NICHT identisch mit _LAYER_TAB_ORDER: kein "all"
# (bei echten, sich ausschliessenden Tabs ergibt "alles gleichzeitig zeigen"
# keinen Sinn mehr), dafuer zusaetzlich "legende" (hat keine Bild-Overlay-
# Entsprechung, daher auch keine Zeile in _LAYER_TAB_ORDER).
_PANEL_TAB_ORDER = ["legende", "roi", "shrinkage", "scale", "querschnitt"]

# Welcher GRAPH-DOCK-Tab (unten rechts, tabifiziert: timeseries_dock/
# shrinkage_dock/crosssection_dock, siehe ui_build.py) zu welchem oberen
# Ebenen-/Panel-Tab gehoert -- siehe _set_active_layer_tab fuer die
# Synchronisation (Nutzerwunsch: "Wenn ich oben rechts auf Querschnitt
# druecke, dann soll sich auch unten der Graphtab entsprechend anpassen...
# Gilt fuer die anderen Funktionalitaeten natuerlich genauso"). "scale" hat
# KEINEN eigenen Graphen -- faellt auf "Temperaturverlauf" zurueck, u.a.
# DAMIT ein zuvor aktiver Querschnitt-Dock-Tab beim Wechsel auf Maßstab
# wieder in den Hintergrund tritt (sonst blockiert dessen Klick-Vorrang in
# mouse_ops.py:_on_scene_mouse_clicked weiterhin das Maßstab-Platzieren).
_LAYER_TAB_GRAPH_DOCK = {
    "all": "timeseries_dock",
    "roi": "timeseries_dock",
    "shrinkage": "shrinkage_dock",
    "scale": "timeseries_dock",
    "querschnitt": "crosssection_dock",
}


class _LayerTabsMixin:
    def _build_layer_tab_bar(self) -> QtWidgets.QTabBar:
        self.layer_tab_bar = QtWidgets.QTabBar()
        self.layer_tab_bar.setExpanding(False)
        self.layer_tab_bar.setToolTip(
            "Ebene wählen: blendet nur deren Bereiche im Bild/Panel ein.\n"
            "\"Alle\" zeigt wie bisher alles gleichzeitig."
        )
        for tab in _LAYER_TAB_ORDER:
            self.layer_tab_bar.addTab(_LAYER_TAB_LABELS[tab])
        self.layer_tab_bar.currentChanged.connect(self._on_layer_tab_bar_changed)
        return self.layer_tab_bar

    def _on_layer_tab_bar_changed(self, index: int) -> None:
        tab = _LAYER_TAB_ORDER[index]
        self._set_active_layer_tab(tab)

    def _set_active_layer_tab(self, tab: str) -> None:
        # Ein gerade scharf gestelltes Platzier-/Mess-Werkzeug gehoert fest zu
        # EINER Ebene (ROI-Platzieren -> "roi", Maßstab/Messmodus -> "scale",
        # Probenhoehen-Platzieren -> "shrinkage") -- wechselt der Nutzer die
        # Ebene, OHNE das Werkzeug vorher selbst zu beenden, muss es hier
        # abgebrochen werden. Sonst bliebe es im Hintergrund scharf und ein
        # Klick ins Bild wuerde auf der NEUEN (falschen) Ebene unbemerkt noch
        # das alte Werkzeug ausloesen (Nutzer-Bugreport: ROI liess sich auf
        # dem Schwindungsmessung-Tab setzen). "all" deckt weiterhin jede
        # Ebene ab, dort bleibt jedes Werkzeug unangetastet scharf.
        if tab != "all":
            if tab != "roi" and self._armed_entry is not None:
                # _disarm_roi_placement() (roi_ops.py) raeumt auch eine evtl.
                # GERADE LAUFENDE zweistufige Start-/Ende-Erfassung der
                # Verlaufs-Interpolation mit auf (siehe dort) -- dieselbe
                # zentrale Stelle wird auch von _start_ruler_tool/
                # _start_measurement_tool/_start_sample_height_tool und
                # _on_roi_place_toggled verwendet.
                self._disarm_roi_placement()
            if tab != "scale" and self._ruler_armed:
                self._cancel_ruler_tool()
            if tab != "scale" and self._measurement_armed:
                self._cancel_measurement_tool()
            if tab != "shrinkage" and self._sample_height_armed:
                self._cancel_sample_height_tool()
            if tab != "querschnitt" and self._crosssection_entry_armed_direction is not None:
                self._cancel_crosssection_entry_tool()
        self._active_layer_tab = tab
        self.layer_tab_bar.blockSignals(True)
        self.layer_tab_bar.setCurrentIndex(_LAYER_TAB_ORDER.index(tab))
        self.layer_tab_bar.blockSignals(False)
        # Rechtes Panel nur mitschalten, wenn es fuer diese Kategorie
        # ueberhaupt eine eigene Seite hat ("all" hat keine -- bei echten
        # Tabs ergibt "alles gleichzeitig zeigen" keinen Sinn mehr, siehe
        # _PANEL_TAB_ORDER) -- sonst bleibt das Panel unveraendert auf der
        # zuletzt gewaehlten Seite stehen.
        if tab in _PANEL_TAB_ORDER:
            self.panel_tab_widget.blockSignals(True)
            self.panel_tab_widget.setCurrentIndex(_PANEL_TAB_ORDER.index(tab))
            self.panel_tab_widget.blockSignals(False)
        # Unteren Graph-Dock-Tab mitziehen (siehe _LAYER_TAB_GRAPH_DOCK) --
        # raise_() auf einen tabifizierten QDockWidget bringt ihn in den
        # Vordergrund und schiebt die Geschwister-Docks automatisch in den
        # Hintergrund, exakt wie ein manueller Klick auf dessen Tab-Reiter.
        getattr(self, _LAYER_TAB_GRAPH_DOCK[tab]).raise_()
        self._apply_layer_tab_visibility()

    def _on_panel_tab_widget_changed(self, index: int) -> None:
        """Gegenstueck zu _on_layer_tab_bar_changed fuer das rechte Panel --
        haelt die oberen Bild-Ebenen-Tabs synchron, damit z.B. ein Panel-Tab-
        Wechsel auf "Maßstab" auch die ROI-Boxen im Bild ausblendet und den
        Maßstab einblendet, statt die beiden Tab-Leisten widersprüchlich
        auseinanderlaufen zu lassen. "Legende" hat KEINE Bild-Overlay-
        Entsprechung -- bleibt rein lokale Panel-Navigation (self.
        panel_tab_widget hat bereits selbst umgeschaltet, bevor dieses
        Signal feuert)."""
        tab = _PANEL_TAB_ORDER[index]
        if tab != "legende":
            self._set_active_layer_tab(tab)

    def _is_layer_tab_active(self, category: str) -> bool:
        # Export-Override (siehe window.py:_export_layer_categories,
        # export_visuals.py:_temporary_export_layers): waehrend eines
        # Exports mit gewaehlter Ebenen-Auswahl entscheidet NUR noch diese
        # Menge, unabhaengig vom aktuell im Hauptfenster angezeigten Tab --
        # deckt bewusst NIE "scale" ab (siehe dort), fuer diese Kategorie
        # bleibt _active_layer_tab also weiterhin massgeblich.
        if self._export_layer_categories is not None and category != "scale":
            return category in self._export_layer_categories
        return self._active_layer_tab in ("all", category)

    def _apply_layer_tab_visibility(self) -> None:
        """Zentrale, jederzeit gefahrlos wiederholbar aufrufbare Methode --
        rein setVisible()-basiert, ohne Nebenwirkungen. Die Panel-Abschnitte
        (scale_box/roi_split/Schwindungs-Gruppe) haben KEIN setVisible()-
        Gating mehr -- ihre Sichtbarkeit ergibt sich daraus, ob sie gerade
        die aktive Seite von self.panel_tab_widget sind (siehe
        _set_active_layer_tab/_on_panel_tab_widget_changed)."""
        self._apply_roi_and_shrinkage_visibility()
        self._apply_scale_visuals_visibility()
        self._apply_crosssection_entries_visibility()

    def _apply_roi_and_shrinkage_visibility(self) -> None:
        """Der ROI-/Schwindungs-Teil von _apply_layer_tab_visibility() --
        eigene Methode, damit export_visuals.py:_temporary_export_layers
        NUR diesen Teil (nicht _apply_scale_visuals_visibility(), siehe
        dort) fuer den Export-Ebenen-Override neu anwenden kann."""
        # entry.roi/entry.label existieren fuer JEDEN Eintrag ab dessen
        # Erzeugung (auch vor dem eigentlichen Platzieren, siehe
        # roi_panel_build.py:_add_roi_entry), STARTEN dort aber bewusst
        # versteckt (RoiEntry.__init__: self.roi.setVisible(False)) und
        # werden erst durch entry.place() bzw. die eigene "sichtbar"-
        # Checkbox in der Liste (entry.is_visible_checked(), siehe
        # _on_roi_list_item_changed) eingeblendet. Der Ebenen-Tab darf diese
        # beiden bestehenden Bedingungen nur EINSCHRAENKEN, nicht
        # uebersteuern -- sonst wuerden unplatzierte oder bewusst
        # ausgeblendete ROIs beim Wechsel auf "Temperatur-Messung"/"Alle"
        # faelschlich sichtbar.
        roi_tab_active = self._is_layer_tab_active("roi")
        for entry in self.roi_entries:
            should_show = roi_tab_active and entry.placed and entry.is_visible_checked()
            entry.roi.setVisible(should_show)
            entry.label.setVisible(should_show)

        # Schwindung hat eine EIGENE Sichtbarkeits-Bedingung (Aktivieren-
        # Checkbox) -- deren Anwendungs-Methoden UND-verknüpfen das bereits
        # mit _is_layer_tab_active, hier nur neu auswerten.
        self._apply_shrinkage_roi_visibility()
        self._apply_sample_height_visibility()
        # Querschnitt-Graph (siehe crosssection_ops.py): dessen ROI-/Kontur-
        # Markierungen sollen genau denselben Ebenen-Tabs folgen wie die
        # Bild-Overlays selbst (Nutzerwunsch: bei "Temperatur-Messung" nur
        # ROI-Marker, bei "Schwindungsmessung" nur die Kontur-Marker, statt
        # immer alles gleichzeitig zu zeigen).
        self._update_crosssection_plot()
