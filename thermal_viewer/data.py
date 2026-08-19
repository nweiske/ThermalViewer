"""Laden von Thermokamera-Messreihen aus ';'-getrennten CSV-Dateien.

Dateiformat: eine Zeile pro Bildzeile, Werte per ';' getrennt, Dezimalkomma
(deutsches Format), z.B. "28,6;28,7;...;". Jede Datei ist ein einzelner
Frame; der Zeitstempel steckt im Dateinamen (Record_YYYY-MM-DD_HH-MM-SS.csv).
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path

import numpy as np

FILENAME_RE = re.compile(r"(\d{4}-\d{2}-\d{2})_(\d{2}-\d{2}-\d{2})")


class RecordingError(Exception):
    pass


def parse_timestamp(path: Path) -> datetime:
    match = FILENAME_RE.search(path.stem)
    if not match:
        # Kein Zeitstempel im Namen -> Dateisystem-Änderungszeit als Fallback,
        # damit auch beliebig benannte Dateien geladen werden können.
        return datetime.fromtimestamp(path.stat().st_mtime)
    date_part, time_part = match.groups()
    return datetime.strptime(f"{date_part}_{time_part}", "%Y-%m-%d_%H-%M-%S")


def load_frame(path: Path) -> np.ndarray:
    text = path.read_text(encoding="utf-8-sig")
    rows: list[list[float]] = []
    for line in text.splitlines():
        line = line.strip().rstrip(";")
        if not line:
            continue
        rows.append([float(value.replace(",", ".")) for value in line.split(";")])
    if not rows:
        raise RecordingError(f"Datei enthält keine Daten: {path}")
    return np.asarray(rows, dtype=np.float32)


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
    """Zieht Zeitstempel, die exakt gleich sind (z.B. weil eine Datei per
    Windows-Kopie vervielfältigt wurde und den ursprünglichen Zeitstempel im
    Namen behalten hat), um jeweils 1 ms auseinander. So bleibt die
    Reihenfolge erhalten und die Zeitachse degeneriert nicht zu einem
    einzelnen Punkt."""
    had_duplicates = len(set(timestamps)) != len(timestamps)
    if not had_duplicates:
        return timestamps, False

    adjusted: list[datetime] = []
    previous: datetime | None = None
    offset = timedelta()
    for ts in timestamps:
        offset = offset + timedelta(milliseconds=1) if ts == previous else timedelta()
        adjusted.append(ts + offset)
        previous = ts
    return adjusted, True


def load_paths(paths: list[Path], progress_cb=None) -> Recording:
    """Laedt alle angegebenen Dateien zu einer Recording zusammen.

    Einzelne kaputte/unlesbare Dateien oder Frames mit abweichender
    Aufloesung brechen den Ladevorgang NICHT ab -- sie werden uebersprungen
    und landen in `Recording.skipped_files`, damit der Aufrufer den Nutzer
    warnen kann, ohne die restliche (gueltige) Messreihe wegzuwerfen. Nur
    wenn am Ende gar keine verwertbaren Frames uebrig bleiben, wird ein
    RecordingError geworfen.
    """
    paths = sorted(paths, key=parse_timestamp)

    loaded: list[tuple[Path, datetime, np.ndarray]] = []
    skipped: list[tuple[Path, str]] = []

    for i, p in enumerate(paths):
        try:
            frame = load_frame(p)
        except (OSError, UnicodeDecodeError, ValueError, RecordingError) as exc:
            skipped.append((p, str(exc)))
        else:
            loaded.append((p, parse_timestamp(p), frame))
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


def load_folder(folder: Path, progress_cb=None) -> Recording:
    paths = sorted(Path(folder).glob("*.csv"))
    if not paths:
        raise RecordingError(f"Keine CSV-Dateien in {folder} gefunden.")
    return load_paths(paths, progress_cb=progress_cb)
