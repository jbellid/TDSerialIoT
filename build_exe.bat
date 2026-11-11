@echo off
REM Script to build TdSerial IoT executable with PyInstaller
REM Usage: build_exe.bat

echo.
echo ╔════════════════════════════════════════════════════════════╗
echo ║   BUILDING TdSerial IoT v2.1.0 EXECUTABLE                 ║
echo ╚════════════════════════════════════════════════════════════╝
echo.

REM Verificar si estamos en el directorio correcto
if not exist "tdserial_iot\main.py" (
    echo Error: No se encontro tdserial_iot\main.py
    echo Ejecuta este script desde la raiz del proyecto.
    pause
    exit /b 1
)

REM Crear/activar entorno virtual
if not exist ".venv\Scripts\python.exe" (
    echo Creando entorno virtual...
    py -3 -m venv .venv
    if errorlevel 1 (
        echo Error al crear el entorno virtual.
        pause
        exit /b 1
    )
)

REM Activar entorno virtual
call .venv\Scripts\activate.bat
if errorlevel 1 (
    echo Error al activar el entorno virtual.
    pause
    exit /b 1
)

echo.
echo Step 1: Instalando PyInstaller...
python -m pip install -q pyinstaller
if errorlevel 1 (
    echo Error al instalar PyInstaller
    pause
    exit /b 1
)

echo Step 1: OK ✓
echo.

REM Instalar dependencias de la aplicacion
echo Step 2: Instalando dependencias de la aplicacion...
python -m pip install -q -r requirements.txt
if errorlevel 1 (
    echo Error al instalar las dependencias
    pause
    exit /b 1
)

echo Step 2: OK ✓
echo.

REM Limpiar compilaciones anteriores
echo Step 3: Limpiando compilaciones anteriores...
if exist "build" rmdir /s /q build >nul 2>&1
if exist "dist" rmdir /s /q dist >nul 2>&1
if exist "__pycache__" rmdir /s /q __pycache__ >nul 2>&1
for /d %%D in (tdserial_iot\__pycache__) do rmdir /s /q "%%D" >nul 2>&1

echo Step 3: OK ✓
echo.

REM Compilar con PyInstaller
echo Step 4: Compilando ejecutable con PyInstaller...
echo   (Esto puede tomar 2-5 minutos la primera vez)
echo.

pyinstaller tdserial_iot.spec --windowed --noconfirm
if errorlevel 1 (
    echo Error al compilar con PyInstaller
    pause
    exit /b 1
)

echo.
echo Step 4: OK ✓
echo.

REM Mensaje final
echo.
echo ╔════════════════════════════════════════════════════════════╗
echo ║           ✓ COMPILACION COMPLETADA EXITOSAMENTE          ║
echo ╚════════════════════════════════════════════════════════════╝
echo.

echo El ejecutable se encuentra en:
echo   dist\TdSerialIoT\TdSerialIoT.exe
echo.

echo Proximos pasos:
echo   1. Prueba el ejecutable:
echo      dist\TdSerialIoT\TdSerialIoT.exe
echo.
echo   2. Para distribuir, comprime la carpeta:
echo      dist\TdSerialIoT -> TdSerialIoT_v2.1.0.zip
echo.
echo   3. Los usuarios pueden descargar y ejecutar directamente
echo      sin necesidad de instalar Python.
echo.

REM Desactivar entorno virtual
call .venv\Scripts\deactivate.bat

pause
