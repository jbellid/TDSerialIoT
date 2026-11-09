"""Application entry point."""

from __future__ import annotations

import os
import sys
from pathlib import Path

import PySide6
from PySide6.QtWidgets import QApplication

from tdserial_iot.logging_setup import configure_logging
from tdserial_iot.main_window import MainWindow


def configure_qt_plugin_paths() -> None:
    """Prefer the Qt plugins shipped with this PySide6 installation."""
    plugin_dir = Path(PySide6.__file__).resolve().parent / "plugins"
    platform_plugin_dir = plugin_dir / "platforms"
    if not platform_plugin_dir.is_dir():
        raise RuntimeError(
            "No se encontró la carpeta de plugins de Qt de PySide6: "
            f"{platform_plugin_dir}. Reinstala las dependencias con "
            "\".venv\\Scripts\\python.exe -m pip install -r requirements.txt\"."
        )
    os.environ["QT_PLUGIN_PATH"] = str(plugin_dir)
    os.environ["QT_QPA_PLATFORM_PLUGIN_PATH"] = str(platform_plugin_dir)


def main() -> int:
    configure_logging()
    configure_qt_plugin_paths()
    application = QApplication(sys.argv)
    application.setApplicationName("TdSerial IoT")
    window = MainWindow()
    window.show()
    return application.exec()


if __name__ == "__main__":
    raise SystemExit(main())
