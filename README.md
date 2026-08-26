# DataViewer

Thermo-Sequenz-Viewer für gestapelte Thermokamera-CSV-Aufnahmen
(standardmäßig `Record_YYYY-MM-DD_hh-mm-ss.csv`, ';'-getrennt, Dezimalkomma
-- Zahlenfelder in der App akzeptieren beim Tippen sowohl Komma als auch
Punkt als Dezimaltrennzeichen). Passt beim „Ordner öffnen…“ keine Datei zu
diesem Namensschema, fragt die App nach (neuer Ordner / Namensschema
anpassen – mit Live-Vorschau, welche Dateien passen würden / abbrechen);
ein angepasstes Schema lässt sich optional dauerhaft als neuer Standard
speichern.

**Neueste Version / Downloads:** [github.com/nweiske/ThermalViewer](https://github.com/nweiske/ThermalViewer.git)
-- hier liegt immer der aktuelle Stand, inklusive fertiger Releases zum
Herunterladen (siehe „Release bauen“ unten).

Projektverwaltung über [uv](https://docs.astral.sh/uv/):

```
uv run python run.py
```

`uv run` legt beim ersten Aufruf automatisch eine projekteigene, isolierte
`.venv` an und installiert die in `pyproject.toml`/`uv.lock` gepinnten
Abhängigkeiten (PySide6, pyqtgraph, numpy) – kein manuelles
`pip install` nötig. Abhängigkeit hinzufügen: `uv add <paket>`.

## Bedienoberfläche

Links das Thermobild mit Legende (Farbskala + einstellbare/automatische
Min/Max-Limits, mehrere Farbverläufe inkl. Invertierung) und einer Zeitleiste
darunter; am Cursor-Kreuz im Bild wird zusätzlich live die Temperatur des
aktuellen Bildpunkts eingeblendet, die sich sowohl bei Mausbewegung als
auch beim Frame-Wechsel während der Wiedergabe mitaktualisiert.

Rechts andockbare Panels für beliebig viele (standardmäßig 5 – "Oben",
"Links", "Mitte", "Rechts", "Unten" – per "+"-Knopf erweiterbar) frei
rechteckig skalierbare Messbereiche (ROIs, wahlweise mit
Verlaufs-Interpolation über die Zeit zwischen Start- und Ende-Position/
-Größe; während der Erfassung treten alle anderen ROIs verblasst zurück,
damit der bearbeitete im Bild im Fokus bleibt) und deren Temperaturverlauf
über die Zeit sowie einen Live-Verlauf am Mauszeiger (optional zusätzlich
direkt in den Zeitverlauf-Graphen eingeblendet). Jedes ROI ist per
Namensliste (Haken davor: sichtbar/ausgeblendet, Doppelklick: umbenennen)
oder per Klick direkt auf sein Rechteck im Thermobild auswählbar – beides
zeigt es sofort in der Detailansicht rechts an und aktiviert "Messbereich
setzen". Die Beschriftung eines platzierten Messbereichs im Thermobild
zeigt live und rechts neben dem Namen dessen aktuell gemittelte Temperatur
(z.B. "ROI 1: 24,5 °C").

Auswertungsstart und -ende (Standard: erster/letzter Frame, per Feld in
der Zeitleiste oder direkt per Ziehen der grünen/roten Markierung im
Frame-Regler frei verschiebbar) begrenzen sowohl die Wiedergabe (Play
bleibt darauf beschränkt, außer der Cursor wird manuell außerhalb
positioniert) als auch das Ziel von "Start"/"Ende festlegen" bei der
Verlaufs-Interpolation, die ihrerseits linear über den Frame-Index
zwischen diesen beiden Bildern interpoliert.

Beide Kurven-Graphen (Zeitverlauf/Live) zeigen unten rechts je einen
Knopf "Achsen zurücksetzen" (setzt Zoom/Verschieben auf den vollen
Datenbereich zurück) und "Achsen einstellen…" (X-Achsen-Wertebereich
sowie Y-Achsen-Wertebereich UND -Schrittweite wahlweise automatisch oder
manuell festlegen) sowie einen Umschalter zwischen echter Uhrzeit und
relativer Laufzeit (HH:MM:SS), synchron für beide Graphen. Alle Panels
sind in der Breite verstellbar (Docking-System). Jedes ROI hat eine frei
wählbare Farbe (Klick auf das Farbfeld).

## Export

Über das Menü „Export“ gibt es drei Wege, jeweils EIN Fenster, das selbst
abfragt, was konkret exportiert werden soll:

- **„Grafik exportieren…“** – Thermobild (mit Position der Messbereiche/
  des Cursors) zusammen mit dem Temperaturverlauf, wahlweise Zeitverlauf
  und/oder Live-Cursor-Kurve, kombiniert oder getrennt als
  PNG/JPEG/BMP/TIFF/WebP/SVG mit wählbarer DPI, eng zugeschnitten ohne
  Leerraum zwischen Bild und Achse/Farbskala, optional mit eigenem
  Farbverlauf/eigener Skalierung nur für diesen Export, eigener Zeitachse
  (Uhrzeit/Laufzeit/Beide gleichzeitig als Doppelachse) und
  Cursor-Position im Bild (Standard: aus). Ein Rechtsklick direkt auf den
  Zeitverlauf- oder Live-Graphen bietet "Grafik speichern…" als Abkürzung
  für exakt denselben Export; ein Rechtsklick auf das Thermobild selbst
  exportiert (ohne Menü-Entsprechung) nur dieses eine Bild. Es entsteht
  zusätzlich eine gleichnamige `.json`-Datei mit Metadaten (ROI-Koordinaten,
  Farben, Zeitstempel aller Frames, Quellordner, DPI, Bildgröße).
- **„Werte exportieren…“** – die reinen Messwerte als CSV (';'-getrennt),
  JSON oder Text (Tab-getrennt) -- wahlweise Messbereiche und/oder
  Live-Cursor in EINER Datei, mit Spaltennamen frei editierbar und mit
  Pixel- UND/ODER mm-Größe befüllbar (live beim Anklicken von „px“/„mm“);
  ist der Live-Cursor mit dabei, stehen dessen (über die Aufnahme
  konstante) Pixel-Koordinaten als eigene Spalten mit in der Datei.
- **„Video / Bildstapel exportieren…“** – ein wählbarer Frame-Bereich
  (vorbelegt mit Auswertungsstart/-ende) entweder als MP4-, AVI- oder
  WebM-Video, oder als Bildstapel (eine Bilddatei pro Frame in einem
  gewählten Zielordner, Format PNG/JPEG/BMP/TIFF/WebP, mit frei wählbarem
  Dateiname-Präfix). Der Präfix versteht dieselben Zeitstempel-Platzhalter
  wie das Namensschema beim Laden (YYYY/MM/DD/hh/mm/ss) und wird pro Frame
  mit dessen echtem Zeitstempel gefüllt, z.B. ergibt der Präfix
  `Frame_YYYY-MM-DD_hh-mm-ss_` die Dateien `Frame_2026-01-01_12-00-00_1.png`,
  `Frame_2026-01-01_12-00-01_2.png`, … (der Frame-Index folgt dabei ohne
  automatisches Trennzeichen direkt auf den Präfix – ein „_“ davor tippt
  man selbst mit ein). Beide Ausgabeformen unterstützen optional denselben
  Kurven-Graphen samt wandernder Zeit-Markierung wie im Hauptfenster (frei
  positionierbar über/unter/links/rechts vom Thermobild), Cursor-Position
  und eine einblendbare Zeitachse unten im Bild: Laufzeit (mit
  tatsächlicher Position des Ausschnitts innerhalb der Gesamtaufnahme)/
  Zeitstempel/Beides/Keine (Standard: Beides). Die Haupt-UI bleibt während
  des Renderns unverändert (keine sichtbaren Linien-/Schriftgrößen-Sprünge).
  Der Video-Export (nicht der Bildstapel-Export) benötigt intern
  `imageio`/`imageio-ffmpeg`, das in den fertigen Release-Binärdateien
  bereits mitgebündelt ist.

Über „Werkzeuge > Maßstab festlegen…“ lässt sich eine Referenzstrecke im
Bild in mm definieren, um Messbereichsgrößen zusätzlich real (in mm)
anzuzeigen; die Endpunkte der Referenzlinie lassen sich danach per Maus
nachträglich verschieben (die reale Länge bleibt dabei fest), und ein
Doppelklick auf die Linie/Beschriftung öffnet einen Dialog, um die reale
Länge direkt zu ändern (die Endpunkte bleiben dabei fest). Über
„Werkzeuge > Live-Cursor-Bereichsgröße“ lässt sich der Live-Verlauf statt
eines Einzelpixels auf den Mittelwert eines 3×3/5×5/7×7/10×10-Blocks um
den Cursor umstellen. Nach „Datei > Ordner öffnen…“ überwacht die App
automatisch im Hintergrund diesen Ordner und lädt alle 10 Sekunden neu
abgelegte CSV-Dateien nach, um parallel zu einer laufenden Messung nutzbar
zu sein. Native Qt-Dialogtexte (z.B. „Abbrechen“/„OK“) werden beim Start
über Qts eigene deutsche Übersetzung eingedeutscht.

Über „Ansicht > Dunkelmodus“ (ein einzelner an-/abwählbarer Menüpunkt)
lässt sich zwischen hellem und dunklem Erscheinungsbild wechseln – gilt
einheitlich für die gesamte Oberfläche inklusive Thermobild und
Kurven-Graphen; die Wahl wird gespeichert und beim nächsten Start
wiederhergestellt.

Über „Datei > Projekt speichern…/laden…“ lassen sich Messbereiche,
Farbverlauf, Legenden-Limits und der Quellordner der Messreihe in einer
`.tvproj`-Datei gesichert und auf eine andere Sitzung derselben (oder
einer kompatiblen) Messreihe angewendet werden – „Projekt laden…“ ohne
bereits geladene Messreihe lädt dafür automatisch den gespeicherten
Quellordner mit (falls noch vorhanden), ohne Umweg über „Ordner öffnen…“.
Frame-Navigation per Tastatur: Pfeiltasten (±1 Frame), Bild-Auf/-Ab
(±10 Frames), Pos1/Ende (erster/letzter Frame), Leertaste (Play/Pause) –
vollständige Liste in [documentation/Tastatur-Shortcuts.txt](documentation/Tastatur-Shortcuts.txt).
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

## Tests

```
uv run pytest
```

Die Test-Suite unter [tests/](tests/) ist das **Build-Freigabe-Gate**: sie
läuft automatisch vor jedem exe-Build – lokal über
[scripts/build_local.ps1](scripts/build_local.ps1) UND in jedem der drei
GitHub-Actions-Jobs (siehe unten) – und muss vollständig durchlaufen,
bevor PyInstaller überhaupt gestartet wird. Schlägt (egal wo) auch nur
ein Test fehl, bricht der Build an dieser Stelle ab: keine neue exe.

## Lokal die exe bauen (ohne Release)

```
pwsh -File scripts/build_local.ps1
```

Baut auf dem eigenen Rechner dieselbe `dist/ThermalViewer.exe` wie der
`build-windows`-Job in CI (siehe unten) – inklusive vorherigem Test-Gate –,
ohne dass dafür ein Tag gepusht oder ein echtes GitHub Release erzeugt
werden muss. Gedacht zum schnellen lokalen Prüfen, ob/wie sich die UI
verhält bzw. ob die exe überhaupt startet.

## Release bauen

Ein Push eines Tags im Format `vX.Y.Z` (z.B. `v1.0.0`) löst automatisch
[.github/workflows/release.yml](.github/workflows/release.yml) aus und
veröffentlicht drei Varianten als Anhang eines neuen GitHub Release
(unter [github.com/nweiske/ThermalViewer](https://github.com/nweiske/ThermalViewer.git)) –
jeder der drei Jobs führt dabei zuerst die Test-Suite aus (siehe oben) und
baut nur bei vollständig grünen Tests tatsächlich eine exe:

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

Alle drei Varianten enthalten neben der Anwendung auch `README.md` sowie
`documentation/Bedienungsanleitung.txt` und
`documentation/Tastatur-Shortcuts.txt`.

```
git tag v1.0.0
git push origin v1.0.0
```

Lokal denselben modernen Build erzeugen (Windows) – für den Windows-Fall
identisch zu [scripts/build_local.ps1](scripts/build_local.ps1) (siehe
oben), nur ohne den vorgeschalteten Test-Lauf:

```
uv run pyinstaller --noconfirm --clean --windowed --onefile --name ThermalViewer --icon thermal_viewer/resources/icon.ico --add-data "thermal_viewer/resources/icon.ico;resources" --collect-all imageio_ffmpeg --collect-all imageio run.py
```

Unter Linux entsprechend (ohne `--windowed`/`--icon`, `;` wird zu `:`):

```
uv run pyinstaller --noconfirm --clean --onefile --name ThermalViewer --add-data "thermal_viewer/resources/icon.ico:resources" --collect-all imageio_ffmpeg --collect-all imageio run.py
```

`--collect-all imageio_ffmpeg --collect-all imageio` bündelt die von
`imageio-ffmpeg` zur Laufzeit nachgeladene `ffmpeg`-Binärdatei mit ein –
ohne das schlägt der Video-Export (nicht der Bildstapel-Export) auf einem
frischen Zielrechner ohne lokal installiertes `imageio` fehl.

Die fertige exe liegt danach in `dist/ThermalViewer.exe`. Für den
Windows-7-Build lokal `requirements-win7.txt` in eine Python-3.8-Umgebung
installieren und denselben `pyinstaller`-Befehl dort (ohne `uv run`
davor und ohne die beiden `--collect-all`-Flags, siehe
`requirements-win7.txt`) ausführen.
