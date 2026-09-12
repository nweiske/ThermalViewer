"""Schwindungsmessung (Punkt 9, Nutzerwunsch): automatische Breitenmessung
über die Zeit per Kantenverfolgung, nach dem Vorbild eines anderen Tools
des Nutzers -- zwei "Flanken"-Bereiche (links/rechts) werden ab einem frei
wählbaren Startbild verfolgt: pro Bild wird innerhalb des jeweiligen
Bereichs die Spalte gesucht, an der die Temperatur den Schwellenwert quert,
und der Bereich für das NÄCHSTE Bild um genau diese Stelle neu zentriert
(so "wandert" er mit einer schrumpfenden/sich verschiebenden Probe mit).
Ein dritter, rein visueller Referenzrahmen ("Probenbreite") hilft beim
Ausrichten, geht aber NICHT in die Berechnung ein.

Ausdrücklich EXPERIMENTELL (Nutzer-Hinweis): typische Schwindungswerte
liegen bei < 10%, oft < 1% -- ob das bei der jeweiligen Kamera-Auflösung
überhaupt sinnvoll aufgelöst werden kann, ist offen. Diese erste Version
bewusst schlank gehalten: EINE Messung gleichzeitig (wie das Maßstab-
Werkzeug), Boxen werden direkt im Bild per Maus gezogen/skaliert (dieselben
AdjustableROI-Griffe wie bei Messbereichen), keine eigene Klick-Platzieren-
Mechanik. Das Ergebnis lebt als eigene Kurve im Zeitverlauf-Graphen und
ist optional als zusätzliche Spalte im Werte-Export (CSV/JSON/Text, siehe
export_csv.py) enthalten. Kein Video-/Bildstapel-Export-Anschluss (keine
Boxen-Overlays im exportierten Bild/Video) in dieser Version.

Zwei wählbare Auswertungs-Geometrien FÜR DIE BREITENMESSUNG (Nutzerfrage:
ist die Messung geometrieabhängig?): "rechteckig/quaderförmig" nimmt je Box
den Median der Kantenspalte über die Boxzeilen (richtig für gerade,
parallele Kanten); "rund/zylindrisch" sucht stattdessen zeilenweise die
größte Kante-zu-Kante-Distanz innerhalb der sich überlappenden Zeilen
beider Boxen (der "Äquator" der gewölbten Kontur) -- ein echt
geometrieunabhängiger Algorithmus ist mit zwei simplen Flanken-Boxen nicht
zuverlässig machbar, daher die Beschränkung auf diese zwei Fälle.

Zusätzlich zur Breitenmessung (Nutzerwunsch) eine zweite, komplett separate
Messart "Fläche": EIN Bereich wird über die gesamte Probe (mit etwas Rand)
gezogen, pro Bild wird darin per Schwellenwert segmentiert (Pixel über/
unter dem Schwellenwert gezählt) -- das ist von Haus aus geometrie-
unabhängig (funktioniert gleichermaßen für eckige und runde Proben, ganz
ohne die beiden Modi oben), braucht aber KEINE Kantenverfolgung/kein
Startbild, da jedes Bild unabhängig für sich ausgewertet wird. Fachlich ist
das eine andere (aber verwandte) Kenngröße als die Breite: bei gleich-
mäßiger (isotroper) Schrumpfung gilt näherungsweise
ΔFläche/Fläche ≈ 2 × ΔBreite/Breite (Fläche skaliert mit Länge²) -- bei
ungleichmäßiger Schrumpfung können Breite und Fläche unterschiedliche
Bilder zeigen. Beide Messarten sind gegenseitig exklusiv (wie beim
Maßstab-Werkzeug nur EINE aktive Messung/EIN Ergebnis gleichzeitig);
Umschalten verwirft ein vorhandenes Ergebnis der jeweils anderen Messart."""
from __future__ import annotations

import numpy as np
import pyqtgraph as pg
from qtpy import QtCore, QtWidgets

from ..roi import AdjustableROI


def _detect_edge_profile(
    frame: np.ndarray, row0: int, row1: int, col0: int, col1: int,
    threshold: float, warmer: bool, from_left: bool,
) -> np.ndarray:
    """Wie _detect_edge_in_window, liefert aber die Kantenspalte JE ZEILE
    zurueck (NaN, wenn in der betreffenden Zeile kein Uebergang gefunden
    wurde) statt sie ueber die ganze Box zu einem einzelnen Median-Wert
    zusammenzufassen. Grundlage fuer die geometrieabhaengige Breiten-
    berechnung bei runden/zylindrischen Proben (siehe _paired_row_widths):
    deren Kontur woelbt sich zur Bildmitte hin, weshalb die Kantenspalte
    dort -- anders als bei geraden/quaderfoermigen Kanten -- von Zeile zu
    Zeile echt unterschiedlich sein kann."""
    region = frame[row0:row1, col0:col1]
    profile = np.full(region.shape[0], np.nan)
    if region.size == 0:
        return profile
    hits = region > threshold if warmer else region < threshold
    for r, row_hits in enumerate(hits):
        idx = np.flatnonzero(row_hits)
        if idx.size:
            profile[r] = col0 + (idx[0] if from_left else idx[-1])
    return profile


def _detect_edge_in_window(
    frame: np.ndarray, row0: int, row1: int, col0: int, col1: int,
    threshold: float, warmer: bool, from_left: bool,
) -> int | None:
    """Sucht innerhalb der Box (row0:row1, col0:col1) pro Zeile die erste
    Spalte, an der die Probe beginnt (von "aussen" nach "innen" gesehen --
    from_left=True durchsucht die Zeile von links nach rechts, False von
    rechts nach links) -- "Probe" bedeutet je nach warmer entweder
    Wert > threshold oder Wert < threshold. Gibt den GERUNDETEN MEDIAN der
    so je Zeile gefundenen Spalten zurueck (robuster gegen einzelne
    Rausch-Zeilen als z.B. der Mittelwert), oder None, wenn in KEINER Zeile
    ein Uebergang gefunden wurde."""
    profile = _detect_edge_profile(frame, row0, row1, col0, col1, threshold, warmer, from_left)
    valid = profile[~np.isnan(profile)]
    if valid.size == 0:
        return None
    return int(round(float(np.median(valid))))


def _paired_row_widths(
    profile_l: np.ndarray, row0_l: int, profile_r: np.ndarray, row0_r: int,
) -> list[float]:
    """Bildet aus zwei Zeilen-Kantenprofilen (siehe _detect_edge_profile,
    row0_l/row0_r verschieben die jeweils zeilen-relativen Profile auf
    absolute Bildzeilen) die Breite (rechte minus linke Kante) fuer JEDE
    Zeile, in der sich beide Such-Boxen ueberlappen UND in der jeweiligen
    Zeile beidseitig eine Kante gefunden wurde. Grundlage der "rund/
    zylindrisch"-Geometrie: dort wird spaeter das Maximum dieser Breiten
    (= die Stelle mit dem groessten Durchmesser, der "Aequator") als
    Probenbreite verwendet, statt -- wie bei geraden Kanten -- ueber die
    Zeilen zu mitteln, was die Breite einer gewoelbten Kontur systematisch
    unterschaetzen wuerde."""
    row0 = max(row0_l, row0_r)
    row1 = min(row0_l + len(profile_l), row0_r + len(profile_r))
    widths = []
    for r in range(row0, row1):
        left = profile_l[r - row0_l]
        right = profile_r[r - row0_r]
        if not (np.isnan(left) or np.isnan(right)):
            widths.append(float(right - left))
    return widths


def _track_edge_and_boxes_across_frames(
    frames: np.ndarray, order: list[int], row0: int, row1: int, box_width: int,
    initial_col0: int, threshold: float, warmer: bool, from_left: bool, n_cols: int,
) -> tuple[dict[int, int], dict[int, tuple[int, int]]]:
    """Verfolgt EINE Flanke ueber order (eine Frame-Index-Sequenz, z.B.
    [ref, ref+1, ref+2, ...] oder [ref, ref-1, ref-2, ...]) -- die Such-Box
    behaelt ihre Breite (box_width), wird aber nach jedem Frame um die
    zuletzt gefundene Kante neu zentriert, damit sie einer wandernden/
    schrumpfenden Probe folgt. Wird in einem Frame keine Kante gefunden
    (z.B. Rauschen/Kontrast zu gering), uebernimmt dieser Frame das
    Ergebnis des VORHERIGEN (in dieser Reihenfolge bereits verarbeiteten)
    Frames, statt abzustuerzen oder NaN zu erzeugen -- ganz ohne
    Vorgaenger (allererster Frame in order) faellt auf die Box-Mitte
    zurueck. Liefert neben der Kante je Frame zusaetzlich die je Frame
    TATSAECHLICH verwendete Box-Spaltengrenze (col0, col1) zurueck -- wird
    fuer die geometrieabhaengige Breitenberechnung bei runden/zylindrischen
    Proben benoetigt (siehe _paired_row_widths), da dort das Zeilen-Profil
    innerhalb der jeweils mitwandernden Box neu ausgewertet werden muss."""
    edges: dict[int, int] = {}
    boxes: dict[int, tuple[int, int]] = {}
    col0 = initial_col0
    for idx in order:
        col1 = min(n_cols, col0 + box_width)
        col0 = max(0, col1 - box_width)
        edge = _detect_edge_in_window(frames[idx], row0, row1, col0, col1, threshold, warmer, from_left)
        if edge is None:
            edge = next(reversed(edges.values())) if edges else (col0 + col1) // 2
        edges[idx] = edge
        boxes[idx] = (col0, col1)
        half = box_width // 2
        col0 = max(0, min(n_cols - box_width, edge - half))
    return edges, boxes


def _track_edge_across_frames(
    frames: np.ndarray, order: list[int], row0: int, row1: int, box_width: int,
    initial_col0: int, threshold: float, warmer: bool, from_left: bool, n_cols: int,
) -> dict[int, int]:
    """Wie _track_edge_and_boxes_across_frames, liefert aber nur die Kante
    je Frame zurueck (ohne die Box-Grenzen) -- fuer alle Aufrufer, die die
    einzelnen Box-Positionen nicht brauchen."""
    edges, _boxes = _track_edge_and_boxes_across_frames(
        frames, order, row0, row1, box_width, initial_col0, threshold, warmer, from_left, n_cols,
    )
    return edges


def _fill_tracking_gaps(d: dict, n: int) -> dict:
    """Ergaenzt ein von _track_edge_and_boxes_across_frames geliefertes
    edges-/boxes-dict um die von der Verfolgung ausgelassenen (von der
    Rohdaten-Bereinigung ausgeschlossenen) Bild-Indizes -- deren eigener Wert
    wird ohnehin nirgends angezeigt/exportiert (siehe _update_shrinkage_curve/
    export_csv.py, die ausgeschlossene Bilder selbst herausfiltern), er darf
    nur nicht fehlen (sonst KeyError in _edge_frame_width). Fehlende Indizes
    werden mit dem Wert des jeweils zuletzt verfolgten Bildes aufgefuellt
    (eigene Modul-Funktion statt in _compute_shrinkage_width verschachtelt,
    damit sie wie die uebrigen Bausteine dieser Datei unabhaengig lesbar/
    testbar bleibt, siehe Modul-Docstring)."""
    if len(d) == n:
        return d
    filled = dict(d)
    for i in range(n):
        if i in filled:
            continue
        if i - 1 in filled:
            filled[i] = filled[i - 1]
        else:
            j = i + 1
            while j not in d:
                j += 1
            filled[i] = d[j]
    return filled


def _edge_frame_width(
    i: int, left_edges: dict, right_edges: dict, geometry_round: bool,
    left_boxes: dict, right_boxes: dict, frames: np.ndarray,
    row0_l: int, row1_l: int, row0_r: int, row1_r: int,
    threshold: float, warmer: bool,
) -> float:
    """Breite von Bild i aus den bereits verfolgten Kanten/Boxen -- eigene
    Modul-Funktion statt in _compute_shrinkage_width verschachtelt (siehe
    _fill_tracking_gaps oben)."""
    median_width = float(right_edges[i] - left_edges[i])
    if not geometry_round:
        return median_width
    # Runde/zylindrische Probe: die tatsaechlich fuer diesen Frame
    # verwendeten (mitgewanderten) Box-Grenzen erneut auswerten, aber diesmal
    # zeilenweise (statt als Median) und die groesste Zeilen-Breite
    # ("Äquator") nehmen -- siehe _paired_row_widths.
    col0_li, col1_li = left_boxes[i]
    col0_ri, col1_ri = right_boxes[i]
    profile_l = _detect_edge_profile(frames[i], row0_l, row1_l, col0_li, col1_li, threshold, warmer, True)
    profile_r = _detect_edge_profile(frames[i], row0_r, row1_r, col0_ri, col1_ri, threshold, warmer, False)
    widths = _paired_row_widths(profile_l, row0_l, profile_r, row0_r)
    # Ohne Zeilen-Ueberlappung (z.B. Boxen auf sehr unterschiedlicher Hoehe
    # platziert) auf die Median-Breite zurueckfallen, statt abzustuerzen oder
    # eine Luecke in der Kurve zu erzeugen.
    return max(widths) if widths else median_width


def _segment_area_px(
    frame: np.ndarray, row0: int, row1: int, col0: int, col1: int, threshold: float, warmer: bool,
) -> int:
    """Zaehlt innerhalb der Box (row0:row1, col0:col1) die Pixel, die zur
    Probe gehoeren (je nach warmer entweder Wert > threshold oder
    Wert < threshold) -- Grundlage der geometrieunabhaengigen Flaechen-
    messung (Gegenstueck zur Breitenmessung oben): anders als dort wird die
    Box NICHT nachgefuehrt/neu zentriert, sie muss die Probe ueber die
    GESAMTE Aufnahme hinweg (mit etwas Rand) umschliessen -- da die Probe
    nur schrumpft (nicht waechst) und Schwindungen laut Nutzer ohnehin klein
    sind, bleibt sie dabei innerhalb der einmal gesetzten Box."""
    region = frame[row0:row1, col0:col1]
    if region.size == 0:
        return 0
    hits = region > threshold if warmer else region < threshold
    return int(np.count_nonzero(hits))


class _ShrinkageMixin:
    _SHRINKAGE_MODE_INFO = {
        "width": (
            "Breite: zwei Bereiche (gelb = linke Flanke, blau = rechte Flanke) direkt im "
            "Thermobild an die jeweilige Kante ziehen -- der gestrichelte Rahmen ist nur zur "
            "groben Orientierung (Gesamtbreite) und fließt nicht in die Berechnung ein."
        ),
        "area": (
            "Fläche: EINEN (grünen) Bereich über die gesamte Probe -- mit etwas Rand -- ziehen. "
            "Pro Bild wird darin die Pixelzahl über/unter dem Schwellenwert gezählt; die Box "
            "wird NICHT nachgeführt, muss die Probe also über die ganze Aufnahme hinweg "
            "umschließen. Geometrieunabhängig (funktioniert für eckige wie runde Proben "
            "gleichermaßen), braucht aber kein Startbild."
        ),
    }

    def _build_shrinkage_panel(self, parent_layout: QtWidgets.QBoxLayout) -> None:
        # Drei Boxen im Bild (siehe roi.py:AdjustableROI) -- Standardgroesse/
        # -position wird erst bekannt, wenn eine Aufnahme geladen ist (siehe
        # _reset_shrinkage_boxes_for_recording), hier nur Platzhalter-Geometrie
        # (view_box existiert zu diesem Zeitpunkt in __init__ bereits).
        self.roi_shrink_left = AdjustableROI((10, 10), 20, pen=pg.mkPen("#facc15", width=2))
        self.roi_shrink_right = AdjustableROI((70, 10), 20, pen=pg.mkPen("#38bdf8", width=2))
        # "Probenbreite": rein visueller Referenzrahmen (siehe Modul-Docstring)
        # -- gestrichelt UND nicht ueber den anderen beiden Boxen liegend
        # (niedrigere Z-Ebene), damit er das Ausrichten der Flanken-Boxen
        # nicht behindert.
        self.roi_shrink_width = AdjustableROI(
            (10, 10), [80, 20], pen=pg.mkPen("#e5e7eb", width=1.5, style=QtCore.Qt.PenStyle.DashLine)
        )
        self.roi_shrink_width.setZValue(-1)
        # Vierte Box NUR fuer die "Flaeche"-Messart (siehe Modul-Docstring)
        # -- deckt die ganze Probe (mit Rand) ab, wird NICHT nachgefuehrt.
        self.roi_shrink_area = AdjustableROI((10, 10), [80, 60], pen=pg.mkPen("#34d399", width=2))
        for roi in (self.roi_shrink_left, self.roi_shrink_right, self.roi_shrink_width, self.roi_shrink_area):
            roi.setVisible(False)
            self.view_box.addItem(roi)

        self._shrinkage_result: dict | None = None
        self.shrinkage_curve = self.timeseries_plot.plot(
            pen=pg.mkPen("#f472b6", width=2), name="Schwindung (Breite)",
        )
        self.shrinkage_curve.setVisible(False)

        box = QtWidgets.QGroupBox("Schwindungsmessung (experimentell)")
        # Als self.-Attribut gehalten, damit layer_tabs_ops.py die
        # Sichtbarkeit dieses gesamten Panel-Abschnitts je nach aktivem
        # Ebenen-Tab steuern kann (siehe _apply_layer_tab_visibility).
        self._shrinkage_groupbox = box
        layout = QtWidgets.QVBoxLayout(box)

        info = QtWidgets.QLabel(
            "Automatische Schwindungsmessung über die Zeit -- wahlweise als Breite (zwei "
            "verfolgte Flanken) oder als Fläche (ein Bereich, geometrieunabhängig). "
            "Experimentell: sehr kleine Schwindungen (oft < 1%) sind je nach Kamera-Auflösung "
            "u.U. nicht zuverlässig auflösbar."
        )
        info.setWordWrap(True)
        info.setStyleSheet("color:#6b7280;")
        layout.addWidget(info)

        self.chk_shrinkage_enabled = QtWidgets.QCheckBox("Aktivieren (Bereiche im Bild einblenden)")
        self.chk_shrinkage_enabled.toggled.connect(self._on_shrinkage_enabled_toggled)
        layout.addWidget(self.chk_shrinkage_enabled)

        # Messart (Nutzerwunsch, zusaetzlich zur Breitenmessung): "Breite"
        # (Standard, bisheriges Verhalten) oder "Fläche" (siehe Modul-
        # Docstring) -- gegenseitig exklusiv, EIN aktives Ergebnis
        # gleichzeitig (wie beim Maßstab-Werkzeug).
        mode_row = QtWidgets.QHBoxLayout()
        self.radio_shrinkage_mode_width = QtWidgets.QRadioButton("Breite (zwei Flanken-Boxen verfolgen)")
        self.radio_shrinkage_mode_area = QtWidgets.QRadioButton("Fläche (ein Bereich, geometrieunabhängig)")
        self.radio_shrinkage_mode_width.setChecked(True)
        mode_group = QtWidgets.QButtonGroup(box)
        mode_group.addButton(self.radio_shrinkage_mode_width)
        mode_group.addButton(self.radio_shrinkage_mode_area)
        self.radio_shrinkage_mode_width.toggled.connect(self._on_shrinkage_mode_changed)
        self.radio_shrinkage_mode_area.toggled.connect(self._on_shrinkage_mode_changed)
        mode_row.addWidget(self.radio_shrinkage_mode_width)
        mode_row.addWidget(self.radio_shrinkage_mode_area)
        layout.addLayout(mode_row)

        self.lbl_shrinkage_mode_info = QtWidgets.QLabel(self._SHRINKAGE_MODE_INFO["width"])
        self.lbl_shrinkage_mode_info.setWordWrap(True)
        self.lbl_shrinkage_mode_info.setStyleSheet("color:#6b7280;")
        layout.addWidget(self.lbl_shrinkage_mode_info)

        form = QtWidgets.QFormLayout()
        self.spin_shrinkage_threshold = QtWidgets.QDoubleSpinBox()
        self.spin_shrinkage_threshold.setRange(-100.0, 2000.0)
        self.spin_shrinkage_threshold.setDecimals(1)
        self.spin_shrinkage_threshold.setSuffix(" °C")
        self.spin_shrinkage_threshold.setValue(30.0)
        form.addRow("Schwellenwert (Kante):", self.spin_shrinkage_threshold)
        layout.addLayout(form)

        direction_row = QtWidgets.QHBoxLayout()
        self.radio_shrinkage_warmer = QtWidgets.QRadioButton("Probe wärmer als Hintergrund")
        self.radio_shrinkage_colder = QtWidgets.QRadioButton("Probe kälter als Hintergrund")
        self.radio_shrinkage_warmer.setChecked(True)
        direction_group = QtWidgets.QButtonGroup(box)
        direction_group.addButton(self.radio_shrinkage_warmer)
        direction_group.addButton(self.radio_shrinkage_colder)
        direction_row.addWidget(self.radio_shrinkage_warmer)
        direction_row.addWidget(self.radio_shrinkage_colder)
        layout.addLayout(direction_row)

        # Probengeometrie (Nutzerfrage: ist die Messung geometrieabhaengig?):
        # bei geraden/parallelen Kanten (Quader) liefert der Median je Box
        # bereits die richtige Breite unabhaengig von der genauen Boxhoehe.
        # Bei einer runden/zylindrischen Probe woelbt sich die Kontur zur
        # Bildmitte -- dort muss stattdessen die breiteste Stelle innerhalb
        # der Box (der "Äquator") gesucht werden, sonst wird die Breite
        # systematisch unterschaetzt. Echt geometrieunabhaengig (beliebige
        # Kontur) ist mit zwei simplen Flanken-Boxen nicht zuverlaessig
        # machbar -- daher zwei explizite, waehlbare Modi statt eines
        # "universellen" Algorithmus (Nutzer-Rueckfrage: "falls nicht
        # zuverlaessig geht, auf quadratisch/kreisförmig beschränken").
        geometry_row = QtWidgets.QHBoxLayout()
        self.radio_shrinkage_rect = QtWidgets.QRadioButton("Probe quaderförmig / rechteckig (gerade Kanten)")
        self.radio_shrinkage_round = QtWidgets.QRadioButton("Probe rund / zylindrisch (gewölbte Kontur)")
        self.radio_shrinkage_rect.setChecked(True)
        self.radio_shrinkage_rect.setToolTip(
            "Breite je Bild = rechte Median-Kante minus linke Median-Kante über die jeweilige "
            "Box -- passend für Proben mit geraden, zueinander parallelen Kanten."
        )
        self.radio_shrinkage_round.setToolTip(
            "Breite je Bild = größte Kanten-zu-Kanten-Distanz innerhalb der sich überlappenden "
            "Zeilen beider Boxen (\"Äquator\" der Kontur) -- passend für runde/zylindrische Proben, "
            "bei denen die Kontur sich zur Bildmitte hin wölbt. Ohne Zeilen-Überlappung zwischen "
            "linker und rechter Box fällt der Modus je Bild auf die Median-Breite zurück."
        )
        geometry_group = QtWidgets.QButtonGroup(box)
        geometry_group.addButton(self.radio_shrinkage_rect)
        geometry_group.addButton(self.radio_shrinkage_round)
        geometry_row.addWidget(self.radio_shrinkage_rect)
        geometry_row.addWidget(self.radio_shrinkage_round)
        # Nur fuer die Breitenmessung relevant (siehe Modul-Docstring) -- in
        # einem eigenen Widget, damit sich die ganze Zeile bei "Fläche" per
        # setVisible() ausblenden laesst (ein QLayout selbst hat keine
        # eigene Sichtbarkeit).
        self._shrinkage_geometry_widget = QtWidgets.QWidget()
        self._shrinkage_geometry_widget.setLayout(geometry_row)
        layout.addWidget(self._shrinkage_geometry_widget)

        ref_row = QtWidgets.QHBoxLayout()
        self.btn_shrinkage_set_ref = QtWidgets.QPushButton("Startbild für Konturerkennung festlegen")
        self.btn_shrinkage_set_ref.setToolTip(
            "Merkt sich das AKTUELL angezeigte Bild als Ausgangspunkt der Kantenverfolgung -- "
            "muss nicht das erste Bild der Aufnahme sein."
        )
        self.btn_shrinkage_set_ref.clicked.connect(self._on_shrinkage_set_ref_clicked)
        ref_row.addWidget(self.btn_shrinkage_set_ref)
        self.lbl_shrinkage_ref = QtWidgets.QLabel("Startbild: nicht gesetzt")
        ref_row.addWidget(self.lbl_shrinkage_ref)
        ref_row.addStretch(1)
        # Ebenfalls nur fuer die Breitenmessung -- die Flaechenmessung
        # braucht kein Startbild, da jedes Bild unabhaengig ausgewertet wird
        # (siehe _segment_area_px).
        self._shrinkage_ref_widget = QtWidgets.QWidget()
        self._shrinkage_ref_widget.setLayout(ref_row)
        layout.addWidget(self._shrinkage_ref_widget)

        self.btn_shrinkage_compute = QtWidgets.QPushButton("Berechnen")
        self.btn_shrinkage_compute.clicked.connect(self._on_shrinkage_compute_clicked)
        layout.addWidget(self.btn_shrinkage_compute)

        self.lbl_shrinkage_result = QtWidgets.QLabel("Noch nicht berechnet.")
        self.lbl_shrinkage_result.setWordWrap(True)
        layout.addWidget(self.lbl_shrinkage_result)

        self._set_shrinkage_controls_enabled(False)
        parent_layout.addWidget(box)

    def _set_shrinkage_controls_enabled(self, enabled: bool) -> None:
        for widget in (
            self.spin_shrinkage_threshold, self.radio_shrinkage_warmer, self.radio_shrinkage_colder,
            self.radio_shrinkage_mode_width, self.radio_shrinkage_mode_area,
            self.radio_shrinkage_rect, self.radio_shrinkage_round,
            self.btn_shrinkage_set_ref, self.btn_shrinkage_compute,
        ):
            widget.setEnabled(enabled)

    def _apply_shrinkage_roi_visibility(self) -> None:
        """Zentrale Stelle, welche der vier Boxen gerade sichtbar sein
        sollen -- haengt sowohl von "Aktivieren" als auch von der Messart
        ab (Breite: links/rechts/Referenzrahmen, Fläche: die eine gruene
        Box), siehe _on_shrinkage_enabled_toggled/_on_shrinkage_mode_changed/
        _reset_shrinkage_state_for_recording."""
        visible = (
            self._shrinkage_enabled and self.recording is not None
            and self._is_layer_tab_active("shrinkage")
        )
        is_width = self._shrinkage_mode == "width"
        for roi in (self.roi_shrink_left, self.roi_shrink_right, self.roi_shrink_width):
            roi.setVisible(visible and is_width)
        self.roi_shrink_area.setVisible(visible and not is_width)

    def _on_shrinkage_enabled_toggled(self, checked: bool) -> None:
        self._shrinkage_enabled = checked
        if checked:
            # Ebenen-Tabs (Nutzerwunsch): Aktivieren springt automatisch auf
            # die passende Ebene, ruft dabei bereits
            # _apply_shrinkage_roi_visibility() mit auf (siehe
            # layer_tabs_ops.py) -- Deaktivieren schaltet NICHT automatisch
            # weg, da der Nutzer das Ergebnis/die Kontrollen evtl. noch
            # sehen will.
            self._set_active_layer_tab("shrinkage")
        else:
            self._apply_shrinkage_roi_visibility()
        self._set_shrinkage_controls_enabled(checked)
        if not checked:
            self.shrinkage_curve.setVisible(False)
        elif self._shrinkage_result is not None:
            self.shrinkage_curve.setVisible(True)

    def _on_shrinkage_mode_changed(self, *_args) -> None:
        mode = "area" if self.radio_shrinkage_mode_area.isChecked() else "width"
        if mode == self._shrinkage_mode:
            return
        self._shrinkage_mode = mode
        # Ein vorhandenes Ergebnis gehoert zur ANDEREN Messart (Breite vs.
        # Fläche sind unterschiedliche physikalische Groessen) -- verwerfen
        # statt es unter neuer Bedeutung stehen zu lassen.
        self._shrinkage_result = None
        self.lbl_shrinkage_result.setText("Noch nicht berechnet.")
        self.shrinkage_curve.clear()
        self.shrinkage_curve.setVisible(False)
        is_width = mode == "width"
        self._shrinkage_geometry_widget.setVisible(is_width)
        self._shrinkage_ref_widget.setVisible(is_width)
        self.lbl_shrinkage_mode_info.setText(self._SHRINKAGE_MODE_INFO[mode])
        self._apply_shrinkage_roi_visibility()

    def _reset_shrinkage_state_for_recording(self) -> None:
        """Positioniert die vier Boxen neu passend zur (neu geladenen)
        Aufnahme und verwirft ein evtl. vorhandenes Ergebnis -- Boxen/
        Ergebnis der VORHERIGEN Aufnahme sind fuer eine andere Bildgroesse/
        Frame-Anzahl bedeutungslos (gleiche Invariante wie bei ROIs/
        Messungen/Bereinigung, siehe frame_nav.py). Die gewaehlte Messart
        selbst bleibt (wie Schwellenwert/Richtung) ueber einen Reload
        hinweg erhalten."""
        self._shrinkage_ref_frame: int | None = None
        self._shrinkage_result = None
        self.lbl_shrinkage_ref.setText("Startbild: nicht gesetzt")
        self.lbl_shrinkage_result.setText("Noch nicht berechnet.")
        self.shrinkage_curve.clear()
        self.shrinkage_curve.setVisible(False)
        if self.recording is None:
            self._apply_shrinkage_roi_visibility()
            return
        rows, cols = self.recording.shape
        band_h = max(2, round(rows * 0.3))
        row0 = max(0, round(rows * 0.35))
        flank_w = max(2, round(cols * 0.15))
        self.roi_shrink_left.setPos((cols * 0.1, row0), update=False)
        self.roi_shrink_left.setSize((flank_w, band_h))
        self.roi_shrink_right.setPos((cols * 0.75, row0), update=False)
        self.roi_shrink_right.setSize((flank_w, band_h))
        self.roi_shrink_width.setPos((cols * 0.05, row0), update=False)
        self.roi_shrink_width.setSize((cols * 0.9, band_h))
        # Flaechen-Box: deckt (mit Rand) fast das ganze Bild ab, da sie
        # NICHT nachgefuehrt wird und die Probe ueber die gesamte Aufnahme
        # hinweg umschliessen muss (siehe _segment_area_px).
        self.roi_shrink_area.setPos((cols * 0.05, rows * 0.1), update=False)
        self.roi_shrink_area.setSize((cols * 0.9, rows * 0.8))
        self._apply_shrinkage_roi_visibility()

    def _on_shrinkage_set_ref_clicked(self) -> None:
        if self.recording is None:
            return
        self._shrinkage_ref_frame = self.current_index
        self.lbl_shrinkage_ref.setText(f"Startbild: Bild {self.current_index + 1}")

    def _on_shrinkage_compute_clicked(self) -> None:
        if self.recording is None:
            return
        if self._shrinkage_mode == "area":
            self._compute_shrinkage_area()
        else:
            self._compute_shrinkage_width()

    def _compute_shrinkage_width(self) -> None:
        if self._shrinkage_ref_frame is None:
            QtWidgets.QMessageBox.information(
                self, "Kein Startbild",
                "Bitte zuerst über \"Startbild für Konturerkennung festlegen\" ein Startbild wählen.",
            )
            return
        rows, cols = self.recording.shape
        row0_l, row1_l, col0_l, col1_l = self.roi_shrink_left.bounds_px((rows, cols))
        row0_r, row1_r, col0_r, col1_r = self.roi_shrink_right.bounds_px((rows, cols))
        threshold = self.spin_shrinkage_threshold.value()
        warmer = self.radio_shrinkage_warmer.isChecked()
        geometry_round = self.radio_shrinkage_round.isChecked()
        n = self.recording.n_frames
        ref = min(self._shrinkage_ref_frame, n - 1)
        excluded = self._excluded_frame_indices
        # Von der Rohdaten-Bereinigung ausgeblendete Bilder (siehe
        # data_cleaning_ops.py) sollen die Kantenverfolgung NICHT
        # beeinflussen -- sie enthalten per Definition Stoerungen/
        # Ausreisser, deren (moeglicherweise falsch erkannte) Kante sonst
        # die Box-Position ALLER nachfolgenden, eigentlich guten Bilder
        # verfaelschen wuerde (die Box wird ja nach jedem Frame neu um die
        # zuletzt gefundene Kante zentriert). ref selbst bleibt auch bei
        # eigenem Ausschluss der noetige Ankerpunkt, ohne den die
        # Verfolgung gar nicht erst beginnen koennte.
        forward = [ref] + [i for i in range(ref + 1, n) if i not in excluded]
        backward = [ref] + [i for i in range(ref - 1, -1, -1) if i not in excluded]
        frames = self.recording.frames

        left_edges_back, left_boxes_back = _track_edge_and_boxes_across_frames(
            frames, backward, row0_l, row1_l, col1_l - col0_l, col0_l, threshold, warmer, True, cols
        )
        left_edges_fwd, left_boxes_fwd = _track_edge_and_boxes_across_frames(
            frames, forward, row0_l, row1_l, col1_l - col0_l, col0_l, threshold, warmer, True, cols
        )
        left_edges = _fill_tracking_gaps({**left_edges_back, **left_edges_fwd}, n)
        left_boxes = _fill_tracking_gaps({**left_boxes_back, **left_boxes_fwd}, n)

        right_edges_back, right_boxes_back = _track_edge_and_boxes_across_frames(
            frames, backward, row0_r, row1_r, col1_r - col0_r, col0_r, threshold, warmer, False, cols
        )
        right_edges_fwd, right_boxes_fwd = _track_edge_and_boxes_across_frames(
            frames, forward, row0_r, row1_r, col1_r - col0_r, col0_r, threshold, warmer, False, cols
        )
        right_edges = _fill_tracking_gaps({**right_edges_back, **right_edges_fwd}, n)
        right_boxes = _fill_tracking_gaps({**right_boxes_back, **right_boxes_fwd}, n)

        widths_px = np.array(
            [
                _edge_frame_width(
                    i, left_edges, right_edges, geometry_round, left_boxes, right_boxes,
                    frames, row0_l, row1_l, row0_r, row1_r, threshold, warmer,
                )
                for i in range(n)
            ],
            dtype=float,
        )
        self._shrinkage_result = {
            "mode": "width",
            "left_edges": left_edges, "right_edges": right_edges, "widths_px": widths_px,
            "geometry": "round" if geometry_round else "rect",
        }
        self._update_shrinkage_curve()

        first, last = widths_px[0], widths_px[-1]
        unit = "mm" if self._px_to_mm is not None else "px"
        first_disp = first * self._px_to_mm if self._px_to_mm is not None else first
        last_disp = last * self._px_to_mm if self._px_to_mm is not None else last
        shrink_pct = (first - last) / first * 100.0 if first else 0.0
        self.lbl_shrinkage_result.setText(
            f"Breite Bild 1: {first_disp:.2f} {unit}  |  Breite letztes Bild: {last_disp:.2f} {unit}  |  "
            f"Schwindung: {shrink_pct:.2f} % (experimentell, siehe Hinweis oben)"
        )

    def _compute_shrinkage_area(self) -> None:
        rows, cols = self.recording.shape
        row0, row1, col0, col1 = self.roi_shrink_area.bounds_px((rows, cols))
        threshold = self.spin_shrinkage_threshold.value()
        warmer = self.radio_shrinkage_warmer.isChecked()
        n = self.recording.n_frames
        frames = self.recording.frames

        areas_px = np.array(
            [_segment_area_px(frames[i], row0, row1, col0, col1, threshold, warmer) for i in range(n)],
            dtype=float,
        )
        self._shrinkage_result = {"mode": "area", "areas_px": areas_px}
        self._update_shrinkage_curve()

        first, last = areas_px[0], areas_px[-1]
        scale = self._px_to_mm ** 2 if self._px_to_mm is not None else 1.0
        unit = "mm²" if self._px_to_mm is not None else "px²"
        first_disp, last_disp = first * scale, last * scale
        shrink_pct = (first - last) / first * 100.0 if first else 0.0
        self.lbl_shrinkage_result.setText(
            f"Fläche Bild 1: {first_disp:.2f} {unit}  |  Fläche letztes Bild: {last_disp:.2f} {unit}  |  "
            f"Schwindung: {shrink_pct:.2f} % (Fläche -- bei gleichmäßiger Schrumpfung ungefähr "
            f"2× die Breiten-Schwindung; experimentell, siehe Hinweis oben)"
        )

    def _update_shrinkage_curve(self) -> None:
        if self._shrinkage_result is None or self.recording is None:
            self.shrinkage_curve.clear()
            self.shrinkage_curve.setVisible(False)
            return
        if self._shrinkage_result["mode"] == "area":
            raw = self._shrinkage_result["areas_px"]
            scale = self._px_to_mm ** 2 if self._px_to_mm is not None else 1.0
        else:
            raw = self._shrinkage_result["widths_px"]
            scale = self._px_to_mm if self._px_to_mm is not None else 1.0
        values = raw * scale
        unix = self.recording.unix_seconds()
        if self._excluded_frame_indices:
            keep_mask = np.ones(len(unix), dtype=bool)
            keep_mask[list(self._excluded_frame_indices)] = False
            self.shrinkage_curve.setData(unix[keep_mask], values[keep_mask])
        else:
            self.shrinkage_curve.setData(unix, values)
        self.shrinkage_curve.setVisible(self._shrinkage_enabled)
