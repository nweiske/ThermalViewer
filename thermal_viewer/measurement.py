"""Ad-hoc-Streckenmessung im Thermobild: eine benannte, farbige Linie samt
frei verschiebbarer Beschriftung -- nutzt den vom Maßstab definierten
px-zu-mm-Faktor rein LESEND (siehe MainWindow, wo dieser Faktor lebt)."""
from __future__ import annotations

import pyqtgraph as pg
from qtpy import QtCore, QtWidgets

from .roi_entry import mm_value_de

# Obergrenze fuer "beliebig viele Messungen gleichzeitig" (Punkt 8) -- schuetzt
# MainWindow._load_project analog zu roi_entry.MAX_ROI_COUNT.
MAX_MEASUREMENT_COUNT = 500
# Farbe des nicht-interaktiven Zwei-Klick-Vorschau-Markers waehrend der
# Messungs-Erfassung (vor dem ersten fertigen Ergebnis, siehe
# MainWindow._handle_measurement_click) -- die eigentliche MeasurementEntry
# bekommt danach ihre eigene Farbe ueber roi_color_for_number().
MEASUREMENT_PREVIEW_COLOR = "#2dd4bf"


def clamp_label_offset(offset: QtCore.QPointF, line_length: float) -> QtCore.QPointF:
    """Begrenzt den Versatz einer Maßstab-/Messungs-Beschriftung zum
    Linien-Mittelpunkt auf die naehere Umgebung der Linie (Nutzerwunsch:
    "kann nur in der näheren Umgebung der Linie verschoben werden, nicht
    beliebig weit weg") -- der erlaubte Radius skaliert mit der
    Linienlaenge selbst (kurze Linien -> enger Radius, lange Linien -> weiter
    Radius), mit einer kleinen festen Untergrenze fuer sehr kurze/
    punktfoermige Linien. Modulweite Funktion (statt Methode auf
    MainWindow), damit sowohl MeasurementEntry (siehe unten, eigene
    Beschriftung/Linie) als auch der Maßstab (MainWindow._MeasurementMixin)
    dieselbe Formel nutzen, ohne dass MeasurementEntry auf MainWindow
    zugreifen muesste.

    Bugfix Folgeanfrage ("auch wenn ich die Box loslasse springt sie nicht
    zurueck"): der urspruengliche Faktor 1.5 ergab fuer eine Linie, die einen
    guten Teil des Bilds ueberspannt (typischer Maßstab, oft fast so breit
    wie das Thermobild selbst), einen Radius, der GROESSER als das gesamte
    sichtbare Bild sein konnte -- das Clamping griff dann bei jeder in der
    Praxis vorkommenden Ablage-Position ueberhaupt nicht mehr, wirkte also
    wie "gar nicht wirksam". Faktor auf 0.5 (statt 1.5) reduziert: die
    Beschriftung darf sich hoechstens um die HALBE Linienlaenge vom
    Mittelpunkt entfernen -- bleibt bei jeder Linienlaenge sichtbar in deren
    unmittelbarer Naehe."""
    max_dist = max(line_length * 0.5, 20.0)
    dist = (offset.x() ** 2 + offset.y() ** 2) ** 0.5
    if dist <= max_dist or dist < 1e-9:
        return offset
    scale = max_dist / dist
    return QtCore.QPointF(offset.x() * scale, offset.y() * scale)


class DraggableTextItem(pg.TextItem):
    """pg.TextItem mit nativer Qt-Verschiebbarkeit (ItemIsMovable) -- pyqtgraphs
    eigenes TextItem bietet dafuer (anders als seine ROI-Klassen) kein
    movable-Flag/kein mouseDragEvent. Fuer Maßstab-/Messungs-Beschriftungen
    (Punkt 9, Nutzerwunsch): mehrere nah beieinanderliegende Messungen
    (z.B. mehrere Durchmesser desselben Kreises) ueberlappen sich sonst in
    ihrer Text-Beschriftung, wenn diese immer starr am Linien-Mittelpunkt
    haengt. on_moved wird NACH jedem Loslassen der Maus aufgerufen (nicht
    waehrend des Ziehens), damit der Aufrufer den resultierenden Versatz
    zum Linien-Mittelpunkt genau einmal einfrieren kann (siehe
    MainWindow._on_ruler_label_moved/_on_measurement_label_moved)."""

    def __init__(self, *args, on_moved=None, on_double_clicked=None, clamp_fn=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.setFlag(QtWidgets.QGraphicsItem.GraphicsItemFlag.ItemIsMovable)
        self._on_moved = on_moved
        self._on_double_clicked = on_double_clicked
        # Bugfix Folgeanfrage ("kann die Beschriftung noch quer durchs Bild
        # ziehen"): Qts eigener ItemIsMovable-Mechanismus verschiebt das Item
        # in mouseMoveEvent() zunaechst voellig frei -- ein Clamping erst
        # NACH dem Loslassen (siehe on_moved oben) liess die Beschriftung
        # WAEHREND des Ziehens selbst weiter ungebremst dem Mauszeiger folgen
        # und nur am Ende sichtbar zurueckspringen. clamp_fn korrigiert die
        # Position daher schon live nach jedem einzelnen Bewegungsschritt.
        self._clamp_fn = clamp_fn

    def mouseMoveEvent(self, ev) -> None:
        super().mouseMoveEvent(ev)
        if self._clamp_fn is not None:
            clamped = self._clamp_fn(self.pos())
            if clamped != self.pos():
                self.setPos(clamped)

    def mouseReleaseEvent(self, ev) -> None:
        super().mouseReleaseEvent(ev)
        if self._on_moved is not None:
            self._on_moved()

    def mouseDoubleClickEvent(self, ev) -> None:
        """Bugfix (Punkt 11 Folgeanfrage): seit ItemIsMovable (siehe oben) auch
        auf DIESEM Item aktiv ist, faengt es Mausereignisse selbst ab, bevor sie
        die Szene als generischer Doppelklick erreichen -- ein Doppelklick
        direkt auf die Beschriftung (statt auf die Linie) loeste dadurch nicht
        mehr die Laengen-Bearbeitung aus (Bugreport: "kann den Maßstab nur noch
        durch einen Doppelklick auf die Maßstablinie verändern - nicht mehr
        auf die Textbox"). super().mouseDoubleClickEvent() wird bewusst NICHT
        aufgerufen (dessen Default wuerde nur mousePressEvent() erneut anstossen
        und so eine ungewollte Verschiebung einleiten)."""
        if self._on_double_clicked is not None:
            self._on_double_clicked()
            ev.accept()
            return
        super().mouseDoubleClickEvent(ev)


class MeasurementEntry:
    """Eine benannte, farbige Ad-hoc-Streckenmessung im Thermobild (Punkt 8,
    Nutzerwunsch: "beliebig viele (Größen-)Messungen gleichzeitig") -- nutzt
    den vom Maßstab definierten px-zu-mm-Faktor rein LESEND (aendert ihn nie).
    Analog zu RoiEntry gebuendelt: Bild-Objekte (line/text) UND die
    zugehoerigen Steuer-Widgets im rechten Panel."""

    def __init__(
        self,
        number: int,
        color: str,
        view_box: pg.ViewBox,
        start: tuple[float, float],
        end: tuple[float, float],
        on_label_moved,
    ) -> None:
        # start/end werden NUR bei der Erzeugung gebraucht (LineSegmentROI
        # kennt anders als PolyLineROI kein setPoints() zum nachtraeglichen
        # Umsetzen beider Endpunkte auf einmal -- eine neue Messung bekommt
        # daher wie frueher die Lineal-Linie eine frisch positionierte Linie
        # statt einer wiederverwendeten).
        self.number = number
        self.color = color
        self.name = f"Messung {number}"
        self.mm_value: float = 0.0
        # None = Beschriftung folgt automatisch dem Linien-Mittelpunkt; nach
        # manuellem Verschieben (Punkt 9) haelt dieser Versatz relativ zum
        # Mittelpunkt fest, statt bei der naechsten Aktualisierung wieder
        # dorthin zurueckzuspringen.
        self.label_offset: QtCore.QPointF | None = None

        self.line = pg.LineSegmentROI(positions=[list(start), list(end)], pen=pg.mkPen(color, width=3))
        self.line.setZValue(11)
        view_box.addItem(self.line)

        self.text = DraggableTextItem(
            color=color, anchor=(0.5, 0), fill=(0, 0, 0, 160), on_moved=lambda: on_label_moved(self),
            clamp_fn=self._clamp_label_pos,
        )
        self.text.setZValue(11)
        self.text.setVisible(False)
        view_box.addItem(self.text)

        # Von MainWindow._add_measurement_row gesetzt, hier nur Platzhalter.
        self.row_widget: QtWidgets.QWidget | None = None
        self.name_edit: QtWidgets.QLineEdit | None = None
        self.value_label: QtWidgets.QLabel | None = None
        self.color_button: QtWidgets.QPushButton | None = None

    def remove_from_view_box(self, view_box: pg.ViewBox) -> None:
        view_box.removeItem(self.line)
        view_box.removeItem(self.text)

    def set_color(self, color: str) -> None:
        self.color = color
        self.line.setPen(pg.mkPen(color, width=3))
        self.text.setColor(color)
        if self.color_button is not None:
            self.color_button.setStyleSheet(
                f"background-color:{color}; border:1px solid #333; border-radius:4px;"
            )

    def endpoints(self) -> tuple[QtCore.QPointF, QtCore.QPointF]:
        return self.line.listPoints()

    def _clamp_label_pos(self, raw_pos: QtCore.QPointF) -> QtCore.QPointF:
        """clamp_fn fuer self.text (siehe DraggableTextItem.mouseMoveEvent) --
        haelt die Beschriftung schon waehrend des Ziehens in der naeheren
        Umgebung DIESER Messstrecke."""
        p1, p2 = self.endpoints()
        mid = (p1 + p2) / 2
        offset = clamp_label_offset(raw_pos - mid, (p2 - p1).length())
        return QtCore.QPointF(mid.x() + offset.x(), mid.y() + offset.y())

    def update_text_position(self) -> None:
        p1, p2 = self.endpoints()
        mid = (p1 + p2) / 2
        pos = mid if self.label_offset is None else mid + self.label_offset
        self.text.setText(f"{self.name}: {mm_value_de(self.mm_value)} mm")
        self.text.setPos(pos.x(), pos.y())
        # An self.line.isVisible() gekoppelt statt fest True: sonst wuerde ein
        # Umbenennen/Farbaendern (self.name_edit/self.color_button bleiben nach
        # _hide_measurement_visuals() im Panel weiter bedienbar) die Beschriftung
        # einer per neu geladener Aufnahme ausgeblendeten Messung wieder
        # einblenden -- als verwaiste Beschriftung ohne sichtbare Linie, an
        # Pixel-Koordinaten, die sich auf die ALTE Aufnahme bezogen.
        self.text.setVisible(self.line.isVisible())
        if self.value_label is not None:
            self.value_label.setText(f"{mm_value_de(self.mm_value)} mm")
