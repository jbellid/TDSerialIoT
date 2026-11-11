# -*- mode: python ; coding: utf-8 -*-
"""
PyInstaller spec file for TdSerial IoT application.
Build with: pyinstaller tdserial_iot.spec
"""

block_cipher = None

a = Analysis(
    ['tdserial_iot/main.py'],
    pathex=[],
    binaries=[],
    datas=[
        ('tdserial_iot', 'tdserial_iot'),
        ('config.json', '.'),
        ('TdSerialIoT.ico', '.'),  # ✓ Incluir el icono
    ],
    hiddenimports=[
        'PySide6.QtCore',
        'PySide6.QtGui',
        'PySide6.QtWidgets',
        'PySide6.QtSerialPort',
        'serial',
        'keyring',
        'keyring.backends.windows',
        'logging',
        'json',
    ],
    hookspath=[],
    hooksconfig={
        'PySide6': {
            'collect_all': True,
        }
    },
    runtime_hooks=[],
    excludedimports=[],
    win_no_prefer_redirects=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name='TdSerialIoT',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,  # Sin ventana de consola
    disable_windowed_traceback=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon='TdSerialIoT.ico',  # Opcional: si tienes un icono
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='TdSerialIoT'
)
