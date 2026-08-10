# DataViewer

Thermo-Sequenz-Viewer für gestapelte Thermokamera-CSV-Aufnahmen
(`Record_YYYY-MM-DD_HH-MM-SS.csv`, ';'-getrennt, Dezimalkomma).

Projektverwaltung über [uv](https://docs.astral.sh/uv/):

```
uv run python run.py
```

`uv run` legt beim ersten Aufruf automatisch eine projekteigene, isolierte
`.venv` an und installiert die in `pyproject.toml`/`uv.lock` gepinnten
Abhängigkeiten (PySide6, pyqtgraph, numpy) – kein manuelles
`pip install` nötig. Abhängigkeit hinzufügen: `uv add <paket>`.

Links das Thermobild mit Legende (Farbskala + einstellbare Min/Max-Limits),
rechts andockbare Panels für bis zu 5 quadratische Messbereiche (ROIs) und
deren Temperaturverlauf über die Zeit sowie einen Live-Verlauf am
Mauszeiger. Alle Panels sind in der Breite verstellbar (Docking-System).
Jedes ROI hat eine frei wählbare Farbe (Klick auf das Farbfeld). Beide
Graphen (Zeitverlauf & Live) lassen sich über „Graph speichern…“ als
PNG/JPEG/BMP/TIFF/WebP mit wählbarer DPI exportieren – dabei entsteht
zusätzlich eine gleichnamige `.json`-Datei mit Metadaten (ROI-Koordinaten,
Farben, Zeitstempel aller Frames, Quellordner, DPI, Bildgröße).

Der Code ist über [qtpy](https://github.com/spyder-ide/qtpy) von der
konkreten Qt-Anbindung entkoppelt: lokal läuft er unter PySide6 (siehe
`pyproject.toml`), für den Windows-7-Release-Build unter PyQt5 (siehe
`requirements-win7.txt`) – ohne Code-Änderung.

Ausführliche Bedienungsanleitung (reine Textdatei, ohne Zusatzsoftware
lesbar): [documentation/Bedienungsanleitung.txt](documentation/Bedienungsanleitung.txt).

## App-Icon

Fenster-/Taskleisten-Icon und das exe-Icon kommen aus
`thermal_viewer/resources/icon.ico` (mehrere Auflösungen, generiert aus
`documentation/Gemini_Generated_Image_scxf5qscxf5qscxf.png`). Icon
ersetzen: neue `.ico`-Datei an derselben Stelle ablegen (idealerweise
mit mehreren eingebetteten Größen 16–256px).

## Release bauen

Ein Push eines Tags im Format `vX.Y.Z` (z.B. `v1.0.0`) löst automatisch
[.github/workflows/release.yml](.github/workflows/release.yml) aus und
veröffentlicht zwei exe-Varianten als Anhang eines neuen GitHub Release:

- **`ThermalViewer-vX.Y.Z-windows.zip`** – moderner Stack (PySide6, aktuelles
  Python via uv) für Windows 10/11.
- **`ThermalViewer-vX.Y.Z-windows7-legacy.zip`** – Kompatibilitäts-Build
  (PyQt5 / Qt 5.15 LTS, Python 3.8) für ältere Laborrechner unter Windows 7.
  PySide6/Qt6 unterstützt Windows 7 grundsätzlich nicht (nie, unabhängig von
  der Version), und CPython selbst nur noch bis Python 3.8 – dieser Stack
  ist daher bewusst alt und **unsupportet** (Python 3.8 ist seit 10/2024 EOL,
  Qt 5.15 Open-Source erhält keine Fixes mehr). Vor dem Rollout auf den
  Ziel-Laborrechnern testen.

```
git tag v1.0.0
git push origin v1.0.0
```

Lokal denselben modernen Build erzeugen:

```
uv run pyinstaller --noconfirm --clean --windowed --onefile --name ThermalViewer --icon thermal_viewer/resources/icon.ico --add-data "thermal_viewer/resources/icon.ico;resources" run.py
```

Die fertige exe liegt danach in `dist/ThermalViewer.exe`. Für den
Windows-7-Build lokal `requirements-win7.txt` in eine Python-3.8-Umgebung
installieren und denselben `pyinstaller`-Befehl dort (ohne `uv run`
davor) ausführen.
