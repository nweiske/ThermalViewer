"""Startet den Thermo-Sequenz-Viewer."""
import sys

from qtpy import QtGui, QtWidgets

from thermal_viewer.assets import ICON_PATH
from thermal_viewer.main_window import MainWindow


def main() -> None:
    app = QtWidgets.QApplication(sys.argv)
    app.setWindowIcon(QtGui.QIcon(str(ICON_PATH)))
    window = MainWindow()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
