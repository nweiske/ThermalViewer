"""Referenzpunkte der Rohdaten-Bereinigung per ECHTER Maus-Geste verschieben
(nicht nur ueber den direkt aufgerufenen Handler) -- inkl. grosszuegigem
Greifbereich um das kleine Symbol."""
import pytest
from qtpy import QtCore, QtGui, QtWidgets
from qtpy.QtTest import QTest


def _move_with_left_button_held(viewport, point: QtCore.QPoint) -> None:
    """QTest.mouseMove() meldet unter Qt5 (Win7-Build) KEINE gedrueckte
    Taste mit -- pyqtgraph sieht dann nie eine Drag-Geste. Daher das
    Move-Ereignis mit gehaltener linker Taste selbst senden."""
    event = QtGui.QMouseEvent(
        QtCore.QEvent.MouseMove, QtCore.QPointF(point), QtCore.QPointF(viewport.mapToGlobal(point)),
        QtCore.Qt.NoButton, QtCore.Qt.LeftButton, QtCore.Qt.NoModifier,
    )
    QtWidgets.QApplication.sendEvent(viewport, event)


def _drag_point_by_mouse(window, press_offset_px: int):
    from thermal_viewer.dialogs import DataCleaningDialog

    window._cleaning_points = [(2, 3, "or", True)]
    dlg = DataCleaningDialog(window)
    dlg.show()
    QtWidgets.QApplication.processEvents()
    dlg.refresh_points()
    QtWidgets.QApplication.processEvents()
    try:
        preview = dlg.preview

        def viewport_point(x, y):
            scene_pos = preview.view_box.mapViewToScene(QtCore.QPointF(x, y))
            return preview.glw.mapFromScene(scene_pos)

        start = viewport_point(2.5, 3.5) + QtCore.QPoint(press_offset_px, press_offset_px)
        end = viewport_point(6.5, 5.5)
        viewport = preview.glw.viewport()
        QTest.mousePress(viewport, QtCore.Qt.LeftButton, QtCore.Qt.NoModifier, start)
        for step in range(1, 11):
            _move_with_left_button_held(viewport, QtCore.QPoint(
                start.x() + (end.x() - start.x()) * step // 10,
                start.y() + (end.y() - start.y()) * step // 10,
            ))
            QtWidgets.QApplication.processEvents()
        QTest.mouseRelease(viewport, QtCore.Qt.LeftButton, QtCore.Qt.NoModifier, end)
        QtWidgets.QApplication.processEvents()
        return list(window._cleaning_points)
    finally:
        dlg.close()


@pytest.mark.parametrize("press_offset_px", [0, 9, 12])
def test_cleaning_point_can_be_dragged_with_the_mouse_near_its_symbol(loaded_main_window, press_offset_px):
    points = _drag_point_by_mouse(loaded_main_window, press_offset_px)
    assert points == [(6, 5, "or", True)], points


def test_dragging_far_from_a_cleaning_point_does_not_move_it(loaded_main_window):
    points = _drag_point_by_mouse(loaded_main_window, 40)
    assert points[0] == (2, 3, "or", True), points
