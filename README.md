# DataViewer

Thermo-Sequenz-Viewer für gestapelte Thermokamera-CSV-Aufnahmen
(standardmäßig `Record_YYYY-MM-DD_hh-mm-ss.csv`, ';'-getrennt, Dezimalkomma).
Passt das Namensschema der Dateien nicht, bietet die App beim Öffnen eines
Ordners an, es anzupassen (mit Live-Vorschau) und optional als neuen
Standard zu speichern.

**Neueste Version / Downloads:** [github.com/nweiske/ThermalViewer](https://github.com/nweiske/ThermalViewer.git)
-- dort liegen auch die fertigen Releases (siehe „Release bauen“ unten).

Start (Projektverwaltung über [uv](https://docs.astral.sh/uv/), legt beim
ersten Aufruf automatisch eine eigene `.venv` an):

```
uv run python run.py
```

## Funktionen

- Thermokamera-CSV-Serie als „Video“ abspielen, Live-Temperatur am
  Mauszeiger über alle Frames
- Beliebig viele frei platzier- und skalierbare Messbereiche (ROIs) mit
  Zeitverlauf-Graph, optional mit Verlaufs-Interpolation bei bewegten
  Objekten
- Maßstab (reale mm-Größen) und freie Punkt-zu-Punkt-Streckenmessungen
- Schwindungsmessung: Fläche (Kontur-Box) oder Breite (Probenfenster mit
  Referenzfenstern), dazu beliebig viele unabhängige „Probenhöhen“
- Querschnitt-Graphen (horizontale/vertikale Schnitte durch das Bild)
- Rohdaten-Bereinigung: Ausreißer-Bilder erkennen und ausblenden
- Export: Grafik (Bild + Graph), Werte (CSV/JSON/Text), Video/Bildstapel –
  jeweils mit Live-Vorschau
- Undo/Redo, Projekte speichern/laden (`.tvproj`)
- Hell-/Dunkelmodus, Live-Ordner-Überwachung für parallel laufende
  Messungen
- Die Statuszeile unten links nennt immer den nächsten Schritt

## Dokumentation

- [Kurzanleitung](documentation/Kurzanleitung.txt) – typischer Ablauf in
  wenigen Schritten
- [Bedienungsanleitung](documentation/Bedienungsanleitung.txt) – alle
  Funktionen im Detail
- [Tastatur-Shortcuts](documentation/Tastatur-Shortcuts.txt)
- [Bedien-Checkliste](documentation/Checkliste_ThermalViewer_Bedienung.docx)
  (Word; im Release-Zip als PDF)

Die drei Textdateien werden im Release-Zip mitgeliefert, die Checkliste als
PDF: der Release-Workflow wandelt die `.docx` bei jedem Build automatisch um
(nur die `.docx` pflegen, die PDF wird nicht eingecheckt).

## Technik

Der Code nutzt [qtpy](https://github.com/spyder-ide/qtpy) und läuft damit
unter PySide6 (siehe `pyproject.toml`) sowie – für den Windows-7-Build –
unter PyQt5 (siehe `requirements-win7.txt`).

## App-Icon

Fenster-/Taskleisten-Icon und das exe-Icon kommen aus
`thermal_viewer/resources/icon.ico` (mehrere Auflösungen, generiert aus
`documentation/Gemini_Generated_Image_scxf5qscxf5qscxf.png`). Icon
ersetzen: neue `.ico`-Datei an derselben Stelle ablegen (idealerweise
mit mehreren eingebetteten Größen 16–256px).

## Tests

```
uv run pytest
```

Die Test-Suite unter [tests/](tests/) ist das **Build-Freigabe-Gate**: sie
läuft vor jedem exe-Build (lokal über
[scripts/build_local.ps1](scripts/build_local.ps1) und in jedem
GitHub-Actions-Job) und muss vollständig grün sein, sonst entsteht keine exe.

Zusätzlich gibt es einen eigenständigen, headless End-to-End-Smoke-Test
(nicht Teil des Build-Gates, erzeugt sein Testdatenset selbst):

```
uv run python tests/smoke_test.py
```

## Lokal die exe bauen (ohne Release)

```
pwsh -File scripts/build_local.ps1
```

Baut lokal dieselbe `dist/ThermalViewer.exe` wie der CI-Job (inklusive
Test-Gate), ohne Tag oder Release – zum schnellen Prüfen der exe.

## Release bauen

Ein Push eines Tags im Format `vX.Y.Z` (z.B. `v1.0.0`) löst automatisch
[.github/workflows/release.yml](.github/workflows/release.yml) aus und
veröffentlicht drei Varianten als Anhang eines neuen GitHub Release (jeweils
nur bei grünen Tests):

- **`ThermalViewer-vX.Y.Z-windows10-11.zip`** – moderner Stack (PySide6, aktuelles
  Python via uv) für Windows 10/11.
- **`ThermalViewer-vX.Y.Z-windows7-legacy.zip`** – Kompatibilitäts-Build
  (PyQt5 / Qt 5.15 LTS, Python 3.8) für ältere Laborrechner unter Windows 7.
  Dieser Stack ist bewusst alt und **unsupportet** (Python 3.8 und Qt 5.15
  erhalten keine Fixes mehr) – vor dem Rollout auf den Zielrechnern testen.
- **`ThermalViewer-vX.Y.Z-linux.tar.gz`** – moderner Stack (PySide6, aktuelles
  Python via uv) als einzelne ausführbare Binärdatei für x86_64-Linux, gebaut
  auf Ubuntu. Benötigt auf dem Zielsystem die üblichen Qt-Laufzeit-
  bibliotheken (u.a. `libgl1`, `libegl1`, `libxkbcommon0`, die `libxcb-*`-
  Pakete, `libdbus-1-3`, `libfontconfig1` – unter Debian/Ubuntu z.B. per
  `sudo apt install libgl1 libegl1 libxkbcommon0 libdbus-1-3 libfontconfig1
  libxcb-cursor0 libxcb-icccm4 libxcb-image0 libxcb-keysyms1 libxcb-randr0
  libxcb-render-util0 libxcb-shape0 libxcb-xinerama0`
  nachinstallierbar). Vor dem Ausführen `chmod +x ThermalViewer` setzen.

Jede Variante enthält neben der Anwendung die README und die drei Dokumentationsdateien.

```
git tag v1.0.0
git push origin v1.0.0
```

Manueller Build (Windows, ohne Test-Lauf; entspricht `scripts/build_local.ps1`):

```
uv run pyinstaller --noconfirm --clean --windowed --onefile --noupx --name ThermalViewer --icon thermal_viewer/resources/icon.ico --add-data "thermal_viewer/resources/icon.ico;resources" --collect-all imageio_ffmpeg --collect-all imageio run.py
```

Linux: ohne `--windowed`/`--icon`, `;` wird zu `:`. Die `--collect-all`-Flags
bündeln `ffmpeg` für den Video-Export. Das Ergebnis liegt in `dist/`.
Windows-7-Build: `requirements-win7.txt` in eine Python-3.8-Umgebung
installieren und denselben Befehl ohne `uv run` ausführen.
