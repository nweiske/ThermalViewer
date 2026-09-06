"""Hell-/Dunkelmodus, Thermobild-/Graph-Farbschema, Zeit-/Laufzeit-Anzeigemodus."""
from __future__ import annotations


from qtpy import QtGui, QtWidgets

from .constants import (
    THEMES,
)


class _ThemeMixin:
    def _apply_default_theme(self, key: str) -> None:
        """Menue-Knoepfe "Ansicht > Alles: Hell"/"Alles: Dunkel" (Folgeanfrage:
        "ich möchte zwei 'Default'-Settings haben (Light-/Dark), die die
        gesamte UI umstellen, UND Buttons, mit denen ich dieselben UI-
        Elemente umstellen kann, aber gezielt und unabhängig voneinander") --
        setzt Fenster-, Thermobild- UND Graph-Farbschema in einem Rutsch auf
        denselben Wert. Danach bleiben alle drei ueber ihre jeweils eigenen
        Untermenues (Fenster-/Thermobild-/Graph-Farbschema) weiterhin
        unabhaengig voneinander veraenderbar -- dieser Knopf setzt nur einen
        gemeinsamen Ausgangspunkt, er ist selbst kein dauerhaft "aktiver"
        Zustand (nach spaeteren Einzel-Aenderungen gibt es ohnehin keinen
        einzelnen gemeinsamen Wert mehr, den ein Haekchen sinnvoll anzeigen
        koennte)."""
        self._apply_window_theme(key)
        self._apply_image_theme(key)
        self._apply_graph_theme(key)

    def _apply_window_theme(self, key: str) -> None:
        """Fenster-Farbschema (Ansicht > Fenster-Farbschema, frueher der
        einzelne "Dunkelmodus"-Schalter) -- Hintergrund-/Schriftfarbe von
        Fenster, Menues und Panels, unabhaengig von Thermobild und Graph
        (siehe _apply_image_theme/_apply_graph_theme). Gleiches Muster wie
        die beiden anderen Farbschema-Setter (act-Lookup + QSettings)."""
        self._window_theme = key

        app = QtWidgets.QApplication.instance()
        if app is not None:
            app.setStyle("Fusion")
            # Bugfix: "app.style().standardPalette()" fuer Hell klingt neutral,
            # liefert unter Windows aber die vom Betriebssystem-Design
            # abgeleitete Palette -- ist dort der Windows-eigene Dunkelmodus
            # aktiv, blieb das Fenster trotz Umschalten auf "Hell" faktisch
            # dunkel (Bugreport: "Warum ist das gesamte Fenster immernoch im
            # Darkmode?"). Beide Modi verwenden jetzt eine explizit fest
            # definierte Palette, unabhaengig vom OS-Design.
            app.setPalette(self._dark_palette() if key == "dark" else self._light_palette())
            # Widgets mit eigenem setStyleSheet (z.B. Zeitstempel-Anzeige) haben
            # in der Praxis nicht immer zuverlaessig die neue QApplication-
            # Palette uebernommen -- explizites Neu-Polieren erzwingt die
            # Aktualisierung (Bugreport: Zeitstempel blieb nach Dunkel->Hell-
            # Wechsel in grauer, auf hellem Hintergrund kaum lesbarer Schrift).
            for widget in app.allWidgets():
                widget.style().unpolish(widget)
                widget.style().polish(widget)
                widget.update()

        act = getattr(self, "_window_theme_actions", {}).get(key)
        if act is not None:
            # Kein blockSignals -- siehe ausfuehrliche Begruendung in
            # _apply_image_theme.
            act.setChecked(True)
        self._settings.setValue("window_theme", key)

    def _apply_curve_colors(self, bg: str, fg: str) -> None:
        """Setzt Hintergrund-/Vordergrundfarbe der beiden Kurven-Graphen
        (Zeitverlauf, Live) -- unabhaengig vom App-Design (Dunkelmodus-
        Schalter) UND unabhaengig vom Thermobild (_apply_image_colors), per
        Ansichts-Manager (Punkt 5) frei waehlbar (siehe _apply_graph_theme,
        "Ansicht"-Menue). Reiner Farb-Setter -- Speichern der Wahl/Menue-
        Abgleich uebernimmt _apply_graph_theme."""
        self.timeseries_plot.setBackground(bg)
        self.live_plot.setBackground(bg)

        for plot_item in (self.timeseries_plot.getPlotItem(), self.live_plot.getPlotItem()):
            for axis_name in ("left", "bottom", "right", "top"):
                axis = plot_item.getAxis(axis_name)
                axis.setPen(fg)
                axis.setTextPen(fg)

        legend = self.timeseries_plot.getPlotItem().legend
        if legend is not None:
            legend.setLabelTextColor(fg)
            # Bugfix (pyqtgraph): LegendItem.setLabelTextColor() aktualisiert
            # nur legend.opts["labelTextColor"] -- fuer BEREITS vorhandene
            # Eintraege ruft es lediglich LabelItem.setAttr("color", ...) auf,
            # was nur das opts-dict des Labels aendert, aber (anders als
            # setText()) KEIN erneutes Rendern des schon erzeugten HTML
            # ausloest. Ein Messbereich, dessen Kurve VOR diesem Aufruf schon
            # in der Legende stand (z.B. die 5 Standard-Messbereiche beim
            # Programmstart), blieb dadurch dauerhaft bei der Farbe haengen,
            # die beim urspruenglichen Hinzufuegen galt (LabelItem faellt bei
            # color=None auf pyqtgraphs globalen Standard-Vordergrund zurueck
            # -- ein helles Grau, eigentlich fuer dunkle Hintergruende
            # gedacht) -- waehrend NEU hinzugefuegte Eintraege (z.B. "Live
            # (Cursor)", erst bei aktiviertem "Live-Cursor-Kurve zusaetzlich
            # anzeigen" hinzugefuegt) die zu diesem spaeteren Zeitpunkt schon
            # gesetzte echte Vordergrundfarbe direkt korrekt mitbekamen.
            # Bugreport: "Live-Cursor fett und schwarz, waehrend in der
            # Legende alle anderen Kurven ausgegraut sind" -- kein Fett-
            # Unterschied (Schriftgewicht war ueberall gleich), sondern
            # GENAU dieser Farb-Bug. Fix: jedes bestehende Label explizit
            # per setText() neu rendern lassen.
            for _sample, label in legend.items:
                label.setText(label.text, color=fg)

        self._graph_bg = bg
        self._graph_fg = fg

    def _apply_image_colors(self, bg: str, fg: str) -> None:
        """Setzt Hintergrund-/Vordergrundfarbe des Thermobild-Widgets --
        unabhaengig vom App-Design UND von den Kurven-Graphen, per
        Ansichts-Manager (Punkt 5) frei waehlbar (siehe _apply_image_theme,
        "Ansicht"-Menue). Reiner Farb-Setter, siehe _apply_curve_colors."""
        self.glw.setBackground(bg)
        for axis_name in ("left", "bottom", "right", "top"):
            axis = self.plot_item.getAxis(axis_name)
            axis.setPen(fg)
            axis.setTextPen(fg)
        self.histogram.axis.setPen(fg)
        self.histogram.axis.setTextPen(fg)
        self._image_bg = bg
        self._image_fg = fg

    @staticmethod
    def _theme_colors(key: str) -> tuple[str, str]:
        theme = THEMES[key]
        return theme["pg_background"], theme["pg_foreground"]

    def _apply_image_theme(self, key: str) -> None:
        """Wendet die gewaehlte Thermobild-Darstellung (Hell/Dunkel, Punkt 5
        "Ansichts-Manager") an, haelt die Menue-Actions (siehe _build_menu,
        image_theme_menu) synchron und speichert die Wahl -- Gegenstueck zu
        _apply_graph_theme."""
        self._image_theme = key
        self._apply_image_colors(*self._theme_colors(key))
        act = getattr(self, "_image_theme_actions", {}).get(key)
        if act is not None:
            # KEIN blockSignals hier: die Actions haengen in einer exklusiven
            # QActionGroup (siehe _build_menu), die ihren "aktuell markiert"-
            # Zustand ueber genau dieses toggled()-Signal nachverfolgt. Wird
            # es unterdrueckt (z.B. beim Wiederherstellen des gespeicherten
            # Farbschemas beim Programmstart), "vergisst" die Gruppe, welche
            # Action zuletzt aktiv war -- ein spaeterer echter Klick auf eine
            # ANDERE Action haekt diese dann zwar an, laesst die alte aber
            # faelschlich mit angehakt stehen (Bugreport: "der Punkt bleibt
            # bei 'hell' drin"). Kein Rekursionsrisiko: setChecked() emittiert
            # kein triggered(), nur unser eigener Slot haengt an triggered.
            act.setChecked(True)
        self._settings.setValue("image_theme", key)

    def _apply_graph_theme(self, key: str) -> None:
        """Gegenstueck zu _apply_image_theme, fuer die Kurven-Graphen
        (Zeitverlauf/Live)."""
        self._graph_theme = key
        self._apply_curve_colors(*self._theme_colors(key))
        act = getattr(self, "_graph_theme_actions", {}).get(key)
        if act is not None:
            # Kein blockSignals -- siehe ausführliche Begründung in
            # _apply_image_theme.
            act.setChecked(True)
        self._settings.setValue("graph_theme", key)

    def _on_time_display_changed(self, _index: int) -> None:
        combo = self.sender()
        self._apply_time_display_mode(combo.currentData())

    def _apply_time_display_mode(self, mode: str) -> None:
        """Schaltet die x-Achsen-Beschriftung beider Kurven-Graphen zwischen
        echter Uhrzeit und relativer Laufzeit um -- beide Umschalter (je
        einer pro Graph) bleiben synchron, da beide Graphen dieselbe
        Zeitachse abbilden."""
        self._time_display_mode = mode
        t0 = self.recording.unix_seconds()[0] if self.recording is not None and self.recording.n_frames else 0.0
        runtime = mode == "runtime"
        self.axis_timeseries_bottom.set_runtime_mode(runtime, t0)
        self.axis_live_bottom.set_runtime_mode(runtime, t0)
        for combo in self._time_display_combos:
            combo.blockSignals(True)
            idx = combo.findData(mode)
            if idx >= 0:
                combo.setCurrentIndex(idx)
            combo.blockSignals(False)
        # Das Laufzeit-Format ist nur relevant, solange ueberhaupt "Laufzeit"
        # (statt "Uhrzeit") gezeigt wird.
        for combo in self._runtime_unit_combos:
            combo.setEnabled(runtime)
        self._settings.setValue("time_display_mode", mode)

    def _on_runtime_unit_changed(self, _index: int) -> None:
        combo = self.sender()
        self._apply_runtime_unit(combo.currentData())

    def _apply_runtime_unit(self, unit: str) -> None:
        """Setzt das Laufzeit-Format ("dritte Zeitachse", Nutzerwunsch) --
        "hhmmss" (Standard) oder eine fortlaufende Zahl in "s"/"min"/"h".
        Wirkt global: beide Graph-Achsen (auch waehrend eines Exports, da
        dieser dieselben TimeAxisItem-Instanzen wiederverwendet, siehe
        _temporary_time_display_mode/_dual_time_axis_export) UND
        _format_runtime() (Statuszeile, Video-/Bildstapel-Export-Overlay,
        CSV-Export) greifen auf denselben self._runtime_unit zurueck."""
        self._runtime_unit = unit
        # Auch die (normalerweise ausgeblendeten) OBEREN Zeitachsen mit
        # synchron halten -- sie werden nur waehrend eines Grafik-/Video-
        # Exports mit Zeitachse "Beide" kurz sichtbar (siehe
        # _dual_time_axis_export) und muessten sonst dort faelschlich immer
        # bei "hhmmss" (dem TimeAxisItem-Standardwert) bleiben, unabhaengig
        # vom hier gewaehlten Format.
        self.axis_timeseries_bottom.set_runtime_unit(unit)
        self.axis_live_bottom.set_runtime_unit(unit)
        self.axis_timeseries_top.set_runtime_unit(unit)
        self.axis_live_top.set_runtime_unit(unit)
        for combo in self._runtime_unit_combos:
            combo.blockSignals(True)
            idx = combo.findData(unit)
            if idx >= 0:
                combo.setCurrentIndex(idx)
            combo.blockSignals(False)
        self._settings.setValue("runtime_unit", unit)

    @staticmethod
    def _dark_palette() -> QtGui.QPalette:
        """Vollstaendige dunkle Palette fuer den Fusion-Stil.

        Bugfix: die vorherige Version setzte nur Window/Base/Text/Button
        & Co., aber NICHT Light/Midlight/Dark/Mid/Shadow/Link -- genau
        diese Rollen nutzt Fusion fuer 3D-Kanten/Schattierungen (Rahmen
        von GroupBox/Buttons, Schieberegler-Rille, Scrollbalken,
        deaktivierte Bedienelemente). Ohne sie blieben solche Elemente
        auf ihren urspruenglichen HELLEN Standardwerten haengen, wodurch
        der Dunkelmodus fleckig/unvollstaendig wirkte (Bugreport:
        "funktioniert noch nicht flaechendeckend/sauber")."""
        palette = QtGui.QPalette()
        window = QtGui.QColor("#2b2b2b")
        base = QtGui.QColor("#232323")
        alternate_base = QtGui.QColor("#2f2f2f")
        button = QtGui.QColor("#3a3a3a")
        text = QtGui.QColor("#e0e0e0")
        disabled_text = QtGui.QColor("#7a7a7a")
        highlight = QtGui.QColor("#3b82f6")
        link = QtGui.QColor("#60a5fa")

        palette.setColor(QtGui.QPalette.Window, window)
        palette.setColor(QtGui.QPalette.WindowText, text)
        palette.setColor(QtGui.QPalette.Base, base)
        palette.setColor(QtGui.QPalette.AlternateBase, alternate_base)
        palette.setColor(QtGui.QPalette.ToolTipBase, window)
        palette.setColor(QtGui.QPalette.ToolTipText, text)
        palette.setColor(QtGui.QPalette.Text, text)
        palette.setColor(QtGui.QPalette.Button, button)
        palette.setColor(QtGui.QPalette.ButtonText, text)
        palette.setColor(QtGui.QPalette.BrightText, QtGui.QColor("#ff5555"))
        palette.setColor(QtGui.QPalette.Highlight, highlight)
        palette.setColor(QtGui.QPalette.HighlightedText, QtGui.QColor("#ffffff"))
        # 3D-Schattierungen (Rahmen, Rillen, Trennlinien) -- ohne diese vier
        # bleiben GroupBox-Rahmen, Schieberegler-Rille etc. hell (siehe oben).
        palette.setColor(QtGui.QPalette.Light, QtGui.QColor("#4a4a4a"))
        palette.setColor(QtGui.QPalette.Midlight, QtGui.QColor("#3f3f3f"))
        palette.setColor(QtGui.QPalette.Dark, QtGui.QColor("#1a1a1a"))
        palette.setColor(QtGui.QPalette.Mid, QtGui.QColor("#2f2f2f"))
        palette.setColor(QtGui.QPalette.Shadow, QtGui.QColor("#0d0d0d"))
        palette.setColor(QtGui.QPalette.Link, link)
        palette.setColor(QtGui.QPalette.LinkVisited, link)
        if hasattr(QtGui.QPalette, "PlaceholderText"):
            palette.setColor(QtGui.QPalette.PlaceholderText, disabled_text)
        palette.setColor(QtGui.QPalette.Disabled, QtGui.QPalette.WindowText, disabled_text)
        palette.setColor(QtGui.QPalette.Disabled, QtGui.QPalette.Text, disabled_text)
        palette.setColor(QtGui.QPalette.Disabled, QtGui.QPalette.ButtonText, disabled_text)
        palette.setColor(QtGui.QPalette.Disabled, QtGui.QPalette.Base, window)
        palette.setColor(QtGui.QPalette.Disabled, QtGui.QPalette.Button, window)
        palette.setColor(QtGui.QPalette.Disabled, QtGui.QPalette.Highlight, QtGui.QColor("#454545"))
        palette.setColor(QtGui.QPalette.Disabled, QtGui.QPalette.HighlightedText, disabled_text)
        return palette

    @staticmethod
    def _light_palette() -> QtGui.QPalette:
        """Vollstaendige helle Palette fuer den Fusion-Stil, Gegenstueck zu
        _dark_palette() -- wird explizit gesetzt statt sich auf
        app.style().standardPalette() zu verlassen (siehe _apply_window_theme
        fuer den Grund: diese folgt unter Windows dem OS-Design)."""
        palette = QtGui.QPalette()
        window = QtGui.QColor("#efefef")
        base = QtGui.QColor("#ffffff")
        alternate_base = QtGui.QColor("#f5f5f5")
        button = QtGui.QColor("#efefef")
        text = QtGui.QColor("#000000")
        disabled_text = QtGui.QColor("#a0a0a0")
        highlight = QtGui.QColor("#3b82f6")
        link = QtGui.QColor("#2563eb")

        palette.setColor(QtGui.QPalette.Window, window)
        palette.setColor(QtGui.QPalette.WindowText, text)
        palette.setColor(QtGui.QPalette.Base, base)
        palette.setColor(QtGui.QPalette.AlternateBase, alternate_base)
        palette.setColor(QtGui.QPalette.ToolTipBase, QtGui.QColor("#ffffdc"))
        palette.setColor(QtGui.QPalette.ToolTipText, text)
        palette.setColor(QtGui.QPalette.Text, text)
        palette.setColor(QtGui.QPalette.Button, button)
        palette.setColor(QtGui.QPalette.ButtonText, text)
        palette.setColor(QtGui.QPalette.BrightText, QtGui.QColor("#cc0000"))
        palette.setColor(QtGui.QPalette.Highlight, highlight)
        palette.setColor(QtGui.QPalette.HighlightedText, QtGui.QColor("#ffffff"))
        palette.setColor(QtGui.QPalette.Light, QtGui.QColor("#ffffff"))
        palette.setColor(QtGui.QPalette.Midlight, QtGui.QColor("#e3e3e3"))
        palette.setColor(QtGui.QPalette.Dark, QtGui.QColor("#a0a0a0"))
        palette.setColor(QtGui.QPalette.Mid, QtGui.QColor("#b8b8b8"))
        palette.setColor(QtGui.QPalette.Shadow, QtGui.QColor("#767676"))
        palette.setColor(QtGui.QPalette.Link, link)
        palette.setColor(QtGui.QPalette.LinkVisited, link)
        if hasattr(QtGui.QPalette, "PlaceholderText"):
            palette.setColor(QtGui.QPalette.PlaceholderText, disabled_text)
        palette.setColor(QtGui.QPalette.Disabled, QtGui.QPalette.WindowText, disabled_text)
        palette.setColor(QtGui.QPalette.Disabled, QtGui.QPalette.Text, disabled_text)
        palette.setColor(QtGui.QPalette.Disabled, QtGui.QPalette.ButtonText, disabled_text)
        palette.setColor(QtGui.QPalette.Disabled, QtGui.QPalette.Base, window)
        palette.setColor(QtGui.QPalette.Disabled, QtGui.QPalette.Button, window)
        palette.setColor(QtGui.QPalette.Disabled, QtGui.QPalette.Highlight, QtGui.QColor("#d4d4d4"))
        palette.setColor(QtGui.QPalette.Disabled, QtGui.QPalette.HighlightedText, disabled_text)
        return palette

    def _connect_scene_events(self) -> None:
        self.glw.scene().sigMouseMoved.connect(self._on_scene_mouse_moved)
        self.glw.scene().sigMouseClicked.connect(self._on_scene_mouse_clicked)

