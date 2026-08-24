"""Startet den Thermo-Sequenz-Viewer."""
import sys

from qtpy import QtCore, QtGui, QtWidgets

from thermal_viewer.assets import ICON_PATH
from thermal_viewer.main_window import MainWindow


def _install_german_translations(app: QtWidgets.QApplication) -> None:
    """Laedt Qts eigene deutsche Uebersetzungen fuer eingebaute Texte
    (Standard-Knopfbeschriftungen wie "Abbrechen"/"OK", native Datei-/
    Farbauswahl-Dialoge) -- ohne diese bleiben solche Texte englisch, obwohl
    die App selbst durchgehend deutsch beschriftet ist (Bugreport: Knopf
    "Abbrechen" zeigte "Cancel"). "qtbase" deckt Qt6 (PySide6) ab, "qt"
    zusaetzlich fuer den Windows-7-Legacy-Build (PyQt5/Qt5, siehe
    requirements-win7.txt) -- beide werden versucht, ein fehlendes/nicht
    ladbares Sprachpaket bricht den Start bewusst NICHT ab (dann bleiben die
    betroffenen Texte einfach englisch statt die App am Start zu hindern)."""
    # QLibraryInfo.path()/LibraryPath (Qt6/PySide6) vs. das aeltere
    # .location()/QLibraryLocation (Qt5/PyQt5, siehe requirements-win7.txt)
    # -- beide Varianten abgefragt, da der Win7-Legacy-Build unter PyQt5
    # laeuft und qtpy diesen Unterschied nicht durchgehend angleicht.
    info = QtCore.QLibraryInfo
    if hasattr(info, "path") and hasattr(info, "LibraryPath"):
        translations_path = info.path(info.LibraryPath.TranslationsPath)
    else:
        translations_path = info.location(info.LibraryLocation.TranslationsPath)
    locale = QtCore.QLocale("de_DE")
    for name in ("qtbase", "qt"):
        translator = QtCore.QTranslator(app)
        if translator.load(locale, name, "_", translations_path):
            app.installTranslator(translator)


def main() -> None:
    app = QtWidgets.QApplication(sys.argv)
    _install_german_translations(app)
    app.setWindowIcon(QtGui.QIcon(str(ICON_PATH)))
    window = MainWindow()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
