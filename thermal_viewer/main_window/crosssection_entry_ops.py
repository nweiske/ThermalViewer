"""Querschnitte: beliebig viele (max. 10) benannte, farbige Querschnitt-
Linien gleichzeitig (Nutzerwunsch: "ein neuer Tab ... ähnlich wie Maßstab
... eine Liste/Übersicht über alle Querschnitte ... mehr als nur einen
Querschnitt auf einmal setzen ... mit unterschiedlichen Farben ... mit der
Option eine Linie zu löschen"). Ersetzt den fruehren einzelnen, der Maus
folgenden Hover/Pin-Querschnitt vollstaendig (Folgeanfrage: "da ich ja jetzt
einen eigenen Tab habe, brauche ich die urspruengliche Querschnittsfunktion
... nicht mehr").

Jede Querschnitt-Linie ist FEST horizontal ODER vertikal (bei der Erzeugung
gewaehlt, siehe die zwei "+ Horizontal"/"+ Vertikal"-Knoepfe -- eine
nachtraegliche Richtungsaenderung wuerde eine voellig andere Positions-
Bedeutung (Zeile vs. Spalte) implizieren, daher stattdessen neu anlegen),
analog zu einer Probenhöhe (sample_height_ops.py), aber ohne deren Kanten-
Erkennung -- hier zaehlt nur das rohe Temperaturprofil, keine Breiten-
Messung.

Die Kurve jeder Querschnitt-Linie liegt auf dem gemeinsamen Graphen
self.crosssection_plot (siehe ui_build.py) -- X-Achse ist der Pixel-Index
entlang der gewaehlten Zeile/Spalte (die Achsenbeschriftung "Bildbreite bzw.
Höhe (px)" ist generisch genug fuer gemischte Richtungen). Anders als die
Probenhöhen- ODER Schwindungs-Kurven (die ueber die ZEIT verlaufen) zeigt
diese Kurve das Profil des AKTUELLEN Bildes -- muss also bei JEDEM
Frame-Wechsel neu gezeichnet werden (siehe _update_all_crosssection_entries,
aufgerufen von mouse_ops.py:_update_status_bar bei jedem Frame-/
Positions-Wechsel UND von layer_tabs_ops.py bei jedem Ebenen-Tab-Wechsel).

Die Linien im Thermobild folgen einem EIGENEN Ebenen-Tab ("Querschnitte",
siehe layer_tabs_ops.py) -- wie ROI-Boxen/Schwindungsbox nur sichtbar,
waehrend dieser Tab aktiv ist. Die KURVEN selbst sind davon bewusst
UNABHAENGIG (immer sichtbar, sofern aktiv).

Y-Achse (Folgeanfrage): standardmaessig an die aktuelle Farbskala/"Legende"
gekoppelt (self.image_item.getLevels(), siehe _update_crosssection_y_axis)
-- dieselbe Temperaturskala wie im Thermobild, unabhaengig davon, welche
Werte die gerade sichtbaren Querschnitt-Kurven annehmen (KEIN Fit-auf-
Kurvendaten wie pyqtgraphs eigenes autoRange). Per "Y-Achse einstellen…"
(ui_build.py) manuell ueberschreibbar -- analog zu AxisSettingsDialog bei
den anderen Graphen, aber bewusst nur Y (ein Pixel-Index als X-Achse hat
keine sinnvolle "Achsen einstellen"-Entsprechung)."""
from __future__ import annotations

import colorsys
from functools import partial

import numpy as np
import pyqtgraph as pg
from qtpy import QtCore, QtGui, QtWidgets

MAX_CROSSSECTION_ENTRY_COUNT = 10
_CROSSSECTION_ENTRY_COLORS = ["#fb7185", "#84cc16", "#6366f1", "#fbbf24", "#06b6d4"]


def _crosssection_entry_color(number: int) -> str:
    """Analog zu sample_height_ops.py:_sample_height_color -- eigene, von
    ROI-/Probenhöhen-/Schwindungsbox-Farben klar unterscheidbare Palette."""
    idx = number - 1
    if 0 <= idx < len(_CROSSSECTION_ENTRY_COLORS):
        return _CROSSSECTION_ENTRY_COLORS[idx]
    hue = (idx * 0.6180339887498949 + 0.82) % 1.0
    r, g, b = colorsys.hsv_to_rgb(hue, 0.65, 0.95)
    return "#%02x%02x%02x" % (round(r * 255), round(g * 255), round(b * 255))


class CrosssectionEntry:
    """Bündelt EINE benannte Querschnitt-Linie mit ihrer Position im Bild
    und ihrer Kurve im Querschnitt-Graphen -- analog zu SampleHeightEntry
    (sample_height_ops.py), aber mit fester Richtung (horizontal/vertikal)
    statt immer nur horizontal."""

    def __init__(self, number: int, color: str, direction: str, view_box: pg.ViewBox, curve: pg.PlotDataItem):
        self.number = number
        self.color = color
        self.name = f"Querschnitt {number}"
        self.direction = direction  # "horizontal" oder "vertical", fest ab Erzeugung
        self.position = 0  # Zeile (horizontal) bzw. Spalte (vertikal)
        self.enabled = True
        self.curve = curve
        self.curve.opts["name"] = self.name

        angle = 0 if direction == "horizontal" else 90
        self.line = pg.InfiniteLine(
            angle=angle, movable=True,
            pen=pg.mkPen(color, width=1.5, style=QtCore.Qt.DashLine),
            hoverPen=pg.mkPen(color, width=2.5, style=QtCore.Qt.DashLine),
        )
        self.line.setVisible(False)
        view_box.addItem(self.line)

        self.name_label = pg.TextItem(text=self.name, color=color, anchor=(0, 1), fill=(0, 0, 0, 140))
        self.name_label.setVisible(False)
        view_box.addItem(self.name_label)

        # Von MainWindow gesetzt, hier nur Platzhalter fuer Typklarheit.
        self.spin_position: QtWidgets.QSpinBox | None = None
        self.edit_name: QtWidgets.QLineEdit | None = None
        self.btn_color: QtWidgets.QPushButton | None = None
        self.chk_enabled: QtWidgets.QCheckBox | None = None

    def set_name(self, name: str) -> None:
        self.name = name
        self.curve.opts["name"] = name
        self.name_label.setText(name)

    def set_color(self, color: str) -> None:
        self.color = color
        self.line.setPen(pg.mkPen(color, width=1.5, style=QtCore.Qt.DashLine))
        self.line.hoverPen = pg.mkPen(color, width=2.5, style=QtCore.Qt.DashLine)
        self.curve.setPen(pg.mkPen(color, width=2))
        self.name_label.setColor(color)
        if self.btn_color is not None:
            self.btn_color.setStyleSheet(
                f"background-color:{color}; border:1px solid #333; border-radius:4px;"
            )

    def remove_from_view(self, view_box: pg.ViewBox, plot: pg.PlotWidget) -> None:
        view_box.removeItem(self.line)
        view_box.removeItem(self.name_label)
        plot.removeItem(self.curve)


class _CrosssectionEntryMixin:
    def _build_crosssection_entries_panel(self) -> None:
        box = QtWidgets.QGroupBox("Querschnitte (mehrere gleichzeitig)")
        self._crosssection_entries_groupbox = box
        layout = QtWidgets.QVBoxLayout(box)

        info = QtWidgets.QLabel(
            "Beliebig viele (max. 10) dauerhafte, benannte Querschnitt-Linien gleichzeitig -- "
            "jede mit eigener Farbe. „+ Horizontal“/„+ Vertikal“ einschalten und dann beliebig "
            "oft ins Bild klicken, um weitere Linien zu platzieren (wie bei den Probenhöhen). "
            "Die Kurven erscheinen gemeinsam im Querschnitt-Graphen (Tab „Querschnitt“)."
        )
        info.setWordWrap(True)
        info.setAlignment(QtCore.Qt.AlignJustify)
        info.setStyleSheet("color:#6b7280;")
        layout.addWidget(info)

        # Bugreport: mit den Knoepfen UNTER der Liste rutschten sie bei
        # leerer/kurzer Liste trotzdem aus dem sichtbaren Dock-Bereich
        # (die Liste beansprucht ueber ihren Stretch-Faktor 1 unten
        # weiterhin den gesamten verfuegbaren Platz der Seite, siehe
        # Kommentar bei crosssection_entry_list) -- man musste scrollen, um
        # sie zu erreichen. Jetzt VOR der Liste, damit sie unabhaengig von
        # deren Groesse immer sofort erreichbar sind.
        add_row = QtWidgets.QHBoxLayout()
        self.btn_add_crosssection_horizontal = QtWidgets.QPushButton("+ Horizontal")
        self.btn_add_crosssection_horizontal.setCheckable(True)
        self.btn_add_crosssection_horizontal.setToolTip(
            f"Platzieren-Modus: Klick ins Bild setzt eine neue horizontale Querschnitt-Linie\n"
            f"(bis zu {MAX_CROSSSECTION_ENTRY_COUNT}, beliebig oft -- erneut klicken zum Beenden)."
        )
        self.btn_add_crosssection_horizontal.toggled.connect(
            partial(self._on_crosssection_entry_mode_toggled, "horizontal")
        )
        add_row.addWidget(self.btn_add_crosssection_horizontal)
        self.btn_add_crosssection_vertical = QtWidgets.QPushButton("+ Vertikal")
        self.btn_add_crosssection_vertical.setCheckable(True)
        self.btn_add_crosssection_vertical.setToolTip(
            f"Platzieren-Modus: Klick ins Bild setzt eine neue vertikale Querschnitt-Linie\n"
            f"(bis zu {MAX_CROSSSECTION_ENTRY_COUNT}, beliebig oft -- erneut klicken zum Beenden)."
        )
        self.btn_add_crosssection_vertical.toggled.connect(
            partial(self._on_crosssection_entry_mode_toggled, "vertical")
        )
        add_row.addWidget(self.btn_add_crosssection_vertical)
        layout.addLayout(add_row)

        # Anders als sample_height_ops.py (Liste dort TEILT sich die Seite
        # mit der Schwindungsmessung-Box, braucht also eine feste Hoehen-
        # grenze) hat diese Liste eine EIGENE Panel-Seite fuer sich allein
        # (siehe roi_panel_build.py, analog zu "Maßstab") -- Bugreport: bei
        # vielen Eintraegen musste trotz sichtbar freiem Platz in der Seite
        # manuell gescrollt werden. KEINE Maximalhoehe mehr, stattdessen
        # Stretch-Faktor 1 unten, damit die Liste den gesamten verfuegbaren
        # Platz der Seite einnimmt (siehe auch crosssection_entries_page_
        # layout in roi_panel_build.py, das dafuer ebenfalls angepasst ist).
        self.crosssection_entry_list = QtWidgets.QListWidget()
        layout.addWidget(self.crosssection_entry_list, 1)

    def _update_crosssection_entry_curve(self, entry: CrosssectionEntry) -> None:
        """Zeichnet das Temperaturprofil DES AKTUELL ANGEZEIGTEN Bildes an
        entry.position -- anders als Probenhöhen-/Schwindungs-Kurven (die
        ueber die ZEIT laufen) ist das hier eine reine Pro-Bild-Momentaufnahme,
        muss also bei jedem Frame-Wechsel neu gesetzt werden (siehe
        _update_all_crosssection_entries)."""
        if self.recording is None or self.recording.n_frames == 0:
            entry.curve.clear()
            entry.curve.setVisible(False)
            return
        frame = self.recording.frames[self.current_index]
        rows, cols = self.recording.shape
        if entry.direction == "horizontal":
            row = max(0, min(rows - 1, entry.position))
            values = frame[row, :]
            xs = np.arange(cols)
        else:
            col = max(0, min(cols - 1, entry.position))
            values = frame[:, col]
            xs = np.arange(rows)
        entry.curve.setData(xs, values)
        entry.curve.setVisible(entry.enabled)

    def _update_all_crosssection_entries(self) -> None:
        for entry in self._crosssection_entries:
            self._update_crosssection_entry_curve(entry)
        self._update_crosssection_y_axis()

    def _update_crosssection_y_axis(self) -> None:
        """Y-Achse (Temperatur) folgt standardmaessig der aktuellen Farbskala/
        "Legende" (self.image_item.getLevels()) -- bei jeder Frame-/Positions-
        Aenderung neu angewendet (siehe _update_all_crosssection_entries),
        damit z.B. "Automatisch: Pro Bild" (frame_nav.py:_level_mode) auch
        hier live mitlaeuft, statt wie pyqtgraphs eigenes autoRange auf die
        gerade sichtbaren KURVENWERTE zu passen. Eine manuell gesetzte
        Y-Achse (siehe _open_crosssection_axis_settings) bleibt davon
        unberuehrt, bis sie ueber "Achsen zuruecksetzen"
        (_reset_crosssection_axes) wieder aufgehoben wird."""
        plot_item = self.crosssection_plot.getPlotItem()
        if self._crosssection_y_manual:
            lo, hi = self._crosssection_y_range
        else:
            if self.recording is None:
                return
            levels = self.image_item.getLevels()
            if levels is None:
                return
            lo, hi = levels
        plot_item.setYRange(lo, hi, padding=0)
        y_axis_item = plot_item.getAxis("left")
        if self._crosssection_y_spacing is not None:
            y_axis_item.setTickSpacing(major=self._crosssection_y_spacing, minor=self._crosssection_y_spacing / 5)
        else:
            y_axis_item.setTickSpacing()

    def _reset_crosssection_axes(self) -> None:
        """"Achsen zurücksetzen"-Knopf (ui_build.py): verwirft eine evtl.
        gesetzte eigene Y-Achse (danach folgt sie wieder automatisch der
        Farbskala/Legende, siehe _update_crosssection_y_axis) UND setzt die
        X-Achse auf Autofit zurueck (analog zu _reset_plot_view bei den
        anderen Graphen)."""
        self._crosssection_y_manual = False
        self._crosssection_y_range = None
        self._crosssection_y_spacing = None
        self.crosssection_plot.getPlotItem().enableAutoRange(x=True)
        self._update_crosssection_y_axis()

    def _open_crosssection_axis_settings(self) -> None:
        """"Y-Achse einstellen…"-Knopf (ui_build.py, Folgeanfrage: "gib mir
        zusätzlich die Option (wie bei den anderen Graphen) mir die Y-Achse
        in einem Menü anzupassen") -- AxisSettingsDialog mit show_x=False
        (ein Pixel-Index als X-Achse hat keine sinnvolle "Achsen
        einstellen"-Entsprechung, siehe dessen Docstring)."""
        if self._crosssection_y_manual:
            current_min, current_max = self._crosssection_y_range
        else:
            levels = self.image_item.getLevels() if self.recording is not None else None
            current_min, current_max = levels if levels is not None else (0.0, 50.0)

        # Lokaler statt Modul-Import, siehe ui_build.py:_open_axis_settings
        # fuer die volle Begruendung (Test-Monkeypatchbarkeit).
        from . import AxisSettingsDialog

        dialog = AxisSettingsDialog(
            self,
            current_x_min=0.0, current_x_max=1.0,
            current_y_min=current_min, current_y_max=current_max,
            y_manual_range=self._crosssection_y_manual,
            y_spacing=self._crosssection_y_spacing,
            show_x=False,
        )
        if dialog.exec() != QtWidgets.QDialog.DialogCode.Accepted:
            return

        if dialog.y_manual_range():
            self._crosssection_y_manual = True
            self._crosssection_y_range = dialog.y_range()
        else:
            self._crosssection_y_manual = False
            self._crosssection_y_range = None
        self._crosssection_y_spacing = dialog.y_spacing() if dialog.y_manual_spacing() else None
        self._update_crosssection_y_axis()
        self.statusBar().showMessage("Y-Achse des Querschnitt-Graphen aktualisiert.", 4000)

    def _update_crosssection_entry_line_pos(self, entry: CrosssectionEntry) -> None:
        entry.line.blockSignals(True)
        entry.line.setPos(entry.position + 0.5)
        entry.line.blockSignals(False)
        self._update_crosssection_entry_label_pos(entry)

    def _update_crosssection_entry_label_pos(self, entry: CrosssectionEntry) -> None:
        if self.recording is None:
            return
        if entry.direction == "horizontal":
            entry.name_label.setPos(0, entry.position + 0.5)
        else:
            entry.name_label.setPos(entry.position + 0.5, 0)

    def _apply_crosssection_entries_visibility(self) -> None:
        """Analog zu sample_height_ops.py:_apply_sample_height_visibility --
        die LINIEN im Thermobild folgen einem eigenen Ebenen-Tab
        ("querschnitt", siehe layer_tabs_ops.py), die KURVEN selbst sind
        davon bewusst unberuehrt (siehe Moduldocstring)."""
        tab_active = self._is_layer_tab_active("querschnitt") and self.recording is not None
        for entry in self._crosssection_entries:
            visible = tab_active and entry.enabled
            entry.line.setVisible(visible)
            entry.name_label.setVisible(visible)

    def _refresh_crosssection_entry_rows(self) -> None:
        self.crosssection_entry_list.clear()
        rows, cols = self.recording.shape if self.recording is not None else (0, 0)
        for i, entry in enumerate(self._crosssection_entries):
            row_widget = QtWidgets.QWidget()
            row_layout = QtWidgets.QHBoxLayout(row_widget)
            row_layout.setContentsMargins(4, 2, 4, 2)

            chk = QtWidgets.QCheckBox()
            chk.setChecked(entry.enabled)
            chk.setToolTip("Querschnitt-Linie ein-/ausblenden (Linie im Bild + Kurve).")
            chk.toggled.connect(partial(self._on_crosssection_entry_enabled_toggled, i))
            row_layout.addWidget(chk)
            entry.chk_enabled = chk

            edit_name = QtWidgets.QLineEdit(entry.name)
            edit_name.setToolTip("Name (frei editierbar, wie bei Messbereichen).")
            edit_name.editingFinished.connect(partial(self._on_crosssection_entry_name_edited, i))
            row_layout.addWidget(edit_name, 1)
            entry.edit_name = edit_name

            is_horizontal = entry.direction == "horizontal"
            row_layout.addWidget(QtWidgets.QLabel("Zeile:" if is_horizontal else "Spalte:"))
            spin_position = QtWidgets.QSpinBox()
            spin_position.setRange(0, max(0, (rows if is_horizontal else cols) - 1))
            spin_position.setValue(entry.position)
            spin_position.valueChanged.connect(partial(self._on_crosssection_entry_position_spin_changed, i))
            spin_position.editingFinished.connect(self._end_grouped_undo_edit)
            row_layout.addWidget(spin_position)
            entry.spin_position = spin_position

            btn_color = QtWidgets.QPushButton()
            btn_color.setFixedSize(20, 20)
            btn_color.setCursor(QtCore.Qt.PointingHandCursor)
            btn_color.setToolTip("Farbe ändern.")
            btn_color.setStyleSheet(
                f"background-color:{entry.color}; border:1px solid #333; border-radius:4px;"
            )
            btn_color.clicked.connect(partial(self._on_crosssection_entry_color_clicked, i))
            row_layout.addWidget(btn_color)
            entry.btn_color = btn_color

            btn_remove = QtWidgets.QPushButton("×")
            btn_remove.setFixedSize(22, 22)
            btn_remove.setToolTip("Querschnitt-Linie endgültig entfernen.")
            btn_remove.clicked.connect(partial(self._on_crosssection_entry_remove_clicked, i))
            row_layout.addWidget(btn_remove)

            item = QtWidgets.QListWidgetItem()
            item.setSizeHint(row_widget.sizeHint())
            self.crosssection_entry_list.addItem(item)
            self.crosssection_entry_list.setItemWidget(item, row_widget)
        at_max = len(self._crosssection_entries) >= MAX_CROSSSECTION_ENTRY_COUNT
        self.btn_add_crosssection_horizontal.setEnabled(not at_max)
        self.btn_add_crosssection_vertical.setEnabled(not at_max)

    def _create_crosssection_entry(self, number: int, color: str, direction: str) -> CrosssectionEntry:
        """Gemeinsame Stelle fuer die beiden "+ Horizontal"/"+ Vertikal"-
        Knoepfe UND das Wiederherstellen aus einer Projektdatei/einem
        Undo-Snapshot (siehe project_io.py:_load_project_crosssection_
        entries), damit die Signal-Verdrahtung an GENAU einer Stelle passiert
        (analog zu sample_height_ops.py:_create_sample_height_entry)."""
        curve = self.crosssection_plot.plot(pen=pg.mkPen(color, width=2), name=f"Querschnitt {number}")
        entry = CrosssectionEntry(number, color, direction, self.view_box, curve)
        entry.line.sigPositionChangeFinished.connect(partial(self._on_crosssection_entry_line_dragged, entry))
        return entry

    def _add_crosssection_entry(self, direction: str, position: int) -> CrosssectionEntry:
        """Erzeugt EINE neue, platzierte Querschnitt-Linie -- vom Klick-
        Platzieren-Modus (siehe _handle_crosssection_entry_click) fuer JEDEN
        Linksklick ins Bild aufgerufen."""
        self._push_undo_snapshot()
        number = self._crosssection_entry_next_number
        self._crosssection_entry_next_number += 1
        entry = self._create_crosssection_entry(number, _crosssection_entry_color(number), direction)
        entry.position = position
        self._update_crosssection_entry_line_pos(entry)
        self._crosssection_entries.append(entry)
        self._refresh_crosssection_entry_rows()
        self._update_crosssection_entry_curve(entry)
        self._apply_crosssection_entries_visibility()
        return entry

    def _on_crosssection_entry_mode_toggled(self, direction: str, checked: bool) -> None:
        if not checked:
            self._cancel_crosssection_entry_tool()
            return
        if not self._start_crosssection_entry_tool(direction):
            btn = (
                self.btn_add_crosssection_horizontal if direction == "horizontal"
                else self.btn_add_crosssection_vertical
            )
            btn.blockSignals(True)
            btn.setChecked(False)
            btn.blockSignals(False)

    def _start_crosssection_entry_tool(self, direction: str) -> bool:
        if self.recording is None:
            QtWidgets.QMessageBox.information(self, "Keine Daten", "Bitte zuerst eine Messreihe laden.")
            return False
        if len(self._crosssection_entries) >= MAX_CROSSSECTION_ENTRY_COUNT:
            QtWidgets.QMessageBox.information(
                self, "Maximum erreicht",
                f"Maximal {MAX_CROSSSECTION_ENTRY_COUNT} Querschnitte gleichzeitig möglich.",
            )
            return False
        # Dieselbe Mutual-Exclusion wie Maßstab/Messmodus/ROI-Platzieren/
        # Probenhöhen (siehe mouse_ops.py:_on_scene_mouse_clicked).
        if self._armed_entry is not None:
            self._disarm_roi_placement()
        if self._ruler_armed:
            self._cancel_ruler_tool()
        if self._measurement_armed:
            self._cancel_measurement_tool()
        if self._sample_height_armed:
            self._cancel_sample_height_tool()
        # Die jeweils ANDERE Richtung mit abschalten -- nur eine kann
        # gleichzeitig armiert sein.
        if self._crosssection_entry_armed_direction is not None:
            self._cancel_crosssection_entry_tool()
        self._set_active_layer_tab("querschnitt")
        self._crosssection_entry_armed_direction = direction
        label = "horizontale" if direction == "horizontal" else "vertikale"
        self.statusBar().showMessage(
            f"Querschnitte-Modus aktiv: Klick ins Bild platziert eine neue {label} Linie "
            "(beliebig oft, zum Beenden den Knopf erneut klicken)."
        )
        return True

    def _cancel_crosssection_entry_tool(self) -> None:
        self._crosssection_entry_armed_direction = None
        self.crosssection_entry_preview_line.setVisible(False)
        for btn in (self.btn_add_crosssection_horizontal, self.btn_add_crosssection_vertical):
            if btn.isChecked():
                btn.blockSignals(True)
                btn.setChecked(False)
                btn.blockSignals(False)

    def _handle_crosssection_entry_hover(self, scene_pos) -> None:
        """Reines Maus-Hover waehrend "+ Horizontal"/"+ Vertikal" armiert ist
        (siehe mouse_ops.py:_on_scene_mouse_moved, dort mit derselben
        Prioritaet VOR dem Dock-Tab-Hover behandelt wie der Klick-Handler) --
        laesst die Vorschau-Linie der Maus folgen, UNABHAENGIG vom Pin-
        Zustand des einzelnen Hover/Pin-Querschnitts (siehe ui_build.py fuer
        die Begruendung)."""
        row_col = self._pixel_at_scene_pos(scene_pos)
        if row_col is None:
            self.crosssection_entry_preview_line.setVisible(False)
            return
        row, col = row_col
        if self._crosssection_entry_armed_direction == "horizontal":
            self.crosssection_entry_preview_line.setAngle(0)
            self.crosssection_entry_preview_line.setPos(row + 0.5)
        else:
            self.crosssection_entry_preview_line.setAngle(90)
            self.crosssection_entry_preview_line.setPos(col + 0.5)
        self.crosssection_entry_preview_line.setVisible(True)

    def _handle_crosssection_entry_click(self, event) -> None:
        if event.button() != QtCore.Qt.LeftButton:
            self._cancel_crosssection_entry_tool()
            self.statusBar().showMessage("Querschnitte-Platzieren abgebrochen.", 3000)
            return
        row_col = self._pixel_at_scene_pos(event.scenePos())
        if row_col is None:
            return
        row, col = row_col
        if len(self._crosssection_entries) >= MAX_CROSSSECTION_ENTRY_COUNT:
            self._cancel_crosssection_entry_tool()
            QtWidgets.QMessageBox.information(
                self, "Maximum erreicht",
                f"Maximal {MAX_CROSSSECTION_ENTRY_COUNT} Querschnitte gleichzeitig möglich.",
            )
            return
        direction = self._crosssection_entry_armed_direction
        position = row if direction == "horizontal" else col
        entry = self._add_crosssection_entry(direction, position)
        if len(self._crosssection_entries) >= MAX_CROSSSECTION_ENTRY_COUNT:
            self._cancel_crosssection_entry_tool()
        self.statusBar().showMessage(f"„{entry.name}“ platziert.", 3000)

    def _on_crosssection_entry_enabled_toggled(self, index: int, checked: bool) -> None:
        if not (0 <= index < len(self._crosssection_entries)):
            return
        self._push_undo_snapshot()
        self._crosssection_entries[index].enabled = checked
        self._apply_crosssection_entries_visibility()
        self._update_crosssection_entry_curve(self._crosssection_entries[index])

    def _on_crosssection_entry_name_edited(self, index: int) -> None:
        if not (0 <= index < len(self._crosssection_entries)):
            return
        entry = self._crosssection_entries[index]
        text = entry.edit_name.text().strip() or f"Querschnitt {entry.number}"
        if text == entry.name:
            return
        self._push_undo_snapshot()
        entry.set_name(text)

    def _on_crosssection_entry_position_spin_changed(self, index: int, value: int) -> None:
        if not (0 <= index < len(self._crosssection_entries)):
            return
        self._begin_grouped_undo_edit()
        entry = self._crosssection_entries[index]
        entry.position = value
        self._update_crosssection_entry_line_pos(entry)
        self._update_crosssection_entry_curve(entry)

    def _on_crosssection_entry_line_dragged(self, entry: CrosssectionEntry, line) -> None:
        # Ueber die ENTRY-Referenz gebunden (siehe _create_crosssection_entry
        # fuer den Grund, analog zu sample_height_ops.py).
        if entry not in self._crosssection_entries or self.recording is None:
            return
        self._push_undo_snapshot()
        rows, cols = self.recording.shape
        limit = rows if entry.direction == "horizontal" else cols
        entry.position = max(0, min(limit - 1, int(round(line.value() - 0.5))))
        if entry.spin_position is not None:
            entry.spin_position.blockSignals(True)
            entry.spin_position.setValue(entry.position)
            entry.spin_position.blockSignals(False)
        self._update_crosssection_entry_curve(entry)
        self.statusBar().showMessage(f"„{entry.name}“ verschoben.", 3000)

    def _on_crosssection_entry_color_clicked(self, index: int) -> None:
        if not (0 <= index < len(self._crosssection_entries)):
            return
        entry = self._crosssection_entries[index]
        color = QtWidgets.QColorDialog.getColor(QtGui.QColor(entry.color), self, "Farbe des Querschnitts wählen")
        if not color.isValid():
            return
        self._push_undo_snapshot()
        entry.set_color(color.name())

    def _on_crosssection_entry_remove_clicked(self, index: int) -> None:
        if not (0 <= index < len(self._crosssection_entries)):
            return
        self._push_undo_snapshot()
        entry = self._crosssection_entries.pop(index)
        entry.remove_from_view(self.view_box, self.crosssection_plot)
        name = entry.name
        self._refresh_crosssection_entry_rows()
        self.statusBar().showMessage(f"Querschnitt „{name}“ entfernt.", 3000)

    def _clear_all_crosssection_entries_for_new_recording(self) -> None:
        """Wirft ALLE Querschnitte vollstaendig weg -- vom Laden einer
        WEITEREN Messreihe aufzurufen (frame_nav.py:_reset_state_for_new_
        recording), analog zu sample_height_ops.py:_clear_all_sample_
        heights_for_new_recording."""
        if self._crosssection_entry_armed_direction is not None:
            self._cancel_crosssection_entry_tool()
        for entry in self._crosssection_entries:
            entry.remove_from_view(self.view_box, self.crosssection_plot)
        self._crosssection_entries.clear()
        self._crosssection_entry_next_number = 1
        self._refresh_crosssection_entry_rows()

    def _reset_crosssection_entries_for_recording(self) -> None:
        """Bei jedem (Neu-)Laden einer Aufnahme (siehe frame_nav.py:
        _set_recording) -- Positionen der VORHERIGEN Aufnahme auf die neue
        Bildgroesse klemmen (NICHT verwerfen), analog zu sample_height_
        ops.py:_reset_sample_heights_for_recording."""
        if self.recording is None:
            for entry in self._crosssection_entries:
                self._update_crosssection_entry_curve(entry)
            self._apply_crosssection_entries_visibility()
            return
        rows, cols = self.recording.shape
        for entry in self._crosssection_entries:
            limit = rows if entry.direction == "horizontal" else cols
            entry.position = max(0, min(limit - 1, entry.position))
            if entry.spin_position is not None:
                entry.spin_position.blockSignals(True)
                entry.spin_position.setRange(0, max(0, limit - 1))
                entry.spin_position.setValue(entry.position)
                entry.spin_position.blockSignals(False)
            self._update_crosssection_entry_line_pos(entry)
        self._update_all_crosssection_entries()
        self._apply_crosssection_entries_visibility()
