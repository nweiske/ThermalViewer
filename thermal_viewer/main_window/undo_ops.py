"""Rückgängig/Wiederholen (Nutzerwunsch: "voller, mehrstufiger Undo/Redo-
Verlauf" über die ganze Sitzung) -- SNAPSHOT-basiert statt eines Command-
Musters mit einzeln invertierbaren Aktionen: der Undo-Stack speichert volle
Zustands-Snapshots in genau der Form, die project_io.py::
_build_project_state_dict() ohnehin schon für "Projekt speichern…" baut
(ROIs, Messungen, Bereinigung, Schwindung, Anzeige-Einstellungen) -- das
deckt exakt den Zustand ab, der als "Projekt" gilt, und project_io.py::
_restore_project_state_dict() kann einen solchen Snapshot bereits dialogfrei
auf die laufende UI anwenden (dieselben Methoden, die auch "Projekt
laden…" nutzt, siehe dort).

Ablauf: JEDE mutierende Aktion (ROI/Messung/Bereinigungspunkt hinzufügen/
entfernen/verschieben, Farbe ändern, Box ziehen, ...) ruft VOR ihrer eigenen
Änderung _push_undo_snapshot() auf -- das haengt den AKTUELLEN Zustand
(also den Stand VOR der gleich folgenden Aenderung) an den Undo-Stack an.
Strg+Z (_on_undo) legt den jetzigen Zustand auf den Redo-Stack, holt den
letzten Undo-Snapshot zurueck und wendet ihn an; Strg+Y/Strg+Umschalt+Z
(_on_redo) macht das Gegenstueck.

Zwei Sonderfaelle, die eine einzelne Nutzer-GESTE (nicht ein einzelnes Qt-
Signal) als EINEN Undo-Schritt behandeln muessen:

- Ziehen (ROI-/Schwindungs-Boxen, Maßstab-/Messungs-Linien, Beschriftungen):
  pg.ROI/LineSegmentROI feuern sigRegionChangeStarted GENAU einmal beim
  Beginn einer echten Maus-Interaktion (NIE bei einem programmgesteuerten
  setPos()/setSize(), siehe roi.py/pyqtgraph-Quelle) -- direkt an
  _push_undo_snapshot() angeschlossen, kein Gruppieren noetig.
- Tippen (Spinboxen/Umbenennen-Feld): QAbstractSpinBox/QLineEdit feuern
  valueChanged/textChanged bei JEDEM Tastendruck. _begin_grouped_undo_edit()
  pusht nur beim ERSTEN Tastendruck seit dem letzten "fertig" (editingFinished)
  einen Snapshot, alle weiteren Tastendruecke derselben Eingabe-Sitzung
  bleiben ununterbrochen -- EIN einzelnes Flag (self._active_undo_edit)
  genuegt, da diese UI nie zwei sich ueberlappende Zieh-/Tipp-Gesten
  gleichzeitig zulaesst (nur eine Maus).

Re-Entranz-Schutz (self._restoring_undo_snapshot): project_io.py::
_load_project_rois setzt u.a. Checkboxen/den Listeneintrag OHNE
blockSignals -- aendert sich ihr Wert beim Wiederherstellen tatsaechlich,
feuern sie ganz normal ihre Handler, die (nach Punkt 4 unten) selbst wieder
_push_undo_snapshot() aufrufen wuerden. Ohne dieses Flag wuerde ein einzelnes
Strg+Z mitten im eigenen Wiederherstellen neue Snapshots erzeugen und beide
Stacks verderben."""
from __future__ import annotations

# Sicherheitsgrenze gegen unbegrenztes Wachstum bei sehr langen Sitzungen
# (nicht aus Performance-Gruenden -- ein einzelner Snapshot ist selbst bei
# vielen ROIs/Messungen/Bereinigungspunkten sub-Megabyte-klein, siehe
# project_io.py::_build_project_state_dict).
_UNDO_STACK_MAX = 100


class _UndoMixin:
    def _push_undo_snapshot(self) -> None:
        """VOR jeder mutierenden Aktion aufzurufen -- haengt den Zustand
        VOR der gleich folgenden Aenderung an den Undo-Stack an. No-op ohne
        geladene Aufnahme (nichts Sinnvolles zu snapshotten) oder waehrend
        _restore_project_state_dict() selbst laeuft (Re-Entranz-Schutz,
        siehe Modul-Docstring)."""
        if self.recording is None or self._restoring_undo_snapshot:
            return
        self._undo_stack.append(self._build_project_state_dict())
        if len(self._undo_stack) > _UNDO_STACK_MAX:
            del self._undo_stack[0]
        # Jede NEUE Aktion macht eine zuvor per Undo "verworfene" Zukunft
        # endgueltig ungueltig -- Standard-Undo/Redo-Verhalten.
        self._redo_stack.clear()
        self._update_undo_redo_actions()

    def _on_undo(self) -> None:
        if not self._undo_stack or self.recording is None:
            return
        self._redo_stack.append(self._build_project_state_dict())
        snapshot = self._undo_stack.pop()
        self._restoring_undo_snapshot = True
        try:
            self._restore_project_state_dict(snapshot)
        finally:
            self._restoring_undo_snapshot = False
        self._update_undo_redo_actions()
        self.statusBar().showMessage("Rückgängig.", 3000)

    def _on_redo(self) -> None:
        if not self._redo_stack or self.recording is None:
            return
        self._undo_stack.append(self._build_project_state_dict())
        snapshot = self._redo_stack.pop()
        self._restoring_undo_snapshot = True
        try:
            self._restore_project_state_dict(snapshot)
        finally:
            self._restoring_undo_snapshot = False
        self._update_undo_redo_actions()
        self.statusBar().showMessage("Wiederholt.", 3000)

    def _begin_grouped_undo_edit(self) -> None:
        """An den ANFANG jedes Handlers stellen, der auf ein bei jedem
        Tastendruck feuerndes Signal reagiert (Spinbox valueChanged,
        Umbenennen-Feld textChanged) -- pusht einen Snapshot nur beim
        ERSTEN Tastendruck seit dem letzten _end_grouped_undo_edit(), siehe
        Modul-Docstring."""
        if self._active_undo_edit:
            return
        self._active_undo_edit = True
        self._push_undo_snapshot()

    def _end_grouped_undo_edit(self) -> None:
        """An editingFinished (Spinbox/QLineEdit) anschliessen -- beendet
        die per _begin_grouped_undo_edit() begonnene Eingabe-Sitzung, damit
        die NAECHSTE Aenderung wieder einen frischen Snapshot pusht."""
        self._active_undo_edit = False

    def _clear_undo_history(self) -> None:
        """Beim Laden einer (weiteren) Aufnahme oder eines Projekts ueber
        den regulaeren Datei-Dialog (NICHT ueber _restore_project_state_dict
        selbst) -- der referenzierte Zustand wird dort ohnehin komplett
        verworfen/ersetzt, eine History darueber hinweg waere sinnlos.
        Setzt auch _active_undo_edit zurueck: eine noch offene Eingabe-
        Sitzung (siehe _begin_grouped_undo_edit) bezog sich auf die ALTE
        Aufnahme -- ohne diesen Reset wuerde die naechste Aktion auf der
        NEUEN Aufnahme faelschlich als Fortsetzung derselben Sitzung
        behandelt und keinen eigenen Snapshot mehr pushen."""
        self._undo_stack.clear()
        self._redo_stack.clear()
        self._active_undo_edit = False
        self._update_undo_redo_actions()

    def _update_undo_redo_actions(self) -> None:
        self.act_undo.setEnabled(bool(self._undo_stack))
        self.act_redo.setEnabled(bool(self._redo_stack))
