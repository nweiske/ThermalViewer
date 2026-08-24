# DataViewer

Thermo-Sequenz-Viewer für gestapelte Thermokamera-CSV-Aufnahmen
(standardmäßig `Record_YYYY-MM-DD_HH-MM-SS.csv`, ';'-getrennt, Dezimalkomma).
Passt beim „Ordner öffnen…“ keine Datei zu diesem Namensschema, fragt die
App nach (neuer Ordner / Namensschema anpassen – mit Live-Vorschau, welche
Dateien passen würden / abbrechen); ein angepasstes Schema lässt sich
optional dauerhaft als neuer Standard speichern.

Projektverwaltung über [uv](https://docs.astral.sh/uv/):

```
uv run python run.py
```

`uv run` legt beim ersten Aufruf automatisch eine projekteigene, isolierte
`.venv` an und installiert die in `pyproject.toml`/`uv.lock` gepinnten
Abhängigkeiten (PySide6, pyqtgraph, numpy) – kein manuelles
`pip install` nötig. Abhängigkeit hinzufügen: `uv add <paket>`.

Links das Thermobild mit Legende (Farbskala + einstellbare/automatische
Min/Max-Limits, mehrere Farbverläufe inkl. Invertierung) und einer Zeitleiste
darunter; am Cursor-Kreuz im Bild wird zusätzlich live die Temperatur des
aktuellen Bildpunkts eingeblendet, die sich sowohl bei Mausbewegung als
auch beim Frame-Wechsel während der Wiedergabe mitaktualisiert. Rechts
andockbare Panels für beliebig viele (standardmäßig 5 – "Oben", "Links",
"Mitte", "Rechts", "Unten" – per "+"-Knopf erweiterbar) frei rechteckig
skalierbare Messbereiche (ROIs, wahlweise mit Verlaufs-Interpolation über
die Zeit zwischen Start- und Ende-Position/-Größe; während der Erfassung
treten alle anderen ROIs verblasst zurück, damit der bearbeitete im Bild
im Fokus bleibt) und deren Temperaturverlauf über die Zeit sowie einen
Live-Verlauf am Mauszeiger. Jedes ROI ist per Namensliste (Haken davor:
sichtbar/ausgeblendet, Doppelklick: umbenennen) oder per Klick direkt auf
sein Rechteck im Thermobild auswählbar – beides zeigt es sofort in der
Detailansicht rechts an und aktiviert "Messbereich setzen". Auswertungsstart
und -ende (Standard: erster/letzter Frame, per Feld in der Zeitleiste oder
direkt per Ziehen der grünen/roten Markierung im Frame-Regler frei
verschiebbar) begrenzen sowohl die Wiedergabe (Play bleibt darauf
beschränkt, außer der Cursor wird manuell außerhalb positioniert) als auch
das Ziel von "Start"/"Ende festlegen" bei der Verlaufs-Interpolation, die
ihrerseits linear über den Frame-Index zwischen diesen beiden Bildern
interpoliert; die Zeitachse beider Graphen ist unten rechts wahlweise auf
echte Uhrzeit oder relative Laufzeit (HH:MM:SS) umschaltbar. Alle Panels sind in der
Breite verstellbar (Docking-System). Jedes ROI hat eine frei wählbare
Farbe (Klick auf das Farbfeld). Über das Menü „Export“ lassen sich
Grafiken (Thermobild + Kurve, kombiniert oder getrennt, als
PNG/JPEG/BMP/TIFF/WebP/SVG mit wählbarer DPI, eng zugeschnitten ohne
Leerraum zwischen Bild und Achse/Farbskala, optional mit eigenem
Farbverlauf/eigener Skalierung nur für diesen Export, eigener
Zeitachse (Uhrzeit/Laufzeit/Beide gleichzeitig als Doppelachse) und Cursor-Position
im Bild – Standard: aus), die reinen Messwerte als
CSV (Spaltennamen frei mit Pixel- UND/ODER mm-Größe befüllbar, live beim
Anklicken von „px“/„mm“) sowie
ein wählbarer Frame-Bereich als MP4-Video (vorbelegt mit Auswertungsstart/
-ende, optional mit demselben Kurven-Graphen samt wandernder Zeit-Markierung
wie im Hauptfenster – frei positionierbar über/unter/links/rechts vom
Thermobild –, mit Cursor-Position und mit
einblendbarer Laufzeit-Anzeige unten im Video: Zeitleiste (mit tatsächlicher
Position des Ausschnitts innerhalb der Gesamtaufnahme)/Zeitstempel/Beides,
Standard: Beides). Die Haupt-UI bleibt während des Renderns unverändert
(keine sichtbaren Linien-/Schriftgrößen-Sprünge mehr). Ein
Rechtsklick direkt auf den Zeitverlauf- oder Live-Graphen bietet
"Grafik speichern…" als Abkürzung für exakt denselben Export wie der
jeweilige Menüpunkt; ein Rechtsklick auf das Thermobild selbst exportiert
(ohne Menü-Entsprechung) nur dieses eine Bild. Bei der Grafik entsteht
zusätzlich eine gleichnamige
`.json`-Datei mit Metadaten (ROI-Koordinaten, Farben, Zeitstempel aller
Frames, Quellordner, DPI, Bildgröße). Über „Werkzeuge > Maßstab
festlegen…“ lässt sich eine Referenzstrecke im Bild in mm definieren, um
Messbereichsgrößen zusätzlich real (in mm) anzuzeigen; über „Werkzeuge >
Live-Cursor-Bereichsgröße“ lässt sich der Live-Verlauf statt eines
Einzelpixels auf den Mittelwert eines 3×3/5×5/7×7-Blocks um den
Cursor umstellen. Nach „Datei >
Ordner öffnen…“ überwacht die App automatisch im Hintergrund diesen
Ordner und lädt alle 10 Sekunden neu abgelegte CSV-Dateien nach, um
parallel zu einer laufenden Messung nutzbar zu sein. Native Qt-Dialogtexte
(z.B. „Abbrechen“/„OK“) werden beim Start über Qts eigene deutsche
Übersetzung eingedeutscht.

Über „Ansicht > Design“ lässt sich zwischen hellem und dunklem Farbschema
wechseln; die Wahl wird gespeichert und beim nächsten Start wiederhergestellt.

Über „Datei > Projekt speichern…/laden…“ lassen sich Messbereiche,
Farbverlauf und Legenden-Limits in einer `.tvproj`-Datei sichern und auf
eine andere Sitzung derselben (oder einer kompatiblen) Messreihe anwenden.
Frame-Navigation per Tastatur: Pfeiltasten (±1 Frame), Bild-Auf/-Ab
(±10 Frames), Pos1/Ende (erster/letzter Frame), Leertaste (Play/Pause).
Beim Laden werden kaputte/unlesbare Dateien oder Frames mit abweichender
Auflösung einzeln übersprungen (mit Warnung) statt den gesamten Ladevorgang
abzubrechen.

Der Code ist über [qtpy](https://github.com/spyder-ide/qtpy) von der
konkreten Qt-Anbindung entkoppelt: lokal läuft er unter PySide6 (siehe
`pyproject.toml`), für den Windows-7-Release-Build unter PyQt5 (siehe
`requirements-win7.txt`) – ohne Code-Änderung.

Ausführliche Bedienungsanleitung (reine Textdatei, ohne Zusatzsoftware
lesbar): [documentation/Bedienungsanleitung.txt](documentation/Bedienungsanleitung.txt).
Separate Kurzreferenz aller Tastatur-Shortcuts:
[documentation/Tastatur-Shortcuts.txt](documentation/Tastatur-Shortcuts.txt).
Beide Dateien werden im Release-Zip mitausgeliefert (siehe unten).

## App-Icon

Fenster-/Taskleisten-Icon und das exe-Icon kommen aus
`thermal_viewer/resources/icon.ico` (mehrere Auflösungen, generiert aus
`documentation/Gemini_Generated_Image_scxf5qscxf5qscxf.png`). Icon
ersetzen: neue `.ico`-Datei an derselben Stelle ablegen (idealerweise
mit mehreren eingebetteten Größen 16–256px).

## Release bauen

Ein Push eines Tags im Format `vX.Y.Z` (z.B. `v1.0.0`) löst automatisch
[.github/workflows/release.yml](.github/workflows/release.yml) aus und
veröffentlicht drei Varianten als Anhang eines neuen GitHub Release:

- **`ThermalViewer-vX.Y.Z-windows10-11.zip`** – moderner Stack (PySide6, aktuelles
  Python via uv) für Windows 10/11.
- **`ThermalViewer-vX.Y.Z-windows7-legacy.zip`** – Kompatibilitäts-Build
  (PyQt5 / Qt 5.15 LTS, Python 3.8) für ältere Laborrechner unter Windows 7.
  PySide6/Qt6 unterstützt Windows 7 grundsätzlich nicht (nie, unabhängig von
  der Version), und CPython selbst nur noch bis Python 3.8 – dieser Stack
  ist daher bewusst alt und **unsupportet** (Python 3.8 ist seit 10/2024 EOL,
  Qt 5.15 Open-Source erhält keine Fixes mehr). Vor dem Rollout auf den
  Ziel-Laborrechnern testen.
- **`ThermalViewer-vX.Y.Z-linux.tar.gz`** – moderner Stack (PySide6, aktuelles
  Python via uv) als einzelne ausführbare Binärdatei für x86_64-Linux, gebaut
  auf Ubuntu. Benötigt auf dem Zielsystem die üblichen Qt-Laufzeit-
  bibliotheken (u.a. `libgl1`, `libegl1`, `libxkbcommon0`, die `libxcb-*`-
  Pakete, `libdbus-1-3`, `libfontconfig1` – unter Debian/Ubuntu z.B. per
  `sudo apt install libgl1 libegl1 libxkbcommon0 libdbus-1-3 libfontconfig1`
  nachinstallierbar). Vor dem Ausführen `chmod +x ThermalViewer` setzen.

```
git tag v1.0.0
git push origin v1.0.0
```

Lokal denselben modernen Build erzeugen (Windows):

```
uv run pyinstaller --noconfirm --clean --windowed --onefile --name ThermalViewer --icon thermal_viewer/resources/icon.ico --add-data "thermal_viewer/resources/icon.ico;resources" run.py
```

Unter Linux entsprechend (ohne `--windowed`/`--icon`, `;` wird zu `:`):

```
uv run pyinstaller --noconfirm --clean --onefile --name ThermalViewer --add-data "thermal_viewer/resources/icon.ico:resources" run.py
```

Die fertige exe liegt danach in `dist/ThermalViewer.exe`. Für den
Windows-7-Build lokal `requirements-win7.txt` in eine Python-3.8-Umgebung
installieren und denselben `pyinstaller`-Befehl dort (ohne `uv run`
davor) ausführen.
