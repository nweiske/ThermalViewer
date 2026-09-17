"""Schwindungsmessung (Punkt 9, Nutzerwunsch): automatische Erkennung der
Probenkontur über die Zeit anhand einer zeilenweisen Lauflängen-Kette
("Row-Span-Chain") -- der Nutzer zieht GENAU EINE Box grob über die ganze
Probe (mit etwas Rand), das Programm findet darin selbst die tatsächliche,
zusammenhängende Kontur -- GEOMETRIEUNABHÄNGIG (funktioniert für eckige wie
runde/deformierte Proben gleichermaßen), zeigt sie als rote Linie im
Thermobild an, und leitet daraus GLEICHZEITIG alle Kenngrößen ab (Fläche,
Breite bei geraden Kanten, Breite bei gewölbter Kontur). WAS davon
angezeigt/exportiert wird, wählt der Nutzer NACHTRÄGLICH über ein Dropdown
("Kenngröße") -- die frühere Unterscheidung zwischen zwei getrennten
Eingabe-"Messarten" (zwei Flanken-Boxen vs. eine Flächen-Box, mit
Startbild-Verfolgung nur bei der ersten) entfällt komplett: Nutzerfeedback
"am Ende können wir uns auf den Flächenmodus einigen... die Probe soll
standardmäßig immer grob mit einem Fenster vormarkiert werden".

Kernidee: pro Bild wird zeilenweise vorgegangen. In jeder Zeile der Box
werden per automatischem Otsu-Schwellenwert (_otsu_threshold) die
zusammenhängenden Segmente ("Runs", siehe _row_runs) ermittelt, die zur
Probe gehören könnten. Ausgehend von einer Saat-Zeile/-Spalte (Boxmitte)
wird der jeweils am stärksten mit der Nachbarzeile überlappende Run
übernommen (_run_overlapping_most) -- das IST die räumliche Konnektivität:
ein unzusammenhängender Bereich anderswo im Bild (z.B. Hintergrund, der
zufällig denselben Schwellenwert überschreitet) kann nie ausgewählt werden,
weil er mit keiner Nachbarzeile überlappt. Endet eine Richtung ohne
überlappenden Run, hat die Probe dort ihre natürliche vertikale Grenze.

Zweistufige Rauschunterdrückung (Nutzerfeedback anhand echter Aufnahmen,
u.a. eines sehr flach auslaufenden Zylinderprofils: die Kontur wirkte an
einem graduellen (nicht scharfen) Temperaturübergang deutlich ausgefranster
als an einer schärferen Kante -- das ist an solchen Stellen KEIN Fehler in
der Glättung, sondern echtes Sensorrauschen, das dort besonders stark auf
die pro-Zeile-Schwellenwertbildung durchschlägt, weil der Temperaturverlauf
über zig Pixel hinweg fast eben ist):
1. VOR der Maskenbildung wird jede Bildzeile der Box horizontal geglättet
   (_horizontal_blur_region, laufender Mittelwert nur über Spalten -- KEINE
   Vermischung zwischen Zeilen, damit die vertikale Auflösung/Kontur-Kurve
   unangetastet bleibt). Die Fensterbreite skaliert mit der Boxbreite
   (_adaptive_blur_kernel) statt fest zu sein: eine breite, mit Rand über
   die ganze Probe gezogene Box (typisch: reale Aufnahmen) bekommt ein
   proportional größeres Fenster als eine schmale (z.B. in Tests) -- sonst
   wäre entweder das reale Rauschen zu schwach gedämpft oder eine schmale
   Box würde bis zum gegenüberliegenden Rand hinein verwischt.
2. Das daraus resultierende rohe Zeilen-Spans-Ergebnis wird zusätzlich
   zeilenweise GEGLÄTTET (_smooth_spans, laufender Median) -- eine einzelne
   verrauschte Zeile wird vom Median klar überstimmt; eine echte, über
   mehrere Zeilen reichende Wölbung (z.B. der "Äquator" einer runden Probe)
   bleibt dabei unangetastet, weil sie in ihrem eigenen Fenster die
   Mehrheit stellt.

Aus GENAU dieser einen Datenstruktur (dict[Zeile -> (Spalte_links,
Spalte_rechts)], "spans") werden alle drei Kenngrößen UND die Kontur
abgeleitet (_spans_metrics/_spans_to_polygon) -- die Box wird dabei NICHT
nachgeführt (muss die Probe über die gesamte Aufnahme hinweg umschließen,
unkritisch da die Probe laut Aufgabe nur schrumpft, nicht wächst) und jedes
Bild unabhängig ausgewertet (kein Startbild nötig). Die Box selbst ist
damit bereits der Anti-Sprung-Schutz: ein unzusammenhängender Bereich
anderswo im Bild kann nie erreicht werden, selbst wenn er denselben Otsu-
Schwellenwert überschreitet -- die Saat sitzt immer in der Boxmitte.

Zusätzlich automatisch erkannt (statt manueller "wärmer/kälter"-Wahl): ob
die Probe wärmer oder kälter als der Hintergrund ist -- einmalig beim Klick
auf "Berechnen" (_detect_polarity_area) und danach für die GESAMTE Messung
festgehalten (nur der eigentliche Otsu-Schwellenwert wird JE BILD frisch
ermittelt, siehe _otsu_threshold).

Ausdrücklich EXPERIMENTELL (Nutzer-Hinweis): typische Schwindungswerte
liegen bei < 10%, oft < 1% -- ob das bei der jeweiligen Kamera-Auflösung
überhaupt sinnvoll aufgelöst werden kann, ist offen. Kein Video-/
Bildstapel-Export-Anschluss (keine Boxen-/Kontur-Overlays im exportierten
Bild/Video) in dieser Version."""
from __future__ import annotations

import numpy as np
import pyqtgraph as pg
from qtpy import QtCore, QtGui, QtWidgets

from ..roi import AdjustableROI

# Anzeigename je waehlbarer Kenngroesse (Dropdown im Panel) UND der
# zugehoerige Schluessel in self._shrinkage_result -- eine Stelle fuer
# beide, damit Panel/Export/CSV nicht auseinanderlaufen koennen.
_SHRINKAGE_METRIC_LABELS = {
    "flaeche": "Fläche",
    "breite_rechteckig": "Breite (quaderförmig, Median)",
    "breite_rund": "Breite (rund, Äquator)",
}
_SHRINKAGE_METRIC_KEYS = {
    "flaeche": "areas_px",
    "breite_rechteckig": "rect_widths_px",
    "breite_rund": "round_widths_px",
}


def _otsu_threshold(values: np.ndarray) -> float:
    """Automatischer Schwellenwert (Otsu-Verfahren) fuer eine Menge von
    Temperaturwerten -- ersetzt einen frueher manuell eingegebenen festen
    Schwellenwert (siehe Modul-Docstring). Arbeitet direkt auf den
    kontinuierlichen Werten (256 gleich breite Bins zwischen deren Minimum/
    Maximum) statt auf einem fertigen 8-bit-Histogramm -- fuer die Trennung
    von zwei Temperatur-"Clustern" (Probe/Hintergrund) reicht diese
    Aufloesung locker. Bei einer (nahezu) einfarbigen Flaeche -- z.B. wenn
    Probe und Hintergrund thermisch nahezu im Gleichgewicht sind -- ist das
    Ergebnis zwangslaeufig beliebig/instabil; das ist eine physikalische
    Grenze der Messung, kein Programmierfehler."""
    finite = values[np.isfinite(values)]
    if finite.size == 0:
        return 0.0
    lo, hi = float(finite.min()), float(finite.max())
    if hi <= lo:
        return lo
    hist, edges = np.histogram(finite, bins=256, range=(lo, hi))
    hist = hist.astype(np.float64)
    bin_centers = (edges[:-1] + edges[1:]) / 2.0
    weight1 = np.cumsum(hist)
    weight2 = hist.sum() - weight1
    cum_value = np.cumsum(hist * bin_centers)
    total_value = cum_value[-1]
    with np.errstate(invalid="ignore", divide="ignore"):
        mean1 = np.where(weight1 > 0, cum_value / weight1, 0.0)
        mean2 = np.where(weight2 > 0, (total_value - cum_value) / weight2, 0.0)
    between_class_variance = weight1 * weight2 * (mean1 - mean2) ** 2
    return float(bin_centers[int(np.argmax(between_class_variance))])


def _detect_polarity_area(
    frame: np.ndarray, row0: int, row1: int, col0: int, col1: int, margin_frac: float = 0.12,
) -> bool:
    """Ermittelt automatisch, ob die Probe waermer oder kaelter als der
    Hintergrund ist (Nutzerwunsch, statt einer manuellen Auswahl) -- der
    Rand der Box ("mit etwas Rand", siehe Modul-Docstring) sollte
    ueberwiegend Hintergrund zeigen, ihr Inneres ueberwiegend die Probe --
    Vergleich der Mittelwerte von Zentrum vs. aeusserem Rand-Ring. True =
    Probe waermer als Hintergrund."""
    region = frame[row0:row1, col0:col1]
    if region.size == 0:
        return True
    h, w = region.shape
    my = max(1, round(h * margin_frac))
    mx = max(1, round(w * margin_frac))
    if h <= 2 * my or w <= 2 * mx:
        return True
    inner = region[my:h - my, mx:w - mx]
    ring_mask = np.ones((h, w), dtype=bool)
    ring_mask[my:h - my, mx:w - mx] = False
    return float(np.mean(inner)) >= float(np.mean(region[ring_mask]))


def _adaptive_blur_kernel(width: int, frac: float = 0.09, lo: int = 3, hi: int = 21) -> int:
    """Fensterbreite für _horizontal_blur_region, proportional zur
    Boxbreite (siehe Modul-Docstring) statt fest -- ein fester Wert wäre
    entweder für eine schmale Box (u.a. in Tests: wenige Dutzend Pixel
    breit) zu groß (verwischt bis zum gegenüberliegenden Rand) oder für
    eine breite reale Box (typisch hunderte Pixel) zu klein (dämpft das
    tatsächliche Sensorrauschen kaum). Immer ungerade (symmetrisches
    Fenster) und auf [lo, hi] begrenzt."""
    k = int(round(width * frac))
    if k % 2 == 0:
        k += 1
    return max(lo, min(hi, k))


def _horizontal_blur_region(region: np.ndarray, kernel: int) -> np.ndarray:
    """Laufender Mittelwert NUR über Spalten (jede Zeile unabhängig) --
    dämpft Sensorrauschen entlang der Zeile, BEVOR daraus die Maske/der
    Schwellenwert gebildet wird (siehe Modul-Docstring), ohne zwischen
    Zeilen zu vermischen: die vertikale Auflösung (und damit eine echte,
    über mehrere Zeilen reichende Kontur-Wölbung) bleibt dadurch komplett
    unangetastet. Randspalten der Box werden gespiegelt fortgesetzt
    (np.pad mode="reflect"), damit der Rand nicht künstlich zum
    Box-Mittelwert hin verwischt wird. kernel <= 1 -> unveraendert."""
    if kernel <= 1:
        return region
    pad = kernel // 2
    padded = np.pad(region, ((0, 0), (pad, pad)), mode="reflect")
    cumsum = np.cumsum(padded, axis=1)
    cumsum = np.hstack([np.zeros((cumsum.shape[0], 1)), cumsum])
    total = cumsum[:, kernel:] - cumsum[:, :-kernel]
    return total / kernel


def _candidate_mask(region: np.ndarray, warmer: bool) -> np.ndarray:
    """Boolesche Probe-Maske ueber region -- Otsu-Schwellenwert FRISCH aus
    region selbst (siehe _otsu_threshold) plus die einmalig erkannte
    Polaritaet."""
    threshold = _otsu_threshold(region)
    return region > threshold if warmer else region < threshold


def _row_runs(bool_row: np.ndarray) -> list[tuple[int, int]]:
    """Zusammenhaengende True-Segmente ("Runs") einer 1D-Bool-Zeile, als
    (start, ende_exklusiv) -- Grundbaustein der zeilenweisen Lauflaengen-
    Kette (siehe Modul-Docstring): liefert die Kandidaten-Segmente einer
    Zeile, aus denen anschliessend das zur Nachbarzeile passende
    ausgewaehlt wird."""
    if bool_row.size == 0:
        return []
    diff = np.diff(bool_row.astype(np.int8))
    starts = list(np.flatnonzero(diff == 1) + 1)
    ends = list(np.flatnonzero(diff == -1) + 1)
    if bool_row[0]:
        starts.insert(0, 0)
    if bool_row[-1]:
        ends.append(bool_row.size)
    return list(zip(starts, ends))


def _run_containing_or_largest(runs: list[tuple[int, int]], col: int) -> tuple[int, int] | None:
    """Waehlt aus runs (siehe _row_runs) den Run, der col enthaelt, oder --
    falls keiner col direkt enthaelt -- den GROESSTEN. Nur fuer die SAAT-
    ZEILE (Boxmitte, siehe Modul-Docstring) -- alle weiteren Zeilen
    verwenden _run_overlapping_most (Ueberlappung mit einer Zeilenspanne).

    Bugfix (Nutzer-Reproduktion anhand echter, kontrastarmer Aufnahmen): die
    Box wird nur GROB ueber die Probe gezogen, ihr Mittelpunkt (col) landet
    dadurch in der Praxis oft nicht EXAKT auf der Probe, sondern knapp
    daneben im Hintergrund. Der fruehere Fallback ("naechstgelegener Run")
    griff dann leicht zu einem winzigen, durch Rauschen entstandenen
    Hintergrund-Fleck statt der tatsaechlichen, deutlich breiteren Probe --
    einmal auf dem falschen Run gestartet, lief die gesamte Kontur-
    Verfolgung (_run_overlapping_most kennt nur Ueberlappung, keine
    Groesse) fortan querfeldein durchs Bild statt der echten Kontur zu
    folgen. Der GROESSTE Run in der Saat-Zeile ist die weit zuverlaessigere
    Annahme: echte Rausch-Flecken sind nach der Vorglaettung (siehe
    _horizontal_blur_region) typischerweise winzig gegenueber der Probe,
    die die Box laut Anleitung "mit etwas Rand" ohnehin grossflaechig
    ausfuellen soll."""
    if not runs:
        return None
    for c0, c1 in runs:
        if c0 <= col < c1:
            return c0, c1
    return max(runs, key=lambda run: run[1] - run[0])


def _run_overlapping_most(runs: list[tuple[int, int]], anchor: tuple[int, int]) -> tuple[int, int] | None:
    """Waehlt aus runs den Run mit der laengsten Ueberlappung zu anchor
    (Zeilenspanne der Nachbarzeile) -- das IST die raeumliche Konnektivitaet
    (siehe Modul-Docstring): ein unzusammenhaengender Run, der anchor nicht
    ueberlappt, wird nie ausgewaehlt. None, wenn KEIN Run ueberlappt -- die
    aufrufende Stelle bricht die Verfolgung in dieser Richtung dann ab."""
    a0, a1 = anchor
    best = None
    best_overlap = 0
    for c0, c1 in runs:
        overlap = min(c1, a1) - max(c0, a0)
        if overlap > best_overlap:
            best_overlap = overlap
            best = (c0, c1)
    return best


def _sweep_spans_from_seed(
    mask: np.ndarray, seed_row: int, seed_col: int, row_offset: int, col_offset: int,
) -> dict[int, tuple[int, int]]:
    """Baut die Zeilen-Spans (siehe Modul-Docstring) einer einzelnen Probe
    ausgehend von EINER Saat-Zeile/-Spalte (fensterlokale Indizes, i.d.R.
    die Boxmitte) -- zuerst wird der Run in der Saat-Zeile bestimmt
    (_run_containing_or_largest), dann zeilenweise nach oben UND unten
    fortgesetzt (_run_overlapping_most), bis eine Richtung keinen
    ueberlappenden Run mehr findet -- das ergibt automatisch auch die
    vertikale Ausdehnung der Probe. Ergebnis-Keys/-Werte sind ABSOLUTE
    Bildkoordinaten (row_offset/col_offset addiert)."""
    seed_runs = _row_runs(mask[seed_row])
    seed_span = _run_containing_or_largest(seed_runs, seed_col)
    if seed_span is None:
        return {}
    spans: dict[int, tuple[int, int]] = {
        row_offset + seed_row: (seed_span[0] + col_offset, seed_span[1] + col_offset)
    }
    anchor = seed_span
    for r in range(seed_row + 1, mask.shape[0]):
        found = _run_overlapping_most(_row_runs(mask[r]), anchor)
        if found is None:
            break
        anchor = found
        spans[row_offset + r] = (found[0] + col_offset, found[1] + col_offset)
    anchor = seed_span
    for r in range(seed_row - 1, -1, -1):
        found = _run_overlapping_most(_row_runs(mask[r]), anchor)
        if found is None:
            break
        anchor = found
        spans[row_offset + r] = (found[0] + col_offset, found[1] + col_offset)
    return spans


def _smooth_spans(spans: dict[int, tuple[int, int]], window: int = 5) -> dict[int, tuple[int, int]]:
    """Glaettet die Kontur (Nutzerfeedback: eine Kante wirkte "ausgefranst"/
    verrauscht) durch einen laufenden MEDIAN ueber `window` benachbarte
    Zeilen, unabhaengig fuer linke und rechte Spalte -- daempft Rauschen an
    graduellen (nicht scharfen) Temperaturuebergaengen, ohne eine echte,
    ueber mehrere Zeilen reichende Kruemmung (z.B. den "Äquator" einer
    runden Probe) zu verwischen: eine einzelne Rausch-Zeile wird vom Median
    klar ueberstimmt, eine mehrzeilige echte Woelbung stellt in ihrem
    eigenen Fenster dagegen die Mehrheit und bleibt unangetastet. Zu wenige
    Zeilen fuer ein volles Fenster -> unveraendert zurueckgeben."""
    if len(spans) < window:
        return spans
    rows = sorted(spans.keys())
    lefts = [spans[r][0] for r in rows]
    rights = [spans[r][1] for r in rows]
    half = window // 2
    n = len(rows)
    smoothed: dict[int, tuple[int, int]] = {}
    for i, r in enumerate(rows):
        lo, hi = max(0, i - half), min(n, i + half + 1)
        smoothed[r] = (
            int(round(float(np.median(lefts[lo:hi])))),
            int(round(float(np.median(rights[lo:hi])))),
        )
    return smoothed


def _spans_metrics(spans: dict[int, tuple[int, int]]) -> tuple[float, float, float]:
    """Flaeche (Pixelzahl), rect-Breite (Median der Zeilenbreiten) und
    round-Breite (deren Maximum, der "Äquator" einer gewölbten Kontur) aus
    EINER Zeilen-Span-Menge (siehe Modul-Docstring) -- alle drei werden
    IMMER gemeinsam berechnet, die Auswahl im Panel entscheidet nur, welche
    davon angezeigt/exportiert wird."""
    if not spans:
        return 0.0, 0.0, 0.0
    widths = np.array([c1 - c0 for c0, c1 in spans.values()], dtype=float)
    return float(widths.sum()), float(np.median(widths)), float(widths.max())


def _spans_to_polygon(spans: dict[int, tuple[int, int]]) -> tuple[np.ndarray, np.ndarray]:
    """Baut aus den Zeilen-Spans EINE geschlossene Polylinie (Nutzerwunsch:
    die tatsaechlich erkannte Kontur als rote Linie im Bild) -- linke Kante
    von oben nach unten, dann rechte Kante von unten nach oben, zurueck zum
    Startpunkt geschlossen. Leere spans ergeben ein leeres Polygon (keine
    Linie sichtbar)."""
    if not spans:
        return np.array([]), np.array([])
    rows = sorted(spans.keys())
    xs: list[float] = []
    ys: list[float] = []
    for r in rows:
        xs.append(float(spans[r][0]))
        ys.append(float(r))
    for r in reversed(rows):
        xs.append(float(spans[r][1]))
        ys.append(float(r))
    xs.append(xs[0])
    ys.append(ys[0])
    return np.array(xs), np.array(ys)


def _track_sample_blob(
    frames: np.ndarray, row0: int, row1: int, col0: int, col1: int, warmer: bool,
    seed_row: int, seed_col: int,
) -> dict[int, dict[int, tuple[int, int]]]:
    """Verfolgt die Probenkontur ueber ALLE Bilder -- jedes Bild wird
    UNABHAENGIG ausgewertet (siehe Modul-Docstring: die Box wird nicht
    nachgefuehrt, kein Startbild noetig), immer mit demselben festen
    Boxzentrum (seed_row/seed_col, absolute Bildkoordinaten) als Saatpunkt
    (dieselbe Annahme, die _detect_polarity_area bereits macht). Das feste,
    nie verschobene Suchfenster (row0:row1, col0:col1 -- die vom Nutzer
    "mit etwas Rand" ueber die ganze Probe gezogene Box) ist bereits der
    Anti-Sprung-Schutz: ein unzusammenhaengender Bereich anderswo im Bild
    kann nie erreicht werden, selbst wenn er denselben Otsu-Schwellenwert
    ueberschreitet. Jedes Bild wird VOR der Maskenbildung horizontal
    vorgeglaettet (_horizontal_blur_region, Fensterbreite proportional zur
    Boxbreite, siehe _adaptive_blur_kernel) UND danach ZWEIFACH zeilenweise
    nachgeglaettet (_smooth_spans) -- siehe Modul-Docstring fuer die
    Begruendung der zweistufigen Rauschunterdrueckung. Der zweite Durchlauf
    (Nutzerfeedback: selbst bei gutem Kontrast blieb am oberen Proben-Rand
    ein kleiner Rest-Zickzack) ist fuer bereits einmal geglaettete, echte
    mehrzeilige Kruemmungen NACHWEISLICH idempotent (siehe die Tests fuer
    _smooth_spans/die 3-Zeilen-Woelbungs-Testfixture: exakt gleiche Werte
    nach ein oder zwei Durchlaeufen) -- veraendert also nichts an bereits
    bekannten/getesteten Faellen, daempft aber verbleibendes
    Einzelzeilen-Rauschen auf echten Aufnahmen zusaetzlich."""
    seed_row_local = max(0, min(row1 - row0 - 1, seed_row - row0))
    seed_col_local = max(0, min(col1 - col0 - 1, seed_col - col0))
    blur_kernel = _adaptive_blur_kernel(col1 - col0)
    result: dict[int, dict[int, tuple[int, int]]] = {}
    for idx in range(len(frames)):
        region = _horizontal_blur_region(frames[idx][row0:row1, col0:col1], blur_kernel)
        mask = _candidate_mask(region, warmer)
        spans = _sweep_spans_from_seed(mask, seed_row_local, seed_col_local, row0, col0)
        result[idx] = _smooth_spans(_smooth_spans(spans))
    return result


class _ShrinkageMixin:
    def _build_shrinkage_panel(self) -> None:
        # EINE Box im Bild (siehe roi.py:AdjustableROI) -- Standardgroesse/
        # -position wird erst bekannt, wenn eine Aufnahme geladen ist (siehe
        # _reset_shrinkage_state_for_recording), hier nur Platzhalter-
        # Geometrie (view_box existiert zu diesem Zeitpunkt in __init__
        # bereits). Deckt (mit Rand) fast das ganze Bild ab, da sie NICHT
        # nachgefuehrt wird und die Probe ueber die gesamte Aufnahme hinweg
        # umschliessen muss (siehe _track_sample_blob).
        self.roi_shrink_area = AdjustableROI((10, 10), [80, 60], pen=pg.mkPen(self._shrinkage_color_area, width=2))
        self.roi_shrink_area.setVisible(False)
        # sigRegionChangeStarted feuert einmal beim Beginn des Ziehens
        # (siehe undo_ops.py-Moduldocstring) -- EIN Snapshot pro Drag.
        self.roi_shrink_area.sigRegionChangeStarted.connect(self._push_undo_snapshot)
        self.view_box.addItem(self.roi_shrink_area)

        # Kontur-Ueberlagerung im Thermobild NACH "Berechnen" (Nutzerwunsch:
        # die tatsaechlich erkannte Kontur sehen) -- EINE geschlossene Linie,
        # die pro Frame neu gezeichnet wird (siehe
        # _rebuild_shrinkage_contour_overlay). Farbe eigenstaendig waehlbar
        # (Nutzerwunsch, bisher fest Rot -- siehe _shrinkage_color_contour
        # in window.py), analog zur Boxfarbe.
        self.shrinkage_contour_line = pg.PlotDataItem(pen=pg.mkPen(self._shrinkage_color_contour, width=2))
        self.shrinkage_contour_line.setZValue(11)
        self.shrinkage_contour_line.setVisible(False)
        self.view_box.addItem(self.shrinkage_contour_line)

        self._shrinkage_result: dict | None = None
        # Nutzerwunsch: die Y-Achse zeigt IMMER Prozent (siehe
        # _update_shrinkage_curve) -- ohne dies wuerde pyqtgraph bei
        # Werten >= 1000% (theoretisch, nicht praktisch relevant, aber
        # z.B. bei falsch gezogener Box moeglich) ein SI-Praefix wie "k%"
        # anzeigen, was fuer einen Prozentwert irritierend waere.
        self.shrinkage_plot.getAxis("left").enableAutoSIPrefix(False)
        # Eigener kleiner Graph statt des Zeitverlauf-Graphen (siehe
        # ui_build.py:_build_plots) -- die Kenngroessen haben eine andere
        # Einheit (Prozent) als die °C-Kurven dort.
        self.shrinkage_curve = self.shrinkage_plot.plot(
            pen=pg.mkPen("#f472b6", width=2), name="Schwindung",
        )
        self.shrinkage_curve.setVisible(False)

        box = QtWidgets.QGroupBox("Schwindungsmessung (experimentell)")
        # Als self.-Attribut gehalten: roi_panel_build.py haengt DIESE
        # Gruppenbox unveraendert in ihre eigene Panel-Registerkarte
        # "Schwindungsmessung" ein (siehe _build_control_panel).
        self._shrinkage_groupbox = box
        layout = QtWidgets.QVBoxLayout(box)
        layout.setSpacing(10)

        # Blocksatz (Nutzerwunsch: UI "professioneller") fuer die
        # mehrzeiligen Erklaerungstexte -- setAlignment(AlignJustify)
        # zusaetzlich zu setWordWrap.
        info = QtWidgets.QLabel(
            "Einen Bereich über die gesamte Probe -- mit etwas Rand -- ziehen. Das Programm sucht "
            "darin automatisch die tatsächliche, zusammenhängende Kontur der Probe (geometrieunabhängig "
            "-- funktioniert für eckige wie runde Proben gleichermaßen) und zeigt sie als rote Linie im "
            "Thermobild an. Schwellenwert und Polarität (wärmer/kälter als Hintergrund) werden "
            "automatisch erkannt. Experimentell: sehr kleine Schwindungen (oft < 1%) sind je nach "
            "Kamera-Auflösung u.U. nicht zuverlässig auflösbar."
        )
        info.setWordWrap(True)
        info.setAlignment(QtCore.Qt.AlignJustify)
        info.setStyleSheet("color:#6b7280;")
        layout.addWidget(info)

        self.chk_shrinkage_enabled = QtWidgets.QCheckBox("Aktivieren (Bereich im Bild einblenden)")
        self.chk_shrinkage_enabled.toggled.connect(self._on_shrinkage_enabled_toggled)
        layout.addWidget(self.chk_shrinkage_enabled)

        # KEIN manueller Schwellenwert/keine manuelle "wärmer/kälter"-Wahl
        # (siehe Modul-Docstring) -- wird bei "Berechnen" automatisch
        # ermittelt, dieses Label zeigt das Ergebnis nur zur
        # Nachvollziehbarkeit an.
        self.lbl_shrinkage_polarity = QtWidgets.QLabel(
            "Polarität (wärmer/kälter als Hintergrund): wird bei \"Berechnen\" automatisch erkannt."
        )
        self.lbl_shrinkage_polarity.setWordWrap(True)
        self.lbl_shrinkage_polarity.setAlignment(QtCore.Qt.AlignJustify)
        self.lbl_shrinkage_polarity.setStyleSheet("color:#6b7280;")
        layout.addWidget(self.lbl_shrinkage_polarity)

        # Box- und Ergebnis-Bereich optisch klar getrennt (Nutzerwunsch:
        # UI "professioneller") -- analog zum bestehenden gerahmten
        # Spalten-Muster an anderer Stelle im Panel. Boxfarbe/Konturfarbe/
        # Kenngröße bewusst in EINER Zeile (Nutzerwunsch: "aktuell wird viel
        # Platz für wenig Widgets verbraucht") statt je einer eigenen Zeile
        # für die beiden kompakten Farb-Swatches.
        box_frame = QtWidgets.QFrame()
        box_frame.setFrameShape(QtWidgets.QFrame.StyledPanel)
        box_frame_layout = QtWidgets.QHBoxLayout(box_frame)

        box_frame_layout.addWidget(QtWidgets.QLabel("Boxfarbe:"))
        self.btn_shrinkage_color = QtWidgets.QPushButton()
        self.btn_shrinkage_color.setFixedSize(20, 20)
        self.btn_shrinkage_color.setCursor(QtCore.Qt.PointingHandCursor)
        self.btn_shrinkage_color.setToolTip(
            "Farbe der Box ändern -- hilfreich, falls sie im aktuell gewählten Farbverlauf kaum zu "
            "erkennen ist."
        )
        self.btn_shrinkage_color.clicked.connect(self._on_shrinkage_color_clicked)
        box_frame_layout.addWidget(self.btn_shrinkage_color)
        self._apply_shrinkage_box_colors()

        box_frame_layout.addSpacing(12)
        box_frame_layout.addWidget(QtWidgets.QLabel("Konturfarbe:"))
        self.btn_shrinkage_contour_color = QtWidgets.QPushButton()
        self.btn_shrinkage_contour_color.setFixedSize(20, 20)
        self.btn_shrinkage_contour_color.setCursor(QtCore.Qt.PointingHandCursor)
        self.btn_shrinkage_contour_color.setToolTip(
            "Farbe der erkannten Kontur-Linie im Thermobild ändern -- hilfreich, falls sie im aktuell "
            "gewählten Farbverlauf kaum zu erkennen ist."
        )
        self.btn_shrinkage_contour_color.clicked.connect(self._on_shrinkage_contour_color_clicked)
        box_frame_layout.addWidget(self.btn_shrinkage_contour_color)
        self._apply_shrinkage_contour_color()

        box_frame_layout.addSpacing(12)
        box_frame_layout.addWidget(QtWidgets.QLabel("Kenngröße:"))
        self.combo_shrinkage_metric = QtWidgets.QComboBox()
        for value, label in _SHRINKAGE_METRIC_LABELS.items():
            self.combo_shrinkage_metric.addItem(label, value)
        self.combo_shrinkage_metric.setToolTip(
            "Welche Kenngröße aus der erkannten Kontur berechnet wird -- \"quaderförmig\" nimmt den "
            "Median der Zeilenbreiten (passend für gerade, parallele Kanten), \"rund\" die breiteste "
            "Zeile (den \"Äquator\", passend für eine gewölbte Kontur). Nach \"Berechnen\" wirkt ein "
            "Wechsel sofort, ohne erneut berechnen zu müssen."
        )
        self.combo_shrinkage_metric.currentIndexChanged.connect(self._on_shrinkage_metric_changed)
        box_frame_layout.addWidget(self.combo_shrinkage_metric, 1)
        layout.addWidget(box_frame)

        self.btn_shrinkage_compute = QtWidgets.QPushButton("Berechnen")
        self.btn_shrinkage_compute.clicked.connect(self._on_shrinkage_compute_clicked)
        layout.addWidget(self.btn_shrinkage_compute)

        result_frame = QtWidgets.QFrame()
        result_frame.setFrameShape(QtWidgets.QFrame.StyledPanel)
        result_frame_layout = QtWidgets.QVBoxLayout(result_frame)
        self.lbl_shrinkage_result = QtWidgets.QLabel("Noch nicht berechnet.")
        self.lbl_shrinkage_result.setWordWrap(True)
        self.lbl_shrinkage_result.setAlignment(QtCore.Qt.AlignJustify)
        result_frame_layout.addWidget(self.lbl_shrinkage_result)
        layout.addWidget(result_frame)

        self._set_shrinkage_controls_enabled(False)

    def _set_shrinkage_controls_enabled(self, enabled: bool) -> None:
        for widget in (
            self.combo_shrinkage_metric, self.btn_shrinkage_compute,
            self.btn_shrinkage_color, self.btn_shrinkage_contour_color,
        ):
            widget.setEnabled(enabled)

    def _apply_shrinkage_roi_visibility(self) -> None:
        """Zentrale Stelle, ob die Box gerade sichtbar sein soll -- haengt
        von "Aktivieren" UND dem aktiven Ebenen-Tab ab, siehe
        _on_shrinkage_enabled_toggled/_reset_shrinkage_state_for_recording."""
        visible = (
            self._shrinkage_enabled and self.recording is not None
            and self._is_layer_tab_active("shrinkage")
        )
        self.roi_shrink_area.setVisible(visible)
        self._rebuild_shrinkage_contour_overlay()

    def _on_shrinkage_color_clicked(self) -> None:
        color = QtWidgets.QColorDialog.getColor(QtGui.QColor(self._shrinkage_color_area), self, "Farbe der Box wählen")
        if not color.isValid():
            return
        self._push_undo_snapshot()
        self._shrinkage_color_area = color.name()
        self._apply_shrinkage_box_colors()

    def _apply_shrinkage_box_colors(self) -> None:
        """Uebertraegt self._shrinkage_color_area auf die Box-Umrandung UND
        den Farb-Swatch-Knopf -- gemeinsame Stelle fuer _on_shrinkage_
        color_clicked, den initialen Aufbau und das Laden eines Projekts
        (siehe project_io.py). Die Kontur-Linie selbst hat eine eigene,
        unabhaengig waehlbare Farbe, siehe _apply_shrinkage_contour_color."""
        self.roi_shrink_area.setPen(pg.mkPen(self._shrinkage_color_area, width=2))
        self.btn_shrinkage_color.setStyleSheet(
            f"background-color:{self._shrinkage_color_area}; border:1px solid #333; border-radius:4px;"
        )

    def _on_shrinkage_contour_color_clicked(self) -> None:
        color = QtWidgets.QColorDialog.getColor(
            QtGui.QColor(self._shrinkage_color_contour), self, "Farbe der Kontur-Linie wählen",
        )
        if not color.isValid():
            return
        self._push_undo_snapshot()
        self._shrinkage_color_contour = color.name()
        self._apply_shrinkage_contour_color()

    def _apply_shrinkage_contour_color(self) -> None:
        """Uebertraegt self._shrinkage_color_contour auf die Kontur-Linie
        (shrinkage_contour_line) UND den Farb-Swatch-Knopf -- gemeinsame
        Stelle fuer _on_shrinkage_contour_color_clicked, den initialen
        Aufbau und das Laden eines Projekts (siehe project_io.py)."""
        self.shrinkage_contour_line.setPen(pg.mkPen(self._shrinkage_color_contour, width=2))
        self.btn_shrinkage_contour_color.setStyleSheet(
            f"background-color:{self._shrinkage_color_contour}; border:1px solid #333; border-radius:4px;"
        )

    def _rebuild_shrinkage_contour_overlay(self) -> None:
        """Zeichnet im Thermobild die als Probe erkannte, zusammenhaengende
        Kontur als rote Linie -- ausgewertet fuer das AKTUELL angezeigte
        Bild, mit den beim letzten "Berechnen" tatsaechlich ermittelten
        Zeilen-Spans (siehe _compute_shrinkage), NICHT neu aus der evtl.
        seither weiterverschobenen Box berechnet -- sonst wuerde die
        Ueberlagerung nicht mehr zum berechneten Ergebnis passen. Ohne
        Ergebnis/bei deaktivierter Messung/auf einem anderen Ebenen-Tab
        bleibt die Linie unsichtbar."""
        result = self._shrinkage_result
        if (
            result is None or self.recording is None or not self._shrinkage_enabled
            or not self._is_layer_tab_active("shrinkage")
        ):
            self.shrinkage_contour_line.setVisible(False)
            return
        idx = max(0, min(self.current_index, self.recording.n_frames - 1))
        xs, ys = _spans_to_polygon(result["spans"].get(idx, {}))
        if xs.size == 0:
            self.shrinkage_contour_line.setVisible(False)
            return
        self.shrinkage_contour_line.setData(xs, ys)
        self.shrinkage_contour_line.setVisible(True)

    def _on_shrinkage_enabled_toggled(self, checked: bool) -> None:
        self._push_undo_snapshot()
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
        self._rebuild_shrinkage_contour_overlay()

    def _on_shrinkage_metric_changed(self, _index: int) -> None:
        """Kenngroessen-Dropdown gewechselt -- wirkt SOFORT auf Kurve/
        Ergebnis-Text, OHNE neu zu "Berechnen": alle drei Kenngroessen
        liegen bereits (aus derselben Kontur-Erkennung) in self._shrinkage_
        result vor, siehe Modul-Docstring."""
        self._push_undo_snapshot()
        self._shrinkage_metric = self.combo_shrinkage_metric.currentData()
        self._update_shrinkage_curve()

    def _reset_shrinkage_state_for_recording(self) -> None:
        """Positioniert die Box neu passend zur (neu geladenen) Aufnahme
        und verwirft ein evtl. vorhandenes Ergebnis -- Box/Ergebnis der
        VORHERIGEN Aufnahme sind fuer eine andere Bildgroesse/Frame-Anzahl
        bedeutungslos (gleiche Invariante wie bei ROIs/Messungen/
        Bereinigung, siehe frame_nav.py). Die gewaehlte Kenngroesse selbst
        bleibt (wie die Boxfarbe) ueber einen Reload hinweg erhalten."""
        self._shrinkage_result = None
        self.lbl_shrinkage_result.setText("Noch nicht berechnet.")
        self.lbl_shrinkage_result.setToolTip("")
        self.lbl_shrinkage_polarity.setText(
            "Polarität (wärmer/kälter als Hintergrund): wird bei \"Berechnen\" automatisch erkannt."
        )
        self.shrinkage_curve.clear()
        self.shrinkage_curve.setVisible(False)
        if self.recording is None:
            self._apply_shrinkage_roi_visibility()
            return
        rows, cols = self.recording.shape
        self.roi_shrink_area.setPos((cols * 0.05, rows * 0.1), update=False)
        self.roi_shrink_area.setSize((cols * 0.9, rows * 0.8))
        self._apply_shrinkage_roi_visibility()

    def _on_shrinkage_compute_clicked(self) -> None:
        if self.recording is None:
            return
        self._compute_shrinkage()

    def _compute_shrinkage(self) -> None:
        rows, cols = self.recording.shape
        row0, row1, col0, col1 = self.roi_shrink_area.bounds_px((rows, cols))
        n = self.recording.n_frames
        frames = self.recording.frames
        # Nutzerfeedback: die Berechnung laeuft synchron im UI-Thread und
        # kann bei vielen Bildern mehrere Sekunden dauern -- ohne jegliche
        # Rueckmeldung wirkte die App dabei "eingefroren". Statuszeile +
        # Sanduhr-Cursor VOR dem eigentlichen (blockierenden) Rechenschritt
        # setzen und per processEvents() erzwungen einmal anzeigen lassen,
        # da Qt Statuszeilen-Text sonst erst beim naechsten Event-Loop-
        # Durchlauf tatsaechlich neu zeichnen wuerde -- der direkt
        # anschliessende, lange Rechenschritt kaeme dafuer nie dazu.
        self.statusBar().showMessage(f"Schwindungsberechnung läuft… ({n} Bilder)")
        QtWidgets.QApplication.setOverrideCursor(QtGui.QCursor(QtCore.Qt.WaitCursor))
        QtWidgets.QApplication.processEvents()
        try:
            # Polaritaet einmalig am aktuell angezeigten Bild automatisch
            # ermittelt und fuer die gesamte Messung festgehalten (kein
            # "Startbild"-Konzept mehr, siehe Modul-Docstring).
            warmer = _detect_polarity_area(frames[min(self.current_index, n - 1)], row0, row1, col0, col1)
            # Saatpunkt = Boxzentrum, JEDES Bild unabhaengig (siehe
            # _track_sample_blob) -- dieselbe Annahme, die _detect_polarity_area
            # bereits macht.
            seed_row = (row0 + row1) // 2
            seed_col = (col0 + col1) // 2
            spans_by_frame = _track_sample_blob(frames, row0, row1, col0, col1, warmer, seed_row, seed_col)

            areas_px = np.empty(n, dtype=float)
            rect_widths_px = np.empty(n, dtype=float)
            round_widths_px = np.empty(n, dtype=float)
            for i in range(n):
                areas_px[i], rect_widths_px[i], round_widths_px[i] = _spans_metrics(spans_by_frame[i])

            self._shrinkage_result = {
                "areas_px": areas_px, "rect_widths_px": rect_widths_px, "round_widths_px": round_widths_px,
                "warmer": warmer,
                # Zeilen-Spans je Bild -- Grundlage der Kontur-Ueberlagerung im
                # Bild (siehe _rebuild_shrinkage_contour_overlay).
                "spans": spans_by_frame,
            }
        finally:
            QtWidgets.QApplication.restoreOverrideCursor()
        self._update_shrinkage_curve()
        self._set_shrinkage_polarity_label(warmer)
        self.statusBar().showMessage(f"Schwindungsberechnung abgeschlossen ({n} Bilder).", 4000)

    def _set_shrinkage_polarity_label(self, warmer: bool) -> None:
        """Zeigt die automatisch erkannte Polaritaet an (Nutzerwunsch,
        siehe Modul-Docstring) -- rein informativ, keine Eingabe."""
        richtung = "wärmer" if warmer else "kälter"
        self.lbl_shrinkage_polarity.setText(
            f"Polarität (automatisch erkannt): Probe ist {richtung} als der Hintergrund."
        )

    def _shrinkage_metric_key(self) -> str:
        return _SHRINKAGE_METRIC_KEYS[self._shrinkage_metric]

    def _shrinkage_metric_label(self) -> str:
        return _SHRINKAGE_METRIC_LABELS[self._shrinkage_metric]

    def _shrinkage_metric_is_area(self) -> bool:
        return self._shrinkage_metric == "flaeche"

    def _update_shrinkage_curve(self) -> None:
        if self._shrinkage_result is None or self.recording is None:
            self.shrinkage_curve.clear()
            self.shrinkage_curve.setVisible(False)
            self.lbl_shrinkage_result.setText("Noch nicht berechnet.")
            self.lbl_shrinkage_result.setToolTip("")
            self._rebuild_shrinkage_contour_overlay()
            return
        is_area = self._shrinkage_metric_is_area()
        raw = self._shrinkage_result[self._shrinkage_metric_key()]
        scale = (self._px_to_mm ** 2 if is_area else self._px_to_mm) if self._px_to_mm is not None else 1.0
        # Nutzerwunsch: die Kurve (wie das Ergebnis-Label) IMMER als
        # Prozentwert relativ zum ersten Bild zeigen (A/A0 bzw. l/l0) --
        # entkoppelt von der gewaehlten Kenngroesse/dem Maßstab, siehe
        # _update_shrinkage_result_label. Erstes Bild = 0.0 (first==0
        # abgefangen, um eine Division durch 0 zu vermeiden).
        first = float(raw[0])
        values = (first - raw) / first * 100.0 if first else np.zeros_like(raw)
        unix = self.recording.unix_seconds()
        if self._excluded_frame_indices:
            keep_mask = np.ones(len(unix), dtype=bool)
            keep_mask[list(self._excluded_frame_indices)] = False
            self.shrinkage_curve.setData(unix[keep_mask], values[keep_mask])
        else:
            self.shrinkage_curve.setData(unix, values)
        self.shrinkage_plot.setLabel("left", f"Schwindung ({self._shrinkage_metric_label()})", units="%")
        self.shrinkage_curve.setVisible(self._shrinkage_enabled)
        units = ("mm²" if is_area else "mm") if self._px_to_mm is not None else ("px²" if is_area else "px")
        self._update_shrinkage_result_label(raw, scale, units)
        self._rebuild_shrinkage_contour_overlay()

    def _update_shrinkage_result_label(self, raw: np.ndarray, scale: float, units: str) -> None:
        """Nutzerwunsch: die Schwindung unten rechts IMMER als Prozentwert
        (A/A0 bzw. l/l0) anzeigen, unabhaengig von der gewaehlten
        Kenngroesse -- keine absoluten mm/px-Werte mehr in der Hauptzeile.
        Diese bleiben als Tooltip erhalten, damit die Information nicht
        komplett verloren geht."""
        first, last = float(raw[0]), float(raw[-1])
        shrink_pct = (first - last) / first * 100.0 if first else 0.0
        self.lbl_shrinkage_result.setText(
            f"Schwindung: {shrink_pct:.2f} % (experimentell, siehe Hinweis oben)"
        )
        label = self._shrinkage_metric_label()
        first_disp, last_disp = first * scale, last * scale
        self.lbl_shrinkage_result.setToolTip(
            f"{label} Bild 1: {first_disp:.2f} {units}\n{label} letztes Bild: {last_disp:.2f} {units}"
        )
