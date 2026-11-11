# Generación y Distribución de Ejecutable - TdSerial IoT v2.1.0

## 📦 Cómo Generar el Ejecutable

### Opción A: Automática (Recomendado)

Ejecuta el script helper desde el directorio del proyecto:

```bash
build_exe.bat
```

El script hará automáticamente:
1. ✓ Crear/activar entorno virtual
2. ✓ Instalar PyInstaller
3. ✓ Instalar dependencias
4. ✓ Limpiar compilaciones anteriores
5. ✓ Compilar el ejecutable con PyInstaller

**Tiempo estimado:** 2-5 minutos (la primera vez tarda más)

**Resultado:** `dist/TdSerialIoT/TdSerialIoT.exe`

### Opción B: Manual

Si prefieres hacer paso a paso:

```bash
# 1. Crear entorno virtual
python -m venv .venv
.venv\Scripts\activate.bat

# 2. Instalar PyInstaller
pip install pyinstaller

# 3. Instalar dependencias de la aplicación
pip install -r requirements.txt

# 4. Compilar
pyinstaller tdserial_iot.spec --windowed --noconfirm

# 5. Desactivar entorno virtual
.venv\Scripts\deactivate.bat
```

## 📂 Estructura del Ejecutable Generado

```
dist/
└── TdSerialIoT/
    ├── TdSerialIoT.exe          ← Ejecutable principal
    ├── config.json              ← Configuración base
    ├── _internal/               ← Dependencias (PySide6, pyserial, etc.)
    │   ├── PySide6/
    │   ├── serial/
    │   ├── keyring/
    │   └── ...
    └── ... (otros archivos)
```

## 🚀 Distribución a Usuarios

### Método 1: Carpeta ZIP (Simple)

```bash
# Comprimir toda la carpeta
# Usuarios descargan y descomprimen
dist/TdSerialIoT/ → TdSerialIoT_v2.1.0.zip
```

**Ventajas:**
- Simple
- No requiere instalación
- Solo descomprimir y ejecutar

**Pasos para el usuario:**
1. Descargar `TdSerialIoT_v2.1.0.zip`
2. Descomprimir en `C:\Programas\` (o donde prefiera)
3. Ejecutar `TdSerialIoT.exe`

### Método 2: Instalador NSIS (Profesional)

Para crear un instalador `.exe`, se puede usar NSIS:

```bash
# 1. Instalar NSIS (https://nsis.sourceforge.io/)
# 2. Crear script installer.nsi
# 3. Compilar: makensis installer.nsi
```

Esto crearía:
- `TdSerialIoT_v2.1.0_setup.exe` (instalador)
- Acceso directo en Inicio
- Desinstalador

### Método 3: GitHub Releases (Recomendado)

Subir el ejecutable a las **releases de GitHub**:

```bash
# En https://github.com/jbellid/TdSerialIoT/releases
# 1. Crear nuevo release: v2.1.0
# 2. Subir archivo: TdSerialIoT_v2.1.0.zip
# 3. Publicar con notas de cambio
```

**Ventajas:**
- Versioning automático
- Control de descargas
- Fácil de actualizar
- Usuarios descubren versiones nuevas

## 📋 Consideraciones de Seguridad

✅ **Ya implementadas en v2.1.0:**
- ICCID se almacena en Credential Manager (NO en archivos)
- config.json nunca contiene datos sensibles
- Credenciales API en Credential Manager seguro
- Logs redactan información sensible

✅ **Verificación previa a distribuir:**

```bash
# 1. Verificar que config.json NO contiene ICCID
grep "onence_iccid" dist/TdSerialIoT/config.json
# Debería no encontrar nada

# 2. Verificar que no haya credenciales en el ejecutable
strings dist/TdSerialIoT/TdSerialIoT.exe | grep -i "secret\|token\|password"
# Debería no encontrar nada sensible
```

## 🔄 Actualización de Versiones

Para generar ejecutable de nueva versión:

```bash
# 1. Actualizar versión en tdserial_iot/__init__.py
# 2. Hacer commit y crear tag
git tag -a v2.1.1 -m "Version 2.1.1"

# 3. Generar ejecutable
build_exe.bat

# 4. Comprimir
# dist/TdSerialIoT → TdSerialIoT_v2.1.1.zip

# 5. Crear release en GitHub
# https://github.com/jbellid/TdSerialIoT/releases/new
```

## 📊 Tamaño Esperado

| Componente | Tamaño |
|-----------|--------|
| PySide6 (Qt) | ~120 MB |
| Python runtime | ~40 MB |
| Dependencias (pyserial, keyring) | ~5 MB |
| Aplicación + config | ~1 MB |
| **Total** | **~166 MB** |

**Nota:** El tamaño es grande por PySide6/Qt, pero es necesario para la GUI.

## 🐛 Troubleshooting

### Error: "No se encuentra el directorio de plugins de Qt"

**Solución:**
```bash
# Reinstalar dependencias
pip install --force-reinstall PySide6>=6.7
```

### Error: "No se puede ejecutar el programa"

**Verifica:**
- Ejecutar desde PowerShell con permisos de administrador
- Ejecutar directamente el `.exe`, no desde la carpeta

### Error: "ModuleNotFoundError"

**Solución:**
- Agregar el módulo a `hiddenimports` en `tdserial_iot.spec`
- Recompilar

## 📚 Referencias

- **PyInstaller:** https://pyinstaller.org/
- **PySide6:** https://doc.qt.io/qtforpython/
- **NSIS Installer:** https://nsis.sourceforge.io/

## 💡 Comandos Útiles

```bash
# Limpiar caché de compilación
rmdir /s build __pycache__ dist

# Compilar en modo debug (más información)
pyinstaller tdserial_iot.spec --windowed --debug

# Compilar con console (ver errores)
pyinstaller tdserial_iot.spec --console

# Ver lista de dependencias detectadas
pyinstaller --collect-all pyserial tdserial_iot.spec
```
