#!/bin/bash
# QUICK START GUIDE - TdSerial IoT v2.1.0 Distribution

## ⚡ PASOS RÁPIDOS PARA DISTRIBUIR

### OPCIÓN 1: GitHub Releases (RECOMENDADO)

```bash
# Ir a: https://github.com/jbellid/TDSerialIoT/releases/new

# 1. Crear nuevo release
Tag: v2.1.0
Title: TdSerial IoT v2.1.0 - Secure Distribution Release

# 2. Descripción (copiar y personalizar)
## 🎉 TdSerial IoT v2.1.0

### 🔐 Security Release
- Secure ICCID storage in Windows Credential Manager
- Removed all secrets from config files
- Git history cleaned and verified
- Ready for public distribution

### ✨ What's New
- Standalone Windows executable (no Python required)
- All dependencies bundled (PySide6, pyserial, keyring)
- Improved credential management
- Better documentation

### 📦 Installation
Download `TdSerialIoT_v2.1.0_Windows.zip`, extract, and run `TdSerialIoT.exe`

### 📋 Requirements
- Windows 10 or higher (64-bit)
- USB serial port available
- No Python installation needed

# 3. Subir archivo
Attach binaries: releases/TdSerialIoT_v2.1.0_Windows.zip

# 4. Publicar
Click "Publish release"
```

### OPCIÓN 2: Compartir ZIP Directo

```bash
# Compartir archivo:
# releases/TdSerialIoT_v2.1.0_Windows.zip (16.1 MB)

# Usuarios:
# 1. Descargar ZIP
# 2. Descomprimir
# 3. Ejecutar: TdSerialIoT.exe
```

---

## 🧪 PRUEBAS ANTES DE DISTRIBUIR

```bash
# 1. Verificar seguridad
grep -i "iccid" dist/TdSerialIoT/config.json
# Resultado esperado: (nada, vacío)

# 2. Ejecutar la aplicación
dist/TdSerialIoT/TdSerialIoT.exe
# Resultado esperado: Se abre la interfaz gráfica

# 3. Verificar funcionalidad básica
# - Conectar dispositivo USB
# - Ingresar ICCID
# - Verificar que se guarda en Credential Manager (NO en config.json)
# - Ingresar credenciales API
# - Conectar a 1NCE OS

# 4. Verificar git history limpio
git log --all --oneline | grep -i iccid
# Resultado esperado: (nada, vacío)
```

---

## 📋 CHECKLIST DE DISTRIBUCIÓN

- [ ] Ejecutable probado en máquina limpia (sin Python)
- [ ] Credenciales no aparecen en logs
- [ ] ICCID se guarda en Credential Manager
- [ ] config.json no contiene secrets
- [ ] Git history limpiado
- [ ] Documentación completa (README, BUILD_EXE_GUIDE, etc.)
- [ ] ZIP creado y comprimido
- [ ] GitHub Release creado (si aplica)
- [ ] Usuarios notificados de disponibilidad

---

## 🔄 PARA FUTURAS COMPILACIONES

```bash
# Simplemente ejecutar:
build_exe.bat

# El script:
# 1. Crea/activa entorno virtual
# 2. Instala dependencias
# 3. Limpia compilaciones anteriores
# 4. Compila con PyInstaller
# 5. Genera: dist/TdSerialIoT/TdSerialIoT.exe

# Luego comprimir:
# dist/TdSerialIoT/ -> TdSerialIoT_v[VERSION]_Windows.zip
```

---

## 📁 ESTRUCTURA DE DISTRIBUCIÓN

```
TdSerialIoT_v2.1.0_Windows.zip
└── TdSerialIoT/
    ├── TdSerialIoT.exe          ← EJECUTAR ESTO
    ├── config.json              ← Configuración base (sin secrets)
    ├── _internal/               ← Dependencias (no tocar)
    │   ├── PySide6/
    │   ├── serial/
    │   ├── keyring/
    │   └── ...
    └── [otros archivos]
```

---

## ⚠️ IMPORTANTE - NOTAS DE SEGURIDAD

✅ **SEGURO PARA DISTRIBUIR** - v2.1.0 incluye:

- ✅ ICCID almacenado en Windows Credential Manager (encriptado)
- ✅ config.json 100% libre de secrets
- ✅ Historial de git limpiado y verificado
- ✅ Logs redactan información sensible
- ✅ No requiere archivos de configuración manual

❌ **NUNCA HACER**:

- ❌ Compartir archivos .env o config.json con secrets
- ❌ Hacer push de commits con credentials
- ❌ Hardcodear contraseñas en el código
- ❌ Cometer credenciales al repositorio

---

## 🎯 HACER REPOSITORIO PÚBLICO

```
1. Ir a: https://github.com/jbellid/TDSerialIoT/settings
2. Bajar a: "Danger Zone"
3. Cambiar a "Public"
4. Confirmar
```

**Verificar antes**:
- ✅ Repositorio sin secrets
- ✅ .gitignore incluye *.env, config.json, etc.
- ✅ Documentación completa
- ✅ README actualizado
- ✅ LICENCE presente

---

## 📞 SOPORTE DESPUÉS DE PUBLICAR

Usuarios pueden reportar problemas en:
- GitHub Issues: https://github.com/jbellid/TDSerialIoT/issues

Responder con:
- Versión utilizada
- Sistema operativo
- Pasos para reproducir
- Capturas de pantalla (si aplica)
- Archivo de logs (redactado)

---

**Versión**: 2.1.0  
**Estado**: ✅ Listo para Producción  
**Última Actualización**: 2025-01-02
