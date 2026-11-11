# TdSerial IoT

Aplicación de escritorio para Windows orientada al diagnóstico serie y al control remoto de módems IoT. Incluye la **Fase 1** (conexión, monitor TX/RX y comandos AT), la **Fase 2** de diagnóstico, la **Fase 3** de comunicaciones UDP para SIM7080G, la **Fase 4** de consulta de mensajes de 1NCE OS Device Inspector Historian y la **Fase 5** de comunicación bidireccional con una placa de desarrollo mediante la API de 1NCE OS y UDP.

## Versión Actual

**v2.1.0** — Versión de distribución con seguridad mejorada y ejecutable standalone.

### Cambios en v2.1.0

- 🔐 **Almacenamiento seguro**: ICCID del dispositivo ahora se almacena en Windows Credential Manager (encriptado) en lugar de config.json
- 📦 **Ejecutable standalone**: Disponible como `.exe` independiente sin necesidad de Python instalado
- ✅ **Auditoría de seguridad**: Repositorio limpiado de secrets, historial verificado
- 📚 **Documentación completa**: Guías de compilación, distribución y seguridad

## Requisitos

### Para Usuarios Finales (Ejecutable v2.1.0)

- **Windows 10/11** (64-bit)
- **USB serial port** disponible
- **Ejecutable TdSerialIoT.exe** (descargar de [Releases](https://github.com/jbellid/TDSerialIoT/releases))
- ❌ **NO requiere Python** — todas las dependencias incluidas

### Para Desarrolladores (Compilación desde código)

- Windows 10/11
- Python 3.12 o superior
- Dependencias indicadas en `requirements.txt`

## Instalación

### Opción 1: Ejecutable Standalone (Recomendado para Usuarios)

1. Descargar `TdSerialIoT_v2.1.0_Windows.zip` desde [Releases](https://github.com/jbellid/TDSerialIoT/releases)
2. Descomprimir en la carpeta deseada
3. Ejecutar `TdSerialIoT.exe` (doble clic)
4. ¡Listo! No requiere instalación adicional

### Opción 2: Desde Código Fuente (Desarrolladores)

Desde PowerShell, en la carpeta del proyecto:

```powershell
py -3 -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

También se puede ejecutar `run_app.bat`; este crea el entorno virtual e instala dependencias si aún no existe `.venv`.
El lanzador y el punto de entrada Python configuran las rutas de plugins de Qt desde la instalación local de PySide6 para evitar conflictos con rutas Qt del sistema.

## Ejecución y uso

### Desde Ejecutable (v2.1.0)

```
TdSerialIoT.exe
```

### Desde Código Fuente

```powershell
.\.venv\Scripts\python.exe -m tdserial_iot.main
```

La configuración inicial es COM6, 115200 baudios, 8 bits, sin paridad, 1 bit de parada y sin control de flujo. Si el puerto no está disponible, actualiza la lista o escribe el puerto en el selector. El monitor presenta TX a partir del número de bytes que `pyserial` confirma haber escrito y RX a partir de los bytes leídos desde el puerto. Las respuestas AT de texto se separan por CR/LF, incluso cuando llegan fragmentadas; el payload de CARECV se extrae por su longitud declarada y conserva los bytes de control. Si el módem devuelve el eco del comando, este aparece como RX porque son bytes recibidos realmente; no se elimina ni se clasifica por comparar el texto con el último TX.

La conexión serie usa apertura explícita de pyserial: crea el objeto sin abrir, solicita DTR=False, RTS=False y BREAK=False, desactiva XON/XOFF, RTS/CTS y DSR/DTR, y después abre el puerto. El log técnico registra los estados solicitados y consultados antes y después de abrir, además de las operaciones de apertura/cierre. Aun así, Windows o el controlador USB pueden cambiar brevemente las líneas durante la apertura física; la aplicación no puede garantizar que el dispositivo no observe ese transitorio.

Al conectar se activa por defecto el **modo pasivo**: solo se inicia la recepción y el estado indica **Puerto conectado — modo pasivo**. No se envían comandos AT, no se ejecuta ATE0, no se inicia diagnóstico ni se activa monitorización. Los comandos manuales están disponibles inmediatamente. Para optar por la inicialización, marca antes de conectar **Inicializar comunicación AT al conectar**; entonces la aplicación espera 10 segundos para dejar terminar el arranque del firmware LilyGO y ejecuta `AT` seguido de `ATE0` por la cola serie, esperando la respuesta terminal de cada comando. El timeout o error cancela el resto de esa inicialización. Esta preferencia se guarda en `config.json` y está desactivada de forma predeterminada.

El campo de comandos acepta varias líneas pegadas: cada línea no vacía es un comando independiente. La cola transmite solo uno a la vez y espera líneas completas `OK`, `ERROR`, `+CME ERROR` o `+CMS ERROR`; los ecos, respuestas parciales y URC intercalados no la adelantan. `AT+CAOPEN` requiere tanto `OK` como el URC `+CAOPEN` del mismo identificador de conexión, en cualquier orden. El timeout por comando se configura en segundos (1–600, predeterminado 10). Si vence, la secuencia se pausa y exige **Reanudar** para continuar; una respuesta tardía no libera la cola pausada. **Al encontrar error** permite elegir **Detener** (predeterminado), **Continuar** o **Preguntar**. El monitor indica el comando actual y los contadores de comandos ejecutados, pendientes, completados y fallidos. **Cancelar secuencia** descarta los comandos pendientes, pero deja terminar la operación actual; no se cierra ni se interrumpe el puerto serie. La interacción prompt/payload de `AT+CASEND` está disponible en la pestaña Comunicaciones IoT. Enter envía el contenido; Shift+Enter inserta una línea al editar manualmente. El contador indica los bytes totales y el número de comandos. Las flechas arriba/abajo recorren el historial. Para transmitir un carácter o texto sin tratarlo como un comando AT, activa **Enviar texto/caracteres sin terminación**: se envían exactamente los bytes UTF-8 escritos, sin CR/LF y sin esperar una respuesta terminal; no puede intercalarse con una secuencia AT activa. El TX aparece cuando el puerto confirma los bytes escritos y las respuestas recibidas continúan mostrándose como RX. La casilla **Activar/desactivar eco** envía `ATE1` al marcarse y `ATE0` al desmarcarse, usando la misma cola serie. El monitor permite pausar solo la visualización (la recepción continúa), autoscroll, limpiar, copiar, guardar y cambiar entre ASCII y HEX. La casilla **Mostrar caracteres especiales y tamaño (bytes)** está desmarcada por defecto: desmarcada, omite los bytes de control/no ASCII y el contador de tamaño del monitor; marcada, muestra los controles como símbolos visibles (por ejemplo, CR como `␍`, LF como `␊`) y añade el tamaño TX/RX en bytes. Al alternarla, el monitor se regenera con la vista elegida, incluidos los registros anteriores. Las preferencias de timeout, política de error y parámetros de Comunicaciones IoT se guardan en `config.json`.

Los parámetros convencionales se pasan al controlador serie tal como se seleccionan. Algunos puertos USB Serial/JTAG pueden no admitir o reflejar todos los ajustes; la aplicación informa de errores sin intentar modificar parámetros del módem. Las comunicaciones serie se realizan desde un único hilo de trabajo para no bloquear la interfaz.

## Diagnóstico IoT SIM7080G

La pestaña **Diagnóstico IoT** reutiliza la misma conexión, lector y cola serie. **Ejecutar diagnóstico completo** consulta identificación, SIM, registro, operador, señal, datos y sockets uno a uno; los botones de cada tarjeta permiten actualizar solo ese grupo. Las respuestas siguen apareciendo en el monitor serie TX/RX. IMEI, ICCID e IMSI se muestran completos en el panel y en los informes copiados/exportados.

El botón **Copiar diagnóstico** copia al portapapeles la fecha y hora del último diagnóstico y todos los campos del panel, incluidos los que todavía indiquen N/D, con los identificadores completos.

La interpretación del registro usa el segundo campo de `+CEREG` cuando se informa `<n>,<stat>`. El operador se muestra tal como lo informa el módem; si solo se recibe un código numérico no se inventa un nombre comercial. El modo de sistema de `AT+CPSI?` determina la tecnología: `LTE NB-IOT` muestra **Calidad de señal NB-IoT**, `LTE CAT-M1` muestra **Calidad de señal LTE-M**, y cualquier modo no reconocido usa el título genérico **Calidad de señal**. RSRP, RSRQ y RSSI se presentan como medidos según el reporte CPSI; RSSNR se identifica como reportado, y SINR se identifica siempre como calculado. El manual SIMCom SIM7070/SIM7080/SIM7090 V1.07, sección CPSI, página 131, indica para CAT-M1 y NB-IOT la fórmula `SINR = 2 × RSSNR − 20`. La aplicación calcula esa fórmula una sola vez desde el campo RSSNR de CPSI; la clasificación consume el SINR resultante y no vuelve a transformarlo. La suite simula ambas tecnologías, pero no sustituye la comprobación de la revisión de firmware que informa el módem instalado: ejecutar el diagnóstico y verificar `AT+CGMR` y la respuesta CPSI física si se requiere certificar esa revisión concreta.

Los umbrales son orientativos, no límites universales de radio. Se mantienen configuraciones independientes por RAT bajo `signal_quality_thresholds` en `config.json`, con tres límites descendentes por métrica `[excelente, buena, aceptable]`; inicialmente NB-IoT y LTE-M usan los mismos valores. Por ejemplo, `signal_quality_thresholds.LTE-M.rsrp` puede ajustarse sin cambiar NB-IoT. Los parámetros no válidos se registran en el log y conservan el valor predeterminado correspondiente. Para tecnología desconocida se usa el umbral inicial común, sin atribuirlo a una RAT. La evaluación global toma conservadoramente la peor métrica disponible y muestra cuando faltan datos. CSQ 99 y los campos ausentes se muestran como N/D. El RSSI y el indicador cualitativo derivados de CSQ se identifican explícitamente como estimaciones. Los contextos PDP y los contextos de aplicación se muestran por separado. El diagnóstico de módem no equivale a comprobar conectividad extremo a extremo.

**Monitorización automática** consulta únicamente `AT+CEREG?`, `AT+CSQ`, `AT+CPSI?` y `AT+CGATT?`, con intervalos seleccionables de 10, 30 o 60 segundos, o 5 minutos. Está detenida al conectar y debe activarse explícitamente desde la pestaña; un indicador muestra claramente si está activa o detenida. No consulta durante la inicialización AT, no inicia una ronda mientras haya otra en curso ni añade trabajo periódico a una cola ocupada; los comandos manuales tienen prioridad. Al detenerla se cancelan las consultas periódicas pendientes, y la desconexión detiene sus temporizadores y limpia las órdenes restantes de la cola.

La configuración local se guarda en `config.json` al cerrar la aplicación. El registro técnico se escribe en `logs/tdserial-iot.log` e incluye las aperturas/cierres del puerto, estados DTR/RTS/BREAK, cambios de conexión, inicialización, monitorización, comandos automáticos y timeouts.
Al cerrar la ventana, la aplicación detiene el hilo serie y cierra el puerto; no hace falta pulsar **Desconectar** antes de salir normalmente.

## Comunicaciones IoT — SIM7080G / 1NCE UDP

La pestaña **Comunicaciones IoT** implementa las operaciones UDP del SIM7080G con valores iniciales `udp.os.1nce.com:4445`, APN `iot.1nce.net`, contexto 0 y socket 0. Permite consultar contexto y sockets, configurar/activar el contexto, abrir/cerrar el socket, enviar y leer datos. Las comprobaciones de registro, servicio de paquetes, contexto y socket se ejecutan antes de abrir o enviar; una operación no se inicia si la cola AT está ocupada o pausada.

El editor admite ASCII/UTF-8, JSON, HEX y archivo binario. JSON se valida y se transmite sin reformatear; el contador refleja bytes codificados reales. HEX admite espacios y requiere pares de dígitos. Los archivos y payloads están limitados a 1460 bytes, máximo de `AT+CASEND` documentado por SIMCom. El comando `AT+CASEND` declara la longitud exacta y los bytes se envían solo al detectar `>`; no se añade CR/LF al payload. El timeout del prompt, la ventana de entrada CASEND y el timeout de respuesta final son configurables.

La operación de lectura `AT+CARECV` interpreta el campo de longitud de `+CARECV` y extrae exactamente esa cantidad de bytes, sin separar el payload binario por CR/LF; los datos permanecen además visibles como RX en el monitor. Esta extracción se basa en el encuadre documentado por el SIM7080G y debe verificarse con el firmware y el puente serie de la placa utilizados.

Un `OK` del módem significa que el comando fue aceptado/completado por el módem, no que el servidor UDP remoto recibió el datagrama. El monitor confirma los bytes que el puerto COM informó haber escrito, no que el firmware del ESP32-S3 los haya reenviado sin cambios ni que exista entrega remota. Si el puente serie del firmware almacena la entrada hasta recibir Enter, puede impedir el envío interactivo exacto requerido por CASEND; en ese caso será necesario modificar el firmware. No se realizan reintentos automáticos ni cambios implícitos de configuración del módem.

Si aparece un error de carga del plugin de plataforma de Qt, reinstala las dependencias en el entorno del proyecto:

```powershell
.\.venv\Scripts\python.exe -m pip install --force-reinstall -r requirements.txt
```

## Pruebas sin hardware

```powershell
.\.venv\Scripts\python.exe -m pytest
```

Los tests sustituyen el puerto por un puerto serie simulado y no necesitan una placa conectada.

## Alcance y validación física

El diagnóstico y las operaciones de socket están implementados exclusivamente para SIM7080G; no se incluyen perfiles funcionales para SIM7670G u otros módems. Las pruebas automatizadas no requieren una placa. La compatibilidad real del encuadre CARECV, el transporte binario interactivo a través del puente del ESP32-S3 y la recepción del datagrama en 1NCE deben confirmarse con el hardware y el visor remoto; esta aplicación no afirma una recepción UDP remota sin una confirmación específica del servidor.

## Fase 5 — Comunicación bidireccional con 1NCE Device Controller

La aplicación envía órdenes remotas mediante la API HTTPS de 1NCE Device Controller; 1NCE las entrega por UDP a la placa, y el firmware puede enviar telemetría y mensajes `command_ack` de vuelta a 1NCE OS. Esos mensajes pueden consultarse en la pestaña **1NCE Cloud** mediante Historian. La comunicación remota no requiere que la placa esté conectada por USB al ordenador.

### Firmware de la placa

En [`sketches/TdSIM7080_1NCE_UDP_v0_6_1.zip`](./sketches/TdSIM7080_1NCE_UDP_v0_6_1.zip) se incluye el firmware **TdSIM7080_1NCE_UDP v0.6.1** para LilyGO T-SIM7080G-S3 con SIM7080G. El archivo comprimido contiene el sketch Arduino, `utilities.h`, un README propio y una imagen de conexiones. Consulta ese README para la preparación de la placa, bibliotecas y configuración; indica Arduino-ESP32 3.x, XPowersLib, DHT sensor library y ArduinoJson 7.x.

Configura un token de comandos propio en el firmware y el mismo token en Device Controller; no publiques tokens reales ni credenciales. Para identificar la SIM en la API, utiliza el **Device ID exacto** de Device Inspector: puede tener una longitud distinta al ICCID comunicado por `AT+CCID`.

### Configuración y envío de órdenes

La pestaña **1NCE Device Controller** funciona sin conexión serie. Configura primero el Client ID y Client Secret en **1NCE Cloud**; el botón **Guardar configuración** de Device Controller también los guarda en el almacén seguro de Windows. La URL debe ser HTTPS y la validación TLS permanece activa. El timeout por defecto es de 30 segundos. **Cerrar sesión** invalida el token local sin borrar las credenciales guardadas.

Añade un dispositivo con nombre, ICCID/Device ID exacto, modelo, puerto UDP (normalmente 3000), observaciones y token del firmware. El token debe tener al menos 16 bytes y se guarda en el almacén seguro, no en la configuración ni en el historial. Los metadatos, contadores y el historial se guardan en `%LOCALAPPDATA%\TdSerialIoT\device-controller.sqlite3`; contienen identificadores de dispositivos y deben tratarse como datos privados.

En la fila del ICCID, **Actualizar dispositivos** consulta el inventario de la cuenta en 1NCE, igual que en Cloud. El selector muestra LABEL e ICCID cuando están disponibles; si no hay LABEL, muestra el ICCID. Al elegir un dispositivo aún no guardado localmente, completa sus datos y token y pulsa **Guardar dispositivo** antes de enviar.

Selecciona el dispositivo y después una acción para reservar el ID y ver el JSON con el token oculto. Revisa la vista previa y pulsa **Enviar comando**. Para una primera prueba elige **Encender LED** (`led_on`). También se admiten `led_off`, `led_toggle`, `buzzer` (500 ms), `telemetry`, `gps`, `status` y `reboot`; el reinicio exige confirmación. GNSS puede interrumpir temporalmente la conectividad celular. Los ID se conservan entre sesiones y se limita el JSON a los 400 bytes admitidos por el firmware v0.6.1.

Con **Editar comando** puedes modificar el objeto JSON: se conservan sus campos, `********` se sustituye por el token configurado y un token escrito expresamente se envía tal cual. Si `id` contiene `Se asignará al enviar`, se reserva uno nuevo; un ID numérico introducido manualmente se conserva y avanza el contador, pero no se admite reutilizar uno ya reservado. También se solicita confirmación si se escribe `reboot` manualmente. El editor puede mostrar cualquier token que escribas en él; no incluyas secretos si otras personas pueden ver la pantalla.

**SEND_NOW** solicita el envío inmediato; **SEND_WHEN_ACTIVE** lo programa para cuando el dispositivo vuelva a comunicarse. Un HTTP 202 significa que 1NCE aceptó la solicitud, no que el dispositivo la haya recibido o ejecutado. Introduce el ID de acción en **Consultar estado** para ver el estado de 1NCE; incluso `SUCCEEDED` en UDP no confirma la recepción. La columna de ejecución permanece **Sin confirmar**: la correlación automática con `command_ack` de Historian no está implementada.

Para comprobar la respuesta, consulta Historian para el mismo dispositivo y localiza el `command_ack` con el `cmd_id` enviado. En el firmware incluido, `executed` indica ejecución de acciones inmediatas; `accepted` indica aceptación de una tarea diferida, no su finalización. En particular, el ACK de reinicio puede no llegar y no confirma por sí solo que el reinicio se haya completado.

Al pulsar una fila del historial local, su ID de acción se copia al campo de consulta de estado. Si la fila todavía no tiene ID, el campo se vacía para evitar consultar una acción anterior.

Las operaciones HTTP se ejecutan fuera del hilo de la interfaz. Si una petición falla por red o timeout, su resultado puede ser desconocido: comprueba el historial y el dispositivo antes de volver a enviar, especialmente `led_toggle` y `reboot`. No hay cancelación de una petición HTTP en curso; antes de cerrar hay que esperar su finalización o timeout. Comprueba credenciales y permisos ante HTTP 401/403, ICCID ante 404 y límites de la cuenta ante 429.

Ante HTTP 404 al enviar, comprueba también la URL base y que el **Device ID** seleccionado coincida exactamente con el de Device Inspector de la cuenta autenticada. Puede diferir del ICCID leído por el módem; la aplicación no elimina dígitos automáticamente. Un 404 por sí solo no permite determinar cuál de esos recursos no se ha encontrado.

## Fase 4 — 1NCE OS Device Inspector Historian

La pestaña **1NCE Cloud** funciona independientemente de COM6. Usa HTTPS con validación TLS, autenticación OAuth en `https://api.1nce.com/management-api/oauth/token`, HTTP Basic y el cuerpo JSON `{"grant_type":"client_credentials"}`. El botón **Probar conexión** comprueba la autenticación; los permisos de Historian se comprueban al consultar.

Las credenciales solo se guardan o borran por acción explícita del usuario en el almacén de credenciales de Windows mediante `keyring`; no se incluyen en `config.json` ni en las exportaciones. El token permanece en memoria y se renueva antes de caducar; no se muestra ni se registra. Los fallos de autenticación, permisos, red, timeout y límite de solicitudes se presentan sin incluir secretos.

Hay tres modos de consulta: **Todos los dispositivos** usa `GET /v1/inspect/devices/history` sin ICCID; **Seleccionar dispositivo** carga el inventario de `GET /v1/inspect/devices` y cruza los ICCID con los LABEL de `GET /v1/sims`; e **ICCID manual** mantiene la consulta directa `GET /v1/inspect/devices/{deviceId}/history`. LABEL repetidos no se consideran identificadores únicos: el selector muestra cada LABEL junto con su ICCID. Si no hay LABEL o falla la consulta de metadatos SIM, se puede seguir seleccionando el dispositivo por ICCID. **Actualizar dispositivos** vuelve a cargar esa correspondencia en memoria.

Los tres modos admiten filtros de fechas y protocolo (UDP, CoAP o LwM2M) y paginación. Al lanzar una consulta, **Hasta** se actualiza a la hora actual para todos los periodos salvo **Personalizado**; **Últimos 7 días** también recalcula **Desde** dentro del límite de retención. Historian limita cada página a 25 mensajes; **Recuperar todos** respeta el límite total configurable y se detiene ante tokens repetidos. La lista de dispositivos y los metadatos SIM también tienen límites de paginación. Las páginas de mensajes se combinan sin duplicados. La casilla **Actualización automática** utiliza el mismo estilo visual que **Monitorización automática** de Diagnóstico IoT, no se solapa con una consulta en curso y respeta `Retry-After` ante HTTP 429. **Última consulta** se actualiza al iniciar cada petición y al completar correctamente la respuesta; así también se ve la hora de un intento que termina con error. Las fechas con zona se normalizan para mostrarse en hora local; las fechas sin zona se mantienen como tales, sin asumir UTC.

La tabla muestra fecha y hora, LABEL, ICCID, protocolo, contenido, tamaño y estado de decodificación; los objetos JSON también se despliegan en columnas dinámicas por propiedad. La tabla y el detalle conservan el payload original y ofrecen representaciones como JSON, texto, HEX y Base64. Para payloads que llegan codificados en Base64, la representación JSON intenta también decodificar el contenido seleccionado, aunque el protocolo no active la decodificación automática. Las estadísticas distinguen los registros devueltos en la consulta del total histórico (no asumido), y presentan UDP, última fecha y correlación con transmisiones locales. La correlación prioriza `seq` si el JSON ya lo contiene; no se altera automáticamente el contenido enviado. Como alternativa, se compara payload y tiempo en una ventana aproximada de cinco minutos. Coincidencias duplicadas se marcan ambiguas; la ausencia de coincidencia solo significa que no aparece en el periodo consultado y no demuestra pérdida. Un `OK` AT tampoco confirma recepción remota.

Los mensajes se pueden copiar y exportar como CSV o JSON. El ICCID y el Device ID se muestran y exportan completos.

Para diagnosticar solicitudes, `logs/tdserial-iot.log` registra método, URL completa, parámetros, código HTTP, duración, una lista permitida de cabeceras de respuesta y el cuerpo de error de 1NCE con valores sensibles redactados. No registra Client Secret, access token, Authorization ni Basic. En el modo global la URL no incluye ICCID; las consultas por dispositivo envían el Device ID exactamente como lo devuelve el inventario o como se introduce manualmente, sin quitar dígitos. El ICCID obtenido mediante `AT+CCID` puede diferir en longitud del Device ID de Device Inspector.

La integración real se puede probar sin iniciar la GUI ni conectar COM6. Guarda antes las credenciales en el almacén seguro desde la pestaña **1NCE Cloud** y pasa el Device ID exacto que funcionó en PowerShell:

```powershell
$env:TDSERIAL_ONENCE_DEVICE_ID = "Device ID exacto de Device Inspector"
.\.venv\Scripts\python.exe -m pytest -s -q tests\test_onence_live.py
```

Esta prueba fuerza la obtención de un access token nuevo desde el almacén de credenciales y consulta el endpoint sin filtros ni parámetros adicionales, para comparar con la petición directa de PowerShell; muestra los mensajes y payloads decodificados. Se omite en la suite normal si no está definida la variable `TDSERIAL_ONENCE_DEVICE_ID`; no requiere ni importa el controlador serie.

La documentación oficial consultada confirma que Historian continúa documentado bajo `/v1/inspect/...`, con paginación `pageSize`/`nextToken` (1–25 mensajes por página) y un periodo predeterminado de siete días. La migración a v2 publicada para finales de 2026 se refiere a endpoints de SIM Management; no se presupone que Historian tenga una ruta v2.

Referencias: [OAuth token](https://help.1nce.com/api/authorization/post-access-token-post/), [Device Historian endpoint](https://help.1nce.com/api/1nce-os/get-device-historian-messages/), [Historian API examples](https://help.1nce.com/docs/1nce-os/1nce-os-device-inspector/device-inspector-historian-api/) y [Management API migration](https://help.1nce.com/platform-migration/api-migration/).
