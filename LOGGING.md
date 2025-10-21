# 📋 Logging System Documentation

## Overview

El sistema de logging del backend está diseñado para proporcionar información detallada sobre las operaciones de la API, incluyendo requests, responses, errores y actividad del sistema.

## Características

### ✅ **Log Levels Configurables**

- Control granular de niveles de log por componente
- Configurable vía variables de entorno

### ✅ **File Logging con Rotación**

- Logs guardados en archivos con rotación diaria
- Retención automática de 30 días
- Archivos separados para logs de aplicación y requests

### ✅ **Detailed Request/Response Logging**

- Logueo de request body (POST/PUT/PATCH)
- Logueo de response body
- Información de timing y performance
- Información del cliente (IP)

### ✅ **Structured Logging**

- Logs en formato JSON para fácil parsing
- Información contextual completa

---

## Configuración

### Variables de Entorno

Añade estas variables a tu archivo `.env`:

```bash
# Niveles de log
LOG_LEVEL=INFO                    # DEBUG, INFO, WARNING, ERROR, CRITICAL
SQLALCHEMY_LOG_LEVEL=ERROR        # Logs de base de datos
BITTENSOR_LOG_LEVEL=WARNING       # Logs de Bittensor
UVICORN_LOG_LEVEL=INFO            # Logs del servidor web
UVICORN_ACCESS_LOG=false          # Mostrar access logs de Uvicorn

# File logging
LOG_TO_FILE=true                  # Habilitar guardado en archivos
LOG_FILE_PATH=logs/app.log        # Ruta del archivo principal

# Detailed logging
LOG_REQUEST_BODY=true             # Loguear payloads de requests
LOG_RESPONSE_BODY=true            # Loguear response data
```

Ver `config/logging.env.example` para más detalles.

---

## Archivos de Log

Cuando `LOG_TO_FILE=true`, se crean los siguientes archivos:

```
logs/
├── app.log                    # Logs generales de la aplicación (actual)
├── requests.log               # Logs detallados de requests/responses (actual)
├── app.log.2025-10-21        # Backup del día anterior
├── app.log.2025-10-20        # Backup de hace 2 días
├── requests.log.2025-10-21   # Backup de requests del día anterior
└── ...                        # Hasta 30 días de historia
```

### Rotación

- **Frecuencia**: Diaria (medianoche)
- **Retención**: 30 días
- **Formato de backup**: `YYYY-MM-DD`

---

## Ejemplos de Uso

### 1. **Desarrollo Local** (Máximo detalle)

```bash
LOG_LEVEL=DEBUG
LOG_TO_FILE=true
LOG_REQUEST_BODY=true
LOG_RESPONSE_BODY=true
SQLALCHEMY_LOG_LEVEL=DEBUG  # Ver queries SQL
```

### 2. **Producción** (Logs moderados)

```bash
LOG_LEVEL=INFO
LOG_TO_FILE=true
LOG_REQUEST_BODY=false
LOG_RESPONSE_BODY=false
SQLALCHEMY_LOG_LEVEL=ERROR
```

### 3. **Debugging en Producción** (Temporalmente detallado)

```bash
LOG_LEVEL=DEBUG
LOG_TO_FILE=true
LOG_REQUEST_BODY=true
LOG_RESPONSE_BODY=true
```

⚠️ **Importante**: Recuerda volver a la configuración de producción después de debuggear.

---

## Formato de Logs

### Console Output (stderr)

```
2025-10-21 12:30:45,123 - app - INFO - Starting Autoppia IWA Platform API...
```

### File Logs (más detallado)

```
2025-10-21 12:30:45,123 - app.requests - INFO - dispatch:45 - → POST /api/v1/validator-rounds | {"type": "request", "method": "POST", "path": "/api/v1/validator-rounds", "client": "172.68.229.32", "body": {...}}
```

### Request Logs (JSON estructurado)

```json
{
  "type": "request",
  "method": "POST",
  "path": "/api/v1/validator-rounds",
  "client": "172.68.229.32",
  "query_params": null,
  "body": {
    "validator_round_id": "round_123",
    "validator_uid": 102,
    "data": {...}
  }
}
```

### Response Logs

```json
{
  "type": "response",
  "method": "POST",
  "path": "/api/v1/validator-rounds",
  "status": 201,
  "elapsed_seconds": 0.045,
  "body": {
    "success": true,
    "data": {...}
  }
}
```

---

## Analyzing Logs

### Ver logs en tiempo real

```bash
# Logs generales
tail -f logs/app.log

# Logs de requests
tail -f logs/requests.log

# Ambos simultáneamente
tail -f logs/*.log
```

### Filtrar por nivel

```bash
# Solo errores
grep "ERROR" logs/app.log

# Solo warnings y errores
grep -E "WARNING|ERROR" logs/app.log
```

### Filtrar por endpoint

```bash
# Ver todos los requests a /api/v1/validator-rounds
grep "/api/v1/validator-rounds" logs/requests.log

# Ver solo los errores en ese endpoint
grep "/api/v1/validator-rounds" logs/requests.log | grep "ERROR"
```

### Analizar performance

```bash
# Ver requests lentos (>1 segundo)
grep "elapsed_seconds" logs/requests.log | awk -F'"elapsed_seconds": ' '{print $2}' | awk '$1 > 1.0'
```

### Ver requests de un cliente específico

```bash
grep "172.68.229.32" logs/requests.log
```

---

## Troubleshooting

### No se crean archivos de log

**Problema**: `LOG_TO_FILE=true` pero no aparecen archivos en `logs/`

**Solución**:

1. Verifica que el directorio `logs/` exista y tenga permisos de escritura
2. Revisa si hay errores de permisos en stderr
3. Asegúrate de que la variable esté en el `.env` correcto

```bash
# Crear directorio manualmente
mkdir -p logs
chmod 755 logs
```

### Los logs son demasiado verbosos

**Solución**: Ajusta los niveles de log

```bash
# Reducir verbosidad
LOG_LEVEL=WARNING
SQLALCHEMY_LOG_LEVEL=ERROR
UVICORN_ACCESS_LOG=false
```

### Quiero ver queries SQL

**Solución**:

```bash
SQLALCHEMY_LOG_LEVEL=DEBUG
```

### Los archivos de log ocupan mucho espacio

**Solución**:

1. Los archivos se rotan automáticamente cada día
2. Se eliminan automáticamente después de 30 días
3. Para cambiar la retención, edita `app/logging.py`:

```python
backupCount=30,  # Cambia a 7 para retener solo 7 días
```

---

## Security Considerations

⚠️ **IMPORTANTE**: Los logs pueden contener información sensible

### Datos que se pueden loguear:

- Request bodies (pueden incluir passwords, tokens, etc.)
- Response bodies (pueden incluir datos privados)
- Query parameters (pueden incluir IDs sensibles)
- Headers (pueden incluir auth tokens)

### Recomendaciones:

1. **Producción**: Deshabilita `LOG_REQUEST_BODY` y `LOG_RESPONSE_BODY`
2. **Permisos**: Asegura que solo usuarios autorizados puedan leer los logs
3. **Rotación**: Mantén la retención en 30 días o menos
4. **Backup**: No incluyas logs en backups públicos

```bash
# Permisos seguros para logs
chmod 640 logs/*.log
chown www-data:www-data logs/*.log
```

---

## Performance Impact

### Overhead de Logging

| Configuración            | Impacto en Performance    |
| ------------------------ | ------------------------- |
| `LOG_TO_FILE=false`      | Mínimo (~0.1ms/request)   |
| `LOG_TO_FILE=true`       | Bajo (~0.5ms/request)     |
| `LOG_REQUEST_BODY=true`  | Medio (~1-2ms/request)    |
| `LOG_RESPONSE_BODY=true` | Medio (~1-2ms/request)    |
| Todo habilitado          | Moderado (~2-5ms/request) |

**Nota**: El impacto real depende del tamaño de los payloads.

---

## Best Practices

### ✅ **DO**

- Usar `LOG_LEVEL=INFO` en producción
- Habilitar `LOG_TO_FILE=true` siempre
- Monitorear el tamaño del directorio `logs/`
- Revisar logs regularmente para detectar problemas
- Usar niveles apropiados según el entorno

### ❌ **DON'T**

- No dejar `LOG_REQUEST_BODY=true` en producción permanentemente
- No compartir logs públicamente sin sanitización
- No loguear passwords o tokens explícitamente en el código
- No subir logs a git (añade `logs/` a `.gitignore`)

---

## Integración con Herramientas

### Logrotate (Linux)

```bash
# /etc/logrotate.d/autoppia-backend
/path/to/logs/*.log {
    daily
    rotate 30
    compress
    delaycompress
    notifempty
    create 0640 www-data www-data
}
```

### Monitoring con Prometheus + Grafana

Los logs estructurados en JSON son fáciles de parsear con herramientas como:

- **Loki** (de Grafana)
- **ELK Stack** (Elasticsearch, Logstash, Kibana)
- **Datadog**
- **CloudWatch Logs** (AWS)

---

## Debugging Common Issues

### 1. **No veo requests en los logs**

```bash
# Verifica que el middleware esté activo
grep "LOG_REQUEST_BODY\|LOG_RESPONSE_BODY" .env

# Debe mostrar:
# LOG_REQUEST_BODY=true
# LOG_RESPONSE_BODY=true
```

### 2. **Veo duplicados de logs**

Esto es normal: los logs aparecen tanto en stderr como en archivos.

### 3. **No veo el body de los requests**

Solo se loguean bodies para `POST`, `PUT`, `PATCH`. Los `GET` no tienen body.

### 4. **Los logs están truncados**

Los bodies muy largos (>1000 caracteres) se truncan automáticamente. Para cambiar:

```python
# En app/middleware/logging_middleware.py
if len(body_str) > 1000:  # Cambia a 5000 para más contexto
```

---

## Support

Para más información:

- Ver código en `app/logging.py`
- Ver middleware en `app/middleware/logging_middleware.py`
- Configuración en `app/config.py`
- Ejemplo en `config/logging.env.example`
