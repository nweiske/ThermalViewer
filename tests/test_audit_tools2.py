"""Regressionstests der zweiten Werkzeug-Audit-Runde (ROI-Platzieren, Maßstab,
Messungen, Querschnitte)."""
from __future__ import annotations

from qtpy import QtCore, QtWidgets


class _FakeEvent:
    def __init__(self, scene_pos, button=QtCore.Qt.LeftButton):
        self._pos = scene_pos
        self._button = button

    def button(self):
        return self._button

    def scenePos(self):
        return self._pos

    def double(self):
        return False


class _Dialog:
    result = QtWidgets.QDialog.DialogCode.Rejected

    def __init__(self, *args, **kwargs):
        pass

    def exec(self):
        return self.result

    def mm_value(self):
        return 10.0


def _identity_view_box(mw):
    mw.view_box.sceneBoundingRect = lambda: QtCore.QRectF(-1000, -1000, 2000, 2000)
    mw.view_box.mapSceneToView = lambda pos: pos


def test_cancelled_ruler_dialog_hides_the_preview_line(loaded_main_window, monkeypatch):
    import thermal_viewer.main_window.measurement_ops as measurement_ops_mod

    monkeypatch.setattr(measurement_ops_mod, "RulerLengthDialog", _Dialog)
    mw = loaded_main_window
    p1 = mw.view_box.mapViewToScene(QtCore.QPointF(2, 2))
    p2 = mw.view_box.mapViewToScene(QtCore.QPointF(12, 2))
    mw._start_ruler_tool()
    mw._handle_ruler_click(_FakeEvent(p1))
    mw._handle_ruler_click(_FakeEvent(p2))
    assert not mw._ruler_preview_marker.isVisible()
    assert mw._px_to_mm is None


def test_restarting_ruler_midway_drops_the_stale_start_marker(loaded_main_window):
    mw = loaded_main_window
    p1 = mw.view_box.mapViewToScene(QtCore.QPointF(2, 2))
    mw._start_ruler_tool()
    mw._handle_ruler_click(_FakeEvent(p1))
    assert mw._ruler_preview_marker.isVisible()
    mw._start_ruler_tool()
    assert not mw._ruler_preview_marker.isVisible()
    mw._cancel_ruler_tool()
    assert not mw._ruler_preview_marker.isVisible()


def test_measurements_stay_hidden_without_scale_across_tab_switches(loaded_main_window):
    mw = loaded_main_window
    mw._set_active_layer_tab("scale")
    mw._px_to_mm = 0.5
    entry = mw._create_measurement((1.0, 1.0), (6.0, 1.0), 5.0)
    mw._clear_ruler_scale()
    assert not entry.line.isVisible()
    mw._set_active_layer_tab("roi")
    mw._set_active_layer_tab("scale")
    assert not entry.line.isVisible()
    assert not entry.text.isVisible()
    mw.chk_scale_visible.setChecked(False)
    mw.chk_scale_visible.setChecked(True)
    assert not entry.line.isVisible()


def test_roi_shortcut_switches_to_roi_layer_tab(loaded_main_window):
    mw = loaded_main_window
    mw._set_active_layer_tab("scale")
    mw._on_arm_roi_shortcut(2)
    assert mw._armed_entry is mw.roi_entries[1]
    assert mw._active_layer_tab == "roi"


def test_clicking_another_roi_in_image_selects_without_moving_it(loaded_main_window):
    # Der Bildklick, der einen ANDEREN Messbereich auswaehlt/armiert, darf
    # denselben Messbereich nicht im selben Atemzug unter den Mauszeiger
    # versetzen (sigClicked des ROI und Szenenklick bekommen dasselbe Event).
    mw = loaded_main_window
    _identity_view_box(mw)
    a, b = mw.roi_entries[0], mw.roi_entries[1]
    a.place(4, 4, 4, 4)
    b.place(14, 14, 6, 6)
    mw._select_roi(a)
    mw._disarm_roi_placement()
    event = _FakeEvent(QtCore.QPointF(16, 15))
    mw._on_roi_clicked_in_image(b, b.roi, event)
    assert mw._armed_entry is b
    mw._on_scene_mouse_clicked(event)
    assert b.center() == (14.0, 14.0)
    assert mw._armed_entry is b, "Messbereich bleibt zum Platzieren armiert"
    # Der NAECHSTE Klick platziert wie gewohnt.
    mw._on_scene_mouse_clicked(_FakeEvent(QtCore.QPointF(8, 9)))
    assert b.center() == (8.0, 9.0)
    assert mw._armed_entry is None


def test_add_roi_button_respects_max_roi_count(loaded_main_window, monkeypatch):
    import thermal_viewer.main_window.roi_ops as roi_ops_mod

    shown = []
    monkeypatch.setattr(
        roi_ops_mod.QtWidgets.QMessageBox, "information", lambda *a, **k: shown.append(a)
    )
    mw = loaded_main_window
    mw._roi_next_number = roi_ops_mod.MAX_ROI_COUNT + 1
    count = len(mw.roi_entries)
    mw._on_add_roi_clicked()
    assert len(mw.roi_entries) == count
    assert shown


def test_roi_name_with_ampersand_survives_in_group_box_title(loaded_main_window):
    mw = loaded_main_window
    entry = mw.roi_entries[0]
    entry.list_item.setText("Rand & Mitte")
    assert entry.name == "Rand & Mitte"
    assert entry.tab_widget.title() == "Rand && Mitte"


def test_crosssection_line_snaps_to_clamped_pixel_after_drag(loaded_main_window):
    mw = loaded_main_window
    mw._add_crosssection_entry("horizontal", 5)
    entry = mw._crosssection_entries[0]
    rows, _cols = mw.recording.shape
    entry.line.setPos(rows + 7.3)
    mw._on_crosssection_entry_line_dragged(entry, entry.line)
    assert entry.position == rows - 1
    assert entry.line.value() == rows - 1 + 0.5


def test_crosssection_line_drag_is_undone_in_one_step(loaded_main_window):
    # sigPositionChanged setzt entry.position live; der Snapshot muss daher
    # beim ersten Zwischenschritt (VOR der Aenderung) entstehen, nicht erst
    # beim Loslassen.
    mw = loaded_main_window
    mw._add_crosssection_entry("horizontal", 5)
    entry = mw._crosssection_entries[0]
    undo_depth = len(mw._undo_stack)
    entry.line.setPos(8.5)  # feuert sigPositionChanged wie ein Maus-Zug
    entry.line.setPos(9.5)
    assert entry.position == 9
    entry.line.sigPositionChangeFinished.emit(entry.line)
    assert len(mw._undo_stack) == undo_depth + 1
    mw._on_undo()
    assert mw._crosssection_entries[0].position == 5


def test_roi_visibility_checkbox_click_is_undoable(loaded_main_window):
    mw = loaded_main_window
    entry = mw.roi_entries[0]
    entry.place(5, 5, 4, 4)
    item = entry.list_item
    mw._on_roi_list_item_pressed(item)  # Mausdruck auf das Haekchen
    item.setCheckState(QtCore.Qt.CheckState.Unchecked)
    assert not entry.is_visible_checked()
    assert not entry.roi.isVisible()
    mw._on_undo()
    restored = next(e for e in mw.roi_entries if e.number == entry.number)
    assert restored.is_visible_checked()
    mw._on_redo()
    restored = next(e for e in mw.roi_entries if e.number == entry.number)
    assert not restored.is_visible_checked()
