"""Regressionstests der Werkzeug-Audit-Runde (ROI-Platzieren, Maßstab, Messungen,
Querschnitte): gegenseitiger Ausschluss und Armier-Zustand."""
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


def test_removing_armed_roi_does_not_arm_a_neighbour(loaded_main_window):
    mw = loaded_main_window
    mw._select_roi(mw.roi_entries[2])
    assert mw._armed_entry is mw.roi_entries[2]
    mw._remove_roi_entry(mw.roi_entries[2])
    assert mw._armed_entry is None
    assert not any(e.btn_place.isChecked() for e in mw.roi_entries)
    # Die Listenauswahl rutscht nach, der Stack folgt ihr trotzdem.
    selected = mw._selected_roi_entry()
    assert selected is not None
    assert mw.roi_stack.currentWidget() is selected.tab_widget


def test_removing_unarmed_roi_keeps_other_armed_state(loaded_main_window):
    mw = loaded_main_window
    entry = mw.roi_entries[0]
    entry.btn_place.setChecked(True)
    assert mw._armed_entry is entry
    mw._remove_roi_entry(mw.roi_entries[3])
    assert mw._armed_entry is entry
    assert entry.btn_place.isChecked()


def test_removing_current_row_while_other_selected_keeps_user_click_arming(loaded_main_window):
    # Nach dem Entfernen muss ein echter Klick in die Liste wieder armieren
    # (Unterdrueckung darf nicht haengen bleiben).
    mw = loaded_main_window
    mw._remove_roi_entry(mw.roi_entries[1])
    assert mw._roi_list_arming_suppressed is False
    mw._select_roi(mw.roi_entries[2])
    assert mw._armed_entry is mw.roi_entries[2]


def test_switching_crosssection_direction_keeps_new_button_checked(loaded_main_window):
    mw = loaded_main_window
    mw.btn_add_crosssection_horizontal.setChecked(True)
    assert mw._crosssection_entry_armed_direction == "horizontal"
    mw.btn_add_crosssection_vertical.setChecked(True)
    assert mw._crosssection_entry_armed_direction == "vertical"
    assert mw.btn_add_crosssection_vertical.isChecked()
    assert not mw.btn_add_crosssection_horizontal.isChecked()
    mw.btn_add_crosssection_vertical.setChecked(False)
    assert mw._crosssection_entry_armed_direction is None


def test_roi_arming_cancels_crosssection_tool(loaded_main_window):
    mw = loaded_main_window
    mw.btn_add_crosssection_horizontal.setChecked(True)
    assert mw._crosssection_entry_armed_direction == "horizontal"
    mw._on_arm_roi_shortcut(1)
    assert mw._armed_entry is mw.roi_entries[0]
    assert mw._crosssection_entry_armed_direction is None
    assert not mw.btn_add_crosssection_horizontal.isChecked()


def test_interp_capture_of_other_side_discards_open_capture(loaded_main_window):
    mw = loaded_main_window
    entry = mw.roi_entries[0]
    entry.chk_interp.setChecked(True)
    mw._on_roi_interp_capture(entry, True)
    assert entry.interp_arm_start
    mw._on_roi_interp_capture(entry, False)
    assert entry.interp_arm_end
    assert not entry.interp_arm_start
    assert "übernehmen" not in entry.btn_interp_start.text()
    assert "übernehmen" in entry.btn_interp_end.text()


def test_cleared_ruler_start_is_dropped_with_scale(loaded_main_window):
    mw = loaded_main_window
    mw._start_ruler_tool()
    mw._ruler_start = (1.0, 1.0)
    mw._clear_ruler_scale()
    assert mw._ruler_start is None
    assert mw._ruler_armed is False


def test_measurements_reappear_after_scale_is_set_again(loaded_main_window, monkeypatch):
    import thermal_viewer.main_window.measurement_ops as measurement_ops_mod

    class _Dialog:
        def __init__(self, *args, **kwargs):
            pass

        def exec(self):
            return QtWidgets.QDialog.DialogCode.Accepted

        def mm_value(self):
            return 10.0

    monkeypatch.setattr(measurement_ops_mod, "RulerLengthDialog", _Dialog)
    mw = loaded_main_window
    mw._set_active_layer_tab("scale")
    p1 = mw.view_box.mapViewToScene(QtCore.QPointF(2, 2))
    p2 = mw.view_box.mapViewToScene(QtCore.QPointF(12, 2))

    mw._px_to_mm = 0.5
    entry = mw._create_measurement((1.0, 1.0), (6.0, 1.0), 5.0)
    assert entry.line.isVisible()

    mw._clear_ruler_scale()
    assert not entry.line.isVisible()

    mw._start_ruler_tool()
    mw._handle_ruler_click(_FakeEvent(p1))
    mw._handle_ruler_click(_FakeEvent(p2))
    assert mw._px_to_mm is not None
    assert entry.line.isVisible()
    assert entry.text.isVisible()
