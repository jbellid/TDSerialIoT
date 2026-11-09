@echo off
setlocal
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
    echo No se encontro el entorno virtual .venv.
    echo Creandolo con la version de Python disponible...
    py -3 -m venv .venv
    if errorlevel 1 (
        echo No se pudo crear el entorno virtual. Instala Python 3.12 o superior.
        pause
        exit /b 1
    )
    ".venv\Scripts\python.exe" -m pip install -r requirements.txt
    if errorlevel 1 (
        echo No se pudieron instalar las dependencias.
        pause
        exit /b 1
    )
)
set "PYSIDE6_PLUGIN_DIR=%CD%\.venv\Lib\site-packages\PySide6\plugins"
if not exist "%PYSIDE6_PLUGIN_DIR%\platforms" (
    echo No se encontro el directorio de plugins de Qt de PySide6:
    echo "%PYSIDE6_PLUGIN_DIR%\platforms"
    echo Reinstala las dependencias ejecutando:
    echo ".venv\Scripts\python.exe" -m pip install -r requirements.txt
    pause
    exit /b 1
)
set "QT_PLUGIN_PATH=%PYSIDE6_PLUGIN_DIR%"
set "QT_QPA_PLATFORM_PLUGIN_PATH=%PYSIDE6_PLUGIN_DIR%\platforms"
".venv\Scripts\python.exe" -m tdserial_iot.main
if errorlevel 1 pause
