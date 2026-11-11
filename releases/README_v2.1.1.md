aplicación# TdSerial IoT v2.1.1 - Ejecutable para Windows

## 📦 Contenido del Archivo

Este archivo contiene la aplicación TdSerial IoT completamente preparada para ejecutar en Windows, sin necesidad de instalar Python.

## 🚀 Instalación

1. **Descargar y descomprimir**
   - Descargar el archivo ZIP
   - Descomprimir en una ubicación de tu preferencia (ej: C:\Programas\)

2. **Ejecutar la aplicación**
   - Navegar a la carpeta descomprimida
   - Hacer doble clic en: **TdSerialIoT.exe**
   - La aplicación se iniciará automáticamente

## 📋 Requisitos

- **Sistema Operativo**: Windows 10 o superior (64-bit)
- **Memoria RAM**: Mínimo 512 MB (recomendado 2 GB)
- **Espacio en disco**: ~200 MB
- **Conectividad**: Puerto serial USB disponible (para dispositivos 1NCE)

## ✨ Características v2.1.1

- ✅ Almacenamiento seguro de credenciales en Windows Credential Manager
- ✅ ICCID del dispositivo guardado de forma segura (NO en archivos de configuración)
- ✅ Interfaz gráfica intuitiva con PySide6/Qt
- ✅ Comunicación serial con dispositivos IoT
- ✅ Integración con plataforma 1NCE OS

## 🔒 Seguridad

**Importante**: A partir de v2.1.1, todas las credenciales sensibles se almacenan de forma segura:
- Las credenciales de API se guardan en **Windows Credential Manager** (encriptadas)
- El ICCID del dispositivo se almacena de forma segura (NO en config.json)
- Los archivos de configuración JSON nunca contienen datos sensibles

## 🐛 Solución de Problemas

### "El programa no se inicia"
- Descargar e instalar: .NET Framework 4.8+ (si no lo tienes)
- Intentar ejecutar como administrador
- Verificar que tienes permisos de lectura/escritura en la carpeta

### "No reconoce el puerto serial"
- Conectar el dispositivo USB
- Verificar que Windows reconoce el puerto (Administrador de dispositivos → Puertos COM)
- Reiniciar la aplicación

### "Error de credenciales"
- Abrir: Control Panel → User Accounts → Credential Manager
- Verificar que tienes las credenciales de 1NCE OS guardadas
- Eliminar credenciales antiguas si existen

## 📚 Documentación

Para más información sobre cómo usar la aplicación:
- Ver archivo: SECURITY_IMPROVEMENTS.md
- Visitar repositorio: https://github.com/jbellid/TDSerialIoT

## 💬 Soporte

¿Problemas? Contactar a través del repositorio de GitHub:
https://github.com/jbellid/TDSerialIoT/issues

## 📄 Licencia

Ver archivo LICENSE en el repositorio

---

**Versión**: 2.1.0
**Fecha de Publicación**: 2026-11-11
**Actualización de Seguridad**: Sí
