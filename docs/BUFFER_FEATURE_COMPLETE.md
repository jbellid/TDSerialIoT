# Lectura del buffer serie al conectar

## Contexto

Incorporada en v2.3.0 para mostrar los datos que el dispositivo o su controlador hayan conservado antes de abrir el puerto. La recuperación depende del hardware y del controlador; no es posible recuperar datos que ya se hayan descartado.

La implementación inicial utilizaba la apertura estándar de pySerial para Windows, que vacía las colas al abrir el puerto. Esto impedía recuperar datos disponibles en el dispositivo utilizado para la prueba. La apertura específica para Windows evita esa purga; posteriormente, pySerial sigue gestionando las operaciones serie.

## Comportamiento

- Con **Limpiar buffer** desmarcado, los bytes disponibles inmediatamente después de abrir el puerto se muestran en el monitor como **BUFFER**. Los bytes leídos posteriormente aparecen como **RX**.
- Con **Limpiar buffer** marcado, se descarta la entrada disponible al conectar sin mostrarla. La opción se guarda en `config.json` y está desmarcada por defecto.
- La etiqueta **BUFFER** identifica el momento de lectura, no el momento de transmisión. El monitor admite las vistas ASCII y HEX.
- Un error al leer o limpiar el buffer se comunica al usuario y se cierra el puerto.

## Comprobación

Durante la revisión de la funcionalidad, una prueba con la placa conectada a COM6 recuperó 176 bytes de arranque, repartidos en seis líneas, usando la nueva apertura. El resultado depende del dispositivo y no garantiza que otros controladores conserven sus datos.

Las pruebas relacionadas están en [test_buffer_reading.py](../tests/test_buffer_reading.py), [test_serial_windows.py](../tests/test_serial_windows.py), [test_serial_worker.py](../tests/test_serial_worker.py) y [test_monitor_display.py](../tests/test_monitor_display.py).
