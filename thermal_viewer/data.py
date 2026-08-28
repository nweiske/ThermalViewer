"""Laden von Thermokamera-Messreihen aus ';'-getrennten CSV-Dateien.

Dateiformat: eine Zeile pro Bildzeile, Werte per ';' getrennt, Dezimalkomma
(deutsches Format), z.B. "28,6;28,7;...;". Jede Datei ist ein einzelner
Frame; der Zeitstempel steckt standardmaessig im Dateinamen
(Record_YYYY-MM-DD_hh-mm-ss.csv) -- ueber ein Namens-Template (siehe
compile_filename_template) auch fuer andere Namensschemata anpassbar.
"""
from __future__ import annotations

import dataclasses
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path

import numpy as np

# Platzhalter bewusst nach international gebraeuchlicher Konvention (wie z.B.
# Excel/JavaScript/Moment.js: YYYY=Jahr, MM=Monat GROSS vs. mm=Minute klein,
# um die sonst mehrdeutige Abkuerzung "MM" fuer Monat UND Minute eindeutig zu
# machen) statt deutscher Buchstaben (JJJJ/...) -- siehe
# FilenameTemplateDialog (dialogs.py) fuer die Nutzer-Erklaerung dazu.
FILENAME_TEMPLATE_TOKENS: dict[str, tuple[str, str]] = {
    "YYYY": (r"\d{4}", "%Y"),
    "MM": (r"\d{2}", "%m"),
    "DD": (r"\d{2}", "%d"),
    "hh": (r"\d{2}", "%H"),
    "mm": (r"\d{2}", "%M"),
    "ss": (r"\d{2}", "%S"),
}
DEFAULT_FILENAME_TEMPLATE = "Record_YYYY-MM-DD_hh-mm-ss"


_FILENAME_TOKEN_CHARS = frozenset("YMDhms")


def _decompose_token_run(run: str) -> list[str] | None:
    """Zerlegt einen Lauf aus reinen Platzhalter-Buchstaben (Y/M/D/H/m/s)
    per Backtracking (laengste Tokens zuerst probiert) VOLLSTAENDIG in eine
    Folge gueltiger Platzhalter, z.B. "YYYYMMDD" -> ["YYYY","MM","DD"] oder
    "hhmmss" -> ["hh","mm","ss"]. Gibt None zurueck, wenn der Lauf sich
    nicht restlos zerlegen laesst (z.B. "MMM" oder "MD")."""
    tokens_longest_first = sorted(FILENAME_TEMPLATE_TOKENS, key=len, reverse=True)
    memo: dict[int, list[str] | None] = {}

    def solve(pos: int) -> list[str] | None:
        if pos == len(run):
            return []
        if pos in memo:
            return memo[pos]
        for tok in tokens_longest_first:
            if run.startswith(tok, pos):
                rest = solve(pos + len(tok))
                if rest is not None:
                    memo[pos] = [tok] + rest
                    return memo[pos]
        memo[pos] = None
        return None

    return solve(0)


def _tokenize_filename_template(template: str) -> list[tuple[str, str]]:
    """Zerlegt template in eine Folge von ("literal", text)/("token", NAME)-
    Stuecken -- gemeinsame Grundlage von compile_filename_template() und
    validate_filename_template(), damit beide garantiert dieselben Stellen
    als Platzhalter erkennen.

    Ein zusammenhaengender Lauf aus Platzhalter-Buchstaben wird NUR als
    Platzhalter(-Folge) gewertet, wenn er sich (a) restlos in gueltige
    Tokens zerlegen laesst UND (b) unmittelbar davor/danach KEIN
    gewoehnlicher Buchstabe steht (Bugreport: ein literaler Praefix wie
    "Messung_" enthaelt zufaellig "ss" und wurde bisher faelschlich als
    Sekunden-Platzhalter gelesen). Bedingung (b) wird auf Ebene des
    GESAMTEN zusammenhaengenden Laufs geprueft, nicht pro Einzel-Token --
    direkt aneinandergereihte Platzhalter wie "YYYYMMDD" oder "hhmmss"
    (deren Tokens sich gegenseitig beruehren) bleiben dadurch weiterhin
    korrekt erkennbar."""
    pieces: list[tuple[str, str]] = []
    literal_buf: list[str] = []
    n = len(template)
    i = 0

    def flush_literal() -> None:
        if literal_buf:
            pieces.append(("literal", "".join(literal_buf)))
            literal_buf.clear()

    while i < n:
        ch = template[i]
        if ch in _FILENAME_TOKEN_CHARS:
            j = i
            while j < n and template[j] in _FILENAME_TOKEN_CHARS:
                j += 1
            run = template[i:j]
            decomposition = _decompose_token_run(run)
            before_ok = i == 0 or not template[i - 1].isalpha()
            after_ok = j == n or not template[j].isalpha()
            if decomposition is not None and before_ok and after_ok:
                flush_literal()
                pieces.extend(("token", tok) for tok in decomposition)
            else:
                literal_buf.append(run)
            i = j
        else:
            literal_buf.append(ch)
            i += 1
    flush_literal()
    return pieces


def compile_filename_template(template: str) -> tuple[re.Pattern, str]:
    """Uebersetzt ein Namensschema-Template (z.B. "Record_YYYY-MM-DD_hh-mm-ss")
    in (a) ein Regex-Muster mit GENAU EINER Erfassungsgruppe, die den
    zeitstempel-relevanten Teilstring liefert, und (b) den passenden
    strptime()-Formatstring dafuer -- gemeinsam genutzt von parse_timestamp()
    (tatsaechliches Laden) und FilenameTemplateDialog (Live-Vorschau/
    Validierung beim Anpassen des Namensschemas), damit beide GARANTIERT
    dasselbe Verhalten zeigen.

    Literale Zeichen im Template (alles ausser den erkannten Platzhaltern,
    siehe _tokenize_filename_template) werden 1:1 escaped uebernommen -- ein
    Praefix wie "Record_" muss also nicht separat behandelt werden, sondern
    ist einfach Teil des Templates."""
    regex_body: list[str] = []
    fmt: list[str] = []
    for kind, value in _tokenize_filename_template(template):
        if kind == "literal":
            regex_body.append(re.escape(value))
            fmt.append(value)
        else:
            digit_pattern, directive = FILENAME_TEMPLATE_TOKENS[value]
            regex_body.append(digit_pattern)
            fmt.append(directive)
    pattern = re.compile("(" + "".join(regex_body) + ")")
    return pattern, "".join(fmt)


def render_filename_template(template: str, timestamp: datetime) -> str:
    """Setzt die Platzhalter (siehe FILENAME_TEMPLATE_TOKENS) in template mit
    den tatsaechlichen Werten von timestamp ein -- quasi die Umkehrung von
    compile_filename_template() (das einen BESTEHENDEN Dateinamen parst,
    dies hier ERZEUGT stattdessen einen neuen Namen). Fuer den Bildstapel-
    Export: ein frei eingegebener Dateiname-Praefix wie
    "Frame_YYYY-MM-DD_hh-mm-ss_" wird damit pro Frame mit dessen echtem
    Zeitstempel gefuellt, statt nur ein fester Text vor dem Frame-Index zu
    sein. Literale Zeichen (alles ausser erkannten Platzhaltern) bleiben
    unveraendert -- dieselbe Tokenisierung wie beim Parsen, daher IMMER
    konsistent mit compile_filename_template()."""
    parts: list[str] = []
    for kind, value in _tokenize_filename_template(template):
        if kind == "literal":
            parts.append(value)
        else:
            _digit_pattern, directive = FILENAME_TEMPLATE_TOKENS[value]
            parts.append(timestamp.strftime(directive))
    return "".join(parts)


def validate_filename_template(template: str) -> str | None:
    """Prueft, ob template alle sechs Zeitbestandteile GENAU EINMAL enthaelt
    (eine vollstaendige Zeitstempel-Aufloesung ist Voraussetzung fuer eine
    sinnvolle Zeitachse/Sortierung -- ein nur teilweiser Zeitstempel, z.B.
    ohne Uhrzeit, waere fuer die App nicht ausreichend). Gibt bei einem
    Problem eine deutschsprachige Fehlermeldung zurueck, sonst None."""
    counts: dict[str, int] = {}
    for kind, value in _tokenize_filename_template(template):
        if kind == "token":
            counts[value] = counts.get(value, 0) + 1
    missing = [t for t in FILENAME_TEMPLATE_TOKENS if counts.get(t, 0) == 0]
    duplicated = [t for t, c in counts.items() if c > 1]
    if missing:
        return "Es fehlen noch folgende Platzhalter: " + ", ".join(missing)
    if duplicated:
        return "Folgende Platzhalter dürfen nur je einmal vorkommen: " + ", ".join(duplicated)
    return None


DEFAULT_FILENAME_PATTERN, DEFAULT_FILENAME_STRPTIME_FMT = compile_filename_template(DEFAULT_FILENAME_TEMPLATE)
# Rueckwaertskompatibler Name (falls andere Module/Skripte -- z.B.
# DatasetGenerator.py -- die alte Konstante direkt referenzieren).
FILENAME_RE = DEFAULT_FILENAME_PATTERN


class RecordingError(Exception):
    pass


@dataclass
class ImportSettings:
    """Konfiguration, wie eine rohe Frame-Datei in ein 2D-Temperatur-Array
    umgewandelt wird. Die Standardwerte entsprechen exakt dem bisherigen,
    fest einprogrammierten Format (';'-getrennt, Dezimalkomma, UTF-8, keine
    Kopf-/Fusszeilen, keine zu entfernenden Spalten) -- bestehende Aufrufer
    ohne explizite ImportSettings verhalten sich dadurch unveraendert.

    Ueber den Datenimport-Manager (siehe dialogs.ImportSettingsDialog)
    anpassbar, falls kuenftige Messreihen aus anderen Quellen/Geraeten ein
    abweichendes Rohformat mitbringen (z.B. zusaetzliche Kopfzeilen, eine
    fuehrende Index-Spalte, Tabulator statt Semikolon, Dezimalpunkt statt
    -komma)."""

    delimiter: str = ";"  # "" bedeutet: beliebig viele Leerzeichen (str.split())
    decimal_separator: str = ","
    encoding: str = "utf-8-sig"
    skip_header_lines: int = 0
    skip_footer_lines: int = 0
    skip_leading_columns: int = 0
    skip_trailing_columns: int = 0

    def to_dict(self) -> dict:
        return dataclasses.asdict(self)

    @staticmethod
    def from_dict(data: dict) -> "ImportSettings":
        """Baut aus einem (z.B. aus QSettings/JSON stammenden) dict eine
        ImportSettings -- unbekannte/fehlende Schluessel fallen auf den
        jeweiligen Standardwert zurueck, damit alte/neue Programmversionen
        gespeicherte Einstellungen des jeweils anderen Stands nicht mit
        einem Fehler ablehnen."""
        defaults = ImportSettings()
        kwargs = {f.name: data.get(f.name, getattr(defaults, f.name)) for f in dataclasses.fields(ImportSettings)}
        return ImportSettings(**kwargs)


def parse_timestamp(
    path: Path,
    pattern: re.Pattern = DEFAULT_FILENAME_PATTERN,
    strptime_fmt: str = DEFAULT_FILENAME_STRPTIME_FMT,
) -> datetime:
    match = pattern.search(path.stem)
    if not match:
        # Kein Zeitstempel im Namen -> Dateisystem-Änderungszeit als Fallback,
        # damit auch beliebig benannte Dateien geladen werden können.
        return datetime.fromtimestamp(path.stat().st_mtime)
    return datetime.strptime(match.group(1), strptime_fmt)


def files_matching_template(folder: Path, pattern: re.Pattern) -> list[Path]:
    """Liefert alle ".csv"-DATEIEN (keine Ordner) in folder, deren Dateiname
    (ohne Endung) auf pattern passt -- fuer die Live-Vorschau im
    FilenameTemplateDialog UND fuer die Vorab-Pruefung beim Ordner-Oeffnen
    (MainWindow._open_folder), ob das aktuelle Namensschema ueberhaupt zu den
    vorhandenen Dateien passt."""
    return sorted(
        p for p in Path(folder).glob("*.csv")
        if p.is_file() and pattern.search(p.stem)
    )


def _select_data_lines(text: str, settings: ImportSettings) -> list[str]:
    lines = text.splitlines()
    if settings.skip_header_lines:
        lines = lines[settings.skip_header_lines:]
    if settings.skip_footer_lines:
        lines = lines[: -settings.skip_footer_lines] if settings.skip_footer_lines < len(lines) else []
    return lines


def _parse_data_line(line: str, settings: ImportSettings) -> list[float]:
    line = line.strip()
    if settings.delimiter:
        line = line.rstrip(settings.delimiter)
    if not line:
        return []
    parts = line.split(settings.delimiter) if settings.delimiter else line.split()
    if settings.skip_leading_columns:
        parts = parts[settings.skip_leading_columns:]
    if settings.skip_trailing_columns:
        parts = parts[: -settings.skip_trailing_columns] if settings.skip_trailing_columns < len(parts) else []
    sep = settings.decimal_separator
    return [float(value.strip().replace(sep, ".") if sep and sep != "." else value.strip()) for value in parts]


def parse_frame_text(text: str, settings: ImportSettings | None = None) -> np.ndarray:
    """Wandelt den rohen Inhalt EINER Frame-Datei gemaess settings in ein
    2D-Temperatur-Array um -- Kernlogik von load_frame() UND der
    Live-Vorschau im Datenimport-Manager (dialogs.ImportSettingsDialog),
    damit beide GARANTIERT dasselbe Ergebnis liefern.

    Wirft RecordingError mit einer fuer Nutzer verstaendlichen Meldung bei
    leerem Ergebnis, ungueltigen Zahlenwerten oder uneinheitlicher
    Spaltenzahl je Zeile -- letzteres ergaebe sonst erst beim spaeteren
    np.stack() der ganzen Serie einen kryptischen numpy-Fehler weit weg von
    der eigentlichen Ursache."""
    settings = settings or ImportSettings()
    rows: list[list[float]] = []
    for line in _select_data_lines(text, settings):
        try:
            values = _parse_data_line(line, settings)
        except ValueError as exc:
            raise RecordingError(f"Ungültiger Zahlenwert in Zeile „{line.strip()}“: {exc}") from exc
        if values:
            rows.append(values)
    if not rows:
        raise RecordingError(
            "Keine verwertbaren Datenzeilen gefunden -- Kopf-/Fußzeilen, Trennzeichen und "
            "Spalten-Einstellungen im Datenimport prüfen."
        )
    lengths = {len(row) for row in rows}
    if len(lengths) > 1:
        raise RecordingError(
            f"Unterschiedliche Spaltenzahl je Zeile (gefunden: {sorted(lengths)}) -- Trennzeichen und "
            f"Spalten-Einstellungen im Datenimport prüfen."
        )
    return np.asarray(rows, dtype=np.float32)


def load_frame(path: Path, import_settings: ImportSettings | None = None) -> np.ndarray:
    settings = import_settings or ImportSettings()
    try:
        text = path.read_text(encoding=settings.encoding)
    except LookupError as exc:
        raise RecordingError(f"Unbekannte Zeichenkodierung „{settings.encoding}“: {exc}") from exc
    except UnicodeDecodeError as exc:
        raise RecordingError(f"Datei lässt sich nicht mit Kodierung „{settings.encoding}“ lesen: {exc}") from exc
    return parse_frame_text(text, settings)


@dataclass
class Recording:
    paths: list[Path] = field(default_factory=list)
    timestamps: list[datetime] = field(default_factory=list)
    frames: np.ndarray | None = None  # (n_frames, rows, cols)
    had_duplicate_timestamps: bool = False
    # Dateien, die beim Laden uebersprungen wurden (kaputte/unlesbare CSV
    # oder abweichende Bildaufloesung), zusammen mit dem jeweiligen Grund.
    skipped_files: list[tuple[Path, str]] = field(default_factory=list)

    @property
    def n_frames(self) -> int:
        return 0 if self.frames is None else self.frames.shape[0]

    @property
    def shape(self) -> tuple[int, int]:
        return (0, 0) if self.frames is None else self.frames.shape[1:]

    def timestamp_seconds(self) -> np.ndarray:
        if not self.timestamps:
            return np.zeros(0)
        t0 = self.timestamps[0]
        return np.asarray([(t - t0).total_seconds() for t in self.timestamps])

    def unix_seconds(self) -> np.ndarray:
        return np.asarray([t.timestamp() for t in self.timestamps])


def _deduplicate_timestamps(timestamps: list[datetime]) -> tuple[list[datetime], bool]:
    """Zieht Zeitstempel, die exakt gleich sind oder (nach vorheriger
    Anpassung, siehe append_paths) nicht mehr streng steigen, um jeweils
    1 ms auseinander. So bleibt die Reihenfolge erhalten und die Zeitachse
    degeneriert nicht zu einem einzelnen Punkt.

    Erzwingt eine streng monoton steigende Folge statt nur exakte Duplikate
    des jeweils UNVERAENDERTEN Vorgaengers zu erkennen -- append_paths ruft
    dies auf einer Mischung aus bereits angepassten (alten) und neuen,
    unangepassten Zeitstempeln auf; ein Vergleich gegen den unangepassten
    Vorgaenger wuerde dabei erneute Kollisionen uebersehen (z.B. bereits
    angepasstes T+1ms plus neuer echter Duplikat-Zeitstempel T)."""
    adjusted: list[datetime] = []
    previous: datetime | None = None
    changed = False
    for ts in timestamps:
        if previous is not None and ts <= previous:
            ts = previous + timedelta(milliseconds=1)
            changed = True
        adjusted.append(ts)
        previous = ts
    return adjusted, changed


def load_paths(
    paths: list[Path],
    progress_cb=None,
    pattern: re.Pattern = DEFAULT_FILENAME_PATTERN,
    strptime_fmt: str = DEFAULT_FILENAME_STRPTIME_FMT,
    import_settings: ImportSettings | None = None,
) -> Recording:
    """Laedt alle angegebenen Dateien zu einer Recording zusammen.

    Einzelne kaputte/unlesbare Dateien oder Frames mit abweichender
    Aufloesung brechen den Ladevorgang NICHT ab -- sie werden uebersprungen
    und landen in `Recording.skipped_files`, damit der Aufrufer den Nutzer
    warnen kann, ohne die restliche (gueltige) Messreihe wegzuwerfen. Nur
    wenn am Ende gar keine verwertbaren Frames uebrig bleiben, wird ein
    RecordingError geworfen.

    pattern/strptime_fmt: siehe compile_filename_template() -- erlaubt ein
    vom Standard ("Record_YYYY-MM-DD_hh-mm-ss") abweichendes Namensschema
    (MainWindow._filename_pattern/_filename_strptime_fmt, siehe
    FilenameTemplateDialog).

    import_settings: siehe ImportSettings -- erlaubt ein vom Standard
    (';'-getrennt, Dezimalkomma, keine Kopf-/Fusszeilen) abweichendes
    Roh-Dateiformat (MainWindow._import_settings, siehe
    dialogs.ImportSettingsDialog). Ohne Angabe gilt das bisherige feste
    Format.
    """
    def _ts(p: Path) -> datetime:
        return parse_timestamp(p, pattern, strptime_fmt)

    paths = sorted(paths, key=_ts)

    loaded: list[tuple[Path, datetime, np.ndarray]] = []
    skipped: list[tuple[Path, str]] = []

    for i, p in enumerate(paths):
        try:
            frame = load_frame(p, import_settings)
        except (OSError, UnicodeDecodeError, ValueError, RecordingError) as exc:
            skipped.append((p, str(exc)))
        else:
            loaded.append((p, _ts(p), frame))
        if progress_cb is not None:
            progress_cb(i + 1, len(paths))

    if not loaded:
        details = "\n".join(f"- {p.name}: {err}" for p, err in skipped)
        raise RecordingError(f"Keine der ausgewählten Dateien konnte geladen werden:\n{details}")

    shape_counts: dict[tuple[int, int], int] = {}
    for _, _, frame in loaded:
        shape_counts[frame.shape] = shape_counts.get(frame.shape, 0) + 1
    reference_shape = max(shape_counts, key=shape_counts.get)

    kept: list[tuple[Path, datetime, np.ndarray]] = []
    for p, ts, frame in loaded:
        if frame.shape == reference_shape:
            kept.append((p, ts, frame))
        else:
            skipped.append(
                (
                    p,
                    f"Abweichende Bildaufloesung {frame.shape} "
                    f"({shape_counts[frame.shape]} von {len(loaded)} Datei(en)) -- "
                    f"erwartet wurde {reference_shape} "
                    f"({shape_counts[reference_shape]} von {len(loaded)} Datei(en))",
                )
            )

    if not kept:
        raise RecordingError("Keine Dateien mit einheitlicher Bildauflösung gefunden.")

    kept_paths = [p for p, _, _ in kept]
    kept_timestamps = [ts for _, ts, _ in kept]
    kept_frames = [frame for _, _, frame in kept]
    kept_timestamps, had_duplicates = _deduplicate_timestamps(kept_timestamps)

    return Recording(
        paths=kept_paths,
        timestamps=kept_timestamps,
        frames=np.stack(kept_frames),
        had_duplicate_timestamps=had_duplicates,
        skipped_files=skipped,
    )


def append_paths(
    recording: Recording,
    new_paths: list[Path],
    progress_cb=None,
    pattern: re.Pattern = DEFAULT_FILENAME_PATTERN,
    strptime_fmt: str = DEFAULT_FILENAME_STRPTIME_FMT,
    import_settings: ImportSettings | None = None,
) -> Recording:
    """Erweitert eine bestehende Recording um zusaetzliche, neu hinzugekommene
    Frames (Live-Ordner-Ueberwachung waehrend einer laufenden Messung, siehe
    MainWindow._check_for_new_files) und gibt eine NEUE Recording zurueck.

    Dateien, die (per Pfad) bereits Teil von `recording.paths` sind, werden
    ignoriert. Frames mit von der bisherigen Aufnahme abweichender
    Bildaufloesung werden -- wie bei load_paths -- einzeln uebersprungen statt
    die gesamte Erweiterung abzubrechen. Alle Frames (alt + neu) werden
    anschliessend nach Zeitstempel neu sortiert, fuer den Fall, dass neue
    Dateien nicht streng chronologisch nachgeliefert werden.

    pattern/strptime_fmt/import_settings: siehe load_paths() -- muessen mit
    dem beim urspruenglichen Laden dieser Recording verwendeten
    Namensschema/Datenimport uebereinstimmen (MainWindow uebergibt dafuer
    konsistent self._active_filename_pattern/-strptime_fmt/-import_settings)."""
    def _ts(p: Path) -> datetime:
        return parse_timestamp(p, pattern, strptime_fmt)

    existing = set(recording.paths)
    candidates = sorted((p for p in new_paths if p not in existing), key=_ts)
    if not candidates:
        return recording

    reference_shape = recording.shape
    loaded: list[tuple[Path, datetime, np.ndarray]] = []
    skipped: list[tuple[Path, str]] = list(recording.skipped_files)

    for i, p in enumerate(candidates):
        try:
            frame = load_frame(p, import_settings)
        except (OSError, UnicodeDecodeError, ValueError, RecordingError) as exc:
            skipped.append((p, str(exc)))
        else:
            if reference_shape != (0, 0) and frame.shape != reference_shape:
                skipped.append(
                    (p, f"Abweichende Bildaufloesung {frame.shape} -- erwartet wurde {reference_shape}")
                )
            else:
                loaded.append((p, _ts(p), frame))
        if progress_cb is not None:
            progress_cb(i + 1, len(candidates))

    if not loaded:
        return Recording(
            paths=recording.paths,
            timestamps=recording.timestamps,
            frames=recording.frames,
            had_duplicate_timestamps=recording.had_duplicate_timestamps,
            skipped_files=skipped,
        )

    existing_frames = list(recording.frames) if recording.frames is not None else []
    combined = list(zip(recording.paths, recording.timestamps, existing_frames)) + loaded
    combined.sort(key=lambda entry: entry[1])

    paths = [p for p, _, _ in combined]
    timestamps = [ts for _, ts, _ in combined]
    frames = [frame for _, _, frame in combined]
    timestamps, had_duplicates = _deduplicate_timestamps(timestamps)

    return Recording(
        paths=paths,
        timestamps=timestamps,
        frames=np.stack(frames),
        had_duplicate_timestamps=had_duplicates,
        skipped_files=skipped,
    )


def load_folder(
    folder: Path,
    progress_cb=None,
    pattern: re.Pattern = DEFAULT_FILENAME_PATTERN,
    strptime_fmt: str = DEFAULT_FILENAME_STRPTIME_FMT,
    import_settings: ImportSettings | None = None,
) -> Recording:
    paths = sorted(Path(folder).glob("*.csv"))
    if not paths:
        raise RecordingError(f"Keine CSV-Dateien in {folder} gefunden.")
    return load_paths(
        paths, progress_cb=progress_cb, pattern=pattern, strptime_fmt=strptime_fmt, import_settings=import_settings
    )
