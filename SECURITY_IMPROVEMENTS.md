# Mejoras de Seguridad - Almacenamiento del ICCID

## Resumen

A partir de la versión v2.1.0, el identificador del dispositivo **ICCID** ya **no se guarda en el archivo `config.json`** del repositorio.

## Cambios Implementados

### 1. **Almacenamiento Seguro**
- ✅ El ICCID se almacena en **Windows Credential Manager** (almacén seguro del sistema operativo)
- ✅ El archivo `config.json` nunca contiene el ICCID
- ✅ El ICCID está **encriptado** en el almacén del sistema

### 2. **Nuevas Funciones en `onence_api.py`**

```python
# Cargar el ICCID guardado
iccid = load_device_iccid()  # Returns: str | None

# Guardar el ICCID de forma segura
store_device_iccid("******************")  # Raises: ValueError, CredentialStoreError

# Eliminar el ICCID almacenado
delete_device_iccid()  # Raises: CredentialStoreError
```

### 3. **Flujo de la Aplicación**

1. **Al iniciar** la aplicación:
   - Lee el ICCID del Credential Manager
   - Carga el valor en el campo de entrada (`onence_iccid_edit`)

2. **Durante la ejecución**:
   - Cuando el usuario modifica el ICCID en la interfaz gráfica
   - Se guarda **automáticamente** en Windows Credential Manager
   - **NO se guarda** en `config.json`

3. **Al cerrar** la aplicación:
   - Los datos de configuración se persisten en `config.json`
   - El ICCID NO se incluye nunca en este archivo

## Ventajas de Seguridad

| Aspecto | Antes | Ahora |
|--------|--------|-------|
| **Almacenamiento** | Texto plano en `config.json` | Encriptado en Credential Manager |
| **Accesibilidad** | Visible en el repositorio | Solo accesible en la máquina del usuario |
| **Historial de git** | Guardado en historio | No en repositorio |
| **Repositorio público** | Inseguro | ✅ Seguro |
| **Migración de archivos** | ICCID viaja con config.json | Permanece en el sistema |

## Impacto en el Repositorio Público

✅ **El repositorio es SEGURO para publicar**:
- No hay ICCID en `config.json`
- No hay ICCID en el historial de git
- No hay credenciales API en el código
- Las credenciales usan Windows Credential Manager

## Migración de Usuarios Existentes

Si los usuarios ya tenían un ICCID guardado en una versión anterior:

1. **Opción A - Automática**: Dejar que la aplicación lo migre
   - La aplicación cargaría el ICCID antiguo de `config.json` en primera ejecución
   - Se guardaría automáticamente en Credential Manager

2. **Opción B - Manual**: Los usuarios pueden:
   - Copiar el ICCID del archivo `config.json` antiguo
   - Pegarlo en la interfaz gráfica de la nueva versión
   - Se guardará automáticamente de forma segura

## Recuperación de Errores

Si hay error al acceder a Windows Credential Manager:

```
⚠️ "No se pudo leer el ICCID del almacén seguro"
```

**Soluciones**:
1. Verificar que Windows Credential Manager esté disponible
2. Verificar permisos de usuario
3. Intentar guardar nuevamente el ICCID en la interfaz gráfica

## Notas para Desarrolladores

### Imports necesarios

```python
from tdserial_iot.onence_api import (
    load_device_iccid,
    store_device_iccid,
    delete_device_iccid,
)
```

### Excepciones a manejar

```python
from tdserial_iot.onence_api import CredentialStoreError

try:
    iccid = load_device_iccid()
except CredentialStoreError as e:
    # Credential Manager no disponible
    logger.error(f"Error: {e}")
```

### Limpieza en `app_config.py`

La función `save_config()` automáticamente filtra el ICCID:

```python
def save_config(config: dict[str, Any]) -> None:
    # Filtrar ICCID - nunca se guarda en archivo
    config_to_save = {k: v for k, v in config.items() if k != "onence_iccid"}
    # ... guardar solo el resto de configuración
```

## Referencias

- [Windows Credential Manager](https://learn.microsoft.com/en-us/windows/win32/secauthn/credentials-management)
- [Python keyring documentation](https://keyring.readthedocs.io/)
- Commit: `7cda1f6` - "Store device ICCID in secure Credential Manager"
- Commit: `722ef28` - "Remove sensitive ICCID from config.json"
