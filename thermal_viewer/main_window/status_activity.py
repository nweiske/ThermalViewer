"""Persistente Aktivitäts-/Hinweisleiste unten links im Fenster: zeigt
laufende Hintergrundvorgänge (mit Fortschrittsbalken) an, und -- solange
nichts läuft -- einen kurzen Hinweis, was als Nächstes zu tun ist.

Bewusst GETRENNT von self.statusBar().showMessage(): dessen Text wird an
über 20 Stellen im Programm für kurzlebige Einzelmeldungen genutzt (z.B.
"Frame X geladen", "Export abgebrochen") und würde einen dauerhaften
Hinweis nach spätestens der nächsten solchen Meldung wieder überschreiben.
Diese Leiste hier ist ein PERMANENTER Statusleisten-Bereich (addPermanentWidget),
bleibt also unabhängig von showMessage()-Aufrufen immer sichtbar."""
from __future__ import annotations

from qtpy import QtWidgets


class _StatusActivityMixin:
    def _build_status_activity(self) -> None:
        container = QtWidgets.QWidget()
        layout = QtWidgets.QHBoxLayout(container)
        layout.setContentsMargins(0, 0, 6, 0)
        layout.setSpacing(6)

        self._activity_label = QtWidgets.QLabel()
        layout.addWidget(self._activity_label)

        self._activity_progress = QtWidgets.QProgressBar()
        self._activity_progress.setFixedHeight(14)
        self._activity_progress.setFixedWidth(160)
        # QProgressBar ist per Default horizontal "Expanding" -- wuerde ohne
        # dies trotz setFixedWidth() vom Layout als dehnbar behandelt und
        # damit den gesamten uebrigen Platz DER STATUSLEISTE fuer sich
        # beanspruchen, statt direkt neben dem Text zu bleiben.
        self._activity_progress.setSizePolicy(QtWidgets.QSizePolicy.Fixed, QtWidgets.QSizePolicy.Fixed)
        self._activity_progress.setRange(0, 1000)
        self._activity_progress.setTextVisible(False)
        self._activity_progress.setVisible(False)
        layout.addWidget(self._activity_progress)
        layout.addStretch(1)

        # stretch=1: nimmt den ueblicherweise ungenutzten Platz der
        # Statusleiste ein, statt auf Minimalgroesse zusammengequetscht zu
        # werden -- bleibt (im Unterschied zu addWidget()) auch waehrend
        # einer per showMessage() angezeigten Einzelmeldung sichtbar.
        self.statusBar().addPermanentWidget(container, 1)
        # Sicherheitsnetz: fast jede Zustandsaenderung (Werkzeug armiert/
        # abgebrochen, Ergebnis berechnet, ...) loest eine Statuszeilen-
        # Meldung aus (oder laesst sie auslaufen) -- der "Was ist jetzt zu
        # tun"-Hinweis zieht dann automatisch nach.
        # Nicht waehrend eines laufenden Vorgangs mit Fortschrittsanzeige
        # (siehe _set_activity_progress) -- sonst wuerde jede Einzelmeldung
        # bzw. jedes Auslaufen einer solchen den Fortschritt wegraeumen.
        self.statusBar().messageChanged.connect(self._on_status_message_changed)
        self._refresh_idle_guidance()

    def _set_activity_progress(self, text: str, fraction: float | None = None) -> None:
        """Zeigt einen laufenden Hintergrundvorgang an -- fraction (0.0-1.0)
        blendet den Fortschrittsbalken ein, None zeigt nur den Text (z.B.
        waehrend eines Schritts ohne bekannte Gesamtzahl). Gegenstueck:
        _refresh_idle_guidance(), sobald der Vorgang fertig ist."""
        self._activity_busy = True
        self._activity_label.setText(text)
        if fraction is None:
            self._activity_progress.setVisible(False)
            return
        self._activity_progress.setVisible(True)
        self._activity_progress.setValue(round(max(0.0, min(1.0, fraction)) * 1000))

    def _on_status_message_changed(self, _message: str) -> None:
        if not getattr(self, "_activity_busy", False) and not getattr(self, "_guidance_refresh_suppressed", False):
            self._refresh_idle_guidance()

    def _refresh_idle_guidance(self) -> None:
        """Kein Vorgang laeuft gerade -- zeigt statt eines Fortschritts
        IMMER einen kurzen Hinweis, was als Naechstes zu tun ist (Nutzer-
        wunsch). Der Text ergibt sich ausschliesslich aus dem aktuellen
        Programmzustand (siehe _guidance_text); aufgerufen nach jeder
        Zustandsaenderung, die ihn aendern kann -- zusaetzlich als
        Sicherheitsnetz bei JEDER Statuszeilen-Meldung (messageChanged, siehe
        _build_status_activity), da fast jede Zustandsaenderung eine solche
        Meldung ausloest."""
        if getattr(self, "_activity_label", None) is None:
            return  # UI-Aufbau noch nicht bis hierher gekommen
        self._activity_busy = False
        self._activity_progress.setVisible(False)
        text = self._guidance_text()
        if self._activity_label.text() != text:
            self._activity_label.setText(text)

    def _guidance_text(self) -> str:
        live_note = ""
        if self._watched_folder is not None and self._live_watch_timer.isActive():
            live_note = "  |  ● Live-Überwachung aktiv"
        if self.recording is None:
            return "Kein Ordner geladen — Datei > Ordner öffnen…, um zu starten."

        # 1. Ein Platzier-/Mess-Werkzeug wartet auf Klicks (hoechste Prioritaet).
        if self._crosssection_entry_armed_direction is not None:
            direction = "horizontale" if self._crosssection_entry_armed_direction == "horizontal" else "vertikale"
            return (
                f"Querschnitt-Modus: Klick ins Bild setzt eine {direction} Linie — "
                f"Knopf erneut klicken = beenden.{live_note}"
            )
        if self._ruler_armed:
            if self._ruler_start is None:
                return f"Maßstab: Startpunkt der Referenzlinie im Bild anklicken (Rechtsklick = abbrechen).{live_note}"
            return f"Maßstab: jetzt den Endpunkt der Referenzlinie anklicken (Rechtsklick = abbrechen).{live_note}"
        if self._measurement_armed:
            if self._measurement_start is None:
                return (
                    "Messmodus: Startpunkt der Strecke anklicken — beliebig viele Messungen, "
                    f"Knopf erneut klicken = beenden.{live_note}"
                )
            return f"Messmodus: jetzt den Endpunkt der Strecke anklicken (Rechtsklick = abbrechen).{live_note}"
        if self._sample_height_armed:
            return (
                "Probenhöhen-Modus: Klick ins Bild setzt eine Probenhöhe auf die angeklickte Zeile — "
                f"Knopf erneut klicken = beenden.{live_note}"
            )
        capturing = next((e for e in self.roi_entries if e.interp_arm_start or e.interp_arm_end), None)
        if capturing is not None:
            which = "Start" if capturing.interp_arm_start else "Ende"
            return (
                f"„{capturing.name}“: Messbereich für den {which} im Bild positionieren, dann erneut auf den "
                f"„…übernehmen“-Knopf klicken.{live_note}"
            )
        if self._armed_entry is not None:
            return (
                f"„{self._armed_entry.name}“ bereit — ins Bild klicken, um den Messbereich dort zu setzen "
                f"(erneut „Messbereich setzen“ klicken = abbrechen).{live_note}"
            )

        # 2. Hinweis passend zum aktiven Ebenen-Tab.
        tab = self._active_layer_tab
        if tab == "shrinkage":
            if not self._shrinkage_enabled:
                return f"Schwindungsmessung: „Aktivieren“ anhaken (oder „+ Probenhöhe“ nutzen).{live_note}"
            if self._shrinkage_result is None:
                if self._shrinkage_mode == "width":
                    return (
                        "Schwindung (Breite): Probenfenster über die Probenbreite ziehen, je ein Referenzfenster "
                        f"links/rechts auf Hintergrund + Probentisch setzen, dann „Berechnen“.{live_note}"
                    )
                return (
                    "Schwindung (Fläche): Box mit etwas Rand über die ganze Probe ziehen, "
                    f"dann „Berechnen“ klicken.{live_note}"
                )
            return (
                "Schwindung berechnet — Kenngröße/Messart wählbar, „+ Probenhöhe“ ergänzt Breiten an "
                f"einzelnen Zeilen, Export über das Datei-Menü.{live_note}"
            )
        if tab == "scale":
            if self._px_to_mm is None:
                return (
                    "Maßstab: „Festlegen…“ klicken und eine Strecke mit bekannter Länge im Bild "
                    f"anklicken (Start, Ende).{live_note}"
                )
            return (
                f"Maßstab gesetzt (1 px ≈ {self._format_de(self._px_to_mm, 4)} mm) — mit dem Messmodus "
                f"Strecken messen oder den Maßstab neu festlegen.{live_note}"
            )
        if tab == "querschnitt":
            return (
                "Querschnitte: „+ Horizontal“/„+ Vertikal“ klicken und ins Bild klicken — "
                f"oder mit der Maus über das Bild fahren.{live_note}"
            )

        # 3. Temperatur-Messung / Alle.
        selected = self._selected_roi_entry()
        if not any(e.placed for e in self.roi_entries):
            # VOR dem Live-Ueberwachung-Hinweis (nicht danach): die
            # Live-Ueberwachung startet nach "Ordner öffnen…" IMMER sofort
            # automatisch (siehe _load_folder), noch BEVOR der Nutzer einen
            # Messbereich platziert hat -- der Platzieren-Hinweis wuerde sonst
            # in der Praxis nie sichtbar.
            return (
                f"{self.recording.n_frames} Frame(s) geladen — jetzt einen Messbereich platzieren "
                f"(Taste 1–9/0 oder Liste im Panel rechts, dann ins Bild klicken).{live_note}"
            )
        if selected is not None and selected.placed and self._roi_selected_by_user:
            return (
                f"„{selected.name}“ ausgewählt — in der Mitte ziehen = verschieben, Ecken/Kanten ziehen = "
                f"skalieren; „Messbereich setzen“ (oder Klick in die Liste) = neu platzieren.{live_note}"
            )
        return (
            "Weiter: Messbereiche ziehen/skalieren, Maßstab setzen, Schwindung messen oder "
            f"über das Datei-Menü exportieren.{live_note}"
        )
