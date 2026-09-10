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
        self._refresh_idle_guidance()

    def _set_activity_progress(self, text: str, fraction: float | None = None) -> None:
        """Zeigt einen laufenden Hintergrundvorgang an -- fraction (0.0-1.0)
        blendet den Fortschrittsbalken ein, None zeigt nur den Text (z.B.
        waehrend eines Schritts ohne bekannte Gesamtzahl). Gegenstueck:
        _refresh_idle_guidance(), sobald der Vorgang fertig ist."""
        self._activity_label.setText(text)
        if fraction is None:
            self._activity_progress.setVisible(False)
            return
        self._activity_progress.setVisible(True)
        self._activity_progress.setValue(round(max(0.0, min(1.0, fraction)) * 1000))

    def _refresh_idle_guidance(self) -> None:
        """Kein Vorgang laeuft gerade -- zeigt statt eines Fortschritts einen
        kurzen Hinweis, was als Naechstes zu tun ist (bzw. dass die Live-
        Ordner-Ueberwachung im Hintergrund aktiv ist, siehe
        _check_for_new_files). Aufgerufen nach jeder Zustandsaenderung, die
        den passenden Hinweis aendern kann (siehe Aufrufer: _set_recording,
        ROI-Platzierung, Live-Ordner-Ueberwachung start/stop)."""
        self._activity_progress.setVisible(False)
        if self.recording is None:
            self._activity_label.setText("Kein Ordner geladen — Datei > Ordner öffnen…, um zu starten.")
        elif not any(e.placed for e in self.roi_entries):
            # VOR dem Live-Ueberwachung-Hinweis pruefen (nicht danach): die
            # Live-Ueberwachung startet nach "Ordner öffnen…" IMMER sofort
            # automatisch (siehe _load_folder), noch BEVOR der Nutzer einen
            # Messbereich platziert hat -- mit vertauschter Reihenfolge waere
            # dieser Hinweis (der eigentliche Kern des Nutzerwunschs: "was
            # User als naechstes machen muss") in der Praxis nie sichtbar,
            # weil er von der (fast immer gleichzeitig zutreffenden)
            # Live-Ueberwachung-Meldung dauerhaft verdeckt wuerde.
            self._activity_label.setText(
                f"{self.recording.n_frames} Frame(s) geladen — jetzt einen Messbereich platzieren (Panel rechts)."
            )
        elif self._watched_folder is not None and self._live_watch_timer.isActive():
            self._activity_label.setText(f"● Live-Überwachung aktiv: „{self._watched_folder.name}“ auf neue Dateien.")
        else:
            self._activity_label.setText("Bereit.")
