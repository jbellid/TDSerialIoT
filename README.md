# TdSerial IoT

Aplicación de escritorio para Windows orientada al diagnóstico serie de módems IoT. Incluye la **Fase 1** (conexión, monitor TX/RX y comandos AT), la **Fase 2** de diagnóstico y la **Fase 3** de comunicaciones UDP para SIM7080G.

## Requisitos

- Windows 10/11.
- Python 3.12 o superior.
- Dependencias indicadas en `requirements.txt`.

## Instalación

Desde PowerShell, en la carpeta del proyecto:

```powershell
py -3 -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

También se puede ejecutar `run_app.bat`; este crea el entorno virtual e instala dependencias si aún no existe `.venv`.
El lanzador y el punto de entrada Python configuran las rutas de plugins de Qt desde la instalación local de PySide6 para evitar conflictos con rutas Qt del sistema.

## Ejecución y uso

```powershell
.\.venv\Scripts\python.exe -m tdserial_iot.main
```

La configuración inicial es COM6, 115200 baudios, 8 bits, sin paridad, 1 bit de parada y sin control de flujo. Si el puerto no está disponible, actualiza la lista o escribe el puerto en el selector. El monitor presenta TX a partir del número de bytes que `pyserial` confirma haber escrito y RX a partir de los bytes leídos desde el puerto. Las respuestas AT de texto se separan por CR/LF, incluso cuando llegan fragmentadas; el payload de CARECV se extrae por su longitud declarada y conserva los bytes de control. Si el módem devuelve el eco del comando, este aparece como RX porque son bytes recibidos realmente; no se elimina ni se clasifica por comparar el texto con el último TX.

La conexión serie usa apertura explícita de pyserial: crea el objeto sin abrir, solicita DTR=False, RTS=False y BREAK=False, desactiva XON/XOFF, RTS/CTS y DSR/DTR, y después abre el puerto. El log técnico registra los estados solicitados y consultados antes y después de abrir, además de las operaciones de apertura/cierre. Aun así, Windows o el controlador USB pueden cambiar brevemente las líneas durante la apertura física; la aplicación no puede garantizar que el dispositivo no observe ese transitorio.

Al conectar se activa por defecto el **modo pasivo**: solo se inicia la recepción y el estado indica **Puerto conectado — modo pasivo**. No se envían comandos AT, no se ejecuta ATE0, no se inicia diagnóstico ni se activa monitorización. Los comandos manuales están disponibles inmediatamente. Para optar por la inicialización, marca antes de conectar **Inicializar comunicación AT al conectar**; entonces la aplicación espera 10 segundos para dejar terminar el arranque del firmware LilyGO y ejecuta `AT` seguido de `ATE0` por la cola serie, esperando la respuesta terminal de cada comando. El timeout o error cancela el resto de esa inicialización. Esta preferencia se guarda en `config.json` y está desactivada de forma predeterminada.

El campo de comandos acepta varias líneas pegadas: cada línea no vacía es un comando independiente. La cola transmite solo uno a la vez y espera líneas completas `OK`, `ERROR`, `+CME ERROR` o `+CMS ERROR`; los ecos, respuestas parciales y URC intercalados no la adelantan. `AT+CAOPEN` requiere tanto `OK` como el URC `+CAOPEN` del mismo identificador de conexión, en cualquier orden. El timeout por comando se configura en segundos (1–600, predeterminado 10). Si vence, la secuencia se pausa y exige **Reanudar** para continuar; una respuesta tardía no libera la cola pausada. **Al encontrar error** permite elegir **Detener** (predeterminado), **Continuar** o **Preguntar**. El monitor indica el comando actual y los contadores de comandos ejecutados, pendientes, completados y fallidos. **Cancelar secuencia** descarta los comandos pendientes, pero deja terminar la operación actual; no se cierra ni se interrumpe el puerto serie. La interacción prompt/payload de `AT+CASEND` está disponible en la pestaña Comunicaciones IoT. Enter envía el contenido; Shift+Enter inserta una línea al editar manualmente. El contador indica los bytes totales y el número de comandos. Las flechas arriba/abajo recorren el historial. La casilla **Activar/desactivar eco** envía `ATE1` al marcarse y `ATE0` al desmarcarse, usando la misma cola serie. El monitor permite pausar solo la visualización (la recepción continúa), autoscroll, limpiar, copiar, guardar y cambiar entre ASCII y HEX. La casilla **Mostrar caracteres especiales y tamaño (bytes)** está desmarcada por defecto: desmarcada, omite los bytes de control/no ASCII y el contador de tamaño del monitor; marcada, muestra los controles como símbolos visibles (por ejemplo, CR como `␍`, LF como `␊`) y añade el tamaño TX/RX en bytes. Al alternarla, el monitor se regenera con la vista elegida, incluidos los registros anteriores. Las preferencias de timeout, política de error y parámetros de Comunicaciones IoT se guardan en `config.json`.

Los parámetros convencionales se pasan al controlador serie tal como se seleccionan. Algunos puertos USB Serial/JTAG pueden no admitir o reflejar todos los ajustes; la aplicación informa de errores sin intentar modificar parámetros del módem. Las comunicaciones serie se realizan desde un único hilo de trabajo para no bloquear la interfaz.

## Diagnóstico IoT SIM7080G

La pestaña **Diagnóstico IoT** reutiliza la misma conexión, lector y cola serie. **Ejecutar diagnóstico completo** consulta identificación, SIM, registro, operador, señal, datos y sockets uno a uno; los botones de cada tarjeta permiten actualizar solo ese grupo. Las respuestas siguen apareciendo en el monitor serie TX/RX. IMEI, ICCID e IMSI se muestran enmascarados hasta activar **Mostrar identificadores completos**.

El botón **Copiar diagnóstico** copia al portapapeles la fecha y hora del último diagnóstico y todos los campos del panel, incluidos los que todavía indiquen N/D. Los identificadores respetan el estado de enmascarado de la interfaz; al habilitar su visualización completa también se copiarán completos.

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
