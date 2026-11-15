# TdSerial IoT v2.3.1 - Ejecutable para Windows

## Instalación

1. Descarga y descomprime el paquete en una carpeta de tu preferencia.
2. Conserva todos los archivos y carpetas de la distribución.
3. Ejecuta `TdSerialIoT.exe`. No es necesario instalar Python.

## Cambios en v2.3.1

- Se eliminó **Inicializar comunicación AT al conectar**.
- Al abrir el puerto, el estado pasa a **Puerto conectado** y los comandos manuales quedan disponibles inmediatamente.
- La aplicación no envía comandos AT automáticamente al conectar. El diagnóstico y la monitorización automática solo transmiten consultas al solicitarlos explícitamente.
- Las configuraciones existentes que contengan la antigua preferencia de inicialización AT se ignoran.

## Buffer serie al conectar

De forma predeterminada, los datos disponibles al abrir el puerto se muestran en el monitor como **BUFFER**. En Windows se evita vaciar la cola de entrada durante la apertura. Los datos recibidos después aparecen como **RX**.

Para descartar la entrada disponible al conectar, marca **Limpiar buffer**. Los datos anteriores solo pueden mostrarse si el dispositivo o su controlador los han conservado; la recuperación no está garantizada.

## Requisitos

- Windows 10 o superior (64-bit).
- Puerto serie USB para las funciones serie.
- Conexión a Internet para las funciones de la API de 1NCE.

Para más información sobre las características y el uso de la aplicación, consulta el [README del proyecto](../README.md).

**Versión**: 2.3.1
