"""Platzhalter-Ersetzung fuer den Bildstapel-Export-Dateinamen (IDX/LAUF) --
bewusst getrennt von data.py's Zeitstempel-Namensschema (siehe Docstrings
unten), das nur beim LADEN bestehender Dateien gilt."""
from __future__ import annotations

import re
from datetime import datetime

from ..data import render_filename_template

_INVALID_FILENAME_CHARS = re.compile(r'[\\/:*?"<>|]')


def sanitize_filename_prefix(prefix: str, fallback: str = "Frame") -> str:
    """Ersetzt unter Windows/macOS/Linux in Dateinamen ungueltige Zeichen
    (bzw. "/"/"\\", die ungewollt Unterordner erzeugen wuerden) durch "_" --
    gemeinsam genutzt von der Live-Dateiname-Vorschau in VideoExportDialog
    UND dem tatsaechlichen Bildstapel-Export (MainWindow._export_video),
    damit die Vorschau niemals einen Dateinamen zeigt, der beim
    tatsaechlichen Speichern anders aussehen wuerde."""
    return _INVALID_FILENAME_CHARS.sub("_", prefix).strip() or fallback


# Bewusst NICHT Teil von data.FILENAME_TEMPLATE_TOKENS/_tokenize_filename_
# template(): dieser Platzhalter gilt nur fuer den Bildstapel-Export-Praefix
# (fortlaufende Frame-Nummer), nicht fuer das Namensschema beim LADEN
# bestehender Dateien (compile_filename_template/validate_filename_template)
# -- eine Vermischung wuerde dort z.B. literales "IDX" in einem Ordnerpfad
# faelschlich als Zahlen-Platzhalter interpretieren. "IDX" (statt z.B. dem
# kuerzeren "ID") bewusst gewaehlt, um Kollisionen mit zufaellig in einem
# getippten Praefix vorkommenden Buchstaben "ID" zu vermeiden.
INDEX_TOKEN = "IDX"


def render_index_token(prefix: str, index: int, digits: int) -> tuple[str, bool]:
    """Ersetzt INDEX_TOKEN im (bereits Zeitstempel-gerenderten) Praefix durch
    die auf `digits` Stellen nullgefuellte, 1-basierte laufende Nummer.

    Gibt (Ergebnis, gefunden) zurueck: gefunden=False, wenn der Platzhalter
    nicht vorkam -- der Aufrufer haengt die Nummer dann wie bisher (vor
    dieser Funktion) automatisch ans Dateiname-Ende an, fuer bestehende
    Praefixe ohne den neuen Platzhalter also unveraendertes Verhalten.
    Hintergrund (Nutzerwunsch): enthaelt der Praefix bereits einen vollen
    Zeitstempel (YYYY-MM-DD_hh-mm-ss), sind die Dateien dadurch meist schon
    eindeutig unterscheidbar -- die bisher IMMER zusaetzlich angehaengte
    Nummer war dann ueberfluessig. Mit IDX kann die Nummer stattdessen an
    beliebiger Stelle im Praefix platziert werden, statt zwingend ans Ende
    angehaengt zu werden."""
    if INDEX_TOKEN not in prefix:
        return prefix, False
    return prefix.replace(INDEX_TOKEN, f"{index:0{digits}d}"), True


# Aus demselben Grund wie INDEX_TOKEN NICHT Teil von data.FILENAME_TEMPLATE_
# TOKENS -- gelten ebenfalls nur fuer den Bildstapel-Export-Praefix. Der
# kleine Buchstabe nach "LAUF" waehlt direkt die Einheit (s/m/h) -- KEIN
# separates Dropdown mehr im Dialog (Nutzerfeedback: "das Dropdown mit der
# Zeiteinheit bitte wieder weg"), da das Muster dadurch komplett fuer sich
# selbst spricht und auch ohne den Dialog geoeffnet zu haben verstaendlich
# bleibt (z.B. beim spaeteren Wiedererkennen bereits exportierter Dateien).
RUNTIME_TOKEN_SECONDS = "LAUFs"
RUNTIME_TOKEN_MINUTES = "LAUFm"
RUNTIME_TOKEN_HOURS = "LAUFh"
# Reihenfolge wichtig: laengere/spezifischere Tokens ZUERST pruefen, damit
# z.B. "LAUFm" nicht durch einen (hier ohnehin nicht mehr existierenden)
# kuerzeren "LAUF"-Treffer vorzeitig verstuemmelt wird.
_RUNTIME_TOKEN_FORMATTERS = {
    RUNTIME_TOKEN_SECONDS: lambda elapsed: f"{int(round(elapsed)):04d}s",
    RUNTIME_TOKEN_MINUTES: lambda elapsed: f"{int(round(elapsed / 60.0)):03d}min",
    RUNTIME_TOKEN_HOURS: lambda elapsed: f"{int(round(elapsed / 3600.0)):02d}h",
}


def render_runtime_token(prefix: str, seconds: float) -> tuple[str, bool]:
    """Ersetzt jedes von RUNTIME_TOKEN_SECONDS/_MINUTES/_HOURS im Praefix
    durch die verstrichene Aufnahmezeit (seconds, ab Aufnahmebeginn) in der
    per Tokenname gewaehlten Einheit -- "LAUFs": 4-stellig nullgefuellt +
    "s" (z.B. "0125s"), "LAUFm": 3-stellig + "min" (z.B. "003min"), "LAUFh":
    2-stellig + "h" (z.B. "03h") -- feste Formate nach Nutzervorgabe, nicht
    wie bei IDX frei waehlbar. Gibt (Ergebnis, gefunden) zurueck, siehe
    render_index_token(). LAUF garantiert bewusst KEINE Dateiname-
    Eindeutigkeit (mehrere Frames koennen dieselbe gerundete Sekunde/Minute/
    Stunde teilen) -- die bestehende Kollisions-Erkennung (render_export_
    filename ueber echte Beispiel-Frames verglichen) faengt das automatisch
    mit ab, ohne dass diese Funktion dafuer eine Sonderbehandlung braucht."""
    elapsed = max(0.0, seconds)
    found = False
    for token, fmt in _RUNTIME_TOKEN_FORMATTERS.items():
        if token in prefix:
            prefix = prefix.replace(token, fmt(elapsed))
            found = True
    return prefix, found


def render_export_filename(prefix: str, timestamp: datetime, elapsed_seconds: float, index: int, digits: int) -> str:
    """Verkettet render_filename_template() (Zeitstempel-Platzhalter) ->
    render_index_token() (IDX) -> render_runtime_token() (LAUFs/LAUFm/LAUFh)
    -- EINZIGE Stelle, die alle drei Platzhalter-Arten fuer den Bildstapel-
    Export kombiniert, genutzt von der Dialog-Vorschau
    (_update_filename_preview), der Kollisions-Vorpruefung UND der
    eigentlichen Export-Schleife (beide in MainWindow._export_video) -- so
    bleiben alle drei Stellen garantiert konsistent, ohne die Substitution
    dreifach zu implementieren."""
    rendered = render_filename_template(prefix, timestamp)
    rendered, _ = render_index_token(rendered, index, digits)
    rendered, _ = render_runtime_token(rendered, elapsed_seconds)
    return rendered
