"""Registerkarten ("Ebenen"/Masken) über dem Thermobild (Nutzerwunsch: "das
Thermobild wird recht voll") -- fünf Tabs "Alle" / "Bereinigung" /
"Temperatur-Messung" / "Schwindungsmessung" / "Maßstab" blenden jeweils nur
die zur aktiven Ebene gehörigen Bild-Overlays und Panel-Abschnitte ein;
"Alle" zeigt (wie das bisherige, ungefilterte Verhalten) alles gleichzeitig.

Startet eine Aktion, die eindeutig zu einer Ebene gehört (z.B. "+ Messbereich"
klicken), schaltet der jeweilige Handler selbst auf den passenden Tab um
(siehe die `_set_active_layer_tab(...)`-Aufrufe in roi_ops.py/shrinkage_ops.py/
measurement_ops.py/data_cleaning_ops.py) -- bewusst auf Button-/Checkbox-
Handler-Ebene und nicht in den tieferliegenden Hilfsfunktionen, damit
programmatische Aufrufe (v.a. aus Tests) nicht ungewollt Tabs umschalten.

"Bereinigung" hat KEINEN eigenen Panel-Abschnitt -- die Referenzpunkte-
Verwaltung bleibt im eigenständigen DataCleaningDialog (Nutzer-Rückfrage:
kleinerer Eingriff als eine vollständige Einbettung). Der Tab steuert
stattdessen, ob dieser (bereits vorhandene, ggf. None) Dialog sichtbar ist --
_apply_cleaning_dialog_visibility() ist rein reaktiv und öffnet NIE selbst
einen noch nie geöffneten Dialog, das bleibt Aufgabe von
_open_data_cleaning_dialog() (data_cleaning_ops.py)."""
from __future__ import annotations

from qtpy import QtWidgets

_LAYER_TAB_ORDER = ["all", "cleaning", "roi", "shrinkage", "scale"]
_LAYER_TAB_LABELS = {
    "all": "Alle",
    "cleaning": "Bereinigung",
    "roi": "Temperatur-Messung",
    "shrinkage": "Schwindungsmessung",
    "scale": "Maßstab",
}


class _LayerTabsMixin:
    def _build_layer_tab_bar(self) -> QtWidgets.QTabBar:
        self.layer_tab_bar = QtWidgets.QTabBar()
        self.layer_tab_bar.setExpanding(False)
        self.layer_tab_bar.setToolTip(
            "Ebene wählen: blendet nur die dazu gehörigen Bereiche im Thermobild und im "
            "rechten Panel ein -- \"Alle\" zeigt (wie bisher) alles gleichzeitig."
        )
        for tab in _LAYER_TAB_ORDER:
            self.layer_tab_bar.addTab(_LAYER_TAB_LABELS[tab])
        self.layer_tab_bar.currentChanged.connect(self._on_layer_tab_bar_changed)
        return self.layer_tab_bar

    def _on_layer_tab_bar_changed(self, index: int) -> None:
        tab = _LAYER_TAB_ORDER[index]
        if tab == "cleaning":
            # Oeffnet/hebt den Dialog hervor UND setzt den Tab (siehe dort) --
            # derselbe Weg wie die Menüaktion "Daten > Rohdaten säubern…",
            # damit beide Einstiege denselben synchronisierten Zustand ergeben.
            self._open_data_cleaning_dialog()
        else:
            self._set_active_layer_tab(tab)

    def _set_active_layer_tab(self, tab: str) -> None:
        self._active_layer_tab = tab
        self.layer_tab_bar.blockSignals(True)
        self.layer_tab_bar.setCurrentIndex(_LAYER_TAB_ORDER.index(tab))
        self.layer_tab_bar.blockSignals(False)
        self._apply_layer_tab_visibility()
        self._apply_cleaning_dialog_visibility()

    def _is_layer_tab_active(self, category: str) -> bool:
        return self._active_layer_tab in ("all", category)

    def _apply_layer_tab_visibility(self) -> None:
        """Zentrale, jederzeit gefahrlos wiederholbar aufrufbare Methode --
        rein setVisible()-basiert, ohne Nebenwirkungen (Dialog öffnen o.ä.
        passiert ausschließlich in _apply_cleaning_dialog_visibility bzw. den
        expliziten "Aktion X startet Ebene Y"-Aufrufen)."""
        self.scale_box.setVisible(self._is_layer_tab_active("scale"))
        self._shrinkage_groupbox.setVisible(self._is_layer_tab_active("shrinkage"))
        self.roi_split.setVisible(self._is_layer_tab_active("roi"))

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

        cleaning_visible = self._is_layer_tab_active("cleaning")
        for item in self._cleaning_point_items:
            for key in ("dot", "label", "area"):
                obj = item.get(key)
                if obj is not None:
                    obj.setVisible(cleaning_visible)

        # Schwindung/Maßstab haben jeweils eine EIGENE Sichtbarkeits-
        # Bedingung (Aktivieren-Checkbox bzw. "Anzeigen"-Checkbox) -- deren
        # Anwendungs-Methoden UND-verknüpfen das bereits mit
        # _is_layer_tab_active, hier nur neu auswerten.
        self._apply_shrinkage_roi_visibility()
        self._apply_scale_visuals_visibility()

    def _apply_cleaning_dialog_visibility(self) -> None:
        if self._cleaning_dialog is None:
            return
        self._cleaning_dialog.setVisible(self._is_layer_tab_active("cleaning"))
