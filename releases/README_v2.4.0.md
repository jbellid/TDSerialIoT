# TdSerial IoT v2.4.0

Esta versión incorpora una plantilla Arduino para enviar telemetría de la LilyGO T-SIM7080G-S3 a 1NCE OS. TinyGSM gestiona el registro y la conexión de datos, y el SIM7080G envía el JSON por UDP mediante comandos AT.

## Plantilla Arduino

Descarga [TdSIM7080G_Plantilla_TinyGSM_1NCE.zip](../sketches/TdSIM7080G_Plantilla_TinyGSM_1NCE.zip). Necesitas Arduino IDE con soporte ESP32, TinyGSM, XPowersLib y una SIM 1NCE activa. La [guía de la plantilla](../README.md#plantilla-tinygsm-para-1nce) explica cómo configurar la placa, añadir lecturas de sensores y verificar la recepción en Historian.

## Cambios en el monitor serie

- Un envío manual de una sola línea usa la terminación seleccionada y permite continuar sin esperar una respuesta AT.
- Los envíos de varias líneas mantienen la cola AT. Fuera de una operación AT, el monitor agrupa los fragmentos de texto recibidos.
- La apertura del puerto aplica el control de flujo seleccionado.

## Ejecutable para Windows

Descomprime `TdSerialIoT.v2.4.0.zip` y ejecuta `TdSerialIoT.exe` desde la carpeta extraída. Conserva la carpeta `_internal` junto al ejecutable. El paquete incluye Python y las dependencias de la aplicación.

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


