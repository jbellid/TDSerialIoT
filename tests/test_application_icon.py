from pathlib import Path

from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QApplication

import tdserial_iot
import tdserial_iot.main as main


def test_icon_path_uses_package_location_when_entry_point_is_frozen(monkeypatch, tmp_path):
    bundle = tmp_path / "_internal"
    monkeypatch.setattr(main, "__file__", str(bundle / "main.py"))
    monkeypatch.setattr(tdserial_iot, "__file__", str(bundle / "tdserial_iot" / "__init__.py"))

    assert main.application_icon_path() == bundle / "TdSerialIoT.ico"


def test_source_icon_is_loadable(monkeypatch):
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    app = QApplication.instance() or QApplication([])
    icon_path = main.application_icon_path()

    assert icon_path == Path(tdserial_iot.__file__).resolve().parent.parent / "TdSerialIoT.ico"
    assert not QIcon(str(icon_path)).isNull()
    assert app is QApplication.instance()
