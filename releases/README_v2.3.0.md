# TdSerial IoT v2.3.0 - Ejecutable para Windows

## 📦 Contenido del Archivo

La distribución de esta versión contiene la aplicación preparada para ejecutar en Windows, sin necesidad de instalar Python. Conserva toda la carpeta descomprimida, incluidas sus dependencias.

## 🔧 Sketches Arduino incluidos

Esta distribución incluye la carpeta `sketches/` con dos paquetes Arduino para la placa **LilyGO SIM7080G-S3**:

### ATBridge
Sketch para comunicación directa con el módem SIM7080G:
- Envío de comandos AT sin procesar al módulo
- Recepción de respuestas en tiempo real
- Ideal para diagnóstico y validación de configuración

### 1NCE_UDP
Sketch para testing y conexión bidireccional con **1NCE OS**:
- Comunicación UDP con plataforma 1NCE OS
- Configuración de contexto APN y socket
- Envío y recepción de payloads JSON
- Desarrollo e integración IoT

**Nota**: Extrae la carpeta `sketches/` de la distribución si planeas usarlos con Arduino IDE.

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

## ✨ Características v2.3.0

- ✅ Almacenamiento seguro de credenciales en Windows Credential Manager
- ✅ ICCID del dispositivo guardado de forma segura (NO en archivos de configuración)
- ✅ Interfaz gráfica intuitiva con PySide6/Qt
- ✅ Comunicación serial con dispositivos IoT
- ✅ Visualización del buffer de entrada disponible al conectar, con opción para limpiarlo
- ✅ Integración con plataforma 1NCE OS
- ✅ Correlación de ACK de Historian con comandos de Device Controller al consultar Historian
- ✅ Estado de ejecución y fecha del ACK guardados en el historial local, independientes del estado de la API

## Buffer serie al conectar

De forma predeterminada, los datos disponibles al abrir el puerto se muestran en el monitor como **BUFFER**. En Windows se evita vaciar la cola de entrada durante la apertura. Los datos recibidos después aparecen como **RX**.

Para descartar la entrada disponible al conectar, marca **Limpiar buffer** junto a **Conectar**. La preferencia se guarda para futuras sesiones.

Los datos anteriores solo pueden mostrarse si el dispositivo o su controlador los han conservado; la recuperación no está garantizada. La etiqueta **BUFFER** indica cuándo se leyeron los datos, no cuándo fueron enviados.

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

### "No aparecen datos BUFFER"
- Comprueba que **Limpiar buffer** esté desmarcado antes de conectar.
- Los datos enviados antes de abrir el puerto solo están disponibles si el dispositivo o su controlador los han conservado.
- Revisa la velocidad y los demás parámetros serie del dispositivo.

### "Error de credenciales"
- Abrir: Control Panel → User Accounts → Credential Manager
- Verificar que tienes las credenciales de 1NCE OS guardadas
- Eliminar credenciales antiguas si existen

## 📚 Documentación

Para más información, consulta el [README del proyecto](https://github.com/jbellid/TDSerialIoT) y la [documentación de seguridad](https://github.com/jbellid/TDSerialIoT/blob/main/docs/SECURITY_IMPROVEMENTS.md).

## 💬 Soporte

¿Problemas? Contactar a través del repositorio de GitHub:
https://github.com/jbellid/TDSerialIoT/issues

---

**Versión**: 2.3.0
