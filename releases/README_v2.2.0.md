# TdSerial IoT v2.2.0 - Ejecutable para Windows

## 📦 Contenido del Archivo

La distribución de esta versión contiene la aplicación preparada para ejecutar en Windows, sin necesidad de instalar Python. Conserva toda la carpeta descomprimida, incluidas sus dependencias.

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
- **Conectividad**: Internet para la API de 1NCE; puerto serial USB para las funciones serie

## ✨ Características v2.2.0

- ✅ Almacenamiento seguro de credenciales en Windows Credential Manager
- ✅ ICCID del dispositivo guardado de forma segura (NO en archivos de configuración)
- ✅ Interfaz gráfica intuitiva con PySide6/Qt
- ✅ Comunicación serial con dispositivos IoT
- ✅ Integración con plataforma 1NCE OS
- ✅ Correlación de ACK de Historian con comandos de Device Controller al consultar Historian
- ✅ Estado de ejecución y fecha del ACK guardados en el historial local, independientes del estado de la API

## Correlación de comandos

Envía el comando en **1NCE Device Controller** y consulta Historian en **1NCE Cloud** para el mismo dispositivo y periodo. Cada página recuperada se contrasta con el historial local por identificador de dispositivo, ID de comando y nombre de comando. La correlación no genera consultas adicionales.

El historial indica **Aceptado por el dispositivo**, **Ejecutado según dispositivo** o **Correlación ambigua**, y muestra la fecha del ACK. **Sin confirmar** no significa que el comando haya fallado. Una respuesta HTTP 202 o `SUCCEEDED` tampoco prueba que se haya ejecutado.

El historial muestra y exporta las fechas sin desplazamiento horario, pero conserva las fechas originales en la base de datos. Si Historian no proporciona zona horaria, el orden temporal no se puede comprobar con precisión. La fecha corresponde al registro del ACK en Historian, no necesariamente al instante exacto de ejecución. `accepted` indica aceptación de una tarea diferida, no su finalización.

## 🔒 Seguridad

**Importante**: Las credenciales sensibles se almacenan de forma segura:
- Las credenciales de API se guardan en **Windows Credential Manager** (encriptadas)
- El ICCID del dispositivo se almacena de forma segura (NO en config.json)
- El historial y los metadatos de Device Controller contienen identificadores de dispositivos; trata su base de datos y las exportaciones como datos privados.

## 🐛 Solución de Problemas

### "El programa no se inicia"
- Verificar que se ha descomprimido toda la carpeta de la distribución
- Verificar que tienes permisos de lectura y escritura

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

**Versión**: 2.2.0
