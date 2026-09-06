"""Modulweite Konstanten des Hauptfensters, die von mehreren Mixins
gemeinsam genutzt werden -- in einem eigenen, abhängigkeitsfreien Modul,
damit die Mixins sie importieren können, ohne einen Ringimport mit
window.py (das seinerseits alle Mixins importiert) zu erzeugen."""
from __future__ import annotations

# Zwei feste Farbschemata (Hell/Dunkel) fuer Bild- und Kurven-Widgets sowie
# die restliche Qt-Oberflaeche. "Hell" entspricht dem klassischen weissen
# Hintergrund; "Dunkel" ist das bisherige (unabsichtliche) Erscheinungsbild
# mit schwarzem Bildhintergrund, jetzt als bewusste Wahl mit passender
# Restoberflaeche.
THEMES = {
    "light": {
        "label": "Hell",
        "pg_background": "#ffffff",
        "pg_foreground": "#000000",
    },
    "dark": {
        "label": "Dunkel",
        "pg_background": "#1e1e1e",
        "pg_foreground": "#e0e0e0",
    },
}
DEFAULT_THEME = "light"

COLORMAPS = [
    ("Ironbow", "CET-L17"),
    ("Inferno", "inferno"),
    ("Plasma", "plasma"),
    ("Viridis", "viridis"),
    ("Magma", "magma"),
    ("Turbo", "turbo"),
    ("Graustufen", "CET-L1"),
    ("Hot", "CET-L3"),
    ("Cividis", "cividis"),
    ("Coolwarm", "CET-D1"),
    ("Rainbow", "CET-R2"),
]

# pyqtgraph-IDs, deren Farbverlauf in seiner Rohform (Stopp bei 0.0 ->
# Stopp bei 1.0) entgegen der uebrigen Paletten NICHT von dunkel/kalt nach
# hell/warm verlaeuft -- geprueft anhand der tatsaechlichen LUT-Werte
# (CET-L17 "Ironbow" geht z.B. von WEISS bei 0.0 zu DUNKELBLAU bei 1.0,
# waehrend z.B. Inferno/Turbo/Hot/... bereits korrekt dunkel->hell laufen).
# Diese Paletten werden in _apply_colormap() standardmaessig (Haken
# "Invertiert" AUS) zusaetzlich gespiegelt, damit auch sie kalt=dunkel,
# heiss=hell zeigen.
COLORMAPS_BASE_REVERSED = {"CET-L17"}

# Ab so vielen Frames werden Punktmarker auf den Kurven ausgeblendet (nur
# noch Linie), damit es bei langen Aufnahmen nicht überladen wirkt. Bei
# wenigen Frames (z.B. nur 1) sind Marker nötig, sonst ist gar nichts zu
# sehen -- eine Linie braucht mindestens zwei Punkte.
MAX_FRAMES_WITH_SYMBOLS = 60

# Beschriftungen der Start-/Ende-Buttons der Verlaufs-Interpolation, sowohl im
# Ruhezustand als auch (siehe _on_roi_interp_capture) waehrend des zweistufigen
# Ablaufs "hinspringen -> Messbereich setzen -> hier klicken zum Uebernehmen".
# Bewusst OHNE festen Frame-Bezug im Text (frueher "(1. Bild)"/"(letztes
# Bild)") -- das Ziel-Bild ist jetzt per Spinbox frei waehlbar (Standard:
# weiterhin erstes/letztes Bild), siehe spin_interp_start_frame/-end_frame.
INTERP_START_LABEL = "Start festlegen…"
INTERP_END_LABEL = "Ende festlegen…"
# Eigene Beschriftung je Start/Ende (statt eines gemeinsamen "Position
# übernehmen"): sind beide Buttons gleichzeitig armiert (Start armiert, dann
# ohne abzuschliessen auch Ende angeklickt), waeren sonst zwei Buttons mit
# identischem Text nicht mehr unterscheidbar.
INTERP_START_CAPTURE_LABEL = "Start übernehmen"
INTERP_END_CAPTURE_LABEL = "Ende übernehmen"
